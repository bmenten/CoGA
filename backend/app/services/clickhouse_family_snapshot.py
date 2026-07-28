"""Snapshot/restore of a family's ClickHouse variant + interval data.

ClickHouse writes are non-transactional, and overwrite-mode import is
delete-then-insert (``replace_family_small_variants`` /
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
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from uuid import uuid4

from ..core.clickhouse import execute_clickhouse
from ..core.config import settings
from .clickhouse_interval_tracks import ensure_clickhouse_interval_table
from .clickhouse_variant_ids import _require_clickhouse_identifier
from .clickhouse_variant_storage import (
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
    assembly_name: str, family_uuid: str
) -> FamilyClickHouseSnapshot:
    """Copy the family's rows out of every family-scoped ClickHouse table.

    Each backup table is created with the source's exact structure + engine
    (``CREATE TABLE … AS …``, so CollapsingMergeTree ``sign`` and ORDER BY are
    preserved) then filled with just this family's rows. On partial failure the
    already-created backups are dropped before re-raising, so nothing leaks.
    """
    await ensure_clickhouse_variant_tables(assembly_name)
    await ensure_clickhouse_interval_table(assembly_name)
    token = uuid4().hex
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
    small-variant summaries are recomputed from the restored ``entries`` afterwards.
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


async def discard_family_clickhouse_snapshot(
    snapshot: FamilyClickHouseSnapshot,
) -> None:
    """Drop the backup tables. Best-effort: a leaked backup must never fail an import."""
    for _source, backup in snapshot.specs:
        try:
            await execute_clickhouse(f"DROP TABLE IF EXISTS {backup} SYNC")
        except Exception:  # noqa: BLE001 - cleanup must not mask the import outcome
            logger.warning(
                "Failed to drop import snapshot backup table %s", backup, exc_info=True
            )
