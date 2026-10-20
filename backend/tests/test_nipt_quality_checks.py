"""The NIPT quality checks' verdicts (REQ-NIPT-015), made once for the NIPT page and the
signed record (``nipt_quality_checks``).

The page made the fetal-fraction and the fetal-sex verdicts in the browser; a signed record
must hold values made by the backend's tested code, so both are made here now, beside the
plasma sample's and paternity's. Synthetic values only.
"""

from __future__ import annotations

import pytest

from backend.app.services.nipt_analysis import (
    CHRY_FEMALE_MIN_FF,
    FetalFractionEstimate,
    FetalSexResult,
    NiptAnalysisResult,
    PaternalTransmissionEvidence,
    chry_fetal_sex,
)
from backend.app.services.nipt_quality_checks import (
    NO_SEX_PROFILE,
    PLASMA_PROFILE_VERDICTS,
    fetal_fraction_verdict,
    fetal_sex_verdict,
    nipt_quality_checks,
    paternity_verdict,
    plasma_sample_verdict,
)
from backend.app.services.nipt_service import NiptFamilySummary
from backend.app.services.nipt_target_coverage import SexChromosomeCoverage, TargetCoverageSummary
from backend.app.services.sample_integrity_qc import evaluate_paternity


def _ff(**fields) -> FetalFractionEstimate:
    values = {
        "ff": 0.12,
        "ff_computed": 0.12,
        "ff_median": 0.118,
        "ci_low": 0.109,
        "ci_high": 0.131,
        "n_sites": 40,
        "method": "category7_pooled",
        "low_confidence": False,
        "vaf_q05": 0.04,
        "vaf_q95": 0.08,
    }
    values.update(fields)
    return FetalFractionEstimate(**values)


def _sex(profile: str, *, y_ratio: float | None = 0.1, x_ratio: float | None = 1.2) -> SexChromosomeCoverage:
    return SexChromosomeCoverage(
        autosomal_mean=1000.0,
        x_mean=1200.0,
        y_mean=100.0,
        y_targets=1,
        x_ratio=x_ratio,
        y_ratio=y_ratio,
        profile=profile,
        fetal_fraction_estimate=0.16,
    )


# --- The fetal fraction ------------------------------------------------------------


def test_no_estimate_fails_the_fetal_fraction_check() -> None:
    verdict = fetal_fraction_verdict(_ff(ff=0.0, ff_computed=None, n_sites=3, low_confidence=True))
    assert verdict.status == "fail"
    assert verdict.message == "Not estimated, from 3 category-7 sites (an estimate needs at least 5)."
    one = fetal_fraction_verdict(_ff(ff=0.0, ff_computed=None, n_sites=1, low_confidence=True))
    assert one.message == "Not estimated, from 1 category-7 site (an estimate needs at least 5)."


def test_a_low_confidence_estimate_warns() -> None:
    verdict = fetal_fraction_verdict(_ff(n_sites=12, low_confidence=True))
    assert verdict.status == "warn"
    assert verdict.message == (
        "12.0% (95% CI 10.9%–13.1%) from 12 category-7 sites: low confidence (fewer than 30 "
        "sites, or an interval wider than ±3 percentage points)."
    )


def test_a_confident_estimate_passes() -> None:
    assert fetal_fraction_verdict(_ff()).status == "pass"
    assert fetal_fraction_verdict(_ff()).message == "12.0% (95% CI 10.9%–13.1%) from 40 category-7 sites."
    # Without an interval, none is named.
    assert fetal_fraction_verdict(_ff(ci_low=None, ci_high=None)).message == "12.0% from 40 category-7 sites."


# --- The fetal sex -------------------------------------------------------------------


def _paternal_x(inferred: str) -> FetalSexResult:
    return FetalSexResult(inferred, 9 if inferred == "female" else 0, 9 if inferred == "male" else 0, 9)


def test_signals_that_disagree_fail_the_fetal_sex_check() -> None:
    verdict = fetal_sex_verdict(_paternal_x("female"), "female_with_male_fetal_signal", 0.12)
    assert (verdict.call, verdict.status, verdict.chry) == ("discordant", "fail", "male")
    assert verdict.message == (
        "The father's X alleles read female and the plasma's chrY coverage reads male: the two "
        "signals disagree; check the samples."
    )


def test_no_signal_that_tells_warns() -> None:
    verdict = fetal_sex_verdict(_paternal_x("indeterminate"), "no_chrY_targets", 0.12)
    assert (verdict.call, verdict.status) == ("indeterminate", "warn")
    assert verdict.message.startswith("Indeterminate: neither the father's X alleles nor")
    # Without chrY signal at a low fetal fraction the chrY coverage tells nothing either.
    low = fetal_sex_verdict(_paternal_x("indeterminate"), "female_no_chrY_signal", CHRY_FEMALE_MIN_FF / 2)
    assert (low.call, low.chry) == ("indeterminate", "indeterminate")
    # No estimate reads as a fetal fraction of 0.
    assert fetal_sex_verdict(_paternal_x("indeterminate"), "female_no_chrY_signal", None).chry == "indeterminate"


