"""A person approves or refuses a delivery of a watched import folder (E2E, real Postgres and ClickHouse).

The watcher checks each package a pipeline delivers; nothing is imported until a person decides
(``import_delivery_review``). Through the app over HTTP, as the e2e administrator:

- a ready delivery with warnings is refused approval until they are acknowledged; approved, its
  import job is queued as the approver, into the package file's project, the delivery ``queued``;
  the job runs (reading the manifest made from the package file), and the next watch round makes
  the delivery ``imported``; the family's package record names the delivery, its run and its
  package file;
- a dry run of another delivery queues a dry-run job and leaves the delivery as it was;
- a delivery whose folder changed after it was checked is not approved (409), and nothing queued;
- a refusal needs a reason; refused, the delivery is decided: neither refused nor approved again;
- once its family exists, the folder changed is a new delivery that needs attention: approved with
  cancel it is refused (400), with update it is queued;
- each decision is on the ``system:import-inbox`` chain, by the approver, and the chain verifies;
- an unknown delivery answers 404.

The values are synthetic: the families are new and uniquely named. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, job_outcome, one_sample_package
from backend.tests.e2e.test_e2e_import_watcher import write_package

pytestmark = pytest.mark.integration


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import import_watcher
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        project = (
            await session.execute(text("SELECT name FROM projects WHERE id = CAST(:p AS uuid)"), {"p": project_id})
        ).scalar_one()
    names = {key: f"E2E_APPROVE_{key}_{run}" for key in ("NEW", "DRY", "REFUSE", "CHANGED")}
    folders = {key: one_sample_package(base / _harness.PROJECT_NAME / name, name, variants=2, datasets=("snv",)) for key, name in names.items()}
    for key, folder in folders.items():
        write_package(folder, project=project, family_id=names[key])
    roots = [base.resolve()]
    await import_watcher.run_watch_round(sessionmaker, roots)

    out: dict[str, Any] = {"names": names, "project_id": project_id}

    async def delivery_of(name: str) -> dict[str, Any]:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id::text AS id, state, job_id::text AS job_id, package_fingerprint, run_id, marker_sha256 "
                        "FROM import_deliveries WHERE folder_name = :n ORDER BY first_seen_at DESC LIMIT 1"
                    ),
                    {"n": name},
                )
            ).mappings().one()
        return dict(row)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"

        async def approve(name: str, **body: Any) -> dict[str, Any]:
            delivery = await delivery_of(name)
            return capture(
                await client.post(f"/api/family-imports/deliveries/{delivery['id']}/approve", json={"revision": 0, **body})
            )

        new = await delivery_of(names["NEW"])
        out["new_before"] = new
        out["unacknowledged"] = await approve(names["NEW"])
        out["approved"] = await approve(names["NEW"], acknowledge_warnings=True)
        out["approved_again"] = await approve(names["NEW"], acknowledge_warnings=True)
        job_id = out["approved"]["json"]["job"]["_id"]
        worker = f"e2e-approve-{uuid4().hex[:8]}"
        await claim_import_job(job_id, worker)
        await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
        out["job"] = await job_outcome(job_id)
        async with sessionmaker() as session:
            out["job_delivery"] = (
                await session.execute(
                    text("SELECT metadata -> 'delivery' FROM family_import_jobs WHERE id = CAST(:j AS uuid)"), {"j": job_id}
                )
            ).scalar_one()
        out["sync_round"] = await import_watcher.run_watch_round(sessionmaker, roots)
        out["new_after"] = await delivery_of(names["NEW"])
        async with sessionmaker() as session:
            out["record"] = (
                await session.execute(
                    text("SELECT metadata -> 'package_import' -> 'delivery' FROM families WHERE family_id = :f"),
                    {"f": names["NEW"]},
                )
            ).scalar_one()

        out["dry"] = await approve(names["DRY"], dry_run=True, acknowledge_warnings=True)
        out["dry_after"] = await delivery_of(names["DRY"])

        (folders["CHANGED"] / "late.txt").write_text("written after the check\n", encoding="utf-8")
        out["changed"] = await approve(names["CHANGED"], acknowledge_warnings=True)
        async with sessionmaker() as session:
            out["changed_jobs"] = (
                await session.execute(
                    text("SELECT count(*) FROM family_import_jobs WHERE submitted_path = :p"),
                    {"p": str(folders["CHANGED"].resolve())},
                )
            ).scalar_one()

        refuse = await delivery_of(names["REFUSE"])
        out["refuse_blank"] = capture(
            await client.post(f"/api/family-imports/deliveries/{refuse['id']}/refuse", json={"reason": "   "})
        )
        out["refused"] = capture(
            await client.post(
                f"/api/family-imports/deliveries/{refuse['id']}/refuse", json={"reason": "Wrong family: rerun requested."}
            )
        )
        out["refused_again"] = capture(
            await client.post(f"/api/family-imports/deliveries/{refuse['id']}/refuse", json={"reason": "Again."})
        )
        out["approve_refused"] = await approve(names["REFUSE"], acknowledge_warnings=True)

        # The imported folder changes: a new delivery of a family that exists.
        (folders["NEW"] / "notes.txt").write_text("a later output\n", encoding="utf-8")
        write_package(folders["NEW"], project=project, family_id=names["NEW"])
        await import_watcher.run_watch_round(sessionmaker, roots)
        out["existing"] = await delivery_of(names["NEW"])
        async with sessionmaker() as session:
            out["existing_issues"] = (
                await session.execute(
                    text("SELECT issues FROM import_deliveries WHERE id = CAST(:id AS uuid)"), {"id": out["existing"]["id"]}
                )
            ).scalar_one()
        out["existing_cancel"] = await approve(names["NEW"], acknowledge_warnings=True)
        out["existing_update"] = await approve(names["NEW"], conflict_mode="update", acknowledge_warnings=True)

        out["unknown"] = capture(
            await client.post(
                f"/api/family-imports/deliveries/{uuid4()}/approve", json={"revision": 0, "acknowledge_warnings": True}
            )
        )
        out["not_an_id"] = capture(await client.post("/api/family-imports/deliveries/nope/refuse", json={"reason": "x"}))
        out["verified"] = capture(
            await client.get(
                "/api/admin/integrity/verify",
                params={"table": "clinical_audit_events", "family_id": import_watcher.IMPORT_INBOX_CHAIN},
            )
        )
    async with sessionmaker() as session:
        out["events"] = [
            tuple(row)
            for row in (
                await session.execute(
                    text(
                        "SELECT action, actor, after ->> 'folder', after ->> 'reason' FROM clinical_audit_events "
                        "WHERE family_identifier = :chain AND after ->> 'folder' LIKE :run ORDER BY created_at, id"
                    ),
                    {"chain": import_watcher.IMPORT_INBOX_CHAIN, "run": f"%{run}"},
                )
            ).all()
        ]
        await session.execute(
            text(
                "UPDATE family_import_jobs SET status = 'failed', worker_id = NULL, completed_at = now(), "
                "error = 'e2e: left unfinished' WHERE submitted_path LIKE :run AND status IN ('queued', 'validating', 'running')"
            ),
            {"run": f"%{run}"},
        )
        await session.commit()
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_delivery_approval")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, run))


def test_a_ready_delivery_is_approved_once_its_warnings_are_acknowledged(ran) -> None:
    from backend.app.services.import_delivery_review import WARNINGS_NOT_ACKNOWLEDGED

    assert ran["new_before"]["state"] == "ready"
    assert (ran["unacknowledged"]["status"], ran["unacknowledged"]["json"]["detail"]) == (400, WARNINGS_NOT_ACKNOWLEDGED)
    approved = ran["approved"]
    assert approved["status"] == 200, approved
    job = approved["json"]["job"]
    assert (job["dry_run"], job["project_id"], job["requested_by"]) == (False, ran["project_id"], "admin@example.com")
    assert approved["json"]["delivery"]["state"] == "queued"
    assert approved["json"]["delivery"]["job_id"] == job["_id"]
    assert ran["approved_again"]["status"] == 409


def test_the_approved_import_runs_and_the_delivery_is_imported(ran) -> None:
    assert ran["job"]["status"] == "completed", ran["job"]
    assert ran["sync_round"].jobs_ended == 1
    assert ran["new_after"]["state"] == "imported"


def test_the_family_and_the_job_name_the_delivery_it_came_from(ran) -> None:
    before = ran["new_before"]
    expected = {
        "id": before["id"],
        "run_id": before["run_id"],
        "marker_sha256": before["marker_sha256"],
        "package_fingerprint": before["package_fingerprint"],
        # Not corrected in CoGA: revision 0, no corrections, the package as the watcher checked it.
        "revision": 0,
        "package_sha256": ran["record"]["package_sha256"],
        "corrections": [],
        "origin": "watched",
    }
    assert len(ran["record"]["package_sha256"]) == 64
    assert ran["record"] == expected
    # The job's record, written from the validation's metadata as the import ran, keeps it too.
    assert ran["job_delivery"] == expected


def test_a_dry_run_leaves_the_delivery_as_it_was(ran) -> None:
    dry = ran["dry"]
    assert dry["status"] == 200, dry
    assert dry["json"]["job"]["dry_run"] is True
    assert ran["dry_after"]["state"] == "ready"
    assert ran["dry_after"]["job_id"] == dry["json"]["job"]["_id"]


def test_a_delivery_whose_folder_changed_since_it_was_checked_is_not_approved(ran) -> None:
    from backend.app.services.import_delivery_review import DELIVERY_CHANGED

    assert (ran["changed"]["status"], ran["changed"]["json"]["detail"]) == (409, DELIVERY_CHANGED)
    assert ran["changed_jobs"] == 0


def test_a_refusal_needs_a_reason_and_decides_the_delivery(ran) -> None:
    assert ran["refuse_blank"]["status"] == 400
    assert (ran["refused"]["status"], ran["refused"]["json"]["state"]) == (200, "refused")
    assert ran["refused_again"]["status"] == 409
    assert ran["approve_refused"]["status"] == 409


def test_a_new_delivery_of_an_existing_family_is_approved_only_with_update_or_overwrite(ran) -> None:
    assert ran["existing"]["state"] == "needs_attention"
    assert {issue["code"] for issue in ran["existing_issues"]} <= {"existing_family", "existing_samples"}
    assert ran["existing_cancel"]["status"] == 400
    update = ran["existing_update"]
    assert update["status"] == 200, update
    assert update["json"]["job"]["metadata"]["conflict_mode"] == "update"
    assert update["json"]["job"]["metadata"]["requested_family_id"] == ran["names"]["NEW"]


def test_an_unknown_delivery_answers_404(ran) -> None:
    assert ran["unknown"]["status"] == 404
    assert ran["not_an_id"]["status"] == 404


def test_each_decision_is_on_the_chain_by_the_approver_and_the_chain_verifies(ran) -> None:
    decisions = [(action, actor, reason) for action, actor, _folder, reason in ran["events"] if actor != "system:import-watcher"]
    assert ("import_delivery_queued", "admin", None) in decisions
    assert ("import_delivery_dry_run", "admin", None) in decisions
    assert ("import_delivery_refused", "admin", "Wrong family: rerun requested.") in decisions
    assert ("import_delivery_imported", "system:import-watcher") in {(action, actor) for action, actor, _f, _r in ran["events"]}
    assert ran["verified"]["json"]["verified"] is True
