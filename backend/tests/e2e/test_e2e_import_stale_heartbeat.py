"""A live import whose heartbeat goes stale is stopped, and a restore without its backup
deletes nothing (E2E, TF-06 H16).

A running import writes its job's heartbeat every minute, and a worker that finds it ten
minutes old ends the job as interrupted and drops the job's overwrite backups. Here the
import's process is alive and only its heartbeat cannot be written.

Over two new one-sample families this runs real import jobs (``POST /api/family-imports``,
then the worker's ``run_family_import_job``) against Postgres and ClickHouse. While each
import is held inside a dataset, its heartbeat is made ten minutes old and a worker's claim
ends the job as interrupted (``claim_next_family_import_job``, then
``handle_claimed_family_import_job``, which drops an overwrite's backups). Then:

* the import of a new family, held inside its SNV dataset after the loader's first flush,
  runs on while its heartbeat cannot be written (each beat fails, as one without a database
  connection does); once it can be, and the import is let go on, the heartbeat stops it
  where it is: its SNV dataset stays part-written (2 of 6 variants) and its coverage never
  begins, the family keeps its ``import_unfinished`` entry and its sign-out is refused, the
  job keeps the record the claim gave it, the family's variant-write locks are free, and the
  log says why;
* an overwrite of a complete family (6 variants), which replaced its small variants (4) and
  is held at its coverage dataset, its heartbeat written no more, is let go on: coverage
  fails, and its restore finds the backup gone, so it deletes nothing: the family keeps the
  overwrite's 4 variants, and its flag names coverage as failed and snv, whose rows are
  there, as imported. The end the job could not record is said in the log.

Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py). The families are new and uniquely named; the job rows end
failed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import DRIFT_AND_QC_ACKNOWLEDGED, capture, ensure_postgres_schema, gate_refusal, wait_until
from backend.tests.e2e._import_jobs import (
    WarningRecords,
    claim_import_job,
    end_stale_job_as_a_worker_does,
    import_backups,
    one_sample_package,
    queue_import,
    variant_write_locks_free,
)

pytestmark = pytest.mark.integration

_VARIANTS = 6
_OVERWRITE_VARIANTS = 4
_BATCH = 2  # the SNV loader flushes every _BATCH variants
# How long each later flush of a held SNV dataset waits once let go on: the import reads on,
# slowly, while its heartbeat (every _BEAT seconds) finds its job ended.
_READING_PACE = 2.0
_BEAT = 0.05


async def _collect(base: Path, mp: pytest.MonkeyPatch) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import variant_upload_service
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_source_rows
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # Where an import is held: inside its SNV dataset once the loader has flushed its first
    # batch (each later flush then waits _READING_PACE first), or at the start of its
    # coverage dataset (which then fails).
    hold: dict[str, Any] = {"where": None, "inside": asyncio.Event(), "go_on": asyncio.Event()}
    mp.setattr(variant_upload_service, "SMALL_VARIANT_UPLOAD_BATCH_SIZE", _BATCH)
    real_insert = variant_upload_service.insert_small_variant_records
    flushes: list[int] = []

    async def insert(*args, **kwargs):
        if hold["where"] == "snv" and flushes:
            if not hold["inside"].is_set():
                hold["inside"].set()
                await hold["go_on"].wait()
            await asyncio.sleep(_READING_PACE)
        result = await real_insert(*args, **kwargs)
        flushes.append(len(args[3]) if len(args) > 3 else 0)
        return result

    mp.setattr(variant_upload_service, "insert_small_variant_records", insert)
    real_import_dataset = package_import._import_dataset

    async def import_dataset(*args, **kwargs):
        if hold["where"] == "coverage" and kwargs["summary"].dataset_type == "coverage":
            hold["inside"].set()
            await hold["go_on"].wait()
            raise RuntimeError("e2e: the coverage file could not be read")
        return await real_import_dataset(*args, **kwargs)

    mp.setattr(package_import, "_import_dataset", import_dataset)

    # The heartbeat: while `beats["written"]` is False, each beat fails, as one without a
    # database connection does.
    real_beat = package_import._beat_family_import_job
    beats: dict[str, Any] = {"written": True, "failed": 0}

    async def beat(*args, **kwargs):
        if not beats["written"]:
            beats["failed"] += 1
            raise ConnectionRefusedError("e2e: no database connection for the heartbeat")
        return await real_beat(*args, **kwargs)

    mp.setattr(package_import, "_beat_family_import_job", beat)
    warnings = WarningRecords()
    package_import.logger.addHandler(warnings)

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT status, family_id, worker_id, error, logs, dataset_summaries, "
                        "completed_at FROM family_import_jobs WHERE id = CAST(:j AS uuid)"
                    ),
                    {"j": job_id},
                )
            ).mappings().one()
        return dict(row)

    async def family(family_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text("SELECT id::text AS uuid, metadata FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).mappings().one()
            coverage = await count_interval_track_source_rows(
                s, family_uuid=row["uuid"], track_type="coverage"
            )
        metadata = row["metadata"] or {}
        return {
            "uuid": row["uuid"],
            "unfinished": metadata.get("import_unfinished"),
            "incomplete": metadata.get("import_incomplete"),
            "snvs": await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id]
            ),
            "coverage_rows": coverage,
        }

    out: dict[str, Any] = {}
    jobs: list[str] = []
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def queue(root: Path, conflict_mode: str, family_id: str | None = None) -> str:
                jobs.append(
                    await queue_import(root, project_id, conflict_mode=conflict_mode, family_id=family_id)
                )
                return jobs[-1]

            async def sign_out(family_id: str, body: dict) -> dict:
                # As the sign-out dialogs do: each acknowledgement with its refusal's fingerprint.
                return capture(await _harness.sign_out(ac, f"/api/families/{family_id}/report/sign-out", body))

            async def held_run(job_id: str, where: str) -> asyncio.Task:
                """Claim the job and run it, held at ``where``."""
                worker = f"e2e-stale-{uuid4().hex[:8]}"
                await claim_import_job(job_id, worker)
                hold.update(where=where, inside=asyncio.Event(), go_on=asyncio.Event())
                flushes.clear()
                run = asyncio.create_task(
                    package_import.run_family_import_job(job_id=job_id, worker_id=worker)
                )
                await asyncio.wait_for(hold["inside"].wait(), timeout=60)
                return run

            async def let_go_on(run: asyncio.Task) -> str:
                hold["go_on"].set()
                try:
                    await asyncio.wait_for(run, timeout=60)
                except Exception as exc:  # noqa: BLE001 - recorded for the assertions
                    return type(exc).__name__
                finally:
                    hold["where"] = None
                return "returned"

            # 1. A new family's import, held inside its SNV dataset after the first flush.
            mp.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", _BEAT)
            fam_live = f"FAM_STALE_{uuid4().hex[:8].upper()}"
            job_id = await queue(one_sample_package(base / fam_live, fam_live, variants=_VARIANTS), "cancel")
            run = await held_run(job_id, "snv")
            record: dict[str, Any] = {"job_id": job_id}
            # Its heartbeat cannot be written: each beat fails and is tried again.
            beats.update(written=False, failed=0)
            await wait_until(lambda: beats["failed"] >= 3)
            record["running_while_unwritten"] = not run.done()
            record["claimed"] = await end_stale_job_as_a_worker_does(job_id)
            record["job_after_claim"] = await job(job_id)
            # The heartbeat can be written again, and the import goes on.
            beats["written"] = True
            record["run"] = await let_go_on(run)
            record["flushed"] = list(flushes)
            record["job_after"] = await job(job_id)
            record["family_after"] = await family(fam_live)
            record["locks_free"] = await variant_write_locks_free(record["family_after"]["uuid"])
            record["signout"] = await sign_out(fam_live, DRIFT_AND_QC_ACKNOWLEDGED)
            record["stopped_log"] = warnings.saying("was stopped where it was")
            out["live"] = record

            # 2. A complete family, and an overwrite of it held at its coverage dataset,
            # which then fails. Its heartbeat is written no more: the import reaches its
            # restore before its heartbeat could stop it.
            fam_ow = f"FAM_STALE_{uuid4().hex[:8].upper()}"
            first = await queue(one_sample_package(base / fam_ow, fam_ow, variants=_VARIANTS), "cancel")
            worker = f"e2e-stale-{uuid4().hex[:8]}"
            await claim_import_job(first, worker)
            await package_import.run_family_import_job(job_id=first, worker_id=worker)
            record = {"first": await job(first), "family_before": await family(fam_ow)}
            overwrite = one_sample_package(
                base / f"{fam_ow}_v2", fam_ow, variants=_OVERWRITE_VARIANTS, first_position=10_000
            )
            job_id = await queue(overwrite, "overwrite", fam_ow)
            record["job_id"] = job_id
            mp.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", 3600.0)
            run = await held_run(job_id, "coverage")
            record["backups_inside"] = await import_backups(job_id)
            record["family_inside"] = await family(fam_ow)
            record["claimed"] = await end_stale_job_as_a_worker_does(job_id)
            record["backups_after_claim"] = await import_backups(job_id)
            record["job_after_claim"] = await job(job_id)
            record["run"] = await let_go_on(run)
            record["job_after"] = await job(job_id)
            record["family_after"] = await family(fam_ow)
            record["backups_after"] = await import_backups(job_id)
            record["unrecorded_log"] = warnings.saying("could not record it")
            out["overwrite"] = record
    finally:
        package_import.logger.removeHandler(warnings)
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE family_import_jobs SET status = 'failed', worker_id = NULL, "
                    "completed_at = now(), error = 'e2e: left unfinished' "
                    "WHERE id::text = ANY(:jobs) AND status IN ('queued', 'validating', 'running')"
                ),
                {"jobs": jobs},
            )
            await s.commit()
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_stale_heartbeat")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base, mp))


# --- a live import whose job another worker ended ------------------------------------------


def test_the_import_runs_on_while_its_heartbeat_cannot_be_written(snap) -> None:
    run = snap["live"]
    assert run["running_while_unwritten"] is True
    assert run["claimed"] == {"status": "failed", "claimed_from": "running"}


def test_its_heartbeat_stops_it_where_it_is(snap) -> None:
    run = snap["live"]
    assert run["run"] == "returned"
    after = run["family_after"]
    # It stops after the loader's first flush: no coverage, and its entry stays.
    assert run["flushed"] == [_BATCH]
    assert after["snvs"] == _BATCH
    assert after["coverage_rows"] == 0
    entry = after["unfinished"][run["job_id"]]
    assert entry["datasets"] == ["coverage", "snv"]
    assert entry["finished_datasets"] == []
    assert after["incomplete"] is None
    # Its variant-write locks went with their transaction.
    assert run["locks_free"] is True
    assert len([line for line in run["stopped_log"] if run["job_id"] in line]) == 1


def test_the_job_keeps_the_record_the_claim_gave_it(snap) -> None:
    run = snap["live"]
    assert run["job_after"] == run["job_after_claim"]
    assert (run["job_after"]["status"], run["job_after"]["worker_id"]) == ("failed", None)
    assert run["job_after"]["error"].startswith("Interrupted: the import's heartbeat stopped")
    assert run["job_after"]["logs"][-1].startswith("The import stopped part-way")


def test_the_family_it_left_part_way_is_refused_for_sign_out(snap) -> None:
    run = snap["live"]
    detail = gate_refusal(run["signout"], "import_incomplete")
    assert set(detail["import_unfinished"]) == {run["job_id"]}


# --- an overwrite whose backup was dropped ---------------------------------------------------


def test_the_overwrite_replaced_the_family_and_its_backup_was_dropped(snap) -> None:
    run = snap["overwrite"]
    assert run["first"]["status"] == "completed", run["first"]["error"]
    assert run["family_before"]["snvs"] == _VARIANTS
    assert run["family_before"]["coverage_rows"] > 0
    assert run["family_inside"]["snvs"] == _OVERWRITE_VARIANTS
    assert len(run["backups_inside"]) == 5, run["backups_inside"]
    assert run["claimed"] == {"status": "failed", "claimed_from": "running"}
    assert run["backups_after_claim"] == []


def test_a_restore_without_its_backup_deletes_nothing(snap) -> None:
    run = snap["overwrite"]
    assert run["run"] == "returned"
    after = run["family_after"]
    # The restore finds no backup and deletes nothing: the overwrite's variants stay.
    assert after["snvs"] == _OVERWRITE_VARIANTS
    assert after["coverage_rows"] == run["family_before"]["coverage_rows"]
    assert run["backups_after"] == []


def test_its_flag_names_no_deleted_dataset_as_imported(snap) -> None:
    run = snap["overwrite"]
    after = run["family_after"]
    flag = after["incomplete"]
    assert flag["failed_datasets"] == ["coverage"]
    # snv's rows are there: the flag claims nothing the restore removed.
    assert flag["imported_datasets"] == ["snv"] and after["snvs"] > 0
    assert after["unfinished"] is None
    # The job keeps the record the claim gave it; the end it could not record is logged.
    assert run["job_after"] == run["job_after_claim"]
    (unrecorded,) = [line for line in run["unrecorded_log"] if run["job_id"] in line]
    assert "ended failed" in unrecorded
