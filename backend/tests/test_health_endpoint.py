from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.main import _docs_kwargs, app


def test_health_liveness_and_routing_without_datastores() -> None:
    """The app mounts health + the API routers without touching Postgres/ClickHouse.

    Boots with startup tasks skipped, so this needs no datastores and runs in the
    normal backend job. It catches import / router-wiring / middleware crashes —
    the kind that previously only surfaced at runtime as "Unable to reach API".
    """
    app.state.skip_startup_tasks = True
    try:
        with TestClient(app) as client:
            live = client.get("/api/health")
            assert live.status_code == 200
            assert live.json() == {"status": "ok"}

            # Routing, auth and middleware are wired: an unauthenticated data route
            # is rejected with 401 — not a 404 (missing) or 500 (broken wiring).
            assert client.get("/api/projects/").status_code == 401
    finally:
        app.state.skip_startup_tasks = False


def test_security_headers_present_on_responses() -> None:
    app.state.skip_startup_tasks = True
    try:
        with TestClient(app) as client:
            headers = client.get("/api/health").headers
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["x-frame-options"] == "DENY"
            assert "frame-ancestors 'none'" in headers["content-security-policy"]
            assert headers["referrer-policy"] == "no-referrer"
            assert headers["cross-origin-opener-policy"] == "same-origin"
    finally:
        app.state.skip_startup_tasks = False


def test_hsts_absent_by_default_present_when_enabled(monkeypatch) -> None:
    app.state.skip_startup_tasks = True
    try:
        with TestClient(app) as client:
            # HSTS is opt-in — never emitted over plain HTTP.
            assert "strict-transport-security" not in client.get("/api/health").headers
            monkeypatch.setattr(settings, "enable_hsts", True)
            hsts = client.get("/api/health").headers.get("strict-transport-security")
            assert hsts is not None and "max-age=" in hsts
    finally:
        app.state.skip_startup_tasks = False


def test_version_endpoint_reports_build_identity() -> None:
    app.state.skip_startup_tasks = True
    try:
        with TestClient(app) as client:
            # /health stays EXACTLY {"status": "ok"} — /version is a separate endpoint,
            # so the liveness/healthcheck contract is untouched.
            assert client.get("/api/health").json() == {"status": "ok"}

            resp = client.get("/api/version")
            assert resp.status_code == 200
            body = resp.json()
            assert set(body) == {"version", "git_sha"}
            assert body["version"] == settings.app_version
            assert body["git_sha"] == settings.git_sha
    finally:
        app.state.skip_startup_tasks = False


def test_version_endpoint_serves_a_named_model_for_the_frontend() -> None:
    # The app footer and every report footer show this build (TF-15 §1), read through the
    # type generated from this schema (scripts/generate-api-types.py). A bare
    # dict[str, str] response has no named schema, so no type is generated for it.
    spec = app.openapi()
    response = spec["paths"]["/api/version"]["get"]["responses"]["200"]
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/VersionOut"
    }
    model = spec["components"]["schemas"]["VersionOut"]
    assert set(model["required"]) == {"version", "git_sha"}
    assert {name: prop["type"] for name, prop in model["properties"].items()} == {
        "version": "string",
        "git_sha": "string",
    }


def test_fastapi_app_version_is_wired_from_settings() -> None:
    # version= is threaded into FastAPI(...), so OpenAPI/docs report the build identity.
    assert app.version == settings.app_version


def test_openapi_route_available_in_development_env() -> None:
    # APP_ENV=test is a development env, so the interactive docs / OpenAPI HTTP routes
    # are exposed (the enabled branch of _docs_kwargs, end to end).
    app.state.skip_startup_tasks = True
    try:
        with TestClient(app) as client:
            assert client.get("/openapi.json").status_code == 200
    finally:
        app.state.skip_startup_tasks = False


def test_docs_kwargs_disabled_in_production(monkeypatch) -> None:
    # In production the same gating turns /docs, /redoc and /openapi.json off (the
    # in-process app.openapi() the trailing-slash normaliser uses still works).
    monkeypatch.setattr(settings, "app_env", "production")
    assert _docs_kwargs() == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    monkeypatch.setattr(settings, "app_env", "development")
    assert _docs_kwargs() == {}
