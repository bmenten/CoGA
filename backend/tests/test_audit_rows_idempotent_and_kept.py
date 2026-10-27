"""The request audit and the UI-event log store each event once, and keep the row of a deleted user (SEC-17).

Both workers write what they drain in one INSERT, and write a batch again after a failed attempt
(``event_pipeline.write_event_batch_with_retry``). A commit whose answer never reached the writer
(the connection dropped as Postgres committed) looked like a failure, so the batch was written
again and each of its rows was stored twice. Each event now carries an id of its own, given when
the event is made, before it is queued; the INSERT does nothing for an id the table holds
(``ON CONFLICT (event_id) DO NOTHING``), so every write of an event stores it once.

A row naming a user whose account was deleted while the row waited in the queue failed the
foreign key on ``user_id`` and was lost. It is now stored without the link, the user's id kept in
the row's own data (``request_meta._deleted_user_id``, ``detail._deleted_user_id``), and the log
names the error by its kind, without values.

The writers run against a stand-in for Postgres behind their sessionmaker: the two tables, the
accounts their rows may name, and the unique event id an INSERT may say ON CONFLICT on. The
integration test (``integration/test_audit_rows_idempotent_and_kept_integration.py``) shows the
same on real Postgres, as the runtime role ``coga_app``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

import pytest
from asyncpg.exceptions import ForeignKeyViolationError, UniqueViolationError
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.services import audit_log_pg, event_pipeline, ui_event_pg
from app.services.audit_log_pg import AUDIT_PIPELINE_NAME, AuditLogEventPayload
from app.services.event_pipeline import dropped_event_count
from app.services.ui_event_pg import UiEventPayload

# Synthetic accounts: one that exists, one deleted while its events waited in the queue.
_KEPT_USER = "00000000-0000-4000-8000-00000000a001"
_DELETED_USER = "00000000-0000-4000-8000-00000000d001"
# Where a row stored without its user link keeps the user's id.
_DELETED_USER_KEY = "_deleted_user_id"


def _integrity_error(driver_error: Exception, statement: str, rows: list[dict]) -> IntegrityError:
    """What SQLAlchemy raises when Postgres refuses a row: its asyncpg adapter's error, raised
    from asyncpg's, wrapped with the statement and every row it was given."""
    adapted = AsyncAdapt_asyncpg_dbapi.IntegrityError(str(driver_error), driver_error)
    adapted.__cause__ = driver_error
    return IntegrityError(statement, rows, adapted)


def _foreign_key_violation(table: str, statement: str, rows: list[dict]) -> IntegrityError:
    driver_error = ForeignKeyViolationError(
        f'insert or update on table "{table}" violates foreign key constraint "{table}_user_id_fkey"'
    )
    driver_error.constraint_name = f"{table}_user_id_fkey"
    return _integrity_error(driver_error, statement, rows)


def _unique_violation(table: str, statement: str, rows: list[dict]) -> IntegrityError:
    driver_error = UniqueViolationError(f'duplicate key value violates unique constraint "{table}_event_id_key"')
    driver_error.constraint_name = f"{table}_event_id_key"
    return _integrity_error(driver_error, statement, rows)


class _Postgres:
    """The two tables behind the writers' sessionmaker, as Postgres keeps them.

    A row naming a user no account has fails its statement (the foreign key), and so does an
    event id the table holds, unless the INSERT says ``ON CONFLICT (event_id) DO NOTHING``: then
    that row is skipped. Rows reach the table at the commit. With ``lose_first_commit_answer``
    the first commit is made and its answer lost, as when the connection drops while Postgres
    commits: the writer sees an error, and the rows are stored."""

    def __init__(self, *, users: tuple[str, ...] = (), lose_first_commit_answer: bool = False) -> None:
        self.tables: dict[str, list[dict]] = {"audit_log_events": [], "ui_events": []}
        self.users = set(users)
        self.lose_first_commit_answer = lose_first_commit_answer
        self.commits = 0

    def sessionmaker(self) -> Callable[[], _Session]:
        return lambda: _Session(self)


