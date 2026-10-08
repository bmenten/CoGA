"""Monogenic NIPT: fetal fraction, per-variant classification and fetal inheritance.

The analytical core of the monogenic NIPT feature. Given per-site observations joining
the father's call with the maternal-plasma (cfDNA) call, it:

  1. applies the cfDNA quality filter and estimates the fetal fraction (FF) from the
     paternal alleles the fetus inherited (category 7),
  2. classifies every site into one of the eight maternal/fetal categories with a
     beta-binomial likelihood around the FF-defined allele fractions, among the fetal
     states the father's genotype allows, with Mendelian priors,
  3. gives the fetal-inheritance probabilities the presets read: whether the fetus
     inherited a paternal allele (``paternal_transmission_probability``), the maternal
     allele (``maternal_allele_probability``), or both (``fetal_hom_alt_probability``),
  4. counts the categories, the filter funnel, the paternal-X fetal sex and the
     paternity evidence.

The model's constants are those of the lab's R validation (NIPT-M v0.5.1, six families
with trio exomes), frozen here: the overdispersion and reference bias of the consensus
allele fraction at maternal heterozygous sites, the selected cfDNA quality filter, and the
paternal genotype classes. ``NIPT_MODEL_REFERENCE`` names it; the API reports it.

Pure Python (no numpy/scipy, no I/O). See docs/monogenic-nipt.md for the implementation
notes and frontend/src/content/docs/monogenic-nipt.md for the model and the rules.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

NIPT_MODEL_REFERENCE = "R NIPT-M v0.5.1 validation (6 families with trio exomes)"

_EPS = 1e-4

# Overdispersion of the consensus allele fraction beyond binomial sampling, and its
# reference bias at a maternal heterozygous site (the allele fraction of a het site sits
# 1.1 points below its expectation): the leave-one-family-out medians of the R
# validation's beta-binomial fits (rho 0.00355-0.00387, bias -0.0110 to -0.0115).
OVERDISPERSION = 0.003721
MATERNAL_HET_BIAS = -0.011230
# The alt fraction a site shows without the allele: consensus-read noise. A paternal allele
# the fetus did not inherit is read against it.
BACKGROUND_ERROR_RATE = 0.002
# De novo (category 1) is rare: it wins only on clearly stronger evidence than a maternal
# state. The de novo triage (nipt_triage) then ranks the candidates.
_DE_NOVO_PRIOR_WEIGHT = 0.02
# A paternal allele the father is homozygous for is always transmitted; the small rest
# lets the data say otherwise, which is the category-8 (false-negative) signal.
_OBLIGATE_TRANSMISSION_PRIOR = 0.99
# An allele the mother does not carry sits at FF/2 in the plasma; one she carries
# heterozygously at 0.5 - FF/2 or more. Their midpoint, 0.25 whatever the FF, separates
# them: below it the mother does not carry the allele. (The R pipeline's FF window ran to
# 0.35; at an FF above 20% that lets in the sites both parents carry and the fetus did not
# inherit, at 0.5 - FF/2 less their spread, which raises the FF estimate and counts them as
# paternal alleles the fetus inherited.)
MATERNAL_ALLELE_VAF_BOUNDARY = 0.25
# Expected alt reads from which an untransmitted allele counts for paternity: Poisson
# P(0 | 10) = 4.5e-5, so a transmitted one is all but never missed.
PATERNITY_MIN_EXPECTED_READS = 10.0
# |z| of a maternal-band allele fraction under its best fetal state above which the
# site's allele balance fits no fetal genotype (site-specific capture bias, a copy-number
# change): the R validation's confident "not inherited" errors were such sites.
ALLELE_BALANCE_OUTLIER_Z = 4.0

_CATEGORY_LABELS: dict[int, str] = {
    1: "de novo in fetus",
    2: "maternal het, not inherited",
    3: "maternal het, inherited (het fetus)",
    4: "maternal het, inherited (hom fetus)",
    5: "maternal hom, het fetus",
    6: "maternal hom, hom fetus",
    7: "paternal, transmitted to fetus",
    8: "paternal hom-alt, absent (false negative)",
}

# category -> (maternal_state, fetal_inheritance)
_CATEGORY_AXES: dict[int, tuple[str, str]] = {
    1: ("hom_ref", "de_novo"),
    2: ("het", "maternal_not_inherited"),
    3: ("het", "maternal_inherited_het"),
    4: ("het", "maternal_inherited_hom"),
    5: ("hom", "maternal_inherited_het"),
    6: ("hom", "shared_hom"),
    7: ("hom_ref", "paternal_transmitted"),
    8: ("hom_ref", "paternal_not_transmitted"),
}

# Maternal-allele inheritance distinctions that collapse as FF -> 0.
_FF_LIMITED_CATEGORIES = frozenset({2, 3, 4, 5, 6})
_MATERNAL_HET_BAND = (2, 3, 4)
_MATERNAL_HOM_BAND = (5, 6)

# The father's genotype classes. ``absent``: his per-sample file has no call at the site
# (no alt read the caller reported), read as reference. ``low_vaf``: a well-supported call
# below the het fraction (noise, mosaicism, or an artefact both samples share).
# ``low_support``: a call too thin or too weak to class. ``missing``: no data (a joint
# VCF's no-call).
FATHER_STATES = ("hom_ref", "absent", "het", "hom_alt", "low_vaf", "low_support", "missing")


@dataclass(slots=True)
class NiptSiteObservation:
    """A biallelic site: the father's call and the cfDNA call (or the lack of one)."""

    variant_id: str
    chrom: str
    pos: int
    is_autosomal: bool
    # cfDNA (maternal plasma mixture) -- the signal we classify. Without a call, cf_dp
    # is the plasma's depth there from its coverage (cf_depth_estimated) and
    # cf_alt_reads 0.
    cf_present: bool
    cf_dp: int | None
    cf_alt_reads: int | None
    cf_vaf: float | None
    cf_qual: float | None
    # The father's genotype class (FATHER_STATES), from his allele fraction; the analysis
    # reads a call below min_father_dp / min_father_qual as missing.
    father_state: str
    father_dp: int | None = None
    father_qual: float | None = None
    father_vaf: float | None = None
    father_alt_reads: int | None = None
    ref: str = ""
    alt: str = ""
    # The cfDNA call's Fisher strand-bias score (Mutect2 FS) and its own FILTER values and
    # caller metrics (per-sample VCF); empty for a joint VCF.
    cf_fs: float | None = None
    cf_filters: list[str] = field(default_factory=list)
    cf_metrics: dict[str, float] = field(default_factory=dict)
    cf_depth_estimated: bool = False
    # Where one sample's call was read off the other representation of the allele
    # (REPRESENTATION_FLAGS); None when both calls are the record's own.
    representation: str | None = None

    @property
    def variant_class(self) -> str:
        return variant_class(self.ref, self.alt)


