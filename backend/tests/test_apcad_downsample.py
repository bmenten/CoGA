"""The APCAD downsample (``clickhouse_interval_tracks.fetch_apcad_downsampled``): the point
budget, and the origins it reads.

The budget (``_apcad_chromosome_budgets``, ``_apcad_band_targets``). APCAD is downsampled
server-side to ~budget points: split first over the chromosomes in proportion to the sites
each holds, so each keeps its own density, then within each chromosome across two BAF bands,
keeping the highest-quality markers in each. The band split must keep the heterozygous
(phasing) signal while leaving the homozygous bands visible — including the case where het is
rare (its full set must survive) and where het is plentiful (the homozygous band must still
get a share).

The origins. The APCAD track answers "which parent did this allele come from", so it normally
draws only ``paternal``/``maternal`` markers. HiFiCNV's minor-allele-fraction bigWig lands on
the same track but carries no parental origin -- bigWig has nowhere to record one -- so every
one of its points is ``und`` and a hard origin filter would render the whole track blank.

The fallback is therefore a property of the *track*, not of the window being drawn. That
distinction is the point of the origin tests: on a genuinely phased track a stretch with no
informative markers renders empty, and that emptiness is the autozygosity signal. Filling it
with ``und`` points would mask exactly what the view exists to show. Markers without a quality
score (a MAF bigWig) are spread across the chromosome instead of taken from its start.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.services import clickhouse_interval_tracks as tracks
from backend.app.services.clickhouse_interval_tracks import _apcad_band_targets, _apcad_chromosome_budgets

# --- The budget ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("het", "homo", "budget", "expected"),
    [
        # Every het marker survives; the rest of the budget goes to homozygous.
        pytest.param(283, 35_000, 9108, (283, 9108 - 283), id="rare het is kept in full"),
        # The homozygous reserve (40% of the budget), the rest het.
        pytest.param(800_000, 700_000, 9000, (9000 - 3600, 3600), id="plentiful het leaves room for homozygous"),
        pytest.param(500_000, 500_000, 6000, (3600, 2400), id="the total stays within the budget"),
        pytest.param(100, 50, 9000, (100, 50), id="a small track keeps everything"),
        # No het: the band is excluded and homozygous takes the whole budget.
        pytest.param(0, 10_000, 5000, (0, 5000), id="an empty band is excluded"),
    ],
)
def test_the_bands_share_a_chromosome_s_budget(het: int, homo: int, budget: int, expected: tuple[int, int]) -> None:
    het_target, homo_target = _apcad_band_targets(het_count=het, homo_count=homo, budget=budget)
    assert (het_target, homo_target) == expected
    assert het_target + homo_target <= budget


@pytest.mark.parametrize(
    ("site_counts", "budget", "expected"),
    [
        pytest.param(
            {"1": 200_000, "9": 120_000, "21": 40_000}, 9000, {"1": 5000, "9": 3000, "21": 1000},
            id="in proportion to their sites",
        ),
        # 444.4, 111.1, 444.4: the point left over goes to the largest remainder, the first of
        # the tied two in the order given.
        pytest.param({"1": 2000, "21": 500, "9": 2000}, 1000, {"1": 445, "21": 111, "9": 444}, id="a tie"),
        pytest.param({"1": 300, "X": 20}, 9000, {"1": 300, "X": 20}, id="a track within the budget keeps every site"),
        pytest.param(
            {"1": 1_000_000, "Y": 3, "M": 0}, 100, {"1": 100}, id="a chromosome without sites or share is left out"
        ),
        pytest.param({}, 100, {}, id="no sites, no budget"),
    ],
)
def test_the_chromosomes_share_the_budget(site_counts: dict[str, int], budget: int, expected: dict[str, int]) -> None:
    budgets = _apcad_chromosome_budgets(site_counts, budget=budget)
    assert budgets == expected
    # The shares add up to the budget, or to every site of a track within it.
    assert sum(budgets.values()) == min(budget, sum(site_counts.values()))


async def _noop(*args: Any, **kwargs: Any) -> None:
    return None


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

    monkeypatch.setattr(tracks, "_execute", fake_execute)
    monkeypatch.setattr(tracks, "ensure_clickhouse_interval_table", _noop)
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


# --- The origins -----------------------------------------------------------------------------


class RecordingClickHouse:
    """Answers the query sequence of ``fetch_apcad_downsampled`` from canned rows."""

    def __init__(self, *, has_phased: bool, het: int, homo: int) -> None:
        self._has_phased = has_phased
        self._het = het
        self._homo = homo
        self.queries: list[str] = []

    async def __call__(self, query: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        self.queries.append(" ".join(query.split()))
        if "LIMIT 1" in query and "SELECT 1" in query:
            return [(1,)] if self._has_phased else []
        if "countIf" in query:
            # Counted per chromosome: no row at all where nothing matches.
            return [("1", self._het, self._homo)] if self._het or self._homo else []
        # A band query: one row is enough to see which origin came back.
        origin = "paternal" if self._has_phased else "und"
        return [("1", 100, 101, 0.5, origin)]

    @property
    def band_queries(self) -> list[str]:
        return [q for q in self.queries if q.startswith("SELECT chrom AS chr")]


async def _run(fake: RecordingClickHouse, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    monkeypatch.setattr(tracks, "_execute", fake)
    monkeypatch.setattr(tracks, "ensure_clickhouse_interval_table", _noop)
    return await tracks.fetch_apcad_downsampled(
        "GRCh38",
        sample_uuid="11111111-1111-1111-1111-111111111111",
        family_uuid="22222222-2222-2222-2222-222222222222",
        chromosomes=["1"],
        origins=["paternal", "maternal"],
        budget=100,
        start=1_000,
        end=2_000,
    )


@pytest.mark.asyncio
async def test_a_track_with_no_phased_markers_shows_its_unphased_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = RecordingClickHouse(has_phased=False, het=60, homo=40)
    rows = await _run(fake, monkeypatch)

    # The HiFiCNV MAF case: every point is `und`, so keeping the origin filter
    # would blank the track entirely.
    assert rows
    assert all("origin IN" not in query for query in fake.band_queries)


@pytest.mark.asyncio
async def test_a_phased_track_keeps_the_origin_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = RecordingClickHouse(has_phased=True, het=60, homo=40)
    rows = await _run(fake, monkeypatch)

    # `und` markers are not phasing-informative; a trio's track must not gain them.
    assert rows
    assert all("origin IN" in query for query in fake.band_queries)


@pytest.mark.asyncio
async def test_a_phased_track_stays_empty_where_no_informative_markers_fall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Phased markers exist on the track, but none in this window.
    fake = RecordingClickHouse(has_phased=True, het=0, homo=0)
    rows = await _run(fake, monkeypatch)

    # The track-scoped probe exists for this case: an empty region on a phased
    # track is the autozygosity signal, not a rendering gap.
    assert rows == []
    assert fake.band_queries == []


@pytest.mark.asyncio
async def test_the_phased_probe_is_a_limit_one_over_the_primary_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = RecordingClickHouse(has_phased=True, het=10, homo=10)
    await _run(fake, monkeypatch)

    probe = next(q for q in fake.queries if q.startswith("SELECT 1"))
    # Scoped to the whole track (family + sample + track_type), deliberately *not*
    # to the requested window, and cheap enough to run per request.
    assert "family_guid" in probe and "sample_guid" in probe
    assert "track_type = 'apcad'" in probe
    assert "LIMIT 1" in probe
    assert "start <=" not in probe and "chrom IN" not in probe


@pytest.mark.asyncio
async def test_no_probe_runs_when_the_caller_asks_for_every_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = RecordingClickHouse(has_phased=True, het=10, homo=10)
    monkeypatch.setattr(tracks, "_execute", fake)
    monkeypatch.setattr(tracks, "ensure_clickhouse_interval_table", _noop)
    await tracks.fetch_apcad_downsampled(
        "GRCh38",
        sample_uuid="11111111-1111-1111-1111-111111111111",
        family_uuid=None,
        chromosomes=["1"],
        origins=None,
        budget=100,
    )

    # Nothing to prefer, nothing to fall back from.
    assert not [q for q in fake.queries if q.startswith("SELECT 1")]


@pytest.mark.asyncio
async def test_markers_without_a_quality_score_are_spread_across_the_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = RecordingClickHouse(has_phased=False, het=60, homo=40)
    await _run(fake, monkeypatch)

    band = fake.band_queries[0]
    # Ranking by quality is only a ranking while there is a quality to rank by. A
    # MAF bigWig records a value per site and nothing else, so every row extracts
    # 0.0 and the ORDER BY becomes a constant -- the LIMIT then keeps whatever
    # ClickHouse read first, which in primary-key order is the start of the
    # chromosome. Measured on the reference family: 2000 points inside 2.1 Mb of
    # chr1's 249 Mb, with the rest of the track blank.
    assert "cityHash64(chrom, start)" in band
    # Quality decides the ranking outright where it exists; the hash only makes
    # the tie order, otherwise arbitrary, reproducible and spatially even.
    assert band.index("qual") < band.index("cityHash64")
