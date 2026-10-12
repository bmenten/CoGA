from __future__ import annotations

import asyncio
import logging
import socket

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.services import audit_log_pg, event_pipeline, ui_event_pg
from app.services.audit_log_pg import AUDIT_PIPELINE_NAME, AuditLogEventPayload
from app.services.event_pipeline import (
    dropped_event_count,
    enqueue_event,
    write_event_batch_with_retry,
    write_event_now,
)
from app.services.ui_event_pg import UiEventPayload

NAME = "test_pipe"


@pytest.fixture(autouse=True)
def _reset_counts(monkeypatch):
    # Each test counts from zero, on tallies of its own, as the other pipeline tests do.
    monkeypatch.setattr(event_pipeline, "_dropped_counts", {})


def _recorder():
    calls: list[list] = []

    async def write_batch(batch):
        calls.append(list(batch))

    return calls, write_batch


def _fast_retries(monkeypatch, attempts: int) -> None:
    monkeypatch.setattr(settings, "audit_log_max_write_attempts", attempts)
    monkeypatch.setattr(settings, "audit_log_flush_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "audit_log_backpressure_timeout_seconds", 0.05)


class ForeignKeyViolationError(Exception):
    """Stands in for asyncpg's error, which SQLAlchemy's adapter raises its own from."""


class _AdaptedIntegrityError(Exception):
    sqlstate = "23503"


def _foreign_key_violation(rows: list) -> IntegrityError:
    # What SQLAlchemy raises when Postgres refuses one row of an INSERT, here for a user deleted
    # while the row waited in the queue. Its text quotes the statement and every row it was
    # given, so the values of the whole batch.
    orig = _AdaptedIntegrityError("insert violates foreign key constraint")
    orig.__cause__ = ForeignKeyViolationError("insert violates foreign key constraint")
    failure = IntegrityError("INSERT INTO audit_log_events ...", [{"row": repr(row)} for row in rows], orig)
    assert "INSERT INTO" in str(failure) and "[parameters:" in str(failure)
    return failure


def _database(refuses: object):
    """A writer that stores the rows it is given, unless they hold ``refuses``: then the
    INSERT fails and stores none of them, as one row Postgres refuses fails the statement."""
    stored: list = []
    calls: list[list] = []

    async def write_batch(batch):
        calls.append(list(batch))
        if refuses in batch:
            raise _foreign_key_violation(batch)
        stored.extend(batch)

    return stored, calls, write_batch


def _messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def _lost(caplog) -> list[str]:
    return [message for message in _messages(caplog) if "event not persisted" in message]


@pytest.mark.asyncio
async def test_enqueue_fast_path_enqueues_without_writing():
    calls, write_batch = _recorder()
    queue: asyncio.Queue = asyncio.Queue(maxsize=4)

    await enqueue_event(queue, "evt", name=NAME, write_batch=write_batch)

    assert queue.get_nowait() == "evt"
    assert calls == []
    assert dropped_event_count(NAME) == 0


@pytest.mark.asyncio
async def test_full_queue_drops_only_when_drop_allowed(monkeypatch):
    monkeypatch.setattr(settings, "audit_log_drop_allowed", True)
    calls, write_batch = _recorder()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait("first")

    await enqueue_event(queue, "second", name=NAME, write_batch=write_batch)

    # Dropped: not written, not enqueued, but counted (never silent).
    assert calls == []
    assert queue.qsize() == 1 and queue.get_nowait() == "first"
    assert dropped_event_count(NAME) == 1


@pytest.mark.asyncio
async def test_full_queue_falls_back_to_synchronous_write(monkeypatch):
    monkeypatch.setattr(settings, "audit_log_drop_allowed", False)
    monkeypatch.setattr(settings, "audit_log_backpressure_timeout_seconds", 0.05)
    calls, write_batch = _recorder()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait("first")  # full, with no consumer

    await enqueue_event(queue, "second", name=NAME, write_batch=write_batch)

    # Backpressure times out -> the event is written synchronously, never lost.
    assert calls == [["second"]]
    assert dropped_event_count(NAME) == 0


@pytest.mark.asyncio
async def test_backpressure_enqueues_when_space_frees(monkeypatch):
    monkeypatch.setattr(settings, "audit_log_drop_allowed", False)
    monkeypatch.setattr(settings, "audit_log_backpressure_timeout_seconds", 5.0)
    calls, write_batch = _recorder()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait("first")

    task = asyncio.create_task(
        enqueue_event(queue, "second", name=NAME, write_batch=write_batch)
    )
    # Let the producer block on put(), then free a slot so it proceeds.
    for _ in range(3):
        await asyncio.sleep(0)
    assert queue.get_nowait() == "first"
    await task

    assert queue.get_nowait() == "second"
    assert calls == []
    assert dropped_event_count(NAME) == 0


@pytest.mark.asyncio
async def test_synchronous_fallback_failure_is_recorded_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(settings, "audit_log_drop_allowed", False)
    monkeypatch.setattr(settings, "audit_log_backpressure_timeout_seconds", 0.05)
    caplog.set_level(logging.ERROR, logger="app.services.event_pipeline")
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait("first")

    async def failing_write(_batch):
        raise RuntimeError("db down")

    # Must not raise into the caller (the request middleware).
    await enqueue_event(queue, "second", name=NAME, write_batch=failing_write)

    assert dropped_event_count(NAME) == 1
    assert [record.getMessage() for record in caplog.records] == [
        f"Audit pipeline {NAME}: event not persisted (synchronous fallback failed: RuntimeError); "
        "dropped_total=1 payload='second'"
    ]


@pytest.mark.asyncio
async def test_an_event_written_at_once_skips_the_queue_and_is_not_counted():
    # AUDIT_LOG_MODE=sync, or no worker running: the request writes its own event.
    calls, write_batch = _recorder()

    await write_event_now("evt", name=NAME, write_batch=write_batch)

    assert calls == [["evt"]]
    assert dropped_event_count(NAME) == 0


@pytest.mark.asyncio
async def test_an_event_that_cannot_be_written_at_once_is_recorded_not_raised(caplog):
    # Such a loss used to raise into the request and stay out of the count, so the alert on
    # coga_audit_events_not_persisted_total missed it. It is now recorded as the worker
    # records one: counted, and logged once with its payload, the error by its kind.
    caplog.set_level(logging.ERROR, logger="app.services.event_pipeline")

    async def failing(_batch):
        raise RuntimeError("INSERT failed [parameters: ('row-a',)]")

    await write_event_now("row-a", name=NAME, write_batch=failing)

    assert dropped_event_count(NAME) == 1
    assert [record.getMessage() for record in caplog.records] == [
        f"Audit pipeline {NAME}: event not persisted (synchronous write failed: RuntimeError); "
        "dropped_total=1 payload='row-a'"
    ]


@pytest.mark.asyncio
async def test_batch_retry_succeeds_after_transient_failure(monkeypatch):
    monkeypatch.setattr(settings, "audit_log_max_write_attempts", 3)
    monkeypatch.setattr(settings, "audit_log_flush_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "audit_log_backpressure_timeout_seconds", 0.05)
    attempts = {"n": 0}

    async def flaky(_batch):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("transient")

    await write_event_batch_with_retry(flaky, ["a", "b"], name=NAME)

    assert attempts["n"] == 2
    assert dropped_event_count(NAME) == 0


@pytest.mark.asyncio
async def test_a_failure_of_every_row_writes_each_event_once_and_counts_it_once(monkeypatch):
    # A failure that hits every row although the database answers (a permission or a table
    # it no longer has): after the batch's last attempt each event is written alone, once,
    # without waiting, and each is recorded once. The extra work is one write per event.
    _fast_retries(monkeypatch, attempts=2)
    calls: list[list] = []

    async def always_fail(batch):
        calls.append(list(batch))
        raise RuntimeError("permission denied for table audit_log_events")

    await write_event_batch_with_retry(always_fail, ["a", "b", "c"], name=NAME)

    # Bounded: two batch attempts, then one write per event, none of them retried.
    assert calls == [["a", "b", "c"], ["a", "b", "c"], ["a"], ["b"], ["c"]]
    assert dropped_event_count(NAME) == 3  # every event recorded, none silent


@pytest.mark.asyncio
async def test_empty_batch_is_noop():
    calls, write_batch = _recorder()
    await write_event_batch_with_retry(write_batch, [], name=NAME)
    assert calls == []
    assert dropped_event_count(NAME) == 0


@pytest.mark.asyncio
async def test_a_failed_batch_is_logged_by_kind_not_by_its_text(monkeypatch, caplog):
    # A failed INSERT's text repeats every row of the batch; the batch line names the error
    # instead, and each event lost is logged once with its own line (S-5).
    monkeypatch.setattr(settings, "audit_log_max_write_attempts", 1)
    caplog.set_level(logging.WARNING, logger="app.services.event_pipeline")

    async def failing(batch):
        raise _foreign_key_violation(batch)

    await write_event_batch_with_retry(failing, ["row-a", "row-b"], name=NAME)

    messages = _messages(caplog)
    batch_lines = [m for m in messages if "failed after" in m]
    assert batch_lines == [
        f"Audit pipeline {NAME}: batch of 2 events failed after 1 attempts: "
        "IntegrityError (ForeignKeyViolationError, SQLSTATE 23503)"
    ]
    assert sum("payload='row-a'" in m for m in messages) == 1
    assert sum("payload='row-b'" in m for m in messages) == 1
    assert not [m for m in messages if "INSERT INTO" in m or "[parameters:" in m]


@pytest.mark.asyncio
async def test_a_row_the_database_refuses_costs_only_itself(monkeypatch, caplog):
    # One row Postgres refuses for something other than its characters (here a foreign key:
    # its user was deleted while the row waited) fails the INSERT of its whole batch, on
    # every attempt. Every other event of the batch, up to AUDIT_LOG_BATCH_SIZE of any user's,
    # used to be lost with it. After the last attempt each event is now written alone, once.
    _fast_retries(monkeypatch, attempts=2)
    caplog.set_level(logging.WARNING, logger="app.services.event_pipeline")
    stored, calls, write_batch = _database(refuses="row-b")

    await write_event_batch_with_retry(write_batch, ["row-a", "row-b", "row-c"], name=NAME)

    assert stored == ["row-a", "row-c"]
    assert calls == [["row-a", "row-b", "row-c"], ["row-a", "row-b", "row-c"], ["row-a"], ["row-b"], ["row-c"]]
    assert dropped_event_count(NAME) == 1
    # The refused event is logged once with its payload, the error by its kind.
    assert _lost(caplog) == [
        f"Audit pipeline {NAME}: event not persisted (batch write failed, then its own write: "
        "IntegrityError (ForeignKeyViolationError, SQLSTATE 23503)); dropped_total=1 payload='row-b'"
    ]
    messages = _messages(caplog)
    assert (
        f"Audit pipeline {NAME}: of the batch's 3 events, 2 stored one at a time, 1 not persisted"
        in messages
    )
    # The events stored are not logged, and no line quotes the error's text.
    assert not [m for m in messages if "row-a" in m or "row-c" in m]
    assert not [m for m in messages if "INSERT INTO" in m or "[parameters:" in m]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (ConnectionRefusedError(61, "Connect call failed ('192.0.2.7', 5432)"), "ConnectionRefusedError"),
        (TimeoutError(), "TimeoutError"),
        (PoolTimeoutError("QueuePool limit of size 5 overflow 10 reached"), "TimeoutError"),
    ],
)
async def test_a_database_that_cannot_be_reached_loses_the_batch_as_before(
    failure, kind, monkeypatch, caplog
):
    # A failure that hits every row: no connection can be had. The first event's own write
    # fails the same way, so the others are recorded untried rather than written one by one
    # against a database that cannot be reached; each event is counted once.
    _fast_retries(monkeypatch, attempts=2)
    caplog.set_level(logging.WARNING, logger="app.services.event_pipeline")
    calls: list[list] = []

    async def unreachable(batch):
        calls.append(list(batch))
        raise failure

    await write_event_batch_with_retry(unreachable, ["row-a", "row-b", "row-c"], name=NAME)

    assert calls == [["row-a", "row-b", "row-c"], ["row-a", "row-b", "row-c"], ["row-a"]]
    assert dropped_event_count(NAME) == 3
    assert _lost(caplog) == [
        f"Audit pipeline {NAME}: event not persisted (batch write failed, then its own write: "
        f"{kind}); dropped_total=1 payload='row-a'",
        f"Audit pipeline {NAME}: event not persisted (batch write failed, database unreachable: "
        f"{kind}); dropped_total=2 payload='row-b'",
        f"Audit pipeline {NAME}: event not persisted (batch write failed, database unreachable: "
        f"{kind}); dropped_total=3 payload='row-c'",
    ]
    messages = _messages(caplog)
    assert (
        f"Audit pipeline {NAME}: of the batch's 3 events, 0 stored one at a time, 3 not persisted"
        in messages
    )
    assert not [m for m in messages if "192.0.2.7" in m or "QueuePool" in m]


