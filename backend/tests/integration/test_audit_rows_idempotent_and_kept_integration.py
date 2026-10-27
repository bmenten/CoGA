"""Each audit event is stored once, and a deleted user's row is kept, on real Postgres (SEC-17).

The request audit (``audit_log_events``) and the UI-event log (``ui_events``) are written by
their workers, as the runtime role ``coga_app``, which holds INSERT and SELECT on these
append-only tables and nothing more (05_grants.sql). Each test runs the real worker and writer
against real Postgres, every transaction of the writer under ``SET LOCAL ROLE coga_app``:

- A batch whose first commit is made but whose answer is lost (the connection drops as Postgres
  commits) is written again by the worker. Each event carries the id it was given before it was
  queued, and the INSERT does nothing for an id the table holds (``ON CONFLICT (event_id) DO
  NOTHING``): every event is stored once. Before, every row of the batch was stored twice.
- A row naming a user whose account was deleted while the row waited in the queue fails the
  foreign key on ``user_id`` on every attempt of its batch. Written alone, it is now stored
  without the link, the user's id kept in ``request_meta._deleted_user_id`` or
  ``detail._deleted_user_id``, its copied e-mail and role as they were; the batch's other rows
  keep their link. One line says so, naming the error by its kind; nothing is counted as lost.
  Before, the row was lost.

The values are synthetic. Both tables are append-only, so their rows stay, under a path or a
label of the run's own. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke``
job sets it.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_DELETED_USER_KEY = "_deleted_user_id"


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


def _as_the_app_role(monkeypatch, module) -> list[str]:
    """Run every transaction of ``module``'s writer as coga_app; returns the roles it ran as."""

    from backend.app.core.postgres import get_postgres_sessionmaker

    roles: list[str] = []

    def _sessionmaker():
        sessionmaker = get_postgres_sessionmaker()

        @asynccontextmanager
        async def _session():
            async with sessionmaker() as session:
                await session.execute(text("SET LOCAL ROLE coga_app"))
                roles.append(str((await session.execute(text("SELECT current_user"))).scalar_one()))
                yield session

        return _session

    monkeypatch.setattr(module, "get_postgres_sessionmaker", _sessionmaker)
    return roles


def _run(make_coro):
    from backend.app.core.postgres import close_postgres_engine, init_postgres_schema

    async def _wrapped():
        try:
            await init_postgres_schema()
            return await make_coro()
        finally:
            await close_postgres_engine()

    return asyncio.run(_wrapped())


def _audit_event(name: str, prefix: str, user: tuple[str, str] | None):
    from backend.app.services.audit_log_pg import AuditLogEventPayload

    return AuditLogEventPayload(
        method="GET",
        path=f"{prefix}{name}",
        status_code=200,
        duration_ms=1,
        user_id=user[0] if user else None,
        user_email=user[1] if user else None,
        user_role="viewer" if user else None,
        request_meta={"headers": {"accept": "*/*"}},
    )


def _ui_event(name: str, prefix: str, user: tuple[str, str] | None):
    from backend.app.services.ui_event_pg import UiEventPayload

    return UiEventPayload(
        event_type="click",
        category="button",
        label=f"{prefix}{name}",
        detail={"count": 1},
        user_id=user[0] if user else None,
        user_email=user[1] if user else None,
        user_role="viewer" if user else None,
    )


# name: (module, writer, start, stop, write one event, make an event, table, name column, JSON data)
def _pipelines():
    from backend.app.services import audit_log_pg, ui_event_pg

    return {
        "audit_log": (
            audit_log_pg,
            "_write_audit_log_batch",
            audit_log_pg.start_audit_log_worker,
            audit_log_pg.stop_audit_log_worker,
            audit_log_pg.write_audit_log_event,
            _audit_event,
            "audit_log_events",
            "path",
            "request_meta",
        ),
        "ui_event": (
            ui_event_pg,
            "_write_ui_event_batch",
            ui_event_pg.start_ui_event_worker,
            ui_event_pg.stop_ui_event_worker,
            ui_event_pg.write_ui_event,
            _ui_event,
            "ui_events",
            "label",
            "detail",
        ),
    }


async def _through_the_worker(start, stop, write_event, events) -> None:
    await start()
    try:
        for event in events:
            await write_event(event)
    finally:
        await stop()  # drains the queue: the events form one batch


async def _stored(table: str, name_column: str, data_column: str, prefix: str) -> list[dict]:
    """The rows of the run, read as the owner, in the order they were stored: each its name,
    event id, user link, copied actor and JSON data."""

    from backend.app.core.postgres import get_postgres_sessionmaker

    assert (table, name_column, data_column) in {
        ("audit_log_events", "path", "request_meta"),
        ("ui_events", "label", "detail"),
    }  # names from this file only, safe to write into the statement
    async with get_postgres_sessionmaker()() as session:
        result = await session.execute(
            text(
                f"SELECT to_jsonb(t) AS row FROM {table} t "
                f"WHERE {name_column} LIKE :prefix ORDER BY created_at, {name_column}"
            ),
            {"prefix": f"{prefix}%"},
        )
        rows = [row[0] for row in result.all()]
    return [
        {
            "name": row[name_column],
            "event_id": row.get("event_id"),
            "user_id": row["user_id"],
            "user_email": row["user_email"],
            "user_role": row["user_role"],
            "data": row[data_column],
        }
        for row in rows
    ]


