from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Iterable, Sequence

from .clickhouse_column_bounds import INT32, UINT16, UINT32, UINT64, column_float32, column_int
from .data_scope import normalize_chromosome
from .clickhouse_variant_ids import (
    build_small_variant_id,
    build_structural_variant_id,
    small_variant_key,
    structural_variant_key,
    _stable_uint64,
    _xpos,
)
from .clickhouse_variant_records import (
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

logger = logging.getLogger(__name__)

# Every value below is bound to its column before the write: a value the column
# cannot hold, or that is no finite number, is stored as missing (clickhouse_column_bounds).
# A record whose position no column holds is left out: its loader already skipped it as
# malformed, and counted it, so this only guards a loader that did not.


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


def _annotation_canonical_json(annotation: Any) -> str:
    """The annotation as its hashes read it: JSON with sorted keys and no spaces."""
    return json.dumps(annotation, sort_keys=True, separators=(",", ":"), default=str)


def _annotation_payload_hash(annotation: dict[str, Any], payload: str | None = None) -> int:
    """The hash of one annotation; ``payload`` is its canonical JSON, when made already."""
    return _stable_uint64("small_annotation", _annotation_canonical_json(annotation) if payload is None else payload)


def _annotation_set_hash(annotations: Sequence[dict[str, Any]], payloads: Sequence[str] | None = None) -> int:
    """The hash of a variant's annotations. ``payloads`` are their canonical JSON, one per
    annotation, when made already: the list's JSON is theirs joined, as ``json.dumps``
    writes a list with these separators, so each annotation is serialized once."""
    if payloads is None:
        payload = _annotation_canonical_json(list(annotations or []))
    else:
        payload = f"[{','.join(payloads)}]"
    return _stable_uint64("small_annotation_set", payload)


def _max_or_none(values: Iterable[float | int | None]) -> Any:
    candidates = [value for value in values if value is not None]
    return max(candidates) if candidates else None


def _annotation_gene_symbols(annotations: Sequence[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for annotation in annotations:
        for key in ("gene", "gene_symbol", "hgnc_symbol"):
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
            "gnomad_exomes_af",
            "gnomad_genomes_af",
            "gnomad_popmax_af",
            "topmed_af",
        ):
            # As the frequency columns store it: a value that is no finite number or
            # lies beyond Float32 is missing, never a frequency over 5 %.
            stored = column_float32(annotation.get(key))
            if stored is not None:
                values.append(stored)
        frequencies = annotation.get("population_frequencies")
        if isinstance(frequencies, dict):
            for raw in frequencies.values():
                stored = column_float32(raw)
                if stored is not None:
                    values.append(stored)
        return values

    return any(value > 0.05 for annotation in annotations for value in _candidate_values(annotation))


def _popmax_column(annotation: dict[str, Any], population_frequencies: dict[str, float]) -> float | None:
    """The popmax a frequency ceiling reads (the ``gnomad_popmax_af`` column): gnomAD's own
    popmax, else VEP's MAX_AF, the highest frequency over every population VEP reads (1000
    Genomes and gnomAD). An annotation with MAX_AF but no gnomAD frequency would otherwise let
    a common variant through a frequency ceiling. The card and the frozen evidence name the two
    apart (``max_af``)."""
    gnomad = _population_float(annotation, population_frequencies, "gnomad_popmax_af")
    if gnomad is not None:
        return gnomad
    return _population_float(annotation, population_frequencies, "max_af")


def _population_float(
    annotation: dict[str, Any], population_frequencies: dict[str, float], *keys: str
) -> float | None:
    """``population_frequencies`` is the annotation's (``_annotation_population_frequencies``),
    read once for its five frequencies."""
    values = [_annotation_float(annotation, *keys)]
    for key in keys:
        values.append(population_frequencies.get(key))
    # Highest frequency across the direct annotation value and all population
    # fallbacks (conservative for rare-variant filtering); _max_or_none ignores Nones,
    # among them a value no Float32 column holds.
    return _max_or_none(column_float32(value) for value in values)


def _annotation_float32(annotation: dict[str, Any], *keys: str) -> float | None:
    return column_float32(_annotation_float(annotation, *keys))


def _annotation_count(annotation: dict[str, Any], *keys: str) -> int | None:
    return column_int(_annotation_int(annotation, *keys), UINT32)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(slots=True)
class _AnnotationColumns:
    """One annotation's values as the ``annotations`` table stores them. The variant's
    ``annotation_index`` row and ``gene_index`` rows hold the same values gathered over its
    annotations, so each annotation is read once for all three tables (the read of an
    annotation is most of a whole-genome import's row building). Not frozen, which would
    make each of the millions built slower; nothing changes one once built."""

    gene: str
    gene_id: str
    transcript_id: str
    hgvsc: str
    hgvsp: str
    impact: str
    # As read, unsorted: the annotation index gathers them in this order, the annotations
    # row stores them sorted.
    effect_terms: set[str]
    clinvar_terms: set[str]
    canonical: bool
    mane_select: bool
    mane_plus_clinical: bool
    lof: str
    gnomad_af: float | None
    gnomad_exomes_af: float | None
    gnomad_genomes_af: float | None
    gnomad_popmax_af: float | None
    topmed_af: float | None
    gnomad_ac: int | None
    gnomad_hom_count: int | None
    gnomad_hemi_count: int | None
    cadd_phred: float | None
    revel: float | None
    spliceai_max: float | None
    sift: str
    polyphen: str


def _annotation_columns(annotation: dict[str, Any]) -> _AnnotationColumns:
    population_frequencies = _annotation_population_frequencies(annotation)
    return _AnnotationColumns(
        gene=_casefold(_annotation_gene(annotation)),
        gene_id=_casefold(_annotation_gene_id(annotation)),
        transcript_id=_clean_text(_annotation_text(annotation, "transcript_id")),
        hgvsc=_clean_text(_annotation_text(annotation, "hgvsc")),
        hgvsp=_clean_text(_annotation_text(annotation, "hgvsp")),
        impact=_casefold(_annotation_text(annotation, "impact")),
        effect_terms=_annotation_terms(_annotation_effect(annotation)),
        clinvar_terms=_status_terms(_annotation_clinvar(annotation)),
        canonical=_annotation_bool(annotation, "canonical"),
        mane_select=_annotation_bool(annotation, "mane_select"),
        mane_plus_clinical=_annotation_bool(annotation, "mane_plus_clinical"),
        lof=_casefold(_annotation_text(annotation, "lof") or ""),
        gnomad_af=_population_float(annotation, population_frequencies, "gnomad_af"),
        gnomad_exomes_af=_population_float(annotation, population_frequencies, "gnomad_exomes_af"),
        gnomad_genomes_af=_population_float(
            annotation, population_frequencies, "gnomad_genomes_af"
        ),
        gnomad_popmax_af=_popmax_column(annotation, population_frequencies),
        topmed_af=_population_float(annotation, population_frequencies, "topmed_af"),
        gnomad_ac=_annotation_count(annotation, "gnomad_ac"),
        gnomad_hom_count=_annotation_count(annotation, "gnomad_hom_count"),
        gnomad_hemi_count=_annotation_count(annotation, "gnomad_hemi_count"),
        cadd_phred=_annotation_float32(annotation, "cadd_phred"),
        revel=_annotation_float32(annotation, "revel"),
        spliceai_max=column_float32(_annotation_spliceai_max(annotation)),
        sift=_casefold(_annotation_text(annotation, "sift")),
        polyphen=_casefold(_annotation_text(annotation, "polyphen")),
    )


def _annotation_row_from_columns(
    *,
    variant_key: int,
    variant_id: str,
    annotation_version: str,
    annotation_set_hash: int,
    annotation_hash: int,
    chrom: str,
    pos: int,
    record: SmallVariantRecord,
    columns: _AnnotationColumns,
) -> tuple[Any, ...]:
    return (
        variant_key,
        variant_id,
        annotation_version,
        annotation_set_hash,
        annotation_hash,
        chrom,
        pos,
        record.ref,
        record.alt,
        columns.gene,
        columns.gene_id,
        columns.transcript_id,
        columns.hgvsc,
        columns.hgvsp,
        columns.impact,
        sorted(columns.effect_terms),
        sorted(columns.clinvar_terms),
        columns.canonical,
        columns.mane_select,
        columns.mane_plus_clinical,
        columns.lof,
        columns.gnomad_af,
        columns.gnomad_exomes_af,
        columns.gnomad_genomes_af,
        columns.gnomad_popmax_af,
        columns.topmed_af,
        columns.gnomad_ac,
        columns.gnomad_hom_count,
        columns.gnomad_hemi_count,
        columns.cadd_phred,
        columns.revel,
        columns.spliceai_max,
        columns.sift,
        columns.polyphen,
    )


def _small_annotation_index_row(
    *,
    variant_key: int,
    variant_id: str,
    annotation_version: str,
    annotation_set_hash: int,
    chrom: str,
    pos: int,
    record: SmallVariantRecord,
    annotations: Sequence[dict[str, Any]],
    columns: Sequence[_AnnotationColumns],
) -> tuple[Any, ...]:
    """The variant's ``annotation_index`` row, from the ``columns`` of its ``annotations``
    (one per annotation, in their order)."""
    return (
        variant_key,
        variant_id,
        annotation_version,
        annotation_set_hash,
        chrom,
        pos,
        record.ref,
        record.alt,
        record.rsid or _annotation_rsid(annotations),
        _string_list(column.gene for column in columns),
        _string_list(column.gene_id for column in columns),
        _string_list(column.hgvsc for column in columns),
        _string_list(column.hgvsp for column in columns),
        _string_list(column.impact for column in columns),
        _string_list(term for column in columns for term in column.effect_terms),
        _string_list(term for column in columns for term in column.clinvar_terms),
        any(column.canonical for column in columns),
        any(column.mane_select for column in columns),
        any(column.mane_plus_clinical for column in columns),
        any(term not in {"", ".", "na", "n/a"} for term in {column.lof for column in columns}),
        _max_or_none(column.gnomad_af for column in columns),
        _max_or_none(column.gnomad_exomes_af for column in columns),
        _max_or_none(column.gnomad_genomes_af for column in columns),
        _max_or_none(column.gnomad_popmax_af for column in columns),
        _max_or_none(column.topmed_af for column in columns),
        _max_or_none(column.gnomad_ac for column in columns),
        _max_or_none(column.gnomad_hom_count for column in columns),
        _max_or_none(column.gnomad_hemi_count for column in columns),
        _max_or_none(column.cadd_phred for column in columns),
        _max_or_none(column.revel for column in columns),
        _max_or_none(column.spliceai_max for column in columns),
    )


def _small_annotation_gene_index_rows(
    *,
    variant_key: int,
    variant_id: str,
    annotation_version: str,
    annotation_set_hash: int,
    chrom: str,
    pos: int,
    record: SmallVariantRecord,
    columns: Sequence[_AnnotationColumns],
) -> list[tuple[Any, ...]]:
    gene_terms = _string_list(
        [
            *(_casefold(symbol) for symbol in record.gene_symbols),
            *(column.gene for column in columns),
            *(column.gene_id for column in columns),
        ]
    )
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


def _small_call_af(call: SmallVariantCall) -> list[float]:
    """The call's allele fractions as stored, one per alternate allele: all of them, or none
    when one is no finite number or lies beyond Float32, as the VCF reader keeps them
    (``_parse_float_list``). Leaving one out would move each later allele's fraction onto the
    allele before it."""
    stored = [column_float32(value) for value in call.af]
    numbers = [number for number in stored if number is not None]
    return numbers if len(numbers) == len(stored) else []


def _small_call_ad(call: SmallVariantCall) -> list[int]:
    """The call's allele depths as stored, ``UInt32``. A depth the column cannot hold is
    stored as 0, as the VCF reader stores a missing or unreadable one (``_parse_int_list``),
    so the other alleles' depths keep their places."""
    return [column_int(depth, UINT32) or 0 for depth in call.ad]


def _small_call_ab(af: Sequence[float], ad: Sequence[int]) -> float | None:
    """The call's allele balance from its stored allele fractions, else its allele depths."""
    if af:
        return af[0]
    if len(ad) > 1:
        total_depth = sum(ad)
        if total_depth > 0:
            return column_float32(float(ad[1]) / float(total_depth))
    return None


def _small_call_gq(call: SmallVariantCall) -> int | None:
    return column_int(call.gq, UINT16)


def _small_call_dp(call: SmallVariantCall) -> int | None:
    return column_int(call.dp, UINT32)


def _small_call_ps(call: SmallVariantCall) -> int | None:
    return column_int(call.ps, UINT64)


def _small_call_metrics(call: SmallVariantCall) -> dict[str, float]:
    """The call's caller metrics as stored: finite numbers a Float32 holds, under their
    names."""
    metrics: dict[str, float] = {}
    for name, value in (call.metrics or {}).items():
        number = column_float32(value)
        if number is not None and str(name).strip():
            metrics[str(name).strip()] = number
    return metrics


def _structural_call_qual(call: StructuralVariantCall) -> float | None:
    return column_float32(call.qual)


def _structural_call_read_support(call: StructuralVariantCall) -> int | None:
    return column_int(call.read_support, UINT32)


def _storable_position(value: Any) -> bool:
    """Whether a ``UInt32`` position column holds ``value``."""
    return column_int(value, UINT32) is not None


def _log_unstorable(count: int, kind: str) -> None:
    if count:
        logger.warning(
            "Left out %d %s record(s) whose position no ClickHouse column holds", count, kind
        )


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
    unstorable = 0
    for record in records:
        if not _storable_position(record.start):
            unstorable += 1
            continue
        variant_id = record.variant_id or build_small_variant_id(
            record.chr,
            record.start,
            record.ref,
            record.alt,
        )
        variant_key = record.variant_key or small_variant_key(assembly_name, variant_id)
        record_annotation_version = active_annotation_version
        chrom = normalize_chromosome(record.chr)
        pos = int(record.start)
        # Each annotation serialized and read once (_AnnotationColumns): its JSON gives its
        # own hash and the set's, its values its annotations row and the variant's index rows.
        payloads = [_annotation_canonical_json(annotation) for annotation in record.annotations]
        record_annotation_set_hash = _annotation_set_hash(record.annotations, payloads)
        row_annotations: list[Any] = record.annotations or [{}]
        row_payloads = payloads if record.annotations else [_annotation_canonical_json({})]
        row_columns = [_annotation_columns(annotation) for annotation in row_annotations]
        # The index rows read the annotations that are mappings; with none, the annotations
        # row of an empty annotation is no annotation of the variant.
        index_columns = [
            columns
            for annotation, columns in zip(record.annotations, row_columns)
            if isinstance(annotation, dict)
        ]
        index_annotations = [annotation for annotation in record.annotations if isinstance(annotation, dict)]
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
                chrom,
                pos,
                record.ref,
                record.alt,
                rsid,
                annotations_json,
            )
        )
        annotation_index_rows.append(
            _small_annotation_index_row(
                variant_key=variant_key,
                variant_id=variant_id,
                annotation_version=record_annotation_version,
                annotation_set_hash=record_annotation_set_hash,
                chrom=chrom,
                pos=pos,
                record=record,
                annotations=index_annotations,
                columns=index_columns,
            )
        )
        annotation_gene_index_rows.extend(
            _small_annotation_gene_index_rows(
                variant_key=variant_key,
                variant_id=variant_id,
                annotation_version=record_annotation_version,
                annotation_set_hash=record_annotation_set_hash,
                chrom=chrom,
                pos=pos,
                record=record,
                columns=index_columns,
            )
        )
        for annotation, payload, columns in zip(row_annotations, row_payloads, row_columns):
            annotation_rows.append(
                _annotation_row_from_columns(
                    variant_key=variant_key,
                    variant_id=variant_id,
                    annotation_version=record_annotation_version,
                    annotation_set_hash=record_annotation_set_hash,
                    annotation_hash=_annotation_payload_hash(annotation, payload),
                    chrom=chrom,
                    pos=pos,
                    record=record,
                    columns=columns,
                )
            )
        gnomad_over_5_percent = _annotation_gnomad_over_5_percent(record.annotations)
        xpos = _xpos(record.chr, record.start)
        sample_ids = [call.sample for call in record.calls]
        sample_gts = [call.gt for call in record.calls]
        sample_gqs = [_small_call_gq(call) for call in record.calls]
        sample_dps = [_small_call_dp(call) for call in record.calls]
        sample_afs = [_small_call_af(call) for call in record.calls]
        sample_ads = [_small_call_ad(call) for call in record.calls]
        sample_abs = [_small_call_ab(af, ad) for af, ad in zip(sample_afs, sample_ads)]
        sample_pss = [_small_call_ps(call) for call in record.calls]
        sample_filters = [_string_list(call.filters) for call in record.calls]
        sample_metrics = [_small_call_metrics(call) for call in record.calls]
        for project_id in normalized_project_ids:
            entry_rows.append(
                (
                    variant_key,
                    variant_id,
                    record_annotation_version,
                    record_annotation_set_hash,
                    project_id,
                    family_uuid,
                    xpos,
                    chrom,
                    pos,
                    record.ref,
                    record.alt,
                    record.source or "",
                    rsid,
                    gnomad_over_5_percent,
                    bool(gene_symbols),
                    gene_symbols,
                    filters,
                    column_float32(record.qual),
                    sample_ids,
                    sample_gts,
                    sample_gqs,
                    sample_dps,
                    sample_abs,
                    sample_afs,
                    sample_ads,
                    sample_pss,
                    sample_filters,
                    sample_metrics,
                    1,
                )
            )
    _log_unstorable(unstorable, "small-variant")
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
    unstorable = 0
    for record in records:
        if not (_storable_position(record.start) and _storable_position(record.end)):
            unstorable += 1
            continue
        variant_id = record.variant_id or build_structural_variant_id(
            record.chr,
            record.start,
            record.end,
            record.sv_type,
            remote_chr=record.remote_chr,
            remote_start=record.remote_start,
            remote_end=record.remote_end,
        )
        source = record.source or ""
        # A row written back from storage keeps its stored key; a new one gets a key that
        # names its caller, since the id may not (see structural_variant_key).
        variant_key = record.variant_key or structural_variant_key(
            assembly_name,
            family_uuid,
            variant_id,
            source=source,
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
                source,
                normalize_chromosome(record.remote_chr) if record.remote_chr else None,
                column_int(record.remote_start, UINT32),
                column_int(record.remote_end, UINT32),
                column_int(record.sv_len, INT32),
                filters,
                _json_payload(record.annotations),
            )
        )
        lookup_rows.append((family_uuid, variant_id, source, variant_key))
        sample_ids = [call.sample for call in record.calls]
        sample_gts = [call.gt for call in record.calls]
        sample_quals = [_structural_call_qual(call) for call in record.calls]
        sample_read_supports = [_structural_call_read_support(call) for call in record.calls]
        sample_filters = [call.filter for call in record.calls]
        sample_phase_sets = [column_int(call.phase_set, UINT64) for call in record.calls]
        sample_copy_numbers = [column_int(call.copy_number, UINT16) for call in record.calls]
        for project_id in normalized_project_ids:
            entry_rows.append(
                (
                    variant_key,
                    variant_id,
                    project_id,
                    family_uuid,
                    normalize_chromosome(record.chr),
                    int(record.start),
                    int(record.end),
                    record.sv_type,
                    source,
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
    _log_unstorable(unstorable, "structural-variant")
    return detail_rows, lookup_rows, entry_rows
