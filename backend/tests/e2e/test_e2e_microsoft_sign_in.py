"""Microsoft sign-in from the first sign-in to project access and roles (E2E, real Postgres, SEC-4).

With Azure AD configured, a colleague's first Microsoft sign-in makes their CoGA account on
real Postgres: bound to the token's object ID, a viewer's, active, in no project and without a
password, so they see no family until an administrator adds them to a project through the
project API, as a local account is added; then they see its family. Two requests of the same
first sign-in, at once, make one account. A later sign-in follows the token's new e-mail
address. A first sign-in with the e-mail address of an existing local account is refused and
takes nothing over. The emergency administrator (the seeded local admin) signs in with its
password while ``AZURE_ADMIN_OVERRIDE`` is on; it gives the colleague the admin role, and the
request audit rows on Postgres record the account made, the role change from and to, and flag
every request of the emergency sign-in. Nobody changes their own role or the emergency
administrator's, nor deactivates the emergency administrator; a deactivated colleague is told
so. With the override off the emergency
token and the password sign-in are refused, and there is no sign-up.

The directory is a stand-in made at run time (``_microsoft_sign_in_fakes.FakeDirectory``): its
keys are no directory's and the real token check runs against it. Requests go over HTTP
through an in-process ``httpx.ASGITransport`` client on one event loop (see
test_e2e_api_contract.py for why). The values are synthetic, and what the test makes is
deleted at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


async def _exercise(run: str, directory) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.tests.e2e import _harness

    family_id = f"MS_SIGN_IN_{run}"
    colleague_email = f"colleague-{run}@example.org"
    renamed_email = f"colleague-renamed-{run}@example.org"
    local_email = f"local-{run}@example.org"
    colleague_oid, intruder_oid = str(uuid4()), str(uuid4())
    out: dict[str, Any] = {}

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, shared_project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        shared = (
            await session.execute(
                text("SELECT species_id::text AS species_id, assembly_id::text AS assembly_id FROM projects WHERE id = CAST(:id AS uuid)"),
                {"id": shared_project_id},
            )
        ).mappings().one()
        local_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
                    VALUES (:email, :hashed, 'viewer', :email, '{}'::jsonb, now())
                    RETURNING id::text
                    """
                ),
                # Made at run time: the repository holds no password-shaped literal.
                {"email": local_email, "hashed": get_password_hash(uuid4().hex)},
            )
        ).scalar_one()
        await session.commit()

    def bearer(object_id: str, email: str) -> dict[str, str]:
        token = directory.access_token(
            object_id, email=email, preferred_username=email, given_name="Co", family_name=f"League {run}"
        )
        return {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    project_id = None
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as client:
            settings.azure_admin_override = True
            login = await client.post(
                "/api/auth/login", json={"email": settings.admin_email, "password": settings.admin_password}
            )
            out["emergency_login"] = login.status_code
            emergency = {"Authorization": f"Bearer {login.json()['access_token']}"}

            created_project = await client.post(
                "/api/projects/",
                json={"name": f"ms-sign-in-{run}", "species_id": shared["species_id"], "assembly_id": shared["assembly_id"]},
                headers=emergency,
            )
            out["project"] = created_project.status_code
            project_id = created_project.json()["_id"]
            created_family = await client.post(
                "/api/ped/manual",
                json={
                    "family_id": family_id,
                    "project_id": project_id,
                    "members": [{"sample_id": f"{family_id}_S1", "sex": "female", "is_proband": True}],
                },
                headers=emergency,
            )
            out["family"] = created_family.status_code

            colleague = bearer(colleague_oid, colleague_email)
            first, second = await asyncio.gather(
                client.get("/api/auth/me", headers=colleague), client.get("/api/auth/me", headers=colleague)
            )
            out["first_sign_in"] = [(r.status_code, r.json().get("email"), r.json().get("role")) for r in (first, second)]
            out["first_sign_in_ids"] = {r.json().get("id") for r in (first, second)}
            out["first_me"] = first.json()
            colleague_id = first.json()["id"]

            out["families_before"] = [
                f["family_id"] for f in (await client.get("/api/families/", headers=colleague)).json()
            ]
            added = await client.put(
                f"/api/projects/{project_id}", json={"user_ids": [colleague_id]}, headers=emergency
            )
            out["added"] = (added.status_code, added.json().get("user_ids"))
            out["families_after"] = [
                f["family_id"] for f in (await client.get("/api/families/", headers=colleague)).json()
            ]

            promoted = await client.patch(
                f"/api/auth/users/{colleague_id}", json={"role": "admin"}, headers=emergency
            )
            out["promoted"] = (promoted.status_code, promoted.json().get("role"))
            out["me_after_promotion"] = (await client.get("/api/auth/me", headers=colleague)).json()["role"]
            own = await client.patch(f"/api/auth/users/{colleague_id}", json={"role": "viewer"}, headers=colleague)
            out["own_role"] = (own.status_code, own.json())
            emergency_role = await client.patch(
                f"/api/auth/users/{admin.id}", json={"role": "viewer"}, headers=colleague
            )
            out["emergency_role"] = (emergency_role.status_code, emergency_role.json())
            emergency_off = await client.patch(
                f"/api/auth/users/{admin.id}", json={"is_active": False}, headers=colleague
            )
            out["emergency_off"] = (emergency_off.status_code, emergency_off.json())

            intruder = await client.get("/api/auth/me", headers=bearer(intruder_oid, local_email))
            out["intruder"] = (intruder.status_code, intruder.json())

            renamed = await client.get("/api/auth/me", headers=bearer(colleague_oid, renamed_email))
            out["renamed"] = (renamed.status_code, renamed.json().get("id"), renamed.json().get("email"))

            deactivated = await client.patch(
                f"/api/auth/users/{colleague_id}", json={"is_active": False}, headers=emergency
            )
            out["deactivated"] = deactivated.status_code
            refused = await client.get("/api/auth/me", headers=bearer(colleague_oid, renamed_email))
            out["inactive"] = (refused.status_code, refused.json())

            settings.azure_admin_override = False
            out["emergency_token_off"] = (await client.get("/api/auth/me", headers=emergency)).status_code
            login_off = await client.post(
                "/api/auth/login", json={"email": settings.admin_email, "password": settings.admin_password}
            )
            out["login_off"] = (login_off.status_code, login_off.json())
            signup = await client.post(
                "/api/auth/signup",
                json={
                    "email": f"signup-{run}@example.org",
                    "password": uuid4().hex,
                    "first_name": "Sign",
                    "last_name": "Up",
                    "affiliation": "Lab",
                },
            )
            out["signup"] = signup.status_code

        async with sessionmaker() as session:
            out["rows"] = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT u.azure_object_id::text AS azure_object_id, u.email, u.username, u.role,
                                   u.is_active, u.hashed_password, u.first_name, u.last_name,
                                   ARRAY(SELECT pu.project_id::text FROM project_users pu WHERE pu.user_id = u.id)
                                       AS projects
                            FROM users u
                            WHERE u.azure_object_id IN (CAST(:colleague AS uuid), CAST(:intruder AS uuid))
                               OR u.email IN (:local, :signup)
                            ORDER BY u.email
                            """
                        ),
                        {
                            "colleague": colleague_oid,
                            "intruder": intruder_oid,
                            "local": local_email,
                            "signup": f"signup-{run}@example.org",
                        },
                    )
                ).mappings()
            ]
            out["audit"] = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT path, method, status_code, user_email,
                                   request_meta->'account_change' AS account_change,
                                   (request_meta->>'emergency_sign_in')::boolean AS emergency_sign_in
                            FROM audit_log_events
                            WHERE (path LIKE '/api/auth/%' OR path LIKE '/api/projects/%' OR path = '/api/ped/manual'
                                   OR path = '/api/families/')
                              AND created_at > now() - interval '1 hour'
                              AND (user_email IN (:admin_email, :colleague, :renamed) OR user_email IS NULL)
                            ORDER BY created_at, id
                            """
                        ),
                        {"admin_email": settings.admin_email, "colleague": colleague_email, "renamed": renamed_email},
                    )
                ).mappings()
            ]
            out["colleague_id"] = colleague_id
            out["admin_id"] = admin.id
    finally:
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM families WHERE family_id = :family_id"), {"family_id": family_id})
            if project_id:
                await session.execute(text("DELETE FROM projects WHERE id = CAST(:id AS uuid)"), {"id": project_id})
            await session.execute(
                text(
                    "DELETE FROM users WHERE azure_object_id IN (CAST(:c AS uuid), CAST(:i AS uuid)) "
                    "OR id = CAST(:local AS uuid)"
                ),
                {"c": colleague_oid, "i": intruder_oid, "local": local_id},
            )
            await session.commit()
    out["colleague_oid"], out["intruder_oid"] = colleague_oid, intruder_oid
    out["emails"] = {"colleague": colleague_email, "renamed": renamed_email, "local": local_email}
    out["project_id"] = project_id
    out["family_id"] = family_id
    out["run"] = run
    return out


