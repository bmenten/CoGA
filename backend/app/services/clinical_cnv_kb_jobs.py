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
rebuild: ``_run_job`` ends every job it starts as ``completed`` or ``failed``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

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


async def get_clinical_cnv_kb_status(session: AsyncSession) -> ClinicalCnvKbStatusOut:
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
        detail=None
        if script_available
        else "The clinical CNV knowledgebase build script is not available on the server.",
    )


async def queue_clinical_cnv_kb_rebuild(
    session: AsyncSession,
    *,
    assembly: str,
    skip_clinvar: bool,
    requested_by: str | None,
) -> ClinicalCnvKbJobOut:
    if _script_path() is None:
        raise HTTPException(
            status_code=503,
            detail="The clinical CNV knowledgebase build script is not available on the server.",
        )
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
    # partial unique index idx_clinical_cnv_kb_jobs_one_active (02_reference.sql).
    try:
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


async def _update_job(job_id: str, assignments: str, params: dict[str, Any]) -> None:
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        await session.execute(
            text(f"UPDATE clinical_cnv_kb_jobs SET {assignments} WHERE id = CAST(:job_id AS uuid)"),
            {**params, "job_id": job_id},
        )
        await session.commit()


async def _record_failure(job_id: str, error: str) -> None:
    """Mark the job failed; when even that fails, say in the log which job is left active."""
    try:
        await _update_job(
            job_id,
            "status = 'failed', completed_at = now(), error = :error",
            {"error": error},
        )
    except Exception:
        logger.exception(
            "Clinical CNV knowledgebase rebuild %s could not be marked failed; "
            "it stays active and refuses later rebuilds until cleared",
            job_id,
        )


async def _run_job(job_id: str) -> None:
    # Every step, reading the job and its switch to running included, is inside the error
    # handling: a job left queued or running once this ends would refuse every later rebuild.
    tmp_dir: str | None = None
    try:
        script = _script_path()
        sessionmaker = get_postgres_sessionmaker()
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT assembly_id::text AS assembly_id, assembly_name, skip_clinvar, requested_by "
                        "FROM clinical_cnv_kb_jobs WHERE id = CAST(:job_id AS uuid)"
                    ),
                    {"job_id": job_id},
                )
            ).mappings().first()
        if row is None or script is None:
            await _update_job(
                job_id,
                "status = 'failed', completed_at = now(), error = :error",
                {"error": "Build script or job record unavailable."},
            )
            return

        await _update_job(job_id, "status = 'running', started_at = now()", {})

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
        _, stderr = await process.communicate()
        log_tail = (stderr or b"").decode("utf-8", "replace")[-_STDERR_TAIL_CHARS:]

        if process.returncode != 0:
            await _update_job(
                job_id,
                "status = 'failed', completed_at = now(), error = :error, log = :log",
                {
                    "error": f"Knowledgebase build failed (exit {process.returncode}).",
                    "log": log_tail,
                },
            )
            return

        tsv_text = await asyncio.to_thread(Path(out_path).read_text, encoding="utf-8")
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
            "status = 'completed', completed_at = now(), inserted = :inserted, log = :log",
            {"inserted": result.inserted, "log": log_tail},
        )
    except Exception as exc:  # surfaced to the admin UI as the job's error
        logger.exception("Clinical CNV knowledgebase rebuild failed")
        await _record_failure(job_id, str(exc)[:2000])
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
