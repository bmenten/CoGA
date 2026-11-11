"""A family's variants follow it from project to project (``family_project_variants``).

ClickHouse stores a family's small and structural variant rows once per project it is linked
to, and the family is read through its projects only. A change of its projects copies its rows
to each project it is added to before the new links commit, and removes those under the
projects it left once they have; a project on another assembly is refused. A family moved to
another project shows its variants there.

Over a fake ClickHouse (the storage module's ``_execute``) and a fake Postgres session:

* per callset, the rows of a linked project are copied (the most rows among the linked
  projects), with every entries column and the new project in place of the old one;
* a project the family is not linked to yet has whatever it holds replaced, even with as many
  rows as the origin: no write has kept them current;
* a linked project's rows are never deleted by the copy; a linked project without a callset
  gets it (a project an import added);
* the copy refreshes the small-variant summaries or moves the SV data version only for what
  it wrote; the prune deletes only rows outside the kept projects;
* a family without a project keeps its rows under ``unassigned``, as the writers store them;
* a project on another assembly is refused with 409 before anything is written, and the
  change runs copy, commit, then prune, in its own transaction;
* a family in no project, whose assembly no project names, is looked for on every assembly:
  a project on another one is refused, and the copy and prune run where its rows are.

The values are synthetic.
"""

from __future__ import annotations

from typing import Any

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.core.config import settings
from backend.app.services import clickhouse_variant_storage as storage
from backend.app.services import family_package_import as package_import
from backend.app.services import family_package_registration as registration
from backend.app.services import family_project_variants, family_structure_service, metadata_service
from backend.app.services.family_package_common import ImportRefused, PackageExecutionResult, PackageManifest
from backend.app.services.family_package_manifest import _parse_ped_text_strict
from backend.tests._family_import_fakes import admin_user, two_sample_context
from backend.tests._kept_manifest import kept_manifest

ASSEMBLY = "GRCh38"
FAMILY = "00000000-0000-4000-8000-0000000000f1"
A = "00000000-0000-4000-8000-00000000000a"
B = "00000000-0000-4000-8000-00000000000b"
C = "00000000-0000-4000-8000-00000000000c"


class _FakeClickHouse:
    """Answers the copy's and prune's reads from ``stored`` and records every statement."""

    def __init__(self, small: dict[str, dict[str, int]], structural: dict[str, dict[str, int]]) -> None:
        self.stored = {"SNV_INDEL": small, "SV": structural}
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.refreshed = 0
        self.bumped = 0

    @staticmethod
    def _kind(query: str) -> str:
        return "SNV_INDEL" if "/SNV_INDEL/" in query else "SV"

    async def execute(self, query: str, params: dict[str, Any] | None = None, data: Any = None) -> Any:
        params = params or {}
        self.statements.append((query, params))
        stored = self.stored[self._kind(query)]
        if query.startswith("SELECT source, project_guid, count()"):
            return [(source, project, count) for source, projects in stored.items() for project, count in projects.items()]
        if query.startswith("SELECT 1") and "NOT IN" in query:
            keep = set(params["keep"])
            return [(1,)] if any(project not in keep for projects in stored.values() for project in projects) else []
        return None

    def writes(self) -> list[tuple[str, str, dict[str, Any]]]:
        """(kind, verb, params) of every write: DELETE or INSERT."""
        out = []
        for query, params in self.statements:
            if query.startswith("ALTER TABLE"):
                out.append((self._kind(query), "DELETE", params))
            elif query.startswith("INSERT INTO"):
                out.append((self._kind(query), "INSERT", params))
        return out


@pytest.fixture
def clickhouse(monkeypatch):
    def make(small: dict[str, dict[str, int]] | None = None, structural: dict[str, dict[str, int]] | None = None):
        fake = _FakeClickHouse(small or {}, structural or {})

        async def ensure(_assembly: str) -> None:
            return None

        async def refresh(_assembly: str, _family: str) -> None:
            fake.refreshed += 1

        async def bump(_assembly: str, _family: str) -> None:
            fake.bumped += 1

        monkeypatch.setattr(storage, "_execute", fake.execute)
        monkeypatch.setattr(storage, "ensure_clickhouse_variant_tables", ensure)
        monkeypatch.setattr(storage, "refresh_family_small_variant_summaries", refresh)
        monkeypatch.setattr(storage, "bump_family_structural_variant_data_version", bump)
        return fake

    return make


async def _copy(project_ids: list[str], linked: list[str]) -> bool:
    return await storage.copy_family_variants_to_projects(
        ASSEMBLY, FAMILY, project_ids=project_ids, linked_project_ids=linked
    )


@pytest.mark.asyncio
async def test_a_project_the_family_is_added_to_gets_every_callsets_rows(clickhouse) -> None:
    fake = clickhouse({"clair3": {A: 10}, "glimpse2": {A: 4}}, {"needlr": {A: 3}})

    assert await _copy([B], [A]) is True

    assert [(kind, verb, params["source"], params["origin"], params["project"]) for kind, verb, params in fake.writes()] == [
        ("SNV_INDEL", "INSERT", "clair3", A, B),
        ("SNV_INDEL", "INSERT", "glimpse2", A, B),
        ("SV", "INSERT", "needlr", A, B),
    ]
    assert (fake.refreshed, fake.bumped) == (1, 1)


