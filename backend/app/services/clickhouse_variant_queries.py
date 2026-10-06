from __future__ import annotations

import json
import logging
import re
from typing import Any, Collection, Iterable, Sequence

from fastapi import HTTPException

from ..schemas import (
    GenotypeOut,
    SmallVariantReviewOut,
    SmallVariantTranscriptOut,
    VariantOut,
    VariantPage,
)
from .clickhouse_variant_ids import (
    _small_table_name,
)
from .data_scope import chromosome_aliases, normalize_chromosome
from .variant_annotation_parser import _spliceai_delta
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
from .sex_chromosomes import (
    XY_CHROMOSOME_NAMES,
    SexChromosome,
    hemizygous_chromosome,
    pseudoautosomal_regions,
)
from .family_variant_filters import (
    SmallVariantQueryFilters,
    StructuralVariantQueryFilters,
    parse_small_variant_sample_filter,
    parse_structural_sample_filter,
)
from .variant_prioritization import (
    MODE_COMPOUND_HET,
    MODE_DE_NOVO,
    MODE_DOMINANT,
    MODE_HOM_RECESSIVE,
    MODE_X_LINKED,
)
from .clickhouse_variant_records import (
    CLINVAR_FREQUENCY_RESCUE_TERMS,
    Region,
    PanelFilterConstraints,
    SmallVariantCall,
    SmallVariantRecord,
    SmallVariantCompoundHetPair,
    StructuralVariantRecord,
    _casefold,
    _contains_casefold,
    _status_filter_terms,
    _flexible_status_match,
    _coerce_int,
    _coerce_float,
    _annotation_terms,
    _annotation_text,
    _annotation_float,
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
    _annotation_matches_normal,
)


logger = logging.getLogger(__name__)


# Callset ``source`` values that hold imputed (not directly-called) genotypes.
# These are excluded by default from the diagnostic per-family small-variant list,
# counts, and summary (matching the global Variant Explorer's default), but remain
# available to callers that request a source explicitly — the phased-marker /
# relative-haplotype colouring and sample-integrity QC readers. Kept in sync with
# variant_explorer_service._IMPUTED_SOURCES.
IMPUTED_SMALL_VARIANT_SOURCES: tuple[str, ...] = ("glimpse2", "shapeit")
# A monogenic NIPT's callset stored one variant-only VCF per sample (the plasma and the
# father; family_package_nipt): a sample without a call at a variant had no alt read
# there, and a Mutect2 tumour-only GT says 0/1 whatever the allele fraction, so a reader
# that needs genotypes takes them from the allele depths (vaf_genotype).
NIPT_SMALL_VARIANT_SOURCE = "nipt"
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
# BED is 0-based, and taken as 1-based its interval would shift by a base (#604).
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


_X_CHROMOSOME_TOKENS = {"X", "23"}


_COMPOUND_HET_INHERITANCE = "compound_het"


_RECESSIVE_INHERITANCE = "recessive"


_RECESSIVE_HOMOZYGOUS_INHERITANCE = "recessive_homozygous"


_DE_NOVO_DOMINANT_INHERITANCE = "de_novo_dominant"


_X_LINKED_INHERITANCE = "x_linked"


_PAIR_BASED_SMALL_INHERITANCE = {
    _COMPOUND_HET_INHERITANCE,
    _RECESSIVE_INHERITANCE,
}


_SMALL_INHERITANCE_MIN_CANDIDATE_ROWS = 1000


_SMALL_INHERITANCE_MAX_CANDIDATE_ROWS = 5000


_SMALL_INHERITANCE_PAGE_CANDIDATE_MULTIPLIER = 25


_SMALL_COUNT_LIMIT = 10001


_SMALL_INHERITANCE_ALIASES = {
    "compound_heterozygous": _COMPOUND_HET_INHERITANCE,
    "recessive_hom": _RECESSIVE_HOMOZYGOUS_INHERITANCE,
    "homozygous_recessive": _RECESSIVE_HOMOZYGOUS_INHERITANCE,
    "de_novo": _DE_NOVO_DOMINANT_INHERITANCE,
    "dominant": _DE_NOVO_DOMINANT_INHERITANCE,
    "xlinked": _X_LINKED_INHERITANCE,
    "x_linked_recessive": _X_LINKED_INHERITANCE,
}


_SUPPORTED_SMALL_INHERITANCE = {
    _COMPOUND_HET_INHERITANCE,
    _RECESSIVE_INHERITANCE,
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


def _visible_clickhouse_sample_ids(context: FamilyMetadataContext) -> list[str]:
    sample_ids: list[str] = []
    for sample_name, sample_uuid in context.sample_name_to_uuid.items():
        _append_unique(sample_ids, sample_name)
        _append_unique(sample_ids, sample_uuid)
    return sample_ids


def _display_sample_name(context: FamilyMetadataContext, stored_sample_id: Any) -> str:
    sample_id = str(stored_sample_id or "").strip()
    if not sample_id:
        return ""
    if sample_id in context.sample_name_to_uuid:
        return sample_id
    mapped_name = context.sample_uuid_to_name.get(sample_id)
    if mapped_name:
        return mapped_name
    for sample_name, sample_uuid in context.sample_name_to_uuid.items():
        if sample_id == sample_uuid:
            return sample_name
    return sample_id


def _clickhouse_ids_for_sample(context: FamilyMetadataContext, sample_name: str) -> tuple[str, ...]:
    sample_ids: list[str] = []
    _append_unique(sample_ids, sample_name)
    _append_unique(sample_ids, context.sample_name_to_uuid.get(sample_name))
    return tuple(sample_ids)


def _chromosome_options(chromosome: str) -> tuple[str, ...]:
    return tuple(chromosome_aliases(chromosome))


def _chromosome_match_key(chromosome: str) -> str:
    normalized = normalize_chromosome(chromosome).upper()
    return "MT" if normalized in {"M", "MT"} else normalized


def _clickhouse_chromosome_match_expr(expr: str) -> str:
    stripped = f"upper(if(startsWith(lower({expr}), 'chr'), substring({expr}, 4), {expr}))"
    return f"if({stripped} IN ('M', 'MT'), 'MT', {stripped})"


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
    return (rank * 1_000_000_000) + max(int(pos), 0)


def _string_list(values: Any) -> list[str]:
    if values in (None, ""):
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
        parsed = _coerce_int(item)
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
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        "annotations",
        "sortedTranscriptConsequences",
        "transcriptConsequences",
        "transcripts",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    for value in payload.values():
        annotations = _collect_annotations(value)
        if annotations:
            return annotations
    return []


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
    seen: set[tuple[str, str, str, str, str]] = set()
    for annotation in sorted(annotations, key=_annotation_rank, reverse=True):
        transcript_id = _annotation_text(annotation, "transcript_id", "transcriptId")
        hgvsc = _annotation_text(annotation, "hgvsc")
        hgvsp = _annotation_text(annotation, "hgvsp")
        effect = _annotation_effect(annotation)
        impact = _annotation_text(annotation, "impact")
        if not any((transcript_id, hgvsc, hgvsp, effect, impact)):
            continue
        key = (
            transcript_id or "",
            hgvsc or "",
            hgvsp or "",
            effect or "",
            impact or "",
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
                gene=_annotation_gene(annotation),
                gene_id=_annotation_gene_id(annotation),
                transcript_id=transcript_id,
                transcript_source=_transcript_source(transcript_id),
                feature_type=_annotation_text(annotation, "feature_type", "featureType"),
                transcript_biotype=_annotation_text(annotation, "transcript_biotype", "transcriptBiotype"),
                impact=impact,
                effect=effect,
                hgvsc=hgvsc,
                hgvsp=hgvsp,
                exon=_annotation_text(annotation, "exon"),
                intron=_annotation_text(annotation, "intron"),
                canonical=_annotation_bool(annotation, "canonical"),
                mane_select=_annotation_bool(annotation, "mane_select", "maneSelect"),
                mane_plus_clinical=_annotation_bool(annotation, "mane_plus_clinical", "manePlusClinical"),
                primary=False,
            )
        )
    return transcripts


