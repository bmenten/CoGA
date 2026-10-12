"""Sign-in tokens: the local HS256 JWT, and with Azure AD the admin-only local fallback.

REQ-SEC-003 (TF-09a): authenticate via JWT (HS256), with a local fallback restricted to
admins. Without Azure AD, ``get_current_user`` accepts the token the login issues
(``create_access_token``: HS256, signed with ``SECRET_KEY``, with an expiry) and refuses,
with a 401, a token signed with another key or another algorithm, an unsigned or expired
one, one that names no user, and one of an unknown or inactive user. With Azure AD
configured the bearer token must be an Azure token: a locally issued one is accepted only
with ``AZURE_ADMIN_OVERRIDE`` on, only for an admin or a superuser (``ADMIN_ROLES``), and
every such use is logged. The override does not touch Azure tokens.

The requests go through the real login route, token code and ``get_current_user``. Only
the user lookup, the login's database calls, the Azure signature check (its keys come from
Microsoft) and the audit write are replaced. Keys are made here and are no deployment's;
the accounts are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import uuid

from fastapi.testclient import TestClient
import jwt
import pytest

from backend.app import dependencies
from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import auth as auth_router
from backend.app.services.access_control import CurrentUser

# Long enough for HS512 too, so the token signed with another algorithm is made without a
# short-key warning and is refused for its algorithm alone.
_KEY = "k" * 64
_OTHER_KEY = "o" * 64

ADMIN = "admin@example.com"
SUPERUSER = "superuser@example.com"
VIEWER = "viewer@example.com"
INACTIVE_ADMIN = "inactive-admin@example.com"
_PASSWORD = "correct horse battery staple"


def _user(email: str, role: str, *, active: bool = True) -> CurrentUser:
    return CurrentUser(
        id=f"user-{email.split('@')[0]}",
        username=email,
        email=email,
        role=role,
        is_active=active,
        created_at=datetime.now(timezone.utc),
    )


_USERS = {
    ADMIN: _user(ADMIN, "admin"),
    SUPERUSER: _user(SUPERUSER, "superuser"),
    VIEWER: _user(VIEWER, "viewer"),
    INACTIVE_ADMIN: _user(INACTIVE_ADMIN, "admin", active=False),
}


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    """The app without Azure AD, signing its callers in with the real ``get_current_user``."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    monkeypatch.setattr(settings, "secret_key", _KEY)
    monkeypatch.setattr(settings, "azure_tenant_id", None)
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.setattr(settings, "azure_admin_override", False)

    async def user_by_email(_session: Any, email: str) -> CurrentUser | None:
        return _USERS.get(email)

    async def no_audit_write(_payload: Any) -> None:
        return None

    async def override_get_postgres_session():
        yield _FakeSession()

    monkeypatch.setattr(dependencies, "get_current_user_by_email", user_by_email)
    monkeypatch.setattr(request_logging, "write_audit_log_event", no_audit_write)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides = original_overrides


# The tokens the replaced Azure check accepts, with the claims it returns for each.
_AZURE_TOKENS: dict[str, dict[str, Any]] = {}


def _azure_token(**claims: Any) -> str:
    token = f"azure-{uuid.uuid4().hex}"
    _AZURE_TOKENS[token] = claims
    return token


