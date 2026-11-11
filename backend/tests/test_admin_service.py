"""The administrator's data service (``admin_service``): the data inventory and its counts, the
ClickHouse storage listing, and the deletion of a sample's data.

The inventory counts each family's variants with one query per assembly, not one per family
and assembly, each family under its own project and a project-less one unscoped; its members
lookup binds the families as one uuid[] parameter. The ClickHouse listing names an
assembly by its dataset key, so a Postgres name with a space is listed, not skipped, and appears
once beside the key ClickHouse derives. A sample's interval track is counted and deleted per
caller; a source the delete cannot scope to is refused before anything is locked, read or
written.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
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
    # parameter per family failed past the 32,767 asyncpg takes.
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


# --------------------------------------------------------------------------- the data inventory


@pytest.mark.asyncio
async def test_list_data_inventory_page_batches_counts_per_assembly(monkeypatch) -> None:
    family_rows = [
        {
            "family_uuid": "fam-1",
            "family_id": "F1",
            "project_ids": ["p1"],
            "assembly_names": ["GRCh38", "GRCh37"],
            "sample_count": 2,
            "metadata": {},
        },
        {
            "family_uuid": "fam-2",
            "family_id": "F2",
            "project_ids": ["p2"],
            "assembly_names": ["GRCh38"],
            "sample_count": 1,
            "metadata": {},
        },
        # A project-less family: counted unscoped.
        {
            "family_uuid": "fam-3",
            "family_id": "F3",
            "project_ids": [],
            "assembly_names": ["GRCh38"],
            "sample_count": 1,
            "metadata": {},
        },
    ]

    async def fake_family_rows(session, *, search=None, page=1, page_size=50):
        return (3, family_rows)

    small_by_assembly = {
        "GRCh38": {"fam-1": 10, "fam-2": 7, "fam-3": 9},
        "GRCh37": {"fam-1": 5},
    }
    sv_by_assembly = {
        "GRCh38": {"fam-1": 1, "fam-2": 3},
        "GRCh37": {"fam-1": 2},
    }
    small_calls: list[tuple] = []
    sv_calls: list[tuple] = []

    async def fake_small(assembly_name, *, family_project_pairs, families_without_project=()):
        small_calls.append((assembly_name, tuple(family_project_pairs), tuple(families_without_project)))
        return dict(small_by_assembly.get(assembly_name, {}))

    async def fake_sv(assembly_name, *, family_project_pairs, families_without_project=()):
        sv_calls.append((assembly_name, tuple(family_project_pairs), tuple(families_without_project)))
        return dict(sv_by_assembly.get(assembly_name, {}))

    async def fake_interval(session, uuids):
        return {}

    async def fake_repeat(session, uuids):
        return {}

    monkeypatch.setattr(admin_service, "_family_rows", fake_family_rows)
    monkeypatch.setattr(admin_service, "count_family_small_variants_by_family", fake_small)
    monkeypatch.setattr(admin_service, "count_family_structural_variants_by_family", fake_sv)
    monkeypatch.setattr(admin_service, "_interval_counts_by_family", fake_interval)
    monkeypatch.setattr(admin_service, "_repeat_counts_by_family", fake_repeat)

    page = await admin_service.list_data_inventory_page(None, page=1, page_size=50)

    assert page.total == 3
    by_family = {item.family_id: item for item in page.items}
    # Counts summed across each family's assemblies.
    assert by_family["F1"].track_counts["small_variants"] == 15  # 10 + 5
    assert by_family["F1"].track_counts["structural_variants"] == 3  # 1 + 2
    assert by_family["F2"].track_counts["small_variants"] == 7
    assert by_family["F2"].track_counts["structural_variants"] == 3
    assert by_family["F3"].track_counts["small_variants"] == 9
    assert by_family["F3"].track_counts["structural_variants"] == 0

    # One query per assembly, not per (family, assembly).
    assert len(small_calls) == 2
    assert {call[0] for call in small_calls} == {"GRCh38", "GRCh37"}
    grch38 = next(call for call in small_calls if call[0] == "GRCh38")
    # GRCh38 pairs carry each family's own project; fam-3 (no projects) is unscoped.
    assert ("fam-1", "p1") in grch38[1]
    assert ("fam-2", "p2") in grch38[1]
    assert grch38[2] == ("fam-3",)


# --------------------------------------------------------------------------- the ClickHouse listing


class _AssemblyNamesSession:
    """Answers the listing's read of the Postgres assemblies with ``rows``, one name each."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(all=lambda: self._rows)


def _status_dict(name: str) -> dict:
    return {
        "assembly_name": name,
        "health": "ready",
        "expected_table_count": 12,
        "existing_table_count": 12,
        "missing_tables": [],
        "pending_mutations": 0,
        "total_rows": 0,
        "total_bytes_on_disk": 0,
        "small_variant_rows": 0,
        "structural_variant_rows": 0,
        "tables": [],
    }


def _patch_clickhouse_listing(monkeypatch, ch_keys):
    async def _ch():
        return set(ch_keys)

    async def _status(name):
        return _status_dict(name)

    monkeypatch.setattr(admin_service, "list_clickhouse_variant_assemblies", _ch)
    monkeypatch.setattr(admin_service, "get_clickhouse_variant_storage_status", _status)


def test_list_normalizes_assembly_names_for_clickhouse(monkeypatch) -> None:
    # "T2T CHM13v2.0" (a space) is normalized to its dataset key and listed,
    # not skipped and not a 500.
    _patch_clickhouse_listing(monkeypatch, ch_keys=set())
    session = _AssemblyNamesSession([("GRCh38",), ("T2T CHM13v2.0",)])
    result = asyncio.run(admin_service.list_clickhouse_variant_status(session))
    assert sorted(a.assembly_name for a in result.assemblies) == ["GRCh38", "T2T_CHM13v2.0"]


def test_list_dedups_pg_name_against_clickhouse_key(monkeypatch) -> None:
    # The Postgres name and the ClickHouse-derived key both reduce to the same
    # dataset key, so the assembly appears once.
    _patch_clickhouse_listing(monkeypatch, ch_keys={"T2T_CHM13v2.0"})
    session = _AssemblyNamesSession([("T2T CHM13v2.0",)])
    result = asyncio.run(admin_service.list_clickhouse_variant_status(session))
    assert [a.assembly_name for a in result.assemblies] == ["T2T_CHM13v2.0"]