def _lines(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


@pytest.mark.parametrize("name", ["audit_log", "ui_event"])
def test_a_batch_written_again_after_its_commits_answer_was_lost_is_stored_once(
    name, pipeline, monkeypatch, caplog
) -> None:
    module, writer_name, start, stop, write_event, make_event, table, name_column, data_column = _pipelines()[name]
    prefix = f"/api/sec17-once-{uuid4().hex[:12]}/" if name == "audit_log" else f"sec17-once-{uuid4().hex[:12]} "
    events = [make_event(event, prefix, None) for event in ("first", "second", "third")]
    roles = _as_the_app_role(monkeypatch, module)
    writer = getattr(module, writer_name)
    calls: list[int] = []

    async def _commit_then_lose_the_answer(batch) -> None:
        # The real writer commits; the answer of its first commit never arrives.
        calls.append(len(batch))
        await writer(batch)
        if len(calls) == 1:
            raise ConnectionResetError(54, "Connection reset by peer")

    monkeypatch.setattr(module, writer_name, _commit_then_lose_the_answer)

    async def _exercise() -> list[dict]:
        await _through_the_worker(start, stop, write_event, events)
        return await _stored(table, name_column, data_column, prefix)

    rows = _run(_exercise)

    # Written twice, stored once: each row is its event's, under the id the event was given.
    assert calls == [3, 3]
    assert [row["name"] for row in rows] == [getattr(event, name_column) for event in events]
    assert [row["event_id"] for row in rows] == [event.event_id for event in events]
    assert pipeline.dropped_event_count(name) == 0
    assert not [line for line in _lines(caplog) if "event not persisted" in line]
    # Every write ran as the runtime role, which holds INSERT and SELECT on the table only.
    assert roles and set(roles) == {"coga_app"}


@pytest.mark.parametrize("name", ["audit_log", "ui_event"])
def test_a_row_naming_a_user_deleted_while_it_waited_is_stored_without_the_link(
    name, pipeline, monkeypatch, caplog
) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker

    module, _writer, start, stop, write_event, make_event, table, name_column, data_column = _pipelines()[name]
    run = uuid4().hex[:12]
    prefix = f"/api/sec17-kept-{run}/" if name == "audit_log" else f"sec17-kept-{run} "
    roles = _as_the_app_role(monkeypatch, module)

    users: dict[str, tuple[str, str]] = {}
    events: list = []

    async def _exercise() -> list[dict]:
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:  # as the owner: two synthetic accounts
                for kind in ("kept", "deleted"):
                    email = f"sec17-{kind}-{run}@example.org"
                    created = await session.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:e, 'x', 'viewer', :e) RETURNING id::text"
                        ),
                        {"e": email},
                    )
                    users[kind] = (str(created.scalar_one()), email)
                await session.commit()
            events.extend(
                [
                    make_event("first", prefix, users["kept"]),
                    make_event("deleted", prefix, users["deleted"]),
                    make_event("third", prefix, users["kept"]),
                ]
            )
            # The account is deleted (operator SQL) after its event was made, before it is stored.
            async with sessionmaker() as session:
                await session.execute(
                    text("DELETE FROM users WHERE id = CAST(:u AS uuid)"), {"u": users["deleted"][0]}
                )
                await session.commit()
            await _through_the_worker(start, stop, write_event, events)
            return await _stored(table, name_column, data_column, prefix)
        finally:
            # The synthetic accounts go; their rows stay, the link nulled by the account's deletion.
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM users WHERE email LIKE :e"), {"e": f"sec17-%-{run}@example.org"})
                await session.commit()

    rows = _run(_exercise)

    # Every row is stored, in queue order, each under its event's id.
    assert [row["name"] for row in rows] == [getattr(event, name_column) for event in events]
    assert [row["event_id"] for row in rows] == [event.event_id for event in events]
    # The deleted user's row without the link, and its id in the row's own data; the others
    # keep theirs. The copied e-mail and role keep the actor.
    assert [row["user_id"] for row in rows] == [users["kept"][0], None, users["kept"][0]]
    assert rows[1]["data"][_DELETED_USER_KEY] == users["deleted"][0]
    assert (rows[1]["user_email"], rows[1]["user_role"]) == (users["deleted"][1], "viewer")
    assert _DELETED_USER_KEY not in rows[0]["data"] and _DELETED_USER_KEY not in rows[2]["data"]
    assert pipeline.dropped_event_count(name) == 0
    lines = _lines(caplog)
    assert not [line for line in lines if "event not persisted" in line]
    assert [line for line in lines if "without its user link" in line] == [
        f"Audit pipeline {name}: event stored without its user link (IntegrityError "
        "(ForeignKeyViolationError, SQLSTATE 23503)): the user's account no longer exists, "
        "and the row keeps the user's id"
    ]
    # No line carries the row's values or quotes the database's error, which repeats them.
    assert not [line for line in lines if users["deleted"][0] in line or run in line]
    assert not [line for line in lines if "violates foreign key constraint" in line or "[parameters:" in line]
    assert roles and set(roles) == {"coga_app"}
