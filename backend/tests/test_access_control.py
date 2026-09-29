import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.app.services.access_control import (
    CurrentUser,
    ensure_user_can_access_metadata_projects,
    visible_metadata_project_ids,
)
from backend.app.services.ped_service import (
    _ensure_user_can_replace_existing_families,
    _resolve_accessible_project_id,
)


class _ScalarResult:
    def __init__(self, value: str | None):
        self.value = value

    def scalar_one_or_none(self) -> str | None:
        return self.value


class _ProjectLookupSession:
    def __init__(self, existing_project_id: str | None):
        self.existing_project_id = existing_project_id

    async def execute(self, statement, params):
        return _ScalarResult(self.existing_project_id)


def _user(role: str, project_ids: list[str]) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        username="viewer@example.com",
        email="viewer@example.com",
        role=role,
        projects=project_ids,
        metadata_project_ids=project_ids,
        created_at=datetime.now(timezone.utc),
    )


def test_resolve_accessible_project_id_requires_viewer_assignment() -> None:
    allowed_project_id = str(uuid4())
    hidden_project_id = str(uuid4())
    user = _user("viewer", [allowed_project_id])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            _resolve_accessible_project_id(
                _ProjectLookupSession(hidden_project_id),
                user,
                hidden_project_id,
            )
        )

    assert exc_info.value.status_code == 403


def test_resolve_accessible_project_id_accepts_assigned_project() -> None:
    project_id = str(uuid4())

    resolved_project_id = asyncio.run(
        _resolve_accessible_project_id(
            _ProjectLookupSession(project_id),
            _user("viewer", [project_id]),
            project_id,
        )
    )

    assert resolved_project_id == project_id


def test_viewer_family_project_ids_are_filtered_to_visible_projects() -> None:
    visible_project_id = str(uuid4())
    hidden_project_id = str(uuid4())

    assert visible_metadata_project_ids(
        [hidden_project_id, visible_project_id],
        _user("viewer", [visible_project_id]),
    ) == [visible_project_id]


def test_non_admin_cannot_replace_existing_families() -> None:
    # Replacing existing families/samples is admin-only. This is a role-based
    # policy, not a per-project check — the function does not inspect rows.
    with pytest.raises(HTTPException) as exc_info:
        _ensure_user_can_replace_existing_families(_user("viewer", []))

    assert exc_info.value.status_code == 403


def test_admin_can_replace_existing_families() -> None:
    # An admin is permitted (no exception raised).
    _ensure_user_can_replace_existing_families(_user("admin", []))


# --- PHI access gate: ensure_user_can_access_metadata_projects -------------
# This is the central checkpoint every family/sample endpoint flows through
# (build_family_metadata_context -> get_accessible_family_mapping -> here). The
# cases below cover the cross-user / multi-project / admin scenarios so a
# regression that widens access is caught.


def test_viewer_cannot_access_family_in_unassigned_project() -> None:
    # The core IDOR guard: a viewer passing a family_id whose project they are
    # not a member of must be rejected, regardless of the id being valid.
    owner_project = str(uuid4())
    other_project = str(uuid4())
    intruder = _user("viewer", [other_project])

    with pytest.raises(HTTPException) as exc_info:
        ensure_user_can_access_metadata_projects([owner_project], intruder)

    assert exc_info.value.status_code == 403


def test_viewer_can_access_family_sharing_one_assigned_project() -> None:
    # A family linked to several projects is visible if the viewer is a member
    # of at least one of them (no exception).
    shared = str(uuid4())
    foreign = str(uuid4())
    viewer = _user("viewer", [shared])

    ensure_user_can_access_metadata_projects([foreign, shared], viewer)


def test_viewer_with_no_projects_is_denied() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ensure_user_can_access_metadata_projects([str(uuid4())], _user("viewer", []))

    assert exc_info.value.status_code == 403


def test_admin_bypasses_project_scoping() -> None:
    # Admins are not project-scoped: access is granted even with no project
    # assignments and a family in an arbitrary project.
    ensure_user_can_access_metadata_projects([str(uuid4())], _user("admin", []))


def test_admin_sees_all_project_ids_while_viewer_sees_only_assigned() -> None:
    p_assigned, p_foreign = str(uuid4()), str(uuid4())
    family_projects = [p_assigned, p_foreign]

    assert visible_metadata_project_ids(family_projects, _user("admin", [])) == family_projects
    assert visible_metadata_project_ids(family_projects, _user("viewer", [p_assigned])) == [p_assigned]
    assert visible_metadata_project_ids(family_projects, _user("viewer", [])) == []
