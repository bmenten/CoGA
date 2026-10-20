"""Accounts of Microsoft sign-in, and the emergency administrator's password (SEC-4).

A user's first Microsoft sign-in makes their CoGA account, bound to the token's object ID: a
viewer's, active, in no project, with no password (a marker no password matches), with the
token's e-mail address (the ``email`` claim, else ``preferred_username``), user name and name.
Every later sign-in finds it by the object ID and follows the token's e-mail address and name
when they change, unless another account has them. A first sign-in whose e-mail address or user
name another account has is refused (403) and links nothing; so is one whose token gives no
e-mail address. Two requests of the same first sign-in make one account.

The emergency administrator is the local account ``ADMIN_USERNAME`` names, with an
administrator's role; the deployment seed keeps its password equal to ``ADMIN_PASSWORD``, so
rotating that secret rotates the password, and leaves an account of that name that signs in
with Microsoft alone.

The database is a stand-in that keeps the ``users`` rows and their unique columns; the same
flows run on real Postgres in ``e2e/test_e2e_microsoft_sign_in.py``. Values are synthetic.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from backend.app import db_migrate
from backend.app.core.azure import AzureIdentity
from backend.app.core.config import settings
from backend.app.dependencies import get_password_hash, verify_password
from backend.app.services import metadata_service, user_accounts
from backend.app.services.access_control import CurrentUser


def _identity(object_id: str | None = None, **claims: Any) -> AzureIdentity:
    values: dict[str, Any] = {
        "preferred_username": "a.colleague@example.org",
        "email": "a.colleague@example.org",
        "given_name": "A",
        "family_name": "Colleague",
        "name": "A Colleague",
    }
    values.update(claims)
    return AzureIdentity(object_id=object_id or str(uuid4()), **values)


class _Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _Users:
    """The ``users`` rows, with their unique username, email and azure_object_id."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.inserts = 0
        self.updates = 0
        self.commits = 0

    def add(self, **row: Any) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "role": "viewer",
            "is_active": True,
            "first_name": "",
            "last_name": "",
            "azure_object_id": None,
            **row,
        }
        self.rows.append(row)
        return row

    def taken(self, row: dict[str, Any], *, besides: str | None = None) -> bool:
        return any(
            other["id"] != besides
            and (
                other["username"] == row["username"]
                or other["email"] == row["email"]
                or (row.get("azure_object_id") and other["azure_object_id"] == row["azure_object_id"])
            )
            for other in self.rows
        )


class _Session:
    def __init__(self, users: _Users) -> None:
        self.users = users

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = params or {}
        if sql.startswith("SELECT email FROM users WHERE azure_object_id"):
            row = next((r for r in self.users.rows if r["azure_object_id"] == params["object_id"]), None)
            return _Result(row["email"] if row else None)
        if sql.startswith("INSERT INTO users"):
            self.users.inserts += 1
            row = {
                "username": params["username"],
                "email": params["email"],
                "azure_object_id": params["object_id"],
                "role": params["role"],
                "first_name": params["first_name"],
                "last_name": params["last_name"],
                "hashed_password": params["hashed_password"],
            }
            if self.users.taken(row):
                return _Result(None)
            return _Result(self.users.add(**row)["id"])
        if sql.startswith("UPDATE users"):
            self.users.updates += 1
            row = next(r for r in self.users.rows if r["id"] == params["user_id"])
            changed = {key: params[key] for key in ("email", "username", "first_name", "last_name")}
            if self.users.taken({**row, **changed}, besides=row["id"]):
                raise IntegrityError(sql, params, Exception("duplicate key value violates unique constraint"))
            row.update(changed)
            return _Result(None)
        raise AssertionError(f"unexpected statement: {sql}")

    @contextlib.asynccontextmanager
    async def begin_nested(self):
        yield self

    async def commit(self) -> None:
        self.users.commits += 1


