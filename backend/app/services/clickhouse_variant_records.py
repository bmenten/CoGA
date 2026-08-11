from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence


from .variant_annotation_parser import _spliceai_delta
from .family_variant_filters import (
    SmallVariantQueryFilters,
)


logger = logging.getLogger(__name__)


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
    # "trans" when the caller placed the two alts on opposite haplotypes of one phase
    # set, "unknown" when no shared phase block resolves them. Cis pairs are excluded
    # upstream, so they never appear here.
    phase: str = "unknown"


@dataclass(slots=True)
class StructuralVariantCall:
    sample: str
    gt: str
    qual: float | None
    read_support: int | None
    filter: str | None
    # Phase set (PS) for read-based cis/trans against a phased SNV; None when unphased.
    phase_set: int | None = None
    # Copy number (FORMAT/CN) for depth-based CNV callers. GT alone cannot distinguish
    # a 3-copy from a 6-copy duplication, and the ClinGen CNV dosage scoring needs the
    # actual copy number. None for callers that do not report one.
    copy_number: int | None = None


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
