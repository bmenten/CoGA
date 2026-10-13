"""A batch of audit events keeps every row but the one Postgres refuses (integration, real Postgres).

The request audit (``audit_log_events``) and the UI-event log (``ui_events``) each write what
their worker drains in one INSERT. A row refused for something other than its characters fails
that INSERT on every attempt: a request's row naming a user deleted while the row waited in the
queue (the foreign key on ``user_id``), or a UI event whose client timestamp the driver cannot
encode (year 1 at +05:00 is year 0 in UTC). Every other row of the batch, any user's, used to be
lost with it. After the batch's last attempt the worker now writes the rows one at a time, so the
refused row costs only itself.

Each test runs the real worker against real Postgres: three events are queued, the worker drains
them as one batch when it stops, and the rows are read back. The refused event is counted once
and logged once with its payload; no log line quotes the database's error, which repeats the
rows. The values are synthetic. The UI events are removed at the end; ``audit_log_events`` is
append-only, so its rows stay, under a path of the run's own.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

# What no log line may carry: the statement, its parameters, and what Postgres and the driver
# say about the row they refused, which quotes its values.
_ERROR_TEXT = (
    "INSERT INTO",
    "[parameters:",
    "violates foreign key constraint",
    "is not present in table",
    "invalid input for query argument",
    "date value out of range",
)


@pytest.fixture
def pipeline(monkeypatch, caplog):
    from backend.app.core.config import settings
    from backend.app.services import event_pipeline

    # The production mode and batch size, with retries that do not wait.
    monkeypatch.setattr(settings, "audit_log_mode", "async")
    monkeypatch.setattr(settings, "audit_log_batch_size", 50)
    monkeypatch.setattr(settings, "audit_log_max_write_attempts", 2)
    monkeypatch.setattr(settings, "audit_log_flush_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "audit_log_backpressure_timeout_seconds", 0.05)
    monkeypatch.setattr(event_pipeline, "_dropped_counts", {})
    caplog.set_level(logging.WARNING)
    return event_pipeline


def _run(make_coro):
    from backend.app.core.postgres import close_postgres_engine, init_postgres_schema

    async def _wrapped():
        try:
            await init_postgres_schema()
            return await make_coro()
        finally:
            await close_postgres_engine()

    return asyncio.run(_wrapped())


async def _through_the_worker(start, stop, write_event, events) -> None:
    await start()
    try:
        for event in events:
            await write_event(event)
    finally:
        await stop()  # drains the queue: the three events form one batch


def _lines(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def _quoting_the_error(lines: list[str]) -> list[str]:
    return [line for line in lines if any(marker in line for marker in _ERROR_TEXT)]


def test_a_request_row_naming_a_deleted_user_costs_only_itself(pipeline, caplog) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import audit_log_pg
    from backend.app.services.audit_log_pg import AUDIT_PIPELINE_NAME, AuditLogEventPayload

    run = uuid4().hex[:12]
    prefix = f"/api/audit-batch-{run}/"
    paths = [f"{prefix}{name}" for name in ("first", "refused", "third")]
    deleted_user = str(uuid4())  # names no user: deleted while its row waited in the queue
    events = [
        AuditLogEventPayload(
            method="GET",
            path=path,
            status_code=200,
            duration_ms=1,
            user_id=deleted_user if path.endswith("refused") else None,
        )
        for path in paths
    ]

    async def _exercise() -> list[str]:
        await _through_the_worker(
            audit_log_pg.start_audit_log_worker,
            audit_log_pg.stop_audit_log_worker,
            audit_log_pg.write_audit_log_event,
            events,
        )
        async with get_postgres_sessionmaker()() as session:
            result = await session.execute(
                text(
                    "SELECT path FROM audit_log_events WHERE path LIKE :prefix "
                    "ORDER BY created_at, path"
                ),
                {"prefix": f"{prefix}%"},
            )
            return [row.path for row in result]

    stored = _run(_exercise)

    # The other rows are stored, in the order they were queued; the refused one is counted.
    assert stored == [paths[0], paths[2]]
    assert pipeline.dropped_event_count(AUDIT_PIPELINE_NAME) == 1
    lines = _lines(caplog)
    lost = [line for line in lines if "event not persisted" in line]
    assert len(lost) == 1, lines
    assert lost[0].startswith(
        "Audit pipeline audit_log: event not persisted (batch write failed, then its own write: "
        "IntegrityError (ForeignKeyViolationError, SQLSTATE 23503)); dropped_total=1 "
        f"payload=AuditLogEventPayload(method='GET', path='{paths[1]}',"
    )
    assert (
        "Audit pipeline audit_log: of the batch's 3 events, 2 stored one at a time, 1 not persisted"
        in lines
    )
    # A row stored is not logged, the user's id is in its own row's payload only, and no line
    # quotes the database's error.
    assert not [line for line in lines if paths[0] in line or paths[2] in line]
    assert [line for line in lines if deleted_user in line] == lost
    assert _quoting_the_error(lines) == []


def test_a_ui_event_whose_time_the_driver_cannot_encode_costs_only_itself(pipeline, caplog) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import ui_event_pg
    from backend.app.services.ui_event_pg import UiEventPayload

    run = uuid4().hex[:12]
    prefix = f"audit-batch-{run} "
    labels = [f"{prefix}{name}" for name in ("first", "refused", "third")]
    now = datetime.now(timezone.utc)
    unencodable = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5)))  # year 0 in UTC
    events = [
        UiEventPayload(
            event_type="click",
            category="button",
            label=label,
            occurred_at=unencodable if label.endswith("refused") else now,
        )
        for label in labels
    ]

    async def _exercise() -> list[str]:
        sessionmaker = get_postgres_sessionmaker()
        try:
            await _through_the_worker(
                ui_event_pg.start_ui_event_worker,
                ui_event_pg.stop_ui_event_worker,
                ui_event_pg.write_ui_event,
                events,
            )
            async with sessionmaker() as session:
                result = await session.execute(
                    text(
                        "SELECT label FROM ui_events WHERE label LIKE :prefix "
                        "ORDER BY created_at, label"
                    ),
                    {"prefix": f"{prefix}%"},
                )
                return [row.label for row in result]
        finally:
            async with sessionmaker() as session:
                await session.execute(
                    text("DELETE FROM ui_events WHERE label LIKE :prefix"), {"prefix": f"{prefix}%"}
                )
                await session.commit()

    stored = _run(_exercise)

    assert stored == [labels[0], labels[2]]
    assert pipeline.dropped_event_count("ui_event") == 1
    lines = _lines(caplog)
    lost = [line for line in lines if "event not persisted" in line]
    assert len(lost) == 1, lines
    assert lost[0].startswith(
        "Audit pipeline ui_event: event not persisted (batch write failed, then its own write: "
    ), lost[0]
    # The driver refuses to encode the time (asyncpg's DataError). How SQLAlchemy wraps that
    # refusal differs between environments (InterfaceError locally; DBAPIError with SQLSTATE
    # 22000 in CI's job), so only the driver's error is pinned, then the rest of the line.
    reason, _, rest = lost[0].partition("); dropped_total=1 ")
    assert "DataError" in reason, lost[0]
    assert rest.startswith(
        f"payload=UiEventPayload(event_type='click', category='button', label='{labels[1]}',"
    ), lost[0]
    assert (
        "Audit pipeline ui_event: of the batch's 3 events, 2 stored one at a time, 1 not persisted"
        in lines
    )
    assert not [line for line in lines if labels[0] in line or labels[2] in line]
    assert _quoting_the_error(lines) == []
