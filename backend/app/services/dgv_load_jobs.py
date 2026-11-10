"""Loads of the pinned DGV release into ``dgv_variants``: downloaded, checked and loaded in one go.

The Database of Genomic Variants publishes one "variants" file per assembly and release (about
two million rows, 360 MB). The release CoGA loads for GRCh37 and GRCh38 is pinned in
``DGV_PINNED_RELEASES`` (``core/config.py``) by its URL, SHA-256 and size (TRACE-14). A load
streams that file from DGV, parses it as it arrives and inserts its rows in one transaction,
after deleting the assembly's DGV rows in the same transaction; it commits only once the whole
file has been read and its size and SHA-256 are the pinned ones. A file that is not the pinned
one, a download cut short or a load that fails or is stopped commits nothing: the DGV rows from
before stay as they were. Nothing is written to disk, so the load needs no space for the file.
The load is recorded in ``reference_dataset_imports`` with the release, the file and its SHA-256.

An administrator asks for a load on the Reference page; an assembly import from UCSC, and a
start (``REFERENCE_BOOTSTRAP_DGV``), queue one for an assembly with a pinned release and no DGV
rows (``queue_dgv_load_if_missing``). As with the clinical CNV knowledgebase rebuild
(``clinical_cnv_kb_jobs``), the job is a row of ``dgv_load_jobs``, run as a background task of
the process that queued it: one job is queued or running at a time (a partial unique index on a
constant), the running job carries its worker's id and a heartbeat, and the status read and a
load request first close, as failed, a job whose worker has been silent for the stale window. A
load an administrator asked for runs, and commits, only while that account is an active
administrator (SEC-35), checked as it starts and again before the commit; a load a start queued
is no account's (``STARTUP_REQUESTER``), as for the gene-reference refresh.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import logging
import os
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error
from ..core.config import DGV_PINNED_RELEASES, DgvPinnedRelease, settings
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import DgvLoadJobOut, DgvLoadStatusOut, DgvPinnedReleaseOut
from .access_control import CurrentUser, is_admin_user
from .metadata_service import get_current_user_by_id
from .reference_metadata_service import (
    _DGV_INSERT_CHUNK,
    insert_dgv_batch,
    parse_dgv_row,
    record_reference_import,
)

logger = logging.getLogger(__name__)

# Who asked for the load a start or an unattended import queues: no account, so it runs as it
# is (the gene-reference refresh's requester for the same case).
STARTUP_REQUESTER = "startup-bootstrap"
# The worker writes the job's progress and heartbeat at most this often while it loads, and a
# job with no sign of its load for the stale window has lost its worker.
DGV_LOAD_HEARTBEAT_SECONDS = 30.0
DGV_LOAD_STALE_HEARTBEAT = timedelta(minutes=10)
_STALE_ERROR = (
    "Interrupted: no sign of its load for "
    f"{int(DGV_LOAD_STALE_HEARTBEAT.total_seconds() // 60)} minutes; the server running it was "
    "probably restarted or redeployed. Nothing was loaded; load the release again."
)
_FAILED = "status = 'failed', worker_id = NULL, completed_at = now(), error = :error"
_PARSE_BLOCK = 1 << 20  # the download is parsed and inserted 1 MiB at a time
# A read that waits this long for DGV is a stalled download, resumed from the byte it reached,
# at most _MAX_RESUMES times.
_HTTP_TIMEOUT = httpx.Timeout(60.0, read=120.0)
_MAX_RESUMES = 5
# The file as it is, not compressed for the transfer: the pinned size and SHA-256, and the byte
# ranges a resumed download asks for, are the file's own bytes.
_HTTP_HEADERS = {"User-Agent": "CoGA-reference-loader/1.0", "Accept-Encoding": "identity"}
_DGV_HEADER_FIRST_CELLS = frozenset({"variantaccession", "variant_accession"})
_UNCHANGED = "Nothing was loaded: the DGV variants from before are unchanged."


def _requester_lost(lapse: str) -> str:
    return (
        f"Not loaded: the account that requested this load {lapse}, and a load is run and "
        "committed only for an active administrator. " + _UNCHANGED
        + " An active administrator requests it again."
    )


# Why a load is not run, or not committed, as the job's error says (SEC-35). None names the
# account: the job does.
REQUESTER_GONE_ERROR = _requester_lost("no longer exists")
REQUESTER_INACTIVE_ERROR = _requester_lost("has been deactivated")
REQUESTER_NOT_ADMIN_ERROR = _requester_lost("is no longer an administrator")


class DgvFileRefused(RuntimeError):
    """The file DGV served is not the pinned release, or holds no DGV rows: nothing is loaded."""


class _JobClosed(Exception):
    """The job was closed while it ran (its worker taken for gone): no longer this worker's."""