# Mutect2 calls adjacent phased SNVs as one MNV, so the two per-sample VCFs can hold the
# same alleles as an MNV in one sample and as SNVs (or another MNV) in the other (the R
# pipeline atomises the cfDNA MNVs for this). nipt_service.build_nipt_observations looks a
# missing call up base by base and marks the site:
# - the father's calls carry every base of the plasma's allele: his genotype is read off
#   them;
FATHER_CALL_OTHER_REPRESENTATION = "father_call_other_representation"
# - they carry some of its bases: he stays without a call, and the de novo triage reads it
#   as a paternal signal;
FATHER_CALL_OVERLAPS = "father_call_overlaps"
# - the plasma's calls carry every base of the father's allele: the plasma's reads are
#   theirs. Those calls are sites of their own, so this one is no evidence for the fetal
#   fraction, paternity or fetal sex;
PLASMA_CALL_OTHER_REPRESENTATION = "plasma_call_other_representation"
# - they carry some of its bases: the plasma stays without a call, and whether the fetus
#   inherited the allele is uncertain (no evidence either).
PLASMA_CALL_OVERLAPS = "plasma_call_overlaps"
REPRESENTATION_FLAGS = (
    FATHER_CALL_OTHER_REPRESENTATION,
    FATHER_CALL_OVERLAPS,
    PLASMA_CALL_OTHER_REPRESENTATION,
    PLASMA_CALL_OVERLAPS,
)
_NOT_PLASMA_EVIDENCE = frozenset({PLASMA_CALL_OTHER_REPRESENTATION, PLASMA_CALL_OVERLAPS})


def has_own_plasma_call(site: NiptSiteObservation) -> bool:
    """The plasma has a call of its own at the site (not none, and not one read off another
    record)."""
    return (
        bool(site.cf_present)
        and not site.cf_depth_estimated
        and site.representation != PLASMA_CALL_OTHER_REPRESENTATION
    )


def variant_class(ref: str, alt: str) -> str:
    """SNV, MNV (same length, more than one base) or indel; SNV when unknown."""
    if not ref or not alt:
        return "SNV"
    if len(ref) == len(alt):
        return "SNV" if len(ref) == 1 else "MNV"
    return "indel"


@dataclass(slots=True)
class NiptQualityThresholds:
    """The cfDNA quality filter and the paternal genotype classes (R NIPT-M v0.5.1).

    A cfDNA call passes when its quality (Mutect2 TLOD, else QUAL) reaches ``min_qual``,
    it has ``min_cf_alt_reads`` alt reads and a VAF of ``min_vaf`` and of
    ``vaf_ff_fraction`` x FF/2, and its strand-bias score FS is at most ``max_fs``. A
    missing value passes, as in the R pipeline. ``min_cf_dp`` is the plasma depth from
    which an absent allele is read as absent.
    """

    min_cf_dp: int = 20
    min_cf_alt_reads: int = 5
    min_qual: float = 20.0
    min_vaf: float = 0.01
    vaf_ff_fraction: float = 0.25
    max_fs: float = 20.0
    min_father_dp: int = 20
    min_father_alt_reads: int = 5
    min_father_qual: float = 20.0
    father_het_min_vaf: float = 0.20
    father_hom_alt_min_vaf: float = 0.80


@dataclass(slots=True)
class FetalFractionEstimate:
    ff: float
    ff_computed: float | None
    ff_median: float | None
    ci_low: float | None
    ci_high: float | None
    n_sites: int
    method: str
    low_confidence: bool
    # The 5th and 95th percentile of the FF sites' allele fractions: where a fetal allele
    # the mother does not carry sits, which the de novo window reads (nipt_triage).
    vaf_q05: float | None = None
    vaf_q95: float | None = None


@dataclass(slots=True)
class NiptClassification:
    variant_id: str
    category: int | None
    category_label: str
    maternal_state: str
    fetal_inheritance: str
    expected_vaf: float
    observed_vaf: float | None
    confidence: float
    runner_up_category: int | None = None
    runner_up_confidence: float | None = None
    flags: list[str] = field(default_factory=list)
    # The fetal-inheritance probabilities, where the site tells them:
    # the fetus inherited the father's allele (he carries it, the mother does not);
    paternal_transmission_probability: float | None = None
    # the fetus inherited the mother's allele (she carries it);
    maternal_allele_probability: float | None = None
    # the fetus is homozygous for the allele (both parents carry it);
    fetal_hom_alt_probability: float | None = None
    # the fetal genotype at a maternal heterozygous site (hom_ref / het / hom_alt).
    fetal_genotype_posterior: dict[str, float] | None = None
    # Why the cfDNA call fails the quality filter ([] when it passes or there is none).
    quality_failures: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FetalSexResult:
    """Fetal sex inferred from paternal X-chromosome transmission (no chrY needed).

    The father transmits his X to a daughter and his Y to a son, so paternal-only
    alleles on the non-PAR X appear in cfDNA for a female fetus (``x_transmitted``)
    and are absent for a male fetus (``x_not_transmitted``).
    """

    inferred: str  # "female" | "male" | "indeterminate"
    x_transmitted: int
    x_not_transmitted: int
    informative_sites: int


