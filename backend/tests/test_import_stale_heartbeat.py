"""A live import whose heartbeat goes stale (#746, TF-06 H16).

A running import writes its job's heartbeat every minute, and a worker that finds it ten
minutes old takes the import's process for stopped: it ends the job as interrupted and
drops the job's overwrite backups. But the process can be alive and only unable to write
the heartbeat (no database connection for it, an event loop held by synchronous work). Its
import went on: its later progress and its end matched no job row, unnoticed; a failed
overwrite's restore deleted the family's rows from its first table, then failed for want of
the dropped backup; and the import-incomplete flag still named what the overwrite had
imported, so the banner and the sign-out read "snv did import" of small variants the
restore had just deleted. Now:

* the heartbeat that finds its job no longer its worker's stops the import where it is, as
  a stopped process is stopped: none of its fail-clean runs, its family keeps the import's
  ``import_unfinished`` entry, the job keeps the record the claim gave it, and the
  variant-write locks are released; a heartbeat that cannot be written stops nothing;
* a restore checks every backup table before its first delete, and deletes nothing when one
  is missing;
* after a failed restore the flag names as imported only what the restore provably left
  alone: all of it when the restore deleted nothing, none when it failed in any other way
  (part-way, say); such a restore may have removed rows of any dataset, so its flag names
  every dataset of the import as failed, and the family stays flagged until an import has
  imported each again (SAFE-13; before, it named only the one that had failed, whose import
  alone cleared the flag);
* the heartbeat and the stale check use the database's clock, and an update of the job that
  matches no row is said in the log, by the job and the outcome alone.

The job row, the family's import state and its ClickHouse tables are modelled as their
statements document them: the statements run against Postgres in
integration/test_import_crash_bookkeeping_integration.py, and the whole path against
Postgres and ClickHouse in e2e/test_e2e_import_stale_heartbeat.py.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import copy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from sqlalchemy.exc import OperationalError

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import clickhouse_family_snapshot as snapshots
from backend.app.services import family_package_import as package_import
from backend.app.services import family_package_jobs as jobs
from backend.app.services import family_package_registration as registration
from backend.app.services import sv_gene_index_service, variant_ranking_cache
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import FamilyMetadataContext

FAMILY = "FAM001"
FAMILY_UUID = "family-uuid"
JOB = "00000000-0000-0000-0000-0000000000b7"
WORKER = "worker-1"


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
        family_uuid=FAMILY_UUID,
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


async def _until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), timeout)


# --- the model: the job's row and the family's import state -----------------------------


class _Store:
    """The job's row and the family's import state, as their statements change them.

    ``unfinished`` is the family's ``metadata.import_unfinished`` and ``incomplete`` its
    ``metadata.import_incomplete``. ``reachable`` is whether the heartbeat can reach the
    database; ``lost`` holds each update of the job that matched no row.
    """

    def __init__(self, root: Path) -> None:
        self.job: dict[str, Any] = {
            "id": JOB,
            "submitted_path": str(root),
            "project_id": "project-uuid",
            "family_id": None,
            "status": "validating",
            "dry_run": False,
            "worker_id": WORKER,
            "requested_by": "admin@example.com",
            "metadata": {"requested_family_id": None, "conflict_mode": "update"},
            "logs": [],
            "dataset_summaries": [],
            "error": None,
            "completed_at": None,
        }
        self.unfinished: dict[str, Any] = {}
        self.incomplete: dict[str, Any] | None = None
        self.events: list[tuple[Any, ...]] = []
        self.started: list[str] = []
        self.reachable = True
        self.failed_beats = 0
        self.beats: list[bool] = []  # what each written beat found: the job still this worker's
        self.lost: list[str] = []

    def mine(self, worker_id: str, *statuses: str) -> bool:
        """Whether a statement naming this worker (and these statuses) matches the row."""
        return self.job["worker_id"] == worker_id and (not statuses or self.job["status"] in statuses)


class _Session:
    """A database session: the job's row as ``run_family_import_job`` reads it, and the
    family's import state as each statement documents it (told apart by what it binds, as
    test_import_crash_leaves_family_marked.py models it). Each call yields to the event
    loop, as a call to the database does."""

    def __init__(self, store: _Store) -> None:
        self.store = store

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        await asyncio.sleep(0)
        sql = " ".join(str(statement).split())
        params = params or {}
        store = self.store
        if "FROM family_import_jobs" in sql:
            row = store.job
            found = (
                copy.deepcopy(row)
                if row["worker_id"] == params["worker_id"] and row["status"] == "validating"
                else None
            )
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: found))
        if sql.startswith("SELECT"):
            # The family row's import state, read under its lock.
            state = {
                "family_uuid": FAMILY_UUID,
                "flag": copy.deepcopy(store.incomplete),
                "unfinished": copy.deepcopy(store.unfinished) or None,
            }
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: state))
        assert sql.startswith("UPDATE families"), f"unmodelled statement: {sql}"
        if "entry" in params:
            entry = json.loads(params["entry"])
            store.unfinished[params["import_key"]] = entry
            store.events.append(("marked", tuple(entry["finished_datasets"])))
        elif "payload" in params:
            # An import's failure: the merged flag, and its own entry removed.
            store.unfinished.pop(params.get("import_key"), None)
            store.incomplete = json.loads(params["payload"])
            store.events.append(("flagged",))
        elif "flag" in params:
            # A completed import's clear.
            store.incomplete = json.loads(params["flag"])
            store.unfinished = json.loads(params["unfinished"]) or {}
            store.events.append(("cleared",))
        else:
            store.unfinished.pop(params["import_key"], None)
            store.events.append(("ended",))
        return SimpleNamespace(rowcount=1)


def _job_statements(monkeypatch: pytest.MonkeyPatch, store: _Store) -> None:
    """The job's statements, as family_package_jobs documents each: an update applies only
    while its WHERE clause (the job, its worker, its status) matches the row."""

    async def record_family(_session: Any, *, job_id: str, worker_id: str, family_id: str) -> bool:
        await asyncio.sleep(0)
        if not store.mine(worker_id, "validating", "running"):
            return False
        store.job.update(status="running", family_id=family_id)
        return True

    async def beat(_session: Any, *, job_id: str, worker_id: str) -> bool:
        await asyncio.sleep(0)
        if not store.reachable:
            store.failed_beats += 1
            raise OperationalError(
                "UPDATE family_import_jobs", {}, ConnectionRefusedError("no connection")
            )
        store.beats.append(store.mine(worker_id, "validating", "running"))
        return store.beats[-1]

    async def update(
        _session: Any,
        *,
        job_id: str,
        worker_id: str | None,
        status: str | None = None,
        family_id: str | None = None,
        validation: Any = None,
        datasets: list[FamilyImportDatasetSummary] | None = None,
        logs: list[str] | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> bool:
        await asyncio.sleep(0)
        if worker_id is not None and not store.mine(worker_id):
            store.lost.append(status or "progress")
            return False
        if status is not None:
            store.job["status"] = status
        if family_id is not None:
            store.job["family_id"] = family_id
        if datasets is not None:
            store.job["dataset_summaries"] = [(item.dataset_type, item.status) for item in datasets]
        if logs is not None:
            store.job["logs"] = list(logs)
        if error is not None:
            store.job["error"] = error
        if completed:
            store.job.update(completed_at="ended", worker_id=None)
        return True

    monkeypatch.setattr(package_import, "_record_job_family", record_family)
    monkeypatch.setattr(package_import, "_beat_family_import_job", beat)
    monkeypatch.setattr(package_import, "_update_job_progress", update)


def _claimed_as_interrupted(store: _Store) -> dict[str, Any]:
    """What ``claim_next_family_import_job`` does to a running job whose heartbeat is stale:
    ended ``failed`` as interrupted, no longer any worker's, a line added to its log."""
    assert store.job["status"] == "running"
    store.job.update(
        status="failed",
        worker_id=None,
        completed_at="claimed",
        error=jobs.FAMILY_IMPORT_INTERRUPTED_ERROR,
        logs=[
            *store.job["logs"],
            "The import stopped part-way: its worker's heartbeat went stale while it was "
            "running on the family. Ended 2026-10-09 10:00 UTC instead of run again.",
        ],
    )
    return copy.deepcopy(store.job)


