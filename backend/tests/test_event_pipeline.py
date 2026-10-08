from __future__ import annotations

import asyncio
import logging

import pytest

from app.core.config import settings
from app.services import event_pipeline
from app.services.event_pipeline import (
    dropped_event_count,
    enqueue_event,
    write_event_batch_with_retry,
    write_event_now,
)

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
async def test_batch_retry_records_every_event_after_exhaustion(monkeypatch):
    monkeypatch.setattr(settings, "audit_log_max_write_attempts", 2)
    monkeypatch.setattr(settings, "audit_log_flush_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "audit_log_backpressure_timeout_seconds", 0.05)
    attempts = {"n": 0}

    async def always_fail(_batch):
        attempts["n"] += 1
        raise RuntimeError("db down")

    await write_event_batch_with_retry(always_fail, ["a", "b", "c"], name=NAME)

    assert attempts["n"] == 2  # bounded retries, not infinite
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
    # instead, and each payload is still logged once with its own line (S-5).
    monkeypatch.setattr(settings, "audit_log_max_write_attempts", 1)
    caplog.set_level(logging.ERROR, logger="app.services.event_pipeline")

    async def failing(_batch):
        raise RuntimeError("INSERT failed [parameters: ('row-a', 'row-b')]")

    await write_event_batch_with_retry(failing, ["row-a", "row-b"], name=NAME)

    messages = [record.getMessage() for record in caplog.records]
    batch_lines = [m for m in messages if "failed after" in m]
    assert batch_lines == [f"Audit pipeline {NAME}: batch of 2 events failed after 1 attempts: RuntimeError"]
    assert sum("payload='row-a'" in m for m in messages) == 1
    assert all("[parameters:" not in m for m in messages)
