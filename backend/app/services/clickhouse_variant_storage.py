from __future__ import annotations

import asyncio
from hashlib import blake2b
import json
from typing import Any, Iterable, Sequence

from ..core.clickhouse import clickhouse_dataset_key, execute_clickhouse
from ..core.config import settings
from .clickhouse_family_variants import (
    IMPUTED_SMALL_VARIANT_SOURCES,
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
    _annotation_bool,
    _annotation_clinvar,
    _annotation_effect,
    _annotation_float,
    _annotation_gene,
    _annotation_gene_id,
    _annotation_int,
    _annotation_population_frequencies,
    _annotation_spliceai_max,
    _annotation_terms,
    _annotation_text,
    _casefold,
    _status_terms,
)
from .data_scope import normalize_chromosome

_DEFAULT_SMALL_ANNOTATION_VERSION = "current"
_SMALL_GT_REF = {"0/0", "0|0"}
_SMALL_GT_MISSING = {"./.", ".|.", "", "."}
_SMALL_VARIANT_DETAIL_INSERT_ROWS = 1_000
_SMALL_VARIANT_ENTRY_INSERT_ROWS = 2_000
_SMALL_VARIANT_ANNOTATION_INSERT_ROWS = 5_000
_SMALL_VARIANT_INDEX_INSERT_ROWS = 2_000
_SMALL_VARIANT_GENE_INDEX_INSERT_ROWS = 10_000
_ensured_variant_table_assemblies: set[str] = set()
_ensure_variant_tables_lock = asyncio.Lock()


def _require_clickhouse_identifier(value: str) -> str:
    # Normalize any assembly name to a ClickHouse-safe dataset key (shared with
    # the read paths) so e.g. "T2T CHM13v2.0" ingests and queries automatically.
    return clickhouse_dataset_key(value)


def _small_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SNV_INDEL/{suffix}`"


def _structural_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SV/{suffix}`"


def _small_genotype_tuple(values: Iterable[str]) -> str:
    return "(" + ", ".join(repr(value) for value in values) + ")"