@dataclass(slots=True)
class PaternalTransmissionEvidence:
    """Whether the fetus inherited the father's alleles, over the autosomal sites where he
    carries one and the mother does not, each with enough plasma depth that a transmitted
    allele could not have been missed (``PATERNITY_MIN_EXPECTED_READS``).

    A fetus inherits every allele its father is homozygous for, and half of those he is
    heterozygous for. For the true father the homozygous ones are all seen but a few
    coverage dropouts, and half the heterozygous ones; for another man far fewer of both.
    """

    hom_alt_transmitted: int = 0
    hom_alt_not_transmitted: int = 0
    het_transmitted: int = 0
    het_not_transmitted: int = 0

    @property
    def hom_alt_informative(self) -> int:
        return self.hom_alt_transmitted + self.hom_alt_not_transmitted

    @property
    def het_informative(self) -> int:
        return self.het_transmitted + self.het_not_transmitted

    @property
    def hom_alt_rate(self) -> float | None:
        informative = self.hom_alt_informative
        return self.hom_alt_transmitted / informative if informative else None

    @property
    def het_rate(self) -> float | None:
        informative = self.het_informative
        return self.het_transmitted / informative if informative else None


@dataclass(slots=True)
class NiptAnalysisResult:
    fetal_fraction: FetalFractionEstimate
    category_counts: dict[int, int]
    filter_counts: dict[str, int]
    classifications: list[NiptClassification]
    fetal_sex: FetalSexResult = field(
        default_factory=lambda: FetalSexResult("indeterminate", 0, 0, 0)
    )
    # The paternity evidence (see PaternalTransmissionEvidence): only sites with a
    # confident father call count. A missing or thin call lands in category 7 on the de
    # novo prior alone, which says nothing about who the father is, so
    # category_counts[7] is not paternity evidence.
    paternal_transmission: PaternalTransmissionEvidence = field(
        default_factory=PaternalTransmissionEvidence
    )
    # Why the cfDNA calls that failed the quality filter failed, one count per reason.
    quality_failure_counts: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Maths helpers
# --------------------------------------------------------------------------- #

def _site_vaf(site: NiptSiteObservation) -> float:
    if site.cf_vaf is not None:
        return site.cf_vaf
    dp = site.cf_dp or 0
    if dp <= 0:
        return 0.0
    return (site.cf_alt_reads or 0) / dp


def _expected_vaf(category: int, ff: float) -> float:
    """The cfDNA allele fraction a category predicts. A maternal heterozygous site
    carries the consensus reference bias; the hom band and the fetal band do not (the
    bias was measured at maternal het sites)."""
    half = ff / 2.0
    return {
        1: half,
        2: 0.5 - half + MATERNAL_HET_BIAS,
        3: 0.5 + MATERNAL_HET_BIAS,
        4: 0.5 + half + MATERNAL_HET_BIAS,
        5: 1.0 - half,
        6: 1.0,
        7: half,
    }[category]


# The fetus carries one maternal and one paternal allele, so the father's genotype
# bounds the fetal state. A hom-ref father passes no alt: the fetus is not hom-alt
# (categories 4 and 6) and nothing is paternal (7). A hom-alt father always passes
# one: the fetus is not hom-ref (2), not het beside a hom-alt mother (5), and a de
# novo (1) cannot be told from his allele. A het father allows every maternal state;
# only a missing (or untrusted) call leaves de novo and paternal side by side.
#
# The prior of each candidate is P(maternal genotype) x P(fetal genotype | parents):
# flat over the mother's three genotypes, Mendelian for the fetus (the R validation's
# priors at a maternal het site: 1/2, 1/2 beside a hom-ref father; 1/4, 1/2, 1/4 beside a
# het one; 1/2, 1/2 beside a hom-alt one). De novo carries its own small weight.
_CANDIDATE_PRIORS: dict[str, dict[int, float]] = {
    "hom_ref": {1: _DE_NOVO_PRIOR_WEIGHT, 2: 0.5, 3: 0.5, 5: 1.0},
    "het": {2: 0.25, 3: 0.5, 4: 0.25, 5: 0.5, 6: 0.5, 7: 0.5},
    "hom_alt": {3: 0.5, 4: 0.5, 6: 1.0, 7: 1.0},
    "missing": {1: _DE_NOVO_PRIOR_WEIGHT, 2: 1 / 3, 3: 1 / 3, 4: 1 / 3, 5: 0.5, 6: 0.5, 7: 0.5},
}


def trusted_father_state(site: NiptSiteObservation, qc: NiptQualityThresholds) -> str:
    """The father's genotype as the candidates read it: ``hom_ref``, ``het``, ``hom_alt``,
    or ``missing`` when his call cannot be trusted.

    No call in his per-sample file (``absent``) reads as reference, unless his depth there
    is known and too low. A low-level call (``low_vaf``), a thin or weak call
    (``low_support``) or a call below ``min_father_dp`` / ``min_father_qual`` count as no
    call: a het father read as hom-ref at 3x would otherwise turn his transmitted allele
    into a de novo and rule out a hom-alt fetus.
    """
    state = site.father_state
    if state in ("missing", "low_vaf", "low_support"):
        return "missing"
    if state == "absent":
        if site.father_dp is not None and site.father_dp < qc.min_father_dp:
            return "missing"
        return "hom_ref"
    if (site.father_dp or 0) < qc.min_father_dp:
        return "missing"
    if site.father_qual is not None and site.father_qual < qc.min_father_qual:
        return "missing"
    return state if state in ("hom_ref", "het", "hom_alt") else "missing"


