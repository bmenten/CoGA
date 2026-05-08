import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.app.services.metadata_service import CurrentUser, _visible_metadata_project_ids
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

    assert _visible_metadata_project_ids(
        [hidden_project_id, visible_project_id],
        _user("viewer", [visible_project_id]),
    ) == [visible_project_id]


def test_viewer_cannot_replace_family_linked_to_hidden_project() -> None:
    visible_project_id = str(uuid4())
    hidden_project_id = str(uuid4())

    with pytest.raises(HTTPException) as exc_info:
        _ensure_user_can_replace_existing_families(
            [
                {"family_id": "F1", "project_id": visible_project_id},
                {"family_id": "F1", "project_id": hidden_project_id},
            ],
            _user("viewer", [visible_project_id]),
        )

    assert exc_info.value.status_code == 403
