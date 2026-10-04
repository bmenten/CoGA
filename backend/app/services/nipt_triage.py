"""Monogenic NIPT: the de novo triage and the recessive fetal risk (pure, no I/O).

**De novo triage** (the R NIPT-M v0.5.1 pipeline's, paternal_only_recall.R). A de novo
candidate is a cfDNA call that passes the quality filter, where the father has no
supported call (no call, or a low-level or thin one), in the family's fetal window: the
5th to 95th percentile of the allele fractions of the paternal alleles the fetus inherited
(the strict window), widened by a quarter (the loose window), both within 0.5% to 35%.
A fetal allele the mother does not carry sits there whether it came from the father or
arose de novo. Each candidate is scored:

- base: 8 in the strict window, 6 in the loose one;
- annotation: impact HIGH +4 or MODERATE +2, SpliceAI >= 0.2 +2, novel (no rsID) +1, very
  rare (population AF <= 0.1% or unknown) +1, common (> 1%) -3;
- technical, each +1 when met and -1 when not (a missing value is met): FILTER PASS,
  alt mapping quality >= 55, strand bias FS <= 10, mismatches <= 2, repeat units <= 2, and
  no other cfDNA sample carrying the allele; a low-level paternal signal -2;
- class: SNV +1, indel -1, MNV -2, and an indel in a repeat (>= 3 units) -2 more.

High priority: the strict window, an SNV or indel outside a repeat, no paternal signal and
a score of 10 or more. Medium: a score of 7 or more. Low: any other candidate in the
window. An allele another cfDNA sample carries is excluded as recurrent. In the R
validation most shortlisted (high and medium) candidates were not true de novo variants:
the triage ranks; the annotation filters narrow.

**Recessive fetal risk** (CoGA's own; the R pipeline has no gene-level logic). In a gene
where the mother is a heterozygous carrier of an allele and the father of one, the fetus is
affected when it inherited one of each (assuming both are pathogenic and the maternal
alleles in trans of the paternal ones). A parent homozygous for an allele is not a carrier
(for a recessive disease such a parent would be affected; mostly it is a common variant),
so homozygous parental sites are left out. For a maternal allele m and a paternal allele p at different sites
the inheritances are independent meioses, so P(affected via m, p) =
P(m inherited) x P(p inherited); at a site both parents carry it is P(fetus hom-alt). The
gene's risk is the highest of these over its alleles; a carrier couple's prior is 25%.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .nipt_analysis import (
    FATHER_CALL_OVERLAPS,
    FetalFractionEstimate,
    NiptClassification,
    NiptSiteObservation,
    has_own_plasma_call,
)

# R NIPT-M v0.5.1 de novo triage settings (config de_novo_triage and the hard-coded weights).
_WINDOW_MIN_VAF = 0.005
_WINDOW_MAX_VAF = 0.35
_LOOSE_LOW_FACTOR = 0.75
_LOOSE_HIGH_FACTOR = 1.25
_BASE_STRICT = 8
_BASE_LOOSE = 6
_IMPACT_SCORES = {"HIGH": 4, "MODERATE": 2}
_SPLICEAI_MIN = 0.2
_VERY_RARE_MAX_AF = 0.001
_COMMON_MIN_AF = 0.01
_MIN_ALT_MAPPING_QUALITY = 55.0
_MAX_STRAND_BIAS_FS = 10.0  # SBF = 10^(-FS/10) >= 0.1
_MAX_MISMATCHES = 2.0
_MAX_REPEAT_UNITS = 2.0
_REPEAT_INDEL_MIN_UNITS = 3.0
_MAX_OTHER_CFDNA_CARRIERS = 0  # R: in at most 1 family, the index family included
_PATERNAL_UNCERTAIN_PENALTY = -2
_REPEAT_INDEL_PENALTY = -2
_CLASS_SCORES = {"SNV": 1, "indel": -1, "MNV": -2}
_HIGH_PRIORITY_CLASSES = frozenset({"SNV", "indel"})
HIGH_PRIORITY_MIN_SCORE = 10
MEDIUM_PRIORITY_MIN_SCORE = 7

# The father's classes that leave a cfDNA call without paternal support.
_UNSUPPORTED_FATHER_STATES = frozenset({"absent", "hom_ref", "low_vaf", "low_support", "missing"})
_UNCERTAIN_FATHER_STATES = frozenset({"low_vaf", "low_support", "missing"})

DE_NOVO_LABELS = ("high", "medium", "low", "excluded_recurrent", "outside_window")


@dataclass(slots=True)
class DeNovoWindow:
    """The family's fetal window (allele fractions of a fetal allele the mother lacks)."""

    strict_min: float
    strict_max: float
    loose_min: float
    loose_max: float


