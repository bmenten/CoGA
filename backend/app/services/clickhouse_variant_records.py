from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence


from .variant_annotation_parser import _spliceai_delta


_KNOWN_ANNOTATION_KEYS = {
    # The ALT a multi-allelic record's annotation is of, served as a field of its own.
    "alt_allele",
    # Served as fields of their own: ClinVar's review status, conditions and VariationID, the
    # NMD prediction and SpliceAI's positions.
    "clinvar_review_status",
    "clinvar_conditions",
    "clinvar_variation_id",
    "nmd",
    "spliceai_dp_ag",
    "spliceai_dp_al",
    "spliceai_dp_dg",
    "spliceai_dp_dl",
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


# Frozen: a panel's resolution, these regions included, is kept and handed to every
# request that applies the panel (clickhouse_family_variants._fetch_panel_constraints).
@dataclass(frozen=True, slots=True)
class Region:
    chr: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
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
    # The call's own FILTER values and caller metrics (TLOD, FS, MMQ, ...), kept when the
    # VCF holds this one sample: its record's FILTER and INFO then describe this call alone
    # (a per-sample callset, such as the cfDNA and paternal files of a monogenic NIPT).
    # Empty for a call of a multi-sample VCF, whose FILTER and INFO describe the site.
    filters: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SmallSegregationFlags:
    """Which single-variant inheritance modes a variant fits in its family, as the SQL decides
    them (``_small_segregation_flag_columns``), with the clauses the search's inheritance
    filter applies: one definition of each mode."""

    de_novo_dominant: bool
    recessive_homozygous: bool
    x_linked: bool


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
    # The inheritance modes the variant fits, when the fetch was asked for them
    # (``_fetch_small_variant_rows(segregation_flags=True)``); None otherwise.
    segregation: SmallSegregationFlags | None = None
    # Its annotation set as stored (the annotation version and the set's hash), by which its
    # transcripts' stored rows are found (``_filter_transcript_matches``); None for a record
    # not read from the store.
    annotation_version: str | None = None
    annotation_set_hash: int | None = None


@dataclass(slots=True)
class SmallVariantCompoundHetPair:
    pair_key: str
    gene: str | None
    gene_id: str | None
    left: SmallVariantRecord
    right: SmallVariantRecord
    # "trans" when the caller placed the two alts on opposite haplotypes of one phase
    # set, or the parents' genotypes trace one to each parent; "unknown" when neither
    # resolves them. Cis pairs are excluded upstream, so they never appear here.
    phase: str = "unknown"
    # How a trans phase was established: "read" (one phase set) or "segregation" (the
    # parents); None while the phase is unknown.
    phase_evidence: str | None = None


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
    # Written by the write paths (the details-table remoteEnd) and read back by the storage
    # read they rewrite from (StoredStructuralVariantRow). The family view does not fetch
    # it (never surfaced by _structural_variant_out), so view-built records leave it None.
    remote_end: int | None
    sv_len: int | None
    filters: list[str]
    gene_symbols: list[str]
    annotations: list[dict[str, Any]]
    calls: list[StructuralVariantCall]


@dataclass(slots=True)
class StoredStructuralVariantRow:
    """One live row of a family's ``SV/entries`` exactly as stored, with its details.

    A write path that rewrites a family's SVs to change one sample's calls (the per-sample
    upload, the admin per-sample delete) starts from these rather than from the family
    view, so everything it does not mean to change is written back as it was: the project
    the row is written under, every call (samples the view does not show included, under
    the sample id they were stored with), each call's phase set and copy number, the
    genotype as stored, and the breakend's remote end.
    """

    project_id: str
    record: StructuralVariantRecord


# What separates the terms of a ClinVar value (``_status_terms``) and of a consequence
# (``_annotation_terms``), compiled once: they are read for every annotation stored.
_STATUS_TERM_SEPARATORS = re.compile(r"[,;&|/]+")
_ANNOTATION_TERM_SEPARATORS = re.compile(r"[,|&;/]+")


def _casefold(value: Any) -> str:
    return str(value or "").strip().casefold()


def _contains_casefold(value: Any, needle: str | None) -> bool:
    if not needle:
        return True
    return _casefold(needle) in _casefold(value)


def _normalized_status_term(value: Any) -> str:
    return " ".join(_casefold(value).replace("_", " ").split())


# ClinVar significances that "rescue" a variant past the gnomAD frequency ceilings when
# the caller opts in (``clinvar_overrides_frequency``), as the ClickHouse filter reads them
# (#534).
CLINVAR_FREQUENCY_RESCUE_TERMS: tuple[str, ...] = ("Pathogenic", "Likely_pathogenic")

# The transcripts *MANE only* keeps: MANE Select and MANE Plus Clinical. Each flag is an
# annotation key (or its camelCase alias) and a column of the ``annotations`` table; the
# ``annotation_index`` holds it per variant as ``has_<flag>``. The family search, in SQL
# and in Python, and the Variant Explorer read this one list, so they cannot disagree: the
# explorer had read MANE Select alone, and dropped a variant whose MANE transcript is MANE
# Plus Clinical (CLIN-5).
MANE_ONLY_TRANSCRIPT_FLAGS: tuple[tuple[str, str], ...] = (
    ("mane_select", "maneSelect"),
    ("mane_plus_clinical", "manePlusClinical"),
)


def mane_only_condition(column_prefix: str) -> str:
    """*MANE only* as a ClickHouse condition: one of the MANE flags is set.

    ``column_prefix`` precedes each flag's name: ``"a."`` for a transcript row of the
    ``annotations`` table, ``"ai.has_"`` for a variant row of the ``annotation_index``.
    """
    columns = (f"{column_prefix}{flag}" for flag, _alias in MANE_ONLY_TRANSCRIPT_FLAGS)
    return f"({' OR '.join(columns)})"


def _status_terms(value: Any) -> set[str]:
    """Normalised status terms of a ClinVar value, as the import stores them for the SQL to
    read (``clinvar_terms``).

    Multiple values are separated by ``,`` ``;`` ``&`` ``|`` or ``/``. The ``/`` is
    ClinVar's own aggregate form (``Pathogenic/Likely_pathogenic``,
    ``Benign/Likely_benign``) as carried in CLNSIG: without splitting it, such a variant
    matched neither a "Pathogenic" / "Likely pathogenic" filter nor the frequency
    rescue (#534).
    """
    if isinstance(value, (list, tuple, set)):
        terms: set[str] = set()
        for item in value:
            terms.update(_status_terms(item))
        return terms
    return {
        normalized
        for part in _STATUS_TERM_SEPARATORS.split(str(value or ""))
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


def _coerce_int(value: Any) -> int | None:
    if value in (None, "", "."):
        return None
    # OverflowError: an infinite value ("inf", float("inf")) is no whole number either.
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "."):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _call_metrics(value: Any) -> dict[str, float]:
    """A stored call's caller metrics (a ClickHouse map) as name -> float; {} for none."""
    if not isinstance(value, dict):
        return {}
    metrics: dict[str, float] = {}
    for name, raw in value.items():
        number = _coerce_float(raw)
        if number is not None and str(name):
            metrics[str(name)] = number
    return metrics


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
        for term in _ANNOTATION_TERM_SEPARATORS.split(str(value))
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


