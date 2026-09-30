from __future__ import annotations

import asyncio
import json
import math
import types

from backend.app.services import clinical_audit_service as cas


def test_diff_classification_change_labels_from_to() -> None:
    existing = {
        "acmg_class": "acmg_class_3",
        "acmg": {"criteria": [{"code": "PM2", "accepted": True}]},
        "tags": [],
        "note": None,
    }
    new = {
        "acmg_class": "acmg_class_4",
        "acmg": {"criteria": [{"code": "PM2", "accepted": True}, {"code": "PP3", "accepted": True}]},
        "tags": [],
        "note": None,
    }
    events = cas.diff_review_changes(existing, new)
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "classification"
    assert event["summary"] == "Classification VUS (class 3) → Likely pathogenic (class 4)"
    assert event["before"]["criteria"] == ["PM2"]
    assert event["after"]["criteria"] == ["PM2", "PP3"]


def test_diff_criteria_only_change() -> None:
    existing = {"acmg_class": "acmg_class_4", "acmg": {"criteria": [{"code": "PM2", "accepted": True}]}}
    new = {
        "acmg_class": "acmg_class_4",
        "acmg": {"criteria": [{"code": "PM2", "accepted": True}, {"code": "PP3", "accepted": True}]},
    }
    events = cas.diff_review_changes(existing, new)
    assert len(events) == 1 and events[0]["action"] == "classification"
    assert "criteria updated" in events[0]["summary"]


def test_diff_tags_added_and_removed() -> None:
    events = cas.diff_review_changes({"tags": ["report"]}, {"tags": ["acmg_class_4"]})
    event = next(e for e in events if e["action"] == "tags")
    assert "added acmg_class_4" in event["summary"] and "removed report" in event["summary"]


def test_diff_note_lifecycle() -> None:
    assert cas.diff_review_changes({"note": None}, {"note": "hi"})[0]["summary"] == "Note added"
    assert cas.diff_review_changes({"note": "hi"}, {"note": "bye"})[0]["summary"] == "Note updated"
    assert cas.diff_review_changes({"note": "hi"}, {"note": None})[0]["summary"] == "Note removed"


def test_diff_no_change_is_empty() -> None:
    state = {
        "acmg_class": "acmg_class_3",
        "acmg": {"criteria": [{"code": "PM2", "accepted": True}]},
        "tags": ["report"],
        "note": "x",
    }
    assert cas.diff_review_changes(dict(state), dict(state)) == []


def test_diff_new_review_records_every_aspect() -> None:
    events = cas.diff_review_changes(
        None,
        {
            "acmg_class": "acmg_class_4",
            "acmg": {"criteria": [{"code": "PM2", "accepted": True}]},
            "tags": ["report"],
            "note": "x",
        },
    )
    assert {e["action"] for e in events} == {"classification", "tags", "note"}


def test_record_review_changes_writes_one_insert_per_change() -> None:
    calls: list[dict] = []

    class _Result:
        def mappings(self):
            return self

        def first(self):
            return None  # no chain head -> this is the first chained row for the family

    class _Session:
        async def execute(self, _stmt, params=None):
            calls.append(params)
            return _Result()

    user = types.SimpleNamespace(
        username="alice", email="a@x.org", id="00000000-0000-0000-0000-000000000001"
    )
    existing = {
        "acmg_class": "acmg_class_3",
        "acmg": {"criteria": [{"code": "PM2", "accepted": True}]},
        "tags": [],
        "note": None,
    }
    new = {
        "acmg_class": "acmg_class_4",
        "acmg": {"criteria": [{"code": "PM2", "accepted": True}]},
        "tags": ["report"],
        "note": "x",
    }
    asyncio.run(
        cas.record_review_changes(
            _Session(),
            family_uuid="u1",
            family_identifier="FAM1",
            variant_id="1-1-A-G",
            user=user,
            existing=existing,
            new_state=new,
        )
    )
    # Each record_clinical_event now also runs an advisory-lock + chain-head read, so
    # filter to the INSERT calls (the ones carrying the event content).
    inserts = [c for c in calls if c and "action" in c]
    assert len(inserts) == 3  # classification, tags, note
    assert {c["action"] for c in inserts} == {"classification", "tags", "note"}
    assert all(c["actor"] == "alice" and c["variant_id"] == "1-1-A-G" for c in inserts)
    assert all(c["family_identifier"] == "FAM1" for c in inserts)
    # The chain stamped a row_hash on every event.
    assert all(c.get("row_hash") for c in inserts)
    # Small-variant events carry no modality marker (only SV events do).
    assert all(json.loads(c["metadata"]) == {} for c in inserts)


