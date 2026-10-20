"""The phasing-QC check of a PGT embryo call at the ROI (REQ-PGT-005; TF-01 §3.3).

The owner decided on 2026-10-20 that an embryo call gets a clear warning when its phasing QC
at the ROI falls below a threshold, the call staying shown for the analyst to decide.
``pgt_embryo_qc`` holds the thresholds (provisional, to be confirmed by the laboratory
director and QA) and makes the verdict: these tests pin each threshold at, just below and just
above its value, the sides whose markers are checked, the order and content of the findings,
and that a QC that was not counted is no pass.
"""

from __future__ import annotations

import pytest

from backend.app.schemas import PgtEmbryoQcOut, PgtQcThresholdsOut
from backend.app.services import pgt_analysis
from backend.app.services import pgt_embryo_qc as qc_check


def _qc(
    *,
    sites: int = 200,
    errors: int = 0,
    paternal: int = 40,
    maternal: int = 40,
    rate: float | None = None,
) -> PgtEmbryoQcOut:
    """An embryo's QC at the ROI as ``pgt_analysis`` counts it: the rate is errors / sites."""
    return PgtEmbryoQcOut(
        informative_sites=sites,
        mendel_errors=errors,
        mendel_rate=rate if rate is not None else (errors / sites if sites else 0.0),
        paternal_markers=paternal,
        maternal_markers=maternal,
    )


def _findings(verdict) -> list[tuple[str, float, float]]:
    return [(finding.measure, finding.value, finding.threshold) for finding in verdict.findings]


BOTH = ("paternal", "maternal")


def test_the_provisional_thresholds_are_the_ones_proposed() -> None:
    # A change here changes what the family page warns about and what a signed record freezes:
    # the laboratory director and QA confirm it (the in-app haplotype reference lists them).
    assert qc_check.MIN_INFORMATIVE_SITES == 50
    assert qc_check.MAX_MENDEL_RATE == 0.02
    assert qc_check.MIN_MARKERS_PER_SIDE == 6
    # 50 informative sites is where one Mendelian error stops exceeding the 2% maximum.
    assert qc_check.MIN_INFORMATIVE_SITES == round(1 / qc_check.MAX_MENDEL_RATE)
    assert qc_check.QC_THRESHOLDS.model_dump() == {
        "min_informative_sites": 50,
        "max_mendel_rate": 0.02,
        "min_markers_per_side": 6,
    }
    # The analysis states these, in one place: its rules hold the same object.
    assert pgt_analysis.RULES.qc_thresholds is qc_check.QC_THRESHOLDS


def test_a_qc_within_every_threshold_passes() -> None:
    verdict = qc_check.embryo_qc_verdict(_qc(), BOTH)
    assert (verdict.status, verdict.sides, verdict.findings) == ("pass", ["paternal", "maternal"], [])


@pytest.mark.parametrize(
    ("sites", "status"),
    [(0, "warning"), (49, "warning"), (50, "pass"), (51, "pass")],
)
def test_the_informative_sites_warn_below_fifty(sites: int, status: str) -> None:
    verdict = qc_check.embryo_qc_verdict(_qc(sites=sites, paternal=10, maternal=10), BOTH)
    assert verdict.status == status
    assert _findings(verdict) == ([("informative_sites", sites, 50)] if status == "warning" else [])


@pytest.mark.parametrize(
    ("errors", "sites", "status"),
    [
        (1, 50, "pass"),  # exactly 2%: at the maximum, not above it
        (1, 49, "warning"),  # 2.04%
        (2, 101, "pass"),  # 1.98%
        (2, 99, "warning"),  # 2.02%
        (3, 150, "pass"),  # exactly 2% again, from other counts
        (0, 50, "pass"),
    ],
)
def test_the_mendelian_error_rate_warns_above_two_percent(errors: int, sites: int, status: str) -> None:
    # Fifty or more sites, so only the rate can warn (bar 49 sites, which also warns on sites).
    verdict = qc_check.embryo_qc_verdict(_qc(sites=sites, errors=errors), BOTH)
    rate_findings = [finding for finding in _findings(verdict) if finding[0] == "mendel_rate"]
    if status == "warning":
        assert rate_findings == [("mendel_rate", errors / sites, 0.02)]
    else:
        assert rate_findings == []
        assert verdict.status == ("warning" if sites < 50 else "pass")


