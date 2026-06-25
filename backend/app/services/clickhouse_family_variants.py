from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence
from uuid import UUID

from fastapi import HTTPException
from clickhouse_connect.driver.exceptions import ClickHouseError
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import clickhouse_dataset_key, execute_clickhouse
from ..core.config import settings
from ..schemas import (
    GenotypeOut,
    MonarchPhenotypeMatchOut,
    SmallVariantGroupOut,
    SmallVariantReviewOut,
    SmallVariantSampleSummaryOut,
    SmallVariantSummaryOut,
    SmallVariantTranscriptOut,
    VariantInternalCohortOut,
    SvSecondHitOut,
    VariantOut,
    VariantPage,
    VariantPriorityOut,
)
from .data_scope import chromosome_aliases, normalize_chromosome
from .variant_annotation_parser import _spliceai_delta
from .family_metadata_context import FamilyMetadataContext
from .family_variant_filters import (
    SmallVariantQueryFilters,
    StructuralVariantQueryFilters,
    parse_small_variant_sample_filter,
    parse_structural_sample_filter,
)
from .small_variant_review_pg import (
    get_small_variant_review_map,
    list_matching_small_variant_review_ids,
)
from .monarch_phenotype_score import score_genes_for_hpo
from .sv_gene_index_service import (
    get_sv_hit_genes,
    get_sv_second_hits,
    is_index_built,
    store_sv_gene_index,
    summarize_second_hit,
)
from .variant_ranking_cache import (
    canonical_filters,
    compute_ranking_hashes,
    find_superset_candidates,
    get_cached_ranking,
    store_ranking,
)
from .variant_prioritization import (
    MODE_COMPOUND_HET,
    MODE_DE_NOVO,
    MODE_DOMINANT,
    MODE_HOM_RECESSIVE,
    MODE_X_LINKED,
    score_structural_variant,
    score_variant,
)
from .structural_variant_review_pg import (
    get_structural_variant_review_map,
    list_matching_structural_variant_review_ids,
)

logger = logging.getLogger(__name__)

# ClickHouse error substrings that mean "this query was too broad/expensive to
# run", not "the server is broken". We turn these into an actionable 422 so the
# user can narrow their filters instead of seeing an opaque 500.
_CLICKHOUSE_QUERY_TOO_HEAVY_MARKERS = (
    "memory_limit_exceeded",
    "memory limit",
    "timeout_exceeded",
    "timeout exceeded",
    "max_execution_time",
    "too_many_rows",
    "too_many_bytes",
    "too_many_parts",
    "set_size_limit",
)

_INTERVAL_PATTERN = re.compile(
    r"^\s*(?P<chr>[^:\s]+)\s*:\s*(?P<start>\d[\d,]*)\s*-\s*(?P<end>\d[\d,]*)\s*$"
)
_GENE_QUERY_SPLIT = re.compile(r"[\s,;]+")
_HET_GT_VALUES = {"0/1", "1/0", "0|1", "1|0"}
_HOM_ALT_GT_VALUES = {"1/1", "1|1"}
_HOM_REF_GT_VALUES = {"0/0", "0|0"}
# Minimum parental depth to trust a homozygous-reference call when calling de novo;
# a low-coverage ref parent could be a missed heterozygote (false de novo).
_DE_NOVO_MIN_PARENT_DP = 8
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
# Safety cap on the non-native structural-variant page, where every meaningful
# filter forces a fetch-all-then-filter-in-Python path. Far above any realistic
# per-family SV count, so results are identical below the cap; above it the total
# is reported as estimated.
_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP = 50000
_SMALL_TRACK_RESULT_LIMIT = 10000
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
_KNOWN_ANNOTATION_KEYS = {
    "gene",
    "gene_id",
    "geneId",
    "geneID",
    "gene_symbol",
    "geneSymbol",
    "hgnc_symbol",
    "hgncSymbol",
    "impact",
    "effect",
    "majorConsequence",
    "consequence",
    "most_severe_consequence",
    "clinvar",
    "clinvarClinicalSignificance",
    "transcript_id",
    "transcriptId",
    "feature_type",
    "featureType",
    "transcript_biotype",
    "transcriptBiotype",
    "hgvsc",
    "hgvsp",
    "canonical",
    "mane_select",
    "maneSelect",
    "mane_plus_clinical",
    "manePlusClinical",
    "exon",
    "intron",
    "lof",
    "lof_filter",
    "lofFilter",
    "lof_flags",
    "lofFlags",
    "gnomad_af",
    "gnomadAf",
    "gnomad_hom_count",
    "gnomadHomCount",
    "cadd_raw",
    "caddRaw",
    "cadd_phred",
    "caddPhred",
    "revel",
    "sift",
    "siftPrediction",
    "polyphen",
    "polyphenPrediction",
    "spliceai_ds_ag",
    "spliceaiDsAg",
    "spliceai_ds_al",
    "spliceaiDsAl",
    "spliceai_ds_dg",
    "spliceaiDsDg",
    "spliceai_ds_dl",
    "spliceaiDsDl",
    "spliceai_max",
    "spliceaiMax",
    "population_frequencies",
    "populationFrequencies",
    "extra",
    "rsid",
}


@dataclass(slots=True)
class Region:
    chr: str
    start: int
    end: int


@dataclass(slots=True)
class PanelFilterConstraints:
    genes: tuple[str, ...] = ()
    regions: tuple[Region, ...] = ()


@dataclass(slots=True)
class SmallVariantCall:
    sample: str
    gt: str
    gq: float | None
    dp: int | None
    af: list[float]
    ad: list[int]
    ps: int | None


@dataclass(slots=True)
class SmallVariantRecord:
    variant_key: int | None
    variant_id: str
    chr: str
    start: int
    end: int
    ref: str
    alt: str
    source: str | None
    rsid: str | None
    filters: list[str]
    gene_symbols: list[str]
    annotations: list[dict[str, Any]]
    calls: list[SmallVariantCall]
    # Site-level VCF QUAL (column 6). Optional and defaulted so existing
    # construction sites are unaffected; the upload path populates it.
    qual: float | None = None


@dataclass(slots=True)
class SmallVariantCompoundHetPair:
    pair_key: str
    gene: str | None
    gene_id: str | None
    left: SmallVariantRecord
    right: SmallVariantRecord


@dataclass(slots=True)
class StructuralVariantCall:
    sample: str
    gt: str
    qual: float | None
    read_support: int | None
    filter: str | None
    # Phase set (PS) for read-based cis/trans against a phased SNV; None when unphased.
    phase_set: int | None = None


@dataclass(slots=True)
class StructuralVariantRecord:
    variant_key: int | None
    variant_id: str
    chr: str
    start: int
    end: int
    sv_type: str
    source: str | None
    remote_chr: str | None
    remote_start: int | None
    # Populated only by the write path (upload/import → storage writes the
    # details-table remoteEnd). The read path does not fetch it (never
    # surfaced by _structural_variant_out), so read-built records leave it None.
    remote_end: int | None
    sv_len: int | None
    filters: list[str]
    gene_symbols: list[str]
    annotations: list[dict[str, Any]]
    calls: list[StructuralVariantCall]


def _require_clickhouse_identifier(value: str) -> str:
    return clickhouse_dataset_key(value)


def _small_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SNV_INDEL/{suffix}`"


def _small_annotation_table_name(assembly_name: str) -> str:
    return _small_table_name(assembly_name, "variants/annotations")


def _small_annotation_index_table_name(assembly_name: str) -> str:
    return _small_table_name(assembly_name, "variants/annotation_index")


def _small_annotation_gene_index_table_name(assembly_name: str) -> str:
    return _small_table_name(assembly_name, "variants/gene_index")


def _small_summary_table_name(assembly_name: str, suffix: str) -> str:
    return _small_table_name(assembly_name, suffix)


def _structural_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SV/{suffix}`"


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


def _casefold(value: Any) -> str:
    return str(value or "").strip().casefold()


def _contains_casefold(value: Any, needle: str | None) -> bool:
    if not needle:
        return True
    return _casefold(needle) in _casefold(value)


def _normalized_status_term(value: Any) -> str:
    return " ".join(_casefold(value).replace("_", " ").split())


def _status_terms(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        terms: set[str] = set()
        for item in value:
            terms.update(_status_terms(item))
        return terms
    return {
        normalized
        for part in re.split(r"[,;&|]+", str(value or ""))
        if (normalized := _normalized_status_term(part))
    }


def _status_filter_terms(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            normalized
            for value in values
            if (normalized := _normalized_status_term(value))
        )
    )


def _flexible_status_match(value: Any, candidates: Sequence[str]) -> bool:
    candidate_terms = set(_status_filter_terms(candidates))
    return bool(candidate_terms and _status_terms(value).intersection(candidate_terms))


def _coerce_int(value: Any) -> int | None:
    if value in (None, "", "."):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "."):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _casefold(value) in {"1", "true", "yes", "y"}


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


