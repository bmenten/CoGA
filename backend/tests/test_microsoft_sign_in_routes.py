"""The sign-in routes with Microsoft sign-in configured, and an administrator's role changes.

``GET /api/auth/config`` tells the browser how users sign in: with Microsoft (the application
ID, the directory's authority and CoGA's API scope, none of it secret) or with a password.

With Azure AD configured the password sign-in (``/auth/login`` and the Swagger form at
``/auth/token``) is switched off, before any account is looked up, unless
``AZURE_ADMIN_OVERRIDE`` is on; then it serves the emergency administrator alone (the active
local account ``ADMIN_USERNAME`` names, with an administrator's role), whose sign-in is logged
and flagged in the request audit (``request_meta.emergency_sign_in``). Any other account's
right password is refused with 403 and counts as a failed attempt. There is no sign-up:
accounts come from the first Microsoft sign-in.

``PATCH /api/auth/users/{id}`` sets an account's global role as well as its activation,
archives or restores it, and sets its projects with its level in each; a field it does
not take, such as the account list's ``projects``, is refused (422). An administrator cannot change their own role, nor the emergency
administrator's, and cannot deactivate or archive their own account or the emergency
administrator's; an account is not archived and activated at once; a viewer cannot call it.
``DELETE /api/auth/users/{id}`` deletes an account that was never active, and refuses the
administrator's own account and the emergency administrator's. What changed, each field from
and to, or the account deleted, is in the request's audit row (``request_meta.account_change``)
(REQ-SEC-002, REQ-SEC-004).

The requests go through the real routes, ``get_current_user`` and the audit middleware; the
account store, the throttle and the audit write are replaced. The accounts are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
import jwt
import pytest

from backend.app import dependencies
from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import auth as auth_router
from backend.app.schemas import UserRead
from backend.app.services.access_control import CurrentUser

_LOCAL_SIGNER = "k" * 64
# The right password and the stored hash it matches, put together at run time.
_PHRASE = " ".join(("correct", "horse", "battery", "staple"))
_STORED_HASH = "-".join(("stored", "hash"))
_EMERGENCY_USERNAME = "coga-emergency"


def _account(email: str, role: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "username": email,
        "email": email,
        "role": role,
        "is_active": True,
        "azure_object_id": None,
        "hashed_password": _STORED_HASH,
        "first_name": "",
        "last_name": "",
        "affiliation": "",
        "metadata_project_ids": [],
        "created_at": datetime.now(timezone.utc),
        **fields,
    }


EMERGENCY = _account("emergency@example.org", "admin", username=_EMERGENCY_USERNAME)
ADMIN = _account("admin@example.org", "admin", azure_object_id=str(uuid4()))
VIEWER = _account("viewer@example.org", "viewer", azure_object_id=str(uuid4()))
LOCAL_VIEWER = _account("local.viewer@example.org", "viewer")
LOCAL_ADMIN = _account("local.admin@example.org", "admin")
_ACCOUNTS = {account["email"]: account for account in (EMERGENCY, ADMIN, VIEWER, LOCAL_VIEWER, LOCAL_ADMIN)}


def _current_user(account: dict[str, Any]) -> CurrentUser:
    return CurrentUser(
        id=account["id"],
        username=account["username"],
        email=account["email"],
        role=account["role"],
        is_active=account["is_active"],
        azure_object_id=account["azure_object_id"],
        created_at=account["created_at"],
        activated_at=account.get("activated_at"),
        archived_at=account.get("archived_at"),
    )


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _Store:
    """The accounts, the login throttle's bookkeeping and the role and activation updates."""

    def __init__(self) -> None:
        self.accounts = {email: dict(account) for email, account in _ACCOUNTS.items()}
        self.looked_up: list[str] = []
        self.failed: list[str] = []
        self.cleared: list[str] = []
        self.throttle_reads = 0
        self.updates: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def by_id(self, user_id: str) -> dict[str, Any] | None:
        return next((account for account in self.accounts.values() if account["id"] == user_id), None)


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    store = _Store()

    async def account_by_email(_session: Any, email: str) -> dict[str, Any] | None:
        store.looked_up.append(email)
        return store.accounts.get(email)

    async def user_by_email(_session: Any, email: str) -> CurrentUser | None:
        account = store.accounts.get(email)
        return _current_user(account) if account else None

    async def throttle(*_args: Any, **_kwargs: Any) -> None:
        store.throttle_reads += 1
        return None

    async def failed(_session: Any, *, email: str, remote_ip: Any) -> None:
        store.failed.append(email)

    async def cleared(_session: Any, *, email: str, remote_ip: Any) -> None:
        store.cleared.append(email)

    async def account_by_id(_session: Any, user_id: str) -> dict[str, Any] | None:
        return store.by_id(user_id)

    async def update(
        _session: Any,
        *,
        user_id: str,
        is_active: bool | None,
        role: str | None = None,
        archived: bool | None = None,
        project_ids: list[str] | None = None,
        reader_project_ids: list[str] | None = None,
    ):
        account = store.by_id(user_id)
        assert account is not None
        changes: dict[str, dict[str, Any]] = {}
        if project_ids is not None and sorted(project_ids) != sorted(account["metadata_project_ids"]):
            changes["project_ids"] = {"from": sorted(account["metadata_project_ids"]), "to": sorted(project_ids)}
            account["metadata_project_ids"] = list(project_ids)
        if reader_project_ids is not None and sorted(reader_project_ids) != sorted(account.get("reader_project_ids", [])):
            changes["reader_project_ids"] = {
                "from": sorted(account.get("reader_project_ids", [])),
                "to": sorted(reader_project_ids),
            }
            account["reader_project_ids"] = list(reader_project_ids)
        if archived is not None and archived != bool(account.get("archived_at")):
            changes["archived"] = {"from": not archived, "to": archived}
            account["archived_at"] = datetime.now(timezone.utc) if archived else None
            if archived and account["is_active"]:
                changes["is_active"] = {"from": True, "to": False}
                account["is_active"] = False
        if is_active is not None and is_active != account["is_active"]:
            changes["is_active"] = {"from": account["is_active"], "to": is_active}
            account["is_active"] = is_active
        if role is not None and role != account["role"]:
            changes["role"] = {"from": account["role"], "to": role}
            account["role"] = role
        store.updates.append(
            {"user_id": user_id, "is_active": is_active, "role": role}
            | ({"archived": archived} if archived is not None else {})
            | ({"project_ids": project_ids} if project_ids is not None else {})
            | ({"reader_project_ids": reader_project_ids} if reader_project_ids is not None else {})
        )
        return (
            UserRead(
                id=account["id"],
                username=account["username"],
                email=account["email"],
                is_active=account["is_active"],
                role=account["role"],
                sign_in="microsoft" if account["azure_object_id"] else "password",
                created_at=account["created_at"],
                projects=account["metadata_project_ids"],
                reader_projects=account.get("reader_project_ids", []),
            ),
            changes,
        )

    monkeypatch.setattr(auth_router, "get_auth_user_mapping_by_email", account_by_email)
    monkeypatch.setattr(auth_router, "get_login_throttle_state", throttle)
    monkeypatch.setattr(auth_router, "record_failed_login", failed)
    monkeypatch.setattr(auth_router, "clear_login_failures", cleared)
    monkeypatch.setattr(
        auth_router, "verify_password", lambda plain, hashed: plain == _PHRASE and hashed == _STORED_HASH
    )
    monkeypatch.setattr(auth_router, "get_user_account_mapping", account_by_id)
    async def delete(_session: Any, *, user_id: str) -> dict[str, Any]:
        account = store.by_id(user_id)
        assert account is not None
        store.deleted.append(user_id)
        store.accounts.pop(account["email"])
        return {"user_id": user_id, "email": account["email"], "deleted": True}

    monkeypatch.setattr(auth_router, "update_user_account", update)
    monkeypatch.setattr(auth_router, "delete_waiting_user_account", delete)
    monkeypatch.setattr(dependencies, "get_current_user_by_email", user_by_email)
    monkeypatch.setattr(dependencies, "get_auth_user_mapping_by_email", account_by_email)
    return store


