"""Monogenic NIPT: the de novo triage and the recessive fetal risk (pure, no I/O).

**De novo triage** (the R NIPT-M v0.7.3 pipeline's, paternal_only_recall.R). A de novo
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

**Recessive fetal status** (the R NIPT-M v0.7.3 gene pairing, with CoGA's probabilities). The
mother carries an allele when the plasma calls it in her band at 25% or more (``mother_carries``),
the father when his genotype is het or hom-alt. In a
gene where the mother carries an allele and the father one, the fetus is affected when it
inherited one of each (assuming both are pathogenic and the maternal alleles in trans of the
paternal ones). A homozygous (or, on a man's X, hemizygous) parent always passes the allele, as
the R pairing reads it. For a maternal allele m and a paternal allele p at different sites the
inheritances are independent meioses: P(affected) = P(m) x P(p), P(carrier) = P(m)(1 - P(p)) +
(1 - P(m)) P(p), P(unaffected) = (1 - P(m))(1 - P(p)); at a site both parents carry they are the
fetal genotype posterior's hom-alt, het and hom-ref. The gene's pair is the one most likely to
make the fetus affected. Its status, by how many of the pair's alleles the fetus inherited:
``affected`` both (each 0.8 or more), ``carrier`` one, ``unaffected`` neither (0.2 or less), when
each inheritance is told outside the R validation's band of 0.2 to 0.8. Otherwise
``carrier_or_unaffected`` when one allele was surely not inherited (0.2 or less), so the fetus
cannot be affected whatever the other, else ``undetermined`` (a coin flip must not drop a gene).
As the R pairing reads it, a heterozygous father's allele the mother carries too is not told
(``maternal_also_carrier``), and a site both parents carry with the mother homozygous is
undetermined. The locked diagnostic candidates pair only alleles past the technical filter
(``RecessiveCandidate.plasma_call_retained``): the mother's other alleles are left out, the
father's stay with their inheritance not told.

**X-linked** (``mode``): on chrX outside the pseudo-autosomal regions a son has his mother's X
only, so her allele alone makes him affected (``x_linked_son``: P(affected) = P(m), never a
carrier); a daughter is paired as above, her father's X always passed (``x_linked_daughter``),
and one parent's allele alone makes her at most a carrier: her father's always, her mother's
with its inheritance. Of a fetus whose sex is not known (indeterminate or discordant, or more
than one fetus), no maternal model classifies chrX outside the pseudo-autosomal regions: every
allele of the mother's (a plasma call of 25% or more) and of the father's (hemizygous) is listed
with its inheritance not told and the status undetermined (``x_linked_sex_unknown``): a son
inherits his mother's X allele alone, a daughter her father's.

The view lists its genes by status (affected, undetermined, carrier, carrier_or_unaffected,
unaffected), then by risk (``recessive_listing_order``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from .nipt_analysis import (
    FATHER_CALL_OVERLAPS,
    FATHER_X_HET_FLAG,
    MATERNAL_ALLELE_VAF_BOUNDARY,
    SEX_CHROMOSOME_UNSUPPORTED_FLAG,
    X_FEMALE_FLAG,
    X_MALE_FLAG,
    FetalFractionEstimate,
    NiptClassification,
    NiptSiteObservation,
    has_own_plasma_call,
)

# R NIPT-M v0.7.3 de novo triage settings (config de_novo_triage and the hard-coded weights).
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
    representation), inside a capture target where the plasma has a coverage table, that
    passes the quality filter, where the father has no supported call. The triage's window
    decides the rest.

    On chrX outside the pseudo-autosomal regions the site must be classified (the fetal sex
    known): in a son the father passes no X, so his call does not matter (one he has makes
    a low-confidence candidate, ``PATERNAL_X_IN_SON_FLAG``); in a daughter a het call of his
    is an artefact, no genotype, and leaves the candidate in."""
    if not has_own_plasma_call(site) or site.in_target is False:
        return False
    if not site.is_autosomal and classification.category is None:
        return False
    if classification.quality_failures:
        return False
    if X_MALE_FLAG in classification.flags or FATHER_X_HET_FLAG in classification.flags:
        return True
    return site.father_state in _UNSUPPORTED_FATHER_STATES


def _metric(site: NiptSiteObservation, *names: str) -> float | None:
    for name in names:
        value = (site.cf_metrics or {}).get(name)
        if value is not None:
            return value
    return None


