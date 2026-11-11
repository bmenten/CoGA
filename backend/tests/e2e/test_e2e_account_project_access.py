"""An administrator sets an account's projects on the Users page (E2E).

A project's members and an account's projects are the same rows (``project_users``), set from
either side: a project's ``user_ids`` and ``reader_user_ids`` on *Projects & access*, an
account's ``project_ids`` and ``reader_project_ids`` on *Users* (``PATCH /api/auth/users/{id}``).
Over the real API (an in-process ``httpx.ASGITransport`` client on one event loop, see
test_e2e_api_contract.py) and Postgres:

* the admin makes two projects, A and B, and gives an analyst access to both, a reader in B:
  ``project_users``, the account list and the project list say so;
* a change that leaves the reader list out keeps the level of each project the account stays
  in; a change of the reader list alone changes only the levels;
* a reader project that is not one of the account's (400), a project that does not exist
  (404, the role change sent with it not made either) and a field the change does not take
  (``projects``, 422) are refused, and nothing changes;
* each change is in its request's audit row, each field from and to
  (``request_meta.account_change``); a refused one has none;
* a project's save sees and keeps what the account's side set, and saves from both sides at
  once all succeed: every writer of the memberships takes the same lock, so neither waits on
  the other in a deadlock.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The values
are synthetic, and what the test makes is deleted at the end (the append-only audit events
stay, as every e2e test's do).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import capture, ensure_postgres_schema

pytestmark = pytest.mark.integration

# How many times the two sides save at once.
CONCURRENT_ROUNDS = 8


async def _exercise(run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.tests.e2e import _harness

    out: dict[str, Any] = {"run": run}
    await ensure_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, _project, assembly_id = await _harness.ensure_e2e_project(session)
        species_id = (
            await session.execute(
                text("SELECT species_id::text FROM assemblies WHERE id = CAST(:id AS uuid)"), {"id": assembly_id}
            )
        ).scalar_one()
        accounts: dict[str, str] = {}
        for name, role in (("member", "analyst"), ("other", "viewer")):
            email = f"e2e-access-{name}-{run}@example.org"
            accounts[name] = (
                await session.execute(
                    text(
                        """
                        INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
                        VALUES (:email, :hashed, :role, :email, '{}'::jsonb, now())
                        RETURNING id::text
                        """
                    ),
                    # Made at run time: the repository holds no password-shaped literal.
                    {"email": email, "hashed": get_password_hash(uuid4().hex), "role": role},
                )
            ).scalar_one()
        await session.commit()
    member, other = accounts["member"], accounts["other"]
    out["member"], out["member_email"] = member, f"e2e-access-member-{run}@example.org"

    async def memberships() -> list[tuple[str, str]]:
        async with sessionmaker() as session:
            rows = await session.execute(
                text(
                    "SELECT project_id::text, access_level FROM project_users "
                    "WHERE user_id = CAST(:user_id AS uuid) ORDER BY project_id"
                ),
                {"user_id": member},
            )
            return [(str(row[0]), str(row[1])) for row in rows.all()]

    async def role() -> str:
        async with sessionmaker() as session:
            return (
                await session.execute(text("SELECT role FROM users WHERE id = CAST(:id AS uuid)"), {"id": member})
            ).scalar_one()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    projects: dict[str, str] = {}
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
            for name in ("a", "b"):
                created = await ac.post(
                    "/api/projects/",
                    json={"name": f"e2e-access-{run}-{name}", "species_id": species_id, "assembly_id": assembly_id},
                )
                assert created.status_code == 201, created.text
                projects[name] = created.json()["_id"]
            a, b = projects["a"], projects["b"]
            out["a"], out["b"] = a, b
            user_url = f"/api/auth/users/{member}"

            async def seen() -> dict[str, Any]:
                """The account as the account list and the project list show it."""
                users = (await ac.get("/api/auth/users")).json()
                (account,) = [user for user in users if user["id"] == member]
                listed = {project["_id"]: project for project in (await ac.get("/api/projects/")).json()}
                return {
                    "account": (sorted(account["projects"]), sorted(account["reader_projects"])),
                    "projects": {
                        name: (member in listed[pid]["user_ids"], member in listed[pid]["reader_user_ids"])
                        for name, pid in projects.items()
                    },
                }

            # --- set from the account's side -------------------------------------------
            out["set"] = capture(await ac.patch(user_url, json={"project_ids": [a, b], "reader_project_ids": [b]}))
            out["after_set"] = await memberships()
            out["seen_after_set"] = await seen()

            out["narrowed"] = capture(await ac.patch(user_url, json={"project_ids": [b]}))
            out["after_narrowed"] = await memberships()

            out["levels_only"] = capture(await ac.patch(user_url, json={"reader_project_ids": []}))
            out["after_levels_only"] = await memberships()

            # --- refused, each writing nothing ------------------------------------------
            out["reader_not_member"] = capture(
                await ac.patch(user_url, json={"project_ids": [a], "reader_project_ids": [b]})
            )
            out["unknown_project"] = capture(
                await ac.patch(user_url, json={"project_ids": [a, str(uuid4())], "role": "supervisor"})
            )
            out["stray_field"] = capture(await ac.patch(user_url, json={"projects": [a]}))
            out["after_refusals"] = (await memberships(), await role())

            # --- the project's side ----------------------------------------------------
            out["project_side"] = capture(
                await ac.put(f"/api/projects/{a}", json={"user_ids": [member, other], "reader_user_ids": [member]})
            )
            out["after_project_side"] = await memberships()
            out["seen_after_project_side"] = await seen()
            # A save of A's members that leaves the reader list out keeps the account a reader.
            out["project_keeps"] = capture(await ac.put(f"/api/projects/{a}", json={"user_ids": [member]}))
            out["after_project_keeps"] = await memberships()

            # --- both sides at once ----------------------------------------------------
            rounds = []
            for index in range(CONCURRENT_ROUNDS):
                put, patch = await asyncio.gather(
                    ac.put(f"/api/projects/{a}", json={"user_ids": [member, other]}),
                    ac.patch(user_url, json={"project_ids": [a, b] if index % 2 else [b]}),
                )
                rounds.append((put.status_code, patch.status_code))
            out["concurrent"] = rounds

            async with sessionmaker() as session:
                out["audit"] = [
                    dict(row)
                    for row in (
                        await session.execute(
                            text(
                                """
                                SELECT status_code, request_body,
                                       request_meta->'account_change' AS account_change
                                FROM audit_log_events
                                WHERE method = 'PATCH' AND path = :path
                                ORDER BY created_at, id
                                """
                            ),
                            {"path": user_url},
                        )
                    ).mappings()
                ]
    finally:
        async with sessionmaker() as session:
            for project_id in projects.values():
                await session.execute(text("DELETE FROM projects WHERE id = CAST(:id AS uuid)"), {"id": project_id})
            for account_id in accounts.values():
                await session.execute(text("DELETE FROM users WHERE id = CAST(:id AS uuid)"), {"id": account_id})
            await session.commit()
    return out


@pytest.fixture(scope="module")
def snap() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    return _harness.run_async(lambda: _exercise(uuid4().hex[:12]))


def _change(snap: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"user_id": snap["member"], "email": snap["member_email"], **fields}


def test_the_account_side_sets_each_project_and_its_level(snap: dict[str, Any]) -> None:
    a, b = snap["a"], snap["b"]
    assert snap["set"]["status"] == 200, snap["set"]
    assert (sorted(snap["set"]["json"]["projects"]), snap["set"]["json"]["reader_projects"]) == (sorted([a, b]), [b])
    assert snap["after_set"] == sorted([(a, "analyst"), (b, "reader")])
    # The account list and the project list read the same rows.
    assert snap["seen_after_set"] == {
        "account": (sorted([a, b]), [b]),
        "projects": {"a": (True, False), "b": (True, True)},
    }


def test_a_change_without_a_reader_list_keeps_each_level_and_one_of_it_alone_changes_only_levels(
    snap: dict[str, Any],
) -> None:
    b = snap["b"]
    assert snap["narrowed"]["status"] == 200, snap["narrowed"]
    assert snap["after_narrowed"] == [(b, "reader")]
    assert snap["levels_only"]["status"] == 200, snap["levels_only"]
    assert snap["after_levels_only"] == [(b, "analyst")]


def test_each_refusal_changes_nothing(snap: dict[str, Any]) -> None:
    from backend.app.services.metadata_service import READER_PROJECT_NOT_MEMBER_DETAIL

    assert (snap["reader_not_member"]["status"], snap["reader_not_member"]["json"]) == (
        400,
        {"detail": READER_PROJECT_NOT_MEMBER_DETAIL},
    )
    assert (snap["unknown_project"]["status"], snap["unknown_project"]["json"]) == (
        404,
        {"detail": "Project not found"},
    )
    assert snap["stray_field"]["status"] == 422, snap["stray_field"]
    # As the change before them left it, and the role sent with the unknown project not made.
    assert snap["after_refusals"] == ([(snap["b"], "analyst")], "analyst")


def test_each_change_is_audited_field_by_field_and_a_refusal_not(snap: dict[str, Any]) -> None:
    a, b = snap["a"], snap["b"]
    sequential = snap["audit"][:6]
    assert [(row["status_code"], row["account_change"]) for row in sequential] == [
        (
            200,
            _change(
                snap,
                project_ids={"from": [], "to": sorted([a, b])},
                reader_project_ids={"from": [], "to": [b]},
            ),
        ),
        (200, _change(snap, project_ids={"from": sorted([a, b]), "to": [b]})),
        (200, _change(snap, reader_project_ids={"from": [b], "to": []})),
        (400, None),
        (404, None),
        (422, None),
    ]
    assert sequential[0]["request_body"] == {"project_ids": [a, b], "reader_project_ids": [b]}


def test_the_project_side_sees_and_keeps_what_the_account_side_set(snap: dict[str, Any]) -> None:
    a, b = snap["a"], snap["b"]
    assert snap["project_side"]["status"] == 200, snap["project_side"]
    assert snap["after_project_side"] == sorted([(a, "reader"), (b, "analyst")])
    assert snap["seen_after_project_side"]["account"] == (sorted([a, b]), [a])
    assert snap["project_keeps"]["status"] == 200, snap["project_keeps"]
    assert snap["after_project_keeps"] == sorted([(a, "reader"), (b, "analyst")])


def test_saves_from_both_sides_at_once_all_succeed(snap: dict[str, Any]) -> None:
    # Without one lock for every writer, a project's save and an account's, each replacing the
    # rows the other writes, can wait on each other and one of them fail (a deadlock: 500).
    assert snap["concurrent"] == [(200, 200)] * CONCURRENT_ROUNDS
    # And each account change after them still names what it replaced: every one is a 200 with
    # a from and a to, none a 500.
    concurrent = snap["audit"][6:]
    assert [row["status_code"] for row in concurrent] == [200] * CONCURRENT_ROUNDS