@pytest.fixture()
def audit_rows(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    rows: list[Any] = []

    async def keep(payload: Any) -> None:
        rows.append(payload)

    monkeypatch.setattr(request_logging, "write_audit_log_event", keep)
    return rows


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, store: _Store, audit_rows: list[Any]):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    monkeypatch.setattr(settings, "secret_key", _LOCAL_SIGNER)
    monkeypatch.setattr(settings, "azure_tenant_id", None)
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.setattr(settings, "azure_api_scope", None)
    monkeypatch.setattr(settings, "azure_admin_override", False)
    monkeypatch.setattr(settings, "admin_username", _EMERGENCY_USERNAME)

    async def override_get_postgres_session():
        yield _FakeSession()

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides = original_overrides


@pytest.fixture()
def azure(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    tenant, client_id = str(uuid4()), str(uuid4())
    monkeypatch.setattr(settings, "azure_tenant_id", tenant)
    monkeypatch.setattr(settings, "azure_client_id", client_id)
    return {"tenant": tenant, "client": client_id}


def _bearer(email: str) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": email, "exp": now + timedelta(minutes=5), "iat": now.timestamp()}, _LOCAL_SIGNER, algorithm="HS256"
    )
    return {"Authorization": f"Bearer {token}"}


def _rows(audit_rows: list[Any], path: str) -> list[Any]:
    return [row for row in audit_rows if row.path == path]