class _Held:
    """A dataset the import is held inside until the test lets it go on; it then reads on
    for ``reading`` seconds and ends with ``then``: a status, or an exception it raises."""

    def __init__(self, *, reading: float = 0.0, then: Any = "imported") -> None:
        self.inside = asyncio.Event()
        self.go_on = asyncio.Event()
        self.reading = reading
        self.then = then


def _datasets(monkeypatch: pytest.MonkeyPatch, store: _Store, outcomes: dict[str, Any]) -> None:
    """Run the datasets in ``outcomes`` order: a status ends one so, an exception fails it,
    a callable is awaited first (the dataset's writes), a ``_Held`` holds it."""
    monkeypatch.setattr(
        package_import,
        "_enabled_dataset_summaries",
        lambda _validation: [
            FamilyImportDatasetSummary(dataset_type=name, status="valid") for name in outcomes
        ],
    )

    async def import_dataset(_session: Any, *, summary: Any, **_kwargs: Any) -> Any:
        store.started.append(summary.dataset_type)
        outcome = outcomes[summary.dataset_type]
        if isinstance(outcome, _Held):
            outcome.inside.set()
            await outcome.go_on.wait()
            await asyncio.sleep(outcome.reading)
            outcome = outcome.then
        elif callable(outcome):
            outcome = await outcome()
        if isinstance(outcome, Exception):
            raise outcome
        return summary.model_copy(update={"status": outcome, "message": outcome})

    monkeypatch.setattr(package_import, "_import_dataset", import_dataset)


