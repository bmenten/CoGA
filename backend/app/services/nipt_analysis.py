"""Monogenic NIPT fetal-fraction estimation and variant classification.

This is the analytical core of the monogenic NIPT feature (Phase 3). Given
per-site observations joining a father germline call with a maternal-plasma
cfDNA call, it:

  1. estimates the fetal fraction (FF) from category-7 sites,
  2. classifies every cfDNA variant into one of the eight maternal/fetal
     categories using a beta-binomial likelihood against FF-defined centres,
  3. reports per-category counts and quality/artifact filter counts.

It is pure Python (no numpy/scipy, no I/O): the two core functions
``estimate_fetal_fraction`` and ``classify_site`` operate on dataclasses, and
``run_nipt_analysis`` orchestrates filtering + estimation + classification over
an already-loaded list of sites. Wiring to ClickHouse and the assay artifact
list happens later (Phase 5).

See docs/monogenic-nipt.md for the implementation notes and
frontend/src/content/docs/monogenic-nipt.md for the model and the rules.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

_EPS = 1e-4
# De novo (category 1) is rare; down-weight it so a de novo call needs strong
# likelihood evidence rather than winning on a near-tie at the FF/2 centre. With a
# trusted hom-ref father call and min_cf_alt_reads alt reads, this prior is the only
# bar: the site's VAF is not separately tested against the FF interval, so a present
# low-VAF site well below FF/2 still scores as category 1 (no other candidate sits
# near 0).
_DE_NOVO_PRIOR_WEIGHT = 0.02
_LOG_PRIOR: dict[int, float] = {1: math.log(_DE_NOVO_PRIOR_WEIGHT)}

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


@dataclass(slots=True)
class NiptSiteObservation:
    """A single biallelic site joining the father and cfDNA calls."""

    variant_id: str
    chrom: str
    pos: int
    is_autosomal: bool
    # cfDNA (maternal plasma mixture) -- the signal we classify.
    cf_present: bool
    cf_dp: int | None
    cf_alt_reads: int | None
    cf_vaf: float | None
    cf_qual: float | None
    # father -- germline; prunes the candidate categories. The analysis reads it as
    # 'missing' when father_dp / father_qual fall below the thresholds.
    father_state: str  # 'hom_ref' | 'het' | 'hom_alt' | 'missing'
    father_dp: int | None = None
    father_qual: float | None = None


@dataclass(slots=True)
class NiptQualityThresholds:
    min_cf_dp: int = 20
    min_cf_alt_reads: int = 3
    min_qual: float = 20.0
    min_vaf: float = 0.0
    min_father_dp: int = 10
    min_father_qual: float = 20.0


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
class NiptAnalysisResult:
    fetal_fraction: FetalFractionEstimate
    category_counts: dict[int, int]
    filter_counts: dict[str, int]
    classifications: list[NiptClassification]
    fetal_sex: FetalSexResult = field(
        default_factory=lambda: FetalSexResult("indeterminate", 0, 0, 0)
    )
    # The paternity evidence: category-7 (transmitted) and category-8 (absent) sites
    # whose father call is confident -- called, at min_father_dp or deeper. A missing
    # or thin call lands in category 7 on the de novo prior alone, which says nothing
    # about who the father is, so category_counts[7] is not paternity evidence.
    paternal_evidence: dict[int, int] = field(default_factory=lambda: {7: 0, 8: 0})


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
    half = ff / 2.0
    return {
        1: half,
        2: 0.5 - half,
        3: 0.5,
        4: 0.5 + half,
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
_CANDIDATES_BY_FATHER: dict[str, tuple[int, ...]] = {
    "hom_ref": (1, 2, 3, 5),
    "het": (2, 3, 4, 5, 6, 7),
    "hom_alt": (3, 4, 6, 7),
    "missing": (1, 2, 3, 4, 5, 6, 7),
}


def _candidate_categories(father_state: str) -> tuple[int, ...]:
    return _CANDIDATES_BY_FATHER.get(father_state, _CANDIDATES_BY_FATHER["missing"])


def _trusted_father_state(site: NiptSiteObservation, qc: NiptQualityThresholds) -> str:
    """The father's genotype state, or ``missing`` when his call is too thin to trust.

    The candidate categories, the category-8 absence check, the FF sites and the
    paternal-X fetal sex all rest on the father's genotype, so a call below
    ``min_father_dp`` (or ``min_father_qual``, when a quality is known) counts as no
    call. A het father read as hom-ref at 3x would otherwise turn his transmitted
    allele into a de novo and rule out a hom-alt fetus.
    """
    if site.father_state == "missing":
        return "missing"
    if (site.father_dp or 0) < qc.min_father_dp:
        return "missing"
    if site.father_qual is not None and site.father_qual < qc.min_father_qual:
        return "missing"
    return site.father_state


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


# --------------------------------------------------------------------------- #
# Fetal fraction estimation
# --------------------------------------------------------------------------- #

def _is_ff_site(site: NiptSiteObservation, qc: NiptQualityThresholds, vaf_ceiling: float) -> bool:
    """A category-7 site: father carries, mother hom-ref, present at low VAF."""
    if not site.is_autosomal:
        return False
    if _trusted_father_state(site, qc) not in ("het", "hom_alt"):
        return False
    if not site.cf_present:
        return False
    if (site.cf_dp or 0) < qc.min_cf_dp:
        return False
    if (site.cf_alt_reads or 0) < qc.min_cf_alt_reads:
        return False
    if site.cf_qual is not None and site.cf_qual < qc.min_qual:
        return False
    vaf = _site_vaf(site)
    return 0.0 < vaf <= vaf_ceiling


def estimate_fetal_fraction(
    sites: Iterable[NiptSiteObservation],
    qc: NiptQualityThresholds,
    *,
    vaf_ceiling: float = 0.25,
    min_sites: int = 30,
    hard_floor: int = 5,
    max_ci_halfwidth: float = 0.03,
) -> FetalFractionEstimate:
    """Estimate FF from category-7 sites.

    The headline estimate is the depth-pooled ``2 * sum(alt)/sum(dp)`` with a
    Wilson CI; the per-site median is reported as a robustness cross-check.
    """
    cat7 = [site for site in sites if _is_ff_site(site, qc, vaf_ceiling)]
    n_sites = len(cat7)

    ff_computed: float | None = None
    ff_median: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None

    if n_sites >= 1:
        ff_median = 2.0 * statistics.median(_site_vaf(site) for site in cat7)
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
    )


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
) -> NiptClassification:
    label = _CATEGORY_LABELS[category] if category is not None else "undetermined"
    return NiptClassification(
        variant_id=site.variant_id,
        category=category,
        category_label=label,
        maternal_state=maternal_state,
        fetal_inheritance=fetal_inheritance,
        expected_vaf=expected_vaf,
        observed_vaf=site.cf_vaf if site.cf_vaf is not None else (_site_vaf(site) if site.cf_present else None),
        confidence=confidence,
        runner_up_category=runner_up_category,
        runner_up_confidence=runner_up_confidence,
        flags=flags,
    )


def classify_site(
    site: NiptSiteObservation,
    ff_estimate: FetalFractionEstimate,
    qc: NiptQualityThresholds,
    *,
    overdispersion: float = 0.005,
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
    present = bool(site.cf_present) and n > 0 and k >= qc.min_cf_alt_reads
    father_state = _trusted_father_state(site, qc)

    # ---- Absence handling ------------------------------------------------- #
    if not present:
        if father_state == "hom_alt":
            if n < qc.min_cf_dp:
                flags.append("low_depth_dropout")
                return _classification(
                    site, category=None, maternal_state="hom_ref",
                    fetal_inheritance="unknown", expected_vaf=ff / 2.0,
                    confidence=0.0, flags=flags,
                )
            if n * ff / 2.0 >= detect_min:
                flags.append("false_negative")
                return _classification(
                    site, category=8, maternal_state="hom_ref",
                    fetal_inheritance="paternal_not_transmitted", expected_vaf=ff / 2.0,
                    confidence=1.0, flags=flags,
                )
            flags.append("undetectable_at_ff")
            return _classification(
                site, category=None, maternal_state="hom_ref",
                fetal_inheritance="paternal_not_transmitted", expected_vaf=ff / 2.0,
                confidence=0.0, flags=flags,
            )
        if father_state == "het":
            return _classification(
                site, category=None, maternal_state="hom_ref",
                fetal_inheritance="paternal_not_transmitted", expected_vaf=ff / 2.0,
                confidence=0.0, flags=flags,
            )
        flags.append("no_alt_signal")
        return _classification(
            site, category=None, maternal_state="hom_ref",
            fetal_inheritance="unknown", expected_vaf=0.0, confidence=0.0, flags=flags,
        )

    # ---- Present: likelihood over the candidate categories ---------------- #
    if father_state == "missing":
        flags.append("father_no_coverage")

    candidates = _candidate_categories(father_state)
    log_scores = {
        category: _log_beta_binom(k, n, _expected_vaf(category, ff), overdispersion)
        + _LOG_PRIOR.get(category, 0.0)
        for category in candidates
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
    if ff < ff_too_low and best in _FF_LIMITED_CATEGORIES:
        # The fetal contribution is an indistinguishable perturbation on the
        # maternal genotype; report the maternal state but not the fetal call.
        flags.append("ff_too_low")
        fetal_inheritance = "unknown"

    return _classification(
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


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def _passes_quality(site: NiptSiteObservation, qc: NiptQualityThresholds) -> bool:
    """Technical-quality gate: depth and QUAL.

    Deliberately not a VAF floor -- the low-VAF FF/2 signals (de novo, paternal
    transmission) and the zero-VAF false negatives (category 8) must survive to
    classification.
    """
    if (site.cf_dp or 0) < qc.min_cf_dp:
        return False
    if site.cf_qual is not None and site.cf_qual < qc.min_qual:
        return False
    return True


# chrX pseudo-autosomal regions carry no X-vs-Y transmission signal; exclude them.
# Conservative bounds spanning the GRCh37 and GRCh38 PAR1/PAR2 coordinates.
_X_PAR1_MAX = 2_800_000
_X_PAR2_MIN = 154_900_000
# A daughter's paternal-X allele shows in cfDNA at ~FF/2; a higher VAF means the
# mother carries it (uninformative for paternal transmission).
_FETAL_SEX_MATERNAL_VAF_FLOOR = 0.30
MIN_FETAL_SEX_SITES = 8
MIN_TRANSMITTED_FOR_FEMALE = 3


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
    """
    ff = ff_estimate.ff
    transmitted = 0
    not_transmitted = 0
    for site in sites:
        if site.is_autosomal or not _is_nonpar_x(site.chrom, site.pos):
            continue
        # Hemizygous: a haploid "1" or a diploid "1/1" call both read hom_alt.
        if _trusted_father_state(site, qc) != "hom_alt":
            continue
        n = site.cf_dp or 0
        k = site.cf_alt_reads or 0
        present = bool(site.cf_present) and n > 0 and k >= qc.min_cf_alt_reads
        if present:
            vaf = site.cf_vaf if site.cf_vaf is not None else (k / n if n else 0.0)
            if vaf < _FETAL_SEX_MATERNAL_VAF_FLOOR:
                transmitted += 1
        elif n * ff / 2.0 >= detect_min:
            not_transmitted += 1
    informative = transmitted + not_transmitted
    if informative < MIN_FETAL_SEX_SITES:
        inferred = "indeterminate"
    elif transmitted >= MIN_TRANSMITTED_FOR_FEMALE:
        inferred = "female"  # paternal X transmitted -> daughter
    elif transmitted == 0:
        inferred = "male"  # paternal X never transmitted -> son
    else:
        inferred = "indeterminate"
    return FetalSexResult(inferred, transmitted, not_transmitted, informative)