# --- structural-variant (SV / CNV) reviews ----------------------------------------------------


def _cnv(kind: str, *criteria: tuple[str, float, bool]) -> dict:
    return {
        "kind": kind,
        "criteria": [
            {"code": code, "points": points, "accepted": accepted, "evidence": "free text"}
            for code, points, accepted in criteria
        ],
    }


def _sv_review(**fields) -> dict:
    review = {
        "classification": None,
        "cnv_acmg": None,
        "cnv_point_total": None,
        "cnv_class": None,
        "tags": [],
        "note": None,
    }
    review.update(fields)
    return review


def test_structural_state_records_the_scoring_and_every_stored_criterion() -> None:
    state = cas.structural_review_state(
        _sv_review(
            classification="  Likely pathogenic ",
            cnv_acmg=_cnv("loss", ("3B", 0.45, True), ("2A", 1.0, False), ("2H", 0.15, True)),
            cnv_point_total=0.6,
            cnv_class="cnv_class_3",
            note="  ",
        )
    )
    assert state == {
        "classification": "Likely pathogenic",
        "cnv_class": "cnv_class_3",
        "cnv_kind": "loss",
        "cnv_point_total": 0.6,
        # The criteria the class rests on, with their points, sorted.
        "cnv_criteria": [{"code": "2H", "points": 0.15}, {"code": "3B", "points": 0.45}],
        # Every stored criterion as stored, unaccepted ones and evidence included.
        "cnv_criteria_detail": [
            {"code": "2A", "points": 1.0, "accepted": False, "evidence": "free text", "auto_suggested": False},
            {"code": "2H", "points": 0.15, "accepted": True, "evidence": "free text", "auto_suggested": False},
            {"code": "3B", "points": 0.45, "accepted": True, "evidence": "free text", "auto_suggested": False},
        ],
        "tags": [],
        "note": None,
    }


def test_structural_state_reads_a_stored_json_record_and_survives_a_broken_one() -> None:
    stored = json.dumps(_cnv("gain", ("2C", -1.0, True)))
    assert cas.structural_review_state(_sv_review(cnv_acmg=stored))["cnv_criteria"] == [
        {"code": "2C", "points": -1.0}
    ]
    broken = cas.structural_review_state(_sv_review(cnv_acmg="{not json", cnv_class="cnv_class_1"))
    assert (broken["cnv_kind"], broken["cnv_criteria"], broken["cnv_class"]) == (None, [], "cnv_class_1")
    assert cas.structural_review_state(None)["cnv_criteria"] == []


def test_audited_points_are_finite_and_never_negative_zero() -> None:
    # Postgres numerics have no -0: a chained -0.0 would read back as 0 and break the chain.
    assert math.copysign(1.0, cas._audit_points(-0.0)) == 1.0
    assert math.copysign(1.0, cas._audit_points(round(0.30 + 0.15 - 0.45, 2))) == 1.0
    assert cas._audit_points(-0.45) == -0.45 and cas._audit_points("0.9") == 0.9
    assert [cas._audit_points(v) for v in (None, True, "x", float("nan"), float("inf"))] == [None] * 5


def test_structural_diff_classification_label_and_cnv_class() -> None:
    before = _sv_review(
        classification="VUS - class 3",
        cnv_acmg=_cnv("loss", ("3B", 0.45, True)),
        cnv_point_total=0.45,
        cnv_class="cnv_class_3",
    )
    after = _sv_review(
        classification="Pathogenic - class 5",
        cnv_acmg=_cnv("loss", ("2A", 1.0, True), ("3B", 0.45, True)),
        cnv_point_total=1.45,
        cnv_class="cnv_class_5",
    )
    [event] = cas.diff_structural_review_changes(before, after)
    assert event["action"] == "classification"
    assert event["summary"] == (
        "Classification VUS - class 3 → Pathogenic - class 5; "
        "CNV classification VUS - class 3 → Pathogenic - class 5"
    )
    assert event["before"]["cnv_criteria"] == [{"code": "3B", "points": 0.45}]
    assert event["after"]["cnv_criteria"] == [
        {"code": "2A", "points": 1.0},
        {"code": "3B", "points": 0.45},
    ]
    assert set(event["before"]) == {
        "classification",
        "cnv_class",
        "cnv_kind",
        "cnv_point_total",
        "cnv_criteria",
        "cnv_criteria_detail",
    }


