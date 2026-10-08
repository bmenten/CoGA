"""Exomiser-style small-variant prioritization scoring.

Pure scoring math that combines a variant's predicted deleteriousness, rarity, and
segregation evidence with the gene's phenotype-match score into a single priority
score. The phenotype score comes from monarch_phenotype_score; segregation modes are
computed by the ClickHouse query layer using the existing pedigree helpers.

The score is for *ranking within a family*, not a calibrated probability. See
docs/monarch-integration.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# Segregation modes (compatible inheritance patterns given the pedigree).
MODE_DE_NOVO = "de_novo"
MODE_HOM_RECESSIVE = "homozygous_recessive"
MODE_COMPOUND_HET = "compound_het"
MODE_X_LINKED = "x_linked_recessive"
MODE_DOMINANT = "dominant"

_STRONG_MODES = frozenset(
    {MODE_DE_NOVO, MODE_HOM_RECESSIVE, MODE_COMPOUND_HET, MODE_X_LINKED}
)

# Tunables.
_SEG_WEIGHT_STRONG = 1.0
_SEG_WEIGHT_DOMINANT = 0.85
_SEG_WEIGHT_NONE = 0.6
# Weight of the phenotype term in the combined score. Like Exomiser, phenotype
# relevance reorders candidates: a variant in a phenotype-matched gene can outrank an
# equally deleterious variant in a phenotype-irrelevant gene. Genes with no Monarch
# phenotype data score 0 on the phenotype axis (shown explicitly in the UI, beside the
# variant score in the score breakdown); the variant score breaks ties in the ranking.
_PHENOTYPE_WEIGHT = 0.5
# Predictor-only evidence is capped below 1.0; a perfect score is reserved for ClinVar
# pathogenic assertions, so variant scores spread out instead of saturating.
_MAX_PREDICTOR_PATHOGENICITY = 0.9

_HIGH_IMPACT = "high"
# ClinVar significance is matched on whole words (splitting on the ,/&|_ and space
# delimiters), NOT substrings — so "Conflicting_interpretations_of_pathogenicity" does
# not match the "pathogenic" call (its only relevant token is "pathogenicity"). A
# conflicting assertion is excluded from both the pathogenic and benign shortcuts.
_CLINVAR_TOKEN_RE = re.compile(r"[a-z]+")

# AlphaMissense categorical calls (per missense variant).
_AM_PATHOGENIC = ("likely_pathogenic", "pathogenic")
_AM_BENIGN = ("likely_benign", "benign")
_AM_PATHOGENIC_SCORE = 0.85  # treat a likely-pathogenic AlphaMissense call like a strong predictor
_AM_BENIGN_CAP = 0.25  # AlphaMissense-benign missense is de-prioritized when nothing else flags it

# Gene-level constraint priors (gnomAD pLI for LoF intolerance, missense-Z for missense).
_PLI_CONSTRAINED = 0.9
_MISSENSE_Z_CONSTRAINED = 3.0
_CONSTRAINED_LOF_FLOOR = 0.9  # a LoF in a highly LoF-intolerant gene is high-confidence
_CONSTRAINED_MISSENSE_PRIOR = 0.6  # missense in a missense-constrained gene


def clinvar_may_assert_pathogenic(clinvar: str | Iterable[str] | None) -> bool:
    """True when a ClinVar significance carries, or may carry, a pathogenic or likely
    pathogenic assertion: a variant no automatic step may set aside.

    Read on whole words, as ``pathogenicity_score`` reads ClinVar: any "pathogenic" token
    counts -- Pathogenic, Likely_pathogenic, Pathogenic/Likely_pathogenic, "Pathogenic,
    low penetrance", a VEP CLIN_SIG list such as ``uncertain_significance&pathogenic`` --
    and "pathogenicity" does not. Unlike the score, a conflicting record counts too: a
    conflict can hold a P/LP submission, and only ClinVar's aggregate significance is
    imported (not its per-submission CLNSIGCONF breakdown), so no conflict can be shown
    to lack one. Takes a raw significance, or the normalised terms kept in
    ``clinvar_terms``.
    """
    values = [clinvar] if clinvar is None or isinstance(clinvar, str) else list(clinvar)
    for value in values:
        text = (value or "").strip().lower()
        if "conflict" in text or "pathogenic" in _CLINVAR_TOKEN_RE.findall(text):
            return True
    return False


@dataclass(slots=True)
class VariantScore:
    pathogenicity: float
    frequency: float
    segregation_weight: float
    variant_score: float
    phenotype_score: float | None
    combined_score: float
    segregation_modes: list[str] = field(default_factory=list)


def pathogenicity_score(
    *,
    impact: str | None,
    clinvar: str | None,
    cadd_phred: float | None,
    revel: float | None,
    spliceai_max: float | None,
    lof: str | None,
    alpha_missense_class: str | None = None,
    alpha_missense_pathogenicity: float | None = None,
    gene_pli: float | None = None,
    gene_missense_z: float | None = None,
) -> float:
    """Predicted deleteriousness in [0, 1].

    Combines impact, ClinVar, the per-variant predictors (CADD/REVEL/SpliceAI plus
    AlphaMissense), and gene-level constraint priors (pLI for LoF, missense-Z for
    missense). ClinVar pathogenic/benign assertions take precedence.
    """
    clin = (clinvar or "").strip().lower()
    clin_words = set(_CLINVAR_TOKEN_RE.findall(clin))
    if "conflict" not in clin and "pathogenic" in clin_words:
        return 1.0
    if "conflict" not in clin and "benign" in clin_words:
        return 0.05

    impact_value = (impact or "").strip().lower()
    lof_value = (lof or "").strip().upper()
    is_lof = impact_value == _HIGH_IMPACT or lof_value == "HC"
    if is_lof:
        base = 0.85
    elif impact_value == "moderate":
        base = 0.4
    elif impact_value == "low":
        base = 0.15
    else:
        base = 0.05

    # Gene constraint priors raise confidence for the relevant variant class. pLI is a
    # probability in [0, 1]; out-of-range values are ignored as malformed source data.
    if (
        is_lof
        and gene_pli is not None
        and _PLI_CONSTRAINED <= gene_pli <= 1.0
    ):
        base = max(base, _CONSTRAINED_LOF_FLOOR)

    predictors = [base]
    if revel is not None:
        predictors.append(max(0.0, min(1.0, revel)))
    if cadd_phred is not None:
        predictors.append(max(0.0, min(1.0, cadd_phred / 40.0)))
    if spliceai_max is not None:
        predictors.append(max(0.0, min(1.0, spliceai_max)))
    if alpha_missense_pathogenicity is not None:
        predictors.append(max(0.0, min(1.0, alpha_missense_pathogenicity)))

    am_class = (alpha_missense_class or "").strip().lower()
    if am_class in _AM_PATHOGENIC:
        predictors.append(_AM_PATHOGENIC_SCORE)
    if (
        not is_lof
        and gene_missense_z is not None
        and gene_missense_z >= _MISSENSE_Z_CONSTRAINED
    ):
        predictors.append(_CONSTRAINED_MISSENSE_PRIOR)

    score = min(_MAX_PREDICTOR_PATHOGENICITY, max(predictors))

    # AlphaMissense-benign missense, with nothing else strongly flagging it, is
    # de-prioritized — this is the main missense-sharpening signal.
    if am_class in _AM_BENIGN and not is_lof:
        score = min(score, _AM_BENIGN_CAP)
    return score


def frequency_score(
    *, gnomad_popmax_af: float | None, gnomad_af: float | None
) -> float:
    """Rarity score in [0, 1] — rarer is higher. Uses popmax, falling back to AF."""
    af = gnomad_popmax_af if gnomad_popmax_af is not None else gnomad_af
    if af is None or af <= 1e-6:
        return 1.0
    if af < 1e-4:
        return 0.9
    if af < 1e-3:
        return 0.7
    if af < 1e-2:
        return 0.4
    if af < 5e-2:
        return 0.1
    return 0.0


def segregation_weight(modes: list[str], *, evaluated: bool = True) -> float:
    # When segregation cannot be evaluated (no affected individuals in the pedigree),
    # it is neutral rather than a penalty — otherwise every variant would be uniformly
    # down-weighted while phenotype is still scored, skewing the ranking.
    if not evaluated:
        return _SEG_WEIGHT_STRONG
    if any(mode in _STRONG_MODES for mode in modes):
        return _SEG_WEIGHT_STRONG
    if MODE_DOMINANT in modes:
        return _SEG_WEIGHT_DOMINANT
    return _SEG_WEIGHT_NONE


def combine(variant_score: float, phenotype_score: float | None) -> float:
    """Weighted combination of variant and phenotype evidence, in [0, 1].

    Phenotype relevance reorders candidates (Exomiser-style): a phenotype-matched gene
    can outrank an equally deleterious variant in an unrelated gene. A gene with no
    Monarch phenotype data scores 0 on the phenotype axis; the raw ``variant_score`` is
    shown separately in the score breakdown and breaks ties between equal combined scores.
    """
    phenotype = phenotype_score or 0.0
    return (1.0 - _PHENOTYPE_WEIGHT) * variant_score + _PHENOTYPE_WEIGHT * phenotype


# --- Structural / copy-number variant scoring -------------------------------
#
# SVs lack the per-variant predictors (CADD/REVEL/SpliceAI) used above, so their
# predicted impact is derived from the event class, the number of overlapped genes,
# and gene-level constraint (a copy-number loss of a LoF-intolerant gene mimics
# haploinsufficiency). Frequency, segregation, and phenotype combine exactly like
# small variants, so the combined score is directly comparable for ranking.
_SV_TYPE_BASE = {
    "DEL": 0.7,
    "LOSS": 0.7,
    "CNV_LOSS": 0.7,
    "DUP": 0.5,
    "GAIN": 0.5,
    "CNV_GAIN": 0.5,
    "INS": 0.5,
    "INV": 0.45,
    "BND": 0.6,
    "TRA": 0.6,
}
_SV_DEFAULT_BASE = 0.4
# An SV that overlaps no gene is most likely non-coding / benign for ranking purposes.
_SV_NO_GENE_CAP = 0.15
# Copy-number loss event types where a high-pLI overlap implies haploinsufficiency.
_SV_LOSS_TYPES = frozenset({"DEL", "LOSS", "CNV_LOSS"})
_SV_MULTI_GENE_THRESHOLD = 3
_SV_MULTI_GENE_BUMP = 0.1


def structural_pathogenicity_score(
    *,
    sv_type: str | None,
    gene_count: int,
    gene_pli: float | None = None,
) -> float:
    """Predicted impact of a structural variant in [0, 1]."""
    if gene_count <= 0:
        return _SV_NO_GENE_CAP
    sv_upper = (sv_type or "").strip().upper()
    score = _SV_TYPE_BASE.get(sv_upper, _SV_DEFAULT_BASE)
    if (
        sv_upper in _SV_LOSS_TYPES
        and gene_pli is not None
        and _PLI_CONSTRAINED <= gene_pli <= 1.0
    ):
        score = max(score, _CONSTRAINED_LOF_FLOOR)
    if gene_count >= _SV_MULTI_GENE_THRESHOLD:
        score = score + _SV_MULTI_GENE_BUMP
    return min(_MAX_PREDICTOR_PATHOGENICITY, score)


def score_structural_variant(
    *,
    sv_type: str | None,
    gene_count: int,
    control_af: float | None,
    population_af: float | None,
    segregation_modes: list[str],
    phenotype_score: float | None,
    segregation_evaluated: bool = True,
    gene_pli: float | None = None,
) -> VariantScore:
    """Rank-oriented score for a structural variant (see module docstring)."""
    pathogenicity = structural_pathogenicity_score(
        sv_type=sv_type, gene_count=gene_count, gene_pli=gene_pli
    )
    # Rarer is more interesting: prefer the population AF, fall back to the cohort
    # control AF (reusing the small-variant rarity bands).
    frequency = frequency_score(gnomad_popmax_af=population_af, gnomad_af=control_af)
    seg_weight = segregation_weight(segregation_modes, evaluated=segregation_evaluated)
    variant_score = pathogenicity * frequency * seg_weight
    combined = combine(variant_score, phenotype_score)
    return VariantScore(
        pathogenicity=pathogenicity,
        frequency=frequency,
        segregation_weight=seg_weight,
        variant_score=variant_score,
        phenotype_score=phenotype_score,
        combined_score=combined,
        segregation_modes=list(segregation_modes),
    )


def score_variant(
    *,
    impact: str | None,
    clinvar: str | None,
    cadd_phred: float | None,
    revel: float | None,
    spliceai_max: float | None,
    lof: str | None,
    gnomad_popmax_af: float | None,
    gnomad_af: float | None,
    segregation_modes: list[str],
    phenotype_score: float | None,
    segregation_evaluated: bool = True,
    alpha_missense_class: str | None = None,
    alpha_missense_pathogenicity: float | None = None,
    gene_pli: float | None = None,
    gene_missense_z: float | None = None,
) -> VariantScore:
    pathogenicity = pathogenicity_score(
        impact=impact,
        clinvar=clinvar,
        cadd_phred=cadd_phred,
        revel=revel,
        spliceai_max=spliceai_max,
        lof=lof,
        alpha_missense_class=alpha_missense_class,
        alpha_missense_pathogenicity=alpha_missense_pathogenicity,
        gene_pli=gene_pli,
        gene_missense_z=gene_missense_z,
    )
    frequency = frequency_score(gnomad_popmax_af=gnomad_popmax_af, gnomad_af=gnomad_af)
    seg_weight = segregation_weight(segregation_modes, evaluated=segregation_evaluated)
    variant_score = pathogenicity * frequency * seg_weight
    combined = combine(variant_score, phenotype_score)
    return VariantScore(
        pathogenicity=pathogenicity,
        frequency=frequency,
        segregation_weight=seg_weight,
        variant_score=variant_score,
        phenotype_score=phenotype_score,
        combined_score=combined,
        segregation_modes=list(segregation_modes),
    )
