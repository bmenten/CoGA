"""Tests for the ClinGen 2019 CNV classification point system."""

import pytest

from backend.app.services import cnv_acmg_points as p


def test_class_thresholds() -> None:
    assert p.class_key_for_points(0.99) == "cnv_class_5"
    assert p.class_key_for_points(0.90) == "cnv_class_4"
    assert p.class_key_for_points(0.89) == "cnv_class_3"
    assert p.class_key_for_points(0.0) == "cnv_class_3"
    assert p.class_key_for_points(-0.89) == "cnv_class_3"
    assert p.class_key_for_points(-0.90) == "cnv_class_2"
    assert p.class_key_for_points(-0.98) == "cnv_class_2"
    assert p.class_key_for_points(-0.99) == "cnv_class_1"


def test_loss_complete_hi_overlap_is_pathogenic() -> None:
    total, key, label = p.compute_classification(
        "loss", [{"code": "2A", "points": 1.0, "accepted": True}]
    )
    assert total == 1.0
    assert key == "cnv_class_5"
    assert "Pathogenic" in label


def test_loss_benign_region_is_benign() -> None:
    total, key, _ = p.compute_classification(
        "loss", [{"code": "2F", "points": -1.0, "accepted": True}]
    )
    assert total == -1.0
    assert key == "cnv_class_1"


def test_unaccepted_criteria_do_not_score() -> None:
    total, key, _ = p.compute_classification(
        "loss",
        [
            {"code": "2A", "points": 1.0, "accepted": False},
            {"code": "3B", "points": 0.45, "accepted": True},
        ],
    )
    assert total == 0.45
    assert key == "cnv_class_3"


def test_points_are_clamped_to_allowed_range() -> None:
    # 2H allows at most 0.15; a client over-value must not inflate the score.
    assert p.clamp_points("loss", "2H", 5.0) == 0.15
    total, _key, _ = p.compute_classification(
        "loss", [{"code": "2H", "points": 5.0, "accepted": True}]
    )
    assert total == 0.15


def test_gain_and_loss_have_distinct_catalogs() -> None:
    # 2I (gene disruption) only exists in the gain catalog.
    assert p.is_valid_code("gain", "2I")
    assert not p.is_valid_code("loss", "2I")
    # 2C means different things but is valid in both.
    assert p.is_valid_code("gain", "2C")


def test_gain_complete_ts_overlap_is_pathogenic() -> None:
    total, key, _ = p.compute_classification(
        "gain", [{"code": "2A", "points": 1.0, "accepted": True}]
    )
    assert total == 1.0
    assert key == "cnv_class_5"


def test_summed_evidence_reaches_pathogenic() -> None:
    # 0.90 + 0.15 = 1.05: past the 0.99 of Pathogenic. (Named "reaches likely pathogenic"
    # before, while it asserted class 5; the likely classes are pinned below.)
    total, key, _ = p.compute_classification(
        "loss",
        [
            {"code": "2C-1", "points": 0.90, "accepted": True},
            {"code": "5A", "points": 0.15, "accepted": True},
        ],
    )
    assert total == 1.05
    assert key == "cnv_class_5"


@pytest.mark.parametrize(
    ("kind", "criteria", "total", "key"),
    [
        pytest.param("loss", [("2C-1", 0.90)], 0.90, "cnv_class_4", id="likely-pathogenic"),
        pytest.param("loss", [("3B", 0.45), ("5A", 0.45)], 0.90, "cnv_class_4", id="likely-pathogenic-summed"),
        pytest.param("gain", [("4D", -0.90)], -0.90, "cnv_class_2", id="likely-benign"),
        pytest.param("loss", [("5D", -0.45), ("4G", -0.45)], -0.90, "cnv_class_2", id="likely-benign-summed"),
    ],
)
def test_summed_evidence_reaches_the_likely_classes(
    kind: str, criteria: list[tuple[str, float]], total: float, key: str
) -> None:
    scored, class_key, label = p.compute_classification(
        kind, [{"code": code, "points": points, "accepted": True} for code, points in criteria]
    )
    assert (scored, class_key, label) == (total, key, p.CLASS_LABELS[key])


def test_no_accepted_criteria_is_vus_not_a_confident_call() -> None:
    # Degraded / incomplete input must abstain (VUS), never auto-pathogenic/benign
    # (REQ-PERF-003, risk H3).
    total, key, _ = p.compute_classification("loss", [])
    assert total == 0.0
    assert key == "cnv_class_3"


def test_criteria_missing_the_accepted_flag_are_ignored() -> None:
    # An incomplete criterion (no `accepted`) must not score → stays VUS.
    total, key, _ = p.compute_classification("loss", [{"code": "2A", "points": 1.0}])
    assert total == 0.0
    assert key == "cnv_class_3"


@pytest.mark.parametrize(
    ("criterion", "total", "key"),
    [
        # 2A allows exactly 1.0: the unreadable value, read as 0, is clamped up to it.
        pytest.param({"code": "2A", "points": "not-a-number"}, 1.0, "cnv_class_5", id="fixed-range"),
        # 2B allows 0 to 0.90: the unreadable value scores nothing.
        pytest.param({"code": "2B", "points": "not-a-number"}, 0.0, "cnv_class_3", id="range-from-zero"),
        pytest.param({"code": "2B", "points": None}, 0.0, "cnv_class_3", id="none"),
    ],
)
def test_malformed_points_value_does_not_crash_the_classifier(criterion: dict, total: float, key: str) -> None:
    # A non-numeric points value must be handled gracefully (coerced, then clamped
    # to the criterion's allowed range) — a valid classification, never a crash.
    scored, class_key, label = p.compute_classification("loss", [{**criterion, "accepted": True}])
    assert (scored, class_key, label) == (total, key, p.CLASS_LABELS[key])


def test_a_code_outside_the_catalogue_scores_nothing() -> None:
    # The save refuses an unknown code (structural_variant_review_pg); the scorer itself
    # never lets one add points, whatever it claims.
    assert p.clamp_points("loss", "9Z", 5.0) == 0.0
    assert p.clamp_points("gain", "2D-2", 0.9) == 0.0  # a loss-only code
    total, key, _ = p.compute_classification("loss", [{"code": "9Z", "points": 5.0, "accepted": True}])
    assert (total, key) == (0.0, "cnv_class_3")