def test_structural_diff_label_only_and_kind_only_changes() -> None:
    [label] = cas.diff_structural_review_changes(
        _sv_review(classification="Likely benign"), _sv_review(classification="Benign")
    )
    assert label["summary"] == "Classification Likely benign → Benign"

    scored = dict(cnv_point_total=0.0, cnv_class="cnv_class_3")
    [kind] = cas.diff_structural_review_changes(
        _sv_review(cnv_acmg=_cnv("loss", ("3A", 0.0, True)), **scored),
        _sv_review(cnv_acmg=_cnv("gain", ("3A", 0.0, True)), **scored),
    )
    assert kind["summary"] == "CNV criteria updated (VUS - class 3): kind loss → gain"
    assert (kind["before"]["cnv_kind"], kind["after"]["cnv_kind"]) == ("loss", "gain")


def test_structural_diff_tags_and_note_mirror_small_variants() -> None:
    events = cas.diff_structural_review_changes(
        _sv_review(tags=["report"], note="old"), _sv_review(tags=["candidate"], note="new")
    )
    assert [(e["action"], e["summary"]) for e in events] == [
        ("tags", "Tags added candidate; removed report"),
        ("note", "Note updated"),
    ]


def test_structural_diff_no_change_is_empty() -> None:
    review = _sv_review(
        classification="Pathogenic - class 5",
        cnv_acmg=_cnv("loss", ("2A", 1.0, True)),
        cnv_point_total=1.0,
        cnv_class="cnv_class_5",
        tags=["report", "candidate"],
        note="x",
    )
    reordered = {**review, "tags": ["candidate", "report"], "note": " x "}
    assert cas.diff_structural_review_changes(review, reordered) == []


def test_structural_diff_deleted_review_records_every_aspect() -> None:
    review = _sv_review(
        classification="Pathogenic - class 5",
        cnv_acmg=_cnv("loss", ("2A", 1.0, True)),
        cnv_point_total=1.0,
        cnv_class="cnv_class_5",
        tags=["report"],
        note="x",
    )
    events = cas.diff_structural_review_changes(review, None)
    assert [e["action"] for e in events] == ["classification", "tags", "note"]
    assert events[0]["summary"] == (
        "Classification Pathogenic - class 5 → unclassified; "
        "CNV classification Pathogenic - class 5 → unclassified"
    )
    assert events[0]["after"] == {
        "classification": None,
        "cnv_class": None,
        "cnv_kind": None,
        "cnv_point_total": None,
        "cnv_criteria": [],
        "cnv_criteria_detail": [],
    }


def test_record_structural_review_changes_marks_the_events_as_sv() -> None:
    calls: list[dict] = []

    class _Result:
        def mappings(self):
            return self

        def first(self):
            return None

    class _Session:
        async def execute(self, _stmt, params=None):
            calls.append(params)
            return _Result()

    user = types.SimpleNamespace(username="bob", email="b@x.org", id=None)
    asyncio.run(
        cas.record_structural_review_changes(
            _Session(),
            family_uuid="u1",
            family_identifier="FAM1",
            variant_id="1-1000-2000-DEL---",
            user=user,
            existing=None,
            new_state=_sv_review(classification="Pathogenic", note="n"),
        )
    )
    inserts = [c for c in calls if c and "action" in c]
    assert [c["action"] for c in inserts] == ["classification", "note"]
    assert all(json.loads(c["metadata"]) == {"modality": "sv"} for c in inserts)
    assert all(c["actor"] == "bob" and c["variant_id"] == "1-1000-2000-DEL---" for c in inserts)
    assert all(c["row_hash"] for c in inserts)


# --- every change to the stored ACMG record is recorded (small variants) ----------------------
# Criteria are (code, strength, accepted, evidence, auto_suggested), as the review stores them.


def _acmg(acmg_class: str | None, *criteria: tuple, point_total=None, vus_tier=None) -> dict:
    return {
        "acmg_class": acmg_class,
        "acmg": {
            "criteria": [
                {"code": c, "strength": s, "accepted": a, "evidence": e, "auto_suggested": g}
                for c, s, a, e, g in criteria
            ],
            "point_total": point_total,
            "vus_tier": vus_tier,
        },
        "tags": [],
        "note": None,
    }