def de_novo_window(ff: FetalFractionEstimate) -> DeNovoWindow | None:
    """The strict window is the 5th-95th percentile of the FF sites' allele fractions, the
    loose one a quarter wider, both within 0.5% and 35%; None without an FF estimate."""
    if ff.vaf_q05 is None or ff.vaf_q95 is None or ff.ff_computed is None:
        return None
    return DeNovoWindow(
        strict_min=ff.vaf_q05,
        strict_max=min(_WINDOW_MAX_VAF, ff.vaf_q95),
        loose_min=max(_WINDOW_MIN_VAF, _LOOSE_LOW_FACTOR * ff.vaf_q05),
        loose_max=min(_WINDOW_MAX_VAF, _LOOSE_HIGH_FACTOR * ff.vaf_q95),
    )


@dataclass(slots=True)
class DeNovoAnnotation:
    """What the triage reads of a variant's annotation: its most severe impact, highest
    SpliceAI delta score, highest population allele frequency, whether it is known (an
    rsID), and whether a ClinVar record may assert it pathogenic."""

    impact: str | None = None
    spliceai_max: float | None = None
    max_population_af: float | None = None
    is_novel: bool = True
    clinvar_pathogenic: bool = False


@dataclass(slots=True)
class DeNovoTriage:
    window: str  # strict | loose | below | above
    score: int
    label: str  # DE_NOVO_LABELS
    reasons: list[str] = field(default_factory=list)
    other_cfdna_carriers: int = 0


def is_de_novo_candidate(site: NiptSiteObservation, classification: NiptClassification) -> bool:
    """A cfDNA call of the site's own (not a coverage-only site, nor one read off another
    representation) that passes the quality filter, where the father has no supported
    call. The triage's window decides the rest."""
    if not site.is_autosomal or not has_own_plasma_call(site):
        return False
    if classification.quality_failures:
        return False
    return site.father_state in _UNSUPPORTED_FATHER_STATES


def _metric(site: NiptSiteObservation, *names: str) -> float | None:
    for name in names:
        value = (site.cf_metrics or {}).get(name)
        if value is not None:
            return value
    return None


def _repeat_units(site: NiptSiteObservation) -> float | None:
    """Mutect2: the reference's repeat-unit count at a short tandem repeat (1 elsewhere);
    VarDict: MSI."""
    if (site.cf_metrics or {}).get("STR"):
        return _metric(site, "RPA_REF")
    vardict = _metric(site, "MSI")
    if vardict is not None:
        return vardict
    return 1.0 if site.cf_metrics else None


