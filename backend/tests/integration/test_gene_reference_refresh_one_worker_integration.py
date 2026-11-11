"""One worker runs a gene-reference refresh at a time — real Postgres.

Another worker takes a running refresh up once its heartbeat is older than
``GENE_REFERENCE_STALE_HEARTBEAT``. The worker wrote no heartbeat while it loaded the bulk
sources, so a load longer than that let a second worker take the job up and run it beside the
first; and a run that failed ended the job whoever's it was by then. Against the real schema,
with the sources, the gene choice and the external lookups as stand-ins, this checks that

* the worker's heartbeat advances while the sources load, and the refresh then completes;
* a worker whose job another worker took up while it loaded the sources, or while it wrote the
  gene records, stops: the job stays the other worker's, running;
* a worker whose run fails after another worker took its job up leaves that job as it is,
  while a run that fails on its own job ends it failed and frees the slot;
* a heartbeat or a failed end from the worker that lost the job changes nothing.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

WORKER = "gene-worker-a"
OTHER_WORKER = "gene-worker-b"
# Past the five-minute stale window.
_STALE = timedelta(minutes=6)


async def _queue_job(sm) -> str:
    """A refresh of one gene, queued as a start queues it (no account's)."""
    async with sm() as s:
        # The slot is global and the integration database is shared: start with no active job.
        await s.execute(text("DELETE FROM gene_info_refresh_jobs WHERE active_slot IS NOT NULL"))
        job_id = (
            await s.execute(
                text(
                    "INSERT INTO gene_info_refresh_jobs (scope, symbol, status, active_slot, requested_by) "
                    "VALUES ('symbol', 'GENE1', 'queued', 'gene_reference', 'startup-bootstrap') "
                    "RETURNING id::text"
                )
            )
        ).scalar_one()
        await s.commit()
    return job_id


async def _claim(sm, job_id: str, worker: str) -> None:
    from backend.app.services import gene_info_jobs_pg as gene_jobs

    async with sm() as s:
        taken = await gene_jobs.claim_next_gene_reference_refresh_job(s, worker_id=worker)
    assert taken is not None and taken["id"] == job_id and taken["worker_id"] == worker


async def _take_up(sm, job_id: str, worker: str) -> None:
    """``worker`` takes the job up, as its claim does once the job's heartbeat is stale."""
    async with sm() as s:
        await s.execute(
            text("UPDATE gene_info_refresh_jobs SET heartbeat_at = :at WHERE id = CAST(:j AS uuid)"),
            {"at": datetime.now(timezone.utc) - _STALE, "j": job_id},
        )
        await s.commit()
    await _claim(sm, job_id, worker)


async def _hand_over(sm, job_id: str, worker: str) -> None:
    """``worker`` takes the job up while this worker's heartbeat runs beside: the job as another
    worker's claim leaves it, in one statement, so that no heartbeat of this worker's comes
    between the heartbeat going stale and the claim."""
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE gene_info_refresh_jobs SET worker_id = :w, heartbeat_at = :at "
                "WHERE id = CAST(:j AS uuid) AND status = 'running'"
            ),
            {"w": worker, "at": datetime.now(timezone.utc), "j": job_id},
        )
        await s.commit()


async def _job(sm, job_id: str) -> dict[str, Any]:
    async with sm() as s:
        row = (
            await s.execute(
                text(
                    "SELECT status, worker_id, active_slot, error, heartbeat_at "
                    "FROM gene_info_refresh_jobs WHERE id = CAST(:j AS uuid)"
                ),
                {"j": job_id},
            )
        ).mappings().one()
    return dict(row)


