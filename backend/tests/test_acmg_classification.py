import json
import logging

import pytest
from fastapi import HTTPException

from backend.app.schemas import AcmgClassificationPayload, AcmgCriterionSelection
from backend.app.services import acmg_points
from backend.app.services.small_variant_review_acmg import (
    _acmg_json_or_none,
    _deserialize_acmg,
    _normalize_acmg_payload,
)


def crit(code: str, strength: str, accepted: bool = True) -> AcmgCriterionSelection:
    return AcmgCriterionSelection(code=code, strength=strength, accepted=accepted)


def test_point_bands_match_clingen_thresholds() -> None:
    cases = [
        ([("PVS1", "very_strong"), ("PM2", "moderate")], 10, "acmg_class_5"),
        ([("PVS1", "very_strong"), ("PM2", "supporting")], 9, "acmg_class_4"),
        ([("PM2", "supporting")], 1, "acmg_class_3"),
        ([("BP4", "supporting"), ("BP7", "supporting")], -2, "acmg_class_2"),
        ([("BS1", "strong"), ("BS2", "strong")], -8, "acmg_class_1"),
    ]
    for selections, expected_points, expected_class in cases:
        criteria = [{"code": c, "strength": s, "accepted": True} for c, s in selections]
        points, class_key, _ = acmg_points.compute_classification(criteria)
        assert points == expected_points
        assert class_key == expected_class


def test_vus_sub_tier_bands() -> None:
    # Cold 0–1, Warm 2–3, Hot 4–5; None outside the VUS band.
    assert acmg_points.vus_tier_for_points(0, "acmg_class_3") == "cold"
    assert acmg_points.vus_tier_for_points(1, "acmg_class_3") == "cold"
    assert acmg_points.vus_tier_for_points(2, "acmg_class_3") == "warm"
    assert acmg_points.vus_tier_for_points(3, "acmg_class_3") == "warm"
    assert acmg_points.vus_tier_for_points(4, "acmg_class_3") == "hot"
    assert acmg_points.vus_tier_for_points(5, "acmg_class_3") == "hot"
    assert acmg_points.vus_tier_for_points(9, "acmg_class_4") is None
    assert acmg_points.vus_tier_for_points(-2, "acmg_class_2") is None


def test_normalize_stores_vus_tier_only_for_vus() -> None:
    # PM2 supporting (1 pt) → VUS cold.
    vus_blob, _, vus_class = _normalize_acmg_payload(
        AcmgClassificationPayload(criteria=[crit("PM2", "supporting")])
    )
    assert vus_class == "acmg_class_3"
    assert vus_blob["vus_tier"] == "cold"

    # PS1 strong (4 pts) → VUS hot.
    hot_blob, _, _ = _normalize_acmg_payload(
        AcmgClassificationPayload(criteria=[crit("PS1", "strong")])
    )
    assert hot_blob["vus_tier"] == "hot"

    # Likely Pathogenic → no tier.
    lp_blob, _, lp_class = _normalize_acmg_payload(
        AcmgClassificationPayload(criteria=[crit("PVS1", "very_strong"), crit("PM2", "supporting")])
    )
    assert lp_class == "acmg_class_4"
    assert lp_blob["vus_tier"] is None


def test_ba1_forces_benign_regardless_of_points() -> None:
    points, class_key, label = acmg_points.compute_classification(
        [
            {"code": "BA1", "strength": "stand_alone", "accepted": True},
            {"code": "PVS1", "strength": "very_strong", "accepted": True},
        ]
    )
    assert class_key == "acmg_class_1"
    assert label == "Benign - class 1"


def test_unaccepted_criteria_do_not_score() -> None:
    points, class_key, _ = acmg_points.compute_classification(
        [{"code": "PVS1", "strength": "very_strong", "accepted": False}]
    )
    assert points == 0
    assert class_key == "acmg_class_3"


def test_normalize_recomputes_and_ignores_client_total() -> None:
    payload = AcmgClassificationPayload(
        criteria=[crit("PVS1", "very_strong"), crit("PM2", "supporting")],
        point_total=999,  # bogus client value must be ignored
        classification="Benign - class 1",
    )
    blob, total, class_key = _normalize_acmg_payload(payload)
    assert total == 9
    assert class_key == "acmg_class_4"
    assert blob["point_total"] == 9
    assert blob["classification"] == "Likely Pathogenic - class 4"
    assert {c["code"] for c in blob["criteria"]} == {"PVS1", "PM2"}


