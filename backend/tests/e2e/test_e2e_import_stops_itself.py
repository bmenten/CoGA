"""An import stops itself before another write can start (E2E, SAFE-15, TF-06 H16).

A running import writes its job's heartbeat every minute, and a worker that finds it ten
minutes old ends the job as interrupted; and the import holds its family's variant-write
locks on a Postgres connection of its own, which Postgres releases with the connection. An
import whose heartbeat could not reach the database went on for as long as that lasted, and
an import whose lock connection was lost went on writing, unaware that the next writer could
start. Now the import's heartbeat stops it where it is in both cases: once its beats have not
reached the database for half the stale window (``FAMILY_IMPORT_SELF_STOP``), and as soon as
its check of the lock connection fails (every ``FAMILY_IMPORT_LOCK_CHECK_SECONDS``).

Over new one-sample families this runs real import jobs (``POST /api/family-imports``, then
the worker's ``run_family_import_job``) against Postgres and ClickHouse, each held inside its
SNV dataset after the loader's first flush (2 of the variants written), the timings
shortened:

* while the heartbeat cannot be written (each beat fails, as one without a database
  connection does), the import stops itself where it is: its SNV dataset stays part-written
  and its coverage never begins, the family keeps its ``import_unfinished`` entry, the
  family's variant-write locks are free, the job keeps the running record it had until a
  worker finds its heartbeat stale and ends it as interrupted, and the log says why;
* an overwrite of a complete family whose lock connection is ended
  (``pg_terminate_backend``) while it is held stops where it is: nothing is restored (the
  family keeps the overwrite's first 2 variants), its job ends failed and says why, the
  backups it made of the family are dropped, the family stays marked, its sign-out is
  refused, and the log says why.

Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py). The families are new and uniquely named; the job rows end
failed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_VARIANTS = 6
_OVERWRITE_VARIANTS = 4
_BATCH = 2  # the SNV loader flushes every _BATCH variants
_TICK = 0.05
_ACK = "e2e validation"
# The drift and Sample-QC gates come first; they are acknowledged in every attempt here.
_OTHER_GATES = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": _ACK,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": _ACK,
}


def _cap(resp) -> dict:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


def _write_package(
    root: Path, family_id: str, *, variants: int = _VARIANTS, first_position: int = 100
) -> Path:
    """A one-sample package: a family VCF of ``variants`` SNVs and a coverage BED."""
    sample = f"{family_id}_S1"  # sample ids are unique across families
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    (root / "snv").mkdir(exist_ok=True)
    (root / "snv" / "family.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        + "".join(
            f"1\t{first_position + 100 * i}\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n" for i in range(variants)
        ),
        encoding="utf-8",
    )
    (root / "coverage").mkdir(exist_ok=True)
    (root / "coverage" / "S1.bed").write_text(
        "1\t0\t1000\t31.0\n1\t1000\t2000\t29.5\n1\t2000\t3000\t30.2\n", encoding="utf-8"
    )
    (root / "manifest.yaml").write_text(
        f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\ndatasets:\n"
        "  snv:\n    family_vcf: snv/family.vcf\n"
        f"  coverage:\n    per_sample:\n      {sample}:\n        bed: coverage/S1.bed\n",
        encoding="utf-8",
    )
    return root


class _Warnings(logging.Handler):
    """The warnings the import's module logs."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def saying(self, words: str) -> list[str]:
        return [record.getMessage() for record in self.records if words in record.getMessage()]