def _normalize_gt(value: Any) -> str:
    raw = str(value or "").strip()
    normalized = raw.upper()
    if normalized in {"REF", "REFERENCE", "WT", "WILDTYPE"}:
        return "0/0"
    if normalized in {"HET", "HETEROZYGOUS"}:
        return "0/1"
    if normalized in {"HOM", "HOM_ALT", "HOMOZYGOUS", "ALT"}:
        return "1/1"
    if normalized in {"MISSING", "NO_CALL"}:
        return "./."
    return raw or "./."


def _small_type(ref: str, alt: str) -> str:
    if len(ref) == 1 and len(alt) == 1:
        return "SNV"
    return "INDEL"


def _split_gene_terms(raw_value: str | None) -> list[str]:
    return [term for term in _GENE_QUERY_SPLIT.split(str(raw_value or "").strip()) if term]


def _parse_interval_regions(raw_value: str | None, *, label: str = "Interval") -> list[Region]:
    """The regions of an interval list, one per line or ``;``-separated entry.

    An entry that cannot be read as written is refused (422) and named. It used to be
    skipped: the search then covered less than the list asked, and a list with no entry
    left was answered as a family without variants (#604). Blank entries are ignored.
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


def _variant_overlaps_regions(chr_value: str, start: int, end: int, regions: Sequence[Region]) -> bool:
    normalized_chr = normalize_chromosome(chr_value)
    return any(
        normalized_chr == normalize_chromosome(region.chr)
        and start <= region.end
        and end >= region.start
        for region in regions
    )


def _variant_hits_gene_symbols(gene_symbols: Sequence[str], query: str | None) -> bool:
    terms = {_casefold(term) for term in _split_gene_terms(query)}
    if not terms:
        return True
    return bool({_casefold(symbol) for symbol in gene_symbols}.intersection(terms))


def _small_record_hits_gene_terms(record: SmallVariantRecord, terms: Sequence[str]) -> bool:
    normalized_terms = {_casefold(term) for term in terms if str(term).strip()}
    if not normalized_terms:
        return True
    record_terms = {_casefold(symbol) for symbol in record.gene_symbols}
    for annotation in record.annotations:
        for key in ("gene", "gene_id", "geneSymbol", "geneId", "hgnc_symbol", "hgncSymbol"):
            value = _annotation_text(annotation, key)
            if value:
                record_terms.add(_casefold(value))
    return bool(record_terms.intersection(normalized_terms))


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


def _band_position_contains(band: dict[str, Any], position: int) -> bool:
    start = _coerce_int(band.get("start"))
    end = _coerce_int(band.get("end"))
    if start is None or end is None:
        return False
    zero_based_position = max(0, position - 1)
    return start <= zero_based_position <= end


def _band_name_for_position(bands: Sequence[dict[str, Any]], position: int) -> str | None:
    for band in bands:
        name = str(band.get("name") or "").strip()
        if name and _band_position_contains(band, position):
            return name
    return None


def _format_cytoband_label(chromosome: str, start_band: str | None, end_band: str | None) -> str | None:
    if not start_band and not end_band:
        return None
    chrom = normalize_chromosome(chromosome)
    if start_band and end_band and start_band != end_band:
        return f"{chrom}{start_band}-{chrom}{end_band}"
    return f"{chrom}{start_band or end_band}"


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
            "query_ref_reads": _coerce_int(_structural_info_value(record, "Ref_Reads")),
            "query_total_reads": _coerce_int(_structural_info_value(record, "Total_Reads")),
            "maternal_ref_reads": _coerce_int(_structural_info_value(record, "Maternal_Ref_Reads")),
            "maternal_total_reads": _coerce_int(_structural_info_value(record, "Maternal_Total_Reads")),
            "paternal_ref_reads": _coerce_int(_structural_info_value(record, "Paternal_Ref_Reads")),
            "paternal_total_reads": _coerce_int(_structural_info_value(record, "Paternal_Total_Reads")),
        }.items()
        if value is not None
    }
    genotype_counts = {
        key: value
        for key, value in {
            "hom_ref": _coerce_int(_structural_info_value(record, "GT_homWT")),
            "het": _coerce_int(_structural_info_value(record, "GT_het")),
            "hom_alt": _coerce_int(_structural_info_value(record, "GT_homVAR")),
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


def _small_annotation_specific_requested(filters: SmallVariantQueryFilters) -> bool:
    return any(
        (
            filters.transcript,
            filters.impact,
            filters.effect,
            filters.clinvar,
            filters.hgvsc,
            filters.hgvsp,
            filters.canonical_only,
            filters.mane_only,
            filters.lof_only,
            filters.max_gnomad_af is not None,
            filters.max_gnomad_exomes_af is not None,
            filters.max_gnomad_genomes_af is not None,
            filters.max_gnomad_popmax_af is not None,
            filters.max_topmed_af is not None,
            filters.max_gnomad_ac is not None,
            filters.max_gnomad_hom_count is not None,
            filters.max_gnomad_hemi_count is not None,
            filters.min_cadd is not None,
            filters.min_revel is not None,
            filters.min_spliceai is not None,
            filters.sift,
            filters.polyphen,
        )
    )


def _matches_small_annotations(record: SmallVariantRecord, filters: SmallVariantQueryFilters) -> bool:
    annotations = record.annotations or [{}]
    normal_match = any(_annotation_matches_normal(annotation, filters) for annotation in annotations)
    if normal_match:
        return True
    annotation_specific_requested = _small_annotation_specific_requested(filters)
    return not annotation_specific_requested


def _small_record_matches_sample_filters(
    record: SmallVariantRecord,
    filters: SmallVariantQueryFilters,
) -> bool:
    call_map = {call.sample: call for call in record.calls}
    for entry in filters.sample_filters:
        sample_filter = parse_small_variant_sample_filter(entry)
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
        if sample_filter.minimum_genotype_quality is not None:
            if call.gq is None or call.gq < sample_filter.minimum_genotype_quality:
                return False
        if sample_filter.minimum_depth is not None:
            if call.dp is None or call.dp < sample_filter.minimum_depth:
                return False
        if sample_filter.minimum_allele_frequency is not None:
            if not call.af or max(call.af) < sample_filter.minimum_allele_frequency:
                return False
        if sample_filter.minimum_alt_depth is not None:
            alt_depth = call.ad[1] if len(call.ad) > 1 else None
            if alt_depth is None or alt_depth < sample_filter.minimum_alt_depth:
                return False
    return True


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


def _small_record_matches(
    record: SmallVariantRecord,
    filters: SmallVariantQueryFilters,
    include_regions: Sequence[Region],
    exclude_regions: Sequence[Region],
    exclude_gene_regions: Sequence[Region],
    panel_constraints: PanelFilterConstraints | None = None,
) -> bool:
    if filters.chromosome and normalize_chromosome(record.chr) != normalize_chromosome(filters.chromosome):
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
    if filters.variant_type and not _contains_casefold(_small_type(record.ref, record.alt), filters.variant_type):
        return False
    if filters.source and not _contains_casefold(record.source, filters.source):
        return False
    if filters.phase_set is not None and not any(call.ps == filters.phase_set for call in record.calls):
        return False
    if include_regions and not _variant_overlaps_regions(record.chr, record.start, record.end, include_regions):
        return False
    if exclude_regions and _variant_overlaps_regions(record.chr, record.start, record.end, exclude_regions):
        return False
    if exclude_gene_regions and _variant_overlaps_regions(record.chr, record.start, record.end, exclude_gene_regions):
        return False
    if panel_constraints and (panel_constraints.genes or panel_constraints.regions):
        panel_gene_match = bool(panel_constraints.genes) and _small_record_hits_gene_terms(
            record,
            panel_constraints.genes,
        )
        panel_region_match = bool(panel_constraints.regions) and _variant_overlaps_regions(
            record.chr,
            record.start,
            record.end,
            panel_constraints.regions,
        )
        if not (panel_gene_match or panel_region_match):
            return False
    if filters.gene and not _small_record_hits_gene_terms(record, _split_gene_terms(filters.gene)):
        return False
    if filters.rsid and not (
        _contains_casefold(record.rsid, filters.rsid)
        or any(_contains_casefold(_annotation_text(annotation, "rsid"), filters.rsid) for annotation in record.annotations)
    ):
        return False
    if filters.exclude_clinvar and any(
        _flexible_status_match(_annotation_clinvar(annotation), filters.exclude_clinvar)
        for annotation in record.annotations
    ):
        return False
    if not _matches_small_annotations(record, filters):
        return False
    return _small_record_matches_sample_filters(record, filters)


def _structural_record_matches(
    record: StructuralVariantRecord,
    filters: StructuralVariantQueryFilters,
    include_regions: Sequence[Region],
    selected_samples: Sequence[str],
    *,
    panel_gene_terms: set[str] | None = None,
) -> bool:
    if filters.chromosome and normalize_chromosome(record.chr) != normalize_chromosome(filters.chromosome):
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
    if filters.length is not None and abs(record.end - record.start) != filters.length:
        return False
    if filters.min_length is not None and abs(record.end - record.start) < filters.min_length:
        return False
    if filters.remote_chr and normalize_chromosome(record.remote_chr or "") != normalize_chromosome(filters.remote_chr):
        return False
    if filters.remote_start is not None and (record.remote_start is None or record.remote_start < filters.remote_start):
        return False
    if include_regions and not _variant_overlaps_regions(record.chr, record.start, record.end, include_regions):
        return False
    # Large gene panels (the Mendeliome) are matched by gene symbol rather than expanded
    # to thousands of regions — a per-SV overlap over those would dominate the request.
    if panel_gene_terms is not None:
        if {gene.lower() for gene in (record.gene_symbols or [])}.isdisjoint(panel_gene_terms):
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


def _chromosome_sort_key(chromosome: str) -> tuple[int, int, str]:
    cleaned = normalize_chromosome(chromosome).upper()
    numeric = _coerce_int(cleaned)
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
# chrX/chrY) and a multi-allelic one are judged like any other (#511).
def _call_is_het(call: SmallVariantCall | None) -> bool:
    return call is not None and classify_genotype(call.gt) == HET


def _call_is_hom_alt(call: SmallVariantCall | None) -> bool:
    return call is not None and classify_genotype(call.gt) == HOM_ALT


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
    could have passed it on is then decided by :func:`_hemizygous_inheritance_ruled_out`
    (#545).
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


def _is_x_chromosome(chromosome: str | None) -> bool:
    normalized = normalize_chromosome(str(chromosome or ""))
    return normalized.upper() in _X_CHROMOSOME_TOKENS


def _record_matches_de_novo_dominant(
    record: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    sample_sex: dict[str, str] | None = None,
    assembly_name: str | None = None,
) -> bool:
    """Carried in one copy by every affected sample, and by no unaffected one.

    One copy is a heterozygous call. For a male where he is hemizygous (chrX and chrY
    outside the PARs) it is also a haploid ``1`` or a ``1/1`` call, the only way a caller
    can write it there (#545).
    """
    if not affected_samples:
        return False
    call_map = _small_call_map(record)
    sex_of = sample_sex or {}
    hemizygous = hemizygous_chromosome(assembly_name, record.chr, record.start) is not None

    def carries_one_copy(sample: str) -> bool:
        call = call_map.get(sample)
        if _call_is_het(call):
            return True
        return hemizygous and _is_male_sex(sex_of.get(sample, "")) and _call_is_hom_alt(call)

    if not all(carries_one_copy(sample) for sample in affected_samples):
        return False
    return not any(_call_has_alt(call_map.get(sample)) for sample in unaffected_samples)


def _record_matches_homozygous_recessive(
    record: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
) -> bool:
    if not affected_samples:
        return False
    if _is_x_chromosome(record.chr):
        return False
    call_map = _small_call_map(record)
    if not all(_call_is_hom_alt(call_map.get(sample)) for sample in affected_samples):
        return False
    return not any(_call_is_hom_alt(call_map.get(sample)) for sample in unaffected_samples)


def _sample_sex_map(sample_rows: Sequence[dict[str, Any]]) -> dict[str, str]:
    return {
        str(row.get("sample_id") or "").strip(): str(row.get("sex") or "").strip().lower()
        for row in sample_rows
        if str(row.get("sample_id") or "").strip()
    }


def _is_male_sex(value: str) -> bool:
    return value in {"m", "male", "1"}


def _record_matches_x_linked_recessive(
    record: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    sample_sex: dict[str, str],
) -> bool:
    if not affected_samples or not _is_x_chromosome(record.chr):
        return False
    call_map = _small_call_map(record)
    if not all(_call_has_alt(call_map.get(sample)) for sample in affected_samples):
        return False

    for sample in unaffected_samples:
        sex = sample_sex.get(sample, "")
        call = call_map.get(sample)
        if _is_male_sex(sex):
            if _call_has_alt(call):
                return False
            continue
        if _call_is_hom_alt(call):
            return False
    return True


def _phased_alt_haplotype(gt: str | None) -> int | None:
    """Which haplotype carries the alt in a phased het call, or None.

    ``0|1`` -> 1 and ``1|0`` -> 0. None when the call cannot place a single alt on one
    haplotype: unphased (``0/1``), homozygous, no-call, or multi-allelic with an alt on
    both haplotypes (``1|2``), where "the" alt is ambiguous.
    """
    text = str(gt or "").strip()
    if "|" not in text:
        return None
    alleles = text.split("|")
    if len(alleles) != 2:
        return None
    left, right = (allele.strip() for allele in alleles)
    if left in ("", ".") or right in ("", "."):
        return None
    left_is_alt = left != "0"
    right_is_alt = right != "0"
    if left_is_alt == right_is_alt:
        return None
    return 0 if left_is_alt else 1


def _pair_phase_for_sample(
    left_call: SmallVariantCall | None,
    right_call: SmallVariantCall | None,
) -> str | None:
    """``cis``/``trans`` when read-backed phasing resolves the pair, else None.

    Both calls must sit in the same phase set (the caller's PS tag) for their haplotype
    indices to be comparable at all — indices from different phase blocks say nothing
    about each other.
    """
    if left_call is None or right_call is None:
        return None
    if left_call.ps is None or right_call.ps is None or left_call.ps != right_call.ps:
        return None
    left_haplotype = _phased_alt_haplotype(left_call.gt)
    right_haplotype = _phased_alt_haplotype(right_call.gt)
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


def _compound_het_pair_phase(
    left: SmallVariantRecord,
    right: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    pedigree: FamilyPedigree | None = None,
) -> str | None:
    """The pair's phase, or None when it is not a compound-het candidate at all."""
    verdict = _compound_het_pair_verdict(
        left,
        right,
        affected_samples=affected_samples,
        unaffected_samples=unaffected_samples,
        pedigree=pedigree,
    )
    return None if verdict is None else verdict[0]


def _records_form_compound_het_pair(
    left: SmallVariantRecord,
    right: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    pedigree: FamilyPedigree | None = None,
) -> bool:
    return (
        _compound_het_pair_phase(
            left,
            right,
            affected_samples=affected_samples,
            unaffected_samples=unaffected_samples,
            pedigree=pedigree,
        )
        is not None
    )


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
                pair_ids = tuple(sorted((left.variant_id, right.variant_id)))
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


def _normalize_small_variant_inheritance(value: str | None) -> str | None:
    normalized = _casefold(value).replace("-", "_").replace(" ", "_").replace("/", "_")
    if not normalized:
        return None
    normalized = _SMALL_INHERITANCE_ALIASES.get(normalized, normalized)
    if normalized not in _SUPPORTED_SMALL_INHERITANCE:
        raise HTTPException(status_code=400, detail="Unsupported small-variant inheritance filter")
    return normalized


def _carrier_partner_names(
    sample_rows: Sequence[dict[str, Any]],
    relationship_rows: Sequence[dict[str, Any]] | None = None,
) -> tuple[str, str] | None:
    sample_names = {
        str(row.get("sample_id") or "").strip()
        for row in sample_rows
        if str(row.get("sample_id") or "").strip()
    }
    for relationship in relationship_rows or []:
        if relationship.get("relationship_type") != "couple":
            continue
        left = str(relationship.get("sample_id_a") or "").strip()
        right = str(relationship.get("sample_id_b") or "").strip()
        if left and right and left != right and left in sample_names and right in sample_names:
            return left, right
    mother = next((row.get("sample_id") for row in sample_rows if row.get("role") == "mother"), None)
    father = next((row.get("sample_id") for row in sample_rows if row.get("role") == "father"), None)
    if mother and father:
        return str(mother), str(father)
    if len(sample_rows) == 2:
        left = str(sample_rows[0].get("sample_id") or "").strip()
        right = str(sample_rows[1].get("sample_id") or "").strip()
        if left and right and left != right:
            return left, right
    return None


def _has_alt_allele(gt: str) -> bool:
    return genotype_has_alt(gt)


def _filter_expanded_carrier_screening(
    records: Sequence[SmallVariantRecord],
    sample_rows: Sequence[dict[str, Any]],
    relationship_rows: Sequence[dict[str, Any]] | None = None,
    *,
    assembly_name: str | None = None,
) -> list[SmallVariantRecord]:
    """The couple's at-risk findings (REQ-CARR-002), in the order given.

    A gene in which both partners carry a variant: every variant either of them carries
    in it (a recessive condition needs a variant from each). On chrX outside the
    pseudo-autosomal regions, also every variant a female partner carries on her own:
    for an X-linked condition her carrier status alone puts a son at risk, whatever the
    other partner carries. A partner not recorded as male counts as female here, as the
    rest of CoGA reads such a sample as diploid on chrX; on an assembly whose
    pseudo-autosomal regions are not known (``hemizygous_chromosome``) only the
    both-partners rule applies. A variant without a gene is never a finding.
    """
    partners = _carrier_partner_names(sample_rows, relationship_rows)
    if partners is None:
        return []
    sample_sex = _sample_sex_map(sample_rows)
    x_carrier_partners = [partner for partner in partners if not _is_male_sex(sample_sex.get(partner, ""))]
    qualifying_ids: set[str] = set()
    carrier_variants: dict[tuple[str, str], list[SmallVariantRecord]] = {}
    carrier_sets: dict[tuple[str, str], set[str]] = {}
    for record in records:
        gene, gene_id = _primary_gene_keys(record)
        keys: list[tuple[str, str]] = []
        if gene_id:
            keys.append(("gene_id", gene_id))
        if gene:
            keys.append(("gene", gene))
        if not keys:
            continue
        call_map = {call.sample: call.gt for call in record.calls}
        carriers = {partner for partner in partners if _has_alt_allele(call_map.get(partner, "./."))}
        if not carriers:
            continue
        if any(partner in carriers for partner in x_carrier_partners) and (
            hemizygous_chromosome(assembly_name, record.chr, record.start) == "X"
        ):
            qualifying_ids.add(record.variant_id)
        for key in keys:
            carrier_sets.setdefault(key, set()).update(carriers)
            carrier_variants.setdefault(key, []).append(record)
    for key, carriers in carrier_sets.items():
        if all(partner in carriers for partner in partners):
            qualifying_ids.update(record.variant_id for record in carrier_variants.get(key, []))
    return [record for record in records if record.variant_id in qualifying_ids]


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


def _small_variant_out(record: SmallVariantRecord, *, assembly_name: str | None) -> VariantOut:
    annotation = _select_primary_annotation(record.annotations)
    population_frequencies = _annotation_population_frequencies(annotation)
    transcripts = _small_transcript_annotations(record.annotations, annotation)
    return VariantOut(
        _id=record.variant_id,
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
        rsid=record.rsid or _annotation_text(annotation, "rsid"),
        transcript_id=_annotation_text(annotation, "transcript_id", "transcriptId"),
        feature_type=_annotation_text(annotation, "feature_type", "featureType"),
        transcript_biotype=_annotation_text(annotation, "transcript_biotype", "transcriptBiotype"),
        hgvsc=_annotation_text(annotation, "hgvsc"),
        hgvsp=_annotation_text(annotation, "hgvsp"),
        canonical=_annotation_bool(annotation, "canonical"),
        mane_select=_annotation_bool(annotation, "mane_select", "maneSelect"),
        mane_plus_clinical=_annotation_bool(annotation, "mane_plus_clinical", "manePlusClinical"),
        exon=_annotation_text(annotation, "exon"),
        intron=_annotation_text(annotation, "intron"),
        lof=_annotation_text(annotation, "lof"),
        lof_filter=_annotation_text(annotation, "lof_filter", "lofFilter"),
        lof_flags=_annotation_text(annotation, "lof_flags", "lofFlags"),
        gnomad_af=_annotation_float(annotation, "gnomad_af", "gnomadAf"),
        gnomad_hom_count=_annotation_int(annotation, "gnomad_hom_count", "gnomadHomCount"),
        population_frequencies=population_frequencies,
        cadd_raw=_annotation_float(annotation, "cadd_raw", "caddRaw"),
        cadd_phred=_annotation_float(annotation, "cadd_phred", "caddPhred"),
        revel=_annotation_float(annotation, "revel"),
        sift=_annotation_sift(annotation),
        polyphen=_annotation_polyphen(annotation),
        spliceai_ds_ag=_spliceai_delta(_annotation_float(annotation, "spliceai_ds_ag", "spliceaiDsAg")),
        spliceai_ds_al=_spliceai_delta(_annotation_float(annotation, "spliceai_ds_al", "spliceaiDsAl")),
        spliceai_ds_dg=_spliceai_delta(_annotation_float(annotation, "spliceai_ds_dg", "spliceaiDsDg")),
        spliceai_ds_dl=_spliceai_delta(_annotation_float(annotation, "spliceai_ds_dl", "spliceaiDsDl")),
        spliceai_max=_annotation_spliceai_max(annotation),
        alpha_missense_pathogenicity=_annotation_float(
            annotation, "alpha_missense_pathogenicity", "alphaMissensePathogenicity"
        ),
        alpha_missense_class=_normalize_alpha_missense_class(
            _annotation_text(annotation, "alpha_missense_class", "alphaMissenseClass")
        ),
        annotation_extra=_annotation_extra(annotation),
        transcripts=transcripts,
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
) -> VariantOut:
    allowed_samples = set(selected_samples)
    calls = [call for call in record.calls if not allowed_samples or call.sample in allowed_samples]
    length = record.sv_len if record.sv_len is not None else record.end - record.start
    annotation_extra = _structural_annotation_extra(record, track_mode=track_mode)
    if cytoband and not track_mode:
        annotation_extra["cytoband"] = cytoband
    population_frequencies = annotation_extra.get("population_frequencies")
    return VariantOut(
        _id=record.variant_id,
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
    )


def _family_affected_unaffected_sample_names(
    context: FamilyMetadataContext,
) -> tuple[list[str], list[str]]:
    affected_sample_names = [
        sample_name
        for sample_name in context.affected_sample_names
        if sample_name in context.sample_name_to_uuid
    ]
    affected_sample_set = set(affected_sample_names)
    unaffected_sample_names = [
        str(row.get("sample_id") or "").strip()
        for row in context.sample_rows
        if str(row.get("sample_id") or "").strip()
        and str(row.get("clinical_status") or "").lower() == "unaffected"
        and str(row.get("sample_id") or "").strip() not in affected_sample_set
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
    sample_ids = _clickhouse_ids_for_sample(context, sample_name)
    params[sample_param] = sample_ids or (sample_name,)
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

    None when the assembly's PARs are not known: there a male is read as diploid, as
    before (#545). The PAR bounds are bound as parameters.
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


def _small_native_inheritance_clauses(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
) -> tuple[list[str], dict[str, Any]]:
    inheritance = filters.inheritance
    if not inheritance and not filters.expanded_carrier_screening:
        return [], {}

    params: dict[str, Any] = {}
    clauses: list[str] = []
    alt_gt_values = ALT_CLASSES
    het_gt_values = frozenset({HET})
    hom_alt_gt_values = frozenset({HOM_ALT})

    if filters.expanded_carrier_screening:
        partners = _carrier_partner_names(context.sample_rows, context.relationship_rows)
        if partners is None:
            return ["0"], params
        partner_alt_clauses = _small_all_samples_have_gts_condition(
            context,
            sample_names=partners,
            classes=alt_gt_values,
            prefix="carrier_screen_partner_alt",
            params=params,
        )
        clauses.append(f"({' OR '.join(partner_alt_clauses)})")
        clauses.append("length(e.gene_symbols) > 0")

    if not inheritance:
        return clauses, params

    affected_samples, unaffected_samples = _family_affected_unaffected_sample_names(context)
    if not affected_samples:
        return ["0"], params

    if inheritance == _DE_NOVO_DOMINANT_INHERITANCE:
        # One copy in every affected sample: a heterozygous call, or, for a male where he is
        # hemizygous, a haploid "1" or "1/1" call (#545). Mirrors
        # _record_matches_de_novo_dominant.
        sample_sex = _sample_sex_map(context.sample_rows)
        hemizygous_position: str | None = None
        if any(_is_male_sex(sample_sex.get(sample, "")) for sample in affected_samples):
            hemizygous_position = _small_hemizygous_position_condition(
                context.assembly_name, prefix="inheritance_hemizygous", params=params
            )
        for index, sample_name in enumerate(affected_samples):
            het_condition = _small_sample_gt_exists_condition(
                context,
                sample_name=sample_name,
                classes=het_gt_values,
                prefix=f"inheritance_affected_het_{index}",
                params=params,
            )
            if hemizygous_position is None or not _is_male_sex(sample_sex.get(sample_name, "")):
                clauses.append(het_condition)
                continue
            hemizygous_condition = _small_sample_gt_exists_condition(
                context,
                sample_name=sample_name,
                classes=hom_alt_gt_values,
                prefix=f"inheritance_affected_hemizygous_{index}",
                params=params,
            )
            clauses.append(f"({het_condition} OR ({hemizygous_condition} AND {hemizygous_position}))")
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=unaffected_samples,
                classes=alt_gt_values,
                prefix="inheritance_unaffected_alt",
                params=params,
            )
        )
    elif inheritance == _RECESSIVE_HOMOZYGOUS_INHERITANCE:
        params["inheritance_x_chromosomes"] = ("X", "chrX", "23", "chr23")
        clauses.append("e.chrom NOT IN %(inheritance_x_chromosomes)s")
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                classes=hom_alt_gt_values,
                prefix="inheritance_affected_hom_alt",
                params=params,
            )
        )
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=unaffected_samples,
                classes=hom_alt_gt_values,
                prefix="inheritance_unaffected_hom_alt",
                params=params,
            )
        )
    elif inheritance == _X_LINKED_INHERITANCE:
        params["inheritance_x_chromosomes"] = ("X", "chrX", "23", "chr23")
        clauses.append("e.chrom IN %(inheritance_x_chromosomes)s")
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                classes=alt_gt_values,
                prefix="inheritance_affected_alt",
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
                classes=alt_gt_values,
                prefix="inheritance_unaffected_male_alt",
                params=params,
            )
        )
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=other_unaffected,
                classes=hom_alt_gt_values,
                prefix="inheritance_unaffected_hom_alt",
                params=params,
            )
        )
    elif inheritance == _COMPOUND_HET_INHERITANCE:
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                classes=het_gt_values,
                prefix="inheritance_affected_het",
                params=params,
            )
        )
    elif inheritance == _RECESSIVE_INHERITANCE:
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                classes=alt_gt_values,
                prefix="inheritance_affected_alt",
                params=params,
            )
        )

    return clauses, params


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
    if filters.phase_set is not None:
        where_clauses.append("has(e.calls.ps, %(phase_set)s)")
        params["phase_set"] = filters.phase_set
    if filters.variant_type:
        variant_type = _casefold(filters.variant_type).upper()
        if variant_type == "SNV":
            where_clauses.append("length(e.ref) = 1 AND length(e.alt) = 1")
        elif variant_type == "MNV":
            where_clauses.append("length(e.ref) = length(e.alt) AND length(e.ref) > 1")
        elif variant_type == "INDEL":
            where_clauses.append("NOT (length(e.ref) = 1 AND length(e.alt) = 1)")
    if filters.source:
        params["source"] = filters.source
        where_clauses.append("positionCaseInsensitive(e.source, %(source)s) > 0")
    elif exclude_imputed:
        # Diagnostic list/count: hide imputed callsets (glimpse2/shapeit) by default,
        # consistent with the global Variant Explorer. An explicit filters.source still
        # surfaces them (handled above); the phased-marker / sample-QC readers pass
        # exclude_imputed=False so they continue to see every callset.
        params["imputed_sources"] = tuple(IMPUTED_SMALL_VARIANT_SOURCES)
        where_clauses.append("lowerUTF8(e.source) NOT IN %(imputed_sources)s")
    if filters.rsid:
        params["rsid"] = filters.rsid
        annotation_rsid_condition = _small_annotation_key_membership_condition(
            context,
            filters,
            params=params,
            condition="positionCaseInsensitive(ifNull(ai.rsid, ''), %(rsid)s) > 0",
        )
        where_clauses.append(
            f"(positionCaseInsensitive(ifNull(e.rsid, ''), %(rsid)s) > 0 OR {annotation_rsid_condition})"
        )
    if filters.chromosome:
        where_clauses.append("e.chrom IN %(chromosomes)s")
        params["chromosomes"] = _chromosome_options(filters.chromosome)
    if filters.start is not None:
        if filters.overlap and filters.end is not None:
            where_clauses.append("(e.pos <= %(end)s AND (e.pos + length(e.ref) - 1) >= %(start)s)")
            params["start"] = filters.start
            params["end"] = filters.end
        else:
            where_clauses.append("e.pos >= %(start)s")
            params["start"] = filters.start
    if filters.end is not None and not (filters.overlap and filters.start is not None):
        where_clauses.append("e.pos <= %(end)s")
        params["end"] = filters.end
    if filters.chromosome:
        xpos_start = 0 if filters.overlap and filters.end is not None else (filters.start or 0)
        xpos_end = filters.end if filters.end is not None else 999_999_999
        where_clauses.append("e.xpos BETWEEN %(xpos_start)s AND %(xpos_end)s")
        params["xpos_start"] = _xpos(filters.chromosome, xpos_start)
        params["xpos_end"] = _xpos(filters.chromosome, xpos_end)
    return where_clauses, params


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


