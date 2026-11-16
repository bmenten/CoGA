"""The Variant Explorer's carrier summary (``variant_carrier_summary``), on fakes of ClickHouse
and Postgres: its table, a family's contribution and how it is written in one step, the
writers' hook that takes a contribution out before their first write, when a family's
contribution is stale, the reconciler and the worker. Its counts against the families' rows
are compared on real stores in ``integration/test_variant_carrier_summary_clickhouse.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from clickhouse_connect.driver.binding import bind_query

from backend.app.services import variant_carrier_summary as summary
from backend.app.services.variant_carrier_summary import FamilyCarrierState, _Generation

_STATE = FamilyCarrierState(
    projects=("p1", "p2"),
    shown_samples=("MOTHER", "PROBAND"),
    shared_samples=("MOTHER",),
    data_version="3:99",
)
_APPLIED = _Generation(generation=42, state=_STATE, source_project="p1")


def test_the_table_sums_rows_of_one_identity_sorted_by_variant() -> None:
    ddl = " ".join(summary.carriers_table_ddl("coga", "GRCh38").split())
    assert "`GRCh38/SNV_INDEL/carriers`" in ddl
    assert "ENGINE = SummingMergeTree((families, samples, het, hom, applied))" in ddl
    # Sorted by variant first, so a page groups one variant at a time; then the row's
    # identity: family and sample of a carrier row, projects, the class, a marker's generation.
    assert (
        "ORDER BY (xpos, key, family_guid, sample_id, projects, annotationSetHash, rsid, gene_symbols, "
        "imputed, generation)" in ddl
    )
    generations = " ".join(summary.carrier_generations_table_ddl("coga", "GRCh38").split())
    assert "ORDER BY (family_guid, generation)" in generations
    assert "`shared_samples` Array(String)" in generations


def test_a_contribution_reads_the_familys_shown_carriers_of_one_project_copy() -> None:
    query, params = summary._contribution_insert("GRCh38", "coga.`stage`", _APPLIED, family_uuid="f1", sign=-1)
    flat = " ".join(query.split())
    # The rows a merge leaves (FINAL), under the project whose copy was read.
    assert "FROM coga.`GRCh38/SNV_INDEL/entries` AS e FINAL" in flat.replace(flat.split("`GRCh38")[0].split()[-1], "coga.")
    assert "WHERE e.project_guid = %(source_project)s AND e.family_guid = %(family)s AND e.sign = 1" in flat
    assert (params["source_project"], params["family"], params["sign"], params["generation"]) == ("p1", "f1", -1, 42)
    # The calls of the members the family showed, and its samples shown twice.
    assert params["shown"] == ["MOTHER", "PROBAND"]
    assert params["shared"] == ["MOTHER"]
    assert flat.count("has(%(shown)s, s)") == 3
    # A variant of one class no shared sample carries: an aggregate row; else carrier rows.
    assert "if(length(classes) = 1 AND NOT hasAny(arrayFlatten(row_carriers), %(shared)s)" in flat
    assert "GROUP BY xpos, key" in flat
    assert "optimize_aggregation_in_order = 1" in flat
    # The marker of the generation, in the same insert.
    assert "toUInt64(%(generation)s)" in flat and "toInt32(%(sign)s)" in flat
    # The imputed callsets are marked, not left out: the explorer includes them on request.
    assert params["imputed_sources"] == ("glimpse2", "shapeit")
    bind_query(query, params)


def test_a_family_without_rows_under_its_projects_contributes_its_marker_alone() -> None:
    empty = _Generation(generation=7, state=_STATE, source_project="")
    query, params = summary._contribution_insert("GRCh38", "coga.`stage`", empty, family_uuid="f1", sign=1)
    assert "entries" not in query
    assert "%(generation)s" in query and params["sign"] == 1


class _ClickHouse:
    """Records each statement; answers from ``answers`` (a text it holds -> rows), in order."""

    def __init__(self, answers: list[tuple[str, Any]] | None = None, fail: dict[str, Exception] | None = None) -> None:
        self.answers = list(answers or [])
        self.fail = dict(fail or {})
        self.statements: list[str] = []

    async def execute(self, query: str, params: Any = None, *, retry: bool = True) -> Any:
        flat = " ".join(query.split())
        self.statements.append(flat)
        for text_part, error in list(self.fail.items()):
            if text_part in flat:
                del self.fail[text_part]
                raise error
        for index, (text_part, rows) in enumerate(self.answers):
            if text_part in flat:
                del self.answers[index]
                return rows
        return []

    def first(self, text_part: str) -> int:
        return next(index for index, statement in enumerate(self.statements) if text_part in statement)


@pytest.fixture
def clickhouse(monkeypatch: pytest.MonkeyPatch) -> _ClickHouse:
    fake = _ClickHouse()
    monkeypatch.setattr(summary, "execute_clickhouse", fake.execute)
    return fake


def _stage_checks(parts: int = 1, markers: int = 1, applied: int = 1) -> tuple[str, Any]:
    return ("AS parts", [(parts, markers, applied)])


def test_a_contribution_is_attached_in_one_part_with_its_marker(clickhouse: _ClickHouse) -> None:
    clickhouse.answers = [_stage_checks()]
    asyncio.run(summary._write_contribution("GRCh38", "f1", _APPLIED, sign=1))
    kinds = [statement.split()[0] for statement in clickhouse.statements]
    assert kinds == ["CREATE", "INSERT", "OPTIMIZE", "SELECT", "ALTER", "DROP"]
    assert "ATTACH PARTITION tuple() FROM" in clickhouse.statements[4]
    assert "carriers_stage/" in clickhouse.statements[0]


@pytest.mark.parametrize(
    "checks",
    [
        pytest.param((2, 1, 1), id="two-parts"),
        pytest.param((1, 2, 2), id="marker-twice"),
        pytest.param((1, 1, -1), id="marker-of-the-other-sign"),
    ],
)
def test_a_staging_table_that_is_not_one_part_with_one_marker_is_not_attached(
    clickhouse: _ClickHouse, checks: tuple[int, int, int]
) -> None:
    clickhouse.answers = [_stage_checks(*checks)]
    with pytest.raises(summary.CarrierSummaryInconsistent):
        asyncio.run(summary._write_contribution("GRCh38", "f1", _APPLIED, sign=1))
    assert not any(statement.startswith("ALTER") for statement in clickhouse.statements)
    assert clickhouse.statements[-1].startswith("DROP TABLE IF EXISTS")


@pytest.mark.parametrize(("applied_after", "raises"), [({42: 1}, False), ({}, True)])
def test_an_attach_that_failed_is_read_back_from_the_markers(
    clickhouse: _ClickHouse, applied_after: dict[int, int], raises: bool
) -> None:
    clickhouse.answers = [
        _stage_checks(),
        ("HAVING n != 0", [("f1", generation, net) for generation, net in applied_after.items()]),
    ]
    clickhouse.fail = {"ATTACH PARTITION": RuntimeError("read timeout")}
    if raises:
        with pytest.raises(RuntimeError):
            asyncio.run(summary._write_contribution("GRCh38", "f1", _APPLIED, sign=1))
    else:
        asyncio.run(summary._write_contribution("GRCh38", "f1", _APPLIED, sign=1))
    assert clickhouse.statements[-1].startswith("DROP TABLE IF EXISTS")


@pytest.mark.parametrize(
    ("desired", "applied", "stale"),
    [
        (None, None, False),
        (None, _APPLIED, True),
        (FamilyCarrierState((), (), (), "0:0"), None, False),
        (_STATE, None, True),
        (_STATE, _APPLIED, False),
        (FamilyCarrierState(("p1",), _STATE.shown_samples, _STATE.shared_samples, "3:99"), _APPLIED, True),
        (FamilyCarrierState(_STATE.projects, ("PROBAND",), (), "3:99"), _APPLIED, True),
        (FamilyCarrierState(_STATE.projects, _STATE.shown_samples, (), "3:99"), _APPLIED, True),
        (FamilyCarrierState(_STATE.projects, _STATE.shown_samples, _STATE.shared_samples, "4:7"), _APPLIED, True),
    ],
)
def test_a_contribution_is_stale_when_the_family_differs_from_what_it_was_computed_from(
    desired: FamilyCarrierState | None, applied: _Generation | None, stale: bool
) -> None:
    assert summary._stale(desired, applied) is stale


@pytest.fixture
def no_postgres(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The summary's lock granted without Postgres; records each lock taken."""
    taken: list[str] = []

    @asynccontextmanager
    async def session(assembly_name: str, *, exclusive: bool = False, wait: bool = True):
        taken.append("exclusive" if exclusive else "shared")
        yield None, True

    monkeypatch.setattr(summary, "_summary_session", session)
    return taken