def triage_de_novo(
    site: NiptSiteObservation,
    window: DeNovoWindow | None,
    annotation: DeNovoAnnotation,
    *,
    other_cfdna_carriers: int = 0,
) -> DeNovoTriage | None:
    """Score a de novo candidate (``is_de_novo_candidate``); None without a window."""
    if window is None:
        return None
    vaf = site.cf_vaf if site.cf_vaf is not None else (
        (site.cf_alt_reads or 0) / site.cf_dp if site.cf_dp else 0.0
    )
    if window.strict_min <= vaf <= window.strict_max:
        position, base = "strict", _BASE_STRICT
    elif window.loose_min <= vaf <= window.loose_max:
        position, base = "loose", _BASE_LOOSE
    else:
        position, base = ("below" if vaf < window.loose_min else "above"), 0
    reasons: list[str] = []

    annotation_score = _IMPACT_SCORES.get((annotation.impact or "").upper(), 0)
    if annotation_score:
        reasons.append(f"impact {annotation.impact}")
    if annotation.spliceai_max is not None and annotation.spliceai_max >= _SPLICEAI_MIN:
        annotation_score += 2
        reasons.append("SpliceAI >= 0.2")
    if annotation.is_novel:
        annotation_score += 1
        reasons.append("novel")
    af = annotation.max_population_af
    if af is None or af <= _VERY_RARE_MAX_AF:
        annotation_score += 1
        reasons.append("very rare")
    if af is not None and af > _COMMON_MIN_AF:
        annotation_score -= 3
        reasons.append("common in the population")

    def met(condition: bool, failure: str) -> int:
        if not condition:
            reasons.append(failure)
        return 1 if condition else -1

    mapping_quality = _metric(site, "MMQ", "MQ")
    mismatches = _metric(site, "NM")
    repeat_units = _repeat_units(site)
    unique = other_cfdna_carriers <= _MAX_OTHER_CFDNA_CARRIERS
    technical = (
        met(not site.cf_filters or site.cf_filters == ["PASS"], "caller FILTER not PASS")
        + met(mapping_quality is None or mapping_quality >= _MIN_ALT_MAPPING_QUALITY, "low alt mapping quality")
        + met(site.cf_fs is None or site.cf_fs <= _MAX_STRAND_BIAS_FS, "strand bias")
        + met(mismatches is None or mismatches <= _MAX_MISMATCHES, "many mismatches")
        + met(repeat_units is None or repeat_units <= _MAX_REPEAT_UNITS, "repeat context")
        + met(unique, f"in {other_cfdna_carriers} other cfDNA sample(s)")
    )
    paternal_signal = site.father_state in _UNCERTAIN_FATHER_STATES
    if paternal_signal:
        technical += _PATERNAL_UNCERTAIN_PENALTY
        reasons.append("paternal low-level or uncertain call")
    elif site.representation == FATHER_CALL_OVERLAPS:
        # The father calls part of the allele (another MNV, or one of its SNVs).
        paternal_signal = True
        technical += _PATERNAL_UNCERTAIN_PENALTY
        reasons.append("the father has a call over part of the allele")
    effective_class = site.variant_class
    class_score = _CLASS_SCORES.get(effective_class, 0)
    repeat_indel = (
        effective_class == "indel" and repeat_units is not None and repeat_units >= _REPEAT_INDEL_MIN_UNITS
    )
    if repeat_indel:
        class_score += _REPEAT_INDEL_PENALTY
        reasons.append("indel in a repeat")
    score = base + annotation_score + technical + class_score
    eligible_for_high = effective_class in _HIGH_PRIORITY_CLASSES and not repeat_indel and not paternal_signal

    in_window = position in ("strict", "loose")
    # A known pathogenic de novo hotspot recurs between pregnancies tested on one panel:
    # the R pipeline's recurrence exclusion (a research triage) would hide it.
    if not unique and annotation.clinvar_pathogenic:
        reasons.append("ClinVar pathogenic: not excluded as recurrent")
    if in_window and not unique and not annotation.clinvar_pathogenic:
        label = "excluded_recurrent"
    elif position == "strict" and eligible_for_high and score >= HIGH_PRIORITY_MIN_SCORE:
        label = "high"
    elif in_window and score >= MEDIUM_PRIORITY_MIN_SCORE:
        label = "medium"
    elif in_window:
        label = "low"
    else:
        label = "outside_window"
    return DeNovoTriage(
        window=position,
        score=score,
        label=label,
        reasons=reasons,
        other_cfdna_carriers=other_cfdna_carriers,
    )


# --------------------------------------------------------------------------- #
# Recessive fetal risk
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class RecessiveAllele:
    """A parent's allele in a gene and whether the fetus inherited it (None: not told)."""

    variant_id: str
    inherited_probability: float | None
    category: int | None
    note: str | None = None


@dataclass(slots=True)
class RecessiveGeneRisk:
    gene: str
    maternal: list[RecessiveAllele]
    paternal: list[RecessiveAllele]
    # P(the fetus inherited a maternal and a paternal allele), the highest over the
    # gene's allele pairs; None when no pair's inheritances could both be read.
    risk: float | None
    maternal_variant_id: str | None
    paternal_variant_id: str | None
    # A pair's probability read with the 1/2 prior for an inheritance the cfDNA did not tell.
    risk_uses_prior: bool = False


@dataclass(slots=True)
class RecessiveCandidate:
    """A carrier variant the recessive view groups: its genes, the father's trusted
    genotype, the site's classification, and what its annotation says of its rarity."""

    variant_id: str
    genes: Sequence[str]
    father_state: str
    classification: NiptClassification
    variant_class: str = "SNV"
    # False when the annotation has no population frequency at all.
    has_population_af: bool = True


def _frequency_note(candidate: RecessiveCandidate) -> str | None:
    """A frequency filter lets a variant without a population frequency through. An MNV
    has none (gnomAD lists its SNVs) and is often a common haplotype."""
    if candidate.has_population_af:
        return None
    if candidate.variant_class == "MNV":
        return "MNV without a population frequency (gnomAD lists its SNVs)"
    return "no population frequency"


