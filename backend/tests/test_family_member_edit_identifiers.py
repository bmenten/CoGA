"""A member or structure edit refuses a sample ID CoGA cannot store before anything is written.

A family or sample ID is printable text without spaces (``family_identifiers.py``). The member
edits (``PUT /families/{family}/members/{sample}``, ``PUT /families/{family}/members/batch``)
take a member's new sample ID and its father and mother from the request; the structure edit
(``PUT /families/{family}/structure``) takes the IDs of the members it adds. One that holds a
control character (C0 or DEL: a line break, a tab, an escape, a NUL) or whitespace is refused
(400) with the rule's message, before anything is read or written under it: no member of the
request is renamed, and a NUL never reaches Postgres. The whitespace around an ID is still
stripped, and an empty parent still clears the link. All IDs here are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
import pytest

from backend.app.schemas import (
    FamilyMemberBatchUpdate,
    FamilyMemberBatchUpdateItem,
    FamilyMemberImpactOut,
    FamilyMemberOut,
    FamilyMemberUpdate,
    FamilyOut,
    FamilyRelationshipOut,
    FamilyStructureMemberCreate,
    FamilyStructureUpdate,
    FamilyStructureUpdateOut,
)
from backend.app.services import family_member_management_service as members
from backend.app.services import family_structure_service as structure
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_identifiers import IDENTIFIER_RULE

FAMILY = "FAM001"
FATHER, MOTHER, CHILD = "FATHER1", "MOTHER1", "CHILD1"
NUL, TAB, LF, ESC, DEL, NBSP = "\x00", "\t", "\n", "\x1b", "\x7f", "\xa0"


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


class _ExplodingSession:
    """A session every query fails on: what ran against it read and wrote nothing."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("an ID no sample can have reached the database")


class _RecordingSession:
    """A session that keeps the parameters of every statement it is given."""

    def __init__(self) -> None:
        self.params: list[dict[str, Any]] = []

    async def execute(self, _statement: Any, params: Any = None) -> None:
        self.params.append(dict(params or {}))

    async def commit(self) -> None:
        return None


async def _nothing_read(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - must not run
    raise AssertionError("the edit read or wrote something before it refused the ID")


def _family(child: str = CHILD, *, parents: tuple[str, ...] = ("father", "mother")) -> FamilyOut:
    """FATHER1 and MOTHER1 with their child, linked to the parents named."""

    def member(sample_id: str, role: Any, sex: Any) -> FamilyMemberOut:
        return FamilyMemberOut(sample_id=sample_id, role=role, affected=False, sex=sex)

    return FamilyOut(
        _id="fam-uuid",
        family_id=FAMILY,
        created_at=datetime.now(timezone.utc),
        members=[member(FATHER, "father", "male"), member(MOTHER, "mother", "female"), member(child, "proband", "female")],
        relationships=[
            FamilyRelationshipOut(
                id=f"rel-{role}",
                relationship_type="parent_child",
                sample_id_a=FATHER if role == "father" else MOTHER,
                sample_id_b=child,
                role_a=role,
                role_b="child",
            )
            for role in parents
        ],
        projects=[],
        metadata={},
    )


def _refusal(detail: str) -> str:
    return f"{detail} {IDENTIFIER_RULE}"


# --------------------------------------------------------------------------- #
# A member edit: PUT /families/{family}/members/{sample}
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("update", "detail"),
    [
        ({"sample_id": f"CHILD{NUL}2"}, "Sample ID 'CHILD\\x002' contains a control character (\\x00)."),
        ({"sample_id": f"CHILD{TAB}2"}, "Sample ID 'CHILD\\t2' contains a control character (\\t)."),
        ({"sample_id": f" CHILD{LF}2 "}, "Sample ID 'CHILD\\n2' contains a control character (\\n)."),
        ({"sample_id": "CHILD 2"}, "Sample ID 'CHILD 2' contains a space."),
        ({"sample_id": f"CHILD{NBSP}2"}, "Sample ID 'CHILD\\xa02' contains whitespace (\\xa0)."),
        ({"father_id": f"FATHER{ESC}1"}, "Father ID 'FATHER\\x1b1' contains a control character (\\x1b)."),
        ({"mother_id": f"MOTHER{DEL}1"}, "Mother ID 'MOTHER\\x7f1' contains a control character (\\x7f)."),
        # A valid new ID does not let a bad parent through: nothing is renamed either.
        ({"sample_id": "CHILD2", "mother_id": "MOTHER 1"}, "Mother ID 'MOTHER 1' contains a space."),
    ],
)
async def test_a_member_edit_refuses_an_id_no_sample_can_be_stored_under_before_anything_is_read(
    monkeypatch: pytest.MonkeyPatch, update: dict[str, str], detail: str
) -> None:
    monkeypatch.setattr(members, "get_accessible_family_mapping", _nothing_read)
    monkeypatch.setattr(members, "get_family_record", _nothing_read)

    with pytest.raises(HTTPException) as refused:
        await members.update_family_member_for_admin(
            _ExplodingSession(),  # type: ignore[arg-type]
            family_id=FAMILY,
            sample_id=CHILD,
            update=FamilyMemberUpdate(**update),
            user=_admin(),
        )

    assert refused.value.status_code == 400
    assert refused.value.detail == _refusal(detail)


