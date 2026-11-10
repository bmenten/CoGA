"""A family's variants follow it from project to project (``family_project_variants``).

ClickHouse stores a family's small and structural variant rows once per project it is linked
to, and the family is read through its projects only. A change of its projects copies its rows
to each project it is added to before the new links commit, and removes those under the
projects it left once they have; a project on another assembly is refused. Before, the change
touched Postgres only, and a family moved to another project showed no variants.

Over a fake ClickHouse (the storage module's ``_execute``) and a fake Postgres session:

* per callset, the rows of a linked project are copied (the most rows among the linked
  projects, else among all), with every entries column and the new project in place of the
  old one;
* a project the family is not linked to yet has whatever it holds replaced, even with as many
  rows as the origin: no write has kept them current;
* a linked project's rows are never deleted by the copy; a linked project without a callset
  gets it (a family moved before the fix, or a project an import added);
* the copy refreshes the small-variant summaries or moves the SV data version only for what
  it wrote; the prune deletes only rows outside the kept projects;
* a family without a project keeps its rows under ``unassigned``, as the writers store them;
* a project on another assembly is refused with 409 before anything is written, and the
  change runs copy, commit, then prune, in its own transaction.

The values are synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.services import clickhouse_variant_storage as storage
from backend.app.services import family_project_variants, metadata_service

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
    # The family was moved to B before this fix: B is linked, its rows stayed under A.
    fake = clickhouse({"clair3": {A: 10}}, {"needlr": {A: 3}})

    await _copy([B], [B])

    assert [(kind, verb, params["project"], params["origin"]) for kind, verb, params in fake.writes()] == [
        ("SNV_INDEL", "INSERT", B, A),
        ("SV", "INSERT", B, A),
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
    assert storage._copy_origin({A: 3, B: 9}, {C}) == B
    # A tie goes to the first project in sorted order, every time.
    assert storage._copy_origin({B: 5, A: 5}, set()) == A


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