def _detach_fakes(monkeypatch: pytest.MonkeyPatch, *, version: str, applied: dict[str, _Generation]) -> dict[str, Any]:
    from backend.app.services import clickhouse_variant_storage as storage

    seen: dict[str, Any] = {"written": [], "rebuild": []}

    async def ensure(assembly_name: str) -> None:
        return None

    async def data_version(assembly_name: str, family_uuid: str) -> str:
        return version

    async def applied_generations(assembly_name: str, family_uuids: Any = None) -> dict[str, dict[int, int]]:
        return {family: {generation.generation: 1} for family, generation in applied.items()}

    async def applied_records(assembly_name: str, family_uuids: Any = None) -> dict[str, _Generation]:
        return dict(applied)

    async def write(assembly_name: str, family_uuid: str, generation: _Generation, *, sign: int) -> None:
        seen["written"].append((family_uuid, generation.generation, sign))

    async def rebuild(assembly_name: str, reason: str) -> None:
        seen["rebuild"].append(reason)

    monkeypatch.setattr(storage, "ensure_clickhouse_variant_tables", ensure)
    monkeypatch.setattr(storage, "get_family_small_variant_data_version", data_version)
    monkeypatch.setattr(summary, "_applied_generations", applied_generations)
    monkeypatch.setattr(summary, "_applied", applied_records)
    monkeypatch.setattr(summary, "_write_contribution", write)
    monkeypatch.setattr(summary, "_request_rebuild", rebuild)
    return seen


