"""The monogenic NIPT quality checks and their verdicts (REQ-NIPT-015), made in one place.

The NIPT page shows them (``GET /families/{family_id}/nipt/summary``, ``qc``) and the signed
analysis record freezes them (``signed_record_nipt``): the fetal fraction, the fetal sex from
the father's X alleles and from the plasma's chrY coverage, paternity, the maternal plasma
sample's sex profile, the coverage of the capture targets and why cfDNA calls failed the
quality filter, with the de novo window. The page made the fetal-fraction and the fetal-sex
verdicts itself, in the browser; a signed record holds values made by the backend's tested
code, so each verdict is made here and the page and the record both read it.

Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .nipt_analysis import (
    FF_HARD_FLOOR,
    FF_MAX_CI_HALFWIDTH,
    FF_MIN_SITES,
    FetalFractionEstimate,
    FetalSexResult,
    PaternalTransmissionEvidence,
    chry_fetal_sex,
    combined_fetal_sex,
)
from .nipt_target_coverage import SexChromosomeCoverage, TargetCoverageSummary
from .nipt_triage import DeNovoWindow, de_novo_window
from .sample_integrity_qc import PaternityCheck, evaluate_paternity

if TYPE_CHECKING:
    from .nipt_service import NiptFamilySummary

Verdict = Literal["pass", "warn", "fail"]
PlasmaVerdict = Literal["pass", "warn", "fail", "unknown"]

# The plasma's sex profile (nipt_target_coverage.sex_chromosome_coverage) as a verdict: male
# DNA cannot be maternal plasma.
PLASMA_PROFILE_VERDICTS: dict[str, tuple[PlasmaVerdict, str]] = {
    "female_no_chrY_signal": (
        "pass",
        "Female plasma; no chrY signal (a female fetus, or a low fetal fraction).",
    ),
    "female_with_male_fetal_signal": ("pass", "Female plasma with a male fetal chrY signal."),
    "high_chrY_review": (
        "warn",
        "chrY coverage near the autosomal level: check that this is maternal plasma.",
    ),
    "male_like_not_maternal_plasma": (
        "fail",
        "Male DNA (chrY at the autosomal level, chrX low): this sample cannot be maternal plasma.",
    ),
    "no_chrY_targets": ("unknown", "The panel has no chrY target: no sex profile."),
}
NO_SEX_PROFILE: tuple[PlasmaVerdict, str] = ("unknown", "No per-target coverage: no sex profile.")

# The fetal sex call as a verdict: two signals that disagree fail the check, as the NIPT page
# has always marked it; neither telling the sex is a warning.
FETAL_SEX_VERDICTS: dict[str, Verdict] = {
    "female": "pass",
    "male": "pass",
    "indeterminate": "warn",
    "discordant": "fail",
}


@dataclass(slots=True)
class CheckVerdict:
    """A check's verdict and the sentence that says why."""

    status: Verdict
    message: str


@dataclass(slots=True)
class FetalSexVerdict:
    """The fetal sex from the two signals, and the verdict on it."""

    call: str  # female | male | indeterminate | discordant
    status: Verdict
    message: str
    paternal_x: FetalSexResult
    chry_profile: str | None
    # What the chrY coverage alone reads (nipt_analysis.chry_fetal_sex).
    chry: str


@dataclass(slots=True)
class PlasmaSampleVerdict:
    """The maternal plasma sample's sex profile from its chrX and chrY depth."""

    status: PlasmaVerdict
    message: str
    profile: str | None
    coverage: SexChromosomeCoverage | None


@dataclass(slots=True)
class NiptQualityChecks:
    """Every NIPT quality check of a family, with its verdict."""

    fetal_fraction: FetalFractionEstimate
    fetal_fraction_check: CheckVerdict
    de_novo_window: DeNovoWindow | None
    paternity: PaternityCheck
    paternal_transmission: PaternalTransmissionEvidence
    fetal_sex: FetalSexVerdict
    plasma_sample: PlasmaSampleVerdict
    target_coverage: TargetCoverageSummary | None
    quality_failures: dict[str, int]


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _sites(count: int) -> str:
    return f"{count} category-7 site{'' if count == 1 else 's'}"


