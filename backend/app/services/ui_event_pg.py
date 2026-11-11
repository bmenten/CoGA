from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.pg_storable import ESCAPED_KEY, storable_columns
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import UiEventOut, UiEventPageOut
from .event_pipeline import (
    DELETED_USER_KEY,
    insert_event_rows,
    new_event_id,
    run_event_worker,
    stop_event_worker,
    write_batch_unlinking_deleted_user,
    write_or_enqueue_event,
)

_UI_EVENT_PIPELINE_NAME = "ui_event"

# The foreign key by which a row names its user (04_traceability.sql).
_USER_FOREIGN_KEY = "ui_events_user_id_fkey"

# The keys of detail that only this writer sets; a client's own are dropped.
_WRITERS_DETAIL_KEYS = (ESCAPED_KEY, DELETED_USER_KEY)

# As in the request audit (audit_log_pg.py): an event written again after a commit whose
# outcome was unknown is stored once.
_INSERT_UI_EVENT_SQL = text(
    """
    INSERT INTO ui_events (
        event_id,
        occurred_at,
        user_id,
        user_email,
        user_role,
        event_type,
        category,
        label,
        target_id,
        path,
        to_path,
        href,
        component,
        session_id,
        detail,
        remote_ip,
        user_agent
    )
    VALUES (
        CAST(:event_id AS uuid),
        :occurred_at,
        CAST(:user_id AS uuid),
        :user_email,
        :user_role,
        :event_type,
        :category,
        :label,
        :target_id,
        :path,
        :to_path,
        :href,
        :component,
        :session_id,
        CAST(:detail AS jsonb),
        :remote_ip,
        :user_agent
    )
    ON CONFLICT (event_id) DO NOTHING
    """
)

_ui_event_queue: asyncio.Queue[UiEventPayload] | None = None
_ui_event_worker_task: asyncio.Task[None] | None = None
_ui_event_shutdown_event: asyncio.Event | None = None


@dataclass(slots=True)
class UiEventPayload:
    event_type: str
    category: str | None = None
    label: str | None = None
    target_id: str | None = None
    path: str | None = None
    to_path: str | None = None
    href: str | None = None
    component: str | None = None
    session_id: str | None = None
    occurred_at: datetime | None = None
    detail: dict[str, Any] | None = None
    remote_ip: str | None = None
    user_agent: str | None = None
    user_id: str | None = None
    user_email: str | None = None
    user_role: str | None = None
    # Given as the event is made, before it is queued: every write of the event carries it.
    event_id: str = field(default_factory=new_event_id)


def _to_jsonb_text(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=True)


def _ui_event_insert_params(
    payload: UiEventPayload, *, unlink_user: bool = False
) -> dict[str, Any]:
    # As in the request audit (audit_log_pg.py): a value Postgres refuses is escaped rather
    # than failing the INSERT, and detail names the columns where that happened. That key, and
    # the one keeping a deleted user's id, are the writer's: the client's detail cannot set them.
    detail = {
        key: value
        for key, value in (payload.detail or {}).items()
        if key not in _WRITERS_DETAIL_KEYS
    }
    params, escaped = storable_columns(
        {
            "event_id": payload.event_id,
            "occurred_at": payload.occurred_at,
            "user_id": payload.user_id,
            "user_email": payload.user_email,
            "user_role": payload.user_role,
            "event_type": payload.event_type,
            "category": payload.category,
            "label": payload.label,
            "target_id": payload.target_id,
            "path": payload.path,
            "to_path": payload.to_path,
            "href": payload.href,
            "component": payload.component,
            "session_id": payload.session_id,
            "detail": detail,
            "remote_ip": payload.remote_ip,
            "user_agent": payload.user_agent,
        }
    )
    if escaped:
        params["detail"] = {**params["detail"], ESCAPED_KEY: escaped}
    if unlink_user:
        # As in the request audit: stored without the link, the user's id kept in detail.
        params["detail"] = {**params["detail"], DELETED_USER_KEY: params["user_id"]}
        params["user_id"] = None
    params["detail"] = _to_jsonb_text(params["detail"])
    return params


async def _insert_ui_event_rows(
    payloads: list[UiEventPayload], *, unlink_user: bool = False
) -> None:
    await insert_event_rows(
        get_postgres_sessionmaker(),
        _INSERT_UI_EVENT_SQL,
        [_ui_event_insert_params(payload, unlink_user=unlink_user) for payload in payloads],
    )