# --------------------------------------------------------------------------- #
# How users sign in
# --------------------------------------------------------------------------- #


def test_without_azure_the_browser_is_told_to_use_the_password_sign_in(client: TestClient) -> None:
    response = client.get("/api/auth/config")
    assert response.status_code == 200, response.text
    assert response.json() == {"microsoft": None, "cas": None}


def test_with_azure_the_browser_gets_the_application_authority_and_scope(
    client: TestClient, azure: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.get("/api/auth/config")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "microsoft": {
            "client_id": azure["client"],
            "authority": f"https://login.microsoftonline.com/{azure['tenant']}",
            "api_scope": f"api://{azure['client']}/access_as_user",
        },
        "cas": None,
    }

    monkeypatch.setattr(settings, "azure_api_scope", "api://coga.example.org/use_coga")
    assert client.get("/api/auth/config").json()["microsoft"]["api_scope"] == "api://coga.example.org/use_coga"


# --------------------------------------------------------------------------- #
# The password sign-in and the sign-up with Azure AD
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("route", ["login", "token"])
def test_with_azure_and_no_override_the_password_sign_in_is_off_before_any_lookup(
    client: TestClient, azure: dict[str, str], store: _Store, route: str
) -> None:
    for account in (EMERGENCY, LOCAL_VIEWER, {"email": "nobody@example.org"}):
        if route == "login":
            response = client.post("/api/auth/login", json={"email": account["email"], "password": _PHRASE})
        else:
            response = client.post("/api/auth/token", data={"username": account["email"], "password": _PHRASE})
        assert response.status_code == 403, response.text
        assert response.json() == {"detail": auth_router.PASSWORD_SIGN_IN_OFF_DETAIL}
    assert (store.looked_up, store.throttle_reads, store.failed) == ([], 0, [])


@pytest.mark.parametrize("route", ["login", "token"])
def test_the_override_lets_the_emergency_admin_sign_in_logged_and_flagged(
    client: TestClient,
    azure: dict[str, str],
    store: _Store,
    audit_rows: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    route: str,
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)

    with caplog.at_level(logging.WARNING, logger=auth_router.__name__):
        if route == "login":
            response = client.post("/api/auth/login", json={"email": EMERGENCY["email"], "password": _PHRASE})
        else:
            response = client.post("/api/auth/token", data={"username": EMERGENCY["email"], "password": _PHRASE})

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "admin"
    assert jwt.decode(response.json()["access_token"], _LOCAL_SIGNER, algorithms=["HS256"])["sub"] == EMERGENCY["email"]
    assert store.cleared == [EMERGENCY["email"]] and store.failed == []
    (row,) = _rows(audit_rows, f"/api/auth/{route}")
    assert row.request_meta.get("emergency_sign_in") is True
    warnings = [record.getMessage() for record in caplog.records if record.name == auth_router.__name__]
    assert warnings == [
        f"azure_admin_override: the emergency administrator {EMERGENCY['id']} signed in with its password "
        "beside Microsoft sign-in"
    ]


