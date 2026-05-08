from __future__ import annotations

import asyncio
from hashlib import blake2b
import json
import re
from typing import Any, Iterable, Sequence

from ..core.clickhouse import execute_clickhouse
from ..core.config import settings
from .clickhouse_family_variants import (
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from .data_scope import normalize_chromosome

_VALID_CLICKHOUSE_SEGMENT = re.compile(r"^[A-Za-z0-9._/-]+$")
_SMALL_GT_REF = {"0/0", "0|0"}
_SMALL_GT_MISSING = {"./.", ".|.", "", "."}


def _require_clickhouse_identifier(value: str) -> str:
    if not _VALID_CLICKHOUSE_SEGMENT.fullmatch(value):
        raise ValueError("Assembly name is invalid")
    return value


def _small_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SNV_INDEL/{suffix}`"


def _structural_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SV/{suffix}`"


def _expected_clickhouse_variant_tables(assembly_name: str) -> list[tuple[str, str, str]]:
    dataset = _require_clickhouse_identifier(assembly_name)
    return [
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants_disk"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants_memory"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants/details"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/key_lookup"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/entries"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/project_gt_stats"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/gt_stats"),
        ("small_variants", "materialized_view", f"{dataset}/SNV_INDEL/entries_to_project_gt_stats_mv"),
        (
            "small_variants",
            "materialized_view",
            f"{dataset}/SNV_INDEL/project_gt_stats_to_gt_stats_mv",
        ),
        ("structural_variants", "table", f"{dataset}/SV/variants/details"),
        ("structural_variants", "table", f"{dataset}/SV/key_lookup"),
        ("structural_variants", "table", f"{dataset}/SV/entries"),
    ]


def _stable_uint64(*parts: Any) -> int:
    payload = "||".join(str(part) for part in parts).encode()
    return int.from_bytes(blake2b(payload, digest_size=8).digest(), byteorder="big", signed=False)


def build_small_variant_id(chrom: str, start: int, ref: str, alt: str) -> str:
    return f"{normalize_chromosome(chrom)}-{int(start)}-{ref}-{alt}"


def build_structural_variant_id(
    chrom: str,
    start: int,
    end: int,
    sv_type: str,
    *,
    remote_chr: str | None = None,
    remote_start: int | None = None,
    remote_end: int | None = None,
) -> str:
    parts = [
        normalize_chromosome(chrom),
        str(int(start)),
        str(int(end)),
        str(sv_type or ""),
        normalize_chromosome(remote_chr) if remote_chr else "",
        "" if remote_start is None else str(int(remote_start)),
        "" if remote_end is None else str(int(remote_end)),
    ]
    return "-".join(parts)


def small_variant_key(assembly_name: str, family_uuid: str, variant_id: str) -> int:
    return _stable_uint64("small", assembly_name, family_uuid, variant_id)


def structural_variant_key(assembly_name: str, family_uuid: str, variant_id: str) -> int:
    return _stable_uint64("structural", assembly_name, family_uuid, variant_id)


def _xpos(chrom: str, pos: int) -> int:
    normalized = normalize_chromosome(chrom).upper()
    rank_map = {
        "X": 23,
        "Y": 24,
        "M": 25,
        "MT": 25,
    }
    try:
        rank = int(normalized)
    except ValueError:
        rank = rank_map.get(normalized, 99)
    return (rank * 1_000_000_000) + int(pos)


def _json_payload(annotations: Any) -> str:
    return json.dumps({"annotations": annotations if annotations is not None else []})


def _string_list(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _annotation_gene_symbols(annotations: Sequence[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for annotation in annotations:
        for key in ("gene", "gene_symbol", "geneSymbol", "hgnc_symbol", "hgncSymbol"):
            value = str(annotation.get(key) or "").strip()
            if value:
                values.append(value)
                break
    return _string_list(values)


def _annotation_rsid(annotations: Sequence[dict[str, Any]]) -> str | None:
    for annotation in annotations:
        value = str(annotation.get("rsid") or "").strip()
        if value:
            return value
    return None


def _annotation_gnomad_over_5_percent(annotations: Sequence[dict[str, Any]]) -> bool:
    def _candidate_values(annotation: dict[str, Any]) -> list[float]:
        values: list[float] = []
        for key in (
            "gnomad_af",
            "gnomadAf",
            "gnomad_exomes_af",
            "gnomadExomesAf",
            "gnomad_genomes_af",
            "gnomadGenomesAf",
            "gnomad_popmax_af",
            "gnomadPopmaxAf",
            "topmed_af",
            "topmedAf",
        ):
            raw = annotation.get(key)
            try:
                if raw not in (None, "", "."):
                    values.append(float(raw))
            except (TypeError, ValueError):
                continue
        frequencies = annotation.get("population_frequencies") or annotation.get("populationFrequencies")
        if isinstance(frequencies, dict):
            for raw in frequencies.values():
                try:
                    if raw not in (None, "", "."):
                        values.append(float(raw))
                except (TypeError, ValueError):
                    continue
        return values

    return any(value > 0.05 for annotation in annotations for value in _candidate_values(annotation))


def _normalized_project_ids(project_ids: Sequence[str]) -> list[str]:
    deduped = _string_list(project_ids)
    return deduped or ["unassigned"]


def _small_call_ab(call: SmallVariantCall) -> float | None:
    if call.af:
        return float(call.af[0])
    if len(call.ad) > 1:
        total_depth = sum(call.ad)
        if total_depth > 0:
            return float(call.ad[1]) / float(total_depth)
    return None


def _small_call_gq(call: SmallVariantCall) -> int | None:
    return None if call.gq is None else int(call.gq)


def _small_call_dp(call: SmallVariantCall) -> int | None:
    return None if call.dp is None else int(call.dp)


def _small_call_ps(call: SmallVariantCall) -> int | None:
    return None if call.ps is None else int(call.ps)


def _structural_call_gq(call: StructuralVariantCall) -> int | None:
    return None


def _structural_call_qual(call: StructuralVariantCall) -> float | None:
    return None if call.qual is None else float(call.qual)


def _structural_call_read_support(call: StructuralVariantCall) -> int | None:
    return None if call.read_support is None else int(call.read_support)


def _small_variant_entry_rows(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[SmallVariantRecord],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    detail_rows: list[tuple[Any, ...]] = []
    lookup_rows: list[tuple[Any, ...]] = []
    entry_rows: list[tuple[Any, ...]] = []
    normalized_project_ids = _normalized_project_ids(project_ids)
    for record in records:
        variant_id = record.variant_id or build_small_variant_id(
            record.chr,
            record.start,
            record.ref,
            record.alt,
        )
        variant_key = record.variant_key or small_variant_key(assembly_name, family_uuid, variant_id)
        gene_symbols = _string_list(record.gene_symbols or _annotation_gene_symbols(record.annotations))
        rsid = record.rsid or _annotation_rsid(record.annotations)
        filters = _string_list(record.filters)
        annotations_json = _json_payload(record.annotations)
        detail_rows.append(
            (
                variant_key,
                variant_id,
                normalize_chromosome(record.chr),
                int(record.start),
                record.ref,
                record.alt,
                rsid,
                filters,
                annotations_json,
                record.source or "",
                None,
                None,
            )
        )
        lookup_rows.append((variant_id, variant_key))
        sample_ids = [call.sample for call in record.calls]
        sample_gts = [call.gt for call in record.calls]
        sample_gqs = [_small_call_gq(call) for call in record.calls]
        sample_dps = [_small_call_dp(call) for call in record.calls]
        sample_abs = [_small_call_ab(call) for call in record.calls]
        sample_afs = [call.af for call in record.calls]
        sample_ads = [call.ad for call in record.calls]
        sample_pss = [_small_call_ps(call) for call in record.calls]
        for project_id in normalized_project_ids:
            entry_rows.append(
                (
                    variant_key,
                    variant_id,
                    project_id,
                    family_uuid,
                    "WGS",
                    _xpos(record.chr, record.start),
                    normalize_chromosome(record.chr),
                    int(record.start),
                    record.ref,
                    record.alt,
                    _annotation_gnomad_over_5_percent(record.annotations),
                    bool(gene_symbols),
                    gene_symbols,
                    filters,
                    sample_ids,
                    sample_gts,
                    sample_gqs,
                    sample_dps,
                    sample_abs,
                    sample_afs,
                    sample_ads,
                    sample_pss,
                    1,
                )
            )
    return detail_rows, lookup_rows, entry_rows


def _structural_variant_entry_rows(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[StructuralVariantRecord],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    detail_rows: list[tuple[Any, ...]] = []
    lookup_rows: list[tuple[Any, ...]] = []
    entry_rows: list[tuple[Any, ...]] = []
    normalized_project_ids = _normalized_project_ids(project_ids)
    for record in records:
        variant_id = record.variant_id or build_structural_variant_id(
            record.chr,
            record.start,
            record.end,
            record.sv_type,
            remote_chr=record.remote_chr,
            remote_start=record.remote_start,
            remote_end=record.remote_end,
        )
        variant_key = record.variant_key or structural_variant_key(
            assembly_name,
            family_uuid,
            variant_id,
        )
        gene_symbols = _string_list(record.gene_symbols or _annotation_gene_symbols(record.annotations))
        filters = _string_list(record.filters)
        detail_rows.append(
            (
                variant_key,
                variant_id,
                normalize_chromosome(record.chr),
                int(record.start),
                int(record.end),
                record.sv_type,
                record.source or "",
                normalize_chromosome(record.remote_chr) if record.remote_chr else None,
                None if record.remote_start is None else int(record.remote_start),
                None if record.remote_end is None else int(record.remote_end),
                None if record.sv_len is None else int(record.sv_len),
                filters,
                _json_payload(record.annotations),
            )
        )
        lookup_rows.append((variant_id, variant_key))
        sample_ids = [call.sample for call in record.calls]
        sample_gts = [call.gt for call in record.calls]
        sample_gqs = [_structural_call_gq(call) for call in record.calls]
        sample_quals = [_structural_call_qual(call) for call in record.calls]
        sample_read_supports = [_structural_call_read_support(call) for call in record.calls]
        sample_filters = [call.filter for call in record.calls]
        for project_id in normalized_project_ids:
            entry_rows.append(
                (
                    variant_key,
                    variant_id,
                    project_id,
                    family_uuid,
                    "WGS",
                    normalize_chromosome(record.chr),
                    int(record.start),
                    int(record.end),
                    record.sv_type,
                    record.source or "",
                    gene_symbols,
                    sample_ids,
                    sample_gts,
                    sample_gqs,
                    sample_quals,
                    sample_read_supports,
                    sample_filters,
                    1,
                )
            )
    return detail_rows, lookup_rows, entry_rows


async def _execute(query: str, params: dict[str, Any] | None = None, data: Sequence[tuple[Any, ...]] | None = None) -> Any:
    if data is not None:
        return await execute_clickhouse(query, list(data))
    return await execute_clickhouse(query, params or {})


async def ensure_clickhouse_variant_tables(assembly_name: str) -> None:
    dataset = _require_clickhouse_identifier(assembly_name)
    database = settings.clickhouse_database
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/variants_disk`
        (
            `key` UInt64,
            `variantId` String,
            `chrom` LowCardinality(String),
            `pos` UInt32,
            `ref` String,
            `alt` String,
            `rsid` Nullable(String),
            `annotationDigest` String,
            `annotationsJson` String,
            `source` LowCardinality(String),
            `updatedAt` DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(updatedAt)
        PRIMARY KEY key
        ORDER BY key
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/variants_memory`
        (
            `key` UInt64,
            `variantId` String,
            `annotationDigest` String,
            `annotationsJson` String
        )
        ENGINE = Memory
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/variants/details`
        (
            `key` UInt64,
            `variantId` String,
            `chrom` LowCardinality(String),
            `pos` UInt32,
            `ref` String,
            `alt` String,
            `rsid` Nullable(String),
            `filters` Array(LowCardinality(String)),
            `annotationsJson` String,
            `source` LowCardinality(String),
            `liftedOverChrom` LowCardinality(Nullable(String)),
            `liftedOverPos` Nullable(UInt32),
            `updatedAt` DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(updatedAt)
        PRIMARY KEY key
        ORDER BY key
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/key_lookup`
        (
            `variantId` String,
            `key` UInt64
        )
        ENGINE = ReplacingMergeTree
        PRIMARY KEY variantId
        ORDER BY variantId
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/entries`
        (
            `key` UInt64,
            `variantId` String,
            `project_guid` LowCardinality(String),
            `family_guid` String,
            `sample_type` LowCardinality(String),
            `xpos` UInt64,
            `chrom` LowCardinality(String),
            `pos` UInt32,
            `ref` String,
            `alt` String,
            `is_gnomad_gt_5_percent` Bool DEFAULT false,
            `is_annotated_in_any_gene` Bool DEFAULT false,
            `gene_symbols` Array(String),
            `filters` Array(LowCardinality(String)),
            `calls.sampleId` Array(String),
            `calls.gt` Array(LowCardinality(String)),
            `calls.gq` Array(Nullable(UInt16)),
            `calls.dp` Array(Nullable(UInt16)),
            `calls.ab` Array(Nullable(Float32)),
            `calls.af` Array(Array(Nullable(Float32))),
            `calls.ad` Array(Array(Nullable(UInt16))),
            `calls.ps` Array(Nullable(UInt64)),
            `sign` Int8
        )
        ENGINE = CollapsingMergeTree(sign)
        PARTITION BY project_guid
        ORDER BY (project_guid, family_guid, sample_type, chrom, pos, key)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/project_gt_stats`
        (
            `project_guid` LowCardinality(String),
            `key` UInt64,
            `sample_type` LowCardinality(String),
            `ref_samples` UInt64,
            `het_samples` UInt64,
            `hom_samples` UInt64
        )
        ENGINE = SummingMergeTree
        PARTITION BY project_guid
        ORDER BY (project_guid, key, sample_type)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/gt_stats`
        (
            `key` UInt64,
            `ac_wes` UInt64,
            `ac_wgs` UInt64,
            `hom_wes` UInt64,
            `hom_wgs` UInt64
        )
        ENGINE = SummingMergeTree
        ORDER BY key
        """,
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/entries_to_project_gt_stats_mv`
        TO {database}.`{dataset}/SNV_INDEL/project_gt_stats`
        AS
        SELECT
            project_guid,
            key,
            sample_type,
            countIf(gt IN {tuple(sorted(_SMALL_GT_REF))}) AS ref_samples,
            countIf(gt NOT IN {tuple(sorted(_SMALL_GT_REF | _SMALL_GT_MISSING))} AND gt NOT IN ('1/1', '1|1')) AS het_samples,
            countIf(gt IN ('1/1', '1|1')) AS hom_samples
        FROM {database}.`{dataset}/SNV_INDEL/entries`
        ARRAY JOIN `calls.sampleId` AS sampleId, `calls.gt` AS gt
        GROUP BY project_guid, key, sample_type
        """,
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/project_gt_stats_to_gt_stats_mv`
        TO {database}.`{dataset}/SNV_INDEL/gt_stats`
        AS
        SELECT
            key,
            sumIf((het_samples * 1) + (hom_samples * 2), sample_type = 'WES') AS ac_wes,
            sumIf((het_samples * 1) + (hom_samples * 2), sample_type = 'WGS') AS ac_wgs,
            sumIf(hom_samples, sample_type = 'WES') AS hom_wes,
            sumIf(hom_samples, sample_type = 'WGS') AS hom_wgs
        FROM {database}.`{dataset}/SNV_INDEL/project_gt_stats`
        GROUP BY key
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SV/variants/details`
        (
            `key` UInt64,
            `variantId` String,
            `chrom` LowCardinality(String),
            `start` UInt32,
            `end` UInt32,
            `svType` LowCardinality(String),
            `source` LowCardinality(String),
            `remoteChrom` LowCardinality(Nullable(String)),
            `remoteStart` Nullable(UInt32),
            `remoteEnd` Nullable(UInt32),
            `svLen` Nullable(Int32),
            `filters` Array(LowCardinality(String)),
            `annotationsJson` String,
            `updatedAt` DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(updatedAt)
        PRIMARY KEY key
        ORDER BY key
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SV/key_lookup`
        (
            `variantId` String,
            `key` UInt64
        )
        ENGINE = ReplacingMergeTree
        PRIMARY KEY variantId
        ORDER BY variantId
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SV/entries`
        (
            `key` UInt64,
            `variantId` String,
            `project_guid` LowCardinality(String),
            `family_guid` String,
            `sample_type` LowCardinality(String),
            `chrom` LowCardinality(String),
            `start` UInt32,
            `end` UInt32,
            `svType` LowCardinality(String),
            `source` LowCardinality(String),
            `gene_symbols` Array(String),
            `calls.sampleId` Array(String),
            `calls.gt` Array(LowCardinality(String)),
            `calls.gq` Array(Nullable(UInt16)),
            `calls.qual` Array(Nullable(Float32)),
            `calls.readSupport` Array(Nullable(UInt32)),
            `calls.filter` Array(Nullable(String)),
            `sign` Int8
        )
        ENGINE = CollapsingMergeTree(sign)
        PARTITION BY project_guid
        ORDER BY (project_guid, family_guid, svType, chrom, start, key)
        """,
    ]
    for statement in statements:
        await _execute(statement)


async def delete_family_small_variants(assembly_name: str, family_uuid: str) -> None:
    await ensure_clickhouse_variant_tables(assembly_name)
    await _execute(
        f"ALTER TABLE {_small_table_name(assembly_name, 'entries')} DELETE WHERE family_guid = %(family_guid)s SETTINGS mutations_sync = 1",
        {"family_guid": family_uuid},
    )


async def delete_family_structural_variants(
    assembly_name: str,
    family_uuid: str,
    *,
    source: str | None = None,
) -> None:
    await ensure_clickhouse_variant_tables(assembly_name)
    query = f"ALTER TABLE {_structural_table_name(assembly_name, 'entries')} DELETE WHERE family_guid = %(family_guid)s"
    params: dict[str, Any] = {"family_guid": family_uuid}
    if source is not None:
        query += " AND source = %(source)s"
        params["source"] = source
    query += " SETTINGS mutations_sync = 1"
    await _execute(query, params)


async def insert_small_variant_records(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[SmallVariantRecord],
) -> None:
    await ensure_clickhouse_variant_tables(assembly_name)
    detail_rows, lookup_rows, entry_rows = _small_variant_entry_rows(
        assembly_name,
        family_uuid,
        project_ids,
        records,
    )
    if detail_rows:
        await _execute(
            f"""
            INSERT INTO {_small_table_name(assembly_name, 'variants/details')} (
                key,
                variantId,
                chrom,
                pos,
                ref,
                alt,
                rsid,
                filters,
                annotationsJson,
                source,
                liftedOverChrom,
                liftedOverPos
            ) VALUES
            """,
            data=detail_rows,
        )
    if lookup_rows:
        await _execute(
            f"INSERT INTO {_small_table_name(assembly_name, 'key_lookup')} (variantId, key) VALUES",
            data=lookup_rows,
        )
    if entry_rows:
        await _execute(
            f"""
            INSERT INTO {_small_table_name(assembly_name, 'entries')} (
                key,
                variantId,
                project_guid,
                family_guid,
                sample_type,
                xpos,
                chrom,
                pos,
                ref,
                alt,
                is_gnomad_gt_5_percent,
                is_annotated_in_any_gene,
                gene_symbols,
                filters,
                `calls.sampleId`,
                `calls.gt`,
                `calls.gq`,
                `calls.dp`,
                `calls.ab`,
                `calls.af`,
                `calls.ad`,
                `calls.ps`,
                sign
            ) VALUES
            """,
            data=entry_rows,
        )


async def replace_family_small_variants(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[SmallVariantRecord],
) -> None:
    await delete_family_small_variants(assembly_name, family_uuid)
    if records:
        await insert_small_variant_records(assembly_name, family_uuid, project_ids, records)


async def insert_structural_variant_records(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[StructuralVariantRecord],
) -> None:
    await ensure_clickhouse_variant_tables(assembly_name)
    detail_rows, lookup_rows, entry_rows = _structural_variant_entry_rows(
        assembly_name,
        family_uuid,
        project_ids,
        records,
    )
    if detail_rows:
        await _execute(
            f"""
            INSERT INTO {_structural_table_name(assembly_name, 'variants/details')} (
                key,
                variantId,
                chrom,
                start,
                end,
                svType,
                source,
                remoteChrom,
                remoteStart,
                remoteEnd,
                svLen,
                filters,
                annotationsJson
            ) VALUES
            """,
            data=detail_rows,
        )
    if lookup_rows:
        await _execute(
            f"INSERT INTO {_structural_table_name(assembly_name, 'key_lookup')} (variantId, key) VALUES",
            data=lookup_rows,
        )
    if entry_rows:
        await _execute(
            f"""
            INSERT INTO {_structural_table_name(assembly_name, 'entries')} (
                key,
                variantId,
                project_guid,
                family_guid,
                sample_type,
                chrom,
                start,
                end,
                svType,
                source,
                gene_symbols,
                `calls.sampleId`,
                `calls.gt`,
                `calls.gq`,
                `calls.qual`,
                `calls.readSupport`,
                `calls.filter`,
                sign
            ) VALUES
            """,
            data=entry_rows,
        )


async def replace_family_structural_variants(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[StructuralVariantRecord],
    *,
    source: str | None = None,
) -> None:
    await delete_family_structural_variants(assembly_name, family_uuid, source=source)
    if records:
        await insert_structural_variant_records(assembly_name, family_uuid, project_ids, records)


async def count_family_small_variants(
    assembly_name: str,
    family_uuid: str,
    *,
    project_ids: Sequence[str] | None = None,
) -> int:
    await ensure_clickhouse_variant_tables(assembly_name)
    clauses = ["family_guid = %(family_guid)s", "sign = 1"]
    params: dict[str, Any] = {"family_guid": family_uuid}
    if project_ids:
        clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(_normalized_project_ids(project_ids))
    rows = await _execute(
        f"""
        SELECT count()
        FROM (
            SELECT key
            FROM {_small_table_name(assembly_name, 'entries')}
            WHERE {' AND '.join(clauses)}
            GROUP BY key
        )
        """,
        params,
    )
    return int(rows[0][0]) if rows else 0


async def count_family_structural_variants(
    assembly_name: str,
    family_uuid: str,
    *,
    project_ids: Sequence[str] | None = None,
    source: str | None = None,
) -> int:
    await ensure_clickhouse_variant_tables(assembly_name)
    clauses = ["family_guid = %(family_guid)s", "sign = 1"]
    params: dict[str, Any] = {"family_guid": family_uuid}
    if project_ids:
        clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(_normalized_project_ids(project_ids))
    if source is not None:
        clauses.append("source = %(source)s")
        params["source"] = source
    rows = await _execute(
        f"""
        SELECT count()
        FROM (
            SELECT key
            FROM {_structural_table_name(assembly_name, 'entries')}
            WHERE {' AND '.join(clauses)}
            GROUP BY key
        )
        """,
        params,
    )
    return int(rows[0][0]) if rows else 0


async def count_family_structural_variants_by_sample(
    assembly_name: str,
    family_uuid: str,
    *,
    sample_ids: Sequence[str],
    project_ids: Sequence[str] | None = None,
    source: str | None = None,
) -> dict[str, int]:
    deduped_sample_ids = list(dict.fromkeys(str(sample_id).strip() for sample_id in sample_ids if str(sample_id).strip()))
    if not deduped_sample_ids:
        return {}
    await ensure_clickhouse_variant_tables(assembly_name)
    clauses = [
        "family_guid = %(family_guid)s",
        "sign = 1",
        "sampleId IN %(sample_ids)s",
    ]
    params: dict[str, Any] = {
        "family_guid": family_uuid,
        "sample_ids": tuple(deduped_sample_ids),
    }
    if project_ids:
        clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(_normalized_project_ids(project_ids))
    if source is not None:
        clauses.append("source = %(source)s")
        params["source"] = source
    rows = await _execute(
        f"""
        SELECT sampleId, countDistinct(key)
        FROM {_structural_table_name(assembly_name, 'entries')}
        ARRAY JOIN `calls.sampleId` AS sampleId
        WHERE {' AND '.join(clauses)}
        GROUP BY sampleId
        """,
        params,
    )
    return {str(sample_id): int(count) for sample_id, count in rows}


async def list_clickhouse_variant_assemblies() -> list[str]:
    rows = await _execute(
        """
        SELECT name
        FROM system.tables
        WHERE database = %(database)s
          AND (name LIKE %(small_pattern)s OR name LIKE %(structural_pattern)s)
        """,
        {
            "database": settings.clickhouse_database,
            "small_pattern": "%/SNV_INDEL/%",
            "structural_pattern": "%/SV/%",
        },
    )
    assemblies = {
        table_name.split("/", 1)[0]
        for (table_name,) in rows
        if isinstance(table_name, str) and "/" in table_name
    }
    return sorted(assemblies)


async def get_clickhouse_variant_storage_status(assembly_name: str) -> dict[str, Any]:
    dataset = _require_clickhouse_identifier(assembly_name)
    expected_tables = _expected_clickhouse_variant_tables(dataset)
    table_names = tuple(table_name for _variant_type, _kind, table_name in expected_tables)
    params = {
        "database": settings.clickhouse_database,
        "table_names": table_names,
    }
    table_rows = await _execute(
        """
        SELECT name, engine
        FROM system.tables
        WHERE database = %(database)s
          AND name IN %(table_names)s
        """,
        params,
    )
    part_rows = await _execute(
        """
        SELECT table, sum(rows) AS row_count, sum(bytes_on_disk) AS bytes_on_disk
        FROM system.parts
        WHERE active
          AND database = %(database)s
          AND table IN %(table_names)s
        GROUP BY table
        """,
        params,
    )
    mutation_rows = await _execute(
        """
        SELECT table, countIf(NOT is_done) AS pending_mutations
        FROM system.mutations
        WHERE database = %(database)s
          AND table IN %(table_names)s
        GROUP BY table
        """,
        params,
    )

    table_engines = {str(name): str(engine) for name, engine in table_rows}
    table_metrics = {
        str(name): {
            "row_count": int(row_count or 0),
            "bytes_on_disk": int(bytes_on_disk or 0),
        }
        for name, row_count, bytes_on_disk in part_rows
    }
    table_mutations = {
        str(name): int(pending_mutations or 0) for name, pending_mutations in mutation_rows
    }

    tables: list[dict[str, Any]] = []
    missing_tables: list[str] = []
    total_rows = 0
    total_bytes_on_disk = 0
    pending_mutations = 0
    small_variant_rows = 0
    structural_variant_rows = 0

    for variant_type, kind, table_name in expected_tables:
        exists = table_name in table_engines
        metrics = table_metrics.get(table_name, {})
        row_count = int(metrics.get("row_count") or 0)
        bytes_on_disk = int(metrics.get("bytes_on_disk") or 0)
        table_pending_mutations = int(table_mutations.get(table_name) or 0)
        if not exists:
            missing_tables.append(table_name)
        total_rows += row_count
        total_bytes_on_disk += bytes_on_disk
        pending_mutations += table_pending_mutations
        if table_name == f"{dataset}/SNV_INDEL/entries":
            small_variant_rows = row_count
        elif table_name == f"{dataset}/SV/entries":
            structural_variant_rows = row_count
        tables.append(
            {
                "name": table_name,
                "variant_type": variant_type,
                "kind": kind,
                "exists": exists,
                "engine": table_engines.get(table_name),
                "row_count": row_count,
                "bytes_on_disk": bytes_on_disk,
                "pending_mutations": table_pending_mutations,
            }
        )

    health = "missing"
    if not missing_tables:
        health = "mutating" if pending_mutations else "ready"

    return {
        "assembly_name": dataset,
        "health": health,
        "expected_table_count": len(expected_tables),
        "existing_table_count": len(expected_tables) - len(missing_tables),
        "missing_tables": missing_tables,
        "pending_mutations": pending_mutations,
        "total_rows": total_rows,
        "total_bytes_on_disk": total_bytes_on_disk,
        "small_variant_rows": small_variant_rows,
        "structural_variant_rows": structural_variant_rows,
        "tables": tables,
    }


async def ensure_clickhouse_variant_storage_ready(assembly_name: str) -> dict[str, Any]:
    await ensure_clickhouse_variant_tables(assembly_name)
    return await get_clickhouse_variant_storage_status(assembly_name)


async def optimize_clickhouse_variant_tables(
    assembly_name: str,
    *,
    final: bool = False,
) -> dict[str, Any]:
    dataset = _require_clickhouse_identifier(assembly_name)
    await ensure_clickhouse_variant_tables(dataset)
    optimize_targets = [
        table_name
        for _variant_type, kind, table_name in _expected_clickhouse_variant_tables(dataset)
        if kind == "table" and not table_name.endswith("/variants_memory")
    ]
    for table_name in optimize_targets:
        query = f"OPTIMIZE TABLE {settings.clickhouse_database}.`{table_name}`"
        if final:
            query += " FINAL"
        await _execute(query)
    return await get_clickhouse_variant_storage_status(dataset)
