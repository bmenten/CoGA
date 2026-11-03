"""Admin-only routes refuse anyone else at the route (REQ-SEC-002).

``get_current_admin_user`` refuses a signed-in user who holds neither admin role with
``403 Admin access required``. Every route that names it among its dependencies is walked from
the app and asked by a viewer: each answers that 403 before it reads the database, so a guard
that runs after something else fails here. Every route under ``/api/admin`` is an admin's, and
the admin routes elsewhere that only read are listed, so a route that loses its admin guard
fails the walk's catalogue; the routes that change something are catalogued in
``test_project_access_levels.py``. The examples named are the panel and tag-definition
changes (admin enforced at the route, not just in the service), the import of the NIPT-M
pipeline's recurrent-artefact table (REQ-NIPT-016) and the replacement of a family's annotation
manifest.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import dependencies
from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.services.access_control import CurrentUser
from backend.tests._app_routes import dependency_calls, filled_path, route_operations

ADMIN_ONLY = (403, {"detail": "Admin access required"})
ADMIN_OPERATIONS = sorted(
    key
    for key, route in route_operations().items()
    if dependencies.get_current_admin_user in dependency_calls(route.dependant)
)
ADMIN_CHANGES = [key for key in ADMIN_OPERATIONS if key[0] != "GET"]
ADMIN_READS = [key for key in ADMIN_OPERATIONS if key[0] == "GET"]
# The admin routes outside /api/admin that only read. One that changes something is held by
# test_project_access_levels.py::test_every_change_route_is_classified.
ADMIN_READS_OUTSIDE_THE_ADMIN_PREFIX = {
    ("GET", "/api/assemblies/reference-import/assemblies"),
    ("GET", "/api/assemblies/reference-import/organisms"),
    ("GET", "/api/assemblies/reference-import/recent"),
    ("GET", "/api/auth/users"),
    ("GET", "/api/family-imports"),
    ("GET", "/api/family-imports/packages"),
    ("GET", "/api/family-imports/{job_id}"),
    ("GET", "/api/users"),
}


def _ids(operations: list[tuple[str, str]]) -> list[str]:
    return [f"{method} {path}" for method, path in operations]


class _FakeSession:
    """Records every statement: a request refused at the role check runs none."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: Any, *_args: Any, **_kwargs: Any) -> None:
        self.statements.append(str(statement))
        raise AssertionError("a viewer's request reached the database")

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

    session = _FakeSession()

    async def override_get_postgres_session():
        yield session

    async def override_get_current_user():
        return viewer

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as client:
        yield client, session
    app.dependency_overrides = original_overrides


def test_every_admin_route_is_walked_and_none_is_left_unguarded() -> None:
    # Every route under /api/admin is an admin's.
    under_the_prefix = {key for key in route_operations() if key[1].startswith("/api/admin/")}
    assert under_the_prefix and under_the_prefix <= set(ADMIN_OPERATIONS)
    # The others that only read are listed: one that lost its guard is missing here.
    elsewhere = {key for key in ADMIN_READS if not key[1].startswith("/api/admin/")}
    assert elsewhere == ADMIN_READS_OUTSIDE_THE_ADMIN_PREFIX
    assert {
        ("DELETE", "/api/panels/{panel_id}"),
        ("POST", "/api/panels/mendeliome/regenerate"),
        ("DELETE", "/api/families/{family_id}/small-variant-tags/{tag_key}"),
        ("POST", "/api/admin/nipt/artifacts/import"),
        ("PUT", "/api/families/{family_id}/annotation-manifest"),
    } <= set(ADMIN_CHANGES)
    # A floor, so a walk that found no route cannot pass.
    assert len(ADMIN_CHANGES) >= 55 and len(ADMIN_READS) >= 30


@pytest.mark.parametrize(("method", "path"), ADMIN_CHANGES, ids=_ids(ADMIN_CHANGES))
def test_admin_mutations_reject_a_viewer(viewer_client, method: str, path: str) -> None:
    client, session = viewer_client
    response = client.request(method, filled_path(path))
    assert (response.status_code, response.json()) == ADMIN_ONLY, f"{method} {path}"
    assert session.statements == []