async def _until(condition: Callable[[], bool], timeout: float = 30.0) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


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
    from backend.app.services.family_variant_write_lock import (
        SMALL_VARIANTS,
        family_variant_lock_key,
        try_share_family_variant_writes,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # Where an import is held: inside its SNV dataset once the loader has flushed its first
    # batch. It is never let go on: what stops it is its heartbeat.
    hold: dict[str, Any] = {"active": False, "inside": asyncio.Event()}
    mp.setattr(variant_upload_service, "SMALL_VARIANT_UPLOAD_BATCH_SIZE", _BATCH)
    real_insert = variant_upload_service.insert_small_variant_records
    flushes: list[int] = []

    async def insert(*args, **kwargs):
        if hold["active"] and flushes:
            hold["inside"].set()
            await asyncio.Event().wait()  # held until its heartbeat stops the import
        result = await real_insert(*args, **kwargs)
        flushes.append(1)
        return result

    mp.setattr(variant_upload_service, "insert_small_variant_records", insert)

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
    mp.setattr(package_import, "FAMILY_IMPORT_LOCK_CHECK_SECONDS", _TICK)
    warnings = _Warnings()
    package_import.logger.addHandler(warnings)

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT status, family_id, worker_id, error, completed_at "
                        "FROM family_import_jobs WHERE id = CAST(:j AS uuid)"
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
            "snvs": await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id]
            ),
            "coverage_rows": coverage,
        }

    async def backups(job_id: str) -> list[str]:
        return [table.name for table in await list_import_backup_tables(owner=job_id)]

    async def locks_free(family_uuid: str) -> bool:
        async with sm() as s:
            free = await try_share_family_variant_writes(s, family_uuid)
            await s.rollback()
        return free

    async def lock_holders(family_uuid: str) -> list[int]:
        """The backends that hold the family's small-variant write lock: the key is
        hashtext's int4, sign-extended, kept in an advisory lock's classid and objid."""
        async with sm() as s:
            rows = await s.execute(
                text(
                    "SELECT DISTINCT pid FROM pg_locks WHERE locktype = 'advisory' AND granted "
                    "AND objsubid = 1 "
                    "AND ((classid::bigint << 32) | objid::bigint) = hashtext(:k)::bigint"
                ),
                {"k": family_variant_lock_key(family_uuid, SMALL_VARIANTS)},
            )
            return [int(pid) for pid in rows.scalars().all()]

    async def end_backend(pid: int) -> bool:
        async with sm() as s:
            ended = (await s.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})).scalar_one()
            await s.commit()
        return bool(ended)

    async def claim(job_id: str, worker: str) -> None:
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

    async def ended_as_interrupted(job_id: str) -> dict[str, Any] | None:
        """Ten minutes on, the heartbeat is stale, and a worker claims the job as the worker
        loop does: the claim ends it as interrupted."""
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE family_import_jobs SET heartbeat_at = now() - interval '11 minutes' "
                    "WHERE id = CAST(:j AS uuid)"
                ),
                {"j": job_id},
            )
            await s.commit()
        reclaimer = f"e2e-reclaim-{uuid4().hex[:8]}"
        for _ in range(50):
            async with sm() as s:
                claimed = await package_import.claim_next_family_import_job(s, worker_id=reclaimer)
            if claimed is None:
                return None
            try:
                await package_import.handle_claimed_family_import_job(claimed, worker_id=reclaimer)
            except Exception:  # noqa: BLE001 - the worker logs it and goes on
                pass
            if claimed["id"] == job_id:
                return {key: claimed[key] for key in ("status", "claimed_from")}
        return None

    out: dict[str, Any] = {}
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
                # As the sign-out dialogs do: each acknowledgement with its refusal's fingerprint.
                return _cap(await _harness.sign_out(ac, f"/api/families/{family_id}/report/sign-out", body))

            async def held_run(job_id: str) -> asyncio.Task:
                """Claim the job and run it, held inside its SNV dataset."""
                worker = f"e2e-self-stop-{uuid4().hex[:8]}"
                await claim(job_id, worker)
                hold.update(active=True, inside=asyncio.Event())
                flushes.clear()
                run = asyncio.create_task(
                    package_import.run_family_import_job(job_id=job_id, worker_id=worker)
                )
                await asyncio.wait_for(hold["inside"].wait(), timeout=60)
                return run

            async def outcome(run: asyncio.Task) -> str:
                try:
                    await asyncio.wait_for(run, timeout=30)
                except Exception as exc:  # noqa: BLE001 - recorded for the assertions
                    return type(exc).__name__
                finally:
                    hold["active"] = False
                return "returned"

            # 1. A new family's import whose heartbeat cannot reach the database.
            mp.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", _TICK)
            mp.setattr(package_import, "FAMILY_IMPORT_SELF_STOP", timedelta(seconds=0.5))
            fam = f"FAM_SELFSTOP_{uuid4().hex[:8].upper()}"
            job_id = await queue(_write_package(base / fam, fam), "cancel")
            run = await held_run(job_id)
            record: dict[str, Any] = {"job_id": job_id}
            beats.update(written=False, failed=0)
            record["run"] = await outcome(run)
            beats["written"] = True
            record["beats_failed"] = beats["failed"]
            record["flushed"] = len(flushes)
            record["job_after"] = await job(job_id)
            record["family_after"] = await family(fam)
            record["locks_free"] = await locks_free(record["family_after"]["uuid"])
            record["stopped_log"] = warnings.saying("stopped itself where it was")
            record["claimed"] = await ended_as_interrupted(job_id)
            record["job_after_claim"] = await job(job_id)
            out["beat_lost"] = record

            # 2. A complete family, and an overwrite of it whose lock connection is ended.
            mp.setattr(package_import, "FAMILY_IMPORT_HEARTBEAT_SECONDS", 3600.0)
            fam_ow = f"FAM_SELFSTOP_{uuid4().hex[:8].upper()}"
            first = await queue(_write_package(base / fam_ow, fam_ow), "cancel")
            worker = f"e2e-self-stop-{uuid4().hex[:8]}"
            await claim(first, worker)
            await package_import.run_family_import_job(job_id=first, worker_id=worker)
            record = {"first": await job(first), "family_before": await family(fam_ow)}
            overwrite = _write_package(
                base / f"{fam_ow}_v2", fam_ow, variants=_OVERWRITE_VARIANTS, first_position=10_000
            )
            job_id = await queue(overwrite, "overwrite", fam_ow)
            record["job_id"] = job_id
            run = await held_run(job_id)
            uuid = record["family_before"]["uuid"]
            record["backups_inside"] = await backups(job_id)
            record["holders"] = await lock_holders(uuid)
            record["ended"] = [await end_backend(pid) for pid in record["holders"]]
            record["run"] = await outcome(run)
            record["job_after"] = await job(job_id)
            record["family_after"] = await family(fam_ow)
            record["backups_after"] = await backups(job_id)
            record["locks_free"] = await locks_free(uuid)
            record["signout"] = await sign_out(fam_ow, _OTHER_GATES)
            record["lost_log"] = warnings.saying("lost the connection that held its family")
            record["stopped_log"] = warnings.saying("Nothing was restored")
            out["locks_lost"] = record
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

    base = tmp_path_factory.mktemp("import_stops_itself")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base, mp))