def _small_region_filter_condition(
    regions: Sequence[Region],
    *,
    prefix: str,
    params: dict[str, Any],
) -> str | None:
    region_chromosomes: list[str] = []
    region_starts: list[int] = []
    region_ends: list[int] = []
    seen: set[tuple[str, int, int]] = set()
    for region in regions:
        chrom = _chromosome_match_key(region.chr)
        if not chrom:
            continue
        start = int(region.start)
        end = int(region.end)
        if end < start:
            start, end = end, start
        key = (chrom, start, end)
        if key in seen:
            continue
        seen.add(key)
        region_chromosomes.append(chrom)
        region_starts.append(start)
        region_ends.append(end)
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


# Above this many panel regions, inlining the (chrom, start, end) arrays and running a
# per-variant arrayExists over them is both slow and big enough to risk the ClickHouse
# query-size limit. Large panels are gene panels (e.g. the ~5,300-gene Mendeliome), so the
# compact gene-symbol + gene-index matching below covers them — skip the region expansion.
_PANEL_REGION_INLINE_LIMIT = 1000


def _small_panel_filter_condition(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    panel_constraints: PanelFilterConstraints,
    *,
    params: dict[str, Any],
) -> str | None:
    conditions: list[str] = []
    skip_regions = (
        bool(panel_constraints.genes)
        and len(panel_constraints.regions) > _PANEL_REGION_INLINE_LIMIT
    )
    region_condition = (
        None
        if skip_regions
        else _small_region_filter_condition(
            panel_constraints.regions,
            prefix="panel_region",
            params=params,
        )
    )
    if region_condition:
        conditions.append(region_condition)
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