@pytest.mark.parametrize(("method", "path"), ADMIN_READS, ids=_ids(ADMIN_READS))
def test_admin_reads_reject_a_viewer(viewer_client, method: str, path: str) -> None:
    client, session = viewer_client
    response = client.request(method, filled_path(path))
    assert (response.status_code, response.json()) == ADMIN_ONLY, f"{method} {path}"
    assert session.statements == []


def test_dead_sample_projects_route_is_removed(viewer_client) -> None:
    # The always-failing /admin/samples/{id}/projects route was removed entirely.
    client, _session = viewer_client
    response = client.put("/api/admin/samples/SAMPLE1/projects", json={"project_ids": []})
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
        return {"family_id": "FAM1", "source": "manual", "modules": []}

    monkeypatch.setattr(families_reports, "set_family_annotation_manifest", _set_manifest)
    return calls


def test_a_viewer_cannot_replace_a_family_annotation_manifest(viewer_client, monkeypatch) -> None:
    calls = _record_manifest_writes(monkeypatch)
    client, _session = viewer_client
    response = client.put(_MANIFEST_PATH, json=_MANIFEST_BODY)
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"
    assert calls == []  # refused before the manifest service is reached


@contextmanager
def _client_as(role: str, session=None):
    """A TestClient signed in with ``role``; the Postgres session is ``session``."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    user = CurrentUser(
        id="a1",
        username=f"{role}@example.com",
        email=f"{role}@example.com",
        role=role,
        created_at=datetime.now(timezone.utc),
    )

    async def override_get_postgres_session():
        yield session if session is not None else _FakeSession()

    async def override_get_current_user():
        return user

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides = original_overrides


@pytest.mark.parametrize("role", ["admin", "superuser"])
def test_an_admin_can_replace_a_family_annotation_manifest(monkeypatch, role) -> None:
    calls = _record_manifest_writes(monkeypatch)
    with _client_as(role) as client:
        response = client.put(_MANIFEST_PATH, json=_MANIFEST_BODY)
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["user"].role == role
    assert calls[0]["modules"] == _MANIFEST_BODY["modules"]
    assert "source" not in calls[0]  # the route never forwards a caller's source


class _RecordingSession(_FakeSession):
    """Records each statement with its parameters; commits are no-ops."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.executed.append((" ".join(str(statement).split()), dict(params or {})))

    async def commit(self) -> None:
        return None


def test_a_replacement_is_recorded_as_manual_whatever_source_the_caller_sends(monkeypatch) -> None:
    # A replacement through the API is by definition curated by hand. A caller-supplied
    # source must not let a hand-typed manifest pass as parsed from a VCF header (which
    # the next import would then overwrite, since only 'manual' is protected).
    import types

    from backend.app.services import annotation_manifest_service as ams

    events: list[dict] = []

    async def _context(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(
            family_uuid="u1", family_id="FAM1", assembly_id=None, assembly_name="GRCh38"
        )

    async def _no_row(session, family_uuid):
        return None

    async def _family(session, family_id, user):
        return types.SimpleNamespace(metadata={})

    async def _event(session, **kwargs):
        events.append(kwargs)

    async def _manifest(session, *, family_id, user, project_id=None):
        return {"family_id": "FAM1", "source": "manual", "modules": []}

    monkeypatch.setattr(ams, "build_family_metadata_context", _context)
    monkeypatch.setattr(ams, "_family_manifest_row", _no_row)
    monkeypatch.setattr(ams, "get_family_record", _family)
    monkeypatch.setattr(ams, "record_clinical_event", _event)
    monkeypatch.setattr(ams, "get_family_annotation_manifest", _manifest)

    session = _RecordingSession()
    with _client_as("admin", session) as client:
        response = client.put(
            _MANIFEST_PATH, json={**_MANIFEST_BODY, "source": "vcf_header"}
        )
    assert response.status_code == 200, response.text
    upserts = [p for sql, p in session.executed if "INSERT INTO family_annotation_manifest" in sql]
    assert len(upserts) == 1 and upserts[0]["source"] == "manual"
    assert events[0]["after"]["source"] == "manual"
