from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.services.access_control import CurrentUser


class _FakeSession:
    async def rollback(self) -> None:
        return None


@pytest.fixture()
def viewer_client(monkeypatch: pytest.MonkeyPatch):
    """A signed-in non-admin (viewer). The real ``get_current_admin_user`` runs on
    top of the overridden ``get_current_user``, so admin-gated routes return 403."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    viewer = CurrentUser(
        id="v1",
        username="viewer@example.com",
        email="viewer@example.com",
        role="viewer",
        created_at=datetime.now(timezone.utc),
    )

    async def override_get_postgres_session():
        yield _FakeSession()

    async def override_get_current_user():
        return viewer

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as client:
        yield client
    app.dependency_overrides = original_overrides


@pytest.mark.parametrize(
    "method,path",
    [
        # Panel mutations (admin enforced at the route, not just in the service).
        ("delete", "/api/panels/PANEL1"),
        ("post", "/api/panels/mendeliome/regenerate"),
        # Small-variant tag-definition mutations.
        ("delete", "/api/families/FAM1/small-variant-tags/tagkey"),
    ],
)
def test_admin_mutations_reject_a_viewer(viewer_client, method, path) -> None:
    response = getattr(viewer_client, method)(path)
    assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


def test_dead_sample_projects_route_is_removed(viewer_client) -> None:
    # The always-failing /admin/samples/{id}/projects route was removed entirely.
    response = viewer_client.put(
        "/api/admin/samples/SAMPLE1/projects", json={"project_ids": []}
    )
    assert response.status_code == 404
