"""The phenotypes an import changes are on the family's clinical audit trail (SEC-1).

A phenotype added, changed or removed on screen writes a ``phenotype`` event (#887); so does
every import that changes a family's phenotypes (HPO terms): a package import's phenotypes, a
failed import's restore that puts them back, a failed import that removes the family it had
created, and a PED upload or the Family Builder that replaces a family. Each writes, in the
transaction of the change and under the family's phenotype lock (the one an edit takes), one
event per member whose phenotypes changed: as the user who ran the import, naming the package
or file, with each annotation the change altered or removed as it was (before) and each it
added or altered as it is (after), and a summary that names the changes. An import that
changes no phenotype writes none.

The database is replaced by a session that answers the service's statements from an
in-memory ``individual_hpo``; ``record_clinical_event`` by one that records each event and
whether the transaction had been committed when it was written. Where each writer calls this
and in what order is tested with the writers (test_ped_overwrite_deletes_variants_last.py,
test_family_variant_writes_serialized.py, test_family_import_compensation.py,
test_family_package_import.py); the events on real Postgres, with their chain, in
``e2e/test_e2e_imported_phenotypes_audited.py``. All IDs are synthetic.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import families_reports
from backend.app.schemas import ClinicalAuditOut
from backend.app.services import hpo_service, postgres_family_snapshot
from backend.app.services.access_control import CurrentUser
from backend.app.services.hpo_service import (
    FAMILY_BUILDER,
    PACKAGE_IMPORT,
    PED_UPLOAD,
    PHENOTYPES_IMPORTED,
    PHENOTYPES_PUT_BACK,
    PHENOTYPES_REPLACED,
    PHENOTYPES_ROLLED_BACK,
    HpoAnnotationImportRow,
    PhenotypeImport,
    imported_phenotype_changes,
    imported_phenotypes_summary,
    phenotype_audit_state,
)
from backend.app.services.postgres_family_snapshot import FamilyPostgresSnapshot

FAMILY_UUID = "00000000-0000-4000-8000-0000000000f1"
SAMPLES = {"S1": "00000000-0000-4000-8000-0000000000a1", "S2": "00000000-0000-4000-8000-0000000000a2"}
LABELS = {
    "HP:0001250": "Seizure",
    "HP:0001263": "Global developmental delay",
    "HP:0000252": "Microcephaly",
}
IMPORTER = SimpleNamespace(id="00000000-0000-4000-8000-0000000000e1", username="importer@example.org")
ORIGIN = PhenotypeImport(
    user=IMPORTER,  # type: ignore[arg-type]
    via=PACKAGE_IMPORT,
    source="/imports/FAM1_pkg",
    files=("phenotypes.tsv",),
    job_id="job-1",
)


def _annotation(sample_id: str, hpo_id: str, status: str = "present", **fields: Any) -> dict[str, Any]:
    return {
        "id": f"annotation-{sample_id}-{hpo_id}-{status}",
        "sample_id": sample_id,
        "hpo_id": hpo_id,
        "label": LABELS.get(hpo_id, hpo_id),
        "definition": None,
        "status": status,
        "onset": fields.get("onset"),
        "evidence": fields.get("evidence"),
        "source": fields.get("source", "manual"),
        "note": fields.get("note"),
    }


def _state(sample_id: str, hpo_id: str, status: str = "present", **fields: Any) -> dict[str, Any]:
    state = phenotype_audit_state(_annotation(sample_id, hpo_id, status, **fields))
    assert state is not None
    return state


def _states(*annotations: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    states = {}
    for annotation in annotations:
        state = phenotype_audit_state(annotation)
        assert state is not None
        states[(state["sample_id"], state["hpo_id"], state["status"])] = state
    return states


class _Session:
    """The family's ``individual_hpo`` (by member, term and status) and the statements the
    import runs against it, in order."""

    def __init__(self, *annotations: dict[str, Any]) -> None:
        self.store = {(row["sample_id"], row["hpo_id"], row["status"]): row for row in annotations}
        self.statements: list[str] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> None:
        sql = " ".join(str(statement).split())
        if "pg_advisory_xact_lock" in sql:
            self.statements.append(f"lock {params['k']}")
            return None
        if sql.startswith("INSERT INTO individual_hpo"):
            self.statements.append("upsert")
            by_uuid = {uuid: sample_id for sample_id, uuid in SAMPLES.items()}
            for row in params:
                key = (by_uuid[row["sample_uuid"]], row["hpo_id"], row["status"])
                self.store[key] = _annotation(
                    key[0],
                    row["hpo_id"],
                    row["status"],
                    onset=row["onset"],
                    evidence=row["evidence"],
                    source=row["source"],
                    note=row["note"],
                )
            return None
        raise AssertionError(f"unexpected statement: {sql[:80]}")

    async def commit(self) -> None:
        self.statements.append("commit")
        self.commits += 1


@pytest.fixture()
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The events written, each with the statements its transaction had run before it."""
    written: list[dict[str, Any]] = []

    async def record_event(session: Any, **event: Any) -> None:
        written.append({**event, "statements_before": list(session.statements)})

    async def read(session: Any, *, family_uuid: str, **_kwargs: Any) -> list[dict[str, Any]]:
        assert family_uuid == FAMILY_UUID
        session.statements.append("read")
        return [dict(row) for row in session.store.values()]

    async def known_terms(_session: Any, hpo_ids: Any) -> set[str]:
        return {hpo_id for hpo_id in hpo_ids if hpo_id in LABELS}

    monkeypatch.setattr(hpo_service, "record_clinical_event", record_event)
    monkeypatch.setattr(hpo_service, "list_family_hpo_annotations", read)
    monkeypatch.setattr(hpo_service, "_existing_hpo_terms", known_terms)
    return written