@pytest.mark.asyncio
async def test_the_event_of_a_one_event_batch_is_not_written_again(monkeypatch, caplog):
    # Its batch was the event alone, written as many times as the batch was tried. Its line
    # names the error's kind, as every lost event's line does.
    _fast_retries(monkeypatch, attempts=2)
    caplog.set_level(logging.WARNING, logger="app.services.event_pipeline")
    stored, calls, write_batch = _database(refuses="only")

    await write_event_batch_with_retry(write_batch, ["only"], name=NAME)

    assert stored == []
    assert calls == [["only"], ["only"]]
    assert dropped_event_count(NAME) == 1
    assert _lost(caplog) == [
        f"Audit pipeline {NAME}: event not persisted (batch write failed: "
        "IntegrityError (ForeignKeyViolationError, SQLSTATE 23503)); dropped_total=1 payload='only'"
    ]
    assert not [m for m in _messages(caplog) if "one at a time" in m]
    assert not [m for m in _messages(caplog) if "INSERT INTO" in m or "[parameters:" in m]


def _audit_event(path: str) -> AuditLogEventPayload:
    return AuditLogEventPayload(method="GET", path=path, status_code=200, duration_ms=1)


def _ui_event(label: str) -> UiEventPayload:
    return UiEventPayload(event_type="click", label=label)


