"""Package imports claimed and run by import-worker processes of their own (E2E).

Every import ran in the API's process, all of them on the one core a Python process uses,
and on the event loop that answers the requests. ``python -m app.import_worker``
(``backend/app/import_worker.py``) runs the workers without the API, and
``FAMILY_IMPORT_WORKER_COUNT=0`` leaves the API without any, as docker-compose.yml runs them.
What the workers share goes through Postgres: the claim of a job, its heartbeat, stamped and
judged by the database's clock, the end of a job whose heartbeat went stale, and the family's
variant-write locks (advisory locks). This test runs them across processes.

The API is the app of this process, driven through an in-process ``httpx.ASGITransport``
client (see test_e2e_api_contract.py) without its lifespan, so it runs no worker, as with
``FAMILY_IMPORT_WORKER_COUNT=0``: each job below is queued through ``POST /api/family-imports``
and taken by a worker process started for it, which the job's ``worker_id`` names by its
process id. Some run the entrypoint exactly as the image does; the others run it through
``_import_worker_process.py``, which only makes the poll and the heartbeat faster and can hold
an import inside its SNV dataset after its first 2 of 6 variants. Over new one-sample
families, against Postgres and ClickHouse:

* one process imports a family end to end, and on SIGTERM exits 0;
* two processes import two families at once: each holds its own family's variant-write locks,
  which this process finds taken, until both are let go on and complete;
* a process stopped (SIGTERM) inside an import exits 0 promptly and leaves the job as a
  stopped process does: ``running``, its family's locks released, its SNV dataset part-written
  and the family marked; once the job's heartbeat is ten minutes old, another worker process
  ends it as interrupted;
* a live process whose job another worker ended, its heartbeat being stale, stops that import
  where it is, releases the family's locks and goes on running.

The families are new and uniquely named, with synthetic IDs; the job rows end completed or
failed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import ensure_postgres_schema

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
_VARIANTS = 6
# How long a worker process may take to start, to take a job, or to stop.
_WAIT = 90.0
_STOPPED_WITHIN = 30.0


def _write_package(root: Path, family_id: str) -> Path:
    """A one-sample package: a family VCF of six SNVs and a coverage BED."""
    sample = f"{family_id}_S1"  # sample ids are unique across families
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    (root / "snv").mkdir(exist_ok=True)
    (root / "snv" / "family.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        + "".join(f"1\t{100 + 100 * i}\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n" for i in range(_VARIANTS)),
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


class _WorkerProcess:
    """An import-worker process, with one worker, its log in a file: the image's entrypoint,
    or, ``held``, the faster one that holds each import inside its SNV dataset until let go."""

    def __init__(self, base: Path, name: str, *, held: bool) -> None:
        self.name = name
        self.log = base / f"{name}.log"
        self.pause = base / f"{name}-pause"
        env = {
            **os.environ,
            "FAMILY_IMPORT_ROOTS": str(base),
            "FAMILY_IMPORT_WORKER_COUNT": "1",
        }
        if held:
            self.pause.mkdir()
            env["E2E_IMPORT_PAUSE_DIR"] = str(self.pause)
        module = "backend.tests.e2e._import_worker_process" if held else "backend.app.import_worker"
        with self.log.open("wb") as log:
            self.process = subprocess.Popen(
                [sys.executable, "-m", module],
                cwd=REPO,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        self.pid = self.process.pid

    def output(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    def says(self, words: str) -> bool:
        return words in self.output()

    def claimed(self, job_id: str) -> bool:
        """Whether a worker of this process claimed the job, as its log says: the job's
        record names its worker only until the job ends."""
        return any(
            f"Package-import worker {self.pid}-" in line and f"claimed job {job_id}" in line
            for line in self.output().splitlines()
        )

    def running(self) -> bool:
        return self.process.poll() is None

    async def started(self) -> None:
        await _eventually(
            lambda: _now(self.says("Import worker process started") or self._ended()),
            what=f"{self.name} to start",
        )

    def _ended(self) -> bool:
        if self.running():
            return False
        raise AssertionError(f"{self.name} exited {self.process.returncode}:\n{self.output()}")

    async def inside(self) -> None:
        await _eventually(lambda: _now((self.pause / "inside").exists()), what=f"{self.name}'s import")

    def let_go_on(self) -> None:
        (self.pause / "go").touch()

    async def terminate(self) -> dict[str, Any]:
        """SIGTERM, as docker stop sends it: its exit code and how long it took."""
        began = time.monotonic()
        self.process.send_signal(signal.SIGTERM)
        try:
            code = await asyncio.to_thread(self.process.wait, _STOPPED_WITHIN)
        except subprocess.TimeoutExpired:
            self.process.kill()
            code = await asyncio.to_thread(self.process.wait)
            return {"code": code, "seconds": None}
        return {"code": code, "seconds": time.monotonic() - began}


async def _now(value: Any) -> Any:
    return value


async def _eventually(check: Callable[[], Awaitable[Any]], *, what: str, timeout: float = _WAIT) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = await check()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


async def _said(worker: _WorkerProcess, words: str) -> bool:
    """Whether the process logs ``words`` before the wait runs out."""
    try:
        return await _eventually(lambda: _now(worker.says(words)), what=f"{worker.name} to log it")
    except AssertionError:
        return False


async def _collect(base: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.app.services.family_package_import import handle_claimed_family_import_job
    from backend.app.services.family_package_jobs import claim_next_family_import_job
    from backend.app.services.family_variant_write_lock import try_share_family_variant_writes
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT status, worker_id, error, logs FROM family_import_jobs "
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
        metadata = row["metadata"] or {}
        return {
            "uuid": row["uuid"],
            "unfinished": bool(metadata.get("import_unfinished")),
            "snvs": await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id]
            ),
        }

    async def locks_free(family_uuid: str) -> bool:
        """Whether no writer, in any process, holds the family's variant-write locks."""
        async with sm() as s:
            free = await try_share_family_variant_writes(s, family_uuid)
            await s.rollback()
        return free

    def run_by(row: dict[str, Any], worker: _WorkerProcess) -> bool:
        return str(row["worker_id"] or "").startswith(f"{worker.pid}-")

    async def until_status(job_id: str, *statuses: str) -> dict[str, Any]:
        async def check() -> dict[str, Any] | None:
            row = await job(job_id)
            return row if row["status"] in statuses else None

        return await _eventually(check, what=f"job {job_id} to be {' or '.join(statuses)}")

    async def taken_over(job_id: str) -> dict[str, Any] | None:
        """Another worker takes the job for stopped, as its loop does: the heartbeat made ten
        minutes old and the claim in one transaction, so that the live import's next beat
        cannot come in between. A job left by an earlier run, claimed first, is handled as the
        loop handles it."""
        reclaimer = f"e2e-other-worker-{uuid4().hex[:8]}"
        for _ in range(50):
            async with sm() as s:
                await s.execute(
                    text(
                        "UPDATE family_import_jobs SET heartbeat_at = now() - interval '11 minutes' "
                        "WHERE id = CAST(:j AS uuid)"
                    ),
                    {"j": job_id},
                )
                claimed = await claim_next_family_import_job(s, worker_id=reclaimer)
            if claimed is None:
                return None
            try:
                await handle_claimed_family_import_job(claimed, worker_id=reclaimer)
            except Exception:  # noqa: BLE001 - the worker logs it and goes on
                pass
            if claimed["id"] == job_id:
                return {key: claimed[key] for key in ("status", "claimed_from")}
        return None

    out: dict[str, Any] = {}
    jobs: list[str] = []
    workers: list[_WorkerProcess] = []

    def worker_process(name: str, *, held: bool) -> _WorkerProcess:
        workers.append(_WorkerProcess(base, name, held=held))
        return workers[-1]

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def queue(prefix: str) -> tuple[str, str]:
                family_id = f"FAM_WORKER_{prefix}_{uuid4().hex[:8].upper()}"
                resp = await ac.post(
                    "/api/family-imports",
                    json={
                        "folder_path": str(_write_package(base / family_id, family_id)),
                        "project_id": project_id,
                        "dry_run": False,
                        "conflict_mode": "cancel",
                    },
                )
                assert resp.status_code == 200, resp.text
                jobs.append(str(resp.json()["_id"]))
                return family_id, jobs[-1]

            # 1. One process, as the image runs it, imports a family end to end.
            first = worker_process("end-to-end", held=False)
            await first.started()
            family_id, job_id = await queue("A")
            done = await until_status(job_id, "completed", "failed")
            out["end_to_end"] = {
                "job": done,
                "claimed_by_the_process": first.claimed(job_id),
                "family": await family(family_id),
                "stopped": await first.terminate(),
                "log": first.output(),
            }

            # 2. Two processes import two families at once.
            pair = [worker_process("pair-1", held=True), worker_process("pair-2", held=True)]
            for process in pair:
                await process.started()
            queued = [await queue("B"), await queue("C")]
            for process in pair:
                await process.inside()
            rows = [await job(job_id) for _family, job_id in queued]
            families = [await family(family_id) for family_id, _job in queued]
            record: dict[str, Any] = {
                "statuses_inside": [row["status"] for row in rows],
                "run_by_inside": sorted(
                    process.name for row in rows for process in pair if run_by(row, process)
                ),
                "snvs_inside": [state["snvs"] for state in families],
                "locks_free_inside": [await locks_free(state["uuid"]) for state in families],
            }
            for process in pair:
                process.let_go_on()
            record["jobs_after"] = [
                (await until_status(job_id, "completed", "failed"))["status"] for _f, job_id in queued
            ]
            families = [await family(family_id) for family_id, _job in queued]
            record["families_after"] = families
            record["locks_free_after"] = [await locks_free(state["uuid"]) for state in families]
            record["stopped"] = [await process.terminate() for process in pair]
            out["two_at_once"] = record

            # 3. A process stopped inside an import, and its job taken over by another.
            stopped = worker_process("stopped", held=True)
            await stopped.started()
            family_id, job_id = await queue("D")
            await stopped.inside()
            uuid = (await family(family_id))["uuid"]
            record = {"locks_free_inside": await locks_free(uuid)}
            record["stopped"] = await stopped.terminate()
            record["job_after_stop"] = await job(job_id)
            record["run_by_the_stopped_process"] = run_by(record["job_after_stop"], stopped)
            record["family_after_stop"] = await family(family_id)
            record["locks_free_after_stop"] = await locks_free(uuid)
            # Ten minutes on, its heartbeat is stale; a worker process started now takes it.
            async with sm() as s:
                await s.execute(
                    text(
                        "UPDATE family_import_jobs SET heartbeat_at = now() - interval '11 minutes' "
                        "WHERE id = CAST(:j AS uuid)"
                    ),
                    {"j": job_id},
                )
                await s.commit()
            successor = worker_process("successor", held=False)
            record["job_after_takeover"] = await until_status(job_id, "failed", "completed")
            # Logged once the claim has ended the job.
            ended_line = f"Family package import job {job_id} stopped part-way; ended it as interrupted"
            record["successor_ended_it"] = await _said(successor, ended_line)
            record["successor_claimed_it"] = successor.claimed(job_id)
            record["family_after_takeover"] = await family(family_id)
            record["successor_stopped"] = await successor.terminate()
            out["stopped"] = record

            # 4. A live process whose job another worker took for stopped.
            live = worker_process("live", held=True)
            await live.started()
            family_id, job_id = await queue("E")
            await live.inside()
            uuid = (await family(family_id))["uuid"]
            record = {"locks_free_inside": await locks_free(uuid)}
            record["claimed"] = await taken_over(job_id)
            stopped_line = (
                f"Family package import job {job_id} was stopped where it was: the job is no "
                "longer this worker's"
            )
            record["stopped_log"] = await _said(live, stopped_line)
            record["job_after"] = await job(job_id)
            record["family_after"] = await family(family_id)
            record["locks_free_after"] = await locks_free(uuid)
            # The worker goes on, ready for the next job; the import it stopped is not resumed.
            await asyncio.sleep(1.0)
            record["process_running"] = live.running()
            record["stopped"] = await live.terminate()
            out["live"] = record
    finally:
        for process in workers:
            if process.running():
                await process.terminate()
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

    base = tmp_path_factory.mktemp("import_worker_process")
    mp = pytest.MonkeyPatch()
    # The API checks a queued package's folder against the import roots; the worker processes
    # are given the same roots.
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base))


