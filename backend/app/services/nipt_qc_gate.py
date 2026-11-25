"""The monogenic NIPT's QC gate on the fetal reads (REQ-NIPT-021).

A fetal-specific allele (the father's, or one arisen de novo) has about depth x FF/2 reads in
the maternal plasma. The R NIPT-M v0.7.3 validation's review gates each sample on it: the
plasma's median target depth times FF/2, its **expected fetal reads**, must be 40 or more.
Below that, a fetal-specific allele may be missed: a result without one is not reliable, and a
redraw is the remedy. A failed gate fails the check; the sign-out then asks for a reason, as for
the other failed NIPT checks (the owner's decision of 2026-11-25). An allele that was found
stays a finding.

The bound is the laboratory's to set, on the QC thresholds page (profile ``nipt_monogenic``,
metric ``mnipt.expected_fetal_reads``, ``qc_threshold_service``), each change with its reason in
the append-only history. Until QA sets it, the provisional value below applies, the page and the
record say so, and the sign-out asks for a reason (as the conventional NIPT's provisional bounds
do).

The same bound marks the capture targets too thin for it: a target whose mean depth is below
2 x bound / FF (``fetal_read_depth``) expects fewer reads of a fetal-specific allele than the
gate asks of the sample. The coverage QC counts them, over all targets and over the diagnostic
panel's.

Pure, but for ``load_nipt_gate_bound``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from .qc_threshold_service import resolve_profile_thresholds

Verdict = Literal["pass", "warn", "fail"]

#: The QC-threshold profile whose bound the gate reads: the monogenic NIPT's assay.
MNIPT_THRESHOLD_PROFILE = "nipt_monogenic"
#: The gate's metric in the QC-threshold catalogue (``qc_threshold_service.QC_METRICS``).
EXPECTED_FETAL_READS = "mnipt.expected_fetal_reads"
#: The provisional (warning, error) bound until QA sets one: the R NIPT-M v0.7.3 review's 40
#: expected fetal reads (FF 4 % then needs a median depth of 2,000x).
PROVISIONAL_EXPECTED_FETAL_READS: tuple[float | None, float | None] = (None, 40.0)


@dataclass(frozen=True, slots=True)
class NiptGateBound:
    """The gate's bound as the check applies it: QA's (``provisional`` False) or the
    provisional value QA has not replaced."""

    key: str
    warn_value: float | None
    error_value: float | None
    provisional: bool


@dataclass(frozen=True, slots=True)
class ExpectedFetalReadsCheck:
    """The gate's verdict, the sentence that says why, and what it read."""

    status: Verdict
    message: str
    expected_reads: float | None
    median_depth: float | None
    fetal_fraction: float | None
    bound: NiptGateBound


def resolve_nipt_gate_bound(configured: Mapping[str, Mapping[str, float | None]]) -> NiptGateBound:
    """The gate's bound: the one QA set in the monogenic NIPT's profile (``configured``: metric
    key -> ``{"warn_value", "error_value"}``), else the provisional value. A bound QA set without
    an error bound stays without one: the gate then cannot pass, rather than a provisional value
    standing in for one QA chose to leave out."""
    set_by_qa = configured.get(EXPECTED_FETAL_READS)
    if set_by_qa is not None:
        return NiptGateBound(
            key=EXPECTED_FETAL_READS,
            warn_value=set_by_qa.get("warn_value"),
            error_value=set_by_qa.get("error_value"),
            provisional=False,
        )
    warn, error = PROVISIONAL_EXPECTED_FETAL_READS
    return NiptGateBound(key=EXPECTED_FETAL_READS, warn_value=warn, error_value=error, provisional=True)


async def load_nipt_gate_bound(session: AsyncSession) -> NiptGateBound:
    """The gate's bound as QA has set it in the profile ``nipt_monogenic``, else provisional."""
    return resolve_nipt_gate_bound(await resolve_profile_thresholds(session, MNIPT_THRESHOLD_PROFILE))


def expected_fetal_reads(median_depth: float | None, fetal_fraction: float | None) -> float | None:
    """The reads a fetal-specific allele is expected to have: median depth x FF/2."""
    if median_depth is None or fetal_fraction is None:
        return None
    return median_depth * fetal_fraction / 2


def fetal_read_depth(fetal_fraction: float | None, bound: NiptGateBound) -> float | None:
    """The mean target depth at which a fetal-specific allele expects the gate's error bound
    of reads (2 x bound / FF); None without a fetal-fraction estimate or an error bound."""
    if fetal_fraction is None or fetal_fraction <= 0 or bound.error_value is None:
        return None
    return 2 * bound.error_value / fetal_fraction


def expected_fetal_reads_verdict(
    median_depth: float | None, fetal_fraction: float | None, bound: NiptGateBound
) -> ExpectedFetalReadsCheck:
    """``fail`` when the expected fetal reads are below the error bound, or cannot be told (no
    fetal-fraction estimate, no per-target coverage table, no error bound); ``warn`` below the
    warning bound; else ``pass``. Whether the bound is provisional is the bound's to say."""
    reads = expected_fetal_reads(median_depth, fetal_fraction)

    def check(status: Verdict, message: str) -> ExpectedFetalReadsCheck:
        return ExpectedFetalReadsCheck(
            status=status,
            message=message,
            expected_reads=reads,
            median_depth=median_depth,
            fetal_fraction=fetal_fraction,
            bound=bound,
        )

    if fetal_fraction is None:
        return check("fail", "Not assessed: no fetal-fraction estimate.")
    if median_depth is None:
        return check("fail", "Not assessed: the plasma has no per-target coverage table.")
    assert reads is not None
    basis = (
        f"{reads:.0f} expected fetal reads (median target depth {median_depth:.0f}x times "
        f"half the fetal fraction of {fetal_fraction:.1%})"
    )
    if bound.error_value is None:
        return check("fail", f"{basis}; QA set no error bound, so the gate cannot pass.")
    if reads < bound.error_value:
        return check(
            "fail",
            f"{basis}, below {bound.error_value:g}: a fetal-specific allele may be missed, so a "
            "result without one is not reliable. Consider a redraw.",
        )
    if bound.warn_value is not None and reads < bound.warn_value:
        return check(
            "warn",
            f"{basis}, below the warning bound of {bound.warn_value:g}: a fetal-specific allele "
            "is less likely to be found.",
        )
    return check("pass", f"{basis}, at least {bound.error_value:g}.")