@pytest.mark.asyncio
async def test_the_copy_writes_every_entries_column_with_the_new_project(clickhouse) -> None:
    fake = clickhouse({"clair3": {A: 10}}, {"needlr": {A: 3}})

    await _copy([B], [A])

    inserts = {fake._kind(query): query for query, _ in fake.statements if query.startswith("INSERT INTO")}
    for kind, columns in (
        ("SNV_INDEL", storage.SMALL_VARIANT_ENTRY_COLUMNS),
        ("SV", storage.STRUCTURAL_VARIANT_ENTRY_COLUMNS),
    ):
        listed, selected = inserts[kind].split(" SELECT ", 1)
        assert listed.endswith("(" + ", ".join(f"`{column}`" for column in columns) + ")")
        expected = ", ".join("%(project)s" if column == "project_guid" else f"`{column}`" for column in columns)
        assert selected.startswith(expected + " FROM ")
        assert "project_guid = %(origin)s AND source = %(source)s AND sign = 1" in selected


@pytest.mark.asyncio
async def test_rows_left_under_a_project_being_added_are_replaced_even_when_as_many(clickhouse) -> None:
    # B holds as many rows as A, left from a change that did not finish: no write kept them
    # current since, so they are deleted and copied again.
    fake = clickhouse({"clair3": {A: 10, B: 10}})

    await _copy([A, B], [A])

    assert [(verb, params["project"]) for _kind, verb, params in fake.writes()] == [("DELETE", B), ("INSERT", B)]
    assert fake.writes()[1][2]["origin"] == A


@pytest.mark.asyncio
async def test_a_linked_projects_rows_are_never_deleted(clickhouse) -> None:
    # A and B are linked; B has fewer rows. The copy to C comes from A, the linked project
    # with the most, and nothing of B is touched.
    fake = clickhouse({"clair3": {A: 10, B: 7}})

    await _copy([A, B, C], [A, B])

    assert [(verb, params["project"], params["origin"]) for _kind, verb, params in fake.writes()] == [("INSERT", C, A)]


@pytest.mark.asyncio
async def test_a_linked_project_without_a_callset_gets_it(clickhouse) -> None:
    # An import linked the family to B, beside A: B holds none of its callsets yet.
    fake = clickhouse({"clair3": {A: 10}}, {"needlr": {A: 3}})

    await _copy([A, B], [A, B])

    assert [(kind, verb, params["project"], params["origin"]) for kind, verb, params in fake.writes()] == [
        ("SNV_INDEL", "INSERT", B, A),
        ("SV", "INSERT", B, A),
    ]

    # An import linked a family in no project to B: its rows wait under "unassigned", which is
    # not a linked project, and are copied from there.
    fake = clickhouse({"clair3": {"unassigned": 10}})

    await _copy([B], [B])

    assert [(verb, params["project"], params["origin"]) for _kind, verb, params in fake.writes()] == [
        ("INSERT", B, "unassigned")
    ]


@pytest.mark.asyncio
async def test_nothing_to_copy_writes_nothing(clickhouse) -> None:
    fake = clickhouse({"clair3": {A: 10}}, {"needlr": {A: 3}})

    assert await _copy([A], [A]) is False

    assert fake.writes() == []
    assert (fake.refreshed, fake.bumped) == (0, 0)


@pytest.mark.asyncio
async def test_only_what_was_written_is_refreshed(clickhouse) -> None:
    fake = clickhouse({"clair3": {A: 10, B: 10}}, {"needlr": {A: 3}})

    await _copy([A, B], [A, B])

    assert [(kind, verb) for kind, verb, _params in fake.writes()] == [("SV", "INSERT")]
    assert (fake.refreshed, fake.bumped) == (0, 1)


@pytest.mark.asyncio
async def test_a_family_without_a_project_keeps_its_rows_unassigned(clickhouse) -> None:
    fake = clickhouse({"clair3": {A: 10}})

    await _copy([], [A])

    assert [(verb, params["project"], params["origin"]) for _kind, verb, params in fake.writes()] == [
        ("INSERT", "unassigned", A)
    ]

    fake = clickhouse({"clair3": {"unassigned": 10}})
    await _copy([B], [])

    assert [(verb, params["project"], params["origin"]) for _kind, verb, params in fake.writes()] == [
        ("INSERT", B, "unassigned")
    ]


@pytest.mark.asyncio
async def test_the_prune_deletes_only_rows_outside_the_kept_projects(clickhouse) -> None:
    fake = clickhouse({"clair3": {A: 10, B: 10}}, {"needlr": {B: 3}})

    assert await storage.prune_family_variant_projects(ASSEMBLY, FAMILY, project_ids=[B]) is True

    assert [(kind, verb, params["keep"]) for kind, verb, params in fake.writes()] == [("SNV_INDEL", "DELETE", (B,))]
    assert (fake.refreshed, fake.bumped) == (1, 0)


@pytest.mark.asyncio
async def test_the_prune_moves_the_sv_data_version_when_it_deletes_svs(clickhouse) -> None:
    fake = clickhouse({"clair3": {B: 10}}, {"needlr": {A: 3, B: 3}})

    assert await storage.prune_family_variant_projects(ASSEMBLY, FAMILY, project_ids=[B]) is True

    assert [(kind, verb) for kind, verb, _params in fake.writes()] == [("SV", "DELETE")]
    assert (fake.refreshed, fake.bumped) == (0, 1)