def test_a_writer_takes_out_the_contribution_it_was_computed_from(
    monkeypatch: pytest.MonkeyPatch, no_postgres: list[str]
) -> None:
    seen = _detach_fakes(monkeypatch, version="3:99", applied={"f1": _APPLIED})
    asyncio.run(summary.detach_family_variant_carriers("GRCh38", "f1"))
    assert seen["written"] == [("f1", 42, -1)]
    assert (seen["rebuild"], no_postgres) == ([], ["shared"])


def test_a_family_whose_contribution_is_out_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, no_postgres: list[str]
) -> None:
    seen = _detach_fakes(monkeypatch, version="3:99", applied={})
    asyncio.run(summary.detach_family_variant_carriers("GRCh38", "f1"))
    assert (seen["written"], seen["rebuild"], no_postgres) == ([], [], [])


def test_rows_that_changed_while_the_contribution_was_in_have_the_summary_rebuilt(
    monkeypatch: pytest.MonkeyPatch, no_postgres: list[str]
) -> None:
    # The write goes on: the hook never raises.
    seen = _detach_fakes(monkeypatch, version="4:7", applied={"f1": _APPLIED})
    asyncio.run(summary.detach_family_variant_carriers("GRCh38", "f1"))
    assert seen["written"] == []
    # The reason names the family and the kind of error, no value of it (logs carry none).
    assert seen["rebuild"] == ["family f1: CarrierSummaryInconsistent"]