@pytest.fixture(scope="module")
def signed_in() -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests._microsoft_sign_in_fakes import FakeDirectory
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    with pytest.MonkeyPatch.context() as patch:
        directory = FakeDirectory().serve(patch).configure(patch, settings)
        patch.setattr(settings, "azure_admin_override", True)
        return _harness.run_async(lambda: _exercise(run, directory))


def _row(signed_in: dict[str, Any], **match: Any) -> dict[str, Any]:
    (row,) = [r for r in signed_in["rows"] if all(r[key] == value for key, value in match.items())]
    return row


def test_the_emergency_admin_set_up_the_project_and_family(signed_in) -> None:
    assert (signed_in["emergency_login"], signed_in["project"], signed_in["family"]) == (200, 201, 200)


def test_a_first_sign_in_makes_one_viewers_account_bound_to_the_object_id(signed_in) -> None:
    from backend.app.services.user_accounts import MICROSOFT_ACCOUNT_HASH_MARKER

    email = signed_in["emails"]["colleague"]
    assert signed_in["first_sign_in"] == [(200, email, "viewer"), (200, email, "viewer")]
    assert len(signed_in["first_sign_in_ids"]) == 1
    assert signed_in["first_me"]["sign_in"] == "microsoft" and signed_in["first_me"]["projects"] == []
    row = _row(signed_in, azure_object_id=signed_in["colleague_oid"])
    assert row["hashed_password"] == MICROSOFT_ACCOUNT_HASH_MARKER
    assert (row["first_name"], row["last_name"]) == ("Co", f"League {signed_in['run']}")