@pytest.mark.asyncio
async def test_the_prune_with_nothing_left_writes_nothing(clickhouse) -> None:
    fake = clickhouse({"clair3": {B: 10}}, {"needlr": {B: 3}})

    assert await storage.prune_family_variant_projects(ASSEMBLY, FAMILY, project_ids=[B]) is False

    assert fake.writes() == []


def test_the_copy_origin_prefers_a_linked_project_then_the_most_rows() -> None:
    assert storage._copy_origin({A: 3, B: 9}, {A}) == A
    assert storage._copy_origin({A: 3, B: 9}, {A, B}) == B
    # No linked project holds the callset: there is nothing current to copy from.
    assert storage._copy_origin({A: 3, B: 9}, {C}) is None
    # A tie goes to the first project in sorted order, every time.
    assert storage._copy_origin({B: 5, A: 5}, {A, B}) == A


# --- the change of a family's projects ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_project_on_another_assembly_is_refused(monkeypatch) -> None:
    async def assemblies(_session, project_ids):
        return {"GRCh37"} if project_ids == [C] else {ASSEMBLY} if project_ids else set()

    async def stored(name, _family):
        return name == ASSEMBLY

    monkeypatch.setattr(family_project_variants, "_project_assembly_names", assemblies)
    monkeypatch.setattr(family_project_variants, "family_has_project_scoped_variants", stored)

    with pytest.raises(HTTPException) as refused:
        await family_project_variants.family_variant_assemblies(None, FAMILY, linked=[A], project_ids=[C])
    assert refused.value.status_code == 409
    assert "stored on GRCh38" in refused.value.detail

    assert await family_project_variants.family_variant_assemblies(None, FAMILY, linked=[A], project_ids=[B]) == [ASSEMBLY]
    # Leaving every project is allowed: the rows wait under "unassigned".
    assert await family_project_variants.family_variant_assemblies(None, FAMILY, linked=[A], project_ids=[]) == [ASSEMBLY]


@pytest.mark.asyncio
async def test_a_family_without_variants_may_go_to_any_assembly(monkeypatch) -> None:
    async def assemblies(_session, project_ids):
        return {"GRCh37"} if project_ids == [C] else {ASSEMBLY}

    async def stored(_name, _family):
        return False

    monkeypatch.setattr(family_project_variants, "_project_assembly_names", assemblies)
    monkeypatch.setattr(family_project_variants, "family_has_project_scoped_variants", stored)

    assert await family_project_variants.family_variant_assemblies(None, FAMILY, linked=[A], project_ids=[C]) == []


class _AssemblySession:
    """Answers the read of every assembly's name."""

    def __init__(self, names: list[Any]) -> None:
        self.names = names
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(" ".join(str(statement).split()))
        names = self.names

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return names

        return _Result()


@pytest.mark.asyncio
async def test_a_family_in_no_project_is_looked_for_on_every_assembly(monkeypatch) -> None:
    # Its projects saved as none (its rows under "unassigned"): no project names the assembly
    # its rows are on, so every assembly is searched, and a project on another one is refused.
    searched: list[str] = []

    async def assemblies(_session, project_ids):
        return {"GRCh37"} if project_ids == [C] else {ASSEMBLY} if project_ids else set()

    async def every_assembly(_session):
        searched.append("all")
        return {"GRCh37", ASSEMBLY, "T2T"}

    async def stored(name, _family):
        return name == ASSEMBLY

    monkeypatch.setattr(family_project_variants, "_project_assembly_names", assemblies)
    monkeypatch.setattr(family_project_variants, "_all_assembly_names", every_assembly)
    monkeypatch.setattr(family_project_variants, "family_has_project_scoped_variants", stored)

    with pytest.raises(HTTPException) as refused:
        await family_project_variants.family_variant_assemblies(None, FAMILY, linked=[], project_ids=[C])
    assert refused.value.status_code == 409
    assert "stored on GRCh38" in refused.value.detail
    # Through the check a package import into the family runs, too.
    async def no_project(_session, _family):
        return []

    monkeypatch.setattr(family_project_variants, "linked_project_ids", no_project)
    with pytest.raises(HTTPException):
        await family_project_variants.refuse_project_on_other_assembly(None, FAMILY, C)

    # A project on the assembly the rows are on gets them copied there, and saving none again
    # copies and prunes there too.
    assert await family_project_variants.family_variant_assemblies(None, FAMILY, linked=[], project_ids=[B]) == [ASSEMBLY]
    assert await family_project_variants.family_variant_assemblies(None, FAMILY, linked=[], project_ids=[]) == [ASSEMBLY]
    assert searched == ["all"] * 4

    # A family with a project is looked for on its projects' assemblies only.
    assert await family_project_variants.family_variant_assemblies(None, FAMILY, linked=[A], project_ids=[B]) == [ASSEMBLY]
    assert searched == ["all"] * 4


@pytest.mark.asyncio
async def test_every_assembly_is_read_by_name_without_blank_ones() -> None:
    session = _AssemblySession(["GRCh38", "GRCh37", "", "  ", None])
    assert await family_project_variants._all_assembly_names(session) == {"GRCh38", "GRCh37"}
    assert session.statements == ["SELECT DISTINCT assembly_name FROM assemblies"]


class _Session:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.events.append(f"sql:{sql.split(' ')[0]} {sql.split(' ')[1]} {sql.split(' ')[2]}")
        return None

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


