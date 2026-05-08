from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_sessionmaker
from ..schemas import AuditLogEventOut, AuditLogPageOut


@dataclass(slots=True)
class AuditLogEventPayload:
    method: str
    path: str
    status_code: int
    duration_ms: int
    route_path: str | None = None
    query_string: str | None = None
    remote_ip: str | None = None
    user_agent: str | None = None
    referer: str | None = None
    protocol: str | None = None
    request_body: dict[str, Any] | list[Any] | str | None = None
    request_meta: dict[str, Any] | None = None
    db_update: dict[str, Any] | None = None
    error: str | None = None
    user_id: str | None = None
    user_email: str | None = None
    user_role: str | None = None


def log_model_update(
    entity: str,
    entity_id: str | None,
    update_type: str,
    update_fields: list[str] | None = None,
) -> dict[str, Any]:
    update: dict[str, Any] = {
        "dbEntity": entity,
        "updateType": update_type,
    }
    if entity_id:
        update["entityId"] = str(entity_id)
    if update_fields:
        update["updateFields"] = sorted(update_fields)
    return update


async def write_audit_log_event(payload: AuditLogEventPayload) -> None:
    session_factory = get_postgres_sessionmaker()
    async with session_factory() as session:
        try:
            await _insert_audit_log_event(session, payload)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _insert_audit_log_event(session: AsyncSession, payload: AuditLogEventPayload) -> None:
    def _to_jsonb_text(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=True)

    await session.execute(
        text(
            """
            INSERT INTO audit_log_events (
                user_id,
                user_email,
                user_role,
                method,
                route_path,
                path,
                query_string,
                status_code,
                duration_ms,
                remote_ip,
                user_agent,
                referer,
                protocol,
                request_body,
                request_meta,
                db_update,
                error
            )
            VALUES (
                CAST(:user_id AS uuid),
                :user_email,
                :user_role,
                :method,
                :route_path,
                :path,
                :query_string,
                :status_code,
                :duration_ms,
                :remote_ip,
                :user_agent,
                :referer,
                :protocol,
                CAST(:request_body AS jsonb),
                CAST(:request_meta AS jsonb),
                CAST(:db_update AS jsonb),
                :error
            )
            """
        ),
        {
            "user_id": payload.user_id,
            "user_email": payload.user_email,
            "user_role": payload.user_role,
            "method": payload.method,
            "route_path": payload.route_path,
            "path": payload.path,
            "query_string": payload.query_string,
            "status_code": payload.status_code,
            "duration_ms": payload.duration_ms,
            "remote_ip": payload.remote_ip,
            "user_agent": payload.user_agent,
            "referer": payload.referer,
            "protocol": payload.protocol,
            "request_body": _to_jsonb_text(payload.request_body),
            "request_meta": _to_jsonb_text(payload.request_meta or {}),
            "db_update": _to_jsonb_text(payload.db_update),
            "error": payload.error,
        },
    )


def _audit_log_out_from_mapping(row: dict[str, Any]) -> AuditLogEventOut:
    return AuditLogEventOut(
        id=str(row["id"]),
        created_at=row["created_at"],
        user_id=str(row["user_id"]) if row.get("user_id") is not None else None,
        user_email=row.get("user_email"),
        user_role=row.get("user_role"),
        method=row["method"],
        route_path=row.get("route_path"),
        path=row["path"],
        query_string=row.get("query_string"),
        status_code=int(row["status_code"]),
        duration_ms=int(row["duration_ms"]),
        remote_ip=row.get("remote_ip"),
        user_agent=row.get("user_agent"),
        referer=row.get("referer"),
        protocol=row.get("protocol"),
        request_body=row.get("request_body"),
        request_meta=row.get("request_meta") or {},
        db_update=row.get("db_update"),
        error=row.get("error"),
    )


async def list_audit_log_events(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    method: str | None = None,
    status_code: int | None = None,
    user_email: str | None = None,
    path_contains: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
) -> AuditLogPageOut:
    where_clauses: list[str] = []
    params: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }

    if method:
        where_clauses.append("method = :method")
        params["method"] = method.upper()
    if status_code is not None:
        where_clauses.append("status_code = :status_code")
        params["status_code"] = status_code
    if user_email:
        where_clauses.append("user_email ILIKE :user_email")
        params["user_email"] = f"%{user_email.strip()}%"
    if path_contains:
        where_clauses.append("(path ILIKE :path_contains OR route_path ILIKE :path_contains)")
        params["path_contains"] = f"%{path_contains.strip()}%"
    if started_after is not None:
        where_clauses.append("created_at >= :started_after")
        params["started_after"] = started_after
    if started_before is not None:
        where_clauses.append("created_at <= :started_before")
        params["started_before"] = started_before

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    count_result = await session.execute(
        text(f"SELECT COUNT(*) AS total FROM audit_log_events {where_sql}"),
        params,
    )
    total = int(count_result.scalar_one())

    rows = await session.execute(
        text(
            f"""
            SELECT
                id,
                created_at,
                user_id,
                user_email,
                user_role,
                method,
                route_path,
                path,
                query_string,
                status_code,
                duration_ms,
                remote_ip,
                user_agent,
                referer,
                protocol,
                request_body,
                request_meta,
                db_update,
                error
            FROM audit_log_events
            {where_sql}
            ORDER BY created_at DESC
            LIMIT :limit
            OFFSET :offset
            """
        ),
        params,
    )

    items = [_audit_log_out_from_mapping(dict(row)) for row in rows.mappings().all()]
    return AuditLogPageOut(
        page=page,
        page_size=page_size,
        total=total,
        items=items,
    )