@pytest.fixture
def job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Store:
    """A re-import of FAM001 (it exists), claimed by worker-1 and not yet run. The family's
    variant-write locks record when they are taken and released."""
    root = tmp_path / FAMILY
    root.mkdir()
    (root / "family.ped").write_text(f"{FAMILY} S1 0 0 1 2\n{FAMILY} S2 0 0 2 1\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(
        f"schema_version: 1\nfamily_id: {FAMILY}\nped: family.ped\n", encoding="utf-8"
    )
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    store = _Store(root)

    async def user(_session: Any, _email: str) -> CurrentUser:
        return _admin()

    async def register(session: Any, *, import_mark: Any = None, **_kwargs: Any) -> Any:
        await registration._mark_family_import_unfinished(
            session, family_uuid=FAMILY_UUID, mark=import_mark
        )
        return _context(), False

    @asynccontextmanager
    async def hold(_family_uuid: str, **_kwargs: Any):
        store.events.append(("locks held",))
        try:
            yield
        finally:
            store.events.append(("locks released",))

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def no_warnings(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    for name, value in {
        "get_postgres_sessionmaker": lambda: lambda: _Session(store),
        "get_current_user_by_email": user,
        "_ensure_family_from_ped": register,
        "hold_family_variant_writes": hold,
        "_existing_package_entity_warnings": no_warnings,
        "build_family_metadata_context": nothing,
        "precompute_family_haplotype_lineage": nothing,
    }.items():
        monkeypatch.setattr(package_import, name, value)
    monkeypatch.setattr(variant_ranking_cache, "clear_family_ranking_cache", nothing)
    monkeypatch.setattr(sv_gene_index_service, "clear_family_sv_gene_index", nothing)
    _job_statements(monkeypatch, store)
    return store


def _run_job() -> asyncio.Task[None]:
    return asyncio.create_task(package_import.run_family_import_job(job_id=JOB, worker_id=WORKER))


def _warnings(caplog: pytest.LogCaptureFixture, text: str) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == package_import.logger.name
        and record.levelno == logging.WARNING
        and text in record.getMessage()
    ]


# --- the orphaned import is stopped ------------------------------------------------------


@pytest.mark.asyncio
async def test_an_import_whose_job_another_worker_ended_is_stopped_by_its_heartbeat(
    job, monkeypatch, caplog
) -> None:
    # The issue's first case: the job is claimed while its import is held inside a
    # dataset, and the import is then let go on.
    snv = _Held(reading=0.5)
    _datasets(monkeypatch, job, {"snv": snv, "coverage": "imported"})
    monkeypatch.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", 0.01)
    caplog.set_level(logging.WARNING, logger=package_import.logger.name)

    run = _run_job()
    await asyncio.wait_for(snv.inside.wait(), timeout=5)
    # Its heartbeat cannot be written (no database connection for it): each beat fails and
    # is tried again, and that alone stops nothing.
    job.reachable = False
    await _until(lambda: job.failed_beats >= 3)
    assert not run.done()
    # Ten minutes on, a worker takes it for stopped and ends the job as interrupted.
    claimed = _claimed_as_interrupted(job)
    # The database is reachable again, and the import goes on.
    job.reachable = True
    snv.go_on.set()
    await asyncio.wait_for(run, timeout=5)

    # Before: its heartbeat stopped and the import went on, under a job that said it was
    # interrupted: it finished snv, imported coverage and removed its entry.
    assert job.started == ["snv"]
    entry = job.unfinished[JOB]
    assert entry["finished_datasets"] == []
    assert registration.pending_datasets(entry) == {"coverage", "snv"}
    assert job.incomplete is None
    assert not any(event[0] in {"flagged", "cleared", "ended"} for event in job.events)
    # Its variant-write locks went with their transaction.
    assert job.events[-1] == ("locks released",)
    # The job keeps the record the claim gave it, and the import tried no end of its own.
    assert job.job == claimed
    assert not {"completed", "failed"} & set(job.lost)
    (stopped,) = _warnings(caplog, "was stopped where it was")
    assert stopped.args == (JOB,)


