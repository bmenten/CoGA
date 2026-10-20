"""A case is assigned to, and recorded as reviewed by, someone who may work on it (E2E, SEC-1).

A family's "Assigned to" and "Reviewed by" name an active analyst of one of the family's
projects or an active administrator. Over the real API (an in-process ``httpx.ASGITransport``
client on one event loop, see test_e2e_api_contract.py) and Postgres:

* the admin makes project A, where Ana and Carl are analysts and Rita a reader, and project B,
  where Ben is an analyst; a family of A; Gil, an analyst of A whose account is deactivated;
* the family's pickers' list (``GET /api/families/{family_id}/assignable-users``) holds Ana,
  Carl and the administrator, not Rita, Ben or Gil; Rita, a reader, reads it too; Ben, outside
  A, is answered 404 as for any family he may not see;
* Ana assigns the case to herself and records Carl as its reviewer; a save that names Rita (a
  reader), Ben (outside the family's projects) or Gil (inactive) is refused with 422 and why,
  and the family keeps what it held;
* the admin makes Carl a reader: the family still names him as its reviewer, its list no
  longer holds him, a save that leaves him as he is goes through, and naming him anew is
  refused.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The values
are synthetic, and what the test creates is removed at the end.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

HINT = "Choose an analyst of one of the family's projects or an administrator."


def _answer(resp: Any) -> tuple[int, Any]:
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text[:200]


def _listed(resp: Any) -> tuple[int, Any]:
    """The list's status and the IDs it holds (its body when it is no list)."""
    status, body = _answer(resp)
    return status, [user["id"] for user in body] if status == 200 and isinstance(body, list) else body


async def _exercise(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.tests.e2e import _harness

    family_id = f"ASSIGNEE_{run}"
    names = {"ana": "Ana", "carl": "Carl", "rita": "Rita", "ben": "Ben", "gil": "Gil"}
    emails = {who: f"{who}-{run}@example.org" for who in names}
    # Made at run time: the repository holds no password-shaped literal.
    password = uuid4().hex

    await init_postgres_schema()
    await init_clickhouse_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, shared_project_id, assembly_id = await _harness.ensure_e2e_project(session)
        species_id = (
            await session.execute(
                text("SELECT species_id::text FROM projects WHERE id = CAST(:id AS uuid)"), {"id": shared_project_id}
            )
        ).scalar_one()
        users: dict[str, str] = {}
        for who, email in emails.items():
            users[who] = (
                await session.execute(
                    text(
                        """
                        INSERT INTO users (username, hashed_password, role, email, first_name, last_name,
                                           is_active, metadata, created_at)
                        VALUES (:email, :hashed_password, 'viewer', :email, :first, 'Test',
                                :active, '{}'::jsonb, now())
                        RETURNING id::text
                        """
                    ),
                    {
                        "email": email,
                        "hashed_password": get_password_hash(password),
                        "first": names[who],
                        "active": who != "gil",
                    },
                )
            ).scalar_one()
        await session.commit()

    out: dict[str, Any] = {"users": users, "admin": admin.id}
    projects: dict[str, str] = {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin_client:
            admin_client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin_client)}"
            clients: dict[str, AsyncClient] = {}
            for who in ("ana", "rita", "ben"):
                clients[who] = AsyncClient(transport=transport, base_url="http://e2e")
                login = await clients[who].post("/api/auth/login", json={"email": emails[who], "password": password})
                assert login.status_code == 200, login.text
                clients[who].headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            ana, rita, ben = clients["ana"], clients["rita"], clients["ben"]

            for name, members, readers in (
                ("A", [users["ana"], users["carl"], users["rita"], users["gil"]], [users["rita"]]),
                ("B", [users["ben"]], []),
            ):
                created = await admin_client.post(
                    "/api/projects/",
                    json={
                        "name": f"assignee-{name}-{run}",
                        "species_id": species_id,
                        "assembly_id": assembly_id,
                        "user_ids": members,
                        "reader_user_ids": readers,
                    },
                )
                assert created.status_code == 201, created.text
                projects[name] = created.json()["_id"]
            made = await admin_client.post(
                "/api/ped/manual",
                json={
                    "family_id": family_id,
                    "project_id": projects["A"],
                    "members": [{"sample_id": f"{family_id}_S1", "sex": "female", "is_proband": True}],
                },
            )
            assert made.status_code == 200, made.text

            path = f"/api/families/{family_id}"
            out["listed"] = _listed(await ana.get(f"{path}/assignable-users"))
            out["listed_by_reader"] = _listed(await rita.get(f"{path}/assignable-users"))
            out["listed_by_outsider"] = _answer(await ben.get(f"{path}/assignable-users"))
            out["unknown_family"] = _answer(await ben.get(f"/api/families/NO_{family_id}/assignable-users"))

            saved = await ana.put(f"{path}/metadata", json={"assigned_to": users["ana"], "reviewed_by": users["carl"]})
            out["saved"] = (
                saved.status_code,
                (saved.json().get("assigned_to") or {}).get("id"),
                (saved.json().get("reviewed_by") or {}).get("id"),
            )
            out["refused"] = {
                who: _answer(await ana.put(f"{path}/metadata", json={field: users[who]}))
                for who, field in (("rita", "reviewed_by"), ("ben", "assigned_to"), ("gil", "assigned_to"))
            }
            # A refused field leaves the others of the save unwritten.
            out["refused_with_status"] = _answer(
                await ana.put(f"{path}/metadata", json={"status_key": None, "assigned_to": users["rita"]})
            )
            out["after_refusals"] = (await ana.get(path)).json()

            # Carl, the reviewer, becomes a reader of A.
            demoted = await admin_client.put(
                f"/api/projects/{projects['A']}", json={"reader_user_ids": [users["rita"], users["carl"]]}
            )
            assert demoted.status_code == 200, demoted.text
            out["kept"] = (await ana.get(path)).json()["reviewed_by"]
            out["listed_after_demotion"] = _listed(await ana.get(f"{path}/assignable-users"))
            out["left_as_he_is"] = _answer(
                await ana.put(f"{path}/metadata", json={"assigned_to": users["ana"], "reviewed_by": users["carl"]})
            )
            out["named_anew"] = _answer(await ana.put(f"{path}/metadata", json={"assigned_to": users["carl"]}))
            for client in clients.values():
                await client.aclose()

        async with sessionmaker() as session:
            out["stored"] = dict(
                (
                    await session.execute(
                        text(
                            "SELECT assigned_to_id::text AS assigned_to, reviewed_by_id::text AS reviewed_by "
                            "FROM families WHERE family_id = :f"
                        ),
                        {"f": family_id},
                    )
                ).mappings().one()
            )
    finally:
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": family_id})
            for project_id in projects.values():
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


