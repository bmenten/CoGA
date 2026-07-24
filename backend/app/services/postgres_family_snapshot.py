"""Snapshot/restore of a family's import-loop-modified Postgres rows.

Companion to `clickhouse_family_snapshot`. The per-dataset importers COMMIT their
Postgres writes independently (progress visibility / streaming), so a failed
OVERWRITE of a pre-existing family can leave some datasets' Postgres rows updated
while others — and, since #383, the ClickHouse variant data — are rolled back.
This captures the Postgres rows the dataset import LOOP modifies, before the loop,
and restores them on failure so the family's Postgres state matches the restored
ClickHouse state (the Postgres follow-up to issue #365).

Scope — only the tables written DURING the dataset loop, captured after family
registration. Registration idempotently refreshes the family/sample *descriptive*
metadata from the package manifest (families/family_members/samples metadata,
provenance, project links) before any dataset runs; that refresh is intentionally
left in place — this reverts the per-dataset *data*, not the manifest re-sync.

Restore is deliberately IN-PLACE and never `DELETE FROM families`: that would fire
the `ON DELETE SET NULL` cascade on `report_signouts` / `clinical_audit_events` and
detach signed history from the family (and the protective triggers won't let it be
re-linked). Instead each loop-modified table's family-scoped rows are deleted and
re-inserted verbatim (original ids/timestamps preserved) via
`jsonb_populate_recordset`, and `samples.metadata` is restored with an UPDATE.
Postgres is transactional, so the whole restore commits atomically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Family-scoped tables the dataset importers write during the import loop. Every one
# has a `family_id uuid` column and a plain `gen_random_uuid()` PK (not GENERATED
# ALWAYS), so a `to_jsonb` capture round-trips cleanly back through
# `jsonb_populate_recordset` on restore. These names are a fixed allow-list (never
# user input), so interpolating them into the SQL below is safe.
_LOOP_MODIFIED_TABLES: tuple[str, ...] = (
    "repeat_expansions",              # repeats dataset (delete-then-insert per sample)
    "sample_paraphase_results",       # paraphase dataset (delete-then-insert per sample)
    "sample_interval_track_sources",  # wisecondorx/qdnaseq/apcad/coverage/pcf/haplotype tracks
    "individual_hpo",                 # phenotypes dataset (additive upsert -> snapshot also fixes stale rows)
    "family_annotation_manifest",     # SNV + repeats VCF-header provenance (upsert)
)


@dataclass
class FamilyPostgresSnapshot:
    """In-memory capture of a family's loop-modified Postgres rows.

    ``tables`` maps each table to a JSON-array text of its rows (kept as text so no
    driver-specific jsonb (de)serialisation is involved); ``sample_metadata`` maps
    each sample uuid to its ``metadata`` jsonb text.
    """

    family_uuid: str
    tables: dict[str, str] = field(default_factory=dict)
    sample_metadata: dict[str, str] = field(default_factory=dict)


async def snapshot_family_postgres_state(
    session: AsyncSession, family_uuid: str
) -> FamilyPostgresSnapshot:
    """Capture the family's rows in every loop-modified table (read-only)."""
    fid = str(family_uuid)
    snapshot = FamilyPostgresSnapshot(family_uuid=fid)
    for table in _LOOP_MODIFIED_TABLES:
        snapshot.tables[table] = (
            await session.execute(
                text(
                    f"SELECT COALESCE(jsonb_agg(to_jsonb(t)), '[]'::jsonb)::text "
                    f"FROM {table} AS t WHERE family_id = CAST(:fid AS uuid)"
                ),
                {"fid": fid},
            )
        ).scalar_one()
    for sample_uuid, metadata in (
        await session.execute(
            text("SELECT id::text, metadata::text FROM samples WHERE family_id = CAST(:fid AS uuid)"),
            {"fid": fid},
        )
    ).all():
        snapshot.sample_metadata[sample_uuid] = metadata
    return snapshot


async def restore_family_postgres_state(
    session: AsyncSession, snapshot: FamilyPostgresSnapshot
) -> None:
    """Roll the loop-modified tables back to the snapshot, then commit.

    Runs as one transaction: each table's current family rows are deleted and the
    snapshotted rows re-inserted verbatim, then `samples.metadata` is restored.
    Raises on any failure so the caller can roll back and fall back to the
    incomplete flag.
    """
    fid = snapshot.family_uuid
    for table, rows_json in snapshot.tables.items():
        await session.execute(
            text(f"DELETE FROM {table} WHERE family_id = CAST(:fid AS uuid)"),
            {"fid": fid},
        )
        await session.execute(
            text(
                f"INSERT INTO {table} "
                f"SELECT * FROM jsonb_populate_recordset(NULL::{table}, CAST(:rows AS jsonb))"
            ),
            {"rows": rows_json},
        )
    for sample_uuid, metadata in snapshot.sample_metadata.items():
        await session.execute(
            text("UPDATE samples SET metadata = CAST(:metadata AS jsonb) WHERE id = CAST(:sid AS uuid)"),
            {"metadata": metadata, "sid": sample_uuid},
        )
    await session.commit()
