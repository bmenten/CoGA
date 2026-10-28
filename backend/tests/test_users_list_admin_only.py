"""``GET /api/users``, the list of active accounts, answers administrators only (SEC-33).

The list gives the name and e-mail address of every active account. No screen reads it since
#909: the case pickers read the family's own list (``GET /families/{id}/assignable-users``)
and the *Users* page reads ``GET /api/auth/users``. Any signed-in user got it, an accepted
residual (the staff roster, security-posture.md §1) that the owner ended on 2026-10-28: the
route stays, for administrators alone (REQ-SEC-002).

The requests go through the real ``get_current_user`` (a local token, without Microsoft
sign-in) and ``get_current_admin_user``. Only the account lookup and the list's query are
replaced. A user who is no administrator gets the answer of the other admin routes (403), an
inactive administrator the answer every request of an inactive account gets (401), and the
list is not read for either. The accounts are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient
import pytest

from backend.app import dependencies
from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import auth as auth_router
from backend.app.routers import lookups as lookups_router
from backend.app.services.access_control import CurrentUser

PROJECT = "11111111-1111-1111-1111-111111111111"
ADMIN = "admin@example.org"
SUPERUSER = "superuser@example.org"
ANALYST = "analyst@example.org"
READER = "reader@example.org"
OUTSIDER = "outsider@example.org"
INACTIVE_ADMIN = "inactive-admin@example.org"

# The list as the query gives it: synthetic accounts.
_LISTED = [
    {"id": "u1", "username": "ann", "email": "ann@example.org", "first_name": "Ann", "last_name": "Lee"},
    {"id": "u2", "username": "bob", "email": "bob@example.org", "first_name": "Bob", "last_name": "Ng"},
]


def _user(email: str, role: str, *, active: bool = True, readers: bool = False) -> CurrentUser:
    projects = [PROJECT] if email in (ANALYST, READER) else []
    return CurrentUser(
        id=f"user-{email.split('@')[0]}",
        username=email,
        email=email,
        role=role,
        is_active=active,
        metadata_project_ids=projects,
        reader_project_ids=projects if readers else [],
        created_at=datetime.now(timezone.utc),
    )


_USERS = {
    ADMIN: _user(ADMIN, "admin"),
    SUPERUSER: _user(SUPERUSER, "superuser"),
    # A viewer is an analyst or a reader in each of their projects; one in no project at all.
    ANALYST: _user(ANALYST, "viewer"),
    READER: _user(READER, "viewer", readers=True),
    OUTSIDER: _user(OUTSIDER, "viewer"),
    INACTIVE_ADMIN: _user(INACTIVE_ADMIN, "admin", active=False),
}


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def reads(monkeypatch: pytest.MonkeyPatch):
    """The app without Microsoft sign-in, signing its callers in with the real
    ``get_current_user``; yields a client, the list's reads (one per call) and the request
    audit rows, kept instead of written."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    # Made at run time: the repository holds no key-shaped literal.
    monkeypatch.setattr(settings, "secret_key", "k" * 64)
    monkeypatch.setattr(settings, "azure_tenant_id", None)
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.setattr(settings, "azure_api_scope", None)
    monkeypatch.setattr(settings, "azure_admin_override", False)
    calls: list[str] = []
    audit_rows: list[Any] = []

    async def keep(payload: Any) -> None:
        audit_rows.append(payload)

    async def user_by_email(_session: Any, email: str) -> CurrentUser | None:
        return _USERS.get(email)

    async def listed(_session: Any) -> list[dict[str, Any]]:
        calls.append("/api/users")
        return _LISTED

    async def accounts(_session: Any) -> list[dict[str, Any]]:
        calls.append("/api/auth/users")
        return []

    async def override_get_postgres_session():
        yield _FakeSession()

    monkeypatch.setattr(dependencies, "get_current_user_by_email", user_by_email)
    monkeypatch.setattr(lookups_router, "list_assignable_users", listed)
    monkeypatch.setattr(auth_router, "list_user_accounts", accounts)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    try:
        with TestClient(app) as client:
            yield client, calls, audit_rows
    finally:
        app.dependency_overrides = original_overrides


def _get(client: TestClient, path: str, email: str):
    token = dependencies.create_access_token({"sub": email})
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.parametrize("email", [ANALYST, READER, OUTSIDER], ids=["analyst", "reader", "in-no-project"])
def test_a_user_who_is_no_administrator_is_refused_the_account_list(reads, email: str) -> None:
    client, calls, audit_rows = reads

    response = _get(client, "/api/users", email)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "Admin access required"}
    # The answer of the other admin routes, the administrators' own account list among them.
    other = _get(client, "/api/auth/users", email)
    assert (other.status_code, other.json()) == (response.status_code, response.json())
    assert calls == []  # refused before the list is read
    # The refusal is in the request audit, under the caller's name.
    assert (audit_rows[0].path, audit_rows[0].status_code, audit_rows[0].user_email) == ("/api/users", 403, email)


@pytest.mark.parametrize("email", [ADMIN, SUPERUSER], ids=["admin", "superuser"])
def test_an_administrator_still_gets_the_account_list(reads, email: str) -> None:
    client, calls, _audit_rows = reads

    response = _get(client, "/api/users", email)

    assert response.status_code == 200, response.text
    assert response.json() == _LISTED
    assert calls == ["/api/users"]


def test_an_inactive_administrator_is_refused_the_account_list_as_on_the_other_admin_routes(reads) -> None:
    client, calls, _audit_rows = reads

    response = _get(client, "/api/users", INACTIVE_ADMIN)

    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Could not validate credentials"}
    assert response.headers["www-authenticate"] == "Bearer"
    other = _get(client, "/api/auth/users", INACTIVE_ADMIN)
    assert (other.status_code, other.json(), other.headers["www-authenticate"]) == (
        401,
        response.json(),
        "Bearer",
    )
    assert calls == []