def test_a_failure_to_take_a_contribution_out_has_the_summary_rebuilt(
    monkeypatch: pytest.MonkeyPatch, no_postgres: list[str]
) -> None:
    seen = _detach_fakes(monkeypatch, version="3:99", applied={"f1": _APPLIED})

    async def failing(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Code: 241. Memory limit exceeded")

    monkeypatch.setattr(summary, "_write_contribution", failing)
    asyncio.run(summary.detach_family_variant_carriers("GRCh38", "f1"))
    assert len(seen["rebuild"]) == 1


def test_markers_that_sum_to_more_than_one_generation_are_inconsistent(clickhouse: _ClickHouse) -> None:
    clickhouse.answers = [("HAVING n != 0", [("f1", 1, 1), ("f1", 2, 1)])]
    with pytest.raises(summary.CarrierSummaryInconsistent):
        asyncio.run(summary._applied("GRCh38", ["f1"]))


def test_a_generation_reads_back_what_it_was_computed_from(clickhouse: _ClickHouse) -> None:
    clickhouse.answers = [
        ("HAVING n != 0", [("f1", 42, 1)]),
        ("carrier_generations", [("f1", 42, ["p1", "p2"], ["MOTHER", "PROBAND"], ["MOTHER"], "p1", "3:99")]),
    ]
    assert asyncio.run(summary._applied("GRCh38", ["f1"])) == {"f1": _APPLIED}


class _Session:
    """A Postgres session whose statements answer ``rows`` in turn (mappings or tuples)."""

    def __init__(self, *answers: list[Any]) -> None:
        self.answers = list(answers)
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append((" ".join(str(statement).split()), dict(params or {})))
        rows = self.answers.pop(0) if self.answers else []

        class _Result:
            def mappings(self) -> Any:
                return type("M", (), {"all": staticmethod(lambda: rows)})()

            def all(self) -> Any:
                return [tuple(row.values()) if isinstance(row, dict) else row for row in rows]

        return _Result()


def test_a_familys_state_is_its_projects_on_the_assembly_its_shown_members_and_those_shown_twice() -> None:
    f1, f2 = "00000000-0000-0000-0000-0000000000f1", "00000000-0000-0000-0000-0000000000f2"
    session = _Session(
        [
            {"family_uuid": f1, "project_id": "p2"},
            {"family_uuid": f1, "project_id": "p1"},
            {"family_uuid": f2, "project_id": "p1"},
        ],
        [
            {"family_uuid": f1, "sample_id": "PROBAND"},
            {"family_uuid": f1, "sample_id": "MOTHER"},
            {"family_uuid": f2, "sample_id": "MOTHER"},
        ],
        [("MOTHER",)],
    )
    states = asyncio.run(summary._desired_states(session, "a1"))
    assert states == {
        f1: (("p1", "p2"), ("MOTHER", "PROBAND"), ("MOTHER",)),
        f2: (("p1",), ("MOTHER",), ("MOTHER",)),
    }
    projects, members, shared = session.statements
    assert "p.assembly_id = CAST(:assembly_id AS uuid)" in projects[0]
    assert "WHERE fm.active" in members[0]
    # Shown twice: in two families linked to the assembly; the IDs as one array parameter.
    assert "HAVING count(DISTINCT fm.family_id) > 1" in shared[0]
    assert "s.sample_id = ANY(CAST(:sample_ids AS text[]))" in shared[0]
    assert shared[1]["sample_ids"] == ["MOTHER", "PROBAND"]


def _reconcile_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    desired: dict[str, FamilyCarrierState],
    applied: dict[str, _Generation],
    busy: frozenset[str] = frozenset(),
    rebuild: bool = False,
) -> dict[str, Any]:
    from backend.app.services import clickhouse_variant_storage as storage

    seen: dict[str, Any] = {"attached": [], "detached": [], "truncated": [], "rebuild": []}

    async def ensure(assembly_name: str) -> None:
        return None

    async def states(*_args: Any) -> dict[str, FamilyCarrierState]:
        return dict(desired)

    async def family_states(session: Any, assembly_id: str, assembly_name: str, family_uuids: Any) -> Any:
        return {family: desired[family] for family in family_uuids if family in desired}

    async def applied_records(assembly_name: str, family_uuids: Any = None) -> dict[str, _Generation]:
        if family_uuids is None:
            return dict(applied)
        return {family: applied[family] for family in family_uuids if family in applied}

    async def try_lock(session: Any, family_uuid: str) -> bool:
        return family_uuid not in busy

    async def attach(assembly_name: str, family_uuid: str, state: FamilyCarrierState) -> None:
        seen["attached"].append(family_uuid)

    async def detach(assembly_name: str, family_uuid: str, generation: _Generation, version: str) -> None:
        seen["detached"].append(family_uuid)

    async def data_version(assembly_name: str, family_uuid: str) -> str:
        return "3:99"

    async def requested(assembly_name: str) -> bool:
        return rebuild

    async def empty(assembly_name: str) -> bool:
        seen["truncated"].append(assembly_name)
        return True

    async def request(assembly_name: str, reason: str) -> None:
        seen["rebuild"].append(reason)

    monkeypatch.setattr(storage, "ensure_clickhouse_variant_tables", ensure)
    monkeypatch.setattr(storage, "get_family_small_variant_data_version", data_version)
    monkeypatch.setattr(summary, "_current_states", states)
    monkeypatch.setattr(summary, "_family_states", family_states)
    monkeypatch.setattr(summary, "_applied", applied_records)
    monkeypatch.setattr(summary, "_try_lock_family", try_lock)
    monkeypatch.setattr(summary, "_attach", attach)
    monkeypatch.setattr(summary, "_detach", detach)
    monkeypatch.setattr(summary, "_rebuild_requested", requested)
    monkeypatch.setattr(summary, "_empty_summary", empty)
    monkeypatch.setattr(summary, "_request_rebuild", request)
    return seen


def test_the_reconciler_adds_recomputes_and_takes_out_what_differs(
    monkeypatch: pytest.MonkeyPatch, no_postgres: list[str]
) -> None:
    changed = FamilyCarrierState(_STATE.projects, ("PROBAND",), (), "3:99")
    seen = _reconcile_fakes(
        monkeypatch,
        desired={"new": _STATE, "changed": changed, "same": _STATE, "never-written": FamilyCarrierState((), (), (), "0:0")},
        applied={"changed": _APPLIED, "same": _APPLIED, "gone": _APPLIED},
    )
    status = asyncio.run(summary.reconcile_variant_carriers("a1", "GRCh38"))
    assert sorted(status.refreshed) == ["changed", "gone", "new"]
    assert (status.pending, status.emptied) == ([], False)
    assert sorted(seen["attached"]) == ["changed", "new"]
    assert sorted(seen["detached"]) == ["changed", "gone"]


