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
commit or roll back, whichever worker or process runs it. The locks are transaction-scoped
(``pg_advisory_xact_lock``), as the other locks of this code base are: a commit, a rollback
or a lost connection releases them. Readers take no lock.

A writer that waited may find its family or sample deleted by the writer before it. It then
writes nothing and answers 404, as it would have had it started after the delete.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
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


@dataclass
class _Hold:
    keys: frozenset[str]
    active: bool = True


# The hold_family_variant_writes blocks the current task runs in. A task started inside one
# inherits it, but only while the block lasts.
_holds: ContextVar[tuple[_Hold, ...]] = ContextVar("family_variant_write_holds", default=())


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
        token = _holds.set((*_holds.get(), hold))
        try:
            yield
        finally:
            hold.active = False
            _holds.reset(token)
            try:
                await lock_session.rollback()
            except Exception:  # the block's own outcome stands
                # A connection whose rollback fails is discarded, and Postgres releases its
                # locks with it.
                logger.warning(
                    "Could not end the variant-write lock transaction of family %s",
                    family_uuid,
                    exc_info=True,
                )