def _small_annotation_filter_condition(
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
) -> str | None:
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
    if filters.min_spliceai is not None:
        params["detail_min_spliceai"] = filters.min_spliceai
        impact_effect_conditions.append("ifNull(a.spliceai_max, -1) >= %(detail_min_spliceai)s")
    if impact_effect_conditions:
        conditions.append(f"({' OR '.join(impact_effect_conditions)})")

    clinvar_terms = _status_filter_terms(filters.clinvar)
    if clinvar_terms:
        params["detail_clinvar_terms"] = list(clinvar_terms)
        conditions.append("hasAny(a.clinvar_terms, %(detail_clinvar_terms)s)")

    if filters.canonical_only:
        conditions.append("a.canonical")
    if filters.mane_only:
        conditions.append("(a.mane_select OR a.mane_plus_clinical)")
    if filters.lof_only:
        conditions.append("a.lof NOT IN ('', '.', 'na', 'n/a')")

    # The population-frequency block. It is one unit because a ClinVar P/LP call can
    # "rescue" a variant past all of it — the same block the Python matcher skips
    # (clickhouse_variant_records._annotation_matches_normal). Before #534 this SQL
    # applied the ceilings unconditionally, so a recurrent pathogenic allele above the
    # cut-off was dropped here, before the rescue could keep it.
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
        frequency_conditions.append(f"ifNull(a.{column}, 0) <= %({param})s")

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

    if frequency_conditions:
        frequency_block = " AND ".join(frequency_conditions)
        if filters.clinvar_overrides_frequency:
            # clinvar_terms holds the same normalised terms the Python matcher compares.
            # Rows stored before "/" was a separator keep ClinVar's aggregate form as
            # one term, so match that too until the family is re-imported.
            rescue_terms = list(_status_filter_terms(CLINVAR_FREQUENCY_RESCUE_TERMS))
            rescue_terms.append("/".join(rescue_terms))
            params["detail_clinvar_rescue_terms"] = rescue_terms
            conditions.append(
                f"(({frequency_block}) OR hasAny(a.clinvar_terms, %(detail_clinvar_rescue_terms)s))"
            )
        else:
            conditions.extend(frequency_conditions)

    in_silico_conditions: list[str] = []
    if filters.min_cadd is not None:
        params["detail_min_cadd"] = filters.min_cadd
        in_silico_conditions.append("ifNull(a.cadd_phred, -1) >= %(detail_min_cadd)s")
    if filters.min_revel is not None:
        params["detail_min_revel"] = filters.min_revel
        in_silico_conditions.append("ifNull(a.revel, -1) >= %(detail_min_revel)s")
    if filters.sift:
        sift_condition = _text_contains_any(
            "a.sift",
            prefix="detail_sift",
            values=[filters.sift],
            params=params,
        )
        if sift_condition:
            in_silico_conditions.append(sift_condition)
    if filters.polyphen:
        polyphen_condition = _text_contains_any(
            "a.polyphen",
            prefix="detail_polyphen",
            values=[filters.polyphen],
            params=params,
        )
        if polyphen_condition:
            in_silico_conditions.append(polyphen_condition)
    if filters.min_spliceai is not None:
        in_silico_conditions.append("ifNull(a.spliceai_max, -1) >= %(detail_min_spliceai)s")
    if in_silico_conditions:
        conditions.append(f"({' OR '.join(in_silico_conditions)})")

    return f"({' AND '.join(conditions)})" if conditions else None