async def _write_ui_event_batch(payloads: list[UiEventPayload]) -> None:
    # As in the request audit (audit_log_pg._write_audit_log_batch): one event whose user's
    # account was deleted while it waited is stored without the link.
    await write_batch_unlinking_deleted_user(
        _insert_ui_event_rows,
        payloads,
        name=_UI_EVENT_PIPELINE_NAME,
        foreign_key=_USER_FOREIGN_KEY,
    )


async def _ui_event_worker() -> None:
    assert _ui_event_queue is not None
    assert _ui_event_shutdown_event is not None
    await run_event_worker(
        _ui_event_queue,
        _ui_event_shutdown_event,
        _write_ui_event_batch,
        name=_UI_EVENT_PIPELINE_NAME,
    )


async def start_ui_event_worker() -> None:
    global _ui_event_queue, _ui_event_worker_task, _ui_event_shutdown_event

    if settings.audit_log_mode != "async":
        return
    if _ui_event_worker_task is not None:
        return

    _ui_event_queue = asyncio.Queue(maxsize=settings.audit_log_queue_size)
    _ui_event_shutdown_event = asyncio.Event()
    _ui_event_worker_task = asyncio.create_task(_ui_event_worker())


async def stop_ui_event_worker() -> None:
    global _ui_event_queue, _ui_event_worker_task, _ui_event_shutdown_event

    try:
        await stop_event_worker(_ui_event_worker_task, _ui_event_shutdown_event)
    finally:
        _ui_event_queue = None
        _ui_event_worker_task = None
        _ui_event_shutdown_event = None


def ui_event_queue_depth() -> int:
    """UI events waiting for the database (0 when the worker is not running)."""
    return _ui_event_queue.qsize() if _ui_event_queue is not None else 0


async def write_ui_event(payload: UiEventPayload) -> None:
    """Store a UI event. A write that fails is recorded (TF-13 S-5), not raised, as the
    worker records one it cannot store: it does not fail the batch the event came in."""
    await write_or_enqueue_event(
        _ui_event_queue,
        payload,
        name=_UI_EVENT_PIPELINE_NAME,
        write_batch=_write_ui_event_batch,
    )


def _ui_event_out_from_mapping(row: dict[str, Any]) -> UiEventOut:
    return UiEventOut(
        id=str(row["id"]),
        created_at=row["created_at"],
        occurred_at=row.get("occurred_at"),
        user_id=str(row["user_id"]) if row.get("user_id") is not None else None,
        user_email=row.get("user_email"),
        user_role=row.get("user_role"),
        event_type=row["event_type"],
        category=row.get("category"),
        label=row.get("label"),
        target_id=row.get("target_id"),
        path=row.get("path"),
        to_path=row.get("to_path"),
        href=row.get("href"),
        component=row.get("component"),
        session_id=row.get("session_id"),
        detail=row.get("detail") or {},
        remote_ip=row.get("remote_ip"),
        user_agent=row.get("user_agent"),
    )


async def list_ui_events(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    event_type: str | None = None,
    user_email: str | None = None,
    path_contains: str | None = None,
) -> UiEventPageOut:
    where_clauses: list[str] = []
    params: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }

    if event_type:
        where_clauses.append("event_type = :event_type")
        params["event_type"] = event_type.strip().lower()
    if user_email:
        where_clauses.append("user_email ILIKE :user_email")
        params["user_email"] = f"%{user_email.strip()}%"
    if path_contains:
        where_clauses.append(
            "(path ILIKE :path_contains OR to_path ILIKE :path_contains OR label ILIKE :path_contains)"
        )
        params["path_contains"] = f"%{path_contains.strip()}%"

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    count_result = await session.execute(
        text(f"SELECT COUNT(*) AS total FROM ui_events {where_sql}"),
        params,
    )
    total = int(count_result.scalar_one())

    rows = await session.execute(
        text(
            f"""
            SELECT
                id,
                created_at,
                occurred_at,
                user_id,
                user_email,
                user_role,
                event_type,
                category,
                label,
                target_id,
                path,
                to_path,
                href,
                component,
                session_id,
                detail,
                remote_ip,
                user_agent
            FROM ui_events
            {where_sql}
            ORDER BY created_at DESC
            LIMIT :limit
            OFFSET :offset
            """
        ),
        params,
    )

    items = [_ui_event_out_from_mapping(dict(row)) for row in rows.mappings().all()]
    return UiEventPageOut(
        page=page,
        page_size=page_size,
        total=total,
        items=items,
    )
