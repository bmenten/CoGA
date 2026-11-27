"""The ClinVar releases CoGA loaded, in ClickHouse: one table per assembly, one partition per
release.

``<assembly>/SNV_INDEL/clinvar`` holds one row per allele of a release (``clinvar_vcf``), keyed
by the allele in minimal form (``small_allele_key``), where a family's rows meet it. Which
release is current is Postgres's to say (``clinvar_releases``); this table only holds them. A
release is written into its own partition and removed by dropping that partition, never by a
mutation: a load that fails leaves nothing of itself, and the releases kept stay as they were.
The table is created by the first load of its assembly.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from ..core.clickhouse import execute_clickhouse, insert_clickhouse
from ..core.config import settings
from .clickhouse_variant_ids import _require_clickhouse_identifier, _small_table_name
from .clinvar_vcf import CLINVAR_ROW_COLUMNS, ClinvarRow

_SUFFIX = "clinvar"


def clinvar_table(assembly_name: str) -> str:
    """The assembly's ClinVar table, qualified and quoted, as a statement names it."""
    return _small_table_name(assembly_name, _SUFFIX)


async def ensure_clinvar_table(assembly_name: str) -> None:
    await execute_clickhouse(
        f"""
        CREATE TABLE IF NOT EXISTS {clinvar_table(assembly_name)}
        (
            `release` Date,
            `allele_key` UInt64,
            `variantId` String,
            `variation_id` UInt32,
            `allele_id` UInt32,
            `classification` String,
            `classification_terms` Array(LowCardinality(String)),
            `conflicting` String,
            `review_status` LowCardinality(String),
            `conditions` Array(String),
            `genes` Array(String),
            `included_only` Bool
        )
        ENGINE = MergeTree
        PARTITION BY release
        ORDER BY allele_key
        """
    )


async def insert_clinvar_rows(assembly_name: str, release: date, rows: Sequence[ClinvarRow]) -> None:
    await insert_clickhouse(
        f"INSERT INTO {clinvar_table(assembly_name)} ({', '.join(CLINVAR_ROW_COLUMNS)}) VALUES",
        [row.values(release) for row in rows],
    )


async def count_clinvar_rows(assembly_name: str, release: date) -> int:
    rows = await execute_clickhouse(
        f"SELECT count() FROM {clinvar_table(assembly_name)} WHERE release = %(release)s",
        {"release": release},
    )
    return int(rows[0][0]) if rows else 0


async def stored_clinvar_releases(assembly_name: str) -> set[date]:
    """The releases the assembly's table holds a partition of (none before its first load)."""
    rows = await execute_clickhouse(
        "SELECT DISTINCT partition FROM system.parts "
        "WHERE database = %(database)s AND table = %(table)s AND active",
        {
            "database": settings.clickhouse_database,
            "table": f"{_require_clickhouse_identifier(assembly_name)}/SNV_INDEL/{_SUFFIX}",
        },
    )
    return {date.fromisoformat(str(row[0])) for row in rows or []}


async def drop_clinvar_release(assembly_name: str, release: date) -> None:
    """Remove the release's partition whole; nothing to do for a release the table lacks."""
    await execute_clickhouse(
        f"ALTER TABLE {clinvar_table(assembly_name)} DROP PARTITION %(release)s",
        {"release": release.isoformat()},
    )