def _row(sample_id: str, hpo_id: str, status: str = "present", **fields: Any) -> HpoAnnotationImportRow:
    return HpoAnnotationImportRow(
        family_id="FAM1",
        individual_id=sample_id,
        hpo_id=hpo_id,
        status=status,
        source=fields.pop("source", "family_package:phenotypes.tsv"),
        **fields,
    )


def _import(session: _Session, *rows: HpoAnnotationImportRow) -> dict[str, Any]:
    return asyncio.run(
        hpo_service.import_family_hpo_annotations(
            session,  # type: ignore[arg-type]
            family_uuid=FAMILY_UUID,
            family_id="FAM1",
            sample_uuids_by_id=SAMPLES,
            rows=rows,
            origin=ORIGIN,
        )
    )


# --------------------------------------------------------------------------- a package import


def test_an_import_writes_one_event_per_member_whose_phenotypes_it_changed(events: list[dict[str, Any]]) -> None:
    session = _Session()
    _import(
        session,
        _row("S1", "HP:0001250", note="since birth"),
        _row("S1", "HP:0001263", "absent"),
        _row("S2", "HP:0001250"),
    )

    assert [event["metadata"]["sample_id"] for event in events] == ["S1", "S2"]
    first = events[0]
    assert first["action"] == "phenotype"
    assert (first["family_uuid"], first["family_identifier"], first["variant_id"]) == (FAMILY_UUID, "FAM1", None)
    assert (first["actor"], first["actor_id"]) == (IMPORTER.username, IMPORTER.id)
    assert first["summary"] == (
        "Phenotypes imported for S1 by a package import of FAM1_pkg (phenotypes.tsv): "
        "added HP:0001250 Seizure (present); added HP:0001263 Global developmental delay (absent)"
    )
    # Nothing there before, as an edit's event of a term added has no before.
    assert first["before"] is None
    assert first["after"] == {
        "sample_id": "S1",
        "phenotypes": [
            _state("S1", "HP:0001250", note="since birth", source="family_package:phenotypes.tsv"),
            _state("S1", "HP:0001263", "absent", source="family_package:phenotypes.tsv"),
        ],
    }
    assert first["metadata"] == {
        "sample_id": "S1",
        "source": "package_import",
        "change": "imported",
        "package": "/imports/FAM1_pkg",
        "files": ["phenotypes.tsv"],
        "job_id": "job-1",
    }
    # Under the family's phenotype lock, taken before what the rows replace is read, and in
    # the transaction of the rows, which commits once, after the events.
    assert first["statements_before"] == [f"lock hpo:{FAMILY_UUID}", "read", "upsert", "read"]
    assert session.statements[-1] == "commit" and session.commits == 1


def test_an_import_that_changes_no_phenotype_writes_no_event(events: list[dict[str, Any]]) -> None:
    session = _Session(_annotation("S1", "HP:0001250", source="family_package:phenotypes.tsv"))

    result = _import(session, _row("S1", "HP:0001250"))

    assert result["imported"] == 1
    assert events == []
    assert session.commits == 1