def _patch_change(monkeypatch, events: list[str], *, refuse: bool = False) -> None:
    async def family_rows(_session, family_identifiers):
        return [{"id": FAMILY, "family_id": family_identifiers[0]}]

    async def validate(_session, project_ids):
        return list(project_ids)

    async def samples(_session, _family):
        return [{"id": "00000000-0000-4000-8000-0000000000e1", "sample_id": "PROBAND"}]

    async def lock(_session, _family, _types, samples=None):
        events.append("lock")

    async def linked(_session, _family):
        return [A]

    async def assemblies(_session, _family, *, linked, project_ids):
        if refuse:
            raise HTTPException(status_code=409, detail="other assembly")
        return [ASSEMBLY]

    async def copy(_family, _assemblies, *, linked, project_ids):
        events.append(f"copy {linked}->{project_ids}")

    async def prune(_session, _family, _assemblies):
        events.append("prune")

    monkeypatch.setattr(metadata_service, "_fetch_family_rows", family_rows)
    monkeypatch.setattr(metadata_service, "_validate_project_ids", validate)
    monkeypatch.setattr(metadata_service, "_family_sample_uuid_rows", samples)
    monkeypatch.setattr(metadata_service, "lock_family_variant_writes", lock)
    monkeypatch.setattr(family_project_variants, "linked_project_ids", linked)
    monkeypatch.setattr(family_project_variants, "family_variant_assemblies", assemblies)
    monkeypatch.setattr(family_project_variants, "copy_variants_to_projects", copy)
    monkeypatch.setattr(family_project_variants, "prune_left_project_variants", prune)


@pytest.mark.asyncio
async def test_the_change_copies_commits_then_prunes(monkeypatch) -> None:
    events: list[str] = []
    _patch_change(monkeypatch, events)

    result = await metadata_service.update_family_project_assignments(_Session(events), "FAM1", [B])

    assert result["project_ids"] == [B]
    # The lock comes before the family's projects are read; the copy before the links
    # commit; the prune after.
    assert events[0] == "lock"
    assert events[-3:] == [f"copy {[A]}->{[B]}", "commit", "prune"]
    assert "sql:DELETE FROM family_projects" in events


@pytest.mark.asyncio
async def test_a_refused_change_writes_nothing(monkeypatch) -> None:
    events: list[str] = []
    _patch_change(monkeypatch, events, refuse=True)

    with pytest.raises(HTTPException) as refused:
        await metadata_service.update_family_project_assignments(_Session(events), "FAM1", [C])

    assert refused.value.status_code == 409
    assert events == ["lock"]


# --- the probe and the module's own steps ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_probe_reads_only_tables_that_exist_and_makes_none(monkeypatch) -> None:
    calls: list[str] = []

    def fake(made: list[str], rows: list[Any]):
        async def execute(query: str, params: dict[str, Any] | None = None, data: Any = None) -> Any:
            calls.append(query)
            if "system.tables" in query:
                return [(name,) for name in made]
            return rows

        return execute

    async def ensure(_assembly: str) -> None:  # pragma: no cover - must not run
        raise AssertionError("the probe made tables")

    monkeypatch.setattr(storage, "ensure_clickhouse_variant_tables", ensure)

    monkeypatch.setattr(storage, "_execute", fake([], [(1,)]))
    assert await storage.family_has_project_scoped_variants("E2E-other", FAMILY) is False
    assert len(calls) == 1

    calls.clear()
    monkeypatch.setattr(storage, "_execute", fake([f"{ASSEMBLY}/SV/entries"], [(1,)]))
    assert await storage.family_has_project_scoped_variants(ASSEMBLY, FAMILY) is True
    assert "/SV/entries" in calls[1]

    monkeypatch.setattr(storage, "_execute", fake([f"{ASSEMBLY}/SNV_INDEL/entries", f"{ASSEMBLY}/SV/entries"], []))
    assert await storage.family_has_project_scoped_variants(ASSEMBLY, FAMILY) is False


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _ReadSession:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.params: list[Any] = []

    async def execute(self, statement, params=None):
        self.params.append(params)
        return _Rows(self.rows)


@pytest.mark.asyncio
async def test_the_family_projects_and_their_assemblies_are_read() -> None:
    assert await family_project_variants.linked_project_ids(_ReadSession([([B, A],)]), FAMILY) == [B, A]
    assert await family_project_variants.linked_project_ids(_ReadSession([]), FAMILY) is None

    session = _ReadSession([ASSEMBLY])
    assert await family_project_variants._project_assembly_names(session, [A]) == {ASSEMBLY}
    assert await family_project_variants._project_assembly_names(session, []) == set()
    assert len(session.params) == 1


@pytest.mark.asyncio
async def test_the_copy_runs_on_every_assembly_with_the_family_projects(monkeypatch) -> None:
    seen: list[tuple[str, list[str], list[str]]] = []

    async def copy(assembly, _family, *, project_ids, linked_project_ids):
        seen.append((assembly, project_ids, linked_project_ids))

    monkeypatch.setattr(family_project_variants, "copy_family_variants_to_projects", copy)

    await family_project_variants.copy_variants_to_projects(FAMILY, [ASSEMBLY, "T2T"], linked=[A], project_ids=[B])

    assert seen == [(ASSEMBLY, [B], [A]), ("T2T", [B], [A])]


