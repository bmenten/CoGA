from __future__ import annotations

import asyncio
import json
import math
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

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


# --- the trail, a page at a time (AUDIT-1) ------------------------------------------------------
# The report listed the 200 newest events as the whole trail: no paging, no total. A review
# save writes up to three events, so about 70 saves filled it.

_FAMILY_UUID = "00000000-0000-0000-0000-0000000000f1"


def _event_row(n: int, *, total: int) -> dict:
    """A row of the page statement: the trail's count, and event n."""
    return {
        "total": total,
        "id": f"00000000-0000-0000-0000-{n:012d}",
        "created_at": datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=n),
        "variant_id": f"1-{n}-A-G",
        "actor": "alice",
        "action": "classification",
        "summary": f"event {n}",
        "before": None,
        "after": {"n": n},
    }


class _Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self) -> list[dict]:
        return list(self._rows)

    def first(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _TrailSession:
    """Answers the page statement with ``page_rows`` and the lookup of the event a page
    starts after with ``anchor``; records every statement with its parameters."""

    def __init__(self, page_rows: list[dict], anchor: dict | None = None) -> None:
        self.page_rows = page_rows
        self.anchor = anchor
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "COUNT(*)" in sql:
            return _Rows(self.page_rows)
        return _Rows([self.anchor] if self.anchor else [])

    def page_params(self) -> dict:
        [params] = [params for sql, params in self.calls if "COUNT(*)" in sql]
        return params


def _list_trail(monkeypatch, session: _TrailSession, **kwargs) -> dict:
    async def _context(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid=_FAMILY_UUID, family_id=family_identifier, project_ids=[])

    monkeypatch.setattr(cas, "build_family_metadata_context", _context)
    # An admin: the trail is an analyst's or an admin's (see test_project_access_levels.py).
    return asyncio.run(
        cas.list_clinical_audit(session, family_id="FAM1", user=types.SimpleNamespace(role="admin"), **kwargs)
    )


def test_a_trail_longer_than_a_page_says_so_with_its_total(monkeypatch) -> None:
    # 450 events: the page reads one more than it holds, so it knows older ones remain.
    session = _TrailSession([_event_row(n, total=450) for n in range(450, 249, -1)])
    page = _list_trail(monkeypatch, session)

    assert page["family_id"] == "FAM1"
    assert page["total"] == 450
    assert page["has_more"] is True
    assert len(page["events"]) == 200
    assert [event["summary"] for event in page["events"][:2]] == ["event 450", "event 449"]
    assert page["events"][-1]["summary"] == "event 251"
    # The events are served as events: the count is not one of their fields.
    assert set(page["events"][0]) == {
        "id", "created_at", "variant_id", "actor", "action", "summary", "before", "after",
    }
    # The first page: the newest events of this family, 200 and one more.
    assert len(session.calls) == 1
    assert session.page_params() == {
        "family_uuid": _FAMILY_UUID,
        "before_created_at": None,
        "before_id": None,
        "limit": 201,
    }


def test_the_trail_is_read_in_one_order_newest_first_with_its_count(monkeypatch) -> None:
    session = _TrailSession([_event_row(1, total=1)])
    _list_trail(monkeypatch, session)

    [(sql, _params)] = session.calls
    flat = " ".join(sql.split())
    # A total order (the hash chain's, reversed), so a page boundary falls between the same
    # two events on every read; and the count comes from the same statement as the page.
    assert "ORDER BY created_at DESC, id DESC" in flat
    assert "ORDER BY page.created_at DESC, page.id DESC" in flat
    assert (
        "SELECT COUNT(*) AS total FROM clinical_audit_events "
        "WHERE family_id = CAST(:family_uuid AS uuid)"
    ) in flat
    assert (
        "(created_at, id) < (CAST(:before_created_at AS timestamptz), CAST(:before_id AS uuid))"
    ) in flat


def test_the_last_page_says_no_older_events_remain(monkeypatch) -> None:
    session = _TrailSession([_event_row(n, total=3) for n in (3, 2, 1)])
    page = _list_trail(monkeypatch, session)

    assert page["total"] == 3
    assert page["has_more"] is False
    assert [event["summary"] for event in page["events"]] == ["event 3", "event 2", "event 1"]


def test_an_empty_trail_is_listed_as_empty(monkeypatch) -> None:
    # The count's row comes back without an event when the page is empty.
    empty = {field: None for field in _event_row(0, total=0)} | {"total": 0}
    page = _list_trail(monkeypatch, _TrailSession([empty]))
    assert (page["total"], page["has_more"], page["events"]) == (0, False, [])


def test_the_next_page_holds_the_events_recorded_before_the_last_one_shown(monkeypatch) -> None:
    anchor_id = "00000000-0000-0000-0000-000000000251"
    anchor = {"created_at": datetime(2026, 6, 25, 13, 11, tzinfo=timezone.utc), "id": anchor_id}
    session = _TrailSession([_event_row(n, total=450) for n in range(250, 49, -1)], anchor=anchor)
    # The id as the page served it, in any spelling of it.
    page = _list_trail(monkeypatch, session, before="{" + anchor_id.upper() + "}")

    assert page["has_more"] is True
    assert page["events"][0]["summary"] == "event 250"
    lookup_sql, lookup_params = session.calls[0]
    # The event must be one of this family's: looked up by its canonical id and the family.
    assert "family_id = CAST(:family_uuid AS uuid)" in lookup_sql
    assert lookup_params == {"event_id": anchor_id, "family_uuid": _FAMILY_UUID}
    assert session.page_params() == {
        "family_uuid": _FAMILY_UUID,
        "before_created_at": anchor["created_at"],
        "before_id": anchor_id,
        "limit": 201,
    }


def test_a_page_after_an_event_that_is_not_in_the_trail_is_answered_404(monkeypatch) -> None:
    # Unknown, another family's (the lookup finds neither), or no UUID at all: one answer,
    # and no page is read.
    for before in ("00000000-0000-0000-0000-000000000999", "not-an-event"):
        session = _TrailSession([_event_row(1, total=1)], anchor=None)
        with pytest.raises(HTTPException) as refused:
            _list_trail(monkeypatch, session, before=before)
        assert (refused.value.status_code, refused.value.detail) == (404, "Audit event not found")
        assert not [sql for sql, _params in session.calls if "COUNT(*)" in sql]


def test_the_page_size_is_bounded_and_read_as_a_number(monkeypatch) -> None:
    for limit, read in ((10_000, cas.CLINICAL_AUDIT_MAX_PAGE_SIZE + 1), (0, 2), (-5, 2), ("25", 26)):
        session = _TrailSession([_event_row(1, total=1)])
        _list_trail(monkeypatch, session, limit=limit)
        assert session.page_params()["limit"] == read, limit


def _trail_route_client(monkeypatch):
    """A signed-in client whose clinical-audit route records how it calls the service."""
    from fastapi.testclient import TestClient

    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_session
    from backend.app.dependencies import get_current_user
    from backend.app.main import app
    from backend.app.routers import families_reports
    from backend.app.services.access_control import CurrentUser

    calls: list[dict] = []

    async def _list(session, **kwargs):
        calls.append(kwargs)
        return {"family_id": kwargs["family_id"], "total": 3, "has_more": True, "events": []}

    async def _session():
        yield object()

    async def _user():
        return CurrentUser(
            id="u1",
            username="viewer@example.com",
            email="viewer@example.com",
            role="viewer",
            created_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(families_reports, "list_clinical_audit", _list)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, _session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, _user)
    monkeypatch.setattr(app.state, "skip_startup_tasks", True, raising=False)
    # No database here: the request audit is not written.
    monkeypatch.setattr(settings, "audit_log_mode", "off")
    return TestClient(app), calls


def test_the_route_serves_a_page_and_bounds_its_size(monkeypatch) -> None:
    client, calls = _trail_route_client(monkeypatch)
    with client:
        first = client.get("/api/families/FAM1/clinical-audit")
        older = client.get(
            "/api/families/FAM1/clinical-audit",
            params={"limit": 50, "before": "00000000-0000-0000-0000-000000000251"},
        )
        too_small = client.get("/api/families/FAM1/clinical-audit", params={"limit": 0})
        too_large = client.get(
            "/api/families/FAM1/clinical-audit",
            params={"limit": cas.CLINICAL_AUDIT_MAX_PAGE_SIZE + 1},
        )

    assert first.status_code == 200, first.text
    assert first.json() == {"family_id": "FAM1", "total": 3, "has_more": True, "events": []}
    assert older.status_code == 200, older.text
    assert [(call["limit"], call["before"]) for call in calls] == [
        (cas.CLINICAL_AUDIT_PAGE_SIZE, None),
        (50, "00000000-0000-0000-0000-000000000251"),
    ]
    # A page size out of bounds is refused before the trail is read.
    assert (too_small.status_code, too_large.status_code) == (422, 422)
    assert len(calls) == 2