def _small_detail_filter_clauses(filters: SmallVariantQueryFilters) -> tuple[list[str], dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    annotation_condition = _small_annotation_filter_condition(filters, params=params)
    if annotation_condition:
        clauses.append(annotation_condition)

    return clauses, params


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
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    alias: str = "ai",
) -> list[str]:
    clauses: list[str] = []
    if filters.chromosome and "chromosomes" in params:
        clauses.append(f"{alias}.chrom IN %(chromosomes)s")
    if filters.start is not None:
        if filters.overlap and filters.end is not None:
            clauses.append(
                f"({alias}.pos <= %(end)s AND ({alias}.pos + length({alias}.ref) - 1) >= %(start)s)"
            )
        else:
            clauses.append(f"{alias}.pos >= %(start)s")
    if filters.end is not None and not (filters.overlap and filters.start is not None):
        clauses.append(f"{alias}.pos <= %(end)s")
    return clauses


def _small_annotation_gene_membership_condition(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    gene_values: Sequence[str],
    *,
    prefix: str,
    params: dict[str, Any],
) -> str | None:
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
    scope_clauses = _small_annotation_scope_clauses(context, filters, params=params, alias="gi")
    index_where = [*scope_clauses, f"gi.gene_term IN %({terms_param})s"]
    return (
        "(e.key, e.annotation_version, e.annotationSetHash) IN ("
        f"SELECT gi.key, gi.annotation_version, gi.annotationSetHash FROM {gene_index_table} AS gi "
        f"WHERE {' AND '.join(index_where)}"
        ")"
    )


