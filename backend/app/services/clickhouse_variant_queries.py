from __future__ import annotations

import json
import re
from bisect import bisect_right
from dataclasses import dataclass, replace
from typing import Any, Callable, Collection, Final, Iterable, NamedTuple, Protocol, Sequence, cast

from fastapi import HTTPException

from ..schemas import (
    ApiId,
    GenotypeOut,
    SmallVariantFilterTranscriptOut,
    SmallVariantReviewOut,
    SmallVariantTranscriptOut,
    VariantOut,
    VariantPage,
)
from .clickhouse_variant_rows import _annotation_payload_hash
from .analysis_scope import left_out_source_clauses
from .clickhouse_variant_ids import (
    _small_table_name,
    _structural_table_name,
    _xpos,
)
from .data_scope import chromosome_aliases, normalize_chromosome
from .variant_annotation_parser import CLINVAR_FROM_VEP_CACHE_KEY, MANE_REFSEQ_KEY, _spliceai_delta
from .family_metadata_context import FamilyMetadataContext
from .family_package_common import resolve_vcf_sample_id
from .compound_het_phase import (
    CONFIDENT_REFERENCE_MIN_DP,
    PHASE_CIS,
    PHASE_EVIDENCE_READ,
    PHASE_EVIDENCE_SEGREGATION,
    PHASE_TRANS,
    PHASE_UNKNOWN,
    UNKNOWN_CARRIAGE,
    Carriage,
    FamilyPedigree,
    hit_origin,
    phased_alt_haplotype,
    segregation_phase,
    small_variant_carriage,
)
from .genotypes import (
    ALT_CLASSES,
    HET,
    HOM_ALT,
    HOM_REF,
    GenotypeClass,
    classify_genotype,
    clickhouse_genotype_condition,
    genotype_has_alt,
)
from .same_individual import person_of, persons, same_individual_pairs
from .sex_chromosomes import (
    XY_CHROMOSOME_NAMES,
    SexChromosome,
    hemizygous_chromosome,
    pseudoautosomal_regions,
)
from .sv_copy_change import copy_change
from .family_variant_filters import (
    SmallVariantQueryFilters,
    SmallVariantSampleFilter,
    StructuralVariantQueryFilters,
    in_silico_prediction_pattern,
    is_translocation_sv_type,
    parse_small_variant_sample_filter,
    parse_structural_sample_filter,
    structural_variant_length,
    structural_variant_length_filters_match,
)
from .variant_prioritization import (
    MODE_COMPOUND_HET,
    MODE_DE_NOVO,
    MODE_DOMINANT,
    MODE_HOM_RECESSIVE,
    MODE_X_LINKED,
)
from .clinvar_release_store import (
    clinvar_allele_condition,
    clinvar_allele_where_condition,
    clinvar_alleles_held_condition,
)
from .clinvar_review_status import clinvar_conditions, clinvar_stars
from .clickhouse_variant_records import (
    CLINVAR_OVERRIDE_TERMS,
    Region,
    PanelFilterConstraints,
    SmallSegregationFlags,
    SmallVariantCall,
    SmallVariantRecord,
    SmallVariantCarrierGene,
    SmallVariantCompoundHetGene,
    SmallVariantCompoundHetPair,
    StructuralVariantRecord,
    _casefold,
    _contains_casefold,
    _status_filter_terms,
    _coerce_float,
    _annotation_terms,
    _annotation_text,
    _annotation_float,
    _annotation_gnomad_annotated,
    _annotation_int,
    _annotation_bool,
    _annotation_rank,
    _annotation_population_frequencies,
    _annotation_extra,
    _annotation_gene,
    _annotation_gene_id,
    _annotation_effect,
    _annotation_clinvar,
    _annotation_sift,
    _annotation_polyphen,
    _annotation_spliceai_max,
    mane_only_condition,
)
from .vcf_fields import field_int


class _SearchAssembly(Protocol):
    """What a condition over the shared annotation tables reads of its search: the
    assembly whose tables it queries. A family's context, or the Variant Explorer's scope."""

    @property
    def assembly_name(self) -> str | None: ...


# Callset ``source`` values that hold imputed (not directly-called) genotypes.
# These are excluded by default from the diagnostic per-family small-variant list,
# counts, and summary (matching the global Variant Explorer's default), but remain
# available to callers that request a source explicitly — the phased-marker /
# relative-haplotype colouring and sample-integrity QC readers. The Variant Explorer
# hides the same sources unless asked to include them.
IMPUTED_SMALL_VARIANT_SOURCES: tuple[str, ...] = ("glimpse2", "shapeit")
# A monogenic NIPT's callset stored one variant-only VCF per sample (the plasma and the
# father; family_package_nipt): a sample without a call at a variant had no alt read
# there, and a Mutect2 tumour-only GT says 0/1 whatever the allele fraction, so a reader
# that needs genotypes takes them from the allele depths (vaf_genotype).
NIPT_SMALL_VARIANT_SOURCE: Final = "nipt"
PER_SAMPLE_SMALL_VARIANT_SOURCES: tuple[str, ...] = (NIPT_SMALL_VARIANT_SOURCE,)


def vaf_genotype(ad: Sequence[Any] | None, gt: str, *, min_depth: int = 10) -> str:
    """A per-sample call's genotype from its allele depths: hom-alt from an alt fraction
    of 0.80, het from 0.20 (the R NIPT-M classes), else reference; no call below
    ``min_depth`` reads. The GT itself when there are no allele depths."""
    depths = [int(value) for value in (ad or []) if value is not None]
    if len(depths) < 2:
        return gt
    total = sum(depths)
    if total < min_depth:
        return "./."
    fraction = depths[1] / total
    if fraction >= 0.80:
        return "1/1"
    if fraction >= 0.20:
        return "0/1"
    return "0/0"


# One entry of an interval list: chr:start-end, with a hyphen or an en dash, or chr:position
# for one base; thousands separators are allowed. A BED line ("chr start end") is not read:
# BED is 0-based, and taken as 1-based its interval would shift by a base.
_INTERVAL_PATTERN = re.compile(
    r"^\s*(?P<chr>[^:\s]+)\s*:\s*(?P<start>\d[\d,]*)\s*(?:[-\u2013]\s*(?P<end>\d[\d,]*)\s*)?$"
)


_GENE_QUERY_SPLIT = re.compile(r"[\s,;]+")


# Phase of a compound-het pair as derived from the caller's own phasing or the family's
# genotypes (compound_het_phase), distinct from the curator's `phase_status` on a saved
# group review. `cis` never reaches a response: a cis pair is not a compound-het
# candidate, so it is dropped rather than reported.
COMPOUND_HET_PHASE_TRANS = PHASE_TRANS
COMPOUND_HET_PHASE_CIS = PHASE_CIS
COMPOUND_HET_PHASE_UNKNOWN = PHASE_UNKNOWN


# Minimum parental depth to trust a homozygous-reference call when calling de novo;
# a low-coverage ref parent could be a missed heterozygote (false de novo). Tracing a
# compound-het pair through the parents asks the same of a reference call.
_DE_NOVO_MIN_PARENT_DP = CONFIDENT_REFERENCE_MIN_DP


# Chromosome X as the entries table may name it, which the X-linked and homozygous recessive
# modes read (``_small_inheritance_mode_clauses``).
_X_CHROMOSOME_NAMES: Final[tuple[str, ...]] = ("X", "chrX", "23", "chr23")


_COMPOUND_HET_INHERITANCE = "compound_het"


_RECESSIVE_INHERITANCE = "recessive"


_RECESSIVE_HOMOZYGOUS_INHERITANCE = "recessive_homozygous"


_DE_NOVO_DOMINANT_INHERITANCE = "de_novo_dominant"


_X_LINKED_INHERITANCE = "x_linked"


# Every variant the other filters keep, as with no mode, with the compound-het pairs among them
# listed by gene, as the compound-het mode lists them (``_inheritance_result_items``).
_ALL_COMPOUND_HET_GROUPED_INHERITANCE = "all_compound_het_grouped"


_PAIR_BASED_SMALL_INHERITANCE = {
    _COMPOUND_HET_INHERITANCE,
    _RECESSIVE_INHERITANCE,
    _ALL_COMPOUND_HET_GROUPED_INHERITANCE,
}


_SMALL_INHERITANCE_MIN_CANDIDATE_ROWS = 1000


_SMALL_INHERITANCE_MAX_CANDIDATE_ROWS = 5000


_SMALL_INHERITANCE_PAGE_CANDIDATE_MULTIPLIER = 25


_SMALL_COUNT_LIMIT = 10001


# Other spellings of a mode, read as that mode. "de_novo" is none: it is the NIPT list's fetal de
# novo view (nipt_service.DE_NOVO), another filter than the family's de novo / dominant mode, so a
# search saved on the NIPT page that reaches this list is refused, as the NIPT list's other views
# are. The family small-variant page sends a mode only by these names
# (SMALL_VARIANT_INHERITANCE_MODES and SMALL_VARIANT_INHERITANCE_ALIASES in smallVariantSearch.ts);
# test_small_variant_page_sends_what_the_list_reads.py keeps the lists equal.
_SMALL_INHERITANCE_ALIASES = {
    "compound_heterozygous": _COMPOUND_HET_INHERITANCE,
    "recessive_hom": _RECESSIVE_HOMOZYGOUS_INHERITANCE,
    "homozygous_recessive": _RECESSIVE_HOMOZYGOUS_INHERITANCE,
    "dominant": _DE_NOVO_DOMINANT_INHERITANCE,
    "xlinked": _X_LINKED_INHERITANCE,
    "x_linked_recessive": _X_LINKED_INHERITANCE,
}


_SUPPORTED_SMALL_INHERITANCE = {
    _COMPOUND_HET_INHERITANCE,
    _RECESSIVE_INHERITANCE,
    _ALL_COMPOUND_HET_GROUPED_INHERITANCE,
    _RECESSIVE_HOMOZYGOUS_INHERITANCE,
    _DE_NOVO_DOMINANT_INHERITANCE,
    _X_LINKED_INHERITANCE,
}


def _small_annotation_table_name(assembly_name: str) -> str:
    return _small_table_name(assembly_name, "variants/annotations")


def _small_annotation_index_table_name(assembly_name: str) -> str:
    return _small_table_name(assembly_name, "variants/annotation_index")


def _small_annotation_gene_index_table_name(assembly_name: str) -> str:
    return _small_table_name(assembly_name, "variants/gene_index")


def _small_summary_table_name(assembly_name: str, suffix: str) -> str:
    return _small_table_name(assembly_name, suffix)


def _append_unique(values: list[str], value: Any) -> None:
    text_value = str(value or "").strip()
    if text_value and text_value not in values:
        values.append(text_value)


def _clickhouse_sample_ids(sample_names: Iterable[str]) -> list[str]:
    """The IDs the calls of these samples are stored under in ClickHouse: each sample's
    ID, once."""
    sample_ids: list[str] = []
    for sample_name in sample_names:
        _append_unique(sample_ids, sample_name)
    return sample_ids


def _visible_clickhouse_sample_ids(context: FamilyMetadataContext) -> list[str]:
    """The IDs of the calls the family shows: those of its active members
    (``build_family_metadata_context``). The Variant Explorer reads each family's calls by
    the same rule (``variant_explorer_service._shown_calls``)."""
    return _clickhouse_sample_ids(context.sample_name_to_uuid)


def _stored_sample_id(stored_sample_id: Any) -> str:
    """The sample ID a stored call names its sample by."""
    return str(stored_sample_id or "").strip()


def _chromosome_options(chromosome: str) -> tuple[str, ...]:
    return tuple(chromosome_aliases(chromosome))


def _chromosome_match_key(chromosome: str) -> str:
    normalized = normalize_chromosome(chromosome).upper()
    return "MT" if normalized in {"M", "MT"} else normalized


def _clickhouse_chromosome_match_expr(expr: str) -> str:
    stripped = f"upper(if(startsWith(lower({expr}), 'chr'), substring({expr}, 4), {expr}))"
    return f"if({stripped} IN ('M', 'MT'), 'MT', {stripped})"


def _string_list(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, (list, tuple)):
        source = values
    else:
        source = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in source:
        text_value = str(value or "").strip()
        if not text_value or text_value in seen:
            continue
        seen.add(text_value)
        result.append(text_value)
    return result