def _stand_ins(monkeypatch: pytest.MonkeyPatch, *, load, genes: dict[str, list[dict]] | None = None):
    """The sources load with ``load``; the genes chosen are ``genes`` (none by default); the
    external lookup and the gene record's write are no-ops that record the gene. The refresh's
    own job updates run against the database."""
    from backend.app.services import gene_info_jobs_pg as gene_jobs

    ran: list[str] = []

    async def gene_groups(_session, **_kwargs):
        ran.append("genes chosen")
        return gene_jobs.HumanGeneContext(species={}, assemblies=[]), dict(genes or {})

    async def fetch_bundle(*, symbol: str, **_kwargs):
        ran.append(f"looked up {symbol}")
        return {}

    async def upsert(*_args, **_kwargs):
        return None

    monkeypatch.setattr(gene_jobs, "GENE_REFERENCE_HEARTBEAT_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(gene_jobs, "load_human_gene_bulk_context", load)
    monkeypatch.setattr(gene_jobs, "_load_human_gene_groups", gene_groups)
    monkeypatch.setattr(gene_jobs, "fetch_external_gene_bundle", fetch_bundle)
    monkeypatch.setattr(gene_jobs, "_upsert_gene_info_row", upsert)
    return ran


def _with_job(test):
    """Run ``test(sm, job_id)`` against the loaded schema, with a refresh job WORKER has taken up."""
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            job_id = await _queue_job(sm)
            try:
                await _claim(sm, job_id, WORKER)
                await test(sm, job_id)
            finally:
                async with sm() as s:
                    await s.execute(text("DELETE FROM gene_info_refresh_jobs WHERE id = CAST(:j AS uuid)"), {"j": job_id})
                    await s.commit()
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def _run_job(job_id: str):
    from backend.app.services import gene_info_jobs_pg as gene_jobs

    return gene_jobs.run_gene_reference_refresh_job(job_id=job_id, worker_id=WORKER)


def test_the_heartbeat_advances_while_the_sources_load(monkeypatch: pytest.MonkeyPatch) -> None:
    async def test(sm, job_id: str) -> None:
        seen: list[datetime] = []

        async def load(**_kwargs):
            seen.append((await _job(sm, job_id))["heartbeat_at"])
            await asyncio.sleep(0.4)
            seen.append((await _job(sm, job_id))["heartbeat_at"])
            return None

        ran = _stand_ins(monkeypatch, load=load)

        await _run_job(job_id)

        # The job's heartbeat is written while the sources load.
        assert seen[1] > seen[0], "no heartbeat was written while the sources loaded"
        assert ran == ["genes chosen"]
        job = await _job(sm, job_id)
        assert (job["status"], job["worker_id"], job["active_slot"]) == ("completed", None, None)

    _with_job(test)


def test_a_worker_whose_job_was_taken_up_while_it_loaded_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    async def test(sm, job_id: str) -> None:
        async def load(**_kwargs):
            await _hand_over(sm, job_id, OTHER_WORKER)
            await asyncio.sleep(0.5)
            return None

        ran = _stand_ins(monkeypatch, load=load)

        await _run_job(job_id)

        # It chooses no genes and writes no records beside the other worker.
        assert ran == []
        job = await _job(sm, job_id)
        assert (job["status"], job["worker_id"], job["active_slot"], job["error"]) == (
            "running",
            OTHER_WORKER,
            "gene_reference",
            None,
        )

    _with_job(test)


def test_a_worker_whose_job_was_taken_up_while_it_wrote_records_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.services import gene_info_jobs_pg as gene_jobs

    async def test(sm, job_id: str) -> None:
        async def load(**_kwargs):
            return None

        genes = {symbol: [{"assembly_id": None, "gene_id": None}] for symbol in ("GENE1", "GENE2", "GENE3")}
        ran = _stand_ins(monkeypatch, load=load, genes=genes)
        monkeypatch.setattr(gene_jobs, "GENE_REFERENCE_PROGRESS_COMMIT_SYMBOLS", 1)

        async def fetch_bundle(*, symbol: str, **_kwargs):
            ran.append(f"looked up {symbol}")
            if symbol == "GENE2":
                await _take_up(sm, job_id, OTHER_WORKER)
            return {}

        monkeypatch.setattr(gene_jobs, "fetch_external_gene_bundle", fetch_bundle)

        await _run_job(job_id)

        # GENE1's progress was committed while the job was this worker's; GENE2's commit found
        # it the other worker's, and the run stopped there.
        assert ran == ["genes chosen", "looked up GENE1", "looked up GENE2"]
        job = await _job(sm, job_id)
        assert (job["status"], job["worker_id"], job["active_slot"], job["error"]) == (
            "running",
            OTHER_WORKER,
            "gene_reference",
            None,
        )

    _with_job(test)


def test_a_failed_run_leaves_a_job_another_worker_has_taken_up(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.services import gene_info_jobs_pg as gene_jobs

    async def test(sm, job_id: str) -> None:
        async def load(**_kwargs):
            await _hand_over(sm, job_id, OTHER_WORKER)
            raise RuntimeError("a source could not be read")

        _stand_ins(monkeypatch, load=load)
        # No heartbeat finds the job taken first: the run fails, as one whose worker was paused
        # past the stale window does.
        monkeypatch.setattr(gene_jobs, "GENE_REFERENCE_HEARTBEAT_SECONDS", 60.0, raising=False)

        with pytest.raises(RuntimeError):
            await _run_job(job_id)

        # The failed run leaves the other worker's job running, in its slot.
        job = await _job(sm, job_id)
        assert (job["status"], job["worker_id"], job["active_slot"], job["error"]) == (
            "running",
            OTHER_WORKER,
            "gene_reference",
            None,
        )

    _with_job(test)


def test_a_failed_run_ends_its_own_job_and_frees_the_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    async def test(sm, job_id: str) -> None:
        async def load(**_kwargs):
            raise RuntimeError("a source could not be read")

        _stand_ins(monkeypatch, load=load)

        with pytest.raises(RuntimeError):
            await _run_job(job_id)

        # The job's error names the error by its kind, never by its text.
        job = await _job(sm, job_id)
        assert (job["status"], job["worker_id"], job["active_slot"], job["error"]) == (
            "failed",
            None,
            None,
            "The gene reference refresh failed (RuntimeError). The genes it had saved keep their new "
            "records, the others the ones from before; the server log shows where it failed.",
        )

    _with_job(test)


def test_the_worker_that_lost_the_job_changes_nothing_in_it() -> None:
    from backend.app.services import gene_info_jobs_pg as gene_jobs

    async def test(sm, job_id: str) -> None:
        await _take_up(sm, job_id, OTHER_WORKER)
        before = await _job(sm, job_id)

        assert await gene_jobs._beat_gene_reference_job(job_id=job_id, worker_id=WORKER) is False
        async with sm() as s:
            await gene_jobs._end_failed_job(s, job_id=job_id, worker_id=WORKER, error="not this worker's")
        assert await _job(sm, job_id) == before

        # The worker that has it still writes its heartbeat.
        assert await gene_jobs._beat_gene_reference_job(job_id=job_id, worker_id=OTHER_WORKER) is True
        assert (await _job(sm, job_id))["heartbeat_at"] > before["heartbeat_at"]

    _with_job(test)