class _Session:
    def __init__(self, database: _Postgres) -> None:
        self.database = database
        self.staged: list[tuple[str, dict]] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        self.staged = []
        return False

    async def execute(self, statement: Any, rows: list[dict]) -> None:
        sql = " ".join(str(statement).split())
        match = re.match(r"INSERT INTO (\w+) ", sql)
        assert match, sql
        table = match.group(1)
        on_conflict = sql.endswith("ON CONFLICT (event_id) DO NOTHING")
        for row in rows:
            if row.get("user_id") is not None and row["user_id"] not in self.database.users:
                raise _foreign_key_violation(table, sql, rows)
            held = [stored.get("event_id") for stored in self.database.tables[table]]
            held += [staged.get("event_id") for name, staged in self.staged if name == table]
            if row.get("event_id") is not None and row["event_id"] in held:
                if on_conflict:
                    continue
                raise _unique_violation(table, sql, rows)
            self.staged.append((table, dict(row)))

    async def commit(self) -> None:
        for table, row in self.staged:
            self.database.tables[table].append(row)
        self.staged = []
        self.database.commits += 1
        if self.database.lose_first_commit_answer and self.database.commits == 1:
            raise ConnectionResetError(54, "Connection reset by peer")

    async def rollback(self) -> None:
        self.staged = []


def _audit_event(name: str, user_id: str | None) -> AuditLogEventPayload:
    return AuditLogEventPayload(
        method="GET",
        path=f"/api/sec17/{name}",
        status_code=200,
        duration_ms=1,
        user_id=user_id,
        user_email="analyst@example.org" if user_id else None,
        user_role="viewer" if user_id else None,
        request_meta={"headers": {"accept": "*/*"}},
    )


def _ui_event(name: str, user_id: str | None) -> UiEventPayload:
    return UiEventPayload(
        event_type="click",
        label=f"sec17 {name}",
        detail={"count": 1},
        user_id=user_id,
        user_email="analyst@example.org" if user_id else None,
        user_role="viewer" if user_id else None,
    )


class _Pipeline(NamedTuple):
    module: Any
    table: str
    writer: str
    start: Callable[[], Awaitable[None]]
    stop: Callable[[], Awaitable[None]]
    write_event: Callable[[Any], Awaitable[None]]
    make_event: Callable[[str, str | None], Any]
    name_column: str  # the column naming the event in these tests
    data_column: str  # the row's own JSON data


_PIPELINES = {
    AUDIT_PIPELINE_NAME: _Pipeline(
        audit_log_pg,
        "audit_log_events",
        "_write_audit_log_batch",
        audit_log_pg.start_audit_log_worker,
        audit_log_pg.stop_audit_log_worker,
        audit_log_pg.write_audit_log_event,
        _audit_event,
        "path",
        "request_meta",
    ),
    "ui_event": _Pipeline(
        ui_event_pg,
        "ui_events",
        "_write_ui_event_batch",
        ui_event_pg.start_ui_event_worker,
        ui_event_pg.stop_ui_event_worker,
        ui_event_pg.write_ui_event,
        _ui_event,
        "label",
        "detail",
    ),
}


