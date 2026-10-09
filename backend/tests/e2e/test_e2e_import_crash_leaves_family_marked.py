"""A package import whose process stops part-way leaves its family marked (E2E).

An import writes a family over minutes, committing as it goes, and its fail-clean logic
(remove a new family nothing imported into, put back a failed overwrite, flag the rest
``import_incomplete``) runs in the import's own process. When that process ends part-way
-- a restart, a crash, running out of memory -- none of it runs. Observed on 2026-10-02:
a new family's import (cancel mode) was killed after two datasets and part of a third; the
worker that later claimed the stale job ran it again from the start, which refused the
family the first attempt had created ("Package validation failed"), replaced the job's
log, and left the family partly imported with no flag, so the sign-out gates let it be
signed as complete (TF-06 H16).

Over two new families this runs real import jobs (``POST /api/family-imports``, the
worker's claim and ``run_family_import_job``) and stops each import as a dying process
stops: its task is cancelled, so no ``except Exception`` and no fail-clean code runs, while
what it had committed stays. One import is stopped inside its SNV dataset, after the
loader had flushed the first batch of variants (the observed case: a half-written
callset); the other between its datasets, after SNV and before coverage. Then:

* while its job still reads ``running`` the sign-out is refused (409 ``import_in_progress``);
* once its heartbeat is stale the worker's claim ends the job ``failed`` as interrupted
  instead of running it again: the job keeps the first attempt's log and dataset summaries,
  with a line added; the family keeps its ``import_unfinished`` entry, naming what the
  import had not finished, and nothing is flagged by a re-run;
* the sign-out is refused (409 ``import_incomplete``, naming the job and the datasets)
  unless acknowledged with a reason, which freezes the entry into the signed record;
* a re-import by update completes but the family stays marked: the update skipped the
  half-written SNV dataset as "already there" (2 of 6 variants), and its log says so;
* a re-import by overwrite replaces it (6 of 6) and clears the mark; for the family stopped
  between datasets, an overwrite of the dataset it had not finished (coverage) is enough.

Last, an overwrite of the first family, complete by then, is stopped inside its SNV dataset.
It had copied the family's rows into five backup tables named after its job; they outlive
its process until the worker's claim ends the job and drops them. The family is not put
back from them: it stays marked until the next overwrite, which leaves no backup.

Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py). The families are new and uniquely named; the job rows end
completed or failed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e``
job sets it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_VARIANTS = 6
_BATCH = 2  # the SNV loader flushes every _BATCH variants
_ACK = "e2e validation"
# The drift and Sample-QC gates come first; they are acknowledged in every attempt here.
_OTHER_GATES = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": _ACK,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": _ACK,
}
_ALL_GATES = {
    **_OTHER_GATES,
    "acknowledge_import_incomplete": True,
    "import_incomplete_acknowledgement_reason": _ACK,
}


def _cap(resp) -> dict:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


def _write_package(root: Path, family_id: str, *, datasets: tuple[str, ...] = ("snv", "coverage")) -> Path:
    """A one-sample package: a family VCF of _VARIANTS SNVs and a coverage BED."""
    sample = f"{family_id}_S1"  # sample ids are unique across families
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    manifest = [f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\ndatasets:\n"]
    if "snv" in datasets:
        manifest.append("  snv:\n    family_vcf: snv/family.vcf\n")
        (root / "snv").mkdir(exist_ok=True)
        (root / "snv" / "family.vcf").write_text(
            "##fileformat=VCFv4.2\n"
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
            + "".join(f"1\t{100 * (i + 1)}\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n" for i in range(_VARIANTS)),
            encoding="utf-8",
        )
    if "coverage" in datasets:
        manifest.append(f"  coverage:\n    per_sample:\n      {sample}:\n        bed: coverage/S1.bed\n")
        (root / "coverage").mkdir(exist_ok=True)
        (root / "coverage" / "S1.bed").write_text(
            "1\t0\t1000\t31.0\n1\t1000\t2000\t29.5\n1\t2000\t3000\t30.2\n", encoding="utf-8"
        )
    (root / "manifest.yaml").write_text("".join(manifest), encoding="utf-8")
    return root


async def _collect(base: Path, mp: pytest.MonkeyPatch) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import variant_upload_service
    from backend.app.services.clickhouse_family_snapshot import list_import_backup_tables
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_source_rows
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # Where each import is stopped: inside its SNV dataset, once the loader has flushed
    # its first batch; or at the start of its coverage dataset, after SNV finished.
    stop_at: dict[str, Any] = {"where": None, "reached": asyncio.Event()}
    mp.setattr(variant_upload_service, "SMALL_VARIANT_UPLOAD_BATCH_SIZE", _BATCH)
    real_insert = variant_upload_service.insert_small_variant_records
    flushes: list[int] = []

    async def insert(*args, **kwargs):
        if stop_at["where"] == "snv" and flushes:
            stop_at["reached"].set()
            await asyncio.Event().wait()  # never returns: the process dies in here
        result = await real_insert(*args, **kwargs)
        flushes.append(len(args[3]) if len(args) > 3 else 0)
        return result

    mp.setattr(variant_upload_service, "insert_small_variant_records", insert)
    real_import_dataset = package_import._import_dataset

    async def import_dataset(*args, **kwargs):
        if stop_at["where"] == "coverage" and kwargs["summary"].dataset_type == "coverage":
            stop_at["reached"].set()
            await asyncio.Event().wait()
        return await real_import_dataset(*args, **kwargs)

    mp.setattr(package_import, "_import_dataset", import_dataset)

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT status, family_id, worker_id, error, logs, dataset_summaries, "
                        "completed_at IS NOT NULL AS ended FROM family_import_jobs "
                        "WHERE id = CAST(:j AS uuid)"
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
            "unfinished": metadata.get("import_unfinished"),
            "incomplete": metadata.get("import_incomplete"),
            "snvs": await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id]
            ),
            "coverage_rows": coverage,
        }

    async def backups(job_id: str) -> list[str]:
        """The ClickHouse backup tables this job's import made of its family."""
        return [table.name for table in await list_import_backup_tables(owner=job_id)]

    async def claim(job_id: str, worker: str) -> None:
        """Claim this queued job as the worker does, by its id."""
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                    "started_at = now(), heartbeat_at = now(), completed_at = NULL, error = NULL "
                    "WHERE id = CAST(:j AS uuid) AND status = 'queued'"
                ),
                {"j": job_id, "w": worker},
            )
            await s.commit()

    out: dict[str, Any] = {"project_id": project_id}
    jobs: list[str] = []
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def queue(root: Path, conflict_mode: str, family_id: str | None = None) -> str:
                body = {
                    "folder_path": str(root),
                    "project_id": project_id,
                    "dry_run": False,
                    "conflict_mode": conflict_mode,
                }
                if family_id:
                    body["family_id"] = family_id
                resp = await ac.post("/api/family-imports", json=body)
                assert resp.status_code == 200, resp.text
                jobs.append(str(resp.json()["_id"]))
                return jobs[-1]

            async def sign_out(family_id: str, body: dict) -> dict:
                return _cap(await ac.post(f"/api/families/{family_id}/report/sign-out", json=body))

            async def run_job(job_id: str) -> None:
                worker = f"e2e-crash-{uuid4().hex[:8]}"
                await claim(job_id, worker)
                await package_import.run_family_import_job(job_id=job_id, worker_id=worker)

            async def stopped_import(
                family_id: str, root: Path, where: str, conflict_mode: str = "cancel"
            ) -> dict[str, Any]:
                """Queue and run an import, and stop it at ``where``: of a new family
                (cancel), or of an existing one (overwrite, which snapshots it first)."""
                job_id = await queue(
                    root, conflict_mode, family_id if conflict_mode != "cancel" else None
                )
                worker = f"e2e-crash-{uuid4().hex[:8]}"
                await claim(job_id, worker)
                stop_at.update(where=where, reached=asyncio.Event())
                flushes.clear()
                run = asyncio.create_task(
                    package_import.run_family_import_job(job_id=job_id, worker_id=worker)
                )
                await asyncio.wait_for(stop_at["reached"].wait(), timeout=60)
                run.cancel()  # the process dies: nothing after this point of it runs
                try:
                    await run
                except asyncio.CancelledError:
                    pass
                stop_at["where"] = None
                record: dict[str, Any] = {"job_id": job_id, "flushed": list(flushes)}
                record["job_after_stop"] = await job(job_id)
                record["family_after_stop"] = await family(family_id)
                record["backups_after_stop"] = await backups(job_id)
                record["signout_while_running"] = await sign_out(family_id, _ALL_GATES)

                # Ten minutes later the heartbeat is stale, and a worker claims the job,
                # as the worker loop does (family_package_import_worker).
                async with sm() as s:
                    await s.execute(
                        text(
                            "UPDATE family_import_jobs "
                            "SET heartbeat_at = now() - interval '11 minutes' "
                            "WHERE id = CAST(:j AS uuid)"
                        ),
                        {"j": job_id},
                    )
                    await s.commit()
                reclaimer = f"e2e-reclaim-{uuid4().hex[:8]}"
                for _ in range(50):
                    async with sm() as s:
                        claimed = await package_import.claim_next_family_import_job(
                            s, worker_id=reclaimer
                        )
                    if claimed is None:
                        break
                    try:
                        # What the worker loop does with what it claims.
                        await package_import.handle_claimed_family_import_job(
                            claimed, worker_id=reclaimer
                        )
                    except Exception:  # noqa: BLE001 - the worker logs it and goes on
                        pass
                    if claimed["id"] == job_id:
                        record["claimed"] = {
                            key: claimed[key] for key in ("status", "claimed_from") if key in claimed
                        }
                        break
                record["job_after_claim"] = await job(job_id)
                record["family_after_claim"] = await family(family_id)
                record["backups_after_claim"] = await backups(job_id)
                return record

            # 1. Stopped inside its SNV dataset, two variants flushed.
            fam_snv = f"FAM_CRASH_{uuid4().hex[:8].upper()}"
            root_snv = _write_package(base / fam_snv, fam_snv)
            run = await stopped_import(fam_snv, root_snv, "snv")
            run["signout_refused"] = await sign_out(fam_snv, _OTHER_GATES)
            run["signout_acknowledged"] = await sign_out(fam_snv, _ALL_GATES)

            update_job = await queue(root_snv, "update", fam_snv)
            await run_job(update_job)
            run["update_job"] = await job(update_job)
            run["family_after_update"] = await family(fam_snv)

            overwrite_job = await queue(root_snv, "overwrite", fam_snv)
            await run_job(overwrite_job)
            run["overwrite_job"] = await job(overwrite_job)
            run["family_after_overwrite"] = await family(fam_snv)
            run["signout_after_overwrite"] = await sign_out(fam_snv, _OTHER_GATES)
            out["mid_dataset"] = run

            # 2. Stopped between its datasets: SNV finished, coverage not begun.
            fam_cov = f"FAM_CRASH_{uuid4().hex[:8].upper()}"
            root_cov = _write_package(base / fam_cov, fam_cov)
            run = await stopped_import(fam_cov, root_cov, "coverage")
            run["signout_refused"] = await sign_out(fam_cov, _OTHER_GATES)
            coverage_only = _write_package(base / f"{fam_cov}_coverage", fam_cov, datasets=("coverage",))
            overwrite_job = await queue(coverage_only, "overwrite", fam_cov)
            await run_job(overwrite_job)
            run["overwrite_job"] = await job(overwrite_job)
            run["family_after_overwrite"] = await family(fam_cov)
            out["between_datasets"] = run

            # 3. An overwrite of the complete first family, stopped inside its SNV dataset:
            # its snapshot of the family's rows was taken, and the process that would have
            # dropped (or restored) it is gone.
            run = await stopped_import(fam_snv, root_snv, "snv", conflict_mode="overwrite")
            overwrite_job = await queue(root_snv, "overwrite", fam_snv)
            await run_job(overwrite_job)
            run["overwrite_job"] = await job(overwrite_job)
            run["family_after_overwrite"] = await family(fam_snv)
            run["backups_after_overwrite"] = await backups(overwrite_job)
            out["overwrite_stopped"] = run
    finally:
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

    base = tmp_path_factory.mktemp("import_crash")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base, mp))