def test_an_import_that_rewrites_an_annotation_names_each_field_it_changed(events: list[dict[str, Any]]) -> None:
    # An analyst's annotation, which the package's row of the same term and status rewrites.
    session = _Session(_annotation("S1", "HP:0001250", note="since birth", onset="infantile"))

    _import(session, _row("S1", "HP:0001250", note="since age 2"))

    [event] = events
    assert event["summary"] == (
        "Phenotypes imported for S1 by a package import of FAM1_pkg (phenotypes.tsv): "
        "changed HP:0001250 Seizure (present): onset removed, source edited, note edited"
    )
    assert event["before"] == {
        "sample_id": "S1",
        "phenotypes": [_state("S1", "HP:0001250", note="since birth", onset="infantile")],
    }
    assert event["after"] == {
        "sample_id": "S1",
        "phenotypes": [_state("S1", "HP:0001250", note="since age 2", source="family_package:phenotypes.tsv")],
    }


def test_rows_the_import_skips_write_nothing_and_take_no_lock(events: list[dict[str, Any]]) -> None:
    session = _Session()

    result = _import(session, _row("S1", "HP:9999999"), _row("NOBODY", "HP:0001250"))

    assert (result["imported"], result["skipped"]) == (0, 2)
    assert events == []
    assert session.statements == ["commit"]


# --------------------------------------------------------------------------- what changed


def test_the_changes_are_per_member_in_a_fixed_order() -> None:
    before = _states(_annotation("S2", "HP:0000252"), _annotation("S1", "HP:0001250", note="a"))
    after = _states(_annotation("S1", "HP:0001250", note="b"), _annotation("S1", "HP:0001263"))

    changes = imported_phenotype_changes(before, after)

    assert list(changes) == ["S1", "S2"]
    assert changes["S1"]["phrases"] == [
        "changed HP:0001250 Seizure (present): note edited",
        "added HP:0001263 Global developmental delay (present)",
    ]
    assert changes["S2"] == {
        "before": [_state("S2", "HP:0000252")],
        "after": [],
        "phrases": ["removed HP:0000252 Microcephaly (present)"],
    }


def test_a_term_name_from_another_hpo_release_is_no_change() -> None:
    before = _states(_annotation("S1", "HP:0001250"))
    renamed = _states({**_annotation("S1", "HP:0001250"), "label": "Seizures"})
    assert imported_phenotype_changes(before, renamed) == {}
    assert imported_phenotype_changes(before, before) == {}


def test_a_summary_names_five_changes_at_most_and_the_event_holds_them_all() -> None:
    terms = [f"HP:000{number:04d}" for number in range(1, 8)]
    changes = imported_phenotype_changes({}, _states(*(_annotation("S1", term) for term in terms)))

    summary = imported_phenotypes_summary("S1", changes["S1"]["phrases"], ORIGIN, PHENOTYPES_IMPORTED)

    assert summary.endswith("added HP:0000005 (present); and 2 more")
    assert len(changes["S1"]["after"]) == 7


@pytest.mark.parametrize(
    ("origin", "change", "summary"),
    [
        (
            ORIGIN,
            PHENOTYPES_PUT_BACK,
            "Phenotypes put back for S1 after a failed package import of FAM1_pkg: removed HP:0001250 Seizure (present)",
        ),
        (
            ORIGIN,
            PHENOTYPES_ROLLED_BACK,
            "Phenotypes removed for S1 with the family a failed package import of FAM1_pkg had created: "
            "removed HP:0001250 Seizure (present)",
        ),
        (
            PhenotypeImport(user=IMPORTER, via=PED_UPLOAD, source="family.ped"),  # type: ignore[arg-type]
            PHENOTYPES_REPLACED,
            "Phenotypes removed for S1: the family was replaced by a PED upload (family.ped): "
            "removed HP:0001250 Seizure (present)",
        ),
        (
            PhenotypeImport(user=IMPORTER, via=FAMILY_BUILDER),  # type: ignore[arg-type]
            PHENOTYPES_REPLACED,
            "Phenotypes removed for S1: the family was replaced in the Family Builder: "
            "removed HP:0001250 Seizure (present)",
        ),
    ],
)
def test_the_summary_says_how_the_import_changed_them(origin: PhenotypeImport, change: str, summary: str) -> None:
    assert imported_phenotypes_summary("S1", ["removed HP:0001250 Seizure (present)"], origin, change) == summary


