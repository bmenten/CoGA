"""Loads of ClinVar's newest weekly VCF for an assembly: downloaded, checked, stored, released.

A load finds the newest weekly file NCBI lists for the assembly (``clinvar_download``). When
that release is the current one already, the job ends ``unchanged`` and nothing is downloaded.
Otherwise it downloads the file with NCBI's MD5 and refuses it unless the MD5 matches, computes
its SHA-256, and reads it (``clinvar_vcf``), refusing a file whose ``##fileDate`` is not the
date in its name or whose ``##reference`` is not the assembly. Each record that names an allele
is written into the release's own partition of ``<assembly>/SNV_INDEL/clinvar``
(``clinvar_release_store``). The release is released, that is recorded in
``reference_dataset_imports`` and so made the current one, only when the partition holds exactly
the rows stored, and when it has no more than ``MAX_RECORD_DROP`` fewer rows than the current
release. Its release is an event on the clinical audit chain ``system:clinvar-releases``, in the
same transaction. A load that fails, is refused or stopped drops its partition: nothing of it
stays, and the current release stays current. Only a release whose own transaction fails keeps
its partition, as the commit may have made it current: the next load drops it unless it was. ClickHouse keeps the current release and the one
before it (``KEPT_RELEASES``): a release made current drops the partition of the one before
that, and each load first drops a partition no recorded release kept (one an interrupted load
left).

An administrator asks for a load on the Reference page; an assembly import from UCSC, and a
start (``REFERENCE_BOOTSTRAP_CLINVAR``), queue one for a GRCh37 or GRCh38 assembly with no
ClinVar release (``queue_clinvar_load_if_missing``). As for the DGV load (``dgv_load_jobs``),
the job is a row of ``clinvar_load_jobs`` run as a background task of the process that queued
it: one job queued or running at a time, a worker id and a heartbeat, a job whose worker has
been silent for the stale window closed as failed by the status read and the next request. A
load an administrator asked for runs, and is released, only while that account is an active
administrator; a load a start queued is no account's (``STARTUP_REQUESTER``).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, get_args
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error, error_text
from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import (
    ClinvarAssemblyReleasesOut,
    ClinvarLoadJobOut,
    ClinvarLoadStatusOut,
    ClinvarReleaseOut,
)
from .access_control import CurrentUser, is_admin_user
from .clinical_audit_service import record_clinical_event
from .clinvar_download import (
    ClinvarFile,
    DownloadedClinvarFile,
    clinvar_http_client,
    download_clinvar_file,
    newest_clinvar_file,
)
from .clinvar_release_store import (
    count_clinvar_rows,
    drop_clinvar_release,
    ensure_clinvar_table,
    insert_clinvar_rows,
    stored_clinvar_releases,
)
from .clinvar_releases import (
    CLINVAR_ASSEMBLY_FOLDERS,
    CLINVAR_DATASET_TYPE,
    ClinvarRelease,
    current_clinvar_release,
    recorded_clinvar_releases,
)
from .clinvar_vcf import ClinvarFileReader, ClinvarFileRefused, ClinvarRow
from .metadata_service import get_current_user_by_id
from .reference_metadata_service import record_reference_import

logger = logging.getLogger(__name__)

# Who asked for a load no account asked for: a start's, or an assembly import's at a start.
SystemRequester = Literal["startup-bootstrap"]
STARTUP_REQUESTER: SystemRequester = "startup-bootstrap"
_SYSTEM_REQUESTERS = frozenset(get_args(SystemRequester))
# The clinical audit chain each release made current is an event of.
CLINVAR_RELEASE_CHAIN = "system:clinvar-releases"
CLINVAR_RELEASE_ACTION = "clinvar_release_current"
# A release with more than this share fewer rows than the current one is not released (B3b).
MAX_RECORD_DROP = 0.05
# The worker writes the job's progress and heartbeat at most this often, and a job with no sign
# of its load for the stale window has lost its worker.
CLINVAR_LOAD_HEARTBEAT_SECONDS = 30.0
CLINVAR_LOAD_STALE_HEARTBEAT = timedelta(minutes=10)
_INSERT_ROWS = 20_000
_UNCHANGED = "Nothing of this release was kept: the current ClinVar release is unchanged."
_STALE_ERROR = (
    "Interrupted: no sign of its load for "
    f"{int(CLINVAR_LOAD_STALE_HEARTBEAT.total_seconds() // 60)} minutes; the server running it "
    f"was probably restarted or redeployed. {_UNCHANGED} Load it again."
)
_FAILED = "status = 'failed', worker_id = NULL, completed_at = now(), error = :error"


def _requester_lost(lapse: str) -> str:
    return (
        f"Not loaded: the account that requested this load {lapse}, and a load is run and "
        f"released only for an active administrator. {_UNCHANGED} An active administrator "
        "requests it again."
    )


# Why a load is not run, or not released, as the job's error says. None names the account:
# the job does.
REQUESTER_GONE_ERROR = _requester_lost("no longer exists")
REQUESTER_INACTIVE_ERROR = _requester_lost("has been deactivated")
REQUESTER_NOT_ADMIN_ERROR = _requester_lost("is no longer an administrator")


class _JobClosed(Exception):
    """The job was closed while it ran (its worker taken for gone): no longer this worker's."""