def _patch_prune(monkeypatch, events: list[str], *, linked: list[str] | None, fail: bool = False) -> None:
    async def lock(_session, _family, _types, samples=None):
        events.append("lock")

    async def projects(_session, _family):
        events.append("read projects")
        return linked

    async def prune(assembly, _family, *, project_ids):
        if fail:
            raise RuntimeError("synthetic ClickHouse failure")
        events.append(f"prune {assembly} keep {project_ids}")

    monkeypatch.setattr(family_project_variants, "lock_family_variant_writes", lock)
    monkeypatch.setattr(family_project_variants, "linked_project_ids", projects)
    monkeypatch.setattr(family_project_variants, "prune_family_variant_projects", prune)


@pytest.mark.asyncio
async def test_the_prune_reads_the_projects_again_under_the_lock(monkeypatch) -> None:
    events: list[str] = []
    _patch_prune(monkeypatch, events, linked=[B])

    await family_project_variants.prune_left_project_variants(_Session(events), FAMILY, [ASSEMBLY])

    assert events == ["lock", "read projects", f"prune {ASSEMBLY} keep {[B]}", "commit"]


@pytest.mark.asyncio
async def test_the_prune_of_a_family_gone_or_without_variants_deletes_nothing(monkeypatch) -> None:
    events: list[str] = []
    _patch_prune(monkeypatch, events, linked=None)

    await family_project_variants.prune_left_project_variants(_Session(events), FAMILY, [ASSEMBLY])
    assert events == ["lock", "read projects", "commit"]

    events.clear()
    await family_project_variants.prune_left_project_variants(_Session(events), FAMILY, [])
    assert events == []


@pytest.mark.asyncio
async def test_a_failed_prune_is_logged_not_raised(monkeypatch, caplog) -> None:
    events: list[str] = []
    _patch_prune(monkeypatch, events, linked=[B], fail=True)

    with caplog.at_level("WARNING"):
        await family_project_variants.prune_left_project_variants(_Session(events), FAMILY, [ASSEMBLY])

    assert events == ["lock", "read projects", "rollback"]
    assert "Could not remove a family's variant rows under the projects it left" in caplog.text
    # The error is named, never quoted: its text can carry query values.
    assert "synthetic ClickHouse failure" not in caplog.text


# --- a copy that fails part-way -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_copy_removes_what_it_may_have_written_and_raises(clickhouse, monkeypatch) -> None:
    # B is linked and holds none of needlr; the insert fails, possibly after writing part of
    # it. What B holds of needlr goes again, so the next copy writes it whole, and B holds
    # none of it rather than a part no reader can tell from the whole.
    fake = clickhouse({"clair3": {A: 10}}, {"needlr": {A: 3}})
    execute = fake.execute

    async def insert_fails_for_svs(query: str, params: dict[str, Any] | None = None, data: Any = None) -> Any:
        result = await execute(query, params, data)
        if query.startswith("INSERT INTO") and "/SV/" in query:
            raise RuntimeError("synthetic ClickHouse failure")
        return result

    monkeypatch.setattr(storage, "_execute", insert_fails_for_svs)

    with pytest.raises(RuntimeError, match="synthetic ClickHouse failure"):
        await _copy([A, B], [A, B])

    assert [(kind, verb, params["project"], params["source"]) for kind, verb, params in fake.writes()] == [
        ("SNV_INDEL", "INSERT", B, "clair3"),
        ("SV", "INSERT", B, "needlr"),
        ("SV", "DELETE", B, "needlr"),
    ]
    # The small variants copied before the failure are counted, the SVs left as they were.
    assert (fake.refreshed, fake.bumped) == (1, 0)


@pytest.mark.asyncio
async def test_a_removal_that_fails_after_a_failed_copy_is_logged_by_its_kind(clickhouse, monkeypatch, caplog) -> None:
    fake = clickhouse({"clair3": {A: 10}})
    execute = fake.execute

    async def every_write_fails(query: str, params: dict[str, Any] | None = None, data: Any = None) -> Any:
        result = await execute(query, params, data)
        if query.startswith("INSERT INTO"):
            raise RuntimeError("synthetic insert failure")
        if query.startswith("ALTER TABLE"):
            raise RuntimeError("synthetic delete failure quoting PROBAND")
        return result

    monkeypatch.setattr(storage, "_execute", every_write_fails)

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError, match="synthetic insert failure"):
        await _copy([B], [A, B])

    assert "Could not remove what a failed copy of a family's callset to a project wrote" in caplog.text
    # Named by its kind, never quoted: its text can carry query values.
    assert "PROBAND" not in caplog.text
    assert (fake.refreshed, fake.bumped) == (0, 0)


@pytest.mark.asyncio
async def test_a_copy_that_replaces_rows_removes_them_again_when_its_insert_fails(clickhouse, monkeypatch) -> None:
    # C is not linked yet and holds rows left from an earlier change: deleted, the insert
    # fails, and C holds none of the callset.
    fake = clickhouse({"clair3": {A: 10, C: 4}})
    execute = fake.execute

    async def insert_fails(query: str, params: dict[str, Any] | None = None, data: Any = None) -> Any:
        result = await execute(query, params, data)
        if query.startswith("INSERT INTO"):
            raise RuntimeError("synthetic ClickHouse failure")
        return result

    monkeypatch.setattr(storage, "_execute", insert_fails)

    with pytest.raises(RuntimeError):
        await _copy([A, C], [A])

    assert [(verb, params["project"]) for _kind, verb, params in fake.writes()] == [
        ("DELETE", C),
        ("INSERT", C),
        ("DELETE", C),
    ]


