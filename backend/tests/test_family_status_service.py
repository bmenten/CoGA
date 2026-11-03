"""The family status catalogue and the status a family's workflow save names
(``services/family_status_service.py``).

An administrator curates the catalogue of case statuses (``family_statuses``) on the admin
dashboard: the routes are admin-only (``test_admin_route_gating.py`` walks them). A status gets
a key of its own made from its label, a colour that is a hex colour or the default one (the
page sets it as a style, so nothing else is kept), and a place after the others unless it is
given one; a blank label is refused before anything is written, and a status that does not
exist is not found. The family page's picker lists the active statuses, the admin dashboard
every one.

A family's workflow save names a status by its key: one the catalogue does not hold is refused
(404) and nothing of the save is written; ``None`` clears it, and a save that names no field
writes nothing. Who the case may be assigned to, or recorded as reviewed by, is in
``test_case_assignee_eligibility.py``; a reader's save is refused at the family checkpoint
(``test_project_access_levels.py``).

The database is a session that answers the service's statements from an in-memory table.
All IDs are synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.schemas import FamilyMetadataUpdate, FamilyStatusCreate, FamilyStatusUpdate
from backend.app.services import family_status_service, metadata_service
from backend.app.services.access_control import CurrentUser

ADMIN_ID = "00000000-0000-4000-8000-000000000101"
PROJECT = "00000000-0000-4000-8000-00000000000a"
DEFAULT_COLOUR = "#5b6b79"


def _status(key: str, label: str, *, sort_order: int, active: bool = True) -> dict[str, Any]:
    return {
        "id": f"status-{key}",
        "key": key,
        "label": label,
        "description": None,
        "color": DEFAULT_COLOUR,
        "sort_order": sort_order,
        "is_active": active,
    }


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self.rows)

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def one(self) -> dict[str, Any]:
        [row] = self.rows
        return row

    def one_or_none(self) -> dict[str, Any] | None:
        return self.first()

    def scalar_one(self) -> Any:
        return self.scalar

    def scalar_one_or_none(self) -> Any:
        return self.scalar


class _Database:
    """The ``family_statuses`` table, the active accounts and the families' workflow updates;
    records each statement and each commit."""

    def __init__(self, *statuses: dict[str, Any]) -> None:
        self.statuses = {status["key"]: dict(status) for status in statuses}
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.family_updates: list[dict[str, Any]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        self.statements.append((sql, params))
        if sql.startswith("SELECT 1 FROM family_statuses WHERE key"):
            return _Result(scalar=1 if params["key"] in self.statuses else None)
        if sql.startswith("SELECT COALESCE(MAX(sort_order), 0)"):
            return _Result(scalar=max((status["sort_order"] for status in self.statuses.values()), default=0))
        if sql.startswith("INSERT INTO family_statuses"):
            row = {
                "id": f"status-{params['key']}",
                **{name: params[name] for name in ("key", "label", "description", "color", "sort_order")},
                "is_active": True,
            }
            self.statuses[params["key"]] = row
            return _Result([row])
        if sql.startswith("UPDATE family_statuses"):
            row = self.statuses.get(params["key"])
            if row is None:
                return _Result([])
            row.update({name: params[name] for name in ("label", "description", "color", "sort_order", "is_active") if name in params})
            return _Result([row])
        if sql.startswith("DELETE FROM family_statuses"):
            row = self.statuses.pop(params["key"], None)
            return _Result(scalar=row["id"] if row else None)
        if sql.startswith("SELECT id::text AS id, key, label"):
            rows = [row for row in self.statuses.values() if row["is_active"] or "WHERE is_active" not in sql]
            return _Result(sorted(rows, key=lambda row: (row["sort_order"], row["label"].lower())))
        if sql.startswith("SELECT id::text AS id FROM family_statuses WHERE key"):
            row = self.statuses.get(params["key"])
            return _Result(scalar=row["id"] if row else None)
        if sql.startswith("SELECT id::text AS id, username, email, first_name, last_name FROM users"):
            return _Result(
                [
                    {"id": ADMIN_ID, "username": "ada", "email": "ada@example.org", "first_name": "Ada", "last_name": None},
                    {"id": "u-2", "username": "bo", "email": "bo@example.org", "first_name": None, "last_name": None},
                ]
            )
        if sql.startswith("UPDATE families SET"):
            self.family_updates.append(params)
            return _Result()
        raise AssertionError(f"unexpected statement: {sql[:80]}")

    async def commit(self) -> None:
        self.commits += 1


def _admin() -> CurrentUser:
    return CurrentUser(
        id=ADMIN_ID,
        username="ada",
        email="ada@example.org",
        role="admin",
        created_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
    )


def _create(database: _Database, **fields: Any) -> Any:
    return asyncio.run(
        family_status_service.create_family_status(database, payload=FamilyStatusCreate(**fields), user=_admin())  # type: ignore[arg-type]
    )


def _update(database: _Database, key: str, **fields: Any) -> Any:
    return asyncio.run(
        family_status_service.update_family_status(database, key=key, payload=FamilyStatusUpdate(**fields))  # type: ignore[arg-type]
    )


def _refusal(call: Any) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        call()
    return refused.value


# --------------------------------------------------------------------------- the catalogue


def test_a_status_gets_a_key_of_its_own_made_from_its_label() -> None:
    database = _Database(
        _status("awaiting_review", "Awaiting review", sort_order=10),
        _status("awaiting_review_2", "Awaiting review", sort_order=20),
    )

    created = _create(database, label="  Awaiting review! ", description="  ")
    unnamed = _create(database, label="?!")

    assert (created.key, created.label, created.description) == ("awaiting_review_3", "Awaiting review!", None)
    # A label with no letter or digit still gets a key.
    assert unnamed.key == "status"
    assert database.commits == 2
    [first_insert] = [params for sql, params in database.statements if sql.startswith("INSERT")][:1]
    assert first_insert["created_by"] == ADMIN_ID


def test_a_new_status_goes_after_the_others_unless_it_is_placed() -> None:
    database = _Database(_status("new", "New", sort_order=10), _status("solved", "Solved", sort_order=40))

    assert _create(database, label="Reported").sort_order == 50
    assert _create(database, label="Urgent", sort_order=5).sort_order == 5


@pytest.mark.parametrize(
    ("colour", "kept"),
    [
        ("#AABBCC", "#aabbcc"),
        (" #a1b2c3 ", "#a1b2c3"),
        ("red", DEFAULT_COLOUR),
        ("#abc", DEFAULT_COLOUR),
        ("#aabbcc; background: url(x)", DEFAULT_COLOUR),
        ("", DEFAULT_COLOUR),
        (None, DEFAULT_COLOUR),
    ],
    ids=["upper-case", "padded", "a-name", "short-form", "more-than-a-colour", "empty", "none"],
)
def test_a_status_colour_is_a_hex_colour_or_the_default(colour: str | None, kept: str) -> None:
    database = _Database(_status("new", "New", sort_order=10))

    assert _create(database, label="Reported", color=colour).color == kept
    assert _update(database, "new", color=colour).color == kept


def test_a_blank_label_is_refused_before_anything_is_written() -> None:
    database = _Database(_status("new", "New", sort_order=10))

    created = _refusal(lambda: _create(database, label="   "))
    renamed = _refusal(lambda: _update(database, "new", label="  "))

    assert (created.status_code, created.detail) == (400, "Status label is required")
    assert (renamed.status_code, renamed.detail) == (400, "Status label cannot be empty")
    assert database.statements == [] and database.commits == 0
    assert database.statuses["new"]["label"] == "New"


def test_an_update_changes_only_the_fields_it_names() -> None:
    database = _Database(_status("new", "New", sort_order=10))

    described = _update(database, "new", description="  First look  ", label=None, sort_order=None)
    assert (described.label, described.description, described.sort_order) == ("New", "First look", 10)

    retired = _update(database, "new", is_active=False, sort_order=30, label=" Intake ")
    assert (retired.label, retired.sort_order, retired.is_active, retired.description) == ("Intake", 30, False, "First look")
    assert database.commits == 2


def test_an_unknown_status_is_not_found_and_nothing_is_committed() -> None:
    database = _Database()

    updated = _refusal(lambda: _update(database, "no_such", label="Solved"))
    deleted = _refusal(lambda: asyncio.run(family_status_service.delete_family_status(database, key="no_such")))  # type: ignore[arg-type]

    assert (updated.status_code, updated.detail) == (deleted.status_code, deleted.detail) == (404, "Family status not found")
    assert database.commits == 0


def test_a_deleted_status_leaves_the_catalogue() -> None:
    database = _Database(_status("new", "New", sort_order=10), _status("solved", "Solved", sort_order=20))

    asyncio.run(family_status_service.delete_family_status(database, key="new"))  # type: ignore[arg-type]

    assert list(database.statuses) == ["solved"] and database.commits == 1


def test_the_picker_lists_the_active_statuses_and_the_admin_every_one() -> None:
    database = _Database(
        _status("solved", "Solved", sort_order=20),
        _status("retired", "retired", sort_order=10, active=False),
        _status("new", "New", sort_order=10),
    )

    picker = asyncio.run(family_status_service.list_family_statuses(database))  # type: ignore[arg-type]
    dashboard = asyncio.run(family_status_service.list_family_statuses(database, include_inactive=True))  # type: ignore[arg-type]

    assert [status.key for status in picker] == ["new", "solved"]
    assert [(status.key, status.is_active) for status in dashboard] == [("new", True), ("retired", False), ("solved", True)]


def test_the_account_list_names_each_active_account() -> None:
    accounts = asyncio.run(family_status_service.list_assignable_users(_Database()))  # type: ignore[arg-type]

    assert [(account.id, account.username, account.first_name, account.last_name) for account in accounts] == [
        (ADMIN_ID, "ada", "Ada", ""),
        ("u-2", "bo", "", ""),
    ]


# --------------------------------------------------------------------------- a family's save


@pytest.fixture()
def family(monkeypatch: pytest.MonkeyPatch) -> None:
    async def family_rows(_session: Any, *, family_identifiers: list[str] | None = None, **_kwargs: Any) -> list[dict]:
        return [
            {"id": "uuid-FAM1", "family_id": family_id, "project_ids": [PROJECT], "assigned_to_id": None, "reviewed_by_id": None}
            for family_id in family_identifiers or []
            if family_id == "FAM1"
        ]

    async def family_record(_session: Any, family_id: str, _user: Any) -> str:
        return f"record of {family_id}"

    monkeypatch.setattr(metadata_service, "_fetch_family_rows", family_rows)
    monkeypatch.setattr(family_status_service, "get_family_record", family_record)


def _save(database: _Database, **fields: Any) -> Any:
    return asyncio.run(
        family_status_service.update_family_metadata_for_user(
            database,  # type: ignore[arg-type]
            family_id="FAM1",
            update=FamilyMetadataUpdate(**fields),
            user=_admin().model_copy(update={"changes_records": True}),
        )
    )


@pytest.mark.usefixtures("family")
def test_a_save_naming_a_status_the_catalogue_does_not_hold_is_refused_and_writes_nothing() -> None:
    database = _Database(_status("solved", "Solved", sort_order=10))

    refused = _refusal(lambda: _save(database, status_key="no_such", assigned_to=None))

    assert (refused.status_code, refused.detail) == (404, "Unknown family status: no_such")
    assert database.family_updates == [] and database.commits == 0


@pytest.mark.usefixtures("family")
def test_a_status_is_set_by_its_key_and_cleared_with_none() -> None:
    database = _Database(_status("solved", "Solved", sort_order=10))

    assert _save(database, status_key="solved") == "record of FAM1"
    _save(database, status_key=None)

    assert database.family_updates == [
        {"family_uuid": "uuid-FAM1", "status_id": "status-solved"},
        {"family_uuid": "uuid-FAM1", "status_id": None},
    ]
    assert database.commits == 2


@pytest.mark.usefixtures("family")
def test_a_save_that_names_no_field_writes_nothing() -> None:
    database = _Database()

    assert _save(database) == "record of FAM1"
    assert database.statements == [] and database.commits == 0