@pytest.mark.asyncio
async def test_a_heartbeat_that_cannot_be_written_does_not_stop_the_import(job, monkeypatch) -> None:
    # Only a beat that finds the job no longer this worker's stops the import: one that
    # fails (a lost connection, say) is tried again.
    snv = _Held()
    _datasets(monkeypatch, job, {"snv": snv, "coverage": "imported"})
    monkeypatch.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", 0.01)
    job.reachable = False

    run = _run_job()
    await asyncio.wait_for(snv.inside.wait(), timeout=5)
    await _until(lambda: job.failed_beats >= 3)
    snv.go_on.set()
    await asyncio.wait_for(run, timeout=5)

    assert job.started == ["snv", "coverage"]
    assert job.job["status"] == "completed" and job.job["worker_id"] is None
    assert job.unfinished == {} and job.incomplete is None


@pytest.mark.asyncio
async def test_an_import_that_has_ended_is_left_to_record_its_end(job, monkeypatch, caplog) -> None:
    # The import's own end ends the job; a beat that comes while that update commits finds
    # no row, and stops nothing: the import has ended.
    _datasets(monkeypatch, job, {"snv": "imported"})
    monkeypatch.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", 0.01)
    caplog.set_level(logging.WARNING, logger=package_import.logger.name)
    recorded = package_import._update_job_progress

    async def slow_end(session: Any, **kwargs: Any) -> bool:
        written = await recorded(session, **kwargs)
        if kwargs.get("completed"):
            await _until(lambda: False in job.beats)  # committing while the heartbeat beats
        return written

    monkeypatch.setattr(package_import, "_update_job_progress", slow_end)

    await asyncio.wait_for(_run_job(), timeout=5)

    assert (job.job["status"], job.job["worker_id"]) == ("completed", None)
    assert _warnings(caplog, "was stopped where it was") == []
    assert _warnings(caplog, "could not record it") == []


@pytest.mark.asyncio
async def test_a_worker_that_is_itself_stopped_stops_its_import_as_before(job, monkeypatch) -> None:
    # The backend shutting down cancels its workers: the cancellation goes on up, and is
    # not taken for the heartbeat's stop of an import whose job was ended.
    snv = _Held()
    _datasets(monkeypatch, job, {"snv": snv})
    run = _run_job()
    await asyncio.wait_for(snv.inside.wait(), timeout=5)

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert registration.pending_datasets(job.unfinished[JOB]) == {"snv"}
    assert job.events[-1] == ("locks released",)
    assert (job.job["status"], job.job["worker_id"]) == ("running", WORKER)


@pytest.mark.asyncio
async def test_a_worker_stopped_as_its_heartbeat_stops_the_import_is_stopped_too(job, monkeypatch) -> None:
    snv = _Held()
    _datasets(monkeypatch, job, {"snv": snv})
    monkeypatch.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", 0.01)
    run = _run_job()
    await asyncio.wait_for(snv.inside.wait(), timeout=5)

    async def beat_as_the_backend_shuts_down(_session: Any, *, job_id: str, worker_id: str) -> bool:
        run.cancel()  # the backend stops this worker, as the heartbeat finds the job ended
        return False

    monkeypatch.setattr(package_import, "_beat_family_import_job", beat_as_the_backend_shuts_down)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, timeout=5)
    assert registration.pending_datasets(job.unfinished[JOB]) == {"snv"}


# --- an update of the job that matches no row is said -----------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("coverage", "outcome"), [("imported", "completed"), (RuntimeError("bad BED"), "failed")])
async def test_an_end_the_job_cannot_record_is_said_in_the_log(job, monkeypatch, caplog, coverage, outcome) -> None:
    # Another worker ended the job while its import ran, and the import ended before its
    # heartbeat (written once a minute) found that.
    snv = _Held()
    _datasets(monkeypatch, job, {"snv": snv, "coverage": coverage})
    caplog.set_level(logging.WARNING, logger=package_import.logger.name)
    run = _run_job()
    await asyncio.wait_for(snv.inside.wait(), timeout=5)
    claimed = _claimed_as_interrupted(job)
    snv.go_on.set()
    await asyncio.wait_for(run, timeout=5)

    # Nothing of how the import ended reached the job.
    assert job.job == claimed
    assert job.lost[-1] == outcome and job.lost.count("progress") > 1
    # Before: each update matched no row, and nothing said so. Now the end is said, and the
    # lost progress once, by the job and the outcome alone: nothing of the import.
    assert [record.args for record in _warnings(caplog, "could not record it")] == [(JOB, outcome)]
    assert [record.args for record in _warnings(caplog, "a progress update matched no row")] == [(JOB,)]


