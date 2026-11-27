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

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

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


@dataclass(frozen=True, slots=True)
class ClinvarStoredAllele:
    """An allele as a stored release holds it: ClinVar's values a reader compares or shows."""

    variant_id: str
    classification: str
    classification_terms: tuple[str, ...]
    review_status: str
    variation_id: int
    included_only: bool
    conditions: tuple[str, ...]


_ALLELE_COLUMNS = (
    "allele_key, variantId, classification, classification_terms, review_status, variation_id, "
    "included_only, conditions"
)


def _alleles(rows: Iterable[Sequence[Any]] | None) -> dict[int, ClinvarStoredAllele]:
    return {
        int(key): ClinvarStoredAllele(
            variant_id=str(variant_id),
            classification=str(classification),
            classification_terms=tuple(terms),
            review_status=str(review_status),
            variation_id=int(variation_id),
            included_only=bool(included_only),
            conditions=tuple(conditions),
        )
        for key, variant_id, classification, terms, review_status, variation_id, included_only, conditions in rows
        or []
    }


# The keys one lookup names: a query's text holds its bound values, and ClickHouse refuses a
# query longer than ``max_query_size`` (256 KiB), which about 13,000 keys of 20 digits fill.
_KEYS_PER_LOOKUP = 5_000


async def clinvar_alleles(assembly_name: str, release: date, keys: Collection[int]) -> dict[int, ClinvarStoredAllele]:
    """The release's alleles of ``keys`` (``small_allele_key``), by key; an allele the release
    does not hold is left out. Read ``_KEYS_PER_LOOKUP`` keys at a time: a page's alleles in one
    query, a ranking's in a few."""
    ordered = sorted(keys)
    found: dict[int, ClinvarStoredAllele] = {}
    for start in range(0, len(ordered), _KEYS_PER_LOOKUP):
        rows = await execute_clickhouse(
            f"SELECT {_ALLELE_COLUMNS} FROM {clinvar_table(assembly_name)} "
            "WHERE release = %(release)s AND allele_key IN %(keys)s",
            {"release": release, "keys": tuple(ordered[start : start + _KEYS_PER_LOOKUP])},
        )
        found.update(_alleles(rows))
    return found


async def clinvar_alleles_of_callset(
    assembly_name: str, release: date, family_uuid: str, source: str
) -> dict[int, ClinvarStoredAllele]:
    """The release's alleles among those of the family's callset ``source``
    (``entries.allele_keys``), by allele key. The release is read only for the callset's own
    keys, the callset only for the family's rows."""
    rows = await execute_clickhouse(
        f"""
        SELECT {_ALLELE_COLUMNS}
        FROM {clinvar_table(assembly_name)}
        WHERE release = %(release)s
          AND allele_key IN (
              SELECT arrayJoin(allele_keys) FROM {_small_table_name(assembly_name, 'entries')}
              WHERE family_guid = %(family_guid)s AND source = %(source)s AND sign = 1
          )
        """,
        {"release": release, "family_guid": family_uuid, "source": source},
    )
    return _alleles(rows)


def clinvar_allele_condition(
    assembly_name: str,
    release: date,
    terms: Sequence[str],
    *,
    params: dict[str, Any],
    prefix: str,
    alias: str = "e",
) -> str:
    """"An allele of the row (alias ``alias``, its ``allele_keys``) is one the release
    classifies with one of ``terms``" (as ``_status_terms`` stores them), in SQL. The release's
    set is built once for the query, from its partition."""
    params[f"{prefix}_release"] = release
    params[f"{prefix}_terms"] = list(terms)
    return (
        f"arrayExists(k -> k IN (SELECT allele_key FROM {clinvar_table(assembly_name)} "
        f"WHERE release = %({prefix}_release)s AND hasAny(classification_terms, %({prefix}_terms)s)), "
        f"{alias}.allele_keys)"
    )


def clinvar_alleles_held_condition(
    assembly_name: str,
    release: date,
    *,
    params: dict[str, Any],
    prefix: str,
    alias: str = "e",
) -> str:
    """"The release holds every allele of the row (alias ``alias``, its ``allele_keys``)", in
    SQL: where the one rule of the design's phase 2 lets the release alone speak for the row. A
    row without an allele key (a symbolic ALT) is not held."""
    params[f"{prefix}_release"] = release
    return (
        f"(notEmpty({alias}.allele_keys) AND arrayAll(k -> k IN (SELECT allele_key FROM "
        f"{clinvar_table(assembly_name)} WHERE release = %({prefix}_release)s), {alias}.allele_keys))"
    )


async def drop_clinvar_release(assembly_name: str, release: date) -> None:
    """Remove the release's partition whole; nothing to do for a release the table lacks."""
    await execute_clickhouse(
        f"ALTER TABLE {clinvar_table(assembly_name)} DROP PARTITION %(release)s",
        {"release": release.isoformat()},
    )
