"""The password sign-in and sign-up routes, without Microsoft sign-in (REQ-SEC-003, REQ-SEC-005).

The sign-in (``/auth/login``, and the Swagger form at ``/auth/token``) and the sign-up
(``/auth/signup``) are the routes anyone may call before signing in, so each is throttled and
tells a caller nothing about which accounts exist. With Microsoft sign-in configured both are
switched off but for the emergency administrator: ``test_microsoft_sign_in_routes.py``. The
tokens the sign-in issues are checked in ``test_auth_token_verification.py``, the throttle's
bookkeeping in ``test_auth_rate_limit_pg.py``.

Login must not leak account existence through response timing. The no-such-account branch
used to return before calling ``verify_password``, so only real accounts paid the bcrypt cost,
a measurable enumeration oracle. The handler now runs a throwaway verify against a constant
hash on that branch. A throttled sign-in is refused (429, with ``Retry-After``) before any
account is looked up; an inactive account's right password is refused and counts as a failed
attempt. The Swagger form issues the same bearer token, and the OpenAPI password flow points
at it.

The sign-up is rate-limited per IP (429 and ``Retry-After`` before any hash or account is
made), and a new and an already-registered address get the same generic 202, so the sign-up
cannot enumerate accounts. A password under 15 characters is refused with 422 before any of
that (#582).

The requests go through the real routes; the account store, the throttle and the password
check are replaced. The accounts are synthetic.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import auth as auth_router
from backend.app.schemas import UserRead
from backend.app.services.auth_rate_limit_pg import LoginThrottleState


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def password_client(monkeypatch: pytest.MonkeyPatch):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    session = _FakeSession()

    async def override_get_postgres_session():
        yield session

    async def no_audit_row(_payload) -> None:
        return None

    monkeypatch.setattr(request_logging, "write_audit_log_event", no_audit_row)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    with TestClient(app) as client:
        client.session = session  # type: ignore[attr-defined]
        yield client, monkeypatch
    app.dependency_overrides = original_overrides


async def _not_throttled(session, *, email, remote_ip, now=None):
    return None


def _sign_in(client: TestClient, route: str, email: str, password: str):
    if route == "login":
        return client.post("/api/auth/login", json={"email": email, "password": password})
    return client.post("/api/auth/token", data={"username": email, "password": password})


# --------------------------------------------------------------------------- #
# The sign-in
# --------------------------------------------------------------------------- #


def test_login_runs_dummy_verify_when_account_missing(password_client) -> None:
    client, monkeypatch = password_client
    calls = {"verify": 0}

    async def no_such_user(session, email):
        return None

    async def record(session, *, email, remote_ip, now=None):
        return None

    def counting_verify(password, hashed):
        calls["verify"] += 1
        return False

    monkeypatch.setattr(auth_router, "get_login_throttle_state", _not_throttled)
    monkeypatch.setattr(auth_router, "get_auth_user_mapping_by_email", no_such_user)
    monkeypatch.setattr(auth_router, "record_failed_login", record)
    monkeypatch.setattr(auth_router, "verify_password", counting_verify)

    response = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "whatever-password"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Incorrect email or password"
    # The dummy verify runs even though the account doesn't exist, so the response
    # time doesn't distinguish "no such account" from "wrong password".
    assert calls["verify"] == 1


def test_dummy_login_hash_is_a_valid_bcrypt_hash() -> None:
    from backend.app.dependencies import verify_password

    # The equalizer hash must actually be verifiable (a malformed constant would
    # make the dummy verify cheap/raise and defeat the point).
    assert auth_router._DUMMY_LOGIN_PASSWORD_HASH.startswith("$2")
    assert verify_password("coga-login-timing-equalizer", auth_router._DUMMY_LOGIN_PASSWORD_HASH)
    assert not verify_password("wrong", auth_router._DUMMY_LOGIN_PASSWORD_HASH)


@pytest.mark.parametrize("route", ["login", "token"])
def test_a_throttled_sign_in_is_refused_before_the_account_is_looked_up(password_client, route: str) -> None:
    client, monkeypatch = password_client
    touched: list[str] = []

    async def blocked(session, *, email, remote_ip, now=None):
        return LoginThrottleState(retry_after_seconds=17, blocked_until=datetime.now(timezone.utc))

    async def lookup(session, email):
        touched.append("lookup")

    async def record(session, *, email, remote_ip, now=None):
        touched.append("record")

    def verify(password, hashed):
        touched.append("verify")
        return True

    monkeypatch.setattr(auth_router, "get_login_throttle_state", blocked)
    monkeypatch.setattr(auth_router, "get_auth_user_mapping_by_email", lookup)
    monkeypatch.setattr(auth_router, "record_failed_login", record)
    monkeypatch.setattr(auth_router, "verify_password", verify)

    response = _sign_in(client, route, "user@example.com", "a-right-password")

    assert (response.status_code, response.json()) == (429, {"detail": "Too many login attempts. Try again later."})
    assert response.headers["Retry-After"] == "17"
    # Refused before the account is looked up or its password checked, and not counted again.
    assert touched == []


@pytest.mark.parametrize("route", ["login", "token"])
def test_an_inactive_accounts_right_password_is_refused_and_counted(password_client, route: str) -> None:
    client, monkeypatch = password_client
    failed: list[str] = []
    cleared: list[str] = []

    async def inactive(session, email):
        return {"id": "u-1", "email": email, "hashed_password": "stored-hash", "is_active": False, "role": "admin"}

    async def record(session, *, email, remote_ip, now=None):
        failed.append(email)

    async def clear(session, *, email, remote_ip, now=None):
        cleared.append(email)

    monkeypatch.setattr(auth_router, "get_login_throttle_state", _not_throttled)
    monkeypatch.setattr(auth_router, "get_auth_user_mapping_by_email", inactive)
    monkeypatch.setattr(auth_router, "record_failed_login", record)
    monkeypatch.setattr(auth_router, "clear_login_failures", clear)
    monkeypatch.setattr(auth_router, "verify_password", lambda plain, hashed: hashed == "stored-hash")

    response = _sign_in(client, route, "inactive@example.com", "the-right-password")

    assert (response.status_code, response.json()) == (403, {"detail": "User not active"})
    # No token, and the attempt counts towards the throttle like a wrong password.
    assert (failed, cleared) == (["inactive@example.com"], [])
    assert client.session.commits == 1


def test_swagger_token_endpoint_issues_a_bearer_token(password_client) -> None:
    client, monkeypatch = password_client

    async def an_account(*args, **kwargs):
        return {
            "email": "viewer@example.com",
            "hashed_password": "hashed-password",
            "is_active": True,
            "role": "viewer",
        }

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_router, "get_login_throttle_state", no_op)
    monkeypatch.setattr(auth_router, "get_auth_user_mapping_by_email", an_account)
    monkeypatch.setattr(auth_router, "record_failed_login", no_op)
    monkeypatch.setattr(auth_router, "clear_login_failures", no_op)
    monkeypatch.setattr(auth_router, "verify_password", lambda plain, hashed: True)
    monkeypatch.setattr(auth_router, "create_access_token", lambda data: "signed-token")

    response = client.post(
        "/api/auth/token",
        data={"username": "viewer@example.com", "password": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "signed-token",
        "token_type": "bearer",
        "role": "viewer",
    }
    assert client.session.commits == 1


def test_openapi_password_flow_points_to_auth_token(password_client) -> None:
    client, _ = password_client

    schema = client.get("/openapi.json").json()
    token_url = schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]["flows"][
        "password"
    ]["tokenUrl"]

    assert token_url == "/api/auth/token"


# --------------------------------------------------------------------------- #
# The sign-up
# --------------------------------------------------------------------------- #


def _payload() -> dict:
    return {
        "email": "new@example.com",
        "password": "a-strong-password",
        "first_name": "New",
        "last_name": "User",
        "affiliation": "Lab",
    }


def test_signup_returns_429_when_ip_throttled(password_client) -> None:
    client, monkeypatch = password_client
    touched: list[str] = []

    async def blocked(session, *, remote_ip, now=None):
        return LoginThrottleState(
            retry_after_seconds=42, blocked_until=datetime.now(timezone.utc)
        )

    async def record(session, *, remote_ip, now=None):
        touched.append("record")
        return None

    async def create(session, **kwargs):
        touched.append("create")
        return None

    monkeypatch.setattr(auth_router, "get_signup_throttle_state", blocked)
    monkeypatch.setattr(auth_router, "record_signup_attempt", record)
    monkeypatch.setattr(auth_router, "create_user_account", create)

    response = client.post("/api/auth/signup", json=_payload())

    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "42"
    # Blocked before any attempt is recorded or any bcrypt hash / user create runs.
    assert touched == []


def _signed_up(client: TestClient, monkeypatch: pytest.MonkeyPatch, *, existing: bool) -> tuple:
    """Sign up a new address, or one already registered; what the client got, the IP the
    attempt was recorded against and the address the administrator was told of."""
    recorded = {"ip": "unset", "notified": None}

    async def record(session, *, remote_ip, now=None):
        recorded["ip"] = remote_ip
        return None

    async def create(session, **kwargs):
        # create_user_account returns None when the email is already registered.
        if existing:
            return None
        return UserRead(
            id="u-1",
            username=kwargs["email"],
            email=kwargs["email"],
            first_name=kwargs["first_name"],
            last_name=kwargs["last_name"],
            affiliation=kwargs["affiliation"],
            is_active=False,
            role="viewer",
            projects=[],
            created_at=datetime.now(timezone.utc),
        )

    async def allowed(session, *, remote_ip, now=None):
        return None

    monkeypatch.setattr(auth_router, "get_signup_throttle_state", allowed)
    monkeypatch.setattr(auth_router, "record_signup_attempt", record)
    monkeypatch.setattr(auth_router, "create_user_account", create)
    async def administrators(_session):
        return ["the.admin@example.org"]

    def notify(account, recipients):
        assert (account.sign_in, recipients) == ("password", ["the.admin@example.org"])
        recorded["notified"] = account.email
        return True

    monkeypatch.setattr(auth_router, "administrator_addresses", administrators)
    monkeypatch.setattr(auth_router, "notify_account_request", notify)

    response = client.post("/api/auth/signup", json=_payload())
    return response, recorded["ip"], recorded["notified"]


def test_signup_records_attempt_and_creates_when_not_throttled(password_client) -> None:
    client, monkeypatch = password_client

    response, ip, notified = _signed_up(client, monkeypatch, existing=False)

    # Generic acknowledgement (202) rather than the created user record: a fresh
    # signup must be indistinguishable from a duplicate so accounts can't be
    # enumerated. The email/is_active fields are no longer returned.
    assert response.status_code == 202
    body = response.json()
    assert "email" not in body
    assert body["detail"]
    # A genuinely new account still triggers the admin notification.
    assert notified == "new@example.com"
    # The attempt is recorded against the client IP (counts toward the IP rate).
    assert ip is not None and ip != "unset"


def test_signup_duplicate_email_is_indistinguishable(password_client) -> None:
    client, monkeypatch = password_client
    fresh, _ip, _notified = _signed_up(client, monkeypatch, existing=False)

    duplicate, ip, notified = _signed_up(client, monkeypatch, existing=True)

    # Same 202 + generic body as a fresh signup, and no admin notification fires —
    # nothing in the response reveals that the account already existed.
    assert duplicate.status_code == fresh.status_code == 202
    assert duplicate.json() == fresh.json()
    assert "email" not in duplicate.json()
    assert notified is None
    # The duplicate counts toward the IP's rate too.
    assert ip is not None and ip != "unset"


@pytest.mark.parametrize("password", ["", "short", "fourteen-chars"])
def test_signup_refuses_a_password_under_15_characters(password_client, password: str) -> None:
    # Sign-up accepted any string, the empty one included; a local account's password is
    # its only factor (NIST SP 800-63B-4: at least 15 characters).
    client, monkeypatch = password_client
    touched = {"throttle": False, "create": False}

    async def throttle(session, *, remote_ip, now=None):
        touched["throttle"] = True
        return None

    async def create(session, **kwargs):
        touched["create"] = True
        return None

    monkeypatch.setattr(auth_router, "get_signup_throttle_state", throttle)
    monkeypatch.setattr(auth_router, "create_user_account", create)

    response = client.post("/api/auth/signup", json={**_payload(), "password": password})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "password"]
    # Refused before any throttle bookkeeping, hashing or account creation.
    assert touched == {"throttle": False, "create": False}


def test_signup_accepts_a_15_character_password() -> None:
    from backend.app.schemas.auth import SIGNUP_PASSWORD_MIN_LENGTH, UserCreate

    assert SIGNUP_PASSWORD_MIN_LENGTH == 15
    created = UserCreate(**{**_payload(), "password": "x" * 15})
    assert created.password == "x" * 15