@pytest.mark.parametrize(
    "account",
    [
        pytest.param(LOCAL_VIEWER, id="a-local-viewer"),
        pytest.param(LOCAL_ADMIN, id="another-local-admin"),
        pytest.param(
            {**EMERGENCY, "email": "renamed@example.org", "azure_object_id": str(uuid4())},
            id="the-emergency-name-on-a-microsoft-account",
        ),
    ],
)
def test_the_override_refuses_every_other_accounts_right_password(
    client: TestClient,
    azure: dict[str, str],
    store: _Store,
    audit_rows: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    account: dict[str, Any],
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)
    store.accounts[account["email"]] = account

    response = client.post("/api/auth/login", json={"email": account["email"], "password": _PHRASE})

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": auth_router.EMERGENCY_ONLY_DETAIL}
    assert store.failed == [account["email"]] and store.cleared == []
    (row,) = _rows(audit_rows, "/api/auth/login")
    assert "emergency_sign_in" not in row.request_meta


def test_the_override_keeps_the_wrong_password_answer(
    client: TestClient, azure: dict[str, str], store: _Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)

    response = client.post("/api/auth/login", json={"email": EMERGENCY["email"], "password": " ".join(("not", "the", "phrase"))})

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "Incorrect email or password"}
    assert store.failed == [EMERGENCY["email"]]


