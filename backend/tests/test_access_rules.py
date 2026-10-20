"""The project-scoped access rules in services/access_control.py (#528).

The endpoint-level checks are in test_access_control.py and, for the access levels (SEC-1),
test_project_access_levels.py; these pin the rules themselves.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fastapi import HTTPException

from backend.app.services.access_control import (
    AUDIT_TRAIL_DETAIL,
    READ_ONLY_DETAIL,
    CurrentUser,
    changes_only_own_data,
    is_admin_user,
    request_changes_records,
    require_audit_trail_access,
    require_change_access,
    user_analyst_project_ids,
    user_can_access_metadata_projects,
    user_can_change_metadata_projects,
    user_metadata_project_ids,
    visible_metadata_project_ids,
)


def _user(
    role: str,
    projects: list[str] | None = None,
    readers: list[str] | None = None,
    *,
    changes: bool = False,
) -> CurrentUser:
    user = CurrentUser(
        id="u1",
        username="user",
        email="user@example.org",
        role=role,
        metadata_project_ids=projects or [],
        reader_project_ids=readers or [],
        created_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    user.changes_records = changes
    return user


@pytest.mark.parametrize(("role", "admin"), [("admin", True), ("superuser", True), ("viewer", False), ("user", False)])
def test_only_admin_roles_are_admins(role: str, admin: bool) -> None:
    assert is_admin_user(_user(role)) is admin


def test_an_admin_sees_every_project() -> None:
    assert visible_metadata_project_ids(["p1", "p2", "p1", None], _user("admin")) == ["p1", "p2"]


def test_a_member_sees_only_their_own_projects() -> None:
    member = _user("viewer", ["p2", "p3"])
    assert visible_metadata_project_ids(["p1", "p2", "p3"], member) == ["p2", "p3"]
    assert user_metadata_project_ids(member) == ["p2", "p3"]


def test_a_non_member_may_not_see_the_record() -> None:
    assert user_can_access_metadata_projects(["p1"], _user("viewer", ["p2"])) is False
    # A record linked to no project is an admin's alone.
    assert user_can_access_metadata_projects([], _user("viewer", ["p2"])) is False
    assert user_can_access_metadata_projects(None, _user("viewer")) is False


def test_a_member_and_an_admin_may_see_it() -> None:
    assert user_can_access_metadata_projects(["p1", None, "p2"], _user("viewer", ["p2"])) is True
    assert user_can_access_metadata_projects(["p9"], _user("admin")) is True
    assert user_can_access_metadata_projects([], _user("superuser")) is True


# --- access levels (SEC-1) -----------------------------------------------------------------


def test_a_member_is_an_analyst_where_they_are_no_reader() -> None:
    member = _user("viewer", ["p1", "p2", "p3"], readers=["p2"])
    assert user_analyst_project_ids(member) == ["p1", "p3"]
    # A reader may still see every one of their projects.
    assert visible_metadata_project_ids(["p1", "p2", "p9"], member) == ["p1", "p2"]


def test_an_analyst_may_change_a_record_and_a_reader_may_not() -> None:
    member = _user("viewer", ["p1", "p2"], readers=["p2"])
    assert user_can_change_metadata_projects(["p1"], member) is True
    assert user_can_change_metadata_projects(["p2"], member) is False
    # Seeing is not changing: the reader may see the record of p2.
    assert user_can_access_metadata_projects(["p2"], member) is True


def test_a_record_of_two_projects_may_be_changed_by_an_analyst_of_either() -> None:
    assert user_can_change_metadata_projects(["p2", "p1"], _user("viewer", ["p1", "p2"], readers=["p2"])) is True
    assert user_can_change_metadata_projects(["p2", "p3"], _user("viewer", ["p2", "p3"], readers=["p2", "p3"])) is False


def test_a_non_member_may_change_nothing_and_a_record_of_no_project_is_an_admins() -> None:
    assert user_can_change_metadata_projects(["p1"], _user("viewer", ["p2"])) is False
    assert user_can_change_metadata_projects([], _user("viewer", ["p2"])) is False
    assert user_can_change_metadata_projects(None, _user("viewer", ["p2"])) is False


@pytest.mark.parametrize("role", ["admin", "superuser"])
def test_an_admin_may_change_every_record_whatever_their_memberships(role: str) -> None:
    admin = _user(role, ["p1"], readers=["p1"])
    assert user_can_change_metadata_projects(["p1"], admin) is True
    assert user_can_change_metadata_projects(["p9"], admin) is True
    assert user_can_change_metadata_projects([], admin) is True


@pytest.mark.parametrize(
    ("method", "changes"),
    [("GET", False), ("HEAD", False), ("OPTIONS", False), ("get", False),
     ("POST", True), ("PUT", True), ("PATCH", True), ("DELETE", True)],
)
def test_a_request_changes_records_unless_it_only_reads(method: str, changes: bool) -> None:
    def route() -> None:
        return None

    assert request_changes_records(method, route) is changes
    assert request_changes_records(method, None) is changes


def test_a_route_marked_as_changing_only_its_callers_own_data_changes_no_record() -> None:
    @changes_only_own_data
    def save_my_preset() -> str:
        return "saved"

    # The mark leaves the route as it was.
    assert save_my_preset() == "saved"
    for method in ("POST", "PUT", "DELETE"):
        assert request_changes_records(method, save_my_preset) is False


def test_the_change_check_refuses_a_reader_with_403_and_says_why() -> None:
    reader = _user("viewer", ["p1"], readers=["p1"], changes=True)
    with pytest.raises(HTTPException) as refused:
        require_change_access(["p1"], reader)
    assert (refused.value.status_code, refused.value.detail) == (403, READ_ONLY_DETAIL)


def test_the_change_check_lets_a_read_an_analyst_and_an_admin_pass() -> None:
    # A reader who only reads.
    require_change_access(["p1"], _user("viewer", ["p1"], readers=["p1"], changes=False))
    # An analyst and an admin who change.
    require_change_access(["p1"], _user("viewer", ["p1"], changes=True))
    require_change_access(["p1"], _user("admin", [], changes=True))


def test_the_audit_trail_is_refused_a_reader_whatever_the_request() -> None:
    with pytest.raises(HTTPException) as refused:
        require_audit_trail_access(["p1"], _user("viewer", ["p1"], readers=["p1"]))
    assert (refused.value.status_code, refused.value.detail) == (403, AUDIT_TRAIL_DETAIL)
    require_audit_trail_access(["p1"], _user("viewer", ["p1"]))
    require_audit_trail_access(["p1"], _user("superuser"))
