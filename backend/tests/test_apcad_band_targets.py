"""Tests for the APCAD downsample budget allocation (_apcad_chromosome_budgets,
_apcad_band_targets).

APCAD is downsampled server-side to ~budget points: split first over the chromosomes in
proportion to the sites each holds, so each keeps its own density, then within each
chromosome across two BAF bands, keeping the highest-quality markers in each. The band
split must keep the heterozygous (phasing) signal while leaving the homozygous bands
visible — including the case where het is rare (its full set must survive) and where het
is plentiful (the homozygous band must still get a share).
"""
from typing import Any

import pytest

from app.services import clickhouse_interval_tracks as tracks
from app.services.clickhouse_interval_tracks import _apcad_band_targets, _apcad_chromosome_budgets


def test_rare_het_is_kept_in_full():
    het_target, homo_target = _apcad_band_targets(het_count=283, homo_count=35_000, budget=9108)
    assert het_target == 283  # every het marker survives
    assert homo_target == 9108 - 283  # the rest goes to homozygous


def test_plentiful_het_still_leaves_room_for_homozygous():
    het_target, homo_target = _apcad_band_targets(het_count=800_000, homo_count=700_000, budget=9000)
    assert homo_target == 9000 * 2 // 5  # homozygous reserve (40%)
    assert het_target == 9000 - homo_target
    assert het_target + homo_target <= 9000


def test_total_stays_within_budget():
    het_target, homo_target = _apcad_band_targets(het_count=500_000, homo_count=500_000, budget=6000)
    assert het_target + homo_target <= 6000


def test_small_track_keeps_everything():
    het_target, homo_target = _apcad_band_targets(het_count=100, homo_count=50, budget=9000)
    assert het_target == 100 and homo_target == 50


def test_empty_band_is_excluded():
    het_target, homo_target = _apcad_band_targets(het_count=0, homo_count=10_000, budget=5000)
    assert het_target == 0  # no het -> excluded
    assert homo_target == 5000


def test_chromosomes_share_the_budget_in_proportion_to_their_sites():
    budgets = _apcad_chromosome_budgets({"1": 200_000, "9": 120_000, "21": 40_000}, budget=9000)
    assert budgets == {"1": 5000, "9": 3000, "21": 1000}


def test_chromosome_shares_add_up_to_the_budget():
    budgets = _apcad_chromosome_budgets({"1": 2000, "21": 500, "9": 2000}, budget=1000)
    # 444.4, 111.1, 444.4: the point left over goes to the largest remainder, the first of
    # the tied two in the order given.
    assert budgets == {"1": 445, "21": 111, "9": 444}
    assert sum(budgets.values()) == 1000


def test_a_track_within_the_budget_keeps_every_site():
    assert _apcad_chromosome_budgets({"1": 300, "X": 20}, budget=9000) == {"1": 300, "X": 20}


def test_a_chromosome_without_sites_or_share_is_left_out():
    budgets = _apcad_chromosome_budgets({"1": 1_000_000, "Y": 3, "M": 0}, budget=100)
    assert budgets == {"1": 100}


def test_no_sites_no_budget():
    assert _apcad_chromosome_budgets({}, budget=100) == {}


@pytest.mark.asyncio
async def test_each_chromosome_is_ranked_within_its_own_share(monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(query: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        queries.append((" ".join(query.split()), dict(params)))
        if "SELECT 1" in query:
            return [(1,)]
        if "countIf" in query:
            # chr9's markers scored higher by joint calling; its sites are as many as chr1's.
            return [("1", 1200, 800), ("9", 1200, 800)]
        return []

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(tracks, "_execute", fake_execute)
    monkeypatch.setattr(tracks, "ensure_clickhouse_interval_table", noop)
    await tracks.fetch_apcad_downsampled(
        "GRCh38",
        sample_uuid="11111111-1111-1111-1111-111111111111",
        chromosomes=["1", "9"],
        origins=["paternal", "maternal"],
        budget=1000,
    )

    counts_query = next(query for query, _ in queries if "countIf" in query)
    assert "GROUP BY chrom" in counts_query
    union, params = queries[-1]
    bands = union.split(" UNION ALL ")
    # Two chromosomes, two bands each; 500 points per chromosome, 200 of them homozygous.
    assert len(bands) == 4
    assert {params["apcad_chrom_0"], params["apcad_chrom_1"]} == {"1", "9"}
    for index in (0, 1):
        own = [band for band in bands if f"chrom = %(apcad_chrom_{index})s" in band]
        assert [band.rsplit("LIMIT ", 1)[1] for band in own] == ["300", "200"]
