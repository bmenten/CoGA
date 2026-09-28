"""update_family_structure_for_admin should only re-UPDATE members that actually
changed, not every member (Batch-3 dirty-tracking cleanup)."""
from datetime import datetime, timezone

import pytest

from app.schemas import (
    FamilyOut,
    FamilyStructureMemberUpdate,
    FamilyStructureUpdate,
)
from app.services import family_structure_service as service
from app.services.access_control import CurrentUser


def _user() -> CurrentUser:
    return CurrentUser(
        id="11111111-1111-1111-1111-111111111111",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def _member(sample_id: str, *, role: str, clinical_status: str = "unaffected") -> dict:
    return {
        "family_uuid": "fam-uuid",
        "sample_uuid": f"uuid-{sample_id.lower()}",
        "sample_id": sample_id,
        "sex": "female",
        "role": role,
        "clinical_status": clinical_status,
        "carrier_status": "unknown",
        "carrier_type": None,
        "carrier_evidence": {},
        "active": True,
    }


def _current_members() -> dict:
    return {
        service._sample_key("FATHER"): _member("FATHER", role="father"),
        service._sample_key("MOTHER"): _member("MOTHER", role="mother"),
        service._sample_key("CHILD"): _member("CHILD", role="proband", clinical_status="unknown"),
    }


class _FakeSession:
    async def execute(self, *args, **kwargs):
        return None

    async def commit(self):
        return None


def _patch(monkeypatch) -> list[str]:
    updated: list[str] = []

    async def fake_mapping(*args, **kwargs):
        return {"id": "fam-uuid", "family_id": "FAM1"}

    async def fake_version(*args, **kwargs):
        return 1

    async def fake_members(*args, **kwargs):
        return _current_members()

    async def fake_relationships(*args, **kwargs):
        return []

    async def fake_update_member_row(session, *, member):
        updated.append(str(member["sample_id"]))

    async def fake_noop(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "get_accessible_family_mapping", fake_mapping)
    monkeypatch.setattr(service, "_fetch_current_structure_version", fake_version)
    monkeypatch.setattr(service, "_fetch_family_member_rows", fake_members)
    monkeypatch.setattr(service, "_fetch_current_relationship_rows", fake_relationships)
    monkeypatch.setattr(service, "_update_member_row", fake_update_member_row)
    monkeypatch.setattr(service, "_insert_new_member", fake_noop)
    monkeypatch.setattr(service, "_replace_relationship_rows", fake_noop)
    monkeypatch.setattr(service, "_record_family_structure_version", fake_noop)
    monkeypatch.setattr(service, "_pedigree_text_from_target", lambda **kwargs: "")

    async def fake_family_record(*args, **kwargs):
        return FamilyOut(
            _id="fam-uuid",
            family_id="FAM1",
            created_at=datetime.now(timezone.utc),
            members=[],
            relationships=[],
            projects=[],
            metadata={},
        )

    monkeypatch.setattr(service, "get_family_record", fake_family_record)
    return updated


@pytest.mark.asyncio
async def test_only_changed_member_is_updated(monkeypatch) -> None:
    updated = _patch(monkeypatch)
    update = FamilyStructureUpdate(
        members=[FamilyStructureMemberUpdate(sample_id="CHILD", clinical_status="affected")]
    )

    await service.update_family_structure_for_admin(
        _FakeSession(), family_id="FAM1", update=update, user=_user()
    )

    # Only CHILD changed -> only CHILD is re-written (not FATHER/MOTHER).
    assert updated == ["CHILD"]


@pytest.mark.asyncio
async def test_no_op_update_writes_no_member_rows(monkeypatch) -> None:
    updated = _patch(monkeypatch)
    # clinical_status is already "unknown" for CHILD -> no actual change.
    update = FamilyStructureUpdate(
        members=[FamilyStructureMemberUpdate(sample_id="CHILD", clinical_status="unknown")]
    )

    await service.update_family_structure_for_admin(
        _FakeSession(), family_id="FAM1", update=update, user=_user()
    )

    assert updated == []
