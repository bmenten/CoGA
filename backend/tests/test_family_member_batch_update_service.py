import inspect
from datetime import datetime, timezone

import pytest

from backend.app.schemas import (
    FamilyMemberBatchUpdate,
    FamilyMemberBatchUpdateItem,
    FamilyMemberOut,
    FamilyOut,
    FamilyRelationshipOut,
    FamilyStructureUpdateOut,
)
from backend.app.services import family_member_management_service as service
from backend.app.services.access_control import CurrentUser


def _family_record() -> FamilyOut:
    now = datetime.now(timezone.utc)
    return FamilyOut(
        _id="fam-1",
        family_id="FAM1",
        created_at=now,
        members=[
            FamilyMemberOut(
                sample_id="FATHER",
                role="father",
                affected=False,
                sex="male",
                clinical_status="unaffected",
                carrier_status="unknown",
            ),
            FamilyMemberOut(
                sample_id="MOTHER",
                role="mother",
                affected=False,
                sex="female",
                clinical_status="unaffected",
                carrier_status="unknown",
            ),
            FamilyMemberOut(
                sample_id="CHILD",
                role="proband",
                affected=True,
                sex="female",
                clinical_status="affected",
                carrier_status="unknown",
            ),
        ],
        relationships=[
            FamilyRelationshipOut(
                id="rel-father",
                relationship_type="parent_child",
                sample_id_a="FATHER",
                sample_id_b="CHILD",
                role_a="father",
                role_b="child",
                source="ped_import",
            ),
            FamilyRelationshipOut(
                id="rel-mother",
                relationship_type="parent_child",
                sample_id_a="MOTHER",
                sample_id_b="CHILD",
                role_a="mother",
                role_b="child",
                source="ped_import",
            ),
        ],
        projects=[],
        metadata={},
    )


@pytest.mark.asyncio
async def test_batch_metadata_update_ignores_unchanged_parent_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    family = _family_record()
    user = CurrentUser(
        id="11111111-1111-1111-1111-111111111111",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )

    async def fake_accessible_family_mapping(*args, **kwargs):
        return {"id": "fam-1", "family_id": "FAM1"}

    async def fake_family_record(*args, **kwargs):
        return family

    async def fake_structure_update(*args, **kwargs):
        captured["update"] = kwargs["update"]
        return FamilyStructureUpdateOut(family=family, warnings=[])

    monkeypatch.setattr(service, "get_accessible_family_mapping", fake_accessible_family_mapping)
    monkeypatch.setattr(service, "get_family_record", fake_family_record)
    monkeypatch.setattr(service, "update_family_structure_for_admin", fake_structure_update)

    response = await service.update_family_members_batch_for_admin(
        object(),
        family_id="FAM1",
        update=FamilyMemberBatchUpdate(
            updates=[
                FamilyMemberBatchUpdateItem(
                    sample_id="CHILD",
                    carrier_status="carrier",
                    father_id="FATHER",
                    mother_id="MOTHER",
                )
            ],
        ),
        user=user,
    )

    assert response.family.family_id == "FAM1"
    assert captured["update"].relationships is None
    assert captured["update"].members[0].sample_id == "CHILD"
    assert captured["update"].members[0].carrier_status == "carrier"


def test_family_structure_update_query_reason_parameter_is_typed() -> None:
    import backend.app.services.family_structure_service as family_structure_service

    source = inspect.getsource(family_structure_service.update_family_structure_for_admin)
    assert "'reason', CAST(:reason AS text)" in source