def _detail(resp: dict, gate: str) -> dict:
    assert resp["status"] == 409, resp["text"]
    detail = resp["json"]["detail"]
    assert detail["gate"] == gate, detail
    return detail


# --- stopped inside a dataset ----------------------------------------------------------


def test_the_stopped_import_left_a_half_written_callset(snap) -> None:
    run = snap["mid_dataset"]
    assert run["flushed"] == [_BATCH], "stopped after the loader's first flush"
    assert run["family_after_stop"]["snvs"] == _BATCH
    # Its job still reads running, so a sign-out is refused meanwhile.
    assert run["job_after_stop"]["status"] == "running"
    detail = _detail(run["signout_while_running"], "import_in_progress")
    assert detail["import_job"]["id"] == run["job_id"]


def test_the_family_is_marked_from_before_the_first_write(snap) -> None:
    run = snap["mid_dataset"]
    entry = run["family_after_stop"]["unfinished"][run["job_id"]]
    assert entry["job_id"] == run["job_id"]
    assert entry["datasets"] == ["coverage", "snv"]
    assert entry["finished_datasets"] == []
    assert run["family_after_stop"]["incomplete"] is None


def test_the_worker_ends_the_stopped_job_instead_of_running_it_again(snap) -> None:
    run = snap["mid_dataset"]
    after = run["job_after_claim"]
    # Before: the claim ran it again; the cancel refused the family the first attempt had
    # created and the job ended "Package validation failed", its log replaced.
    assert (after["status"], after["worker_id"]) == ("failed", None), after
    assert after["error"].startswith("Interrupted: the import's heartbeat stopped")
    assert run["claimed"] == {"status": "failed", "claimed_from": "running"}
    assert after["ended"]
    stopped = run["job_after_stop"]
    assert after["logs"][: len(stopped["logs"])] == stopped["logs"]
    assert after["logs"][-1].startswith("The import stopped part-way")
    assert after["dataset_summaries"] == stopped["dataset_summaries"]
    assert any(d["dataset_type"] == "snv" and d["status"] == "running" for d in after["dataset_summaries"])
    # The family keeps the entry; no re-run flagged or unflagged anything.
    assert run["family_after_claim"]["unfinished"] == run["family_after_stop"]["unfinished"]
    assert run["family_after_claim"]["incomplete"] is None