def _expected_clickhouse_variant_tables(assembly_name: str) -> list[tuple[str, str, str]]:
    dataset = _require_clickhouse_identifier(assembly_name)
    return [
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants/details"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants/annotations"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants/annotation_index"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants/gene_index"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/entries"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/family_variant_summary"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/family_sample_variant_summary"),
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


def small_variant_key(assembly_name: str, variant_id: str) -> int:
    return _stable_uint64("small", assembly_name, variant_id)


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


def _small_annotation_version(value: Any = None) -> str:
    version = str(value or "").strip()
    if version:
        return version
    return _DEFAULT_SMALL_ANNOTATION_VERSION


def _annotation_payload_hash(annotation: dict[str, Any]) -> int:
    payload = json.dumps(annotation, sort_keys=True, separators=(",", ":"), default=str)
    return _stable_uint64("small_annotation", payload)


def _annotation_set_hash(annotations: Sequence[dict[str, Any]]) -> int:
    payload = json.dumps(list(annotations or []), sort_keys=True, separators=(",", ":"), default=str)
    return _stable_uint64("small_annotation_set", payload)


def _max_or_none(values: Iterable[float | int | None]) -> Any:
    candidates = [value for value in values if value is not None]
    return max(candidates) if candidates else None


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


def _population_float(annotation: dict[str, Any], *keys: str) -> float | None:
    values = [_annotation_float(annotation, *keys)]
    population_frequencies = _annotation_population_frequencies(annotation)
    for key in keys:
        values.append(population_frequencies.get(key))
    # Highest frequency across the direct annotation value and all population
    # fallbacks (conservative for rare-variant filtering); _max_or_none ignores Nones.
    return _max_or_none(values)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _small_annotation_row(
    *,
    variant_key: int,
    variant_id: str,
    annotation_version: str,
    annotation_set_hash: int,
    record: SmallVariantRecord,
    annotation: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        variant_key,
        variant_id,
        annotation_version,
        annotation_set_hash,
        _annotation_payload_hash(annotation),
        normalize_chromosome(record.chr),
        int(record.start),
        record.ref,
        record.alt,
        _casefold(_annotation_gene(annotation)),
        _casefold(_annotation_gene_id(annotation)),
        _clean_text(_annotation_text(annotation, "transcript_id", "transcriptId")),
        _clean_text(_annotation_text(annotation, "hgvsc")),
        _clean_text(_annotation_text(annotation, "hgvsp")),
        _casefold(_annotation_text(annotation, "impact")),
        sorted(_annotation_terms(_annotation_effect(annotation))),
        sorted(_status_terms(_annotation_clinvar(annotation))),
        _annotation_bool(annotation, "canonical"),
        _annotation_bool(annotation, "mane_select", "maneSelect"),
        _annotation_bool(annotation, "mane_plus_clinical", "manePlusClinical"),
        _casefold(_annotation_text(annotation, "lof") or ""),
        _population_float(annotation, "gnomad_af", "gnomadAf"),
        _population_float(annotation, "gnomad_exomes_af", "gnomadExomesAf"),
        _population_float(annotation, "gnomad_genomes_af", "gnomadGenomesAf"),
        _population_float(annotation, "gnomad_popmax_af", "gnomadPopmaxAf"),
        _population_float(annotation, "topmed_af", "topmedAf"),
        _annotation_int(annotation, "gnomad_ac"),
        _annotation_int(annotation, "gnomad_hom_count", "gnomadHomCount"),
        _annotation_int(annotation, "gnomad_hemi_count"),
        _annotation_float(annotation, "cadd_phred", "caddPhred"),
        _annotation_float(annotation, "revel"),
        _annotation_spliceai_max(annotation),
        _casefold(_annotation_text(annotation, "sift", "siftPrediction")),
        _casefold(_annotation_text(annotation, "polyphen", "polyphenPrediction")),
    )


def _small_annotation_index_row(
    *,
    variant_key: int,
    variant_id: str,
    annotation_version: str,
    annotation_set_hash: int,
    record: SmallVariantRecord,
    annotations: Sequence[dict[str, Any]],
) -> tuple[Any, ...]:
    annotation_list = [annotation for annotation in annotations if isinstance(annotation, dict)]
    gene_symbols = _string_list(_casefold(_annotation_gene(annotation)) for annotation in annotation_list)
    gene_ids = _string_list(_casefold(_annotation_gene_id(annotation)) for annotation in annotation_list)
    transcript_ids = _string_list(
        _clean_text(_annotation_text(annotation, "transcript_id", "transcriptId"))
        for annotation in annotation_list
    )
    hgvsc_values = _string_list(
        _clean_text(_annotation_text(annotation, "hgvsc"))
        for annotation in annotation_list
    )
    hgvsp_values = _string_list(
        _clean_text(_annotation_text(annotation, "hgvsp"))
        for annotation in annotation_list
    )
    impacts = _string_list(
        _casefold(_annotation_text(annotation, "impact"))
        for annotation in annotation_list
    )
    effects = _string_list(
        term
        for annotation in annotation_list
        for term in _annotation_terms(_annotation_effect(annotation))
    )
    clinvar_terms = _string_list(
        term
        for annotation in annotation_list
        for term in _status_terms(_annotation_clinvar(annotation))
    )
    sift_terms = _string_list(
        _casefold(_annotation_text(annotation, "sift", "siftPrediction"))
        for annotation in annotation_list
    )
    polyphen_terms = _string_list(
        _casefold(_annotation_text(annotation, "polyphen", "polyphenPrediction"))
        for annotation in annotation_list
    )
    lof_terms = {
        _casefold(_annotation_text(annotation, "lof") or "")
        for annotation in annotation_list
    }
    return (
        variant_key,
        variant_id,
        annotation_version,
        annotation_set_hash,
        normalize_chromosome(record.chr),
        int(record.start),
        record.ref,
        record.alt,
        record.rsid or _annotation_rsid(annotation_list),
        gene_symbols,
        gene_ids,
        transcript_ids,
        hgvsc_values,
        hgvsp_values,
        impacts,
        effects,
        clinvar_terms,
        any(_annotation_bool(annotation, "canonical") for annotation in annotation_list),
        any(_annotation_bool(annotation, "mane_select", "maneSelect") for annotation in annotation_list),
        any(_annotation_bool(annotation, "mane_plus_clinical", "manePlusClinical") for annotation in annotation_list),
        any(term not in {"", ".", "na", "n/a"} for term in lof_terms),
        _max_or_none(_population_float(annotation, "gnomad_af", "gnomadAf") for annotation in annotation_list),
        _max_or_none(_population_float(annotation, "gnomad_exomes_af", "gnomadExomesAf") for annotation in annotation_list),
        _max_or_none(_population_float(annotation, "gnomad_genomes_af", "gnomadGenomesAf") for annotation in annotation_list),
        _max_or_none(_population_float(annotation, "gnomad_popmax_af", "gnomadPopmaxAf") for annotation in annotation_list),
        _max_or_none(_population_float(annotation, "topmed_af", "topmedAf") for annotation in annotation_list),
        _max_or_none(_annotation_int(annotation, "gnomad_ac") for annotation in annotation_list),
        _max_or_none(_annotation_int(annotation, "gnomad_hom_count", "gnomadHomCount") for annotation in annotation_list),
        _max_or_none(_annotation_int(annotation, "gnomad_hemi_count") for annotation in annotation_list),
        _max_or_none(_annotation_float(annotation, "cadd_phred", "caddPhred") for annotation in annotation_list),
        _max_or_none(_annotation_float(annotation, "revel") for annotation in annotation_list),
        _max_or_none(_annotation_spliceai_max(annotation) for annotation in annotation_list),
        sift_terms,
        polyphen_terms,
    )


def _small_annotation_gene_index_rows(
    *,
    variant_key: int,
    variant_id: str,
    annotation_version: str,
    annotation_set_hash: int,
    record: SmallVariantRecord,
    annotations: Sequence[dict[str, Any]],
) -> list[tuple[Any, ...]]:
    annotation_list = [annotation for annotation in annotations if isinstance(annotation, dict)]
    gene_terms = _string_list(
        [
            *(_casefold(symbol) for symbol in record.gene_symbols),
            *(_casefold(_annotation_gene(annotation)) for annotation in annotation_list),
            *(_casefold(_annotation_gene_id(annotation)) for annotation in annotation_list),
        ]
    )
    chrom = normalize_chromosome(record.chr)
    pos = int(record.start)
    return [
        (
            variant_key,
            variant_id,
            annotation_version,
            annotation_set_hash,
            gene_term,
            chrom,
            pos,
            record.ref,
            record.alt,
        )
        for gene_term in gene_terms
    ]


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


def _structural_call_qual(call: StructuralVariantCall) -> float | None:
    return None if call.qual is None else float(call.qual)


def _structural_call_read_support(call: StructuralVariantCall) -> int | None:
    return None if call.read_support is None else int(call.read_support)


def _small_variant_entry_rows(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[SmallVariantRecord],
    *,
    annotation_version: str | None = None,
) -> tuple[
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
]:
    detail_rows: list[tuple[Any, ...]] = []
    entry_rows: list[tuple[Any, ...]] = []
    annotation_rows: list[tuple[Any, ...]] = []
    annotation_index_rows: list[tuple[Any, ...]] = []
    annotation_gene_index_rows: list[tuple[Any, ...]] = []
    normalized_project_ids = _normalized_project_ids(project_ids)
    active_annotation_version = _small_annotation_version(annotation_version)
    for record in records:
        variant_id = record.variant_id or build_small_variant_id(
            record.chr,
            record.start,
            record.ref,
            record.alt,
        )
        variant_key = record.variant_key or small_variant_key(assembly_name, variant_id)
        record_annotation_version = active_annotation_version
        record_annotation_set_hash = _annotation_set_hash(record.annotations)
        gene_symbols = _string_list(record.gene_symbols or _annotation_gene_symbols(record.annotations))
        rsid = record.rsid or _annotation_rsid(record.annotations)
        filters = _string_list(record.filters)
        annotations_json = _json_payload(record.annotations)
        detail_rows.append(
            (
                variant_key,
                variant_id,
                record_annotation_version,
                record_annotation_set_hash,
                normalize_chromosome(record.chr),
                int(record.start),
                record.ref,
                record.alt,
                rsid,
                annotations_json,
                None,
                None,
            )
        )
        annotation_index_rows.append(
            _small_annotation_index_row(
                variant_key=variant_key,
                variant_id=variant_id,
                annotation_version=record_annotation_version,
                annotation_set_hash=record_annotation_set_hash,
                record=record,
                annotations=record.annotations,
            )
        )
        annotation_gene_index_rows.extend(
            _small_annotation_gene_index_rows(
                variant_key=variant_key,
                variant_id=variant_id,
                annotation_version=record_annotation_version,
                annotation_set_hash=record_annotation_set_hash,
                record=record,
                annotations=record.annotations,
            )
        )
        for annotation in record.annotations or [{}]:
            annotation_rows.append(
                _small_annotation_row(
                    variant_key=variant_key,
                    variant_id=variant_id,
                    annotation_version=record_annotation_version,
                    annotation_set_hash=record_annotation_set_hash,
                    record=record,
                    annotation=annotation,
                )
            )
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
                    record_annotation_version,
                    record_annotation_set_hash,
                    project_id,
                    family_uuid,
                    "WGS",
                    _xpos(record.chr, record.start),
                    normalize_chromosome(record.chr),
                    int(record.start),
                    record.ref,
                    record.alt,
                    record.source or "",
                    rsid,
                    _annotation_gnomad_over_5_percent(record.annotations),
                    bool(gene_symbols),
                    gene_symbols,
                    filters,
                    record.qual,
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
    return detail_rows, entry_rows, annotation_rows, annotation_index_rows, annotation_gene_index_rows


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
                family_uuid,
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
        lookup_rows.append((family_uuid, variant_id, variant_key))
        sample_ids = [call.sample for call in record.calls]
        sample_gts = [call.gt for call in record.calls]
        sample_quals = [_structural_call_qual(call) for call in record.calls]
        sample_read_supports = [_structural_call_read_support(call) for call in record.calls]
        sample_filters = [call.filter for call in record.calls]
        sample_phase_sets = [
            None if call.phase_set is None else int(call.phase_set) for call in record.calls
        ]
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
                    sample_quals,
                    sample_read_supports,
                    sample_filters,
                    sample_phase_sets,
                    1,
                )
            )
    return detail_rows, lookup_rows, entry_rows