def _listify(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _indexed(values: Any, index: int) -> Any:
    sequence = _listify(values)
    if index < len(sequence):
        return sequence[index]
    return None


def _int_list(value: Any) -> list[int]:
    result: list[int] = []
    for item in _listify(value):
        parsed = field_int(item)
        if parsed is not None:
            result.append(parsed)
    return result


def _float_list(value: Any) -> list[float]:
    result: list[float] = []
    for item in _listify(value):
        parsed = _coerce_float(item)
        if parsed is not None:
            result.append(parsed)
    return result


def _decode_json_payload(raw_value: Any) -> Any:
    if raw_value in (None, "", b""):
        return None
    if isinstance(raw_value, (dict, list)):
        return raw_value
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode()
    try:
        return json.loads(str(raw_value))
    except json.JSONDecodeError:
        return None


def _collect_annotations(payload: Any) -> list[dict[str, Any]]:
    """The annotations of a stored ``annotationsJson``, as the writer stores them
    (``clickhouse_variant_rows._json_payload``: ``{"annotations": [...]}``); none for a row
    read without one."""
    if not isinstance(payload, dict):
        return []
    return [item for item in payload["annotations"] if isinstance(item, dict)]


def _select_primary_annotation(annotations: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return max(annotations, key=_annotation_rank, default={})


def _transcript_source(transcript_id: str | None) -> str | None:
    value = str(transcript_id or "").strip().upper()
    if not value:
        return None
    if value.startswith("ENST"):
        return "Ensembl"
    if value.startswith(("NM_", "NR_", "XM_", "XR_", "NG_")):
        return "RefSeq"
    return "Other"


def _small_transcript_annotations(
    annotations: Sequence[dict[str, Any]],
    primary_annotation: dict[str, Any],
) -> list[SmallVariantTranscriptOut]:
    transcripts: list[SmallVariantTranscriptOut] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for annotation in sorted(annotations, key=_annotation_rank, reverse=True):
        transcript_id = _annotation_text(annotation, "transcript_id")
        hgvsc = _annotation_text(annotation, "hgvsc")
        hgvsp = _annotation_text(annotation, "hgvsp")
        effect = _annotation_effect(annotation)
        impact = _annotation_text(annotation, "impact")
        alt_allele = _annotation_text(annotation, "alt_allele")
        if not any((transcript_id, hgvsc, hgvsp, effect, impact)):
            continue
        key = (
            transcript_id or "",
            hgvsc or "",
            hgvsp or "",
            effect or "",
            impact or "",
            alt_allele or "",
        )
        if key in seen:
            continue
        seen.add(key)
        # The primary annotation's fields are already carried by the flat VariantOut
        # fields (transcript_id / hgvsc / hgvsp / effect / ...), so omit it here
        # instead of serializing it twice per variant. The frontend reconstructs the
        # primary entry from those flat fields.
        if annotation is primary_annotation:
            continue
        transcripts.append(
            SmallVariantTranscriptOut(
                alt_allele=alt_allele,
                gene=_annotation_gene(annotation),
                gene_id=_annotation_gene_id(annotation),
                transcript_id=transcript_id,
                transcript_source=_transcript_source(transcript_id),
                feature_type=_annotation_text(annotation, "feature_type"),
                transcript_biotype=_annotation_text(annotation, "transcript_biotype"),
                impact=impact,
                effect=effect,
                hgvsc=hgvsc,
                hgvsp=hgvsp,
                exon=_annotation_text(annotation, "exon"),
                intron=_annotation_text(annotation, "intron"),
                canonical=_annotation_bool(annotation, "canonical"),
                mane_select=_annotation_bool(annotation, "mane_select"),
                mane_plus_clinical=_annotation_bool(annotation, "mane_plus_clinical"),
                mane_refseq_id=_annotation_text(annotation, MANE_REFSEQ_KEY),
                primary=False,
            )
        )
    return transcripts


def _normalize_gt(value: Any) -> str:
    """A stored genotype as the variant list shows it: as stored, ``./.`` for none. Only VCF's
    allele notation is stored (the import refuses a word such as ``HET``), and none
    is turned into another: a word read as a genotype here would be a no-call to every filter."""
    return str(value or "").strip() or "./."


def _small_type(ref: str, alt: str) -> str:
    if len(ref) == 1 and len(alt) == 1:
        return "SNV"
    return "INDEL"


def _split_gene_terms(raw_value: str | None) -> list[str]:
    return [term for term in _GENE_QUERY_SPLIT.split(str(raw_value or "").strip()) if term]


def _parse_interval_regions(raw_value: str | None, *, label: str = "Interval") -> list[Region]:
    """The regions of an interval list, one per line or ``;``-separated entry.

    An entry that cannot be read as written is refused (422) and named: skipped, it would
    leave the search covering less than the list asks. Blank entries are ignored.
    """
    regions: list[Region] = []
    for raw_entry in re.split(r"[\n;]+", str(raw_value or "")):
        entry = raw_entry.strip()
        if not entry:
            continue
        match = _INTERVAL_PATTERN.match(entry)
        if not match:
            raise HTTPException(
                status_code=422,
                detail=f"{label} {entry!r} is not chr:start-end.",
            )
        start = int(match.group("start").replace(",", ""))
        end = int((match.group("end") or match.group("start")).replace(",", ""))
        if end < start:
            raise HTTPException(
                status_code=422,
                detail=f"{label} {entry!r} ends before it starts.",
            )
        regions.append(Region(chr=normalize_chromosome(match.group("chr")), start=start, end=end))
    return regions


def _chromosome_filter_matches(chromosome: str, filter_chromosome: str) -> bool:
    """Whether a variant's chromosome is the one a chromosome filter names, as the SQL reads
    it (``e.chrom IN`` the filter's ``_chromosome_options``): the name with or without
    ``chr``, and chrM by any of its names (M, MT)."""
    options = {normalize_chromosome(option) for option in _chromosome_options(filter_chromosome)}
    return normalize_chromosome(chromosome) in options


def _variant_overlaps_regions(chr_value: str, start: int, end: int, regions: Sequence[Region]) -> bool:
    """Whether a variant overlaps one of the regions. The chromosomes are compared by
    ``_chromosome_match_key``, as the SQL region conditions compare them: in any capitals,
    with or without ``chr``, and chrM by any of its names, so an interval typed as ``x:…`` or
    a region on chrMT keeps what the SQL keeps. A region reaches from the lower of its two
    coordinates to the higher, as the SQL reads it."""
    chromosome = _chromosome_match_key(chr_value)
    return any(
        chromosome == _chromosome_match_key(region.chr)
        and start <= max(region.start, region.end)
        and end >= min(region.start, region.end)
        for region in regions
    )


def _variant_hits_gene_symbols(gene_symbols: Sequence[str], query: str | None) -> bool:
    terms = {_casefold(term) for term in _split_gene_terms(query)}
    if not terms:
        return True
    return bool({_casefold(symbol) for symbol in gene_symbols}.intersection(terms))


def _gene_term_set(terms: Iterable[Any]) -> frozenset[str]:
    """Gene names as the SV record filter compares them: casefolded, the blank ones left out."""
    return frozenset(_casefold(term) for term in terms if str(term).strip())


@dataclass(frozen=True, slots=True)
class _RegionLookup:
    """Regions prepared to answer ``_variant_overlaps_regions`` by bisection: per chromosome
    (by ``_chromosome_match_key``, as that function compares them), the regions' starts in
    ascending order and, at each, the furthest end of the regions up to it.

    The regions that start at or before a variant's end are the first ones in that order,
    and one of them ends at or after the variant's start exactly when the furthest end among
    them does: the answer of testing every region, without testing every region."""

    starts: dict[str, list[int]]
    furthest_ends: dict[str, list[int]]

    @classmethod
    def of(cls, regions: Iterable[Region]) -> _RegionLookup:
        bounds: dict[str, list[tuple[int, int]]] = {}
        for region in regions:
            bounds.setdefault(_chromosome_match_key(region.chr), []).append((region.start, region.end))
        return cls._of_keyed(bounds)

    @classmethod
    def of_bounds(cls, chromosomes: Sequence[str], starts: Sequence[int], ends: Sequence[int]) -> _RegionLookup:
        """The lookup of regions read as a region condition binds them: parallel lists of
        chromosomes, already by their match key, and of starts and ends."""
        bounds: dict[str, list[tuple[int, int]]] = {}
        for chromosome, start, end in zip(chromosomes, starts, ends):
            bounds.setdefault(chromosome, []).append((start, end))
        return cls._of_keyed(bounds)

    @classmethod
    def _of_keyed(cls, bounds: dict[str, list[tuple[int, int]]]) -> _RegionLookup:
        starts: dict[str, list[int]] = {}
        furthest_ends: dict[str, list[int]] = {}
        for chromosome, unordered in bounds.items():
            # Each region from the lower of its coordinates to the higher, as
            # _variant_overlaps_regions and the SQL read it.
            chromosome_bounds = sorted((min(start, end), max(start, end)) for start, end in unordered)
            starts[chromosome] = [start for start, _end in chromosome_bounds]
            furthest: list[int] = []
            for _start, end in chromosome_bounds:
                furthest.append(end if not furthest or end > furthest[-1] else furthest[-1])
            furthest_ends[chromosome] = furthest
        return cls(starts=starts, furthest_ends=furthest_ends)

    def overlaps(self, chr_value: str, start: int, end: int) -> bool:
        """``_variant_overlaps_regions(chr_value, start, end, regions)``."""
        chromosome = _chromosome_match_key(chr_value)
        starts = self.starts.get(chromosome)
        if not starts:
            return False
        reached = bisect_right(starts, end)
        return reached > 0 and self.furthest_ends[chromosome][reached - 1] >= start


class _StructuralPanelMatch:
    """A gene panel as the structural-variant record filter applies it
    (``_structural_record_matches``), prepared once per search: an SV whose annotation names
    one of the panel's genes (its entry's gene symbols, in any capitals), or that overlaps
    one of its regions by the SV region rule (``_structural_region_filter_condition``), is
    kept; a panel with neither keeps no SV. The SQL applies the same rule to the same
    regions (``_structural_panel_filter_condition``), whatever the panel's size. Matched on
    its genes alone, a panel would miss an SV in one of its genes whose annotation names that
    gene otherwise; matched on its regions alone, an SV whose annotation names one of its
    genes beside it (VEP's upstream and downstream genes).

    The regions are read only for an SV none of the panel's genes names, through a lookup
    built on first use from the regions the SQL binds (``_structural_region_bounds``), which
    answers by bisection what testing each region answers."""

    __slots__ = ("gene_terms", "_bounds", "_regions")

    def __init__(self, constraints: PanelFilterConstraints) -> None:
        self.gene_terms = _gene_term_set(constraints.genes)
        self._bounds = constraints.regions
        self._regions: _RegionLookup | None = None

    def keeps(self, record: StructuralVariantRecord) -> bool:
        if self.gene_terms and not self.gene_terms.isdisjoint(
            _casefold(symbol) for symbol in record.gene_symbols
        ):
            return True
        if not self._bounds:
            return False
        if self._regions is None:
            self._regions = _RegionLookup.of_bounds(*_structural_region_bounds(self._bounds))
        return self._regions.overlaps(record.chr, record.start, record.end)


_STRUCTURAL_REGION_FLAG_KEYS = (
    "UTR",
    "CDS",
    "ORegAnno",
    "TRE",
    "Centromeric",
    "Pericentromeric",
    "Telomeric",
    "Segdup",
    "Repeat",
    "Gap",
    "Homopolymer",
    "HiConf",
)


def _split_info_terms(value: Any) -> list[str]:
    if value in (None, "", "."):
        return []
    if isinstance(value, (list, tuple)):
        terms: list[str] = []
        for item in value:
            terms.extend(_split_info_terms(item))
        return terms
    return [
        item.strip()
        for item in re.split(r"[,|]+", str(value))
        if item.strip() and item.strip() != "."
    ]


def _first_float_from_info(value: Any) -> float | None:
    parsed = _coerce_float(value)
    if parsed is not None:
        return parsed
    for term in _split_info_terms(value):
        parsed = _coerce_float(term)
        if parsed is not None:
            return parsed
    return None


def _structural_info_payloads(record: StructuralVariantRecord) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for annotation in record.annotations:
        info = annotation.get("info")
        if isinstance(info, dict):
            payloads.append(info)
        else:
            payloads.append(annotation)
    return payloads


def _structural_info_value(record: StructuralVariantRecord, *keys: str) -> Any:
    for info in _structural_info_payloads(record):
        for key in keys:
            if key in info and info[key] not in (None, "", "."):
                return info[key]
    return None


def _structural_info_terms(record: StructuralVariantRecord, *keys: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for info in _structural_info_payloads(record):
        for key in keys:
            for term in _split_info_terms(info.get(key)):
                folded = _casefold(term)
                if folded in seen:
                    continue
                seen.add(folded)
                terms.append(term)
    return terms


def _structural_info_text(record: StructuralVariantRecord, *keys: str) -> str | None:
    terms = _structural_info_terms(record, *keys)
    if terms:
        return ", ".join(terms)
    value = _structural_info_value(record, *keys)
    text_value = str(value or "").strip()
    return text_value or None


def _structural_info_float(record: StructuralVariantRecord, *keys: str) -> float | None:
    for info in _structural_info_payloads(record):
        for key in keys:
            parsed = _first_float_from_info(info.get(key))
            if parsed is not None:
                return parsed
    return None


def _structural_pli(record: StructuralVariantRecord) -> float | None:
    values = [
        parsed
        for parsed in (
            _first_float_from_info(term)
            for term in _structural_info_terms(record, "pLI", "pli", "gene_pli")
        )
        if parsed is not None
    ]
    return max(values) if values else None


def _structural_region_flags(record: StructuralVariantRecord) -> list[str]:
    flags: list[str] = []
    for key in _STRUCTURAL_REGION_FLAG_KEYS:
        value = _structural_info_value(record, key)
        if value in (None, "", ".", "0"):
            continue
        if isinstance(value, str) and _casefold(value) in {"false", "no", "n"}:
            continue
        flags.append(key)
    return flags


def _structural_population_frequencies(record: StructuralVariantRecord) -> dict[str, float]:
    result: dict[str, float] = {}
    for key in (
        "Allele_Freq_ALL_Control",
        "Allele_Freq_ALL",
        "Pop_Freq_ALL",
        "q",
        "Allele_Freq_AFR",
        "Allele_Freq_AMR",
        "Allele_Freq_EAS",
        "Allele_Freq_EUR",
        "Allele_Freq_SAS",
        "Pop_Freq_AFR",
        "Pop_Freq_AMR",
        "Pop_Freq_EAS",
        "Pop_Freq_EUR",
        "Pop_Freq_SAS",
    ):
        parsed = _structural_info_float(record, key)
        if parsed is not None:
            result[key] = parsed
    return result


def _structural_annotation_extra(
    record: StructuralVariantRecord, *, track_mode: bool = False
) -> dict[str, Any]:
    # The genome SV track draws one rectangle per variant from chr/start/end/type and
    # the per-sample genotype; it never reads annotation_extra or population_frequencies.
    # Parsing the (often multi-KB) per-variant annotation JSON for tens of thousands of
    # SVs is what made the track payload ~182 MB / 8.7s per member, so skip it entirely.
    if track_mode:
        return {}
    population_frequencies = _structural_population_frequencies(record)
    region_flags = _structural_region_flags(record)
    read_depths = {
        key: value
        for key, value in {
            "query_ref_reads": field_int(_structural_info_value(record, "Ref_Reads")),
            "query_total_reads": field_int(_structural_info_value(record, "Total_Reads")),
            "maternal_ref_reads": field_int(_structural_info_value(record, "Maternal_Ref_Reads")),
            "maternal_total_reads": field_int(_structural_info_value(record, "Maternal_Total_Reads")),
            "paternal_ref_reads": field_int(_structural_info_value(record, "Paternal_Ref_Reads")),
            "paternal_total_reads": field_int(_structural_info_value(record, "Paternal_Total_Reads")),
        }.items()
        if value is not None
    }
    genotype_counts = {
        key: value
        for key, value in {
            "hom_ref": field_int(_structural_info_value(record, "GT_homWT")),
            "het": field_int(_structural_info_value(record, "GT_het")),
            "hom_alt": field_int(_structural_info_value(record, "GT_homVAR")),
        }.items()
        if value is not None
    }
    return {
        key: value
        for key, value in {
            "inheritance": _structural_info_text(record, "Inheritance"),
            "query_id": _structural_info_text(record, "Query_ID"),
            "control_support": _structural_info_text(record, "Control_support"),
            "omim_phenotype": _structural_info_text(record, "OMIM_phenotype"),
            "omim_moi": _structural_info_text(record, "OMIM_MOI"),
            "gencc_phenotype": _structural_info_text(record, "GENCC_phenotype"),
            "gencc_support": _structural_info_text(record, "GENCC_support"),
            "gencc_moi": _structural_info_text(record, "GENCC_MOI"),
            "hpo_terms": _structural_info_text(record, "HPO_terms"),
            "pli": _structural_pli(record),
            "region_flags": region_flags,
            "control_af": population_frequencies.get("Allele_Freq_ALL_Control"),
            "population_af": population_frequencies.get("Allele_Freq_ALL")
            or population_frequencies.get("Pop_Freq_ALL")
            or population_frequencies.get("q"),
            "population_frequencies": population_frequencies,
            "genotype_counts": genotype_counts,
            "read_depths": read_depths,
            "hwe": _structural_info_text(record, "HWE"),
        }.items()
        if value not in (None, "", [], {})
    }


def _structural_record_matches_sample_filters(
    record: StructuralVariantRecord,
    filters: StructuralVariantQueryFilters,
) -> bool:
    call_map = {call.sample: call for call in record.calls}
    for entry in filters.sample_filters:
        sample_filter = parse_structural_sample_filter(entry)
        if sample_filter is None:
            continue
        call = call_map.get(sample_filter.sample_name)
        if call is None:
            if sample_filter.include_absent:
                continue
            return False
        if (
            sample_filter.genotype_classes
            and classify_genotype(call.gt) not in sample_filter.genotype_classes
        ):
            return False
        if sample_filter.minimum_quality is not None:
            if call.qual is None or call.qual < sample_filter.minimum_quality:
                return False
        if sample_filter.read_support is not None:
            try:
                read_support_threshold: float | None = float(sample_filter.read_support)
            except (TypeError, ValueError):
                read_support_threshold = None
            if read_support_threshold is not None and (
                call.read_support is None or call.read_support < read_support_threshold
            ):
                return False
        if sample_filter.filter_text and not _contains_casefold(call.filter, sample_filter.filter_text):
            return False
    return True


def _structural_annotation_contains(
    record: StructuralVariantRecord,
    query: str | None,
    *keys: str,
) -> bool:
    if not query:
        return True
    terms = _split_gene_terms(query)
    if not terms:
        return True
    haystack = " ".join(
        str(value)
        for key in keys
        for value in _structural_info_terms(record, key)
    )
    return all(_contains_casefold(haystack, term) for term in terms)


def _structural_record_matches_annotations(
    record: StructuralVariantRecord,
    filters: StructuralVariantQueryFilters,
) -> bool:
    if filters.inheritance and not _contains_casefold(
        _structural_info_text(record, "Inheritance"),
        filters.inheritance,
    ):
        return False
    if filters.phenotype and not _structural_annotation_contains(
        record,
        filters.phenotype,
        "OMIM_phenotype",
        "GENCC_phenotype",
    ):
        return False
    if filters.hpo and not _structural_annotation_contains(record, filters.hpo, "HPO_terms"):
        return False
    if filters.moi and not _structural_annotation_contains(record, filters.moi, "OMIM_MOI", "GENCC_MOI"):
        return False
    if filters.gencc_support and not _contains_casefold(
        _structural_info_text(record, "GENCC_support"),
        filters.gencc_support,
    ):
        return False
    if filters.region_flags:
        present_flags = {_casefold(flag) for flag in _structural_region_flags(record)}
        requested_flags = {_casefold(flag) for flag in filters.region_flags if str(flag).strip()}
        if requested_flags and not requested_flags.intersection(present_flags):
            return False
    if filters.max_control_af is not None:
        control_af = _structural_info_float(record, "Allele_Freq_ALL_Control")
        if control_af is not None and control_af > filters.max_control_af:
            return False
    if filters.max_population_af is not None:
        population_values = [
            value
            for value in (
                _structural_info_float(record, "Allele_Freq_ALL"),
                _structural_info_float(record, "Pop_Freq_ALL"),
                _structural_info_float(record, "q"),
                _structural_info_float(record, "Allele_Freq_ALL_Control"),
            )
            if value is not None
        ]
        if population_values and max(population_values) > filters.max_population_af:
            return False
    if filters.min_pli is not None:
        pli = _structural_pli(record)
        if pli is None or pli < filters.min_pli:
            return False
    return True


def _structural_record_matches(
    record: StructuralVariantRecord,
    filters: StructuralVariantQueryFilters,
    include_regions: Sequence[Region],
    selected_samples: Sequence[str],
    *,
    panel: _StructuralPanelMatch | None = None,
) -> bool:
    """Whether the search's ``filters``, its regions (a gene filter's loci) and its gene
    panel (``panel``, prepared once per search) keep ``record``."""
    # As the SQL reads the filter (its aliases), so chrM is chrM by any of its names: MT and M
    # keep the same SVs.
    if filters.chromosome and not _chromosome_filter_matches(record.chr, filters.chromosome):
        return False
    if filters.overlap:
        if filters.start is not None and record.end < filters.start:
            return False
        if filters.end is not None and record.start > filters.end:
            return False
    else:
        if filters.start is not None and record.start < filters.start:
            return False
        if filters.end is not None and record.end > filters.end:
            return False
    if filters.variant_type:
        wanted_types = [value.strip() for value in filters.variant_type.split(",") if value.strip()]
        if wanted_types and not any(
            _contains_casefold(record.sv_type, value) for value in wanted_types
        ):
            return False
    if filters.source and not _contains_casefold(record.source, filters.source):
        return False
    if not structural_variant_length_filters_match(
        sv_type=record.sv_type,
        sv_len=record.sv_len,
        start=record.start,
        end=record.end,
        length=filters.length,
        min_length=filters.min_length,
    ):
        return False
    if filters.remote_chr and normalize_chromosome(record.remote_chr or "") != normalize_chromosome(filters.remote_chr):
        return False
    if filters.remote_start is not None and (record.remote_start is None or record.remote_start < filters.remote_start):
        return False
    if include_regions and not _variant_overlaps_regions(record.chr, record.start, record.end, include_regions):
        return False
    if panel is not None and not panel.keeps(record):
        return False
    if filters.gene and not _variant_hits_gene_symbols(record.gene_symbols, filters.gene):
        return False
    if selected_samples and not any(call.sample in set(selected_samples) for call in record.calls):
        return False
    if not _structural_record_matches_annotations(record, filters):
        return False
    return _structural_record_matches_sample_filters(record, filters)


def _primary_gene_keys(record: SmallVariantRecord) -> tuple[str | None, str | None]:
    annotation = _select_primary_annotation(record.annotations)
    gene = _annotation_gene(annotation) or (record.gene_symbols[0] if record.gene_symbols else None)
    gene_id = _annotation_gene_id(annotation)
    return gene, gene_id


# What a variant's gene is matched by: its gene id and its symbol, each on its own.
GeneMatchKeys = tuple[tuple[str, str], ...]


def _gene_match_keys(record: SmallVariantRecord) -> GeneMatchKeys:
    """The gene id and the symbol of a variant's card's gene (``_primary_gene_keys``), each a
    key its gene is matched by; none for a variant without a gene."""
    gene, gene_id = _primary_gene_keys(record)
    return tuple((kind, value) for kind, value in (("gene_id", gene_id), ("gene", gene)) if value)


def _joined_genes(key_sets: Sequence[GeneMatchKeys]) -> list[tuple[str, str]]:
    """The gene of each of ``key_sets`` (none empty), named by one of its keys: the sets that
    share a gene id or a symbol, directly or through another set, are one gene. A renamed
    gene whose annotations carry the old and the new symbol under one id is one gene."""
    joined: dict[tuple[str, str], tuple[str, str]] = {}

    def gene_of(key: tuple[str, str]) -> tuple[str, str]:
        while joined.setdefault(key, key) != key:
            key = joined[key]
        return key

    for keys in key_sets:
        first = gene_of(keys[0])
        for key in keys[1:]:
            other = gene_of(key)
            if other != first:
                joined[other] = first
    return [gene_of(keys[0]) for keys in key_sets]


def _gene_count(key_sets: Iterable[GeneMatchKeys]) -> int:
    """How many genes variants are in, given each one's ``_gene_match_keys``: each variant
    counts toward its card's gene, variants matched to one gene as ``_joined_genes`` joins
    them (as the carrier screen lists its genes), and a variant without a gene toward none."""
    return len(set(_joined_genes([keys for keys in key_sets if keys])))


def _chromosome_sort_key(chromosome: str) -> tuple[int, int, str]:
    cleaned = normalize_chromosome(chromosome).upper()
    numeric = field_int(cleaned)
    if numeric is not None:
        return 0, numeric, cleaned
    if cleaned == "X":
        return 1, 23, cleaned
    if cleaned == "Y":
        return 1, 24, cleaned
    if cleaned in {"MT", "M"}:
        return 1, 25, cleaned
    return 9, 0, cleaned


def _small_record_sort_key(record: SmallVariantRecord) -> tuple[int, int, str, int, int, str, str]:
    gene, gene_id = _primary_gene_keys(record)
    return (
        *_chromosome_sort_key(record.chr),
        record.start,
        record.end,
        _casefold(gene_id or gene),
        record.variant_id,
    )


def _sample_small_track_records(
    records: Sequence[SmallVariantRecord],
    limit: int,
) -> list[SmallVariantRecord]:
    if limit <= 0 or len(records) <= limit:
        return list(records)
    if limit == 1:
        return [records[0]]
    last_index = len(records) - 1
    return [
        records[(index * last_index) // (limit - 1)]
        for index in range(limit)
    ]


def _resolve_compound_het_pair_gene_labels(
    left: SmallVariantRecord,
    right: SmallVariantRecord,
) -> tuple[str | None, str | None]:
    left_gene, left_gene_id = _primary_gene_keys(left)
    right_gene, right_gene_id = _primary_gene_keys(right)
    gene_id = (
        left_gene_id
        if left_gene_id and right_gene_id and left_gene_id == right_gene_id
        else left_gene_id or right_gene_id
    )
    gene = (
        left_gene
        if left_gene and right_gene and _casefold(left_gene) == _casefold(right_gene)
        else left_gene or right_gene
    )
    return gene, gene_id


def _compound_het_gene_keys(record: SmallVariantRecord) -> list[tuple[str, str]]:
    gene, gene_id = _primary_gene_keys(record)
    keys: list[tuple[str, str]] = []
    if gene_id:
        keys.append(("gene_id", gene_id))
    if gene:
        keys.append(("gene", _casefold(gene)))
    return keys


def _small_call_map(record: SmallVariantRecord) -> dict[str, SmallVariantCall]:
    return {call.sample: call for call in record.calls}


# Genotype classes come from services/genotypes.py, so a haploid call (chrM, male
# chrX/chrY) and a multi-allelic one are judged like any other.
def _call_is_het(call: SmallVariantCall | None) -> bool:
    return call is not None and classify_genotype(call.gt) == HET


def _call_has_alt(call: SmallVariantCall | None) -> bool:
    return call is not None and genotype_has_alt(call.gt)


def _call_is_confident_hom_ref(call: SmallVariantCall | None) -> bool:
    """A genotyped homozygous-reference call with enough depth to trust it.

    A missing call (``./.``) is not confident reference, so it does not qualify — we
    cannot rule out an uncalled inherited allele.
    """
    if call is None or classify_genotype(call.gt) != HOM_REF:
        return False
    return call.dp is None or call.dp >= _DE_NOVO_MIN_PARENT_DP


def _parent_child_links(context: FamilyMetadataContext) -> list[tuple[str, str, str]]:
    """The pedigree's (child, parent, "mother" | "father") links."""
    links: list[tuple[str, str, str]] = []
    for relationship in context.relationship_rows or []:
        if str(relationship.get("relationship_type")) != "parent_child":
            continue
        sample_a = relationship.get("sample_id_a")
        sample_b = relationship.get("sample_id_b")
        role_a = str(relationship.get("role_a") or "").lower()
        role_b = str(relationship.get("role_b") or "").lower()
        if role_b == "child" and role_a in {"mother", "father"} and sample_a and sample_b:
            links.append((str(sample_b), str(sample_a), role_a))
        elif role_a == "child" and role_b in {"mother", "father"} and sample_a and sample_b:
            links.append((str(sample_a), str(sample_b), role_b))
    return links


def _child_parent_map(context: FamilyMetadataContext) -> dict[str, set[str]]:
    """Map each child sample name to its parent sample names from the pedigree."""
    parents: dict[str, set[str]] = {}
    for child, parent, _role in _parent_child_links(context):
        parents.setdefault(child, set()).add(parent)
    return parents


def _parent_roles(context: FamilyMetadataContext) -> dict[str, str]:
    """Map each parent sample name to "mother" or "father" from the pedigree."""
    return {parent: role for _child, parent, role in _parent_child_links(context)}


def _family_pedigree(context: FamilyMetadataContext) -> FamilyPedigree:
    """The pedigree the compound-het phase is traced through (compound_het_phase).

    The same parent links as the de novo check, so both read one pedigree.
    """
    sample_sex = _sample_sex_map(context.sample_rows)
    return FamilyPedigree(
        parents_of={child: frozenset(parents) for child, parents in _child_parent_map(context).items()},
        males=frozenset(sample for sample, value in sample_sex.items() if _is_male_sex(value)),
        assembly_name=context.assembly_name,
    )


def _call_carriage(call: SmallVariantCall | None) -> Carriage:
    """A sample's carriage of a small variant, for tracing a pair through the parents."""
    if call is None:
        return UNKNOWN_CARRIAGE
    return small_variant_carriage(call.gt, call.dp)


def _hemizygous_inheritance_ruled_out(
    call_map: dict[str, SmallVariantCall],
    *,
    parents: set[str],
    chromosome: SexChromosome,
    parent_roles: dict[str, str],
) -> bool:
    """For a son at a position where he is hemizygous: can neither parent have passed it on?

    His X comes from his mother and his Y from his father, so that parent must be
    confidently reference. The other parent does not pass that chromosome to a son and
    need not be genotyped, but must not carry the ALT: an ALT call there points to an
    artifact shared by the males, or a sample problem, not to a de novo event. When the
    pedigree does not name the parent who transmits it, both parents must be confidently
    reference, as for an autosome.
    """
    wanted = "mother" if chromosome == "X" else "father"
    transmitting = [parent for parent in parents if parent_roles.get(parent) == wanted]
    if len(transmitting) != 1:
        return len(parents) >= 2 and all(
            _call_is_confident_hom_ref(call_map.get(parent)) for parent in parents
        )
    if not _call_is_confident_hom_ref(call_map.get(transmitting[0])):
        return False
    return not any(
        _call_has_alt(call_map.get(parent)) for parent in parents if parent != transmitting[0]
    )


def _record_matches_de_novo(
    record: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    child_parents: dict[str, set[str]],
    sample_sex: dict[str, str] | None = None,
    parent_roles: dict[str, str] | None = None,
    assembly_name: str | None = None,
) -> bool:
    """True de novo: carried by an affected child, and by no parent who could have passed it on.

    On an autosome, in a PAR and in a daughter the child call must be heterozygous, and
    both parents confidently reference (a full trio: both genotyped and sufficiently
    covered); otherwise inheritance cannot be excluded and this returns False (the variant
    may still match the dominant pattern). A homozygous-alt child with reference parents is
    biologically implausible there (it would take two independent events) and almost always
    a repetitive-region artifact, so it is left to the homozygous-recessive pattern.

    A son is hemizygous on chrX and chrY outside the PARs, so there his call is an ALT call
    of either ploidy: haploid ``1``, ``1/1``, or ``0/1`` (e.g. mosaic). Whether a parent
    could have passed it on is then decided by :func:`_hemizygous_inheritance_ruled_out`.
    """
    call_map = _small_call_map(record)
    sex_of = sample_sex or {}
    roles = parent_roles or {}
    hemizygous_on = hemizygous_chromosome(assembly_name, record.chr, record.start)
    for child in affected_samples:
        parents = child_parents.get(child) or set()
        child_call = call_map.get(child)
        if hemizygous_on is not None and _is_male_sex(sex_of.get(child, "")):
            if _call_has_alt(child_call) and _hemizygous_inheritance_ruled_out(
                call_map, parents=parents, chromosome=hemizygous_on, parent_roles=roles
            ):
                return True
            continue
        if len(parents) < 2 or not _call_is_het(child_call):
            continue
        if all(_call_is_confident_hom_ref(call_map.get(parent)) for parent in parents):
            return True
    return False


def _sample_sex_map(sample_rows: Sequence[dict[str, Any]]) -> dict[str, str]:
    return {
        str(row.get("sample_id") or "").strip(): str(row.get("sex") or "").strip().lower()
        for row in sample_rows
        if str(row.get("sample_id") or "").strip()
    }


def _is_male_sex(value: str) -> bool:
    return value in {"m", "male", "1"}


def _pair_phase_for_sample(
    left_call: SmallVariantCall | None,
    right_call: SmallVariantCall | None,
) -> str | None:
    """``cis``/``trans`` when read-backed phasing resolves the pair, else None.

    Both calls must sit in the same phase set (the caller's PS tag) for their haplotype
    indices to be comparable at all — indices from different phase blocks say nothing
    about each other. Each call is read by ``compound_het_phase.phased_alt_haplotype``,
    as the SNV + SV second hit reads its calls.
    """
    if left_call is None or right_call is None:
        return None
    if left_call.ps is None or right_call.ps is None or left_call.ps != right_call.ps:
        return None
    left_haplotype = phased_alt_haplotype(left_call.gt)
    right_haplotype = phased_alt_haplotype(right_call.gt)
    if left_haplotype is None or right_haplotype is None:
        return None
    return COMPOUND_HET_PHASE_CIS if left_haplotype == right_haplotype else COMPOUND_HET_PHASE_TRANS


def _compound_het_pair_verdict(
    left: SmallVariantRecord,
    right: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    pedigree: FamilyPedigree | None = None,
) -> tuple[str, str | None] | None:
    """The pair's ``(phase, evidence)``, or None when it is not a compound-het candidate.

    Genotype rules first: every affected sample het for both, and no unaffected sample
    carrying both. Then read-backed phasing: a pair the reads place on the same haplotype
    in any affected sample is in cis, so that sample carries one intact copy of the gene
    and the pair cannot be the recessive explanation; opposite haplotypes are trans. Where
    the reads say nothing, the parents (``pedigree``) are traced by the rule the SNV + SV
    second hit also uses (compound_het_phase): both hits traced to one parent is cis and
    drops the pair, one hit to each parent is trans.
    """
    if left.variant_id == right.variant_id:
        return None
    if not affected_samples:
        return None

    left_calls = _small_call_map(left)
    right_calls = _small_call_map(right)

    if not all(
        _call_is_het(left_calls.get(sample)) and _call_is_het(right_calls.get(sample))
        for sample in affected_samples
    ):
        return None

    if any(
        _call_has_alt(left_calls.get(sample)) and _call_has_alt(right_calls.get(sample))
        for sample in unaffected_samples
    ):
        return None

    resolved = {
        phase
        for sample in affected_samples
        if (phase := _pair_phase_for_sample(left_calls.get(sample), right_calls.get(sample)))
    }
    if COMPOUND_HET_PHASE_CIS in resolved:
        return None
    if COMPOUND_HET_PHASE_TRANS in resolved:
        return COMPOUND_HET_PHASE_TRANS, PHASE_EVIDENCE_READ

    by_family = segregation_phase(
        affected=affected_samples,
        unaffected=unaffected_samples,
        first=lambda sample: _call_carriage(left_calls.get(sample)),
        second=lambda sample: _call_carriage(right_calls.get(sample)),
        pedigree=pedigree,
        loci=[(left.chr, left.start), (right.chr, right.start)],
    )
    if by_family == COMPOUND_HET_PHASE_CIS:
        return None
    if by_family == COMPOUND_HET_PHASE_TRANS:
        return COMPOUND_HET_PHASE_TRANS, PHASE_EVIDENCE_SEGREGATION
    # Neither the reads nor the parents place the two alts: the genotypes are consistent
    # with compound het, but nothing shows they sit on different haplotypes.
    return COMPOUND_HET_PHASE_UNKNOWN, None


def _compound_het_pairs(
    records: Sequence[SmallVariantRecord],
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    pedigree: FamilyPedigree | None = None,
) -> list[SmallVariantCompoundHetPair]:
    if not affected_samples:
        return []

    grouped: dict[tuple[str, str], list[SmallVariantRecord]] = {}
    for record in records:
        gene_keys = _compound_het_gene_keys(record)
        if not gene_keys:
            continue
        for gene_key in gene_keys:
            grouped.setdefault(gene_key, []).append(record)

    pair_map: dict[tuple[str, str], SmallVariantCompoundHetPair] = {}
    for group_records in grouped.values():
        if len(group_records) < 2:
            continue
        for index, left in enumerate(group_records[:-1]):
            for right in group_records[index + 1 :]:
                first_id, second_id = sorted((left.variant_id, right.variant_id))
                pair_ids = (first_id, second_id)
                if pair_ids in pair_map:
                    continue
                verdict = _compound_het_pair_verdict(
                    left,
                    right,
                    affected_samples=affected_samples,
                    unaffected_samples=unaffected_samples,
                    pedigree=pedigree,
                )
                if verdict is None:
                    continue
                phase, phase_evidence = verdict
                ordered_left, ordered_right = sorted((left, right), key=_small_record_sort_key)
                gene, gene_id = _resolve_compound_het_pair_gene_labels(ordered_left, ordered_right)
                pair_map[pair_ids] = SmallVariantCompoundHetPair(
                    pair_key="::".join(pair_ids),
                    gene=gene,
                    gene_id=gene_id,
                    left=ordered_left,
                    right=ordered_right,
                    phase=phase,
                    phase_evidence=phase_evidence,
                )

    return sorted(
        pair_map.values(),
        key=lambda pair: (
            *_small_record_sort_key(pair.left),
            *_small_record_sort_key(pair.right),
        ),
    )


def _record_carriage(record: SmallVariantRecord) -> Callable[[str], Carriage]:
    """Each sample's carriage of ``record``, for tracing it through the parents."""
    calls = _small_call_map(record)
    return lambda sample: _call_carriage(calls.get(sample))


def _compound_het_genes(
    pairs: Sequence[SmallVariantCompoundHetPair],
    *,
    affected_samples: Sequence[str],
    pedigree: FamilyPedigree | None = None,
) -> list[SmallVariantCompoundHetGene]:
    """The compound-het pairs by gene, in the order of their first pair: each gene with every
    variant of its pairs and the parent each traces to (``hit_origin``). A gene of two
    variants from the father and three from the mother is one result with its five variants
    and its six pairs, not six results. A pair is formed under a gene id or a symbol its two
    variants share (``_compound_het_gene_keys``), so pairs that share either are one gene."""
    joined: dict[tuple[str, str], tuple[str, str]] = {}

    def gene_of(key: tuple[str, str]) -> tuple[str, str]:
        while joined.setdefault(key, key) != key:
            key = joined[key]
        return key

    pair_keys: list[list[tuple[str, str]]] = []
    for pair in pairs:
        keys = [*_compound_het_gene_keys(pair.left), *_compound_het_gene_keys(pair.right)]
        first = gene_of(keys[0])
        for key in keys[1:]:
            other = gene_of(key)
            if other != first:
                joined[other] = first
        pair_keys.append(keys)
    by_gene: dict[tuple[str, str], list[SmallVariantCompoundHetPair]] = {}
    first_key: dict[tuple[str, str], tuple[str, str]] = {}
    for pair, keys in zip(pairs, pair_keys, strict=True):
        root = gene_of(keys[0])
        by_gene.setdefault(root, []).append(pair)
        first_key.setdefault(root, keys[0])

    genes: list[SmallVariantCompoundHetGene] = []
    for root, gene_pairs in by_gene.items():
        records = sorted(
            {record.variant_id: record for pair in gene_pairs for record in (pair.left, pair.right)}.values(),
            key=_small_record_sort_key,
        )
        inherited_from: dict[str, str] = {}
        for record in records:
            origin = hit_origin(
                affected=affected_samples,
                carriage=_record_carriage(record),
                pedigree=pedigree,
                locus=(record.chr, record.start),
            )
            if origin is not None:
                inherited_from[record.variant_id] = origin
        kind, value = first_key[root]
        genes.append(
            SmallVariantCompoundHetGene(
                group_key=f"compound-het-gene:{kind}:{value}",
                gene=next((pair.gene for pair in gene_pairs if pair.gene), None),
                gene_id=next((pair.gene_id for pair in gene_pairs if pair.gene_id), None),
                records=tuple(records),
                pairs=tuple(gene_pairs),
                inherited_from=inherited_from,
            )
        )
    return genes


def _normalize_small_variant_inheritance(value: str | None) -> str | None:
    normalized = _casefold(value).replace("-", "_").replace(" ", "_").replace("/", "_")
    if not normalized:
        return None
    normalized = _SMALL_INHERITANCE_ALIASES.get(normalized, normalized)
    if normalized not in _SUPPORTED_SMALL_INHERITANCE:
        raise HTTPException(status_code=400, detail="Unsupported small-variant inheritance filter")
    return normalized


def _carrier_partners(
    sample_rows: Sequence[dict[str, Any]],
    relationship_rows: Sequence[dict[str, Any]] | None = None,
    *,
    left_out: Collection[str] = frozenset(),
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """The couple of a carrier screen, each partner as the samples of that person the reading
    reads. The couple is the two people a couple relationship names; else the mother and the
    father; else the family's two people. Read per person (the family's same-individual links,
    ``same_individual.persons``): two samples of one person are never a couple, a family of one
    person sampled twice has none, and a partner sampled twice is read on each of their samples
    the reading reads, a call of either counting. A sample only another analysis's package lists
    (``AnalysisScope.left_out_members``, ``left_out``) is not read: the analysis's callset holds
    no call of it. None without a couple, or when a partner has no sample the reading reads."""
    sample_names = [
        str(row.get("sample_id") or "").strip()
        for row in sample_rows
        if str(row.get("sample_id") or "").strip()
    ]
    pairs = same_individual_pairs(relationship_rows or [])
    person = person_of(pairs)

    def two_people(left: str, right: str) -> bool:
        return bool(left and right) and person.get(left, left) != person.get(right, right)

    def couple() -> tuple[str, str] | None:
        for relationship in relationship_rows or []:
            if relationship.get("relationship_type") != "couple":
                continue
            left = str(relationship.get("sample_id_a") or "").strip()
            right = str(relationship.get("sample_id_b") or "").strip()
            if two_people(left, right) and left in sample_names and right in sample_names:
                return left, right
        mother, father = (
            next((str(row.get("sample_id") or "").strip() for row in sample_rows if row.get("role") == role), "")
            for role in ("mother", "father")
        )
        if two_people(mother, father):
            return mother, father
        people = persons(sample_names, pairs)
        if len(people) == 2:
            return people[0][0], people[1][0]
        return None

    found = couple()
    if found is None:
        return None
    partners = tuple(
        tuple(
            sample
            for sample in sample_names
            if person.get(sample, sample) == person.get(partner, partner) and sample not in left_out
        )
        for partner in found
    )
    if not all(partners):
        return None
    return partners[0], partners[1]


def _is_mitochondrial_chromosome(chromosome: str | None) -> bool:
    return _chromosome_match_key(str(chromosome or "")) == "MT"


# chrM under the names a stored row may carry, for the carrier screen's SQL prefilter.
_MITOCHONDRIAL_CHROMOSOMES = ("M", "MT", "chrM", "chrMT")


class _CarrierCandidate(NamedTuple):
    """A variant at least one partner carries, as the couple rule reads it."""

    record: SmallVariantRecord
    gene: str | None
    gene_id: str | None
    # The gene id and the symbol the rule matches the variant's gene by.
    keys: tuple[tuple[str, str], ...]
    # The partners (0, 1) with a call with the allele.
    carriers: frozenset[int]
    # On chrX outside the pseudo-autosomal regions.
    x_linked: bool


def _carrier_screening_genes(
    records: Sequence[SmallVariantRecord],
    sample_rows: Sequence[dict[str, Any]],
    relationship_rows: Sequence[dict[str, Any]] | None = None,
    *,
    assembly_name: str | None = None,
    left_out: Collection[str] = frozenset(),
) -> list[SmallVariantCarrierGene]:
    """The couple's at-risk findings (REQ-CARR-002), by gene: each gene with the variants the
    rule keeps in it, in the order given, and the genes in the order of their first variant.
    A partner is read on each of their samples the reading reads (``_carrier_partners``,
    ``left_out`` the samples it does not): a partner sampled twice carries what either
    sample carries.

    A gene in which both partners carry a variant: every variant either of them carries
    in it (a recessive condition needs a variant from each). On chrX outside the
    pseudo-autosomal regions, also every variant either partner carries on their own,
    whatever the other partner carries: a woman's carrier status alone puts a son at risk
    of an X-linked condition, and a man passes his one X to every daughter. Neither rule
    reads a partner's recorded sex. On an assembly whose pseudo-autosomal regions are not
    known (``hemizygous_chromosome``) only the both-partners rule applies; a chrY variant
    follows it too, so it is never a finding on its own. A variant without a gene is
    never a finding, and neither is a mitochondrial one: mtDNA passes from the mother
    alone, so two partners carrying a variant in one chrM gene says nothing of a child's
    risk (the mtDNA analysis reads the female partner's chrM calls).

    A gene is matched by its gene id and by its symbol, each on its own: a renamed gene
    whose annotations carry the old and the new symbol under one id is one gene. The
    variants kept that share either are listed as one gene.
    """
    partners = _carrier_partners(sample_rows, relationship_rows, left_out=left_out)
    if partners is None:
        return []
    candidates: list[_CarrierCandidate] = []
    carrier_sets: dict[tuple[str, str], set[int]] = {}
    for record in records:
        if _is_mitochondrial_chromosome(record.chr):
            continue
        keys = _gene_match_keys(record)
        if not keys:
            continue
        gene, gene_id = _primary_gene_keys(record)
        call_map = {call.sample: call.gt for call in record.calls}
        carriers = frozenset(
            index
            for index, samples in enumerate(partners)
            if any(genotype_has_alt(call_map.get(sample, "./.")) for sample in samples)
        )
        if not carriers:
            continue
        candidates.append(
            _CarrierCandidate(
                record=record,
                gene=gene,
                gene_id=gene_id,
                keys=keys,
                carriers=carriers,
                x_linked=hemizygous_chromosome(assembly_name, record.chr, record.start) == "X",
            )
        )
        for key in keys:
            carrier_sets.setdefault(key, set()).update(carriers)
    both_partners_keys = {key for key, carriers in carrier_sets.items() if len(carriers) == len(partners)}
    kept = [
        candidate
        for candidate in candidates
        if candidate.x_linked or any(key in both_partners_keys for key in candidate.keys)
    ]

    # The kept variants that share a gene id or a symbol, joined into one gene.
    genes: dict[tuple[str, str], list[_CarrierCandidate]] = {}
    for candidate, joined_gene in zip(kept, _joined_genes([candidate.keys for candidate in kept]), strict=True):
        genes.setdefault(joined_gene, []).append(candidate)
    return [_carrier_gene(members, partners) for members in genes.values()]


def _carrier_gene(
    members: Sequence[_CarrierCandidate],
    partners: tuple[tuple[str, ...], tuple[str, ...]],
) -> SmallVariantCarrierGene:
    """One gene of a carrier screen, named by its first variant's gene id or symbol: no
    other gene of the screen shares either (``_carrier_screening_genes``)."""
    kind, value = members[0].keys[0]
    carried = tuple(
        frozenset(member.record.variant_id for member in members if index in member.carriers)
        for index in range(len(partners))
    )
    return SmallVariantCarrierGene(
        group_key=f"carrier-gene:{kind}:{value}",
        gene=next((member.gene for member in members if member.gene), None),
        gene_id=next((member.gene_id for member in members if member.gene_id), None),
        records=tuple(member.record for member in members),
        partners=partners,
        carried=(carried[0], carried[1]),
        both_partners=all(carried),
        x_linked=any(member.x_linked for member in members),
    )


def _filter_expanded_carrier_screening(
    records: Sequence[SmallVariantRecord],
    sample_rows: Sequence[dict[str, Any]],
    relationship_rows: Sequence[dict[str, Any]] | None = None,
    *,
    assembly_name: str | None = None,
    left_out: Collection[str] = frozenset(),
) -> list[SmallVariantRecord]:
    """The couple's at-risk findings (REQ-CARR-002), in the order given: the variants of
    the genes ``_carrier_screening_genes`` finds."""
    kept = {
        record.variant_id
        for gene in _carrier_screening_genes(
            records, sample_rows, relationship_rows, assembly_name=assembly_name, left_out=left_out
        )
        for record in gene.records
    }
    return [record for record in records if record.variant_id in kept]


def _coerce_numeric_metric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _extract_nested_metric(payload: Any, candidate_keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        for key in candidate_keys:
            if key in payload:
                value = _coerce_numeric_metric(payload.get(key))
                if value is not None:
                    return value
        for value in payload.values():
            metric = _extract_nested_metric(value, candidate_keys)
            if metric is not None:
                return metric
        return None
    if isinstance(payload, list):
        for item in payload:
            metric = _extract_nested_metric(item, candidate_keys)
            if metric is not None:
                return metric
    return None


def _extract_gene_constraint_metrics(doc: dict[str, Any]) -> dict[str, float | None]:
    extra = doc.get("extra") or {}
    source_status = doc.get("source_status") or {}
    payloads = [extra] + [
        status.get("payload")
        for status in source_status.values()
        if isinstance(status, dict)
    ]
    pli_keys = ("pLI", "pli", "gene_pli", "lof_pLI", "LOF_PLI")
    missense_keys = ("missense_z", "MISSENSE_Z", "missenseZ", "mis_z", "MIS_Z", "gene_missense_z")
    pli = next(
        (metric for payload in payloads if (metric := _extract_nested_metric(payload, pli_keys)) is not None),
        None,
    )
    missense_z = next(
        (
            metric
            for payload in payloads
            if (metric := _extract_nested_metric(payload, missense_keys)) is not None
        ),
        None,
    )
    return {"gene_pli": pli, "gene_missense_z": missense_z}


def _dedupe_regions(regions: Sequence[Region]) -> tuple[Region, ...]:
    deduped: list[Region] = []
    seen: set[tuple[str, int, int]] = set()
    for region in regions:
        key = (normalize_chromosome(region.chr).upper(), int(region.start), int(region.end))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(region)
    return tuple(deduped)


def _normalize_alpha_missense_class(value: str | None) -> str | None:
    """AlphaMissense no-call (``-`` / empty) becomes None for clean display/scoring."""
    text_value = str(value or "").strip()
    if not text_value or text_value == "-":
        return None
    return text_value


def _filter_transcript_annotation(
    annotations: Sequence[dict[str, Any]],
    primary_annotation: dict[str, Any],
    matched_hashes: Collection[int] | None,
) -> dict[str, Any] | None:
    """The annotation a search's annotation filters held on, when the primary one does not
    meet them; None otherwise.

    The annotation filters must all hold on one transcript, while the variant's fields
    describe its primary transcript, which need not be that one: a variant kept by SIFT
    *deleterious* on a second transcript shows its primary transcript's *tolerated*. Which
    transcripts the filters hold on the SQL decides, on the stored rows the search itself
    reads (``_filter_transcript_matches``): ``matched_hashes`` are their hashes
    (``annotationHash``, the hash of the annotation as stored), None where the search asks
    nothing of a transcript. Of those transcripts, the one picked as the primary is picked:
    MANE Select, then canonical, then the most severe impact (``_annotation_rank``). None
    without annotation filters, when the primary transcript meets them, and when no
    transcript of the variant does, rather than a transcript the SQL did not name.
    """
    if matched_hashes is None:
        return None
    if _annotation_payload_hash(primary_annotation) in matched_hashes:
        return None
    matching = [annotation for annotation in annotations if _annotation_payload_hash(annotation) in matched_hashes]
    if not matching:
        return None
    return _select_primary_annotation(matching)


def _small_filter_transcript_out(annotation: dict[str, Any]) -> SmallVariantFilterTranscriptOut:
    """A transcript's values as the variant's own fields read them for its primary one."""
    return SmallVariantFilterTranscriptOut(
        gene=_annotation_gene(annotation),
        transcript_id=_annotation_text(annotation, "transcript_id"),
        alt_allele=_annotation_text(annotation, "alt_allele"),
        impact=_annotation_text(annotation, "impact"),
        effect=_annotation_effect(annotation),
        hgvsc=_annotation_text(annotation, "hgvsc"),
        hgvsp=_annotation_text(annotation, "hgvsp"),
        lof=_annotation_text(annotation, "lof"),
        cadd_phred=_annotation_float(annotation, "cadd_phred"),
        revel=_annotation_float(annotation, "revel"),
        spliceai_max=_annotation_spliceai_max(annotation),
        sift=_annotation_sift(annotation),
        polyphen=_annotation_polyphen(annotation),
    )


def _small_variant_out(
    record: SmallVariantRecord,
    *,
    assembly_name: str | None,
    filter_annotation_hashes: Collection[int] | None,
) -> VariantOut:
    """A small variant as the API serves it: its fields from its primary transcript.

    ``filter_annotation_hashes`` are the stored transcripts the search's annotation filters
    held on (``_filter_transcript_matches``), or None where no search asked anything of a
    transcript: then the variant names no ``filter_transcript``. Every field read from a
    transcript is the primary transcript's whatever the filters, so the ranking, the ACMG
    suggestions and the report read the same values in every view.
    """
    annotation = _select_primary_annotation(record.annotations)
    population_frequencies = _annotation_population_frequencies(annotation)
    transcripts = _small_transcript_annotations(record.annotations, annotation)
    filter_annotation = _filter_transcript_annotation(record.annotations, annotation, filter_annotation_hashes)
    return VariantOut(
        _id=ApiId(record.variant_id),
        chr=record.chr,
        start=record.start,
        end=record.end,
        length=record.end - record.start,
        hemizygous_in_males=hemizygous_chromosome(assembly_name, record.chr, record.start) is not None,
        type=_small_type(record.ref, record.alt),
        source=record.source,
        ref=record.ref,
        alt=record.alt,
        ps=next((call.ps for call in record.calls if call.ps is not None), None),
        gene=_annotation_gene(annotation) or (record.gene_symbols[0] if record.gene_symbols else None),
        gene_id=_annotation_gene_id(annotation),
        # Every gene the variant overlaps (not just the primary annotation's gene) — lets the
        # SV second-hit overlay match the same genes the require_sv_second_hit filter uses.
        gene_symbols=list(record.gene_symbols),
        impact=_annotation_text(annotation, "impact"),
        effect=_annotation_effect(annotation),
        clinvar=_annotation_clinvar(annotation),
        clinvar_from_vep_cache=_annotation_bool(annotation, CLINVAR_FROM_VEP_CACHE_KEY),
        clinvar_review_status=_annotation_text(annotation, "clinvar_review_status"),
        clinvar_stars=clinvar_stars(_annotation_text(annotation, "clinvar_review_status")),
        clinvar_conditions=clinvar_conditions(_annotation_text(annotation, "clinvar_conditions")),
        clinvar_variation_id=_annotation_text(annotation, "clinvar_variation_id"),
        rsid=record.rsid or _annotation_text(annotation, "rsid"),
        transcript_id=_annotation_text(annotation, "transcript_id"),
        alt_allele=_annotation_text(annotation, "alt_allele"),
        feature_type=_annotation_text(annotation, "feature_type"),
        transcript_biotype=_annotation_text(annotation, "transcript_biotype"),
        hgvsc=_annotation_text(annotation, "hgvsc"),
        hgvsp=_annotation_text(annotation, "hgvsp"),
        hgvsg=_annotation_text(annotation, "hgvsg"),
        canonical=_annotation_bool(annotation, "canonical"),
        mane_select=_annotation_bool(annotation, "mane_select"),
        mane_plus_clinical=_annotation_bool(annotation, "mane_plus_clinical"),
        mane_refseq_id=_annotation_text(annotation, MANE_REFSEQ_KEY),
        exon=_annotation_text(annotation, "exon"),
        intron=_annotation_text(annotation, "intron"),
        lof=_annotation_text(annotation, "lof"),
        lof_filter=_annotation_text(annotation, "lof_filter"),
        lof_flags=_annotation_text(annotation, "lof_flags"),
        nmd=_annotation_text(annotation, "nmd"),
        gnomad_af=_annotation_float(annotation, "gnomad_af"),
        gnomad_annotated=_annotation_gnomad_annotated(annotation),
        gnomad_hom_count=_annotation_int(annotation, "gnomad_hom_count"),
        population_frequencies=population_frequencies,
        cadd_raw=_annotation_float(annotation, "cadd_raw"),
        cadd_phred=_annotation_float(annotation, "cadd_phred"),
        revel=_annotation_float(annotation, "revel"),
        sift=_annotation_sift(annotation),
        polyphen=_annotation_polyphen(annotation),
        spliceai_ds_ag=_spliceai_delta(_annotation_float(annotation, "spliceai_ds_ag")),
        spliceai_ds_al=_spliceai_delta(_annotation_float(annotation, "spliceai_ds_al")),
        spliceai_ds_dg=_spliceai_delta(_annotation_float(annotation, "spliceai_ds_dg")),
        spliceai_ds_dl=_spliceai_delta(_annotation_float(annotation, "spliceai_ds_dl")),
        spliceai_max=_annotation_spliceai_max(annotation),
        spliceai_dp_ag=_annotation_int(annotation, "spliceai_dp_ag"),
        spliceai_dp_al=_annotation_int(annotation, "spliceai_dp_al"),
        spliceai_dp_dg=_annotation_int(annotation, "spliceai_dp_dg"),
        spliceai_dp_dl=_annotation_int(annotation, "spliceai_dp_dl"),
        alpha_missense_pathogenicity=_annotation_float(
            annotation, "alpha_missense_pathogenicity"
        ),
        alpha_missense_class=_normalize_alpha_missense_class(
            _annotation_text(annotation, "alpha_missense_class")
        ),
        annotation_extra=_annotation_extra(annotation),
        transcripts=transcripts,
        filter_transcript=(
            _small_filter_transcript_out(filter_annotation) if filter_annotation is not None else None
        ),
        genotypes=[
            GenotypeOut(
                sample=call.sample,
                gt=call.gt,
                dp=call.dp,
                ad=call.ad or None,
                af=call.af or None,
                ps=call.ps,
            )
            for call in record.calls
        ],
    )


def _group_review_for_pair(
    left: VariantOut,
    right: VariantOut,
):
    for variant, partner in ((left, right), (right, left)):
        review = variant.review
        compound_het_review = review.compound_het if review else None
        if compound_het_review and str(partner.id) in compound_het_review.partner_variant_ids:
            return compound_het_review
    return None


def _variant_gene_keys(variant: VariantOut) -> list[str]:
    """Upper-cased genes a variant hits, primary gene first (for SV second-hit matching)."""
    keys: list[str] = []
    if variant.gene:
        keys.append(variant.gene.upper())
    for symbol in variant.gene_symbols or []:
        upper = str(symbol).upper()
        if upper and upper not in keys:
            keys.append(upper)
    return keys


def _structural_variant_out(
    record: StructuralVariantRecord,
    selected_samples: Sequence[str],
    review: SmallVariantReviewOut | None = None,
    cytoband: str | None = None,
    *,
    track_mode: bool = False,
    pedigree: FamilyPedigree | None = None,
) -> VariantOut:
    """One SV row as the API serves it, with the calls of ``selected_samples`` (every call
    when it is empty).

    ``copy_change`` reads the calls the row lists, so a track, which asks for one sample,
    gets that sample's change: a CNV with one copy in the mother and three in her son is a
    loss on her track and a gain on his. ``pedigree`` names the males, who carry one copy
    of chrX and chrY outside the PARs; without it every sample is read as having two copies.
    """
    allowed_samples = set(selected_samples)
    calls = [call for call in record.calls if not allowed_samples or call.sample in allowed_samples]
    # A translocation has no length (its END − POS, 0 or 1 bp, is none), as the length
    # filters read it.
    length = (
        None
        if is_translocation_sv_type(record.sv_type)
        else structural_variant_length(sv_len=record.sv_len, start=record.start, end=record.end)
    )
    annotation_extra = _structural_annotation_extra(record, track_mode=track_mode)
    if cytoband and not track_mode:
        annotation_extra["cytoband"] = cytoband
    population_frequencies = annotation_extra.get("population_frequencies")
    return VariantOut(
        _id=ApiId(record.variant_id),
        chr=record.chr,
        start=record.start,
        end=record.end,
        length=length,
        type=record.sv_type,
        source=record.source,
        qual=calls[0].qual if calls else None,
        read_support=calls[0].read_support if calls else None,
        filter=calls[0].filter if calls else None,
        remote_chr=record.remote_chr,
        remote_start=record.remote_start,
        gene=record.gene_symbols[0] if record.gene_symbols else None,
        gene_symbols=list(record.gene_symbols),
        gene_count=len(record.gene_symbols),
        gene_pli=annotation_extra.get("pli") if isinstance(annotation_extra.get("pli"), (int, float)) else None,
        population_frequencies=population_frequencies if isinstance(population_frequencies, dict) else {},
        annotation_extra=annotation_extra,
        review=review,
        genotypes=[
            GenotypeOut(
                sample=call.sample,
                gt=call.gt,
                qual=call.qual,
                read_support=call.read_support,
                filter=call.filter,
                ps=call.phase_set,
                cn=call.copy_number,
            )
            for call in calls
        ],
        # Drawn by the genome, chromosome and Circos views, for every SV type a gain or loss
        # can be read from (DEL:ME, DUP:TANDEM and CNV among them), not only exact type names.
        copy_change=copy_change(
            record.sv_type,
            [(call.sample, call.gt, call.copy_number) for call in calls],
            chrom=record.chr,
            start=record.start,
            end=record.end,
            pedigree=pedigree,
        ),
    )


def _family_affected_unaffected_sample_names(
    context: FamilyMetadataContext,
) -> tuple[list[str], list[str]]:
    """The affected and the unaffected members' samples an inheritance mode reads, of the
    analysis the family is read for: a member only another analysis's package lists (a person's
    second sample, sequenced for that analysis) is neither required nor read, as the analysis's
    callset holds no call of it (``AnalysisScope.left_out_members``)."""
    left_out = context.scope.left_out_members
    affected_sample_names = [
        sample_name
        for sample_name in context.affected_sample_names
        if sample_name in context.sample_name_to_uuid and sample_name not in left_out
    ]
    affected_sample_set = set(affected_sample_names)
    unaffected_sample_names = [
        str(row.get("sample_id") or "").strip()
        for row in context.sample_rows
        if str(row.get("sample_id") or "").strip()
        and str(row.get("clinical_status") or "").lower() == "unaffected"
        and str(row.get("sample_id") or "").strip() not in affected_sample_set
        and str(row.get("sample_id") or "").strip() not in left_out
    ]
    return affected_sample_names, unaffected_sample_names


def _small_native_inheritance_supported(inheritance: str | None) -> bool:
    return inheritance in {
        None,
        _DE_NOVO_DOMINANT_INHERITANCE,
        _RECESSIVE_HOMOZYGOUS_INHERITANCE,
        _X_LINKED_INHERITANCE,
    }


def _small_sample_gt_exists_condition(
    context: FamilyMetadataContext,
    *,
    sample_name: str,
    classes: Iterable[GenotypeClass],
    prefix: str,
    params: dict[str, Any],
) -> str:
    sample_param = f"{prefix}_samples"
    params[sample_param] = (sample_name,)
    gt_condition = clickhouse_genotype_condition(
        "gt", classes, param=f"{prefix}_gts", params=params
    )
    return (
        "arrayExists((sample_id, gt) -> "
        f"sample_id IN %({sample_param})s AND {gt_condition}, "
        "e.calls.sampleId, e.calls.gt)"
    )


def _small_all_samples_have_gts_condition(
    context: FamilyMetadataContext,
    *,
    sample_names: Sequence[str],
    classes: Iterable[GenotypeClass],
    prefix: str,
    params: dict[str, Any],
) -> list[str]:
    return [
        _small_sample_gt_exists_condition(
            context,
            sample_name=sample_name,
            classes=classes,
            prefix=f"{prefix}_{index}",
            params=params,
        )
        for index, sample_name in enumerate(sample_names)
    ]


def _small_no_samples_have_gts_condition(
    context: FamilyMetadataContext,
    *,
    sample_names: Sequence[str],
    classes: Iterable[GenotypeClass],
    prefix: str,
    params: dict[str, Any],
) -> list[str]:
    return [
        "NOT "
        + _small_sample_gt_exists_condition(
            context,
            sample_name=sample_name,
            classes=classes,
            prefix=f"{prefix}_{index}",
            params=params,
        )
        for index, sample_name in enumerate(sample_names)
    ]


def _small_hemizygous_position_condition(
    assembly_name: str | None,
    *,
    prefix: str,
    params: dict[str, Any],
) -> str | None:
    """SQL: the entry lies where a male is hemizygous (chrX or chrY outside the PARs).

    None when the assembly's PARs are not known: there a male is read as diploid. The PAR
    bounds are bound as parameters.
    """
    regions = pseudoautosomal_regions(assembly_name)
    if regions is None:
        return None
    chrom_expr = _clickhouse_chromosome_match_expr("e.chrom")
    branches: list[str] = []
    for chrom, names in XY_CHROMOSOME_NAMES.items():
        key = f"{prefix}_{chrom.lower()}"
        params[f"{key}_chromosomes"] = names
        outside: list[str] = []
        for index, (start, end) in enumerate(regions[chrom], start=1):
            params[f"{key}_par{index}_start"] = start
            params[f"{key}_par{index}_end"] = end
            outside.append(
                f"NOT (e.pos BETWEEN %({key}_par{index}_start)s AND %({key}_par{index}_end)s)"
            )
        branches.append(f"({chrom_expr} IN %({key}_chromosomes)s AND {' AND '.join(outside)})")
    return "(" + " OR ".join(branches) + ")"


_SEGREGATION_FLAG_MODES: Final[tuple[str, ...]] = (
    _DE_NOVO_DOMINANT_INHERITANCE,
    _RECESSIVE_HOMOZYGOUS_INHERITANCE,
    _X_LINKED_INHERITANCE,
)


def _small_inheritance_mode_clauses(
    context: FamilyMetadataContext,
    mode: str,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    prefix: str,
    params: dict[str, Any],
) -> list[str]:
    """The SQL of a single-variant inheritance mode on the entries row ``e``: the one
    definition of de novo / dominant, homozygous recessive and X-linked, which the search's
    inheritance filter (``_small_native_inheritance_clauses``) and the segregation flags the
    ranking and the paired searches read (``_small_segregation_flag_columns``) both apply.
    ``affected_samples`` is not empty. Every parameter is named from ``prefix``, so two modes
    can share one query.

    - de novo / dominant: one copy in every affected member (a heterozygous call or, for a
      male where he is hemizygous, a haploid ``1`` or ``1/1`` call), no ALT call in
      any unaffected member;
    - homozygous recessive: off chromosome X, homozygous ALT in every affected member and in
      no unaffected one;
    - X-linked: on chromosome X, an ALT call in every affected member, none in an unaffected
      male, and no unaffected female homozygous.
    """
    clauses: list[str] = []
    x_chromosomes = f"{prefix}_x_chromosomes"
    if mode == _DE_NOVO_DOMINANT_INHERITANCE:
        # One copy in every affected sample: a heterozygous call, or, for a male where he is
        # hemizygous, a haploid "1" or "1/1" call.
        sample_sex = _sample_sex_map(context.sample_rows)
        hemizygous_position: str | None = None
        if any(_is_male_sex(sample_sex.get(sample, "")) for sample in affected_samples):
            hemizygous_position = _small_hemizygous_position_condition(
                context.assembly_name, prefix=f"{prefix}_hemizygous", params=params
            )
        for index, sample_name in enumerate(affected_samples):
            het_condition = _small_sample_gt_exists_condition(
                context,
                sample_name=sample_name,
                classes=frozenset({HET}),
                prefix=f"{prefix}_affected_het_{index}",
                params=params,
            )
            if hemizygous_position is None or not _is_male_sex(sample_sex.get(sample_name, "")):
                clauses.append(het_condition)
                continue
            hemizygous_condition = _small_sample_gt_exists_condition(
                context,
                sample_name=sample_name,
                classes=frozenset({HOM_ALT}),
                prefix=f"{prefix}_affected_hemizygous_{index}",
                params=params,
            )
            clauses.append(f"({het_condition} OR ({hemizygous_condition} AND {hemizygous_position}))")
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=unaffected_samples,
                classes=ALT_CLASSES,
                prefix=f"{prefix}_unaffected_alt",
                params=params,
            )
        )
    elif mode == _RECESSIVE_HOMOZYGOUS_INHERITANCE:
        params[x_chromosomes] = _X_CHROMOSOME_NAMES
        clauses.append(f"e.chrom NOT IN %({x_chromosomes})s")
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                classes=frozenset({HOM_ALT}),
                prefix=f"{prefix}_affected_hom_alt",
                params=params,
            )
        )
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=unaffected_samples,
                classes=frozenset({HOM_ALT}),
                prefix=f"{prefix}_unaffected_hom_alt",
                params=params,
            )
        )
    elif mode == _X_LINKED_INHERITANCE:
        params[x_chromosomes] = _X_CHROMOSOME_NAMES
        clauses.append(f"e.chrom IN %({x_chromosomes})s")
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                classes=ALT_CLASSES,
                prefix=f"{prefix}_affected_alt",
                params=params,
            )
        )
        sample_sex = _sample_sex_map(context.sample_rows)
        male_unaffected = [sample for sample in unaffected_samples if _is_male_sex(sample_sex.get(sample, ""))]
        other_unaffected = [sample for sample in unaffected_samples if sample not in set(male_unaffected)]
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=male_unaffected,
                classes=ALT_CLASSES,
                prefix=f"{prefix}_unaffected_male_alt",
                params=params,
            )
        )
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=other_unaffected,
                classes=frozenset({HOM_ALT}),
                prefix=f"{prefix}_unaffected_hom_alt",
                params=params,
            )
        )
    return clauses


