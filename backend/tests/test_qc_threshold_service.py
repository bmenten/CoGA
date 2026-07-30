"""Sequencing-QC threshold evaluation.

These are the rules that decide whether the interface reports a sequencing run as
inadequate, so the edge cases matter more than the happy path: an unmeasured metric, an
unconfigured metric, an inverted bound pair, and the direction of comparison.
"""

from app.services.qc_threshold_service import (
    QC_METRICS,
    QC_METRICS_BY_KEY,
    evaluate_metric,
    evaluate_sequencing_qc,
    metric_value,
    worst_verdict,
)


# The QC block recorded for the reference PacBio HG002 run.
HG002_QC = {
    "report": "qc/nanoplot/HG002/HG002NanoPlot-report.html",
    "reads": {
        "read_length_n50": 14283.0,
        "median_read_quality": 32.1,
        "read_count": 5038912.0,
        "stdev_read_length": 5363.3,
    },
    "depth": {"mean_depth": 18.57, "mito_mean_depth": 1557.21},
}


def test_metric_catalogue_keys_match_their_paths() -> None:
    for metric in QC_METRICS:
        assert metric.path == tuple(metric.key.split("."))
        assert metric.label and metric.unit and metric.help_text


def test_metric_catalogue_has_no_duplicate_keys() -> None:
    assert len(QC_METRICS_BY_KEY) == len(QC_METRICS)


def test_metric_value_follows_the_dotted_path() -> None:
    assert metric_value(HG002_QC, QC_METRICS_BY_KEY["depth.mean_depth"]) == 18.57
    assert metric_value(HG002_QC, QC_METRICS_BY_KEY["reads.read_length_n50"]) == 14283.0
    # A metric the run did not report is absent, not zero.
    assert metric_value(HG002_QC, QC_METRICS_BY_KEY["reads.mean_percent_identity"]) is None
    assert metric_value(None, QC_METRICS_BY_KEY["depth.mean_depth"]) is None


def test_metric_value_rejects_non_numeric_and_boolean() -> None:
    # A bool is an int in Python; treating True as 1.0 would invent a measurement.
    assert metric_value({"depth": {"mean_depth": True}}, QC_METRICS_BY_KEY["depth.mean_depth"]) is None
    assert metric_value({"depth": {"mean_depth": "18.6"}}, QC_METRICS_BY_KEY["depth.mean_depth"]) is None


def test_unmeasured_or_unconfigured_metrics_are_skip_not_pass() -> None:
    # Not measured.
    assert evaluate_metric(None, direction="lower_is_worse", warn_value=20.0, error_value=10.0) == "skip"
    # Measured but no cut-off configured: it has not been checked, and reporting it as
    # a pass would claim a gate that does not exist.
    assert evaluate_metric(5.0, direction="lower_is_worse", warn_value=None, error_value=None) == "skip"


def test_lower_is_worse_boundaries() -> None:
    verdict = lambda value: evaluate_metric(  # noqa: E731
        value, direction="lower_is_worse", warn_value=20.0, error_value=10.0
    )
    assert verdict(25.0) == "pass"
    # Exactly on the bound passes — the bound is the accepted minimum.
    assert verdict(20.0) == "pass"
    assert verdict(19.9) == "warn"
    assert verdict(10.0) == "warn"
    assert verdict(9.9) == "fail"


def test_higher_is_worse_boundaries() -> None:
    verdict = lambda value: evaluate_metric(  # noqa: E731
        value, direction="higher_is_worse", warn_value=6000.0, error_value=8000.0
    )
    assert verdict(5000.0) == "pass"
    assert verdict(6000.0) == "pass"
    assert verdict(6001.0) == "warn"
    assert verdict(8000.0) == "warn"
    assert verdict(9000.0) == "fail"


def test_only_one_bound_configured_is_honoured() -> None:
    assert evaluate_metric(5.0, direction="lower_is_worse", warn_value=None, error_value=10.0) == "fail"
    assert evaluate_metric(15.0, direction="lower_is_worse", warn_value=20.0, error_value=None) == "warn"


def test_inverted_bounds_still_fail_rather_than_warn() -> None:
    # If a warn/error pair is somehow stored the wrong way round, a value below both must
    # not be softened to a warning. The error bound is tested first for exactly this.
    assert evaluate_metric(5.0, direction="lower_is_worse", warn_value=10.0, error_value=20.0) == "fail"


def test_worst_verdict_ranks_error_above_warn_above_pass() -> None:
    assert worst_verdict(["pass", "warn", "skip"]) == "warn"
    assert worst_verdict(["pass", "fail", "warn"]) == "fail"
    assert worst_verdict(["skip", "skip"]) == "skip"
    assert worst_verdict([]) == "skip"
    # A pass outranks skip: something was actually checked.
    assert worst_verdict(["skip", "pass"]) == "pass"