async def _execute(query: str, params: dict[str, Any] | None = None, data: Sequence[tuple[Any, ...]] | None = None) -> Any:
    if data is not None:
        if not data:
            return None
        return await execute_clickhouse(query, list(data))
    return await execute_clickhouse(query, params or {})


def _row_chunks(rows: Sequence[tuple[Any, ...]], size: int) -> Iterable[Sequence[tuple[Any, ...]]]:
    if size <= 0:
        raise ValueError("ClickHouse insert chunk size must be positive")
    for offset in range(0, len(rows), size):
        yield rows[offset : offset + size]


async def _execute_insert_chunks(
    query: str,
    rows: Sequence[tuple[Any, ...]],
    *,
    chunk_size: int,
) -> None:
    for chunk in _row_chunks(rows, chunk_size):
        await _execute(query, data=chunk)


async def ensure_clickhouse_variant_tables(assembly_name: str) -> None:
    dataset = _require_clickhouse_identifier(assembly_name)
    if dataset in _ensured_variant_table_assemblies:
        return
    database = settings.clickhouse_database
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/variants/details`
        (
            `key` UInt64,
            `variantId` String,
            `annotation_version` LowCardinality(String),
            `annotationSetHash` UInt64,
            `chrom` LowCardinality(String),
            `pos` UInt32,
            `ref` String,
            `alt` String,
            `rsid` Nullable(String),
            `annotationsJson` String,
            `liftedOverChrom` LowCardinality(Nullable(String)),
            `liftedOverPos` Nullable(UInt32),
            `updatedAt` DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(updatedAt)
        PRIMARY KEY (key, annotation_version, annotationSetHash)
        ORDER BY (key, annotation_version, annotationSetHash)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/variants/annotations`
        (
            `key` UInt64,
            `variantId` String,
            `annotation_version` LowCardinality(String),
            `annotationSetHash` UInt64,
            `annotationHash` UInt64,
            `chrom` LowCardinality(String),
            `pos` UInt32,
            `ref` String,
            `alt` String,
            `gene_symbol` String,
            `gene_id` String,
            `transcript_id` String,
            `hgvsc` String,
            `hgvsp` String,
            `impact` LowCardinality(String),
            `effects` Array(String),
            `clinvar_terms` Array(String),
            `canonical` Bool,
            `mane_select` Bool,
            `mane_plus_clinical` Bool,
            `lof` LowCardinality(String),
            `gnomad_af` Nullable(Float32),
            `gnomad_exomes_af` Nullable(Float32),
            `gnomad_genomes_af` Nullable(Float32),
            `gnomad_popmax_af` Nullable(Float32),
            `topmed_af` Nullable(Float32),
            `gnomad_ac` Nullable(UInt32),
            `gnomad_hom_count` Nullable(UInt32),
            `gnomad_hemi_count` Nullable(UInt32),
            `cadd_phred` Nullable(Float32),
            `revel` Nullable(Float32),
            `spliceai_max` Nullable(Float32),
            `sift` LowCardinality(String),
            `polyphen` LowCardinality(String),
            `updatedAt` DateTime DEFAULT now(),
            INDEX idx_ann_key key TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_ann_gene gene_symbol TYPE set(1000) GRANULARITY 4,
            INDEX idx_ann_gene_id gene_id TYPE set(1000) GRANULARITY 4,
            INDEX idx_ann_impact impact TYPE set(64) GRANULARITY 4,
            INDEX idx_ann_effects effects TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_ann_clinvar clinvar_terms TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_ann_gnomad_af ifNull(gnomad_af, 0) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_gnomad_exomes_af ifNull(gnomad_exomes_af, 0) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_gnomad_genomes_af ifNull(gnomad_genomes_af, 0) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_gnomad_popmax_af ifNull(gnomad_popmax_af, 0) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_topmed_af ifNull(topmed_af, 0) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_cadd ifNull(cadd_phred, -1) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_revel ifNull(revel, -1) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_spliceai ifNull(spliceai_max, -1) TYPE minmax GRANULARITY 4
        )
        ENGINE = ReplacingMergeTree(updatedAt)
        PARTITION BY annotation_version
        PRIMARY KEY (annotation_version, chrom, pos, key, annotationSetHash)
        ORDER BY (annotation_version, chrom, pos, key, annotationSetHash, annotationHash)
        """,
        f"""
        ALTER TABLE {database}.`{dataset}/SNV_INDEL/variants/annotations`
        DROP COLUMN IF EXISTS annotation_json
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/variants/annotation_index`
        (
            `key` UInt64,
            `variantId` String,
            `annotation_version` LowCardinality(String),
            `annotationSetHash` UInt64,
            `chrom` LowCardinality(String),
            `pos` UInt32,
            `ref` String,
            `alt` String,
            `rsid` Nullable(String),
            `gene_symbols` Array(String),
            `gene_ids` Array(String),
            `transcript_ids` Array(String),
            `hgvsc_values` Array(String),
            `hgvsp_values` Array(String),
            `impacts` Array(String),
            `effects` Array(String),
            `clinvar_terms` Array(String),
            `has_canonical` Bool,
            `has_mane_select` Bool,
            `has_mane_plus_clinical` Bool,
            `has_lof` Bool,
            `max_gnomad_af` Nullable(Float32),
            `max_gnomad_exomes_af` Nullable(Float32),
            `max_gnomad_genomes_af` Nullable(Float32),
            `max_gnomad_popmax_af` Nullable(Float32),
            `max_topmed_af` Nullable(Float32),
            `max_gnomad_ac` Nullable(UInt32),
            `max_gnomad_hom_count` Nullable(UInt32),
            `max_gnomad_hemi_count` Nullable(UInt32),
            `max_cadd_phred` Nullable(Float32),
            `max_revel` Nullable(Float32),
            `max_spliceai` Nullable(Float32),
            `sift_terms` Array(String),
            `polyphen_terms` Array(String),
            `updatedAt` DateTime DEFAULT now(),
            INDEX idx_ann_idx_key key TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_ann_idx_gene gene_symbols TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_ann_idx_gene_id gene_ids TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_ann_idx_impact impacts TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_ann_idx_effect effects TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_ann_idx_clinvar clinvar_terms TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_ann_idx_gnomad ifNull(max_gnomad_af, 0) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_idx_gnomad_popmax ifNull(max_gnomad_popmax_af, 0) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_idx_cadd ifNull(max_cadd_phred, -1) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_idx_revel ifNull(max_revel, -1) TYPE minmax GRANULARITY 4,
            INDEX idx_ann_idx_spliceai ifNull(max_spliceai, -1) TYPE minmax GRANULARITY 4
        )
        ENGINE = ReplacingMergeTree(updatedAt)
        PARTITION BY annotation_version
        PRIMARY KEY (annotation_version, chrom, pos, key, annotationSetHash)
        ORDER BY (annotation_version, chrom, pos, key, annotationSetHash)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/variants/gene_index`
        (
            `key` UInt64,
            `variantId` String,
            `annotation_version` LowCardinality(String),
            `annotationSetHash` UInt64,
            `gene_term` String,
            `chrom` LowCardinality(String),
            `pos` UInt32,
            `ref` String,
            `alt` String,
            `updatedAt` DateTime DEFAULT now(),
            INDEX idx_gene_index_key key TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_gene_index_chrom chrom TYPE set(128) GRANULARITY 4
        )
        ENGINE = ReplacingMergeTree(updatedAt)
        PARTITION BY annotation_version
        PRIMARY KEY (annotation_version, gene_term, chrom, pos, key, annotationSetHash)
        ORDER BY (annotation_version, gene_term, chrom, pos, key, annotationSetHash)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/entries`
        (
            `key` UInt64,
            `variantId` String,
            `annotation_version` LowCardinality(String),
            `annotationSetHash` UInt64,
            `project_guid` LowCardinality(String),
            `family_guid` String,
            `sample_type` LowCardinality(String),
            `xpos` UInt64,
            `chrom` LowCardinality(String),
            `pos` UInt32,
            `ref` String,
            `alt` String,
            `source` LowCardinality(String),
            `rsid` Nullable(String),
            `is_gnomad_gt_5_percent` Bool DEFAULT false,
            `is_annotated_in_any_gene` Bool DEFAULT false,
            `gene_symbols` Array(String),
            `filters` Array(LowCardinality(String)),
            `qual` Nullable(Float32),
            `calls.sampleId` Array(String),
            `calls.gt` Array(LowCardinality(String)),
            `calls.gq` Array(Nullable(UInt16)),
            `calls.dp` Array(Nullable(UInt16)),
            `calls.ab` Array(Nullable(Float32)),
            `calls.af` Array(Array(Nullable(Float32))),
            `calls.ad` Array(Array(Nullable(UInt16))),
            `calls.ps` Array(Nullable(UInt64)),
            `sign` Int8,
            INDEX idx_entry_variant variantId TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_entry_annotation_version annotation_version TYPE set(32) GRANULARITY 4,
            INDEX idx_entry_annotation_set annotationSetHash TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_entry_source source TYPE set(64) GRANULARITY 4,
            INDEX idx_entry_rsid ifNull(rsid, '') TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_entry_gene_symbols gene_symbols TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_entry_sample_ids `calls.sampleId` TYPE bloom_filter(0.01) GRANULARITY 4
        )
        ENGINE = CollapsingMergeTree(sign)
        PARTITION BY project_guid
        ORDER BY (project_guid, family_guid, xpos, key)
        """,
        f"""
        ALTER TABLE {database}.`{dataset}/SNV_INDEL/entries`
        ADD COLUMN IF NOT EXISTS `qual` Nullable(Float32) AFTER filters
        """,
        # NOTE: the `project_gt_stats` / `gt_stats` SummingMergeTree tables and the
        # two materialized views that fed them (entries -> project_gt_stats ->
        # gt_stats) used to be created here. Nothing ever read them (the Small
        # Variant Explorer aggregates from `entries` directly), and their MVs
        # ignored the CollapsingMergeTree `sign` so re-imports inflated them. They
        # are dropped from existing databases by _drop_legacy_gt_stats_aggregates().
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/family_variant_summary`
        (
            `family_guid` String,
            `project_guid` LowCardinality(String),
            `total_variants` UInt64,
            `snv_count` UInt64,
            `indel_count` UInt64,
            `updated_at` DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(updated_at)
        PRIMARY KEY (family_guid, project_guid)
        ORDER BY (family_guid, project_guid)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/family_sample_variant_summary`
        (
            `family_guid` String,
            `project_guid` LowCardinality(String),
            `sample_id` String,
            `non_ref_count` UInt64,
            `het_count` UInt64,
            `hom_alt_count` UInt64,
            `updated_at` DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(updated_at)
        PRIMARY KEY (family_guid, project_guid, sample_id)
        ORDER BY (family_guid, project_guid, sample_id)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SV/variants/details`
        (
            `key` UInt64,
            `variantId` String,
            `family_guid` String,
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
            `family_guid` String,
            `variantId` String,
            `key` UInt64
        )
        ENGINE = ReplacingMergeTree
        PRIMARY KEY (family_guid, variantId)
        ORDER BY (family_guid, variantId)
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
            `calls.ps` Array(Nullable(UInt64)),
            `sign` Int8
        )
        ENGINE = CollapsingMergeTree(sign)
        PARTITION BY project_guid
        ORDER BY (project_guid, family_guid, svType, chrom, start, key)
        """,
        f"""
        ALTER TABLE {database}.`{dataset}/SV/entries`
        ADD COLUMN IF NOT EXISTS `calls.ps` Array(Nullable(UInt64)) AFTER `calls.filter`
        """,
    ]
    async with _ensure_variant_tables_lock:
        if dataset in _ensured_variant_table_assemblies:
            return
        await _migrate_legacy_family_sample_variant_summary(database, dataset)
        await _drop_legacy_gt_stats_aggregates(database, dataset)
        for statement in statements:
            await _execute(statement)
        _ensured_variant_table_assemblies.add(dataset)


async def _migrate_legacy_family_sample_variant_summary(database: str, dataset: str) -> None:
    """Drop the pre-``project_guid`` ``family_sample_variant_summary`` so it is recreated.

    The per-sample summary was originally keyed ``(family_guid, sample_id)`` with no
    ``project_guid`` column, so per-sample counts aggregated across every project a family
    belonged to and leaked cross-project counts to project-scoped users. ``project_guid``
    must live in the ReplacingMergeTree sort key (otherwise a family's per-project rows
    collapse into one), and ClickHouse cannot insert a column mid-key in place, so the
    legacy table is dropped and recreated by the ``CREATE TABLE IF NOT EXISTS`` that runs
    afterwards. The summary is a cache: the read path falls back to a project-scoped live
    query against ``entries`` until each family is re-refreshed, so no counts are lost.
    """
    table = f"{dataset}/SNV_INDEL/family_sample_variant_summary"
    rows = await _execute(
        """
        SELECT countIf(name = 'project_guid')
        FROM system.columns
        WHERE database = %(database)s AND table = %(table)s
        """,
        {"database": database, "table": table},
    )
    has_project_guid = bool(rows and rows[0] and int(rows[0][0] or 0) > 0)
    if has_project_guid:
        return
    # No-op when the table does not exist yet; drops the legacy table when present.
    await _execute(f"DROP TABLE IF EXISTS {database}.`{table}` SYNC")


async def _drop_legacy_gt_stats_aggregates(database: str, dataset: str) -> None:
    """Drop the never-read ``project_gt_stats`` / ``gt_stats`` aggregate cascade.

    Two ``SummingMergeTree`` tables and the two materialized views feeding them
    (``entries`` -> ``project_gt_stats`` -> ``gt_stats``) pre-aggregated cohort
    genotype counts, but nothing read them: the Small Variant Explorer aggregates
    from ``entries`` directly (see ``variant_explorer_service``), and the MVs
    ignored the ``entries`` CollapsingMergeTree ``sign`` column so re-imports and
    deletes silently inflated them. They only added per-insert work and a
    startup-fragility footgun -- the cascade's many tiny SummingMergeTree parts
    piled up, and a single unclean shutdown truncated enough of them to trip
    ``max_suspicious_broken_parts`` and block the table (and any ``system.tables``
    scan of the database) from attaching on the next boot.

    Drop the two views first so inserts into ``entries`` stop fanning out, then the
    target tables. ``IF EXISTS`` makes this a no-op on databases that never had
    them (fresh installs) or that were already migrated.
    """
    for suffix in (
        # Views first: stop the insert-time fan-out before the targets disappear.
        "entries_to_project_gt_stats_mv",
        "project_gt_stats_to_gt_stats_mv",
        "gt_stats",
        "project_gt_stats",
    ):
        await _execute(
            f"DROP TABLE IF EXISTS {database}.`{dataset}/SNV_INDEL/{suffix}` SYNC"
        )


async def delete_family_small_variants(
    assembly_name: str,
    family_uuid: str,
    *,
    source: str | None = None,
) -> None:
    await ensure_clickhouse_variant_tables(assembly_name)
    params: dict[str, Any] = {"family_guid": family_uuid}
    if source is not None:
        # Source-scoped delete: only clear this callset's entries (e.g. re-importing
        # the glimpse2 dataset must not wipe the clair3 annotated SNVs, and vice
        # versa). The summary tables have no source column and are rebuilt from the
        # surviving entries by refresh_family_small_variant_summaries, so the caller
        # refreshes them after re-inserting rather than blanket-clearing them here.
        params["source"] = source
        await _execute(
            f"ALTER TABLE {_small_table_name(assembly_name, 'entries')} DELETE "
            "WHERE family_guid = %(family_guid)s AND source = %(source)s "
            "SETTINGS mutations_sync = 1",
            params,
        )
        return
    for suffix in (
        "entries",
        "family_variant_summary",
        "family_sample_variant_summary",
    ):
        await _execute(
            f"ALTER TABLE {_small_table_name(assembly_name, suffix)} DELETE WHERE family_guid = %(family_guid)s SETTINGS mutations_sync = 1",
            params,
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
    if source is None:
        for suffix in ("variants/details", "key_lookup"):
            await _execute(
                f"ALTER TABLE {_structural_table_name(assembly_name, suffix)} DELETE WHERE family_guid = %(family_guid)s SETTINGS mutations_sync = 1",
                {"family_guid": family_uuid},
            )


async def insert_small_variant_records(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[SmallVariantRecord],
    *,
    annotation_version: str | None = None,
) -> None:
    await ensure_clickhouse_variant_tables(assembly_name)
    detail_rows, entry_rows, annotation_rows, annotation_index_rows, annotation_gene_index_rows = _small_variant_entry_rows(
        assembly_name,
        family_uuid,
        project_ids,
        records,
        annotation_version=annotation_version,
    )
    if detail_rows:
        await _execute_insert_chunks(
            f"""
            INSERT INTO {_small_table_name(assembly_name, 'variants/details')} (
                key,
                variantId,
                annotation_version,
                annotationSetHash,
                chrom,
                pos,
                ref,
                alt,
                rsid,
                annotationsJson,
                liftedOverChrom,
                liftedOverPos
            ) VALUES
            """,
            detail_rows,
            chunk_size=_SMALL_VARIANT_DETAIL_INSERT_ROWS,
        )
    if entry_rows:
        await _execute_insert_chunks(
            f"""
            INSERT INTO {_small_table_name(assembly_name, 'entries')} (
                key,
                variantId,
                annotation_version,
                annotationSetHash,
                project_guid,
                family_guid,
                sample_type,
                xpos,
                chrom,
                pos,
                ref,
                alt,
                source,
                rsid,
                is_gnomad_gt_5_percent,
                is_annotated_in_any_gene,
                gene_symbols,
                filters,
                qual,
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
            entry_rows,
            chunk_size=_SMALL_VARIANT_ENTRY_INSERT_ROWS,
        )
    if annotation_rows:
        await _execute_insert_chunks(
            f"""
            INSERT INTO {_small_table_name(assembly_name, 'variants/annotations')} (
                key,
                variantId,
                annotation_version,
                annotationSetHash,
                annotationHash,
                chrom,
                pos,
                ref,
                alt,
                gene_symbol,
                gene_id,
                transcript_id,
                hgvsc,
                hgvsp,
                impact,
                effects,
                clinvar_terms,
                canonical,
                mane_select,
                mane_plus_clinical,
                lof,
                gnomad_af,
                gnomad_exomes_af,
                gnomad_genomes_af,
                gnomad_popmax_af,
                topmed_af,
                gnomad_ac,
                gnomad_hom_count,
                gnomad_hemi_count,
                cadd_phred,
                revel,
                spliceai_max,
                sift,
                polyphen
            ) VALUES
            """,
            annotation_rows,
            chunk_size=_SMALL_VARIANT_ANNOTATION_INSERT_ROWS,
        )
    if annotation_index_rows:
        await _execute_insert_chunks(
            f"""
            INSERT INTO {_small_table_name(assembly_name, 'variants/annotation_index')} (
                key,
                variantId,
                annotation_version,
                annotationSetHash,
                chrom,
                pos,
                ref,
                alt,
                rsid,
                gene_symbols,
                gene_ids,
                transcript_ids,
                hgvsc_values,
                hgvsp_values,
                impacts,
                effects,
                clinvar_terms,
                has_canonical,
                has_mane_select,
                has_mane_plus_clinical,
                has_lof,
                max_gnomad_af,
                max_gnomad_exomes_af,
                max_gnomad_genomes_af,
                max_gnomad_popmax_af,
                max_topmed_af,
                max_gnomad_ac,
                max_gnomad_hom_count,
                max_gnomad_hemi_count,
                max_cadd_phred,
                max_revel,
                max_spliceai,
                sift_terms,
                polyphen_terms
            ) VALUES
            """,
            annotation_index_rows,
            chunk_size=_SMALL_VARIANT_INDEX_INSERT_ROWS,
        )
    if annotation_gene_index_rows:
        await _execute_insert_chunks(
            f"""
            INSERT INTO {_small_table_name(assembly_name, 'variants/gene_index')} (
                key,
                variantId,
                annotation_version,
                annotationSetHash,
                gene_term,
                chrom,
                pos,
                ref,
                alt
            ) VALUES
            """,
            annotation_gene_index_rows,
            chunk_size=_SMALL_VARIANT_GENE_INDEX_INSERT_ROWS,
        )


