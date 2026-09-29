"""Admin-triggered rebuilds of the clinical CNV knowledgebase.

Runs the knowledgebase build script (``scripts/clinical_cnv_knowledgebase.py``)
as an isolated subprocess so its heavy/optional dependencies and external
network fetches never touch the API process, then loads the resulting TSV into
``clinical_cnvs`` for the target assembly via the standard reference loader.

Progress is tracked in ``clinical_cnv_kb_jobs``; the build runs as an in-process
background task and the admin UI polls the status endpoint. One job may be active (queued
or running) at a time: a partial unique index on a constant refuses a second one, so a
request made while a rebuild is active gets a 409, even when two arrive together. A job
must therefore never stay active once its run has ended, or it would refuse every later
rebuild: ``_run_job`` ends every job it starts as ``completed`` or ``failed``, and stops a
build that outlives ``CLINICAL_CNV_KB_BUILD_TIMEOUT_SECONDS``.

A run that ends with its server (a restart, a redeploy) cannot record that. As in the
gene-reference and family-import queues, the running job carries its worker's id and a
heartbeat that the worker refreshes while the build runs, and a job whose heartbeat is
older than the stale window has lost its worker. Nothing polls for work here, so the status
read and the rebuild request close such a job as failed first, and a new rebuild is then
accepted; a job still queued past the window never started, and is closed the same way.
Should the old worker come back, the job is no longer its own: each of its writes is
guarded by its worker id.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import ClinicalCnvKbJobOut, ClinicalCnvKbStatusOut
from .reference_metadata_service import apply_reference_dataset_text

logger = logging.getLogger(__name__)

_STDERR_TAIL_CHARS = 8000

# As in the family-import queue: the worker heartbeats every minute while the build runs,
# and a job with no sign of its build for ten minutes has lost its worker.
CLINICAL_CNV_KB_HEARTBEAT_SECONDS = 60.0
CLINICAL_CNV_KB_STALE_HEARTBEAT = timedelta(minutes=10)
_STALE_ERROR = (
    "Interrupted: no sign of its build for "
    f"{int(CLINICAL_CNV_KB_STALE_HEARTBEAT.total_seconds() // 60)} minutes; the server running "
    "it was probably restarted or redeployed. Request a new rebuild."
)
_SCRIPT_MISSING = "The clinical CNV knowledgebase build script is not available on the server."
_FAILED = "status = 'failed', worker_id = NULL, completed_at = now(), error = :error"


# What the knowledgebase build script needs from the environment, and nothing else: it
# makes network calls to external sources, so it must not inherit the backend's database
# passwords, signing keys or cloud credentials (#522). OMIM_API_KEY is its one credential.
_SCRIPT_ENV_ALLOWLIST = frozenset(
    {
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
        "PYTHONPATH", "PYTHONHOME", "PYTHONIOENCODING", "PYTHONUNBUFFERED", "VIRTUAL_ENV",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
        "OMIM_API_KEY",
    }
)


def _build_script_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    return {name: value for name, value in source.items() if name in _SCRIPT_ENV_ALLOWLIST}


def _script_path() -> Path | None:
    raw = settings.clinical_cnv_kb_script_path
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _job_out(row: Mapping[str, Any]) -> ClinicalCnvKbJobOut:
    return ClinicalCnvKbJobOut(
        _id=row["id"],
        assembly_id=row["assembly_id"],
        assembly_name=row["assembly_name"],
        status=row["status"],
        skip_clinvar=bool(row["skip_clinvar"]),
        requested_by=row.get("requested_by"),
        requested_at=row["requested_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        inserted=int(row.get("inserted") or 0),
        error=row.get("error"),
    )


_JOB_COLUMNS = (
    "id::text AS id, assembly_id::text AS assembly_id, assembly_name, status, "
    "skip_clinvar, requested_by, requested_at, started_at, completed_at, inserted, error"
)


async def _close_jobs_whose_worker_is_gone(session: AsyncSession) -> None:
    """Close as failed an active job with no sign of its build for the stale window.

    A running job's last sign is its heartbeat (its start, for a job from before the
    heartbeat); a queued one's is its request, as its run starts at once. The database
    clock judges both, as it stamped them. The caller commits.
    """
    closed = (
        await session.execute(
            text(
                """
                UPDATE clinical_cnv_kb_jobs
                SET status = 'failed', worker_id = NULL, completed_at = now(), error = :error
                WHERE status IN ('queued', 'running')
                  AND COALESCE(heartbeat_at, started_at, requested_at)
                      < now() - CAST(:stale_window AS interval)
                RETURNING id::text AS id
                """
            ),
            {"stale_window": CLINICAL_CNV_KB_STALE_HEARTBEAT, "error": _STALE_ERROR},
        )
    ).mappings().all()
    for row in closed:
        logger.warning("Closed clinical CNV knowledgebase rebuild %s: its worker is gone", row["id"])


async def get_clinical_cnv_kb_status(session: AsyncSession) -> ClinicalCnvKbStatusOut:
    # What the admin page polls: a job whose worker is gone is closed here, so it no longer
    # shows as active and a new rebuild can be requested.
    await _close_jobs_whose_worker_is_gone(session)
    await session.commit()
    rows = (
        await session.execute(
            text(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM clinical_cnv_kb_jobs
                ORDER BY requested_at DESC
                LIMIT 10
                """
            )
        )
    ).mappings().all()
    jobs = [_job_out(row) for row in rows]
    active = next((job for job in jobs if job.status in ("queued", "running")), None)
    script_available = _script_path() is not None
    return ClinicalCnvKbStatusOut(
        active_job=active,
        recent_jobs=jobs,
        available=script_available,
        detail=None if script_available else _SCRIPT_MISSING,
    )