def _log_beta_binom(k: int, n: int, mu: float, rho: float) -> float:
    """Log beta-binomial pmf. rho<=0 falls back to the binomial."""
    mu = min(max(mu, _EPS), 1.0 - _EPS)
    log_choose = math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
    if rho <= 0:
        return log_choose + k * math.log(mu) + (n - k) * math.log(1.0 - mu)
    s = (1.0 - rho) / rho
    a = mu * s
    b = (1.0 - mu) * s
    return (
        log_choose
        + math.lgamma(k + a)
        + math.lgamma(n - k + b)
        - math.lgamma(n + a + b)
        + math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
    )


def _softmax(log_scores: dict[int, float]) -> dict[int, float]:
    top = max(log_scores.values())
    exps = {key: math.exp(value - top) for key, value in log_scores.items()}
    total = sum(exps.values())
    return {key: value / total for key, value in exps.items()}


def _wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    if trials <= 0:
        return (0.0, 0.0)
    p = successes / trials
    denom = 1.0 + z * z / trials
    center = (p + z * z / (2.0 * trials)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials * trials))
    return (max(0.0, center - half), min(1.0, center + half))


def _quantile(sorted_values: Sequence[float], fraction: float) -> float | None:
    """Linear-interpolated quantile (R type 7, numpy's default, as the R pipeline computes
    it). The target coverage QC (nipt_target_coverage) reads it too."""
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def _beta_binomial_z(k: int, n: int, mu: float, rho: float) -> float:
    """How many standard deviations ``k`` of ``n`` lies from a beta-binomial mean ``mu``."""
    mu = min(max(mu, _EPS), 1.0 - _EPS)
    variance = n * mu * (1.0 - mu) * (1.0 + (n - 1) * max(rho, 0.0))
    if variance <= 0:
        return 0.0
    return abs(k - n * mu) / math.sqrt(variance)


# --------------------------------------------------------------------------- #
# The cfDNA quality filter
# --------------------------------------------------------------------------- #

def call_quality(site: NiptSiteObservation) -> float | None:
    """The cfDNA call's quality: Mutect2's TLOD, else the call's own QUAL, else the
    record's QUAL. None when the call has none (it then passes the quality test)."""
    metrics = site.cf_metrics or {}
    for key in ("TLOD", "QUAL"):
        value = metrics.get(key)
        if value is not None:
            return value
    return site.cf_qual


def quality_failures(
    site: NiptSiteObservation, qc: NiptQualityThresholds, *, ff: float | None = None
) -> list[str]:
    """Why a cfDNA call fails the quality filter; [] when it passes, or when the plasma
    has no call at the site (there is nothing to filter).

    The R NIPT-M v0.5.1 setting: quality (TLOD) >= 20, >= 5 alt reads, VAF >= 0.01,
    FS <= 20, and, once FF is known, VAF >= 0.25 x FF/2. A missing value passes.
    """
    if site.cf_depth_estimated or not site.cf_present:
        return []
    failures: list[str] = []
    quality = call_quality(site)
    if quality is not None and quality < qc.min_qual:
        failures.append("low_quality")
    if (site.cf_alt_reads or 0) < qc.min_cf_alt_reads:
        failures.append("few_alt_reads")
    vaf = _site_vaf(site)
    if vaf < qc.min_vaf:
        failures.append("low_vaf")
    if site.cf_fs is not None and site.cf_fs > qc.max_fs:
        failures.append("strand_bias")
    if ff is not None and ff > 0 and vaf < qc.vaf_ff_fraction * ff / 2.0 and "low_vaf" not in failures:
        failures.append("below_fetal_vaf_floor")
    return failures


def _passes_quality(site: NiptSiteObservation, qc: NiptQualityThresholds, *, ff: float | None = None) -> bool:
    return not quality_failures(site, qc, ff=ff)


# --------------------------------------------------------------------------- #
# Fetal fraction estimation
# --------------------------------------------------------------------------- #

def _is_ff_site(site: NiptSiteObservation, qc: NiptQualityThresholds, vaf_ceiling: float) -> bool:
    """A category-7 site: the father carries the allele, the mother does not, and the
    fetus inherited it -- a passing cfDNA call in the fetal band."""
    if not site.is_autosomal or not has_own_plasma_call(site):
        return False
    if trusted_father_state(site, qc) not in ("het", "hom_alt"):
        return False
    if (site.cf_dp or 0) < qc.min_cf_dp:
        return False
    if not _passes_quality(site, qc):
        return False
    vaf = _site_vaf(site)
    return 0.005 <= vaf <= vaf_ceiling


def estimate_fetal_fraction(
    sites: Iterable[NiptSiteObservation],
    qc: NiptQualityThresholds,
    *,
    vaf_ceiling: float = MATERNAL_ALLELE_VAF_BOUNDARY,
    min_sites: int = 30,
    hard_floor: int = 5,
    max_ci_halfwidth: float = 0.03,
) -> FetalFractionEstimate:
    """Estimate FF from category-7 sites.

    The headline estimate is the depth-pooled ``2 * sum(alt)/sum(dp)`` with a
    Wilson CI; the per-site median (the R pipeline's estimate) is a cross-check, and the
    5th/95th percentiles of the sites' allele fractions bound the de novo window.
    """
    cat7 = [site for site in sites if _is_ff_site(site, qc, vaf_ceiling)]
    n_sites = len(cat7)

    ff_computed: float | None = None
    ff_median: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    vaf_q05: float | None = None
    vaf_q95: float | None = None

    if n_sites >= 1:
        vafs = sorted(_site_vaf(site) for site in cat7)
        ff_median = 2.0 * statistics.median(vafs)
        vaf_q05 = _quantile(vafs, 0.05)
        vaf_q95 = _quantile(vafs, 0.95)
        total_alt = sum((site.cf_alt_reads or 0) for site in cat7)
        total_dp = sum((site.cf_dp or 0) for site in cat7)
        if total_dp > 0:
            low, high = _wilson_interval(total_alt, total_dp)
            ci_low, ci_high = max(0.0, 2.0 * low), min(1.0, 2.0 * high)
            if n_sites >= hard_floor:
                ff_computed = 2.0 * (total_alt / total_dp)

    low_confidence = ff_computed is None or n_sites < min_sites
    if ci_low is not None and ci_high is not None and (ci_high - ci_low) / 2.0 > max_ci_halfwidth:
        low_confidence = True

    ff = ff_computed if ff_computed is not None else 0.0

    return FetalFractionEstimate(
        ff=ff,
        ff_computed=ff_computed,
        ff_median=ff_median,
        ci_low=ci_low,
        ci_high=ci_high,
        n_sites=n_sites,
        method="category7_pooled",
        low_confidence=low_confidence,
        vaf_q05=vaf_q05 if n_sites >= hard_floor else None,
        vaf_q95=vaf_q95 if n_sites >= hard_floor else None,
    )


