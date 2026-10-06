from __future__ import annotations

import pytest

from backend.app.services import clickhouse_interval_tracks


@pytest.mark.asyncio
async def test_track_presence_applies_region_filter_for_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object] | None, object | None]] = []

    async def fake_execute(query: str, params=None, data=None):
        calls.append((query, params, data))
        if query.lstrip().upper().startswith("CREATE TABLE"):
            return None
        return [("sample-uuid",)]

    monkeypatch.setattr(clickhouse_interval_tracks, "_execute", fake_execute)

    present = await clickhouse_interval_tracks.get_interval_track_presence_by_sample(
        "GRCh38",
        family_uuid="family-uuid",
        sample_uuid_to_name={"sample-uuid": "S1"},
        track_type="coverage",
        chromosomes=["1"],
        start=1000,
        end=2000,
    )

    assert present == {"S1"}
    query, params, _data = calls[-1]
    assert "start <= %(window_end)s AND end >= %(window_start)s" in query
    assert params is not None
    assert params["window_start"] == 1000
    assert params["window_end"] == 2000


@pytest.mark.asyncio
async def test_track_rows_of_some_genes_are_read_by_record_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object] | None]] = []

    async def fake_execute(query: str, params=None, data=None):
        calls.append((query, params))
        return None if query.lstrip().upper().startswith("CREATE TABLE") else []

    monkeypatch.setattr(clickhouse_interval_tracks, "_execute", fake_execute)

    await clickhouse_interval_tracks.fetch_interval_track_rows(
        "GRCh38",
        sample_uuid="sample-uuid",
        track_type="target_coverage",
        chromosomes=[],
        record_ids=["smn1", "PKD1'); DROP TABLE x; --"],
    )

    query, params = calls[-1]
    # Case-insensitive, and bound as a parameter, never written into the SQL.
    assert "upper(record_id) IN %(record_ids)s" in query
    assert "PKD1" not in query
    assert params is not None
    assert params["record_ids"] == ("SMN1", "PKD1'); DROP TABLE X; --")
