from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.pg_storable import ESCAPED_KEY, storable_columns
from ..core.postgres import get_postgres_sessionmaker
from ..schemas import UiEventOut, UiEventPageOut
from .event_pipeline import enqueue_event, write_event_batch_with_retry, write_event_now

_UI_EVENT_PIPELINE_NAME = "ui_event"

_INSERT_UI_EVENT_SQL = text(
    """
    INSERT INTO ui_events (
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


def _to_jsonb_text(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=True)


def _ui_event_insert_params(payload: UiEventPayload) -> dict[str, Any]:
    # As in the request audit (audit_log_pg.py): a value Postgres refuses is escaped rather
    # than failing the INSERT, and detail names the columns where that happened. That key is
    # the writer's: the client's detail cannot set it.
    detail = {key: value for key, value in (payload.detail or {}).items() if key != ESCAPED_KEY}
    params, escaped = storable_columns(
        {
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
    params["detail"] = _to_jsonb_text(params["detail"])
    return params


async def _write_ui_event_batch(payloads: list[UiEventPayload]) -> None:
    if not payloads:
        return
    session_factory = get_postgres_sessionmaker()
    async with session_factory() as session:
        try:
            await session.execute(
                _INSERT_UI_EVENT_SQL,
                [_ui_event_insert_params(payload) for payload in payloads],
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _drain_ui_event_queue(limit: int) -> list[UiEventPayload]:
    if _ui_event_queue is None:
        return []
    items: list[UiEventPayload] = []
    while len(items) < limit:
        try:
            items.append(_ui_event_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


async def _ui_event_worker() -> None:
    assert _ui_event_queue is not None
    assert _ui_event_shutdown_event is not None

    while True:
        if _ui_event_shutdown_event.is_set():
            batch = _drain_ui_event_queue(settings.audit_log_batch_size)
            if not batch:
                return
            await write_event_batch_with_retry(
                _write_ui_event_batch, batch, name=_UI_EVENT_PIPELINE_NAME
            )
            continue

        try:
            first_item = await asyncio.wait_for(
                _ui_event_queue.get(),
                timeout=settings.audit_log_flush_interval_seconds,
            )
        except asyncio.TimeoutError:
            continue

        batch = [first_item, *_drain_ui_event_queue(settings.audit_log_batch_size - 1)]
        await write_event_batch_with_retry(
            _write_ui_event_batch, batch, name=_UI_EVENT_PIPELINE_NAME
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

    if _ui_event_worker_task is None or _ui_event_shutdown_event is None:
        _ui_event_queue = None
        _ui_event_worker_task = None
        _ui_event_shutdown_event = None
        return

    _ui_event_shutdown_event.set()
    try:
        await _ui_event_worker_task
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
    if settings.audit_log_mode == "off":
        return
    if settings.audit_log_mode != "async" or _ui_event_queue is None:
        # As for the request audit (audit_log_pg.write_audit_log_event): sync mode, or no
        # worker running.
        await write_event_now(
            payload, name=_UI_EVENT_PIPELINE_NAME, write_batch=_write_ui_event_batch
        )
        return
    await enqueue_event(
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
    started_after: datetime | None = None,
    started_before: datetime | None = None,
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
    if started_after is not None:
        where_clauses.append("created_at >= :started_after")
        params["started_after"] = started_after
    if started_before is not None:
        where_clauses.append("created_at <= :started_before")
        params["started_before"] = started_before

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
