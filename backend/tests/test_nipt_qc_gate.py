"""The monogenic NIPT's QC gate on the expected fetal reads (REQ-NIPT-021): median target depth
x FF/2 against QA's bound, or the provisional one."""

from __future__ import annotations

import pytest

from backend.app.services.nipt_qc_gate import (
    EXPECTED_FETAL_READS,
    MNIPT_THRESHOLD_PROFILE,
    PROVISIONAL_EXPECTED_FETAL_READS,
    NiptGateBound,
    expected_fetal_reads,
    expected_fetal_reads_verdict,
    fetal_read_depth,
    load_nipt_gate_bound,
    resolve_nipt_gate_bound,
)
from backend.app.services import nipt_qc_gate

QA_BOUND = NiptGateBound(key=EXPECTED_FETAL_READS, warn_value=60.0, error_value=40.0, provisional=False)


def test_the_bound_is_qas_else_the_provisional_one() -> None:
    provisional = resolve_nipt_gate_bound({})
    assert (provisional.warn_value, provisional.error_value) == PROVISIONAL_EXPECTED_FETAL_READS == (None, 40.0)
    assert provisional.provisional is True
    set_by_qa = resolve_nipt_gate_bound({EXPECTED_FETAL_READS: {"warn_value": 60.0, "error_value": 50.0}})
    assert (set_by_qa.warn_value, set_by_qa.error_value, set_by_qa.provisional) == (60.0, 50.0, False)
    # A bound QA set without an error bound stays without one: no provisional stand-in.
    no_error = resolve_nipt_gate_bound({EXPECTED_FETAL_READS: {"warn_value": 60.0, "error_value": None}})
    assert (no_error.error_value, no_error.provisional) == (None, False)


@pytest.mark.asyncio
async def test_the_bound_is_read_from_the_monogenic_nipt_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    async def thresholds(_session: object, profile_key: str) -> dict[str, dict[str, float | None]]:
        asked.append(profile_key)
        return {EXPECTED_FETAL_READS: {"warn_value": None, "error_value": 30.0}}

    monkeypatch.setattr(nipt_qc_gate, "resolve_profile_thresholds", thresholds)
    bound = await load_nipt_gate_bound(None)  # type: ignore[arg-type]
    assert asked == [MNIPT_THRESHOLD_PROFILE] == ["nipt_monogenic"]
    assert (bound.error_value, bound.provisional) == (30.0, False)


def test_the_expected_fetal_reads_and_the_depth_that_gives_the_bound() -> None:
    # A fetal-specific allele has about depth x FF/2 reads.
    assert expected_fetal_reads(1000.0, 0.10) == pytest.approx(50.0)
    assert expected_fetal_reads(None, 0.10) is None
    assert expected_fetal_reads(1000.0, None) is None
    # The review's example: FF 4 % needs 2,000x for 40 reads.
    assert fetal_read_depth(0.04, QA_BOUND) == pytest.approx(2000.0)
    assert fetal_read_depth(None, QA_BOUND) is None
    assert fetal_read_depth(0.0, QA_BOUND) is None
    assert fetal_read_depth(0.1, resolve_nipt_gate_bound({EXPECTED_FETAL_READS: {"error_value": None}})) is None


@pytest.mark.parametrize(
    ("median_depth", "ff", "status"),
    [
        (1000.0, 0.15, "pass"),  # 75 reads
        (1200.0, 0.10, "pass"),  # 60: at the warning bound
        (1000.0, 0.10, "warn"),  # 50: below the warning bound of 60
        (800.0, 0.10, "warn"),  # 40: at the error bound, below the warning bound
        (1000.0, 0.06, "fail"),  # 30: below the error bound of 40
    ],
)
def test_the_verdict_by_the_bounds(median_depth: float, ff: float, status: str) -> None:
    check = expected_fetal_reads_verdict(median_depth, ff, QA_BOUND)
    assert check.status == status
    assert check.expected_reads == pytest.approx(median_depth * ff / 2)
    assert (check.median_depth, check.fetal_fraction, check.bound) == (median_depth, ff, QA_BOUND)


def test_a_failed_gate_says_a_result_without_a_fetal_allele_is_not_reliable() -> None:
    check = expected_fetal_reads_verdict(1000.0, 0.06, QA_BOUND)
    assert check.status == "fail"
    assert check.message == (
        "30 expected fetal reads (median target depth 1000x times half the fetal fraction of 6.0%), "
        "below 40: a fetal-specific allele may be missed, so a result without one is not reliable. "
        "Consider a redraw."
    )


def test_the_provisional_bound_applies_until_qa_sets_one() -> None:
    check = expected_fetal_reads_verdict(1500.0, 0.12, resolve_nipt_gate_bound({}))
    assert (check.status, check.bound.provisional) == ("pass", True)
    assert check.message == (
        "90 expected fetal reads (median target depth 1500x times half the fetal fraction of 12.0%), "
        "at least 40."
    )


def test_a_gate_that_cannot_be_told_fails() -> None:
    no_ff = expected_fetal_reads_verdict(1500.0, None, QA_BOUND)
    assert (no_ff.status, no_ff.message) == ("fail", "Not assessed: no fetal-fraction estimate.")
    no_table = expected_fetal_reads_verdict(None, 0.12, QA_BOUND)
    assert (no_table.status, no_table.message) == (
        "fail",
        "Not assessed: the plasma has no per-target coverage table.",
    )
    no_error = expected_fetal_reads_verdict(
        1500.0, 0.12, resolve_nipt_gate_bound({EXPECTED_FETAL_READS: {"warn_value": 60.0, "error_value": None}})
    )
    assert no_error.status == "fail"
    assert no_error.message.endswith("QA set no error bound, so the gate cannot pass.")
