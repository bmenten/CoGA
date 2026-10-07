from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    FamilyImportDatasetSummary,
    FamilyPackageImportJobOut,
    FamilyPackageValidationOut,
)
from .access_control import CurrentUser, is_admin_user

from .family_identifiers import IDENTIFIER_RULE, identifier_problem, visible
from .family_package_source import package_folder_path
from .family_package_common import _dataset_summary_list, _issue_list, _json_dict, _json_list, _model_list_json


logger = logging.getLogger(__name__)


FAMILY_IMPORT_STALE_HEARTBEAT = timedelta(minutes=10)

# What a job ends with when a worker finds that its import stopped part-way: its heartbeat
# went stale while it was `running` on its family, the state in which an import writes the
# family, so the process running it ended once the import may have begun writing.
FAMILY_IMPORT_INTERRUPTED_ERROR = (
    "Interrupted: the process running this import stopped (a restart, a crash or running "
    "out of memory) while the import was running on the family, so it is not run again: a "
    "run from the start would not undo what it had written. If it wrote any of the family, "
    "the family stays marked import-incomplete, naming the datasets the import had not "
    "finished: import them again with overwrite to complete it."
)


def _serialize_job(mapping: dict[str, Any]) -> FamilyPackageImportJobOut:
    return FamilyPackageImportJobOut(
        id=str(mapping["id"]),
        submitted_path=str(mapping["submitted_path"]),
        family_id=mapping.get("family_id"),
        project_id=str(mapping["project_id"]) if mapping.get("project_id") else None,
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
        metadata=_json_dict(mapping.get("metadata")),
        error=mapping.get("error"),
    )


async def queue_family_import_job(
    session: AsyncSession,
    *,
    folder_path: str,
    project_id: str | None,
    dry_run: bool,
    requested_family_id: str | None = None,
    conflict_mode: str = "cancel",
    requested_by: str,
) -> FamilyPackageImportJobOut:
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
    metadata = {
        "requested_family_id": requested_family_id,
        "conflict_mode": conflict_mode,
    }
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
                requested_at
            )
            VALUES (
                :submitted_path,
                CAST(NULLIF(:project_id, '') AS uuid),
                'queued',
                :dry_run,
                CAST(:metadata AS jsonb),
                :requested_by,
                :requested_at
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
                error
            """
        ),
        {
            "submitted_path": package_folder_path(folder_path),
            "project_id": project_id or "",
            "dry_run": dry_run,
            "metadata": json.dumps(metadata),
            "requested_by": requested_by,
            "requested_at": datetime.now(timezone.utc),
        },
    )
    await session.commit()
    return _serialize_job(dict(result.mappings().one()))


async def get_family_import_job(
    session: AsyncSession,
    *,
    job_id: str,
    user: CurrentUser,
) -> FamilyPackageImportJobOut:
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
                error
            FROM family_import_jobs
            WHERE id = CAST(:job_id AS uuid)
            """
        ),
        {"job_id": job_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Family import job not found")
    if not is_admin_user(user) and str(row["requested_by"]) != user.email:
        raise HTTPException(status_code=403, detail="Not authorized for this import job")
    return _serialize_job(dict(row))


async def list_family_import_jobs(
    session: AsyncSession,
    *,
    user: CurrentUser,
    limit: int = 25,
) -> list[FamilyPackageImportJobOut]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if not is_admin_user(user):
        clauses.append("requested_by = :requested_by")
        params["requested_by"] = user.email
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    result = await session.execute(
        text(
            f"""
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
                error
            FROM family_import_jobs
            {where}
            ORDER BY requested_at DESC
            LIMIT :limit
            """
        ),
        params,
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
    """
    now = datetime.now(timezone.utc)
    stale_before = now - FAMILY_IMPORT_STALE_HEARTBEAT
    when = now.strftime("%Y-%m-%d %H:%M UTC")
    result = await session.execute(
        text(
            """
            WITH candidate AS (
                SELECT id, status AS claimed_from
                FROM family_import_jobs
                WHERE status = 'queued'
                   OR (status IN ('validating', 'running') AND heartbeat_at < :stale_before)
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
                started_at = COALESCE(job.started_at, CAST(:now AS timestamptz)),
                heartbeat_at = CAST(:now AS timestamptz),
                completed_at = CASE
                    WHEN candidate.claimed_from = 'running' THEN CAST(:now AS timestamptz)
                    ELSE NULL
                END,
                error = CASE
                    WHEN candidate.claimed_from = 'running' THEN CAST(:interrupted_error AS text)
                    ELSE NULL
                END,
                logs = CASE candidate.claimed_from
                    WHEN 'running'
                        THEN COALESCE(job.logs, '[]'::jsonb) || CAST(:interrupted_log AS jsonb)
                    WHEN 'validating'
                        THEN COALESCE(job.logs, '[]'::jsonb) || CAST(:reclaimed_log AS jsonb)
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
                candidate.claimed_from
            """
        ),
        {
            "worker_id": worker_id,
            "now": now,
            "stale_before": stale_before,
            "interrupted_error": FAMILY_IMPORT_INTERRUPTED_ERROR,
            "interrupted_log": json.dumps(
                [
                    f"The import stopped part-way: its worker's heartbeat went stale while it "
                    f"was running on the family. Ended {when} instead of run again."
                ]
            ),
            "reclaimed_log": json.dumps(
                [
                    f"The worker running this job stopped before the import wrote anything "
                    f"of the family. Claimed again {when}; run again from the start."
                ]
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
                heartbeat_at = :heartbeat_at
            WHERE id = CAST(:job_id AS uuid)
              AND worker_id = :worker_id
              AND status IN ('validating', 'running')
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "family_id": family_id,
            "heartbeat_at": datetime.now(timezone.utc),
        },
    )
    await session.commit()
    return result.rowcount == 1


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
    """Write the job's heartbeat, while it is this worker's and has not ended. Committed.
    False when no row changed: another worker has claimed the job, or it has ended."""
    result = await session.execute(
        text(
            """
            UPDATE family_import_jobs
            SET heartbeat_at = :heartbeat_at
            WHERE id = CAST(:job_id AS uuid)
              AND worker_id = :worker_id
              AND status IN ('validating', 'running')
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "heartbeat_at": datetime.now(timezone.utc),
        },
    )
    await session.commit()
    return result.rowcount == 1


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
) -> None:
    params: dict[str, Any] = {
        "job_id": job_id,
        "heartbeat_at": datetime.now(timezone.utc),
    }
    clauses = ["heartbeat_at = :heartbeat_at"]
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
        clauses.append("completed_at = :completed_at")
        clauses.append("worker_id = NULL")
        params["completed_at"] = datetime.now(timezone.utc)

    worker_clause = " AND worker_id = :worker_id" if worker_id is not None else ""
    await session.execute(
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