def test_a_family_a_writer_holds_is_left_for_the_next_round(
    monkeypatch: pytest.MonkeyPatch, no_postgres: list[str]
) -> None:
    seen = _reconcile_fakes(monkeypatch, desired={"busy": _STATE, "free": _STATE}, applied={}, busy=frozenset({"busy"}))
    status = asyncio.run(summary.reconcile_variant_carriers("a1", "GRCh38"))
    assert (status.refreshed, status.pending, seen["attached"]) == (["free"], ["busy"], ["free"])


def test_families_past_the_time_budget_are_left_for_the_next_round(
    monkeypatch: pytest.MonkeyPatch, no_postgres: list[str]
) -> None:
    _reconcile_fakes(monkeypatch, desired={"a": _STATE, "b": _STATE}, applied={})
    status = asyncio.run(summary.reconcile_variant_carriers("a1", "GRCh38", time_budget_seconds=-1))
    assert (status.refreshed, status.pending) == ([], ["a", "b"])


def test_a_summary_asked_to_be_rebuilt_is_emptied_first(
    monkeypatch: pytest.MonkeyPatch, no_postgres: list[str]
) -> None:
    seen = _reconcile_fakes(monkeypatch, desired={"a": _STATE}, applied={}, rebuild=True)
    status = asyncio.run(summary.reconcile_variant_carriers("a1", "GRCh38"))
    assert (status.emptied, seen["truncated"], seen["attached"]) == (True, ["GRCh38"], ["a"])


def test_an_inconsistency_found_while_reconciling_has_the_summary_rebuilt(
    monkeypatch: pytest.MonkeyPatch, no_postgres: list[str]
) -> None:
    seen = _reconcile_fakes(monkeypatch, desired={"a": _STATE}, applied={})

    async def inconsistent(*_args: Any, **_kwargs: Any) -> Any:
        raise summary.CarrierSummaryInconsistent("family a: markers [(1, 1), (2, 1)]")

    monkeypatch.setattr(summary, "_applied", inconsistent)
    status = asyncio.run(summary.reconcile_variant_carriers("a1", "GRCh38"))
    assert (status.refreshed, seen["rebuild"]) == ([], ["family a: markers [(1, 1), (2, 1)]"])


def test_the_pending_families_are_those_of_the_scope_that_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_p2 = FamilyCarrierState(("p2",), ("PROBAND",), (), "1:1")
    moved_out = FamilyCarrierState(("p2",), ("PROBAND",), (), "1:1")
    counted_in_p1 = _Generation(generation=1, state=FamilyCarrierState(("p1",), ("PROBAND",), (), "1:1"), source_project="p1")
    _reconcile_fakes(
        monkeypatch,
        desired={"stale": _STATE, "fresh": _STATE, "other-scope": in_p2, "moved": moved_out},
        applied={"fresh": _APPLIED, "moved": counted_in_p1},
    )

    async def family_states(session: Any, assembly_id: str, assembly_name: str, family_uuids: Any) -> Any:
        return {"stale": _STATE, "fresh": _STATE, "other-scope": in_p2, "moved": moved_out}

    monkeypatch.setattr(summary, "_family_states", family_states)
    pending = asyncio.run(summary.pending_variant_carrier_families(None, "a1", "GRCh38", ["p1"]))
    # "moved" left P1 but its contribution still counts it there.
    assert pending == ["moved", "stale"]


def test_old_staging_tables_are_dropped(clickhouse: _ClickHouse) -> None:
    clickhouse.answers = [
        (
            "FROM system.tables",
            [
                ("GRCh38/SNV_INDEL/carriers_stage/1000-abcdef",),
                ("GRCh38/SNV_INDEL/carriers_stage/9000-abcdef",),
                ("GRCh38/SNV_INDEL/carriers",),
            ],
        )
    ]
    assert asyncio.run(summary.drop_stale_carrier_stages(now=1000 + summary._STAGE_MAX_AGE_SECONDS + 1)) == 1
    assert clickhouse.statements[-1].endswith("`GRCh38/SNV_INDEL/carriers_stage/1000-abcdef`")