@pytest.mark.asyncio
async def test_a_member_edit_strips_the_whitespace_around_the_ids_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _RecordingSession()
    structure_updates: list[Any] = []

    async def mapping(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "fam-uuid", "family_id": FAMILY}

    async def sample_uuid(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        return "uuid-child", CHILD

    async def available(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def impact(_session: Any, *, sample_id: str, **_kwargs: Any) -> FamilyMemberImpactOut:
        return FamilyMemberImpactOut(sample_id=sample_id)

    async def family_record(*_args: Any, **_kwargs: Any) -> FamilyOut:
        # The family as read after the rename, once the UPDATE ran.
        return _family("CHILD2" if session.params else CHILD)

    async def structure_update(_session: Any, *, update: Any, **_kwargs: Any) -> FamilyStructureUpdateOut:
        structure_updates.append(update)
        return FamilyStructureUpdateOut(family=_family("CHILD2", parents=("father",)))

    monkeypatch.setattr(members, "get_accessible_family_mapping", mapping)
    monkeypatch.setattr(members, "_sample_uuid_for_member", sample_uuid)
    monkeypatch.setattr(members, "_ensure_sample_id_available", available)
    monkeypatch.setattr(members, "get_family_member_impact_for_user", impact)
    monkeypatch.setattr(members, "get_family_record", family_record)
    monkeypatch.setattr(members, "update_family_structure_for_admin", structure_update)

    response = await members.update_family_member_for_admin(
        session,  # type: ignore[arg-type]
        family_id=FAMILY,
        sample_id=CHILD,
        update=FamilyMemberUpdate(sample_id=" CHILD2 ", father_id=f" {FATHER} ", mother_id=""),
        user=_admin(),
    )

    assert session.params == [{"sample_uuid": "uuid-child", "sample_id": "CHILD2"}]
    # The father is linked under his ID without the whitespace; the empty mother clears hers.
    links = [(link.parent, link.child, link.parent_role) for link in structure_updates[0].relationships.parent_child]
    assert links == [(FATHER, "CHILD2", "father")]
    assert response.member.sample_id == "CHILD2"


def test_an_id_that_is_empty_once_stripped_is_left_to_the_route() -> None:
    # No rename for the batch, no parent link: the routes read these as they did.
    members._require_storable_member_ids(
        FamilyMemberBatchUpdateItem(sample_id=CHILD, new_sample_id="  ", father_id="", mother_id=" "),
        new_sample_id_field="new_sample_id",
    )
    members._require_storable_member_ids(FamilyMemberUpdate(sample_id=None, father_id=None), new_sample_id_field="sample_id")


# --------------------------------------------------------------------------- #
# A batch edit: PUT /families/{family}/members/batch
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("item", "detail"),
    [
        ({"sample_id": MOTHER, "new_sample_id": f"MOTHER{LF}2"}, "Sample ID 'MOTHER\\n2' contains a control character (\\n)."),
        ({"sample_id": MOTHER, "new_sample_id": f"MOTHER{NUL}2"}, "Sample ID 'MOTHER\\x002' contains a control character (\\x00)."),
        ({"sample_id": MOTHER, "new_sample_id": "MOTHER 2"}, "Sample ID 'MOTHER 2' contains a space."),
        ({"sample_id": CHILD, "father_id": f"FATHER{ESC}1"}, "Father ID 'FATHER\\x1b1' contains a control character (\\x1b)."),
        ({"sample_id": CHILD, "mother_id": f"MOTHER{TAB}1"}, "Mother ID 'MOTHER\\t1' contains a control character (\\t)."),
    ],
)
async def test_a_batch_edit_refuses_such_an_id_before_its_first_member_is_renamed(
    monkeypatch: pytest.MonkeyPatch, item: dict[str, str], detail: str
) -> None:
    monkeypatch.setattr(members, "get_accessible_family_mapping", _nothing_read)
    monkeypatch.setattr(members, "get_family_record", _nothing_read)
    # The first item would rename FATHER1: the batch loop writes it before it reads the second.
    batch = FamilyMemberBatchUpdate(
        updates=[
            FamilyMemberBatchUpdateItem(sample_id=FATHER, new_sample_id="FATHER2"),
            FamilyMemberBatchUpdateItem(**item),
        ]
    )

    with pytest.raises(HTTPException) as refused:
        await members.update_family_members_batch_for_admin(
            _ExplodingSession(),  # type: ignore[arg-type]
            family_id=FAMILY,
            update=batch,
            user=_admin(),
        )

    assert refused.value.status_code == 400
    assert refused.value.detail == _refusal(detail)


@pytest.mark.asyncio
async def test_a_batch_edit_strips_the_whitespace_around_the_ids_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _RecordingSession()
    structure_updates: list[Any] = []

    async def mapping(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "fam-uuid", "family_id": FAMILY}

    async def family_record(*_args: Any, **_kwargs: Any) -> FamilyOut:
        return _family(parents=("mother",))

    async def sample_uuid(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        return "uuid-child", CHILD

    async def available(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def impact(_session: Any, *, sample_id: str, **_kwargs: Any) -> FamilyMemberImpactOut:
        return FamilyMemberImpactOut(sample_id=sample_id)

    async def structure_update(_session: Any, *, update: Any, **_kwargs: Any) -> FamilyStructureUpdateOut:
        structure_updates.append(update)
        return FamilyStructureUpdateOut(family=_family("CHILD2", parents=("father",)))

    monkeypatch.setattr(members, "get_accessible_family_mapping", mapping)
    monkeypatch.setattr(members, "get_family_record", family_record)
    monkeypatch.setattr(members, "_sample_uuid_for_member", sample_uuid)
    monkeypatch.setattr(members, "_ensure_sample_id_available", available)
    monkeypatch.setattr(members, "get_family_member_impact_for_user", impact)
    monkeypatch.setattr(members, "update_family_structure_for_admin", structure_update)

    await members.update_family_members_batch_for_admin(
        session,  # type: ignore[arg-type]
        family_id=FAMILY,
        update=FamilyMemberBatchUpdate(
            updates=[
                FamilyMemberBatchUpdateItem(
                    sample_id=CHILD, new_sample_id=" CHILD2 ", father_id=f" {FATHER} ", mother_id=""
                )
            ]
        ),
        user=_admin(),
    )

    assert session.params == [{"sample_uuid": "uuid-child", "sample_id": "CHILD2"}]
    links = [(link.parent, link.child, link.parent_role) for link in structure_updates[0].relationships.parent_child]
    assert links == [(FATHER, "CHILD2", "father")]
    assert [update.sample_id for update in structure_updates[0].members] == ["CHILD2"]


# --------------------------------------------------------------------------- #
# A structure edit: PUT /families/{family}/structure
# --------------------------------------------------------------------------- #


def _member_row(sample_id: str, *, role: str, sex: str) -> dict[str, Any]:
    return {
        "family_uuid": "fam-uuid",
        "sample_uuid": f"uuid-{sample_id.lower()}",
        "sample_id": sample_id,
        "sex": sex,
        "role": role,
        "clinical_status": "unknown",
        "carrier_status": "unknown",
        "carrier_type": None,
        "carrier_evidence": {},
        "active": True,
    }


def _structure_family(monkeypatch: pytest.MonkeyPatch) -> None:
    """The structure edit reads the trio, unlinked, at version 1."""

    async def mapping(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "fam-uuid", "family_id": FAMILY}

    async def version(*_args: Any, **_kwargs: Any) -> int:
        return 1

    async def member_rows(*_args: Any, **_kwargs: Any) -> dict[str, dict[str, Any]]:
        rows = [
            _member_row(FATHER, role="father", sex="male"),
            _member_row(MOTHER, role="mother", sex="female"),
            _member_row(CHILD, role="proband", sex="female"),
        ]
        return {structure._sample_key(row["sample_id"]): row for row in rows}

    async def relationship_rows(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(structure, "get_accessible_family_mapping", mapping)
    monkeypatch.setattr(structure, "_fetch_current_structure_version", version)
    monkeypatch.setattr(structure, "_fetch_family_member_rows", member_rows)
    monkeypatch.setattr(structure, "_fetch_current_relationship_rows", relationship_rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sample_id", "detail"),
    [
        (f"SIB{ESC}1", "Sample ID 'SIB\\x1b1' contains a control character (\\x1b)."),
        (f"SIB{NUL}1", "Sample ID 'SIB\\x001' contains a control character (\\x00)."),
        (f" SIB{LF}1 ", "Sample ID 'SIB\\n1' contains a control character (\\n)."),
        ("SIB 1", "Sample ID 'SIB 1' contains a space."),
    ],
)
async def test_a_structure_edit_refuses_to_add_a_member_no_sample_can_be_stored_under(
    monkeypatch: pytest.MonkeyPatch, sample_id: str, detail: str
) -> None:
    _structure_family(monkeypatch)
    # The added IDs are looked up (already taken?) and then written: neither may happen.
    monkeypatch.setattr(structure, "_sample_id_conflicts", _nothing_read)
    monkeypatch.setattr(structure, "_insert_new_member", _nothing_read)
    update = FamilyStructureUpdate(
        add_members=[FamilyStructureMemberCreate(sample_id="SIB2"), FamilyStructureMemberCreate(sample_id=sample_id)]
    )

    with pytest.raises(HTTPException) as refused:
        await structure.update_family_structure_for_admin(
            _ExplodingSession(),  # type: ignore[arg-type]
            family_id=FAMILY,
            update=update,
            user=_admin(),
        )

    assert refused.value.status_code == 400
    assert refused.value.detail == _refusal(detail)


@pytest.mark.asyncio
async def test_a_structure_edit_adds_a_member_without_the_whitespace_around_its_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _structure_family(monkeypatch)
    session = _RecordingSession()
    looked_up: list[list[str]] = []
    inserted: list[str] = []

    async def conflicts(_session: Any, *, sample_ids: list[str]) -> list[dict[str, Any]]:
        looked_up.append(sample_ids)
        return []

    async def insert(_session: Any, *, member: dict[str, Any], **_kwargs: Any) -> None:
        inserted.append(member["sample_id"])
        member["sample_uuid"] = "uuid-sib1"

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def family_record(*_args: Any, **_kwargs: Any) -> FamilyOut:
        return _family()

    monkeypatch.setattr(structure, "_sample_id_conflicts", conflicts)
    monkeypatch.setattr(structure, "_insert_new_member", insert)
    monkeypatch.setattr(structure, "_update_member_row", nothing)
    monkeypatch.setattr(structure, "_record_family_structure_version", nothing)
    monkeypatch.setattr(structure, "get_family_record", family_record)

    await structure.update_family_structure_for_admin(
        session,  # type: ignore[arg-type]
        family_id=FAMILY,
        update=FamilyStructureUpdate(add_members=[FamilyStructureMemberCreate(sample_id=" SIB1 ")]),
        user=_admin(),
    )

    assert looked_up == [["SIB1"]]
    assert inserted == ["SIB1"]
    # The stored pedigree names the new member by the same ID.
    pedigree = next(params["pedigree"] for params in session.params if "pedigree" in params)
    assert f"{FAMILY} SIB1 0 0 0 0" in pedigree.splitlines()