def _small_segregation_flag_columns(context: FamilyMetadataContext) -> tuple[list[str], dict[str, Any]]:
    """The SELECT columns that say, per key, which single-variant inheritance modes the
    variant fits in this family (``segregation_<mode>``), and their parameters: the clauses
    of ``_small_inheritance_mode_clauses``, each mode with parameters of its own. A key is
    read as the WHERE admits it, by any of its rows (``max``). Without an affected member
    no variant fits a mode, as the inheritance filter keeps none."""
    params: dict[str, Any] = {}
    affected_samples, unaffected_samples = _family_affected_unaffected_sample_names(context)
    columns: list[str] = []
    for mode in _SEGREGATION_FLAG_MODES:
        condition = "0"
        if affected_samples:
            clauses = _small_inheritance_mode_clauses(
                context,
                mode,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
                prefix=f"segregation_{mode}",
                params=params,
            )
            condition = " AND ".join(f"({clause})" for clause in clauses)
        columns.append(f"max({condition}) AS segregation_{mode}")
    return columns, params


def _small_native_inheritance_clauses(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
) -> tuple[list[str], dict[str, Any]]:
    inheritance = filters.inheritance
    if not inheritance and not filters.expanded_carrier_screening:
        return [], {}

    params: dict[str, Any] = {}
    clauses: list[str] = []

    if filters.expanded_carrier_screening:
        partners = _carrier_partners(
            context.sample_rows, context.relationship_rows, left_out=context.scope.left_out_members
        )
        if partners is None:
            return ["0"], params
        partner_alt_clauses = _small_all_samples_have_gts_condition(
            context,
            sample_names=[*partners[0], *partners[1]],
            classes=ALT_CLASSES,
            prefix="carrier_screen_partner_alt",
            params=params,
        )
        clauses.append(f"({' OR '.join(partner_alt_clauses)})")
        clauses.append("length(e.gene_symbols) > 0")
        # Mitochondrial variants are not part of the couple rule
        # (_filter_expanded_carrier_screening): they must not fill its candidate window.
        params["carrier_screen_mito_chromosomes"] = _MITOCHONDRIAL_CHROMOSOMES
        clauses.append("e.chrom NOT IN %(carrier_screen_mito_chromosomes)s")

    # The grouped view keeps what the search without a mode keeps, an affected member or not:
    # it only lists the compound-het pairs among those variants together.
    if not inheritance or inheritance == _ALL_COMPOUND_HET_GROUPED_INHERITANCE:
        return clauses, params

    affected_samples, unaffected_samples = _family_affected_unaffected_sample_names(context)
    if not affected_samples:
        return ["0"], params

    if inheritance in _SEGREGATION_FLAG_MODES:
        clauses.extend(
            _small_inheritance_mode_clauses(
                context,
                inheritance,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
                prefix="inheritance",
                params=params,
            )
        )
    elif inheritance == _COMPOUND_HET_INHERITANCE:
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                classes=frozenset({HET}),
                prefix="inheritance_affected_het",
                params=params,
            )
        )
    elif inheritance == _RECESSIVE_INHERITANCE:
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                classes=ALT_CLASSES,
                prefix="inheritance_affected_alt",
                params=params,
            )
        )

    return clauses, params


