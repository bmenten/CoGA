from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import auth as auth_router
from backend.app.schemas import UserRead
from backend.app.services.auth_rate_limit_pg import LoginThrottleState


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def signup_client(monkeypatch: pytest.MonkeyPatch):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def override_get_postgres_session():
        yield _FakeSession()

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    with TestClient(app) as client:
        yield client, monkeypatch
    app.dependency_overrides = original_overrides


def _payload() -> dict:
    return {
        "email": "new@example.com",
        "password": "a-strong-password",
        "first_name": "New",
        "last_name": "User",
        "affiliation": "Lab",
    }


def test_signup_returns_429_when_ip_throttled(signup_client) -> None:
    client, monkeypatch = signup_client
    recorded = {"called": False}

    async def blocked(session, *, remote_ip, now=None):
        return LoginThrottleState(
            retry_after_seconds=42, blocked_until=datetime.now(timezone.utc)
        )

    async def record(session, *, remote_ip, now=None):
        recorded["called"] = True
        return None

    monkeypatch.setattr(auth_router, "get_signup_throttle_state", blocked)
    monkeypatch.setattr(auth_router, "record_signup_attempt", record)

    response = client.post("/api/auth/signup", json=_payload())

    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "42"
    # Blocked before any attempt is recorded or any bcrypt hash / user create runs.
    assert recorded["called"] is False


def test_signup_records_attempt_and_creates_when_not_throttled(signup_client) -> None:
    client, monkeypatch = signup_client
    recorded = {"ip": "unset", "notified": None}

    async def allowed(session, *, remote_ip, now=None):
        return None

    async def record(session, *, remote_ip, now=None):
        recorded["ip"] = remote_ip
        return None

    async def fake_create(session, **kwargs):
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

    monkeypatch.setattr(auth_router, "get_signup_throttle_state", allowed)
    monkeypatch.setattr(auth_router, "record_signup_attempt", record)
    monkeypatch.setattr(auth_router, "create_user_account", fake_create)
    monkeypatch.setattr(
        auth_router, "notify_admin", lambda email: recorded.__setitem__("notified", email)
    )

    response = client.post("/api/auth/signup", json=_payload())

    # Generic acknowledgement (202) rather than the created user record: a fresh
    # signup must be indistinguishable from a duplicate so accounts can't be
    # enumerated. The email/is_active fields are no longer returned.
    assert response.status_code == 202
    body = response.json()
    assert "email" not in body
    assert body["detail"]
    # A genuinely new account still triggers the admin notification.
    assert recorded["notified"] == "new@example.com"
    # The attempt is recorded against the client IP (counts toward the IP rate).
    assert recorded["ip"] is not None


def test_signup_duplicate_email_is_indistinguishable(signup_client) -> None:
    client, monkeypatch = signup_client
    recorded = {"notified": None}

    async def allowed(session, *, remote_ip, now=None):
        return None

    async def record(session, *, remote_ip, now=None):
        return None

    async def fake_create_existing(session, **kwargs):
        # create_user_account returns None when the email is already registered.
        return None

    monkeypatch.setattr(auth_router, "get_signup_throttle_state", allowed)
    monkeypatch.setattr(auth_router, "record_signup_attempt", record)
    monkeypatch.setattr(auth_router, "create_user_account", fake_create_existing)
    monkeypatch.setattr(
        auth_router, "notify_admin", lambda email: recorded.__setitem__("notified", email)
    )

    response = client.post("/api/auth/signup", json=_payload())

    # Same 202 + generic body as a fresh signup, and no admin notification fires —
    # nothing in the response reveals that the account already existed.
    assert response.status_code == 202
    body = response.json()
    assert "email" not in body
    assert body["detail"]
    assert recorded["notified"] is None


@pytest.mark.parametrize("password", ["", "short", "fourteen-chars"])
def test_signup_refuses_a_password_under_15_characters(signup_client, password: str) -> None:
    # Sign-up accepted any string, the empty one included; a local account's password is
    # its only factor (NIST SP 800-63B-4: at least 15 characters).
    client, monkeypatch = signup_client
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