# --------------------------------------------------------------------------- #
# Fetal inheritance probabilities
# --------------------------------------------------------------------------- #

def paternal_transmission_probability(
    k: int,
    n: int,
    ff: float,
    *,
    father_state: str,
    overdispersion: float = OVERDISPERSION,
    error_rate: float = BACKGROUND_ERROR_RATE,
) -> float | None:
    """P(the fetus inherited the father's allele) from ``k`` alt reads of ``n`` at a site
    the mother does not carry: a transmitted allele sits at FF/2, an untransmitted one at
    the noise level. The prior is 1/2 for a het father and near 1 for a hom-alt one. None
    without depth or FF."""
    if n <= 0 or ff <= 0 or father_state not in ("het", "hom_alt"):
        return None
    prior = 0.5 if father_state == "het" else _OBLIGATE_TRANSMISSION_PRIOR
    log_t = math.log(prior) + _log_beta_binom(k, n, ff / 2.0, overdispersion)
    log_n = math.log(1.0 - prior) + _log_beta_binom(k, n, error_rate, overdispersion)
    top = max(log_t, log_n)
    t = math.exp(log_t - top)
    u = math.exp(log_n - top)
    return t / (t + u)


def _band_posterior(
    k: int,
    n: int,
    ff: float,
    categories: Sequence[int],
    priors: dict[int, float],
    overdispersion: float,
) -> dict[int, float]:
    scores = {
        category: math.log(priors[category]) + _log_beta_binom(k, n, _expected_vaf(category, ff), overdispersion)
        for category in categories
        if priors.get(category, 0.0) > 0
    }
    return _softmax(scores) if scores else {}


def _maternal_probabilities(
    best: int,
    k: int,
    n: int,
    ff: float,
    father_state: str,
    overdispersion: float,
) -> tuple[float | None, float | None, dict[str, float] | None]:
    """(maternal allele inherited, fetus hom-alt, fetal genotype posterior) at a site the
    mother carries, from the posterior over her band's categories alone.

    Beside a hom-ref father the fetus inherited her allele when it is het (category 3);
    beside a het father either parent's allele can make it het, so half of category 3
    counts, and category 4 (hom-alt) in full; beside a hom-alt father only category 4. A
    hom-alt mother always passes her allele.
    """
    priors = _CANDIDATE_PRIORS.get(father_state, _CANDIDATE_PRIORS["missing"])
    if best in _MATERNAL_HET_BAND:
        posterior = _band_posterior(k, n, ff, _MATERNAL_HET_BAND, priors, overdispersion)
        p2, p3, p4 = posterior.get(2, 0.0), posterior.get(3, 0.0), posterior.get(4, 0.0)
        genotype = {"hom_ref": p2, "het": p3, "hom_alt": p4}
        maternal: float | None
        if father_state == "hom_ref":
            maternal = p3 + p4
        elif father_state == "hom_alt":
            maternal = p4 / (p3 + p4) if (p3 + p4) > 0 else None
        else:
            maternal = p4 + 0.5 * p3
        hom_alt = p4 if father_state in ("het", "hom_alt", "missing") else None
        return maternal, hom_alt, genotype
    if best in _MATERNAL_HOM_BAND:
        posterior = _band_posterior(k, n, ff, _MATERNAL_HOM_BAND, priors, overdispersion)
        hom_alt = posterior.get(6, 0.0) if father_state in ("het", "missing") else (
            1.0 if father_state == "hom_alt" else 0.0
        )
        return 1.0, hom_alt, None
    return None, None, None


# --------------------------------------------------------------------------- #
# Per-variant classification
# --------------------------------------------------------------------------- #

def _classification(
    site: NiptSiteObservation,
    *,
    category: int | None,
    maternal_state: str,
    fetal_inheritance: str,
    expected_vaf: float,
    confidence: float,
    flags: list[str],
    runner_up_category: int | None = None,
    runner_up_confidence: float | None = None,
    paternal_transmission_probability: float | None = None,
) -> NiptClassification:
    label = _CATEGORY_LABELS[category] if category is not None else "undetermined"
    observed: float | None
    if site.cf_vaf is not None:
        observed = site.cf_vaf
    elif site.cf_present or site.cf_depth_estimated:
        observed = _site_vaf(site)
    else:
        observed = None
    return NiptClassification(
        variant_id=site.variant_id,
        category=category,
        category_label=label,
        maternal_state=maternal_state,
        fetal_inheritance=fetal_inheritance,
        expected_vaf=expected_vaf,
        observed_vaf=observed,
        confidence=confidence,
        runner_up_category=runner_up_category,
        runner_up_confidence=runner_up_confidence,
        flags=flags,
        paternal_transmission_probability=paternal_transmission_probability,
    )