# The FILTER values of a call that passed its caller's filters: PASS, and "." or nothing,
# which say no filter was applied. Every other value names a filter the call failed.
_PASSING_FILTER_VALUES: Final[tuple[str, ...]] = ("", ".", "PASS")


def _small_pass_only_condition(params: dict[str, Any]) -> str:
    """*Pass variants only*, on the entries row ``e``: every call that carries the ALT allele
    passed its caller's FILTER (PASS, ``.`` or none). Once one call of the row has a FILTER of
    its own (a callset of one file per sample), each call is read by its own; otherwise the
    row's FILTER is every call's (a multi-sample VCF; the owner's decision of 2026-11-03).
    Only the calls of the family's members are read (``%(visible_sample_ids)s``, which the
    caller binds), as the record a search reads holds their calls alone. A member the family
    does not show neither holds a variant back nor decides how its calls are read (the
    owner's decision of 2026-11-06)."""
    params["pass_only_filter_values"] = _PASSING_FILTER_VALUES
    carries_alt = clickhouse_genotype_condition(
        "gt", ALT_CLASSES, param="pass_only_alt_genotypes", params=params
    )
    passing = "arrayAll(value -> value IN %(pass_only_filter_values)s, {values})"
    visible = "sample_id IN %(visible_sample_ids)s"
    return (
        f"if(arrayExists((sample_id, own) -> {visible} AND notEmpty(own), e.calls.sampleId, e.calls.filters), "
        f"arrayAll((sample_id, gt, own) -> NOT ({visible}) OR NOT ({carries_alt}) "
        f"OR {passing.format(values='own')}, e.calls.sampleId, e.calls.gt, e.calls.filters), "
        f"{passing.format(values='e.filters')})"
    )


