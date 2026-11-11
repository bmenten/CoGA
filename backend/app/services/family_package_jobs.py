from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import require_uuid, rows_matched
from ..schemas import (
    ApiId,
    FamilyImportDatasetSummary,
    FamilyPackageImportJobOut,
    FamilyPackageValidationOut,
)
from .access_control import CurrentUser, has_superuser_rights

from .family_identifiers import IDENTIFIER_RULE, identifier_problem, visible
from .family_package_fingerprint import PackageFingerprint
from .family_package_source import package_folder_path
from .family_package_validation import take_package_fingerprint
from .family_package_common import (
    _dataset_summary_list,
    _issue_list,
    _json_list,
    _model_list_json,
    manifest_text_problem,
    manifest_text_sha256,
)
from .stored_values import json_object


# A job's heartbeat is stamped, and its age judged, by the database's clock (`now()`), never
# by a host's: the worker that writes it and the worker that finds it stale may run on
# hosts whose clocks differ.
FAMILY_IMPORT_STALE_HEARTBEAT = timedelta(minutes=10)

# What a job ends with when a worker finds that its import stopped part-way: its heartbeat
# went stale while it was `running` on its family, the state in which an import writes the
# family. Its process ended once the import may have begun writing, or is alive but could
# not write the heartbeat: such an import stops where it is once its heartbeat finds the
# job ended (family_package_import.run_family_import_job).
FAMILY_IMPORT_INTERRUPTED_ERROR = (
    "Interrupted: the import's heartbeat stopped for ten minutes while it was running on "
    "the family. Either the process running it stopped (a restart, a crash or running out "
    "of memory), or it could not write the heartbeat, and stops the import where it is as "
    "soon as the heartbeat reaches the database again. It is not run again: a run from the "
    "start would not undo what it had written. If it wrote any of the family, the family "
    "stays marked import-incomplete, naming the datasets the import had not finished: "
    "import them again with overwrite to complete it."
)

# A time in a job's log line, by the database's clock: "2026-10-09 14:05 UTC".
_DATABASE_TIME = """to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI "UTC"')"""


def _serialize_job(mapping: dict[str, Any]) -> FamilyPackageImportJobOut:
    return FamilyPackageImportJobOut(
        _id=ApiId(mapping["id"]),
        submitted_path=str(mapping["submitted_path"]),
        family_id=mapping.get("family_id"),
        project_id=ApiId(mapping["project_id"]) if mapping.get("project_id") else None,
        status=mapping["status"],
        dry_run=bool(mapping.get("dry_run")),
        worker_id=mapping.get("worker_id"),
        requested_by=mapping["requested_by"],
        requested_at=mapping["requested_at"],
        started_at=mapping.get("started_at"),
        heartbeat_at=mapping.get("heartbeat_at"),
        completed_at=mapping.get("completed_at"),
        validation_errors=_issue_list(mapping.get("validation_errors")),
        validation_warnings=_issue_list(mapping.get("validation_warnings")),
        logs=[str(item) for item in _json_list(mapping.get("logs"))],
        datasets=_dataset_summary_list(mapping.get("dataset_summaries")),
        metadata=json_object(mapping.get("metadata")),
        error=mapping.get("error"),
        manifest_sha256=mapping.get("manifest_sha256"),
        package_fingerprint=mapping.get("package_fingerprint"),
    )


_PROJECT_NOT_FOUND = "Project not found"


async def _require_import_project(session: AsyncSession, project_id: str) -> str:
    """The canonical id of the project an import request names.

    A value that spells no UUID, or a UUID of no project, is refused as an unknown project
    (404) before the job is written; unchecked, the insert's uuid cast would refuse the first,
    and its foreign key the second, with a 500. Only an admin may queue an import, and an admin
    sees every project: the answer tells the caller nothing they may not know.
    """
    project_uuid = require_uuid(project_id, _PROJECT_NOT_FOUND, status_code=404)
    found = await session.execute(
        text("SELECT 1 FROM projects WHERE id = CAST(:project_id AS uuid)"),
        {"project_id": project_uuid},
    )
    if found.first() is None:
        raise HTTPException(status_code=404, detail=_PROJECT_NOT_FOUND)
    return project_uuid


