from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Collection, Iterable, NamedTuple, Sequence

from .clickhouse_column_bounds import INT32, UINT16, UINT32, UINT64, column_float32, column_int
from .data_scope import normalize_chromosome
from .clickhouse_variant_ids import (
    build_small_variant_id,
    build_structural_variant_id,
    record_allele_keys,
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


def _payload_hash(payload: str) -> int:
    """The hash of one annotation, from its canonical JSON."""
    return _stable_uint64("small_annotation", payload)


def _annotation_payload_hash(annotation: dict[str, Any]) -> int:
    """The hash of one annotation."""
    return _payload_hash(_annotation_canonical_json(annotation))


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


# The fields of an ``entries`` row's ``transcripts``, as ``(name, ClickHouse type)``: the
# columns of ``variants/annotations`` that the family search's per-transcript filters test
# (``clickhouse_variant_queries._small_annotation_filter_condition`` and the salient
# condition), under the same names and types, so that a condition on a transcript row ``a``
# means the same on the shared table and on a row's own transcripts. The text columns
# (transcript, HGVS c. and p.) are left out: on a synthetic whole-genome trio they took 92 B a
# row, against 36 B for these (ZSTD; 83 B for the rest of the row), and their filters read the
# shared table.
ENTRY_TRANSCRIPT_FIELDS: tuple[tuple[str, str], ...] = (
    ("impact", "LowCardinality(String)"),
    ("effects", "Array(String)"),
    ("canonical", "Bool"),
    ("mane_select", "Bool"),
    ("mane_plus_clinical", "Bool"),
    ("lof", "LowCardinality(String)"),
    ("gnomad_af", "Nullable(Float32)"),
    ("gnomad_exomes_af", "Nullable(Float32)"),
    ("gnomad_genomes_af", "Nullable(Float32)"),
    ("gnomad_popmax_af", "Nullable(Float32)"),
    ("topmed_af", "Nullable(Float32)"),
    ("gnomad_ac", "Nullable(UInt32)"),
    ("gnomad_hom_count", "Nullable(UInt32)"),
    ("gnomad_hemi_count", "Nullable(UInt32)"),
    ("cadd_phred", "Nullable(Float32)"),
    ("revel", "Nullable(Float32)"),
    ("spliceai_max", "Nullable(Float32)"),
    ("sift", "LowCardinality(String)"),
    ("polyphen", "LowCardinality(String)"),
)
ENTRY_TRANSCRIPTS_TYPE = (
    f"Array(Tuple({', '.join(f'{name} {column_type}' for name, column_type in ENTRY_TRANSCRIPT_FIELDS)}))"
)


def _entry_transcripts(columns: Sequence[_AnnotationColumns]) -> list[dict[str, Any]]:
    """An ``entries`` row's ``transcripts``: for each of the variant's ``annotations`` rows
    (``columns``, one per annotation, the empty annotation of a variant without one
    included), the values of ``ENTRY_TRANSCRIPT_FIELDS`` as that row stores them, each
    distinct tuple once, in the order of the annotations. A condition holds on some
    transcript here exactly when it holds on some ``annotations`` row of the variant's
    annotation set: both are made from the same annotations, and a repeated tuple adds no
    transcript a condition could hold on.

    Each transcript is a mapping of field to value, the form the ClickHouse client reads a
    named tuple back in: a rewrite writes the rows it read (``fetch_family_small_variant_entries``)
    and the rows it builds in one insert, and the client takes one form per column."""
    distinct: dict[tuple[Any, ...], None] = {}
    for column in columns:
        distinct.setdefault(
            (
                column.impact,
                tuple(sorted(column.effect_terms)),
                column.canonical,
                column.mane_select,
                column.mane_plus_clinical,
                column.lof,
                column.gnomad_af,
                column.gnomad_exomes_af,
                column.gnomad_genomes_af,
                column.gnomad_popmax_af,
                column.topmed_af,
                column.gnomad_ac,
                column.gnomad_hom_count,
                column.gnomad_hemi_count,
                column.cadd_phred,
                column.revel,
                column.spliceai_max,
                column.sift,
                column.polyphen,
            ),
            None,
        )
    names = [name for name, _type in ENTRY_TRANSCRIPT_FIELDS]
    return [
        dict(zip(names, (impact, list(effects), *rest), strict=True))
        for impact, effects, *rest in distinct
    ]


# A variant's rows in the shared tables (``variants/details``, ``annotations``,
# ``annotation_index`` and ``gene_index``) of one annotation version: its key, the hash of its
# annotation set and its rsID. Those rows hold nothing else that is not the key's (its
# position and alleles) or its annotations', so two writes of one identity write the same rows.
SharedRowsIdentity = tuple[int, int, str | None]


@dataclass(slots=True)
class PreparedSmallVariant:
    """A record as the writer stores it, each annotation serialized and read once: its JSON
    gives its own hash and the set's, its values (``columns``) its ``annotations`` row, the
    variant's index rows and the ``entries`` row's transcripts. ``payloads`` and ``columns``
    hold one per ``annotations`` row: the record's annotations, or the empty annotation of a
    variant without one. Not frozen, for the reason ``_AnnotationColumns`` is not."""

    record: SmallVariantRecord
    variant_id: str
    key: int
    chrom: str
    pos: int
    rsid: str | None
    annotation_set_hash: int
    payloads: list[str]
    columns: list[_AnnotationColumns]

    @property
    def shared_rows(self) -> SharedRowsIdentity:
        return (self.key, self.annotation_set_hash, self.rsid)

    def index_columns(self) -> list[_AnnotationColumns]:
        """The columns the index rows gather: none for a variant without an annotation, whose
        ``annotations`` row of the empty annotation is no annotation of it."""
        return self.columns if self.record.annotations else []


def _annotation_row(
    variant: PreparedSmallVariant, annotation_version: str, payload: str, columns: _AnnotationColumns
) -> tuple[Any, ...]:
    return (
        variant.key,
        variant.variant_id,
        annotation_version,
        variant.annotation_set_hash,
        _payload_hash(payload),
        variant.chrom,
        variant.pos,
        variant.record.ref,
        variant.record.alt,
        columns.gene,
        columns.gene_id,
        columns.transcript_id,
        columns.hgvsc,
        columns.hgvsp,
        columns.impact,
        sorted(columns.effect_terms),
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
    variant: PreparedSmallVariant, annotation_version: str, columns: Sequence[_AnnotationColumns]
) -> tuple[Any, ...]:
    """The variant's ``annotation_index`` row, from its ``index_columns``."""
    return (
        variant.key,
        variant.variant_id,
        annotation_version,
        variant.annotation_set_hash,
        variant.chrom,
        variant.pos,
        variant.record.ref,
        variant.record.alt,
        variant.rsid,
        _string_list(column.gene for column in columns),
        _string_list(column.gene_id for column in columns),
        _string_list(column.hgvsc for column in columns),
        _string_list(column.hgvsp for column in columns),
        _string_list(column.impact for column in columns),
        _string_list(term for column in columns for term in column.effect_terms),
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
    variant: PreparedSmallVariant, annotation_version: str, columns: Sequence[_AnnotationColumns]
) -> list[tuple[Any, ...]]:
    """The variant's ``gene_index`` rows: one per gene symbol and gene ID of its
    ``index_columns``, the terms the index rebuild reads out of ``annotation_index``
    (``rebuild_small_variant_gene_index``)."""
    gene_terms = _string_list([*(column.gene for column in columns), *(column.gene_id for column in columns)])
    return [
        (
            variant.key,
            variant.variant_id,
            annotation_version,
            variant.annotation_set_hash,
            gene_term,
            variant.chrom,
            variant.pos,
            variant.record.ref,
            variant.record.alt,
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


def prepare_small_variants(assembly_name: str, records: Sequence[SmallVariantRecord]) -> list[PreparedSmallVariant]:
    """``records`` as the writer stores them, each annotation serialized and read once; a
    record whose position no column holds is left out."""
    variants: list[PreparedSmallVariant] = []
    unstorable = 0
    for record in records:
        if not _storable_position(record.start):
            unstorable += 1
            continue
        variant_id = record.variant_id or build_small_variant_id(record.chr, record.start, record.ref, record.alt)
        payloads = [_annotation_canonical_json(annotation) for annotation in record.annotations]
        variants.append(
            PreparedSmallVariant(
                record=record,
                variant_id=variant_id,
                key=record.variant_key or small_variant_key(assembly_name, variant_id),
                chrom=normalize_chromosome(record.chr),
                pos=int(record.start),
                rsid=record.rsid or _annotation_rsid(record.annotations),
                annotation_set_hash=_annotation_set_hash(record.annotations, payloads),
                payloads=payloads if record.annotations else [_annotation_canonical_json({})],
                columns=[_annotation_columns(annotation) for annotation in record.annotations or [{}]],
            )
        )
    _log_unstorable(unstorable, "small-variant")
    return variants


class SmallVariantRows(NamedTuple):
    """The rows a write of small variants inserts, per table."""

    details: list[tuple[Any, ...]]
    entries: list[tuple[Any, ...]]
    annotations: list[tuple[Any, ...]]
    annotation_index: list[tuple[Any, ...]]
    gene_index: list[tuple[Any, ...]]


def _add_shared_rows(rows: SmallVariantRows, variant: PreparedSmallVariant, annotation_version: str) -> None:
    record = variant.record
    rows.details.append(
        (
            variant.key,
            variant.variant_id,
            annotation_version,
            variant.annotation_set_hash,
            variant.chrom,
            variant.pos,
            record.ref,
            record.alt,
            variant.rsid,
            _json_payload(record.annotations),
        )
    )
    for payload, columns in zip(variant.payloads, variant.columns, strict=True):
        rows.annotations.append(_annotation_row(variant, annotation_version, payload, columns))
    index_columns = variant.index_columns()
    rows.annotation_index.append(_small_annotation_index_row(variant, annotation_version, index_columns))
    rows.gene_index.extend(_small_annotation_gene_index_rows(variant, annotation_version, index_columns))


def _add_entry_rows(
    rows: SmallVariantRows,
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    variant: PreparedSmallVariant,
    annotation_version: str,
) -> None:
    record = variant.record
    gene_symbols = _string_list(record.gene_symbols or _annotation_gene_symbols(record.annotations))
    gnomad_over_5_percent = _annotation_gnomad_over_5_percent(record.annotations)
    transcripts = _entry_transcripts(variant.columns)
    filters = _string_list(record.filters)
    xpos = _xpos(record.chr, record.start)
    allele_keys = record_allele_keys(assembly_name, record.chr, record.start, record.ref, record.alt)
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
    for project_id in project_ids:
        rows.entries.append(
            (
                variant.key,
                variant.variant_id,
                annotation_version,
                variant.annotation_set_hash,
                project_id,
                family_uuid,
                xpos,
                variant.chrom,
                variant.pos,
                record.ref,
                record.alt,
                allele_keys,
                record.source or "",
                variant.rsid,
                gnomad_over_5_percent,
                bool(gene_symbols),
                gene_symbols,
                transcripts,
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


def small_variant_rows(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    variants: Sequence[PreparedSmallVariant],
    *,
    annotation_version: str,
    stored: Collection[SharedRowsIdentity],
) -> SmallVariantRows:
    """The rows that write ``variants`` for the family in each of ``project_ids``: its
    ``entries`` rows, and the shared rows of each variant whose identity is not ``stored``."""
    rows = SmallVariantRows([], [], [], [], [])
    normalized_project_ids = _normalized_project_ids(project_ids)
    for variant in variants:
        if variant.shared_rows not in stored:
            _add_shared_rows(rows, variant, annotation_version)
        _add_entry_rows(rows, assembly_name, family_uuid, normalized_project_ids, variant, annotation_version)
    return rows


def _small_variant_entry_rows(
    assembly_name: str,
    family_uuid: str,
    project_ids: Sequence[str],
    records: Sequence[SmallVariantRecord],
    *,
    annotation_version: str | None = None,
) -> SmallVariantRows:
    """Every row that writes ``records`` where none of their shared rows is stored."""
    return small_variant_rows(
        assembly_name,
        family_uuid,
        project_ids,
        prepare_small_variants(assembly_name, records),
        annotation_version=_small_annotation_version(annotation_version),
        stored=(),
    )


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