@dataclass(slots=True)
class NiptFilteredSites:
    """The sites that pass the quality and artifact filters, the funnel counts, and the
    fetal fraction estimated over those sites."""

    passed: list[NiptSiteObservation]
    filter_counts: dict[str, int]
    fetal_fraction: FetalFractionEstimate


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
    """
    total_in = 0
    failed_quality = 0
    failed_artifact = 0
    passed: list[NiptSiteObservation] = []
    for site in sites:
        total_in += 1
        if not _passes_quality(site, qc):
            failed_quality += 1
            continue
        if artifact_lookup(site.variant_id):
            failed_artifact += 1
            continue
        passed.append(site)
    return NiptFilteredSites(
        passed=passed,
        filter_counts={
            "total_in": total_in,
            "passed": len(passed),
            "failed_quality": failed_quality,
            "failed_artifact": failed_artifact,
        },
        fetal_fraction=estimate_fetal_fraction(passed, qc),
    )


def run_nipt_analysis(
    sites: Sequence[NiptSiteObservation],
    qc: NiptQualityThresholds,
    *,
    artifact_lookup: Callable[[str], bool] = lambda _variant_id: False,
    overdispersion: float = 0.005,
) -> NiptAnalysisResult:
    """Filter, estimate FF, classify, and tally."""
    filtered = filter_sites_and_estimate_ff(sites, qc, artifact_lookup=artifact_lookup)
    ff_estimate = filtered.fetal_fraction
    classifications = [
        classify_site(site, ff_estimate, qc, overdispersion=overdispersion)
        for site in filtered.passed
    ]

    category_counts = {category: 0 for category in range(1, 9)}
    paternal_evidence = {7: 0, 8: 0}
    for site, classification in zip(filtered.passed, classifications):
        if classification.category is None:
            continue
        category_counts[classification.category] += 1
        if classification.category in paternal_evidence and _trusted_father_state(
            site, qc
        ) in ("het", "hom_alt"):
            paternal_evidence[classification.category] += 1

    return NiptAnalysisResult(
        fetal_fraction=ff_estimate,
        category_counts=category_counts,
        filter_counts=filtered.filter_counts,
        classifications=classifications,
        fetal_sex=infer_fetal_sex(filtered.passed, ff_estimate, qc),
        paternal_evidence=paternal_evidence,
    )