def test_the_pickers_list_the_familys_analysts_and_the_administrators(ran: dict[str, Any]) -> None:
    users = ran["users"]
    status, listed = ran["listed"]
    assert status == 200
    assert {users["ana"], users["carl"], ran["admin"]} <= set(listed)
    # Not a reader, an analyst of another project, nor an inactive account.
    assert not {users["rita"], users["ben"], users["gil"]} & set(listed)
    assert ran["listed_by_reader"] == (200, listed)


def test_a_family_outside_the_users_projects_answers_like_an_unknown_one(ran: dict[str, Any]) -> None:
    assert ran["listed_by_outsider"] == ran["unknown_family"] == (404, {"detail": "Family not found"})


def test_a_save_names_an_analyst_and_is_refused_anyone_else(ran: dict[str, Any]) -> None:
    users = ran["users"]
    assert ran["saved"] == (200, users["ana"], users["carl"])
    assert ran["refused"] == {
        "rita": (
            422,
            {
                "detail": "Rita Test cannot be recorded as this case's reviewer: they are a reader in this "
                f"family's projects. {HINT}"
            },
        ),
        "ben": (
            422,
            {"detail": f"Ben Test cannot be assigned this case: they are not a member of this family's projects. {HINT}"},
        ),
        "gil": (422, {"detail": f"Gil Test cannot be assigned this case: their account is not active. {HINT}"}),
    }
    assert ran["refused_with_status"][0] == 422
    family = ran["after_refusals"]
    assert ((family["assigned_to"] or {}).get("id"), (family["reviewed_by"] or {}).get("id")) == (
        users["ana"],
        users["carl"],
    )


def test_a_reviewer_who_became_a_reader_stays_until_changed(ran: dict[str, Any]) -> None:
    users = ran["users"]
    assert (ran["kept"] or {}).get("id") == users["carl"]
    status, listed = ran["listed_after_demotion"]
    assert status == 200 and users["carl"] not in listed
    assert ran["left_as_he_is"][0] == 200
    assert ran["named_anew"] == (
        422,
        {"detail": f"Carl Test cannot be assigned this case: they are a reader in this family's projects. {HINT}"},
    )
    assert ran["stored"] == {"assigned_to": users["ana"], "reviewed_by": users["carl"]}