def test_the_worker_reconciles_every_round_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    rounds: list[float | None] = []

    async def no_stages(now: float | None = None) -> int:
        return 0

    async def reconcile(*, time_budget_seconds: float | None = None) -> None:
        rounds.append(time_budget_seconds)
        if len(rounds) == 1:
            raise RuntimeError("ClickHouse unreachable")
        stop.set()

    stop = asyncio.Event()
    monkeypatch.setattr(summary, "drop_stale_carrier_stages", no_stages)
    monkeypatch.setattr(summary, "reconcile_all_variant_carriers", reconcile)
    monkeypatch.setattr(summary, "VARIANT_CARRIER_ROUND_SECONDS", 0.01)
    asyncio.run(asyncio.wait_for(summary.variant_carrier_worker(stop), timeout=5))
    # A failed round is logged, and the next one runs.
    assert rounds == [60.0, 60.0]


# --- the writers' hook ---------------------------------------------------------------


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every ClickHouse statement of the storage and the snapshot restore, with the carrier
    hook's calls among them as ``detach <family>``."""
    from backend.app.services import clickhouse_family_snapshot as snapshot
    from backend.app.services import clickhouse_variant_storage as storage

    log: list[str] = []

    async def detach(assembly_name: str, family_uuid: str) -> None:
        log.append(f"detach {family_uuid}")

    async def execute(query: str, params: Any = None, data: Any = None) -> Any:
        log.append(" ".join(query.split()))
        if "SELECT 1 FROM" in query or "GROUP BY source, project_guid" in query:
            return [("x",)] if "SELECT 1 FROM" in query else [("clair3", "p1", 1)]
        return []

    async def ensure(assembly_name: str) -> None:
        return None

    async def execute_clickhouse(query: str, params: Any = None, **_kwargs: Any) -> Any:
        log.append(" ".join(query.split()))
        return [("x",)] if "system.tables" in query else []

    monkeypatch.setattr(storage, "detach_family_variant_carriers", detach)
    monkeypatch.setattr(snapshot, "detach_family_variant_carriers", detach)
    monkeypatch.setattr(storage, "_execute", execute)
    monkeypatch.setattr(storage, "ensure_clickhouse_variant_tables", ensure)
    monkeypatch.setattr(snapshot, "execute_clickhouse", execute_clickhouse)
    return log


def _first_write_after_detach(log: list[str]) -> None:
    detached = log.index("detach f1")
    entries_writes = [
        index
        for index, statement in enumerate(log)
        if "SNV_INDEL/entries" in statement and statement.split()[0] in {"INSERT", "ALTER"}
    ]
    assert entries_writes and detached < min(entries_writes)


@pytest.mark.parametrize("source", [None, "clair3"])
def test_a_delete_takes_the_family_out_first(writes: list[str], source: str | None) -> None:
    from backend.app.services import clickhouse_variant_storage as storage

    asyncio.run(storage.delete_family_small_variants("GRCh38", "f1", source=source))
    _first_write_after_detach(writes)


def test_an_insert_of_entries_takes_the_family_out_first(writes: list[str]) -> None:
    from backend.app.services import clickhouse_variant_storage as storage
    from backend.tests.integration._datastores import small_variant

    asyncio.run(storage.insert_small_variant_records("GRCh38", "f1", ["p1"], [small_variant("1-100-A-T", [])]))
    _first_write_after_detach(writes)


def test_an_insert_of_the_shared_rows_alone_leaves_the_family_in(writes: list[str]) -> None:
    from backend.app.services import clickhouse_variant_storage as storage
    from backend.tests.integration._datastores import small_variant

    asyncio.run(
        storage.insert_small_variant_records("GRCh38", "f1", ["p1"], [small_variant("1-100-A-T", [])], entries=False)
    )
    assert "detach f1" not in writes


def test_a_copy_and_a_prune_take_the_family_out_first(writes: list[str]) -> None:
    from backend.app.services import clickhouse_variant_storage as storage

    asyncio.run(storage.prune_family_variant_projects("GRCh38", "f1", project_ids=["p1"]))
    _first_write_after_detach(writes)
    writes.clear()
    asyncio.run(
        storage.copy_family_variants_to_projects("GRCh38", "f1", project_ids=["p1", "p2"], linked_project_ids=["p1"])
    )
    assert writes[0] == "detach f1"


def test_a_summaries_refresh_takes_the_family_out_before_it_moves_the_data_version(writes: list[str]) -> None:
    from backend.app.services import clickhouse_variant_storage as storage

    asyncio.run(storage.refresh_family_small_variant_summaries("GRCh38", "f1"))
    assert writes[0] == "detach f1"
    assert any("family_data_version" in statement for statement in writes[1:])


