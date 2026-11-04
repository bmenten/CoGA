"""Every route needs a signed-in user, but the few a browser calls before anyone signs in.

``get_current_user`` signs the caller in (``get_current_admin_user`` runs on top of it), and a
route needs it among its dependencies to refuse a request without a valid token. The routes
that do without are the sign-in and sign-up themselves and the sign-in configuration, the
health, readiness and version checks, the metrics endpoint (outside ``/api``, with a token of
its own) and the QC-report download, which a browser navigation opens and which checks its own
signed link first (``test_request_control_characters.py``). Every other route is walked from
the app and asked without a token: each answers 401 before it reads the database, so a route
that loses its sign-in, or reads anything before it, fails here.

The reference routers need a signed-in user too (#522), the species list included: reference
data (genes, assemblies, the CNV catalogue) is not project-scoped and not PHI, but it must not
be scraped, or used to load the database, anonymously. ``/assemblies/reference-status``
embeds import provenance (the source and the operator's e-mail address who performed it): it is
refused without a token, and a plain signed-in viewer reads it.
"""

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import dependencies
from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.routers import assemblies as assemblies_router
from backend.app.services.access_control import CurrentUser
from backend.tests._app_routes import dependency_calls, filled_path, route_operations

# The operations that answer without a signed-in user.
PUBLIC_OPERATIONS = {
    ("GET", "/api/auth/config"),
    ("POST", "/api/auth/cas/session"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/token"),
    ("GET", "/api/health"),
    ("GET", "/api/health/ready"),
    ("GET", "/api/version"),
    ("GET", "/metrics"),
    # Checks its own signed link first (403), see test_request_control_characters.py.
    ("GET", "/api/families/{family_id}/qc-report/{sample_id}"),
}
SIGNED_IN_OPERATIONS = sorted(
    key
    for key, route in route_operations().items()
    if dependencies.get_current_user in dependency_calls(route.dependant)
)
# The reference and genomic-data endpoints, read by every page that draws a genome.
REFERENCE_DATA_OPERATIONS = {
    ("GET", "/api/genes/{assembly}/{chrom}"),
    ("GET", "/api/chromosomes/{assembly}"),
    ("GET", "/api/chromosomes/{assembly}/details"),
    ("GET", "/api/chromosomes/{assembly}/{chrom}"),
    ("GET", "/api/cnvs/{assembly}/catalog"),
    ("GET", "/api/cnvs/{assembly}/{chrom}"),
    ("GET", "/api/dgv/{assembly}/{chrom}"),
    ("GET", "/api/blacklist/{assembly}/{chrom}"),
    ("GET", "/api/segmental-duplications/{assembly}/{chrom}"),
    ("GET", "/api/assemblies/"),
    ("GET", "/api/assemblies/reference-status"),
    ("GET", "/api/species/"),
}


class _FakeSession:
    """Records every statement: a request refused at sign-in runs none."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: Any, *_args: Any, **_kwargs: Any) -> None:
        self.statements.append(str(statement))
        raise AssertionError("a request without a token reached the database")

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def reference_status_client(monkeypatch: pytest.MonkeyPatch):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    session = _FakeSession()

    async def override_get_postgres_session():
        yield session

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session

    with TestClient(app) as client:
        yield client, monkeypatch, session

    app.dependency_overrides = original_overrides


def test_every_route_but_the_public_ones_signs_its_caller_in() -> None:
    not_signed_in = {key for key in route_operations() if key not in set(SIGNED_IN_OPERATIONS)}

    assert not_signed_in == PUBLIC_OPERATIONS
    # A floor, so a walk that found no route cannot pass.
    assert len(SIGNED_IN_OPERATIONS) >= 180
    assert REFERENCE_DATA_OPERATIONS <= set(SIGNED_IN_OPERATIONS)


@pytest.mark.parametrize(("method", "path"), SIGNED_IN_OPERATIONS, ids=[f"{m} {p}" for m, p in SIGNED_IN_OPERATIONS])
def test_every_signed_in_route_refuses_a_request_without_a_token(reference_status_client, method: str, path: str) -> None:
    client, _monkeypatch, session = reference_status_client

    response = client.request(method, filled_path(path))

    assert (response.status_code, response.json()) == (401, {"detail": "Not authenticated"}), path
    assert response.headers["www-authenticate"] == "Bearer"
    assert session.statements == []


def test_reference_status_requires_authentication(reference_status_client) -> None:
    client, _monkeypatch, _session = reference_status_client

    # The response embeds import provenance (source + performed-by operator email),
    # so an unauthenticated request must be rejected before any data is returned.
    response = client.get("/api/assemblies/reference-status")

    assert response.status_code == 401


def test_reference_status_returns_data_for_authenticated_user(reference_status_client) -> None:
    client, monkeypatch, _session = reference_status_client

    user = CurrentUser(
        id="u1",
        username="viewer@example.com",
        email="viewer@example.com",
        role="viewer",
        created_at=datetime.now(timezone.utc),
    )

    async def override_get_current_user():
        return user

    async def fake_list_reference_statuses(session):
        return []

    app.dependency_overrides[get_current_user] = override_get_current_user
    monkeypatch.setattr(
        assemblies_router, "list_reference_statuses", fake_list_reference_statuses
    )

    # A plain (non-admin) signed-in user may still read the reference-data page.
    response = client.get("/api/assemblies/reference-status")

    assert response.status_code == 200
    assert response.json() == []
