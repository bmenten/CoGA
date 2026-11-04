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
  write keeps failing and so does the event's own write) is logged at ERROR with its
  full, already-sanitised payload — so it survives in the log stream that feeds
  monitoring — and counted, never silently discarded and never raised into the request.
  The database error is named, never quoted: its text repeats the rows it could not
  insert.
* What an event holds cannot make it unpersistable: the writers escape the values
  Postgres refuses (a NUL, half a surrogate pair, NaN) before the INSERT
  (``core/pg_storable.py``), so one such event cannot fail its batch either.
* One event the database refuses for another reason (a constraint, a value its driver
  cannot encode) fails the INSERT of its whole batch, on every attempt. After the last
  attempt the worker therefore writes the batch's events one at a time, each once, so
  that event costs only itself (``_write_each_event_alone``).
* ``AUDIT_LOG_DROP_ALLOWED`` true (non-production only; refused in prod by the
  settings validator): a full queue drops with a WARN, preserving the original
  low-overhead dev/test behaviour.
* Writing an event again cannot store it twice (SEC-17). Each event carries an id of its
  own (``new_event_id``), given as it is made, before it is queued, and the writers'
  INSERT does nothing for an id the table holds (``ON CONFLICT (event_id) DO NOTHING``).
  A commit whose answer never reached the worker looks like a failure, and the batch is
  written again: its events are stored once.
* A row naming a user whose account was deleted while the row waited (operator SQL only)
  fails the foreign key on ``user_id``. Written alone, as the worker writes a failed
  batch's events, its writer stores it without the link and keeps the user's id in the
  row (``DELETED_USER_KEY``), and the log says so without values (``record_unlinked``).

``dropped_event_count`` exposes the per-pipeline drop tally for monitoring/alerting; ``GET
/metrics`` serves it as ``coga_audit_events_not_persisted_total``.

The two pipelines (``audit_log_pg``, ``ui_event_pg``) share their worker
(``run_event_worker``), the choice between the queue and an immediate write
(``write_or_enqueue_event``), and their batch writer's handling of a deleted user
(``write_batch_unlinking_deleted_user``); each keeps its own queue, table and row.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.base import Executable

from ..core.coga_logging import describe_error
from ..core.config import settings

logger = logging.getLogger(__name__)

WriteBatch = Callable[[list[Any]], Awaitable[None]]

_Event = TypeVar("_Event")


class UserEvent(Protocol):
    """An event that names the user it came from (``AuditLogEventPayload``, ``UiEventPayload``)."""

    @property
    def user_id(self) -> str | None: ...


class InsertRows(Protocol):
    """A pipeline's INSERT of a list of its events; ``unlink_user`` stores each without its user
    link and keeps the user's id in the row (``DELETED_USER_KEY``)."""

    def __call__(self, payloads: list[Any], *, unlink_user: bool = ...) -> Awaitable[None]: ...

# A write that failed with one of these never reached the database: it could not connect
# (refused, unreachable, timed out, TLS), or no pooled connection came free in time. Every
# other event's write would fail the same way.
_UNREACHABLE_ERRORS = (OSError, PoolTimeoutError)

# The key, in ``audit_log_events.request_meta`` and ``ui_events.detail``, that keeps the id of
# the user a row was stored without (``user_link_refused``). Only the writers set it.
DELETED_USER_KEY = "_deleted_user_id"

# Postgres's SQLSTATE for a row that a foreign key refuses.
_FOREIGN_KEY_VIOLATION = "23503"

_dropped_counts: dict[str, int] = {}


def new_event_id() -> str:
    """An event's own id, given as the event is made, before it is queued: every write of the
    event carries it, and its table stores it once (``ON CONFLICT (event_id) DO NOTHING``)."""
    return str(uuid4())


def user_link_refused(exc: BaseException, foreign_key: str) -> bool:
    """Whether ``exc`` is Postgres refusing a row by ``foreign_key``, the row's link to its
    user: no account has the user's id any more. Accounts are deleted only by operator SQL, so
    the user's account went while the row waited in the queue."""
    if not isinstance(exc, IntegrityError):
        return False
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != _FOREIGN_KEY_VIOLATION:
        return False
    # SQLAlchemy's asyncpg adapter raises its own error from asyncpg's, which names the constraint.
    driver_error = getattr(orig, "__cause__", None) or orig
    return getattr(driver_error, "constraint_name", None) == foreign_key


def record_unlinked(name: str, reason: str) -> None:
    """Record an event its writer stored without its user link (``user_link_refused``). The
    event is stored, so it is not counted; the line names the error by its kind
    (``describe_error``) and carries no value: the row keeps the user's id itself."""
    logger.warning(
        "Audit pipeline %s: event stored without its user link (%s): the user's account no "
        "longer exists, and the row keeps the user's id",
        name,
        reason,
    )


def dropped_event_count(name: str) -> int:
    """Events the named pipeline has failed to persist since process startup."""
    return _dropped_counts.get(name, 0)


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


async def write_or_enqueue_event(
    queue: asyncio.Queue[Any] | None,
    payload: Any,
    *,
    name: str,
    write_batch: WriteBatch,
) -> None:
    """Store one event of the named pipeline as ``AUDIT_LOG_MODE`` says.

    ``off``: not at all. ``async`` with the pipeline's worker running: through its ``queue``
    (``enqueue_event``). ``sync``, or no worker running (``queue`` None: before the lifespan
    starts it, after shutdown, under an in-process ASGI client): written at once, and a failed
    write counts toward the alert as the worker's would (``write_event_now``).
    """
    if settings.audit_log_mode == "off":
        return
    if settings.audit_log_mode != "async" or queue is None:
        await write_event_now(payload, name=name, write_batch=write_batch)
        return
    await enqueue_event(queue, payload, name=name, write_batch=write_batch)