def test_a_name_from_outside_is_shown_with_its_control_characters_escaped(events: list[dict[str, Any]]) -> None:
    # An uploaded file's name is the client's: one with a NUL could not be stored at all.
    origin = PhenotypeImport(user=IMPORTER, via=PED_UPLOAD, source="fam\x00ily\n.ped")  # type: ignore[arg-type]
    session = _Session(_annotation("S1", "HP:0001250"))

    asyncio.run(
        hpo_service.record_phenotypes_deleted_with_family(
            session,  # type: ignore[arg-type]
            family_uuid=FAMILY_UUID,
            family_identifier="FAM1",
            origin=origin,
            change=PHENOTYPES_REPLACED,
        )
    )

    [event] = events
    assert "(fam\\x00ily\\n.ped)" in event["summary"]
    assert event["metadata"]["file"] == "fam\\x00ily\\n.ped"
    assert "\x00" not in event["summary"] + event["metadata"]["file"]


# --------------------------------------------------------------------------- a family deleted


def test_the_phenotypes_a_family_goes_with_are_recorded_as_removed(events: list[dict[str, Any]]) -> None:
    session = _Session(_annotation("S1", "HP:0001250"), _annotation("S2", "HP:0001263", "absent", note="n"))

    written = asyncio.run(
        hpo_service.record_phenotypes_deleted_with_family(
            session,  # type: ignore[arg-type]
            family_uuid=FAMILY_UUID,
            family_identifier="FAM1",
            origin=ORIGIN,
            change=PHENOTYPES_ROLLED_BACK,
        )
    )

    assert written == 2
    assert [(event["before"], event["after"]) for event in events] == [
        ({"sample_id": "S1", "phenotypes": [_state("S1", "HP:0001250")]}, None),
        ({"sample_id": "S2", "phenotypes": [_state("S2", "HP:0001263", "absent", note="n")]}, None),
    ]
    assert events[0]["metadata"]["change"] == "rolled_back"
    # Under the family's phenotype lock; the caller deletes the family and commits.
    assert events[0]["statements_before"] == [f"lock hpo:{FAMILY_UUID}", "read"]
    assert session.commits == 0


def test_a_family_without_phenotypes_goes_without_an_event(events: list[dict[str, Any]]) -> None:
    written = asyncio.run(
        hpo_service.record_phenotypes_deleted_with_family(
            _Session(),  # type: ignore[arg-type]
            family_uuid=FAMILY_UUID,
            family_identifier="FAM1",
            origin=ORIGIN,
            change=PHENOTYPES_REPLACED,
        )
    )
    assert written == 0 and events == []


# --------------------------------------------------------------------------- a failed import's restore


class _RestoreSession(_Session):
    """The restore's statements: each table's rows deleted and put back, the metadata reset.
    Putting ``individual_hpo`` back sets the store to ``snapshot``."""

    def __init__(self, snapshot: list[dict[str, Any]], *current: dict[str, Any]) -> None:
        super().__init__(*current)
        self.snapshot = snapshot

    async def execute(self, statement: Any, params: Any = None) -> None:
        sql = " ".join(str(statement).split())
        if "pg_advisory_xact_lock" in sql:
            return await super().execute(statement, params)
        if sql.startswith("INSERT INTO individual_hpo"):
            self.store = {(row["sample_id"], row["hpo_id"], row["status"]): row for row in self.snapshot}
        self.statements.append(sql.split(" WHERE")[0].split(" SELECT")[0][:40])
        return None


def test_a_restore_records_the_phenotypes_it_puts_back(events: list[dict[str, Any]]) -> None:
    # The failed import had added Microcephaly and rewritten Seizure's note.
    before_import = [_annotation("S1", "HP:0001250", note="since birth")]
    session = _RestoreSession(
        before_import,
        _annotation("S1", "HP:0001250", note="since age 2"),
        _annotation("S1", "HP:0000252"),
    )
    snapshot = FamilyPostgresSnapshot(family_uuid=FAMILY_UUID, tables={"individual_hpo": "[]"})

    asyncio.run(
        postgres_family_snapshot.restore_family_postgres_state(
            session,  # type: ignore[arg-type]
            snapshot,
            family_identifier="FAM1",
            phenotype_import=ORIGIN,
        )
    )

    [event] = events
    assert event["summary"] == (
        "Phenotypes put back for S1 after a failed package import of FAM1_pkg: "
        "removed HP:0000252 Microcephaly (present); changed HP:0001250 Seizure (present): note edited"
    )
    assert event["before"] == {
        "sample_id": "S1",
        "phenotypes": [_state("S1", "HP:0000252"), _state("S1", "HP:0001250", note="since age 2")],
    }
    assert event["after"] == {"sample_id": "S1", "phenotypes": [_state("S1", "HP:0001250", note="since birth")]}
    assert event["metadata"]["change"] == "put_back"
    # What the restore replaces is read under the lock before its first delete, and the
    # event is written in the restore's transaction, before its commit.
    statements = event["statements_before"]
    assert statements[:2] == [f"lock hpo:{FAMILY_UUID}", "read"]
    assert statements[2].startswith("DELETE FROM individual_hpo")
    assert statements[-1] == "read"
    assert session.statements[-1] == "commit" and session.commits == 1


