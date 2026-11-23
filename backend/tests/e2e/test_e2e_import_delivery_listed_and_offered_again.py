"""CoGA lists a file the import reads that the package file does not, and offers a failed import again (E2E, real Postgres and ClickHouse).

The owner's decisions of 2026-11-23. Through the watcher's rounds, the app over HTTP (an
in-process ``httpx.ASGITransport`` client, see test_e2e_api_contract.py) as the e2e administrator,
and the import job as a worker runs it, for synthetic one-sample packages:

- a file put into the folder after the pipeline wrote its package file, which a dataset reads (a
  coverage BED here, as a CRAM added later), is listed by CoGA in the package with its size, its
  SHA-256 kept: the delivery is ready, with the warning ``package_files_proposed``, never blocked
  by ``package_file_not_listed``; the package endpoint names the file; approved and imported, the
  family's raw-file row of it holds that SHA-256;
- a delivery whose import job failed is ``failed``, and in the same round checked again and
  offered again (``ready``, the failed job still named), recorded as
  ``import_delivery_offered_again``; approved again, its import is queued;
- a failed delivery whose folder changed since is ``superseded``, and the folder's new delivery is
  the one listed; one whose folder is gone is ``gone`` after two rounds;
- a lock file that comes and goes (a document opened on the share) supersedes the delivery, and its
  going reopens it, superseding the lock file's delivery;
- an approval of a delivery with warnings names them by their SHA-256: unnamed it is refused
  (400), named as they were before a change (409);
- an open delivery of an existing family is not checked again while nothing of the family changes,
  and is once a member's status changes in CoGA;
- the chain verifies.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import acknowledged, claim_import_job, job_outcome
from backend.tests.e2e.test_e2e_import_watcher import package_folder, write_package

pytestmark = pytest.mark.integration


async def _fail_job(job_id: str) -> None:
    """End the import job ``job_id`` failed before it wrote anything, as one whose database ran out
    of space at its first write ends."""
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE family_import_jobs SET status = 'failed', worker_id = NULL, completed_at = now(), "
                "error = 'e2e: the import failed' WHERE id = CAST(:j AS uuid)"
            ),
            {"j": job_id},
        )
        await session.commit()


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
    names = {key: f"E2E_LISTED_{key}_{run}" for key in ("LATER", "FAILED", "MOVED", "LOCK", "GONE")}
    folders = {key: package_folder(base, name, project) for key, name in names.items()}
    for key, folder in folders.items():
        write_package(folder, project=project, family_id=names[key])
    # A file the package file does not list: the delivery warns of it.
    (folders["LOCK"] / "notes.txt").write_text("a note of the lab\n", encoding="utf-8")
    # The pipeline's file names a coverage dataset whose BED was put into the folder after it was
    # written: its package file lists the VCF and the PED, not the BED.
    later = folders["LATER"]
    sample = f"{names['LATER']}_S1"
    write_package(
        later,
        project=project,
        family_id=names["LATER"],
        full=False,
        members=[{"sample_id": sample, "sex": "male", "role": "proband", "clinical_status": "affected"}],
        datasets={
            "snv": {"family_vcf": "snv/family.vcf"},
            "coverage": {"per_sample": {sample: {"bed": "coverage/S1.bed", "content": "read_depth"}}},
        },
    )
    (later / "coverage").mkdir()
    (later / "coverage" / "S1.bed").write_text("1\t0\t1000\t31.0\n1\t1000\t2000\t29.5\n", encoding="utf-8")
    bed_sha256 = hashlib.sha256((later / "coverage" / "S1.bed").read_bytes()).hexdigest()
    roots = [base.resolve()]
    await import_watcher.run_watch_round(sessionmaker, roots)

    out: dict[str, Any] = {"names": names, "bed_sha256": bed_sha256, "folders": folders}

    async def delivery_of(name: str) -> dict[str, Any]:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id::text AS id, state, issues, warnings, package, package_check, job_id::text AS job_id "
                        "FROM import_deliveries WHERE folder_name = :n ORDER BY first_seen_at DESC, id LIMIT 1"
                    ),
                    {"n": name},
                )
            ).mappings().one()
        return dict(row)

    async def deliveries_of(name: str) -> list[tuple[str, str]]:
        async with sessionmaker() as session:
            return [
                (str(row[0]), str(row[1]))
                for row in (
                    await session.execute(
                        text("SELECT id::text, state FROM import_deliveries WHERE folder_name = :n ORDER BY first_seen_at, id"),
                        {"n": name},
                    )
                ).all()
            ]

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"

        async def approve(name: str) -> dict[str, Any]:
            delivery = await delivery_of(name)
            return capture(
                await client.post(
                    f"/api/family-imports/deliveries/{delivery['id']}/approve",
                    json=await acknowledged(delivery["id"]),
                )
            )

        # A file the import reads that the package file does not list.
        out["later"] = await delivery_of(names["LATER"])
        out["later_package"] = capture(await client.get(f"/api/family-imports/deliveries/{out['later']['id']}/package"))
        approved = await approve(names["LATER"])
        out["later_approved"] = approved
        job_id = approved["json"]["job"]["_id"]
        worker = f"e2e-listed-{uuid4().hex[:8]}"
        await claim_import_job(job_id, worker)
        await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
        out["later_job"] = await job_outcome(job_id)
        async with sessionmaker() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT r.storage_path, r.sha256 FROM raw_import_files r JOIN families f ON f.id = r.family_id "
                        "WHERE f.family_id = :f AND r.dataset = 'coverage'"
                    ),
                    {"f": names["LATER"]},
                )
            ).mappings().all()
        out["later_recorded"] = {
            Path(row["storage_path"]).relative_to(later.resolve()).as_posix(): row["sha256"] for row in rows
        }

        # A failed import, offered again; and one whose folder changed since.
        failed_jobs: dict[str, str] = {}
        for key in ("FAILED", "MOVED", "GONE"):
            approved = await approve(names[key])
            out[f"{key.lower()}_approved"] = approved
            failed_jobs[key] = approved["json"]["job"]["_id"]
            await _fail_job(failed_jobs[key])
        out["failed_jobs"] = failed_jobs
        (folders["MOVED"] / "notes.txt").write_text("written after the failed import\n", encoding="utf-8")
        shutil.rmtree(folders["GONE"])
        out["round"] = await import_watcher.run_watch_round(sessionmaker, roots)
        out["offered"] = await delivery_of(names["FAILED"])
        out["moved"] = await deliveries_of(names["MOVED"])
        out["list"] = capture(await client.get("/api/family-imports/deliveries", params={"limit": 200}))
        out["approved_again"] = await approve(names["FAILED"])

        # A lock file comes and goes: its delivery supersedes this one, and its going reopens it.
        lock_file = folders["LOCK"] / "~$qc.xlsx"
        lock_file.write_text("opened on the share\n", encoding="utf-8")
        await import_watcher.run_watch_round(sessionmaker, roots)
        out["lock_during"] = await deliveries_of(names["LOCK"])
        lock_file.unlink()
        out["lock_round"] = await import_watcher.run_watch_round(sessionmaker, roots)
        out["lock_after"] = await deliveries_of(names["LOCK"])
        out["gone"] = await deliveries_of(names["GONE"])

        # The warnings an approval acknowledges are the ones its approver saw.
        lock_id = out["lock_after"][0][0]
        lock_url = f"/api/family-imports/deliveries/{lock_id}/approve"
        out["warnings_unnamed"] = capture(
            await client.post(lock_url, json={"revision": 0, "dry_run": True, "acknowledge_warnings": True})
        )
        out["warnings_stale"] = capture(
            await client.post(
                lock_url,
                json={
                    "revision": 0,
                    "dry_run": True,
                    "acknowledge_warnings": True,
                    "warnings_sha256": import_watcher.warnings_sha256([]),
                },
            )
        )
        out["warnings_named"] = capture(await client.post(lock_url, json=await acknowledged(lock_id, dry_run=True)))

        # An open delivery of an existing family: checked again once a member's status changes.
        copy = later.parent / f"E2E_LISTED_COPY_{run}"
        shutil.copytree(later, copy)
        await import_watcher.run_watch_round(sessionmaker, roots)
        await import_watcher.run_watch_round(sessionmaker, roots)
        out["copy_before"] = await delivery_of(copy.name)
        async with sessionmaker() as session:
            out["copy_family_state"] = (
                await session.execute(
                    text("SELECT validation ->> 'family_state' FROM import_deliveries WHERE id = CAST(:id AS uuid)"),
                    {"id": out["copy_before"]["id"]},
                )
            ).scalar_one()
            await session.execute(
                text(
                    "UPDATE family_members SET clinical_status = 'unaffected', affected = false "
                    "WHERE family_id = (SELECT id FROM families WHERE family_id = :f)"
                ),
                {"f": names["LATER"]},
            )
            await session.commit()
        out["copy_round"] = await import_watcher.run_watch_round(sessionmaker, roots)

        out["verified"] = capture(
            await client.get(
                "/api/admin/integrity/verify",
                params={"table": "clinical_audit_events", "family_id": import_watcher.IMPORT_INBOX_CHAIN},
            )
        )
    async with sessionmaker() as session:
        out["events"] = [
            (str(row[0]), str(row[1]), dict(row[2] or {}))
            for row in (
                await session.execute(
                    text(
                        "SELECT action, after ->> 'folder', after FROM clinical_audit_events "
                        "WHERE family_identifier = :chain AND after ->> 'folder' LIKE :run ORDER BY created_at, id"
                    ),
                    {"chain": import_watcher.IMPORT_INBOX_CHAIN, "run": f"%{run}"},
                )
            ).all()
        ]
        # Nothing of this module's jobs is left for another module's rounds.
        await session.execute(
            text(
                "UPDATE family_import_jobs SET status = 'failed', worker_id = NULL, completed_at = now(), "
                "error = 'e2e: left unfinished' WHERE submitted_path LIKE :run AND status IN ('queued', 'validating', 'running')"
            ),
            {"run": f"%{run}"},
        )
        await session.execute(
            text("UPDATE import_deliveries SET state = 'refused' WHERE folder_name LIKE :run AND state <> 'imported'"),
            {"run": f"%{run}"},
        )
        await session.commit()
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("listed_and_offered_again")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    return _harness.run_async(lambda: _exercise(base, uuid4().hex[:10].upper()))


def test_a_file_the_import_reads_that_the_package_file_does_not_list_is_listed_by_coga(ran) -> None:
    later = ran["later"]
    assert later["state"] == "ready", later["issues"]
    assert "package_file_not_listed" not in {issue["code"] for issue in later["issues"]}
    assert "package_files_proposed" in {warning["code"] for warning in later["warnings"]}
    listed = {entry["path"]: entry for entry in later["package"]["files"]}
    assert listed["coverage/S1.bed"] == {"path": "coverage/S1.bed", "size": len("1\t0\t1000\t31.0\n1\t1000\t2000\t29.5\n")}
    assert later["package_check"]["proposed_files"] == ["coverage/S1.bed"]
    assert later["package_check"]["sha256"]["coverage/S1.bed"] == ran["bed_sha256"]
    package = ran["later_package"]
    assert package["status"] == 200, package
    assert package["json"]["proposed_files"] == ["coverage/S1.bed"]
    assert {entry["path"] for entry in package["json"]["package"]["files"]} >= {"coverage/S1.bed", "snv/family.vcf"}


def test_the_listed_file_is_imported_with_the_sha256_coga_read(ran) -> None:
    assert ran["later_approved"]["status"] == 200, ran["later_approved"]
    assert ran["later_job"]["status"] == "completed", ran["later_job"]
    assert ran["later_recorded"] == {"coverage/S1.bed": ran["bed_sha256"]}


def test_a_failed_import_is_offered_again_in_the_round_that_finds_it_failed(ran) -> None:
    assert ran["failed_approved"]["status"] == 200, ran["failed_approved"]
    assert ran["round"].jobs_ended >= 2
    assert ran["round"].offered_again.get("ready", 0) >= 1
    offered = ran["offered"]
    # Checked again: a new family still, nothing of it written, so ready; its failed job named.
    assert offered["state"] == "ready", offered["issues"]
    assert offered["job_id"] == ran["failed_jobs"]["FAILED"]
    listed = {item["_id"]: item for item in ran["list"]["json"]}
    assert listed[offered["id"]]["job_status"] == "failed"
    approved_again = ran["approved_again"]
    assert approved_again["status"] == 200, approved_again
    assert approved_again["json"]["delivery"]["state"] == "queued"
    assert approved_again["json"]["job"]["_id"] != ran["failed_jobs"]["FAILED"]


def test_a_failed_delivery_whose_folder_changed_is_superseded_by_the_folders_new_delivery(ran) -> None:
    assert [state for _id, state in ran["moved"]] == ["superseded", "ready"]


def test_a_failed_delivery_whose_folder_is_gone_goes_after_two_rounds(ran) -> None:
    assert [state for _id, state in ran["gone"]] == ["gone"]
    gone = [
        after
        for action, folder, after in ran["events"]
        if folder == ran["names"]["GONE"] and action == "import_delivery_gone"
    ]
    assert len(gone) == 1 and gone[0]["failed_job_id"] == ran["failed_jobs"]["GONE"]


def test_a_lock_file_that_comes_and_goes_reopens_the_delivery_it_set_aside(ran) -> None:
    assert [state for _id, state in ran["lock_during"]] == ["superseded", "ready"]
    assert [state for _id, state in ran["lock_after"]] == ["ready", "superseded"]
    assert ran["lock_round"].reopened == {"ready": 1}
    lock = ran["names"]["LOCK"]
    actions = [action for action, folder, _after in ran["events"] if folder == lock]
    assert actions.count("import_delivery_reopened") == 1
    assert actions.count("import_delivery_superseded") == 2


def test_an_approval_names_the_warnings_it_acknowledges(ran) -> None:
    from backend.app.services.import_delivery_review import WARNINGS_CHANGED, WARNINGS_NOT_NAMED

    assert (ran["warnings_unnamed"]["status"], ran["warnings_unnamed"]["json"]["detail"]) == (400, WARNINGS_NOT_NAMED)
    assert (ran["warnings_stale"]["status"], ran["warnings_stale"]["json"]["detail"]) == (409, WARNINGS_CHANGED)
    assert ran["warnings_named"]["status"] == 200, ran["warnings_named"]


def test_an_open_delivery_is_checked_again_when_what_it_read_of_its_family_changes(ran) -> None:
    copy = ran["copy_before"]
    assert copy["state"] == "needs_attention"
    assert len(ran["copy_family_state"]) == 64
    copy_events = [action for action, folder, _after in ran["events"] if folder.startswith("E2E_LISTED_COPY_")]
    # Seen again in a round with nothing of the family changed: not checked again; checked again
    # once a member's status changed.
    assert copy_events.count("import_delivery_checked_again") == 1
    assert sum(ran["copy_round"].checked_again.values()) >= 1


def test_each_step_is_on_the_chain_and_the_chain_verifies(ran) -> None:
    names = ran["names"]
    by_folder: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for action, folder, after in ran["events"]:
        by_folder.setdefault(folder, []).append((action, after))
    failed = [action for action, _after in by_folder[names["FAILED"]]]
    assert failed[-3:] == ["import_delivery_failed", "import_delivery_offered_again", "import_delivery_queued"]
    offered_after = next(after for action, after in by_folder[names["FAILED"]] if action == "import_delivery_offered_again")
    assert (offered_after["previous_state"], offered_after["state"]) == ("failed", "ready")
    assert offered_after["failed_job_id"] == ran["failed_jobs"]["FAILED"]
    moved = [(action, after) for action, after in by_folder[names["MOVED"]]]
    superseded = [after for action, after in moved if action == "import_delivery_superseded"]
    assert superseded and superseded[0]["failed_job_id"] == ran["failed_jobs"]["MOVED"]
    assert ran["verified"]["json"]["verified"] is True