def test_a_new_account_sees_no_family_until_an_admin_adds_it_to_a_project(signed_in) -> None:
    assert signed_in["families_before"] == []
    status, user_ids = signed_in["added"]
    assert status == 200 and signed_in["colleague_id"] in user_ids
    assert signed_in["families_after"] == [signed_in["family_id"]]
    assert _row(signed_in, azure_object_id=signed_in["colleague_oid"])["projects"] == [signed_in["project_id"]]


def test_the_emergency_admin_gives_the_admin_role_and_nobody_changes_their_own_or_its(signed_in) -> None:
    from backend.app.routers.auth import EMERGENCY_ACTIVE_DETAIL, EMERGENCY_ROLE_DETAIL, OWN_ROLE_DETAIL

    assert signed_in["promoted"] == (200, "admin")
    assert signed_in["me_after_promotion"] == "admin"
    assert signed_in["own_role"] == (403, {"detail": OWN_ROLE_DETAIL})
    assert signed_in["emergency_role"] == (403, {"detail": EMERGENCY_ROLE_DETAIL})
    assert signed_in["emergency_off"] == (403, {"detail": EMERGENCY_ACTIVE_DETAIL})


def test_a_first_sign_in_never_takes_over_a_local_account(signed_in) -> None:
    from backend.app.services.user_accounts import EMAIL_TAKEN_DETAIL

    assert signed_in["intruder"] == (403, {"detail": EMAIL_TAKEN_DETAIL})
    local = _row(signed_in, email=signed_in["emails"]["local"])
    assert local["azure_object_id"] is None and local["role"] == "viewer"
    assert not [r for r in signed_in["rows"] if r["azure_object_id"] == signed_in["intruder_oid"]]


def test_a_later_sign_in_follows_the_new_e_mail_address(signed_in) -> None:
    assert signed_in["renamed"] == (200, signed_in["colleague_id"], signed_in["emails"]["renamed"])
    assert _row(signed_in, azure_object_id=signed_in["colleague_oid"])["email"] == signed_in["emails"]["renamed"]


def test_a_deactivated_account_is_told_so(signed_in) -> None:
    from backend.app.dependencies import INACTIVE_MICROSOFT_ACCOUNT_DETAIL

    assert signed_in["deactivated"] == 200
    assert signed_in["inactive"] == (401, {"detail": INACTIVE_MICROSOFT_ACCOUNT_DETAIL})


def test_with_the_override_off_only_microsoft_signs_in_and_there_is_no_sign_up(signed_in) -> None:
    from backend.app.routers.auth import PASSWORD_SIGN_IN_OFF_DETAIL

    assert signed_in["emergency_token_off"] == 401
    assert signed_in["login_off"] == (403, {"detail": PASSWORD_SIGN_IN_OFF_DETAIL})
    assert signed_in["signup"] == 403
    assert not [r for r in signed_in["rows"] if r["email"].startswith("signup-")]


def test_the_audit_rows_record_the_account_made_its_role_and_the_emergency_sign_in(signed_in) -> None:
    audit = signed_in["audit"]
    colleague_id = signed_in["colleague_id"]
    mine = [row for row in audit if (row["account_change"] or {}).get("user_id") == colleague_id]
    made = [row["account_change"] for row in mine if row["account_change"].get("created")]
    assert made == [
        {
            "user_id": colleague_id,
            "email": signed_in["emails"]["colleague"],
            "created": "microsoft_sign_in",
            "role": {"from": None, "to": "viewer"},
        }
    ]
    changes = [row["account_change"] for row in mine if row["method"] == "PATCH" and row["status_code"] == 200]
    assert changes == [
        {"user_id": colleague_id, "email": signed_in["emails"]["colleague"], "role": {"from": "viewer", "to": "admin"}},
        {"user_id": colleague_id, "email": signed_in["emails"]["renamed"], "is_active": {"from": True, "to": False}},
    ]
    # Every request of the emergency sign-in is flagged, its sign-in included; no other is.
    flagged = {(row["method"], row["path"]) for row in audit if row["emergency_sign_in"]}
    assert ("POST", "/api/auth/login") in flagged
    assert ("POST", "/api/projects/") in flagged and ("PATCH", f"/api/auth/users/{colleague_id}") in flagged
    assert all(
        row["user_email"] != signed_in["emails"]["colleague"] for row in audit if row["emergency_sign_in"]
    )
    assert [row["status_code"] for row in audit if row["path"] == "/api/auth/login" and row["emergency_sign_in"]] == [200]