# Each queued load's task, kept until it ends: the event loop holds only a weak reference to a
# task, and one nothing else refers to may be collected before it is done.
_RUNNING: set[asyncio.Task[None]] = set()


def pinned_release(assembly_name: str) -> DgvPinnedRelease | None:
    """The DGV release pinned for the assembly, or None when the assembly has none."""
    return DGV_PINNED_RELEASES.get(assembly_name)


def _file_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def recorded_release(pin: DgvPinnedRelease, digest: str) -> str:
    """The release a load records (``reference_dataset_imports.source_version``): DGV's
    release, the file and its SHA-256, e.g. ``2025-12-01 (GRCh38_hg38_variants_2025-12-01.txt,
    SHA-256 3f1c…)``."""
    return f"{pin.release} ({_file_name(pin.url)}, SHA-256 {digest})"


def parse_dgv_lines(
    lines: Iterable[str],
    *,
    header_index: dict[str, int] | None,
    assembly_id: str,
) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    """The ``dgv_variants`` rows of these lines of a DGV TSV, and the header's column index.

    The header (its first cell ``variantaccession``) names the columns, as in the upload and
    ``scripts/import_dgv.py``; ``header_index`` carries it from one part of the file to the next.
    Comment and track lines, and rows ``parse_dgv_row`` cannot read, are skipped."""
    rows: list[dict[str, Any]] = []
    for row in csv.reader(lines, delimiter="\t"):
        if not row:
            continue
        first = row[0].strip().lower()
        if header_index is None and first in _DGV_HEADER_FIRST_CELLS:
            header_index = {name.strip().lower(): index for index, name in enumerate(row)}
            continue
        if first.startswith("#") or first.startswith("track"):
            continue
        parsed = parse_dgv_row(row, header_index=header_index, assembly_id=assembly_id)
        if parsed is not None:
            rows.append(parsed)
    return rows, header_index


def _parse_block(
    block: bytes, header_index: dict[str, int] | None, assembly_id: str
) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    """``parse_dgv_lines`` for whole lines of the download, as bytes. DGV's file is UTF-8; a
    block ends at a line end, so no character is split between two blocks."""
    lines = block.decode("utf-8").splitlines()
    return parse_dgv_lines(lines, header_index=header_index, assembly_id=assembly_id)