class _RequesterRefused(Exception):
    """The account that asked for the load may no longer run or release it; the message says
    why (``REQUESTER_*_ERROR``)."""


# Each queued load's task, kept until it ends: the event loop holds only a weak reference to a
# task, and one nothing else refers to may be collected before it is done.
_RUNNING: set[asyncio.Task[None]] = set()


def recorded_release(file: ClinvarFile, sha256: str) -> str:
    """The release a load records (``reference_dataset_imports.source_version``): the release,
    the file and its SHA-256, e.g. ``2026-11-22 (clinvar_20261122.vcf.gz, SHA-256 3f1c…)``."""
    return f"{file.release.isoformat()} ({file.file_name}, SHA-256 {sha256})"


_JOB_COLUMNS = (
    "id::text AS id, assembly_id::text AS assembly_id, assembly_name, release, source_url, "
    "source_md5, source_sha256, status, requested_by, requested_at, started_at, completed_at, "
    "bytes_read, bytes_total, records_read, records_stored, records_skipped, error"
)


def _job_out(row: RowMapping) -> ClinvarLoadJobOut:
    return ClinvarLoadJobOut(
        _id=row["id"],
        assembly_id=row["assembly_id"],
        assembly_name=row["assembly_name"],
        release=row["release"],
        source_url=row["source_url"],
        source_md5=row["source_md5"],
        source_sha256=row["source_sha256"],
        status=row["status"],
        requested_by=row["requested_by"],
        requested_at=row["requested_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        bytes_read=int(row["bytes_read"]),
        bytes_total=row["bytes_total"],
        records_read=int(row["records_read"]),
        records_stored=int(row["records_stored"]),
        records_skipped={str(reason): int(count) for reason, count in dict(row["records_skipped"]).items()},
        error=row["error"],
    )


def _release_out(release: ClinvarRelease | None) -> ClinvarReleaseOut | None:
    if release is None:
        return None
    return ClinvarReleaseOut(
        release=release.release,
        records=release.records,
        source_url=release.source_url,
        source_version=release.source_version,
        loaded_at=release.loaded_at,
        loaded_by=release.loaded_by,
    )


async def _close_jobs_whose_worker_is_gone(session: AsyncSession) -> None:
    """Close as failed an active job with no sign of its load for the stale window: a running
    job's last sign is its heartbeat, a queued one's its request (its run starts at once). The
    caller commits."""
    closed = (
        await session.execute(
            text(
                """
                UPDATE clinvar_load_jobs
                SET status = 'failed', worker_id = NULL, completed_at = now(), error = :error
                WHERE status IN ('queued', 'running')
                  AND COALESCE(heartbeat_at, requested_at)
                      < now() - CAST(:stale_window AS interval)
                RETURNING id::text AS id
                """
            ),
            {"stale_window": CLINVAR_LOAD_STALE_HEARTBEAT, "error": _STALE_ERROR},
        )
    ).mappings().all()
    for row in closed:
        logger.warning("Closed ClinVar load %s: its worker is gone", row["id"])


async def get_clinvar_load_status(session: AsyncSession) -> ClinvarLoadStatusOut:
    """The active load, the ten latest, and the current and previous release of each assembly
    ClinVar publishes a VCF for, as the Reference page polls them. A job whose worker is gone
    is closed here first."""
    await _close_jobs_whose_worker_is_gone(session)
    await session.commit()
    rows = (
        await session.execute(
            text(f"SELECT {_JOB_COLUMNS} FROM clinvar_load_jobs ORDER BY requested_at DESC LIMIT 10")
        )
    ).mappings().all()
    jobs = [_job_out(row) for row in rows]
    present = set((await session.execute(text("SELECT assembly_name FROM assemblies"))).scalars())
    assemblies: list[ClinvarAssemblyReleasesOut] = []
    for name in CLINVAR_ASSEMBLY_FOLDERS:
        if name not in present:
            continue
        kept = await recorded_clinvar_releases(session, name)
        assemblies.append(
            ClinvarAssemblyReleasesOut(
                assembly_name=name,
                current=_release_out(kept[0] if kept else None),
                previous=_release_out(kept[1] if len(kept) > 1 else None),
            )
        )
    active = next((job for job in jobs if job.status in ("queued", "running")), None)
    return ClinvarLoadStatusOut(active_job=active, recent_jobs=jobs, assemblies=assemblies)


def _start(job_id: str) -> None:
    task = asyncio.create_task(_run_job(job_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def queue_clinvar_load(
    session: AsyncSession,
    *,
    assembly: str,
    requested_by: CurrentUser | SystemRequester,
) -> ClinvarLoadJobOut:
    """Queue the load of ClinVar's newest weekly VCF for the assembly, and start it.
    ``requested_by`` is the administrator who asked, kept by the account's id, or the system
    requester that queued it. 400 for an assembly ClinVar publishes no VCF for, 404 for an
    unknown one, 409 while another load is queued or running."""
    if assembly not in CLINVAR_ASSEMBLY_FOLDERS:
        raise HTTPException(
            status_code=400,
            detail=f"ClinVar publishes no VCF for {assembly}: it has no ClinVar release.",
        )
    assembly_id = (
        await session.execute(
            text("SELECT id::text FROM assemblies WHERE assembly_name = :assembly"),
            {"assembly": assembly},
        )
    ).scalar_one_or_none()
    if assembly_id is None:
        raise HTTPException(status_code=404, detail="Assembly not found")
    if isinstance(requested_by, CurrentUser):
        requester, requester_id = str(requested_by.email), requested_by.id
    else:
        requester, requester_id = requested_by, None
    try:
        await _close_jobs_whose_worker_is_gone(session)
        row = (
            await session.execute(
                text(
                    f"""
                    INSERT INTO clinvar_load_jobs (
                        assembly_id, assembly_name, status, requested_by, requested_by_id
                    )
                    VALUES (
                        CAST(:assembly_id AS uuid), :assembly_name, 'queued', :requested_by,
                        CAST(:requested_by_id AS uuid)
                    )
                    RETURNING {_JOB_COLUMNS}
                    """
                ),
                {
                    "assembly_id": assembly_id,
                    "assembly_name": assembly,
                    "requested_by": requester,
                    "requested_by_id": requester_id,
                },
            )
        ).mappings().one()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A ClinVar load is already queued or running.") from exc
    job = _job_out(row)
    _start(str(job.id))
    return job


async def queue_clinvar_load_if_missing(
    session: AsyncSession,
    *,
    assembly_name: str,
    requested_by: CurrentUser | None,
) -> tuple[ClinvarLoadJobOut | None, str | None]:
    """Queue the load for an assembly ClinVar publishes a VCF for and CoGA holds no release of,
    as an assembly import (as its administrator) and a start (``requested_by`` None) do. The
    job, or None, and a sentence for the admin when there is one to say."""
    if assembly_name not in CLINVAR_ASSEMBLY_FOLDERS:
        return None, None
    known = (
        await session.execute(
            text("SELECT EXISTS (SELECT 1 FROM assemblies WHERE assembly_name = :assembly)"),
            {"assembly": assembly_name},
        )
    ).scalar_one()
    if not known or await current_clinvar_release(session, assembly_name) is not None:
        return None, None
    try:
        job = await queue_clinvar_load(
            session, assembly=assembly_name, requested_by=requested_by or STARTUP_REQUESTER
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return None, (
            f"The ClinVar load for {assembly_name} was not started: another ClinVar load is "
            "running. Load it on the Reference page once that one has ended."
        )
    return job, f"The ClinVar load for {assembly_name} has started in the background."


async def _claim_job(job_id: str, worker_id: str) -> dict[str, Any] | None:
    """Take the queued job for this worker: running, with its first heartbeat. None when the job
    is no longer queued (or gone): it is not this worker's to run."""
    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    """
                    UPDATE clinvar_load_jobs
                    SET status = 'running', worker_id = :worker_id,
                        started_at = now(), heartbeat_at = now()
                    WHERE id = CAST(:job_id AS uuid) AND status = 'queued'
                    RETURNING assembly_id::text AS assembly_id, assembly_name, requested_by,
                        requested_by_id::text AS requested_by_id
                    """
                ),
                {"job_id": job_id, "worker_id": worker_id},
            )
        ).mappings().first()
        await session.commit()
    return dict(row) if row is not None else None


async def _update_job(job_id: str, worker_id: str | None, assignments: str, params: dict[str, Any]) -> bool:
    """Write to the job while it is this worker's; False once it is not (it was closed).
    Without a worker (a run that could not take its job), only a job still queued is written."""
    bound = {**params, "job_id": job_id}
    if worker_id is not None:
        owned = "worker_id = :worker_id"
        bound["worker_id"] = worker_id
    else:
        owned = "status = 'queued'"
    async with get_postgres_sessionmaker()() as session:
        result = await session.execute(
            text(
                f"UPDATE clinvar_load_jobs SET {assignments} "
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
    except Exception as exc:  # noqa: BLE001 - logged by type; the job is closed as stale later
        logger.error(
            "ClinVar load %s could not be marked failed (%s); it stays active until it has been "
            "silent for the stale window and is closed",
            job_id,
            describe_error(exc),
        )


async def _requester_refusal(job: dict[str, Any]) -> str | None:
    """Why the load is not run, or not released, for the account that asked for it: it no
    longer exists, has been deactivated, or is no longer an administrator. None while it is an
    active administrator, and for a load a system requester queued, which is no account's."""
    requester_id = job.get("requested_by_id")
    if requester_id is None and job.get("requested_by") in _SYSTEM_REQUESTERS:
        return None
    user = None
    if requester_id:
        async with get_postgres_sessionmaker()() as session:
            user = await get_current_user_by_id(session, str(requester_id))
    if user is None:
        return REQUESTER_GONE_ERROR
    if not user.is_active:
        return REQUESTER_INACTIVE_ERROR
    if not is_admin_user(user):
        return REQUESTER_NOT_ADMIN_ERROR
    return None


class _Progress:
    """The job's progress and heartbeat, written at most every CLINVAR_LOAD_HEARTBEAT_SECONDS
    from a session of its own. Raises ``_JobClosed`` once the job is no longer this worker's; a
    write that fails is logged and the load goes on."""

    def __init__(self, job_id: str, worker_id: str) -> None:
        self._job_id, self._worker_id = job_id, worker_id
        self._loop = asyncio.get_running_loop()
        self._last = self._loop.time()

    async def download(self, bytes_read: int, bytes_total: int | None) -> None:
        await self._write(
            "bytes_read = :bytes_read, bytes_total = :bytes_total",
            {"bytes_read": bytes_read, "bytes_total": bytes_total},
        )

    async def records(self, reader: ClinvarFileReader, stored: int, *, now: bool = False) -> None:
        await self._write(
            "records_read = :records_read, records_stored = :records_stored, "
            "records_skipped = CAST(:records_skipped AS jsonb)",
            {
                "records_read": reader.records_read,
                "records_stored": stored,
                "records_skipped": json.dumps(dict(reader.skipped)),
            },
            now=now,
        )

    async def _write(self, assignments: str, params: dict[str, Any], *, now: bool = False) -> None:
        moment = self._loop.time()
        if not now and moment - self._last < CLINVAR_LOAD_HEARTBEAT_SECONDS:
            return
        self._last = moment
        try:
            still_ours = await _update_job(
                self._job_id, self._worker_id, f"heartbeat_at = now(), {assignments}", params
            )
        except Exception as exc:  # noqa: BLE001 - logged by type only; the load goes on
            logger.error("ClinVar load %s heartbeat failed (%s)", self._job_id, describe_error(exc))
            return
        if not still_ours:
            raise _JobClosed()


def _row_batches(path: Path, reader: ClinvarFileReader) -> Iterator[list[ClinvarRow]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        batch: list[ClinvarRow] = []
        for row in reader.rows(handle):
            batch.append(row)
            if len(batch) >= _INSERT_ROWS:
                yield batch
                batch = []
        if batch:
            yield batch


async def _store(
    assembly_name: str, release: date, path: Path, reader: ClinvarFileReader, progress: _Progress
) -> int:
    """Read the file into the release's partition, one batch at a time; the rows stored."""
    batches = _row_batches(path, reader)
    stored = 0
    while (batch := await asyncio.to_thread(next, batches, None)) is not None:
        await insert_clinvar_rows(assembly_name, release, batch)
        stored += len(batch)
        await progress.records(reader, stored)
    await progress.records(reader, stored, now=True)
    return stored


async def _check_release(
    assembly_name: str, file: ClinvarFile, stored: int, current: ClinvarRelease | None
) -> None:
    """``ClinvarFileRefused`` unless the release's partition holds exactly the rows stored, and
    at most MAX_RECORD_DROP fewer than the current release."""
    if stored == 0:
        raise ClinvarFileRefused(f"{file.file_name} holds no ClinVar record.")
    held = await count_clinvar_rows(assembly_name, file.release)
    if held != stored:
        raise ClinvarFileRefused(
            f"ClickHouse holds {held} rows of ClinVar {file.release.isoformat()}, not the {stored} stored."
        )
    if current is not None and stored < current.records * (1 - MAX_RECORD_DROP):
        raise ClinvarFileRefused(
            f"ClinVar {file.release.isoformat()} has {stored} records, more than "
            f"{MAX_RECORD_DROP:.0%} fewer than the current release {current.release.isoformat()} "
            f"({current.records})."
        )


def _release_record(release: ClinvarRelease | None) -> dict[str, Any] | None:
    if release is None:
        return None
    return {"release": release.release.isoformat(), "records": release.records, "source": release.source_version}


async def _release(
    job: dict[str, Any],
    file: ClinvarFile,
    downloaded: DownloadedClinvarFile,
    stored: int,
    current: ClinvarRelease | None,
    job_id: str,
) -> None:
    """Make the release the current one: its ``reference_dataset_imports`` row and its event on
    the clinical audit chain, in one transaction."""
    version = recorded_release(file, downloaded.sha256)
    async with get_postgres_sessionmaker()() as session:
        await record_reference_import(
            session,
            assembly_id=job["assembly_id"],
            dataset_type=CLINVAR_DATASET_TYPE,
            inserted=stored,
            replaced=current is not None,
            source="clinvar",
            performed_by=job["requested_by"],
            source_url=file.url,
            source_version=version,
            source_release_date=file.release,
        )
        await record_clinical_event(
            session,
            family_uuid=None,
            family_identifier=CLINVAR_RELEASE_CHAIN,
            variant_id=None,
            actor=job["requested_by"],
            actor_id=job.get("requested_by_id"),
            action=CLINVAR_RELEASE_ACTION,
            summary=(
                f"ClinVar {file.release.isoformat()} is the current ClinVar release for "
                f"{job['assembly_name']}"
            ),
            before=_release_record(current),
            after={
                "release": file.release.isoformat(),
                "records": stored,
                "source": version,
                "url": file.url,
                "md5": downloaded.md5,
            },
            metadata={"job_id": job_id, "assembly": job["assembly_name"]},
        )
        await session.commit()


async def _keep_only(assembly_name: str, kept: set[date]) -> None:
    """Drop every partition of the assembly's table but those of the ``kept`` releases."""
    for release in await stored_clinvar_releases(assembly_name) - kept:
        await drop_clinvar_release(assembly_name, release)


async def _advance(job_id: str, owner: str, assignments: str, params: dict[str, Any]) -> None:
    """Write the job's next step; ``_JobClosed`` once it is no longer this worker's."""
    if not await _update_job(job_id, owner, f"heartbeat_at = now(), {assignments}", params):
        raise _JobClosed()


async def _drop_quietly(assembly_name: str, release: date) -> None:
    try:
        await drop_clinvar_release(assembly_name, release)
    except Exception as exc:  # noqa: BLE001 - the next load drops it: no recorded release keeps it
        logger.error(
            "ClinVar %s partition of %s not dropped (%s); the next load drops it",
            release,
            assembly_name,
            describe_error(exc),
        )


async def _store_and_release(
    job_id: str,
    owner: str,
    job: dict[str, Any],
    file: ClinvarFile,
    downloaded: DownloadedClinvarFile,
    current: ClinvarRelease | None,
    progress: _Progress,
) -> int:
    """Write the file into its release's partition, check it, and make it the current release;
    the rows stored. A store or a check that fails, is refused or is stopped drops the
    partition: nothing of the release stays. A release whose own transaction fails leaves it
    to the next load, which drops a partition no recorded release keeps: the commit may have
    made it current."""
    assembly = job["assembly_name"]
    reader = ClinvarFileReader(assembly_name=assembly, release=file.release, file_name=file.file_name)
    try:
        stored = await _store(assembly, file.release, downloaded.path, reader, progress)
        await _check_release(assembly, file, stored, current)
        # Released only while the job is still this worker's, and its requester still an active
        # administrator: the load ran for minutes.
        await _advance(job_id, owner, "records_stored = :stored", {"stored": stored})
        refusal = await _requester_refusal(job)
        if refusal is not None:
            raise _RequesterRefused(refusal)
    except BaseException:
        await _drop_quietly(assembly, file.release)
        raise
    await _release(job, file, downloaded, stored, current, job_id)
    return stored


async def _load(job_id: str, owner: str, job: dict[str, Any]) -> None:
    """Load and release NCBI's newest file for the job's assembly, and end the job.
    ``_RequesterRefused`` when its requester may no longer release it."""
    assembly = job["assembly_name"]
    async with get_postgres_sessionmaker()() as session:
        kept = await recorded_clinvar_releases(session, assembly)
    current = kept[0] if kept else None
    progress = _Progress(job_id, owner)
    async with clinvar_http_client() as http:
        file = await newest_clinvar_file(http, assembly)
        found = {"release": file.release, "source_url": file.url}
        if current is not None and file.release <= current.release:
            await _update_job(
                job_id,
                owner,
                "status = 'unchanged', worker_id = NULL, completed_at = now(), "
                "release = :release, source_url = :source_url",
                found,
            )
            logger.info("ClinVar for %s is current: %s", assembly, current.release)
            return
        await _advance(job_id, owner, "release = :release, source_url = :source_url", found)
        with tempfile.TemporaryDirectory(prefix="coga-clinvar-") as folder:
            downloaded = await download_clinvar_file(http, file, Path(folder), progress.download)
            await _advance(
                job_id,
                owner,
                "source_md5 = :md5, source_sha256 = :sha256, bytes_read = :size, bytes_total = :size",
                {"md5": downloaded.md5, "sha256": downloaded.sha256, "size": downloaded.size},
            )
            await ensure_clinvar_table(assembly)
            await _keep_only(assembly, {release.release for release in kept})
            stored = await _store_and_release(job_id, owner, job, file, downloaded, current, progress)
    try:
        await _keep_only(assembly, {file.release} | ({current.release} if current else set()))
    except Exception as exc:  # noqa: BLE001 - released; the next load drops what is left
        logger.error("ClinVar releases of %s before the kept two not dropped (%s)", assembly, describe_error(exc))
    await _update_job(job_id, owner, "status = 'completed', worker_id = NULL, completed_at = now()", {})
    logger.info("Released ClinVar %s for %s: %d alleles", file.release, assembly, stored)


async def _run_job(job_id: str) -> None:
    # Every step, the claim included, is inside the error handling: a job left queued or running
    # once this ends would refuse every later load until it is closed as stale.
    worker_id = f"{os.getpid()}-{uuid4().hex}"
    owner: str | None = None  # this worker, once it has taken the job
    try:
        job = await _claim_job(job_id, worker_id)
        if job is None:
            logger.info("ClinVar load %s is no longer queued; not started", job_id)
            return
        owner = worker_id
        refusal = await _requester_refusal(job)
        if refusal is not None:
            raise _RequesterRefused(refusal)
        timeout = settings.clinvar_load_timeout_seconds
        try:
            await asyncio.wait_for(_load(job_id, owner, job), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ClinvarFileRefused(
                f"The ClinVar load was stopped after {timeout} seconds (CLINVAR_LOAD_TIMEOUT_SECONDS)."
            ) from exc
    except _RequesterRefused as exc:
        logger.warning(
            "ClinVar load %s not run or released: the account that requested it is no longer an "
            "active administrator",
            job_id,
        )
        await _record_failure(job_id, owner, error_text(exc))
    except _JobClosed:
        logger.warning(
            "ClinVar load %s was closed while it ran (its worker was taken for gone); stopped "
            "without releasing it",
            job_id,
        )
    except ClinvarFileRefused as exc:
        logger.error("ClinVar load %s refused its file", job_id)
        await _record_failure(job_id, owner, f"{error_text(exc)[:1800]} {_UNCHANGED}")
    except httpx.HTTPError as exc:
        logger.error("ClinVar load %s could not download its file (%s)", job_id, describe_error(exc))
        await _record_failure(
            job_id, owner, f"The ClinVar file could not be downloaded ({describe_error(exc)}). {_UNCHANGED}"
        )
    except Exception as exc:  # noqa: BLE001 - the job's error names it by type; nothing is released
        logger.error("ClinVar load %s failed (%s)", job_id, describe_error(exc))
        await _record_failure(job_id, owner, f"The ClinVar load failed ({describe_error(exc)}). {_UNCHANGED}")