@pytest.fixture()
def users(monkeypatch: pytest.MonkeyPatch) -> _Users:
    users = _Users()

    async def user_by_email(_session: Any, email: str) -> CurrentUser | None:
        row = next((r for r in users.rows if r["email"] == email), None)
        if row is None:
            return None
        return CurrentUser(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            azure_object_id=row["azure_object_id"],
            role=row["role"],
            is_active=row["is_active"],
            created_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(user_accounts, "get_current_user_by_email", user_by_email)
    return users


def _sign_in(users: _Users, identity: AzureIdentity):
    return asyncio.run(user_accounts.microsoft_account_user(_Session(users), identity))


# --------------------------------------------------------------------------- #
# The first sign-in, and every one after it
# --------------------------------------------------------------------------- #


def test_a_first_sign_in_makes_a_viewers_account_bound_to_the_object_id(users: _Users) -> None:
    identity = _identity()

    user, created = _sign_in(users, identity)

    (row,) = users.rows
    assert row["azure_object_id"] == identity.object_id
    assert (row["role"], row["email"], row["username"]) == ("viewer", "a.colleague@example.org", "a.colleague@example.org")
    assert (row["first_name"], row["last_name"]) == ("A", "Colleague")
    # No password matches the account's stored value.
    assert row["hashed_password"] == user_accounts.MICROSOFT_ACCOUNT_HASH_MARKER
    assert verify_password("", row["hashed_password"]) is False
    assert (user.id, user.role, user.is_active, user.metadata_project_ids) == (row["id"], "viewer", True, [])
    assert created == {
        "user_id": row["id"],
        "email": "a.colleague@example.org",
        "created": "microsoft_sign_in",
        "role": {"from": None, "to": "viewer"},
    }


def test_a_later_sign_in_finds_the_account_by_its_object_id(users: _Users) -> None:
    identity = _identity()
    first, _ = _sign_in(users, identity)
    # A local account with another address is no match, and neither is the address.
    users.add(username="someone.else@example.org", email="someone.else@example.org")

    again, created = _sign_in(users, identity)

    assert again.id == first.id and created is None
    assert users.inserts == 1


def test_the_account_follows_a_changed_e_mail_address_and_name(users: _Users) -> None:
    identity = _identity()
    first, _ = _sign_in(users, identity)

    renamed, created = _sign_in(
        users,
        _identity(
            identity.object_id,
            preferred_username="a.renamed@example.org",
            email="a.renamed@example.org",
            family_name="Renamed",
        ),
    )

    assert renamed.id == first.id and created is None
    assert (renamed.email, renamed.username, renamed.last_name) == (
        "a.renamed@example.org",
        "a.renamed@example.org",
        "Renamed",
    )


def test_an_address_another_account_has_is_not_taken_on_a_later_sign_in(users: _Users) -> None:
    identity = _identity()
    first, _ = _sign_in(users, identity)
    users.add(username="taken@example.org", email="taken@example.org")

    same, _ = _sign_in(users, _identity(identity.object_id, preferred_username="taken@example.org", email="taken@example.org"))

    assert same.id == first.id and same.email == "a.colleague@example.org"


def test_a_token_without_names_keeps_the_names_the_account_has(users: _Users) -> None:
    identity = _identity()
    _sign_in(users, identity)

    same, _ = _sign_in(users, _identity(identity.object_id, given_name=None, family_name=None, name=None))

    assert (same.first_name, same.last_name) == ("A", "Colleague")
    assert users.updates == 0


def test_the_e_mail_address_is_the_email_claim_else_the_preferred_username(users: _Users) -> None:
    by_upn, _ = _sign_in(users, _identity(email=None, preferred_username="upn.only@Example.ORG", name="Upn Only", given_name=None, family_name=None))

    assert by_upn.email == "upn.only@example.org"  # the domain as an e-mail address reads it
    assert by_upn.username == "upn.only@Example.ORG"
    assert (by_upn.first_name, by_upn.last_name) == ("Upn Only", "")


# --------------------------------------------------------------------------- #
# A first sign-in that is refused
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param({"username": "local@example.org", "email": "a.colleague@example.org"}, id="its-e-mail-address"),
        pytest.param({"username": "a.colleague@example.org", "email": "local@example.org"}, id="its-user-name"),
    ],
)
def test_a_first_sign_in_never_takes_over_another_account(users: _Users, existing: dict[str, str]) -> None:
    local = users.add(role="admin", **existing)

    with pytest.raises(HTTPException) as refused:
        _sign_in(users, _identity())

    assert (refused.value.status_code, refused.value.detail) == (403, user_accounts.EMAIL_TAKEN_DETAIL)
    assert users.rows == [local] and local["azure_object_id"] is None


def test_a_token_without_an_e_mail_address_makes_no_account(users: _Users) -> None:
    with pytest.raises(HTTPException) as refused:
        _sign_in(users, _identity(email=None, preferred_username="not an address"))

    assert (refused.value.status_code, refused.value.detail) == (403, user_accounts.NO_EMAIL_DETAIL)
    assert users.rows == [] and users.inserts == 0