def _absent_site(
    site: NiptSiteObservation,
    father_state: str,
    ff: float,
    flags: list[str],
    qc: NiptQualityThresholds,
    *,
    detect_min: int,
    overdispersion: float,
) -> NiptClassification:
    """A site without the allele in the plasma (no call, or fewer than ``min_cf_alt_reads``
    alt reads): whether the fetus missed a paternal allele."""
    n = site.cf_dp or 0
    k = site.cf_alt_reads or 0
    if father_state in ("hom_alt", "het"):
        if site.cf_dp is None or n <= 0:
            flags.append("no_plasma_depth")
            return _classification(
                site, category=None, maternal_state="hom_ref", fetal_inheritance="unknown",
                expected_vaf=ff / 2.0, confidence=0.0, flags=flags,
            )
        if n < qc.min_cf_dp:
            flags.append("low_depth_dropout")
            return _classification(
                site, category=None, maternal_state="hom_ref", fetal_inheritance="unknown",
                expected_vaf=ff / 2.0, confidence=0.0, flags=flags,
            )
        transmitted = paternal_transmission_probability(
            k, n, ff, father_state=father_state, overdispersion=overdispersion
        )
        if n * ff / 2.0 < detect_min:
            flags.append("undetectable_at_ff")
            return _classification(
                site, category=None, maternal_state="hom_ref",
                fetal_inheritance="paternal_not_transmitted" if father_state == "hom_alt" else "unknown",
                expected_vaf=ff / 2.0, confidence=0.0, flags=flags,
                paternal_transmission_probability=transmitted,
            )
        if transmitted is not None and transmitted >= 0.5:
            # A few alt reads at a low depth x FF: consistent with the allele, below the
            # quality filter's read count.
            flags.append("few_alt_reads")
            return _classification(
                site, category=7, maternal_state="hom_ref", fetal_inheritance="paternal_transmitted",
                expected_vaf=ff / 2.0, confidence=transmitted, flags=flags,
                paternal_transmission_probability=transmitted,
            )
        not_transmitted = 1.0 - transmitted if transmitted is not None else 0.0
        if father_state == "hom_alt":
            flags.append("false_negative")
            return _classification(
                site, category=8, maternal_state="hom_ref", fetal_inheritance="paternal_not_transmitted",
                expected_vaf=ff / 2.0, confidence=not_transmitted, flags=flags,
                paternal_transmission_probability=transmitted,
            )
        return _classification(
            site, category=None, maternal_state="hom_ref", fetal_inheritance="paternal_not_transmitted",
            expected_vaf=ff / 2.0, confidence=not_transmitted, flags=flags,
            paternal_transmission_probability=transmitted,
        )
    flags.append("no_alt_signal")
    return _classification(
        site, category=None, maternal_state="hom_ref",
        fetal_inheritance="unknown", expected_vaf=0.0, confidence=0.0, flags=flags,
    )


def classify_site(
    site: NiptSiteObservation,
    ff_estimate: FetalFractionEstimate,
    qc: NiptQualityThresholds,
    *,
    overdispersion: float = OVERDISPERSION,
    min_separation: float = 0.90,
    detect_min: int = 3,
    ff_too_low: float = 0.01,
) -> NiptClassification:
    ff = ff_estimate.ff
    flags: list[str] = []

    if not site.is_autosomal:
        flags.append("sex_chromosome_unsupported")
        return _classification(
            site,
            category=None,
            maternal_state="unknown",
            fetal_inheritance="unknown",
            expected_vaf=0.0,
            confidence=0.0,
            flags=flags,
        )

    if ff_estimate.low_confidence:
        flags.append("ff_low_confidence")

    n = site.cf_dp or 0
    k = site.cf_alt_reads or 0
    present = bool(site.cf_present) and not site.cf_depth_estimated and n > 0 and k >= qc.min_cf_alt_reads
    father_state = trusted_father_state(site, qc)
    if site.father_state == "low_vaf":
        flags.append("father_low_level_signal")
    elif site.father_state == "absent":
        flags.append("father_no_call")
    if site.representation:
        flags.append(site.representation)
    failures = quality_failures(site, qc, ff=ff if ff > 0 else None)

    # ---- Absence handling ------------------------------------------------- #
    if not present:
        classification = _absent_site(
            site, father_state, ff, flags, qc, detect_min=detect_min, overdispersion=overdispersion
        )
        classification.quality_failures = failures
        return classification

    # ---- Present: likelihood over the candidate categories ---------------- #
    if father_state == "missing":
        flags.append("father_no_coverage")

    priors = _CANDIDATE_PRIORS.get(father_state, _CANDIDATE_PRIORS["missing"])
    log_scores = {
        category: _log_beta_binom(k, n, _expected_vaf(category, ff), overdispersion) + math.log(prior)
        for category, prior in priors.items()
    }
    posteriors = _softmax(log_scores)
    ranked = sorted(posteriors.items(), key=lambda item: item[1], reverse=True)
    best, confidence = ranked[0]
    runner_up_category, runner_up_confidence = ranked[1] if len(ranked) > 1 else (None, None)

    maternal_state, fetal_inheritance = _CATEGORY_AXES[best]

    if n < qc.min_cf_dp:
        flags.append("low_depth")
    if confidence < min_separation:
        flags.append("ambiguous")
    for failure in failures:
        flags.append(f"quality:{failure}")
    if ff < ff_too_low and best in _FF_LIMITED_CATEGORIES:
        # The fetal contribution is an indistinguishable perturbation on the
        # maternal genotype; report the maternal state but not the fetal call.
        flags.append("ff_too_low")
        fetal_inheritance = "unknown"

    classification = _classification(
        site,
        category=best,
        maternal_state=maternal_state,
        fetal_inheritance=fetal_inheritance,
        expected_vaf=_expected_vaf(best, ff),
        confidence=confidence,
        flags=flags,
        runner_up_category=runner_up_category,
        runner_up_confidence=runner_up_confidence,
    )
    classification.quality_failures = failures

    if best == 7 and father_state in ("het", "hom_alt"):
        classification.paternal_transmission_probability = paternal_transmission_probability(
            k, n, ff, father_state=father_state, overdispersion=overdispersion
        )
    if best in _MATERNAL_HET_BAND or best in _MATERNAL_HOM_BAND:
        if ff >= ff_too_low:
            maternal, hom_alt, genotype = _maternal_probabilities(
                best, k, n, ff, father_state, overdispersion
            )
            classification.maternal_allele_probability = maternal
            classification.fetal_hom_alt_probability = hom_alt
            classification.fetal_genotype_posterior = genotype
        if best in _MATERNAL_HET_BAND:
            if site.variant_class != "SNV":
                # The R validation's maternal model was 74% accurate on indels (95% on
                # SNVs): the consensus allele fraction of an indel is biased.
                flags.append("maternal_inference_indel")
            if _beta_binomial_z(k, n, _expected_vaf(best, ff), overdispersion) > ALLELE_BALANCE_OUTLIER_Z:
                flags.append("allele_balance_outlier")
    return classification


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

