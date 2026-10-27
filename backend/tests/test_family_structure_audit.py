"""A family's pedigree on the clinical record: chained structure versions and pedigree events (SEC-7).

Each change of a family's pedigree (its members with their sex, role, affected and carrier
status, whether each is active, and their parents, couples and relatives) is stored as the
family's next structure version, on a hash chain of its own per family, and recorded on the
family's clinical audit trail as a ``pedigree`` event naming who made it and what changed,
before and after. Before, a structure version could be rewritten or deleted, carried no
hash chain, and no pedigree or member change reached the clinical audit trail.

These tests cover the audited pedigree and its diff (a status change, a member added,
deleted or made inactive, a rename, the couples and the relatives), the summaries, the
chain (an append links to the head and verifies; an edited row breaks it), and the writers
that record a change: family creation, a structure edit, a member edit's rename, a package
import's status refresh and an admin's deletion of a sample. The values are synthetic; the
database is a fake that keeps the rows as Postgres gives them back. Real Postgres is
exercised by ``integration/test_family_structure_chain_integration.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas import (
    FamilyMemberImpactOut,
    FamilyMemberOut,
    FamilyMemberUpdate,
    FamilyOut,
    FamilyRelationshipOut,
    FamilyStructureUpdate,
    FamilyStructureUpdateOut,
)
from app.services import admin_service
from app.services import family_member_management_service as members_service
from app.services import family_package_registration
from app.services import family_structure_audit as audit
from app.services import family_structure_service as structure
from app.services import integrity_anchor_service
from app.services.access_control import CurrentUser
from backend.tests._deletion_mark_fakes import UnmarkedDeletion

FAMILY = "FAM-SEC7"


def _admin() -> CurrentUser:
    return CurrentUser(
        id="11111111-1111-1111-1111-111111111111",
        username="analyst-a",
        email="analyst-a@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def _member(sample_id: str, role: str, sex: str, status: str = "unaffected", **fields: Any) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "sex": sex,
        "role": role,
        "clinical_status": status,
        "carrier_status": "unknown",
        "carrier_type": None,
        "carrier_evidence": {},
        "active": True,
        **fields,
    }


def _parent(parent: str, child: str, role: str, **fields: Any) -> dict[str, Any]:
    return {
        "relationship_type": "parent_child",
        "sample_id_a": parent,
        "sample_id_b": child,
        "role_a": role,
        "role_b": "child",
        "source": "pedigree",
        "metadata": {},
        **fields,
    }


def _couple(a: str, b: str, **metadata: Any) -> dict[str, Any]:
    return {
        "relationship_type": "couple",
        "sample_id_a": a,
        "sample_id_b": b,
        "role_a": "partner",
        "role_b": "partner",
        "source": "pedigree",
        "metadata": metadata,
    }


def _trio(child_status: str = "unknown") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    members = [
        _member("F1", "father", "male"),
        _member("M1", "mother", "female"),
        _member("P1", "proband", "und", child_status),
    ]
    relationships = [_parent("F1", "P1", "father"), _parent("M1", "P1", "mother"), _couple("F1", "M1")]
    return members, relationships


# --------------------------------------------------------------------------- the audited pedigree


def test_the_audited_pedigree_holds_each_member_with_its_parents_and_the_active_links() -> None:
    members, relationships = _trio()
    relationships += [
        {**_couple("F1", "P1"), "active": False},  # an inactive row: not part of the pedigree
        {
            "relationship_type": "relative",
            "sample_id_a": "M1",
            "sample_id_b": "R1",
            "role_a": "relative",
            "role_b": "relative",
            "metadata": {"relation": "cousin"},
        },
        _parent("X1", "R1", "parent"),
    ]
    members += [_member("R1", "relative", "female"), _member("X1", "relative", "und", active=False)]

    state = audit.pedigree_state(members, relationships)

    assert state["members"]["P1"] == {
        "sample_id": "P1",
        "sex": "und",
        "role": "proband",
        "clinical_status": "unknown",
        "carrier_status": "unknown",
        "carrier_type": None,
        "carrier_evidence": {},
        "active": True,
        "father": "F1",
        "mother": "M1",
        "parents": [],
    }
    # A parent recorded without a sex-specific role is an other parent.
    assert (state["members"]["R1"]["father"], state["members"]["R1"]["parents"]) == (None, ["X1"])
    assert state["members"]["X1"]["active"] is False
    assert state["couples"] == [{"partners": ["F1", "M1"], "metadata": {}}]
    assert state["relatives"] == [{"member": "R1", "related_to": "M1", "metadata": {"relation": "cousin"}}]


def test_an_unchanged_pedigree_has_no_change() -> None:
    state = audit.pedigree_state(*_trio())
    assert audit.diff_pedigree(state, audit.pedigree_state(*_trio())) == ([], {}, {})


def test_a_status_change_names_the_member_and_its_old_and_new_value() -> None:
    before = audit.pedigree_state(*_trio("unknown"))
    after = audit.pedigree_state(*_trio("affected"))

    phrases, was, now = audit.diff_pedigree(before, after)

    assert phrases == ["P1: affected status unknown → affected"]
    assert was == {"members": {"P1": before["members"]["P1"]}}
    assert now == {"members": {"P1": after["members"]["P1"]}}


def test_a_member_added_deleted_or_made_inactive_is_named() -> None:
    before = audit.pedigree_state(*_trio())
    members, relationships = _trio()
    members[1] = {**members[1], "active": False}  # M1 removed in the editor: inactive
    members.append(_member("S1", "sibling", "female", "affected"))
    relationships = [link for link in relationships if "M1" not in (link["sample_id_a"], link["sample_id_b"])]
    after = audit.pedigree_state(members, relationships)
    members_gone = [member for member in members if member["sample_id"] != "F1"]
    gone = audit.pedigree_state(members_gone, [])

    phrases, was, now = audit.diff_pedigree(before, after)
    assert phrases == [
        "M1 made inactive",
        "P1: mother M1 → none",
        "S1 added (sibling, female, affected)",
        "couple F1 + M1 removed",
    ]
    assert was["members"]["S1"] is None and now["members"]["S1"]["role"] == "sibling"
    assert was["couples"] == [{"partners": ["F1", "M1"], "metadata": {}}] and now["couples"] == []

    phrases, was, now = audit.diff_pedigree(after, gone)
    assert "F1 deleted" in phrases
    assert now["members"]["F1"] is None and was["members"]["F1"]["role"] == "father"


def test_a_rename_is_one_member_renamed_and_its_child_keeps_its_parent() -> None:
    before = audit.pedigree_state(*_trio())
    members, relationships = _trio()
    members[0] = {**members[0], "sample_id": "F1b", "sex": "male"}
    relationships = [
        {**link, "sample_id_a": "F1b" if link["sample_id_a"] == "F1" else link["sample_id_a"]}
        for link in relationships
    ]
    after = audit.pedigree_state(members, relationships)

    phrases, was, now = audit.diff_pedigree(before, after, {"F1b": "F1"})

    # The child's father is the same member under his new ID, and so is the couple.
    assert phrases == ["F1 renamed F1b"]
    assert (was["members"]["F1b"]["sample_id"], now["members"]["F1b"]["sample_id"]) == ("F1", "F1b")
    # Without the rename it reads as one member gone and another new.
    phrases, _was, _now = audit.diff_pedigree(before, after)
    assert "F1 deleted" in phrases and "F1b added (father, male, unaffected)" in phrases


def test_a_rename_with_other_changes_names_them_all() -> None:
    before = audit.pedigree_state(*_trio())
    members, relationships = _trio()
    members[2] = {**members[2], "sample_id": "P2", "sex": "female", "clinical_status": "affected"}
    relationships = [
        {**link, "sample_id_b": "P2" if link["sample_id_b"] == "P1" else link["sample_id_b"]}
        for link in relationships
    ]
    after = audit.pedigree_state(members, relationships)

    phrases, _was, _now = audit.diff_pedigree(before, after, {"P2": "P1"})

    assert phrases == ["P1 renamed P2: sex unknown → female, affected status unknown → affected"]


def test_couples_and_relatives_added_removed_or_changed_are_named() -> None:
    members, relationships = _trio()
    members.append(_member("R1", "relative", "male"))
    before = audit.pedigree_state(members, relationships)
    relative = {
        "relationship_type": "relative",
        "sample_id_a": "M1",
        "sample_id_b": "R1",
        "role_a": "relative",
        "role_b": "relative",
        "metadata": {},
    }
    changed = [*relationships[:2], _couple("F1", "M1", context="consanguineous"), relative]
    after = audit.pedigree_state(members, changed)

    phrases, was, now = audit.diff_pedigree(before, after)

    assert phrases == ["couple F1 + M1 changed", "relative R1 (through M1) added"]
    assert now["couples"] == [{"partners": ["F1", "M1"], "metadata": {"context": "consanguineous"}}]
    assert (was["relatives"], now["relatives"]) == ([], [{"member": "R1", "related_to": "M1", "metadata": {}}])


def test_the_carrier_fields_are_named_without_quoting_the_evidence() -> None:
    before = audit.pedigree_state(*_trio())
    members, relationships = _trio()
    members[1] = {
        **members[1],
        "carrier_status": "carrier",
        "carrier_type": "obligate",
        "carrier_evidence": {"basis": "affected son"},
    }
    after = audit.pedigree_state(members, relationships)

    phrases, _was, now = audit.diff_pedigree(before, after)

    assert phrases == ["M1: carrier status unknown → carrier, carrier type none → obligate, carrier evidence added"]
    assert now["members"]["M1"]["carrier_evidence"] == {"basis": "affected son"}


def test_the_summaries_name_at_most_five_changes() -> None:
    members = [_member(f"S{index}", "sibling", "female") for index in range(1, 7)]
    state = audit.pedigree_state(members, [])
    assert audit.pedigree_recorded_summary(state) == (
        "Pedigree recorded with 6 members: S1 (sibling, female, unaffected); S2 (sibling, female, unaffected); "
        "S3 (sibling, female, unaffected); S4 (sibling, female, unaffected); S5 (sibling, female, unaffected); "
        "and 1 more"
    )
    assert audit.pedigree_recorded_summary(audit.pedigree_state([members[0]], [])) == (
        "Pedigree recorded with 1 member: S1 (sibling, female, unaffected)"
    )
    assert audit.pedigree_changed_summary(audit.STRUCTURE_EDIT, ["a", "b"]) == "Pedigree changed: a; b"
    assert audit.pedigree_changed_summary(audit.PACKAGE_IMPORT, ["a"]) == "Pedigree changed by a package import: a"
    assert audit.pedigree_changed_summary(audit.SAMPLE_DELETED, ["S1 deleted"]) == (
        "Pedigree changed by deleting a sample with its data: S1 deleted"
    )


# ----------------------------------------------------------------------------------- the chain


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _ChainDatabase:
    """``family_structure_versions`` and ``clinical_audit_events`` as Postgres keeps them:
    each JSONB value given back decoded from its text, each timestamp as stored."""

    def __init__(self, snapshot: dict[str, Any] | None = None) -> None:
        self.versions: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.locks: list[str] = []
        self.snapshot = snapshot or {"members": [], "relationships": []}
        self.commits = 0
        # What a family's deletion reads of it: its samples and the data Postgres holds.
        self.samples: list[str] = []
        self.held: dict[str, int] = {}

    def _chain(self, rows: list[dict[str, Any]], family_identifier: Any) -> list[dict[str, Any]]:
        return sorted(
            (row for row in rows if row["family_identifier"] == family_identifier and row["row_hash"]),
            key=lambda row: (row["created_at"], row["id"]),
        )

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        if "pg_advisory_xact_lock" in sql:
            self.locks.append(params["k"])
            return _Result()
        if "FROM family_members" in sql:
            return _Result(self.snapshot["members"])
        if "FROM family_relationships" in sql:
            return _Result(self.snapshot["relationships"])
        if "COALESCE(MAX(version), 0) + 1" in sql:
            numbers = [row["version"] for row in self.versions if row["family_id"] == params["family_uuid"]]
            return _Result(scalar=max(numbers, default=0) + 1)
        if sql.startswith("INSERT INTO family_structure_versions"):
            row = dict(params)
            row["family_id"] = row.pop("family_uuid")
            row["snapshot"] = json.loads(row["snapshot"])
            row["metadata"] = json.loads(row["metadata"])
            self.versions.append(row)
            return _Result()
        if sql.startswith("INSERT INTO clinical_audit_events"):
            row = dict(params)
            for key in ("before", "after", "metadata"):
                row[key] = json.loads(row[key]) if row[key] is not None else None
            self.events.append(row)
            return _Result()
        if "SELECT created_at, row_hash FROM family_structure_versions" in sql:
            chain = self._chain(self.versions, params["family_identifier"])
            return _Result(chain[-1:][::-1])
        if "SELECT created_at, row_hash FROM clinical_audit_events" in sql:
            chain = self._chain(self.events, params["family_identifier"])
            return _Result(chain[-1:][::-1])
        if "FROM family_structure_versions" in sql and "ORDER BY created_at ASC" in sql:
            return _Result(self._chain(self.versions, params["family_identifier"]))
        if sql.startswith("SELECT sample_id FROM samples"):
            return _Result([(sample_id,) for sample_id in self.samples])  # type: ignore[misc]
        if sql.startswith("SELECT MAX(version) FROM family_structure_versions"):
            numbers = [row["version"] for row in self.versions if row["family_id"] == params["family_uuid"]]
            return _Result(scalar=max(numbers, default=None))
        if "'small_variant_reviews' AS kind" in sql:
            return _Result([(kind, count) for kind, count in self.held.items()])  # type: ignore[misc]
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self) -> None:
        self.commits += 1


def _snapshot(members: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "members": members,
        "relationships": [{"active": True, **link} for link in relationships],
    }


@pytest.mark.asyncio
async def test_each_version_links_to_the_head_of_its_familys_chain_and_the_chain_verifies() -> None:
    database = _ChainDatabase(_snapshot(*_trio()))

    first = await audit.append_structure_version(
        database, family_uuid="fam-uuid", family_identifier=FAMILY, snapshot=database.snapshot  # type: ignore[arg-type]
    )
    database.snapshot = _snapshot(*_trio("affected"))
    second = await audit.append_structure_version(
        database,  # type: ignore[arg-type]
        family_uuid="fam-uuid",
        family_identifier=FAMILY,
        snapshot=database.snapshot,
        metadata={"reason": "family_member_detail", "data_counts_before_change": {"small_variants": 3}},
        created_by="11111111-1111-1111-1111-111111111111",
    )

    assert (first.version, second.version) == (1, 2)
    assert database.locks == [f"fsv:{FAMILY}", f"fsv:{FAMILY}"]
    stored = database.versions
    assert stored[0]["prev_hash"] is None and stored[1]["prev_hash"] == stored[0]["row_hash"] == first.row_hash
    assert stored[1]["created_at"] > stored[0]["created_at"]
    assert stored[1]["family_identifier"] == FAMILY and stored[1]["structure_hash"] == second.structure_hash
    # The structure hash is the ranking cache's key: the stored snapshot's SHA-256, as before.
    assert second.structure_hash == audit.structure_hash(database.snapshot)

    verified = await audit.verify_family_structure_chain(database, FAMILY)  # type: ignore[arg-type]
    assert (verified.verified, verified.rows_checked) == (True, 2)

    # An edited version (the affected status put back) breaks the chain at that row.
    stored[1]["snapshot"]["members"][2]["clinical_status"] = "unknown"
    tampered = await audit.verify_family_structure_chain(database, FAMILY)  # type: ignore[arg-type]
    assert tampered.verified is False
    assert tampered.first_bad_row == str(stored[1]["id"])
    assert tampered.reason == "row_hash mismatch (row content tampered)"


@pytest.mark.asyncio
async def test_a_deleted_version_breaks_the_chain() -> None:
    database = _ChainDatabase(_snapshot(*_trio()))
    for _ in range(3):
        await audit.append_structure_version(
            database, family_uuid="fam-uuid", family_identifier=FAMILY, snapshot=database.snapshot  # type: ignore[arg-type]
        )
    del database.versions[1]

    result = await audit.verify_family_structure_chain(database, FAMILY)  # type: ignore[arg-type]

    assert result.verified is False
    assert result.reason == "prev_hash does not match the predecessor row_hash (row deleted or reordered)"


@pytest.mark.asyncio
async def test_a_new_familys_pedigree_is_recorded_on_the_clinical_audit_trail() -> None:
    database = _ChainDatabase(_snapshot(*_trio()))
    change = audit.PedigreeChange(source=audit.FAMILY_CREATED, actor="analyst-a", actor_id=None)

    version = await audit.record_structure_version(
        database, family_uuid="fam-uuid", family_identifier=FAMILY, change=change  # type: ignore[arg-type]
    )

    [event] = database.events
    assert (event["action"], event["actor"], event["family_identifier"]) == ("pedigree", "analyst-a", FAMILY)
    assert event["summary"] == (
        "Pedigree recorded with 3 members: F1 (father, male, unaffected); "
        "M1 (mother, female, unaffected); P1 (proband, sex unknown, affected status unknown)"
    )
    assert event["before"] is None
    assert event["after"]["members"]["P1"]["father"] == "F1"
    assert event["metadata"] == {
        "source": "family_created",
        "structure_version": 1,
        "structure_hash": version.structure_hash,
    }
    # The pedigree lock (taken before the pedigree is read, and again by the append), then
    # the clinical audit trail's.
    assert database.locks == [f"fsv:{FAMILY}", f"fsv:{FAMILY}", f"cae:{FAMILY}"]
    assert database.commits == 0  # the caller's transaction


@pytest.mark.asyncio
async def test_an_edit_that_changes_nothing_of_the_pedigree_records_no_event() -> None:
    database = _ChainDatabase(_snapshot(*_trio()))
    change = audit.pedigree_change_by(_admin(), source=audit.STRUCTURE_EDIT, before=audit.pedigree_state(*_trio()))

    await audit.record_structure_version(
        database, family_uuid="fam-uuid", family_identifier=FAMILY, change=change  # type: ignore[arg-type]
    )

    assert len(database.versions) == 1 and database.events == []


@pytest.mark.asyncio
async def test_a_writer_other_than_the_editor_records_a_version_only_when_the_pedigree_changed() -> None:
    before = _snapshot(*_trio())
    database = _ChainDatabase(_snapshot(*_trio()))

    unchanged = await audit.record_structure_change(
        database,  # type: ignore[arg-type]
        family_uuid="fam-uuid",
        family_identifier=FAMILY,
        before_snapshot=before,
        user=_admin(),
        source=audit.PACKAGE_IMPORT,
    )
    assert unchanged is None and database.versions == [] and database.events == []

    database.snapshot = _snapshot(*_trio("affected"))
    changed = await audit.record_structure_change(
        database,  # type: ignore[arg-type]
        family_uuid="fam-uuid",
        family_identifier=FAMILY,
        before_snapshot=before,
        user=_admin(),
        source=audit.PACKAGE_IMPORT,
    )

    assert changed is not None and changed.version == 1
    [version] = database.versions
    assert version["metadata"] == {"reason": "package_import"}
    assert version["created_by"] == "11111111-1111-1111-1111-111111111111"
    [event] = database.events
    assert event["summary"] == "Pedigree changed by a package import: P1: affected status unknown → affected"
    assert (event["actor"], event["actor_id"]) == ("analyst-a", "11111111-1111-1111-1111-111111111111")
    assert event["before"]["members"]["P1"]["clinical_status"] == "unknown"
    assert event["after"]["members"]["P1"]["clinical_status"] == "affected"
    assert event["metadata"]["source"] == "package_import"


# ---------------------------------------------------------------------------- the writers


@pytest.mark.asyncio
async def test_a_structure_edit_records_what_it_changed_with_the_members_it_renamed(monkeypatch) -> None:
    current = {
        structure._sample_key(row["sample_id"]): {**row, "family_uuid": "fam-uuid", "sample_uuid": f"u-{row['sample_id']}"}
        for row in _trio()[0]
    }
    recorded: dict[str, Any] = {}

    async def mapping(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "fam-uuid", "family_id": FAMILY}

    async def version(*_args: Any, **_kwargs: Any) -> int:
        return 1

    async def member_rows(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {key: dict(row) for key, row in current.items()}

    async def relationship_rows(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [link for link in _trio()[1]]

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def record_version(_session: Any, **kwargs: Any) -> None:
        recorded.update(kwargs)

    async def family_record(*_args: Any, **_kwargs: Any) -> FamilyOut:
        return FamilyOut(
            _id="fam-uuid", family_id=FAMILY, created_at=datetime.now(timezone.utc),
            members=[], relationships=[], projects=[], metadata={},
        )

    for name, fake in {
        "get_accessible_family_mapping": mapping,
        "_fetch_current_structure_version": version,
        "_fetch_family_member_rows": member_rows,
        "_fetch_current_relationship_rows": relationship_rows,
        "_update_member_row": noop,
        "_record_family_structure_version": record_version,
        "get_family_record": family_record,
    }.items():
        monkeypatch.setattr(structure, name, fake)

    class _Session:
        async def execute(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    await structure.update_family_structure_for_admin(
        _Session(),  # type: ignore[arg-type]
        family_id=FAMILY,
        update=FamilyStructureUpdate(
            change_reason="family_member_detail",
            members=[{"sample_id": "P1", "clinical_status": "affected"}],
        ),
        user=_admin(),
        renamed_members={"P1": "P0"},
    )

    change = recorded["change"]
    assert recorded["family_identifier"] == FAMILY
    assert (change.source, change.actor, change.reason) == ("structure_edit", "analyst-a", "family_member_detail")
    # The edit read the family after the caller renamed P0 to P1: the pedigree it records
    # as before has the member under its old ID again.
    assert change.before == audit.pedigree_before_renames(audit.pedigree_state(*_trio()), {"P1": "P0"})
    assert sorted(change.before["members"]) == ["F1", "M1", "P0"]
    assert change.renamed == {"P1": "P0"}


@pytest.mark.asyncio
@pytest.mark.parametrize("clear", [False, True])
async def test_a_structure_edit_holds_the_pedigree_lock_before_it_reads_the_pedigree(monkeypatch, clear: bool) -> None:
    # Two edits of one family wait for each other from the start: each reads the pedigree the
    # other committed, so neither audit event records another's change as its own. A clear
    # of the imported data takes the variant-write locks first, as every writer holding both.
    steps: list[str] = []

    async def mapping(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "fam-uuid", "family_id": FAMILY}

    async def version(*_args: Any, **_kwargs: Any) -> int:
        steps.append("read version")
        return 1

    async def member_rows(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        steps.append("read members")
        return {structure._sample_key(row["sample_id"]): {**row, "sample_uuid": row["sample_id"]} for row in _trio()[0]}

    async def relationship_rows(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(_trio()[1])

    async def counts(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        return {}

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def family_record(*_args: Any, **_kwargs: Any) -> FamilyOut:
        return FamilyOut(
            _id="fam-uuid", family_id=FAMILY, created_at=datetime.now(timezone.utc),
            members=[], relationships=[], projects=[], metadata={},
        )

    for name, fake in {
        "get_accessible_family_mapping": mapping,
        "_fetch_current_structure_version": version,
        "_fetch_family_member_rows": member_rows,
        "_fetch_current_relationship_rows": relationship_rows,
        "_family_genomic_data_counts": counts,
        "_update_member_row": noop,
        "_record_family_structure_version": noop,
        "get_family_record": family_record,
    }.items():
        monkeypatch.setattr(structure, name, fake)

    class _Session:
        async def execute(self, statement: Any, params: Any = None) -> None:
            if "pg_advisory_xact_lock" in str(statement):
                steps.append(f"lock {params['k']}")

        async def commit(self) -> None:
            steps.append("commit")

    await structure.update_family_structure_for_admin(
        _Session(),  # type: ignore[arg-type]
        family_id=FAMILY,
        update=FamilyStructureUpdate(
            clear_existing_genomic_data=clear, members=[{"sample_id": "P1", "clinical_status": "affected"}]
        ),
        user=_admin(),
    )

    variant_locks = [f"lock family-variant-writes:{kind}:fam-uuid" for kind in ("small_variants", "structural_variants")]
    expected = [*(variant_locks if clear else []), f"lock fsv:{FAMILY}", "read version", "read members"]
    assert steps[: len(expected)] == expected
    assert steps[-1] == "commit"


def test_the_pedigree_before_a_rename_has_the_member_and_its_links_under_the_old_id() -> None:
    # Read after two renames of one change: A1 to A2, then C1 to A1 (the ID A1 was free).
    members = [
        _member("A2", "father", "male"),
        _member("A1", "mother", "female"),
        _member("K1", "proband", "female", "affected"),
    ]
    relationships = [_parent("A2", "K1", "father"), _parent("A1", "K1", "mother"), _couple("A2", "A1")]
    read_after = audit.pedigree_state(members, relationships)

    before = audit.pedigree_before_renames(read_after, {"A2": "A1", "A1": "C1"})

    assert sorted(before["members"]) == ["A1", "C1", "K1"]
    assert (before["members"]["K1"]["father"], before["members"]["K1"]["mother"]) == ("A1", "C1")
    assert before["couples"] == [{"partners": ["A1", "C1"], "metadata": {}}]
    phrases, _was, _now = audit.diff_pedigree(before, read_after, {"A2": "A1", "A1": "C1"})
    assert phrases == ["C1 renamed A1", "A1 renamed A2"]


@pytest.mark.asyncio
async def test_a_member_edit_that_renames_tells_the_structure_edit_the_old_id(monkeypatch) -> None:
    passed: dict[str, Any] = {}

    def family(child: str) -> FamilyOut:
        return FamilyOut(
            _id="fam-uuid",
            family_id=FAMILY,
            created_at=datetime.now(timezone.utc),
            members=[
                FamilyMemberOut(sample_id="F1", role="father", affected=False, sex="male"),
                FamilyMemberOut(sample_id=child, role="proband", affected=True, sex="female"),
            ],
            relationships=[
                FamilyRelationshipOut(
                    id="rel-father", relationship_type="parent_child", sample_id_a="F1", sample_id_b=child,
                    role_a="father", role_b="child",
                )
            ],
            projects=[],
            metadata={},
        )

    async def mapping(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "fam-uuid", "family_id": FAMILY}

    async def sample_uuid(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        return "uuid-child", "P1"

    async def rename(*_args: Any, **_kwargs: Any) -> None:
        passed["renamed"] = True

    async def family_record(*_args: Any, **_kwargs: Any) -> FamilyOut:
        # The family as read before the rename and after it.
        return family("P2" if passed.get("renamed") else "P1")

    async def impact(_session: Any, *, sample_id: str, **_kwargs: Any) -> FamilyMemberImpactOut:
        return FamilyMemberImpactOut(sample_id=sample_id)

    async def structure_update(_session: Any, **kwargs: Any) -> FamilyStructureUpdateOut:
        passed.update(kwargs)
        return FamilyStructureUpdateOut(family=family("P2"))

    monkeypatch.setattr(members_service, "get_accessible_family_mapping", mapping)
    monkeypatch.setattr(members_service, "_sample_uuid_for_member", sample_uuid)
    monkeypatch.setattr(members_service, "_rename_member", rename)
    monkeypatch.setattr(members_service, "get_family_record", family_record)
    monkeypatch.setattr(members_service, "get_family_member_impact_for_user", impact)
    monkeypatch.setattr(members_service, "update_family_structure_for_admin", structure_update)

    class _Session:
        async def execute(self, statement: Any, params: Any = None) -> None:
            if "pg_advisory_xact_lock" in str(statement):
                passed.setdefault("locks", []).append((params["k"], passed.get("renamed", False)))

    await members_service.update_family_member_for_admin(
        _Session(),  # type: ignore[arg-type]
        family_id=FAMILY,
        sample_id="P1",
        update=FamilyMemberUpdate(sample_id="P2"),
        user=_admin(),
    )

    assert passed["renamed"] is True
    assert passed["renamed_members"] == {"P2": "P1"}
    # The family's pedigree lock, taken before the rename.
    assert passed["locks"] == [(f"fsv:{FAMILY}", False)]


@pytest.mark.asyncio
async def test_a_package_imports_status_refresh_is_checked_for_a_pedigree_change(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    before = _snapshot(*_trio())

    async def snapshot(_session: Any, family_uuid: str) -> dict[str, Any]:
        return before

    async def structure_change(_session: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    async def no_raw_files(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(family_package_registration, "read_structure_snapshot", snapshot)
    monkeypatch.setattr(family_package_registration, "record_structure_change", structure_change)
    monkeypatch.setattr(family_package_registration, "_record_package_raw_files", no_raw_files)

    class _Session:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))

        async def commit(self) -> None:
            calls.append({"commit": True})

    bundle = SimpleNamespace(
        manifest=SimpleNamespace(
            schema_version=1, metadata={}, analysis_type=None, samples={}, relationships=[],
            carriers={}, members={},
        ),
        ped=SimpleNamespace(members=[], text=""),
        root=Path("/packages/F1"),
        source_uri=None,
        manifest_path=Path("/packages/F1/manifest.yaml"),
        ped_path=None,
    )
    monkeypatch.setattr(family_package_registration, "_manifest_pgt_metadata", lambda manifest: {})
    monkeypatch.setattr(family_package_registration, "_normalize_manifest_samples", lambda samples: {})
    monkeypatch.setattr(family_package_registration, "_sample_provenance", lambda bundle: {})
    monkeypatch.setattr(family_package_registration, "_manifest_carrier_types", lambda manifest: {})
    monkeypatch.setattr(family_package_registration, "_manifest_member_status_overrides", lambda manifest, ped: {})
    monkeypatch.setattr(family_package_registration, "_dataset_provenance", lambda validation: [])

    await family_package_registration._register_package_provenance(
        _Session(),  # type: ignore[arg-type]
        bundle=bundle,  # type: ignore[arg-type]
        validation=SimpleNamespace(family_id=FAMILY),  # type: ignore[arg-type]
        family_uuid="fam-uuid",
        family_identifier=FAMILY,
        user=_admin(),
    )

    [change, commit] = calls
    assert commit == {"commit": True}  # recorded before the import's commit
    assert change["before_snapshot"] is before
    assert (change["family_uuid"], change["family_identifier"], change["source"]) == (
        "fam-uuid",
        FAMILY,
        "package_import",
    )
    assert change["user"].username == "analyst-a"


@pytest.mark.asyncio
async def test_deleting_a_sample_with_its_data_records_the_pedigree_change(monkeypatch) -> None:
    trace: list[Any] = []
    before = _snapshot(*_trio())

    async def sample_row(_session: Any, sample_id: str) -> dict[str, Any]:
        return {"sample_uuid": "uuid-p1", "sample_id": sample_id, "family_uuid": "fam-uuid", "family_id": FAMILY}

    async def rows_by_family(_session: Any, family_uuids: list[str]) -> dict[str, list[Any]]:
        return {family_uuid: [] for family_uuid in family_uuids}

    async def contexts(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def nothing(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def pairs(contexts: list[Any], _sample_row: Any) -> list[Any]:
        return []

    async def snapshot(_session: Any, family_uuid: str) -> dict[str, Any]:
        trace.append(("snapshot", family_uuid))
        return before

    async def phenotypes_deleted(_session: Any, **kwargs: Any) -> int:
        trace.append(("phenotypes", kwargs))
        return 0

    async def structure_change(_session: Any, **kwargs: Any) -> None:
        trace.append(("change", kwargs))

    class _Session:
        async def execute(self, statement: Any, params: Any = None) -> Any:
            if "DELETE FROM samples" in str(statement):
                trace.append(("delete", dict(params)))
            if "pg_advisory_xact_lock" in str(statement):
                trace.append(("lock", params["k"]))
            return SimpleNamespace(fetchall=lambda: [])

        async def commit(self) -> None:
            trace.append(("commit", None))

    for name, fake in {
        "_sample_row_or_404": sample_row,
        "_sample_rows_by_family": rows_by_family,
        "_family_assembly_contexts": contexts,
        "lock_family_variant_writes": nothing,
        "delete_interval_track_sources": nothing,
        "_small_variant_entries_without_sample": pairs,
        "_structural_variant_rows_without_sample": pairs,
        "purge_sample_managed_files": nothing,
        "record_phenotypes_deleted_with_sample": phenotypes_deleted,
        "read_structure_snapshot": snapshot,
        "record_structure_change": structure_change,
        # Its mark is test_deletions_mark_the_family.py's (SAFE-31).
        "FamilyDeletion": UnmarkedDeletion,
    }.items():
        monkeypatch.setattr(admin_service, name, fake)

    await admin_service.delete_sample_with_data(_Session(), "P1", True, user=_admin())  # type: ignore[arg-type]

    # The sample's phenotypes are recorded as removed first, before the pedigree lock (TRACE-16).
    assert [step for step, _detail in trace] == ["phenotypes", "lock", "snapshot", "delete", "change", "commit"]
    phenotypes = trace[0][1]
    assert (phenotypes["family_identifier"], phenotypes["sample_id"], phenotypes["change"]) == (
        FAMILY,
        "P1",
        "deleted_with_sample",
    )
    assert (phenotypes["origin"].via, phenotypes["origin"].user.username) == ("admin_deletion", "analyst-a")
    assert trace[1] == ("lock", f"fsv:{FAMILY}")
    change = trace[4][1]
    assert change["before_snapshot"] is before
    assert (change["family_identifier"], change["source"], change["metadata"]) == (
        FAMILY,
        "sample_deleted",
        {"sample_id": "P1"},
    )
    assert change["user"].username == "analyst-a"


def test_the_structure_versions_are_verified_and_anchored_with_the_other_chains() -> None:
    from app.routers import admin as admin_router

    assert admin_router._INTEGRITY_VERIFIERS["family_structure_versions"] is audit.verify_family_structure_chain
    assert integrity_anchor_service._CHAIN_ORDER_COLS["family_structure_versions"] == ["created_at", "id"]


# ------------------------------------------------------------------------ the family's deletion


@pytest.mark.asyncio
async def test_a_familys_deletion_is_the_last_event_of_its_chain_with_what_went() -> None:
    database = _ChainDatabase(_snapshot(*_trio()))
    await audit.record_structure_version(
        database,  # type: ignore[arg-type]
        family_uuid="fam-uuid",
        family_identifier=FAMILY,
        change=audit.PedigreeChange(source=audit.FAMILY_CREATED, actor="analyst-b"),
    )
    database.samples = ["F1", "M1", "P1"]
    database.held = {"small_variant_reviews": 4, "structural_variant_reviews": 1, "repeat_expansions": 2}

    await audit.record_family_deleted(
        database,  # type: ignore[arg-type]
        family_uuid="fam-uuid",
        family_identifier=FAMILY,
        user=_admin(),
        source=audit.ADMIN_DELETE,
        variant_counts={"small_variants": 1204, "structural_variants": 3},
    )

    created, deleted = database.events
    assert (deleted["action"], deleted["actor"], deleted["actor_id"]) == (
        "family_deleted",
        "analyst-a",
        "11111111-1111-1111-1111-111111111111",
    )
    assert deleted["summary"] == (
        "Family deleted with its data: 3 samples (F1, M1, P1), 1204 small variants, 3 structural variants, "
        "5 variant reviews"
    )
    assert deleted["before"] == {
        "samples": ["F1", "M1", "P1"],
        "structure_version": 1,
        "data": {
            "small_variants": 1204,
            "structural_variants": 3,
            "small_variant_reviews": 4,
            "structural_variant_reviews": 1,
            "repeat_expansions": 2,
            "reviews": 5,
        },
    }
    assert deleted["after"] is None and deleted["metadata"] == {"source": "admin_delete"}
    # On the family's chain, after its pedigree: the deletion links to the event before it.
    assert deleted["prev_hash"] == created["row_hash"] and deleted["family_identifier"] == FAMILY
    # Under the family's pedigree lock, then the audit trail's.
    assert database.locks[-2:] == [f"fsv:{FAMILY}", f"cae:{FAMILY}"]
    assert database.commits == 0  # the deletion's transaction


def test_the_deletion_summary_names_five_samples_at_most_and_says_why() -> None:
    samples = [f"S{index}" for index in range(1, 8)]
    assert audit.family_deleted_summary(audit.PEDIGREE_REPLACED, samples, {"reviews": 0}) == (
        "Family deleted, to be replaced by the pedigree uploaded with overwrite: 7 samples "
        "(S1, S2, S3, S4, S5 and 2 more), 0 variant reviews"
    )
    assert audit.family_deleted_summary(audit.IMPORT_FAILED, ["S1"], {"reviews": 0}) == (
        "Family deleted after the package import that created it imported nothing: 1 sample (S1), "
        "0 variant reviews"
    )
    assert audit.family_deleted_summary(audit.ADMIN_DELETE, [], {}) == "Family deleted with its data: 0 samples"


def _admin_family_delete(monkeypatch, steps: list[str], *, failing: str | None = None) -> None:
    """The admin's family delete over fakes of its stores; ``steps`` records what it did."""

    async def resolve(_session: Any, family_id: str) -> str:
        return "fam-uuid"

    async def sample_rows(_session: Any, family_uuids: list[str]) -> dict[str, list[Any]]:
        return {uuid: [] for uuid in family_uuids}

    async def contexts(*_args: Any, **_kwargs: Any) -> list[Any]:
        return [SimpleNamespace(assembly_name="GRCh38", family_uuid="fam-uuid", project_ids=["p1"])]

    def step(name: str, result: Any = None):
        async def run(*_args: Any, **_kwargs: Any) -> Any:
            steps.append(name)
            if name == failing:
                raise RuntimeError(f"{name} failed")
            return result

        return run

    async def counted(*_args: Any, **_kwargs: Any) -> int:
        steps.append("count")
        return 7

    async def family_deleted(_session: Any, **kwargs: Any) -> None:
        steps.append(f"audit: family deleted {kwargs['family_identifier']} {kwargs['variant_counts']}")

    async def phenotypes_deleted(_session: Any, **kwargs: Any) -> int:
        origin = kwargs["origin"]
        steps.append(f"audit: phenotypes {kwargs['change']} by {origin.via} as {origin.user.username}")
        return 0

    for name, fake in {
        "_resolve_family_uuid": resolve,
        "_sample_rows_by_family": sample_rows,
        "_family_assembly_contexts": contexts,
        "lock_family_variant_writes": step("lock"),
        "count_family_small_variants": counted,
        "count_family_structural_variants": counted,
        "record_phenotypes_deleted_with_family": phenotypes_deleted,
        "record_family_deleted": family_deleted,
        "delete_family_small_variants": step("ClickHouse: delete small variants"),
        "delete_family_structural_variants": step("ClickHouse: delete structural variants"),
        "delete_interval_tracks": step("ClickHouse: delete interval tracks"),
        "delete_interval_track_sources": step("delete interval track sources", 0),
        "purge_family_managed_files": step("purge raw files", 0),
        # Its mark is test_deletions_mark_the_family.py's (SAFE-31).
        "FamilyDeletion": UnmarkedDeletion,
    }.items():
        monkeypatch.setattr(admin_service, name, fake)