def _small_annotation_key_membership_condition(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    condition: str,
    negate: bool = False,
) -> str:
    if not context.assembly_name:
        return "0" if not negate else "1"
    annotation_index_table = _small_annotation_index_table_name(context.assembly_name)
    scope_clauses = _small_annotation_scope_clauses(context, filters, params=params)
    operator = "NOT IN" if negate else "IN"
    index_where = [*scope_clauses, f"({condition})"]
    return (
        f"(e.key, e.annotation_version, e.annotationSetHash) {operator} ("
        f"SELECT ai.key, ai.annotation_version, ai.annotationSetHash FROM {annotation_index_table} AS ai "
        f"WHERE {' AND '.join(index_where)}"
        ")"
    )


def _small_annotation_row_scope_clauses(
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
) -> list[str]:
    clauses: list[str] = []
    if filters.chromosome and "chromosomes" in params:
        clauses.append("a.chrom IN %(chromosomes)s")
    if filters.start is not None:
        if filters.overlap and filters.end is not None:
            clauses.append("(a.pos <= %(end)s AND (a.pos + length(a.ref) - 1) >= %(start)s)")
        else:
            clauses.append("a.pos >= %(start)s")
    if filters.end is not None and not (filters.overlap and filters.start is not None):
        clauses.append("a.pos <= %(end)s")
    return clauses


