"""The global roles, lowest first, each with every right of the ones before it (E2E).

A viewer reads the records of their projects; an analyst also analyses them; a supervisor also
signs an analysis out (TF-06 H15); a superuser also has every project and the configuration the
analyses read (gene panels, variant tags, filter presets, sequencing QC thresholds, package
import); an admin, the whole administration (services/access_control.py).

On real Postgres, through the app with real sign-ins:

* the admin gives four accounts their role (``PATCH /api/auth/users/{id}``), refuses a role
  CoGA does not know, and makes a project with the viewer, the analyst and the supervisor as
  analysts (their access level) and a family in it; the superuser is a member of nothing;
* the viewer reads the family, which reads as read-only and not to be signed out, and is
  refused a phenotype change and the clinical audit trail, each with the reason that names their
  role;
* the analyst changes a phenotype, reads the audit trail, and is refused the sign-out with the
  reason (403), before any gate: nothing is signed;
* the supervisor signs the analysis out (version 1);
* the superuser, in no project, reads the family and may sign it out; reads the admin
  dashboard's superuser workspaces (QC thresholds, variant tags, filter presets, import jobs)
  and is refused the admin's (the account list, the audit log);
* every refusal is in the request audit under its caller's name.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The values
are synthetic, and what the test creates is removed at the end (the append-only audit events
and the signed record stay, as every e2e test's do).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import DRIFT_AND_QC_ACKNOWLEDGED, ensure_postgres_schema, status_and_body

pytestmark = pytest.mark.integration

SEIZURE = "HP:0001250"
ROLES = ("viewer", "analyst", "supervisor", "superuser")


async def _exercise(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.tests.e2e import _harness

    password = f"roles-password-{run}"
    emails = {role: f"e2e-role-{role}-{run}@example.com" for role in ROLES}
    family_id, sample_id = f"E2E_ROLES_{run}", f"E2E_ROLES_{run}_P"

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, shared_project_id, assembly_id = await _harness.ensure_e2e_project(session)
        species_id = (
            await session.execute(
                text("SELECT species_id::text FROM projects WHERE id = CAST(:id AS uuid)"), {"id": shared_project_id}
            )
        ).scalar_one()
        users: dict[str, str] = {}
        for role, email in emails.items():
            # Each account starts as a viewer, as a sign-up or a first sign-in makes it.
            users[role] = (
                await session.execute(
                    text(
                        """
                        INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
                        VALUES (:email, :hashed_password, 'viewer', :email, '{}'::jsonb, now())
                        RETURNING id::text
                        """
                    ),
                    {"email": email, "hashed_password": get_password_hash(password)},
                )
            ).scalar_one()
        await _harness.seed_hpo_term(session, SEIZURE, "Seizure")
        await session.commit()

    out: dict[str, Any] = {"users": users, "emails": emails}
    project_id: str | None = None
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin:
            admin.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin)}"

            # --- the admin gives each account its role -----------------------------------
            out["role_changes"] = {
                role: status_and_body(await admin.patch(f"/api/auth/users/{users[role]}", json={"role": role}))[0]
                for role in ROLES
                if role != "viewer"
            }
            out["unknown_role"] = (
                await admin.patch(f"/api/auth/users/{users['analyst']}", json={"role": "signer"})
            ).status_code
            accounts = {account["id"]: account for account in (await admin.get("/api/auth/users")).json()}
            out["roles"] = {role: accounts[users[role]]["role"] for role in ROLES}

            created = await admin.post(
                "/api/projects/",
                json={
                    "name": f"global-roles-{run}",
                    "species_id": species_id,
                    "assembly_id": assembly_id,
                    "user_ids": [users["viewer"], users["analyst"], users["supervisor"]],
                    "reader_user_ids": [],
                },
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["_id"]
            made = await admin.post(
                "/api/ped/manual",
                json={
                    "family_id": family_id,
                    "project_id": project_id,
                    "members": [{"sample_id": sample_id, "sex": "female", "is_proband": True}],
                },
            )
            assert made.status_code == 200, made.text

            clients: dict[str, AsyncClient] = {}
            for role, email in emails.items():
                clients[role] = AsyncClient(transport=transport, base_url="http://e2e")
                login = await clients[role].post("/api/auth/login", json={"email": email, "password": password})
                assert login.status_code == 200, login.text
                out[f"login_role_{role}"] = login.json()["role"]
                clients[role].headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            family_url = f"/api/families/{family_id}"

            # --- what each reads of the family ---------------------------------------------
            for role, client in clients.items():
                family = await client.get(family_url)
                body = family.json() if family.status_code == 200 else {}
                out[f"family_{role}"] = (family.status_code, body.get("read_only"), body.get("can_sign_out"))

            # --- the viewer reads only -----------------------------------------------------
            viewer = clients["viewer"]
            out["viewer_hpo"] = status_and_body(
                await viewer.post(f"{family_url}/members/{sample_id}/hpo", json={"hpo_id": SEIZURE})
            )
            out["viewer_audit_trail"] = status_and_body(await viewer.get(f"{family_url}/clinical-audit"))

            # --- the analyst analyses, and does not sign out ------------------------------
            analyst = clients["analyst"]
            out["analyst_hpo"] = (
                await analyst.post(f"{family_url}/members/{sample_id}/hpo", json={"hpo_id": SEIZURE})
            ).status_code
            out["analyst_audit_trail"] = (await analyst.get(f"{family_url}/clinical-audit")).status_code
            out["analyst_sign_out"] = status_and_body(
                await _harness.sign_out(analyst, f"{family_url}/report/sign-out", DRIFT_AND_QC_ACKNOWLEDGED)
            )
            out["signouts_after_analyst"] = (await admin.get(f"{family_url}/report/sign-outs")).json()

            # --- the supervisor signs out ---------------------------------------------------
            out["supervisor_sign_out"] = status_and_body(
                await _harness.sign_out(
                    clients["supervisor"], f"{family_url}/report/sign-out", DRIFT_AND_QC_ACKNOWLEDGED
                )
            )

            # --- the superuser: every project, the configuration, not the administration --
            superuser = clients["superuser"]
            out["superuser_reads"] = {
                path: (await superuser.get(path)).status_code
                for path in (
                    "/api/admin/qc-thresholds",
                    "/api/admin/variant-tags",
                    "/api/admin/small-variant-filter-presets",
                    "/api/family-imports",
                    "/api/auth/users",
                    "/api/admin/audit-logs",
                )
            }
            for client in clients.values():
                await client.aclose()

        async with sessionmaker() as session:
            out["refusals_audited"] = {
                role: (
                    await session.execute(
                        text("SELECT count(*) FROM audit_log_events WHERE user_email = :email AND status_code = 403"),
                        {"email": email},
                    )
                ).scalar_one()
                for role, email in emails.items()
            }
    finally:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin:
            admin.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin)}"
            await admin.request(
                "DELETE", f"/api/admin/families/{family_id}", params={"confirm": "true"}, json={"reason": "e2e"}
            )
        async with sessionmaker() as session:
            if project_id is not None:
                await session.execute(text("DELETE FROM projects WHERE id = CAST(:id AS uuid)"), {"id": project_id})
            for user_id in users.values():
                await session.execute(text("DELETE FROM users WHERE id = CAST(:id AS uuid)"), {"id": user_id})
            await session.commit()
    return out


@pytest.fixture(scope="module")
def ran() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(run))


def test_the_admin_gives_each_account_its_role_and_refuses_an_unknown_one(ran: dict[str, Any]) -> None:
    assert ran["role_changes"] == {"analyst": 200, "supervisor": 200, "superuser": 200}
    assert ran["unknown_role"] == 422
    assert ran["roles"] == {role: role for role in ROLES}
    # The sign-in hands the role to the browser.
    assert [ran[f"login_role_{role}"] for role in ROLES] == list(ROLES)


def test_each_role_reads_the_family_with_its_own_rights(ran: dict[str, Any]) -> None:
    # (status, read_only, can_sign_out); the superuser is a member of no project.
    assert ran["family_viewer"] == (200, True, False)
    assert ran["family_analyst"] == (200, False, False)
    assert ran["family_supervisor"] == (200, False, True)
    assert ran["family_superuser"] == (200, False, True)


def test_a_viewer_reads_only_and_is_told_it_is_their_role(ran: dict[str, Any]) -> None:
    from backend.app.services.access_control import VIEWER_AUDIT_TRAIL_DETAIL, VIEWER_READ_ONLY_DETAIL

    assert ran["viewer_hpo"] == (403, {"detail": VIEWER_READ_ONLY_DETAIL})
    assert ran["viewer_audit_trail"] == (403, {"detail": VIEWER_AUDIT_TRAIL_DETAIL})


def test_an_analyst_analyses_and_is_refused_the_sign_out(ran: dict[str, Any]) -> None:
    from backend.app.services.access_control import SIGN_OUT_ROLE_DETAIL

    assert ran["analyst_hpo"] == 200
    assert ran["analyst_audit_trail"] == 200
    # Refused before any gate: the first attempt, with nothing acknowledged, is the refusal.
    assert ran["analyst_sign_out"] == (403, {"detail": SIGN_OUT_ROLE_DETAIL})
    assert ran["signouts_after_analyst"]["signouts"] == []


def test_a_supervisor_signs_the_analysis_out(ran: dict[str, Any]) -> None:
    status, body = ran["supervisor_sign_out"]
    assert status == 200, body
    assert body["version"] == 1
    assert body["signed_out_by"] == ran["emails"]["supervisor"]


def test_a_superuser_has_the_configuration_and_not_the_administration(ran: dict[str, Any]) -> None:
    assert ran["superuser_reads"] == {
        "/api/admin/qc-thresholds": 200,
        "/api/admin/variant-tags": 200,
        "/api/admin/small-variant-filter-presets": 200,
        "/api/family-imports": 200,
        "/api/auth/users": 403,
        "/api/admin/audit-logs": 403,
    }


def test_every_refusal_is_audited_under_its_callers_name(ran: dict[str, Any]) -> None:
    assert ran["refusals_audited"] == {"viewer": 2, "analyst": 1, "supervisor": 0, "superuser": 2}
