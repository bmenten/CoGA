"""Every pedigree edit refreshes the genome-overview lineage and warms the ranking again.

The genome overview serves a precomputed lineage, hash-guarded: after a pedigree edit it
shows the relatives grey until the lineage is computed again, and nothing computes it on a
page load. So each edit schedules that precompute, and a warm of the prioritised ranking,
whose cache key the edit changed. The member edits (single, batch, removal) and a PED
upload did; `PUT /families/{id}/structure`, the structure editor's save, did not, so its
relatives stayed grey until some other edit came along. It now schedules both. A request
that fails schedules nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import families as families_router
from backend.app.schemas import FamilyMemberBatchUpdateOut, FamilyOut, FamilyStructureUpdateOut
from backend.app.services.access_control import CurrentUser


class _FakeSession:
    async def rollback(self) -> None:
        return None


def _family() -> FamilyOut:
    return FamilyOut(
        _id="11111111-1111-1111-1111-111111111111",
        family_id="FAM1",
        created_at=datetime.now(timezone.utc),
        members=[],
        relationships=[],
        projects=[],
        metadata={},
    )


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch):
    original = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    admin = CurrentUser(
        id="admin-1",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )

    async def override_session():
        yield _FakeSession()

    async def override_admin():
        return admin

    scheduled: list[tuple[str, str, str]] = []

    async def fake_lineage(family_identifier, user):
        scheduled.append(("lineage", family_identifier, user.id))

    async def fake_ranking(family_identifier, user):
        scheduled.append(("ranking", family_identifier, user.id))

    app.dependency_overrides[get_postgres_session] = override_session
    app.dependency_overrides[families_router.get_current_admin_user] = override_admin
    monkeypatch.setattr(families_router, "precompute_family_lineage_safe", fake_lineage)
    monkeypatch.setattr(families_router, "precompute_family_ranking_safe", fake_ranking)
    with TestClient(app) as client:
        yield client, monkeypatch, scheduled
    app.dependency_overrides = original


_STRUCTURE_BODY = {
    "expected_structure_version": 3,
    "change_reason": "family_detail_page",
    "clear_existing_genomic_data": False,
    "remove_members": ["FATHER"],
}


def test_a_structure_save_refreshes_the_lineage_and_warms_the_ranking(admin_client) -> None:
    client, monkeypatch, scheduled = admin_client

    async def fake_update(session, *, family_id, update, user):
        return FamilyStructureUpdateOut(family=_family())

    monkeypatch.setattr(families_router, "update_family_structure_for_admin", fake_update)

    response = client.put("/api/families/FAM1/structure", json=_STRUCTURE_BODY)

    assert response.status_code == 200
    assert scheduled == [("lineage", "FAM1", "admin-1"), ("ranking", "FAM1", "admin-1")]


def test_a_structure_save_that_fails_schedules_nothing(admin_client) -> None:
    client, monkeypatch, scheduled = admin_client

    async def refused(session, *, family_id, update, user):
        raise HTTPException(status_code=409, detail="Family structure changed since it was loaded")

    monkeypatch.setattr(families_router, "update_family_structure_for_admin", refused)

    response = client.put("/api/families/FAM1/structure", json=_STRUCTURE_BODY)

    assert response.status_code == 409
    assert scheduled == []


def test_a_member_batch_edit_schedules_the_same_two_tasks(admin_client) -> None:
    client, monkeypatch, scheduled = admin_client

    async def fake_batch(session, *, family_id, update, user):
        return FamilyMemberBatchUpdateOut(family=_family())

    monkeypatch.setattr(families_router, "update_family_members_batch_for_admin", fake_batch)

    response = client.put("/api/families/FAM1/members/batch", json={"updates": []})

    assert response.status_code == 200
    assert scheduled == [("lineage", "FAM1", "admin-1"), ("ranking", "FAM1", "admin-1")]