_WORKERS = {
    # name: (module, its batch writer, start, stop, write one event, make an event)
    AUDIT_PIPELINE_NAME: (
        audit_log_pg,
        "_write_audit_log_batch",
        audit_log_pg.start_audit_log_worker,
        audit_log_pg.stop_audit_log_worker,
        audit_log_pg.write_audit_log_event,
        _audit_event,
    ),
    "ui_event": (
        ui_event_pg,
        "_write_ui_event_batch",
        ui_event_pg.start_ui_event_worker,
        ui_event_pg.stop_ui_event_worker,
        ui_event_pg.write_ui_event,
        _ui_event,
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", sorted(_WORKERS))
async def test_the_workers_write_a_failed_batch_one_event_at_a_time(pipeline, monkeypatch):
    # The request audit's and the UI-event log's workers both write what they drain through
    # write_event_batch_with_retry: a batch with one refused row loses that row only.
    module, writer_name, start, stop, write_event, make_event = _WORKERS[pipeline]
    events = [make_event("/api/first"), make_event("/api/refused"), make_event("/api/third")]
    stored, calls, write_batch = _database(refuses=events[1])
    monkeypatch.setattr(settings, "audit_log_mode", "async")
    monkeypatch.setattr(settings, "audit_log_batch_size", 50)
    _fast_retries(monkeypatch, attempts=2)
    monkeypatch.setattr(module, writer_name, write_batch)

    await start()
    try:
        for event in events:
            await write_event(event)
    finally:
        await stop()  # drains the queue: the three events form one batch

    assert calls[0] == events
    assert stored == [events[0], events[2]]
    assert dropped_event_count(pipeline) == 1


@pytest.mark.asyncio
async def test_the_real_writer_stops_at_a_database_that_refuses_connections(monkeypatch, caplog):
    # The request audit's own writer and driver, against a local port nothing listens on:
    # asyncpg's refused connection reaches the pipeline as the OSError it is, so one write
    # more than the batch's attempts finds the database unreachable, and the other events
    # are recorded untried.
    _fast_retries(monkeypatch, attempts=2)
    caplog.set_level(logging.WARNING, logger="app.services.event_pipeline")
    events = [_audit_event(f"/api/projects/p{index}") for index in range(3)]
    calls: list[int] = []
    # A port the system just handed out and took back. (A port bound but not listening would
    # not do: on macOS a connection to it hangs until the driver's timeout.)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    engine = create_async_engine(f"postgresql+asyncpg://coga@127.0.0.1:{port}/coga")
    monkeypatch.setattr(
        audit_log_pg,
        "get_postgres_sessionmaker",
        lambda: async_sessionmaker(engine, expire_on_commit=False),
    )

    async def counted_write(batch):
        calls.append(len(batch))
        await audit_log_pg._write_audit_log_batch(batch)

    try:
        await write_event_batch_with_retry(counted_write, events, name=AUDIT_PIPELINE_NAME)
    finally:
        await engine.dispose()

    assert calls == [3, 3, 1]
    assert dropped_event_count(AUDIT_PIPELINE_NAME) == 3
    reasons = [message.split(" (", 1)[1].split(");", 1)[0] for message in _lost(caplog)]
    assert reasons == [
        "batch write failed, then its own write: ConnectionRefusedError",
        "batch write failed, database unreachable: ConnectionRefusedError",
        "batch write failed, database unreachable: ConnectionRefusedError",
    ]