def test_a_restore_takes_the_family_out_before_its_delete(writes: list[str]) -> None:
    from backend.app.services import clickhouse_family_snapshot as snapshot

    restore = snapshot.FamilyClickHouseSnapshot(
        assembly_name="GRCh38",
        family_uuid="f1",
        token="t",
        specs=[("coga.`GRCh38/SNV_INDEL/entries`", "coga.`GRCh38/SNAPSHOT/t/SNV_INDEL/entries`")],
    )

    async def nothing_missing(_snapshot: Any) -> list[str]:
        return []

    async def no_refresh(*_args: Any) -> None:
        return None

    import backend.app.services.clickhouse_family_snapshot as module

    pytest.MonkeyPatch().setattr(module, "_missing_backup_tables", nothing_missing)
    pytest.MonkeyPatch().setattr(module, "refresh_family_small_variant_summaries", no_refresh)
    pytest.MonkeyPatch().setattr(module, "bump_family_structural_variant_data_version", no_refresh)
    asyncio.run(snapshot.restore_family_clickhouse_state(restore))
    _first_write_after_detach(writes)


# --- the steps of a refresh ----------------------------------------------------------


def test_an_attach_records_its_generation_writes_it_and_drops_older_records(
    clickhouse: _ClickHouse, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[tuple[str, int, str]] = []

    async def write(assembly_name: str, family_uuid: str, generation: _Generation, *, sign: int) -> None:
        written.append((family_uuid, sign, generation.source_project))

    monkeypatch.setattr(summary, "_write_contribution", write)
    clickhouse.answers = [("SELECT min(project_guid)", [("p2",)])]
    generation = asyncio.run(summary._attach("GRCh38", "f1", _STATE))
    assert generation.state == _STATE and generation.generation > 0
    assert written == [("f1", 1, "p2")]
    # The source project: the first of the family's projects holding its rows.
    assert "WHERE project_guid IN %(projects)s AND family_guid = %(family)s AND sign = 1" in clickhouse.statements[0]
    # Its record goes in before its rows, and the older records go once it is in.
    assert clickhouse.statements[1].startswith("INSERT INTO") and "carrier_generations" in clickhouse.statements[1]
    assert "DELETE WHERE family_guid = %(family)s AND generation != %(generation)s" in clickhouse.statements[2]
    assert "mutations_sync = 1" in clickhouse.statements[2]


def test_a_family_without_projects_reads_no_rows_for_its_source(clickhouse: _ClickHouse) -> None:
    assert asyncio.run(summary._source_project("GRCh38", "f1", ())) == ""
    assert clickhouse.statements == []


def test_the_data_versions_are_count_and_sum_of_each_familys_tokens(clickhouse: _ClickHouse) -> None:
    clickhouse.answers = [("family_data_version", [("f1", 3, 99), ("f2", 1, 7)])]
    assert asyncio.run(summary._data_versions("GRCh38", ["f1", "f2"])) == {"f1": "3:99", "f2": "1:7"}
    assert "WHERE family_guid IN %(families)s" in clickhouse.statements[0]
    assert asyncio.run(summary._data_versions("GRCh38", [])) == {}


def test_asking_for_a_rebuild_writes_a_record_of_no_family(clickhouse: _ClickHouse) -> None:
    asyncio.run(summary._request_rebuild("GRCh38", "family f1: CarrierSummaryInconsistent"))
    assert "carrier_generations" in clickhouse.statements[0]
    clickhouse.answers = [("WHERE family_guid = ''", [(1,)])]
    assert asyncio.run(summary._rebuild_requested("GRCh38")) is True
    assert asyncio.run(summary._rebuild_requested("GRCh38")) is False


@pytest.mark.parametrize("granted", [True, False])
def test_the_summary_is_emptied_only_under_its_exclusive_lock(
    clickhouse: _ClickHouse, monkeypatch: pytest.MonkeyPatch, granted: bool
) -> None:
    taken: list[tuple[bool, bool]] = []

    @asynccontextmanager
    async def session(assembly_name: str, *, exclusive: bool = False, wait: bool = True):
        taken.append((exclusive, wait))
        yield None, granted

    monkeypatch.setattr(summary, "_summary_session", session)
    assert asyncio.run(summary._empty_summary("GRCh38")) is granted
    # Tried, never waited for: a change of a contribution holds it shared meanwhile.
    assert taken == [(True, False)]
    truncated = [statement for statement in clickhouse.statements if statement.startswith("TRUNCATE")]
    assert len(truncated) == (2 if granted else 0)


def test_one_round_reconciles_every_assembly_with_a_project(monkeypatch: pytest.MonkeyPatch) -> None:
    rounds: list[tuple[str, str, float | None]] = []

    async def assemblies() -> list[tuple[str, str]]:
        return [("a1", "GRCh38"), ("a2", "T2T")]

    async def reconcile(assembly_id: str, assembly_name: str, *, time_budget_seconds: float | None = None) -> Any:
        rounds.append((assembly_id, assembly_name, time_budget_seconds))
        return summary.CarrierSummaryStatus(refreshed=["f1"] if assembly_id == "a1" else [])

    monkeypatch.setattr(summary, "_assemblies_with_projects", assemblies)
    monkeypatch.setattr(summary, "reconcile_variant_carriers", reconcile)
    asyncio.run(summary.reconcile_all_variant_carriers(time_budget_seconds=5.0))
    assert rounds == [("a1", "GRCh38", 5.0), ("a2", "T2T", 5.0)]


def test_markers_are_read_by_family_from_the_head_of_the_table(clickhouse: _ClickHouse) -> None:
    clickhouse.answers = [("HAVING n != 0", [("f1", 42, 1), ("f2", 7, -1)])]
    assert asyncio.run(summary._applied_generations("GRCh38")) == {"f1": {42: 1}, "f2": {7: -1}}
    assert "WHERE c.xpos = 0 AND c.key = 0 GROUP BY" in clickhouse.statements[0]
    assert asyncio.run(summary._applied_generations("GRCh38", [])) == {}


class _LockSession:
    """A session whose advisory-lock statements answer ``granted``; records them."""

    def __init__(self, granted: bool) -> None:
        self.granted = granted
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_LockSession":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def begin(self) -> "_LockSession":
        return self

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append((str(statement), dict(params or {})))
        granted = self.granted
        return type("R", (), {"scalar_one": staticmethod(lambda: granted)})()


@pytest.mark.parametrize(
    ("exclusive", "wait", "function"),
    [
        (False, True, "pg_advisory_xact_lock_shared"),
        (True, False, "pg_try_advisory_xact_lock"),
    ],
)
def test_the_summary_lock_is_one_advisory_lock_per_assembly(
    monkeypatch: pytest.MonkeyPatch, exclusive: bool, wait: bool, function: str
) -> None:
    session = _LockSession(granted=False)
    monkeypatch.setattr(summary, "get_postgres_sessionmaker", lambda: lambda: session)

    async def run() -> bool:
        async with summary._summary_session("GRCh38", exclusive=exclusive, wait=wait) as (_session, granted):
            return granted

    granted = asyncio.run(run())
    statement, params = session.statements[0]
    assert f"SELECT {function}(hashtext(:k))" in statement
    assert params == {"k": "variant-carriers:GRCh38"}
    # A lock waited for is granted; a tried one as the database answers.
    assert granted is wait


def test_a_family_is_refreshed_only_when_no_writer_holds_its_lock() -> None:
    session = _LockSession(granted=False)
    assert asyncio.run(summary._try_lock_family(session, "f1")) is False  # type: ignore[arg-type]
    statement, params = session.statements[0]
    assert "pg_try_advisory_xact_lock(hashtext(:k))" in statement
    assert params == {"k": "family-variant-writes:small_variants:f1"}


def test_while_the_summary_is_rebuilt_every_written_family_in_scope_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reconcile_fakes(
        monkeypatch,
        desired={"written": _STATE, "never": FamilyCarrierState(("p1",), (), (), "0:0")},
        applied={},
        rebuild=True,
    )

    async def family_states(*_args: Any) -> Any:
        return {"written": _STATE, "never": FamilyCarrierState(("p1",), (), (), "0:0")}

    monkeypatch.setattr(summary, "_family_states", family_states)
    assert asyncio.run(summary.pending_variant_carrier_families(None, "a1", "GRCh38", ["p1"])) == ["written"]


def test_an_inconsistent_summary_leaves_every_family_in_scope_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    _reconcile_fakes(monkeypatch, desired={"a": _STATE}, applied={})

    async def family_states(*_args: Any) -> Any:
        return {"a": _STATE}

    async def inconsistent(*_args: Any, **_kwargs: Any) -> Any:
        raise summary.CarrierSummaryInconsistent("markers")

    monkeypatch.setattr(summary, "_family_states", family_states)
    monkeypatch.setattr(summary, "_applied", inconsistent)
    assert asyncio.run(summary.pending_variant_carrier_families(None, "a1", "GRCh38", ["p1"])) == ["a"]