def test_two_requests_of_one_first_sign_in_make_one_account(users: _Users, monkeypatch: pytest.MonkeyPatch) -> None:
    identity = _identity()
    original = _Session.execute

    async def racing(self: _Session, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        # The other request's account appears between this one's lookup and its insert.
        if str(statement).lstrip().startswith("INSERT INTO users") and not self.users.rows:
            self.users.add(
                username=identity.preferred_username, email=identity.email, azure_object_id=identity.object_id
            )
        return await original(self, statement, params)

    monkeypatch.setattr(_Session, "execute", racing)

    user, created = _sign_in(users, identity)

    assert len(users.rows) == 1 and user.id == users.rows[0]["id"]
    assert created is None


# --------------------------------------------------------------------------- #
# The emergency administrator
# --------------------------------------------------------------------------- #


def test_the_emergency_administrator_is_the_local_admin_username_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "admin_username", "coga-emergency")
    local = {"username": "coga-emergency", "azure_object_id": None, "role": "admin"}

    assert user_accounts.is_emergency_admin(local)
    assert user_accounts.is_emergency_admin({**local, "role": "superuser"})
    assert not user_accounts.is_emergency_admin({**local, "role": "viewer"})
    assert user_accounts.is_emergency_admin_account({**local, "role": "viewer"})
    assert not user_accounts.is_emergency_admin({**local, "username": "another-admin"})
    assert not user_accounts.is_emergency_admin({**local, "azure_object_id": str(uuid4())})
    assert not user_accounts.is_emergency_admin_account({**local, "azure_object_id": str(uuid4())})


def test_the_account_list_says_how_each_account_signs_in() -> None:
    row = {
        "id": str(uuid4()),
        "username": "u@example.org",
        "email": "u@example.org",
        "is_active": True,
        "role": "viewer",
        "created_at": datetime.now(timezone.utc),
    }
    assert metadata_service._user_read_from_mapping({**row, "azure_object_id": None}).sign_in == "password"
    assert metadata_service._user_read_from_mapping({**row, "azure_object_id": str(uuid4())}).sign_in == "microsoft"


class _SeedConnection:
    def __init__(self, account: dict[str, Any] | None) -> None:
        self.account = account
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None):
        sql = " ".join(str(statement).split())
        self.statements.append((sql, params or {}))
        account = self.account

        class _Rows:
            def mappings(self):
                return self

            def first(self):
                return account

        return _Rows()


class _SeedEngine:
    def __init__(self, connection: _SeedConnection) -> None:
        self.connection = connection

    @contextlib.asynccontextmanager
    async def begin(self):
        yield self.connection


def _seed(monkeypatch: pytest.MonkeyPatch, account: dict[str, Any] | None, phrase: str) -> _SeedConnection:
    connection = _SeedConnection(account)
    monkeypatch.setattr(db_migrate, "get_postgres_engine", lambda: _SeedEngine(connection))
    monkeypatch.setattr(settings, "admin_password", phrase)
    asyncio.run(db_migrate.init_postgres_admin_user())
    return connection


def test_a_rotated_admin_password_replaces_the_emergency_administrators(monkeypatch: pytest.MonkeyPatch) -> None:
    old_phrase, new_phrase = (" ".join(("rotated", "phrase", str(n))) for n in (1, 2))
    account = {"id": str(uuid4()), "hashed_password": get_password_hash(old_phrase), "microsoft": False}

    connection = _seed(monkeypatch, account, new_phrase)

    (_select, params), = [(sql, p) for sql, p in connection.statements if sql.startswith("UPDATE users")]
    assert params["id"] == account["id"]
    assert verify_password(new_phrase, params["hashed_password"])
    assert not verify_password(old_phrase, params["hashed_password"])


def test_an_unchanged_password_and_a_microsoft_account_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    phrase = " ".join(("unchanged", "phrase"))
    same = {"id": str(uuid4()), "hashed_password": get_password_hash(phrase), "microsoft": False}
    microsoft = {"id": str(uuid4()), "hashed_password": user_accounts.MICROSOFT_ACCOUNT_HASH_MARKER, "microsoft": True}

    for account in (same, microsoft):
        connection = _seed(monkeypatch, account, phrase)
        assert [sql for sql, _ in connection.statements if not sql.startswith("SELECT")] == []


def test_without_the_account_the_seed_makes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    phrase = " ".join(("first", "phrase"))

    connection = _seed(monkeypatch, None, phrase)

    ((sql, params),) = [(sql, p) for sql, p in connection.statements if sql.startswith("INSERT INTO users")]
    assert params["username"] == settings.admin_username and "'admin'" in sql
    assert verify_password(phrase, params["hashed_password"])
