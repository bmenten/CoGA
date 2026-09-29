"""Integration smoke test: the app BOOTS and serves as the restricted role `coga_app`.

This is the deployability proof for the P1-3/P1-4 privilege-separation split. It exercises
the exact deployed shape of the DSN flip (docs/db-runtime-role-runbook.md):

1. Schema migrations run **out-of-band as the owner** (``run_schema_migrations`` — the
   ``python -m backend.app.db_migrate`` entrypoint), creating ``coga_app`` and its REVOKEs.
2. The same migration gives ``coga_app`` a login from ``POSTGRES_APP_PASSWORD``, as the
   Google Cloud db-migrate job does. It stores a SCRAM verifier, not the password, so the
   login below also proves that verifier matches the password the app then sends.
3. The app is repointed at ``coga_app`` with ``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP``
   disabled, and booted through its real lifespan. It must start and serve **without**
   attempting owner-only DDL (which would crash-loop, the gap codex flagged).

It then asserts, on a **real ``coga_app`` login connection** (stronger than the ``SET ROLE``
check in ``test_app_role_privileges``), that the runtime role may append an audit row but may
not update it — i.e. the append-only revoke is enforced against the actual app credential.

Skipped unless RUN_INTEGRATION=1 (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

# Deterministic (test-only) password for coga_app; enables its login for this run only.
_APP_ROLE_PASSWORD = "coga-app-smoke-not-a-real-secret"


def test_app_boots_and_serves_as_restricted_coga_app_role(monkeypatch) -> None:
    from backend.app.core.config import settings
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
    )
    from backend.app.db_migrate import run_schema_migrations

    # 1) + 2) Owner path, as the deploy pipeline runs it: apply the schema (creates coga_app
    #    NOLOGIN + the append-only REVOKEs), seed the admin user, and enable coga_app's login
    #    from POSTGRES_APP_PASSWORD.
    monkeypatch.setattr(settings, "postgres_app_password", _APP_ROLE_PASSWORD)
    asyncio.run(run_schema_migrations())

    # 3) The deployed flip: connect as coga_app and do NOT run schema migrations on startup.
    monkeypatch.setattr(settings, "postgres_user", "coga_app")
    monkeypatch.setattr(settings, "postgres_password", _APP_ROLE_PASSWORD)
    monkeypatch.setattr(settings, "postgres_run_schema_migrations_on_startup", False)
    # Force the next engine build to pick up the coga_app DSN.
    asyncio.run(close_postgres_engine())

    from backend.app.main import app

    # TestClient's context manager runs the real lifespan AS coga_app. A DDL attempt here
    # (or a startup seed needing owner privileges) would raise and fail the test — exactly
    # the crash-loop the split prevents.
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        ready = client.get("/api/health/ready")
        assert ready.status_code == 200
        # Readiness ran a real SELECT over the coga_app connection.
        assert ready.json()["postgres"] == "ok"

    # The lifespan disposed the engine on shutdown; rebuild a fresh coga_app engine (still
    # coga_app via monkeypatch) to probe the runtime role's exact privilege set.
    async def _probe_runtime_privileges() -> None:
        try:
            sm = get_postgres_sessionmaker()
            marker = "p1-3-boot-smoke"
            # Positive: coga_app (the live app credential) may append an audit row.
            async with sm() as s:
                await s.execute(
                    text(
                        "INSERT INTO clinical_audit_events (actor, action, summary) "
                        "VALUES ('smoke', 'boot', :m)"
                    ),
                    {"m": marker},
                )
                await s.commit()
            # Negative: it may NOT rewrite one — the REVOKE is enforced on the real login,
            # not merely via SET ROLE.
            with pytest.raises(DBAPIError) as exc_info:
                async with sm() as s:
                    await s.execute(text("UPDATE clinical_audit_events SET summary = summary"))
                    await s.commit()
            assert "permission denied" in str(exc_info.value).lower(), str(exc_info.value)
        finally:
            await close_postgres_engine()

    asyncio.run(_probe_runtime_privileges())