async def queue_clinical_cnv_kb_rebuild(
    session: AsyncSession,
    *,
    assembly: str,
    skip_clinvar: bool,
    requested_by: str | None,
) -> ClinicalCnvKbJobOut:
    if _script_path() is None:
        raise HTTPException(status_code=503, detail=_SCRIPT_MISSING)
    assembly_row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, assembly_name
                FROM assemblies
                WHERE assembly_name = :assembly
                """
            ),
            {"assembly": assembly},
        )
    ).mappings().first()
    if assembly_row is None:
        raise HTTPException(status_code=404, detail="Assembly not found")

    # One active job at a time: while a job is queued or running, the insert violates the
    # partial unique index idx_clinical_cnv_kb_jobs_one_active (02_reference.sql). A job
    # whose worker is gone is closed first, in the same transaction.
    try:
        await _close_jobs_whose_worker_is_gone(session)
        row = (
            await session.execute(
                text(
                    f"""
                    INSERT INTO clinical_cnv_kb_jobs (assembly_id, assembly_name, status, skip_clinvar, requested_by)
                    VALUES (CAST(:assembly_id AS uuid), :assembly_name, 'queued', :skip_clinvar, :requested_by)
                    RETURNING {_JOB_COLUMNS}
                    """
                ),
                {
                    "assembly_id": assembly_row["id"],
                    "assembly_name": assembly_row["assembly_name"],
                    "skip_clinvar": skip_clinvar,
                    "requested_by": requested_by,
                },
            )
        ).mappings().first()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A clinical CNV knowledgebase rebuild is already queued or running.",
        ) from exc

    job = _job_out(row)
    # Run the build in the background; it manages its own DB session.
    asyncio.create_task(_run_job(str(job.id)))
    return job


async def _claim_job(job_id: str, worker_id: str) -> dict[str, Any] | None:
    """Take the queued job for this worker: running, with its first heartbeat.

    None when the job is no longer queued (or gone): it is not this worker's to run.
    """
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        row = (
            await session.execute(
                text(
                    """
                    UPDATE clinical_cnv_kb_jobs
                    SET status = 'running', worker_id = :worker_id,
                        started_at = now(), heartbeat_at = now()
                    WHERE id = CAST(:job_id AS uuid) AND status = 'queued'
                    RETURNING assembly_id::text AS assembly_id, assembly_name, skip_clinvar, requested_by
                    """
                ),
                {"job_id": job_id, "worker_id": worker_id},
            )
        ).mappings().first()
        await session.commit()
    return dict(row) if row is not None else None


async def _update_job(
    job_id: str, worker_id: str | None, assignments: str, params: dict[str, Any]
) -> bool:
    """Write to the job while it is this worker's; False once it is not (it was closed).

    Without a worker (a run that could not take its job), only a job still queued is written.
    """
    bound = {**params, "job_id": job_id}
    if worker_id is not None:
        owned = "worker_id = :worker_id"
        bound["worker_id"] = worker_id
    else:
        owned = "status = 'queued'"
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            text(
                f"UPDATE clinical_cnv_kb_jobs SET {assignments} "
                f"WHERE id = CAST(:job_id AS uuid) AND {owned} RETURNING id"
            ),
            bound,
        )
        written = result.first() is not None
        await session.commit()
    return written


async def _record_failure(job_id: str, worker_id: str | None, error: str) -> None:
    """Mark the job failed; when even that fails, say in the log which job is left active."""
    try:
        await _update_job(job_id, worker_id, _FAILED, {"error": error})
    except Exception:
        logger.exception(
            "Clinical CNV knowledgebase rebuild %s could not be marked failed; it stays active "
            "until it has been silent for the stale window and is closed",
            job_id,
        )


class _JobClosed(Exception):
    """The job was closed while it ran (its worker taken for gone): no longer this worker's."""


