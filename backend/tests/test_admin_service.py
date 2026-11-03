from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import HTTPException

from backend.app.services import admin_service
from backend.app.services.family_metadata_context import FamilyMetadataContext


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


class _RecordingSession:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.sql: str | None = None
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return _FakeExecuteResult(self.rows)


@pytest.mark.asyncio
async def test_assembly_project_rows_orders_by_selected_alias() -> None:
    session = _RecordingSession(
        [
            {
                "project_id": "project-1",
                "assembly_id": "assembly-1",
                "assembly_name": "GRCh38",
            }
        ]
    )

    rows = await admin_service._assembly_project_rows(session, "family-uuid")

    assert rows == [
        {
            "project_id": "project-1",
            "assembly_id": "assembly-1",
            "assembly_name": "GRCh38",
        }
    ]
    assert session.sql is not None
    assert "SELECT DISTINCT" in session.sql
    assert "ORDER BY a.assembly_name, project_id" in session.sql
    assert session.params == {"family_uuid": "family-uuid"}


@pytest.mark.asyncio
async def test_sample_rows_by_family_uses_uuid_family_filter() -> None:
    family_uuid = "11111111-1111-4111-8111-111111111111"
    session = _RecordingSession(
        [
            {
                "family_uuid": family_uuid,
                "sample_uuid": "sample-1",
                "sample_id": "proband",
                "sex": "female",
                "role": "proband",
                "affected": True,
            }
        ]
    )

    rows = await admin_service._sample_rows_by_family(session, [family_uuid])

    assert rows[family_uuid][0]["sample_id"] == "proband"
    assert session.sql is not None
    # The families go as one uuid[] parameter, compared with the uuid column itself: one
    # parameter per family failed past the 32,767 asyncpg takes (DATA-6).
    assert "fm.family_id = ANY(CAST(:family_uuids AS uuid[]))" in session.sql
    assert "POSTCOMPILE" not in session.sql
    assert "::text = ANY" not in session.sql
    assert session.params == {"family_uuids": [UUID(family_uuid)]}


@pytest.mark.asyncio
async def test_structural_sample_counts_uses_clickhouse_aggregate_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_count_by_sample(assembly_name, family_uuid, *, sample_ids, project_ids=None, source=None):
        assert assembly_name == "GRCh38"
        assert family_uuid == "family-uuid"
        assert list(sample_ids) == ["son", "mother"]
        assert project_ids == ["project-uuid"]
        return {"son": 4, "mother": 2}

    monkeypatch.setattr(
        admin_service,
        "count_family_structural_variants_by_sample",
        fake_count_by_sample,
    )

    counts = await admin_service._structural_sample_counts(
        [
            FamilyMetadataContext(
                family_uuid="family-uuid",
                family_id="demo_family",
                project_ids=["project-uuid"],
                sample_rows=[],
                sample_uuid_to_name={"sample-son": "son", "sample-mother": "mother"},
                sample_name_to_uuid={"son": "sample-son", "mother": "sample-mother"},
                affected_sample_names=[],
                assembly_id="assembly-uuid",
                assembly_name="GRCh38",
            )
        ]
    )

    assert counts == {"sample-son": 4, "sample-mother": 2}


@pytest.mark.asyncio
async def test_interval_source_counts_by_sample_counts_each_callers_track() -> None:
    sample_uuid = "22222222-2222-4222-8222-222222222222"
    session = _RecordingSession(
        [
            {"sample_uuid": sample_uuid, "track_type": "coverage", "source": "wisecondorx", "count": 3},
            {"sample_uuid": sample_uuid, "track_type": "coverage", "source": "qdnaseq", "count": 4},
            {"sample_uuid": sample_uuid, "track_type": "segments", "source": "hificnv", "count": 1},
        ]
    )

    counts = await admin_service.interval_source_counts_by_sample(session, [sample_uuid])

    # A long-read sample's coverage from two callers: each is counted, and deleted, on its own.
    assert counts == {sample_uuid: {"coverage": {"wisecondorx": 3, "qdnaseq": 4}, "segments": {"hificnv": 1}}}
    assert session.sql is not None
    assert "GROUP BY sample_id, track_type, source" in session.sql
    assert "sample_id = ANY(CAST(:sample_uuids AS uuid[]))" in session.sql
    assert session.params == {"sample_uuids": [UUID(sample_uuid)]}


