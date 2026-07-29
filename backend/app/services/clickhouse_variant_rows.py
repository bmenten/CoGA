from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from .data_scope import normalize_chromosome
from .clickhouse_variant_ids import (
    build_small_variant_id,
    build_structural_variant_id,
    small_variant_key,
    structural_variant_key,
    _stable_uint64,
    _xpos,
)
from .clickhouse_family_variants import (
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


_DEFAULT_SMALL_ANNOTATION_VERSION = "current"


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
        sample_copy_numbers = [
            None if call.copy_number is None else int(call.copy_number) for call in record.calls
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
                    sample_copy_numbers,
                    1,
                )
            )
    return detail_rows, lookup_rows, entry_rows