def _stopped_cleanly(stopped: dict[str, Any]) -> None:
    assert stopped["code"] == 0, stopped
    assert stopped["seconds"] is not None and stopped["seconds"] < _STOPPED_WITHIN, stopped


# --- one process imports a family end to end ------------------------------------------------


def test_a_worker_process_imports_a_family_end_to_end(snap) -> None:
    run = snap["end_to_end"]
    assert run["job"]["status"] == "completed", run["job"]["error"]
    # The job was taken by the worker process, not by this process's API.
    assert run["claimed_by_the_process"] is True
    assert run["family"]["snvs"] == _VARIANTS
    assert run["family"]["unfinished"] is False


def test_a_worker_process_stops_on_sigterm_and_exits_0(snap) -> None:
    run = snap["end_to_end"]
    _stopped_cleanly(run["stopped"])
    assert "Import worker process started with 1 package-import worker(s)" in run["log"]
    assert "Import worker process stopping" in run["log"]


# --- two processes import two families at once ----------------------------------------------


def test_two_worker_processes_import_two_families_at_once(snap) -> None:
    run = snap["two_at_once"]
    # Both imports inside their SNV dataset at the same time, each in its own process.
    assert run["statuses_inside"] == ["running", "running"]
    assert run["run_by_inside"] == ["pair-1", "pair-2"]
    assert run["snvs_inside"] == [2, 2]
    # Each process holds its family's variant-write locks: this process finds them taken.
    assert run["locks_free_inside"] == [False, False]
    assert run["jobs_after"] == ["completed", "completed"]
    assert [state["snvs"] for state in run["families_after"]] == [_VARIANTS, _VARIANTS]
    assert [state["unfinished"] for state in run["families_after"]] == [False, False]
    assert run["locks_free_after"] == [True, True]
    for stopped in run["stopped"]:
        _stopped_cleanly(stopped)


