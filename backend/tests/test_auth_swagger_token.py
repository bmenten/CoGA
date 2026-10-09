from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import auth as auth_router


@dataclass
class _FakeSession:
    commits: int = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture()
def swagger_token_client(monkeypatch: pytest.MonkeyPatch):
    fake_session = _FakeSession()
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def override_get_postgres_session():
        yield fake_session

    async def fake_get_login_throttle_state(*args, **kwargs):
        return None

    async def fake_get_auth_user_mapping_by_email(*args, **kwargs):
        return {
            "email": "viewer@example.com",
            "hashed_password": "hashed-password",
            "is_active": True,
            "role": "viewer",
        }

    async def fake_record_failed_login(*args, **kwargs):
        return None

    async def fake_clear_login_failures(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_router, "get_login_throttle_state", fake_get_login_throttle_state)
    monkeypatch.setattr(auth_router, "get_auth_user_mapping_by_email", fake_get_auth_user_mapping_by_email)
    monkeypatch.setattr(auth_router, "record_failed_login", fake_record_failed_login)
    monkeypatch.setattr(auth_router, "clear_login_failures", fake_clear_login_failures)
    monkeypatch.setattr(auth_router, "verify_password", lambda plain, hashed: True)
    monkeypatch.setattr(
        auth_router,
        "create_access_token",
        lambda data: "signed-token",
    )

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session

    with TestClient(app) as client:
        yield client, fake_session

    app.dependency_overrides = original_overrides


def test_swagger_token_endpoint_issues_a_bearer_token(swagger_token_client) -> None:
    client, fake_session = swagger_token_client

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
    assert fake_session.commits == 1


def test_openapi_password_flow_points_to_auth_token(swagger_token_client) -> None:
    client, _ = swagger_token_client

    schema = client.get("/openapi.json").json()
    token_url = schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]["flows"][
        "password"
    ]["tokenUrl"]

    assert token_url == "/api/auth/token"