def test_the_sign_out_names_what_the_stopped_import_had_not_finished(snap) -> None:
    run = snap["mid_dataset"]
    # Before: no flag and no running job, so this signed out (200).
    detail = _detail(run["signout_refused"], "import_incomplete")
    assert detail["import_incomplete"] is None
    assert set(detail["import_unfinished"]) == {run["job_id"]}
    assert "coverage, snv may be partly written or missing" in detail["message"]
    assert f"Import job {run['job_id']}" in detail["message"]

    signed = run["signout_acknowledged"]
    assert signed["status"] == 200, signed["text"]
    snapshot = signed["json"]["snapshot"]
    assert snapshot["import_unfinished"][run["job_id"]]["finished_datasets"] == []
    assert snapshot["acknowledged_import_incomplete"] is True
    assert snapshot["import_incomplete_acknowledgement_reason"] == _ACK


def test_an_update_completes_but_does_not_clear_the_half_written_callset(snap) -> None:
    run = snap["mid_dataset"]
    update = run["update_job"]
    assert update["status"] == "completed", update["error"]
    # It skipped the SNV dataset ("small variants already exist") and kept 2 of 6.
    assert run["family_after_update"]["snvs"] == _BATCH
    assert set(run["family_after_update"]["unfinished"]) == {run["job_id"]}
    note = next(line for line in update["logs"] if "stays marked import-incomplete" in line)
    assert f"import job {run['job_id']}" in note and "snv" in note and "overwrite" in note


