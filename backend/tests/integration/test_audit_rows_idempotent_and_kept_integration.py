"""Each audit event of a batch is stored once, or costs only itself, on real Postgres (SEC-17, TF-13 S-5).

The request audit (``audit_log_events``) and the UI-event log (``ui_events``) each write what
their worker drains in one INSERT, as the runtime role ``coga_app``, which holds INSERT and
SELECT on these append-only tables and nothing more (05_grants.sql). Each test runs the real
worker and writer against real Postgres: three events are queued, the worker drains them as one
batch when it stops, and the rows are read back.

- A batch whose first commit is made but whose answer is lost (the connection drops as Postgres
  commits) is written again by the worker. Each event carries the id it was given before it was
  queued, and the INSERT does nothing for an id the table holds (``ON CONFLICT (event_id) DO
  NOTHING``): every event is stored once. Before, every row of the batch was stored twice.
  Every write runs under ``SET LOCAL ROLE coga_app``.
- A row naming a user whose account was deleted while the row waited in the queue fails the
  foreign key on ``user_id`` on every attempt of its batch. Written alone, it is now stored
  without the link, the user's id kept in ``request_meta._deleted_user_id`` or
  ``detail._deleted_user_id``, its copied e-mail and role as they were; the batch's other rows
  keep their link. One line says so, naming the error by its kind; nothing is counted as lost.
  Before, the row was lost. Every write runs under ``SET LOCAL ROLE coga_app``.
- A row refused for something other than its characters fails that INSERT on every attempt: a
  request's row whose duration the driver cannot encode (more than the 32-bit ``duration_ms``
  holds), or a UI event whose client timestamp it cannot encode (year 1 at +05:00 is year 0 in
  UTC). Every other row of the batch, any user's, used to be lost with it. After the batch's
  last attempt the worker now writes the rows one at a time, so the refused row costs only
  itself: it is counted once and logged once with its payload, and no log line quotes the
  database's error, which repeats the rows. (A row naming a deleted user was this case's
  example until SEC-17; it is now stored without the link, as above.)
- The rows stored are read back by the administrator's lists (``list_audit_log_events``,
  ``list_ui_events``), which no other test ran: each filter (the request's method, its status,
  the user's e-mail address, a path, route, label or target the text is part of, the UI
  event's type) keeps exactly the rows it names, the count with them, newest first, and the
  pages split them.

The values are synthetic. Both tables are append-only, so their rows stay, under a path or a
label of the run's own. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke``
job sets it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.integration._datastores import run_closing_stores

pytestmark = pytest.mark.integration

_DELETED_USER_KEY = "_deleted_user_id"

# What no log line may carry: the statement, its parameters, and what Postgres and the driver
# say about the row they refused, which quotes its values.
_ERROR_TEXT = (
    "INSERT INTO",
    "[parameters:",
    "violates foreign key constraint",
    "is not present in table",
    "invalid input for query argument",
    "out of int32 range",
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


def _audit_event(name: str, prefix: str, user: tuple[str, str] | None, **values):
    from backend.app.services.audit_log_pg import AuditLogEventPayload

    return AuditLogEventPayload(
        **{
            "method": "GET",
            "path": f"{prefix}{name}",
            "status_code": 200,
            "duration_ms": 1,
            "user_id": user[0] if user else None,
            "user_email": user[1] if user else None,
            "user_role": "viewer" if user else None,
            "request_meta": {"headers": {"accept": "*/*"}},
            **values,
        }
    )


def _ui_event(name: str, prefix: str, user: tuple[str, str] | None, **values):
    from backend.app.services.ui_event_pg import UiEventPayload

    return UiEventPayload(
        **{
            "event_type": "click",
            "category": "button",
            "label": f"{prefix}{name}",
            "detail": {"count": 1},
            "user_id": user[0] if user else None,
            "user_email": user[1] if user else None,
            "user_role": "viewer" if user else None,
            **values,
        }
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


def _prefix(name: str, kind: str) -> tuple[str, str]:
    """A path (request audit) or label (UI event) of the run's own, and the run's tag."""

    run = uuid4().hex[:12]
    return (f"/api/{kind}-{run}/" if name == "audit_log" else f"{kind}-{run} "), run


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


def _quoting_the_error(lines: list[str]) -> list[str]:
    return [line for line in lines if any(marker in line for marker in _ERROR_TEXT)]


def _unencodable(name: str) -> tuple[str, object, object]:
    """Per pipeline, a field with an ordinary value and a value the driver cannot encode: one
    past what duration_ms, a 32-bit INTEGER, holds; and year 1 at +05:00, year 0 in UTC."""

    return {
        "audit_log": ("duration_ms", 1, 2**31),
        "ui_event": (
            "occurred_at",
            datetime.now(timezone.utc),
            datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5))),
        ),
    }[name]


@pytest.mark.parametrize("name", ["audit_log", "ui_event"])
def test_a_batch_written_again_after_its_commits_answer_was_lost_is_stored_once(
    name, pipeline, monkeypatch, caplog
) -> None:
    module, writer_name, start, stop, write_event, make_event, table, name_column, data_column = _pipelines()[name]
    prefix, _run = _prefix(name, "sec17-once")
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

    rows = run_closing_stores(_exercise, init_schema=True)

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
    prefix, run = _prefix(name, "sec17-kept")
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

    rows = run_closing_stores(_exercise, init_schema=True)

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


@pytest.mark.parametrize(
    "name", ["audit_log", "ui_event"], ids=["a request's duration", "a UI event's time"]
)
def test_a_row_whose_value_the_driver_cannot_encode_costs_only_itself(name, pipeline, caplog) -> None:
    _module, _writer, start, stop, write_event, make_event, table, name_column, data_column = _pipelines()[name]
    field, ordinary, unencodable = _unencodable(name)
    prefix, _run = _prefix(name, "audit-batch")
    events = [
        make_event(event, prefix, None, **{field: unencodable if event == "refused" else ordinary})
        for event in ("first", "refused", "third")
    ]
    names = [getattr(event, name_column) for event in events]

    async def _exercise() -> list[dict]:
        await _through_the_worker(start, stop, write_event, events)
        return await _stored(table, name_column, data_column, prefix)

    rows = run_closing_stores(_exercise, init_schema=True)

    # The other rows are stored, in the order they were queued; the refused one is counted.
    assert [row["name"] for row in rows] == [names[0], names[2]]
    assert pipeline.dropped_event_count(name) == 1
    lines = _lines(caplog)
    lost = [line for line in lines if "event not persisted" in line]
    assert len(lost) == 1, lines
    assert lost[0].startswith(
        f"Audit pipeline {name}: event not persisted (batch write failed, then its own write: "
    ), lost[0]
    # The driver refuses to encode the value (asyncpg's DataError). How SQLAlchemy wraps that
    # refusal differs between environments (InterfaceError locally; DBAPIError with SQLSTATE
    # 22000 in CI's job), so only the driver's error is pinned, then the rest of the line.
    reason, _, rest = lost[0].partition("); dropped_total=1 ")
    assert "DataError" in reason, lost[0]
    expected_payload = {
        "audit_log": (
            f"payload=AuditLogEventPayload(method='GET', path='{names[1]}', status_code=200, "
            f"duration_ms={unencodable},"
        ),
        "ui_event": f"payload=UiEventPayload(event_type='click', category='button', label='{names[1]}',",
    }[name]
    assert rest.startswith(expected_payload), lost[0]
    assert (
        f"Audit pipeline {name}: of the batch's 3 events, 2 stored one at a time, 1 not persisted"
        in lines
    )
    # A row stored is not logged, and no line quotes the database's error.
    assert not [line for line in lines if names[0] in line or names[2] in line]
    assert _quoting_the_error(lines) == []


def test_the_admins_audit_log_list_reads_the_stored_rows_by_each_filter(pipeline) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import audit_log_pg

    run = uuid4().hex[:12]
    reader, writer = f"reader-{run}@example.org", f"writer-{run}@example.org"
    events = [
        _audit_event("a", f"/api/audit-list-{run}/", None, method="GET", status_code=200, user_email=reader),
        # Found by its route alone: the path does not hold the run's tag.
        _audit_event(
            "b", "/api/audit-list/", None, method="POST", status_code=403, user_email=writer,
            route_path=f"/api/audit-list-{run}/{{family_id}}",
        ),
        _audit_event("c", f"/api/audit-list-{run}/", None, method="GET", status_code=500, user_email=reader),
    ]

    async def _exercise() -> dict[str, tuple[int, list[str]]]:
        for event in events:  # a batch each: each row its own created_at, the newest listed first
            await _through_the_worker(
                audit_log_pg.start_audit_log_worker,
                audit_log_pg.stop_audit_log_worker,
                audit_log_pg.write_audit_log_event,
                [event],
            )
        async with get_postgres_sessionmaker()() as session:

            async def listed(page: int = 1, page_size: int = 50, **filters) -> tuple[int, list[str]]:
                found = await audit_log_pg.list_audit_log_events(session, page=page, page_size=page_size, **filters)
                assert (found.page, found.page_size) == (page, page_size)
                return found.total, [item.path.rsplit("/", 1)[-1] for item in found.items]

            return {
                "the run": await listed(path_contains=f"  {run} "),
                "GET": await listed(method="get", path_contains=run),
                "403": await listed(status_code=403, path_contains=run),
                "the reader": await listed(user_email=f" READER-{run} ", path_contains=run),
                "page 1": await listed(page=1, page_size=2, path_contains=run),
                "page 2": await listed(page=2, page_size=2, path_contains=run),
                "another run": await listed(path_contains=uuid4().hex),
            }

    found = run_closing_stores(_exercise, init_schema=True)

    assert found == {
        "the run": (3, ["c", "b", "a"]),
        "GET": (2, ["c", "a"]),
        "403": (1, ["b"]),
        "the reader": (2, ["c", "a"]),
        "page 1": (3, ["c", "b"]),
        "page 2": (3, ["a"]),
        "another run": (0, []),
    }


def test_the_admins_ui_event_list_reads_the_stored_rows_by_each_filter(pipeline) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import ui_event_pg

    run = uuid4().hex[:12]
    reader = f"reader-{run}@example.org"
    events = [
        # The run's tag in the label, the page's path, or the target page's path.
        _ui_event("a", f"ui-list-{run} ", None, event_type="click", user_email=reader),
        _ui_event("b", "", None, event_type="navigate", path=f"/families/{run}", user_email=reader),
        _ui_event("c", "", None, event_type="click", to_path=f"/reports/{run}", user_email=f"writer-{run}@example.org"),
    ]

    async def _exercise() -> dict[str, tuple[int, list[str]]]:
        for event in events:  # a batch each: each row its own created_at, the newest listed first
            await _through_the_worker(
                ui_event_pg.start_ui_event_worker, ui_event_pg.stop_ui_event_worker, ui_event_pg.write_ui_event, [event]
            )
        async with get_postgres_sessionmaker()() as session:

            async def listed(page: int = 1, page_size: int = 50, **filters) -> tuple[int, list[str]]:
                found = await ui_event_pg.list_ui_events(session, page=page, page_size=page_size, **filters)
                assert (found.page, found.page_size) == (page, page_size)
                return found.total, [(item.label or "").rsplit(" ", 1)[-1] for item in found.items]

            return {
                "the run": await listed(path_contains=f" {run} "),
                "clicks": await listed(event_type=" CLICK ", path_contains=run),
                "the reader": await listed(user_email=f" READER-{run} ", path_contains=run),
                "page 1": await listed(page=1, page_size=2, path_contains=run),
                "page 2": await listed(page=2, page_size=2, path_contains=run),
                "another run": await listed(path_contains=uuid4().hex),
            }

    found = run_closing_stores(_exercise, init_schema=True)

    assert found == {
        "the run": (3, ["c", "b", "a"]),
        "clicks": (2, ["c", "a"]),
        "the reader": (2, ["b", "a"]),
        "page 1": (3, ["c", "b"]),
        "page 2": (3, ["a"]),
        "another run": (0, []),
    }
