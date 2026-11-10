"""The interval-track storage (``clickhouse_interval_tracks``): what it sends ClickHouse and
Postgres, against fakes of both. A track's presence and rows are read by region and by record
id, every value bound, the per-row metadata only when asked for; a delete is synchronous and
never unfiltered, in either store; a row that holds no position is left out of an insert, and
an insert left without rows is not sent; a read about no family or sample asks nothing."""

from __future__ import annotations

from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_a_track_delete_is_synchronous_bound_and_never_unfiltered(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, dict[str, object] | None]] = []

    async def fake_execute(query: str, params=None, data=None):
        sent.append((" ".join(query.split()), params))
        return None

    monkeypatch.setattr(clickhouse_interval_tracks, "_execute", fake_execute)

    # Without a filter it would delete every track of the assembly: refused before it is sent.
    with pytest.raises(ValueError, match="At least one interval-track delete filter is required"):
        await clickhouse_interval_tracks.delete_interval_tracks("GRCh38")
    assert not [query for query, _params in sent if query.startswith("ALTER TABLE")]

    await clickhouse_interval_tracks.delete_interval_tracks(
        "GRCh38", sample_uuid="uuid-S1", track_type="coverage", source="web", filename="S1.bed"
    )

    query, params = sent[-1]
    # Returns once the rows are gone (AGENTS.md, "Deletes are synchronous"), every value bound.
    assert query == (
        "ALTER TABLE coga.`GRCh38/INTERVAL/entries` DELETE WHERE sample_guid = %(sample_uuid)s "
        "AND track_type = %(track_type)s AND source = %(source)s AND filename = %(filename)s "
        "SETTINGS mutations_sync = 1"
    )
    assert params == {"sample_uuid": "uuid-S1", "track_type": "coverage", "source": "web", "filename": "S1.bed"}


@pytest.mark.asyncio
async def test_a_track_record_delete_without_a_filter_is_refused() -> None:
    # Postgres's record of every track would go with it.
    statements: list[str] = []

    class _Session:
        async def execute(self, statement, params=None):
            statements.append(" ".join(str(statement).split()))
            return SimpleNamespace(scalar_one=lambda: 0)

    with pytest.raises(ValueError, match="At least one interval-track source delete filter is required"):
        await clickhouse_interval_tracks.delete_interval_track_sources(_Session())  # type: ignore[arg-type]
    assert not [sql for sql in statements if sql.startswith("DELETE")]


@pytest.mark.asyncio
async def test_no_track_rows_send_no_insert_and_a_row_without_a_position_is_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted: list[list[tuple[object, ...]]] = []

    async def execute_clickhouse(query: str, payload=None):
        if isinstance(payload, list):
            inserted.append(payload)
        return None

    monkeypatch.setattr(clickhouse_interval_tracks, "execute_clickhouse", execute_clickhouse)

    await clickhouse_interval_tracks.insert_interval_track_rows("GRCh38", [])
    assert inserted == []

    row = {"family_id": "family-uuid", "sample_id": "uuid-S1", "track_type": "coverage", "chr": "chr1", "value": 1.5}
    # Rows none of which holds a position: nothing left to send.
    await clickhouse_interval_tracks.insert_interval_track_rows(
        "GRCh38", [{**row, "start": -5, "end": 1000}, {**row, "start": 1000, "end": None}]
    )
    assert inserted == []

    await clickhouse_interval_tracks.insert_interval_track_rows(
        "GRCh38",
        [{**row, "start": 0, "end": 1000}, {**row, "start": -5, "end": 1000}, {**row, "start": 1000, "end": None}],
    )
    [rows] = inserted
    assert [(stored[5], stored[6], stored[7]) for stored in rows] == [("1", 0, 1000)]
    # A file name kept in the row's metadata is the row's file name; metadata that is no
    # JSON names none.
    await clickhouse_interval_tracks.insert_interval_track_rows(
        "GRCh38",
        [
            {**row, "start": 0, "end": 10, "metadata_json": '{"filename": "S1.bed"}'},
            {**row, "start": 0, "end": 10, "metadata_json": "{not json"},
        ],
    )
    assert [stored[4] for stored in inserted[-1]] == ["S1.bed", ""]