def test_an_overwrite_replaces_it_and_clears_the_mark(snap) -> None:
    run = snap["mid_dataset"]
    assert run["overwrite_job"]["status"] == "completed", run["overwrite_job"]["error"]
    after = run["family_after_overwrite"]
    assert after["snvs"] == _VARIANTS
    assert after["unfinished"] is None and after["incomplete"] is None
    # Signed out with only the drift and QC acknowledgements: no import gate any more.
    resp = run["signout_after_overwrite"]
    assert resp["status"] == 200, resp["text"]
    assert resp["json"]["snapshot"]["import_unfinished"] == {}


# --- stopped between datasets ----------------------------------------------------------


def test_an_import_stopped_between_datasets_names_the_one_it_had_not_begun(snap) -> None:
    run = snap["between_datasets"]
    entry = run["family_after_stop"]["unfinished"][run["job_id"]]
    assert entry["finished_datasets"] == ["snv"]
    assert run["family_after_stop"]["snvs"] == _VARIANTS
    assert run["family_after_stop"]["coverage_rows"] == 0
    assert run["job_after_claim"]["status"] == "failed"
    detail = _detail(run["signout_refused"], "import_incomplete")
    assert "coverage may be partly written or missing (snv had finished)" in detail["message"]


def test_an_overwrite_of_the_dataset_it_had_not_finished_completes_the_family(snap) -> None:
    run = snap["between_datasets"]
    assert run["overwrite_job"]["status"] == "completed", run["overwrite_job"]["error"]
    after = run["family_after_overwrite"]
    assert after["unfinished"] is None and after["incomplete"] is None
    assert after["coverage_rows"] > 0 and after["snvs"] == _VARIANTS