# --- an import whose heartbeat cannot reach the database -------------------------------------


def test_an_import_whose_heartbeat_cannot_be_written_stops_itself(snap) -> None:
    run = snap["beat_lost"]
    # Before: it went on for as long as its heartbeat could not be written (here: for ever).
    assert run["run"] == "returned"
    assert run["beats_failed"] >= 2
    assert len(run["stopped_log"]) == 1, run["stopped_log"]
    assert run["job_id"] in run["stopped_log"][0]


def test_it_stops_where_it_is_and_leaves_its_family_marked(snap) -> None:
    run = snap["beat_lost"]
    family = run["family_after"]
    # Its first flush only: the SNV dataset stays part-written, coverage never begins.
    assert (run["flushed"], family["snvs"], family["coverage_rows"]) == (1, 2, 0)
    assert family["unfinished"], "the import's import_unfinished entry must stay"
    assert run["locks_free"] is True


def test_its_job_is_ended_as_interrupted_once_its_heartbeat_is_stale(snap) -> None:
    run = snap["beat_lost"]
    # The job could not be ended by the import: it keeps the record it had.
    assert run["job_after"]["status"] in ("validating", "running")
    assert run["job_after"]["completed_at"] is None
    assert run["claimed"] == {"status": "failed", "claimed_from": "running"}
    assert run["job_after_claim"]["status"] == "failed"


# --- an import whose lock connection is lost --------------------------------------------------


def test_the_family_was_complete_before_its_overwrite(snap) -> None:
    run = snap["locks_lost"]
    assert run["first"]["status"] == "completed"
    assert run["family_before"]["snvs"] == _VARIANTS
    assert run["backups_inside"], "the overwrite snapshots the family before it writes"
    assert len(run["holders"]) == 1 and run["ended"] == [True]


def test_an_import_whose_lock_connection_is_lost_stops_where_it_is(snap) -> None:
    run = snap["locks_lost"]
    family = run["family_after"]
    # Before: it went on writing, though the next writer could start.
    assert run["run"] == "returned"
    # Nothing restored: the overwrite's first flush stays, the family keeps its mark.
    assert family["snvs"] == _BATCH
    assert family["unfinished"], "the overwrite's import_unfinished entry must stay"
    assert run["locks_free"] is True


def test_its_job_ends_failed_and_says_why(snap) -> None:
    from backend.app.services.family_package_import import LOCKS_LOST_ERROR

    run = snap["locks_lost"]
    assert run["job_after"]["status"] == "failed"
    assert run["job_after"]["completed_at"] is not None
    assert run["job_after"]["error"] == LOCKS_LOST_ERROR
    assert run["backups_after"] == []
    assert len(run["lost_log"]) == 1 and len(run["stopped_log"]) == 1


def test_its_family_cannot_be_signed_out(snap) -> None:
    run = snap["locks_lost"]
    assert run["signout"]["status"] == 409, run["signout"]["text"]
    assert run["signout"]["json"]["detail"]["gate"] == "import_incomplete"
