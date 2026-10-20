"""A case is assigned to, and recorded as reviewed by, someone who may work on it (SEC-1).

A family's "Assigned to" and "Reviewed by" name an active analyst of one of the family's
projects or an active administrator. The pickers read the family's own list
(``GET /families/{family_id}/assignable-users``, ``list_family_assignable_users``), answered like
the family itself: 404 for a family outside the caller's projects. A save that names a reader,
a user outside the family's projects or an inactive account is refused (422, saying why) and
changes nothing; a value the family already holds is kept as it is, so a save that leaves an
assignee who has since become a reader is not refused.

The database is replaced by a session that answers the service's statements from an in-memory
table of users and memberships; the family checkpoint is the real one, over stubbed family
rows. The list's SQL on real Postgres is in ``e2e/test_e2e_case_assignee_eligibility.py``.
All IDs are synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

from backend.app.schemas import FamilyMetadataUpdate
from backend.app.services import family_status_service, metadata_service
from backend.app.services.access_control import CurrentUser, RecordNotVisible

PROJECT_A = "00000000-0000-4000-8000-00000000000a"
PROJECT_B = "00000000-0000-4000-8000-00000000000b"
PROJECT_C = "00000000-0000-4000-8000-00000000000c"
FAMILIES = {"FAM_A": [PROJECT_A], "FAM_B": [PROJECT_B], "FAM_AB": [PROJECT_A, PROJECT_B]}


def _uid(number: int) -> str:
    return f"00000000-0000-4000-8000-0000000001{number:02d}"


# The accounts: their name, role, whether active, and their level in each project.
ANA, RITA, MIXED, OUTSIDER, ADMIN, GONE = (_uid(n) for n in range(1, 7))
ACCOUNTS: dict[str, dict[str, Any]] = {
    ANA: {"first_name": "Ana", "last_name": "Lyst", "role": "viewer", "active": True, "levels": {PROJECT_A: "analyst"}},
    RITA: {"first_name": "Rita", "last_name": "Reader", "role": "viewer", "active": True, "levels": {PROJECT_B: "reader"}},
    # A reader in A and an analyst in B.
    MIXED: {"first_name": "", "last_name": "", "role": "viewer", "active": True,
            "levels": {PROJECT_A: "reader", PROJECT_B: "analyst"}},
    OUTSIDER: {"first_name": "Otto", "last_name": "Side", "role": "viewer", "active": True, "levels": {PROJECT_C: "analyst"}},
    ADMIN: {"first_name": "Ada", "last_name": "Admin", "role": "admin", "active": True, "levels": {}},
    GONE: {"first_name": "Gil", "last_name": "Gone", "role": "viewer", "active": False, "levels": {PROJECT_A: "analyst"}},
}


def _username(user_id: str) -> str:
    return f"user-{user_id[-2:]}"


def _user(user_id: str) -> CurrentUser:
    account = ACCOUNTS[user_id]
    projects = list(account["levels"])
    return CurrentUser(
        id=user_id,
        username=_username(user_id),
        email=f"{_username(user_id)}@example.org",
        role=account["role"],
        projects=projects,
        metadata_project_ids=projects,
        reader_project_ids=[project for project, level in account["levels"].items() if level == "reader"],
        created_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
    )


class _Rows:
    def __init__(self, rows: list[dict[str, Any]] | None = None, value: Any = None) -> None:
        self.rows = rows or []
        self.value = value

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self) -> Any:
        return self.value


def _ref(user_id: str) -> dict[str, Any]:
    account = ACCOUNTS[user_id]
    return {
        "id": user_id,
        "username": _username(user_id),
        "email": f"{_username(user_id)}@example.org",
        "first_name": account["first_name"],
        "last_name": account["last_name"],
    }


class _Database:
    """Answers the service's statements from ``ACCOUNTS``; records each list query's
    parameters and each family update."""

    def __init__(self) -> None:
        self.list_queries: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        projects = {str(project_id) for project_id in params.get("project_ids", [])}
        if "AS access_levels" in sql:
            account = ACCOUNTS.get(params["user_id"])
            if account is None:
                return _Rows()
            return _Rows(
                [
                    {
                        **_ref(params["user_id"]),
                        "is_active": account["active"],
                        "role": account["role"],
                        "access_levels": sorted(
                            {level for project, level in account["levels"].items() if project in projects}
                        ),
                    }
                ]
            )
        if "u.role IN" in sql:
            self.list_queries.append(params)
            # What the query's WHERE says, for the test's accounts.
            return _Rows(
                [
                    _ref(user_id)
                    for user_id, account in ACCOUNTS.items()
                    if account["active"]
                    and (
                        account["role"] in params["admin_roles"]
                        or any(
                            level == params["analyst"] and project in projects
                            for project, level in account["levels"].items()
                        )
                    )
                ]
            )
        if sql.startswith("SELECT id::text AS id FROM family_statuses"):
            return _Rows(value="status-1")
        if sql.startswith("UPDATE families SET"):
            self.updates.append(params)
            return _Rows()
        raise AssertionError(f"unexpected statement: {sql[:80]}")

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture()
def database(monkeypatch: pytest.MonkeyPatch) -> _Database:
    stored = {"assigned_to_id": None, "reviewed_by_id": None}

    async def family_rows(_session: Any, *, family_identifiers: list[str] | None = None, **_kwargs: Any) -> list[dict]:
        return [
            {"id": f"uuid-{family_id}", "family_id": family_id, "project_ids": FAMILIES[family_id], **stored}
            for family_id in family_identifiers or []
            if family_id in FAMILIES
        ]

    async def family_record(_session: Any, family_id: str, _user: Any) -> str:
        return f"record of {family_id}"

    database = _Database()
    database.stored = stored  # type: ignore[attr-defined]
    monkeypatch.setattr(metadata_service, "_fetch_family_rows", family_rows)
    monkeypatch.setattr(family_status_service, "get_family_record", family_record)
    return database


def _list(database: _Database, family_id: str, user: CurrentUser) -> list[str]:
    users = asyncio.run(family_status_service.list_family_assignable_users(database, family_id=family_id, user=user))  # type: ignore[arg-type]
    return [user.id for user in users]


def _save(database: _Database, user: CurrentUser, family_id: str = "FAM_AB", **fields: Any) -> Any:
    changing = user.model_copy(update={"changes_records": True})
    return asyncio.run(
        family_status_service.update_family_metadata_for_user(
            database,  # type: ignore[arg-type]
            family_id=family_id,
            update=FamilyMetadataUpdate(**fields),
            user=changing,
        )
    )


def _refusal(database: _Database, user: CurrentUser, **fields: Any) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        _save(database, user, **fields)
    return refused.value


# --------------------------------------------------------------------------- the pickers' list


def test_the_list_is_the_familys_analysts_and_the_administrators(database: _Database) -> None:
    listed = _list(database, "FAM_AB", _user(ANA))

    # An analyst of A, an analyst of B (a reader in A) and the admin; not Rita, a reader of B,
    # not the analyst of another project, nor the inactive analyst of A.
    assert listed == [ANA, MIXED, ADMIN]
    [query] = database.list_queries
    assert query["admin_roles"] == ["admin", "superuser"]
    assert query["analyst"] == "analyst"
    assert query["project_ids"] == [UUID(PROJECT_A), UUID(PROJECT_B)]


def test_each_family_lists_the_analysts_of_its_own_projects(database: _Database) -> None:
    assert _list(database, "FAM_A", _user(ANA)) == [ANA, ADMIN]
    assert _list(database, "FAM_B", _user(RITA)) == [MIXED, ADMIN]


def test_a_reader_reads_the_list(database: _Database) -> None:
    # Reading the family is theirs; the pickers that would use the list are disabled for them.
    assert _list(database, "FAM_B", _user(RITA)) == [MIXED, ADMIN]


def test_a_family_outside_the_users_projects_answers_like_an_unknown_one(database: _Database) -> None:
    with pytest.raises(RecordNotVisible) as hidden:
        _list(database, "FAM_B", _user(ANA))
    with pytest.raises(HTTPException) as unknown:
        _list(database, "NO_FAMILY", _user(ANA))

    assert (hidden.value.status_code, hidden.value.detail) == (unknown.value.status_code, unknown.value.detail)
    assert hidden.value.status_code == 404
    assert database.list_queries == []


# --------------------------------------------------------------------------- the save


def test_an_analyst_or_an_admin_may_be_assigned_and_record_the_review(database: _Database) -> None:
    assert _save(database, _user(ANA), assigned_to=MIXED, reviewed_by=ADMIN) == "record of FAM_AB"

    assert database.updates == [
        {"family_uuid": "uuid-FAM_AB", "assigned_to_id": MIXED, "reviewed_by_id": ADMIN},
    ]
    assert database.commits == 1


@pytest.mark.parametrize(
    ("field", "named", "detail"),
    [
        (
            "assigned_to",
            RITA,
            "Rita Reader cannot be assigned this case: they are a reader in this family's projects. "
            "Choose an analyst of one of the family's projects or an administrator.",
        ),
        (
            "reviewed_by",
            RITA,
            "Rita Reader cannot be recorded as this case's reviewer: they are a reader in this "
            "family's projects. Choose an analyst of one of the family's projects or an administrator.",
        ),
        (
            "assigned_to",
            OUTSIDER,
            "Otto Side cannot be assigned this case: they are not a member of this family's projects. "
            "Choose an analyst of one of the family's projects or an administrator.",
        ),
        (
            "reviewed_by",
            GONE,
            "Gil Gone cannot be recorded as this case's reviewer: their account is not active. "
            "Choose an analyst of one of the family's projects or an administrator.",
        ),
    ],
)
def test_a_save_naming_someone_who_may_not_work_on_the_case_is_refused(
    database: _Database, field: str, named: str, detail: str
) -> None:
    refused = _refusal(database, _user(ANA), **{field: named})

    assert (refused.status_code, refused.detail) == (422, detail)
    assert database.updates == [] and database.commits == 0


def test_a_member_who_reads_one_of_the_familys_projects_and_analyses_another_may_be_named(
    database: _Database,
) -> None:
    # A reader in A, an analyst in B: on a family of A alone they are a reader.
    refused = _refusal(database, _user(ANA), assigned_to=MIXED, family_id="FAM_A")
    assert refused.status_code == 422 and "a reader in this family's projects" in refused.detail
    _save(database, _user(ANA), assigned_to=MIXED, family_id="FAM_AB")
    assert database.updates[-1]["assigned_to_id"] == MIXED


def test_a_refused_field_leaves_the_others_of_the_save_unwritten(database: _Database) -> None:
    refused = _refusal(database, _user(ANA), status_key="solved", assigned_to=ANA, reviewed_by=RITA)

    assert refused.status_code == 422
    assert database.updates == [] and database.commits == 0


def test_an_unknown_user_is_not_found(database: _Database) -> None:
    refused = _refusal(database, _user(ANA), assigned_to=_uid(99))
    assert (refused.status_code, refused.detail) == (404, "User not found")


def test_a_stored_assignee_who_became_a_reader_stays_until_it_is_changed(database: _Database) -> None:
    # Rita was assigned, and reviewed, before she became a reader.
    database.stored.update(assigned_to_id=RITA, reviewed_by_id=RITA)  # type: ignore[attr-defined]

    # A save that leaves her as she is (the status changed) is not refused, and keeps her.
    _save(database, _user(ANA), status_key="solved", assigned_to=RITA)
    assert database.updates == [
        {"family_uuid": "uuid-FAM_AB", "status_id": "status-1", "assigned_to_id": RITA},
    ]
    # However the save spells her ID, and whichever field it is.
    _save(database, _user(ANA), assigned_to=RITA.upper(), reviewed_by=RITA)
    assert database.updates[-1] == {"family_uuid": "uuid-FAM_AB", "assigned_to_id": RITA, "reviewed_by_id": RITA}
    # Clearing her, or naming someone who may work on the case, changes it.
    _save(database, _user(ANA), assigned_to=None, reviewed_by=ANA)
    assert database.updates[-1] == {"family_uuid": "uuid-FAM_AB", "assigned_to_id": None, "reviewed_by_id": ANA}


def test_naming_a_stored_reviewer_as_the_assignee_is_a_change_that_is_checked(database: _Database) -> None:
    database.stored.update(reviewed_by_id=RITA)  # type: ignore[attr-defined]
    refused = _refusal(database, _user(ANA), assigned_to=RITA)
    assert refused.status_code == 422 and refused.detail.startswith("Rita Reader cannot be assigned this case")