class _Recorder:
    """A session that records each statement and its parameters; an UPDATE changes
    ``rowcount`` rows, and a claim finds no job."""

    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        self.statements.append((" ".join(str(statement).split()), dict(params or {})))
        return SimpleNamespace(
            rowcount=self.rowcount, mappings=lambda: SimpleNamespace(first=lambda: None)
        )

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_job_update_says_whether_it_was_recorded() -> None:
    # Before: it said nothing, so an update another worker's claim had made lost was lost
    # without a trace.
    assert await jobs._update_job_progress(_Recorder(1), job_id=JOB, worker_id=WORKER, logs=["x"]) is True
    assert await jobs._update_job_progress(_Recorder(0), job_id=JOB, worker_id=WORKER, logs=["x"]) is False


# --- one clock ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_heartbeat_is_stamped_and_judged_by_the_databases_clock(monkeypatch) -> None:
    class _NoHostClock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            raise AssertionError("a job statement read this host's clock")

    # Before: each statement bound its host's time, so a worker whose clock ran ahead took a
    # live import for stopped.
    monkeypatch.setattr(jobs, "datetime", _NoHostClock)
    session = _Recorder()

    assert await jobs._record_job_family(session, job_id=JOB, worker_id=WORKER, family_id=FAMILY)
    assert await jobs._beat_family_import_job(session, job_id=JOB, worker_id=WORKER)
    assert await jobs._update_job_progress(session, job_id=JOB, worker_id=WORKER, logs=["x"])
    assert await jobs._update_job_progress(
        session, job_id=JOB, worker_id=WORKER, status="completed", completed=True
    )
    assert await jobs.claim_next_family_import_job(session, worker_id=WORKER) is None

    record, beat, progress, end, claim = session.statements
    for sql, params in session.statements:
        assert not any(isinstance(value, datetime) for value in params.values()), sql
    for sql, _params in (record, beat, progress, end):
        assert "heartbeat_at = now()" in sql
    assert "completed_at = now()" in end[0]
    claim_sql, claim_params = claim
    assert "heartbeat_at < now() - CAST(:stale_after AS interval)" in claim_sql
    assert claim_params["stale_after"] == jobs.FAMILY_IMPORT_STALE_HEARTBEAT
    assert "started_at = COALESCE(job.started_at, now())" in claim_sql
    assert "heartbeat_at = now()" in claim_sql
    # The log line the claim adds names the database's time of the claim.
    assert claim_sql.count("to_char(now() AT TIME ZONE 'UTC'") == 2
    assert claim_params["interrupted_log"].count("%s") == 1
    assert claim_params["reclaimed_log"].count("%s") == 1


# --- the restore, and the flag after one that failed --------------------------------------

_NAME = re.compile(r"`([^`]+)`")


class _ClickHouse:
    """The assembly's family-scoped tables, each a list of rows ``(family, what wrote it)``,
    changed as each statement the snapshot module sends changes them. ``fail`` fails the
    statements it matches, as a ClickHouse error does, before they change anything."""

    def __init__(self, rows: dict[str, list[tuple[str, str]]]) -> None:
        self.tables: dict[str, list[tuple[str, str]]] = {
            f"GRCh38/{rel}": list(rows.get(rel, [])) for rel in snapshots._FAMILY_TABLE_RELS
        }
        self.statements: list[str] = []
        self.fail: Callable[[str], bool] = lambda _sql: False

    def family_rows(self, rel: str) -> list[tuple[str, str]]:
        return [row for row in self.tables[f"GRCh38/{rel}"] if row[0] == FAMILY_UUID]

    def backups(self) -> list[str]:
        return sorted(name for name in self.tables if "/SNAPSHOT/" in name)

    async def execute(self, query: str, params: Any = None) -> Any:
        await asyncio.sleep(0)
        sql = " ".join(query.split())
        self.statements.append(sql)
        if self.fail(sql):
            raise RuntimeError("Code: 241. DB::Exception: Memory limit exceeded. (MEMORY_LIMIT_EXCEEDED)")
        names = _NAME.findall(sql)
        if sql.startswith("SELECT name FROM system.tables"):
            assert params["database"] == settings.clickhouse_database
            return [(name,) for name in params["names"] if name in self.tables]
        if sql.startswith("SELECT name, metadata_modification_time FROM system.tables"):
            return [(name, None) for name in self.backups()]
        if sql.startswith("CREATE TABLE"):
            self.tables[names[0]] = []
        elif sql.startswith("ALTER TABLE"):
            assert sql.endswith("SETTINGS mutations_sync = 1"), sql
            self.tables[names[0]] = [row for row in self.tables[names[0]] if row[0] != params["family_guid"]]
        elif sql.startswith("INSERT INTO"):
            target, source = names
            if source not in self.tables:
                raise RuntimeError(f"Code: 60. DB::Exception: Table {source} does not exist. (UNKNOWN_TABLE)")
            rows = self.tables[source]
            if params:  # the snapshot's copy: the family's rows only
                rows = [row for row in rows if row[0] == params["family_guid"]]
            self.tables[target] = [*self.tables[target], *rows]
        elif sql.startswith("DROP TABLE"):
            self.tables.pop(names[0], None)
        else:
            raise AssertionError(f"unmodelled statement: {sql}")
        return None

    def restore_statements(self) -> list[str]:
        """The statements that rewrite a live table from a backup: its delete and insert."""
        return [
            sql
            for sql in self.statements
            if sql.startswith("ALTER TABLE")
            or (sql.startswith("INSERT INTO") and "/SNAPSHOT/" in sql.split(" FROM ", 1)[1])
        ]


