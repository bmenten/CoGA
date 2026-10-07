"""Durability helpers shared by the audit-log and UI-event pipelines.

Both pipelines enqueue events onto a bounded ``asyncio.Queue`` drained by a single
background worker. Under sustained load the queue can fill, and the worker's batch
write can fail; this module makes that overflow explicit and durable so the IVDR
accountability trail (TF-13 S-5) is never silently lost:

* ``AUDIT_LOG_DROP_ALLOWED`` false (production default): a full queue applies
  backpressure for up to ``AUDIT_LOG_BACKPRESSURE_TIMEOUT_SECONDS`` and then falls
  back to a synchronous write, so the event is always persisted.
* Without a queue (``AUDIT_LOG_MODE=sync``, or no worker running: before the lifespan
  starts it, after shutdown, under an in-process ASGI client) each event is written as
  its request runs, the way that fallback writes it (``write_event_now``).
* A genuinely unpersistable event (a synchronous write fails, or the worker's batch
  write keeps failing) is logged at ERROR with its full, already-sanitised payload —
  so it survives in the log stream that feeds monitoring — and counted, never silently
  discarded and never raised into the request. The database error is named, never
  quoted: its text repeats the rows it could not insert.
* What an event holds cannot make it unpersistable: the writers escape the values
  Postgres refuses (a NUL, half a surrogate pair, NaN) before the INSERT
  (``core/pg_storable.py``), so one such event cannot fail its batch either.
* ``AUDIT_LOG_DROP_ALLOWED`` true (non-production only; refused in prod by the
  settings validator): a full queue drops with a WARN, preserving the original
  low-overhead dev/test behaviour.

``dropped_event_count`` exposes the per-pipeline drop tally for monitoring/alerting; ``GET
/metrics`` serves it as ``coga_audit_events_not_persisted_total``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from ..core.coga_logging import describe_error
from ..core.config import settings

logger = logging.getLogger(__name__)

WriteBatch = Callable[[list[Any]], Awaitable[None]]

_dropped_counts: dict[str, int] = {}


def dropped_event_count(name: str) -> int:
    """Events the named pipeline has failed to persist since process startup."""
    return _dropped_counts.get(name, 0)


def reset_dropped_event_counts() -> None:
    """Test hook: clear the drop tallies."""
    _dropped_counts.clear()


def _increment_drop(name: str) -> int:
    _dropped_counts[name] = _dropped_counts.get(name, 0) + 1
    return _dropped_counts[name]


def record_unpersisted(name: str, payload: Any, reason: str) -> None:
    """Record an event that will not reach its table: counted for the alert, and logged with
    its payload. ``reason`` names a database error by its kind (``describe_error``), never by
    its text, which quotes the row."""
    # ERROR (not WARN): losing an accountability event is integrity-relevant. The
    # full payload is logged so the event is recoverable from the log stream.
    total = _increment_drop(name)
    logger.error(
        "Audit pipeline %s: event not persisted (%s); dropped_total=%d payload=%r",
        name,
        reason,
        total,
        payload,
    )


async def write_event_now(
    payload: Any,
    *,
    name: str,
    write_batch: WriteBatch,
    reason: str = "synchronous write failed",
) -> None:
    """Write one event at once, outside the queue. A failed write is recorded
    (``record_unpersisted``), never raised: the caller is a request, which must not fail
    because an event could not be stored."""
    try:
        await write_batch([payload])
    except Exception as exc:  # noqa: BLE001 — last resort, never raise into the request
        record_unpersisted(name, payload, f"{reason}: {describe_error(exc)}")


async def enqueue_event(
    queue: asyncio.Queue[Any],
    payload: Any,
    *,
    name: str,
    write_batch: WriteBatch,
) -> None:
    """Enqueue ``payload`` for the background worker, durably by default.

    The fast path is a non-blocking put. On a full queue, behaviour depends on
    ``AUDIT_LOG_DROP_ALLOWED``: drop-with-WARN when allowed (dev/test), otherwise
    apply bounded backpressure and fall back to a synchronous write so the event
    is not lost.
    """
    try:
        queue.put_nowait(payload)
        return
    except asyncio.QueueFull:
        pass

    if settings.audit_log_drop_allowed:
        total = _increment_drop(name)
        logger.warning(
            "Audit pipeline %s: dropping event, queue full (dropped_total=%d)",
            name,
            total,
        )
        return

    try:
        await asyncio.wait_for(
            queue.put(payload),
            timeout=settings.audit_log_backpressure_timeout_seconds,
        )
        return
    except asyncio.TimeoutError:
        logger.warning(
            "Audit pipeline %s: queue full for %.1fs, writing event synchronously",
            name,
            settings.audit_log_backpressure_timeout_seconds,
        )

    await write_event_now(
        payload, name=name, write_batch=write_batch, reason="synchronous fallback failed"
    )


async def write_event_batch_with_retry(
    write_batch: WriteBatch,
    batch: Sequence[Any],
    *,
    name: str,
) -> None:
    """Persist a drained batch, retrying transient failures with bounded backoff.

    The worker has already removed these events from the queue, so a final failure
    would lose them; each unpersisted event is therefore recorded (ERROR + count)
    rather than silently dropped.
    """
    if not batch:
        return
    attempts = settings.audit_log_max_write_attempts
    for attempt in range(1, attempts + 1):
        try:
            await write_batch(list(batch))
            return
        except Exception as exc:  # noqa: BLE001 - retried below; a final loss is recorded per event
            if attempt >= attempts:
                # The error is named, not quoted: its text repeats every row of the batch,
                # and each event's payload is logged once below.
                logger.error(
                    "Audit pipeline %s: batch of %d events failed after %d attempts: %s",
                    name,
                    len(batch),
                    attempts,
                    describe_error(exc),
                )
                for payload in batch:
                    record_unpersisted(name, payload, "batch write failed")
                return
            backoff = min(
                settings.audit_log_flush_interval_seconds * attempt,
                settings.audit_log_backpressure_timeout_seconds,
            )
            logger.warning(
                "Audit pipeline %s: batch write attempt %d/%d failed; retrying in %.1fs",
                name,
                attempt,
                attempts,
                backoff,
            )
            await asyncio.sleep(backoff)
