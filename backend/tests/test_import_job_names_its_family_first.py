"""A package import writes nothing of a family before its job names that family (TF-06 H16).

The report sign-out refuses a family while a package-import job of it is queued, validating
or running, and finds that job by the family the job records (``family_id``) or the one its
request named (``report_signout_service._ACTIVE_IMPORT_JOB``). An import writes the family's
pedigree, samples and provenance before it takes the family's variant-write locks, so until
it holds them its job is all that keeps a sign-out out. The update that recorded the job as
``running`` on its family was one of the import's best-effort progress updates: when it
failed, or matched no row because another worker had claimed the job, the import registered
the family all the same, under a job no sign-out could find.

The job row is modelled as Postgres keeps it: an UPDATE applies only when its WHERE clause
matches the row (the job's id, the worker that runs it, its status) and others see it once
it is committed; a failed statement aborts its transaction until it is rolled back; an
injected fault fails a statement before any of it applies, as a database error does. Each
write of the family is recorded with whether a sign-out, reading the committed job row at
that moment, would have found the import.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable

import pytest
from sqlalchemy.exc import OperationalError

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import family_package_import as package_import
from backend.app.services import sv_gene_index_service, variant_ranking_cache
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import FamilyMetadataContext

FAMILY = "FAM001"
JOB = "00000000-0000-0000-0000-0000000000a1"
WORKER = "worker-1"

# An injected fault: called with an UPDATE's assignments and the row it would change.
Fault = Callable[[dict[str, Any], dict[str, Any]], bool]


def _a_sign_out_finds_the_import(row: dict[str, Any]) -> bool:
    """``report_signout_service._ACTIVE_IMPORT_JOB`` over the committed job row."""
    metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])
    return (
        row["status"] in {"queued", "validating", "running"}
        and not row["dry_run"]
        and (row["family_id"] == FAMILY or metadata.get("requested_family_id") == FAMILY)
    )


_SELECT = re.compile(r"^SELECT .+? FROM family_import_jobs WHERE (?P<where>.+)$")
_UPDATE = re.compile(r"^UPDATE family_import_jobs SET (?P<set>.+?) WHERE (?P<where>.+)$")
_ASSIGNMENT = re.compile(r"(\w+) = (?:CAST\(:(\w+) AS \w+\)|:(\w+)|'([^']*)'|(NULL)|(now\(\)))")
_CONDITION = re.compile(
    r"^(\w+) (?:= CAST\(:(\w+) AS uuid\)|= :(\w+)|= '([^']*)'|IN \(([^)]*)\))$"
)


def _assignments(clause: str, params: dict[str, Any]) -> dict[str, Any]:
    found = _ASSIGNMENT.findall(clause)
    assert len(found) == clause.count(" = "), f"unmodelled assignment in: {clause}"
    changes: dict[str, Any] = {}
    for column, cast_param, param, literal, null, now in found:
        if cast_param:
            changes[column] = params[cast_param]
        elif param:
            changes[column] = params[param]
        elif null:
            changes[column] = None
        elif now:  # the database's clock
            changes[column] = datetime.now(timezone.utc)
        else:
            changes[column] = literal
    return changes


def _matches(where: str, row: dict[str, Any], params: dict[str, Any]) -> bool:
    for condition in where.split(" AND "):
        match = _CONDITION.match(condition)
        assert match, f"unmodelled condition: {condition}"
        column, uuid_param, param, literal, members = match.groups()
        value = row[column]
        if uuid_param:
            holds = value == params[uuid_param]
        elif param:
            holds = value == params[param]
        elif literal is not None:
            holds = value == literal
        else:
            holds = value in {member.strip().strip("'") for member in members.split(",")}
        if not holds:
            return False
    return True


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _JobTable:
    """One job's ``family_import_jobs`` row, and what happened to it and to the family."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.committed = row
        self.faults: list[Fault] = []
        self.events: list[tuple[Any, ...]] = []
        self.raised: BaseException | None = None

    def session(self) -> "_Session":
        return _Session(self)

    def commit(self, row: dict[str, Any]) -> None:
        before = (self.committed["status"], self.committed["family_id"])
        self.committed = row
        if (row["status"], row["family_id"]) != before:
            self.events.append(("job", row["status"], row["family_id"]))

    def wrote(self, what: str) -> None:
        self.events.append(("write", what, _a_sign_out_finds_the_import(self.committed)))

    @property
    def writes(self) -> list[tuple[Any, ...]]:
        return [event for event in self.events if event[0] == "write"]