PS3 = ("PS3", "strong", True, "functional study", False)
PM2 = ("PM2", "moderate", True, None, False)
PP3 = ("PP3", "supporting", True, None, False)
LP = "acmg_class_4"


def _classification_event(existing: dict, new: dict) -> dict:
    events = cas.diff_review_changes(existing, new)
    assert [event["action"] for event in events] == ["classification"], events
    return events[0]


def test_diff_a_strength_change_alone_is_recorded() -> None:
    event = _classification_event(
        _acmg(LP, PS3, PM2, PP3, point_total=7),
        _acmg(LP, PS3, ("PM2", "supporting", True, None, False), PP3, point_total=6),
    )
    assert event["summary"] == (
        "ACMG criteria updated (Likely pathogenic (class 4)): PM2 moderate → supporting; total 7 → 6"
    )
    assert event["before"]["criteria"] == event["after"]["criteria"] == ["PM2", "PP3", "PS3"]
    strengths = {c["code"]: c["strength"] for c in event["after"]["criteria_detail"]}
    assert strengths == {"PM2": "supporting", "PP3": "supporting", "PS3": "strong"}
    assert (event["before"]["point_total"], event["after"]["point_total"]) == (7, 6)


def test_diff_an_evidence_edit_alone_is_recorded() -> None:
    event = _classification_event(
        _acmg(LP, PS3, PM2, point_total=6),
        _acmg(LP, ("PS3", "strong", True, "functional study, replicated", False), PM2, point_total=6),
    )
    assert event["summary"] == "ACMG criteria updated (Likely pathogenic (class 4)): PS3 evidence edited"
    [ps3] = [c for c in event["after"]["criteria_detail"] if c["code"] == "PS3"]
    assert ps3["evidence"] == "functional study, replicated"


def test_diff_a_suggestion_that_appears_or_goes_is_recorded() -> None:
    suggested = ("PM1", "moderate", False, None, True)
    appeared = _classification_event(_acmg(LP, PS3, PM2, point_total=6), _acmg(LP, PS3, PM2, suggested, point_total=6))
    assert appeared["summary"] == "ACMG criteria updated (Likely pathogenic (class 4)): PM1 suggested"
    gone = _classification_event(_acmg(LP, PS3, PM2, suggested, point_total=6), _acmg(LP, PS3, PM2, point_total=6))
    assert gone["summary"] == "ACMG criteria updated (Likely pathogenic (class 4)): PM1 removed"


def test_diff_accepting_or_rejecting_a_suggestion_names_it() -> None:
    offered = ("PP3", "supporting", False, None, True)
    taken = ("PP3", "supporting", True, None, True)
    accepted = _classification_event(_acmg(LP, PS3, PM2, offered, point_total=6), _acmg(LP, PS3, PM2, taken, point_total=7))
    assert accepted["summary"] == "ACMG criteria updated (Likely pathogenic (class 4)): PP3 accepted; total 6 → 7"
    rejected = _classification_event(_acmg(LP, PS3, PM2, taken, point_total=7), _acmg(LP, PS3, PM2, offered, point_total=6))
    assert rejected["summary"] == "ACMG criteria updated (Likely pathogenic (class 4)): PP3 rejected; total 7 → 6"


def test_diff_the_vus_tier_is_recorded() -> None:
    event = _classification_event(
        _acmg("acmg_class_3", PS3, point_total=4, vus_tier="hot"),
        _acmg("acmg_class_3", ("PS3", "moderate", True, "functional study", False), point_total=2, vus_tier="warm"),
    )
    assert event["summary"] == (
        "ACMG criteria updated (VUS (class 3)): PS3 strong → moderate; total 4 → 2; VUS tier hot → warm"
    )
    assert (event["before"]["vus_tier"], event["after"]["vus_tier"]) == ("hot", "warm")


def test_diff_a_class_change_keeps_its_headline_and_records_the_detail() -> None:
    event = _classification_event(_acmg("acmg_class_3", PS3, point_total=4), _acmg(LP, PS3, PM2, point_total=6))
    assert event["summary"] == "Classification VUS (class 3) → Likely pathogenic (class 4)"
    assert [c["code"] for c in event["after"]["criteria_detail"]] == ["PM2", "PS3"]


def test_diff_an_unchanged_resave_of_the_full_record_writes_nothing() -> None:
    record = _acmg(LP, PS3, PM2, ("PM1", "moderate", False, "considered", True), point_total=6)
    reordered = _acmg(LP, ("PM1", "moderate", False, "considered", True), PM2, PS3, point_total=6)
    assert cas.diff_review_changes(record, reordered) == []


