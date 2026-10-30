"""The thinned APCAD track keeps each chromosome's own density, on real ClickHouse (E2E).

The genome overview asks for every chromosome's APCAD points at once, within one point
budget, and ``fetch_apcad_downsampled`` thins the track in ClickHouse. It ranked the
markers by their VCF ``qual`` across the whole genome. Joint calling scores a chromosome's
markers higher wherever any sample of the cohort carries extra reads there: one embryo's
trisomy raised a whole chromosome's ``qual`` for every embryo, and the overview drew that
chromosome far denser than the others while the stored sites were as dense as elsewhere.
The budget is now split over the chromosomes in proportion to their sites first, and the
markers are ranked by quality within each chromosome.

Synthetic markers go in through the app's own writer, for a new family and sample each run:
chr1 and chr9 with the same number of sites over the same span, chr9's scored higher, and
chr21 with a quarter of the sites over a quarter of the span. Read back within a budget
smaller than the track:

* every chromosome keeps its share of the budget, in proportion to its sites (before: chr9
  took the whole budget and chr1 and chr21 drew nothing);
* within a chromosome the highest-quality markers are still the ones kept;
* a window on one chromosome still gets the whole budget.

The rows are deleted at the end. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the
CI ``e2e`` job sets it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_STEP = 50_000  # one marker every 50 kb on every chromosome: the same density
_BUDGET = 1_000


def _markers(family_uuid: str, sample_uuid: str, chrom: str, sites: int, qual: float) -> list[dict[str, Any]]:
    """``sites`` PASS markers on ``chrom``, 3 in 5 heterozygous, alternating parents; every
    tenth marker scores 5 higher than the rest."""
    rows = []
    for index in range(sites):
        value = 0.5 if index % 5 < 3 else float(index % 2)
        marker_qual = qual + 5 if index % 10 == 0 else qual
        rows.append(
            {
                "family_id": family_uuid,
                "sample_id": sample_uuid,
                "track_type": "apcad",
                "source": "e2e",
                "chr": chrom,
                "start": index * _STEP,
                "end": index * _STEP + 1,
                "value": value,
                "origin": "paternal" if index % 2 else "maternal",
                "metadata_json": f'{{"filter": "PASS", "qual": {marker_qual}}}',
            }
        )
    return rows


async def _exercise() -> dict[str, Any]:
    from backend.app.services.clickhouse_interval_tracks import (
        delete_interval_tracks,
        fetch_apcad_downsampled,
        insert_interval_track_rows,
    )

    family_uuid, sample_uuid = str(uuid4()), str(uuid4())
    rows = (
        _markers(family_uuid, sample_uuid, "1", 2_000, qual=40.0)
        + _markers(family_uuid, sample_uuid, "9", 2_000, qual=60.0)
        + _markers(family_uuid, sample_uuid, "21", 500, qual=40.0)
    )
    try:
        await insert_interval_track_rows(_ASSEMBLY, rows)
        genome = await fetch_apcad_downsampled(
            _ASSEMBLY,
            sample_uuid=sample_uuid,
            family_uuid=family_uuid,
            chromosomes=["1", "9", "21"],
            origins=["paternal", "maternal"],
            budget=_BUDGET,
        )
        window = await fetch_apcad_downsampled(
            _ASSEMBLY,
            sample_uuid=sample_uuid,
            family_uuid=family_uuid,
            chromosomes=["9"],
            origins=["paternal", "maternal"],
            budget=100,
            start=0,
            end=20_000_000,
        )
        return {"genome": genome, "window": window, "rows": rows}
    finally:
        await delete_interval_tracks(_ASSEMBLY, family_uuid=family_uuid, sample_uuid=sample_uuid)


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    return _harness.run_async(_exercise)


def test_each_chromosome_keeps_its_share_of_the_budget(result) -> None:
    per_chromosome = Counter(point["chr"] for point in result["genome"])
    # 4,500 sites, 1,000 points: 2,000 / 4,500 of them each for chr1 and chr9 (444.4, the
    # point left over goes to chr1, the first of the two tied remainders) and 500 / 4,500
    # for chr21. Ranked across the genome, chr9 took all 1,000.
    assert per_chromosome == {"1": 445, "9": 444, "21": 111}
    assert sum(per_chromosome.values()) == _BUDGET


def test_points_per_mb_match_across_chromosomes(result) -> None:
    per_chromosome = Counter(point["chr"] for point in result["genome"])
    span_mb = {"1": 2_000 * _STEP / 1e6, "9": 2_000 * _STEP / 1e6, "21": 500 * _STEP / 1e6}
    density = {chrom: per_chromosome[chrom] / span_mb[chrom] for chrom in span_mb}
    assert max(density.values()) - min(density.values()) < 0.1


def test_the_highest_quality_markers_are_kept_within_a_chromosome(result) -> None:
    kept = {(point["chr"], point["start"]) for point in result["genome"] if point["chr"] == "21"}
    best = {(row["chr"], row["start"]) for row in result["rows"] if row["chr"] == "21" and '"qual": 45.0' in row["metadata_json"]}
    # chr21 keeps 111 of its 500 markers; its 50 best-scored ones are among them.
    assert best <= kept


def test_a_window_on_one_chromosome_gets_the_whole_budget(result) -> None:
    window = result["window"]
    assert len(window) == 100
    assert {point["chr"] for point in window} == {"9"}
    assert all(point["start"] <= 20_000_000 for point in window)
