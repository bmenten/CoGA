"""Each change to a family's phenotypes is on its clinical audit trail (SEC-1).

Phenotypes feed the prioritised ranking and the PP4 suggestion, and an analyst of the
family's project or an admin may add, change and remove them. Each change writes one event
to the family's hash-chained clinical audit trail (``clinical_audit_events``), in the
transaction of the change: action ``phenotype``, the annotation before and after (None for one
added or removed), a summary that names what changed, and the member and annotation in its
metadata. A save that changes nothing writes no event.

The database is replaced by a session that answers the service's statements and an in-memory
annotation store; ``record_clinical_event`` is replaced by one that records each event and
whether the transaction had been committed when it was written. The access rules are tested in
test_project_access_levels.py; the events on real Postgres, with their chain, in
``e2e/test_e2e_project_reader_role.py``. All IDs are synthetic.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.schemas import HpoAnnotationUpdate
from backend.app.services import hpo_service
from backend.app.services.hpo_service import phenotype_audit_state, phenotype_change_summary

FAMILY_UUID = "00000000-0000-4000-8000-0000000000f1"
SAMPLE_UUID = "00000000-0000-4000-8000-0000000000f2"
ANNOTATION_ID = "00000000-0000-4000-8000-0000000000f3"
ANALYST = SimpleNamespace(id="00000000-0000-4000-8000-0000000000f4", username="analyst@example.org")
LABELS = {"HP:0001250": "Seizure", "HP:0001263": "Global developmental delay"}


def _annotation(**fields: Any) -> dict[str, Any]:
    annotation = {
        "id": ANNOTATION_ID,
        "sample_id": "FAM1_S1",
        "hpo_id": "HP:0001250",
        "label": "Seizure",
        "definition": "A seizure.",
        "status": "present",
        "onset": None,
        "evidence": None,
        "source": "manual",
        "note": None,
        "created_at": "2026-10-14T10:00:00+00:00",
        "updated_at": "2026-10-14T10:00:00+00:00",
    }
    annotation.update(fields)
    return annotation


class _Result:
    def __init__(self, value: Any = None, rowcount: int = 0) -> None:
        self.value = value
        self.rowcount = rowcount

    def scalar_one(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _Session:
    """Answers the service's own statements from ``store`` (annotation id -> annotation)."""

    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.store = store
        self.commits = 0
        self.locks: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        if "pg_advisory_xact_lock" in sql:
            self.locks.append(params["k"])
            return _Result()
        if sql.startswith("SELECT id::text FROM individual_hpo"):
            held = [
                annotation_id
                for annotation_id, row in self.store.items()
                if (row["hpo_id"], row["status"]) == (params["hpo_id"], params["status"])
            ]
            return _Result(held[0] if held else None)
        if sql.startswith("INSERT INTO individual_hpo"):
            held = [
                annotation_id
                for annotation_id, row in self.store.items()
                if (row["hpo_id"], row["status"]) == (params["hpo_id"], params["status"])
            ]
            annotation_id = held[0] if held else ANNOTATION_ID
            row = self.store.get(annotation_id) or _annotation(
                id=annotation_id, hpo_id=params["hpo_id"], status=params["status"], label=LABELS[params["hpo_id"]]
            )
            row.update({name: params[name] for name in ("onset", "evidence", "source", "note")})
            self.store[annotation_id] = row
            return _Result(annotation_id)
        if sql.startswith("UPDATE individual_hpo"):
            row = self.store[params["annotation_id"]]
            row.update({name: params[name] for name in ("hpo_id", "status", "onset", "evidence", "source", "note")})
            row["label"] = LABELS[params["hpo_id"]]
            return _Result(rowcount=1)
        if sql.startswith("DELETE FROM individual_hpo"):
            removed = self.store.pop(params["annotation_id"], None)
            return _Result(rowcount=1 if removed else 0)
        raise AssertionError(f"unexpected statement: {sql[:80]}")

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The events written, and the database the service sees."""
    store: dict[str, dict[str, Any]] = {}
    session = _Session(store)
    events: list[dict[str, Any]] = []

    async def schema_available(_session: Any) -> None:
        return None

    async def sample_uuid(_session: Any, *, family_uuid: str, sample_id: str) -> str:
        if sample_id != "FAM1_S1":
            raise HTTPException(status_code=404, detail="Family member was not found")
        return SAMPLE_UUID

    async def known_terms(_session: Any, hpo_ids: Any) -> set[str]:
        return {hpo_id for hpo_id in hpo_ids if hpo_id in LABELS}

    async def annotation_by_id(_session: Any, *, family_uuid: str, annotation_id: str) -> dict[str, Any] | None:
        row = store.get(annotation_id)
        return dict(row) if row is not None else None

    async def no_stale_mark(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def record_event(_session: Any, **event: Any) -> None:
        events.append({**event, "committed_before": session.commits})

    monkeypatch.setattr(hpo_service, "_ensure_hpo_schema_available", schema_available)
    monkeypatch.setattr(hpo_service, "_sample_uuid_for", sample_uuid)
    monkeypatch.setattr(hpo_service, "_existing_hpo_terms", known_terms)
    monkeypatch.setattr(hpo_service, "_annotation_by_id", annotation_by_id)
    monkeypatch.setattr(hpo_service, "mark_family_hpo_annotations_stale", no_stale_mark)
    monkeypatch.setattr(hpo_service, "record_clinical_event", record_event)
    return {"session": session, "store": store, "events": events}


def _add(recorded: dict[str, Any], **payload: Any) -> dict[str, Any]:
    fields = {"hpo_id": "HP:0001250", "status": "present", "onset": None, "evidence": None, "source": None, "note": None}
    fields.update(payload)
    return asyncio.run(
        hpo_service.create_individual_hpo_annotation(
            recorded["session"],
            family_uuid=FAMILY_UUID,
            family_identifier="FAM1",
            sample_id="FAM1_S1",
            payload=SimpleNamespace(**fields),
            user=ANALYST,
        )
    )


def _change(recorded: dict[str, Any], **fields: Any) -> dict[str, Any]:
    # The API's own update model: a field it leaves out keeps its value.
    return asyncio.run(
        hpo_service.update_individual_hpo_annotation(
            recorded["session"],
            family_uuid=FAMILY_UUID,
            family_identifier="FAM1",
            annotation_id=ANNOTATION_ID,
            payload=HpoAnnotationUpdate(**fields),
            user=ANALYST,
        )
    )


def _remove(recorded: dict[str, Any]) -> None:
    asyncio.run(
        hpo_service.delete_individual_hpo_annotation(
            recorded["session"],
            family_uuid=FAMILY_UUID,
            family_identifier="FAM1",
            annotation_id=ANNOTATION_ID,
            user=ANALYST,
        )
    )


def _state(**fields: Any) -> dict[str, Any]:
    state = phenotype_audit_state(_annotation(**fields))
    assert state is not None
    return state


def test_a_phenotype_added_is_one_event_with_what_was_added(recorded: dict[str, Any]) -> None:
    _add(recorded, note="since birth")

    [event] = recorded["events"]
    assert event["action"] == "phenotype"
    assert (event["family_uuid"], event["family_identifier"], event["variant_id"]) == (FAMILY_UUID, "FAM1", None)
    assert (event["actor"], event["actor_id"]) == (ANALYST.username, ANALYST.id)
    assert event["summary"] == "Phenotype added for FAM1_S1: HP:0001250 Seizure (present)"
    assert event["before"] is None
    assert event["after"] == _state(note="since birth")
    assert event["metadata"] == {"sample_id": "FAM1_S1", "annotation_id": ANNOTATION_ID}
    # Written in the transaction of the change, which is committed once, after it.
    assert event["committed_before"] == 0
    assert recorded["session"].commits == 1


def test_each_change_waits_for_the_last_one_of_its_family(recorded: dict[str, Any]) -> None:
    _add(recorded)
    _change(recorded, status="absent")
    _remove(recorded)
    assert recorded["session"].locks == [f"hpo:{FAMILY_UUID}"] * 3


def test_adding_a_phenotype_the_member_has_records_nothing(recorded: dict[str, Any]) -> None:
    _add(recorded)
    _add(recorded)
    assert len(recorded["events"]) == 1


def test_adding_a_phenotype_the_member_has_with_a_new_note_records_the_change(recorded: dict[str, Any]) -> None:
    _add(recorded)
    _add(recorded, note="since birth")

    event = recorded["events"][-1]
    assert event["summary"] == "Phenotype changed for FAM1_S1: HP:0001250 Seizure: note added"
    assert (event["before"]["note"], event["after"]["note"]) == (None, "since birth")


def test_a_phenotype_changed_is_one_event_with_before_and_after(recorded: dict[str, Any]) -> None:
    _add(recorded, note="since birth")
    _change(recorded, status="absent", hpo_id="HP:0001263", note="since age 2")

    event = recorded["events"][-1]
    assert event["summary"] == (
        "Phenotype changed for FAM1_S1: HP:0001263 Global developmental delay: "
        "term HP:0001250 Seizure → HP:0001263 Global developmental delay; status present → absent; "
        "note edited"
    )
    assert event["before"] == _state(note="since birth")
    assert event["after"] == _state(
        hpo_id="HP:0001263", label="Global developmental delay", status="absent", note="since age 2"
    )
    assert event["committed_before"] == 1


def test_a_change_that_changes_nothing_records_nothing(recorded: dict[str, Any]) -> None:
    _add(recorded)
    _change(recorded, status="present")
    assert len(recorded["events"]) == 1


def test_a_phenotype_removed_is_one_event_with_what_was_removed(recorded: dict[str, Any]) -> None:
    _add(recorded, evidence="clinical exam")
    _remove(recorded)

    event = recorded["events"][-1]
    assert event["summary"] == "Phenotype removed for FAM1_S1: HP:0001250 Seizure (present)"
    assert (event["before"], event["after"]) == (_state(evidence="clinical exam"), None)
    assert recorded["store"] == {}


def test_removing_an_unknown_phenotype_records_nothing(recorded: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as refused:
        _remove(recorded)
    assert refused.value.status_code == 404
    assert recorded["events"] == []


def test_the_audited_state_is_text_or_none() -> None:
    assert phenotype_audit_state(None) is None
    assert phenotype_audit_state(_annotation(note="  ", onset=" infantile ")) == {
        "sample_id": "FAM1_S1",
        "hpo_id": "HP:0001250",
        "label": "Seizure",
        "status": "present",
        "onset": "infantile",
        "evidence": None,
        "source": "manual",
        "note": None,
    }


def test_a_summary_names_each_field_that_changed() -> None:
    before = _state(onset="infantile", evidence="exam", note="n", source="manual")
    after = _state(onset=None, evidence="EEG", note="n", source="import")
    assert phenotype_change_summary(before, after) == (
        "Phenotype changed for FAM1_S1: HP:0001250 Seizure: onset removed; evidence edited; source edited"
    )
    # A term without a label in the loaded release is named by its ID alone.
    unlabelled = _state(hpo_id="HP:9999999", label="HP:9999999")
    assert phenotype_change_summary(None, unlabelled) == "Phenotype added for FAM1_S1: HP:9999999 (present)"