# chrX pseudo-autosomal regions carry no X-vs-Y transmission signal; exclude them.
# Conservative bounds spanning the GRCh37 and GRCh38 PAR1/PAR2 coordinates.
_X_PAR1_MAX = 2_800_000
_X_PAR2_MIN = 154_900_000
# A daughter's paternal-X allele shows in cfDNA at ~FF/2; a higher VAF means the
# mother carries it (uninformative for paternal transmission).
_FETAL_SEX_MATERNAL_VAF_FLOOR = 0.30
MIN_FETAL_SEX_SITES = 8
# A daughter shows (nearly) every allele of her father's X, a son (nearly) none. A share in
# between is no sex: the alleles are not the fetus's father's (see the paternity check), or
# the calls are too noisy to tell.
FEMALE_MIN_TRANSMITTED_SHARE = 0.80
MALE_MAX_TRANSMITTED_SHARE = 0.10


def _is_nonpar_x(chrom: str, pos: int) -> bool:
    if chrom.lower().removeprefix("chr") != "x":
        return False
    return _X_PAR1_MAX < pos < _X_PAR2_MIN


def infer_fetal_sex(
    sites: Sequence[NiptSiteObservation],
    ff_estimate: FetalFractionEstimate,
    qc: NiptQualityThresholds,
    *,
    detect_min: int = 3,
) -> FetalSexResult:
    """Fetal sex from paternal X transmission across non-PAR chrX sites.

    At a non-PAR chrX site where the father carries the alt (hemizygous hom-alt)
    and the mother is reference, a daughter shows the paternal alt in cfDNA at
    ~FF/2 (transmitted) and a son shows nothing — the paternal X is not transmitted
    (the category-8 "absent" signal). A present alt at a maternal level (high VAF)
    means the mother carries it, so it is excluded as uninformative.

    Of at least ``MIN_FETAL_SEX_SITES`` informative sites, a transmitted share of 80% or
    more reads female, 10% or less male, and a share in between indeterminate.
    """
    ff = ff_estimate.ff
    transmitted = 0
    not_transmitted = 0
    for site in sites:
        if site.is_autosomal or not _is_nonpar_x(site.chrom, site.pos):
            continue
        if site.representation in _NOT_PLASMA_EVIDENCE:
            continue
        # Hemizygous: a haploid "1", a diploid "1/1" or an allele fraction near 1 all read
        # hom_alt.
        if trusted_father_state(site, qc) != "hom_alt":
            continue
        n = site.cf_dp or 0
        k = site.cf_alt_reads or 0
        present = bool(site.cf_present) and not site.cf_depth_estimated and n > 0 and k >= qc.min_cf_alt_reads
        if present:
            vaf = site.cf_vaf if site.cf_vaf is not None else (k / n if n else 0.0)
            if vaf < _FETAL_SEX_MATERNAL_VAF_FLOOR:
                transmitted += 1
        elif k == 0 and n * ff / 2.0 >= detect_min:
            not_transmitted += 1
    informative = transmitted + not_transmitted
    share = transmitted / informative if informative else 0.0
    if informative < MIN_FETAL_SEX_SITES:
        inferred = "indeterminate"
    elif share >= FEMALE_MIN_TRANSMITTED_SHARE:
        inferred = "female"  # paternal X transmitted -> daughter
    elif share <= MALE_MAX_TRANSMITTED_SHARE:
        inferred = "male"  # paternal X not transmitted -> son
    else:
        inferred = "indeterminate"
    return FetalSexResult(inferred, transmitted, not_transmitted, informative)


def paternal_transmission_evidence(
    sites: Iterable[NiptSiteObservation],
    ff_estimate: FetalFractionEstimate,
    qc: NiptQualityThresholds,
    *,
    overdispersion: float = OVERDISPERSION,
    min_expected_reads: float = PATERNITY_MIN_EXPECTED_READS,
) -> PaternalTransmissionEvidence:
    """Count the father's alleles the fetus inherited and those it did not, at the autosomal
    sites where his call is confident, the mother does not carry the allele (no plasma
    call, or one in the fetal band) and the plasma depth makes a transmitted allele
    impossible to miss. See ``PaternalTransmissionEvidence``."""
    evidence = PaternalTransmissionEvidence()
    ff = ff_estimate.ff
    if ff <= 0:
        return evidence
    for site in sites:
        if not site.is_autosomal or site.representation in _NOT_PLASMA_EVIDENCE:
            continue
        father = trusted_father_state(site, qc)
        if father not in ("het", "hom_alt"):
            continue
        n = site.cf_dp or 0
        if n * ff / 2.0 < min_expected_reads:
            continue
        observed = site.cf_present and not site.cf_depth_estimated
        if observed and _site_vaf(site) > MATERNAL_ALLELE_VAF_BOUNDARY:
            continue  # the mother carries it too: uninformative for paternity
        k = (site.cf_alt_reads or 0) if observed else 0
        transmitted = paternal_transmission_probability(
            k, n, ff, father_state=father, overdispersion=overdispersion
        )
        if transmitted is None:
            continue
        if father == "hom_alt":
            if transmitted >= 0.5:
                evidence.hom_alt_transmitted += 1
            else:
                evidence.hom_alt_not_transmitted += 1
        elif transmitted >= 0.5:
            evidence.het_transmitted += 1
        else:
            evidence.het_not_transmitted += 1
    return evidence