def test_evaluate_sequencing_qc_reports_overall_and_breached() -> None:
    thresholds = {
        # 18.57x is below the 20x warning but above the 10x error.
        "depth.mean_depth": {"warn_value": 20.0, "error_value": 10.0},
        "reads.read_length_n50": {"warn_value": 10000.0, "error_value": 6000.0},
    }

    result = evaluate_sequencing_qc(HG002_QC, thresholds)

    assert result is not None
    assert result["verdict"] == "warn"
    assert result["breached"] == ["depth.mean_depth"]
    by_key = {entry["metric_key"]: entry for entry in result["metrics"]}
    assert by_key["depth.mean_depth"]["verdict"] == "warn"
    assert by_key["reads.read_length_n50"]["verdict"] == "pass"
    # Reported but ungated metrics are present and marked skip, so the UI can show
    # the number without implying it was checked.
    assert by_key["depth.mito_mean_depth"]["verdict"] == "skip"


def test_evaluate_sequencing_qc_escalates_to_error() -> None:
    result = evaluate_sequencing_qc(
        HG002_QC, {"depth.mean_depth": {"warn_value": 30.0, "error_value": 25.0}}
    )

    assert result is not None
    assert result["verdict"] == "fail"
    assert result["breached"] == ["depth.mean_depth"]


def test_evaluate_sequencing_qc_only_includes_measured_metrics() -> None:
    result = evaluate_sequencing_qc(HG002_QC, {})

    assert result is not None
    measured = {entry["metric_key"] for entry in result["metrics"]}
    assert measured == {
        "depth.mean_depth",
        "depth.mito_mean_depth",
        "reads.read_length_n50",
        "reads.median_read_quality",
        "reads.read_count",
        "reads.stdev_read_length",
    }
    # No thresholds at all: nothing is judged, so the run is not claimed to pass.
    assert result["verdict"] == "skip"


def test_evaluate_sequencing_qc_returns_none_without_recorded_qc() -> None:
    assert evaluate_sequencing_qc(None, {}) is None
    assert evaluate_sequencing_qc({}, {}) is None
    # Nested-only content with no scalar the catalogue knows: nothing to evaluate.
    assert evaluate_sequencing_qc({"reads": {"quality_cutoffs": {"q20": {"reads": 1}}}}, {}) is None


def test_only_spread_and_contamination_fail_on_the_high_side() -> None:
    # Everything else fails when it is too LOW. Pin the exceptions so a catalogue edit
    # cannot silently invert a comparison.
    higher_is_worse = [metric.key for metric in QC_METRICS if metric.direction == "higher_is_worse"]
    assert higher_is_worse == ["reads.stdev_read_length", "mtdna.contamination"]


def test_mtdna_metrics_are_configured_here_but_evaluated_elsewhere() -> None:
    # Their cut-offs belong in the one admin place, but their values come from the
    # mitochondrial analysis' own coverage/contamination inputs, not from
    # samples.metadata["sequencing_qc"] — so the sequencing-QC evaluator must skip them
    # rather than reporting them as unmeasured.
    mtdna = {metric.key for metric in QC_METRICS if metric.source == "mtdna"}
    assert mtdna == {"mtdna.mean_depth", "mtdna.contamination"}

    result = evaluate_sequencing_qc(
        HG002_QC, {"mtdna.mean_depth": {"warn_value": 50.0, "error_value": 10.0}}
    )

    assert result is not None
    assert not any(entry["metric_key"].startswith("mtdna.") for entry in result["metrics"])


# --- mitochondrial QC now reads the same configured cut-offs --------------------------


def test_mito_sample_qc_uses_configured_limits_not_constants() -> None:
    from app.schemas import MitoDNACoverageOut
    from app.services.mitochondrial_analysis import _sample_qc

    coverage = MitoDNACoverageOut(mean_depth=30.0)

    # Depth 30x against a 50x warning limit.
    warned = _sample_qc({}, coverage, {"mtdna.mean_depth": {"warn_value": 50.0, "error_value": 10.0}})
    assert warned.status == "warn"
    assert any("50.0" in note for note in warned.notes)

    # The same depth against a stricter limit fails instead. Nothing about this verdict
    # is hard-coded in the analysis module any more.
    failed = _sample_qc({}, coverage, {"mtdna.mean_depth": {"warn_value": 60.0, "error_value": 40.0}})
    assert failed.status == "fail"


def test_mito_sample_qc_without_limits_is_not_assessed() -> None:
    """`skip` — not assessed — never `pass`, and never the old `unknown` spelling."""
    from app.schemas import MitoDNACoverageOut
    from app.services.mitochondrial_analysis import _sample_qc

    # A measured sample with no configured cut-off must not be reported as passing a gate
    # that does not exist — this used to be a hard-coded `< 50x` rule.
    verdict = _sample_qc({}, MitoDNACoverageOut(mean_depth=5.0), {})

    assert verdict.status == "skip"
    assert verdict.notes == []


def test_mito_contamination_fails_on_the_high_side() -> None:
    from app.schemas import MitoDNACoverageOut
    from app.services.mitochondrial_analysis import _sample_qc

    thresholds = {"mtdna.contamination": {"warn_value": 0.01, "error_value": 0.03}}
    coverage = MitoDNACoverageOut()

    assert _sample_qc({"mtdna": {"contamination": 0.005}}, coverage, thresholds).status == "pass"
    assert _sample_qc({"mtdna": {"contamination": 0.02}}, coverage, thresholds).status == "warn"
    assert _sample_qc({"mtdna": {"contamination": 0.05}}, coverage, thresholds).status == "fail"
