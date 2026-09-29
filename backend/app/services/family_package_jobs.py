from __future__ import annotations

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

from .family_package_source import package_folder_path
from .family_package_common import _dataset_summary_list, _issue_list, _json_dict, _json_list, _model_list_json  # noqa: F401


logger = logging.getLogger(__name__)


FAMILY_IMPORT_STALE_HEARTBEAT = timedelta(minutes=10)


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
    now = datetime.now(timezone.utc)
    stale_before = now - FAMILY_IMPORT_STALE_HEARTBEAT
    result = await session.execute(
        text(
            """
            WITH candidate AS (
                SELECT id
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
                status = 'validating',
                worker_id = :worker_id,
                started_at = COALESCE(job.started_at, :now),
                heartbeat_at = :now,
                completed_at = NULL,
                error = NULL
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
                job.error
            """
        ),
        {
            "worker_id": worker_id,
            "now": now,
            "stale_before": stale_before,
        },
    )
    row = result.mappings().first()
    if row is None:
        await session.rollback()
        return None
    await session.commit()
    return dict(row)


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
