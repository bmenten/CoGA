from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import families as families_router
from backend.app.routers import panels as panels_router
from backend.app.routers import projects as projects_router
from backend.app.services.access_control import CurrentUser


def _route_paths() -> set[str]:
    # Starlette 1.x wraps included-router routes in an opaque _IncludedRouter
    # instead of flattening them into app.routes, so enumerate via the OpenAPI
    # schema (a stable public interface) which exposes the full route paths.
    return set(app.openapi().get("paths", {}))


def test_application_routes_are_mounted_under_api_prefix() -> None:
    paths = _route_paths()

    assert "/api/projects/" in paths
    assert "/api/panels/" in paths
    assert "/api/families/" in paths
    assert "/api/auth/login" in paths
    assert "/api/admin/projects" in paths


def test_unprefixed_application_routes_are_not_registered() -> None:
    paths = _route_paths()

    assert "/projects/" not in paths
    assert "/panels/" not in paths
    assert "/families/" not in paths
    assert "/auth/login" not in paths
    assert "/admin/projects" not in paths


def test_api_collection_roots_accept_missing_trailing_slash_without_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    user = CurrentUser(
        id="user1",
        username="viewer@example.com",
        email="viewer@example.com",
        role="viewer",
        created_at=datetime.now(timezone.utc),
    )

    async def override_get_postgres_session():
        yield object()

    async def override_get_current_user():
        return user

    async def fake_empty_list(*args, **kwargs):
        return []

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[projects_router.get_current_user] = override_get_current_user
    app.dependency_overrides[panels_router.get_current_user] = override_get_current_user
    app.dependency_overrides[families_router.get_current_user] = override_get_current_user
    monkeypatch.setattr(projects_router, "list_project_dashboards", fake_empty_list)
    monkeypatch.setattr(panels_router, "list_panels_data", fake_empty_list)
    monkeypatch.setattr(families_router, "list_families_for_user", fake_empty_list)

    try:
        with TestClient(app) as client:
            for path in ("/api/projects", "/api/panels", "/api/families"):
                response = client.get(path, follow_redirects=False)

                assert response.status_code == 200
                assert response.json() == []
                assert "location" not in response.headers
    finally:
        app.dependency_overrides = original_overrides