def _small_variant_where_clauses(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    include_variant_ids: Sequence[str] | None = None,
    exclude_variant_ids: Sequence[str] = (),
    exclude_imputed: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    where_clauses = ["e.family_guid = %(family_guid)s", "e.sign = 1"]
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    if context.project_ids:
        where_clauses.append("e.project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(context.project_ids)
    where_clauses.extend(_small_analysis_scope_clauses(context, params=params))
    visible_sample_ids = _visible_clickhouse_sample_ids(context)
    if visible_sample_ids:
        where_clauses.append("hasAny(e.calls.sampleId, %(visible_sample_ids)s)")
        params["visible_sample_ids"] = visible_sample_ids
    else:
        where_clauses.append("0")
    sample_filter_clauses, sample_filter_params = _small_native_sample_filter_clauses(context, filters)
    where_clauses.extend(sample_filter_clauses)
    params.update(sample_filter_params)
    inheritance_clauses, inheritance_params = _small_native_inheritance_clauses(context, filters)
    where_clauses.extend(inheritance_clauses)
    params.update(inheritance_params)
    if include_variant_ids is not None:
        normalized_include_ids = tuple(
            str(variant_id).strip()
            for variant_id in include_variant_ids
            if str(variant_id).strip()
        )
        if normalized_include_ids:
            where_clauses.append("e.variantId IN %(include_variant_ids)s")
            params["include_variant_ids"] = normalized_include_ids
        else:
            where_clauses.append("0")
    normalized_exclude_ids = tuple(
        str(variant_id).strip()
        for variant_id in exclude_variant_ids
        if str(variant_id).strip()
    )
    if normalized_exclude_ids:
        where_clauses.append("e.variantId NOT IN %(exclude_variant_ids)s")
        params["exclude_variant_ids"] = normalized_exclude_ids
    # The phase set and the callers' FILTER are read on the calls of the family's members
    # alone, as the record a search reads holds their calls alone: a member the family does
    # not show (a sample taken out of it, or not yet added) neither keeps nor holds back a
    # variant (the owner's decision of 2026-11-06). Without a member the search keeps nothing.
    if visible_sample_ids and filters.phase_set is not None:
        where_clauses.append(
            "arrayExists((sample_id, ps) -> sample_id IN %(visible_sample_ids)s AND ps = %(phase_set)s, "
            "e.calls.sampleId, e.calls.ps)"
        )
        params["phase_set"] = filters.phase_set
    if visible_sample_ids and filters.pass_only:
        where_clauses.append(_small_pass_only_condition(params))
    where_clauses.extend(
        _small_row_filter_clauses(context, filters, params=params, exclude_imputed=exclude_imputed)
    )
    return where_clauses, params


def _small_variant_type_condition(variant_type: str | None, *, alias: str = "e") -> str | None:
    """The variant type filter on the rows ``alias``: SNV one base for one, MNV REF and ALT
    of one length longer than one base, INDEL every variant that is no SNV (an MNV
    included). Another value, or none, asks nothing (None)."""
    normalized = _casefold(variant_type or "").upper()
    if normalized == "SNV":
        return f"length({alias}.ref) = 1 AND length({alias}.alt) = 1"
    if normalized == "MNV":
        return f"length({alias}.ref) = length({alias}.alt) AND length({alias}.ref) > 1"
    if normalized == "INDEL":
        return f"NOT (length({alias}.ref) = 1 AND length({alias}.alt) = 1)"
    return None


def _small_analysis_scope_clauses(context: FamilyMetadataContext, *, params: dict[str, Any]) -> list[str]:
    """The rows of the analysis the family is read for (``context.scope``): the callsets of the
    family's other analyses are left out, so a variant both an analysis's and another's callset
    hold is read from its own (analysis_scope.py). A reading of a family that holds several
    analyses with small variants of their own names the analysis, or is refused, whether or not
    it names a callset. A reading that names one callset reads that callset alone
    (``_small_callset_clauses``) among the analysis's own: a callset of another analysis reads
    nothing, in the list, its count and its export alike."""
    return left_out_source_clauses(context.scope.require_named().left_out_sources("small_variants"), params)


def _small_callset_clauses(
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    exclude_imputed: bool,
    alias: str = "e",
) -> list[str]:
    """The callset filter on the rows ``alias``: the callset (``source``) named, whole, in any
    case and but for the spaces around it, so one callset's name never selects another whose
    name holds it (``deepvariant`` and ``deepvariant_sr``). Without one, ``exclude_imputed``
    hides the imputed callsets (GLIMPSE2, SHAPEIT), as the family page's list and count and the
    Variant Explorer do by default; the phased-marker and sample-QC readers see every callset.
    A blank callset asks nothing, so the imputed-callset default applies to it."""
    source = (filters.source or "").strip()
    if source:
        params["source"] = source.lower()
        return [f"lowerUTF8({alias}.source) = %(source)s"]
    if exclude_imputed:
        params["imputed_sources"] = tuple(IMPUTED_SMALL_VARIANT_SOURCES)
        return [f"lowerUTF8({alias}.source) NOT IN %(imputed_sources)s"]
    return []


def _small_locus_clauses(
    filters: SmallVariantQueryFilters, *, params: dict[str, Any], alias: str = "e"
) -> list[str]:
    """The location filters on the rows ``alias``: the chromosome with or without ``chr``,
    and chrM by any of its names (``_chromosome_options``); a variant that starts at or after
    the start or, with ``overlap``, whose REF reaches it (from a start alone too); one that
    starts at or before the end; and, with a
    chromosome, the ``xpos`` range of the locus, which the sort key reads. The annotation
    subqueries repeat the locus as these clauses bind it (``_small_annotation_scope_clauses``,
    ``_small_family_key_condition``)."""
    clauses: list[str] = []
    if filters.chromosome:
        clauses.append(f"{alias}.chrom IN %(chromosomes)s")
        params["chromosomes"] = _chromosome_options(filters.chromosome)
    if filters.start is not None:
        clauses.append(
            f"({alias}.pos + length({alias}.ref) - 1) >= %(start)s"
            if filters.overlap
            else f"{alias}.pos >= %(start)s"
        )
        params["start"] = filters.start
    if filters.end is not None:
        clauses.append(f"{alias}.pos <= %(end)s")
        params["end"] = filters.end
    if filters.chromosome:
        xpos_start = 0 if filters.overlap else (filters.start or 0)
        xpos_end = filters.end if filters.end is not None else 999_999_999
        clauses.append(f"{alias}.xpos BETWEEN %(xpos_start)s AND %(xpos_end)s")
        params["xpos_start"] = _xpos(filters.chromosome, xpos_start)
        params["xpos_end"] = _xpos(filters.chromosome, xpos_end)
    return clauses


def _small_row_filter_clauses(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    exclude_imputed: bool,
) -> list[str]:
    """The filters on an entries row (alias ``e``) that need no family: the variant type, the
    callset, the location and the rsID. The family search (``_small_variant_where_clauses``)
    and the Variant Explorer (``variant_explorer_service._carriers_where``) read them here.

    The rsID is found, as typed but for the spaces around it, in the row's own rsID or in
    its own annotation's (the row's annotation set, ``_small_annotation_key_membership_condition``).
    It comes after the locus, which the annotation subquery's scope repeats."""
    clauses: list[str] = []
    variant_type = _small_variant_type_condition(filters.variant_type)
    if variant_type:
        clauses.append(variant_type)
    clauses.extend(_small_callset_clauses(filters, params=params, exclude_imputed=exclude_imputed))
    clauses.extend(_small_locus_clauses(filters, params=params))
    rsid = (filters.rsid or "").strip()
    if rsid:
        params["rsid"] = rsid
        annotation_rsid_condition = _small_annotation_key_membership_condition(
            context,
            filters,
            params=params,
            condition="positionCaseInsensitive(ifNull(ai.rsid, ''), %(rsid)s) > 0",
        )
        clauses.append(
            f"(positionCaseInsensitive(ifNull(e.rsid, ''), %(rsid)s) > 0 OR {annotation_rsid_condition})"
        )
    return clauses


def _text_contains_any(expr: str, *, prefix: str, values: Sequence[str], params: dict[str, Any]) -> str | None:
    clauses: list[str] = []
    for index, value in enumerate(values):
        text_value = str(value or "").strip()
        if not text_value:
            continue
        param = f"{prefix}_{index}"
        params[param] = text_value
        clauses.append(f"positionCaseInsensitive({expr}, %({param})s) > 0")
    return f"({' OR '.join(clauses)})" if clauses else None


def _small_gene_filter_condition(
    gene_values: Sequence[str],
    *,
    prefix: str,
    params: dict[str, Any],
) -> str | None:
    normalized_gene_values = [str(value).strip() for value in gene_values if str(value).strip()]
    if not normalized_gene_values:
        return None
    terms_param = f"{prefix}_terms"
    params[terms_param] = tuple(_casefold(term) for term in normalized_gene_values)
    entry_gene_condition = f"arrayExists(gene -> lower(gene) IN %({terms_param})s, e.gene_symbols)"
    return entry_gene_condition


def _small_region_bounds(regions: Sequence[Region]) -> tuple[list[str], list[int], list[int]]:
    """The regions as the small-variant region condition reads them, as three parallel
    lists: each chromosome by its match key (``_chromosome_match_key``), each start and end
    in order, and the regions of a chromosome that share a base merged into one, in order of
    chromosome and start. A region without a chromosome is left out.

    A variant overlaps a merged region by the rule of ``_small_region_filter_condition``
    exactly when it overlaps one of the regions merged into it: each base of the merged
    region lies in one of them. (A REF of no base, which no VCF record has, overlaps by the
    rule a region holding the bases on both sides of its position; the merged region holds
    both only when one of its regions does, as regions that merely touch stay apart.) A
    panel lists each gene's locus once per transcript in a gene reference of one row per
    transcript, some 42,000 loci for the Mendeliome's 5,300 genes, which merge into about one
    region per gene: a query about five times smaller, and a join over about an eighth as many
    regions (measured in docs/variant-ranking-cache.md, "Applying a panel")."""
    by_chromosome: dict[str, list[tuple[int, int]]] = {}
    for region in regions:
        chrom = _chromosome_match_key(region.chr)
        if not chrom:
            continue
        start, end = sorted((int(region.start), int(region.end)))
        by_chromosome.setdefault(chrom, []).append((start, end))
    region_chromosomes: list[str] = []
    region_starts: list[int] = []
    region_ends: list[int] = []
    for chrom in sorted(by_chromosome):
        merged: list[list[int]] = []
        for start, end in sorted(by_chromosome[chrom]):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            region_chromosomes.append(chrom)
            region_starts.append(start)
            region_ends.append(end)
    return region_chromosomes, region_starts, region_ends


def _small_region_filter_condition(
    regions: Sequence[Region],
    *,
    prefix: str,
    params: dict[str, Any],
) -> str | None:
    """The entry row (alias ``e``) overlaps one of ``regions``, tested against each region:
    on the same chromosome (by match key), with the REF's first base at or before the
    region's end and its last base (``pos + length(ref) - 1``) at or after its start. None
    without a region.

    This is the overlap rule of every small-variant region filter. The intervals and the
    excluded genes and intervals are tested this way; a gene panel's regions, which can be
    thousands (the Mendeliome's), are found by a join that keeps the same rows
    (``_small_region_window_rows``)."""
    region_chromosomes, region_starts, region_ends = _small_region_bounds(regions)
    if not region_chromosomes:
        return None

    chroms_param = f"{prefix}_chromosomes"
    starts_param = f"{prefix}_starts"
    ends_param = f"{prefix}_ends"
    params[chroms_param] = region_chromosomes
    params[starts_param] = region_starts
    params[ends_param] = region_ends
    chrom_expr = _clickhouse_chromosome_match_expr("e.chrom")
    return (
        "arrayExists((region_chrom, region_start, region_end) -> "
        f"{chrom_expr} = region_chrom "
        "AND e.pos <= region_end "
        "AND (e.pos + length(e.ref) - 1) >= region_start, "
        f"%({chroms_param})s, %({starts_param})s, %({ends_param})s)"
    )


# The window of the region joins (_small_region_window_rows), in bases.
_REGION_WINDOW = 1 << 16


def _small_region_window_rows(
    regions: Sequence[Region], *, prefix: str, params: dict[str, Any]
) -> str | None:
    """The regions, read as ``_small_region_bounds`` reads them, as the rows a join on
    64-kb windows reads: ``region_chrom``, ``region_start``, ``region_end`` and
    ``region_window``, one row for each window a region touches. None without a region.

    Testing each variant against every region (``_small_region_filter_condition``) costs
    the number of variants times the number of regions. A join lists each region under the
    windows it touches and each variant under the windows its REF touches
    (``_small_variant_windows``), and keeps a pair that shares a window when the two overlap
    by that rule (``_SMALL_REGION_WINDOW_MATCH``). A variant and a region that overlap share
    the window of a base they share, so no pair is missed, and the rule decides each pair,
    so none is added. The Variant Explorer joins its annotation index this way
    (``variant_explorer_service._region_key_condition``), and the family page its
    family's own rows (``_small_family_region_condition``)."""
    return _region_window_rows(_small_region_bounds(regions), prefix=prefix, params=params)


def _region_window_rows(
    bounds: tuple[list[str], list[int], list[int]], *, prefix: str, params: dict[str, Any]
) -> str | None:
    """Regions read as a region condition binds them (``_small_region_bounds``,
    ``_structural_region_bounds``: chromosomes by match key, starts, ends), bound once as
    three array parameters, as the rows a join on 64-kb windows reads: ``region_chrom``,
    ``region_start``, ``region_end`` and ``region_window``, one row for each window a region
    touches. None without a region."""
    chromosomes, starts, ends = bounds
    if not chromosomes:
        return None
    chromosomes_param, starts_param, ends_param = (
        f"{prefix}_chromosomes",
        f"{prefix}_starts",
        f"{prefix}_ends",
    )
    params[chromosomes_param] = chromosomes
    params[starts_param] = starts
    params[ends_param] = ends
    return (
        "SELECT region.1 AS region_chrom, region.2 AS region_start, region.3 AS region_end,"
        " region_window"
        f" FROM (SELECT arrayJoin(arrayZip(%({chromosomes_param})s, %({starts_param})s,"
        f" %({ends_param})s)) AS region)"
        f" ARRAY JOIN range(intDiv(toInt64(region.2), {_REGION_WINDOW}),"
        f" intDiv(toInt64(region.3), {_REGION_WINDOW}) + 1) AS region_window"
    )


def _small_variant_windows(alias: str) -> tuple[str, str]:
    """What a region join reads of the variant rows ``alias``: the columns (``chrom_key``,
    the chromosome's match key; ``pos``; ``ref_end``, the REF's last base as the overlap
    rule counts it; ``variant_window``), and the ARRAY JOIN that lists each row under each
    window its REF touches.

    The windows of a REF are counted in signed numbers: a row without a REF at position 0
    (no VCF record has one) falls in window 0 instead of wrapping to every window."""
    chromosome = _clickhouse_chromosome_match_expr(f"{alias}.chrom")
    ref_first = f"toInt64({alias}.pos)"
    ref_last = f"toInt64({alias}.pos) + length({alias}.ref) - 1"
    columns = (
        f"{chromosome} AS chrom_key, {alias}.pos AS pos,"
        f" {alias}.pos + length({alias}.ref) - 1 AS ref_end, variant_window"
    )
    windows = (
        f"ARRAY JOIN range(intDiv(least({ref_first}, {ref_last}), {_REGION_WINDOW}),"
        f" intDiv(greatest({ref_first}, {ref_last}), {_REGION_WINDOW}) + 1) AS variant_window"
    )
    return columns, windows


# The join of the variant rows (v, _small_variant_windows) and the region rows (r,
# _small_region_window_rows) that share a window, keeping a pair that overlaps by the rule
# of _small_region_filter_condition.
_SMALL_REGION_WINDOW_MATCH = (
    "ON v.chrom_key = r.region_chrom AND v.variant_window = r.region_window"
    " WHERE v.pos <= r.region_end AND v.ref_end >= r.region_start"
)


class _SmallRegionCondition(Protocol):
    """Builds "the variant overlaps one of these regions", as ``_small_region_filter_condition``
    does, or None without a region."""

    def __call__(
        self, regions: Sequence[Region], *, prefix: str, params: dict[str, Any]
    ) -> str | None: ...


def _small_family_region_condition(
    context: FamilyMetadataContext, filters: SmallVariantQueryFilters
) -> _SmallRegionCondition:
    """The family page's condition for a gene panel's regions, on the search's entry rows
    (alias ``e``): the rows that overlap a region by the rule of
    ``_small_region_filter_condition``, found by a join on 64-kb windows over the family's
    own rows (``_small_region_window_rows``).

    Tested against every region for each row (``arrayExists`` over the regions), the rule costs
    1.3 s at 500 regions and 2.1 s at 1,000 for a family of about 190,000 rows, and longer for
    a whole genome; the region arrays, repeated for every row ClickHouse reads, could
    also run it out of memory. The join costs about one more read of the family's rows.

    A row is kept when its chromosome, position and REF length, all that the rule reads of
    it, are those of a row the join keeps. It is then kept exactly when it overlaps a region
    itself, provided the join reads every row the search reads: it reads the family's rows
    (``sign = 1``) in the family's projects and within the search's locus, each a clause the
    search's own WHERE holds (``_small_variant_where_clauses``, mirrored as the annotation
    subqueries mirror it: ``_small_annotation_scope_clauses``). A clause left out would make
    the join read more rows, never keep another row."""

    def in_regions(regions: Sequence[Region], *, prefix: str, params: dict[str, Any]) -> str | None:
        if not context.assembly_name:
            # No entries table to join without an assembly: the rule, tested per row.
            return _small_region_filter_condition(regions, prefix=prefix, params=params)
        region_rows = _small_region_window_rows(regions, prefix=prefix, params=params)
        if region_rows is None:
            return None
        family_param = f"{prefix}_family_guid"
        params[family_param] = context.family_uuid
        rows_where = [f"fe.family_guid = %({family_param})s", "fe.sign = 1"]
        if context.project_ids:
            projects_param = f"{prefix}_project_ids"
            params[projects_param] = tuple(context.project_ids)
            rows_where.append(f"fe.project_guid IN %({projects_param})s")
        rows_where.extend(_small_annotation_scope_clauses(filters, params=params, alias="fe"))
        if filters.chromosome and "xpos_start" in params and "xpos_end" in params:
            rows_where.append("fe.xpos BETWEEN %(xpos_start)s AND %(xpos_end)s")
        columns, windows = _small_variant_windows("fe")
        entries_table = _small_table_name(context.assembly_name, "entries")
        return (
            "(e.chrom, e.pos, length(e.ref)) IN (SELECT v.chrom, v.pos, v.ref_length FROM ("
            f"SELECT fe.chrom AS chrom, length(fe.ref) AS ref_length, {columns}"
            f" FROM {entries_table} AS fe {windows} WHERE {' AND '.join(rows_where)})"
            f" AS v INNER JOIN ({region_rows}) AS r {_SMALL_REGION_WINDOW_MATCH})"
        )

    return in_regions


def _small_panel_filter_condition(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    panel_constraints: PanelFilterConstraints,
    *,
    params: dict[str, Any],
    region_condition: _SmallRegionCondition = _small_region_filter_condition,
) -> str | None:
    """A variant in one of the panel's regions, or annotated with one of its genes: in the
    entry row's own gene symbols or in the gene index. Every panel is matched both ways,
    whatever its size, the Mendeliome included: matched on gene symbols alone, a panel would
    miss a variant it reaches by position only (the owner's decision of 2026-10-14).

    ``region_condition`` builds the regions' condition; by default, the overlap rule tested
    against each region (``_small_region_filter_condition``). The family page passes a join
    over the family's own rows that keeps the same rows (``_small_family_region_condition``),
    and the Variant Explorer one over the annotation index, for every family's
    variants at once (``variant_explorer_service._region_key_condition``). Both
    bind the region arrays once, as parameters of the join."""
    conditions: list[str] = []
    in_regions = region_condition(panel_constraints.regions, prefix="panel_region", params=params)
    if in_regions:
        conditions.append(in_regions)
    gene_condition = _small_gene_filter_condition(
        panel_constraints.genes,
        prefix="panel_gene",
        params=params,
    )
    if gene_condition:
        conditions.append(gene_condition)
    annotation_gene_membership = _small_annotation_gene_membership_condition(
        context,
        filters,
        panel_constraints.genes,
        prefix="panel_annotation_gene",
        params=params,
    )
    if annotation_gene_membership:
        conditions.append(annotation_gene_membership)
    return f"({' OR '.join(conditions)})" if conditions else None


def _clinvar_overridden(block: str, *, params: dict[str, Any]) -> str:
    """``block``, or a ClinVar pathogenic or likely pathogenic record on the same transcript
    row: the *ClinVar P/LP override*. ``clinvar_terms`` holds the normalised terms
    (``_status_terms``) the filter terms read; ClinVar's aggregate form is stored split into
    its terms."""
    params["detail_clinvar_override_terms"] = list(_status_filter_terms(CLINVAR_OVERRIDE_TERMS))
    return f"({block} OR hasAny(a.clinvar_terms, %(detail_clinvar_override_terms)s))"


def _small_annotation_filter_condition(
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    strict_transcript_filters: bool = False,
    lifted: bool = False,
) -> str | None:
    """The search's per-transcript filters as one condition on a transcript row (alias ``a``),
    or None when the search asks nothing of a transcript: a variant is kept when they all hold
    on one of its transcripts.

    With ``clinvar_pathogenic_override``, a transcript row with a ClinVar pathogenic or likely
    pathogenic record passes three filters whatever its values: the consequence filter (the
    impacts, the consequences and the SpliceAI minimum, any of which keeps a transcript), the
    in-silico filter (CADD, REVEL, SIFT, PolyPhen and SpliceAI, any of which keeps it) and the
    frequency filter (the population frequencies and the gnomAD counts, all together). A
    synonymous or splice-region variant that ClinVar holds pathogenic so stays in a search for
    HIGH and MODERATE impact or for damaging scores, and a recurrent pathogenic allele above
    the frequency cut-off in a search for rare variants. The other filters still hold on that
    row: the transcript, HGVS, the ClinVar statuses, *Canonical only*, *MANE only* and *LoF
    only*.

    ``strict_transcript_filters=True`` leaves the consequence and in-silico filters strict:
    the transcript a variant names as the one its filters held on
    (``_filter_transcript_matches``) is one whose own consequence and scores meet them, and a
    variant the override alone keeps names none. The frequency filter keeps its override
    there: a variant's frequencies are the same on each of its transcripts.

    ``lifted=True`` is what the override leaves of the filters for a variant CoGA's ClinVar
    release holds (likely) pathogenic, an allele's classification that lifts the three filters
    on every transcript at once (``_clinvar_coga_lifted``): the other filters alone, or None
    when the search asks no other."""
    conditions: list[str] = []

    if filters.transcript:
        condition = _text_contains_any(
            "a.transcript_id",
            prefix="detail_transcript",
            values=[filters.transcript],
            params=params,
        )
        if condition:
            conditions.append(condition)
    if filters.hgvsc:
        condition = _text_contains_any(
            "a.hgvsc",
            prefix="detail_hgvsc",
            values=[filters.hgvsc],
            params=params,
        )
        if condition:
            conditions.append(condition)
    if filters.hgvsp:
        condition = _text_contains_any(
            "a.hgvsp",
            prefix="detail_hgvsp",
            values=[filters.hgvsp],
            params=params,
        )
        if condition:
            conditions.append(condition)

    # The consequence block: a transcript of one of the impacts, with one of the
    # consequences, or with a SpliceAI score at the minimum.
    impact_effect_conditions: list[str] = []
    impact_terms = tuple(_casefold(value) for value in filters.impact if str(value).strip())
    if impact_terms:
        params["detail_impact_terms"] = impact_terms
        impact_effect_conditions.append("a.impact IN %(detail_impact_terms)s")
    effect_terms = tuple(
        dict.fromkeys(
            term
            for value in filters.effect
            for term in _annotation_terms(value)
            if term
        )
    )
    if effect_terms:
        params["detail_effect_terms"] = list(effect_terms)
        impact_effect_conditions.append("hasAny(a.effects, %(detail_effect_terms)s)")
    # A frequency or a score is stored as Float32, and compared with the threshold at that
    # precision (toFloat32): at 64 bits a value stored exactly at the threshold could round
    # past it, so that a CADD score of 25.3 would fail a minimum of 25.3, a gnomAD frequency
    # of 0.001 a maximum of 0.001.
    if filters.min_spliceai is not None:
        params["detail_min_spliceai"] = filters.min_spliceai
        impact_effect_conditions.append("ifNull(a.spliceai_max, -1) >= toFloat32(%(detail_min_spliceai)s)")
    # The ClinVar P/LP override of the consequence and in-silico blocks.
    override_scores = filters.clinvar_pathogenic_override and not strict_transcript_filters
    if impact_effect_conditions and not lifted:
        consequence_block = f"({' OR '.join(impact_effect_conditions)})"
        if override_scores:
            consequence_block = _clinvar_overridden(consequence_block, params=params)
        conditions.append(consequence_block)

    clinvar_terms = _status_filter_terms(filters.clinvar)
    if clinvar_terms:
        params["detail_clinvar_terms"] = list(clinvar_terms)
        conditions.append("hasAny(a.clinvar_terms, %(detail_clinvar_terms)s)")

    if filters.canonical_only:
        conditions.append("a.canonical")
    if filters.mane_only:
        conditions.append(mane_only_condition("a."))
    if filters.lof_only:
        conditions.append("a.lof NOT IN ('', '.', 'na', 'n/a')")

    # The frequency block: one unit, which the ClinVar P/LP override lifts whole.
    frequency_conditions: list[str] = []
    max_float_filters = [
        ("detail_max_gnomad_af", filters.max_gnomad_af, "gnomad_af"),
        ("detail_max_gnomad_exomes_af", filters.max_gnomad_exomes_af, "gnomad_exomes_af"),
        ("detail_max_gnomad_genomes_af", filters.max_gnomad_genomes_af, "gnomad_genomes_af"),
        ("detail_max_gnomad_popmax_af", filters.max_gnomad_popmax_af, "gnomad_popmax_af"),
        ("detail_max_topmed_af", filters.max_topmed_af, "topmed_af"),
    ]
    for param, maximum, column in max_float_filters:
        if maximum is None:
            continue
        params[param] = maximum
        frequency_conditions.append(f"ifNull(a.{column}, 0) <= toFloat32(%({param})s)")

    max_int_filters = [
        ("detail_max_gnomad_ac", filters.max_gnomad_ac, "gnomad_ac"),
        ("detail_max_gnomad_hom_count", filters.max_gnomad_hom_count, "gnomad_hom_count"),
        ("detail_max_gnomad_hemi_count", filters.max_gnomad_hemi_count, "gnomad_hemi_count"),
    ]
    for param, maximum, column in max_int_filters:
        if maximum is None:
            continue
        params[param] = maximum
        frequency_conditions.append(f"ifNull(a.{column}, 0) <= %({param})s")

    if frequency_conditions and not lifted:
        if filters.clinvar_pathogenic_override:
            frequency_block = f"({' AND '.join(frequency_conditions)})"
            conditions.append(_clinvar_overridden(frequency_block, params=params))
        else:
            conditions.extend(frequency_conditions)

    # The in-silico block: a transcript with one of the predictions asked for.
    in_silico_conditions: list[str] = []
    if filters.min_cadd is not None:
        params["detail_min_cadd"] = filters.min_cadd
        in_silico_conditions.append("ifNull(a.cadd_phred, -1) >= toFloat32(%(detail_min_cadd)s)")
    if filters.min_revel is not None:
        params["detail_min_revel"] = filters.min_revel
        in_silico_conditions.append("ifNull(a.revel, -1) >= toFloat32(%(detail_min_revel)s)")
    # The stored SIFT or PolyPhen value names the prediction asked for, whatever its score:
    # the filter's one test (in_silico_prediction_pattern), so "deleterious" does not keep
    # "deleterious_low_confidence(0.03)".
    for column, prediction in (("sift", filters.sift), ("polyphen", filters.polyphen)):
        pattern = in_silico_prediction_pattern(prediction)
        if pattern is not None:
            params[f"detail_{column}_pattern"] = pattern
            in_silico_conditions.append(f"match(a.{column}, %(detail_{column}_pattern)s)")
    if filters.min_spliceai is not None:
        in_silico_conditions.append("ifNull(a.spliceai_max, -1) >= toFloat32(%(detail_min_spliceai)s)")
    if in_silico_conditions and not lifted:
        in_silico_block = f"({' OR '.join(in_silico_conditions)})"
        if override_scores:
            in_silico_block = _clinvar_overridden(in_silico_block, params=params)
        conditions.append(in_silico_block)

    return f"({' AND '.join(conditions)})" if conditions else None


def _small_detail_filter_clauses(filters: SmallVariantQueryFilters) -> tuple[list[str], dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    annotation_condition = _small_annotation_filter_condition(filters, params=params)
    if annotation_condition:
        clauses.append(annotation_condition)

    return clauses, params


# The impacts the small-variant track marks as salient (``smallVariantMarkKind`` in
# ``frontend/src/lib/smallVariantMarks.ts``), as the import stores them (in lower case).
_SALIENT_IMPACTS: Final = ("high", "moderate", "medium")


def _small_salient_condition(
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
) -> str | None:
    """With ``salient_only``, the transcript rows (alias ``a``) of a variant the small-variant
    track marks as salient: a ClinVar diamond, or of HIGH or MODERATE impact and not ClinVar
    (likely) benign. ClinVar is read as the browser reads it (``clinvarClass``,
    ``frontend/src/lib/clinvar.ts``): a diamond lists a (likely) pathogenic classification, red
    when it lists no other, orange beside one that disagrees; (likely) benign lists no other
    classification; ClinVar's aggregate conflict is neither. The impact is any transcript's, so
    the set holds every variant the track would mark by its shown transcript, and no
    pathogenic variant is left out for its impact.
    """
    if not filters.salient_only:
        return None
    params["salient_impacts"] = _SALIENT_IMPACTS
    return f"({_clinvar_diamond('a.clinvar_terms')} OR ({_SALIENT_IMPACT} AND NOT {_clinvar_benign('a.clinvar_terms')}))"


_SALIENT_IMPACT: Final = "a.impact IN %(salient_impacts)s"


def _clinvar_diamond(terms: str) -> str:
    """"``terms`` (ClinVar terms as ``_status_terms`` stores them) list a (likely) pathogenic
    classification": a ClinVar diamond on the small-variant track. ClinVar's aggregate conflict
    is none."""
    return f"arrayExists(term -> position(term, 'pathogenic') > 0 AND position(term, 'conflict') = 0, {terms})"


def _clinvar_benign(terms: str) -> str:
    """"``terms`` list a (likely) benign classification and no other": without a pathogenic term
    (the diamond's), benign beside an uncertain classification or a conflict is a conflict."""
    return (
        f"(arrayExists(term -> position(term, 'benign') > 0, {terms})"
        f" AND NOT arrayExists(term -> term = 'uncertain significance' OR position(term, 'conflict') > 0, {terms}))"
    )


def _salient_by_the_rule(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    annotated: str,
    *,
    params: dict[str, Any],
) -> str:
    """The salient rows (``annotated``: the annotation's, ``_small_salient_condition``) with
    ClinVar read by the design's one rule (B8), as the track marks a variant: the classification
    in CoGA's release of the request where it holds the row's alleles, else the annotation's. A
    row is salient for an allele the release holds (likely) pathogenic, for a held row of HIGH or
    MODERATE impact the release does not hold (likely) benign, and for any other row as without a
    release. A multi-allelic row the release holds in part is read both ways: the set holds every
    variant the track could mark."""
    if filters.clinvar_coga_release is None or not context.assembly_name:
        return annotated
    release = filters.clinvar_coga_release
    diamond = clinvar_allele_where_condition(
        context.assembly_name, release, _clinvar_diamond("classification_terms"), params=params, prefix="coga_diamond"
    )
    benign = clinvar_allele_where_condition(
        context.assembly_name, release, _clinvar_benign("classification_terms"), params=params, prefix="coga_benign"
    )
    impact = _small_annotation_row_membership_condition(context, filters, params=params, condition=_SALIENT_IMPACT)
    held = _clinvar_coga_held(context, filters, params=params)
    return f"({diamond} OR ({held} AND {impact} AND NOT {benign}) OR (NOT {held} AND {annotated}))"


def _small_annotation_exclude_filter_condition(
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
) -> str | None:
    exclude_clinvar_terms = _status_filter_terms(filters.exclude_clinvar)
    if not exclude_clinvar_terms:
        return None
    params["detail_exclude_clinvar_terms"] = list(exclude_clinvar_terms)
    return "hasAny(a.clinvar_terms, %(detail_exclude_clinvar_terms)s)"


def _small_annotation_scope_clauses(
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    alias: str = "ai",
) -> list[str]:
    clauses: list[str] = []
    if filters.chromosome and "chromosomes" in params:
        clauses.append(f"{alias}.chrom IN %(chromosomes)s")
    # The location as _small_variant_where_clauses reads it.
    if filters.start is not None:
        clauses.append(
            f"({alias}.pos + length({alias}.ref) - 1) >= %(start)s"
            if filters.overlap
            else f"{alias}.pos >= %(start)s"
        )
    if filters.end is not None:
        clauses.append(f"{alias}.pos <= %(end)s")
    return clauses


def _small_family_key_condition(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    alias: str,
    narrow: bool = False,
) -> str | None:
    """On the family page, "the annotation row ``alias`` is of a variant the family carries":
    its key is the key of one of the rows the search reads. None for a search that is not a
    family's (the Variant Explorer's reads every family's variants at once), and for a
    ``narrow`` subquery, one that reads few rows by itself: the rows of the genes a user typed,
    which the gene index's sort key finds (``_small_annotation_gene_membership_condition``).

    The annotation tables are shared by every family on the assembly. The family search tests
    its per-transcript filters on its rows' own transcripts (``_small_own_transcripts_condition``),
    and reads the shared tables for the rest: the gene (``gene_index``), the rsID
    (``annotation_index``) and the per-transcript filters with a transcript or HGVS text
    (``annotations``). Such a subquery without this clause gathers every matching variant of
    the whole cohort into the set its ``IN`` tests, whatever the family's size: a frequency
    filter alone gathered almost every variant stored, 1.6 GiB for 8 M variants, and at a few
    hundred genomes would pass the 4 GB a query may hold
    (``clickhouse/users.d/memory-guards.xml``). With it the set holds at most the family's own
    variants, and no more as other families are added. The clause costs a set of the family's
    keys, about 240 MiB for a whole-genome trio, and one more read of its rows: for a gene,
    whose own rows the gene index reads in a few granules, six times what the search holds
    without it (38 MiB), so a narrow subquery goes without it. A 5,300-gene panel's genes are
    not narrow: without the clause the set of the cohort's variants in them grew with the
    cohort, the panel search holding 513, 536 and 745 MiB at 50, 100 and 250 genomes, against
    481, 430 and 428 MiB with it (docs/clickhouse-scale-benchmark.md).

    The key is tested through ``identity``, which ClickHouse does not match to an index:
    ``key`` is in the primary key of the annotation tables and has a bloom filter, so a plain
    ``IN`` keeps a second copy of the set to test the index with (480 instead of 170 MiB for
    that family), and the keys of a whole genome fall in nearly every granule, so the index
    would skip none.

    It keeps the same rows, provided it reads every row the search reads: the family's rows
    (``sign = 1``) in the family's projects and within the search's locus, each a clause the
    search's own WHERE holds (``_small_variant_where_clauses``), as the panel's region join
    reads them (``_small_family_region_condition``). A row the search reads then has its key
    here, so it is in the narrowed set exactly when it was in the whole one, also for a
    ``NOT IN``. A clause left out would make the set larger, never change a row. Every
    subquery of one search writes the same text here, and ClickHouse builds its set once.

    A search that reads only some variants' rows, whatever their family, names their keys in
    ``annotation_keys`` (the Variant Explorer reading the columns of one page's variants):
    the annotation row's key is then one of those, by the same argument."""
    page_keys = tuple(getattr(context, "annotation_keys", None) or ())
    if page_keys:
        params["annotation_keys"] = page_keys
        return f"{alias}.key IN %(annotation_keys)s"
    if narrow or not isinstance(context, FamilyMetadataContext) or not context.assembly_name:
        return None
    params["annotation_family_guid"] = context.family_uuid
    rows_where = ["fk.family_guid = %(annotation_family_guid)s", "fk.sign = 1"]
    if context.project_ids:
        params["annotation_project_ids"] = tuple(context.project_ids)
        rows_where.append("fk.project_guid IN %(annotation_project_ids)s")
    rows_where.extend(_small_annotation_scope_clauses(filters, params=params, alias="fk"))
    if filters.chromosome and "xpos_start" in params and "xpos_end" in params:
        rows_where.append("fk.xpos BETWEEN %(xpos_start)s AND %(xpos_end)s")
    entries_table = _small_table_name(context.assembly_name, "entries")
    return f"identity({alias}.key) IN (SELECT fk.key FROM {entries_table} AS fk WHERE {' AND '.join(rows_where)})"


# An entries row (alias ``e``) is read with its own annotation: the rows of the annotation
# tables with its key and its annotation set (``annotationSetHash``, the hash of the variant's
# annotations as the import read them). The annotation version, which names where the
# annotations came from (``vcf_info``, ``vep_tsv``), is not matched: the rows of one key and
# one set hold the same annotations under any version, so their transcripts, genes and ClinVar
# terms meet the same filters. Matched too, the version (a LowCardinality string) makes the set
# an annotation subquery builds two to three times slower: on 4 M entries rows of 2 M variants
# a Variant Explorer search under a frequency, impact or CADD filter took 0.6-1.0 s with it
# and 0.23-0.47 s without, the family list 5-30 % longer (ClickHouse 26.8, 2026-11-06).
# One value of ``annotation_index`` is not the annotation's: the rsID of the record (its VCF
# ID). A row's own is in ``e.rsid``, which the rsID filter reads first; through the index a
# row also finds the rsID another import of the variant wrote with the same annotations, under
# any version.
_OWN_ANNOTATION: Final = "(e.key, e.annotationSetHash)"


def _small_annotation_gene_membership_condition(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    gene_values: Sequence[str],
    *,
    prefix: str,
    params: dict[str, Any],
    narrow: bool = False,
) -> str | None:
    """The entry row (alias ``e``) is, through the gene index of its own annotation, of one of
    ``gene_values`` (a symbol or an Ensembl gene ID). The gene index is read by its sort key,
    the genes' rows alone. With ``narrow`` (the genes a user typed: the gene filter, the
    excluded genes) the subquery goes without the family's keys, which would cost more than
    the genes' rows; a panel's genes, thousands of them, are narrowed to the family's keys
    (``_small_family_key_condition``)."""
    normalized_gene_values = tuple(
        _casefold(value)
        for value in gene_values
        if str(value or "").strip()
    )
    if not normalized_gene_values:
        return None
    if not context.assembly_name:
        return "0"
    terms_param = f"{prefix}_terms"
    params[terms_param] = normalized_gene_values
    gene_index_table = _small_annotation_gene_index_table_name(context.assembly_name)
    scope_clauses = _small_annotation_scope_clauses(filters, params=params, alias="gi")
    index_where = [*scope_clauses, f"gi.gene_term IN %({terms_param})s"]
    family_keys = _small_family_key_condition(context, filters, params=params, alias="gi", narrow=narrow)
    if family_keys:
        index_where.append(family_keys)
    return (
        f"{_OWN_ANNOTATION} IN ("
        f"SELECT gi.key, gi.annotationSetHash FROM {gene_index_table} AS gi "
        f"WHERE {' AND '.join(index_where)}"
        ")"
    )


def _small_annotation_key_membership_condition(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    condition: str,
    negate: bool = False,
) -> str:
    if not context.assembly_name:
        return "0" if not negate else "1"
    annotation_index_table = _small_annotation_index_table_name(context.assembly_name)
    scope_clauses = _small_annotation_scope_clauses(filters, params=params)
    operator = "NOT IN" if negate else "IN"
    index_where = [*scope_clauses, f"({condition})"]
    family_keys = _small_family_key_condition(context, filters, params=params, alias="ai")
    if family_keys:
        index_where.append(family_keys)
    return (
        f"{_OWN_ANNOTATION} {operator} ("
        f"SELECT ai.key, ai.annotationSetHash FROM {annotation_index_table} AS ai "
        f"WHERE {' AND '.join(index_where)}"
        ")"
    )


def _small_own_transcripts_condition(condition: str, *, negate: bool = False) -> str:
    """On the family page, "a transcript of the entry row's (alias ``e``) own annotation meets
    ``condition``", a condition on a transcript row ``a`` of ``variants/annotations``, read
    from the row's ``transcripts``: the values of its annotation's ``annotations`` rows that
    the filters test, under the same names and types
    (``clickhouse_variant_rows.ENTRY_TRANSCRIPT_FIELDS``), written with the row. With
    ``negate``, none does.

    It keeps the rows the subquery over the shared table keeps
    (``_small_annotation_row_membership_condition``), for ``IN`` and ``NOT IN`` alike: that
    subquery keeps a row when an ``annotations`` row of its key and annotation set, under any
    version, meets the condition, and the row's transcripts are made from the same annotations
    (the annotation set is their hash), each annotation's values as its ``annotations`` row
    stores them; a variant without an annotation holds the one transcript of the empty
    annotation, as the table holds its row. The subquery's locus clauses
    (``_small_annotation_scope_clauses``) leave out no row the search reads: a variant's
    annotation rows lie at its own position, which the search's WHERE holds to the locus.

    The shared table holds the whole cohort's variants, and the subquery read all of it for
    every search, list and count, however few rows the family has: 360 M rows for a rare
    search at 250 genomes, about 750 M at 1,000. The row's own transcripts are read with the
    row, so the search reads the family's rows alone, and builds no set."""
    test = f"arrayExists(a -> ({condition}), e.transcripts)"
    return f"NOT {test}" if negate else test


def _small_annotation_row_membership_condition(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    condition: str,
    negate: bool = False,
    reads_text: bool = False,
) -> str:
    """"A transcript of the entry row's (alias ``e``) own annotation meets ``condition``" (a
    condition on a transcript row ``a`` of ``variants/annotations``); with ``negate``, none
    does. A family search reads it from the row's own transcripts
    (``_small_own_transcripts_condition``), unless the condition ``reads_text`` (a transcript
    or HGVS text, which the row's transcripts do not hold); that search, and the Variant
    Explorer's, read the shared table, narrowed to the family's keys on the family page
    (``_small_family_key_condition``)."""
    if not context.assembly_name:
        return "0" if not negate else "1"
    if isinstance(context, FamilyMetadataContext) and not reads_text:
        return _small_own_transcripts_condition(condition, negate=negate)
    annotations_table = _small_annotation_table_name(context.assembly_name)
    scope_clauses = _small_annotation_scope_clauses(filters, params=params, alias="a")
    operator = "NOT IN" if negate else "IN"
    annotation_where = [*scope_clauses, f"({condition})"]
    family_keys = _small_family_key_condition(context, filters, params=params, alias="a")
    if family_keys:
        annotation_where.append(family_keys)
    return (
        f"{_OWN_ANNOTATION} {operator} ("
        f"SELECT a.key, a.annotationSetHash FROM {annotations_table} AS a "
        f"WHERE {' AND '.join(annotation_where)}"
        ")"
    )


def _small_native_sample_filter_clauses(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
) -> tuple[list[str], dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for index, entry in enumerate(filters.sample_filters):
        parsed = parse_small_variant_sample_filter(entry)
        if parsed is None:
            continue
        clauses.append(
            _small_sample_call_condition(
                (parsed.sample_name,), parsed, prefix=f"sample_filter_{index}", params=params
            )
        )
    return clauses, params


def _small_sample_call_condition(
    sample_ids: Sequence[str],
    sample_filter: SmallVariantSampleFilter,
    *,
    prefix: str,
    params: dict[str, Any],
    alias: str = "e",
) -> str:
    """One sample filter on the calls of the rows ``alias``: the sample, by any of its
    stored ``sample_ids``, has a call of one of the filter's genotype classes (any, without
    one) that meets its GQ, DP, allele-fraction and ALT-depth minimums; with
    ``include_absent``, a row without a call of the sample passes too. The family search
    reads each sample filter here (``_small_native_sample_filter_clauses``), and the Variant
    Explorer its genotype filter (``variant_explorer_service._carriers_where``)."""
    sample_param = f"{prefix}_samples"
    params[sample_param] = tuple(sample_ids)
    conditions = [f"sample_id IN %({sample_param})s"]
    if sample_filter.genotype_classes:
        conditions.append(
            clickhouse_genotype_condition(
                "gt", sample_filter.genotype_classes, param=f"{prefix}_gts", params=params
            )
        )
    if sample_filter.minimum_genotype_quality is not None:
        gq_param = f"{prefix}_min_gq"
        conditions.append(f"gq >= %({gq_param})s")
        params[gq_param] = sample_filter.minimum_genotype_quality
    if sample_filter.minimum_depth is not None:
        dp_param = f"{prefix}_min_dp"
        conditions.append(f"dp >= %({dp_param})s")
        params[dp_param] = sample_filter.minimum_depth
    if sample_filter.minimum_allele_frequency is not None:
        # At the precision the fraction is stored at (Float32), as in Python: 0.45 is
        # stored a little below 0.45, and at 64 bits would fail a minimum of 0.45.
        af_param = f"{prefix}_min_af"
        conditions.append(
            "("
            f"(length(af) > 0 AND arrayMax(af) >= toFloat32(%({af_param})s)) "
            f"OR (length(af) = 0 AND ab >= toFloat32(%({af_param})s))"
            ")"
        )
        params[af_param] = sample_filter.minimum_allele_frequency
    if sample_filter.minimum_alt_depth is not None:
        ad_param = f"{prefix}_min_ad_alt"
        conditions.append(f"(length(ad) > 1 AND ad[2] >= %({ad_param})s)")
        params[ad_param] = sample_filter.minimum_alt_depth
    calls = ", ".join(
        f"{alias}.calls.{column}" for column in ("sampleId", "gt", "gq", "dp", "ab", "af", "ad")
    )
    present_clause = (
        f"arrayExists((sample_id, gt, gq, dp, ab, af, ad) -> {' AND '.join(conditions)}, {calls})"
    )
    if not sample_filter.include_absent:
        return present_clause
    return (
        "("
        f"NOT arrayExists(sample_id -> sample_id IN %({sample_param})s, {alias}.calls.sampleId) "
        f"OR {present_clause}"
        ")"
    )


def _structural_region_bounds(regions: Sequence[Region]) -> tuple[list[str], list[int], list[int]]:
    """The regions as the structural-variant region condition reads them, as three parallel
    lists: each chromosome by its match key (``_chromosome_match_key``), each start and end
    in order, and of a chromosome's regions only those that lie inside no other, each once,
    in order of chromosome and start. A region without a chromosome is left out.

    An SV that overlaps a region by the rule of ``_structural_region_filter_condition`` (its
    start at or before the region's end, its end at or after the region's start) overlaps
    every region that holds that one, so the regions left keep every answer. They are the
    regions whose ends rise with their starts: the furthest end of the regions up to each
    start, as ``_RegionLookup`` reads them. A gene reference of one row per transcript
    resolves the Mendeliome to some 42,000 loci; in the synthetic cohort of
    docs/variant-ranking-cache.md ("Structural variants") about a third of them lie inside no
    other.

    Merging the regions that share a base, as the small-variant condition does
    (``_small_region_bounds``), would leave fewer but change an answer: an SV stored with its
    END two or more bases before its POS (an END a caller wrote so is stored as written)
    overlaps by the rule a region that holds both, and a chain of merged regions can hold
    both where none of its regions does."""
    by_chromosome: dict[str, list[tuple[int, int]]] = {}
    for region in regions:
        chrom = _chromosome_match_key(region.chr)
        if not chrom:
            continue
        start, end = sorted((int(region.start), int(region.end)))
        by_chromosome.setdefault(chrom, []).append((start, end))
    region_chromosomes: list[str] = []
    region_starts: list[int] = []
    region_ends: list[int] = []
    for chrom in sorted(by_chromosome):
        furthest: int | None = None
        # By start, and at one start the furthest end first: a region is inside one before
        # it exactly when its end reaches no further than the furthest end before it.
        for start, end in sorted(by_chromosome[chrom], key=lambda bound: (bound[0], -bound[1])):
            if furthest is not None and end <= furthest:
                continue
            furthest = end
            region_chromosomes.append(chrom)
            region_starts.append(start)
            region_ends.append(end)
    return region_chromosomes, region_starts, region_ends


def _structural_region_filter_condition(
    regions: Sequence[Region],
    *,
    prefix: str,
    params: dict[str, Any],
) -> str | None:
    """SQL for "this SV overlaps any of these regions", tested against each region
    (``_structural_region_bounds``): on the same chromosome (by match key), with the SV's
    start at or before the region's end and its end at or after the region's start. None
    without a region.

    This is the overlap rule of every structural-variant region filter. A gene filter's loci
    are tested this way; a gene panel's regions, which can be thousands (the Mendeliome's),
    are found by a join that keeps the same rows (``_structural_family_region_condition``).
    The small-variant equivalent works off ``e.pos``/``e.ref``; an SV carries an explicit
    span, so the overlap is between ``e.start``/``e.end`` and the region. An SV's breakend
    mate (its remote locus) is not read.
    """
    region_chromosomes, region_starts, region_ends = _structural_region_bounds(regions)
    if not region_chromosomes:
        return None

    chroms_param = f"{prefix}_chromosomes"
    starts_param = f"{prefix}_starts"
    ends_param = f"{prefix}_ends"
    params[chroms_param] = region_chromosomes
    params[starts_param] = region_starts
    params[ends_param] = region_ends
    chrom_expr = _clickhouse_chromosome_match_expr("e.chrom")
    return (
        "arrayExists((region_chrom, region_start, region_end) -> "
        f"{chrom_expr} = region_chrom "
        "AND e.start <= region_end "
        "AND e.end >= region_start, "
        f"%({chroms_param})s, %({starts_param})s, %({ends_param})s)"
    )


def _structural_variant_windows(alias: str) -> tuple[str, str]:
    """What a region join reads of the SV rows ``alias``: the columns (``chrom_key``, the
    chromosome's match key; ``sv_start`` and ``sv_end``, all that the overlap rule reads of
    the SV's span; ``variant_window``), and the ARRAY JOIN that lists each row under each
    64-kb window its span touches, from the lower of its start and end to the higher (an END
    stored before its POS included)."""
    chromosome = _clickhouse_chromosome_match_expr(f"{alias}.chrom")
    first = f"least(toInt64({alias}.start), toInt64({alias}.end))"
    last = f"greatest(toInt64({alias}.start), toInt64({alias}.end))"
    columns = f"{chromosome} AS chrom_key, {alias}.start AS sv_start, {alias}.end AS sv_end, variant_window"
    windows = (
        f"ARRAY JOIN range(intDiv({first}, {_REGION_WINDOW}), intDiv({last}, {_REGION_WINDOW}) + 1)"
        " AS variant_window"
    )
    return columns, windows


# The join of the SV rows (v, _structural_variant_windows) and the region rows (r,
# _region_window_rows over _structural_region_bounds) that share a window, keeping a pair
# that overlaps by the rule of _structural_region_filter_condition.
_STRUCTURAL_REGION_WINDOW_MATCH = (
    "ON v.chrom_key = r.region_chrom AND v.variant_window = r.region_window"
    " WHERE v.sv_start <= r.region_end AND v.sv_end >= r.region_start"
)


def _structural_locus_clauses(
    filters: StructuralVariantQueryFilters, *, alias: str, params: dict[str, Any]
) -> list[str]:
    """The search's chromosome and locus on the SV rows ``alias``: an SV on the chromosome,
    and starting at or after the locus's start and ending at or before its end or, with
    ``overlap`` and both given, overlapping the locus."""
    clauses: list[str] = []
    if filters.chromosome:
        clauses.append(f"{alias}.chrom IN %(chromosomes)s")
        params["chromosomes"] = _chromosome_options(filters.chromosome)
    if filters.start is not None:
        if filters.overlap and filters.end is not None:
            clauses.append(f"({alias}.start <= %(end)s AND {alias}.end >= %(start)s)")
            params["start"] = filters.start
            params["end"] = filters.end
        else:
            clauses.append(f"{alias}.start >= %(start)s")
            params["start"] = filters.start
    if filters.end is not None and not (filters.overlap and filters.start is not None):
        clauses.append(f"{alias}.end <= %(end)s")
        params["end"] = filters.end
    return clauses


def _structural_family_region_condition(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    regions: Sequence[Region],
    *,
    prefix: str,
    params: dict[str, Any],
) -> str | None:
    """The SV search's condition for a gene panel's regions, on its entry rows (alias
    ``e``): the rows that overlap a region by the rule of
    ``_structural_region_filter_condition``, found by a join on 64-kb windows over the
    family's own SV rows, as the small-variant family page finds its rows
    (``_small_family_region_condition``). None without a region.

    Tested against every region for each row (``arrayExists`` over the regions), the
    Mendeliome's thousands of loci cost each SV row thousands of tests, and the region arrays,
    repeated for every row ClickHouse reads, could run it out of memory. The join lists each
    region under the windows it touches and each SV under the windows its span touches, and
    keeps a pair that shares a window when the two overlap by the rule. An SV and a region
    that overlap share a window: one of a base they share or, for an SV stored with its END
    before its POS, every window from its END to its POS, all inside the region.

    A row is kept when its chromosome, start and end, all that the rule reads of it, are
    those of a row the join keeps. It is then kept exactly when it overlaps a region itself,
    provided the join reads every row the search reads: it reads the family's rows (``sign =
    1``) in the family's projects and within the search's chromosome and locus, each a clause
    of the search's own WHERE (``_structural_variant_where_clauses``). A clause left out
    would make the join read more rows, never keep another row. For the same reason a search
    of one chromosome (a genome track's) binds only the regions on that chromosome: no other
    can overlap a row it reads."""
    if filters.chromosome:
        keys = {_chromosome_match_key(option) for option in _chromosome_options(filters.chromosome)}
        regions = [region for region in regions if _chromosome_match_key(region.chr) in keys]
    if not context.assembly_name:
        # No entries table to join without an assembly: the rule, tested per row.
        return _structural_region_filter_condition(regions, prefix=prefix, params=params)
    region_rows = _region_window_rows(_structural_region_bounds(regions), prefix=prefix, params=params)
    if region_rows is None:
        return None
    family_param = f"{prefix}_family_guid"
    params[family_param] = context.family_uuid
    rows_where = [f"fe.family_guid = %({family_param})s", "fe.sign = 1"]
    if context.project_ids:
        projects_param = f"{prefix}_project_ids"
        params[projects_param] = tuple(context.project_ids)
        rows_where.append(f"fe.project_guid IN %({projects_param})s")
    rows_where.extend(_structural_locus_clauses(filters, alias="fe", params=params))
    columns, windows = _structural_variant_windows("fe")
    entries_table = _structural_table_name(context.assembly_name, "entries")
    return (
        "(e.chrom, e.start, e.end) IN (SELECT v.chrom, v.sv_start, v.sv_end FROM ("
        f"SELECT fe.chrom AS chrom, {columns}"
        f" FROM {entries_table} AS fe {windows} WHERE {' AND '.join(rows_where)})"
        f" AS v INNER JOIN ({region_rows}) AS r {_STRUCTURAL_REGION_WINDOW_MATCH})"
    )


def _structural_panel_filter_condition(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    panel: PanelFilterConstraints,
    *,
    params: dict[str, Any],
) -> str:
    """An SV that overlaps one of the panel's regions (``_structural_family_region_condition``)
    or whose annotation names one of its genes (the entry row's gene symbols, in any
    capitals): the rule of ``_StructuralPanelMatch``, whatever the panel's size, in SQL and so
    before the candidate cap. A panel with neither keeps no SV."""
    conditions: list[str] = []
    in_regions = _structural_family_region_condition(
        context, filters, panel.regions, prefix="sv_panel_region", params=params
    )
    if in_regions:
        conditions.append(in_regions)
    gene_condition = _small_gene_filter_condition(panel.genes, prefix="sv_panel_gene", params=params)
    if gene_condition:
        conditions.append(gene_condition)
    return f"({' OR '.join(conditions)})" if conditions else "0"


def _structural_variant_where_clauses(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    include_regions: Sequence[Region] = (),
    include_variant_ids: Collection[str] | None = None,
    exclude_variant_ids: Collection[str] = (),
    panel: PanelFilterConstraints | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """The WHERE of the family's SV rows (alias ``e``): its scope
    (``_structural_variant_scope_clauses``), then its regions and panel
    (``_structural_variant_region_clauses``)."""
    where_clauses, params = _structural_variant_scope_clauses(
        context, filters, include_variant_ids=include_variant_ids, exclude_variant_ids=exclude_variant_ids
    )
    where_clauses.extend(
        _structural_variant_region_clauses(context, filters, include_regions, panel, params=params)
    )
    return where_clauses, params


def _structural_variant_region_clauses(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    include_regions: Sequence[Region],
    panel: PanelFilterConstraints | None,
    *,
    params: dict[str, Any],
) -> list[str]:
    """The clauses of a search's regions (a gene filter's loci) and gene panel, on the rows
    of its scope (alias ``e``), binding their values in ``params``.

    A gene filter's loci and a gene panel are applied in SQL. Pushing them into SQL is
    what makes a candidate cap safe: the cap then bounds the ranking of an already-narrowed
    set rather than deciding, before the filter runs, which rows the filter gets to see."""
    where_clauses: list[str] = []
    region_condition = _structural_region_filter_condition(
        include_regions, prefix="sv_include_region", params=params
    )
    if region_condition:
        where_clauses.append(region_condition)
    if panel is not None:
        where_clauses.append(_structural_panel_filter_condition(context, filters, panel, params=params))
    return where_clauses


def _structural_variant_scope_clauses(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    *,
    include_variant_ids: Collection[str] | None = None,
    exclude_variant_ids: Collection[str] = (),
) -> tuple[list[str], dict[str, Any]]:
    """The family's SV rows (alias ``e``) a search reads before its regions and panel: the
    family's live rows in its projects with a call of a visible member, within the
    search's chromosome and locus, and of its review selection. A search of one of the family's
    analyses, named or chosen by its structural variants, leaves out the other analyses' SV
    sources (``AnalysisScope.left_out_sources``, analysis_scope.py)."""
    where_clauses = ["e.family_guid = %(family_guid)s", "e.sign = 1"]
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    if context.project_ids:
        where_clauses.append("e.project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(context.project_ids)
    where_clauses.extend(left_out_source_clauses(context.scope.left_out_sources("structural_variants"), params))
    visible_sample_ids = _visible_clickhouse_sample_ids(context)
    if visible_sample_ids:
        where_clauses.append("hasAny(e.calls.sampleId, %(visible_sample_ids)s)")
        params["visible_sample_ids"] = visible_sample_ids
    else:
        where_clauses.append("0")
    where_clauses.extend(_structural_locus_clauses(filters, alias="e", params=params))
    # A review selection (the report's "report" tag, a classification, notes) resolves to
    # variant ids in Postgres. In SQL, like the regions, it narrows the rows before the
    # candidate cap, so the cap cannot decide which reviewed SVs the search sees: a
    # reported SV beyond the first 50,000 rows of the callset is in the report too.
    # ``None`` means no selection; an empty selection matches nothing.
    if include_variant_ids is not None:
        normalized_include_ids = sorted(
            {str(variant_id).strip() for variant_id in include_variant_ids if str(variant_id).strip()}
        )
        if normalized_include_ids:
            where_clauses.append("e.variantId IN %(sv_include_variant_ids)s")
            params["sv_include_variant_ids"] = tuple(normalized_include_ids)
        else:
            where_clauses.append("0")
    normalized_exclude_ids = sorted(
        {str(variant_id).strip() for variant_id in exclude_variant_ids if str(variant_id).strip()}
    )
    if normalized_exclude_ids:
        where_clauses.append("e.variantId NOT IN %(sv_exclude_variant_ids)s")
        params["sv_exclude_variant_ids"] = tuple(normalized_exclude_ids)
    return where_clauses, params


def _structural_row_details(assembly_name: str, where_clauses: Sequence[str], columns: Sequence[str]) -> str:
    """The SV ``variants/details`` rows (``key`` and ``columns``) of the ``entries`` rows,
    alias ``e``, that ``where_clauses`` keep: the right side of a join on ``d.key = e.key``
    that answers exactly as the whole table does.

    ``where_clauses`` are a family's scope (``_structural_variant_scope_clauses``), which
    binds ``family_guid``. The details are read by their sort key, the family and then the
    key, for those rows' keys only: a key's details row is its family's, as the key hashes
    the family, so the read is the family's own rows. Joined whole, the right side is built
    from every family's SVs, with their annotation JSON, to answer one family's page or one
    SV."""
    entries_table = _structural_table_name(assembly_name, "entries")
    details_table = _structural_table_name(assembly_name, "variants/details")
    return (
        f"(SELECT key, {', '.join(columns)} FROM {details_table}"
        " WHERE family_guid = %(family_guid)s"
        f" AND key IN (SELECT e.key FROM {entries_table} AS e WHERE {' AND '.join(where_clauses)}))"
    )


def _page_offset(page: int, page_size: int) -> int:
    return max(page - 1, 0) * page_size


def _clamp_small_variant_page(page: int, page_size: int) -> int:
    """Bound ``page`` so a huge value can't force a deep-OFFSET scan+skip.

    The small-variant total is capped at ``_SMALL_COUNT_LIMIT``, so any page past the one
    that could hold real data returns empty regardless. Without this, ``page=10_000_000``
    would turn into a ~10^10-row DB OFFSET on the native/track_mode list path — a cheap
    amplification vector.
    """
    page = max(page, 1)
    if page_size <= 0:
        return page
    # One page beyond the last that could contain data (total <= _SMALL_COUNT_LIMIT).
    max_page = (_SMALL_COUNT_LIMIT + page_size - 1) // page_size + 1
    return min(page, max_page)


def _append_limit_offset(
    query: str,
    params: dict[str, Any],
    *,
    limit: int | None,
    offset: int,
) -> str:
    if limit is None:
        return query
    params["limit"] = max(int(limit), 0)
    params["offset"] = max(int(offset), 0)
    return f"{query}\n        LIMIT %(limit)s OFFSET %(offset)s"


def _small_query_filter_parts(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    panel_constraints: PanelFilterConstraints | None = None,
    include_variant_ids: Sequence[str] | None = None,
    exclude_variant_ids: Sequence[str] = (),
    include_regions: Sequence[Region] = (),
    exclude_regions: Sequence[Region] = (),
    exclude_gene_regions: Sequence[Region] = (),
    exclude_gene_terms: Sequence[str] = (),
    exclude_imputed: bool = False,
) -> tuple[list[str], dict[str, Any], bool]:
    where_clauses, params = _small_variant_where_clauses(
        context,
        filters,
        include_variant_ids=include_variant_ids,
        exclude_variant_ids=exclude_variant_ids,
        exclude_imputed=exclude_imputed,
    )
    where_clauses.extend(_small_annotation_filter_clauses(context, filters, params=params))

    panel_constraints = panel_constraints or PanelFilterConstraints()
    panel_condition = _small_panel_filter_condition(
        context,
        filters,
        panel_constraints,
        params=params,
        region_condition=_small_family_region_condition(context, filters),
    )
    if panel_condition:
        where_clauses.append(panel_condition)

    # "Second hit": restrict to genes also hit by a structural variant (intersected with any
    # panel/gene constraint above). sv_hit_genes is resolved from the family's SV→gene index.
    if filters.require_sv_second_hit:
        sv_hit_condition = (
            _small_gene_filter_condition(filters.sv_hit_genes, prefix="sv_hit_gene", params=params)
            if filters.sv_hit_genes
            else None
        )
        # No SV-hit genes (or none resolved) ⇒ nothing can qualify.
        where_clauses.append(sv_hit_condition or "0")

    include_region_condition = _small_region_filter_condition(
        include_regions,
        prefix="include_region",
        params=params,
    )
    if include_region_condition:
        where_clauses.append(include_region_condition)

    excluded_regions = [*exclude_regions, *exclude_gene_regions]
    exclude_region_condition = _small_region_filter_condition(
        excluded_regions,
        prefix="exclude_region",
        params=params,
    )
    if exclude_region_condition:
        where_clauses.append(f"NOT {exclude_region_condition}")

    exclude_gene_condition = _small_gene_condition(
        context,
        filters,
        exclude_gene_terms,
        entry_prefix="entry_exclude_gene",
        annotation_prefix="annotation_exclude_gene",
        params=params,
    )
    if exclude_gene_condition:
        where_clauses.append(f"NOT {exclude_gene_condition}")

    return where_clauses, params, False


def _small_gene_condition(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    gene_terms: Sequence[str],
    *,
    entry_prefix: str,
    annotation_prefix: str,
    params: dict[str, Any],
) -> str | None:
    """The entry row (alias ``e``) is of one of ``gene_terms``, in any capitals: in the row's
    own gene symbols, or in the gene index of its own annotation (its symbols and Ensembl
    gene IDs, ``_small_annotation_gene_membership_condition``). None without a term."""
    conditions = [
        condition
        for condition in (
            _small_gene_filter_condition(gene_terms, prefix=entry_prefix, params=params),
            _small_annotation_gene_membership_condition(
                context, filters, gene_terms, prefix=annotation_prefix, params=params, narrow=True
            ),
        )
        if condition
    ]
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else f"({' OR '.join(conditions)})"


def _clinvar_coga_condition(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    terms: Sequence[str],
    *,
    params: dict[str, Any],
    prefix: str,
) -> str:
    """"An allele of the row (alias ``e``) is one CoGA's ClinVar release of the request
    (``clinvar_coga_release``) classifies with one of ``terms``": the *ClinVar (CoGA)* filter,
    and the P/LP override's reading of that release. Without a release, no row's is."""
    if filters.clinvar_coga_release is None or not context.assembly_name:
        return "0"
    return clinvar_allele_condition(
        context.assembly_name, filters.clinvar_coga_release, terms, params=params, prefix=prefix
    )


def _clinvar_coga_lifted(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    condition: str,
    membership: str,
    *,
    params: dict[str, Any],
    reads_text: bool,
) -> str:
    """The transcript filters' ``membership`` (of their ``condition``), or, with the ClinVar P/LP
    override, an allele of
    the row (likely) pathogenic in CoGA's ClinVar release and the filters the override does not
    lift held on one of its transcripts (design B1): the override lifts the consequence,
    in-silico and frequency filters for CoGA's classification as for the annotation's. CoGA's
    is the allele's, not a transcript's, so it lifts them on every transcript of the row."""
    if not filters.clinvar_pathogenic_override or filters.clinvar_coga_release is None:
        return membership
    rest = _small_annotation_filter_condition(filters, params=params, lifted=True)
    if rest == condition:
        return membership  # the search asks none of the filters the override lifts
    pathogenic = _clinvar_coga_condition(
        context, filters, _status_filter_terms(CLINVAR_OVERRIDE_TERMS), params=params, prefix="coga_override"
    )
    if rest is None:
        return f"({membership} OR {pathogenic})"
    rest_membership = _small_annotation_row_membership_condition(
        context, filters, params=params, condition=rest, reads_text=reads_text
    )
    return f"({membership} OR ({pathogenic} AND {rest_membership}))"


def _clinvar_coga_held(context: _SearchAssembly, filters: SmallVariantQueryFilters, *, params: dict[str, Any]) -> str:
    """"CoGA's ClinVar release of the request holds every allele of the row (alias ``e``)": where
    the one rule of the design's phase 2 (B8) reads that release alone. Without a release, none."""
    if filters.clinvar_coga_release is None or not context.assembly_name:
        return "0"
    return clinvar_alleles_held_condition(
        context.assembly_name, filters.clinvar_coga_release, params=params, prefix="coga_held"
    )


def _transcript_filters_clause(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    reads_text: bool,
) -> str | None:
    """"One transcript of the row meets every per-transcript filter together", with the ClinVar
    P/LP override's reading of CoGA's release (``_clinvar_coga_lifted``); None when the search
    asks nothing of a transcript."""
    condition = _small_annotation_filter_condition(filters, params=params)
    if condition is None:
        return None
    membership = _small_annotation_row_membership_condition(
        context, filters, params=params, condition=condition, reads_text=reads_text
    )
    return _clinvar_coga_lifted(context, filters, condition, membership, params=params, reads_text=reads_text)


def _clinvar_read_by_the_rule(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    reads_text: bool,
) -> str | None:
    """The per-transcript filters with the *ClinVar status* filter read by the design's one rule
    (B8): CoGA's release of the request where it holds the row's alleles, else the annotation.
    A row the release holds whole is kept when one of its alleles has a status asked for there,
    and the other transcript filters hold on one of its transcripts; any other row when, as
    without a release, one transcript meets them all, ClinVar's statuses in the annotation
    included. A multi-allelic row the release holds in part is read both ways."""
    annotated = _transcript_filters_clause(context, filters, params=params, reads_text=reads_text)
    if not filters.clinvar or filters.clinvar_coga_release is None or not context.assembly_name:
        return annotated
    coga = _clinvar_coga_condition(
        context, filters, _status_filter_terms(filters.clinvar), params=params, prefix="coga_clinvar"
    )
    rest = _transcript_filters_clause(
        context, replace(filters, clinvar=[]), params=params, reads_text=reads_text
    )
    held = _clinvar_coga_held(context, filters, params=params)
    return f"(({coga}{f' AND {rest}' if rest else ''}) OR (NOT {held} AND {annotated}))"


def _small_annotation_filter_clauses(
    context: _SearchAssembly,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
) -> list[str]:
    """The annotation filters on an entries row (alias ``e``), each read on the row's own
    annotation (its key and annotation set, ``_OWN_ANNOTATION``): the gene
    (``_small_gene_condition``); one transcript meeting every per-transcript filter together
    (``_small_annotation_filter_condition``: impact, consequence, ClinVar, HGVS, frequency,
    in-silico scores, the ClinVar P/LP override of consequence, scores and frequency,
    Canonical, MANE and LoF only); no transcript of an excluded ClinVar class; and, for the small-variant
    track's wide window, the salient variants alone (``_small_salient_condition``). The family
    search (``_small_query_filter_parts``) and the Variant Explorer
    (``variant_explorer_service._carriers_where``) read them here, after the row filters
    (``_small_row_filter_clauses``), whose locus the annotation subqueries repeat. The
    family search reads the transcript conditions on the row's own transcripts, but with a
    transcript or HGVS text (``_small_annotation_row_membership_condition``).

    A variant that two families carry under two annotations (two VEP runs) is so kept for
    the family whose own annotation meets the filters: the Explorer counts that family's
    carriers alone (the owner's decision of 2026-11-06)."""
    clauses: list[str] = []
    # The transcript and HGVS filters test text a row's own transcripts do not hold
    # (``ENTRY_TRANSCRIPT_FIELDS``), as _text_contains_any reads them: their condition reads
    # the shared table.
    reads_text = any(
        str(value or "").strip() for value in (filters.transcript, filters.hgvsc, filters.hgvsp)
    )
    gene_condition = _small_gene_condition(
        context,
        filters,
        _split_gene_terms(filters.gene),
        entry_prefix="entry_gene",
        annotation_prefix="annotation_gene",
        params=params,
    )
    if gene_condition:
        clauses.append(gene_condition)

    transcript_clause = _clinvar_read_by_the_rule(context, filters, params=params, reads_text=reads_text)
    if transcript_clause:
        clauses.append(transcript_clause)

    if filters.clinvar_coga:
        clauses.append(
            _clinvar_coga_condition(
                context, filters, _status_filter_terms(filters.clinvar_coga), params=params, prefix="coga_filter"
            )
        )

    exclude_annotation_condition = _small_annotation_exclude_filter_condition(filters, params=params)
    if exclude_annotation_condition:
        # No transcript of an excluded ClinVar status, read by the one rule (B8): a row CoGA's
        # release holds whole is left out by its statuses there alone.
        not_annotated = _small_annotation_row_membership_condition(
            context,
            filters,
            params=params,
            condition=exclude_annotation_condition,
            negate=True,
        )
        if filters.clinvar_coga_release is None or not context.assembly_name:
            clauses.append(not_annotated)
        else:
            excluded = _clinvar_coga_condition(
                context,
                filters,
                _status_filter_terms(filters.exclude_clinvar),
                params=params,
                prefix="coga_exclude",
            )
            held = _clinvar_coga_held(context, filters, params=params)
            clauses.append(f"(NOT {excluded} AND ({held} OR {not_annotated}))")

    salient_condition = _small_salient_condition(filters, params=params)
    if salient_condition:
        annotated = _small_annotation_row_membership_condition(
            context,
            filters,
            params=params,
            condition=salient_condition,
        )
        clauses.append(_salient_by_the_rule(context, filters, annotated, params=params))
    return clauses


def _selected_structural_samples(
    context: FamilyMetadataContext,
    sample_names: Sequence[str],
) -> list[str] | None:
    if not sample_names:
        return [row["sample_id"] for row in context.sample_rows]
    selected = [
        sample_name
        for sample_name in sample_names
        if sample_name in context.sample_name_to_uuid
    ]
    return selected or None


def _has_filter_values(values: Sequence[Any] | None) -> bool:
    return any(str(value).strip() for value in values or [])


def _can_use_small_native_page(
    filters: SmallVariantQueryFilters,
    *,
    track_mode: bool,
) -> bool:
    return not any(
        (
            track_mode,
            filters.page_size <= 0,
            not _small_native_inheritance_supported(filters.inheritance),
            filters.expanded_carrier_screening,
        )
    )


def _small_track_limit_response(*, track_result_limit: int) -> VariantPage:
    return VariantPage(
        total=track_result_limit,
        total_is_estimated=True,
        count_limit=track_result_limit,
        variants=[],
    )


def _can_use_structural_native_page(
    filters: StructuralVariantQueryFilters,
    *,
    track_mode: bool,
) -> bool:
    return not any(
        (
            track_mode,
            filters.page_size <= 0,
            # No SQL applies a length filter: structural_variant_length_filters_match, in
            # _structural_record_matches, is its only rule.
            filters.length is not None,
            filters.min_length is not None,
            filters.variant_type,
            filters.source,
            _has_filter_values(filters.sample_filters),
            _has_filter_values(filters.selected_samples),
            filters.remote_chr,
            filters.remote_start is not None,
            filters.gene,
            filters.panel_id,
            filters.inheritance,
            filters.phenotype,
            filters.hpo,
            filters.moi,
            filters.gencc_support,
            _has_filter_values(filters.region_flags),
            filters.max_control_af is not None,
            filters.max_population_af is not None,
            filters.min_pli is not None,
            _has_filter_values(filters.review_classifications),
            _has_filter_values(filters.review_tags),
            _has_filter_values(filters.exclude_review_tags),
            filters.has_notes,
        )
    )


def _small_search_keeps_by_pairing(filters: SmallVariantQueryFilters) -> bool:
    """Whether the search keeps or lists a variant for what else it finds, not for the variant
    alone: compound-het and recessive pairing, the grouped view's compound-het genes, and the
    couple's gene rule of expanded carrier screening. No SQL decides these: such a search reads
    a window of candidates and applies them in Python, ranked or not."""
    return filters.inheritance in _PAIR_BASED_SMALL_INHERITANCE or filters.expanded_carrier_screening


def _small_pair_inheritance_candidate_limit(filters: SmallVariantQueryFilters) -> int | None:
    if not _small_search_keeps_by_pairing(filters):
        return None
    requested_rows = max(filters.page, 1) * max(filters.page_size, 1) * _SMALL_INHERITANCE_PAGE_CANDIDATE_MULTIPLIER
    candidate_rows = min(
        max(requested_rows, _SMALL_INHERITANCE_MIN_CANDIDATE_ROWS),
        _SMALL_INHERITANCE_MAX_CANDIDATE_ROWS,
    )
    return candidate_rows + 1


def _inheritance_item_sort_key(
    item: tuple[str, SmallVariantCompoundHetGene | SmallVariantRecord],
) -> tuple[int, int, int, str, int, int, str, str]:
    item_type, item_value = item
    if item_type == "group":
        return (0, *_small_record_sort_key(cast(SmallVariantCompoundHetGene, item_value).records[0]))
    return (1, *_small_record_sort_key(cast(SmallVariantRecord, item_value)))


def _segregation_flags(record: SmallVariantRecord) -> SmallSegregationFlags:
    """The single-variant inheritance modes the SQL found ``record`` to fit. A record read
    without them is a caller's mistake, never a variant that fits no mode."""
    if record.segregation is None:
        raise RuntimeError(
            "a small-variant record was read without its segregation flags "
            "(_fetch_small_variant_rows(segregation_flags=True))"
        )
    return record.segregation


def _inheritance_result_items(
    *,
    inheritance: str,
    records: Sequence[SmallVariantRecord],
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    pedigree: FamilyPedigree | None = None,
) -> list[tuple[str, SmallVariantCompoundHetGene | SmallVariantRecord]]:
    """What an inheritance mode keeps of ``records``: the compound-het pairs it finds among
    them, by gene (``_compound_het_genes``), and the variants a single-variant mode keeps, by
    the modes the SQL found each to fit (``SmallVariantRecord.segregation``). Recessive keeps
    both kinds: the genes with pairs, and the homozygous recessive and X-linked variants. The
    grouped view keeps every record: the genes with pairs, and each record in no pair as a
    single variant."""
    genes = _compound_het_genes(
        _compound_het_pairs(
            records,
            affected_samples=affected_samples,
            unaffected_samples=unaffected_samples,
            pedigree=pedigree,
        ),
        affected_samples=affected_samples,
        pedigree=pedigree,
    )
    pair_items: list[tuple[str, SmallVariantCompoundHetGene | SmallVariantRecord]] = [
        ("group", gene) for gene in genes
    ]
    if inheritance == _COMPOUND_HET_INHERITANCE:
        return pair_items

    if inheritance == _ALL_COMPOUND_HET_GROUPED_INHERITANCE:
        paired = {record.variant_id for gene in genes for record in gene.records}
        unpaired_items: list[tuple[str, SmallVariantCompoundHetGene | SmallVariantRecord]] = [
            ("variant", record) for record in records if record.variant_id not in paired
        ]
        return sorted([*pair_items, *unpaired_items], key=_inheritance_item_sort_key)

    if inheritance == _DE_NOVO_DOMINANT_INHERITANCE:
        return [("variant", record) for record in records if _segregation_flags(record).de_novo_dominant]

    if inheritance == _RECESSIVE_HOMOZYGOUS_INHERITANCE:
        return [("variant", record) for record in records if _segregation_flags(record).recessive_homozygous]

    if inheritance == _X_LINKED_INHERITANCE:
        return [("variant", record) for record in records if _segregation_flags(record).x_linked]

    if inheritance == _RECESSIVE_INHERITANCE:
        single_items = [
            ("variant", record)
            for record in records
            if _segregation_flags(record).recessive_homozygous or _segregation_flags(record).x_linked
        ]
        return sorted([*pair_items, *single_items], key=_inheritance_item_sort_key)

    return []


def _segregation_modes_by_variant(
    records: Sequence[SmallVariantRecord],
    *,
    context: FamilyMetadataContext,
) -> dict[str, list[str]]:
    affected, unaffected = _family_affected_unaffected_sample_names(context)
    sample_sex = _sample_sex_map(context.sample_rows)
    child_parents = _child_parent_map(context)
    parent_roles = _parent_roles(context)
    modes: dict[str, list[str]] = {record.variant_id: [] for record in records}
    if not affected:
        return modes
    for pair in _compound_het_pairs(
        records,
        affected_samples=affected,
        unaffected_samples=unaffected,
        pedigree=_family_pedigree(context),
    ):
        for vid in (pair.left.variant_id, pair.right.variant_id):
            if MODE_COMPOUND_HET not in modes.setdefault(vid, []):
                modes[vid].append(MODE_COMPOUND_HET)
    for record in records:
        bucket = modes.setdefault(record.variant_id, [])
        flags = _segregation_flags(record)
        if flags.recessive_homozygous:
            bucket.append(MODE_HOM_RECESSIVE)
        if flags.x_linked:
            bucket.append(MODE_X_LINKED)
        # Prefer the stronger, trio-confirmed de novo call; fall back to the dominant
        # pattern (which also covers inherited-dominant or no-trio cases). The trio de novo
        # is ranking evidence, read here alone: no search keeps or drops a variant by it.
        if _record_matches_de_novo(
            record,
            affected_samples=affected,
            child_parents=child_parents,
            sample_sex=sample_sex,
            parent_roles=parent_roles,
            assembly_name=context.assembly_name,
        ):
            bucket.append(MODE_DE_NOVO)
        elif flags.de_novo_dominant:
            bucket.append(MODE_DOMINANT)
    return modes


def _structural_segregation_modes(
    annotation_extra: dict[str, Any],
    context: FamilyMetadataContext | None = None,
) -> list[str]:
    """Derive coarse segregation modes from the SV's annotated inheritance.

    SV calls carry an ``Inheritance`` annotation (de novo / maternal / paternal /
    inherited) for the query sample (``Query_ID``) rather than per-sample genotype
    segregation. A de novo call maps to the strong de novo mode.

    An inherited call supports a dominant model only when the parent it came from is
    affected, and so is the child it was called in -- the rule the small-variant matcher
    applies to genotypes, so an SV from an unaffected parent does not rank as supporting
    segregation it argues against. "maternal" names
    the child's mother, "paternal" the father, and a bare "inherited" either parent. With
    no ``context``, or no pedigree link to read the parent from, the inherited call is left
    neutral (empty), matching the small-variant ``segregation_weight`` semantics.
    """
    inheritance = (annotation_extra.get("inheritance") or "").strip().lower()
    if not inheritance:
        return []
    if "de" in inheritance and "novo" in inheritance:
        return [MODE_DE_NOVO]
    if "maternal" in inheritance:
        transmitting_roles = {"mother"}
    elif "paternal" in inheritance:
        transmitting_roles = {"father"}
    elif "inherited" in inheritance:
        transmitting_roles = {"mother", "father"}
    else:
        return []
    if context is None:
        return []
    affected = set(context.affected_sample_names)
    parents_by_child: dict[str, list[tuple[str, str]]] = {}
    for child, parent, role in _parent_child_links(context):
        parents_by_child.setdefault(child, []).append((parent, role))
    query = str(annotation_extra.get("query_id") or "").strip()
    query_sample = resolve_vcf_sample_id(query, set(parents_by_child)) if query else None
    # Without a readable Query_ID, the inheritance can only describe a child the pedigree
    # gives parents to; any affected one will do.
    children = [query_sample] if query_sample else list(parents_by_child)
    for child in children:
        if child not in affected:
            continue
        if any(
            parent in affected
            for parent, role in parents_by_child.get(child, [])
            if role in transmitting_roles
        ):
            return [MODE_DOMINANT]
    return []
