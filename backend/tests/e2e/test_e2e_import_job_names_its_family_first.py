"""A package import writes nothing of a family before its job names that family (E2E).

The report sign-out refuses a family while a package-import job of it is queued, validating
or running, and finds the job by the family the job records or by the family its request
named (test_e2e_signout_during_import.py). An import writes the family's pedigree, samples
and provenance before it takes the family's variant-write locks: until then its job is all
that keeps a sign-out out. The update that recorded the job as ``running`` on its family was
best-effort, so when it failed the import went on to write the family under a job no
sign-out could find, and a sign-out meanwhile froze the family part-way (TF-06 H16).

Over the golden trio this runs real re-import jobs whose request names no family
(``POST /api/family-imports`` without ``family_id``, then the worker's
``run_family_import_job``), so only the family a job records can name it to a sign-out. When
an import makes its first write of the family (its registration), the job row is read and
the report is signed out through ``POST /api/families/FAM_TRIO/report/sign-out`` with every
acknowledgement given:

* the job records its family: at the first write the job reads ``running`` on FAM_TRIO, and
  the sign-out is refused (409 ``import_in_progress``, naming the job);
* Postgres refuses the update that records it (a trigger fails the statement, as a database
  error would): the import writes nothing of the family, neither its registration nor a
  dataset, and the job ends ``failed``, saying why;
* Postgres refuses every update of the job, its end included: nothing is written, and the
  job stays claimed and names no family, as a stopped worker leaves it, so no sign-out finds
  it; the family is as it was.

The trigger fires only for this module's refused packages (a marker in their folder) and is
dropped before the job rows this adds are failed on the way out. Everything runs in ONE
event loop via an in-process ``httpx.ASGITransport`` client (see test_e2e_api_contract.py).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_WORKER = "e2e-names-its-family-worker"
# Every acknowledgement a sign-out can give: none of them opens the import gate.
_SIGNOUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e validation",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e validation",
    "acknowledge_import_incomplete": True,
    "import_incomplete_acknowledgement_reason": "e2e validation",
}

# The update of a job row to one of the trigger's statuses fails, for the packages in a
# folder named e2e-job-update-refused only.
_REFUSED_FOLDER = "e2e-job-update-refused"
_FAULT_FUNCTION = """
CREATE FUNCTION e2e_refuse_import_job_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF strpos(NEW.submitted_path, '/e2e-job-update-refused/') > 0
       AND NEW.status = ANY (TG_ARGV) THEN
        RAISE EXCEPTION 'e2e: the import job row cannot be updated to %', NEW.status;
    END IF;
    RETURN NEW;
END
$$
"""
# The update that records the job as running on its family.
_REFUSE_RUNNING = (
    "CREATE TRIGGER e2e_refuse_import_job_update BEFORE UPDATE ON family_import_jobs "
    "FOR EACH ROW EXECUTE FUNCTION e2e_refuse_import_job_update('running')"
)
# Every update of a claimed job, its end included.
_REFUSE_EVERY_UPDATE = (
    "CREATE TRIGGER e2e_refuse_import_job_update BEFORE UPDATE ON family_import_jobs "
    "FOR EACH ROW EXECUTE FUNCTION "
    "e2e_refuse_import_job_update('validating', 'running', 'completed', 'failed')"
)


def _cap(resp) -> dict:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


async def _refuse_job_updates(trigger: str | None) -> None:
    """Install the trigger (or, with None, drop it)."""
    from backend.app.core.postgres import get_postgres_engine

    async with get_postgres_engine().begin() as conn:
        await conn.exec_driver_sql(
            "DROP TRIGGER IF EXISTS e2e_refuse_import_job_update ON family_import_jobs"
        )
        await conn.exec_driver_sql("DROP FUNCTION IF EXISTS e2e_refuse_import_job_update()")
        if trigger is not None:
            await conn.exec_driver_sql(_FAULT_FUNCTION)
            await conn.exec_driver_sql(trigger)


async def _job(job_id: str) -> dict[str, Any]:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, family_id, worker_id, error, completed_at IS NOT NULL AS ended "
                    "FROM family_import_jobs WHERE id = CAST(:j AS uuid)"
                ),
                {"j": job_id},
            )
        ).mappings().one()
    return dict(row)


async def _registered_at() -> str | None:
    """When the family's package provenance was last written: its registration."""
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        return (
            await session.execute(
                text(
                    "SELECT metadata -> 'package_import' ->> 'registered_at' "
                    "FROM families WHERE family_id = :f"
                ),
                {"f": FAMILY},
            )
        ).scalar_one()


async def _claim(job_id: str) -> None:
    """Claim this job as the worker does (``claim_next_family_import_job``), by its id."""
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                "started_at = now(), heartbeat_at = now(), completed_at = NULL, error = NULL "
                "WHERE id = CAST(:j AS uuid) AND status = 'queued'"
            ),
            {"j": job_id, "w": _WORKER},
        )
        await session.commit()