def _annotation_value(annotation: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in annotation:
            return annotation[key]
    extra = annotation.get("extra")
    if isinstance(extra, dict):
        for key in keys:
            if key in extra:
                return extra[key]
    return None


def _annotation_terms(value: Any) -> set[str]:
    if value in (None, "", "."):
        return set()
    if isinstance(value, (list, tuple, set)):
        terms: set[str] = set()
        for item in value:
            terms.update(_annotation_terms(item))
        return terms
    return {
        _casefold(term)
        for term in re.split(r"[,|&;/]+", str(value))
        if term.strip() and term.strip() != "."
    }


def _annotation_text(annotation: dict[str, Any], *keys: str) -> str | None:
    value = _annotation_value(annotation, *keys)
    text_value = str(value or "").strip()
    return text_value or None


def _annotation_float(annotation: dict[str, Any], *keys: str) -> float | None:
    return _coerce_float(_annotation_value(annotation, *keys))


def _annotation_int(annotation: dict[str, Any], *keys: str) -> int | None:
    return _coerce_int(_annotation_value(annotation, *keys))


def _annotation_bool(annotation: dict[str, Any], *keys: str) -> bool:
    return _coerce_bool(_annotation_value(annotation, *keys))


def _annotation_rank(annotation: dict[str, Any]) -> tuple[int, int, int]:
    impact_order = {"HIGH": 4, "MODERATE": 3, "MEDIUM": 3, "LOW": 2, "MODIFIER": 1}
    return (
        1 if _annotation_bool(annotation, "mane_select", "maneSelect") else 0,
        1 if _annotation_bool(annotation, "canonical") else 0,
        impact_order.get(
            _casefold(_annotation_text(annotation, "impact") or "").upper(),
            0,
        ),
    )


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


def _parse_interval_regions(raw_value: str | None) -> list[Region]:
    regions: list[Region] = []
    for entry in re.split(r"[\n;]+", str(raw_value or "")):
        match = _INTERVAL_PATTERN.match(entry.strip())
        if not match:
            continue
        regions.append(
            Region(
                chr=normalize_chromosome(match.group("chr")),
                start=int(match.group("start").replace(",", "")),
                end=int(match.group("end").replace(",", "")),
            )
        )
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


def _annotation_population_frequencies(annotation: dict[str, Any]) -> dict[str, float]:
    payload = _annotation_value(annotation, "population_frequencies", "populationFrequencies")
    result: dict[str, float] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            parsed = _coerce_float(value)
            if parsed is not None:
                result[str(key)] = parsed
    for key in (
        ("gnomad_exomes_af", "gnomadExomesAf"),
        ("gnomad_genomes_af", "gnomadGenomesAf"),
        ("gnomad_popmax_af", "gnomadPopmaxAf"),
        ("topmed_af", "topmedAf"),
    ):
        parsed = _annotation_float(annotation, *key)
        if parsed is not None:
            result[key[0]] = parsed
    return result


def _annotation_extra(annotation: dict[str, Any]) -> dict[str, Any]:
    payload = _annotation_value(annotation, "extra")
    result: dict[str, Any] = payload if isinstance(payload, dict) else {}
    for key, value in annotation.items():
        if key in _KNOWN_ANNOTATION_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result.setdefault(key, value)
    return result


def _annotation_gene(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(
        annotation,
        "gene",
        "gene_symbol",
        "geneSymbol",
        "hgnc_symbol",
        "hgncSymbol",
    )


def _annotation_gene_id(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(annotation, "gene_id", "geneId", "geneID")


def _annotation_effect(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(
        annotation,
        "effect",
        "majorConsequence",
        "consequence",
        "most_severe_consequence",
    )


def _annotation_clinvar(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(annotation, "clinvar", "clinvarClinicalSignificance")


def _annotation_sift(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(annotation, "sift", "siftPrediction")


def _annotation_polyphen(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(annotation, "polyphen", "polyphenPrediction")


def _annotation_spliceai_max(annotation: dict[str, Any]) -> float | None:
    explicit = _spliceai_delta(_annotation_float(annotation, "spliceai_max", "spliceaiMax"))
    if explicit is not None:
        return explicit
    values = [
        _spliceai_delta(_annotation_float(annotation, "spliceai_ds_ag", "spliceaiDsAg")),
        _spliceai_delta(_annotation_float(annotation, "spliceai_ds_al", "spliceaiDsAl")),
        _spliceai_delta(_annotation_float(annotation, "spliceai_ds_dg", "spliceaiDsDg")),
        _spliceai_delta(_annotation_float(annotation, "spliceai_ds_dl", "spliceaiDsDl")),
    ]
    present = [value for value in values if value is not None]
    return max(present) if present else None


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


async def _fetch_structural_cytoband_map(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    records: Sequence[StructuralVariantRecord],
) -> dict[str, str]:
    if not assembly_id or not records:
        return {}
    chromosomes = list(
        dict.fromkeys(
            alias
            for record in records
            for alias in (normalize_chromosome(record.chr), f"chr{normalize_chromosome(record.chr)}")
        )
    )
    result = await session.execute(
        text(
            """
            SELECT chr, bands
            FROM chromosomes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND chr IN :chromosomes
            """
        ).bindparams(bindparam("chromosomes", expanding=True)),
        {
            "assembly_id": assembly_id,
            "chromosomes": chromosomes or [""],
        },
    )
    band_map: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        bands = row.get("bands") or []
        if isinstance(bands, str):
            try:
                bands = json.loads(bands)
            except json.JSONDecodeError:
                bands = []
        if not isinstance(bands, list):
            continue
        normalized = normalize_chromosome(str(row["chr"]))
        band_map[normalized] = [band for band in bands if isinstance(band, dict)]

    cytobands: dict[str, str] = {}
    for record in records:
        bands = band_map.get(normalize_chromosome(record.chr))
        if not bands:
            continue
        start_band = _band_name_for_position(bands, record.start)
        end_band = _band_name_for_position(bands, record.end)
        label = _format_cytoband_label(record.chr, start_band, end_band)
        if label:
            cytobands[record.variant_id] = label
    return cytobands


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


def _nullable_lte(value: float | int | None, maximum: float | int | None) -> bool:
    if maximum is None:
        return True
    if value is None:
        return True
    return value <= maximum


def _annotation_matches_normal(annotation: dict[str, Any], filters: SmallVariantQueryFilters) -> bool:
    if filters.transcript and not _contains_casefold(
        _annotation_text(annotation, "transcript_id", "transcriptId"),
        filters.transcript,
    ):
        return False
    if filters.hgvsc and not _contains_casefold(_annotation_text(annotation, "hgvsc"), filters.hgvsc):
        return False
    if filters.hgvsp and not _contains_casefold(_annotation_text(annotation, "hgvsp"), filters.hgvsp):
        return False
    if filters.canonical_only and not _annotation_bool(annotation, "canonical"):
        return False
    if filters.mane_only and not (
        _annotation_bool(annotation, "mane_select", "maneSelect")
        or _annotation_bool(annotation, "mane_plus_clinical", "manePlusClinical")
    ):
        return False
    if filters.lof_only and _casefold(_annotation_text(annotation, "lof") or "") in {"", ".", "na", "n/a"}:
        return False
    # ClinVar Pathogenic/Likely_pathogenic can "rescue" a variant past the gnomAD
    # frequency thresholds when the caller opts in — a recurrent pathogenic allele
    # may sit above the rarity cut-off yet must not be filtered out.
    clinvar_rescue = filters.clinvar_overrides_frequency and _flexible_status_match(
        _annotation_clinvar(annotation), ("Pathogenic", "Likely_pathogenic")
    )
    if not clinvar_rescue:
        if not _nullable_lte(_annotation_float(annotation, "gnomad_af", "gnomadAf"), filters.max_gnomad_af):
            return False
        population_frequencies = _annotation_population_frequencies(annotation)
        if not _nullable_lte(population_frequencies.get("gnomad_exomes_af"), filters.max_gnomad_exomes_af):
            return False
        if not _nullable_lte(population_frequencies.get("gnomad_genomes_af"), filters.max_gnomad_genomes_af):
            return False
        if not _nullable_lte(population_frequencies.get("gnomad_popmax_af"), filters.max_gnomad_popmax_af):
            return False
        if not _nullable_lte(population_frequencies.get("topmed_af"), filters.max_topmed_af):
            return False
        if not _nullable_lte(_annotation_int(annotation, "gnomad_ac"), filters.max_gnomad_ac):
            return False
        if not _nullable_lte(
            _annotation_int(annotation, "gnomad_hom_count", "gnomadHomCount"), filters.max_gnomad_hom_count
        ):
            return False
        if not _nullable_lte(_annotation_int(annotation, "gnomad_hemi_count"), filters.max_gnomad_hemi_count):
            return False

    impact_terms = {_casefold(value) for value in filters.impact if str(value).strip()}
    effect_terms = {_casefold(value) for value in filters.effect if str(value).strip()}
    any_impact_effect = bool(impact_terms or effect_terms or filters.min_spliceai is not None)
    if any_impact_effect:
        impact_match = bool(impact_terms) and _casefold(_annotation_text(annotation, "impact")) in impact_terms
        effect_match = bool(effect_terms) and bool(effect_terms.intersection(_annotation_terms(_annotation_effect(annotation))))
        splice_match = (
            filters.min_spliceai is not None
            and (_annotation_spliceai_max(annotation) or -1.0) >= filters.min_spliceai
        )
        if not (impact_match or effect_match or splice_match):
            return False

    clinvar_terms = [value for value in filters.clinvar if str(value).strip()]
    if clinvar_terms and not _flexible_status_match(_annotation_clinvar(annotation), clinvar_terms):
        return False

    in_silico_requested = any(
        value is not None and value != ""
        for value in (
            filters.min_cadd,
            filters.min_revel,
            filters.min_spliceai,
            filters.sift,
            filters.polyphen,
        )
    )
    if in_silico_requested:
        cadd_match = filters.min_cadd is not None and (
            (_annotation_float(annotation, "cadd_phred", "caddPhred") or -1.0) >= filters.min_cadd
        )
        revel_match = filters.min_revel is not None and (
            (_annotation_float(annotation, "revel") or -1.0) >= filters.min_revel
        )
        splice_match = filters.min_spliceai is not None and (
            (_annotation_spliceai_max(annotation) or -1.0) >= filters.min_spliceai
        )
        sift_match = bool(filters.sift) and _flexible_status_match(_annotation_sift(annotation), [filters.sift])
        polyphen_match = bool(filters.polyphen) and _flexible_status_match(
            _annotation_polyphen(annotation),
            [filters.polyphen],
        )
        if not (cadd_match or revel_match or splice_match or sift_match or polyphen_match):
            return False
    return True


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
        if sample_filter.genotype_values and call.gt not in set(sample_filter.genotype_values):
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
        if sample_filter.genotype_values and call.gt not in set(sample_filter.genotype_values):
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


def _call_is_het(call: SmallVariantCall | None) -> bool:
    return call is not None and call.gt in _HET_GT_VALUES


def _call_is_hom_alt(call: SmallVariantCall | None) -> bool:
    return call is not None and call.gt in _HOM_ALT_GT_VALUES


def _call_has_alt(call: SmallVariantCall | None) -> bool:
    return call is not None and _has_alt_allele(call.gt)


def _call_is_confident_hom_ref(call: SmallVariantCall | None) -> bool:
    """A genotyped homozygous-reference call with enough depth to trust it.

    A missing call (``./.``) is not confident reference, so it does not qualify — we
    cannot rule out an uncalled inherited allele.
    """
    if call is None or call.gt not in _HOM_REF_GT_VALUES:
        return False
    return call.dp is None or call.dp >= _DE_NOVO_MIN_PARENT_DP


def _child_parent_map(context: FamilyMetadataContext) -> dict[str, set[str]]:
    """Map each child sample name to its parent sample names from the pedigree."""
    parents: dict[str, set[str]] = {}
    for relationship in context.relationship_rows or []:
        if str(relationship.get("relationship_type")) != "parent_child":
            continue
        sample_a = relationship.get("sample_id_a")
        sample_b = relationship.get("sample_id_b")
        role_a = str(relationship.get("role_a") or "").lower()
        role_b = str(relationship.get("role_b") or "").lower()
        if role_b == "child" and role_a in {"mother", "father"} and sample_a and sample_b:
            parents.setdefault(str(sample_b), set()).add(str(sample_a))
        elif role_a == "child" and role_b in {"mother", "father"} and sample_a and sample_b:
            parents.setdefault(str(sample_a), set()).add(str(sample_b))
    return parents


def _record_matches_de_novo(
    record: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    child_parents: dict[str, set[str]],
) -> bool:
    """True de novo: heterozygous in an affected child, confidently absent in both parents.

    Restricted to heterozygous child calls — the classic de novo scenario. A
    homozygous-alt child with reference parents is biologically implausible (it would
    require two independent events) and almost always a repetitive-region artifact, so
    it is left to the homozygous-recessive pattern instead. Requires a full trio (both
    parents genotyped and sufficiently covered); otherwise inheritance cannot be
    excluded and this returns False (the variant may still match the dominant pattern).
    """
    call_map = _small_call_map(record)
    for child in affected_samples:
        parents = child_parents.get(child)
        if not parents or len(parents) < 2:
            continue
        if not _call_is_het(call_map.get(child)):
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
) -> bool:
    if not affected_samples:
        return False
    call_map = _small_call_map(record)
    if not all(_call_is_het(call_map.get(sample)) for sample in affected_samples):
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


def _records_form_compound_het_pair(
    left: SmallVariantRecord,
    right: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
) -> bool:
    if left.variant_id == right.variant_id:
        return False
    if not affected_samples:
        return False

    left_calls = _small_call_map(left)
    right_calls = _small_call_map(right)

    if not all(
        _call_is_het(left_calls.get(sample)) and _call_is_het(right_calls.get(sample))
        for sample in affected_samples
    ):
        return False

    return not any(
        _call_has_alt(left_calls.get(sample)) and _call_has_alt(right_calls.get(sample))
        for sample in unaffected_samples
    )


def _compound_het_pairs(
    records: Sequence[SmallVariantRecord],
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
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
                if not _records_form_compound_het_pair(
                    left,
                    right,
                    affected_samples=affected_samples,
                    unaffected_samples=unaffected_samples,
                ):
                    continue
                ordered_left, ordered_right = sorted((left, right), key=_small_record_sort_key)
                gene, gene_id = _resolve_compound_het_pair_gene_labels(ordered_left, ordered_right)
                pair_map[pair_ids] = SmallVariantCompoundHetPair(
                    pair_key="::".join(pair_ids),
                    gene=gene,
                    gene_id=gene_id,
                    left=ordered_left,
                    right=ordered_right,
                )

    return sorted(
        pair_map.values(),
        key=lambda pair: (
            *_small_record_sort_key(pair.left),
            *_small_record_sort_key(pair.right),
        ),
    )


def _compound_het_partner_map(
    records: Sequence[SmallVariantRecord],
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
) -> dict[str, set[str]]:
    partner_map: dict[str, set[str]] = {}
    for pair in _compound_het_pairs(
        records,
        affected_samples=affected_samples,
        unaffected_samples=unaffected_samples,
    ):
        partner_map.setdefault(pair.left.variant_id, set()).add(pair.right.variant_id)
        partner_map.setdefault(pair.right.variant_id, set()).add(pair.left.variant_id)
    return partner_map


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
    alleles = gt.replace("|", "/").split("/")
    return any(allele not in {"", ".", "0"} for allele in alleles)


def _filter_expanded_carrier_screening(
    records: Sequence[SmallVariantRecord],
    sample_rows: Sequence[dict[str, Any]],
    relationship_rows: Sequence[dict[str, Any]] | None = None,
) -> list[SmallVariantRecord]:
    partners = _carrier_partner_names(sample_rows, relationship_rows)
    if partners is None:
        return []
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
        for key in keys:
            carrier_sets.setdefault(key, set()).update(carriers)
            carrier_variants.setdefault(key, []).append(record)
    qualifying_ids: set[str] = set()
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


async def _fetch_gene_regions(
    session: AsyncSession,
    *,
    gene_query: str,
    assembly_id: str | None,
) -> list[Region]:
    return await _fetch_gene_regions_for_terms(
        session,
        terms=_split_gene_terms(gene_query),
        assembly_id=assembly_id,
    )


async def _fetch_gene_regions_for_terms(
    session: AsyncSession,
    *,
    terms: Sequence[str],
    assembly_id: str | None,
) -> list[Region]:
    terms = [str(term).strip() for term in terms if str(term).strip()]
    if not terms:
        return []
    clauses = ["(upper(hgnc_symbol) IN :terms OR upper(gene_id) IN :terms)"]
    params: dict[str, Any] = {"terms": [term.upper() for term in terms]}
    bind_params = [bindparam("terms", expanding=True)]
    if assembly_id:
        clauses.append("assembly_id = CAST(:assembly_id AS uuid)")
        params["assembly_id"] = assembly_id
    result = await session.execute(
        text(
            f"""
            SELECT chr, start, "end"
            FROM genes
            WHERE {' AND '.join(clauses)}
            """
        ).bindparams(*bind_params),
        params,
    )
    return [
        Region(chr=row["chr"], start=int(row["start"]), end=int(row["end"]))
        for row in result.mappings().all()
    ]


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


async def _fetch_panel_constraints(
    session: AsyncSession,
    panel_id: str,
    *,
    assembly_id: str | None = None,
) -> PanelFilterConstraints:
    try:
        UUID(panel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid panel id") from exc
    gene_result = await session.execute(
        text(
            """
            SELECT gene_symbol
            FROM gene_panel_genes
            WHERE panel_id = CAST(:panel_id AS uuid)
            ORDER BY gene_symbol
            """
        ),
        {"panel_id": panel_id},
    )
    genes: list[str] = []
    for row in gene_result.mappings().all():
        _append_unique(genes, row.get("gene_symbol"))

    region_result = await session.execute(
        text(
            """
            SELECT gene, chr, start, "end"
            FROM gene_panel_regions
            WHERE panel_id = CAST(:panel_id AS uuid)
            ORDER BY gene, chr, start, "end"
            """
        ),
        {"panel_id": panel_id},
    )
    region_rows = [dict(row) for row in region_result.mappings().all()]
    regions = [
        Region(chr=row["chr"], start=int(row["start"]), end=int(row["end"]))
        for row in region_rows
    ]
    for row in region_rows:
        _append_unique(genes, row.get("gene"))
    if assembly_id and genes:
        regions.extend(
            await _fetch_gene_regions_for_terms(
                session,
                terms=genes,
                assembly_id=assembly_id,
            )
        )
    if genes or regions:
        return PanelFilterConstraints(genes=tuple(genes), regions=_dedupe_regions(regions))

    exists = await session.execute(
        text("SELECT 1 FROM gene_panels WHERE id = CAST(:panel_id AS uuid)"),
        {"panel_id": panel_id},
    )
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    return PanelFilterConstraints()


async def _fetch_panel_regions(
    session: AsyncSession,
    panel_id: str,
    *,
    assembly_id: str | None = None,
) -> list[Region]:
    constraints = await _fetch_panel_constraints(session, panel_id, assembly_id=assembly_id)
    return list(constraints.regions)


async def _fetch_gene_constraint_metric_map(
    session: AsyncSession,
    variants: Sequence[VariantOut],
) -> dict[str, dict[str, float | None]]:
    if not variants:
        return {}
    gene_symbols = sorted({variant.gene for variant in variants if variant.gene})
    gene_ids = sorted({variant.gene_id for variant in variants if variant.gene_id})
    clauses: list[str] = []
    params: dict[str, Any] = {}
    bind_params = []
    if gene_symbols:
        clauses.append("upper(hgnc_symbol) IN :gene_symbols")
        params["gene_symbols"] = [symbol.upper() for symbol in gene_symbols]
        bind_params.append(bindparam("gene_symbols", expanding=True))
    if gene_ids:
        clauses.append("gene_id IN :gene_ids")
        params["gene_ids"] = gene_ids
        bind_params.append(bindparam("gene_ids", expanding=True))
    if not clauses:
        return {}
    result = await session.execute(
        text(
            f"""
            SELECT hgnc_symbol, gene_id, extra, source_status, updated_at
            FROM gene_info
            WHERE {' OR '.join(clauses)}
            ORDER BY updated_at DESC
            """
        ).bindparams(*bind_params),
        params,
    )
    by_symbol: dict[str, dict[str, float | None]] = {}
    by_gene_id: dict[str, dict[str, float | None]] = {}
    for row in result.mappings().all():
        doc = dict(row)
        metrics = _extract_gene_constraint_metrics(doc)
        symbol = str(doc.get("hgnc_symbol") or "").strip()
        gene_id = str(doc.get("gene_id") or "").strip()
        if symbol and symbol not in by_symbol:
            by_symbol[symbol] = metrics
        if gene_id and gene_id not in by_gene_id:
            by_gene_id[gene_id] = metrics
    result_map: dict[str, dict[str, float | None]] = {}
    for variant in variants:
        if variant.gene_id and variant.gene_id in by_gene_id:
            result_map[str(variant.id)] = by_gene_id[variant.gene_id]
        elif variant.gene and variant.gene in by_symbol:
            result_map[str(variant.id)] = by_symbol[variant.gene]
    return result_map


def _normalize_alpha_missense_class(value: str | None) -> str | None:
    """AlphaMissense no-call (``-`` / empty) becomes None for clean display/scoring."""
    text_value = str(value or "").strip()
    if not text_value or text_value == "-":
        return None
    return text_value


def _small_variant_out(record: SmallVariantRecord) -> VariantOut:
    annotation = _select_primary_annotation(record.annotations)
    population_frequencies = _annotation_population_frequencies(annotation)
    transcripts = _small_transcript_annotations(record.annotations, annotation)
    return VariantOut(
        _id=record.variant_id,
        chr=record.chr,
        start=record.start,
        end=record.end,
        length=record.end - record.start,
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


_INTERNAL_GT_REF_MISSING = ("", ".", "./.", ".|.", "0/0", "0|0")
_INTERNAL_GT_HOM = ("1/1", "1|1")


async def _fetch_internal_cohort_map(
    context: FamilyMetadataContext,
    variant_ids: Sequence[str],
) -> dict[str, VariantInternalCohortOut]:
    """Per-variant occurrence across the family's accessible-project cohort.

    Aggregated directly from ``entries`` (sign = 1) so each carrier sample is
    counted once regardless of how the data was loaded.
    """

    normalized_ids = tuple({str(value).strip() for value in variant_ids if str(value).strip()})
    if not normalized_ids or not context.assembly_name or not context.project_ids:
        return {}
    entries_table = _small_table_name(context.assembly_name, "entries")
    params: dict[str, Any] = {
        "variant_ids": normalized_ids,
        "project_ids": tuple(context.project_ids),
        "gt_ref_missing": _INTERNAL_GT_REF_MISSING,
        "gt_hom": _INTERNAL_GT_HOM,
    }
    rows = await _execute_clickhouse(
        f"""
        SELECT
            variantId,
            uniqExactIf(sample_id, gt IN %(gt_hom)s) AS hom,
            uniqExactIf(sample_id, gt NOT IN %(gt_hom)s) AS het,
            uniqExact(sample_id) AS samples,
            uniqExact(family_guid) AS families
        FROM {entries_table}
        ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
        WHERE sign = 1
          AND project_guid IN %(project_ids)s
          AND variantId IN %(variant_ids)s
          AND gt NOT IN %(gt_ref_missing)s
        GROUP BY variantId
        """,
        params,
    )
    result: dict[str, VariantInternalCohortOut] = {}
    for variant_id, hom, het, samples, families in rows:
        result[str(variant_id)] = VariantInternalCohortOut(
            samples=int(samples or 0),
            het=int(het or 0),
            hom=int(hom or 0),
            families=int(families or 0),
        )
    return result


async def fetch_recurrent_small_variant_ids(
    assembly_name: str,
    *,
    min_carrier_samples: int,
    project_ids: Sequence[str] | None = None,
    limit: int = 100_000,
) -> list[tuple[str, int]]:
    """Variant ids carried (non-ref) by >= ``min_carrier_samples`` distinct
    samples across the assembly cohort -- recurrent-artifact candidates.

    Returns ``(variant_id, carrier_count)`` pairs ordered by recurrence. Counts
    each carrier sample once via ``sign = 1`` over the ``entries`` table.
    """
    if not assembly_name or min_carrier_samples < 1:
        return []
    entries_table = _small_table_name(assembly_name, "entries")
    clauses = ["sign = 1", "gt NOT IN %(gt_ref_missing)s"]
    params: dict[str, Any] = {
        "gt_ref_missing": _INTERNAL_GT_REF_MISSING,
        "min_samples": int(min_carrier_samples),
        "limit": int(limit),
    }
    if project_ids:
        clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(project_ids)
    rows = await _execute_clickhouse(
        f"""
        SELECT variantId, uniqExact(sample_id) AS carriers
        FROM {entries_table}
        ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
        WHERE {' AND '.join(clauses)}
        GROUP BY variantId
        HAVING carriers >= %(min_samples)s
        ORDER BY carriers DESC
        LIMIT %(limit)s
        """,
        params,
    )
    return [(str(variant_id), int(carriers or 0)) for variant_id, carriers in rows]


async def _scan_family_sv_gene_map(
    context: FamilyMetadataContext,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Scan the family's structural variants and group them by overlapped gene."""
    if not context.assembly_name:
        return {}, 0
    entries_table = _structural_table_name(context.assembly_name, "entries")
    rows = await execute_clickhouse(
        f"""
        SELECT e.variantId, e.svType, e.chrom, e.start, e.end, e.gene_symbols,
               e.calls.sampleId, e.calls.gt, e.calls.ps
        FROM {entries_table} AS e
        WHERE e.family_guid = %(family_guid)s AND e.sign = 1 AND length(e.gene_symbols) > 0
        """,
        {"family_guid": context.family_uuid},
    )
    gene_map: dict[str, list[dict[str, Any]]] = {}
    sv_ids: set[str] = set()
    for variant_id, sv_type, chrom, start, end, genes, sample_ids, gts, phase_sets in rows:
        sv_ids.add(str(variant_id))
        gt_map = {
            str(sample): str(gt)
            for sample, gt in zip(sample_ids or [], gts or [])
            if sample not in (None, "")
        }
        ps_map = {
            str(sample): int(ps)
            for sample, ps in zip(sample_ids or [], phase_sets or [])
            if sample not in (None, "") and ps is not None
        }
        sv = {
            "sv_id": str(variant_id),
            "sv_type": str(sv_type or ""),
            "chr": str(chrom or ""),
            "start": int(start) if start is not None else None,
            "end": int(end) if end is not None else None,
            "gt": gt_map,
            "ps": ps_map,
        }
        for gene in genes or []:
            symbol = str(gene).strip()
            if symbol:
                gene_map.setdefault(symbol.upper(), []).append(sv)
    return gene_map, len(sv_ids)


async def _ensure_family_sv_gene_index(
    session: AsyncSession, context: FamilyMetadataContext
) -> None:
    """Build the family's SV→gene index once (lazily); cleared on SV re-import."""
    if await is_index_built(session, context.family_uuid):
        return
    # Ensure the SV entries table is current (notably the calls.ps phase-set column added for
    # read-based phasing) before the scan selects it. Local import avoids a circular dependency.
    if context.assembly_name:
        from .clickhouse_variant_storage import ensure_clickhouse_variant_tables

        await ensure_clickhouse_variant_tables(context.assembly_name)
    gene_map, sv_total = await _scan_family_sv_gene_map(context)
    await store_sv_gene_index(
        session, family_uuid=context.family_uuid, gene_map=gene_map, sv_total=sv_total
    )


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


async def _attach_sv_second_hits(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variants: Sequence[VariantOut],
) -> None:
    """Flag small variants whose gene is also hit by a structural variant (best-effort)."""
    try:
        await _ensure_family_sv_gene_index(session, context)
        genes: set[str] = set()
        for variant in variants:
            genes.update(_variant_gene_keys(variant))
        second_hits = await get_sv_second_hits(
            session, family_uuid=context.family_uuid, gene_symbols=genes
        )
        if not second_hits:
            return
        affected, unaffected = _family_affected_unaffected_sample_names(context)
        for variant in variants:
            hit = next(
                (second_hits[gene] for gene in _variant_gene_keys(variant) if gene in second_hits),
                None,
            )
            if hit:
                snv_gt = {gt.sample: gt.gt for gt in (variant.genotypes or []) if gt.sample}
                snv_ps = {
                    gt.sample: int(gt.ps)
                    for gt in (variant.genotypes or [])
                    if gt.sample and gt.ps is not None
                }
                variant.sv_second_hit = SvSecondHitOut.model_validate(
                    summarize_second_hit(
                        hit["svs"],
                        list(affected),
                        unaffected_samples=list(unaffected),
                        snv_gt_by_sample=snv_gt,
                        snv_ps_by_sample=snv_ps,
                    )
                )
    except Exception:  # noqa: BLE001 - the second-hit overlay must never break the page
        logger.warning("SV second-hit overlay failed for family %s", context.family_id, exc_info=True)


async def _hydrate_small_variant_outs(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variants: Sequence[VariantOut],
) -> None:
    if not variants:
        return
    await _attach_sv_second_hits(session, context=context, variants=variants)

    review_map = await get_small_variant_review_map(
        session,
        family_uuid=context.family_uuid,
        variant_ids=[str(variant.id) for variant in variants],
    )
    for variant in variants:
        variant.review = review_map.get(str(variant.id))

    try:
        internal_map = await _fetch_internal_cohort_map(
            context, [str(variant.id) for variant in variants]
        )
    except Exception:  # pragma: no cover - internal frequency is best-effort
        internal_map = {}
    for variant in variants:
        variant.internal_cohort = internal_map.get(str(variant.id))

    metric_map = await _fetch_gene_constraint_metric_map(session, variants)
    for variant in variants:
        metrics = metric_map.get(str(variant.id))
        if not metrics:
            continue
        if variant.gene_pli is None:
            variant.gene_pli = metrics.get("gene_pli")
        if variant.gene_missense_z is None:
            variant.gene_missense_z = metrics.get("gene_missense_z")


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
            )
            for call in calls
        ],
    )


async def _execute_clickhouse(query: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    try:
        return await execute_clickhouse(query, params)
    except ClickHouseError as exc:
        message = str(exc)
        if "UNKNOWN_TABLE" in message or "doesn't exist" in message:
            return []
        logger.error(
            "ClickHouse variant query failed: %s | params=%s | query=%s",
            message,
            sorted(params.keys()),
            " ".join(query.split()),
        )
        lowered = message.lower()
        if any(marker in lowered for marker in _CLICKHOUSE_QUERY_TOO_HEAVY_MARKERS):
            raise HTTPException(
                status_code=422,
                detail=(
                    "This filter combination matches too many variants to evaluate. "
                    "Narrow the search with a region, gene, or stricter frequency/impact "
                    "thresholds and try again."
                ),
            ) from exc
        raise


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
    gt_values: Sequence[str],
    prefix: str,
    params: dict[str, Any],
) -> str:
    sample_param = f"{prefix}_samples"
    gt_param = f"{prefix}_gts"
    sample_ids = _clickhouse_ids_for_sample(context, sample_name)
    params[sample_param] = sample_ids or (sample_name,)
    params[gt_param] = tuple(gt_values)
    return (
        "arrayExists((sample_id, gt) -> "
        f"sample_id IN %({sample_param})s AND gt IN %({gt_param})s, "
        "e.calls.sampleId, e.calls.gt)"
    )


def _small_all_samples_have_gts_condition(
    context: FamilyMetadataContext,
    *,
    sample_names: Sequence[str],
    gt_values: Sequence[str],
    prefix: str,
    params: dict[str, Any],
) -> list[str]:
    return [
        _small_sample_gt_exists_condition(
            context,
            sample_name=sample_name,
            gt_values=gt_values,
            prefix=f"{prefix}_{index}",
            params=params,
        )
        for index, sample_name in enumerate(sample_names)
    ]


def _small_no_samples_have_gts_condition(
    context: FamilyMetadataContext,
    *,
    sample_names: Sequence[str],
    gt_values: Sequence[str],
    prefix: str,
    params: dict[str, Any],
) -> list[str]:
    return [
        "NOT "
        + _small_sample_gt_exists_condition(
            context,
            sample_name=sample_name,
            gt_values=gt_values,
            prefix=f"{prefix}_{index}",
            params=params,
        )
        for index, sample_name in enumerate(sample_names)
    ]


def _small_native_inheritance_clauses(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
) -> tuple[list[str], dict[str, Any]]:
    inheritance = filters.inheritance
    if not inheritance and not filters.expanded_carrier_screening:
        return [], {}

    params: dict[str, Any] = {}
    clauses: list[str] = []
    alt_gt_values = tuple(sorted(_HET_GT_VALUES.union(_HOM_ALT_GT_VALUES)))
    het_gt_values = tuple(sorted(_HET_GT_VALUES))
    hom_alt_gt_values = tuple(sorted(_HOM_ALT_GT_VALUES))

    if filters.expanded_carrier_screening:
        partners = _carrier_partner_names(context.sample_rows, context.relationship_rows)
        if partners is None:
            return ["0"], params
        partner_alt_clauses = _small_all_samples_have_gts_condition(
            context,
            sample_names=partners,
            gt_values=alt_gt_values,
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
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                gt_values=het_gt_values,
                prefix="inheritance_affected_het",
                params=params,
            )
        )
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=unaffected_samples,
                gt_values=alt_gt_values,
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
                gt_values=hom_alt_gt_values,
                prefix="inheritance_affected_hom_alt",
                params=params,
            )
        )
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=unaffected_samples,
                gt_values=hom_alt_gt_values,
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
                gt_values=alt_gt_values,
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
                gt_values=alt_gt_values,
                prefix="inheritance_unaffected_male_alt",
                params=params,
            )
        )
        clauses.extend(
            _small_no_samples_have_gts_condition(
                context,
                sample_names=other_unaffected,
                gt_values=hom_alt_gt_values,
                prefix="inheritance_unaffected_hom_alt",
                params=params,
            )
        )
    elif inheritance == _COMPOUND_HET_INHERITANCE:
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                gt_values=het_gt_values,
                prefix="inheritance_affected_het",
                params=params,
            )
        )
    elif inheritance == _RECESSIVE_INHERITANCE:
        clauses.extend(
            _small_all_samples_have_gts_condition(
                context,
                sample_names=affected_samples,
                gt_values=alt_gt_values,
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


def _array_text_contains_any(
    expr: str,
    *,
    prefix: str,
    values: Sequence[str],
    params: dict[str, Any],
) -> str | None:
    clauses: list[str] = []
    for index, value in enumerate(values):
        text_value = str(value or "").strip()
        if not text_value:
            continue
        param = f"{prefix}_{index}"
        params[param] = text_value
        clauses.append(
            f"arrayExists(value -> positionCaseInsensitive(value, %({param})s) > 0, {expr})"
        )
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
        conditions.append(f"ifNull(a.{column}, 0) <= %({param})s")

    max_int_filters = [
        ("detail_max_gnomad_ac", filters.max_gnomad_ac, "gnomad_ac"),
        ("detail_max_gnomad_hom_count", filters.max_gnomad_hom_count, "gnomad_hom_count"),
        ("detail_max_gnomad_hemi_count", filters.max_gnomad_hemi_count, "gnomad_hemi_count"),
    ]
    for param, maximum, column in max_int_filters:
        if maximum is None:
            continue
        params[param] = maximum
        conditions.append(f"ifNull(a.{column}, 0) <= %({param})s")

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
        if parsed.genotype_values:
            gt_param = f"sample_filter_{index}_gts"
            conditions.append(f"gt IN %({gt_param})s")
            params[gt_param] = tuple(parsed.genotype_values)
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


def _structural_variant_where_clauses(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
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
    return where_clauses, params


def _page_offset(page: int, page_size: int) -> int:
    return max(page - 1, 0) * page_size


async def _count_small_variant_rows_bounded(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    count_limit: int = _SMALL_COUNT_LIMIT,
    panel_constraints: PanelFilterConstraints | None = None,
    include_variant_ids: Sequence[str] | None = None,
    exclude_variant_ids: Sequence[str] = (),
    include_regions: Sequence[Region] = (),
    exclude_regions: Sequence[Region] = (),
    exclude_gene_regions: Sequence[Region] = (),
    exclude_gene_terms: Sequence[str] = (),
) -> tuple[int, bool]:
    if not context.assembly_name:
        return 0, False
    entries_table = _small_table_name(context.assembly_name, "entries")
    where_clauses, params, _use_detail_join = _small_query_filter_parts(
        context,
        filters,
        panel_constraints=panel_constraints,
        include_variant_ids=include_variant_ids,
        exclude_variant_ids=exclude_variant_ids,
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=exclude_gene_terms,
    )
    params["count_limit"] = max(int(count_limit), 1)
    rows = await _execute_clickhouse(
        f"""
        SELECT count()
        FROM (
            SELECT e.key
            FROM {entries_table} AS e
            WHERE {' AND '.join(where_clauses)}
            GROUP BY e.key
            LIMIT %(count_limit)s
        )
        """,
        params,
    )
    count = int(rows[0][0] or 0) if rows else 0
    return count, count >= params["count_limit"]


async def _fetch_small_variant_detail_map(
    assembly_name: str,
    variants: Sequence[tuple[int, str, int]],
) -> dict[tuple[int, str, int], dict[str, Any]]:
    pairs = [
        (int(key), str(annotation_version or "current"), int(annotation_set_hash))
        for key, annotation_version, annotation_set_hash in variants
        if key is not None and annotation_set_hash is not None
    ]
    keys = tuple(dict.fromkeys(key for key, _annotation_version, _annotation_set_hash in pairs))
    if not keys:
        return {}
    annotation_versions = tuple(
        dict.fromkeys(annotation_version for _key, annotation_version, _annotation_set_hash in pairs)
    )
    annotation_set_hashes = tuple(
        dict.fromkeys(annotation_set_hash for _key, _annotation_version, annotation_set_hash in pairs)
    )
    details_table = _small_table_name(assembly_name, "variants/details")
    rows = await _execute_clickhouse(
        f"""
        SELECT
            key,
            annotation_version,
            annotationSetHash,
            any(rsid) AS rsid,
            any(annotationsJson) AS annotations_json
        FROM {details_table}
        WHERE key IN %(variant_keys)s
          AND annotation_version IN %(annotation_versions)s
          AND annotationSetHash IN %(annotation_set_hashes)s
        GROUP BY key, annotation_version, annotationSetHash
        """,
        {
            "variant_keys": keys,
            "annotation_versions": annotation_versions,
            "annotation_set_hashes": annotation_set_hashes,
        },
    )
    details: dict[tuple[int, str, int], dict[str, Any]] = {}
    for key, annotation_version, annotation_set_hash, rsid, annotations_json in rows:
        parsed_key = _coerce_int(key)
        parsed_annotation_set_hash = _coerce_int(annotation_set_hash)
        if parsed_key is None or parsed_annotation_set_hash is None:
            continue
        details[(parsed_key, str(annotation_version or "current"), parsed_annotation_set_hash)] = {
            "rsid": rsid,
            "annotations_json": annotations_json,
        }
    return details


async def _family_has_small_variants(context: FamilyMetadataContext) -> bool:
    """Cheap presence probe for the family dashboard.

    Filters on the indexed ``family_guid`` (and ``project_guid``) column with
    ``LIMIT 1`` — the canonical per-family key, the same one
    ``family_variant_summary`` is keyed by. The paginated query instead filters
    by ``hasAny(calls.sampleId, …)`` membership, which can't use that index and
    scans the table; for a yes/no presence check that scan is wasted. This answers
    "does this family have small-variant data?" in a few ms.
    """
    if not context.assembly_name:
        return False
    entries_table = _small_table_name(context.assembly_name, "entries")
    where_clauses = ["family_guid = %(family_guid)s", "sign = 1"]
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    if context.project_ids:
        where_clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(context.project_ids)
    rows = await _execute_clickhouse(
        f"SELECT 1 FROM {entries_table} WHERE {' AND '.join(where_clauses)} LIMIT 1",
        params,
    )
    return bool(rows)


async def _family_has_structural_variants(context: FamilyMetadataContext) -> bool:
    """Cheap structural-variant presence probe (see :func:`_family_has_small_variants`)."""
    if not context.assembly_name:
        return False
    entries_table = _structural_table_name(context.assembly_name, "entries")
    where_clauses = ["family_guid = %(family_guid)s", "sign = 1"]
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    if context.project_ids:
        where_clauses.append("project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(context.project_ids)
    rows = await _execute_clickhouse(
        f"SELECT 1 FROM {entries_table} WHERE {' AND '.join(where_clauses)} LIMIT 1",
        params,
    )
    return bool(rows)


async def _fetch_structural_variant_summary(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
) -> tuple[int, dict[str, dict[str, int]]]:
    if not context.assembly_name:
        return 0, {}
    entries_table = _structural_table_name(context.assembly_name, "entries")
    where_clauses, params = _structural_variant_where_clauses(context, filters)
    rows = await _execute_clickhouse(
        f"""
        SELECT sv_type, source_value, count()
        FROM (
            SELECT
                any(e.svType) AS sv_type,
                any(e.source) AS source_value
            FROM {entries_table} AS e
            WHERE {' AND '.join(where_clauses)}
            GROUP BY e.key, e.variantId
        )
        GROUP BY sv_type, source_value
        """,
        params,
    )
    summary: dict[str, dict[str, int]] = {}
    total = 0
    for sv_type, source, count in rows:
        count_int = int(count or 0)
        total += count_int
        type_key = str(sv_type or "")
        source_key = str(source or "")
        summary.setdefault(type_key, {})[source_key] = count_int
    return total, summary


async def _fetch_small_variant_summary(
    context: FamilyMetadataContext,
) -> SmallVariantSummaryOut | None:
    if not context.assembly_name:
        return None

    family_variant_summary: SmallVariantSummaryOut | None = None
    family_params: dict[str, Any] = {"family_guid": context.family_uuid}

    if len(context.project_ids) == 1:
        family_params["project_guid"] = context.project_ids[0]
        family_rows = await _execute_clickhouse(
            f"""
            SELECT total_variants, snv_count, indel_count
            FROM {_small_summary_table_name(context.assembly_name, 'family_variant_summary')}
            WHERE family_guid = %(family_guid)s
              AND project_guid = %(project_guid)s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            family_params,
        )
        if family_rows:
            total_variants, snv_count, indel_count = family_rows[0]
            family_variant_summary = SmallVariantSummaryOut(
                total_variants=int(total_variants or 0),
                snv_count=int(snv_count or 0),
                indel_count=int(indel_count or 0),
            )

    if family_variant_summary is None:
        entries_table = _small_table_name(context.assembly_name, "entries")
        where_clauses = ["family_guid = %(family_guid)s", "sign = 1"]
        params: dict[str, Any] = {"family_guid": context.family_uuid}
        if context.project_ids:
            where_clauses.append("project_guid IN %(project_ids)s")
            params["project_ids"] = tuple(context.project_ids)
        rows = await _execute_clickhouse(
            f"""
            SELECT
                countDistinct(key) AS total_variants,
                countDistinctIf(key, length(ref) = 1 AND length(alt) = 1) AS snv_count,
                countDistinctIf(key, NOT (length(ref) = 1 AND length(alt) = 1)) AS indel_count
            FROM {entries_table}
            WHERE {' AND '.join(where_clauses)}
            """,
            params,
        )
        total_variants, snv_count, indel_count = rows[0] if rows else (0, 0, 0)
        family_variant_summary = SmallVariantSummaryOut(
            total_variants=int(total_variants or 0),
            snv_count=int(snv_count or 0),
            indel_count=int(indel_count or 0),
        )

    sample_rows: list[tuple[Any, ...]] = []
    if len(context.project_ids) == 1:
        sample_rows = await _execute_clickhouse(
            f"""
            SELECT sample_id, non_ref_count, het_count, hom_alt_count
            FROM {_small_summary_table_name(context.assembly_name, 'family_sample_variant_summary')}
            WHERE family_guid = %(family_guid)s
            ORDER BY sample_id
            """,
            {"family_guid": context.family_uuid},
        )

    if not sample_rows:
        entries_table = _small_table_name(context.assembly_name, "entries")
        sample_where_clauses = ["family_guid = %(family_guid)s", "sign = 1"]
        sample_params: dict[str, Any] = {"family_guid": context.family_uuid}
        if context.project_ids:
            sample_where_clauses.append("project_guid IN %(project_ids)s")
            sample_params["project_ids"] = tuple(context.project_ids)
        sample_rows = await _execute_clickhouse(
            f"""
            SELECT
                sample_id,
                countDistinctIf(key, gt NOT IN ('', '.', './.', '.|.', '0/0', '0|0')) AS non_ref_count,
                countDistinctIf(key, gt IN ('0/1', '1/0', '0|1', '1|0')) AS het_count,
                countDistinctIf(key, gt IN ('1/1', '1|1')) AS hom_alt_count
            FROM {entries_table}
            ARRAY JOIN `calls.sampleId` AS sample_id, `calls.gt` AS gt
            WHERE {' AND '.join(sample_where_clauses)}
            GROUP BY sample_id
            ORDER BY sample_id
            """,
            sample_params,
        )

    sample_counts_by_name: dict[str, SmallVariantSampleSummaryOut] = {}
    for stored_sample_id, non_ref_count, het_count, hom_alt_count in sample_rows:
        sample_name = _display_sample_name(context, stored_sample_id)
        if not sample_name or sample_name not in context.sample_name_to_uuid:
            continue
        sample_counts_by_name[sample_name] = SmallVariantSampleSummaryOut(
            sample_id=sample_name,
            non_ref_count=int(non_ref_count or 0),
            het_count=int(het_count or 0),
            hom_alt_count=int(hom_alt_count or 0),
        )

    ordered_sample_counts: list[SmallVariantSampleSummaryOut] = []
    for row in context.sample_rows:
        sample_name = str(row.get("sample_id") or "").strip()
        sample_summary = sample_counts_by_name.pop(sample_name, None)
        if sample_summary is not None:
            ordered_sample_counts.append(sample_summary)
    ordered_sample_counts.extend(sorted(sample_counts_by_name.values(), key=lambda item: item.sample_id))
    family_variant_summary.sample_counts = ordered_sample_counts
    return family_variant_summary


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
) -> tuple[list[str], dict[str, Any], bool]:
    where_clauses, params = _small_variant_where_clauses(
        context,
        filters,
        include_variant_ids=include_variant_ids,
        exclude_variant_ids=exclude_variant_ids,
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


async def _fetch_small_variant_rows(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    *,
    limit: int | None = None,
    offset: int = 0,
    panel_constraints: PanelFilterConstraints | None = None,
    include_variant_ids: Sequence[str] | None = None,
    exclude_variant_ids: Sequence[str] = (),
    include_regions: Sequence[Region] = (),
    exclude_regions: Sequence[Region] = (),
    exclude_gene_regions: Sequence[Region] = (),
    exclude_gene_terms: Sequence[str] = (),
) -> list[SmallVariantRecord]:
    if not context.assembly_name:
        return []
    entries_table = _small_table_name(context.assembly_name, "entries")
    where_clauses, params, _use_detail_join = _small_query_filter_parts(
        context,
        filters,
        panel_constraints=panel_constraints,
        include_variant_ids=include_variant_ids,
        exclude_variant_ids=exclude_variant_ids,
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=exclude_gene_terms,
    )
    query = f"""
        SELECT
            e.key AS key,
            e.variantId AS variant_id,
            e.annotation_version AS annotation_version,
            e.annotationSetHash AS annotation_set_hash,
            e.chrom AS chrom,
            e.pos AS pos,
            e.ref AS ref,
            e.alt AS alt,
            e.source AS source,
            e.rsid AS rsid,
            e.filters AS entry_filters,
            e.gene_symbols AS gene_symbols,
            e.calls.sampleId AS sample_ids,
            e.calls.gt AS sample_gts,
            e.calls.gq AS sample_gqs,
            e.calls.dp AS sample_dps,
            e.calls.ab AS sample_abs,
            e.calls.af AS sample_afs,
            e.calls.ad AS sample_ads,
            e.calls.ps AS sample_phase_sets,
            e.qual AS qual
        FROM {entries_table} AS e
        WHERE {' AND '.join(where_clauses)}
        ORDER BY e.xpos, e.key
    """
    query = _append_limit_offset(query, params, limit=limit, offset=offset)
    rows = await _execute_clickhouse(query, params)
    detail_map = await _fetch_small_variant_detail_map(
        context.assembly_name,
        [
            (parsed_key, str(row[2] or "current"), int(row[3]))
            for row in rows
            if (parsed_key := _coerce_int(row[0])) is not None
            and _coerce_int(row[3]) is not None
        ],
    )
    records: list[SmallVariantRecord] = []
    for row in rows:
        (
            variant_key,
            variant_id,
            annotation_version,
            annotation_set_hash,
            chrom,
            pos,
            ref,
            alt,
            source,
            rsid,
            entry_filters,
            gene_symbols,
            sample_ids,
            sample_gts,
            sample_gqs,
            sample_dps,
            sample_abs,
            sample_afs,
            sample_ads,
            sample_phase_sets,
            qual,
        ) = row
        parsed_variant_key = _coerce_int(variant_key)
        parsed_annotation_set_hash = _coerce_int(annotation_set_hash)
        detail = detail_map.get(
            (
                parsed_variant_key or -1,
                str(annotation_version or "current"),
                parsed_annotation_set_hash or -1,
            ),
            {},
        )
        annotations_json = detail.get("annotations_json")
        rsid = rsid if rsid not in (None, "") else detail.get("rsid")
        calls: list[SmallVariantCall] = []
        sample_id_list = _listify(sample_ids)
        for index, sample_id in enumerate(sample_id_list):
            sample_name = _display_sample_name(context, sample_id)
            if not sample_name or sample_name not in context.sample_name_to_uuid:
                continue
            af_values = _float_list(_indexed(sample_afs, index))
            ab_value = _coerce_float(_indexed(sample_abs, index))
            if not af_values and ab_value is not None:
                af_values = [ab_value]
            calls.append(
                SmallVariantCall(
                    sample=sample_name,
                    gt=_normalize_gt(_indexed(sample_gts, index)),
                    gq=_coerce_float(_indexed(sample_gqs, index)),
                    dp=_coerce_int(_indexed(sample_dps, index)),
                    af=af_values,
                    ad=_int_list(_indexed(sample_ads, index)),
                    ps=_coerce_int(_indexed(sample_phase_sets, index)),
                )
            )
        if not calls:
            continue
        start = int(pos)
        ref_text = str(ref or "")
        records.append(
            SmallVariantRecord(
                variant_key=parsed_variant_key,
                variant_id=str(variant_id),
                chr=normalize_chromosome(str(chrom)),
                start=start,
                end=start + max(len(ref_text), 1) - 1,
                ref=ref_text,
                alt=str(alt or ""),
                source=str(source) if source is not None else None,
                rsid=str(rsid) if rsid not in (None, "") else None,
                filters=_string_list(entry_filters),
                gene_symbols=_string_list(gene_symbols),
                annotations=_collect_annotations(_decode_json_payload(annotations_json)),
                calls=calls,
                qual=_coerce_float(qual),
            )
        )
    return records


async def fetch_imputed_phased_genotypes(
    context: FamilyMetadataContext,
    *,
    chrom: str,
    start: int,
    end: int,
    limit: int,
    source: str | None = "glimpse2",
) -> list[tuple[int, str, str, list[str], list[str]]]:
    """Lean, family-scoped fetch of variant positions, the ref/alt alleles, and
    the per-sample genotype strings in a region — no annotation hydration, so it
    stays cheap even for tens of thousands of sites. Used by the phased-marker
    parent-of-origin computation, relative haplotype colouring (``source=glimpse2``,
    phased ``0|1``) and sample-integrity QC (which also reads ``clair3`` SNVs,
    unphased ``0/1``). Pass ``source=None`` to read across all callsets. Each row
    is ``(pos, ref, alt, sample_ids, gts)``."""
    if not context.assembly_name:
        return []
    filters = SmallVariantQueryFilters(
        page=1,
        page_size=1,
        chromosome=chrom,
        start=start,
        end=end,
        source=source,
        overlap=True,
    )
    where_clauses, params, _ = _small_query_filter_parts(context, filters)
    entries_table = _small_table_name(context.assembly_name, "entries")
    query = f"""
        SELECT e.pos AS pos, e.ref AS ref, e.alt AS alt,
               e.calls.sampleId AS sample_ids, e.calls.gt AS sample_gts
        FROM {entries_table} AS e
        WHERE {' AND '.join(where_clauses)}
        ORDER BY e.pos
        LIMIT %(phased_limit)s
    """
    params["phased_limit"] = int(limit)
    rows = await _execute_clickhouse(query, params)
    return [
        (int(row[0]), str(row[1]), str(row[2]), [str(s) for s in row[3]], [str(g) for g in row[4]])
        for row in rows
    ]


async def fetch_family_variant_sources(context: FamilyMetadataContext) -> list[str]:
    """Distinct small-variant callset ``source`` values for the family (e.g.
    ``glimpse2``, ``clair3``). Lets sample-integrity QC pick the right genotype
    source for the application without hard-coding it."""
    if not context.assembly_name:
        return []
    filters = SmallVariantQueryFilters(page=1, page_size=1)
    where_clauses, params, _ = _small_query_filter_parts(context, filters)
    entries_table = _small_table_name(context.assembly_name, "entries")
    query = f"""
        SELECT DISTINCT e.source AS source
        FROM {entries_table} AS e
        WHERE {' AND '.join(where_clauses)}
        LIMIT 50
    """
    rows = await _execute_clickhouse(query, params)
    return [str(row[0]) for row in rows if row[0]]


async def _fetch_structural_variant_rows(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    *,
    limit: int | None = None,
    offset: int = 0,
    track_mode: bool = False,
) -> list[StructuralVariantRecord]:
    if not context.assembly_name:
        return []
    entries_table = _structural_table_name(context.assembly_name, "entries")
    details_table = _structural_table_name(context.assembly_name, "variants/details")
    where_clauses, params = _structural_variant_where_clauses(context, filters)
    # The genome track never uses the (multi-KB-per-variant) annotation JSON, and we set
    # annotations=[] for it below; not selecting the column means ClickHouse never reads
    # it off disk — the bulk of the remaining per-member fetch time for tens of
    # thousands of SVs. The details join stays so detail-column WHERE filters still work.
    annotations_col = "'' AS annotations_json" if track_mode else "any(d.annotationsJson) AS annotations_json"
    query = f"""
        SELECT
            any(e.key) AS key,
            any(e.variantId) AS variant_id,
            any(e.chrom) AS chrom,
            any(e.start) AS start,
            any(e.end) AS "end",
            any(e.svType) AS sv_type,
            any(e.source) AS source,
            any(d.remoteChrom) AS remote_chr,
            any(d.remoteStart) AS remote_start,
            any(d.svLen) AS sv_len,
            any(d.filters) AS filters,
            {annotations_col},
            any(e.gene_symbols) AS gene_symbols,
            any(e.calls.sampleId) AS sample_ids,
            any(e.calls.gt) AS sample_gts,
            any(e.calls.qual) AS sample_quals,
            any(e.calls.readSupport) AS sample_read_supports,
            any(e.calls.filter) AS sample_filters
        FROM {entries_table} AS e
        LEFT JOIN {details_table} AS d ON d.key = e.key
        WHERE {' AND '.join(where_clauses)}
        GROUP BY e.key, e.variantId
        ORDER BY chrom, start, key
    """
    query = _append_limit_offset(query, params, limit=limit, offset=offset)
    rows = await _execute_clickhouse(query, params)
    records: list[StructuralVariantRecord] = []
    for row in rows:
        (
            variant_key,
            variant_id,
            chrom,
            start,
            end,
            sv_type,
            source,
            remote_chr,
            remote_start,
            sv_len,
            filters_raw,
            annotations_json,
            gene_symbols,
            sample_ids,
            sample_gts,
            sample_quals,
            sample_read_supports,
            sample_filters_raw,
        ) = row
        calls: list[StructuralVariantCall] = []
        sample_id_list = _listify(sample_ids)
        for index, sample_id in enumerate(sample_id_list):
            sample_name = _display_sample_name(context, sample_id)
            if not sample_name or sample_name not in context.sample_name_to_uuid:
                continue
            calls.append(
                StructuralVariantCall(
                    sample=sample_name,
                    gt=_normalize_gt(_indexed(sample_gts, index)),
                    qual=_coerce_float(_indexed(sample_quals, index)),
                    read_support=_coerce_int(_indexed(sample_read_supports, index)),
                    filter=str(_indexed(sample_filters_raw, index) or "").strip() or None,
                )
            )
        if not calls:
            continue
        records.append(
            StructuralVariantRecord(
                variant_key=_coerce_int(variant_key),
                variant_id=str(variant_id),
                chr=normalize_chromosome(str(chrom)),
                start=int(start),
                end=int(end),
                sv_type=str(sv_type or ""),
                source=str(source) if source is not None else None,
                remote_chr=normalize_chromosome(str(remote_chr)) if remote_chr not in (None, "") else None,
                remote_start=_coerce_int(remote_start),
                remote_end=None,
                sv_len=_coerce_int(sv_len),
                filters=_string_list(filters_raw),
                gene_symbols=_string_list(gene_symbols),
                # Track mode never reads annotations downstream (annotation_extra is
                # zeroed); skipping the multi-KB-per-variant JSON decode is the bulk of
                # the speed-up for tens of thousands of SVs.
                annotations=[] if track_mode else _collect_annotations(_decode_json_payload(annotations_json)),
                calls=calls,
            )
        )
    return records


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


def _bounded_page_total(
    *,
    page: int,
    page_size: int,
    fetched_count: int,
) -> tuple[int, bool]:
    page_count = min(fetched_count, page_size)
    has_more = fetched_count > page_size
    total = _page_offset(page, page_size) + page_count
    if has_more:
        total += 1
    return total, has_more


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
) -> list[tuple[str, SmallVariantCompoundHetPair | SmallVariantRecord]]:
    pair_items = [
        ("group", pair)
        for pair in _compound_het_pairs(
            records,
            affected_samples=affected_samples,
            unaffected_samples=unaffected_samples,
        )
    ]
    if inheritance == _COMPOUND_HET_INHERITANCE:
        return pair_items

    if inheritance == _DE_NOVO_DOMINANT_INHERITANCE:
        return [
            ("variant", record)
            for record in records
            if _record_matches_de_novo_dominant(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
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


# Upper bound on variants pulled into a single prioritization pass. The Exomiser-style
# preset applies strict frequency/impact filters first, so the candidate set is normally
# far smaller than this; when it is exceeded the ranking covers only this many (the
# highest-priority variant could lie outside the window), so the response flags
# `ranking_truncated` to prompt the user to narrow their filters.
_PRIORITIZE_CANDIDATE_LIMIT = 5000


async def _affected_present_hpo(
    session: AsyncSession, context: FamilyMetadataContext
) -> tuple[list[str], dict[str, str]]:
    """Present HPO terms for affected individuals (all members if none flagged)."""
    affected_names, _unaffected = _family_affected_unaffected_sample_names(context)
    affected_uuids = [
        context.sample_name_to_uuid[name]
        for name in affected_names
        if name in context.sample_name_to_uuid
    ]
    clauses = ["family_id = CAST(:family_uuid AS uuid)", "ih.status = 'present'"]
    params: dict[str, Any] = {"family_uuid": context.family_uuid}
    if affected_uuids:
        clauses.append("ih.sample_id::text = ANY(:affected_uuids)")
        params["affected_uuids"] = affected_uuids
    rows = await session.execute(
        text(
            f"""
            SELECT DISTINCT ih.hpo_id AS hpo_id, t.label AS label
            FROM individual_hpo ih
            LEFT JOIN hpo_term t ON t.hpo_id = ih.hpo_id
            WHERE {' AND '.join(clauses)}
            """
        ),
        params,
    )
    terms: list[str] = []
    labels: dict[str, str] = {}
    for row in rows.mappings().all():
        terms.append(row["hpo_id"])
        if row["label"]:
            labels[row["hpo_id"]] = row["label"]
    return terms, labels


def _segregation_modes_by_variant(
    records: Sequence[SmallVariantRecord],
    *,
    context: FamilyMetadataContext,
) -> dict[str, list[str]]:
    affected, unaffected = _family_affected_unaffected_sample_names(context)
    sample_sex = _sample_sex_map(context.sample_rows)
    child_parents = _child_parent_map(context)
    modes: dict[str, list[str]] = {record.variant_id: [] for record in records}
    if not affected:
        return modes
    for pair in _compound_het_pairs(
        records, affected_samples=affected, unaffected_samples=unaffected
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
            record, affected_samples=affected, child_parents=child_parents
        ):
            bucket.append(MODE_DE_NOVO)
        elif _record_matches_de_novo_dominant(
            record, affected_samples=affected, unaffected_samples=unaffected
        ):
            bucket.append(MODE_DOMINANT)
    return modes


async def _serve_ranking_from_cache(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    cached: dict[str, Any],
    page: int,
    page_size: int,
    small_variant_summary: SmallVariantSummaryOut | None,
) -> VariantPage | None:
    """Rebuild a prioritised page from a cached ranking.

    The cache holds the ranked (variant id + score breakdown) order; the page's variant
    records and review state are fetched fresh so annotations and review status are never
    served stale. Returns None on any reconstruction problem so the caller falls back to a
    live compute.
    """
    ranking = cached.get("ranking") or []
    if isinstance(ranking, str):
        try:
            ranking = json.loads(ranking)
        except json.JSONDecodeError:
            return None
    total = int(cached.get("total") or 0)
    total_is_estimated = bool(cached.get("total_is_estimated"))
    ranking_truncated = bool(cached.get("ranking_truncated"))
    reported_total = min(total, _SMALL_COUNT_LIMIT)
    unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None

    skip = max(page - 1, 0) * page_size if page_size else 0
    page_entries = ranking[skip : skip + page_size] if page_size else ranking[skip:]
    page_ids = [str(entry["variant_id"]) for entry in page_entries if entry.get("variant_id")]

    page_variants: list[VariantOut] = []
    if page_ids:
        fetch_filters = SmallVariantQueryFilters(page=1, page_size=len(page_ids) + 5)
        records = await _fetch_small_variant_rows(
            context,
            fetch_filters,
            include_variant_ids=page_ids,
            limit=len(page_ids) + 5,
        )
        by_id: dict[str, VariantOut] = {}
        for record in records:
            out = _small_variant_out(record)
            by_id[str(out.id)] = out
        for entry in page_entries:
            variant_out = by_id.get(str(entry.get("variant_id")))
            if variant_out is None:
                continue
            priority = entry.get("priority")
            if priority:
                try:
                    variant_out.priority = VariantPriorityOut.model_validate(priority)
                except Exception:  # noqa: BLE001 — a bad cached blob should fall back to live
                    return None
            page_variants.append(variant_out)
        await _hydrate_small_variant_outs(session, context=context, variants=page_variants)

    return VariantPage(
        total=reported_total,
        total_is_estimated=total_is_estimated,
        unfiltered_total=unfiltered_total,
        unfiltered_total_is_estimated=False,
        count_limit=_SMALL_COUNT_LIMIT - 1,
        ranking_truncated=ranking_truncated,
        ranking_cached=True,
        ranking_computed_at=cached.get("computed_at"),
        variants=page_variants,
        small_variant_summary=small_variant_summary,
    )


async def _serve_subpanel_from_superset(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    base_hash: str,
    panel_constraints: PanelFilterConstraints,
    page: int,
    page_size: int,
    small_variant_summary: SmallVariantSummaryOut | None,
) -> VariantPage | None:
    """Serve a gene panel from a broader cached ranking with the same panel-agnostic
    inputs.

    The per-variant scores don't depend on the panel, so a narrower panel's ranking is the
    superset's ranking restricted to the panel's variants — in the same order. Membership
    is re-validated against ClickHouse with the *exact* panel filter, so the result equals
    a direct compute (no missed variants). Only complete (non-truncated) supersets whose
    genes cover the request are used; otherwise returns None for a live compute.
    """
    request_genes = {gene.lower() for gene in (panel_constraints.genes or []) if gene}
    if not request_genes:
        return None  # no gene panel to narrow from a gene-panel superset

    candidates = await find_superset_candidates(
        session, family_uuid=context.family_uuid, base_hash=base_hash
    )
    for candidate in candidates:
        candidate_panel_id = candidate.get("panel_id")
        if candidate_panel_id:
            candidate_constraints = await _fetch_panel_constraints(
                session, candidate_panel_id, assembly_id=context.assembly_id
            )
            candidate_genes = {gene.lower() for gene in (candidate_constraints.genes or []) if gene}
            if not request_genes.issubset(candidate_genes):
                continue  # this superset doesn't cover all the requested genes

        ranking = candidate.get("ranking") or []
        if isinstance(ranking, str):
            try:
                ranking = json.loads(ranking)
            except json.JSONDecodeError:
                continue
        superset_ids = [str(entry["variant_id"]) for entry in ranking if entry.get("variant_id")]
        if not superset_ids:
            continue

        # Re-validate which of the superset's variants are in the requested panel, using
        # the exact panel filter (SQL + the same Python check the live path applies).
        fetch_filters = SmallVariantQueryFilters(page=1, page_size=len(superset_ids) + 5)
        records = await _fetch_small_variant_rows(
            context,
            fetch_filters,
            include_variant_ids=superset_ids,
            panel_constraints=panel_constraints,
            limit=len(superset_ids) + 5,
        )
        by_id: dict[str, Any] = {}
        for record in records:
            if _small_record_matches(
                record, fetch_filters, [], [], [], panel_constraints=panel_constraints
            ):
                by_id[str(record.variant_id)] = record

        subset_ranking = [
            entry for entry in ranking if str(entry.get("variant_id")) in by_id
        ]
        total = len(subset_ranking)
        reported_total = min(total, _SMALL_COUNT_LIMIT)
        skip = max(page - 1, 0) * page_size if page_size else 0
        page_entries = subset_ranking[skip : skip + page_size] if page_size else subset_ranking[skip:]

        page_variants: list[VariantOut] = []
        for entry in page_entries:
            record = by_id.get(str(entry.get("variant_id")))
            if record is None:
                continue
            variant_out = _small_variant_out(record)
            priority = entry.get("priority")
            if priority:
                try:
                    variant_out.priority = VariantPriorityOut.model_validate(priority)
                except Exception:  # noqa: BLE001 - fall back to live compute on a bad blob
                    return None
            page_variants.append(variant_out)
        await _hydrate_small_variant_outs(session, context=context, variants=page_variants)

        unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None
        return VariantPage(
            total=reported_total,
            total_is_estimated=False,
            unfiltered_total=unfiltered_total,
            unfiltered_total_is_estimated=False,
            count_limit=_SMALL_COUNT_LIMIT - 1,
            ranking_truncated=False,
            ranking_cached=True,
            ranking_computed_at=candidate.get("computed_at"),
            variants=page_variants,
            small_variant_summary=small_variant_summary,
        )
    return None


async def _prioritized_small_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
    page: int,
    page_size: int,
    panel_constraints: PanelFilterConstraints,
    review_variant_ids: set[str] | None,
    excluded_review_variant_ids: set[str],
    include_review_filter_active: bool,
    include_regions: list[Region],
    exclude_regions: Sequence[Region],
    exclude_gene_regions: Sequence[Region],
    exclude_gene_terms: Sequence[str],
    small_variant_summary: SmallVariantSummaryOut | None,
) -> VariantPage:
    if filters.gene:
        include_regions = [
            *include_regions,
            *await _fetch_gene_regions(
                session, gene_query=filters.gene, assembly_id=context.assembly_id
            ),
        ]

    # The phenotype-prioritised ranking is expensive (~10s); serve a cached ranking
    # when the inputs (filters + HPO + pedigree + panel + Monarch release) are unchanged.
    patient_terms, term_labels = await _affected_present_hpo(session, context)
    inputs_hash, base_hash = await compute_ranking_hashes(
        session,
        context=context,
        filters=filters,
        patient_terms=patient_terms,
        review_variant_ids=review_variant_ids if include_review_filter_active else None,
        excluded_review_variant_ids=excluded_review_variant_ids,
        include_review_filter_active=include_review_filter_active,
    )
    cached = await get_cached_ranking(
        session, family_uuid=context.family_uuid, inputs_hash=inputs_hash
    )
    if cached is not None:
        served = await _serve_ranking_from_cache(
            session,
            context=context,
            cached=cached,
            page=page,
            page_size=page_size,
            small_variant_summary=small_variant_summary,
        )
        if served is not None:
            return served

    # No exact hit: a narrower panel can be served from a broader cached ranking with the
    # same panel-agnostic inputs (scores are panel-independent), re-validating membership.
    served = await _serve_subpanel_from_superset(
        session,
        context=context,
        base_hash=base_hash,
        panel_constraints=panel_constraints,
        page=page,
        page_size=page_size,
        small_variant_summary=small_variant_summary,
    )
    if served is not None:
        return served

    records = await _fetch_small_variant_rows(
        context,
        filters,
        panel_constraints=panel_constraints,
        include_variant_ids=review_variant_ids if include_review_filter_active else None,
        exclude_variant_ids=excluded_review_variant_ids,
        # Fetch one extra to detect overflow without dropping a genuine candidate when
        # the set is exactly at the limit.
        limit=_PRIORITIZE_CANDIDATE_LIMIT + 1,
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=exclude_gene_terms,
    )
    capped = len(records) > _PRIORITIZE_CANDIDATE_LIMIT
    if capped:
        records = records[:_PRIORITIZE_CANDIDATE_LIMIT]

    filtered = [
        record
        for record in records
        if record.variant_id not in excluded_review_variant_ids
        and ((not include_review_filter_active) or record.variant_id in (review_variant_ids or set()))
        and _small_record_matches(
            record,
            filters,
            include_regions,
            exclude_regions,
            exclude_gene_regions,
            panel_constraints=panel_constraints,
        )
    ]
    if not filtered:
        await store_ranking(
            session,
            family_uuid=context.family_uuid,
            inputs_hash=inputs_hash,
            base_hash=base_hash,
            panel_id=filters.panel_id,
            total=0,
            total_is_estimated=capped,
            ranking_truncated=capped,
            ranking=[],
            provenance={
                "hpo_count": len(patient_terms),
                "panel_id": filters.panel_id,
                "filters": canonical_filters(filters),
            },
        )
        return VariantPage(
            total=0,
            total_is_estimated=capped,
            count_limit=_SMALL_COUNT_LIMIT - 1,
            variants=[],
            small_variant_summary=small_variant_summary,
        )

    modes_by_variant = _segregation_modes_by_variant(filtered, context=context)
    affected_names, _unaffected = _family_affected_unaffected_sample_names(context)
    segregation_evaluated = bool(affected_names)

    variants = [_small_variant_out(record) for record in filtered]
    gene_symbols = {variant.gene for variant in variants if variant.gene}
    phenotype_scores = (
        await score_genes_for_hpo(
            session, gene_symbols=gene_symbols, patient_hpo_ids=patient_terms
        )
        if patient_terms and gene_symbols
        else {}
    )
    # Gene-level constraint (pLI / missense-Z) from gene_info, for all candidates (the
    # hydrate step only covers the page); also fills variant.gene_pli for display.
    constraint_map = await _fetch_gene_constraint_metric_map(session, variants)

    for variant in variants:
        metrics = constraint_map.get(str(variant.id))
        if metrics:
            if variant.gene_pli is None:
                variant.gene_pli = metrics.get("gene_pli")
            if variant.gene_missense_z is None:
                variant.gene_missense_z = metrics.get("gene_missense_z")

        modes = modes_by_variant.get(str(variant.id), [])
        gene_score = phenotype_scores.get(variant.gene.upper()) if variant.gene else None
        phenotype_value = gene_score.score if gene_score else None
        scored = score_variant(
            impact=variant.impact,
            clinvar=variant.clinvar,
            cadd_phred=variant.cadd_phred,
            revel=variant.revel,
            spliceai_max=variant.spliceai_max,
            lof=variant.lof,
            gnomad_popmax_af=variant.population_frequencies.get("gnomad_popmax_af"),
            gnomad_af=variant.gnomad_af,
            segregation_modes=modes,
            segregation_evaluated=segregation_evaluated,
            phenotype_score=phenotype_value,
            alpha_missense_class=variant.alpha_missense_class,
            alpha_missense_pathogenicity=variant.alpha_missense_pathogenicity,
            gene_pli=variant.gene_pli,
            gene_missense_z=variant.gene_missense_z,
        )
        phenotype_matches = (
            [
                MonarchPhenotypeMatchOut(
                    hpo_id=match["hpo_id"], label=term_labels.get(match["hpo_id"])
                )
                for match in gene_score.matched
            ]
            if gene_score
            else []
        )
        variant.priority = VariantPriorityOut(
            combined_score=round(scored.combined_score, 4),
            variant_score=round(scored.variant_score, 4),
            pathogenicity_score=round(scored.pathogenicity, 4),
            frequency_score=round(scored.frequency, 4),
            segregation_weight=round(scored.segregation_weight, 4),
            phenotype_score=round(phenotype_value, 4) if phenotype_value is not None else None,
            segregation_modes=scored.segregation_modes,
            phenotype_gene=variant.gene if gene_score else None,
            phenotype_matches=phenotype_matches,
        )

    variants.sort(
        key=lambda v: (
            v.priority.combined_score if v.priority else 0.0,
            v.priority.variant_score if v.priority else 0.0,
        ),
        reverse=True,
    )
    for index, variant in enumerate(variants, start=1):
        if variant.priority:
            variant.priority.rank = index

    ranked_total = len(variants)
    # When the candidate set overflowed the ranking window, the ranking only covers the
    # window — report the true filtered count (not the window size) and flag the
    # truncation so the UI can prompt the user to narrow filters.
    if capped:
        true_total, true_estimated = await _count_small_variant_rows_bounded(
            context,
            filters,
            panel_constraints=panel_constraints,
            include_variant_ids=review_variant_ids if include_review_filter_active else None,
            exclude_variant_ids=excluded_review_variant_ids,
            include_regions=include_regions,
            exclude_regions=exclude_regions,
            exclude_gene_regions=exclude_gene_regions,
            exclude_gene_terms=exclude_gene_terms,
        )
        total = max(true_total, ranked_total)
        total_is_estimated = True
    else:
        total = ranked_total
        total_is_estimated = ranked_total >= _SMALL_COUNT_LIMIT

    reported_total = min(total, _SMALL_COUNT_LIMIT)

    # Cache the full ranked order (variant id + score breakdown) so the next open of an
    # unchanged view is served without re-scoring. The page records + review state are
    # re-fetched fresh on a cache hit.
    await store_ranking(
        session,
        family_uuid=context.family_uuid,
        inputs_hash=inputs_hash,
        base_hash=base_hash,
        panel_id=filters.panel_id,
        total=reported_total,
        total_is_estimated=total_is_estimated,
        ranking_truncated=capped,
        ranking=[
            {
                "variant_id": str(variant.id),
                "priority": variant.priority.model_dump(mode="json") if variant.priority else None,
            }
            for variant in variants
        ],
        provenance={
                "hpo_count": len(patient_terms),
                "panel_id": filters.panel_id,
                "filters": canonical_filters(filters),
            },
    )

    skip = max(page - 1, 0) * page_size if page_size else 0
    page_variants = variants[skip : skip + page_size] if page_size else variants[skip:]
    await _hydrate_small_variant_outs(session, context=context, variants=page_variants)

    unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None
    return VariantPage(
        total=reported_total,
        total_is_estimated=total_is_estimated,
        unfiltered_total=unfiltered_total,
        unfiltered_total_is_estimated=False,
        count_limit=_SMALL_COUNT_LIMIT - 1,
        ranking_truncated=capped,
        ranking_cached=False,
        ranking_computed_at=datetime.now(timezone.utc),
        variants=page_variants,
        small_variant_summary=small_variant_summary,
    )


async def precompute_family_ranking_safe(
    family_identifier: str, user: Any, *, project_id: str | None = None
) -> None:
    """Best-effort background warm of the prioritised-ranking cache after an HPO or
    pedigree edit.

    Replays the family's most recent prioritised query with the now-current inputs, so
    the next open of that view is served from cache instead of recomputing (~10s). Opens
    its own session (the triggering request is long gone); logs and swallows every error.
    Does nothing until a family has opened the prioritised view at least once (there is no
    query to replay yet), so it never guesses filters.
    """
    from ..core.postgres import get_postgres_engine, get_postgres_sessionmaker
    from .family_metadata_context import build_family_metadata_context

    try:
        get_postgres_engine()
        async with get_postgres_sessionmaker()() as session:
            context = await build_family_metadata_context(
                session, family_identifier=family_identifier, user=user, project_id=project_id
            )
            row = (
                await session.execute(
                    text(
                        """
                        SELECT provenance
                        FROM family_variant_ranking_cache
                        WHERE family_id = CAST(:family_uuid AS uuid)
                        ORDER BY computed_at DESC
                        LIMIT 1
                        """
                    ),
                    {"family_uuid": context.family_uuid},
                )
            ).mappings().first()
            provenance = dict(row)["provenance"] if row else None
            stored_filters = provenance.get("filters") if isinstance(provenance, dict) else None
            if not stored_filters:
                return  # nothing to replay yet — leave the first open as a (lazy) miss

            filters = SmallVariantQueryFilters(**{**stored_filters, "page": 1, "page_size": 100})
            panel_constraints = (
                await _fetch_panel_constraints(
                    session, filters.panel_id, assembly_id=context.assembly_id
                )
                if filters.panel_id
                else PanelFilterConstraints()
            )
            # Replay the no-review default view (the high-value cached case). Computing it
            # stores the ranking under the now-current inputs hash.
            await _prioritized_small_variants_page(
                session,
                context=context,
                filters=filters,
                page=1,
                page_size=100,
                panel_constraints=panel_constraints,
                review_variant_ids=None,
                excluded_review_variant_ids=set(),
                include_review_filter_active=False,
                include_regions=[],
                exclude_regions=[],
                exclude_gene_regions=[],
                exclude_gene_terms=[],
                small_variant_summary=None,
            )
            logger.info("Warmed prioritised-ranking cache for family %s", family_identifier)
    except Exception:  # pragma: no cover - background best-effort
        logger.exception(
            "Background ranking-cache warm failed for family %s", family_identifier
        )


async def get_family_small_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    page: int,
    page_size: int,
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    intervals: str | None = None,
    inheritance: str | None = None,
    expanded_carrier_screening: bool = False,
    ps: int | None = None,
    type: str | None = None,
    source: str | None = None,
    gene: str | None = None,
    transcript: str | None = None,
    impact: list[str] | None = None,
    effect: list[str] | None = None,
    clinvar: list[str] | None = None,
    exclude_clinvar: list[str] | None = None,
    clinvar_overrides_frequency: bool = False,
    exclude_gene: str | None = None,
    exclude_intervals: str | None = None,
    rsid: str | None = None,
    hgvsc: str | None = None,
    hgvsp: str | None = None,
    canonical_only: bool = False,
    mane_only: bool = False,
    lof_only: bool = False,
    max_gnomad_af: float | None = None,
    max_gnomad_exomes_af: float | None = None,
    max_gnomad_genomes_af: float | None = None,
    max_gnomad_popmax_af: float | None = None,
    max_topmed_af: float | None = None,
    max_gnomad_ac: int | None = None,
    max_gnomad_hom_count: int | None = None,
    max_gnomad_hemi_count: int | None = None,
    min_cadd: float | None = None,
    min_revel: float | None = None,
    min_spliceai: float | None = None,
    sift: str | None = None,
    polyphen: str | None = None,
    panel_id: str | None = None,
    sample_filters: list[str] | None = None,
    review_classifications: list[str] | None = None,
    review_tags: list[str] | None = None,
    exclude_review_tags: list[str] | None = None,
    has_notes: bool = False,
    overlap: bool = False,
    require_sv_second_hit: bool = False,
    prioritize: bool = False,
    track_mode: bool = False,
    track_result_limit: int | None = None,
    count_only: bool = False,
) -> VariantPage:
    normalized_inheritance = _normalize_small_variant_inheritance(inheritance)
    filters = SmallVariantQueryFilters(
        page=page,
        page_size=page_size,
        chromosome=chr,
        start=start,
        end=end,
        intervals=intervals,
        inheritance=normalized_inheritance,
        expanded_carrier_screening=expanded_carrier_screening,
        phase_set=ps,
        variant_type=type,
        source=source,
        gene=gene,
        transcript=transcript,
        impact=impact or [],
        effect=effect or [],
        clinvar=clinvar or [],
        exclude_clinvar=exclude_clinvar or [],
        clinvar_overrides_frequency=clinvar_overrides_frequency,
        exclude_gene=exclude_gene,
        exclude_intervals=exclude_intervals,
        rsid=rsid,
        hgvsc=hgvsc,
        hgvsp=hgvsp,
        canonical_only=canonical_only,
        mane_only=mane_only,
        lof_only=lof_only,
        max_gnomad_af=max_gnomad_af,
        max_gnomad_exomes_af=max_gnomad_exomes_af,
        max_gnomad_genomes_af=max_gnomad_genomes_af,
        max_gnomad_popmax_af=max_gnomad_popmax_af,
        max_topmed_af=max_topmed_af,
        max_gnomad_ac=max_gnomad_ac,
        max_gnomad_hom_count=max_gnomad_hom_count,
        max_gnomad_hemi_count=max_gnomad_hemi_count,
        min_cadd=min_cadd,
        min_revel=min_revel,
        min_spliceai=min_spliceai,
        sift=sift,
        polyphen=polyphen,
        panel_id=panel_id,
        sample_filters=sample_filters or [],
        overlap=overlap,
        require_sv_second_hit=require_sv_second_hit,
    )
    if count_only:
        # Presence check only (family dashboard): probe the indexed family_guid
        # column with LIMIT 1 instead of the bounded filtered count + unfiltered
        # summary scan below. The dashboard only needs total > 0 to decide whether
        # to surface the small-variants workspace, so report a presence-bounded
        # total (0 or 1) and no rows.
        exists = await _family_has_small_variants(context)
        return VariantPage(total=1 if exists else 0, variants=[])
    if filters.require_sv_second_hit:
        # Resolve the family's SV-hit gene set once; every query path below reads it off
        # ``filters`` to intersect results with genes that also carry a structural variant.
        await _ensure_family_sv_gene_index(session, context)
        filters.sv_hit_genes = await get_sv_hit_genes(session, family_uuid=context.family_uuid)
    small_variant_summary = None if track_mode else await _fetch_small_variant_summary(context)
    panel_constraints = PanelFilterConstraints()
    if filters.panel_id:
        panel_constraints = await _fetch_panel_constraints(
            session,
            filters.panel_id,
            assembly_id=context.assembly_id,
        )
        if not panel_constraints.genes and not panel_constraints.regions:
            return VariantPage(total=0, variants=[], small_variant_summary=small_variant_summary)

    include_review_filter_active = bool(review_classifications or review_tags or has_notes)
    review_variant_ids: set[str] | None = None
    if include_review_filter_active:
        review_variant_ids = await list_matching_small_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            classifications=review_classifications,
            tags=review_tags,
            has_notes=has_notes,
        )
        if not review_variant_ids:
            return VariantPage(total=0, variants=[], small_variant_summary=small_variant_summary)
    excluded_review_variant_ids = (
        await list_matching_small_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            tags=exclude_review_tags,
        )
        if exclude_review_tags
        else set()
    )

    include_regions: list[Region] = []
    if filters.intervals:
        interval_regions = _parse_interval_regions(filters.intervals)
        if not interval_regions:
            return VariantPage(total=0, variants=[], small_variant_summary=small_variant_summary)
        include_regions.extend(interval_regions)
    exclude_regions = _parse_interval_regions(filters.exclude_intervals)
    exclude_gene_regions = (
        await _fetch_gene_regions(session, gene_query=filters.exclude_gene, assembly_id=context.assembly_id)
        if filters.exclude_gene
        else []
    )
    exclude_gene_terms = _split_gene_terms(filters.exclude_gene)

    if prioritize and not track_mode:
        return await _prioritized_small_variants_page(
            session,
            context=context,
            filters=filters,
            page=page,
            page_size=page_size,
            panel_constraints=panel_constraints,
            review_variant_ids=review_variant_ids,
            excluded_review_variant_ids=excluded_review_variant_ids,
            include_review_filter_active=include_review_filter_active,
            include_regions=include_regions,
            exclude_regions=exclude_regions,
            exclude_gene_regions=exclude_gene_regions,
            exclude_gene_terms=exclude_gene_terms,
            small_variant_summary=small_variant_summary,
        )

    if track_mode and track_result_limit is not None:
        safe_track_result_limit = min(
            max(int(track_result_limit), 1),
            _SMALL_TRACK_RESULT_LIMIT,
        )
        if not _can_use_small_native_page(
            filters,
            track_mode=False,
        ):
            return _small_track_limit_response(
                track_result_limit=safe_track_result_limit
            )

        total, total_is_estimated = await _count_small_variant_rows_bounded(
            context,
            filters,
            count_limit=safe_track_result_limit,
            panel_constraints=panel_constraints,
            include_variant_ids=review_variant_ids if include_review_filter_active else None,
            exclude_variant_ids=excluded_review_variant_ids,
            include_regions=include_regions,
            exclude_regions=exclude_regions,
            exclude_gene_regions=exclude_gene_regions,
            exclude_gene_terms=exclude_gene_terms,
        )
        if total_is_estimated or total >= safe_track_result_limit:
            return VariantPage(
                total=total,
                total_is_estimated=True,
                count_limit=safe_track_result_limit,
                variants=[],
            )

        fetch_limit = min(max(page_size, 0), max(safe_track_result_limit - 1, 1))
        if fetch_limit <= 0 or total <= 0:
            return VariantPage(
                total=total,
                total_is_estimated=False,
                count_limit=safe_track_result_limit,
                variants=[],
            )

        fetched_records = await _fetch_small_variant_rows(
            context,
            filters,
            limit=fetch_limit,
            offset=_page_offset(page, page_size),
            panel_constraints=panel_constraints,
            include_variant_ids=review_variant_ids if include_review_filter_active else None,
            exclude_variant_ids=excluded_review_variant_ids,
            include_regions=include_regions,
            exclude_regions=exclude_regions,
            exclude_gene_regions=exclude_gene_regions,
            exclude_gene_terms=exclude_gene_terms,
        )
        variants = [_small_variant_out(record) for record in fetched_records]
        await _hydrate_small_variant_outs(
            session,
            context=context,
            variants=variants,
        )
        return VariantPage(
            total=total,
            total_is_estimated=False,
            count_limit=safe_track_result_limit,
            variants=variants,
        )

    if _can_use_small_native_page(
        filters,
        track_mode=track_mode,
    ):
        fetched_records = await _fetch_small_variant_rows(
            context,
            filters,
            limit=page_size + 1,
            offset=_page_offset(page, page_size),
            panel_constraints=panel_constraints,
            include_variant_ids=review_variant_ids if include_review_filter_active else None,
            exclude_variant_ids=excluded_review_variant_ids,
            include_regions=include_regions,
            exclude_regions=exclude_regions,
            exclude_gene_regions=exclude_gene_regions,
            exclude_gene_terms=exclude_gene_terms,
        )
        count_task = _count_small_variant_rows_bounded(
            context,
            filters,
            panel_constraints=panel_constraints,
            include_variant_ids=review_variant_ids if include_review_filter_active else None,
            exclude_variant_ids=excluded_review_variant_ids,
            include_regions=include_regions,
            exclude_regions=exclude_regions,
            exclude_gene_regions=exclude_gene_regions,
            exclude_gene_terms=exclude_gene_terms,
        )
        total, total_is_estimated = await count_task
        unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None
        unfiltered_total_is_estimated = False
        page_records = fetched_records[:page_size]
        if not page_records:
            return VariantPage(
                total=0 if track_mode else total,
                total_is_estimated=total_is_estimated,
                unfiltered_total=unfiltered_total,
                unfiltered_total_is_estimated=unfiltered_total_is_estimated,
                count_limit=_SMALL_COUNT_LIMIT - 1,
                variants=[],
                small_variant_summary=small_variant_summary,
            )
        variants = [_small_variant_out(record) for record in page_records]
        await _hydrate_small_variant_outs(
            session,
            context=context,
            variants=variants,
        )
        return VariantPage(
            total=total,
            total_is_estimated=total_is_estimated,
            unfiltered_total=unfiltered_total,
            unfiltered_total_is_estimated=unfiltered_total_is_estimated,
            count_limit=_SMALL_COUNT_LIMIT - 1,
            variants=variants,
            small_variant_summary=small_variant_summary,
        )

    if filters.gene:
        gene_regions = await _fetch_gene_regions(
            session,
            gene_query=filters.gene,
            assembly_id=context.assembly_id,
        )
        include_regions.extend(gene_regions)
    inheritance_candidate_limit = _small_pair_inheritance_candidate_limit(filters)
    records = await _fetch_small_variant_rows(
        context,
        filters,
        panel_constraints=panel_constraints,
        include_variant_ids=review_variant_ids if include_review_filter_active else None,
        exclude_variant_ids=excluded_review_variant_ids,
        limit=inheritance_candidate_limit,
        include_regions=include_regions,
        exclude_regions=exclude_regions,
        exclude_gene_regions=exclude_gene_regions,
        exclude_gene_terms=exclude_gene_terms,
    )
    inheritance_candidates_capped = (
        inheritance_candidate_limit is not None
        and len(records) >= inheritance_candidate_limit
    )
    if inheritance_candidates_capped:
        records = records[: inheritance_candidate_limit - 1]
    filtered = [
        record
        for record in records
        if record.variant_id not in excluded_review_variant_ids
        and ((not include_review_filter_active) or record.variant_id in (review_variant_ids or set()))
        and _small_record_matches(
            record,
            filters,
            include_regions,
            exclude_regions,
            exclude_gene_regions,
            panel_constraints=panel_constraints,
        )
    ]
    affected_sample_names, unaffected_sample_names = _family_affected_unaffected_sample_names(context)
    if filters.expanded_carrier_screening:
        filtered = _filter_expanded_carrier_screening(
            filtered,
            context.sample_rows,
            context.relationship_rows,
        )
    if track_mode:
        unfiltered_total = None
        unfiltered_total_is_estimated = False
    else:
        unfiltered_total = small_variant_summary.total_variants if small_variant_summary else None
        unfiltered_total_is_estimated = False
    if filters.inheritance:
        inheritance_items = _inheritance_result_items(
            inheritance=filters.inheritance,
            records=filtered,
            affected_samples=affected_sample_names,
            unaffected_samples=unaffected_sample_names,
            sample_rows=context.sample_rows,
        )
        total = len(inheritance_items)
        reported_total = min(total, _SMALL_COUNT_LIMIT)
        total_is_estimated = inheritance_candidates_capped or total >= _SMALL_COUNT_LIMIT
        skip = max(page - 1, 0) * page_size if page_size else 0
        page_items = inheritance_items[skip: skip + page_size] if page_size else inheritance_items[skip:]
        page_variant_groups: list[SmallVariantGroupOut] = []
        page_single_variants: list[VariantOut] = []
        group_variant_outs: list[VariantOut] = []
        for item_type, item_value in page_items:
            if item_type == "group":
                pair = item_value
                left_variant = _small_variant_out(pair.left)
                right_variant = _small_variant_out(pair.right)
                group_variant_outs.extend([left_variant, right_variant])
                page_variant_groups.append(
                    SmallVariantGroupOut(
                        group_key=pair.pair_key,
                        gene=pair.gene,
                        gene_id=pair.gene_id,
                        variants=[left_variant, right_variant],
                    )
                )
            else:
                page_single_variants.append(_small_variant_out(item_value))
        await _hydrate_small_variant_outs(
            session,
            context=context,
            variants=[*group_variant_outs, *page_single_variants],
        )
        for group in page_variant_groups:
            if len(group.variants) >= 2:
                group.review = _group_review_for_pair(group.variants[0], group.variants[1])
        return VariantPage(
            total=0 if track_mode else reported_total,
            total_is_estimated=total_is_estimated,
            unfiltered_total=unfiltered_total,
            unfiltered_total_is_estimated=unfiltered_total_is_estimated,
            count_limit=_SMALL_COUNT_LIMIT - 1,
            variants=page_single_variants,
            variant_groups=page_variant_groups,
            small_variant_summary=small_variant_summary,
        )
    total = len(filtered)
    reported_total = min(total, _SMALL_COUNT_LIMIT)
    total_is_estimated = inheritance_candidates_capped or total >= _SMALL_COUNT_LIMIT
    if track_mode:
        page_records = _sample_small_track_records(filtered, page_size)
    else:
        skip = max(page - 1, 0) * page_size if page_size else 0
        page_records = filtered[skip: skip + page_size] if page_size else filtered[skip:]
    variants = [_small_variant_out(record) for record in page_records]
    await _hydrate_small_variant_outs(
        session,
        context=context,
        variants=variants,
    )
    return VariantPage(
        total=0 if track_mode else reported_total,
        total_is_estimated=total_is_estimated,
        unfiltered_total=unfiltered_total,
        unfiltered_total_is_estimated=unfiltered_total_is_estimated,
        count_limit=_SMALL_COUNT_LIMIT - 1,
        variants=variants,
        small_variant_summary=small_variant_summary,
    )


# Hard cap on rows pulled into a single CSV export. Generous enough for any
# realistic filtered result set, but bounds memory/response size.
_MAX_SMALL_VARIANT_EXPORT_ROWS = 50_000


async def export_family_small_variants(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    limit: int = _MAX_SMALL_VARIANT_EXPORT_ROWS,
    prioritize: bool = False,
    **filters: Any,
) -> list[VariantOut]:
    """Fetch up to ``limit`` filtered small variants for CSV export.

    Reuses :func:`get_family_small_variants_page` with the same filters as the
    table so the export always matches what the user sees, but requests a single
    large page instead of paginating. Compound-het pair groups are flattened in
    alongside single variants.
    """

    limit = max(1, min(limit, _MAX_SMALL_VARIANT_EXPORT_ROWS))
    page = await get_family_small_variants_page(
        session,
        context=context,
        page=1,
        page_size=limit,
        prioritize=prioritize,
        track_mode=False,
        **filters,
    )
    rows: list[VariantOut] = []
    for group in page.variant_groups:
        rows.extend(group.variants)
    rows.extend(page.variants)
    return rows[:limit]


_MAX_STRUCTURAL_VARIANT_EXPORT_ROWS = 50_000


async def export_family_structural_variants(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    limit: int = _MAX_STRUCTURAL_VARIANT_EXPORT_ROWS,
    prioritize: bool = False,
    **filters: Any,
) -> list[VariantOut]:
    """Fetch up to ``limit`` filtered structural variants for CSV export.

    Reuses :func:`get_family_structural_variants_page` with the same filters as the
    table (so ``review_tag=report`` exports exactly the reported set), but requests a
    single large page instead of paginating.
    """

    limit = max(1, min(limit, _MAX_STRUCTURAL_VARIANT_EXPORT_ROWS))
    page = await get_family_structural_variants_page(
        session,
        context=context,
        page=1,
        page_size=limit,
        prioritize=prioritize,
        track_mode=False,
        **filters,
    )
    return page.variants[:limit]


def _structural_segregation_modes(annotation_extra: dict[str, Any]) -> list[str]:
    """Derive coarse segregation modes from the SV's annotated inheritance.

    SV calls carry an ``Inheritance`` annotation (de novo / maternal / paternal /
    inherited) rather than per-sample genotype segregation, so we map a de-novo call
    to the strong de-novo mode and an inherited call to the dominant mode. Anything
    else is left neutral (empty), matching the small-variant ``segregation_weight``
    semantics.
    """
    inheritance = (annotation_extra.get("inheritance") or "").strip().lower()
    if not inheritance:
        return []
    if "de" in inheritance and "novo" in inheritance:
        return [MODE_DE_NOVO]
    if any(token in inheritance for token in ("maternal", "paternal", "inherited")):
        return [MODE_DOMINANT]
    return []


async def _prioritized_structural_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
    page: int,
    page_size: int,
    selected_samples: Sequence[str],
) -> VariantPage:
    """Phenotype-aware ranking of structural variants (Exomiser-style).

    Mirrors ``_prioritized_small_variants_page``: fetch a candidate window, score each
    SV by event class + overlapped-gene constraint + rarity + segregation, blend with
    the best overlapped-gene HPO phenotype match, sort by combined score, then paginate.
    """
    include_regions: list[Region] = []
    panel_gene_terms: set[str] | None = None
    if filters.panel_id:
        panel_constraints = await _fetch_panel_constraints(
            session, filters.panel_id, assembly_id=context.assembly_id
        )
        if not panel_constraints.genes and not panel_constraints.regions:
            return VariantPage(total=0, variants=[], summary={})
        if panel_constraints.genes and len(panel_constraints.regions) > _PANEL_REGION_INLINE_LIMIT:
            panel_gene_terms = {gene.lower() for gene in panel_constraints.genes if gene}
        else:
            include_regions.extend(panel_constraints.regions)
            if not include_regions:
                return VariantPage(total=0, variants=[], summary={})
    if filters.gene:
        gene_regions = await _fetch_gene_regions(
            session, gene_query=filters.gene, assembly_id=context.assembly_id
        )
        if not gene_regions:
            return VariantPage(total=0, variants=[], summary={})
        include_regions.extend(gene_regions)

    review_variant_ids = await list_matching_structural_variant_review_ids(
        session,
        family_uuid=context.family_uuid,
        classifications=filters.review_classifications,
        tags=filters.review_tags,
        has_notes=filters.has_notes,
    )
    include_review_filter_active = bool(
        filters.review_classifications or filters.review_tags or filters.has_notes
    )
    if include_review_filter_active and not review_variant_ids:
        return VariantPage(total=0, variants=[], summary={})
    excluded_review_variant_ids = (
        await list_matching_structural_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            tags=filters.exclude_review_tags,
        )
        if filters.exclude_review_tags
        else set()
    )

    records = await _fetch_structural_variant_rows(
        context, filters, limit=_PRIORITIZE_CANDIDATE_LIMIT + 1
    )
    capped = len(records) > _PRIORITIZE_CANDIDATE_LIMIT
    if capped:
        records = records[:_PRIORITIZE_CANDIDATE_LIMIT]
    filtered = [
        record
        for record in records
        if record.variant_id not in excluded_review_variant_ids
        and ((not include_review_filter_active) or record.variant_id in review_variant_ids)
        and _structural_record_matches(
            record, filters, include_regions, selected_samples, panel_gene_terms=panel_gene_terms
        )
    ]
    if not filtered:
        return VariantPage(total=0, variants=[], summary={})

    affected_names, _unaffected = _family_affected_unaffected_sample_names(context)
    segregation_evaluated = bool(affected_names)
    patient_terms, term_labels = await _affected_present_hpo(session, context)

    # One phenotype-scoring pass over the union of every overlapped gene; each SV then
    # takes its best-matching overlapped gene.
    all_gene_symbols = {symbol for record in filtered for symbol in record.gene_symbols if symbol}
    phenotype_scores = (
        await score_genes_for_hpo(
            session, gene_symbols=all_gene_symbols, patient_hpo_ids=patient_terms
        )
        if patient_terms and all_gene_symbols
        else {}
    )

    scored_records: list[tuple[StructuralVariantRecord, VariantPriorityOut]] = []
    for record in filtered:
        annotation_extra = _structural_annotation_extra(record)
        gene_pli = annotation_extra.get("pli")
        gene_pli = float(gene_pli) if isinstance(gene_pli, (int, float)) else None

        best_gene: str | None = None
        best_gene_score = None
        for symbol in record.gene_symbols:
            candidate = phenotype_scores.get(symbol.upper()) if symbol else None
            if candidate and (best_gene_score is None or candidate.score > best_gene_score.score):
                best_gene_score = candidate
                best_gene = symbol
        phenotype_value = best_gene_score.score if best_gene_score else None

        scored = score_structural_variant(
            sv_type=record.sv_type,
            gene_count=len([symbol for symbol in record.gene_symbols if symbol]),
            control_af=_coerce_float(annotation_extra.get("control_af")),
            population_af=_coerce_float(annotation_extra.get("population_af")),
            segregation_modes=_structural_segregation_modes(annotation_extra),
            segregation_evaluated=segregation_evaluated,
            phenotype_score=phenotype_value,
            gene_pli=gene_pli,
        )
        phenotype_matches = (
            [
                MonarchPhenotypeMatchOut(
                    hpo_id=match["hpo_id"], label=term_labels.get(match["hpo_id"])
                )
                for match in best_gene_score.matched
            ]
            if best_gene_score
            else []
        )
        priority = VariantPriorityOut(
            combined_score=round(scored.combined_score, 4),
            variant_score=round(scored.variant_score, 4),
            pathogenicity_score=round(scored.pathogenicity, 4),
            frequency_score=round(scored.frequency, 4),
            segregation_weight=round(scored.segregation_weight, 4),
            phenotype_score=round(phenotype_value, 4) if phenotype_value is not None else None,
            segregation_modes=scored.segregation_modes,
            phenotype_gene=best_gene if best_gene_score else None,
            phenotype_matches=phenotype_matches,
        )
        scored_records.append((record, priority))

    scored_records.sort(
        key=lambda item: (item[1].combined_score, item[1].variant_score), reverse=True
    )
    for index, (_record, priority) in enumerate(scored_records, start=1):
        priority.rank = index

    summary: dict[str, dict[str, int]] = {}
    for record, _priority in scored_records:
        bucket = summary.setdefault(record.sv_type or "", {})
        source_key = record.source or ""
        bucket[source_key] = bucket.get(source_key, 0) + 1

    total = len(scored_records)
    skip = max(page - 1, 0) * page_size if page_size else 0
    page_items = scored_records[skip : skip + page_size] if page_size else scored_records[skip:]
    page_records = [record for record, _priority in page_items]
    review_map = await get_structural_variant_review_map(
        session,
        family_uuid=context.family_uuid,
        variant_ids=[record.variant_id for record in page_records],
    )
    cytoband_map = await _fetch_structural_cytoband_map(
        session, assembly_id=context.assembly_id, records=page_records
    )
    variants = []
    for record, priority in page_items:
        variant = _structural_variant_out(
            record,
            selected_samples,
            review_map.get(record.variant_id),
            cytoband_map.get(record.variant_id),
        )
        variant.priority = priority
        variants.append(variant)

    return VariantPage(
        total=total,
        total_is_estimated=capped,
        count_limit=_PRIORITIZE_CANDIDATE_LIMIT if capped else None,
        ranking_truncated=capped,
        variants=variants,
        summary=summary,
    )


async def get_family_structural_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    page: int,
    page_size: int,
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    length: int | None = None,
    min_length: int | None = None,
    type: str | None = None,
    source: str | None = None,
    sample_filters: list[str] | None = None,
    samples: list[str] | None = None,
    remote_chr: str | None = None,
    remote_start: int | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    inheritance: str | None = None,
    phenotype: str | None = None,
    hpo: str | None = None,
    moi: str | None = None,
    gencc_support: str | None = None,
    region_flags: list[str] | None = None,
    max_control_af: float | None = None,
    max_population_af: float | None = None,
    min_pli: float | None = None,
    review_classifications: list[str] | None = None,
    review_tags: list[str] | None = None,
    exclude_review_tags: list[str] | None = None,
    has_notes: bool = False,
    overlap: bool = False,
    prioritize: bool = False,
    track_mode: bool = False,
    count_only: bool = False,
) -> VariantPage:
    filters = StructuralVariantQueryFilters(
        page=page,
        page_size=page_size,
        chromosome=chr,
        start=start,
        end=end,
        length=length,
        min_length=min_length,
        variant_type=type,
        source=source,
        sample_filters=sample_filters or [],
        selected_samples=samples or [],
        remote_chr=remote_chr,
        remote_start=remote_start,
        gene=gene,
        panel_id=panel_id,
        inheritance=inheritance,
        phenotype=phenotype,
        hpo=hpo,
        moi=moi,
        gencc_support=gencc_support,
        region_flags=region_flags or [],
        max_control_af=max_control_af,
        max_population_af=max_population_af,
        min_pli=min_pli,
        review_classifications=review_classifications or [],
        review_tags=review_tags or [],
        exclude_review_tags=exclude_review_tags or [],
        has_notes=has_notes,
        overlap=overlap,
    )
    selected_samples = _selected_structural_samples(context, filters.selected_samples)
    if selected_samples is None:
        return VariantPage(total=0, variants=[], summary={})
    if count_only:
        # Presence check only (family dashboard): probe the indexed family_guid
        # column with LIMIT 1 instead of the structural summary GROUP BY scan.
        # The dashboard only needs total > 0 to decide whether to surface the
        # structural-variants workspace.
        exists = await _family_has_structural_variants(context)
        return VariantPage(total=1 if exists else 0, variants=[], summary={})
    if prioritize and not track_mode:
        return await _prioritized_structural_variants_page(
            session,
            context=context,
            filters=filters,
            page=page,
            page_size=page_size,
            selected_samples=selected_samples,
        )
    if _can_use_structural_native_page(filters, track_mode=track_mode):
        total, summary = await _fetch_structural_variant_summary(context, filters)
        if total == 0:
            return VariantPage(total=0, variants=[], summary={})
        page_records = await _fetch_structural_variant_rows(
            context,
            filters,
            limit=page_size,
            offset=_page_offset(page, page_size),
        )
        review_map = await get_structural_variant_review_map(
            session,
            family_uuid=context.family_uuid,
            variant_ids=[record.variant_id for record in page_records],
        )
        cytoband_map = await _fetch_structural_cytoband_map(
            session,
            assembly_id=context.assembly_id,
            records=page_records,
        )
        variants = [
            _structural_variant_out(
                record,
                selected_samples,
                review_map.get(record.variant_id),
                cytoband_map.get(record.variant_id),
            )
            for record in page_records
        ]
        return VariantPage(total=total, variants=variants, summary=summary)

    include_regions: list[Region] = []
    panel_gene_terms: set[str] | None = None
    if filters.panel_id:
        panel_constraints = await _fetch_panel_constraints(
            session, filters.panel_id, assembly_id=context.assembly_id
        )
        if not panel_constraints.genes and not panel_constraints.regions:
            return VariantPage(total=0, variants=[], summary={})
        if panel_constraints.genes and len(panel_constraints.regions) > _PANEL_REGION_INLINE_LIMIT:
            # Big gene panel: match SVs by gene symbol (fast) instead of overlapping each
            # against thousands of regions.
            panel_gene_terms = {gene.lower() for gene in panel_constraints.genes if gene}
        else:
            include_regions.extend(panel_constraints.regions)
            if not include_regions:
                return VariantPage(total=0, variants=[], summary={})
    if filters.gene:
        gene_regions = await _fetch_gene_regions(
            session,
            gene_query=filters.gene,
            assembly_id=context.assembly_id,
        )
        if not gene_regions:
            return VariantPage(total=0, variants=[], summary={})
        include_regions.extend(gene_regions)
    review_variant_ids = await list_matching_structural_variant_review_ids(
        session,
        family_uuid=context.family_uuid,
        classifications=filters.review_classifications,
        tags=filters.review_tags,
        has_notes=filters.has_notes,
    )
    include_review_filter_active = bool(
        filters.review_classifications or filters.review_tags or filters.has_notes
    )
    if include_review_filter_active and not review_variant_ids:
        return VariantPage(total=0, variants=[], summary={})
    excluded_review_variant_ids = (
        await list_matching_structural_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            tags=filters.exclude_review_tags,
        )
        if filters.exclude_review_tags
        else set()
    )
    records = await _fetch_structural_variant_rows(
        context, filters, limit=_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP + 1, track_mode=track_mode
    )
    total_is_estimated = len(records) > _SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP
    if total_is_estimated:
        records = records[:_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP]
    filtered = [
        record
        for record in records
        if record.variant_id not in excluded_review_variant_ids
        and ((not include_review_filter_active) or record.variant_id in review_variant_ids)
        and _structural_record_matches(
            record, filters, include_regions, selected_samples, panel_gene_terms=panel_gene_terms
        )
    ]
    summary: dict[str, dict[str, int]] = {}
    for record in filtered:
        summary.setdefault(record.sv_type or "", {})
        source_key = record.source or ""
        summary[record.sv_type or ""][source_key] = summary[record.sv_type or ""].get(source_key, 0) + 1
    total = len(filtered)
    skip = max(page - 1, 0) * page_size if page_size else 0
    page_records = filtered[skip: skip + page_size] if page_size else filtered[skip:]
    # The genome track shows neither review status nor cytoband, so skip those two
    # Postgres round-trips (over thousands of variant ids) when track_mode is on.
    review_map = (
        {}
        if track_mode
        else await get_structural_variant_review_map(
            session,
            family_uuid=context.family_uuid,
            variant_ids=[record.variant_id for record in page_records],
        )
    )
    cytoband_map = (
        {}
        if track_mode
        else await _fetch_structural_cytoband_map(
            session,
            assembly_id=context.assembly_id,
            records=page_records,
        )
    )
    variants = [
        _structural_variant_out(
            record,
            selected_samples,
            review_map.get(record.variant_id),
            cytoband_map.get(record.variant_id),
            track_mode=track_mode,
        )
        for record in page_records
    ]
    return VariantPage(
        total=0 if track_mode else total,
        total_is_estimated=False if track_mode else total_is_estimated,
        count_limit=(
            _SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP
            if (not track_mode and total_is_estimated)
            else None
        ),
        variants=variants,
        summary=None if track_mode else summary,
    )


async def get_family_compound_het_candidates(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variant_id: str,
    limit: int = 50,
) -> VariantPage:
    # Compound-het partners are always within the source variant's gene, so look
    # up the source variant first to learn its gene, then scope the partner scan
    # to that gene instead of pulling the whole family's small-variant set. The
    # gene filter matches a superset of records sharing the source's primary gene
    # key, and _compound_het_partner_map below recomputes the exact partners, so
    # the result is identical to scanning every variant.
    source_matches = await _fetch_small_variant_rows(
        context,
        SmallVariantQueryFilters(page=1, page_size=1),
        include_variant_ids=[variant_id],
        limit=1,
    )
    source_record = next(
        (record for record in source_matches if record.variant_id == variant_id), None
    )
    if source_record is None:
        return VariantPage(total=0, variants=[])
    source_gene, _source_gene_id = _primary_gene_keys(source_record)
    if source_gene:
        records = await _fetch_small_variant_rows(
            context, SmallVariantQueryFilters(page=1, page_size=1, gene=source_gene)
        )
    else:
        # No gene name to scope by (the partner key is then a bare gene_id, which
        # the fetch cannot filter on); fall back to the whole-family scan.
        records = await _fetch_small_variant_rows(
            context, SmallVariantQueryFilters(page=1, page_size=1)
        )
    affected_sample_names, unaffected_sample_names = _family_affected_unaffected_sample_names(context)
    partner_ids = _compound_het_partner_map(
        records,
        affected_samples=affected_sample_names,
        unaffected_samples=unaffected_sample_names,
    ).get(source_record.variant_id, set())
    if not partner_ids:
        return VariantPage(total=0, variants=[])
    candidates = [
        record
        for record in records
        if record.variant_id in partner_ids and record.variant_id != source_record.variant_id
    ][:limit]
    variants = [_small_variant_out(record) for record in candidates]
    await _hydrate_small_variant_outs(
        session,
        context=context,
        variants=variants,
    )
    return VariantPage(total=len(variants), variants=variants)