@pytest.mark.parametrize(
    ("paternal_x", "profile", "message"),
    [
        ("female", "female_no_chrY_signal", "Female, read from the father's X alleles and the plasma's chrY coverage."),
        ("male", "no_chrY_targets", "Male, read from the father's X alleles."),
        ("indeterminate", "female_with_male_fetal_signal", "Male, read from the plasma's chrY coverage."),
    ],
)
def test_a_fetal_sex_one_signal_or_both_tell_passes(paternal_x: str, profile: str, message: str) -> None:
    verdict = fetal_sex_verdict(_paternal_x(paternal_x), profile, 0.12)
    assert verdict.status == "pass"
    assert verdict.message == message
    assert verdict.paternal_x.inferred == paternal_x and verdict.chry_profile == profile


def test_the_chry_coverage_alone_reads_a_male_or_at_a_fetal_fraction_high_enough_a_female() -> None:
    assert chry_fetal_sex("female_with_male_fetal_signal", 0.0) == "male"
    assert chry_fetal_sex("female_no_chrY_signal", CHRY_FEMALE_MIN_FF) == "female"
    assert chry_fetal_sex("female_no_chrY_signal", CHRY_FEMALE_MIN_FF - 0.001) == "indeterminate"
    assert chry_fetal_sex("male_like_not_maternal_plasma", 0.2) == "indeterminate"
    assert chry_fetal_sex(None, 0.2) == "indeterminate"


# --- The maternal plasma sample and paternity -----------------------------------------


@pytest.mark.parametrize("profile", sorted(PLASMA_PROFILE_VERDICTS))
def test_each_sex_profile_has_its_verdict(profile: str) -> None:
    verdict = plasma_sample_verdict(_sex(profile))
    assert (verdict.status, verdict.message) == PLASMA_PROFILE_VERDICTS[profile]
    assert verdict.profile == profile and verdict.coverage is not None


def test_male_dna_fails_and_no_coverage_is_unknown() -> None:
    assert plasma_sample_verdict(_sex("male_like_not_maternal_plasma")).status == "fail"
    assert plasma_sample_verdict(_sex("high_chrY_review")).status == "warn"
    none = plasma_sample_verdict(None)
    assert (none.status, none.message, none.profile, none.coverage) == (*NO_SEX_PROFILE, None, None)


def test_paternity_is_read_as_the_sample_qc_reads_it() -> None:
    evidence = PaternalTransmissionEvidence(
        hom_alt_transmitted=30, hom_alt_not_transmitted=0, het_transmitted=61, het_not_transmitted=61
    )
    assert paternity_verdict(evidence, "FATHER1") == evaluate_paternity(
        "FATHER1", hom_alt_transmitted=30, hom_alt_not_transmitted=0, het_transmitted=61, het_not_transmitted=61
    )


# --- All of them -----------------------------------------------------------------


def test_every_check_of_a_familys_analysis_with_its_verdict() -> None:
    analysis = NiptAnalysisResult(
        fetal_fraction=_ff(),
        category_counts={category: 0 for category in range(1, 9)},
        filter_counts={"total_in": 10, "passed": 9, "failed_quality": 1, "failed_artifact": 0, "paternal_only": 0},
        classifications=[],
        fetal_sex=_paternal_x("female"),
        paternal_transmission=PaternalTransmissionEvidence(
            hom_alt_transmitted=30, hom_alt_not_transmitted=0, het_transmitted=61, het_not_transmitted=61
        ),
        quality_failure_counts={"low_quality": 1},
    )
    coverage = TargetCoverageSummary(
        targets=3, median_mean=1500.0, q05_mean=300.0, below_critical=1, below_advisory=1, zero_mean=0,
        incomplete=0, critical_mean_depth=300.0, advisory_mean_depth=1000.0,
    )
    summary = NiptFamilySummary(
        analysis=analysis, target_coverage=coverage, sex_chromosomes=_sex("female_with_male_fetal_signal")
    )
    checks = nipt_quality_checks(summary, father="FATHER1")
    assert checks.fetal_fraction_check.status == "pass"
    assert checks.de_novo_window is not None and checks.de_novo_window.strict_min == 0.04
    assert (checks.paternity.father, checks.paternity.status) == ("FATHER1", "pass")
    # The father's X alleles read female, the chrY coverage male: the check fails.
    assert (checks.fetal_sex.call, checks.fetal_sex.status) == ("discordant", "fail")
    assert checks.plasma_sample.status == "pass"
    assert checks.target_coverage is coverage
    assert checks.quality_failures == {"low_quality": 1}
    # Without per-target coverage: no sex profile, read from the father's X alone.
    bare = nipt_quality_checks(NiptFamilySummary(analysis=analysis))
    assert (bare.plasma_sample.status, bare.fetal_sex.call, bare.paternity.father) == ("unknown", "female", "father")