def test_with_azure_there_is_no_sign_up(
    client: TestClient, azure: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    touched: list[str] = []

    async def record(*_args: Any, **_kwargs: Any) -> None:
        touched.append("throttle")

    async def create(*_args: Any, **_kwargs: Any) -> None:
        touched.append("create")

    monkeypatch.setattr(auth_router, "get_signup_throttle_state", record)
    monkeypatch.setattr(auth_router, "record_signup_attempt", record)
    monkeypatch.setattr(auth_router, "create_user_account", create)

    response = client.post(
        "/api/auth/signup",
        json={
            "email": "new@example.org",
            "password": " ".join(("a", "long", "enough", "sign-up", "phrase")),
            "first_name": "New",
            "last_name": "User",
            "affiliation": "Lab",
        },
    )

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": auth_router.SIGNUP_OFF_DETAIL}
    assert touched == []


# --------------------------------------------------------------------------- #
# An administrator sets an account's role
# --------------------------------------------------------------------------- #


def test_an_admin_sets_an_accounts_role_and_the_audit_row_says_from_what_to_what(
    client: TestClient, store: _Store, audit_rows: list[Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=auth_router.__name__):
        response = client.patch(
            f"/api/auth/users/{VIEWER['id']}", json={"role": "admin"}, headers=_bearer(LOCAL_ADMIN["email"])
        )

    assert response.status_code == 200, response.text
    assert (response.json()["role"], response.json()["sign_in"]) == ("admin", "microsoft")
    assert store.updates == [{"user_id": VIEWER["id"], "is_active": None, "role": "admin"}]
    (row,) = _rows(audit_rows, f"/api/auth/users/{VIEWER['id']}")
    assert (row.user_email, row.status_code, row.request_body) == (LOCAL_ADMIN["email"], 200, {"role": "admin"})
    assert row.request_meta["account_change"] == {
        "user_id": VIEWER["id"],
        "email": VIEWER["email"],
        "role": {"from": "viewer", "to": "admin"},
    }
    # The application log names the account and the changed field; who changed it and the values
    # stay in the request audit above, so nothing the request sent, its token included, reaches
    # the log line (CodeQL py/log-injection).
    (line,) = [record.getMessage() for record in caplog.records if " changed: " in record.getMessage()]
    assert line == f"account {VIEWER['id']} changed: role (who changed it, and from and to, in the request audit)"
    assert LOCAL_ADMIN["id"] not in line
    assert "viewer" not in line and "admin" not in line


def test_an_activation_change_is_recorded_the_same_way(
    client: TestClient, store: _Store, audit_rows: list[Any]
) -> None:
    response = client.patch(
        f"/api/auth/users/{LOCAL_VIEWER['id']}", json={"is_active": False}, headers=_bearer(LOCAL_ADMIN["email"])
    )

    assert response.status_code == 200, response.text
    (row,) = _rows(audit_rows, f"/api/auth/users/{LOCAL_VIEWER['id']}")
    assert row.request_meta["account_change"] == {
        "user_id": LOCAL_VIEWER["id"],
        "email": LOCAL_VIEWER["email"],
        "is_active": {"from": True, "to": False},
    }


def test_a_change_that_changes_nothing_records_nothing(
    client: TestClient, store: _Store, audit_rows: list[Any]
) -> None:
    # Your own role, set to what it is, is no change of it.
    response = client.patch(
        f"/api/auth/users/{LOCAL_ADMIN['id']}", json={"role": "admin"}, headers=_bearer(LOCAL_ADMIN["email"])
    )

    assert response.status_code == 200, response.text
    (row,) = _rows(audit_rows, f"/api/auth/users/{LOCAL_ADMIN['id']}")
    assert "account_change" not in row.request_meta


@pytest.mark.parametrize(
    ("target", "body", "detail"),
    [
        pytest.param(LOCAL_ADMIN, {"role": "viewer"}, auth_router.OWN_ROLE_DETAIL, id="your-own-role"),
        pytest.param(EMERGENCY, {"role": "viewer"}, auth_router.EMERGENCY_ROLE_DETAIL, id="the-emergency-admins"),
        pytest.param(
            EMERGENCY, {"role": "superuser"}, auth_router.EMERGENCY_ROLE_DETAIL, id="the-emergency-admin-to-superuser"
        ),
        pytest.param(
            EMERGENCY, {"is_active": False}, auth_router.EMERGENCY_ACTIVE_DETAIL, id="the-emergency-admin-deactivated"
        ),
        # An administrator who deactivated their own account locked themselves out.
        pytest.param(LOCAL_ADMIN, {"is_active": False}, auth_router.OWN_ACTIVE_DETAIL, id="your-own-account-deactivated"),
    ],
)
def test_some_roles_are_not_changed(
    client: TestClient, store: _Store, audit_rows: list[Any], target: dict, body: dict, detail: str
) -> None:
    response = client.patch(f"/api/auth/users/{target['id']}", json=body, headers=_bearer(LOCAL_ADMIN["email"]))

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": detail}
    assert store.updates == []
    assert store.by_id(target["id"])["role"] == target["role"]
    assert store.by_id(target["id"])["is_active"] is True


def test_a_viewer_sets_no_role_and_an_unknown_role_is_refused(client: TestClient, store: _Store) -> None:
    by_viewer = client.patch(
        f"/api/auth/users/{LOCAL_VIEWER['id']}", json={"role": "admin"}, headers=_bearer(LOCAL_VIEWER["email"])
    )
    unknown_role = client.patch(
        f"/api/auth/users/{VIEWER['id']}", json={"role": "owner"}, headers=_bearer(LOCAL_ADMIN["email"])
    )
    unknown_user = client.patch(
        f"/api/auth/users/{uuid4()}", json={"role": "admin"}, headers=_bearer(LOCAL_ADMIN["email"])
    )

    assert by_viewer.status_code == 403 and by_viewer.json() == {"detail": "Admin access required"}
    assert unknown_role.status_code == 422, unknown_role.text
    assert unknown_user.status_code == 404 and unknown_user.json() == {"detail": "User not found"}
    assert store.updates == []


def test_a_field_the_change_does_not_take_is_refused_whole(
    client: TestClient, store: _Store, audit_rows: list[Any]
) -> None:
    # ``projects`` is how the account list names an account's projects; the change takes
    # ``project_ids``. Left out without a word, it would read as a change of access made: the
    # request is refused whole (422, naming the field), its role change with it.
    response = client.patch(
        f"/api/auth/users/{LOCAL_VIEWER['id']}",
        json={"projects": [str(uuid4())], "role": "admin"},
        headers=_bearer(LOCAL_ADMIN["email"]),
    )

    assert response.status_code == 422, response.text
    assert [error["loc"] for error in response.json()["detail"]] == [["body", "projects"]]
    assert store.updates == []
    assert store.by_id(LOCAL_VIEWER["id"])["role"] == "viewer"
    (row,) = _rows(audit_rows, f"/api/auth/users/{LOCAL_VIEWER['id']}")
    assert "account_change" not in row.request_meta


def test_an_admin_sets_an_accounts_projects_and_the_audit_row_says_from_what_to_what(
    client: TestClient, store: _Store, audit_rows: list[Any], caplog: pytest.LogCaptureFixture
) -> None:
    # The same memberships a project's members set, from the account's side. Each id is
    # read as the canonical text of the UUID it spells, whatever its spelling.
    analysed, read = str(uuid4()), str(uuid4())
    with caplog.at_level(logging.INFO, logger=auth_router.__name__):
        response = client.patch(
            f"/api/auth/users/{LOCAL_VIEWER['id']}",
            json={"project_ids": [analysed.upper(), "{" + read + "}"], "reader_project_ids": [f"urn:uuid:{read}"]},
            headers=_bearer(LOCAL_ADMIN["email"]),
        )

    assert response.status_code == 200, response.text
    assert (response.json()["projects"], response.json()["reader_projects"]) == ([analysed, read], [read])
    assert store.updates == [
        {
            "user_id": LOCAL_VIEWER["id"],
            "is_active": None,
            "role": None,
            "project_ids": [analysed, read],
            "reader_project_ids": [read],
        }
    ]
    (row,) = _rows(audit_rows, f"/api/auth/users/{LOCAL_VIEWER['id']}")
    assert row.request_meta["account_change"] == {
        "user_id": LOCAL_VIEWER["id"],
        "email": LOCAL_VIEWER["email"],
        "project_ids": {"from": [], "to": sorted([analysed, read])},
        "reader_project_ids": {"from": [], "to": [read]},
    }
    # The log line names the fields, never the projects: they are in the request audit.
    (line,) = [record.getMessage() for record in caplog.records if " changed: " in record.getMessage()]
    assert line == (
        f"account {LOCAL_VIEWER['id']} changed: project_ids, reader_project_ids "
        "(who changed it, and from and to, in the request audit)"
    )
    assert analysed not in line and read not in line


@pytest.mark.parametrize("field", ["project_ids", "reader_project_ids"])
def test_a_project_that_is_no_uuid_is_not_found_and_nothing_changes(
    client: TestClient, store: _Store, audit_rows: list[Any], field: str
) -> None:
    # As an unknown project is (the service's lookup), before any lookup; the role change sent
    # with it is not made either.
    response = client.patch(
        f"/api/auth/users/{LOCAL_VIEWER['id']}",
        json={field: ["not-a-project"], "role": "analyst"},
        headers=_bearer(LOCAL_ADMIN["email"]),
    )

    assert (response.status_code, response.json()) == (404, {"detail": "Project not found"})
    assert store.updates == []
    assert store.by_id(LOCAL_VIEWER["id"])["role"] == "viewer"
    (row,) = _rows(audit_rows, f"/api/auth/users/{LOCAL_VIEWER['id']}")
    assert "account_change" not in row.request_meta


# --------------------------------------------------------------------------- #
# An administrator archives, restores and deletes an account
# --------------------------------------------------------------------------- #


def test_archiving_an_account_deactivates_it_and_the_audit_row_says_so(
    client: TestClient, store: _Store, audit_rows: list[Any]
) -> None:
    archived = client.patch(
        f"/api/auth/users/{LOCAL_VIEWER['id']}", json={"archived": True}, headers=_bearer(LOCAL_ADMIN["email"])
    )
    restored = client.patch(
        f"/api/auth/users/{LOCAL_VIEWER['id']}", json={"archived": False}, headers=_bearer(LOCAL_ADMIN["email"])
    )

    assert archived.status_code == 200, archived.text
    assert restored.status_code == 200, restored.text
    assert store.updates == [
        {"user_id": LOCAL_VIEWER["id"], "is_active": None, "role": None, "archived": True},
        {"user_id": LOCAL_VIEWER["id"], "is_active": None, "role": None, "archived": False},
    ]
    first, second = _rows(audit_rows, f"/api/auth/users/{LOCAL_VIEWER['id']}")
    assert first.request_meta["account_change"] == {
        "user_id": LOCAL_VIEWER["id"],
        "email": LOCAL_VIEWER["email"],
        "is_active": {"from": True, "to": False},
        "archived": {"from": False, "to": True},
    }
    # Restored, it stays inactive: activating it is a change of its own.
    assert second.request_meta["account_change"] == {
        "user_id": LOCAL_VIEWER["id"],
        "email": LOCAL_VIEWER["email"],
        "archived": {"from": True, "to": False},
    }
    assert store.by_id(LOCAL_VIEWER["id"])["is_active"] is False


@pytest.mark.parametrize(
    ("target", "body", "status", "detail"),
    [
        pytest.param(LOCAL_ADMIN, {"archived": True}, 403, auth_router.OWN_ARCHIVE_DETAIL, id="your-own-account"),
        pytest.param(
            EMERGENCY, {"archived": True}, 403, auth_router.EMERGENCY_ARCHIVE_DETAIL, id="the-emergency-admin"
        ),
        pytest.param(
            LOCAL_VIEWER,
            {"archived": True, "is_active": True},
            400,
            auth_router.ARCHIVE_ACTIVE_DETAIL,
            id="archived-and-active-at-once",
        ),
    ],
)
def test_some_accounts_are_not_archived(
    client: TestClient, store: _Store, audit_rows: list[Any], target: dict, body: dict, status: int, detail: str
) -> None:
    response = client.patch(f"/api/auth/users/{target['id']}", json=body, headers=_bearer(LOCAL_ADMIN["email"]))

    assert (response.status_code, response.json()) == (status, {"detail": detail})
    assert store.updates == []
    (row,) = _rows(audit_rows, f"/api/auth/users/{target['id']}")
    assert "account_change" not in row.request_meta


def test_an_admin_deletes_an_account_and_the_audit_row_names_it(
    client: TestClient, store: _Store, audit_rows: list[Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=auth_router.__name__):
        response = client.delete(f"/api/auth/users/{LOCAL_VIEWER['id']}", headers=_bearer(LOCAL_ADMIN["email"]))

    assert response.status_code == 204, response.text
    assert store.deleted == [LOCAL_VIEWER["id"]]
    (row,) = _rows(audit_rows, f"/api/auth/users/{LOCAL_VIEWER['id']}")
    assert (row.user_email, row.status_code) == (LOCAL_ADMIN["email"], 204)
    assert row.request_meta["account_change"] == {
        "user_id": LOCAL_VIEWER["id"],
        "email": LOCAL_VIEWER["email"],
        "deleted": True,
    }
    # The log names the account's id only: who deleted it, and whom, are in the audit row.
    (line,) = [record.getMessage() for record in caplog.records if " deleted " in record.getMessage()]
    assert line == f"account {LOCAL_VIEWER['id']} deleted (who deleted it, and the account, in the request audit)"


@pytest.mark.parametrize(
    ("target", "caller", "status", "detail"),
    [
        pytest.param(LOCAL_ADMIN, LOCAL_ADMIN, 403, auth_router.OWN_DELETE_DETAIL, id="your-own-account"),
        pytest.param(EMERGENCY, LOCAL_ADMIN, 403, auth_router.EMERGENCY_ARCHIVE_DETAIL, id="the-emergency-admin"),
        pytest.param(LOCAL_VIEWER, LOCAL_VIEWER, 403, "Admin access required", id="by-a-viewer"),
        pytest.param({"id": str(uuid4())}, LOCAL_ADMIN, 404, "User not found", id="an-unknown-account"),
        pytest.param({"id": "not-a-uuid"}, LOCAL_ADMIN, 400, "Invalid user id", id="a-malformed-id"),
    ],
)
def test_some_accounts_are_not_deleted(
    client: TestClient, store: _Store, target: dict, caller: dict, status: int, detail: str
) -> None:
    response = client.delete(f"/api/auth/users/{target['id']}", headers=_bearer(caller["email"]))

    assert (response.status_code, response.json()) == (status, {"detail": detail})
    assert store.deleted == []


def test_the_emergency_admin_is_activated_and_other_accounts_deactivated(
    client: TestClient, store: _Store
) -> None:
    store.accounts[EMERGENCY["email"]]["is_active"] = False

    revived = client.patch(
        f"/api/auth/users/{EMERGENCY['id']}", json={"is_active": True}, headers=_bearer(LOCAL_ADMIN["email"])
    )
    other = client.patch(
        f"/api/auth/users/{LOCAL_VIEWER['id']}", json={"is_active": False}, headers=_bearer(LOCAL_ADMIN["email"])
    )

    assert revived.status_code == 200 and revived.json()["is_active"] is True
    assert other.status_code == 200 and other.json()["is_active"] is False