async def _queued_package_fingerprint(folder_path: str, *, manifest_text: str) -> PackageFingerprint | None:
    """The fingerprint of the local package folder ``folder_path`` as the job is queued, read with
    the kept manifest ``manifest_text``; None for a folder that cannot be read, whose validation
    reports why. Taken in a worker thread: it lists the whole folder."""
    fingerprint, _refusal = await asyncio.to_thread(take_package_fingerprint, folder_path, manifest_text=manifest_text)
    return fingerprint


async def queue_family_import_job(
    session: AsyncSession,
    *,
    folder_path: str,
    project_id: str | None,
    dry_run: bool,
    requested_family_id: str | None = None,
    conflict_mode: str = "cancel",
    requested_by: CurrentUser,
    manifest_text: str,
    delivery: dict[str, Any] | None = None,
    commit: bool = True,
) -> FamilyPackageImportJobOut:
    """Queue the import a user asked for. The job keeps its requester by the account's id,
    which the import runs as and the requester's job list reads, and the e-mail address it
    was asked under, which it shows: a Microsoft sign-in may change the address.

    ``manifest_text`` is the manifest the import validates and imports the package with, the one
    the approved delivery's package makes. The job keeps it, with its SHA-256
    (``manifest_text_sha256``), so a worker that takes the job up, also again after its worker
    stopped, reads it, and the job's record names it. One that holds a
    NUL or a lone surrogate is refused before the job is written (400,
    ``manifest_text_problem``): the job could not store it.

    The job keeps the fingerprint of a local package folder as it is queued, read with that
    manifest (``package_fingerprint``): the worker imports nothing when the folder's differs as
    the job starts. (A delivery's approval refuses a folder whose fingerprint differs from the
    one it was checked with before it queues the job: import_delivery_review.py.) A folder the
    queue cannot read is queued without one; the job's validation reports it.

    ``delivery`` is the delivery of a watched import folder the job imports (its id, run,
    marker's SHA-256 and package fingerprint; import_delivery_review.py), kept in the job's
    metadata for the family's package record. With ``commit`` False the caller commits, so that
    the job and what the caller writes with it are one transaction."""
    # The existing family the request names, read as every ID is. One that no family can be
    # stored under is refused before the job is written: the job could not even store a NUL.
    requested_family_id = (requested_family_id or "").strip() or None
    if requested_family_id is not None:
        problem = identifier_problem(requested_family_id)
        if problem is not None:
            raise HTTPException(
                status_code=400,
                detail=f"family_id '{visible(requested_family_id)}' {problem}. {IDENTIFIER_RULE}",
            )
    manifest_problem = manifest_text_problem(manifest_text)
    if manifest_problem is not None:
        raise HTTPException(status_code=400, detail=manifest_problem)
    project_uuid = await _require_import_project(session, project_id) if project_id else None
    fingerprint = await _queued_package_fingerprint(folder_path, manifest_text=manifest_text)
    metadata: dict[str, Any] = {
        "requested_family_id": requested_family_id,
        "conflict_mode": conflict_mode,
    }
    if delivery is not None:
        metadata["delivery"] = delivery
    result = await session.execute(
        text(
            """
            INSERT INTO family_import_jobs (
                submitted_path,
                project_id,
                status,
                dry_run,
                metadata,
                requested_by,
                requested_by_id,
                requested_at,
                manifest_text,
                manifest_sha256,
                package_fingerprint
            )
            VALUES (
                :submitted_path,
                CAST(:project_id AS uuid),
                'queued',
                :dry_run,
                CAST(:metadata AS jsonb),
                :requested_by,
                CAST(:requested_by_id AS uuid),
                :requested_at,
                :manifest_text,
                :manifest_sha256,
                :package_fingerprint
            )
            RETURNING
                id::text AS id,
                submitted_path,
                family_id,
                project_id::text AS project_id,
                status,
                dry_run,
                worker_id,
                requested_by,
                requested_at,
                started_at,
                heartbeat_at,
                completed_at,
                validation_errors,
                validation_warnings,
                logs,
                dataset_summaries,
                metadata,
                error,
                manifest_sha256,
                package_fingerprint
            """
        ),
        {
            "submitted_path": package_folder_path(folder_path),
            "project_id": project_uuid,
            "dry_run": dry_run,
            "metadata": json.dumps(metadata),
            "requested_by": requested_by.email,
            "requested_by_id": requested_by.id,
            "requested_at": datetime.now(timezone.utc),
            "manifest_text": manifest_text,
            "manifest_sha256": manifest_text_sha256(manifest_text),
            "package_fingerprint": fingerprint.sha256 if fingerprint is not None else None,
        },
    )
    job = _serialize_job(dict(result.mappings().one()))
    if commit:
        await session.commit()
    return job


