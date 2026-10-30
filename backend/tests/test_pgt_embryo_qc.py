"""The phasing-QC check of a PGT embryo call at the ROI (REQ-PGT-005; TF-01 §3.3).

The owner decided on 2026-10-20 that an embryo call gets a clear warning when its phasing QC
at the ROI falls below a threshold, the call staying shown for the analyst to decide, and on
2026-10-30 that it is counted over the window the call reads, with the markers on each flank of
the ROI checked apart. ``pgt_embryo_qc`` holds the thresholds (provisional, to be confirmed by
the laboratory director and QA) and makes the verdict: these tests pin each threshold at, just
below and just above its value, the sides and flanks whose markers are checked, the order and
content of the findings, and that a QC that was not counted is no pass. On 2026-10-30 the owner
also decided that an embryo whose markers at the ROI mix both of a parent's homologs, as where it
carries both (a trisomy), is not called: these tests pin how that discordance is measured (a
crossover alone does not raise it) and when a side counts as mixed.
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
    paternal: int | tuple[int, int] = 40,
    maternal: int | tuple[int, int] = 40,
    rate: float | None = None,
) -> PgtEmbryoQcOut:
    """An embryo's QC as ``pgt_analysis`` counts it: the rate is errors / sites. A side's markers
    are given per flank (left, right), or as one count on each flank."""
    paternal_left, paternal_right = paternal if isinstance(paternal, tuple) else (paternal, paternal)
    maternal_left, maternal_right = maternal if isinstance(maternal, tuple) else (maternal, maternal)
    return PgtEmbryoQcOut(
        informative_sites=sites,
        mendel_errors=errors,
        mendel_rate=rate if rate is not None else (errors / sites if sites else 0.0),
        paternal_markers=paternal_left + paternal_right,
        maternal_markers=maternal_left + maternal_right,
        paternal_markers_left=paternal_left,
        paternal_markers_right=paternal_right,
        maternal_markers_left=maternal_left,
        maternal_markers_right=maternal_right,
    )


def _findings(verdict) -> list[tuple[str, float, float] | tuple[str, float, float, str]]:
    return [
        (finding.measure, finding.value, finding.threshold, finding.flank)
        if finding.flank
        else (finding.measure, finding.value, finding.threshold)
        for finding in verdict.findings
    ]


BOTH = ("paternal", "maternal")


def test_the_provisional_thresholds_are_the_ones_proposed() -> None:
    # A change here changes what the family page warns about and what a signed record freezes:
    # the laboratory director and QA confirm it (the in-app haplotype reference lists them).
    assert qc_check.MIN_INFORMATIVE_SITES == 50
    assert qc_check.MAX_MENDEL_RATE == 0.02
    assert qc_check.MIN_MARKERS_PER_FLANK == 6
    # 50 informative sites is where one Mendelian error stops exceeding the 2% maximum.
    assert qc_check.MIN_INFORMATIVE_SITES == round(1 / qc_check.MAX_MENDEL_RATE)
    assert qc_check.QC_THRESHOLDS.model_dump() == {
        "min_informative_sites": 50,
        "max_mendel_rate": 0.02,
        "min_markers_per_flank": 6,
        "max_marker_discordance": 0.1,
        "min_discordance_markers": 20,
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
def test_the_markers_on_each_flank_of_each_side_the_call_reads_warn_below_six(markers: int, status: str) -> None:
    expected = status == "warning"
    for side in ("paternal", "maternal"):
        for flank, counts in (("left", (markers, 40)), ("right", (40, markers))):
            verdict = qc_check.embryo_qc_verdict(_qc(**{side: counts}), BOTH)
            assert verdict.status == status, (side, flank)
            assert _findings(verdict) == ([(f"{side}_markers", markers, 6, flank)] if expected else [])


def test_markers_on_one_flank_do_not_make_up_for_the_other() -> None:
    # Many markers right of the ROI and few left of it: the left flank is read on too little,
    # whatever the total.
    verdict = qc_check.embryo_qc_verdict(_qc(paternal=(2, 500), maternal=(0, 0)), BOTH)
    assert _findings(verdict) == [
        ("paternal_markers", 2, 6, "left"),
        ("maternal_markers", 0, 6, "left"),
        ("maternal_markers", 0, 6, "right"),
    ]


def test_only_the_sides_the_call_reads_are_checked() -> None:
    # A dominant disorder from the father: the call reads his side alone, so the mother being
    # homozygous across the ROI (no marker on her side) raises nothing.
    father_only = qc_check.embryo_qc_verdict(_qc(paternal=12, maternal=0), ("paternal",))
    assert (father_only.status, father_only.sides, father_only.findings) == ("pass", ["paternal"], [])
    # A son at chrX outside the PARs is read on his mother's X alone.
    son = qc_check.embryo_qc_verdict(_qc(paternal=0, maternal=(3, 9)), ("maternal",))
    assert _findings(son) == [("maternal_markers", 3, 6, "left")]
    # No disease haplotype resolved: the call reads no side, and only the side-free measures
    # can warn.
    none = qc_check.embryo_qc_verdict(_qc(sites=20, paternal=0, maternal=0), ())
    assert (none.sides, _findings(none)) == ([], [("informative_sites", 20, 50)])


def test_the_sides_are_listed_paternal_first_whatever_order_they_come_in() -> None:
    verdict = qc_check.embryo_qc_verdict(_qc(), ("maternal", "paternal"))
    assert verdict.sides == ["paternal", "maternal"]


def test_every_measure_outside_its_threshold_is_named_in_a_fixed_order() -> None:
    verdict = qc_check.embryo_qc_verdict(_qc(sites=6, errors=1, paternal=(3, 1), maternal=(2, 0)), BOTH)
    assert verdict.status == "warning"
    assert _findings(verdict) == [
        ("informative_sites", 6, 50),
        ("mendel_rate", 1 / 6, 0.02),
        ("paternal_markers", 3, 6, "left"),
        ("paternal_markers", 1, 6, "right"),
        ("maternal_markers", 2, 6, "left"),
        ("maternal_markers", 0, 6, "right"),
    ]
    # Counts stay whole numbers in the record; the rate is a fraction; a marker count names its flank.
    dumped = verdict.model_dump(mode="json")
    assert dumped["findings"][0] == {"measure": "informative_sites", "value": 6, "threshold": 50, "flank": None}
    assert isinstance(dumped["findings"][0]["value"], int)
    assert dumped["findings"][1]["threshold"] == 0.02
    assert dumped["findings"][2] == {"measure": "paternal_markers", "value": 3, "threshold": 6, "flank": "left"}


def test_the_verdict_is_made_against_the_thresholds_it_is_given() -> None:
    # The analysis states the thresholds in its rules and makes each verdict against that same
    # object, so the record names the thresholds its verdicts rest on.
    stricter = PgtQcThresholdsOut(
        min_informative_sites=300,
        max_mendel_rate=0.01,
        min_markers_per_flank=50,
        max_marker_discordance=0.1,
        min_discordance_markers=20,
    )
    verdict = qc_check.embryo_qc_verdict(_qc(sites=200, errors=3, paternal=(40, 60), maternal=60), BOTH, stricter)
    assert _findings(verdict) == [
        ("informative_sites", 200, 300),
        ("mendel_rate", 0.015, 0.01),
        ("paternal_markers", 40, 50, "left"),
    ]
    # Without thresholds given, the ones the analysis states.
    assert qc_check.embryo_qc_verdict(_qc(), BOTH).status == "pass"


def test_a_qc_that_was_not_counted_is_no_pass() -> None:
    # The window held more sites than the marker review reads, or the embryo is not recorded as
    # the couple's child: the call could not be checked, which is not a pass.
    verdict = qc_check.embryo_qc_verdict(None, ("paternal",))
    assert (verdict.status, verdict.sides, verdict.findings) == ("not_counted", ["paternal"], [])


# --- Markers that mix both of a parent's homologs -----------------------------------------


def test_one_homolog_has_no_discordance() -> None:
    assert qc_check.marker_discordance([0] * 40) == 0.0
    assert qc_check.marker_discordance([1] * 40) == 0.0


def test_a_crossover_alone_raises_no_discordance() -> None:
    # Two homologs either side of one crossover: the reading the measure allows for.
    assert qc_check.marker_discordance([0] * 25 + [1] * 15) == 0.0


def test_markers_off_the_best_reading_are_the_discordance() -> None:
    # One homolog with 2 stray markers in 40, then a crossover: 2 of 40 off the best reading.
    markers = [0] * 10 + [1] + [0] * 9 + [1] * 19 + [0]
    assert qc_check.marker_discordance(markers) == pytest.approx(2 / 40)


def test_both_homologs_interleaved_read_as_nearly_half_mixed() -> None:
    # Imputation forced into two copies assigns an embryo's sites to either homolog it carries.
    # The one crossover the reading allows absorbs a marker: 19 of 40 are off it.
    assert qc_check.marker_discordance([0, 1] * 20) == pytest.approx(19 / 40)
    assert qc_check.marker_discordance([0, 0, 1] * 20) == pytest.approx(19 / 60)


def test_no_markers_no_discordance() -> None:
    assert qc_check.marker_discordance([]) == 0.0


def _mixed(maternal: float, *, markers: int = 40, paternal: float = 0.0, sides=BOTH):
    """A QC with ``markers`` on each side (all left of the ROI) and the discordances given."""
    qc = _qc(paternal=(markers, 0), maternal=(markers, 0)).model_copy(
        update={"paternal_discordance": paternal, "maternal_discordance": maternal}
    )
    return qc_check.mixed_homolog_sides(qc, sides)


@pytest.mark.parametrize(
    ("discordance", "mixed"),
    [(0.0, []), (0.10, []), (0.11, ["maternal"]), (0.5, ["maternal"])],
)
def test_a_side_mixes_both_homologs_above_ten_percent(discordance: float, mixed: list[str]) -> None:
    # At exactly the maximum the side is read as one homolog.
    assert _mixed(discordance) == mixed


def test_a_side_with_too_few_markers_is_not_judged() -> None:
    # 19 markers: a few discordant sites are noise.
    assert _mixed(0.5, markers=19) == []
    assert _mixed(0.5, markers=20) == ["maternal"]


def test_only_the_sides_the_call_reads_are_judged_for_mixing() -> None:
    assert _mixed(0.5, sides=("paternal",)) == []
    assert _mixed(0.5, paternal=0.5, sides=("paternal",)) == ["paternal"]
    assert _mixed(0.5, paternal=0.5) == ["paternal", "maternal"]


def test_without_a_qc_no_side_is_judged_for_mixing() -> None:
    assert qc_check.mixed_homolog_sides(None, BOTH) == []