def fetal_fraction_verdict(ff: FetalFractionEstimate) -> CheckVerdict:
    """``fail`` without an estimate (fewer than ``FF_HARD_FLOOR`` category-7 sites),
    ``warn`` on a low-confidence one (fewer than ``FF_MIN_SITES`` sites, or a 95% interval
    wider than +/- ``FF_MAX_CI_HALFWIDTH``), else ``pass`` (REQ-NIPT-001)."""
    if ff.ff_computed is None:
        return CheckVerdict(
            "fail",
            f"Not estimated, from {_sites(ff.n_sites)} (an estimate needs at least "
            f"{FF_HARD_FLOOR}).",
        )
    interval = (
        f" (95% CI {_percent(ff.ci_low)}–{_percent(ff.ci_high)})"
        if ff.ci_low is not None and ff.ci_high is not None
        else ""
    )
    estimate = f"{_percent(ff.ff_computed)}{interval} from {_sites(ff.n_sites)}"
    if ff.low_confidence:
        return CheckVerdict(
            "warn",
            f"{estimate}: low confidence (fewer than {FF_MIN_SITES} sites, or an interval "
            f"wider than ±{round(FF_MAX_CI_HALFWIDTH * 100)} percentage points).",
        )
    return CheckVerdict("pass", f"{estimate}.")


def fetal_sex_verdict(
    paternal_x: FetalSexResult, chry_profile: str | None, ff_computed: float | None
) -> FetalSexVerdict:
    """The fetal sex from the father's X alleles and the plasma's chrY coverage
    (``combined_fetal_sex``): signals that disagree fail, none that tells warns."""
    ff = ff_computed or 0.0
    call = combined_fetal_sex(paternal_x.inferred, chry_profile, ff)
    chry = chry_fetal_sex(chry_profile, ff)
    if call == "discordant":
        message = (
            f"The father's X alleles read {paternal_x.inferred} and the plasma's chrY coverage "
            f"reads {chry}: the two signals disagree; check the samples."
        )
    elif call == "indeterminate":
        message = (
            "Indeterminate: neither the father's X alleles nor the plasma's chrY coverage "
            "tells the fetal sex."
        )
    else:
        readers = [
            name
            for name, reading in (
                ("the father's X alleles", paternal_x.inferred),
                ("the plasma's chrY coverage", chry),
            )
            if reading == call
        ]
        message = f"{call.capitalize()}, read from {' and '.join(readers)}."
    return FetalSexVerdict(
        call=call,
        status=FETAL_SEX_VERDICTS[call],
        message=message,
        paternal_x=paternal_x,
        chry_profile=chry_profile,
        chry=chry,
    )


def plasma_sample_verdict(sex: SexChromosomeCoverage | None) -> PlasmaSampleVerdict:
    """The plasma's sex profile as a verdict; ``unknown`` without per-target coverage or
    without a chrY target."""
    profile = sex.profile if sex is not None else None
    status, message = PLASMA_PROFILE_VERDICTS.get(profile or "", NO_SEX_PROFILE)
    return PlasmaSampleVerdict(status=status, message=message, profile=profile, coverage=sex)


def paternity_verdict(evidence: PaternalTransmissionEvidence, father: str) -> PaternityCheck:
    """Paternity from the father's alleles in the plasma (REQ-QC-001), as the Sample QC reads
    it (``sample_integrity_qc.evaluate_paternity``)."""
    return evaluate_paternity(
        father,
        hom_alt_transmitted=evidence.hom_alt_transmitted,
        hom_alt_not_transmitted=evidence.hom_alt_not_transmitted,
        het_transmitted=evidence.het_transmitted,
        het_not_transmitted=evidence.het_not_transmitted,
    )


def nipt_quality_checks(summary: NiptFamilySummary, *, father: str = "father") -> NiptQualityChecks:
    """Every quality check of the family's NIPT analysis (``nipt_service.NiptFamilySummary``),
    each with its verdict. ``father`` names the father's sample in the paternity check."""
    analysis = summary.analysis
    ff = analysis.fetal_fraction
    sex = summary.sex_chromosomes
    return NiptQualityChecks(
        fetal_fraction=ff,
        fetal_fraction_check=fetal_fraction_verdict(ff),
        de_novo_window=de_novo_window(ff),
        paternity=paternity_verdict(analysis.paternal_transmission, father),
        paternal_transmission=analysis.paternal_transmission,
        fetal_sex=fetal_sex_verdict(
            analysis.fetal_sex, sex.profile if sex is not None else None, ff.ff_computed
        ),
        plasma_sample=plasma_sample_verdict(sex),
        target_coverage=summary.target_coverage,
        quality_failures=dict(analysis.quality_failure_counts),
    )