@pytest.fixture()
def azure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Azure AD configured. Its signature check accepts the tokens ``_azure_token`` made and
    refuses any other, as Microsoft's keys refuse a locally signed token. Returns the
    (tenant, client) of every check."""
    monkeypatch.setattr(settings, "azure_tenant_id", "tenant-id")
    monkeypatch.setattr(settings, "azure_client_id", "client-id")
    checks: list[tuple[str, str]] = []

    def verify_azure_token(token: str, tenant_id: str, client_id: str) -> dict[str, Any]:
        checks.append((tenant_id, client_id))
        claims = _AZURE_TOKENS.get(token)
        if claims is None:
            raise jwt.InvalidSignatureError("Signature verification failed")
        return claims

    monkeypatch.setattr(dependencies, "verify_azure_token", verify_azure_token)
    return checks


def _local_token(
    email: str | None,
    *,
    key: str | None = _KEY,
    algorithm: str = "HS256",
    expires_in: timedelta = timedelta(minutes=5),
) -> str:
    payload: dict[str, Any] = {"exp": datetime.now(timezone.utc) + expires_in}
    if email is not None:
        payload["sub"] = email
    return jwt.encode(payload, key, algorithm=algorithm)


def _me(client: TestClient, token: str):
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


def _assert_refused(response) -> None:
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Could not validate credentials"}
    assert response.headers["www-authenticate"] == "Bearer"


def _override_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == dependencies.__name__
        and record.levelno == logging.WARNING
        and "azure_admin_override" in record.getMessage()
    ]


# --------------------------------------------------------------------------- #
# Without Azure AD: the local HS256 token
# --------------------------------------------------------------------------- #


def test_the_token_issued_at_sign_in_is_hs256_with_an_expiry_and_signs_its_user_in(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def not_throttled(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def account(_session: Any, email: str) -> dict[str, Any] | None:
        user = _USERS.get(email)
        if user is None:
            return None
        return {"email": user.email, "hashed_password": "stored-hash", "is_active": True, "role": user.role}

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(auth_router, "get_login_throttle_state", not_throttled)
    monkeypatch.setattr(auth_router, "get_auth_user_mapping_by_email", account)
    monkeypatch.setattr(auth_router, "record_failed_login", no_op)
    monkeypatch.setattr(auth_router, "clear_login_failures", no_op)
    monkeypatch.setattr(
        auth_router,
        "verify_password",
        lambda plain, hashed: plain == _PASSWORD and hashed == "stored-hash",
    )

    signed_in_at = datetime.now(timezone.utc)
    login = client.post("/api/auth/login", json={"email": VIEWER, "password": _PASSWORD})

    assert login.status_code == 200, login.text
    body = login.json()
    assert body["token_type"] == "bearer" and body["role"] == "viewer"
    token = body["access_token"]
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    claims = jwt.decode(token, _KEY, algorithms=["HS256"])
    assert claims["sub"] == VIEWER
    lifetime = datetime.fromtimestamp(claims["exp"], timezone.utc) - signed_in_at
    expected = timedelta(minutes=settings.access_token_expire_minutes)
    assert expected - timedelta(seconds=30) <= lifetime <= expected + timedelta(seconds=30)

    me = _me(client, token)

    assert me.status_code == 200, me.text
    assert (me.json()["email"], me.json()["role"]) == (VIEWER, "viewer")


@pytest.mark.parametrize(
    "make_token",
    [
        pytest.param(lambda: _local_token(VIEWER, key=_OTHER_KEY), id="signed-with-another-key"),
        pytest.param(lambda: _local_token(VIEWER, algorithm="HS512"), id="signed-with-another-algorithm"),
        pytest.param(lambda: _local_token(VIEWER, key=None, algorithm="none"), id="unsigned"),
        pytest.param(lambda: _local_token(VIEWER, expires_in=timedelta(seconds=-5)), id="expired"),
        pytest.param(lambda: _local_token(None), id="naming-no-user"),
        pytest.param(lambda: _local_token("nobody@example.com"), id="of-an-unknown-user"),
        pytest.param(lambda: _local_token(INACTIVE_ADMIN), id="of-an-inactive-user"),
        pytest.param(lambda: "not-a-token", id="not-a-token"),
    ],
)
def test_a_token_that_is_not_a_valid_local_token_is_refused(client: TestClient, make_token) -> None:
    _assert_refused(_me(client, make_token()))


def test_a_local_token_signs_in_whatever_the_role_without_azure(client: TestClient) -> None:
    # Without Azure AD the local token is the sign-in, for every role.
    for email, role in ((VIEWER, "viewer"), (SUPERUSER, "superuser"), (ADMIN, "admin")):
        response = _me(client, _local_token(email))
        assert response.status_code == 200, response.text
        assert (response.json()["email"], response.json()["role"]) == (email, role)


# --------------------------------------------------------------------------- #
# With Azure AD: Azure tokens, and the local fallback for admins only
# --------------------------------------------------------------------------- #


def test_with_azure_an_azure_token_signs_in_the_user_it_names(
    client: TestClient, azure: list[tuple[str, str]]
) -> None:
    # The account is the token's preferred_username, else its email.
    by_username = _me(client, _azure_token(preferred_username=VIEWER, email=ADMIN))
    by_email = _me(client, _azure_token(email=SUPERUSER))
    unknown = _me(client, _azure_token(preferred_username="nobody@example.com"))
    inactive = _me(client, _azure_token(preferred_username=INACTIVE_ADMIN))
    no_account = _me(client, _azure_token(name="Someone"))

    assert by_username.status_code == 200, by_username.text
    assert by_username.json()["email"] == VIEWER
    assert by_email.status_code == 200, by_email.text
    assert by_email.json()["email"] == SUPERUSER
    for refused in (unknown, inactive, no_account):
        _assert_refused(refused)
    # Every token was checked against the configured tenant and application.
    assert azure and set(azure) == {("tenant-id", "client-id")}


@pytest.mark.parametrize("email", [ADMIN, SUPERUSER, VIEWER])
def test_with_azure_and_no_override_a_local_token_is_refused_even_for_an_admin(
    client: TestClient, azure: list[tuple[str, str]], caplog: pytest.LogCaptureFixture, email: str
) -> None:
    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        _assert_refused(_me(client, _local_token(email)))
    assert _override_warnings(caplog) == []


@pytest.mark.parametrize(("email", "role"), [(ADMIN, "admin"), (SUPERUSER, "superuser")])
def test_the_override_admits_an_admins_local_token_and_logs_each_use(
    client: TestClient,
    azure: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    email: str,
    role: str,
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        first = _me(client, _local_token(email))
        second = _me(client, _local_token(email))

    for response in (first, second):
        assert response.status_code == 200, response.text
        assert (response.json()["email"], response.json()["role"]) == (email, role)
    warnings = _override_warnings(caplog)
    assert len(warnings) == 2
    assert all(email in warning and f"role={role}" in warning for warning in warnings)


@pytest.mark.parametrize(
    "make_token",
    [
        pytest.param(lambda: _local_token(VIEWER), id="a-viewer"),
        pytest.param(lambda: _local_token(INACTIVE_ADMIN), id="an-inactive-admin"),
        pytest.param(lambda: _local_token("nobody@example.com"), id="an-unknown-user"),
        pytest.param(lambda: _local_token(ADMIN, key=_OTHER_KEY), id="an-admin-signed-with-another-key"),
        pytest.param(lambda: _local_token(ADMIN, algorithm="HS512"), id="an-admin-another-algorithm"),
        pytest.param(lambda: _local_token(ADMIN, expires_in=timedelta(seconds=-5)), id="an-admins-expired"),
        pytest.param(lambda: _local_token(None), id="naming-no-user"),
    ],
)
def test_the_override_refuses_every_other_local_token(
    client: TestClient,
    azure: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    make_token,
) -> None:
    monkeypatch.setattr(settings, "azure_admin_override", True)

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        _assert_refused(_me(client, make_token()))

    assert _override_warnings(caplog) == []


def test_the_override_leaves_azure_tokens_alone(
    client: TestClient,
    azure: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The admin-only rule is the fallback's: an Azure token signs in a viewer as before,
    # and is not logged as a use of the override.
    monkeypatch.setattr(settings, "azure_admin_override", True)

    with caplog.at_level(logging.WARNING, logger=dependencies.__name__):
        response = _me(client, _azure_token(preferred_username=VIEWER))

    assert response.status_code == 200, response.text
    assert response.json()["email"] == VIEWER
    assert _override_warnings(caplog) == []