# --- a package import that links an existing family to a project ---------------------------


@pytest.mark.asyncio
async def test_an_import_fills_every_linked_project_with_the_callsets_it_lacks(monkeypatch) -> None:
    seen: list[tuple[str, str, list[str], list[str]]] = []

    async def projects(_session, _family):
        return [A, B]

    async def copy(assembly, family, *, project_ids, linked_project_ids):
        seen.append((assembly, family, project_ids, linked_project_ids))
        return True

    monkeypatch.setattr(family_project_variants, "linked_project_ids", projects)
    monkeypatch.setattr(family_project_variants, "copy_family_variants_to_projects", copy)

    assert await family_project_variants.copy_variants_to_linked_projects(None, FAMILY, ASSEMBLY) is True
    # Every linked project is a target and counts as linked: one holding none of a callset
    # gets it, and no linked project's rows are replaced.
    assert seen == [(ASSEMBLY, FAMILY, [A, B], [A, B])]


@pytest.mark.asyncio
async def test_an_import_without_an_assembly_or_a_family_copies_nothing(monkeypatch) -> None:
    async def projects(_session, _family):
        return None

    async def copy(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("copied")

    monkeypatch.setattr(family_project_variants, "linked_project_ids", projects)
    monkeypatch.setattr(family_project_variants, "copy_family_variants_to_projects", copy)

    assert await family_project_variants.copy_variants_to_linked_projects(None, FAMILY, None) is False
    assert await family_project_variants.copy_variants_to_linked_projects(None, FAMILY, ASSEMBLY) is False


@pytest.mark.asyncio
async def test_an_import_into_a_project_on_another_assembly_is_refused(monkeypatch) -> None:
    checked: list[tuple[list[str], list[str]]] = []

    async def projects(_session, _family):
        return [A]

    async def assemblies(_session, _family, *, linked, project_ids):
        checked.append((linked, project_ids))
        if project_ids == [C]:
            raise HTTPException(status_code=409, detail="The family's variants are stored on GRCh38.")
        return [ASSEMBLY]

    monkeypatch.setattr(family_project_variants, "linked_project_ids", projects)
    monkeypatch.setattr(family_project_variants, "family_variant_assemblies", assemblies)

    with pytest.raises(HTTPException) as refused:
        await family_project_variants.refuse_project_on_other_assembly(None, FAMILY, C)
    assert refused.value.status_code == 409
    await family_project_variants.refuse_project_on_other_assembly(None, FAMILY, B)
    # A project the family is linked to already is not checked.
    await family_project_variants.refuse_project_on_other_assembly(None, FAMILY, A)

    assert checked == [([A], [C]), ([A], [B])]


# --- where the package import runs the copy ------------------------------------------------


def _import_steps(monkeypatch, tmp_path: Path, *, created: bool, copy_fails: bool = False) -> list[str]:
    """Run the import's own steps over stand-ins of registration, the variant-write hold,
    the mark, the copy and the dataset loop (whose first step is the snapshot), recording
    their order."""
    steps: list[str] = []
    root = tmp_path / "FAM001"
    root.mkdir()
    (root / "family.ped").write_text("FAM001 S1 0 0 1 2\nFAM001 S2 0 0 2 1\n", encoding="utf-8")
    (root / "manifest.yaml").write_text("schema_version: 1\nfamily_id: FAM001\nped: family.ped\n", encoding="utf-8")
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])

    async def register(_session, **_kwargs):
        steps.append("register (links the project)")
        return two_sample_context(), created

    @asynccontextmanager
    async def hold(_family_uuid, **_kwargs):
        steps.append("hold the locks")
        yield
        steps.append("release the locks")

    async def mark(_session, **_kwargs):
        steps.append("mark")

    async def copy(_session, family_uuid, assembly_name):
        steps.append(f"copy {family_uuid} on {assembly_name}")
        if copy_fails:
            raise RuntimeError("synthetic ClickHouse failure")
        return True

    async def datasets(_session, *, validation, datasets, logs, family_context, **_kwargs):
        steps.append("snapshot, then the datasets")
        return PackageExecutionResult(
            validation=validation, datasets=datasets, logs=logs, family_id=family_context.family_id, completed=True
        )

    async def failed_before_datasets(_session, **_kwargs):
        steps.append("flag the failure")

    async def nothing(*_args, **_kwargs):
        return []

    for name, value in {
        "_ensure_family_from_ped": register,
        "hold_family_variant_writes": hold,
        "_mark_family_import_unfinished": mark,
        "copy_variants_to_linked_projects": copy,
        "_import_family_datasets": datasets,
        "_end_import_failed_before_datasets": failed_before_datasets,
        "_existing_package_entity_warnings": nothing,
        "register_declared_files": nothing,
    }.items():
        monkeypatch.setattr(package_import, name, value)
    return steps


async def _run_import(tmp_path: Path) -> PackageExecutionResult:
    return await package_import.execute_family_package_import(
        object(),  # type: ignore[arg-type]
        folder_path=tmp_path / "FAM001",
        manifest_text=kept_manifest(tmp_path / "FAM001"),
        project_id=B,
        dry_run=False,
        user=admin_user(),
        conflict_mode="update",
    )