@dataclass(slots=True)
class NiptFilteredSites:
    """The sites that pass the quality and artifact filters, the funnel counts, and the
    fetal fraction estimated over those sites."""

    passed: list[NiptSiteObservation]
    filter_counts: dict[str, int]
    fetal_fraction: FetalFractionEstimate
    quality_failure_counts: dict[str, int] = field(default_factory=dict)


def filter_sites_and_estimate_ff(
    sites: Iterable[NiptSiteObservation],
    qc: NiptQualityThresholds,
    *,
    artifact_lookup: Callable[[str], bool] = lambda _variant_id: False,
) -> NiptFilteredSites:
    """Apply the quality and artifact filters, then estimate FF over what passes.

    This is the one fetal-fraction computation. The summary (``run_nipt_analysis``)
    and the variant list (``nipt_service.get_family_nipt_variants``) both take FF from
    here, so the two views report the same estimate and classify against it; a listed
    artifact or a failed-quality site never reaches the estimate.

    The funnel counts the cfDNA calls. A site where only the father has a call (the
    plasma's depth there comes from its coverage, or its reads from the plasma's calls of
    the allele in another representation) has no cfDNA call of its own to filter: it
    passes on, uncounted, for the paternal-transmission evidence (``paternal_only``). The
    quality filter's FF-scaled VAF floor is applied once FF is known.
    """
    total_in = 0
    failed_quality = 0
    failed_artifact = 0
    paternal_only = 0
    failure_counts: dict[str, int] = {}
    candidates: list[NiptSiteObservation] = []
    for site in sites:
        if not has_own_plasma_call(site):
            paternal_only += 1
            candidates.append(site)
            continue
        total_in += 1
        failures = quality_failures(site, qc)
        if failures:
            failed_quality += 1
            for failure in failures:
                failure_counts[failure] = failure_counts.get(failure, 0) + 1
            continue
        if artifact_lookup(site.variant_id):
            failed_artifact += 1
            continue
        candidates.append(site)
    fetal_fraction = estimate_fetal_fraction(candidates, qc)
    passed: list[NiptSiteObservation] = []
    failed_floor = 0
    for site in candidates:
        if has_own_plasma_call(site) and fetal_fraction.ff_computed:
            if quality_failures(site, qc, ff=fetal_fraction.ff):
                failed_floor += 1
                continue
        passed.append(site)
    if failed_floor:
        failed_quality += failed_floor
        failure_counts["below_fetal_vaf_floor"] = failure_counts.get("below_fetal_vaf_floor", 0) + failed_floor
    return NiptFilteredSites(
        passed=passed,
        filter_counts={
            "total_in": total_in,
            "passed": total_in - failed_quality - failed_artifact,
            "failed_quality": failed_quality,
            "failed_artifact": failed_artifact,
            "paternal_only": paternal_only,
        },
        fetal_fraction=fetal_fraction,
        quality_failure_counts=failure_counts,
    )


def run_nipt_analysis(
    sites: Sequence[NiptSiteObservation],
    qc: NiptQualityThresholds,
    *,
    artifact_lookup: Callable[[str], bool] = lambda _variant_id: False,
    overdispersion: float = OVERDISPERSION,
) -> NiptAnalysisResult:
    """Filter, estimate FF, classify, and tally."""
    filtered = filter_sites_and_estimate_ff(sites, qc, artifact_lookup=artifact_lookup)
    ff_estimate = filtered.fetal_fraction
    classifications = [
        classify_site(site, ff_estimate, qc, overdispersion=overdispersion)
        for site in filtered.passed
    ]

    category_counts = {category: 0 for category in range(1, 9)}
    for classification in classifications:
        if classification.category is not None:
            category_counts[classification.category] += 1

    return NiptAnalysisResult(
        fetal_fraction=ff_estimate,
        category_counts=category_counts,
        filter_counts=filtered.filter_counts,
        classifications=classifications,
        fetal_sex=infer_fetal_sex(filtered.passed, ff_estimate, qc),
        paternal_transmission=paternal_transmission_evidence(
            filtered.passed, ff_estimate, qc, overdispersion=overdispersion
        ),
        quality_failure_counts=filtered.quality_failure_counts,
    )


# Below this FF a male fetus's chrY share of the plasma can sit under the noise floor of
# the chrY/autosome ratio (0.02; about 0.9 x FF in the validation cohort), so a missing
# chrY signal does not say "female".
CHRY_FEMALE_MIN_FF = 0.04


def combined_fetal_sex(paternal_x: str, chry_profile: str | None, ff: float) -> str:
    """The fetal sex from the two independent signals: the father's X alleles in the plasma
    (``infer_fetal_sex``) and the plasma's chrY coverage (``nipt_target_coverage``).

    ``female`` or ``male`` when they agree or one is indeterminate, ``discordant`` when they
    disagree, ``indeterminate`` when neither tells.
    """
    if chry_profile == "female_with_male_fetal_signal":
        chry = "male"
    elif chry_profile == "female_no_chrY_signal" and ff >= CHRY_FEMALE_MIN_FF:
        chry = "female"
    else:
        chry = "indeterminate"
    calls = {call for call in (paternal_x, chry) if call in ("female", "male")}
    if len(calls) > 1:
        return "discordant"
    return calls.pop() if calls else "indeterminate"