_JOB_NOT_FOUND = "Family import job not found"


def _require_import_job_reader(user: CurrentUser) -> None:
    """Refuse a user below a superuser, as every import route does
    (``dependencies.get_current_superuser``): a superuser and an admin read every job, and no
    one else reads any. The routes refuse such a user before this is reached; a caller other
    than them is refused the same way, never shown the jobs."""
    if not has_superuser_rights(user):
        raise HTTPException(status_code=403, detail="Superuser access required")


async def get_family_import_job(
    session: AsyncSession,
    *,
    job_id: str,
    user: CurrentUser,
) -> FamilyPackageImportJobOut:
    _require_import_job_reader(user)
    job_uuid = require_uuid(job_id, _JOB_NOT_FOUND, status_code=404)
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                submitted_path,
                family_id,
                project_id::text AS project_id,
                status,
                dry_run,
                worker_id,
                requested_by,
                requested_at,
                started_at,
                heartbeat_at,
                completed_at,
                validation_errors,
                validation_warnings,
                logs,
                dataset_summaries,
                metadata,
                error,
                manifest_sha256,
                package_fingerprint
            FROM family_import_jobs
            WHERE id = CAST(:job_id AS uuid)
            """
        ),
        {"job_id": job_uuid},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=_JOB_NOT_FOUND)
    return _serialize_job(dict(row))


async def list_family_import_jobs(
    session: AsyncSession,
    *,
    user: CurrentUser,
    limit: int = 25,
) -> list[FamilyPackageImportJobOut]:
    _require_import_job_reader(user)
    result = await session.execute(
        text(
            """
            SELECT
                id::text AS id,
                submitted_path,
                family_id,
                project_id::text AS project_id,
                status,
                dry_run,
                worker_id,
                requested_by,
                requested_at,
                started_at,
                heartbeat_at,
                completed_at,
                validation_errors,
                validation_warnings,
                logs,
                dataset_summaries,
                metadata,
                error,
                manifest_sha256,
                package_fingerprint
            FROM family_import_jobs
            ORDER BY requested_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [_serialize_job(dict(row)) for row in result.mappings().all()]