@pytest.mark.asyncio
async def test_an_import_into_an_existing_family_copies_under_the_locks_before_its_snapshot(monkeypatch, tmp_path) -> None:
    steps = _import_steps(monkeypatch, tmp_path, created=False)

    result = await _run_import(tmp_path)

    assert result.completed is True
    assert steps == [
        "register (links the project)",
        "hold the locks",
        "mark",
        f"copy family-uuid on {ASSEMBLY}",
        "snapshot, then the datasets",
        "release the locks",
    ]
    assert "Copied the family's stored variants to the projects it is linked to that held none of them." in result.logs


@pytest.mark.asyncio
async def test_an_import_that_creates_the_family_copies_nothing(monkeypatch, tmp_path) -> None:
    steps = _import_steps(monkeypatch, tmp_path, created=True)

    await _run_import(tmp_path)

    assert not any(step.startswith("copy") for step in steps)


@pytest.mark.asyncio
async def test_a_failed_copy_fails_the_import_before_its_snapshot(monkeypatch, tmp_path) -> None:
    steps = _import_steps(monkeypatch, tmp_path, created=False, copy_fails=True)

    with pytest.raises(RuntimeError, match="synthetic ClickHouse failure"):
        await _run_import(tmp_path)

    # The import's mark becomes the failure flag naming its datasets, while it holds the
    # locks; no dataset ran.
    assert steps == [
        "register (links the project)",
        "hold the locks",
        "mark",
        f"copy family-uuid on {ASSEMBLY}",
        "flag the failure",
    ]


# --- the import's registration --------------------------------------------------------------


_STORED_PEDIGREE = "FAM001 S1 0 0 1 2\nFAM001 S2 0 0 2 1\n"


def _bundle(pedigree: str = _STORED_PEDIGREE) -> Any:
    """A package whose members (``family.add_members``) make ``pedigree``."""
    ped, errors = _parse_ped_text_strict(pedigree)
    assert ped is not None, errors
    return type("Bundle", (), {"ped": ped, "manifest": PackageManifest()})()


def _registration(monkeypatch, *, refuse: bool) -> list[str]:
    """Registration of an existing family whose stored pedigree is ``_STORED_PEDIGREE``,
    recording the import's mark and its link."""
    steps: list[str] = []

    async def resolve(_session, _user, project_id):
        return project_id

    async def existing(_session, *, family_id):
        return {"family_uuid": FAMILY, "metadata": {}, "sample_ids": ["S1", "S2"]}

    async def stored_pedigree(_session, *, family_id):
        return _STORED_PEDIGREE

    async def refuse_other_assembly(_session, family_uuid, project_id):
        steps.append(f"check {project_id}")
        if refuse:
            raise HTTPException(status_code=409, detail="The family's variants are stored on GRCh38.")

    async def mark(_session, **_kwargs):
        steps.append("mark")

    monkeypatch.setattr(registration.ped_service, "_resolve_accessible_project_id", resolve)
    monkeypatch.setattr(registration, "_fetch_existing_family", existing)
    monkeypatch.setattr(registration.ped_service, "build_pedigree_text", stored_pedigree)
    monkeypatch.setattr(registration, "_package_family_id", lambda _validation, _bundle: "FAM001")
    monkeypatch.setattr(registration, "refuse_project_on_other_assembly", refuse_other_assembly)
    monkeypatch.setattr(registration, "_mark_family_import_unfinished", mark)
    return steps


class _LinkSession:
    def __init__(self, steps: list[str]) -> None:
        self.steps = steps

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.steps.append(" ".join(sql.split(" ")[:3]))

    async def commit(self) -> None:
        self.steps.append("commit")


@pytest.mark.asyncio
async def test_an_import_into_a_project_on_another_assembly_is_refused_before_the_family_is_marked(monkeypatch) -> None:
    steps = _registration(monkeypatch, refuse=True)
    bundle = _bundle()

    with pytest.raises(ImportRefused, match="stored on GRCh38"):
        await registration._ensure_family_from_ped(
            _LinkSession(steps),  # type: ignore[arg-type]
            bundle=bundle,  # type: ignore[arg-type]
            project_id=C,
            user=admin_user(),
            validation=None,  # type: ignore[arg-type]
            conflict_mode="update",
            import_mark=object(),  # type: ignore[arg-type]
        )

    # Nothing of the family was written: not its mark, not the link.
    assert steps == [f"check {C}"]