def drain_queue(queue: asyncio.Queue[_Event], limit: int) -> list[_Event]:
    """Up to ``limit`` events waiting in ``queue``, taken without waiting."""
    items: list[_Event] = []
    while len(items) < limit:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


async def run_event_worker(
    queue: asyncio.Queue[Any],
    shutdown: asyncio.Event,
    write_batch: WriteBatch,
    *,
    name: str,
) -> None:
    """The named pipeline's background worker.

    As soon as an event waits in ``queue`` it takes it with up to ``AUDIT_LOG_BATCH_SIZE`` - 1
    more that wait and writes them as one batch (``write_event_batch_with_retry``). Once
    ``shutdown`` is set, it writes what is left in batches of ``AUDIT_LOG_BATCH_SIZE`` and ends.
    """
    while True:
        if shutdown.is_set():
            batch = drain_queue(queue, settings.audit_log_batch_size)
            if not batch:
                return
            await write_event_batch_with_retry(write_batch, batch, name=name)
            continue

        try:
            first_item = await asyncio.wait_for(
                queue.get(),
                timeout=settings.audit_log_flush_interval_seconds,
            )
        except asyncio.TimeoutError:
            continue

        batch = [first_item, *drain_queue(queue, settings.audit_log_batch_size - 1)]
        await write_event_batch_with_retry(write_batch, batch, name=name)


async def stop_event_worker(
    task: asyncio.Task[None] | None, shutdown: asyncio.Event | None
) -> None:
    """Have a pipeline's worker write what is left in its queue, and wait until it ends. The
    caller forgets the worker's queue, task and event afterwards, also when this raises."""
    if task is None or shutdown is None:
        return
    shutdown.set()
    await task


async def insert_event_rows(
    session_factory: async_sessionmaker[AsyncSession],
    statement: Executable,
    rows: list[dict[str, Any]],
) -> None:
    """Execute ``statement`` for each of ``rows`` in one transaction of a session of its own:
    committed whole, or rolled back and the error raised."""
    async with session_factory() as session:
        try:
            await session.execute(statement, rows)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def write_batch_unlinking_deleted_user(
    insert_rows: InsertRows,
    payloads: list[Any],
    *,
    name: str,
    foreign_key: str,
) -> None:
    """Write the named pipeline's ``payloads`` (``UserEvent``) with ``insert_rows``.

    A row naming a user whose account was deleted while the row waited in the queue fails
    ``foreign_key``, the row's link to its user. One such event written alone is written again
    without the link, the user's id kept in the row, and logged without values (SEC-17,
    ``record_unlinked``). A batch is left to the worker, which writes its events one at a time
    after its last attempt (``write_event_batch_with_retry``).
    """
    if not payloads:
        return
    try:
        await insert_rows(payloads)
    except IntegrityError as exc:
        first: UserEvent = payloads[0]
        alone = len(payloads) == 1 and bool(first.user_id)
        if not alone or not user_link_refused(exc, foreign_key):
            raise
        await insert_rows(payloads, unlink_user=True)
        record_unlinked(name, describe_error(exc))


async def write_event_batch_with_retry(
    write_batch: WriteBatch,
    batch: Sequence[Any],
    *,
    name: str,
) -> None:
    """Persist a drained batch, retrying transient failures with bounded backoff.

    The worker has already removed these events from the queue, so a final failure
    would lose them. After the last attempt the events are written one at a time
    (``_write_each_event_alone``), and each event that still cannot be written is
    recorded (ERROR + count) rather than silently dropped.
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
                # and the payload of each event lost is logged once, by record_unpersisted.
                error = describe_error(exc)
                logger.warning(
                    "Audit pipeline %s: batch of %d events failed after %d attempts: %s",
                    name,
                    len(batch),
                    attempts,
                    error,
                )
                await _write_each_event_alone(write_batch, batch, name=name, batch_error=error)
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


async def _write_each_event_alone(
    write_batch: WriteBatch,
    batch: Sequence[Any],
    *,
    name: str,
    batch_error: str,
) -> None:
    """Write the events of a batch that failed its last attempt one at a time, each once.

    An event the database refuses (a constraint, a value its driver cannot encode) fails
    the INSERT of its whole batch, on every attempt; written alone, it costs only itself,
    and the other events are stored. Each event is written once, without waiting, so a
    batch costs at most as many more writes as it holds events (``AUDIT_LOG_BATCH_SIZE``).
    A write that never reached the database (``_UNREACHABLE_ERRORS``) ends the pass: the
    events not yet written are recorded untried, as the whole batch used to be. The event
    of a one-event batch has been written alone already, and is recorded at once with the
    batch's error (``batch_error``, named by ``describe_error``).
    """
    if len(batch) == 1:
        record_unpersisted(name, batch[0], f"batch write failed: {batch_error}")
        return
    stored = 0
    for index, payload in enumerate(batch):
        try:
            await write_batch([payload])
        except Exception as exc:  # noqa: BLE001 - recorded per event, never raised into the worker
            error = describe_error(exc)
            record_unpersisted(name, payload, f"batch write failed, then its own write: {error}")
            if isinstance(exc, _UNREACHABLE_ERRORS):
                for untried in batch[index + 1 :]:
                    record_unpersisted(
                        name, untried, f"batch write failed, database unreachable: {error}"
                    )
                break
        else:
            stored += 1
    logger.warning(
        "Audit pipeline %s: of the batch's %d events, %d stored one at a time, %d not persisted",
        name,
        len(batch),
        stored,
        len(batch) - stored,
    )