class _CommitSession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


def _patch_sample_track_delete(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """The sample-track delete with its reads and writes recorded instead of run."""
    calls: dict[str, list] = {"clickhouse": [], "postgres": [], "locks": []}
    sample_row = {"sample_uuid": "sample-uuid", "sample_id": "PROBAND", "family_uuid": "family-uuid", "family_id": "FAM"}

    async def fake_sample_row(session, sample_id):
        return dict(sample_row)

    async def fake_lock(session, family_uuid, variant_types, *, samples=None):
        calls["locks"].append((family_uuid, tuple(variant_types)))

    async def fake_sample_rows(session, family_uuids):
        return {"family-uuid": []}

    async def fake_contexts(session, *, family_uuid, family_id, sample_rows):
        return [
            FamilyMetadataContext(
                family_uuid=family_uuid,
                family_id=family_id,
                project_ids=["project-uuid"],
                sample_rows=[],
                sample_uuid_to_name={},
                sample_name_to_uuid={},
                affected_sample_names=[],
                assembly_id="assembly-uuid",
                assembly_name="GRCh38",
            )
        ]

    async def fake_delete_tracks(assembly_name, **filters):
        calls["clickhouse"].append((assembly_name, filters))

    async def fake_delete_sources(session, **filters):
        calls["postgres"].append(filters)
        return 3

    monkeypatch.setattr(admin_service, "_sample_row_or_404", fake_sample_row)
    monkeypatch.setattr(admin_service, "lock_family_variant_writes", fake_lock)
    monkeypatch.setattr(admin_service, "_sample_rows_by_family", fake_sample_rows)
    monkeypatch.setattr(admin_service, "_family_assembly_contexts", fake_contexts)
    monkeypatch.setattr(admin_service, "delete_interval_tracks", fake_delete_tracks)
    monkeypatch.setattr(admin_service, "delete_interval_track_sources", fake_delete_sources)
    return calls


@pytest.mark.asyncio
async def test_delete_sample_track_with_a_source_deletes_only_that_callers_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_sample_track_delete(monkeypatch)
    session = _CommitSession()

    result = await admin_service.delete_sample_data_by_type(
        session, "PROBAND", "coverage", True, source="wisecondorx"
    )

    assert result == {"deleted": 3}
    assert session.committed
    # The rows in ClickHouse and Postgres's record of them: WisecondorX's coverage alone, under
    # the family's write lock.
    assert calls["clickhouse"] == [
        ("GRCh38", {"sample_uuid": "sample-uuid", "track_type": "coverage", "source": "wisecondorx"})
    ]
    assert calls["postgres"] == [{"sample_uuid": "sample-uuid", "track_type": "coverage", "source": "wisecondorx"}]
    assert [family_uuid for family_uuid, _types in calls["locks"]] == ["family-uuid"]


@pytest.mark.asyncio
async def test_delete_sample_track_without_a_source_deletes_every_callers_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_sample_track_delete(monkeypatch)

    await admin_service.delete_sample_data_by_type(_CommitSession(), "PROBAND", "segments", True)

    assert calls["clickhouse"] == [("GRCh38", {"sample_uuid": "sample-uuid", "track_type": "segments", "source": None})]
    assert calls["postgres"] == [{"sample_uuid": "sample-uuid", "track_type": "segments", "source": None}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data_type", "source", "detail"),
    [
        ("structural_variants", "sniffles", "Only an interval track is deleted per source"),
        ("repeat_expansions", "trgt", "Only an interval track is deleted per source"),
        ("coverage", "", "Invalid source"),
        ("coverage", " wisecondorx", "Invalid source"),
    ],
)
async def test_delete_sample_data_refuses_a_source_it_cannot_scope_to(
    monkeypatch: pytest.MonkeyPatch, data_type: str, source: str, detail: str
) -> None:
    calls = _patch_sample_track_delete(monkeypatch)
    session = _CommitSession()

    with pytest.raises(HTTPException) as refused:
        await admin_service.delete_sample_data_by_type(session, "PROBAND", data_type, True, source=source)

    assert (refused.value.status_code, refused.value.detail) == (400, detail)
    # Refused before any lock, read or write: nothing of the sample's data goes.
    assert calls == {"clickhouse": [], "postgres": [], "locks": []}
    assert not session.committed
