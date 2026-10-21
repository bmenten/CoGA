"""The sign-in routes with Microsoft sign-in configured, and an administrator's role changes (SEC-4).

``GET /api/auth/config`` tells the browser how users sign in: with Microsoft (the application
ID, the directory's authority and CoGA's API scope, none of it secret) or with a password.

With Azure AD configured the password sign-in (``/auth/login`` and the Swagger form at
``/auth/token``) is switched off, before any account is looked up, unless
``AZURE_ADMIN_OVERRIDE`` is on; then it serves the emergency administrator alone (the active
local account ``ADMIN_USERNAME`` names, with an administrator's role), whose sign-in is logged
and flagged in the request audit (``request_meta.emergency_sign_in``). Any other account's
right password is refused with 403 and counts as a failed attempt. There is no sign-up:
accounts come from the first Microsoft sign-in.

``PATCH /api/auth/users/{id}`` sets an account's global role as well as its activation. An
administrator cannot change their own role, nor the emergency administrator's, and cannot
deactivate the emergency administrator; a viewer cannot call it. What changed, each field from and to, is in the request's audit row
(``request_meta.account_change``) (REQ-SEC-002, REQ-SEC-004).

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

    async def update(_session: Any, *, user_id: str, is_active: bool | None, role: str | None = None):
        account = store.by_id(user_id)
        assert account is not None
        changes: dict[str, dict[str, Any]] = {}
        if is_active is not None and is_active != account["is_active"]:
            changes["is_active"] = {"from": account["is_active"], "to": is_active}
            account["is_active"] = is_active
        if role is not None and role != account["role"]:
            changes["role"] = {"from": account["role"], "to": role}
            account["role"] = role
        store.updates.append({"user_id": user_id, "is_active": is_active, "role": role})
        return (
            UserRead(
                id=account["id"],
                username=account["username"],
                email=account["email"],
                is_active=account["is_active"],
                role=account["role"],
                sign_in="microsoft" if account["azure_object_id"] else "password",
                created_at=account["created_at"],
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
    monkeypatch.setattr(auth_router, "update_user_account", update)
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
    token = jwt.encode(
        {"sub": email, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, _LOCAL_SIGNER, algorithm="HS256"
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
    assert response.json() == {"microsoft": None}


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
        }
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
        # SEC-32: an administrator who deactivated their own account locked themselves out.
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