def test_the_rate_is_read_as_served() -> None:
    # The verdict reads the rate the QC carries, which is what the record freezes and shows.
    verdict = qc_check.embryo_qc_verdict(_qc(sites=100, errors=1, rate=0.0201), BOTH)
    assert _findings(verdict) == [("mendel_rate", 0.0201, 0.02)]


@pytest.mark.parametrize(("markers", "status"), [(0, "warning"), (5, "warning"), (6, "pass"), (7, "pass")])
def test_the_markers_on_each_side_the_call_reads_warn_below_six(markers: int, status: str) -> None:
    paternal = qc_check.embryo_qc_verdict(_qc(paternal=markers), BOTH)
    maternal = qc_check.embryo_qc_verdict(_qc(maternal=markers), BOTH)
    assert (paternal.status, maternal.status) == (status, status)
    expected = status == "warning"
    assert _findings(paternal) == ([("paternal_markers", markers, 6)] if expected else [])
    assert _findings(maternal) == ([("maternal_markers", markers, 6)] if expected else [])


def test_only_the_sides_the_call_reads_are_checked() -> None:
    # A dominant disorder from the father: the call reads his side alone, so the mother being
    # homozygous across the ROI (no marker on her side) raises nothing.
    father_only = qc_check.embryo_qc_verdict(_qc(paternal=12, maternal=0), ("paternal",))
    assert (father_only.status, father_only.sides, father_only.findings) == ("pass", ["paternal"], [])
    # A son at chrX outside the PARs is read on his mother's X alone.
    son = qc_check.embryo_qc_verdict(_qc(paternal=0, maternal=3), ("maternal",))
    assert _findings(son) == [("maternal_markers", 3, 6)]
    # No disease haplotype resolved: the call reads no side, and only the side-free measures
    # can warn.
    none = qc_check.embryo_qc_verdict(_qc(sites=20, paternal=0, maternal=0), ())
    assert (none.sides, _findings(none)) == ([], [("informative_sites", 20, 50)])


def test_the_sides_are_listed_paternal_first_whatever_order_they_come_in() -> None:
    verdict = qc_check.embryo_qc_verdict(_qc(), ("maternal", "paternal"))
    assert verdict.sides == ["paternal", "maternal"]


def test_every_measure_outside_its_threshold_is_named_in_a_fixed_order() -> None:
    verdict = qc_check.embryo_qc_verdict(_qc(sites=6, errors=1, paternal=3, maternal=2), BOTH)
    assert verdict.status == "warning"
    assert _findings(verdict) == [
        ("informative_sites", 6, 50),
        ("mendel_rate", 1 / 6, 0.02),
        ("paternal_markers", 3, 6),
        ("maternal_markers", 2, 6),
    ]
    # Counts stay whole numbers in the record; the rate is a fraction.
    dumped = verdict.model_dump(mode="json")
    assert dumped["findings"][0] == {"measure": "informative_sites", "value": 6, "threshold": 50}
    assert isinstance(dumped["findings"][0]["value"], int)
    assert dumped["findings"][1]["threshold"] == 0.02


def test_the_verdict_is_made_against_the_thresholds_it_is_given() -> None:
    # The analysis states the thresholds in its rules and makes each verdict against that same
    # object, so the record names the thresholds its verdicts rest on.
    stricter = PgtQcThresholdsOut(min_informative_sites=300, max_mendel_rate=0.01, min_markers_per_side=50)
    verdict = qc_check.embryo_qc_verdict(_qc(sites=200, errors=3, paternal=40, maternal=60), BOTH, stricter)
    assert _findings(verdict) == [
        ("informative_sites", 200, 300),
        ("mendel_rate", 0.015, 0.01),
        ("paternal_markers", 40, 50),
    ]
    # Without thresholds given, the ones the analysis states.
    assert qc_check.embryo_qc_verdict(_qc(), BOTH).status == "pass"


def test_a_qc_that_was_not_counted_is_no_pass() -> None:
    # The ROI held more sites than the marker review reads, or the embryo is not recorded as
    # the couple's child: the call could not be checked, which is not a pass.
    verdict = qc_check.embryo_qc_verdict(None, ("paternal",))
    assert (verdict.status, verdict.sides, verdict.findings) == ("not_counted", ["paternal"], [])