def repeat_units(site: NiptSiteObservation) -> float | None:
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
    units = repeat_units(site)
    unique = other_cfdna_carriers <= _MAX_OTHER_CFDNA_CARRIERS
    technical = (
        met(not site.cf_filters or site.cf_filters == ["PASS"], "caller FILTER not PASS")
        + met(mapping_quality is None or mapping_quality >= _MIN_ALT_MAPPING_QUALITY, "low alt mapping quality")
        + met(site.cf_fs is None or site.cf_fs <= _MAX_STRAND_BIAS_FS, "strand bias")
        + met(mismatches is None or mismatches <= _MAX_MISMATCHES, "many mismatches")
        + met(units is None or units <= _MAX_REPEAT_UNITS, "repeat context")
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
        effective_class == "indel" and units is not None and units >= _REPEAT_INDEL_MIN_UNITS
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


# How a gene's alleles are read (``recessive_mode``): autosomal (and chrX's pseudo-autosomal
# regions); on chrX outside them a daughter's (paired as an autosome, her father's X always
# passed, one parent's allele alone at most a carrier), a son's (his mother's allele alone,
# never a carrier), or that of a fetus whose sex is not known (each parent's alleles, the status
# undetermined).
RecessiveMode = Literal["autosomal", "x_linked_daughter", "x_linked_son", "x_linked_sex_unknown"]
# The fetal status of a gene's pair (``_pair_status``).
RecessiveStatus = Literal["affected", "carrier", "unaffected", "carrier_or_unaffected", "undetermined"]


def recessive_mode(classification: NiptClassification, chrom: str) -> RecessiveMode | None:
    """How a classified site's allele is read, from the model that classified it
    (``chromosome_model``): None for chrY and the mitochondrion, which no gene pairs."""
    flags = classification.flags
    if X_MALE_FLAG in flags:
        return "x_linked_son"
    if X_FEMALE_FLAG in flags:
        return "x_linked_daughter"
    if SEX_CHROMOSOME_UNSUPPORTED_FLAG in flags:
        return "x_linked_sex_unknown" if chrom.lower().removeprefix("chr") == "x" else None
    return "autosomal"


@dataclass(slots=True)
class RecessiveGeneRisk:
    gene: str
    maternal: list[RecessiveAllele]
    paternal: list[RecessiveAllele]
    # P(the fetus is affected): it inherited a maternal and a paternal allele (a son's X: the
    # maternal one), the highest over the gene's allele pairs; an inheritance the cfDNA did not
    # tell is read with the 1/2 prior (risk_uses_prior). None for x_linked_sex_unknown.
    risk: float | None
    # The pair the status rests on; None for x_linked_sex_unknown, which pairs nothing.
    maternal_variant_id: str | None
    paternal_variant_id: str | None
    status: RecessiveStatus
    mode: RecessiveMode
    # A pair's probability read with the 1/2 prior for an inheritance the cfDNA did not tell.
    risk_uses_prior: bool = False
    # The probabilities of a carrier (one allele) and of neither.
    carrier_probability: float | None = None
    unaffected_probability: float | None = None


@dataclass(slots=True)
class RecessiveCandidate:
    """A carrier variant of a gene the recessive view pairs: the father's trusted genotype,
    the site's classification and how its allele is read, and what its annotation says of its
    rarity."""

    variant_id: str
    father_state: str
    classification: NiptClassification
    mode: RecessiveMode = "autosomal"
    variant_class: str = "SNV"
    # False when the annotation has no population frequency at all.
    has_population_af: bool = True
    # False when the site's plasma call fails the cfDNA quality filter or lies outside the
    # capture targets (the R validation's technical filter, which the locked diagnostic
    # candidates apply): the mother's allele, known from the plasma alone, is left out; the
    # father's, known from his own genotype, stays with its inheritance not told.
    plasma_call_retained: bool = True
    # False when the locked criteria do not read the allele as the father's: the cohort count of
    # a father's allele (the plasmas that show it or the fathers that carry it) reads it as an
    # unannotated common allele (nipt_diagnostic_filters.is_cohort_common). The mother's allele,
    # read off the plasma, is counted by the plasmas alone.
    paternal_counted: bool = True


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


# The R validation's undetermined band: an inheritance between these is no call either way.
UNDETERMINED_BAND = (0.2, 0.8)


def mother_carries(classification: NiptClassification) -> bool:
    """Whether the plasma shows that the mother carries the allele: a maternal category (2-6)
    on a call at ``MATERNAL_ALLELE_VAF_BOUNDARY`` (25%) or more. Her heterozygous allele sits
    near 50% in the plasma; a lower call that the model places in her band is a low-level
    artefact or the fetus's allele, never hers (the owner's decision of 2026-11-27)."""
    vaf = classification.observed_vaf
    return (
        classification.maternal_state in ("het", "hom")
        and vaf is not None
        and vaf >= MATERNAL_ALLELE_VAF_BOUNDARY
    )


def _maternal_allele(candidate: RecessiveCandidate) -> RecessiveAllele | None:
    """The mother's allele when she carries it (``mother_carries``): heterozygous (categories
    2-4) or homozygous (5-6, always passed). On chrX of a fetus whose sex is not known, a plasma
    call of 25% or more, its inheritance not told."""
    classification = candidate.classification
    if not candidate.plasma_call_retained:
        return None
    if candidate.mode == "x_linked_sex_unknown":
        vaf = classification.observed_vaf
        if vaf is None or vaf < MATERNAL_ALLELE_VAF_BOUNDARY:
            return None
        return RecessiveAllele(
            variant_id=candidate.variant_id,
            inherited_probability=None,
            category=None,
            note=_notes(_SEX_UNKNOWN_NOTE, _frequency_note(candidate)),
        )
    if not mother_carries(classification):
        return None
    if classification.maternal_state == "hom":
        return RecessiveAllele(
            variant_id=candidate.variant_id,
            inherited_probability=1.0,
            category=classification.category,
            note=_notes("mother homozygous", _frequency_note(candidate)),
        )
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
    """The father's allele when he carries it: heterozygous, or homozygous (on his X,
    hemizygous), which he always passes. A son never inherits his father's X.

    As the R pairing reads it, the plasma tells whether a heterozygous father passed his
    allele only where the mother does not carry it and the plasma's call is retained:
    where she carries it too (``maternal_also_carrier``), or the call fails the technical
    filter, the inheritance is not told (the ½ prior, an undetermined status). A site both
    parents carry is paired on its own (``_shared_site_pair``). An allele the locked criteria do
    not count as the father's (``paternal_counted``) is none."""
    classification = candidate.classification
    if not candidate.paternal_counted:
        return None
    if candidate.mode == "x_linked_sex_unknown":
        # A man's X: hemizygous, a het call there an artefact. Passed to a daughter, never to
        # a son.
        if candidate.father_state != "hom_alt":
            return None
        return RecessiveAllele(
            variant_id=candidate.variant_id,
            inherited_probability=None,
            category=None,
            note=_notes("father hemizygous (his X)", _SEX_UNKNOWN_NOTE, _frequency_note(candidate)),
        )
    if candidate.father_state not in ("het", "hom_alt") or candidate.mode == "x_linked_son":
        return None
    note: str | None
    if candidate.father_state == "hom_alt":
        note = "father hemizygous (his X)" if candidate.mode == "x_linked_daughter" else "father homozygous"
        return RecessiveAllele(
            variant_id=candidate.variant_id,
            inherited_probability=1.0,
            category=classification.category,
            note=_notes(note, _frequency_note(candidate)),
        )
    probability: float | None = classification.paternal_transmission_probability
    note = None
    if mother_carries(classification):
        probability, note = None, "the mother carries it too: its inheritance is not told"
    elif not candidate.plasma_call_retained:
        probability, note = None, "its plasma call fails the technical filter: its inheritance is not told"
    return RecessiveAllele(
        variant_id=candidate.variant_id,
        inherited_probability=probability,
        category=classification.category,
        note=_notes(note, _frequency_note(candidate)),
    )


_SEX_UNKNOWN_NOTE = "fetal sex not known: a son inherits his mother's X, a daughter her father's"


def _in_band(probability: float | None) -> bool:
    return probability is None or UNDETERMINED_BAND[0] < probability < UNDETERMINED_BAND[1]


def _not_inherited(probability: float | None) -> bool:
    return probability is not None and probability <= UNDETERMINED_BAND[0]


_STATUS_BY_INHERITED: dict[int, RecessiveStatus] = {2: "affected", 1: "carrier", 0: "unaffected"}


def _pair_status(maternal: float | None, paternal: float | None) -> RecessiveStatus:
    """A pair's fetal status from its two inheritances (R's rule with its band): how many of
    the two the fetus inherited when both are told outside the band, else
    ``carrier_or_unaffected`` when one was surely not inherited, else ``undetermined``."""
    if _in_band(maternal) or _in_band(paternal):
        return "carrier_or_unaffected" if _not_inherited(maternal) or _not_inherited(paternal) else "undetermined"
    assert maternal is not None and paternal is not None
    inherited = (maternal >= UNDETERMINED_BAND[1]) + (paternal >= UNDETERMINED_BAND[1])
    return _STATUS_BY_INHERITED[inherited]


@dataclass(slots=True)
class _Pair:
    affected: float
    carrier: float
    unaffected: float
    status: RecessiveStatus
    maternal_variant_id: str | None
    paternal_variant_id: str | None
    uses_prior: bool


def _two_site_pair(maternal: RecessiveAllele, paternal: RecessiveAllele) -> _Pair:
    m = 0.5 if maternal.inherited_probability is None else maternal.inherited_probability
    p = 0.5 if paternal.inherited_probability is None else paternal.inherited_probability
    return _Pair(
        affected=m * p,
        carrier=m * (1 - p) + (1 - m) * p,
        unaffected=(1 - m) * (1 - p),
        status=_pair_status(maternal.inherited_probability, paternal.inherited_probability),
        maternal_variant_id=maternal.variant_id,
        paternal_variant_id=paternal.variant_id,
        uses_prior=maternal.inherited_probability is None or paternal.inherited_probability is None,
    )


def _shared_site_pair(
    allele: RecessiveAllele, paternal: RecessiveAllele, genotype: dict[str, float] | None, hom_alt: float | None
) -> _Pair:
    """A site both parents carry: the fetal genotype posterior (hom-alt affected, het a
    carrier); without one, the hom-alt probability alone, and the Mendelian 1/4 without that.
    Two homozygous parents always make the fetus homozygous. A homozygous mother (categories
    5-6) beside a heterozygous father is undetermined, as the R pairing reads it: the plasma's
    allele fraction cannot tell his allele beside two of hers."""
    if allele.category in (5, 6) and paternal.inherited_probability == 1.0:
        return _Pair(1.0, 0.0, 0.0, "affected", allele.variant_id, allele.variant_id, False)
    maternal_hom = allele.category in (5, 6)
    if genotype is not None:
        affected, carrier, unaffected = genotype.get("hom_alt", 0.0), genotype.get("het", 0.0), genotype.get("hom_ref", 0.0)
        uses_prior = False
    elif hom_alt is not None:
        affected, carrier, unaffected = hom_alt, 1.0 - hom_alt, 0.0
        uses_prior = False
    else:
        affected, carrier, unaffected = 0.25, 0.5, 0.25
        uses_prior = True
    status: RecessiveStatus
    if maternal_hom or uses_prior or UNDETERMINED_BAND[0] < affected < UNDETERMINED_BAND[1]:
        status = "undetermined"
    elif affected >= UNDETERMINED_BAND[1]:
        status = "affected"
    else:
        status = "carrier" if carrier >= unaffected else "unaffected"
    return _Pair(affected, carrier, unaffected, status, allele.variant_id, allele.variant_id, uses_prior)


def _one_allele_pair(allele: RecessiveAllele, *, maternal: bool) -> _Pair:
    """A daughter's X with one parent's allele alone: she can be a carrier, never affected (her
    father's allele always passed; her mother's with its inheritance)."""
    p = allele.inherited_probability
    value = 0.5 if p is None else p
    status: RecessiveStatus
    if _in_band(p):
        status = "carrier_or_unaffected"
    else:
        status = "carrier" if value >= UNDETERMINED_BAND[1] else "unaffected"
    return _Pair(
        affected=0.0,
        carrier=value,
        unaffected=1.0 - value,
        status=status,
        maternal_variant_id=allele.variant_id if maternal else None,
        paternal_variant_id=None if maternal else allele.variant_id,
        uses_prior=p is None,
    )


def _son_x_pair(maternal: RecessiveAllele) -> _Pair:
    """A son's X: his mother's allele alone makes him affected; he is never a carrier."""
    m = maternal.inherited_probability
    value = 0.5 if m is None else m
    status: RecessiveStatus
    if _in_band(m):
        status = "undetermined"
    else:
        status = "affected" if value >= UNDETERMINED_BAND[1] else "unaffected"
    return _Pair(value, 0.0, 1.0 - value, status, maternal.variant_id, None, m is None)


def _gene_risk(
    gene: str, maternal: list[RecessiveAllele], paternal: list[RecessiveAllele], pair: _Pair, mode: RecessiveMode
) -> RecessiveGeneRisk:
    return RecessiveGeneRisk(
        gene=gene,
        maternal=maternal,
        paternal=paternal,
        risk=pair.affected,
        maternal_variant_id=pair.maternal_variant_id,
        paternal_variant_id=pair.paternal_variant_id,
        status=pair.status,
        mode=mode,
        risk_uses_prior=pair.uses_prior,
        carrier_probability=pair.carrier,
        unaffected_probability=pair.unaffected,
    )


def _paired_risk(
    gene: str,
    mothers: list[RecessiveAllele],
    fathers: list[RecessiveAllele],
    shared: dict[str, tuple[dict[str, float] | None, float | None]],
    mode: RecessiveMode,
) -> RecessiveGeneRisk | None:
    """Both parents' alleles: the pair most likely to make the fetus affected. A daughter's X
    with one parent's alleles alone: the one most likely to make her a carrier."""
    if mothers and fathers:
        pairs = [
            _shared_site_pair(maternal, paternal, *shared.get(maternal.variant_id, (None, None)))
            if maternal.variant_id == paternal.variant_id
            else _two_site_pair(maternal, paternal)
            for maternal in mothers
            for paternal in fathers
        ]
        return _gene_risk(gene, mothers, fathers, max(pairs, key=lambda pair: pair.affected), mode)
    if mode == "x_linked_daughter" and (mothers or fathers):
        pairs = [_one_allele_pair(allele, maternal=True) for allele in mothers] + [
            _one_allele_pair(allele, maternal=False) for allele in fathers
        ]
        return _gene_risk(gene, mothers, fathers, max(pairs, key=lambda pair: pair.carrier), mode)
    return None


def _son_risk(gene: str, mothers: list[RecessiveAllele]) -> RecessiveGeneRisk | None:
    if not mothers:
        return None
    best = max((_son_x_pair(maternal) for maternal in mothers), key=lambda pair: pair.affected)
    return _gene_risk(gene, mothers, [], best, "x_linked_son")


def _sex_unknown_risk(
    gene: str, mothers: list[RecessiveAllele], fathers: list[RecessiveAllele]
) -> RecessiveGeneRisk | None:
    """chrX of a fetus whose sex is not known: each parent's alleles, nothing told or paired."""
    if not mothers and not fathers:
        return None
    return RecessiveGeneRisk(
        gene=gene,
        maternal=mothers,
        paternal=fathers,
        risk=None,
        maternal_variant_id=None,
        paternal_variant_id=None,
        status="undetermined",
        mode="x_linked_sex_unknown",
    )


def recessive_gene_risks(gene: str, candidates: Sequence[RecessiveCandidate]) -> list[RecessiveGeneRisk]:
    """The fetal status of ``gene`` from its carrier variants, one per reading of them: both
    parents' alleles paired (autosomal, or a daughter's X, where one parent's alleles alone are
    listed too), a son's X from his mother's alleles, and the X of a fetus whose sex is not
    known from each parent's. Empty when no reading applies: one parent's autosomal alleles
    alone, or no parent's allele at all."""
    mothers: list[RecessiveAllele] = []
    fathers: list[RecessiveAllele] = []
    shared: dict[str, tuple[dict[str, float] | None, float | None]] = {}
    sons: list[RecessiveAllele] = []
    unknown_mothers: list[RecessiveAllele] = []
    unknown_fathers: list[RecessiveAllele] = []
    daughter = False
    for candidate in candidates:
        maternal, paternal = _maternal_allele(candidate), _paternal_allele(candidate)
        if candidate.mode == "x_linked_sex_unknown":
            unknown_mothers.extend([maternal] if maternal is not None else [])
            unknown_fathers.extend([paternal] if paternal is not None else [])
        elif candidate.mode == "x_linked_son":
            # A son never inherits his father's X (_paternal_allele).
            sons.extend([maternal] if maternal is not None else [])
        elif maternal is not None or paternal is not None:
            daughter = daughter or candidate.mode == "x_linked_daughter"
            mothers.extend([maternal] if maternal is not None else [])
            fathers.extend([paternal] if paternal is not None else [])
            if maternal is not None and paternal is not None:
                classification = candidate.classification
                shared[candidate.variant_id] = (
                    classification.fetal_genotype_posterior,
                    classification.fetal_hom_alt_probability,
                )
    risks = (
        _paired_risk(gene, mothers, fathers, shared, "x_linked_daughter" if daughter else "autosomal"),
        _son_risk(gene, sons),
        _sex_unknown_risk(gene, unknown_mothers, unknown_fathers),
    )
    return [risk for risk in risks if risk is not None]


# The order the recessive view lists its genes in: by status, a fetus likeliest affected first
# and one the cfDNA did not tell before a carrier, then by risk.
_STATUS_ORDER: dict[RecessiveStatus, int] = {
    "affected": 0,
    "undetermined": 1,
    "carrier": 2,
    "carrier_or_unaffected": 3,
    "unaffected": 4,
}


def recessive_listing_order(risk: RecessiveGeneRisk) -> tuple[int, float, str]:
    """The sort key of the recessive view's genes (``_STATUS_ORDER``); a risk not told (the
    fetal sex not known) last among its status."""
    return (_STATUS_ORDER[risk.status], -(risk.risk or 0.0), risk.gene)