async def replace_family_small_variants(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[SmallVariantRecord],
    *,
    annotation_version: str | None = None,
) -> None:
    await delete_family_small_variants(assembly_name, family_uuid)
    if records:
        await insert_small_variant_records(
            assembly_name,
            family_uuid,
            project_ids,
            records,
            annotation_version=annotation_version,
        )
        await refresh_family_small_variant_summaries(assembly_name, family_uuid)


async def refresh_family_small_variant_summaries(
    assembly_name: str,
    family_uuid: str,
) -> None:
    await ensure_clickhouse_variant_tables(assembly_name)
    params = {"family_guid": family_uuid}
    for suffix in ("family_variant_summary", "family_sample_variant_summary"):
        await _execute(
            f"ALTER TABLE {_small_table_name(assembly_name, suffix)} DELETE WHERE family_guid = %(family_guid)s SETTINGS mutations_sync = 1",
            params,
        )

    # The family variant summary is a diagnostic count, so it excludes imputed
    # callsets (glimpse2/shapeit) — matching the live-fallback query in
    # _fetch_small_variant_summary and the default of the per-family variant list.
    params["imputed_sources"] = tuple(IMPUTED_SMALL_VARIANT_SOURCES)
    ref_or_missing_gts = _small_genotype_tuple(sorted(_SMALL_GT_REF | _SMALL_GT_MISSING))
    het_gts = _small_genotype_tuple(("0/1", "1/0", "0|1", "1|0"))
    hom_alt_gts = _small_genotype_tuple(("1/1", "1|1"))

    await _execute(
        f"""
        INSERT INTO {_small_table_name(assembly_name, 'family_variant_summary')} (
            family_guid,
            project_guid,
            total_variants,
            snv_count,
            indel_count
        )
        SELECT
            family_guid,
            project_guid,
            countDistinct(key) AS total_variants,
            countDistinctIf(key, length(ref) = 1 AND length(alt) = 1) AS snv_count,
            countDistinctIf(key, NOT (length(ref) = 1 AND length(alt) = 1)) AS indel_count
        FROM {_small_table_name(assembly_name, 'entries')}
        WHERE family_guid = %(family_guid)s
          AND sign = 1
          AND lowerUTF8(source) NOT IN %(imputed_sources)s
        GROUP BY family_guid, project_guid
        """,
        params,
    )
    await _execute(
        f"""
        INSERT INTO {_small_table_name(assembly_name, 'family_sample_variant_summary')} (
            family_guid,
            project_guid,
            sample_id,
            non_ref_count,
            het_count,
            hom_alt_count
        )
        SELECT
            family_guid,
            project_guid,
            sample_id,
            countDistinctIf(key, gt NOT IN {ref_or_missing_gts}) AS non_ref_count,
            countDistinctIf(key, gt IN {het_gts}) AS het_count,
            countDistinctIf(key, gt IN {hom_alt_gts}) AS hom_alt_count
        FROM {_small_table_name(assembly_name, 'entries')}
        ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
        WHERE family_guid = %(family_guid)s
          AND sign = 1
          AND lowerUTF8(source) NOT IN %(imputed_sources)s
        GROUP BY family_guid, project_guid, sample_id
        """,
        params,
    )


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
                family_guid,
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
            f"INSERT INTO {_structural_table_name(assembly_name, 'key_lookup')} (family_guid, variantId, key) VALUES",
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
                `calls.qual`,
                `calls.readSupport`,
                `calls.filter`,
                `calls.ps`,
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
            FROM {_small_table_name(assembly_name, 'entries')}
            WHERE {' AND '.join(clauses)}
            GROUP BY key
        )
        """,
        params,
    )
    return int(rows[0][0]) if rows else 0


async def _count_distinct_keys_by_family(
    *,
    entries_table: str,
    family_project_pairs: Sequence[tuple[str, str]],
    families_without_project: Sequence[str],
    exclude_sources: Sequence[str] | None = None,
) -> dict[str, int]:
    """Distinct-`key` counts grouped by family_guid in a single query, preserving
    the exact per-family project scope of the single-family counters:
    (family_guid, project_guid) pairs are counted within scope (variants are
    stored replicated per project), and families_without_project are counted with
    no project filter. Uses the same exact nested count() as the per-family path."""
    pairs = tuple(
        (str(family), str(project))
        for family, project in family_project_pairs
        if str(family).strip() and str(project).strip()
    )
    no_project = tuple(
        dict.fromkeys(
            str(family).strip() for family in families_without_project if str(family).strip()
        )
    )
    if not pairs and not no_project:
        return {}
    scope_terms: list[str] = []
    params: dict[str, Any] = {}
    if pairs:
        scope_terms.append("(family_guid, project_guid) IN %(family_project_pairs)s")
        params["family_project_pairs"] = pairs
    if no_project:
        scope_terms.append("family_guid IN %(families_without_project)s")
        params["families_without_project"] = no_project
    source_clause = ""
    if exclude_sources:
        params["exclude_sources"] = tuple(exclude_sources)
        source_clause = " AND lowerUTF8(source) NOT IN %(exclude_sources)s"
    rows = await _execute(
        f"""
        SELECT family_guid, count()
        FROM (
            SELECT family_guid, key
            FROM {entries_table}
            WHERE sign = 1 AND ({' OR '.join(scope_terms)}){source_clause}
            GROUP BY family_guid, key
        )
        GROUP BY family_guid
        """,
        params,
    )
    return {str(family_guid): int(count) for family_guid, count in rows}


async def count_family_small_variants_by_family(
    assembly_name: str,
    *,
    family_project_pairs: Sequence[tuple[str, str]],
    families_without_project: Sequence[str] = (),
) -> dict[str, int]:
    """Distinct small-variant counts per family in one GROUP BY query, preserving
    count_family_small_variants' exact per-family project scope."""
    await ensure_clickhouse_variant_tables(assembly_name)
    return await _count_distinct_keys_by_family(
        entries_table=_small_table_name(assembly_name, "entries"),
        family_project_pairs=family_project_pairs,
        families_without_project=families_without_project,
        exclude_sources=IMPUTED_SMALL_VARIANT_SOURCES,
    )