@pytest.fixture(autouse=True)
def _pipeline_settings(monkeypatch):
    # The production mode and batch size, with retries that do not wait, on counts of their own.
    monkeypatch.setattr(settings, "audit_log_mode", "async")
    monkeypatch.setattr(settings, "audit_log_batch_size", 50)
    monkeypatch.setattr(settings, "audit_log_max_write_attempts", 2)
    monkeypatch.setattr(settings, "audit_log_flush_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "audit_log_backpressure_timeout_seconds", 0.05)
    monkeypatch.setattr(event_pipeline, "_dropped_counts", {})


def _on(pipeline: str, database: _Postgres, monkeypatch) -> _Pipeline:
    """The pipeline, its writer reaching ``database``."""
    chosen = _PIPELINES[pipeline]
    monkeypatch.setattr(chosen.module, "get_postgres_sessionmaker", database.sessionmaker)
    return chosen


async def _through_the_worker(chosen: _Pipeline, events: list) -> None:
    await chosen.start()
    try:
        for event in events:
            await chosen.write_event(event)
    finally:
        await chosen.stop()  # drains the queue: the events form one batch


def _messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", sorted(_PIPELINES))
@pytest.mark.parametrize("attempts", [2, 1], ids=["batch-written-again", "events-written-one-at-a-time"])
async def test_a_batch_written_again_after_a_commit_whose_outcome_was_unknown_stores_each_event_once(
    pipeline, attempts, monkeypatch, caplog
) -> None:
    # The first commit is made, and its answer lost. The worker takes that for a failure and
    # writes the batch again (two attempts), or, after its last attempt, each of its events
    # alone (#820). Either way every event was stored twice.
    monkeypatch.setattr(settings, "audit_log_max_write_attempts", attempts)
    database = _Postgres(users=(_KEPT_USER,), lose_first_commit_answer=True)
    chosen = _on(pipeline, database, monkeypatch)
    caplog.set_level(logging.WARNING)
    events = [chosen.make_event(name, _KEPT_USER) for name in ("first", "second", "third")]

    await _through_the_worker(chosen, events)

    rows = database.tables[chosen.table]
    assert len(rows) == 3, [row[chosen.name_column] for row in rows]
    # Each row is its event's, under the id the event was given before it was queued.
    assert [row[chosen.name_column] for row in rows] == [getattr(event, chosen.name_column) for event in events]
    assert [row["event_id"] for row in rows] == [event.event_id for event in events]
    assert dropped_event_count(pipeline) == 0
    assert not [message for message in _messages(caplog) if "event not persisted" in message]


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", sorted(_PIPELINES))
async def test_a_row_naming_a_user_deleted_while_it_waited_is_stored_without_the_link(
    pipeline, monkeypatch, caplog
) -> None:
    # The account is deleted (operator SQL) while its event waits in the queue. The foreign key
    # refused the row on every attempt of its batch, and then alone: it was counted and lost.
    database = _Postgres(users=(_KEPT_USER,))
    chosen = _on(pipeline, database, monkeypatch)
    caplog.set_level(logging.WARNING)
    events = [
        chosen.make_event("first", _KEPT_USER),
        chosen.make_event("deleted", _DELETED_USER),
        chosen.make_event("third", _KEPT_USER),
    ]

    await _through_the_worker(chosen, events)

    rows = database.tables[chosen.table]
    # Every row is stored, in queue order; the deleted user's without the link.
    assert [row[chosen.name_column] for row in rows] == [getattr(event, chosen.name_column) for event in events]
    assert [row["user_id"] for row in rows] == [_KEPT_USER, None, _KEPT_USER]
    data = [json.loads(row[chosen.data_column]) for row in rows]
    # The row keeps the user's id in its own data, and the actor its copied e-mail and role.
    assert data[1][_DELETED_USER_KEY] == _DELETED_USER
    assert (rows[1]["user_email"], rows[1]["user_role"]) == ("analyst@example.org", "viewer")
    assert _DELETED_USER_KEY not in data[0] and _DELETED_USER_KEY not in data[2]
    assert dropped_event_count(pipeline) == 0
    messages = _messages(caplog)
    assert not [message for message in messages if "event not persisted" in message]
    # One line says so, the error named by its kind; no line carries the row's values.
    assert [message for message in messages if "without its user link" in message] == [
        f"Audit pipeline {pipeline}: event stored without its user link (IntegrityError "
        "(ForeignKeyViolationError, SQLSTATE 23503)): the user's account no longer exists, "
        "and the row keeps the user's id"
    ]
    assert not [message for message in messages if _DELETED_USER in message or "sec17" in message]
    assert not [message for message in messages if "INSERT INTO" in message or "[parameters:" in message]


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", sorted(_PIPELINES))
async def test_an_event_written_at_once_for_a_deleted_user_is_stored_without_the_link(
    pipeline, monkeypatch, caplog
) -> None:
    # AUDIT_LOG_MODE=sync, or no worker running: the request writes its own event, alone.
    monkeypatch.setattr(settings, "audit_log_mode", "sync")
    database = _Postgres(users=(_KEPT_USER,))
    chosen = _on(pipeline, database, monkeypatch)
    caplog.set_level(logging.WARNING)
    event = chosen.make_event("deleted", _DELETED_USER)

    await chosen.write_event(event)

    rows = database.tables[chosen.table]
    assert [(row["event_id"], row["user_id"]) for row in rows] == [(event.event_id, None)]
    assert json.loads(rows[0][chosen.data_column])[_DELETED_USER_KEY] == _DELETED_USER
    assert dropped_event_count(pipeline) == 0
    assert len([message for message in _messages(caplog) if "without its user link" in message]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", sorted(_PIPELINES))
async def test_a_batch_holding_a_deleted_users_row_is_left_whole_to_the_worker(pipeline, monkeypatch) -> None:
    # Which row of a batch the foreign key refused, Postgres says only in its text. The writer
    # leaves such a batch to the worker, which writes its events one at a time after the last
    # attempt; written alone, the row is stored without the link (above).
    database = _Postgres(users=(_KEPT_USER,))
    chosen = _on(pipeline, database, monkeypatch)
    writer = getattr(chosen.module, chosen.writer)

    with pytest.raises(IntegrityError):
        await writer([chosen.make_event("first", _KEPT_USER), chosen.make_event("deleted", _DELETED_USER)])

    assert database.tables[chosen.table] == []


def test_each_event_carries_an_id_of_its_own_into_every_write() -> None:
    audit = _audit_event("first", _KEPT_USER)
    same_request = _audit_event("first", _KEPT_USER)
    ui = _ui_event("first", _KEPT_USER)

    for event in (audit, same_request, ui):
        assert str(uuid.UUID(event.event_id)) == event.event_id
    assert len({audit.event_id, same_request.event_id, ui.event_id}) == 3
    # Every write of the event carries the same id.
    assert audit_log_pg._audit_log_insert_params(audit)["event_id"] == audit.event_id
    assert audit_log_pg._audit_log_insert_params(audit)["event_id"] == audit.event_id
    assert ui_event_pg._ui_event_insert_params(ui)["event_id"] == ui.event_id
    # The INSERT does nothing for an id the table holds.
    for statement in (audit_log_pg._INSERT_AUDIT_LOG_SQL, ui_event_pg._INSERT_UI_EVENT_SQL):
        assert " ".join(str(statement).split()).endswith("ON CONFLICT (event_id) DO NOTHING")


def test_a_client_cannot_set_the_deleted_users_key_in_a_ui_events_detail() -> None:
    event = _ui_event("forged", _KEPT_USER)
    event.detail = {_DELETED_USER_KEY: _DELETED_USER, "count": 1}

    params = ui_event_pg._ui_event_insert_params(event)

    assert json.loads(params["detail"]) == {"count": 1}
    assert params["user_id"] == _KEPT_USER


def test_only_the_tables_own_user_link_is_taken_for_a_deleted_user() -> None:
    statement = "INSERT INTO audit_log_events ..."
    refused = _foreign_key_violation("audit_log_events", statement, [])
    user_link_refused = event_pipeline.user_link_refused

    assert event_pipeline.DELETED_USER_KEY == _DELETED_USER_KEY
    assert user_link_refused(refused, "audit_log_events_user_id_fkey")
    # Another table's link, another constraint, or another error is left to the worker.
    assert not user_link_refused(refused, "ui_events_user_id_fkey")
    assert not user_link_refused(
        _unique_violation("audit_log_events", statement, []), "audit_log_events_user_id_fkey"
    )
    assert not user_link_refused(ConnectionResetError(54, "reset"), "audit_log_events_user_id_fkey")
    unnamed = ForeignKeyViolationError("violates foreign key constraint")
    assert not user_link_refused(_integrity_error(unnamed, statement, []), "audit_log_events_user_id_fkey")