# --- an overwrite stopped part-way: its backup of the family -------------------------------


def test_an_overwrite_stopped_part_way_leaves_its_family_marked(snap) -> None:
    run = snap["overwrite_stopped"]
    # Its SNV dataset was cleared and refilled to the first flush: 2 of the 6 variants.
    assert run["family_after_stop"]["snvs"] == _BATCH
    entry = run["family_after_stop"]["unfinished"][run["job_id"]]
    assert entry["finished_datasets"] == []
    assert run["job_after_claim"]["status"] == "failed"
    assert run["job_after_claim"]["error"].startswith("Interrupted:")


def test_the_backup_a_stopped_overwrite_made_is_dropped_when_its_job_is_ended(snap) -> None:
    run = snap["overwrite_stopped"]
    # The import's backup of the family's rows, named after its job, outlived it: its
    # process was the one to drop it.
    assert len(run["backups_after_stop"]) == 5, run["backups_after_stop"]
    assert all(f"/SNAPSHOT/{run['job_id']}/" in name for name in run["backups_after_stop"])
    # Before: kept for good, a copy of the family's rows no process would ever drop.
    assert run["backups_after_claim"] == []
    # The family is not put back from it: it stays marked, partly overwritten.
    assert run["family_after_claim"]["snvs"] == _BATCH
    assert set(run["family_after_claim"]["unfinished"]) == {run["job_id"]}


def test_an_overwrite_after_it_completes_the_family_and_leaves_no_backup(snap) -> None:
    run = snap["overwrite_stopped"]
    assert run["overwrite_job"]["status"] == "completed", run["overwrite_job"]["error"]
    after = run["family_after_overwrite"]
    assert after["snvs"] == _VARIANTS
    assert after["unfinished"] is None and after["incomplete"] is None
    assert run["backups_after_overwrite"] == []