def test_normalize_empty_criteria_clears() -> None:
    assert _normalize_acmg_payload(AcmgClassificationPayload(criteria=[])) == (None, None, None)
    assert _normalize_acmg_payload(None) == (None, None, None)


def test_normalize_rejects_unknown_code() -> None:
    with pytest.raises(HTTPException) as exc:
        _normalize_acmg_payload(AcmgClassificationPayload(criteria=[crit("PX9", "supporting")]))
    assert exc.value.status_code == 400


def test_normalize_rejects_invalid_strength() -> None:
    with pytest.raises(HTTPException) as exc:
        _normalize_acmg_payload(AcmgClassificationPayload(criteria=[crit("PM2", "ultra")]))
    assert exc.value.status_code == 400


# --- each criterion once, at a strength it takes (CLIN-3) ------------------------------------
# The dialog holds one selection per criterion and offers each criterion only its
# `allowedStrengths` (frontend/src/lib/acmg/criteria.ts). Another payload is refused, so a
# client calling the API cannot count PVS1 twice or apply BS1 at very strong.


def test_normalize_refuses_a_repeated_criterion() -> None:
    # Counted twice, PVS1 alone would score +16: Pathogenic.
    with pytest.raises(HTTPException) as refused:
        _normalize_acmg_payload(
            AcmgClassificationPayload(criteria=[crit("PVS1", "very_strong"), crit("PVS1", "very_strong")])
        )
    assert refused.value.status_code == 400
    assert refused.value.detail == "Repeated ACMG criterion: PVS1"


@pytest.mark.parametrize(
    "criteria",
    [
        # The same code once its case and spacing are normalised.
        [crit("PM2", "supporting"), crit(" pm2 ", "supporting")],
        # Once accepted and once not: the record would hold PVS1 both applied and rejected.
        [crit("PVS1", "very_strong"), crit("PS3", "strong"), crit("PVS1", "strong", accepted=False)],
    ],
)
def test_normalize_refuses_a_criterion_listed_twice_in_any_form(criteria) -> None:
    with pytest.raises(HTTPException) as refused:
        _normalize_acmg_payload(AcmgClassificationPayload(criteria=criteria))
    assert refused.value.status_code == 400
    assert refused.value.detail.startswith("Repeated ACMG criterion: ")


@pytest.mark.parametrize(
    ("code", "strength"),
    [
        ("BS1", "very_strong"),  # −8 on its own: Benign
        ("PM2", "very_strong"),  # +8 on its own: Likely pathogenic
        ("PVS1", "stand_alone"),  # applied, yet 0 points
        ("BA1", "strong"),  # BA1 is stand-alone only
        ("BS1", "stand_alone"),  # and stand-alone is BA1's alone
        ("BP7", "strong"),
    ],
)
def test_normalize_refuses_a_strength_the_criterion_does_not_take(code: str, strength: str) -> None:
    with pytest.raises(HTTPException) as refused:
        _normalize_acmg_payload(AcmgClassificationPayload(criteria=[crit(code, strength)]))
    assert refused.value.status_code == 400
    assert refused.value.detail == f"Invalid ACMG strength for {code}: {strength}"


def test_an_unaccepted_criterion_must_take_its_strength_too() -> None:
    # It scores nothing, but it is stored with the classification and shown again.
    with pytest.raises(HTTPException) as refused:
        _normalize_acmg_payload(
            AcmgClassificationPayload(criteria=[crit("PS3", "strong"), crit("BS1", "very_strong", accepted=False)])
        )
    assert refused.value.status_code == 400


def test_a_stored_classification_the_save_rules_refuse_is_still_served() -> None:
    # Only a save is checked: a record written before CLIN-3, with a repeated criterion and a
    # strength the criterion does not take, is read back as it was written.
    from backend.app.services.small_variant_review_pg import _serialize_review

    written_before = {
        "criteria": [
            {"code": "PVS1", "strength": "very_strong", "accepted": True},
            {"code": "PVS1", "strength": "very_strong", "accepted": True},
            {"code": "BS1", "strength": "very_strong", "accepted": False},
        ],
        "point_total": 16,
        "classification": "Pathogenic - class 5",
    }
    review = _serialize_review(
        {"variant_id": "1-2000-C-T", "classification": "acmg_class_5", "acmg": json.dumps(written_before)}
    )
    assert review.acmg is not None and review.acmg_unreadable is False
    assert [(c.code, c.strength) for c in review.acmg.criteria] == [
        ("PVS1", "very_strong"),
        ("PVS1", "very_strong"),
        ("BS1", "very_strong"),
    ]
    assert review.acmg.point_total == 16