def test_diff_many_changes_are_shortened_in_the_summary_but_all_recorded() -> None:
    codes = ["PM1", "PM2", "PM4", "PM5", "PM6", "PP1", "PP2"]
    before = _acmg(LP, *[(c, "moderate", True, None, False) for c in codes], point_total=6)
    after = _acmg(LP, *[(c, "moderate", True, "noted", False) for c in codes], point_total=6)
    event = _classification_event(before, after)
    assert event["summary"].endswith("PM6 evidence added; and 2 more")
    assert all(c["evidence"] == "noted" for c in event["after"]["criteria_detail"])


# --- every change to the stored ClinGen scoring is recorded (CNVs) ------------------------------
# Criteria are (code, points, accepted, evidence, auto_suggested).


def _scoring(kind: str, *criteria: tuple, total: float, cnv_class: str) -> dict:
    return _sv_review(
        cnv_acmg={
            "kind": kind,
            "criteria": [
                {"code": c, "points": p, "accepted": a, "evidence": e, "auto_suggested": g}
                for c, p, a, e, g in criteria
            ],
        },
        cnv_point_total=total,
        cnv_class=cnv_class,
    )


def _cnv_classification_event(existing: dict, new: dict) -> dict:
    events = cas.diff_structural_review_changes(existing, new)
    assert [event["action"] for event in events] == ["classification"], events
    return events[0]


def test_structural_diff_an_evidence_edit_alone_is_recorded() -> None:
    event = _cnv_classification_event(
        _scoring("loss", ("2A", 1.0, True, "HI gene", False), total=1.0, cnv_class="cnv_class_5"),
        _scoring("loss", ("2A", 1.0, True, "HI gene, whole", False), total=1.0, cnv_class="cnv_class_5"),
    )
    assert event["summary"] == "CNV criteria updated (Pathogenic - class 5): 2A evidence edited"
    assert event["after"]["cnv_criteria_detail"][0]["evidence"] == "HI gene, whole"


def test_structural_diff_a_change_to_an_unaccepted_criterion_is_recorded() -> None:
    accepted = ("2A", 1.0, True, None, False)
    event = _cnv_classification_event(
        _scoring("loss", accepted, ("4D", 0.0, False, None, True), total=1.0, cnv_class="cnv_class_5"),
        _scoring("loss", accepted, ("4D", -0.45, False, None, False), total=1.0, cnv_class="cnv_class_5"),
    )
    assert event["summary"] == (
        "CNV criteria updated (Pathogenic - class 5): 4D 0 → -0.45 points, no longer suggested"
    )


def test_structural_diff_points_moved_between_criteria_are_recorded() -> None:
    # The same total and class, reached with different criteria points.
    event = _cnv_classification_event(
        _scoring("loss", ("2B", 0.15, True, None, False), ("2H", 0.15, True, None, False), total=0.3, cnv_class="cnv_class_3"),
        _scoring("loss", ("2B", 0.3, True, None, False), ("2H", 0.0, True, None, False), total=0.3, cnv_class="cnv_class_3"),
    )
    assert event["summary"] == (
        "CNV criteria updated (VUS - class 3): 2B 0.15 → 0.3 points; 2H 0.15 → 0 points"
    )


def test_structural_diff_rejecting_a_suggested_criterion_names_it() -> None:
    event = _cnv_classification_event(
        _scoring("loss", ("2A", 1.0, True, None, True), total=1.0, cnv_class="cnv_class_5"),
        _scoring("loss", ("2A", 1.0, False, None, True), total=0.0, cnv_class="cnv_class_3"),
    )
    # A class change keeps its headline; the rejection is in the recorded detail.
    assert event["summary"] == "CNV classification Pathogenic - class 5 → VUS - class 3"
    assert event["after"]["cnv_criteria_detail"][0]["accepted"] is False


def test_structural_diff_an_unchanged_resave_of_the_full_scoring_writes_nothing() -> None:
    scoring = _scoring("loss", ("2A", 1.0, True, "x", True), ("4D", 0.0, False, None, False), total=1.0, cnv_class="cnv_class_5")
    reordered = _scoring("loss", ("4D", 0.0, False, None, False), ("2A", 1.0, True, "x", True), total=1.0, cnv_class="cnv_class_5")
    assert cas.diff_structural_review_changes(scoring, reordered) == []
