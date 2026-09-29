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


# --- replacing a family's annotation manifest is admin-only ---
# Every later sign-out freezes this manifest into the signed report as its provenance,
# so a family's viewers may read it but not replace it.

_MANIFEST_PATH = "/api/families/FAM1/annotation-manifest"
_MANIFEST_BODY = {"modules": {"vep": {"version": "112"}}}


def _record_manifest_writes(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    from backend.app.routers import families_reports

    calls: list[dict] = []

    async def _set_manifest(session, **kwargs):
        calls.append(kwargs)
        return {"family_id": "FAM1", "source": kwargs.get("source"), "modules": []}

    monkeypatch.setattr(families_reports, "set_family_annotation_manifest", _set_manifest)
    return calls


def test_a_viewer_cannot_replace_a_family_annotation_manifest(viewer_client, monkeypatch) -> None:
    calls = _record_manifest_writes(monkeypatch)
    response = viewer_client.put(_MANIFEST_PATH, json=_MANIFEST_BODY)
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"
    assert calls == []  # refused before the manifest service is reached


@pytest.mark.parametrize("role", ["admin", "superuser"])
def test_an_admin_can_replace_a_family_annotation_manifest(monkeypatch, role) -> None:
    calls = _record_manifest_writes(monkeypatch)
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    admin = CurrentUser(
        id="a1",
        username=f"{role}@example.com",
        email=f"{role}@example.com",
        role=role,
        created_at=datetime.now(timezone.utc),
    )

    async def override_get_postgres_session():
        yield _FakeSession()

    async def override_get_current_user():
        return admin

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app) as client:
            response = client.put(_MANIFEST_PATH, json=_MANIFEST_BODY)
    finally:
        app.dependency_overrides = original_overrides
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["user"].role == role
    assert calls[0]["modules"] == _MANIFEST_BODY["modules"]