def _small_annotation_row_membership_condition(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    params: dict[str, Any],
    condition: str,
    negate: bool = False,
) -> str:
    if not context.assembly_name:
        return "0" if not negate else "1"
    annotations_table = _small_annotation_table_name(context.assembly_name)
    scope_clauses = _small_annotation_row_scope_clauses(filters, params=params)
    operator = "NOT IN" if negate else "IN"
    annotation_where = [*scope_clauses, f"({condition})"]
    return (
        f"(e.key, e.annotation_version, e.annotationSetHash) {operator} ("
        f"SELECT a.key, a.annotation_version, a.annotationSetHash FROM {annotations_table} AS a "
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
        sample_param = f"sample_filter_{index}_samples"
        sample_ids = _clickhouse_ids_for_sample(context, parsed.sample_name)
        if not sample_ids:
            continue
        conditions = [f"sample_id IN %({sample_param})s"]
        params[sample_param] = sample_ids
        if parsed.genotype_classes:
            conditions.append(
                clickhouse_genotype_condition(
                    "gt", parsed.genotype_classes, param=f"sample_filter_{index}_gts", params=params
                )
            )
        if parsed.minimum_genotype_quality is not None:
            gq_param = f"sample_filter_{index}_min_gq"
            conditions.append(f"gq >= %({gq_param})s")
            params[gq_param] = parsed.minimum_genotype_quality
        if parsed.minimum_depth is not None:
            dp_param = f"sample_filter_{index}_min_dp"
            conditions.append(f"dp >= %({dp_param})s")
            params[dp_param] = parsed.minimum_depth
        if parsed.minimum_allele_frequency is not None:
            af_param = f"sample_filter_{index}_min_af"
            conditions.append(
                "("
                f"(length(af) > 0 AND arrayMax(af) >= %({af_param})s) "
                f"OR (length(af) = 0 AND ab >= %({af_param})s)"
                ")"
            )
            params[af_param] = parsed.minimum_allele_frequency
        if parsed.minimum_alt_depth is not None:
            ad_param = f"sample_filter_{index}_min_ad_alt"
            conditions.append(f"(length(ad) > 1 AND ad[2] >= %({ad_param})s)")
            params[ad_param] = parsed.minimum_alt_depth
        present_clause = (
            "arrayExists((sample_id, gt, gq, dp, ab, af, ad) -> "
            f"{' AND '.join(conditions)}, "
            "e.calls.sampleId, e.calls.gt, e.calls.gq, e.calls.dp, e.calls.ab, e.calls.af, e.calls.ad)"
        )
        if parsed.include_absent:
            clauses.append(
                "("
                f"NOT arrayExists(sample_id -> sample_id IN %({sample_param})s, e.calls.sampleId) "
                f"OR {present_clause}"
                ")"
            )
        else:
            clauses.append(present_clause)
    return clauses, params


def _structural_region_filter_condition(
    regions: Sequence[Region],
    *,
    prefix: str,
    params: dict[str, Any],
) -> str | None:
    """SQL for "this SV overlaps any of these regions".

    The small-variant equivalent works off ``e.pos``/``e.ref``; an SV carries an explicit
    span, so the overlap is between ``e.start``/``e.end`` and the region.
    """
    region_chromosomes: list[str] = []
    region_starts: list[int] = []
    region_ends: list[int] = []
    seen: set[tuple[str, int, int]] = set()
    for region in regions:
        chrom = _chromosome_match_key(region.chr)
        if not chrom:
            continue
        start = int(region.start)
        end = int(region.end)
        if end < start:
            start, end = end, start
        key = (chrom, start, end)
        if key in seen:
            continue
        seen.add(key)
        region_chromosomes.append(chrom)
        region_starts.append(start)
        region_ends.append(end)
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


def _structural_variant_where_clauses(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    include_regions: Sequence[Region] = (),
    include_variant_ids: Collection[str] | None = None,
    exclude_variant_ids: Collection[str] = (),
) -> tuple[list[str], dict[str, Any]]:
    where_clauses = ["e.family_guid = %(family_guid)s", "e.sign = 1"]
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    if context.project_ids:
        where_clauses.append("e.project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(context.project_ids)
    visible_sample_ids = _visible_clickhouse_sample_ids(context)
    if visible_sample_ids:
        where_clauses.append("hasAny(e.calls.sampleId, %(visible_sample_ids)s)")
        params["visible_sample_ids"] = visible_sample_ids
    else:
        where_clauses.append("0")
    if filters.chromosome:
        where_clauses.append("e.chrom IN %(chromosomes)s")
        params["chromosomes"] = _chromosome_options(filters.chromosome)
    if filters.start is not None:
        if filters.overlap and filters.end is not None:
            where_clauses.append("(e.start <= %(end)s AND e.end >= %(start)s)")
            params["start"] = filters.start
            params["end"] = filters.end
        else:
            where_clauses.append("e.start >= %(start)s")
            params["start"] = filters.start
    if filters.end is not None and not (filters.overlap and filters.start is not None):
        where_clauses.append("e.end <= %(end)s")
        params["end"] = filters.end
    # Gene and panel filters resolve to regions. Pushing them into SQL is what makes a
    # candidate cap safe: the cap then bounds the ranking of an already-narrowed set
    # rather than deciding, before the filter runs, which rows the filter gets to see.
    region_condition = _structural_region_filter_condition(
        include_regions, prefix="sv_include_region", params=params
    )
    if region_condition:
        where_clauses.append(region_condition)
    # A review selection (the report's "report" tag, a classification, notes) resolves to
    # variant ids in Postgres. In SQL, like the regions, it narrows the rows before the
    # candidate cap, so the cap cannot decide which reviewed SVs the search sees: a
    # reported SV beyond the first 50,000 rows of the callset was missing from the report.
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


def _page_offset(page: int, page_size: int) -> int:
    return max(page - 1, 0) * page_size


def _clamp_small_variant_page(page: int, page_size: int) -> int:
    """Bound ``page`` so a huge value can't force a deep-OFFSET scan+skip.

    The small-variant total is capped at ``_SMALL_COUNT_LIMIT``, so any page past the one
    that could hold real data returns empty regardless. Without this, ``page=10_000_000``
    turned into a ~10^10-row DB OFFSET on the native/track_mode list path — a cheap
    amplification vector. (#333)
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

    gene_condition = _small_gene_filter_condition(
        _split_gene_terms(filters.gene),
        prefix="entry_gene",
        params=params,
    )
    gene_terms = _split_gene_terms(filters.gene)
    annotation_gene_membership = _small_annotation_gene_membership_condition(
        context,
        filters,
        gene_terms,
        prefix="annotation_gene",
        params=params,
    )
    if gene_condition and annotation_gene_membership:
        where_clauses.append(f"({gene_condition} OR {annotation_gene_membership})")
    elif gene_condition:
        where_clauses.append(gene_condition)
    elif annotation_gene_membership:
        where_clauses.append(annotation_gene_membership)

    detail_where_clauses, detail_params = _small_detail_filter_clauses(filters)
    params.update(detail_params)
    for detail_where_clause in detail_where_clauses:
        where_clauses.append(
            _small_annotation_row_membership_condition(
                context,
                filters,
                params=params,
                condition=detail_where_clause,
            )
        )

    exclude_annotation_condition = _small_annotation_exclude_filter_condition(filters, params=params)
    if exclude_annotation_condition:
        where_clauses.append(
            _small_annotation_row_membership_condition(
                context,
                filters,
                params=params,
                condition=exclude_annotation_condition,
                negate=True,
            )
        )

    panel_constraints = panel_constraints or PanelFilterConstraints()
    panel_condition = _small_panel_filter_condition(context, filters, panel_constraints, params=params)
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

    exclude_gene_condition = _small_gene_filter_condition(
        exclude_gene_terms,
        prefix="entry_exclude_gene",
        params=params,
    )
    exclude_annotation_membership = _small_annotation_gene_membership_condition(
        context,
        filters,
        exclude_gene_terms,
        prefix="annotation_exclude_gene",
        params=params,
    )
    if exclude_gene_condition and exclude_annotation_membership:
        where_clauses.append(f"NOT ({exclude_gene_condition} OR {exclude_annotation_membership})")
    elif exclude_gene_condition:
        where_clauses.append(f"NOT {exclude_gene_condition}")
    elif exclude_annotation_membership:
        where_clauses.append(f"NOT {exclude_annotation_membership}")

    return where_clauses, params, False


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


def _small_pair_inheritance_candidate_limit(filters: SmallVariantQueryFilters) -> int | None:
    if filters.inheritance not in _PAIR_BASED_SMALL_INHERITANCE and not filters.expanded_carrier_screening:
        return None
    requested_rows = max(filters.page, 1) * max(filters.page_size, 1) * _SMALL_INHERITANCE_PAGE_CANDIDATE_MULTIPLIER
    candidate_rows = min(
        max(requested_rows, _SMALL_INHERITANCE_MIN_CANDIDATE_ROWS),
        _SMALL_INHERITANCE_MAX_CANDIDATE_ROWS,
    )
    return candidate_rows + 1


def _inheritance_item_sort_key(
    item: tuple[str, SmallVariantCompoundHetPair | SmallVariantRecord],
) -> tuple[int, int, int, str, int, int, str, str]:
    item_type, item_value = item
    if item_type == "group":
        return (0, *_small_record_sort_key(item_value.left))
    return (1, *_small_record_sort_key(item_value))


def _inheritance_result_items(
    *,
    inheritance: str,
    records: Sequence[SmallVariantRecord],
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    sample_rows: Sequence[dict[str, Any]],
    assembly_name: str | None = None,
    pedigree: FamilyPedigree | None = None,
) -> list[tuple[str, SmallVariantCompoundHetPair | SmallVariantRecord]]:
    pair_items = [
        ("group", pair)
        for pair in _compound_het_pairs(
            records,
            affected_samples=affected_samples,
            unaffected_samples=unaffected_samples,
            pedigree=pedigree,
        )
    ]
    if inheritance == _COMPOUND_HET_INHERITANCE:
        return pair_items

    if inheritance == _DE_NOVO_DOMINANT_INHERITANCE:
        sample_sex = _sample_sex_map(sample_rows)
        return [
            ("variant", record)
            for record in records
            if _record_matches_de_novo_dominant(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
                sample_sex=sample_sex,
                assembly_name=assembly_name,
            )
        ]

    if inheritance == _RECESSIVE_HOMOZYGOUS_INHERITANCE:
        return [
            ("variant", record)
            for record in records
            if _record_matches_homozygous_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
            )
        ]

    sample_sex = _sample_sex_map(sample_rows)

    if inheritance == _X_LINKED_INHERITANCE:
        return [
            ("variant", record)
            for record in records
            if _record_matches_x_linked_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
                sample_sex=sample_sex,
            )
        ]

    if inheritance == _RECESSIVE_INHERITANCE:
        homozygous_items = [
            ("variant", record)
            for record in records
            if _record_matches_homozygous_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
            )
        ]
        x_linked_items = [
            ("variant", record)
            for record in records
            if _record_matches_x_linked_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
                sample_sex=sample_sex,
            )
        ]
        deduped_variant_items: dict[str, tuple[str, SmallVariantRecord]] = {}
        for item_type, record in [*homozygous_items, *x_linked_items]:
            deduped_variant_items[record.variant_id] = (item_type, record)
        return sorted(
            [*pair_items, *deduped_variant_items.values()],
            key=_inheritance_item_sort_key,
        )

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
        if _record_matches_homozygous_recessive(
            record, affected_samples=affected, unaffected_samples=unaffected
        ):
            bucket.append(MODE_HOM_RECESSIVE)
        if _record_matches_x_linked_recessive(
            record,
            affected_samples=affected,
            unaffected_samples=unaffected,
            sample_sex=sample_sex,
        ):
            bucket.append(MODE_X_LINKED)
        # Prefer the stronger, trio-confirmed de novo call; fall back to the dominant
        # pattern (which also covers inherited-dominant or no-trio cases).
        if _record_matches_de_novo(
            record,
            affected_samples=affected,
            child_parents=child_parents,
            sample_sex=sample_sex,
            parent_roles=parent_roles,
            assembly_name=context.assembly_name,
        ):
            bucket.append(MODE_DE_NOVO)
        elif _record_matches_de_novo_dominant(
            record,
            affected_samples=affected,
            unaffected_samples=unaffected,
            sample_sex=sample_sex,
            assembly_name=context.assembly_name,
        ):
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
    applies to genotypes. Any inherited SV used to count as dominant, so an SV from an
    unaffected parent ranked as supporting segregation it argues against. "maternal" names
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
    if "de" in inheritance and "novo" in inheritance:
        return [MODE_DE_NOVO]
    if any(token in inheritance for token in ("maternal", "paternal", "inherited")):
        return [MODE_DOMINANT]
    return []
