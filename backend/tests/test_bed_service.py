import pytest

from backend.app.services import bed_service
from backend.app.services.family_metadata_context import SampleMetadataContext


def _ctx() -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid="s-uuid",
        sample_id="s",
        family_uuid="f-uuid",
        family_id="fam",
        sex="female",
        project_ids=["p"],
        assembly_id="a-uuid",
        assembly_name="GRCh38",
    )


def _patch_fetch(monkeypatch, rows):
    calls: list[dict] = []

    async def fake_fetch(assembly_name, **kwargs):
        calls.append({"assembly_name": assembly_name, **kwargs})
        return rows

    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", fake_fetch)
    return calls


@pytest.mark.asyncio
async def test_fetch_bed_records_for_chroms_uses_one_query_and_groups_by_chrom(monkeypatch) -> None:
    rows = [
        {"chr": "1", "start": 10, "end": 20, "value": 1.0, "origin": "und"},
        {"chr": "1", "start": 30, "end": 40, "value": 2.0, "origin": "und"},
        {"chr": "2", "start": 5, "end": 8, "value": 3.0, "origin": "und"},
        # Mitochondrion stored as "M" while the request used "MT".
        {"chr": "M", "start": 1, "end": 2, "value": 4.0, "origin": "und"},
    ]
    calls = _patch_fetch(monkeypatch, rows)

    records = await bed_service._fetch_bed_records_for_chroms(
        None,
        sample_context=_ctx(),
        bed_type="segments",
        chroms=["1", "2", "MT"],
        window=None,
        start=None,
        end=None,
        limit=100,
    )

    # One ClickHouse query for every requested chromosome, no DB-side limit.
    assert len(calls) == 1
    assert calls[0]["chromosomes"] == ["1", "2", "MT"]
    assert "limit" not in calls[0] or calls[0]["limit"] is None
    # Grouped in request order; the "MT" bucket matched the stored "M" alias.
    assert [r["chr"] for r in records] == ["1", "1", "2", "M"]


@pytest.mark.asyncio
async def test_fetch_bed_records_for_chroms_applies_per_chromosome_limit(monkeypatch) -> None:
    rows = [
        {"chr": "1", "start": 10, "end": 20, "value": 1.0, "origin": "und"},
        {"chr": "1", "start": 30, "end": 40, "value": 2.0, "origin": "und"},
        {"chr": "2", "start": 5, "end": 8, "value": 3.0, "origin": "und"},
    ]
    _patch_fetch(monkeypatch, rows)

    records = await bed_service._fetch_bed_records_for_chroms(
        None,
        sample_context=_ctx(),
        bed_type="segments",
        chroms=["1", "2"],
        window=None,
        start=None,
        end=None,
        limit=1,
    )

    # limit is applied per chromosome (chrom 1's two rows truncated to one).
    assert [r["chr"] for r in records] == ["1", "2"]
    assert [r["start"] for r in records] == [10, 5]


# NOTE: APCAD window downsampling moved entirely into ClickHouse
# (`fetch_apcad_downsampled`); the former Python `_windowed_apcad_rows` helper and
# its `fetch_interval_track_rows`-backed window path were removed, so the two
# tests that exercised them are gone. Coverage windowing (`_windowed_coverage_rows`)
# is still covered by the tests above.