@pytest.mark.asyncio
async def test_a_tracks_row_metadata_is_read_only_when_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    # The per-row metadata (a JSON blob per row) is read for the per-target coverage table
    # alone: streaming it for a whole-genome APCAD track moved megabytes for nothing.
    queries: list[str] = []
    stored = ("uuid-S1", "1", 100, 200, "SMN1", 30.0, None, None, None, None)

    async def fake_execute(query: str, params=None, data=None):
        queries.append(" ".join(query.split()))
        if query.lstrip().upper().startswith("CREATE TABLE"):
            return None
        return [(*stored, '{"median": 31}')] if "metadata_json FROM" in queries[-1] else [stored]

    monkeypatch.setattr(clickhouse_interval_tracks, "_execute", fake_execute)

    [plain] = await clickhouse_interval_tracks.fetch_interval_track_rows(
        "GRCh38", sample_uuid="uuid-S1", track_type="target_coverage", chromosomes=["1"]
    )
    assert "metadata_json" not in queries[-1] and "metadata_json" not in plain
    [with_metadata] = await clickhouse_interval_tracks.fetch_interval_track_rows(
        "GRCh38", sample_uuid="uuid-S1", track_type="target_coverage", chromosomes=["1"], include_metadata=True
    )
    assert "ps, metadata_json FROM" in queries[-1]
    assert with_metadata == {**plain, "metadata_json": '{"median": 31}'}
    assert (plain["sample_uuid"], plain["record_id"], plain["value"]) == ("uuid-S1", "SMN1", 30.0)


@pytest.mark.asyncio
async def test_a_tracks_sources_are_named_per_family_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute(query: str, params=None, data=None):
        if query.lstrip().upper().startswith("CREATE TABLE"):
            return None
        return [
            ("uuid-S1", "wisecondorx"),
            ("uuid-S1", "hificnv"),
            ("uuid-S1", "hificnv"),
            ("uuid-S2", ""),  # no source: not a track of its own
            ("uuid-OTHER", "qdnaseq"),  # not a sample of the family asked about
        ]

    monkeypatch.setattr(clickhouse_interval_tracks, "_execute", fake_execute)

    sources = await clickhouse_interval_tracks.get_interval_track_sources_by_sample(
        "GRCh38",
        family_uuid="family-uuid",
        sample_uuid_to_name={"uuid-S1": "S1", "uuid-S2": "S2"},
        track_type="coverage",
        chromosomes=[],
    )

    assert sources == {"S1": ["hificnv", "wisecondorx"]}


class _NoSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("nothing is asked of Postgres about no family or sample")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("read", "nothing"),
    [
        (
            lambda: clickhouse_interval_tracks.get_interval_track_sources_by_sample(
                "GRCh38", family_uuid="family-uuid", sample_uuid_to_name={}, track_type="coverage", chromosomes=[]
            ),
            {},
        ),
        (
            lambda: clickhouse_interval_tracks.get_interval_track_presence_by_sample(
                "GRCh38", family_uuid="family-uuid", sample_uuid_to_name={}, track_type="coverage", chromosomes=[]
            ),
            set(),
        ),
        (lambda: clickhouse_interval_tracks.interval_counts_by_family(_NoSession(), [], ["coverage"]), {}),
        (lambda: clickhouse_interval_tracks.interval_counts_by_sample(_NoSession(), [], ["coverage"]), {}),
        (lambda: clickhouse_interval_tracks.interval_source_counts_by_sample(_NoSession(), []), {}),
        (
            lambda: clickhouse_interval_tracks.interval_track_normalizations_by_sample(
                _NoSession(), family_uuid="family-uuid", sample_uuid_to_name={}, track_type="coverage"
            ),
            {},
        ),
    ],
    ids=["sources", "presence", "counts-by-family", "counts-by-sample", "source-counts", "normalizations"],
)
async def test_a_track_read_about_no_family_or_sample_asks_nothing(monkeypatch: pytest.MonkeyPatch, read, nothing) -> None:
    async def no_query(*_args, **_kwargs):
        raise AssertionError("nothing is asked of ClickHouse about no sample")

    monkeypatch.setattr(clickhouse_interval_tracks, "_execute", no_query)

    assert await read() == nothing
