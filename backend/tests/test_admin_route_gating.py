from contextlib import contextmanager
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
        # The NIPT artifact list: importing the NIPT-M pipeline's recurrent-artefact table.
        ("post", "/api/admin/nipt/artifacts/import"),
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
        return {"family_id": "FAM1", "source": "manual", "modules": []}

    monkeypatch.setattr(families_reports, "set_family_annotation_manifest", _set_manifest)
    return calls


def test_a_viewer_cannot_replace_a_family_annotation_manifest(viewer_client, monkeypatch) -> None:
    calls = _record_manifest_writes(monkeypatch)
    response = viewer_client.put(_MANIFEST_PATH, json=_MANIFEST_BODY)
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