async def count_family_structural_variants_by_family(
    assembly_name: str,
    *,
    family_project_pairs: Sequence[tuple[str, str]],
    families_without_project: Sequence[str] = (),
) -> dict[str, int]:
    """Distinct structural-variant counts per family in one GROUP BY query,
    preserving count_family_structural_variants' exact per-family project scope."""
    await ensure_clickhouse_variant_tables(assembly_name)
    return await _count_distinct_keys_by_family(
        entries_table=_structural_table_name(assembly_name, "entries"),
        family_project_pairs=family_project_pairs,
        families_without_project=families_without_project,
    )


async def count_family_small_variants_by_sample(
    assembly_name: str,
    family_uuid: str,
    *,
    sample_ids: Sequence[str],
    project_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    deduped_sample_ids = list(dict.fromkeys(str(sample_id).strip() for sample_id in sample_ids if str(sample_id).strip()))
    if not deduped_sample_ids:
        return {}
    await ensure_clickhouse_variant_tables(assembly_name)
    clauses = [
        "family_guid = %(family_guid)s",
        "sign = 1",
        "sample_id IN %(sample_ids)s",
    ]
    params: dict[str, Any] = {
        "family_guid": family_uuid,
        "sample_ids": tuple(deduped_sample_ids),
    }
    if project_ids:
        clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(_normalized_project_ids(project_ids))
    ref_or_missing_gts = tuple(sorted([*_SMALL_GT_REF, *_SMALL_GT_MISSING]))
    rows = await _execute(
        f"""
        SELECT sample_id, countDistinctIf(key, gt NOT IN {ref_or_missing_gts})
        FROM {_small_table_name(assembly_name, 'entries')}
        ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
        WHERE {' AND '.join(clauses)}
        GROUP BY sample_id
        """,
        params,
    )
    return {str(sample_id): int(count) for sample_id, count in rows}


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


# A non-empty term on at least one of the gene_symbols / gene_ids arrays makes a
# key "gene-bearing" — i.e. it contributes at least one row to gene_index. Used
# both to rebuild the index and to detect drift between it and annotation_index.
_GENE_BEARING_KEY_PREDICATE = (
    "arrayExists(term -> length(term) > 0, "
    "arrayMap(t -> lowerUTF8(t), arrayConcat(gene_symbols, gene_ids)))"
)


async def _gene_bearing_annotation_key_count(annotation_index_table: str) -> int:
    rows = await _execute(
        f"SELECT uniqExact(key) FROM {annotation_index_table} WHERE {_GENE_BEARING_KEY_PREDICATE}"
    )
    return int(rows[0][0]) if rows and rows[0] else 0


async def _distinct_key_count(table: str) -> int:
    rows = await _execute(f"SELECT uniqExact(key) FROM {table}")
    return int(rows[0][0]) if rows and rows[0] else 0


async def rebuild_small_variant_gene_index(assembly_name: str) -> dict[str, Any]:
    dataset = _require_clickhouse_identifier(assembly_name)
    await ensure_clickhouse_variant_tables(dataset)
    gene_index_table = _small_table_name(dataset, "variants/gene_index")
    rebuild_table = _small_table_name(dataset, "variants/gene_index_rebuild")
    annotation_index_table = _small_table_name(dataset, "variants/annotation_index")

    # Build into a shadow table and atomically swap it in, so gene/panel queries
    # never observe an empty gene_index during the rebuild (the window a plain
    # TRUNCATE + INSERT leaves open). The swap relies on the Atomic database
    # engine's EXCHANGE TABLES.
    await _execute(f"DROP TABLE IF EXISTS {rebuild_table} SYNC")
    await _execute(f"CREATE TABLE {rebuild_table} AS {gene_index_table}")
    try:
        await _execute(
            f"""
            INSERT INTO {rebuild_table} (
                key, variantId, annotation_version, annotationSetHash,
                gene_term, chrom, pos, ref, alt
            )
            SELECT DISTINCT
                key, variantId, annotation_version, annotationSetHash,
                gene_term, chrom, pos, ref, alt
            FROM {annotation_index_table}
            ARRAY JOIN arrayDistinct(
                arrayFilter(
                    term -> length(term) > 0,
                    arrayMap(term -> lowerUTF8(term), arrayConcat(gene_symbols, gene_ids))
                )
            ) AS gene_term
            """
        )
        # Never swap a bad index over good data: the shadow must pass its part
        # checks and cover exactly the gene-bearing keys of annotation_index.
        check_rows = await _execute(
            f"CHECK TABLE {rebuild_table} SETTINGS check_query_single_value_result = 0"
        )
        bad_parts = [row for row in check_rows if not int(row[1])]
        if bad_parts:
            raise RuntimeError(
                f"gene_index rebuild produced {len(bad_parts)} corrupt part(s); not swapping"
            )
        rebuilt_keys = await _distinct_key_count(rebuild_table)
        source_keys = await _gene_bearing_annotation_key_count(annotation_index_table)
        if rebuilt_keys != source_keys:
            raise RuntimeError(
                "gene_index rebuild key mismatch: "
                f"{rebuilt_keys} rebuilt vs {source_keys} gene-bearing annotation keys; not swapping"
            )
        await _execute(f"EXCHANGE TABLES {gene_index_table} AND {rebuild_table}")
    finally:
        # After a successful swap this holds the old index; after a failure it
        # holds the rejected rebuild. Either way it is safe to drop.
        await _execute(f"DROP TABLE IF EXISTS {rebuild_table} SYNC")
    return await get_clickhouse_variant_storage_status(dataset)


async def check_clickhouse_variant_integrity(assembly_name: str) -> dict[str, Any]:
    """Detect ClickHouse corruption before it surfaces as query 500s.

    Runs three guards over an assembly's variant tables:
      * ``CHECK TABLE`` on each read-path table to find corrupt active parts
        (e.g. the UNKNOWN_CODEC / CHECKSUM_DOESNT_MATCH failures that only break
        gene/panel queries);
      * a scan of ``system.detached_parts`` for ``broken-on-start`` parts, which
        signal storage-volume damage even when the live read path looks intact;
      * a key-count comparison between ``gene_index`` and the gene-bearing keys of
        ``annotation_index`` it is derived from, to catch a stale/partial index.
    """
    dataset = _require_clickhouse_identifier(assembly_name)
    expected = _expected_clickhouse_variant_tables(dataset)
    data_tables = [name for _variant_type, kind, name in expected if kind == "table"]
    params = {
        "database": settings.clickhouse_database,
        "table_names": tuple(data_tables),
    }

    existing_rows = await _execute(
        """
        SELECT name FROM system.tables
        WHERE database = %(database)s AND name IN %(table_names)s
        """,
        params,
    )
    existing = {str(name) for (name,) in existing_rows}

    table_checks: list[dict[str, Any]] = []
    corrupt = False
    for name in data_tables:
        if name not in existing:
            table_checks.append(
                {"name": name, "exists": False, "passed": None, "failed_parts": 0, "messages": []}
            )
            continue
        qualified = f"{settings.clickhouse_database}.`{name}`"
        rows = await _execute(
            f"CHECK TABLE {qualified} SETTINGS check_query_single_value_result = 0"
        )
        failures = [
            str(row[2]) if len(row) > 2 and row[2] else "(no message)"
            for row in rows
            if not int(row[1])
        ]
        if failures:
            corrupt = True
        table_checks.append(
            {
                "name": name,
                "exists": True,
                "passed": not failures,
                "failed_parts": len(failures),
                "messages": failures[:5],
            }
        )

    detached_rows = await _execute(
        """
        SELECT table, reason, count() AS broken_parts
        FROM system.detached_parts
        WHERE database = %(database)s AND table IN %(table_names)s AND reason != ''
        GROUP BY table, reason
        ORDER BY table, reason
        """,
        params,
    )
    detached_broken_parts = [
        {"table": str(table), "reason": str(reason), "count": int(count or 0)}
        for table, reason, count in detached_rows
    ]

    consistency = await _gene_index_consistency(dataset, existing)

    existing_data_tables = [name for name in data_tables if name in existing]
    notes: list[str] = []
    if not existing_data_tables:
        status = "missing"
        notes.append("No variant tables exist for this assembly.")
    elif corrupt:
        status = "corrupt"
        notes.append("One or more active parts failed CHECK TABLE — rebuild or restore affected tables.")
    elif detached_broken_parts or (consistency["checked"] and not consistency["consistent"]):
        status = "degraded"
        if detached_broken_parts:
            total = sum(item["count"] for item in detached_broken_parts)
            notes.append(
                f"{total} detached broken part(s) found — investigate storage-volume health."
            )
        if consistency["checked"] and not consistency["consistent"]:
            notes.append(
                "gene_index keys differ from annotation_index — rebuild the small-variant gene index."
            )
    else:
        status = "ok"

    return {
        "assembly_name": dataset,
        "status": status,
        "table_checks": table_checks,
        "detached_broken_parts": detached_broken_parts,
        "gene_index_consistency": consistency,
        "notes": notes,
    }


async def _gene_index_consistency(dataset: str, existing: set[str]) -> dict[str, Any]:
    gene_index = f"{dataset}/SNV_INDEL/variants/gene_index"
    annotation_index = f"{dataset}/SNV_INDEL/variants/annotation_index"
    if gene_index not in existing or annotation_index not in existing:
        return {
            "checked": False,
            "gene_index_keys": 0,
            "annotation_index_gene_keys": 0,
            "consistent": True,
            "drift": 0,
        }
    gene_index_keys = await _distinct_key_count(_small_table_name(dataset, "variants/gene_index"))
    annotation_keys = await _gene_bearing_annotation_key_count(
        _small_table_name(dataset, "variants/annotation_index")
    )
    return {
        "checked": True,
        "gene_index_keys": gene_index_keys,
        "annotation_index_gene_keys": annotation_keys,
        "consistent": gene_index_keys == annotation_keys,
        "drift": gene_index_keys - annotation_keys,
    }


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
        if kind == "table"
    ]
    for table_name in optimize_targets:
        query = f"OPTIMIZE TABLE {settings.clickhouse_database}.`{table_name}`"
        if final:
            query += " FINAL"
        await _execute(query)
    return await get_clickhouse_variant_storage_status(dataset)