@pytest.mark.asyncio
async def test_an_import_whose_members_differ_from_the_familys_is_refused_before_the_family_is_marked(
    monkeypatch,
) -> None:
    # The package's members are the pedigree the import reads: S2 is a man in it, a woman in CoGA,
    # and S3 is no member of the family. An update adds S3, but changes no member the family holds.
    steps = _registration(monkeypatch, refuse=False)
    bundle = _bundle("FAM001 S1 0 0 1 2\nFAM001 S2 0 0 1 1\nFAM001 S3 0 0 2 1\n")

    with pytest.raises(ImportRefused) as refused:
        await registration._ensure_family_from_ped(
            _LinkSession(steps),  # type: ignore[arg-type]
            bundle=bundle,  # type: ignore[arg-type]
            project_id=B,
            user=admin_user(),
            validation=None,  # type: ignore[arg-type]
            conflict_mode="update",
            import_mark=object(),  # type: ignore[arg-type]
        )

    assert str(refused.value) == registration.members_differ_message(
        "FAM001",
        registration.MemberDifferences(
            added=("S3",),
            changed=(
                ("S2", "S2 is male, father none, mother none in the package, female, father none, mother none in CoGA"),
            ),
        ),
    )
    assert "S2 is male, father none, mother none in the package" in str(refused.value)
    assert "S3 is a member of the package, not of the family" in str(refused.value)
    # Nothing of the family was written, and its project was not checked.
    assert steps == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conflict_mode", "pedigree", "replace"),
    [
        # An update adds S3, a child of S1 and S2, and changes no member the family holds.
        ("update", "FAM001 S1 0 0 1 2\nFAM001 S2 0 0 2 1\nFAM001 S3 S1 S2 1 2\n", False),
        # An overwrite makes the package's members the family's: S2 is gone, S1 a woman, S3 added.
        ("overwrite", "FAM001 S1 0 0 2 2\nFAM001 S3 0 S1 1 2\n", True),
    ],
)
async def test_an_import_gives_the_family_the_packages_members_once_it_is_marked(
    monkeypatch, conflict_mode: str, pedigree: str, replace: bool
) -> None:
    steps = _registration(monkeypatch, refuse=False)
    taken: list[dict[str, Any]] = []

    async def take(_session, **kwargs):
        steps.append("take the pedigree")
        taken.append(kwargs)

    async def context(*_args, **_kwargs):
        raise _Registered

    monkeypatch.setattr(family_structure_service, "take_package_pedigree", take)
    monkeypatch.setattr(registration, "build_family_metadata_context", context)

    with pytest.raises(_Registered):
        await registration._ensure_family_from_ped(
            _LinkSession(steps),  # type: ignore[arg-type]
            bundle=_bundle(pedigree),  # type: ignore[arg-type]
            project_id=B,
            user=admin_user(),
            validation=None,  # type: ignore[arg-type]
            conflict_mode=conflict_mode,
            import_mark=object(),  # type: ignore[arg-type]
        )

    # After the family is marked, before its project is linked.
    assert steps[:3] == [f"check {B}", "mark", "take the pedigree"]
    (call,) = taken
    assert (call["family_id"], call["replace"]) == ("FAM001", replace)
    members = {member["sample_id"]: (member["sex"], member["father_id"], member["mother_id"]) for member in call["members"]}
    if replace:
        assert members == {"S1": ("female", None, None), "S3": ("male", None, "S1")}
    else:
        assert members["S3"] == ("male", "S1", "S2")
    links = {(link["relationship_type"], link["sample_id_a"], link["sample_id_b"]) for link in call["relationships"]}
    assert ("parent_child", "S1", "S3") in links


@pytest.mark.asyncio
async def test_an_import_whose_members_agree_with_the_familys_leaves_its_pedigree(monkeypatch) -> None:
    steps = _registration(monkeypatch, refuse=False)

    async def take(_session, **_kwargs):
        raise AssertionError("the family's pedigree is not changed")

    async def context(*_args, **_kwargs):
        raise _Registered

    monkeypatch.setattr(family_structure_service, "take_package_pedigree", take)
    monkeypatch.setattr(registration, "build_family_metadata_context", context)

    for conflict_mode in ("update", "overwrite"):
        with pytest.raises(_Registered):
            await registration._ensure_family_from_ped(
                _LinkSession(steps),  # type: ignore[arg-type]
                bundle=_bundle(),  # type: ignore[arg-type]
                project_id=B,
                user=admin_user(),
                validation=None,  # type: ignore[arg-type]
                conflict_mode=conflict_mode,
                import_mark=object(),  # type: ignore[arg-type]
            )


@pytest.mark.asyncio
async def test_a_pedigree_the_family_cannot_take_refuses_the_import(monkeypatch) -> None:
    _registration(monkeypatch, refuse=False)

    async def take(_session, **_kwargs):
        raise HTTPException(status_code=409, detail="Sample id already exists: S3")

    monkeypatch.setattr(family_structure_service, "take_package_pedigree", take)

    with pytest.raises(ImportRefused, match="could not take the package's members: Sample id already exists: S3"):
        await registration._ensure_family_from_ped(
            _LinkSession([]),  # type: ignore[arg-type]
            bundle=_bundle("FAM001 S1 0 0 1 2\nFAM001 S2 0 0 2 1\nFAM001 S3 S1 S2 1 2\n"),  # type: ignore[arg-type]
            project_id=B,
            user=admin_user(),
            validation=None,  # type: ignore[arg-type]
            conflict_mode="update",
            import_mark=object(),  # type: ignore[arg-type]
        )


class _Registered(Exception):
    """Stops registration once the family's links have committed."""


@pytest.mark.asyncio
async def test_an_import_into_a_project_on_the_familys_assembly_is_checked_then_marked_then_linked(monkeypatch) -> None:
    steps = _registration(monkeypatch, refuse=False)
    bundle = _bundle()

    async def context(*_args, **_kwargs):
        raise _Registered

    monkeypatch.setattr(registration, "build_family_metadata_context", context)

    with pytest.raises(_Registered):
        await registration._ensure_family_from_ped(
            _LinkSession(steps),  # type: ignore[arg-type]
            bundle=bundle,  # type: ignore[arg-type]
            project_id=B,
            user=admin_user(),
            validation=None,  # type: ignore[arg-type]
            conflict_mode="update",
            import_mark=object(),  # type: ignore[arg-type]
        )

    assert steps == [
        f"check {B}",
        "mark",
        "INSERT INTO family_projects",
        "INSERT INTO sample_projects",
        "commit",
    ]
