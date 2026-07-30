"""When the APCAD downsample falls back from phased markers to unphased ones.

The APCAD track answers "which parent did this allele come from", so it normally
draws only ``paternal``/``maternal`` markers. HiFiCNV's minor-allele-fraction
bigWig lands on the same track but carries no parental origin -- bigWig has
nowhere to record one -- so every one of its points is ``und`` and a hard origin
filter would render the whole track blank.

The fallback is therefore a property of the *track*, not of the window being
drawn. That distinction is the point of this module: on a genuinely phased track
a stretch with no informative markers renders empty, and that emptiness is the
autozygosity signal. Filling it with ``und`` points would mask exactly what the
view exists to show.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import clickhouse_interval_tracks as tracks


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
            return [(self._het, self._homo)]
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


async def _noop(*args: Any, **kwargs: Any) -> None:
    return None


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

    # This is the regression the track-scoped probe exists to prevent: an empty
    # region on a phased track is the autozygosity signal, not a rendering gap.
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
    # Quality still decides the ranking outright where it exists; the hash only
    # makes the previously arbitrary tie order reproducible and spatially even.
    assert band.index("qual") < band.index("cityHash64")