def test_a_restore_that_puts_no_phenotype_back_writes_no_event(events: list[dict[str, Any]]) -> None:
    unchanged = [_annotation("S1", "HP:0001250")]
    session = _RestoreSession(unchanged, *unchanged)

    asyncio.run(
        postgres_family_snapshot.restore_family_postgres_state(
            session,  # type: ignore[arg-type]
            FamilyPostgresSnapshot(family_uuid=FAMILY_UUID, tables={"individual_hpo": "[]"}),
            family_identifier="FAM1",
            phenotype_import=ORIGIN,
        )
    )

    assert events == [] and session.commits == 1


# --------------------------------------------------------------------------- the trail serves them


def _served(event: dict[str, Any], at: datetime) -> dict[str, Any]:
    """An event as the trail's page reads it back (``list_clinical_audit``)."""
    return {
        "id": str(uuid4()),
        "created_at": at,
        "variant_id": event["variant_id"],
        "actor": event["actor"],
        "action": event["action"],
        "summary": event["summary"],
        "before": event["before"],
        "after": event["after"],
    }


def test_the_trail_serves_every_kind_of_import_event(events: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    # Before: an import's before and after were lists, which the served schema
    # (ClinicalAuditEventOut: an object or none) refused, so the family's trail answered 500.
    session = _Session(_annotation("S1", "HP:0001250", note="since birth"))
    _import(session, _row("S1", "HP:0001250", note="since age 2"), _row("S2", "HP:0001263", "absent"))
    restore = _RestoreSession(
        [_annotation("S1", "HP:0001250")],
        _annotation("S1", "HP:0001250", note="since age 2"),
        _annotation("S1", "HP:0000252"),
    )
    asyncio.run(
        postgres_family_snapshot.restore_family_postgres_state(
            restore,  # type: ignore[arg-type]
            FamilyPostgresSnapshot(family_uuid=FAMILY_UUID, tables={"individual_hpo": "[]"}),
            family_identifier="FAM1",
            phenotype_import=ORIGIN,
        )
    )
    for change in (PHENOTYPES_ROLLED_BACK, PHENOTYPES_REPLACED):
        asyncio.run(
            hpo_service.record_phenotypes_deleted_with_family(
                _Session(_annotation("S1", "HP:0001250")),  # type: ignore[arg-type]
                family_uuid=FAMILY_UUID,
                family_identifier="FAM1",
                origin=ORIGIN,
                change=change,
            )
        )
    assert [event["metadata"]["change"] for event in events] == [
        "imported", "imported", "put_back", "rolled_back", "replaced"
    ]
    at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    page = {"family_id": "FAM1", "total": len(events), "has_more": False, "events": [_served(e, at) for e in events]}

    # The schema the route serves the page with takes each of them.
    served = ClinicalAuditOut.model_validate(page)
    assert [(event.before is None, event.after is None) for event in served.events] == [
        (False, False),  # a note rewritten
        (True, False),  # a term added
        (False, False),  # one removed and one changed back
        (False, True),  # removed with the family
        (False, True),
    ]

    # And the route itself answers 200 with them.
    user = CurrentUser(
        id=IMPORTER.id, username=IMPORTER.username, email="importer@example.org", role="admin", created_at=at
    )

    async def page_of_trail(_session: Any, **_kwargs: Any) -> dict[str, Any]:
        return page

    async def no_session():
        yield None

    async def signed_in() -> CurrentUser:
        return user

    original = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    monkeypatch.setattr(families_reports, "list_clinical_audit", page_of_trail)
    app.dependency_overrides[get_postgres_session] = no_session
    app.dependency_overrides[families_reports.get_current_user] = signed_in
    try:
        with TestClient(app) as client:
            response = client.get("/api/families/FAM1/clinical-audit")
    finally:
        app.dependency_overrides = original
    assert response.status_code == 200, response.text
    body = response.json()
    assert [event["after"]["phenotypes"][0]["hpo_id"] for event in body["events"] if event["after"]] == [
        "HP:0001250",
        "HP:0001263",
        "HP:0001250",
    ]
    assert body["events"][3]["before"] == {"sample_id": "S1", "phenotypes": [_state("S1", "HP:0001250")]}

