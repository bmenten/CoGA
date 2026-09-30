"""No sign-out of a family while its package import is queued or running (E2E).

A package import writes a family over minutes: its pedigree and samples, then dataset after
dataset, committing as it goes. It holds the family's variant-write locks from its first
dataset to its end, but the sign-out took none of them, and the family is flagged
import-incomplete only once an import has failed. So a sign-out while an import ran froze a
half-imported family into a signed record as if it were complete (TF-06 H16).

Over the golden trio this runs a real re-import job (``POST /api/family-imports``, then the
worker's ``run_family_import_job``) and signs the report out through
``POST /api/families/FAM_TRIO/report/sign-out`` with every acknowledgement given:

* while the job is queued, and while it runs, held inside its first dataset: refused, 409
  ``import_in_progress``, naming the job;
* while another writer holds the family's variant-write locks (as an upload or an admin
  delete does): refused, 409 ``variant_writes_in_progress``, at once;
* when an import is claimed while the sign-out reads its snapshot: refused, 409
  ``import_in_progress``, and the import writes no dataset until the sign-out has ended;
* once the import has finished: signed out.

Nothing is written for a refused sign-out: no ``report_signouts`` row, no audit event. The
job rows this adds end ``completed`` (or are failed on the way out). Everything runs in ONE
event loop via an in-process ``httpx.ASGITransport`` client (see test_e2e_api_contract.py).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_WORKER = "e2e-signout-worker"
# Every acknowledgement a sign-out can give: none of them opens this gate.
_SIGNOUT_BODY = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e validation",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e validation",
    "acknowledge_import_incomplete": True,
    "import_incomplete_acknowledgement_reason": "e2e validation",
}


def _cap(resp) -> dict:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


_COUNTS = {
    "report_signouts": text("SELECT count(*) FROM report_signouts WHERE family_identifier = :f"),
    "clinical_audit_events": text(
        "SELECT count(*) FROM clinical_audit_events WHERE family_identifier = :f"
    ),
}


async def _count(table: str) -> int:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        return int((await session.execute(_COUNTS[table], {"f": FAMILY})).scalar_one())


async def _job_status(job_id: str) -> str:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        return str(
            (
                await session.execute(
                    text("SELECT status FROM family_import_jobs WHERE id = CAST(:j AS uuid)"),
                    {"j": job_id},
                )
            ).scalar_one()
        )


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


async def _wait_for(predicate, *, timeout: float = 60.0) -> None:
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.05)


async def _collect(root: Path, mp: pytest.MonkeyPatch) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import
    from backend.app.services import report_signout_service as rss
    from backend.app.services.family_variant_write_lock import hold_family_variant_writes
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    out: dict[str, Any] = {"facts": facts, "responses": {}, "counts": {}}
    r = out["responses"]
    jobs: list[str] = []

    # The import's datasets: each call is recorded, and the first one of a run can be held.
    started: list[str] = []
    hold_dataset = asyncio.Event()
    dataset_held = asyncio.Event()
    release_dataset = asyncio.Event()
    real_import_dataset = family_package_import._import_dataset

    async def _import_dataset(*args, **kwargs):
        started.append(kwargs["summary"].dataset_type)
        if hold_dataset.is_set():
            hold_dataset.clear()
            dataset_held.set()
            await release_dataset.wait()
        return await real_import_dataset(*args, **kwargs)

    mp.setattr(family_package_import, "_import_dataset", _import_dataset)

    async def _signouts() -> dict[str, int]:
        return {table: await _count(table) for table in _COUNTS}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            signout_url = f"/api/families/{FAMILY}/report/sign-out"

            async def _queue() -> str:
                resp = await ac.post(
                    "/api/family-imports",
                    json={
                        "folder_path": str(root),
                        "project_id": facts["project_id"],
                        "dry_run": False,
                        "family_id": FAMILY,
                        "conflict_mode": "overwrite",
                    },
                )
                assert resp.status_code == 200, resp.text
                job_id = str(resp.json()["_id"])
                jobs.append(job_id)
                return job_id

            out["counts"]["before"] = await _signouts()

            # 1. A re-import queued for the family, and the same import running.
            job_id = await _queue()
            r["queued"] = _cap(await ac.post(signout_url, json=_SIGNOUT_BODY))
            await _claim(job_id)
            hold_dataset.set()
            run = asyncio.create_task(
                family_package_import.run_family_import_job(job_id=job_id, worker_id=_WORKER)
            )
            async with asyncio.timeout(60):
                await dataset_held.wait()
            out["running_status"] = await _job_status(job_id)
            r["running"] = _cap(await ac.post(signout_url, json=_SIGNOUT_BODY))
            release_dataset.set()
            await asyncio.wait_for(run, timeout=120)
            out["first_job_status"] = await _job_status(job_id)
            out["counts"]["after_refusals"] = await _signouts()

            # 2. Another writer holds the family's variants (an upload, an admin delete).
            async with hold_family_variant_writes(family_uuid):
                async with asyncio.timeout(10):  # refused at once, not after the write
                    r["variant_writes"] = _cap(await ac.post(signout_url, json=_SIGNOUT_BODY))

            # 3. An import claimed while the sign-out reads its snapshot. It commits its job
            # as running before it writes anything; its first dataset then waits for the
            # sign-out's share of the family's locks.
            real_drift = rss.evaluate_classification_drift
            race: dict[str, Any] = {}

            async def _drift_while_an_import_starts(*args, **kwargs):
                if "job" not in race:
                    race["job"] = await _queue()
                    await _claim(race["job"])
                    started.clear()
                    race["run"] = asyncio.create_task(
                        family_package_import.run_family_import_job(
                            job_id=race["job"], worker_id=_WORKER
                        )
                    )

                    async def _running() -> bool:
                        return await _job_status(race["job"]) == "running"

                    await _wait_for(_running)
                    # Long enough for an unhindered import to reach its first dataset.
                    await asyncio.sleep(1.0)
                    race["datasets_during_snapshot"] = list(started)
                return await real_drift(*args, **kwargs)

            mp.setattr(rss, "evaluate_classification_drift", _drift_while_an_import_starts)
            r["import_started"] = _cap(await ac.post(signout_url, json=_SIGNOUT_BODY))
            mp.setattr(rss, "evaluate_classification_drift", real_drift)
            await asyncio.wait_for(race["run"], timeout=120)
            out["race_job"] = race["job"]
            out["race_job_status"] = await _job_status(race["job"])
            out["datasets_during_snapshot"] = race["datasets_during_snapshot"]
            out["counts"]["after_race"] = await _signouts()

            # 4. Nothing is written any more: signed out.
            r["after"] = _cap(await ac.post(signout_url, json=_SIGNOUT_BODY))
            out["counts"]["after"] = await _signouts()
    finally:
        release_dataset.set()
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

    root = tmp_path_factory.mktemp("golden_signout_import") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(root, mp))
    assert out["facts"]["completed"] is True, out["facts"]
    return out


def _refused(snap: dict, key: str, gate: str) -> dict:
    resp = snap["responses"][key]
    assert resp["status"] == 409, resp["text"]
    detail = resp["json"]["detail"]
    assert detail["gate"] == gate, detail
    return detail


def test_a_queued_import_of_the_family_refuses_its_sign_out(snap) -> None:
    detail = _refused(snap, "queued", "import_in_progress")
    assert detail["import_job"] == {"id": snap["jobs"][0], "status": "queued"}
    assert f"import job {snap['jobs'][0]}" in detail["message"]


def test_a_running_import_of_the_family_refuses_its_sign_out(snap) -> None:
    assert snap["running_status"] == "running"
    detail = _refused(snap, "running", "import_in_progress")
    assert detail["import_job"] == {"id": snap["jobs"][0], "status": "running"}
    assert "in progress" in detail["message"]
    assert snap["first_job_status"] == "completed"


def test_a_writer_holding_the_familys_variants_refuses_its_sign_out(snap) -> None:
    detail = _refused(snap, "variant_writes", "variant_writes_in_progress")
    assert "being written" in detail["message"]


def test_an_import_claimed_during_the_snapshot_refuses_the_sign_out(snap) -> None:
    detail = _refused(snap, "import_started", "import_in_progress")
    assert detail["import_job"]["id"] == snap["race_job"]
    # The sign-out shared the family's locks while it read: the import's datasets waited.
    assert snap["datasets_during_snapshot"] == []
    assert snap["race_job_status"] == "completed"


def test_a_refused_sign_out_writes_nothing(snap) -> None:
    counts = snap["counts"]
    assert counts["after_refusals"] == counts["before"], counts
    assert counts["after_race"] == counts["before"], counts


def test_once_the_imports_have_finished_the_report_is_signed_out(snap) -> None:
    resp = snap["responses"]["after"]
    assert resp["status"] == 200, resp["text"]
    counts = snap["counts"]
    assert counts["after"]["report_signouts"] == counts["before"]["report_signouts"] + 1
