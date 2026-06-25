from __future__ import annotations

import asyncio
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

    class _Session:
        async def execute(self, _stmt, params=None):
            calls.append(params)

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
    assert len(calls) == 3  # classification, tags, note
    assert {c["action"] for c in calls} == {"classification", "tags", "note"}
    assert all(c["actor"] == "alice" and c["variant_id"] == "1-1-A-G" for c in calls)
    assert all(c["family_identifier"] == "FAM1" for c in calls)