def test_blob_round_trips_through_storage_helpers() -> None:
    payload = AcmgClassificationPayload(criteria=[crit("PVS1", "very_strong")])
    blob, _, _ = _normalize_acmg_payload(payload)

    stored = _acmg_json_or_none(blob)
    assert isinstance(stored, str)

    restored = _deserialize_acmg(json.loads(stored))
    assert restored is not None
    assert restored.point_total == 8
    assert restored.criteria[0].code == "PVS1"
    assert restored.criteria[0].accepted is True


def test_acmg_json_or_none_returns_none_for_empty() -> None:
    assert _acmg_json_or_none(None) is None
    assert _deserialize_acmg(None) is None


# A stored blob that no longer validates: `code` must be a string. The value is free
# text, the kind of thing that must never be copied into a log.
_UNREADABLE_ACMG = {"criteria": [{"code": {"note": "private curation text"}, "strength": "strong"}]}


def test_an_unreadable_stored_classification_is_logged_without_its_values(caplog) -> None:
    # #514: this used to become None silently — indistinguishable from "never classified".
    with caplog.at_level(logging.ERROR):
        assert _deserialize_acmg(json.dumps(_UNREADABLE_ACMG)) is None
        assert _deserialize_acmg("{not json") is None
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(messages) == 2
    assert "criteria.0.code: string_type" in messages[0]
    assert "JSONDecodeError" in messages[1]
    assert "private curation text" not in caplog.text


def test_a_stored_json_null_is_no_classification_and_not_an_error(caplog) -> None:
    with caplog.at_level(logging.ERROR):
        assert _deserialize_acmg("null") is None
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_the_review_marks_an_unreadable_classification_instead_of_dropping_it(caplog) -> None:
    from backend.app.services.small_variant_review_pg import _serialize_review

    stored = {"variant_id": "1-2000-C-T", "classification": "acmg_class_4"}
    with caplog.at_level(logging.ERROR):
        broken = _serialize_review({**stored, "acmg": json.dumps(_UNREADABLE_ACMG)})
    assert broken.acmg is None
    assert broken.acmg_unreadable is True
    # The editor can warn before an overwrite; the class itself is still served.
    assert broken.classification == "acmg_class_4"

    never = _serialize_review({**stored, "acmg": None})
    assert never.acmg is None and never.acmg_unreadable is False

    payload = AcmgClassificationPayload(criteria=[crit("PVS1", "very_strong")])
    blob, _, _ = _normalize_acmg_payload(payload)
    readable = _serialize_review({**stored, "acmg": _acmg_json_or_none(blob)})
    assert readable.acmg is not None and readable.acmg_unreadable is False


def test_the_structural_review_marks_an_unreadable_cnv_classification(caplog) -> None:
    from backend.app.services.structural_variant_review_pg import (
        _deserialize_cnv_acmg,
        _serialize_review as _serialize_structural_review,
    )

    unreadable = {"kind": "sideways", "criteria": [{"code": "1A", "evidence": "private curation text"}]}
    with caplog.at_level(logging.ERROR):
        assert _deserialize_cnv_acmg(unreadable) is None
        review = _serialize_structural_review({"variant_id": "DEL-1-1000-2000", "cnv_acmg": unreadable})
    assert review.cnv_acmg is None
    assert review.acmg_unreadable is True
    assert "kind: literal_error" in caplog.text
    assert "private curation text" not in caplog.text

    clean = _serialize_structural_review({"variant_id": "DEL-1-1000-2000", "cnv_acmg": {"kind": "loss"}})
    assert clean.cnv_acmg is not None and clean.acmg_unreadable is False


def test_no_accepted_criteria_classifies_as_vus_not_pathogenic_or_benign() -> None:
    # Degraded / incomplete input must abstain (VUS class 3), never auto-pathogenic
    # or benign (REQ-PERF-003, risk H3). Unaccepted criteria — even a very-strong
    # pathogenic PVS1 — must not score.
    empty_points, empty_class, _ = acmg_points.compute_classification([])
    assert empty_points == 0
    assert empty_class == "acmg_class_3"

    unconfirmed = [
        {"code": "PVS1", "strength": "very_strong", "accepted": False},
        {"code": "PM2", "strength": "moderate", "accepted": False},
    ]
    points, class_key, _ = acmg_points.compute_classification(unconfirmed)
    assert points == 0
    assert class_key == "acmg_class_3"
