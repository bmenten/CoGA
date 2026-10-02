"""Snapshot/restore of a family's ClickHouse variant + interval data.

ClickHouse writes are non-transactional, and overwrite-mode import is
delete-then-insert (``delete_family_small_variants`` /
``replace_family_structural_variants`` pre-clear before insert). So a failed
overwrite of a PRE-EXISTING family can destroy its prior variant rows and leave
nothing but the ``import_incomplete`` flag behind (issue #365).

This module provides a pre-import snapshot of the family-scoped ClickHouse rows
(mechanism A: per-import backup tables filled via ``INSERT … SELECT``) and a
restore path, so a failed overwrite can be rolled back to the family's exact
pre-import ClickHouse state instead of being left flagged-but-partially-modified.

Scope is deliberately ClickHouse-only:
  * The summary tables (``family_variant_summary`` / ``family_sample_variant_summary``)
    are rebuildable from ``SNV_INDEL/entries`` and are recomputed on restore rather
    than snapshotted.
  * The shared, additively-keyed tables (``variants/details`` / ``annotations`` /
    ``annotation_index`` / ``gene_index`` on the SNV side) are NOT family-scoped and
    are never cleared by ``delete_family_*`` — so they are out of scope here too.
  * Postgres-resident data (provenance, ``sample_interval_track_sources``, paraphase,
    repeats) is written through the SQLAlchemy session and is not covered here.

A backup table is named after the import that owns it,
``<assembly>/SNAPSHOT/<owner>/<table>``: the owner is the import's key (its job's id, or
``run-<hex>`` for an import run outside a job). The import drops its backups when it
ends. One whose process stopped part-way cannot: the worker that ends its job drops them
(``list_import_backup_tables`` with the job's id, then ``drop_import_backup_tables``), and
startup drops every backup no running import owns (``orphaned_import_backups``). Such a
backup is of no further use: the family cannot be put back from it, as the Postgres half
of the snapshot was in the stopped process's memory and other writes of the family may
have run since; the family stays marked until an import completes it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import re
from uuid import uuid4

from ..core.clickhouse import execute_clickhouse
from ..core.config import settings
from .clickhouse_interval_tracks import ensure_clickhouse_interval_table
from .clickhouse_variant_ids import _require_clickhouse_identifier
from .clickhouse_variant_storage import (
    bump_family_structural_variant_data_version,
    ensure_clickhouse_variant_tables,
    refresh_family_small_variant_summaries,
)

logger = logging.getLogger(__name__)

# Family-scoped ClickHouse tables that an overwrite can destroy, addressed by their
# path suffix under the assembly dataset key. These MUST match the paths used by the
# ingestion/delete helpers (``_small_table_name`` / ``_structural_table_name`` /
# ``_interval_table_name``); every one carries a ``family_guid`` column.
_FAMILY_TABLE_RELS: tuple[str, ...] = (
    "SNV_INDEL/entries",
    "SV/entries",
    "SV/variants/details",
    "SV/key_lookup",
    "INTERVAL/entries",
)

# The owner segment of a backup's name: an import's key (a job's uuid, ``run-<hex>``), or
# the random token of a backup made before backups were named after their import.
_OWNER = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{0,63}$")
# A backup table's name, as _snapshot_specs makes it; the dataset is
# clickhouse_dataset_key's output.
_BACKUP_NAME = re.compile(
    r"^(?P<dataset>[A-Za-z0-9._-]+)/SNAPSHOT/(?P<owner>[^/]+)/(?P<rel>.+)$"
)
# An import job's id: backups so owned are kept while the job may still run.
_JOB_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass
class FamilyClickHouseSnapshot:
    """A captured pre-import copy of one family's ClickHouse rows.

    ``specs`` pairs each live source table with the backup table holding this
    family's snapshotted rows. Always paired with ``discard_family_clickhouse_snapshot``
    (on success, after restore, or on restore failure) so backup tables never leak.
    """

    assembly_name: str
    family_uuid: str
    token: str
    specs: list[tuple[str, str]]


def _snapshot_owner(owner: str | None) -> str:
    """The owner segment of a new backup's name: the import's key, or a random token."""
    token = owner or uuid4().hex
    if not _OWNER.match(token):
        raise ValueError(f"Not an import key a backup can be named after: {token!r}")
    return token


def _snapshot_specs(assembly_name: str, token: str) -> list[tuple[str, str]]:
    database = settings.clickhouse_database
    dataset = _require_clickhouse_identifier(assembly_name)
    specs: list[tuple[str, str]] = []
    for rel in _FAMILY_TABLE_RELS:
        source = f"{database}.`{dataset}/{rel}`"
        backup = f"{database}.`{dataset}/SNAPSHOT/{token}/{rel}`"
        specs.append((source, backup))
    return specs


async def snapshot_family_clickhouse_state(
    assembly_name: str, family_uuid: str, *, owner: str | None = None
) -> FamilyClickHouseSnapshot:
    """Copy the family's rows out of every family-scoped ClickHouse table.

    Each backup table is created with the source's exact structure + engine
    (``CREATE TABLE … AS …``, so CollapsingMergeTree ``sign`` and ORDER BY are
    preserved) then filled with just this family's rows. On partial failure the
    already-created backups are dropped before re-raising, so nothing leaks.

    ``owner`` is the import's key, which names the backup tables: a backup the import
    could not drop (its process stopped) is then found by it. Without one the tables
    carry a random token.
    """
    token = _snapshot_owner(owner)
    await ensure_clickhouse_variant_tables(assembly_name)
    await ensure_clickhouse_interval_table(assembly_name)
    snapshot = FamilyClickHouseSnapshot(
        assembly_name=assembly_name,
        family_uuid=str(family_uuid),
        token=token,
        specs=_snapshot_specs(assembly_name, token),
    )
    try:
        for source, backup in snapshot.specs:
            await execute_clickhouse(f"CREATE TABLE {backup} AS {source}")
            await execute_clickhouse(
                f"INSERT INTO {backup} SELECT * FROM {source} "
                "WHERE family_guid = %(family_guid)s",
                {"family_guid": snapshot.family_uuid},
            )
    except Exception:
        await discard_family_clickhouse_snapshot(snapshot)
        raise
    return snapshot


async def restore_family_clickhouse_state(snapshot: FamilyClickHouseSnapshot) -> None:
    """Roll every family-scoped table back to the snapshot.

    For each table: synchronously delete the family's current rows (whatever the
    failed import wrote) then re-insert the snapshotted rows. The rebuildable
    small-variant summaries are recomputed from the restored ``entries`` afterwards,
    which also moves the small-variant data version; the SV data version is moved
    explicitly, since the restore rewrites the SV tables without the storage helpers.
    Raises on any failure so the caller can fall back to the incomplete flag.
    """
    family_guid = snapshot.family_uuid
    for source, backup in snapshot.specs:
        await execute_clickhouse(
            f"ALTER TABLE {source} DELETE WHERE family_guid = %(family_guid)s "
            "SETTINGS mutations_sync = 1",
            {"family_guid": family_guid},
        )
        await execute_clickhouse(f"INSERT INTO {source} SELECT * FROM {backup}")
    # Summaries are not snapshotted (they are derived); rebuild them from the
    # now-restored SNV entries so counts match the restored data exactly.
    await refresh_family_small_variant_summaries(snapshot.assembly_name, family_guid)
    # An SV→gene index built from the failed import's SVs while it ran must not outlive
    # the restore.
    await bump_family_structural_variant_data_version(snapshot.assembly_name, family_guid)


async def discard_family_clickhouse_snapshot(
    snapshot: FamilyClickHouseSnapshot,
) -> None:
    """Drop the backup tables. Best-effort: a leaked backup must never fail an import."""
    for _source, backup in snapshot.specs:
        try:
            await execute_clickhouse(f"DROP TABLE IF EXISTS {backup} SYNC")
        except Exception:  # cleanup must not mask the import outcome
            logger.warning(
                "Failed to drop import snapshot backup table %s", backup, exc_info=True
            )


@dataclass(frozen=True)
class ImportBackupTable:
    """An import's backup table, as ``system.tables`` lists it."""

    name: str
    owner: str
    # When ClickHouse last changed its metadata (its creation, for a backup); None if unread.
    modified_at: datetime | None