class _Session:
    def __init__(self, table: _JobTable) -> None:
        self.table = table
        self.pending: dict[str, Any] | None = None
        self.aborted = False

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        self.pending = None  # closed without a commit: rolled back
        self.aborted = False
        return False

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        params = params or {}
        sql = " ".join(str(statement).split())
        assert "family_import_jobs" in sql, f"unmodelled statement: {sql}"
        if self.aborted:
            raise OperationalError(sql, params, Exception("current transaction is aborted"))
        row = self.pending if self.pending is not None else self.table.committed
        if select := _SELECT.match(sql):
            return _Result([copy.deepcopy(row)] if _matches(select["where"], row, params) else [])
        update = _UPDATE.match(sql)
        assert update, f"unmodelled statement: {sql}"
        if not _matches(update["where"], row, params):
            return _Result(rowcount=0)
        changes = _assignments(update["set"], params)
        if any(fault(changes, row) for fault in self.table.faults):
            self.aborted = True
            raise OperationalError(sql, params, Exception("injected: the job row could not be updated"))
        self.pending = {**row, **changes}
        return _Result(rowcount=1)

    async def commit(self) -> None:
        if self.aborted:
            raise OperationalError("COMMIT", {}, Exception("current transaction is aborted"))
        if self.pending is not None:
            self.table.commit(self.pending)
            self.pending = None

    async def rollback(self) -> None:
        self.pending = None
        self.aborted = False


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id=FAMILY,
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": "uuid-S1", "sample_id": "S1", "sex": "male", "role": "proband", "affected": True},
            {"sample_uuid": "uuid-S2", "sample_id": "S2", "sex": "female", "role": "mother", "affected": False},
        ],
        sample_uuid_to_name={"uuid-S1": "S1", "uuid-S2": "S2"},
        sample_name_to_uuid={"S1": "uuid-S1", "S2": "uuid-S2"},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _JobTable:
    """A re-import of FAM001, claimed by worker-1, whose request named no family: a sign-out
    can find it only by the family its job records. Every write of the family is recorded."""
    root = tmp_path / FAMILY
    root.mkdir()
    (root / "family.ped").write_text(f"{FAMILY} S1 0 0 1 2\n{FAMILY} S2 0 0 2 1\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(
        f"schema_version: 1\nfamily_id: {FAMILY}\nped: family.ped\n", encoding="utf-8"
    )
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    now = datetime.now(timezone.utc)
    table = _JobTable(
        {
            "id": JOB,
            "submitted_path": str(root),
            "family_id": None,
            "project_id": "project-uuid",
            "status": "validating",
            "dry_run": False,
            "worker_id": WORKER,
            "requested_by": "admin@example.com",
            "requested_at": now,
            "started_at": now,
            "heartbeat_at": now,
            "completed_at": None,
            "validation_errors": "[]",
            "validation_warnings": "[]",
            "logs": "[]",
            "dataset_summaries": "[]",
            "metadata": {"requested_family_id": None, "conflict_mode": "update"},
            "error": None,
        }
    )

    async def user(_session: Any, _email: str) -> CurrentUser:
        return _admin()

    async def no_warnings(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def register(_session: Any, **_kwargs: Any) -> tuple[FamilyMetadataContext, bool]:
        # The import's first write: the pedigree, the samples and the package provenance.
        table.wrote("family registered")
        return _context(), False

    @asynccontextmanager
    async def hold(_family_uuid: str, **_kwargs: Any):
        table.events.append(("variant-write locks held",))
        yield

    async def import_dataset(_session: Any, *, summary: Any, progress: Any = None, **_kwargs: Any) -> Any:
        table.wrote(f"dataset {summary.dataset_type}")
        if progress is not None:  # a long dataset reports as it goes
            await progress(summary.model_copy(update={"status": "running", "message": "halfway"}))
        return summary.model_copy(update={"status": "imported", "message": "ok"})

    async def context(*_args: Any, **_kwargs: Any) -> FamilyMetadataContext:
        return _context()

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    for name, value in {
        "get_postgres_sessionmaker": lambda: table.session,
        "get_current_user_by_email": user,
        "_existing_package_entity_warnings": no_warnings,
        "_ensure_family_from_ped": register,
        "hold_family_variant_writes": hold,
        "_enabled_dataset_summaries": lambda _validation: [
            FamilyImportDatasetSummary(dataset_type="snv", status="valid")
        ],
        "_import_dataset": import_dataset,
        # The family's import state (test_import_crash_leaves_family_marked.py).
        "_mark_family_import_unfinished": nothing,
        "_end_import_failed_before_datasets": nothing,
        "_record_family_import_finished": nothing,
        "_clear_family_import_incomplete": nothing,
        "build_family_metadata_context": context,
        "precompute_family_haplotype_lineage": nothing,
    }.items():
        monkeypatch.setattr(package_import, name, value)
    monkeypatch.setattr(variant_ranking_cache, "clear_family_ranking_cache", nothing)
    monkeypatch.setattr(sv_gene_index_service, "clear_family_sv_gene_index", nothing)
    return table


async def _run(table: _JobTable) -> None:
    """Run the job as the worker does; what it raises, the worker logs."""
    try:
        await package_import.run_family_import_job(job_id=JOB, worker_id=WORKER)
    except Exception as exc:  # noqa: BLE001 - recorded for the assertions
        table.raised = exc


def _sets_running(changes: dict[str, Any], _row: dict[str, Any]) -> bool:
    return changes.get("status") == "running"


@pytest.mark.asyncio
async def test_the_job_names_its_family_as_running_before_the_import_writes_it(jobs) -> None:
    await _run(jobs)

    assert jobs.raised is None
    assert jobs.committed["status"] == "completed"
    running = jobs.events.index(("job", "running", FAMILY))
    first_write = jobs.events.index(jobs.writes[0])
    assert running < first_write, jobs.events
    # Every write of the family happened under a job a sign-out finds.
    assert jobs.writes == [
        ("write", "family registered", True),
        ("write", "dataset snv", True),
    ]


@pytest.mark.asyncio
async def test_an_import_whose_job_cannot_name_its_family_writes_nothing(jobs) -> None:
    jobs.faults.append(_sets_running)

    await _run(jobs)

    # Before: the progress update failed, was logged, and the import registered the family
    # under a job that named no family, so a sign-out then would have frozen it part-way.
    assert jobs.writes == []
    assert ("variant-write locks held",) not in jobs.events
    assert jobs.committed["status"] == "failed"
    assert jobs.committed["completed_at"] is not None
    assert jobs.committed["worker_id"] is None
    error = jobs.committed["error"]
    assert error and FAMILY in error and "nothing of it was written" in error, error


@pytest.mark.asyncio
async def test_an_import_whose_job_another_worker_claimed_writes_nothing(jobs, monkeypatch) -> None:
    real_load = package_import.load_validated_family_package

    def load_while_another_worker_claims_the_job(*args: Any, **kwargs: Any):
        # Its heartbeat went stale while the package was read (staged from a bucket, say):
        # another worker claimed the job, as claim_next_family_import_job does.
        jobs.commit(
            {
                **jobs.committed,
                "worker_id": "worker-2",
                "status": "validating",
                "heartbeat_at": datetime.now(timezone.utc),
            }
        )
        return real_load(*args, **kwargs)

    monkeypatch.setattr(package_import, "load_validated_family_package", load_while_another_worker_claims_the_job)

    await _run(jobs)

    # Before: worker-1's progress update matched no row, which went unnoticed, and it
    # registered the family beside worker-2's run of the same job.
    assert jobs.writes == []
    # The job is worker-2's: worker-1 neither ran it nor ended it.
    assert jobs.committed["worker_id"] == "worker-2"
    assert jobs.committed["status"] == "validating"
    assert jobs.committed["error"] is None and jobs.committed["completed_at"] is None


@pytest.mark.asyncio
async def test_when_the_job_cannot_be_ended_failed_either_nothing_is_written(jobs) -> None:
    jobs.faults.append(lambda _changes, _row: True)  # the job row cannot be updated at all

    await _run(jobs)

    assert jobs.writes == []
    # The worker logs the failure; the job stays claimed, naming no family, until a worker
    # claims it again once its heartbeat is stale and runs it from the start.
    assert jobs.raised is not None
    assert (jobs.committed["status"], jobs.committed["family_id"]) == ("validating", None)
    assert jobs.committed["worker_id"] == WORKER


@pytest.mark.asyncio
async def test_a_progress_update_that_fails_once_the_job_names_its_family_does_not_stop_the_import(
    jobs,
) -> None:
    # Once the job is running on the family, the updates that carry its logs, the dataset
    # summaries and the heartbeat are informational: the sign-out reads the status and the
    # family, which they leave as they are.
    jobs.faults.append(
        lambda changes, row: row["status"] == "running"
        and changes.get("status") not in {"completed", "failed"}
    )

    await _run(jobs)

    assert jobs.raised is None
    assert jobs.writes == [
        ("write", "family registered", True),
        ("write", "dataset snv", True),
    ]
    assert jobs.committed["status"] == "completed"
    assert jobs.committed["error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("case", "ends"), [("dry run", "completed"), ("invalid package", "failed")])
async def test_an_import_that_writes_nothing_names_no_family_as_running(jobs, case, ends) -> None:
    if case == "dry run":
        jobs.committed = {**jobs.committed, "dry_run": True}
    else:  # its PED names another family
        Path(jobs.committed["submitted_path"], "family.ped").write_text("OTHER S1 0 0 1 2\n", encoding="utf-8")

    await _run(jobs)

    assert jobs.raised is None
    assert jobs.writes == []
    assert not any(event[:2] == ("job", "running") for event in jobs.events), jobs.events
    assert jobs.committed["status"] == ends