@pytest.fixture
def clickhouse(monkeypatch: pytest.MonkeyPatch) -> _ClickHouse:
    """FAM001's rows (and another family's) in the assembly's tables, the snapshot module
    talking to them; the summaries and the SV data version it moves after a restore are
    recorded."""
    fake = _ClickHouse(
        {
            "SNV_INDEL/entries": [(FAMILY_UUID, "snv before"), (FAMILY_UUID, "snv before"), ("other", "snv")],
            "SV/entries": [(FAMILY_UUID, "sv before")],
            "INTERVAL/entries": [(FAMILY_UUID, "coverage before")],
        }
    )
    fake.after_restore = []  # type: ignore[attr-defined]

    async def ensured(_assembly: str) -> None:
        return None

    async def refreshed(assembly: str, family: str) -> None:
        fake.after_restore.append(("summaries", family))  # type: ignore[attr-defined]

    async def bumped(assembly: str, family: str) -> None:
        fake.after_restore.append(("sv data version", family))  # type: ignore[attr-defined]

    monkeypatch.setattr(snapshots, "execute_clickhouse", fake.execute)
    monkeypatch.setattr(snapshots, "ensure_clickhouse_variant_tables", ensured)
    monkeypatch.setattr(snapshots, "ensure_clickhouse_interval_table", ensured)
    monkeypatch.setattr(snapshots, "refresh_family_small_variant_summaries", refreshed)
    monkeypatch.setattr(snapshots, "bump_family_structural_variant_data_version", bumped)
    return fake


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, "SNV_INDEL/entries", "INTERVAL/entries"])
async def test_a_restore_deletes_nothing_unless_its_whole_backup_is_there(clickhouse, missing) -> None:
    snapshot = await snapshots.snapshot_family_clickhouse_state("GRCh38", FAMILY_UUID, owner=JOB)
    assert len(clickhouse.backups()) == 5
    clickhouse.tables["GRCh38/SNV_INDEL/entries"] = [("other", "snv"), (FAMILY_UUID, "snv after")]
    if missing:
        clickhouse.tables.pop(f"GRCh38/SNAPSHOT/{JOB}/{missing}")
    clickhouse.statements.clear()

    if missing is None:
        await snapshots.restore_family_clickhouse_state(snapshot)
        assert clickhouse.family_rows("SNV_INDEL/entries") == [(FAMILY_UUID, "snv before")] * 2
        assert clickhouse.after_restore == [("summaries", FAMILY_UUID), ("sv data version", FAMILY_UUID)]
        # Checked once, before anything is deleted; then each table deleted (a mutation
        # waited for, never cancel rows) and filled again.
        check, *rewrite = clickhouse.statements
        assert check.startswith("SELECT name FROM system.tables")
        assert len(rewrite) == 10 and rewrite == clickhouse.restore_statements()
        assert [sql.split()[0] for sql in rewrite] == ["ALTER", "INSERT"] * 5
        return

    with pytest.raises(Exception) as refused:
        await snapshots.restore_family_clickhouse_state(snapshot)
    # Before: it deleted the family's rows table by table and failed at the first table
    # whose backup was gone, having removed them.
    assert clickhouse.restore_statements() == []
    assert clickhouse.family_rows("SNV_INDEL/entries") == [(FAMILY_UUID, "snv after")]
    assert clickhouse.family_rows("INTERVAL/entries") == [(FAMILY_UUID, "coverage before")]
    assert clickhouse.after_restore == []
    (check,) = clickhouse.statements
    assert check.startswith("SELECT name FROM system.tables")
    assert type(refused.value).__name__ == "FamilyRestoreRefused"


def _overwrite(clickhouse: _ClickHouse) -> Callable[[], Any]:
    """The SNV dataset of an overwrite: it replaces the family's small variants."""

    async def write() -> str:
        table = clickhouse.tables["GRCh38/SNV_INDEL/entries"]
        clickhouse.tables["GRCh38/SNV_INDEL/entries"] = [
            *(row for row in table if row[0] != FAMILY_UUID),
            (FAMILY_UUID, "snv overwrite"),
        ]
        return "imported"

    return write