# --- a process stopped inside an import -----------------------------------------------------


def test_a_stopped_worker_process_leaves_its_job_as_a_stopped_process_does(snap) -> None:
    run = snap["stopped"]
    assert run["locks_free_inside"] is False
    _stopped_cleanly(run["stopped"])
    # The job is not ended by the stop: a worker takes it over once its heartbeat is stale.
    assert run["job_after_stop"]["status"] == "running"
    assert run["run_by_the_stopped_process"] is True
    # The import stopped where it was: 2 of 6 variants, the family marked, its locks released
    # with their connection.
    assert run["family_after_stop"]["snvs"] == 2
    assert run["family_after_stop"]["unfinished"] is True
    assert run["locks_free_after_stop"] is True


def test_another_worker_process_ends_a_stale_job_as_interrupted(snap) -> None:
    from backend.app.services.family_package_jobs import FAMILY_IMPORT_INTERRUPTED_ERROR

    run = snap["stopped"]
    job = run["job_after_takeover"]
    assert job["status"] == "failed"
    assert job["error"] == FAMILY_IMPORT_INTERRUPTED_ERROR
    assert job["worker_id"] is None
    assert any("heartbeat went stale while it was running" in str(line) for line in job["logs"])
    assert run["successor_claimed_it"] is True
    assert run["successor_ended_it"] is True
    # Not run again: what the stopped import wrote stays, and the family stays marked.
    assert run["family_after_takeover"]["snvs"] == 2
    assert run["family_after_takeover"]["unfinished"] is True
    _stopped_cleanly(run["successor_stopped"])


# --- a live process whose job another worker ended ------------------------------------------


def test_a_live_worker_process_stops_an_import_whose_job_another_worker_ended(snap) -> None:
    from backend.app.services.family_package_jobs import FAMILY_IMPORT_INTERRUPTED_ERROR

    run = snap["live"]
    assert run["locks_free_inside"] is False
    assert run["claimed"] == {"status": "failed", "claimed_from": "running"}
    assert run["stopped_log"] is True
    assert run["job_after"]["status"] == "failed"
    assert run["job_after"]["error"] == FAMILY_IMPORT_INTERRUPTED_ERROR
    # Stopped where it was, its locks released; the job keeps the claim's record.
    assert run["family_after"]["snvs"] == 2
    assert run["family_after"]["unfinished"] is True
    assert run["locks_free_after"] is True
    # The process goes on, and stops on SIGTERM as ever.
    assert run["process_running"] is True
    _stopped_cleanly(run["stopped"])