def _notes(*notes: str | None) -> str | None:
    return "; ".join(note for note in notes if note) or None


def _maternal_allele(candidate: RecessiveCandidate) -> RecessiveAllele | None:
    """The mother's allele when she is a heterozygous carrier (categories 2-4)."""
    classification = candidate.classification
    if classification.maternal_state != "het":
        return None
    model_note = None
    if "maternal_inference_indel" in classification.flags:
        model_note = "indel: the maternal model is less accurate"
    if "allele_balance_outlier" in classification.flags:
        model_note = "allele balance fits no fetal genotype"
    return RecessiveAllele(
        variant_id=candidate.variant_id,
        inherited_probability=classification.maternal_allele_probability,
        category=classification.category,
        note=_notes(model_note, _frequency_note(candidate)),
    )


def _paternal_allele(candidate: RecessiveCandidate) -> RecessiveAllele | None:
    """The father's allele when he is a heterozygous carrier."""
    if candidate.father_state != "het":
        return None
    classification = candidate.classification
    probability: float | None
    if classification.maternal_state == "het":
        # Both parents carry it: a heterozygous fetus has either parent's allele.
        genotype = classification.fetal_genotype_posterior
        if genotype is not None:
            probability = genotype.get("hom_alt", 0.0) + 0.5 * genotype.get("het", 0.0)
        else:
            probability = classification.fetal_hom_alt_probability
    elif classification.maternal_state == "hom":
        # The mother always transmits the allele: the father's is in a homozygous fetus.
        probability = classification.fetal_hom_alt_probability
    else:
        probability = classification.paternal_transmission_probability
    return RecessiveAllele(
        variant_id=candidate.variant_id,
        inherited_probability=probability,
        category=classification.category,
        note=_frequency_note(candidate),
    )


def recessive_gene_risks(candidates: Sequence[RecessiveCandidate]) -> list[RecessiveGeneRisk]:
    """The genes where the mother and the father each carry an allele among
    ``candidates``, with the fetal risk, highest first."""
    maternal_by_gene: dict[str, list[RecessiveAllele]] = {}
    paternal_by_gene: dict[str, list[RecessiveAllele]] = {}
    shared_hom_alt: dict[str, dict[str, float | None]] = {}
    for candidate in candidates:
        maternal = _maternal_allele(candidate)
        paternal = _paternal_allele(candidate)
        for gene in dict.fromkeys(gene.upper() for gene in candidate.genes if gene):
            if maternal is not None:
                maternal_by_gene.setdefault(gene, []).append(maternal)
            if paternal is not None:
                paternal_by_gene.setdefault(gene, []).append(paternal)
            if maternal is not None and paternal is not None:
                shared_hom_alt.setdefault(gene, {})[candidate.variant_id] = (
                    candidate.classification.fetal_hom_alt_probability
                )
    risks: list[RecessiveGeneRisk] = []
    for gene in sorted(set(maternal_by_gene) & set(paternal_by_gene)):
        best: tuple[float, str, str, bool] | None = None
        for maternal in maternal_by_gene[gene]:
            for paternal in paternal_by_gene[gene]:
                if maternal.variant_id == paternal.variant_id:
                    value = shared_hom_alt.get(gene, {}).get(maternal.variant_id)
                    uses_prior = value is None
                    probability = 0.25 if value is None else value
                else:
                    uses_prior = maternal.inherited_probability is None or paternal.inherited_probability is None
                    probability = (
                        (0.5 if maternal.inherited_probability is None else maternal.inherited_probability)
                        * (0.5 if paternal.inherited_probability is None else paternal.inherited_probability)
                    )
                if best is None or probability > best[0]:
                    best = (probability, maternal.variant_id, paternal.variant_id, uses_prior)
        risks.append(
            RecessiveGeneRisk(
                gene=gene,
                maternal=maternal_by_gene[gene],
                paternal=paternal_by_gene[gene],
                risk=best[0] if best is not None else None,
                maternal_variant_id=best[1] if best is not None else None,
                paternal_variant_id=best[2] if best is not None else None,
                risk_uses_prior=best[3] if best is not None else False,
            )
        )
    risks.sort(key=lambda item: (-(item.risk if item.risk is not None else -1.0), item.gene))
    return risks