def _snapshot_postgres(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    restored: list[str] = []

    async def snapshot(*_args: Any, **_kwargs: Any) -> str:
        return "postgres snapshot"

    async def restore(*_args: Any, **_kwargs: Any) -> None:
        restored.append("postgres restored")

    monkeypatch.setattr(package_import, "snapshot_family_postgres_state", snapshot)
    monkeypatch.setattr(package_import, "restore_family_postgres_state", restore)
    return restored


async def _overwrite_import(job: _Store, job_id: str = JOB) -> Any:
    return await package_import.execute_family_package_import(
        _Session(job),  # type: ignore[arg-type]
        folder_path=job.job["submitted_path"],
        project_id="project-uuid",
        dry_run=False,
        user=_admin(),
        conflict_mode="overwrite",
        job_id=job_id,
    )


def _reinserts_from_a_backup(sql: str) -> bool:
    """A restore's re-insert of a live table from its backup."""
    return sql.startswith("INSERT INTO") and "/SNAPSHOT/" in sql.partition(" FROM ")[2]


@pytest.mark.asyncio
async def test_an_overwrite_whose_backup_is_gone_deletes_nothing_and_its_flag_claims_nothing_deleted(
    job, clickhouse, monkeypatch
) -> None:
    # The issue's second case. The overwrite imports snv, and is held at the start of
    # coverage, which then fails. Meanwhile its heartbeat went stale and the worker that
    # ended its job dropped its backups; the import reaches its restore before its
    # heartbeat stops it.
    coverage = _Held(then=RuntimeError("bad BED"))
    _datasets(monkeypatch, job, {"snv": _overwrite(clickhouse), "coverage": coverage})
    postgres_restored = _snapshot_postgres(monkeypatch)

    run = asyncio.create_task(_overwrite_import(job))
    await asyncio.wait_for(coverage.inside.wait(), timeout=5)
    assert len(clickhouse.backups()) == 5
    await package_import.handle_claimed_family_import_job({"id": JOB, "status": "failed"}, worker_id="worker-2")
    assert clickhouse.backups() == []
    coverage.go_on.set()
    result = await asyncio.wait_for(run, timeout=5)

    assert result.completed is False
    # Before: the restore deleted the family's small variants, then failed for want of
    # their backup, and the flag still read "snv did import".
    assert clickhouse.restore_statements() == []
    assert clickhouse.family_rows("SNV_INDEL/entries") == [(FAMILY_UUID, "snv overwrite")]
    assert clickhouse.family_rows("INTERVAL/entries") == [(FAMILY_UUID, "coverage before")]
    assert postgres_restored == []
    # The family holds what the overwrite left, and the flag says so: coverage failed, snv
    # imported (its rows are there). The import's entry made way for the flag.
    assert job.incomplete is not None
    assert (job.incomplete["failed_datasets"], job.incomplete["imported_datasets"]) == (["coverage"], ["snv"])
    assert job.unfinished == {}
    assert any("the restore deleted nothing" in line for line in result.logs)


REIMPORT_COVERAGE = "00000000-0000-0000-0000-0000000000c1"
REIMPORT_SNV = "00000000-0000-0000-0000-0000000000c2"


class _FlagRead:
    """The family's ``import_incomplete`` as the sign-out reads it (one scalar)."""

    def __init__(self, flag: Any) -> None:
        self.flag = flag

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(scalar_one_or_none=lambda: copy.deepcopy(self.flag))


async def _sign_out_reads(job: _Store) -> dict[str, Any] | None:
    """The import state a sign-out freezes and gates on (None: the gate is open)."""
    from backend.app.services import report_signout_service

    return await report_signout_service._import_incomplete_state(
        _FlagRead(job.incomplete),  # type: ignore[arg-type]
        FAMILY_UUID,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["the ClickHouse restore, part-way", "the Postgres restore"])
async def test_a_restore_that_fails_flags_every_dataset_of_the_import_as_failed(
    job, clickhouse, monkeypatch, failing
) -> None:
    # SAFE-13. The overwrite imports snv, registers sv_needlr only (it writes no rows),
    # fails coverage and imports paraphase after it. Then its restore fails: ClickHouse
    # fails its first re-insert, after its delete of the family's small variants (the
    # overwrite's snv rows are gone, and so are the ones from before); or the ClickHouse
    # restore is done and the Postgres one fails.
    outcomes = {
        "snv": _overwrite(clickhouse),
        "sv_needlr": "registered",
        "coverage": RuntimeError("bad BED"),
        "paraphase": "imported",
    }
    _datasets(monkeypatch, job, outcomes)
    _snapshot_postgres(monkeypatch)
    if failing == "the Postgres restore":

        async def postgres_restore_fails(*_args: Any, **_kwargs: Any) -> None:
            raise OperationalError("DELETE FROM repeat_expansions", {}, ConnectionResetError())

        monkeypatch.setattr(package_import, "restore_family_postgres_state", postgres_restore_fails)
    else:
        clickhouse.fail = _reinserts_from_a_backup

    result = await _overwrite_import(job)

    assert result.completed is False
    assert result.error == "Family package import failed for dataset(s): coverage"
    if failing == "the Postgres restore":
        assert clickhouse.family_rows("SNV_INDEL/entries") == [(FAMILY_UUID, "snv before")] * 2
    else:
        assert clickhouse.family_rows("SNV_INDEL/entries") == []
    # Before: the flag named coverage alone as failed, so an import of coverage alone
    # cleared it. Now every dataset the import set out to import is named as failed, the
    # ones it imported (before the failure and after it) and the one it only registered
    # too, each with this import's job and the scope an import of it again must cover;
    # none is named as imported.
    every = sorted(outcomes)
    flag = job.incomplete
    assert flag is not None
    assert (flag["failed_datasets"], flag["imported_datasets"]) == (every, [])
    assert flag["failed_jobs"] == dict.fromkeys(every, JOB)
    assert sorted(flag["scopes"]) == every
    # The import's entry made way for the flag, and the sign-out reads every dataset failed.
    assert job.unfinished == {}
    state = await _sign_out_reads(job)
    assert state is not None and state["failed_datasets"] == every
    (line,) = [line for line in result.logs if "restore also failed" in line]
    assert "the flag names every one of them as failed" in line
    assert "coverage, paraphase, snv, sv_needlr" in line
    assert "again with overwrite" in line


@pytest.mark.asyncio
async def test_after_a_restore_that_failed_part_way_the_gate_holds_until_each_dataset_is_imported_again(
    job, clickhouse, monkeypatch
) -> None:
    # SAFE-13, the issue's case: the restore removed the family's small variants and
    # failed, and only coverage, the dataset that had failed, is imported again.
    _datasets(monkeypatch, job, {"snv": _overwrite(clickhouse), "coverage": RuntimeError("bad BED")})
    _snapshot_postgres(monkeypatch)
    clickhouse.fail = _reinserts_from_a_backup
    await _overwrite_import(job)
    assert clickhouse.family_rows("SNV_INDEL/entries") == []
    assert job.incomplete is not None
    clickhouse.fail = lambda _sql: False

    _datasets(monkeypatch, job, {"coverage": "imported"})
    coverage_again = await _overwrite_import(job, REIMPORT_COVERAGE)

    assert coverage_again.completed is True
    # Before: that import cleared the flag, and the family signed out without the gate,
    # its small variants gone. Now snv stays flagged, with the job that flagged it.
    assert job.incomplete is not None
    assert job.incomplete["failed_datasets"] == ["snv"]
    assert job.incomplete["failed_jobs"] == {"snv": JOB}
    state = await _sign_out_reads(job)
    assert state is not None and state["failed_datasets"] == ["snv"]
    assert any(
        "stays flagged import-incomplete" in line and f"snv in import job {JOB}" in line
        for line in coverage_again.logs
    )

    _datasets(monkeypatch, job, {"snv": _overwrite(clickhouse)})
    snv_again = await _overwrite_import(job, REIMPORT_SNV)

    # The last dataset imported again: the flag goes, and the gate with it.
    assert snv_again.completed is True
    assert clickhouse.family_rows("SNV_INDEL/entries") == [(FAMILY_UUID, "snv overwrite")]
    assert job.incomplete is None and job.unfinished == {}
    assert await _sign_out_reads(job) is None


@pytest.mark.asyncio
async def test_a_restore_that_succeeds_still_puts_the_family_back_without_a_flag(
    job, clickhouse, monkeypatch
) -> None:
    # Unchanged: the restore puts the family back as it was before the import, so nothing
    # is flagged and the import's entry goes.
    _datasets(monkeypatch, job, {"snv": _overwrite(clickhouse), "coverage": RuntimeError("bad BED")})
    postgres_restored = _snapshot_postgres(monkeypatch)

    result = await _overwrite_import(job)

    assert result.completed is False
    assert clickhouse.family_rows("SNV_INDEL/entries") == [(FAMILY_UUID, "snv before")] * 2
    assert postgres_restored == ["postgres restored"]
    assert job.incomplete is None and job.unfinished == {}
    assert ("ended",) in job.events
    assert any("atomically restored" in line for line in result.logs)