async def _await_build(process: asyncio.subprocess.Process, *, job_id: str, worker_id: str) -> bytes:
    """Wait for the build, heartbeating while it runs, and return its stderr.

    Stops the build when it outlives CLINICAL_CNV_KB_BUILD_TIMEOUT_SECONDS (TimeoutError), or
    when a heartbeat finds the job closed (_JobClosed). As with the family-import queue's
    periodic progress, a heartbeat that fails is logged and the build goes on.
    """
    loop = asyncio.get_running_loop()
    timeout = settings.clinical_cnv_kb_build_timeout_seconds
    deadline = loop.time() + timeout
    output = asyncio.ensure_future(process.communicate())
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Knowledgebase build stopped after {timeout} seconds "
                    "(CLINICAL_CNV_KB_BUILD_TIMEOUT_SECONDS)."
                )
            done, _ = await asyncio.wait(
                {output}, timeout=min(CLINICAL_CNV_KB_HEARTBEAT_SECONDS, remaining)
            )
            if output in done:
                return output.result()[1] or b""
            try:
                still_ours = await _update_job(job_id, worker_id, "heartbeat_at = now()", {})
            except Exception:
                logger.exception("Clinical CNV knowledgebase rebuild heartbeat failed")
                continue
            if not still_ours:
                raise _JobClosed()
    finally:
        # Stopped (timeout, closed job, server shutting down) or failed: no build outlives it.
        if not output.done():
            output.cancel()
            with suppress(asyncio.CancelledError):
                await output
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()


async def _run_job(job_id: str) -> None:
    # Every step, the claim included, is inside the error handling: a job left queued or
    # running once this ends would refuse every later rebuild until it is closed as stale.
    worker_id = f"{os.getpid()}-{uuid4().hex}"
    owner: str | None = None  # this worker, once it has taken the job
    tmp_dir: str | None = None
    try:
        row = await _claim_job(job_id, worker_id)
        if row is None:
            logger.info("Clinical CNV knowledgebase rebuild %s is no longer queued; not started", job_id)
            return
        owner = worker_id
        script = _script_path()
        if script is None:
            await _update_job(job_id, owner, _FAILED, {"error": _SCRIPT_MISSING})
            return

        tmp_dir = tempfile.mkdtemp(prefix="cnv-kb-")
        out_path = os.path.join(tmp_dir, "clinical_cnv_knowledgebase.tsv")
        cmd = [sys.executable, str(script), "--assembly", row["assembly_name"], "--out", out_path]
        if row["skip_clinvar"]:
            cmd.append("--skip-clinvar")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_script_env(),
        )
        stderr = await _await_build(process, job_id=job_id, worker_id=owner)
        log_tail = stderr.decode("utf-8", "replace")[-_STDERR_TAIL_CHARS:]

        if process.returncode != 0:
            await _update_job(
                job_id,
                owner,
                "status = 'failed', worker_id = NULL, completed_at = now(), error = :error, log = :log",
                {
                    "error": f"Knowledgebase build failed (exit {process.returncode}).",
                    "log": log_tail,
                },
            )
            return

        tsv_text = await asyncio.to_thread(Path(out_path).read_text, encoding="utf-8")
        # Replace the knowledgebase only while the job is still this worker's.
        if not await _update_job(job_id, owner, "heartbeat_at = now()", {}):
            raise _JobClosed()
        sessionmaker = get_postgres_sessionmaker()
        async with sessionmaker() as session:
            result = await apply_reference_dataset_text(
                session,
                assembly_id=row["assembly_id"],
                dataset_type="clinical_cnvs",
                text_value=tsv_text,
                overwrite=True,
                performed_by=row.get("requested_by"),
                source="clinical-cnv-kb",
            )
        await _update_job(
            job_id,
            owner,
            "status = 'completed', worker_id = NULL, completed_at = now(), "
            "inserted = :inserted, log = :log",
            {"inserted": result.inserted, "log": log_tail},
        )
    except _JobClosed:
        logger.warning(
            "Clinical CNV knowledgebase rebuild %s was closed while it ran (its worker was taken "
            "for gone); stopped without loading it",
            job_id,
        )
    except Exception as exc:  # surfaced to the admin UI as the job's error
        logger.exception("Clinical CNV knowledgebase rebuild failed")
        await _record_failure(job_id, owner, str(exc)[:2000])
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
