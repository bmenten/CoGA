"""The admin-triggered clinical CNV knowledgebase rebuild: queueing and the job run (#526).

The build script, the database and the subprocess are stood in for; what is pinned is the
job's lifecycle: one active job at a time, a failed build leaves the loaded knowledgebase
untouched, and a successful one replaces it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

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
    def __init__(self, *, assembly=None, insert_error: Exception | None = None, jobs=None) -> None:
        self.assembly, self.insert_error, self.jobs = assembly, insert_error, jobs or []
        self.committed = self.rolled_back = False

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
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
    # The one-active-job rule is a partial unique index; a second insert violates it.
    session = _Session(
        assembly={"id": "asm-1", "assembly_name": "GRCh38"},
        insert_error=IntegrityError("INSERT", {}, Exception("duplicate key")),
    )
    with pytest.raises(HTTPException) as refused:
        asyncio.run(kb.queue_clinical_cnv_kb_rebuild(session, assembly="GRCh38", skip_clinvar=False, requested_by="a"))
    assert refused.value.status_code == 409
    assert session.rolled_back is True


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


# --- running a job -------------------------------------------------------------------------------


class _JobSession:
    def __init__(self, row) -> None:
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        return _Result([self.row] if self.row else [])


class _Process:
    def __init__(self, returncode: int, stderr: bytes) -> None:
        self.returncode, self._stderr = returncode, stderr

    async def communicate(self):
        return b"", self._stderr


def _run(monkeypatch: pytest.MonkeyPatch, *, row, returncode: int = 0, tsv: str = "chr1\t1\t100\tX\n"):
    updates: list[tuple[str, dict]] = []
    commands: list[list[str]] = []
    applied: list[dict] = []

    async def update_job(job_id, assignments, params):
        updates.append((assignments, params))

    async def subprocess_exec(*cmd, **kwargs):
        commands.append(list(cmd))
        out = Path(cmd[cmd.index("--out") + 1])
        if returncode == 0:
            out.write_text(tsv, encoding="utf-8")
        return _Process(returncode, b"downloading ClinGen\nbuild step failed\n" if returncode else b"built 1 region\n")

    async def apply_text(session, **kwargs):
        applied.append(kwargs)
        return type("Result", (), {"inserted": 1})()

    monkeypatch.setattr(kb, "get_postgres_sessionmaker", lambda: (lambda: _JobSession(row)))
    monkeypatch.setattr(kb, "_update_job", update_job)
    monkeypatch.setattr(kb.asyncio, "create_subprocess_exec", subprocess_exec)
    monkeypatch.setattr(kb, "apply_reference_dataset_text", apply_text)
    asyncio.run(kb._run_job("job-1"))
    return updates, commands, applied


def test_a_successful_build_replaces_the_knowledgebase(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"assembly_id": "asm-1", "assembly_name": "GRCh38", "skip_clinvar": True, "requested_by": "lab.admin"}
    updates, commands, applied = _run(monkeypatch, row=row)

    assert commands[0][1:4] == [str(script), "--assembly", "GRCh38"]
    assert commands[0][-1] == "--skip-clinvar"
    assert applied == [
        {
            "assembly_id": "asm-1",
            "dataset_type": "clinical_cnvs",
            "text_value": "chr1\t1\t100\tX\n",
            "overwrite": True,
            "performed_by": "lab.admin",
            "source": "clinical-cnv-kb",
        }
    ]
    assert updates[0][0].startswith("status = 'running'")
    assert updates[-1][0].startswith("status = 'completed'") and updates[-1][1]["inserted"] == 1


def test_a_failed_build_leaves_the_knowledgebase_untouched(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"assembly_id": "asm-1", "assembly_name": "GRCh38", "skip_clinvar": False, "requested_by": "lab.admin"}
    updates, commands, applied = _run(monkeypatch, row=row, returncode=2)

    assert "--skip-clinvar" not in commands[0]
    assert applied == []
    assignments, params = updates[-1]
    assert assignments.startswith("status = 'failed'")
    assert params["error"] == "Knowledgebase build failed (exit 2)."
    assert "build step failed" in params["log"]


def test_a_job_whose_record_is_gone_is_marked_failed(script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    updates, commands, applied = _run(monkeypatch, row=None)
    assert commands == [] and applied == []
    assert updates == [
        (
            "status = 'failed', completed_at = now(), error = :error",
            {"error": "Build script or job record unavailable."},
        )
    ]
