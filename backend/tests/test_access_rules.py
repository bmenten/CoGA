"""The access rules in services/access_control.py: the global roles and the
project-scoped access levels.

The endpoint-level checks are in test_access_control.py and, for the access levels (SEC-1),
test_project_access_levels.py; these pin the rules themselves.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fastapi import HTTPException

import re
import typing
from pathlib import Path

from backend.app.schemas.auth import UserRole
from backend.app.services.access_control import (
    AUDIT_TRAIL_DETAIL,
    READ_ONLY_DETAIL,
    SIGN_OUT_ROLE_DETAIL,
    USER_ROLES,
    VIEWER_AUDIT_TRAIL_DETAIL,
    VIEWER_READ_ONLY_DETAIL,
    CurrentUser,
    changes_only_own_data,
    has_role,
    has_superuser_rights,
    is_admin_user,
    may_sign_out,
    request_changes_records,
    require_audit_trail_access,
    require_change_access,
    require_sign_out_authority,
    role_changes_records,
    user_analyst_project_ids,
    user_can_access_metadata_projects,
    user_can_change_metadata_projects,
    user_can_sign_out_metadata_projects,
    user_metadata_project_ids,
    visible_metadata_project_ids,
)

BASELINE = Path(__file__).resolve().parents[1] / "db" / "schema" / "postgres" / "01_access.sql"


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


# --- the global roles ----------------------------------------------------------------------


def test_the_roles_are_the_schemas_and_the_databases_in_the_same_order() -> None:
    assert USER_ROLES == ("viewer", "analyst", "supervisor", "superuser", "admin")
    assert typing.get_args(UserRole) == USER_ROLES
    check = re.search(r"role TEXT NOT NULL CHECK \(role IN \(([^)]*)\)\)", BASELINE.read_text())
    assert check is not None
    assert tuple(value.strip(" '") for value in check.group(1).split(",")) == USER_ROLES


@pytest.mark.parametrize(
    ("role", "changes", "signs_out", "superuser", "admin"),
    [
        ("viewer", False, False, False, False),
        ("analyst", True, False, False, False),
        ("supervisor", True, True, False, False),
        ("superuser", True, True, True, False),
        ("admin", True, True, True, True),
        # A role CoGA does not know has no right at all.
        ("user", False, False, False, False),
    ],
)
def test_each_role_has_every_right_of_the_roles_below_it(
    role: str, changes: bool, signs_out: bool, superuser: bool, admin: bool
) -> None:
    user = _user(role)
    assert role_changes_records(user) is changes
    assert may_sign_out(user) is signs_out
    assert has_superuser_rights(user) is superuser
    assert is_admin_user(user) is admin
    assert [has_role(user, lower) for lower in USER_ROLES] == [
        role in USER_ROLES and USER_ROLES.index(lower) <= USER_ROLES.index(role) for lower in USER_ROLES
    ]


@pytest.mark.parametrize("role", ["superuser", "admin"])
def test_a_superuser_and_an_admin_see_every_project(role: str) -> None:
    assert visible_metadata_project_ids(["p1", "p2"], _user(role)) == ["p1", "p2"]
    assert user_can_access_metadata_projects(["p9"], _user(role)) is True


@pytest.mark.parametrize("role", ["viewer", "analyst", "supervisor"])
def test_the_roles_below_superuser_see_only_their_own_projects(role: str) -> None:
    assert visible_metadata_project_ids(["p1", "p2"], _user(role, ["p2"])) == ["p2"]
    assert user_can_access_metadata_projects(["p1"], _user(role, ["p2"])) is False


def test_an_admin_sees_every_project() -> None:
    assert visible_metadata_project_ids(["p1", "p2", "p1", None], _user("admin")) == ["p1", "p2"]


def test_a_member_sees_only_their_own_projects() -> None:
    member = _user("viewer", ["p2", "p3"])
    assert visible_metadata_project_ids(["p1", "p2", "p3"], member) == ["p2", "p3"]
    assert user_metadata_project_ids(member) == ["p2", "p3"]
    # A viewer in no project sees none of a record's projects.
    assert visible_metadata_project_ids(["p1", "p2"], _user("viewer")) == []


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
    member = _user("analyst", ["p1", "p2", "p3"], readers=["p2"])
    assert user_analyst_project_ids(member) == ["p1", "p3"]
    # A viewer is an analyst in none of them, whatever their level there.
    assert user_analyst_project_ids(_user("viewer", ["p1", "p2", "p3"], readers=["p2"])) == []
    # A reader may still see every one of their projects.
    assert visible_metadata_project_ids(["p1", "p2", "p9"], member) == ["p1", "p2"]


def test_an_analyst_may_change_a_record_and_a_reader_may_not() -> None:
    member = _user("analyst", ["p1", "p2"], readers=["p2"])
    assert user_can_change_metadata_projects(["p1"], member) is True
    assert user_can_change_metadata_projects(["p2"], member) is False
    # Seeing is not changing: the reader may see the record of p2.
    assert user_can_access_metadata_projects(["p2"], member) is True


def test_a_record_of_two_projects_may_be_changed_by_an_analyst_of_either() -> None:
    assert user_can_change_metadata_projects(["p2", "p1"], _user("analyst", ["p1", "p2"], readers=["p2"])) is True
    assert user_can_change_metadata_projects(["p2", "p3"], _user("analyst", ["p2", "p3"], readers=["p2", "p3"])) is False


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
    reader = _user("analyst", ["p1"], readers=["p1"], changes=True)
    with pytest.raises(HTTPException) as refused:
        require_change_access(["p1"], reader)
    assert (refused.value.status_code, refused.value.detail) == (403, READ_ONLY_DETAIL)


def test_the_change_check_lets_a_read_an_analyst_and_an_admin_pass() -> None:
    # A reader who only reads.
    require_change_access(["p1"], _user("analyst", ["p1"], readers=["p1"], changes=False))
    # An analyst and an admin who change.
    require_change_access(["p1"], _user("analyst", ["p1"], changes=True))
    require_change_access(["p1"], _user("admin", [], changes=True))


def test_the_audit_trail_is_refused_a_reader_whatever_the_request() -> None:
    with pytest.raises(HTTPException) as refused:
        require_audit_trail_access(["p1"], _user("analyst", ["p1"], readers=["p1"]))
    assert (refused.value.status_code, refused.value.detail) == (403, AUDIT_TRAIL_DETAIL)
    require_audit_trail_access(["p1"], _user("analyst", ["p1"]))
    require_audit_trail_access(["p1"], _user("superuser"))


# --- a viewer reads only, whatever their level ------------------------------------------------


def test_a_viewer_changes_nothing_even_where_they_are_an_analyst() -> None:
    viewer = _user("viewer", ["p1"], changes=True)
    assert user_can_change_metadata_projects(["p1"], viewer) is False
    # Reading is not changing: the viewer still sees the record of their project.
    assert user_can_access_metadata_projects(["p1"], viewer) is True
    with pytest.raises(HTTPException) as refused:
        require_change_access(["p1"], viewer)
    # Told why: their role, not a level in the project.
    assert (refused.value.status_code, refused.value.detail) == (403, VIEWER_READ_ONLY_DETAIL)
    # A request that only reads passes.
    require_change_access(["p1"], _user("viewer", ["p1"], changes=False))


def test_the_audit_trail_is_refused_a_viewer_and_says_why() -> None:
    with pytest.raises(HTTPException) as refused:
        require_audit_trail_access(["p1"], _user("viewer", ["p1"]))
    assert (refused.value.status_code, refused.value.detail) == (403, VIEWER_AUDIT_TRAIL_DETAIL)


# --- signing out is a supervisor's (TF-06 H15) ------------------------------------------------


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_signing_out_is_refused_below_supervisor(role: str) -> None:
    user = _user(role, ["p1"])
    assert user_can_sign_out_metadata_projects(["p1"], user) is False
    with pytest.raises(HTTPException) as refused:
        require_sign_out_authority(user)
    assert (refused.value.status_code, refused.value.detail) == (403, SIGN_OUT_ROLE_DETAIL)


@pytest.mark.parametrize("role", ["supervisor", "superuser", "admin"])
def test_a_supervisor_and_above_may_sign_out(role: str) -> None:
    require_sign_out_authority(_user(role))
    assert user_can_sign_out_metadata_projects(["p1"], _user(role, ["p1"])) is True


def test_a_supervisor_signs_out_only_where_they_may_change_the_family() -> None:
    supervisor = _user("supervisor", ["p1", "p2"], readers=["p2"])
    assert user_can_sign_out_metadata_projects(["p1"], supervisor) is True
    # A reader in the family's project signs nothing out, whatever their role.
    assert user_can_sign_out_metadata_projects(["p2"], supervisor) is False
    # Nor outside their projects.
    assert user_can_sign_out_metadata_projects(["p9"], supervisor) is False
    # A superuser signs out in every project.
    assert user_can_sign_out_metadata_projects(["p9"], _user("superuser")) is True
