"""One write at a time to each family's variants.

A family's variants are stored in ClickHouse, which has no transactions, and every write to
them reads the rows it replaces, deletes them and inserts them again changed: a per-sample
upload merges its file's calls into its source's stored rows, the admin sample delete writes
the family's rows back without the sample, a package dataset replaces its source, and a
failed package import restores the rows it snapshotted before it began. Two writes of one
family ran at once, each from what it had read before the other wrote. Both answered, and
one writer's calls were lost, or a variant was stored twice with different calls (a part
merge then keeps one of the rows), or an SV lost its details row to the other write's delete
between its own details and entries inserts.

So each writer of a family's variants holds a Postgres advisory lock for the family's small
variants, its structural variants or both, from before its first read of the rows until its
transaction ends. A second writer of the same family and variant type waits for the first to
commit or roll back, whichever worker or process runs it. The single-file uploads of a
sample's repeat calls and interval tracks hold both: a package import's restore rewrites
those rows too, and the import holds both locks from its snapshot to its restore, so such an
upload never falls in between. A restore then neither undoes the upload nor removes rows the
upload has struck off a whole-family entry (``family_package_registration.record_family_upload``). The locks are transaction-scoped
(``pg_advisory_xact_lock``), as the other locks of this code base are: a commit, a rollback
or a lost connection releases them. Readers take no lock, except the one reader whose reads
must all see one state of the family's variants: the report sign-out, which freezes them
into a signed record. It shares the locks (:func:`try_share_family_variant_writes`), so no
writer starts while it reads, and it refuses rather than waits when a writer holds them.

A writer that waited may find its family or sample deleted by the writer before it. It then
writes nothing and answers 404, as it would have had it started after the delete.

A lock held for a whole package import (:func:`hold_family_variant_writes`) lives on a
connection of its own, and goes with it: Postgres releases it when the connection is lost,
and nothing told the import, which went on writing beside the next writer (SAFE-15). Such a
hold can be checked (:func:`watch_family_variant_holds`): a check runs ``SELECT 1`` in the
lock's transaction, which answers only while that transaction, and so the lock, lives.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
from typing import AsyncIterator, Collection, Iterable

from fastapi import HTTPException
from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_sessionmaker

logger = logging.getLogger(__name__)

SMALL_VARIANTS = "small_variants"
STRUCTURAL_VARIANTS = "structural_variants"
# The order a writer that needs both takes them in, so two such writers never each hold
# one and wait for the other.
VARIANT_TYPES: tuple[str, ...] = (SMALL_VARIANTS, STRUCTURAL_VARIANTS)

_LOCK = text("SELECT pg_advisory_xact_lock(hashtext(:k))")
# Shared and without waiting: granted unless a writer (an exclusive holder) holds the key,
# beside any number of other shared holders.
_TRY_SHARE = text("SELECT pg_try_advisory_xact_lock_shared(hashtext(:k))")
# The named samples the family still has: no row when the family is gone, one NULL row when
# it has none of them. Typed, so that an empty list renders as an empty set of text.
_STORED_SAMPLES = text(
    """
    SELECT s.id::text
    FROM families f
    LEFT JOIN samples s
      ON s.family_id = f.id AND s.id::text IN :sample_uuids
    WHERE f.id = CAST(:family_uuid AS uuid)
    """
).bindparams(bindparam("sample_uuids", expanding=True, type_=String()))


_ALIVE = text("SELECT 1")


class VariantWriteHoldLost(RuntimeError):
    """The connection that held a family's variant-write locks was lost, and Postgres
    released them with it: another write of the family may have started since."""


@dataclass
class _Hold:
    keys: frozenset[str]
    active: bool = True
    # Runs SELECT 1 in the hold's transaction; raises VariantWriteHoldLost when it cannot.
    probe: Callable[[], Awaitable[None]] | None = None


# The hold_family_variant_writes blocks the current task runs in. A task started inside one
# inherits it, but only while the block lasts.
_holds: ContextVar[tuple[_Hold, ...]] = ContextVar("family_variant_write_holds", default=())

# The holds a watch_family_variant_holds block sees: those its tasks take, in any task
# started inside it.
_watched: ContextVar[list[_Hold] | None] = ContextVar("family_variant_write_watched", default=None)


def family_variant_lock_key(family_uuid: str, variant_type: str) -> str:
    return f"family-variant-writes:{variant_type}:{family_uuid}"


def _held_keys() -> frozenset[str]:
    return frozenset(key for hold in _holds.get() if hold.active for key in hold.keys)


async def _require_stored(session: AsyncSession, family_uuid: str, samples: Collection[str]) -> None:
    named = sorted({str(sample) for sample in samples})
    result = await session.execute(
        _STORED_SAMPLES, {"family_uuid": family_uuid, "sample_uuids": named}
    )
    stored: list[str | None] = list(result.scalars().all())
    if not stored:
        raise HTTPException(status_code=404, detail="Family not found")
    if set(named) - {str(value) for value in stored if value is not None}:
        raise HTTPException(status_code=404, detail="Sample not found")


async def lock_family_variant_writes(
    session: AsyncSession,
    family_uuid: str,
    variant_types: Iterable[str],
    *,
    samples: Collection[str] | None = None,
) -> None:
    """Hold the family's write lock for each of ``variant_types`` until ``session``'s
    transaction ends.

    Take it before the first read of the rows the write replaces, and every lock the write
    needs in one call: they are taken in one order. With ``samples`` (sample uuids), the
    family and those samples must still be stored once the locks are held, or the write is
    refused (404) before anything is written. Inside a :func:`hold_family_variant_writes`
    block of the same family the task holds the locks already, and nothing is taken.
    """
    requested = set(variant_types)
    unknown = requested - set(VARIANT_TYPES)
    if unknown:
        raise ValueError(f"Unknown variant type(s): {sorted(unknown)}")
    held = _held_keys()
    keys = [
        key
        for key in (family_variant_lock_key(family_uuid, kind) for kind in VARIANT_TYPES if kind in requested)
        if key not in held
    ]
    if not keys:
        return
    for key in keys:
        await session.execute(_LOCK, {"k": key})
    if samples is not None:
        await _require_stored(session, family_uuid, samples)


async def try_share_family_variant_writes(session: AsyncSession, family_uuid: str) -> bool:
    """Keep every writer of the family's variants out until ``session``'s transaction ends,
    or return False at once when one is writing.

    For a reader whose reads must all see one state of the family's variants: the report
    sign-out builds its snapshot from many reads, in Postgres and ClickHouse, and a write
    between two of them would freeze a state the family never had into a signed record.
    The locks are shared, so such readers do not keep each other out, and every writer
    waits for them. They do not wait for a writer, since a package import holds the locks
    for its whole run; the caller refuses instead. On False a key granted before the refused
    one stays held until the transaction ends, as every lock of it does; the caller ends it.
    """
    for variant_type in VARIANT_TYPES:
        key = family_variant_lock_key(family_uuid, variant_type)
        if not (await session.execute(_TRY_SHARE, {"k": key})).scalar_one():
            return False
    return True


@contextmanager
def watch_family_variant_holds() -> Iterator[Callable[[], Awaitable[None]]]:
    """Watch the :func:`hold_family_variant_writes` blocks the tasks started inside this
    block enter, and yield their check.

    The check runs ``SELECT 1`` in each hold's transaction while the hold lasts, and raises
    :class:`VariantWriteHoldLost` when one cannot: its connection, and with it the locks, is
    gone. For a job that runs its import in a task of its own and checks it from another,
    as the package-import worker's heartbeat does.
    """
    holds: list[_Hold] = []
    token = _watched.set(holds)

    async def check() -> None:
        for hold in list(holds):
            if hold.active and hold.probe is not None:
                await hold.probe()

    try:
        yield check
    finally:
        _watched.reset(token)


@asynccontextmanager
async def hold_family_variant_writes(
    family_uuid: str, *, samples: Collection[str] | None = None
) -> AsyncIterator[None]:
    """Hold both of the family's write locks for the whole block, on a Postgres connection
    of their own.

    For a write that spans many transactions: the package import snapshots the family's
    rows, commits as it goes, and restores the snapshot when a dataset fails, which would
    otherwise put back what the family held before a write that ran meanwhile. The writers
    called in the block take no lock of their own for this family, since the task holds it,
    and neither does a task started in the block while the block lasts; every other writer
    of the family waits until the block ends. ``samples`` are checked as by
    :func:`lock_family_variant_writes`.
    """
    keys = frozenset(family_variant_lock_key(family_uuid, variant_type) for variant_type in VARIANT_TYPES)
    if keys <= _held_keys():
        yield
        return
    async with get_postgres_sessionmaker()() as lock_session:
        await lock_family_variant_writes(lock_session, family_uuid, VARIANT_TYPES, samples=samples)
        hold = _Hold(keys)
        # One use of the lock's session at a time: a check from another task, or the end.
        in_use = asyncio.Lock()

        async def probe() -> None:
            async with in_use:
                if not hold.active:
                    return
                try:
                    await lock_session.execute(_ALIVE)
                except Exception as exc:
                    raise VariantWriteHoldLost(
                        f"The connection that held family {family_uuid}'s variant-write locks "
                        "was lost, and the locks with it."
                    ) from exc

        hold.probe = probe
        watched = _watched.get()
        if watched is not None:
            watched.append(hold)
        token = _holds.set((*_holds.get(), hold))
        try:
            yield
        finally:
            # No check starts from here on; one already running ends before the rollback.
            hold.active = False
            if watched is not None:
                watched.remove(hold)
            _holds.reset(token)
            async with in_use:
                try:
                    await lock_session.rollback()
                except Exception:  # the block's own outcome stands
                    # A connection whose rollback fails is discarded, and Postgres releases
                    # its locks with it.
                    logger.warning(
                        "Could not end the variant-write lock transaction of family %s",
                        family_uuid,
                        exc_info=True,
                    )