async def list_import_backup_tables(owner: str | None = None) -> list[ImportBackupTable]:
    """The import backup tables in the database: every one, or ``owner``'s.

    Only names in the form ``_snapshot_specs`` makes -- a dataset key, the SNAPSHOT
    segment, an owner and one of the family-scoped tables -- so a name listed here is
    safe to put in a statement.
    """
    rows = await execute_clickhouse(
        """
        SELECT name, metadata_modification_time
        FROM system.tables
        WHERE database = %(database)s AND name LIKE %(pattern)s
        """,
        {"database": settings.clickhouse_database, "pattern": "%/SNAPSHOT/%"},
    )
    tables: list[ImportBackupTable] = []
    for name, modified_at in rows or []:
        match = _BACKUP_NAME.match(str(name))
        if (
            match is None
            or not _OWNER.match(match["owner"])
            or match["rel"] not in _FAMILY_TABLE_RELS
        ):
            continue
        if owner is not None and match["owner"] != owner:
            continue
        if isinstance(modified_at, datetime) and modified_at.tzinfo is None:
            # ClickHouse DateTime values come back naive, in UTC.
            modified_at = modified_at.replace(tzinfo=timezone.utc)
        tables.append(
            ImportBackupTable(
                name=str(name),
                owner=match["owner"],
                modified_at=modified_at if isinstance(modified_at, datetime) else None,
            )
        )
    return sorted(tables, key=lambda table: table.name)


def is_job_owned(table: ImportBackupTable) -> bool:
    """Whether the backup's owner is an import job (its id), not a run outside a job."""
    return bool(_JOB_ID.match(table.owner))


def orphaned_import_backups(
    tables: Iterable[ImportBackupTable],
    *,
    running_jobs: set[str],
    now: datetime,
    grace: timedelta,
) -> list[ImportBackupTable]:
    """The backups no running import owns.

    A backup owned by an import job is kept while that job is queued, validating or
    running (``running_jobs``: a stopped import's job stays running until a worker ends
    it, and drops its backups then); once the job has ended, or is gone, its import can no
    longer use it. One owned by an import run outside a job (or made before backups were
    named after their import) is kept for ``grace`` after it was made, as nothing records
    whether that import still runs; one whose age cannot be read is kept.
    """
    orphaned: list[ImportBackupTable] = []
    for table in tables:
        if is_job_owned(table):
            if table.owner not in running_jobs:
                orphaned.append(table)
        elif table.modified_at is not None and now - table.modified_at > grace:
            orphaned.append(table)
    return orphaned


async def drop_import_backup_tables(tables: Iterable[ImportBackupTable]) -> list[str]:
    """Drop these backup tables; returns those dropped. Best-effort, table by table: one
    that cannot be dropped is logged and left for the next attempt."""
    dropped: list[str] = []
    for table in tables:
        try:
            await execute_clickhouse(
                f"DROP TABLE IF EXISTS {settings.clickhouse_database}.`{table.name}` SYNC"
            )
        except Exception:  # left for the next attempt; never fails the caller
            logger.warning("Failed to drop import backup table %s", table.name, exc_info=True)
            continue
        dropped.append(table.name)
    return dropped
