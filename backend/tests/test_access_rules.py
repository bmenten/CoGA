"""The project-scoped access rules in services/access_control.py (#528).

The endpoint-level checks are in test_access_control.py; these pin the rules themselves.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend.app.services.access_control import (
    CurrentUser,
    ensure_user_can_access_metadata_projects,
    is_admin_user,
    user_metadata_project_ids,
    visible_metadata_project_ids,
)


def _user(role: str, projects: list[str] | None = None) -> CurrentUser:
    return CurrentUser(
        id="u1",
        username="user",
        email="user@example.org",
        role=role,
        metadata_project_ids=projects or [],
        created_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(("role", "admin"), [("admin", True), ("superuser", True), ("viewer", False), ("user", False)])
def test_only_admin_roles_are_admins(role: str, admin: bool) -> None:
    assert is_admin_user(_user(role)) is admin


def test_an_admin_sees_every_project() -> None:
    assert visible_metadata_project_ids(["p1", "p2", "p1", None], _user("admin")) == ["p1", "p2"]


def test_a_member_sees_only_their_own_projects() -> None:
    member = _user("viewer", ["p2", "p3"])
    assert visible_metadata_project_ids(["p1", "p2", "p3"], member) == ["p2", "p3"]
    assert user_metadata_project_ids(member) == ["p2", "p3"]


def test_a_non_member_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        ensure_user_can_access_metadata_projects(["p1"], _user("viewer", ["p2"]))
    assert refused.value.status_code == 403


def test_a_member_and_an_admin_are_let_through() -> None:
    ensure_user_can_access_metadata_projects(["p1", "p2"], _user("viewer", ["p2"]))
    ensure_user_can_access_metadata_projects(["p9"], _user("admin"))