def _job_out(row: RowMapping | dict[str, Any]) -> DgvLoadJobOut:
    return DgvLoadJobOut(
        _id=row["id"],
        assembly_id=row["assembly_id"],
        assembly_name=row["assembly_name"],
        release=row["release"],
        source_url=row["source_url"],
        status=row["status"],
        requested_by=row.get("requested_by"),
        requested_at=row["requested_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        bytes_read=int(row.get("bytes_read") or 0),
        bytes_total=row.get("bytes_total"),
        inserted=int(row.get("inserted") or 0),
        error=row.get("error"),
    )


_JOB_COLUMNS = (
    "id::text AS id, assembly_id::text AS assembly_id, assembly_name, release, source_url, "
    "status, requested_by, requested_at, started_at, completed_at, bytes_read, bytes_total, "
    "inserted, error"
)


def _releases() -> list[DgvPinnedReleaseOut]:
    return [
        DgvPinnedReleaseOut(
            assembly_name=name, release=pin.release, source_url=pin.url, size_bytes=pin.size_bytes
        )
        for name, pin in DGV_PINNED_RELEASES.items()
    ]


async def _close_jobs_whose_worker_is_gone(session: AsyncSession) -> None:
    """Close as failed an active job with no sign of its load for the stale window: a running
    job's last sign is its heartbeat, a queued one's its request (its run starts at once). The
    database clock judges both, as it stamped them. The caller commits."""
    closed = (
        await session.execute(
            text(
                """
                UPDATE dgv_load_jobs
                SET status = 'failed', worker_id = NULL, completed_at = now(), error = :error
                WHERE status IN ('queued', 'running')
                  AND COALESCE(heartbeat_at, requested_at)
                      < now() - CAST(:stale_window AS interval)
                RETURNING id::text AS id
                """
            ),
            {"stale_window": DGV_LOAD_STALE_HEARTBEAT, "error": _STALE_ERROR},
        )
    ).mappings().all()
    for row in closed:
        logger.warning("Closed DGV load %s: its worker is gone", row["id"])


async def get_dgv_load_status(session: AsyncSession) -> DgvLoadStatusOut:
    """The active load, the ten latest, and the release pinned per assembly, as the Reference
    page polls them. A job whose worker is gone is closed here first."""
    await _close_jobs_whose_worker_is_gone(session)
    await session.commit()
    rows = (
        await session.execute(
            text(f"SELECT {_JOB_COLUMNS} FROM dgv_load_jobs ORDER BY requested_at DESC LIMIT 10")
        )
    ).mappings().all()
    jobs = [_job_out(row) for row in rows]
    active = next((job for job in jobs if job.status in ("queued", "running")), None)
    return DgvLoadStatusOut(active_job=active, recent_jobs=jobs, releases=_releases())


def _start(job_id: str) -> None:
    task = asyncio.create_task(_run_job(job_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def queue_dgv_load(
    session: AsyncSession,
    *,
    assembly: str,
    requested_by: CurrentUser | None,
) -> DgvLoadJobOut:
    """Queue the load of the assembly's pinned DGV release, and start it. ``requested_by`` is
    the administrator who asked, kept by the account's id (SEC-35), or None for a load a start
    queues (``STARTUP_REQUESTER``). 400 for an assembly with no pinned release, 404 for an
    unknown one, 409 while another load is queued or running."""
    pin = pinned_release(assembly)
    if pin is None:
        raise HTTPException(
            status_code=400,
            detail=f"No DGV release is pinned for {assembly}: upload its DGV file instead.",
        )
    assembly_row = (
        await session.execute(
            text("SELECT id::text AS id, assembly_name FROM assemblies WHERE assembly_name = :assembly"),
            {"assembly": assembly},
        )
    ).mappings().first()
    if assembly_row is None:
        raise HTTPException(status_code=404, detail="Assembly not found")
    try:
        await _close_jobs_whose_worker_is_gone(session)
        row = (
            await session.execute(
                text(
                    f"""
                    INSERT INTO dgv_load_jobs (
                        assembly_id, assembly_name, release, source_url, status, requested_by,
                        requested_by_id
                    )
                    VALUES (
                        CAST(:assembly_id AS uuid), :assembly_name, :release, :source_url, 'queued',
                        :requested_by, CAST(:requested_by_id AS uuid)
                    )
                    RETURNING {_JOB_COLUMNS}
                    """
                ),
                {
                    "assembly_id": assembly_row["id"],
                    "assembly_name": assembly_row["assembly_name"],
                    "release": pin.release,
                    "source_url": pin.url,
                    "requested_by": requested_by.email if requested_by is not None else STARTUP_REQUESTER,
                    "requested_by_id": requested_by.id if requested_by is not None else None,
                },
            )
        ).mappings().first()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A DGV load is already queued or running.") from exc
    if row is None:  # an INSERT ... RETURNING gives back the row it inserted
        raise RuntimeError("The DGV load was not recorded")
    job = _job_out(row)
    _start(str(job.id))
    return job


async def queue_dgv_load_if_missing(
    session: AsyncSession,
    *,
    assembly_name: str,
    requested_by: CurrentUser | None,
) -> tuple[DgvLoadJobOut | None, str | None]:
    """Queue the pinned release's load for an assembly that has no DGV rows yet, as an assembly
    import and a start do. The job, or None, and a sentence for the admin when there is one to
    say: None when the assembly has no pinned release or already has DGV rows."""
    if pinned_release(assembly_name) is None:
        return None, None
    assembly_row = (
        await session.execute(
            text(
                "SELECT a.id::text AS id, "
                "EXISTS (SELECT 1 FROM dgv_variants d WHERE d.assembly_id = a.id) AS loaded "
                "FROM assemblies a WHERE a.assembly_name = :assembly ORDER BY a.version DESC LIMIT 1"
            ),
            {"assembly": assembly_name},
        )
    ).mappings().first()
    if assembly_row is None or assembly_row["loaded"]:
        return None, None
    try:
        job = await queue_dgv_load(session, assembly=assembly_name, requested_by=requested_by)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return None, (
            f"The DGV load for {assembly_name} was not started: another DGV load is running. "
            "Load it on the Reference page once that one has ended."
        )
    return job, f"The DGV {job.release} load for {assembly_name} has started in the background."


async def _claim_job(job_id: str, worker_id: str) -> dict[str, Any] | None:
    """Take the queued job for this worker: running, with its first heartbeat. None when the job
    is no longer queued (or gone): it is not this worker's to run."""
    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    """
                    UPDATE dgv_load_jobs
                    SET status = 'running', worker_id = :worker_id,
                        started_at = now(), heartbeat_at = now()
                    WHERE id = CAST(:job_id AS uuid) AND status = 'queued'
                    RETURNING assembly_id::text AS assembly_id, assembly_name, release, source_url,
                        requested_by, requested_by_id::text AS requested_by_id
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
                f"UPDATE dgv_load_jobs SET {assignments} "
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
            "DGV load %s could not be marked failed (%s); it stays active until it has been "
            "silent for the stale window and is closed",
            job_id,
            describe_error(exc),
        )


async def _requester_refusal(job: dict[str, Any]) -> str | None:
    """Why the load is not run, or not committed, for the account that asked for it: it no
    longer exists (its deletion unlinks the job), has been deactivated, or is no longer an
    administrator, as the route required (SEC-35). None while it is an active administrator,
    and for a load a start queued, which is no account's."""
    requester_id = job.get("requested_by_id")
    if requester_id is None and job.get("requested_by") == STARTUP_REQUESTER:
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


def _refuse_plain_http(request: httpx.Request) -> None:
    if request.url.scheme != "https":
        raise DgvFileRefused(f"Refused to read DGV over a non-HTTPS URL: {request.url}")


async def _https_only(request: httpx.Request) -> None:
    """Every request of the download, each redirect included, goes over HTTPS."""
    _refuse_plain_http(request)


def _resumes_at(response: httpx.Response, offset: int) -> bool:
    """Whether a range request's answer is the file from ``offset`` on: 206, its Content-Range
    starting there. A 200 would be the whole file again."""
    if response.status_code != 206:
        return False
    content_range = response.headers.get("content-range", "")
    return content_range.startswith(f"bytes {offset}-")


async def load_dgv_release(
    session: AsyncSession,
    *,
    assembly_id: str,
    pin: DgvPinnedRelease,
    progress: Callable[[int, int, int], Awaitable[None]],
    client: httpx.AsyncClient | None = None,
) -> tuple[int, str]:
    """Stream the pinned file from DGV into ``dgv_variants`` for the assembly, in the caller's
    transaction, and return the rows inserted and the file's SHA-256.

    ``DgvFileRefused`` when the file is larger than the pinned size, or once read whole is not
    the pinned file (its size or SHA-256 differs), or holds no DGV rows: the caller then does
    not commit. ``progress(bytes_read, bytes_total, inserted)`` is awaited after each part."""
    owned = client is None
    http = client or httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT,
        follow_redirects=True,
        headers=_HTTP_HEADERS,
        event_hooks={"request": [_https_only]},
    )
    hasher = hashlib.sha256()
    bytes_read = inserted = 0
    pending = b""
    header_index: dict[str, int] | None = None
    batch: list[dict[str, Any]] = []

    async def insert_full_batches(final: bool) -> None:
        nonlocal batch, inserted
        while len(batch) >= _DGV_INSERT_CHUNK or (final and batch):
            part, batch = batch[:_DGV_INSERT_CHUNK], batch[_DGV_INSERT_CHUNK:]
            await insert_dgv_batch(session, part)
            inserted += len(part)

    async def take(chunk: bytes) -> None:
        nonlocal bytes_read, pending, header_index
        bytes_read += len(chunk)
        if bytes_read > pin.size_bytes:
            raise DgvFileRefused(
                f"Refused the DGV file {_file_name(pin.url)}: it is larger than the pinned "
                f"release {pin.release} ({pin.size_bytes} bytes). " + _UNCHANGED
            )
        hasher.update(chunk)
        pending += chunk
        if len(pending) < _PARSE_BLOCK:
            return
        cut = pending.rfind(b"\n")
        if cut < 0:
            return
        block, pending = pending[: cut + 1], pending[cut + 1 :]
        rows, header_index = await asyncio.to_thread(_parse_block, block, header_index, assembly_id)
        batch.extend(rows)
        await insert_full_batches(final=False)
        await progress(bytes_read, pin.size_bytes, inserted)

    interruptions = 0
    try:
        _refuse_plain_http(httpx.Request("GET", pin.url))
        while bytes_read < pin.size_bytes:  # a drop after the last byte needs no resume
            # DGV's server can stall or drop the connection part-way through the file: the
            # download then goes on from the byte it reached, as DGV serves byte ranges, so the
            # bytes hashed and parsed stay one unbroken file.
            resume_at = bytes_read
            headers = {"Range": f"bytes={resume_at}-"} if resume_at else {}
            try:
                async with http.stream("GET", pin.url, headers=headers) as response:
                    response.raise_for_status()
                    if resume_at and not _resumes_at(response, resume_at):
                        raise DgvFileRefused(
                            f"DGV did not resume the download of {_file_name(pin.url)} at byte "
                            f"{resume_at} after it was interrupted. " + _UNCHANGED
                        )
                    # As it arrives: what was received counts, should the next read stall.
                    async for chunk in response.aiter_bytes():
                        await take(chunk)
                if bytes_read < pin.size_bytes:
                    raise httpx.RemoteProtocolError("the download ended before the whole file was read")
            except httpx.TransportError as exc:
                interruptions += 1
                if interruptions > _MAX_RESUMES:
                    raise
                logger.warning(
                    "DGV download of %s interrupted at byte %d (%s); resuming (%d of %d)",
                    _file_name(pin.url),
                    bytes_read,
                    describe_error(exc),
                    interruptions,
                    _MAX_RESUMES,
                )
    finally:
        if owned:
            await http.aclose()
    if pending:
        rows, header_index = await asyncio.to_thread(_parse_block, pending, header_index, assembly_id)
        batch.extend(rows)
    digest = hasher.hexdigest()
    if bytes_read != pin.size_bytes or digest != pin.sha256:
        raise DgvFileRefused(
            f"Refused the DGV file {_file_name(pin.url)}: it is not the pinned release "
            f"{pin.release}. It has {bytes_read} bytes and SHA-256 {digest}; the pinned file has "
            f"{pin.size_bytes} bytes and SHA-256 {pin.sha256}. " + _UNCHANGED
        )
    await insert_full_batches(final=True)
    if inserted == 0:
        raise DgvFileRefused(f"The DGV file {_file_name(pin.url)} holds no DGV variant rows. " + _UNCHANGED)
    await progress(bytes_read, pin.size_bytes, inserted)
    return inserted, digest


def _progress_writer(job_id: str, worker_id: str) -> Callable[[int, int, int], Awaitable[None]]:
    """The job's progress and heartbeat, written at most every DGV_LOAD_HEARTBEAT_SECONDS from a
    session of its own. Raises ``_JobClosed`` once the job is no longer this worker's; a write
    that fails is logged and the load goes on."""
    loop = asyncio.get_running_loop()
    last = loop.time()

    async def write(bytes_read: int, bytes_total: int, inserted: int) -> None:
        nonlocal last
        now = loop.time()
        if now - last < DGV_LOAD_HEARTBEAT_SECONDS:
            return
        last = now
        try:
            still_ours = await _update_job(
                job_id,
                worker_id,
                "heartbeat_at = now(), bytes_read = :bytes_read, bytes_total = :bytes_total, "
                "inserted = :inserted",
                {"bytes_read": bytes_read, "bytes_total": bytes_total, "inserted": inserted},
            )
        except Exception as exc:  # noqa: BLE001 - logged by type only; the load goes on
            logger.error("DGV load %s heartbeat failed (%s)", job_id, describe_error(exc))
            return
        if not still_ours:
            raise _JobClosed()

    return write


async def _run_job(job_id: str) -> None:
    # Every step, the claim included, is inside the error handling: a job left queued or running
    # once this ends would refuse every later load until it is closed as stale.
    worker_id = f"{os.getpid()}-{uuid4().hex}"
    owner: str | None = None  # this worker, once it has taken the job
    try:
        job = await _claim_job(job_id, worker_id)
        if job is None:
            logger.info("DGV load %s is no longer queued; not started", job_id)
            return
        owner = worker_id
        refusal = await _requester_refusal(job)
        if refusal is not None:
            logger.warning(
                "DGV load %s not run: the account that requested it is no longer an active administrator",
                job_id,
            )
            await _update_job(job_id, owner, _FAILED, {"error": refusal})
            return
        pin = pinned_release(job["assembly_name"])
        if pin is None or pin.url != job["source_url"]:
            await _update_job(
                job_id,
                owner,
                _FAILED,
                {
                    "error": f"The DGV release pinned for {job['assembly_name']} is no longer the "
                    f"one this load was queued for. {_UNCHANGED} Load it again."
                },
            )
            return
        async with get_postgres_sessionmaker()() as session:
            replaced = bool(
                (
                    await session.execute(
                        text("SELECT EXISTS (SELECT 1 FROM dgv_variants WHERE assembly_id = CAST(:assembly_id AS uuid))"),
                        {"assembly_id": job["assembly_id"]},
                    )
                ).scalar_one()
            )
            await session.execute(
                text("DELETE FROM dgv_variants WHERE assembly_id = CAST(:assembly_id AS uuid)"),
                {"assembly_id": job["assembly_id"]},
            )
            timeout = settings.dgv_load_timeout_seconds
            try:
                inserted, digest = await asyncio.wait_for(
                    load_dgv_release(
                        session,
                        assembly_id=job["assembly_id"],
                        pin=pin,
                        progress=_progress_writer(job_id, owner),
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as exc:
                raise DgvFileRefused(
                    f"The DGV load was stopped after {timeout} seconds (DGV_LOAD_TIMEOUT_SECONDS). " + _UNCHANGED
                ) from exc
            # Commit only while the job is still this worker's, and its requester still an
            # active administrator: the load ran for minutes (SEC-35).
            if not await _update_job(job_id, owner, "heartbeat_at = now()", {}):
                raise _JobClosed()
            refusal = await _requester_refusal(job)
            if refusal is not None:
                logger.warning(
                    "DGV load %s not committed: the account that requested it is no longer an "
                    "active administrator",
                    job_id,
                )
                await session.rollback()
                await _update_job(job_id, owner, _FAILED, {"error": refusal})
                return
            await record_reference_import(
                session,
                assembly_id=job["assembly_id"],
                dataset_type="dgv",
                inserted=inserted,
                replaced=replaced,
                source="dgv",
                performed_by=job.get("requested_by"),
                source_url=pin.url,
                source_version=recorded_release(pin, digest),
                source_release_date=date.fromisoformat(pin.release),
            )
            await session.commit()
        await _update_job(
            job_id,
            owner,
            "status = 'completed', worker_id = NULL, completed_at = now(), inserted = :inserted, "
            "bytes_read = :bytes_read, bytes_total = :bytes_read",
            {"inserted": inserted, "bytes_read": pin.size_bytes},
        )
        logger.info("Loaded DGV %s for %s: %d variants", pin.release, job["assembly_name"], inserted)
    except _JobClosed:
        logger.warning(
            "DGV load %s was closed while it ran (its worker was taken for gone); stopped without "
            "loading it",
            job_id,
        )
    except DgvFileRefused as exc:
        logger.error("DGV load %s refused its file", job_id)
        await _record_failure(job_id, owner, str(exc)[:2000])
    except httpx.HTTPError as exc:
        logger.error("DGV load %s could not download its file (%s)", job_id, describe_error(exc))
        await _record_failure(job_id, owner, f"The DGV file could not be downloaded: {exc}. {_UNCHANGED}"[:2000])
    except Exception as exc:  # noqa: BLE001 - the job's error names it by type; nothing is committed
        logger.error("DGV load %s failed (%s)", job_id, describe_error(exc))
        await _record_failure(job_id, owner, f"The DGV load failed ({describe_error(exc)}). {_UNCHANGED}")