async def claim_next_family_import_job(
    session: AsyncSession,
    *,
    worker_id: str,
) -> dict[str, Any] | None:
    """Claim the oldest job to run: a queued one, or one whose worker stopped (its
    heartbeat is older than FAMILY_IMPORT_STALE_HEARTBEAT). ``claimed_from`` is the
    status it was claimed from.

    A stopped job is run again only if its import had written nothing: it was still
    ``validating``. One that was ``running`` had begun writing its family, and a run from
    the start would not undo that -- an update would skip the datasets it left partly
    written, a cancel would refuse the family it created. So it is ended here instead,
    ``failed`` with FAMILY_IMPORT_INTERRUPTED_ERROR, and returned as such: there is
    nothing to run. Its family keeps the import's ``import_unfinished`` entry. Either way
    the job keeps its record (logs, dataset summaries) and a line is added to its log.

    The heartbeat's age is judged by the database's clock, which stamps it: a host's clock
    that runs ahead would take a live import for stopped. The times this stamps, those in
    the added log line included, are the database's too.
    """
    result = await session.execute(
        text(
            f"""
            WITH candidate AS (
                SELECT id, status AS claimed_from
                FROM family_import_jobs
                WHERE status = 'queued'
                   OR (status IN ('validating', 'running')
                       AND heartbeat_at < now() - CAST(:stale_after AS interval))
                   OR (status IN ('validating', 'running') AND heartbeat_at IS NULL)
                ORDER BY requested_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE family_import_jobs AS job
            SET
                status = CASE
                    WHEN candidate.claimed_from = 'running' THEN 'failed'
                    ELSE 'validating'
                END,
                worker_id = CASE
                    WHEN candidate.claimed_from = 'running' THEN NULL
                    ELSE CAST(:worker_id AS text)
                END,
                started_at = COALESCE(job.started_at, now()),
                heartbeat_at = now(),
                completed_at = CASE
                    WHEN candidate.claimed_from = 'running' THEN now()
                    ELSE NULL
                END,
                error = CASE
                    WHEN candidate.claimed_from = 'running' THEN CAST(:interrupted_error AS text)
                    ELSE NULL
                END,
                logs = CASE candidate.claimed_from
                    WHEN 'running'
                        THEN COALESCE(job.logs, '[]'::jsonb) || jsonb_build_array(
                            format(CAST(:interrupted_log AS text), {_DATABASE_TIME})
                        )
                    WHEN 'validating'
                        THEN COALESCE(job.logs, '[]'::jsonb) || jsonb_build_array(
                            format(CAST(:reclaimed_log AS text), {_DATABASE_TIME})
                        )
                    ELSE job.logs
                END
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING
                job.id::text AS id,
                job.submitted_path,
                job.family_id,
                job.project_id::text AS project_id,
                job.status,
                job.dry_run,
                job.worker_id,
                job.requested_by,
                job.requested_at,
                job.started_at,
                job.heartbeat_at,
                job.completed_at,
                job.validation_errors,
                job.validation_warnings,
                job.logs,
                job.dataset_summaries,
                job.metadata,
                job.error,
                job.manifest_sha256,
                job.package_fingerprint,
                candidate.claimed_from
            """
        ),
        {
            "worker_id": worker_id,
            "stale_after": FAMILY_IMPORT_STALE_HEARTBEAT,
            "interrupted_error": FAMILY_IMPORT_INTERRUPTED_ERROR,
            # Each takes the time of the claim for its %s.
            "interrupted_log": (
                "The import stopped part-way: its worker's heartbeat went stale while it "
                "was running on the family. Ended %s instead of run again."
            ),
            "reclaimed_log": (
                "The worker running this job stopped before the import wrote anything "
                "of the family. Claimed again %s; run again from the start."
            ),
        },
    )
    row = result.mappings().first()
    if row is None:
        await session.rollback()
        return None
    await session.commit()
    return dict(row)