class _DeleteSession:
    def __init__(self, steps: list[str]) -> None:
        self.steps = steps

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.steps.append(" ".join(str(statement).split()).split(" WHERE ")[0])
        return SimpleNamespace(fetchall=lambda: [])

    async def commit(self) -> None:
        self.steps.append("commit")


@pytest.mark.asyncio
async def test_the_admins_family_delete_records_its_event_before_it_deletes_anything(monkeypatch) -> None:
    steps: list[str] = []
    _admin_family_delete(monkeypatch, steps)

    await admin_service.delete_family_with_data(_DeleteSession(steps), FAMILY, True, user=_admin(), reason="duplicate")  # type: ignore[arg-type]

    event = f"audit: family deleted {FAMILY} {{'small_variants': 7, 'structural_variants': 7}}"
    # The phenotypes that go with the family are recorded as removed just before (TRACE-16).
    phenotypes = "audit: phenotypes deleted_with_family by admin_deletion as analyst-a"
    assert steps[: steps.index(event) + 1] == ["lock", "count", "count", phenotypes, event]
    assert steps.index(event) < steps.index("ClickHouse: delete small variants")
    assert steps.index(event) < steps.index("DELETE FROM families")
    assert steps[-1] == "commit"


@pytest.mark.asyncio
async def test_a_family_delete_that_fails_commits_nothing(monkeypatch) -> None:
    # The event is the transaction's: the failure leaves it uncommitted, so it is rolled back
    # with the rest of the deletion.
    steps: list[str] = []
    _admin_family_delete(monkeypatch, steps, failing="purge raw files")

    with pytest.raises(RuntimeError, match="purge raw files failed"):
        await admin_service.delete_family_with_data(_DeleteSession(steps), FAMILY, True, user=_admin(), reason="duplicate")  # type: ignore[arg-type]

    assert any(step.startswith("audit: family deleted") for step in steps)
    assert "commit" not in steps and "DELETE FROM families" not in steps


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "", "   "])
async def test_a_family_delete_without_a_reason_does_nothing(monkeypatch, reason) -> None:
    # The reason is on the family's last clinical audit event: without one, the deletion is
    # refused before it locks, counts or deletes anything (TRACE-16, the owner's decision of
    # 2026-10-27).
    from fastapi import HTTPException

    steps: list[str] = []
    _admin_family_delete(monkeypatch, steps)

    with pytest.raises(HTTPException) as refused:
        await admin_service.delete_family_with_data(_DeleteSession(steps), FAMILY, True, user=_admin(), reason=reason)  # type: ignore[arg-type]

    assert (refused.value.status_code, refused.value.detail) == (400, "A reason is required to delete a family.")
    assert steps == []


@pytest.mark.asyncio
async def test_the_delete_route_takes_the_reason_from_its_body(monkeypatch) -> None:
    # The reason is free text: it travels in the body, not the URL (TRACE-16).
    from app.routers import admin as admin_router
    from app.schemas import FamilyDeleteRequest

    seen: list[dict[str, Any]] = []

    async def deleted(_session: Any, family_id: str, confirm: bool, **kwargs: Any) -> dict[str, Any]:
        seen.append({"family_id": family_id, "confirm": confirm, **kwargs})
        return {}

    monkeypatch.setattr(admin_router, "delete_family_with_data", deleted)
    admin = _admin()

    await admin_router.delete_family(FAMILY, True, FamilyDeleteRequest(reason="Duplicate of F2"), session=None, user=admin)  # type: ignore[arg-type]
    await admin_router.delete_family(FAMILY, True, None, session=None, user=admin)  # type: ignore[arg-type]

    assert [(call["reason"], call["user"]) for call in seen] == [("Duplicate of F2", admin), (None, admin)]
