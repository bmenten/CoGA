"""The admin-triggered clinical CNV knowledgebase rebuild: queueing and the job run (#526).

The build script, the database and the subprocess are stood in for; what is pinned is the
job's lifecycle: one active job at a time, a failed build leaves the loaded knowledgebase
untouched, a successful one replaces it, and a job never stays queued or running once its
run has ended, since it would refuse every later rebuild. The run heartbeats while the
build runs, stops a build that outlives its timeout or whose job was closed meanwhile, and
writes only while the job is its own; the status and a rebuild request first close a job
whose worker is gone. The one-active rule and the closing itself are SQL, checked against
real Postgres in ``integration/test_clinical_cnv_kb_one_active_job.py``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from backend.app.services import clinical_cnv_kb_jobs as kb

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def _job_row(**overrides) -> dict:
    row = {
        "id": "job-1",
        "assembly_id": "asm-1",
        "assembly_name": "GRCh38",
        "status": "queued",
        "skip_clinvar": False,
        "requested_by": "lab.admin",
        "requested_at": NOW,
        "started_at": None,
        "completed_at": None,
        "inserted": 0,
        "error": None,
    }
    row.update(overrides)
    return row


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(
        self, *, assembly=None, insert_error: Exception | None = None, jobs=None, returning=None
    ) -> None:
        self.assembly, self.insert_error, self.jobs = assembly, insert_error, jobs or []
        self.returning = returning or []  # the rows an UPDATE ... RETURNING gives back
        self.committed = self.rolled_back = False
        self.statements: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append((sql, params or {}))
        if sql.startswith("UPDATE clinical_cnv_kb_jobs"):
            return _Result(self.returning)
        if "FROM assemblies" in sql:
            return _Result([self.assembly] if self.assembly else [])
        if sql.startswith("INSERT INTO clinical_cnv_kb_jobs"):
            if self.insert_error:
                raise self.insert_error
            return _Result([_job_row()])
        if "FROM clinical_cnv_kb_jobs" in sql:
            return _Result(self.jobs)
        return _Result([])

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture()
def script(monkeypatch: pytest.MonkeyPatch) -> Path:
    path = Path("/app/scripts/clinical_cnv_knowledgebase.py")
    monkeypatch.setattr(kb, "_script_path", lambda: path)
    return path


# --- status and queueing ------------------------------------------------------------------------


def test_status_names_the_active_job_and_whether_the_script_is_there(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [_job_row(id="j2", status="running"), _job_row(id="j1", status="completed")]
    monkeypatch.setattr(kb, "_script_path", lambda: None)
    status = asyncio.run(kb.get_clinical_cnv_kb_status(_Session(jobs=jobs)))

    assert status.active_job is not None and str(status.active_job.id) == "j2"
    assert [str(job.id) for job in status.recent_jobs] == ["j2", "j1"]
    assert status.available is False and "not available" in (status.detail or "")


def test_a_rebuild_is_refused_without_the_build_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb, "_script_path", lambda: None)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(kb.queue_clinical_cnv_kb_rebuild(_Session(), assembly="GRCh38", skip_clinvar=False, requested_by="a"))
    assert refused.value.status_code == 503


def test_a_rebuild_for_an_unknown_assembly_is_refused(script: Path) -> None:
    with pytest.raises(HTTPException) as refused:
        asyncio.run(kb.queue_clinical_cnv_kb_rebuild(_Session(), assembly="hg19", skip_clinvar=False, requested_by="a"))
    assert refused.value.status_code == 404


def test_only_one_rebuild_runs_at_a_time(script: Path) -> None:
    # The one-active-job rule is a partial unique index on a constant; a second insert, while
    # a job is queued or running, violates it.
    session = _Session(
        assembly={"id": "asm-1", "assembly_name": "GRCh38"},
        insert_error=IntegrityError("INSERT", {}, Exception("duplicate key")),
    )
    with pytest.raises(HTTPException) as refused:
        asyncio.run(kb.queue_clinical_cnv_kb_rebuild(session, assembly="GRCh38", skip_clinvar=False, requested_by="a"))
    assert refused.value.status_code == 409
    assert session.rolled_back is True


def _closes_stale_jobs(sql: str) -> bool:
    return sql.startswith("UPDATE clinical_cnv_kb_jobs SET status = 'failed'") and "stale_window" in sql


def test_the_status_first_closes_a_job_whose_worker_is_gone(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Else a job cut off with its server would show as active for good, and the admin page
    # would never offer a new rebuild.
    monkeypatch.setattr(kb, "_script_path", lambda: None)
    session = _Session(jobs=[_job_row(status="failed")], returning=[{"id": "job-0"}])
    with caplog.at_level(logging.WARNING, logger=kb.logger.name):
        asyncio.run(kb.get_clinical_cnv_kb_status(session))

    (close_sql, close_params), (read_sql, _) = session.statements
    assert _closes_stale_jobs(close_sql) and session.committed
    assert close_params["stale_window"] == kb.CLINICAL_CNV_KB_STALE_HEARTBEAT == timedelta(minutes=10)
    assert close_params["error"].startswith("Interrupted")
    assert read_sql.startswith("SELECT")
    assert "Closed clinical CNV knowledgebase rebuild job-0: its worker is gone" in caplog.text


def test_a_rebuild_request_first_closes_a_job_whose_worker_is_gone(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_job(job_id: str) -> None:
        return None

    monkeypatch.setattr(kb, "_run_job", run_job)
    session = _Session(assembly={"id": "asm-1", "assembly_name": "GRCh38"})
    asyncio.run(kb.queue_clinical_cnv_kb_rebuild(session, assembly="GRCh38", skip_clinvar=False, requested_by="a"))

    sqls = [sql for sql, _ in session.statements]
    closing = next(i for i, sql in enumerate(sqls) if _closes_stale_jobs(sql))
    inserting = next(i for i, sql in enumerate(sqls) if sql.startswith("INSERT INTO clinical_cnv_kb_jobs"))
    assert closing < inserting  # in the same transaction, committed with the new job


def test_a_queued_rebuild_starts_its_job_in_the_background(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    async def run_job(job_id: str) -> None:
        started.append(job_id)

    monkeypatch.setattr(kb, "_run_job", run_job)

    async def queue():
        job = await kb.queue_clinical_cnv_kb_rebuild(
            _Session(assembly={"id": "asm-1", "assembly_name": "GRCh38"}),
            assembly="GRCh38",
            skip_clinvar=True,
            requested_by="lab.admin",
        )
        await asyncio.sleep(0)  # let the background task run
        return job

    job = asyncio.run(queue())
    assert (job.status, str(job.id)) == ("queued", "job-1")
    assert started == ["job-1"]


# --- taking a job and writing to it --------------------------------------------------------------


def test_a_job_is_taken_only_while_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session(returning=[{"assembly_id": "asm-1", "assembly_name": "GRCh38"}])
    monkeypatch.setattr(kb, "get_postgres_sessionmaker", lambda: (lambda: session))
    row = asyncio.run(kb._claim_job("job-1", "worker-a"))

    [(sql, params)] = session.statements
    assert "SET status = 'running', worker_id = :worker_id" in sql and "heartbeat_at = now()" in sql
    assert sql.endswith("WHERE id = CAST(:job_id AS uuid) AND status = 'queued' "
                        "RETURNING assembly_id::text AS assembly_id, assembly_name, skip_clinvar, requested_by")
    assert params == {"job_id": "job-1", "worker_id": "worker-a"} and session.committed
    assert row == {"assembly_id": "asm-1", "assembly_name": "GRCh38"}

    session.returning = []  # no longer queued
    assert asyncio.run(kb._claim_job("job-1", "worker-b")) is None


@pytest.mark.parametrize(
    ("worker", "guard"),
    [("worker-a", "AND worker_id = :worker_id"), (None, "AND status = 'queued'")],
    ids=["by-its-worker", "without-an-owner-only-while-queued"],
)
def test_a_write_is_guarded_by_whose_job_it_is(monkeypatch: pytest.MonkeyPatch, worker, guard: str) -> None:
    session = _Session(returning=[{"id": "job-1"}])
    monkeypatch.setattr(kb, "get_postgres_sessionmaker", lambda: (lambda: session))
    assert asyncio.run(kb._update_job("job-1", worker, "heartbeat_at = now()", {})) is True

    [(sql, params)] = session.statements
    assert sql == f"UPDATE clinical_cnv_kb_jobs SET heartbeat_at = now() WHERE id = CAST(:job_id AS uuid) {guard} RETURNING id"
    assert params == ({"job_id": "job-1", "worker_id": worker} if worker else {"job_id": "job-1"})

    session.returning = []  # the job is no longer its own
    assert asyncio.run(kb._update_job("job-1", worker, "heartbeat_at = now()", {})) is False


# --- running a job -------------------------------------------------------------------------------

_ROW = {"assembly_id": "asm-1", "assembly_name": "GRCh38", "skip_clinvar": False, "requested_by": "lab.admin"}
_HEARTBEAT = "heartbeat_at = now()"


class _JobSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Process:
    """The build subprocess: it exits after ``runtime`` seconds, or (None) only when killed."""

    def __init__(self, returncode: int, stderr: bytes, runtime: float | None) -> None:
        self.returncode: int | None = None
        self._exit, self._stderr, self._runtime = returncode, stderr, runtime
        self.killed = False

    async def communicate(self):
        if self._runtime is None:
            await asyncio.Event().wait()  # a hung build
        else:
            await asyncio.sleep(self._runtime)
        self.returncode = self._exit
        return b"", self._stderr

    def kill(self) -> None:
        self.killed, self.returncode = True, -9

    async def wait(self) -> int | None:
        return self.returncode


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    row: dict | None = _ROW,
    returncode: int = 0,
    tsv: str = "chr1\t1\t100\tX\n",
    runtime: float | None = 0.0,
    claim_error: Exception | None = None,
    update_errors: dict[str, Exception] | None = None,
    still_ours: Callable[[str], bool] = lambda assignments: True,
    stderr: bytes | None = None,
) -> SimpleNamespace:
    """Run one job against stand-ins; each write is recorded with the worker it is guarded by."""
    run = SimpleNamespace(updates=[], commands=[], applied=[], claims=[], processes=[])

    async def claim_job(job_id, worker_id):
        run.claims.append(worker_id)
        if claim_error:
            raise claim_error
        return row

    async def update_job(job_id, worker_id, assignments, params):
        run.updates.append((assignments, params, worker_id))
        for prefix, error in (update_errors or {}).items():
            if assignments.startswith(prefix):
                raise error
        return still_ours(assignments)

    async def subprocess_exec(*cmd, **kwargs):
        run.commands.append(list(cmd))
        out = Path(cmd[cmd.index("--out") + 1])
        if returncode == 0:
            out.write_text(tsv, encoding="utf-8")
        said = stderr if stderr is not None else (
            b"downloading ClinGen\nbuild step failed\n" if returncode else b"built 1 region\n"
        )
        run.processes.append(_Process(returncode, said, runtime))
        return run.processes[-1]

    async def apply_text(session, **kwargs):
        run.applied.append(kwargs)
        return type("Result", (), {"inserted": 1})()

    monkeypatch.setattr(kb, "get_postgres_sessionmaker", lambda: _JobSession)
    monkeypatch.setattr(kb, "_claim_job", claim_job)
    monkeypatch.setattr(kb, "_update_job", update_job)
    monkeypatch.setattr(kb.asyncio, "create_subprocess_exec", subprocess_exec)
    monkeypatch.setattr(kb, "apply_reference_dataset_text", apply_text)
    asyncio.run(asyncio.wait_for(kb._run_job("job-1"), timeout=10))  # a hang fails the test
    return run


@pytest.fixture()
def quick_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb, "CLINICAL_CNV_KB_HEARTBEAT_SECONDS", 0.01)


def test_a_successful_build_replaces_the_knowledgebase(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run(monkeypatch, row={**_ROW, "skip_clinvar": True})

    assert run.commands[0][1:4] == [str(script), "--assembly", "GRCh38"]
    assert run.commands[0][-1] == "--skip-clinvar"
    assert run.applied == [
        {
            "assembly_id": "asm-1",
            "dataset_type": "clinical_cnvs",
            "text_value": "chr1\t1\t100\tX\n",
            "overwrite": True,
            "performed_by": "lab.admin",
            "source": "clinical-cnv-kb",
        }
    ]
    # Every write after the claim is guarded by the worker that took the job.
    assert len(run.claims) == 1 and {worker for *_, worker in run.updates} == set(run.claims)
    assignments, params, _worker = run.updates[-1]
    assert assignments.startswith("status = 'completed'") and "worker_id = NULL" in assignments
    assert params["inserted"] == 1


def test_a_failed_build_leaves_the_knowledgebase_untouched(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run(monkeypatch, returncode=2)

    assert "--skip-clinvar" not in run.commands[0]
    assert run.applied == []
    assignments, params, worker = run.updates[-1]
    assert assignments.startswith("status = 'failed'") and worker == run.claims[0]
    assert params["error"] == "Knowledgebase build failed (exit 2): build step failed"
    assert "build step failed" in params["log"]


def test_a_build_stopped_for_a_missing_source_says_which(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The job's error is what the admin sees; the log is not shown. A progress bar's
    # carriage-return updates come before the script's last word.
    url = "https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg37.bed"
    stderr = (
        b"[build-cnv-kb] Loading ClinGen recurrent CNV regions (named syndromes).\n"
        b"ClinVar overlaps:  31%\rClinVar overlaps: 100%\r"
        b"[build-cnv-kb] Build stopped: The ClinGen recurrent CNV regions could not be downloaded from "
        + url.encode()
        + b": 404 Client Error\n"
    )
    run = _run(monkeypatch, returncode=1, stderr=stderr)

    assert run.applied == []
    _assignments, params, _worker = run.updates[-1]
    assert params["error"] == (
        "Knowledgebase build failed (exit 1): Build stopped: The ClinGen recurrent CNV regions could "
        f"not be downloaded from {url}: 404 Client Error"
    )


def test_a_failed_build_that_said_nothing_keeps_the_plain_error(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run(monkeypatch, returncode=1, stderr=b"")

    _assignments, params, _worker = run.updates[-1]
    assert params["error"] == "Knowledgebase build failed (exit 1)."



def test_a_job_that_is_no_longer_queued_is_not_run(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run(monkeypatch, row=None)
    assert run.commands == [] and run.applied == [] and run.updates == []


def test_a_job_whose_build_script_is_gone_is_marked_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb, "_script_path", lambda: None)
    run = _run(monkeypatch)
    assert run.commands == [] and run.applied == []
    assert run.updates == [(kb._FAILED, {"error": kb._SCRIPT_MISSING}, run.claims[0])]


# --- a job never stays active once its run has ended ---------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        IntegrityError("UPDATE clinical_cnv_kb_jobs", {}, Exception("duplicate key value violates unique constraint")),
        ConnectionError("connection reset"),
    ],
    ids=["refused-by-the-index", "database-unreachable"],
)
def test_a_job_that_cannot_be_taken_is_marked_failed(script: Path, monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    # The switch to running used to sit outside the job's error handling. When it failed (the
    # old index let a job be queued beside a running one, then refused its switch), the run
    # died and the job stayed queued for good, refusing every later rebuild. Not taken, the
    # job has no owner: the write is guarded by its being still queued.
    run = _run(monkeypatch, claim_error=error)

    assert run.commands == [] and run.applied == []
    [(assignments, params, worker)] = run.updates
    assert assignments == kb._FAILED and worker is None
    assert params["error"] == str(error)[:2000]


def test_a_failure_that_cannot_be_recorded_is_logged_not_raised(
    script: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The database is gone: the job cannot be marked failed either. The run still ends
    # quietly, and says in the log which job is left active (until it is closed as stale).
    down = ConnectionError("database unreachable")
    with caplog.at_level(logging.ERROR, logger=kb.logger.name):
        run = _run(monkeypatch, claim_error=down, update_errors={"status": down})

    assert run.commands == [] and run.applied == []
    assert [assignments for assignments, *_ in run.updates] == [kb._FAILED]
    assert "job-1 could not be marked failed" in caplog.text


# --- heartbeat, timeout, a job closed while its build runs ---------------------------------------


def test_a_build_heartbeats_while_it_runs(script: Path, monkeypatch: pytest.MonkeyPatch, quick_heartbeat: None) -> None:
    run = _run(monkeypatch, runtime=0.1)

    beats = [worker for assignments, _, worker in run.updates if assignments == _HEARTBEAT]
    assert len(beats) >= 3 and set(beats) == set(run.claims)
    assert run.updates[-1][0].startswith("status = 'completed'") and len(run.applied) == 1


def test_a_heartbeat_that_fails_does_not_stop_the_build(
    script: Path, monkeypatch: pytest.MonkeyPatch, quick_heartbeat: None
) -> None:
    beats = []

    def still_ours(assignments: str) -> bool:
        if assignments == _HEARTBEAT:
            beats.append(assignments)
            if len(beats) == 1:
                raise ConnectionError("database unreachable")  # a passing hiccup
        return True

    run = _run(monkeypatch, runtime=0.1, still_ours=still_ours)
    assert run.updates[-1][0].startswith("status = 'completed'") and len(run.applied) == 1


def test_a_build_that_outlives_its_timeout_is_stopped_and_marked_failed(
    script: Path, monkeypatch: pytest.MonkeyPatch, quick_heartbeat: None
) -> None:
    assert kb.settings.clinical_cnv_kb_build_timeout_seconds == 7200  # the default: two hours
    monkeypatch.setattr(kb.settings, "clinical_cnv_kb_build_timeout_seconds", 0.05)
    run = _run(monkeypatch, runtime=None)

    assert run.processes[0].killed and run.applied == []
    assignments, params, worker = run.updates[-1]
    assert assignments == kb._FAILED and worker == run.claims[0]
    assert "CLINICAL_CNV_KB_BUILD_TIMEOUT_SECONDS" in params["error"]


def test_a_build_whose_job_was_closed_meanwhile_is_stopped_and_loads_nothing(
    script: Path, monkeypatch: pytest.MonkeyPatch, quick_heartbeat: None
) -> None:
    # Its worker was taken for gone (silent past the stale window) and the job closed; a new
    # rebuild may already be running. This one stops, and writes nothing more.
    run = _run(monkeypatch, runtime=None, still_ours=lambda assignments: assignments != _HEARTBEAT)

    assert run.processes[0].killed and run.applied == []
    assert [assignments for assignments, *_ in run.updates] == [_HEARTBEAT]


def test_a_job_closed_before_the_load_does_not_replace_the_knowledgebase(
    script: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The build finished, but the job was closed meanwhile: the last check before the load
    # finds it no longer its own.
    run = _run(monkeypatch, still_ours=lambda assignments: assignments != _HEARTBEAT)

    assert run.commands and run.applied == []
    assert [assignments for assignments, *_ in run.updates] == [_HEARTBEAT]