async def _record_job_family(
    session: AsyncSession,
    *,
    job_id: str,
    worker_id: str,
    family_id: str,
) -> bool:
    """Record, committed, that the job now writes ``family_id``: ``status = running``.

    The report sign-out refuses a family while a package-import job of it is queued,
    validating or running, and finds the job by the family it records
    (``report_signout_service._ACTIVE_IMPORT_JOB``). So an import records its family here
    before it writes anything of it, and writes nothing if this fails. False when no row
    changed: the job is no longer this worker's to run (another worker claimed it once its
    heartbeat went stale) or has ended.
    """
    if not family_id:
        raise ValueError("An import job records the family it writes, and none was given")
    result = await session.execute(
        text(
            """
            UPDATE family_import_jobs
            SET status = 'running',
                family_id = :family_id,
                heartbeat_at = now()
            WHERE id = CAST(:job_id AS uuid)
              AND worker_id = :worker_id
              AND status IN ('validating', 'running')
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "family_id": family_id,
        },
    )
    await session.commit()
    return rows_matched(result) == 1


async def running_family_import_job_ids(
    session: AsyncSession, job_ids: Iterable[str]
) -> set[str]:
    """Those of these import jobs that are queued, validating or running: the ones whose
    import may still run (one whose process stopped stays running until a worker ends it)."""
    ids = sorted({str(job_id) for job_id in job_ids})
    if not ids:
        return set()
    rows = await session.execute(
        text(
            """
            SELECT id::text
            FROM family_import_jobs
            WHERE id::text = ANY(CAST(:ids AS text[]))
              AND status IN ('queued', 'validating', 'running')
            """
        ),
        {"ids": ids},
    )
    return {str(job_id) for job_id in rows.scalars().all()}


async def _beat_family_import_job(
    session: AsyncSession,
    *,
    job_id: str,
    worker_id: str,
) -> bool:
    """Write the job's heartbeat, by the database's clock, while it is this worker's and has
    not ended. Committed. False when no row changed: another worker has claimed the job, or
    it has ended."""
    result = await session.execute(
        text(
            """
            UPDATE family_import_jobs
            SET heartbeat_at = now()
            WHERE id = CAST(:job_id AS uuid)
              AND worker_id = :worker_id
              AND status IN ('validating', 'running')
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
        },
    )
    await session.commit()
    return rows_matched(result) == 1


async def _update_job_progress(
    session: AsyncSession,
    *,
    job_id: str,
    worker_id: str | None,
    status: str | None = None,
    family_id: str | None = None,
    validation: FamilyPackageValidationOut | None = None,
    datasets: list[FamilyImportDatasetSummary] | None = None,
    logs: list[str] | None = None,
    error: str | None = None,
    completed: bool = False,
) -> bool:
    """Record the job's progress or its end, with its heartbeat (the database's clock), and
    commit. With ``worker_id``, only while the job is that worker's. False when no row
    changed: another worker ended the job as interrupted or claimed it again, so the
    update is lost; the caller says so in the log."""
    params: dict[str, Any] = {"job_id": job_id}
    clauses = ["heartbeat_at = now()"]
    if worker_id is not None:
        params["worker_id"] = worker_id
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    if family_id is not None:
        clauses.append("family_id = :family_id")
        params["family_id"] = family_id
    if validation is not None:
        clauses.append("validation_errors = CAST(:validation_errors AS jsonb)")
        clauses.append("validation_warnings = CAST(:validation_warnings AS jsonb)")
        clauses.append("metadata = CAST(:metadata AS jsonb)")
        params["validation_errors"] = _model_list_json(validation.errors)
        params["validation_warnings"] = _model_list_json(validation.warnings)
        params["metadata"] = json.dumps(validation.metadata)
    if datasets is not None:
        clauses.append("dataset_summaries = CAST(:dataset_summaries AS jsonb)")
        params["dataset_summaries"] = _model_list_json(datasets)
    if logs is not None:
        clauses.append("logs = CAST(:logs AS jsonb)")
        params["logs"] = json.dumps(logs)
    if error is not None:
        clauses.append("error = :error")
        params["error"] = error
    if completed:
        clauses.append("completed_at = now()")
        clauses.append("worker_id = NULL")

    worker_clause = " AND worker_id = :worker_id" if worker_id is not None else ""
    result = await session.execute(
        text(
            f"""
            UPDATE family_import_jobs
            SET {', '.join(clauses)}
            WHERE id = CAST(:job_id AS uuid)
            {worker_clause}
            """
        ),
        params,
    )
    await session.commit()
    return rows_matched(result) == 1