async def _collect(base: Path, mp: pytest.MonkeyPatch) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import
    from backend.app.services import report_signout_service as rss
    from backend.tests.e2e import _harness

    named = base / "e2e-job-names-family" / FAMILY
    refused = base / _REFUSED_FOLDER / FAMILY
    for root in (named, refused):
        shutil.copytree(_FIXTURE, root)

    await _refuse_job_updates(None)  # a trigger a crashed run left behind
    facts = await _harness.import_golden_trio(named)
    out: dict[str, Any] = {"facts": facts, "runs": {}}
    jobs: list[str] = []

    # The import's first write of the family, and its datasets.
    current: dict[str, Any] = {}
    real_register = family_package_import._ensure_family_from_ped
    real_import_dataset = family_package_import._import_dataset

    async def _register(*args, **kwargs):
        run = current["run"]
        run["first_write"] = {
            "job": await _job(run["job"]),
            "signout": _cap(await current["client"].post(current["signout_url"], json=_SIGNOUT_BODY)),
        }
        return await real_register(*args, **kwargs)

    async def _import_dataset(*args, **kwargs):
        current["run"]["datasets"].append(kwargs["summary"].dataset_type)
        return await real_import_dataset(*args, **kwargs)

    mp.setattr(family_package_import, "_ensure_family_from_ped", _register)
    mp.setattr(family_package_import, "_import_dataset", _import_dataset)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            current["client"] = ac
            current["signout_url"] = f"/api/families/{FAMILY}/report/sign-out"

            async def _import(name: str, root: Path, trigger: str | None) -> None:
                resp = await ac.post(
                    "/api/family-imports",
                    json={
                        "folder_path": str(root),
                        "project_id": facts["project_id"],
                        "dry_run": False,
                        # No family_id: the request does not name the family.
                        "conflict_mode": "overwrite",
                    },
                )
                assert resp.status_code == 200, resp.text
                job_id = str(resp.json()["_id"])
                jobs.append(job_id)
                run: dict[str, Any] = {
                    "job": job_id,
                    "datasets": [],
                    "registered_before": await _registered_at(),
                }
                current["run"] = run
                await _claim(job_id)
                await _refuse_job_updates(trigger)
                try:
                    await family_package_import.run_family_import_job(job_id=job_id, worker_id=_WORKER)
                except Exception as exc:  # noqa: BLE001 - the worker logs it and goes on
                    run["raised"] = type(exc).__name__
                finally:
                    await _refuse_job_updates(None)
                run["registered_after"] = await _registered_at()
                run["job_after"] = await _job(job_id)
                async with get_postgres_sessionmaker()() as session:
                    # What the sign-out's job check finds for the family now.
                    run["found_by_signout"] = await rss._active_import_job(session, FAMILY)
                out["runs"][name] = run

            await _import("records_its_family", named, None)
            await _import("running_refused", refused, _REFUSE_RUNNING)
            await _import("every_update_refused", refused, _REFUSE_EVERY_UPDATE)
    finally:
        await _refuse_job_updates(None)
        # A job this left behind would refuse every later sign-out of the family.
        async with get_postgres_sessionmaker()() as session:
            await session.execute(
                text(
                    "UPDATE family_import_jobs SET status = 'failed', worker_id = NULL, "
                    "completed_at = now(), error = 'e2e: left unfinished' "
                    "WHERE id::text = ANY(:jobs) AND status IN ('queued', 'validating', 'running')"
                ),
                {"jobs": jobs},
            )
            await session.commit()
    out["jobs"] = jobs
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    base = tmp_path_factory.mktemp("golden_import_names_family")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(base, mp))
    assert out["facts"]["completed"] is True, out["facts"]
    return out


def test_a_job_records_its_family_as_running_before_the_import_writes_it(snap) -> None:
    run = snap["runs"]["records_its_family"]
    first_write = run["first_write"]
    assert (first_write["job"]["status"], first_write["job"]["family_id"]) == ("running", FAMILY)
    signout = first_write["signout"]
    assert signout["status"] == 409, signout["text"]
    detail = signout["json"]["detail"]
    assert detail["gate"] == "import_in_progress", detail
    assert detail["import_job"] == {"id": run["job"], "status": "running"}
    # The import then wrote the family, and ended.
    assert run["registered_after"] != run["registered_before"]
    assert "snv" in run["datasets"]
    assert run["job_after"]["status"] == "completed", run["job_after"]


def test_an_import_whose_job_cannot_record_its_family_writes_nothing_of_it(snap) -> None:
    run = snap["runs"]["running_refused"]
    # Before: the import registered the family under a job that named none, and the
    # sign-out made then was signed out (200) from a family part-way through its import.
    assert "first_write" not in run, run.get("first_write")
    assert run["datasets"] == []
    assert run["registered_after"] == run["registered_before"]


def test_its_job_ends_failed_and_says_why(snap) -> None:
    run = snap["runs"]["running_refused"]
    job = run["job_after"]
    assert job["status"] == "failed" and job["ended"], job
    assert job["error"].startswith(f"Stopped before writing family {FAMILY}:"), job["error"]
    assert "nothing of it was written" in job["error"], job["error"]
    assert run["found_by_signout"] is None


def test_when_not_even_its_end_can_be_recorded_nothing_is_written(snap) -> None:
    run = snap["runs"]["every_update_refused"]
    assert "first_write" not in run, run.get("first_write")
    assert run["datasets"] == []
    assert run["registered_after"] == run["registered_before"]
    # The worker logs the failure; the job stays claimed and names no family, so no
    # sign-out finds it, and none needs to: the family is as it was. A worker claims it
    # again once its heartbeat is stale and runs it from the start.
    assert "raised" in run
    job = run["job_after"]
    assert (job["status"], job["family_id"], job["worker_id"]) == ("validating", None, _WORKER), job
    assert run["found_by_signout"] is None
