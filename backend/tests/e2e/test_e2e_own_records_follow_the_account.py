"""A Microsoft account keeps its import jobs and presets when its address and user name change (E2E, real Postgres, SEC-32).

A Microsoft sign-in follows the token's e-mail address and user name (SEC-4). The records a
user keeps as their own were found by these: an import job's requester by the e-mail address
the job was requested under, and a user's filter presets by their user name. So a colleague
whose address changed between an import's request and its run had the import refused
("Requesting user no longer exists"), and once another colleague had signed in with the address
they left behind, the import ran as that colleague; the presets went out of the first
colleague's lists and into the other's. Each is now kept by the account's id.

On real Postgres, through the app over HTTP (an in-process ``httpx.ASGITransport`` client on
one event loop, see test_e2e_api_contract.py for why), a colleague, an administrator in the e2e
project, signs in with Microsoft, saves a small-variant preset and two structural-variant
presets (one for a family, one reusable), and queues a dry-run import. Their next sign-in
carries a new address and user name, and a second colleague, a viewer in the same project,
then signs in with the address and user name the first left behind. The first colleague's
presets stay theirs, listed and deleted by them; the second lists none of them and deletes
none. The import runs as the first colleague's account, and keeps the address it was
requested under as its requester's. Made a viewer, the first colleague still finds the job in
their own job list, and the second colleague does not (the service's list: only an
administrator reaches the import routes, and an administrator sees every job).

The directory is a stand-in made at run time (``_microsoft_sign_in_fakes.FakeDirectory``),
and the import is replaced by one that records the account it runs as: what this tests is
whose import it is, not the import. The values are synthetic, and what the test makes is
deleted at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def _cap(response) -> dict[str, Any]:
    out: dict[str, Any] = {"status": response.status_code}
    if response.content:
        out["json"] = response.json()
    return out


async def _exercise(run: str, directory, patch: pytest.MonkeyPatch) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.access_control import RecordNotVisible
    from backend.app.services.family_package_jobs import get_family_import_job, list_family_import_jobs
    from backend.app.services.metadata_service import get_current_user_by_email
    from backend.tests.e2e import _harness

    family_id = f"E2E_SEC32_{run}"
    old, new = f"colleague-{run}@example.org", f"colleague-renamed-{run}@example.org"
    colleague_oid, successor_oid = str(uuid4()), str(uuid4())
    names = {
        "small": f"SEC-32 small {run}",
        "sv_family": f"SEC-32 SV for the family {run}",
        "sv_global": f"SEC-32 SV reusable {run}",
    }
    out: dict[str, Any] = {"run": run, "old": old, "new": new, "names": names}

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        family_uuid = (
            await session.execute(
                text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": family_id}
            )
        ).scalar_one()
        await session.execute(
            text("INSERT INTO family_projects (family_id, project_id) VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"),
            {"f": family_uuid, "p": project_id},
        )
        await session.commit()

    def bearer(object_id: str, email: str) -> dict[str, str]:
        token = directory.access_token(
            object_id, email=email, preferred_username=email, given_name="Co", family_name=f"League {run}"
        )
        return {"Authorization": f"Bearer {token}"}

    # The import, replaced for this test's job by one that records the account it runs as.
    ran_as: list[Any] = []
    real_import = package_import.execute_family_package_import

    async def recording_import(session, **kwargs):
        if kwargs.get("job_id") != out.get("job_id"):
            return await real_import(session, **kwargs)
        ran_as.append(kwargs["user"])
        return SimpleNamespace(error=None, family_id=None, validation=None, datasets=[], logs=["Recorded."])

    patch.setattr(package_import, "execute_family_package_import", recording_import)

    async def make_member(user_id: str, role: str) -> None:
        async with sessionmaker() as session:
            await session.execute(
                text("UPDATE users SET role = :role WHERE id = CAST(:u AS uuid)"), {"role": role, "u": user_id}
            )
            await session.execute(
                text(
                    "INSERT INTO project_users (project_id, user_id) VALUES (CAST(:p AS uuid), CAST(:u AS uuid)) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"p": project_id, "u": user_id},
            )
            await session.commit()

    small = "/api/auth/small-variant-filter-presets"
    small_in_family = f"/api/families/{family_id}/small-variant-filter-presets"
    sv = f"/api/families/{family_id}/structural-variant-filter-presets"
    colleague_id = successor_id = None
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://e2e") as client:
            # The first sign-in makes the colleague's account; it is made an administrator in
            # the e2e project, as an administrator queues imports.
            colleague = bearer(colleague_oid, old)
            me = await client.get("/api/auth/me", headers=colleague)
            out["first_sign_in"] = _cap(me)
            colleague_id = me.json()["id"]
            await make_member(colleague_id, "admin")

            out["saved"] = {
                "small": _cap(
                    await client.post(
                        small_in_family, json={"name": names["small"], "filters": {"impact": ["HIGH"]}}, headers=colleague
                    )
                ),
                "sv_family": _cap(
                    await client.post(sv, json={"name": names["sv_family"], "scope": "family"}, headers=colleague)
                ),
                "sv_global": _cap(
                    await client.post(sv, json={"name": names["sv_global"], "scope": "global"}, headers=colleague)
                ),
            }
            queued = await client.post(
                "/api/family-imports",
                json={"folder_path": f"/data/families/{family_id}", "project_id": project_id, "dry_run": True},
                headers=colleague,
            )
            out["queued"] = _cap(queued)
            out["job_id"] = job_id = queued.json()["_id"]

            # The next sign-in carries a new address and user name; then a second colleague
            # signs in with the ones the first left behind, and is a viewer in the project.
            renamed = bearer(colleague_oid, new)
            out["renamed"] = _cap(await client.get("/api/auth/me", headers=renamed))
            successor = bearer(successor_oid, old)
            me = await client.get("/api/auth/me", headers=successor)
            out["successor_sign_in"] = _cap(me)
            successor_id = me.json()["id"]
            await make_member(successor_id, "viewer")

            out["colleague_lists"] = {
                "small": _cap(await client.get(small, headers=renamed)),
                "small_in_family": _cap(await client.get(small_in_family, headers=renamed)),
                "sv": _cap(await client.get(sv, headers=renamed)),
                "job": _cap(await client.get(f"/api/family-imports/{job_id}", headers=renamed)),
            }
            out["successor_lists"] = {
                "small": _cap(await client.get(small, headers=successor)),
                "sv": _cap(await client.get(sv, headers=successor)),
            }
            preset_ids = {key: (saved.get("json") or {}).get("_id", str(uuid4())) for key, saved in out["saved"].items()}
            out["successor_deletes"] = {
                "small": _cap(await client.delete(f"{small}/{preset_ids['small']}", headers=successor)),
                "sv_family": _cap(await client.delete(f"{sv}/{preset_ids['sv_family']}", headers=successor)),
                "sv_global": _cap(await client.delete(f"{sv}/{preset_ids['sv_global']}", headers=successor)),
            }

            # The worker runs the job (its claim, for this job alone).
            worker_id = f"e2e-sec32-{run}"
            async with sessionmaker() as session:
                await session.execute(
                    text(
                        "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                        "started_at = now(), heartbeat_at = now() WHERE id = CAST(:id AS uuid)"
                    ),
                    {"w": worker_id, "id": job_id},
                )
                await session.commit()
            try:
                await package_import.run_family_import_job(job_id=job_id, worker_id=worker_id)
            except Exception as exc:  # noqa: BLE001 - recorded for the assertions
                out["run_error"] = str(exc)
            out["ran_as"] = [(user.id, user.email) for user in ran_as]
            out["job_after_run"] = _cap(await client.get(f"/api/family-imports/{job_id}", headers=renamed))

            # Made a viewer, each colleague's own job list, as the service gives it.
            await make_member(colleague_id, "viewer")
            async with sessionmaker() as session:
                as_viewer = await get_current_user_by_email(session, new)
                other = await get_current_user_by_email(session, old)
                assert as_viewer is not None and other is not None

                async def job_seen_by(user) -> str:
                    try:
                        await get_family_import_job(session, job_id=job_id, user=user)
                    except RecordNotVisible:
                        return "not visible"
                    return "visible"

                out["own_jobs"] = [job.id for job in await list_family_import_jobs(session, user=as_viewer, limit=100)]
                out["own_job"] = await job_seen_by(as_viewer)
                out["other_jobs"] = [job.id for job in await list_family_import_jobs(session, user=other, limit=100)]
                out["other_job"] = await job_seen_by(other)

            out["colleague_deletes"] = {
                "small": _cap(await client.delete(f"{small}/{preset_ids['small']}", headers=renamed)),
                "sv_family": _cap(await client.delete(f"{sv}/{preset_ids['sv_family']}", headers=renamed)),
                "sv_global": _cap(await client.delete(f"{sv}/{preset_ids['sv_global']}", headers=renamed)),
            }
            out["colleague_lists_after"] = {
                "small": _cap(await client.get(small, headers=renamed)),
                "sv": _cap(await client.get(sv, headers=renamed)),
            }
    finally:
        async with sessionmaker() as session:
            for table in ("small_variant_filter_presets", "structural_variant_filter_presets"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE name = ANY(CAST(:names AS text[]))"), {"names": list(names.values())}
                )
            if out.get("job_id"):
                await session.execute(
                    text("DELETE FROM family_import_jobs WHERE id = CAST(:id AS uuid)"), {"id": out["job_id"]}
                )
            await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": family_id})
            await session.execute(
                text("DELETE FROM users WHERE azure_object_id IN (CAST(:c AS uuid), CAST(:s AS uuid))"),
                {"c": colleague_oid, "s": successor_oid},
            )
            await session.commit()
    out["colleague_id"], out["successor_id"] = colleague_id, successor_id
    return out


@pytest.fixture(scope="module")
def ran() -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests._microsoft_sign_in_fakes import FakeDirectory
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    with pytest.MonkeyPatch.context() as patch:
        directory = FakeDirectory().serve(patch).configure(patch, settings)
        return _harness.run_async(lambda: _exercise(run, directory, patch))


def _names(listed: dict[str, Any]) -> list[str]:
    assert listed["status"] == 200, listed
    return sorted(preset["name"] for preset in listed["json"])


def test_the_colleague_saved_presets_and_queued_an_import_then_signed_in_renamed(ran) -> None:
    assert ran["first_sign_in"]["json"]["email"] == ran["old"]
    assert {key: saved["status"] for key, saved in ran["saved"].items()} == {
        "small": 200,
        "sv_family": 200,
        "sv_global": 200,
    }
    assert ran["queued"]["status"] == 200 and ran["queued"]["json"]["requested_by"] == ran["old"]
    # The same account, with the token's new address; the old one went to a new account.
    assert (ran["renamed"]["json"]["id"], ran["renamed"]["json"]["email"]) == (ran["colleague_id"], ran["new"])
    assert ran["successor_sign_in"]["status"] == 200
    assert ran["successor_sign_in"]["json"]["email"] == ran["old"]
    assert ran["successor_id"] != ran["colleague_id"]


def test_the_presets_stay_the_colleagues_after_the_change(ran) -> None:
    names = ran["names"]
    lists = ran["colleague_lists"]
    # Before, each list read the presets of the user name now another account's: none.
    assert _names(lists["small"]) == [names["small"]]
    assert _names(lists["small_in_family"]) == [names["small"]]
    assert _names(lists["sv"]) == sorted([names["sv_family"], names["sv_global"]])
    # Shown under the user name they were saved with.
    assert {preset["owner"] for preset in lists["small"]["json"] + lists["sv"]["json"]} == {ran["old"]}


def test_the_account_with_the_old_address_and_user_name_gets_none_of_them(ran) -> None:
    # Before, it listed the colleague's presets as its own, and deleted them.
    assert _names(ran["successor_lists"]["small"]) == []
    assert _names(ran["successor_lists"]["sv"]) == []
    assert {key: deleted["status"] for key, deleted in ran["successor_deletes"].items()} == {
        "small": 404,
        "sv_family": 404,
        "sv_global": 404,
    }


def test_the_colleague_deletes_their_presets(ran) -> None:
    assert {key: deleted["status"] for key, deleted in ran["colleague_deletes"].items()} == {
        "small": 204,
        "sv_family": 204,
        "sv_global": 204,
    }
    assert _names(ran["colleague_lists_after"]["small"]) == []
    assert _names(ran["colleague_lists_after"]["sv"]) == []


def test_the_import_runs_as_the_account_that_requested_it(ran) -> None:
    # Before, it ran as the account that had the address by then, or failed with
    # "Requesting user no longer exists".
    assert "run_error" not in ran, ran.get("run_error")
    assert ran["ran_as"] == [(ran["colleague_id"], ran["new"])]
    job = ran["job_after_run"]
    assert job["status"] == 200 and job["json"]["status"] == "completed"
    # The job keeps the address it was requested under, as its requester's.
    assert job["json"]["requested_by"] == ran["old"]
    assert ran["colleague_lists"]["job"]["status"] == 200


def test_the_job_stays_in_the_colleagues_own_list_and_out_of_the_other_accounts(ran) -> None:
    # Before, each read the jobs of the address the account has now.
    assert ran["job_id"] in ran["own_jobs"]
    assert ran["own_job"] == "visible"
    assert ran["job_id"] not in ran["other_jobs"]
    assert ran["other_job"] == "not visible"
