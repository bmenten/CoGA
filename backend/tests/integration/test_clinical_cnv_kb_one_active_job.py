"""One clinical CNV knowledgebase rebuild at a time — real Postgres.

The rule is a partial unique index. It used to be keyed on the status, so a job could be
queued beside a running one: the second request was accepted, its switch to running then
hit the index outside the job's error handling, and it stayed queued for good, refusing
every later rebuild. And a build cut off with its server (a restart, a redeploy) stayed
running for good, with the same effect. Against the real schema, this checks that

* a rebuild is refused (409) while another is queued or running, and accepted once none is;
* two requests at the same moment get one job between them;
* a job whose worker is gone (no heartbeat for the stale window, or never started) is
  closed as failed by the status and by a rebuild request, which is then accepted, while a
  job that still heartbeats is kept; its old worker can no longer write to it;
* a database that still has the old index is upgraded in place by the idempotent schema
  load: the heartbeat columns are added, the jobs the old index let through are closed as
  failed, a running build is kept, the old index is replaced, and a later load leaves an
  active job alone;
* a table created fresh gets the new index and the heartbeat columns.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

pytestmark = pytest.mark.integration

_NEW_INDEX = "idx_clinical_cnv_kb_jobs_one_active"
_OLD_INDEX = "idx_clinical_cnv_kb_jobs_active"
# The index exactly as it was before the fix: keyed on the status, so a queued job and a
# running one did not collide.
_OLD_INDEX_DDL = (
    f"CREATE UNIQUE INDEX {_OLD_INDEX} ON clinical_cnv_kb_jobs USING btree (status) "
    "WHERE (status = ANY (ARRAY['queued'::text, 'running'::text]))"
)
# Either side of the ten-minute stale window.
_STALE = timedelta(minutes=11)
_FRESH = timedelta(minutes=9)
_DONE = "status = 'completed', worker_id = NULL, completed_at = now()"


@pytest.fixture()
def kb(monkeypatch: pytest.MonkeyPatch):
    """The job service with its build script present; a queued job records its start only."""
    from backend.app.services import clinical_cnv_kb_jobs as kb

    started: list[str] = []

    async def run_job(job_id: str) -> None:
        started.append(job_id)

    monkeypatch.setattr(kb, "_script_path", lambda: Path("/app/scripts/clinical_cnv_knowledgebase.py"))
    monkeypatch.setattr(kb, "_run_job", run_job)
    return kb, started


async def _seed_assembly(sm) -> tuple[str, str]:
    """A species and assembly of the test's own; deleting the species removes its jobs."""
    name = f"cnv-kb-{uuid4()}"
    async with sm() as s:
        species = (
            await s.execute(
                text(
                    "INSERT INTO species (name, common_name, tax_id) "
                    "VALUES (:n, 'test', :t) RETURNING id::text"
                ),
                {"n": name, "t": uuid4().int % 2_000_000_000},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01')"
            ),
            {"s": species, "a": name},
        )
        # The rule is global and the integration database is shared: start with no active job.
        await s.execute(text("DELETE FROM clinical_cnv_kb_jobs WHERE status IN ('queued', 'running')"))
        await s.commit()
    return species, name


async def _drop_species(sm, species: str) -> None:
    async with sm() as s:
        await s.execute(text("DELETE FROM species WHERE id = CAST(:s AS uuid)"), {"s": species})
        await s.commit()


async def _queue(kb, sm, assembly: str):
    async with sm() as s:
        return await kb.queue_clinical_cnv_kb_rebuild(
            s, assembly=assembly, skip_clinvar=True, requested_by="lab.admin"
        )


async def _refusal(kb, sm, assembly: str) -> int:
    with pytest.raises(HTTPException) as refused:
        await _queue(kb, sm, assembly)
    return refused.value.status_code


async def _status(kb, sm):
    async with sm() as s:
        return await kb.get_clinical_cnv_kb_status(s)


async def _insert_job(
    sm,
    assembly: str,
    *,
    status: str,
    requested_ago: timedelta,
    started_ago: timedelta | None = None,
    heartbeat_ago: timedelta | None = None,
) -> str:
    """A job row as a worker would have left it, its times that long ago (by the database clock)."""
    params: dict[str, object] = {
        "status": status,
        "a": assembly,
        "requested_ago": requested_ago,
        "started_ago": started_ago,
    }
    columns = "assembly_id, assembly_name, status, requested_at, started_at"
    values = (
        "id, assembly_name, :status, now() - CAST(:requested_ago AS interval), "
        "now() - CAST(:started_ago AS interval)"
    )
    if heartbeat_ago is not None:  # a table from before the heartbeat has no such columns
        columns += ", worker_id, heartbeat_at"
        values += ", 'a-worker-that-is-gone', now() - CAST(:heartbeat_ago AS interval)"
        params["heartbeat_ago"] = heartbeat_ago
    async with sm() as s:
        job_id = (
            await s.execute(
                text(
                    f"INSERT INTO clinical_cnv_kb_jobs ({columns}) "
                    f"SELECT {values} FROM assemblies WHERE assembly_name = :a RETURNING id::text"
                ),
                params,
            )
        ).scalar_one()
        await s.commit()
    return job_id


async def _job_status(sm, job_id: str) -> tuple[str, str | None]:
    async with sm() as s:
        row = (
            await s.execute(
                text("SELECT status, error FROM clinical_cnv_kb_jobs WHERE id = CAST(:j AS uuid)"),
                {"j": job_id},
            )
        ).one()
    return row[0], row[1]


async def _indexes(sm) -> dict[str, str]:
    async with sm() as s:
        rows = await s.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = 'clinical_cnv_kb_jobs'"
            )
        )
        return {name: definition for name, definition in rows.all()}


async def _columns(sm) -> set[str]:
    async with sm() as s:
        rows = await s.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'clinical_cnv_kb_jobs'"
            )
        )
        return {name for (name,) in rows.all()}


def _with_assembly(test):
    """Run ``test(sm, assembly)`` against the loaded schema, on an assembly of its own."""
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            species, assembly = await _seed_assembly(sm)
            try:
                await test(sm, assembly)
            finally:
                await _drop_species(sm, species)
        finally:
            # Whatever happened, leave the schema in its current shape for the tests that follow.
            await init_postgres_schema()
            await close_postgres_engine()

    asyncio.run(_run())


# --- one active job -------------------------------------------------------------------------------


def test_a_rebuild_is_refused_while_another_is_queued_or_running(kb) -> None:
    service, started = kb

    async def test(sm, assembly: str) -> None:
        first = await _queue(service, sm, assembly)
        assert await _refusal(service, sm, assembly) == 409  # while it is queued

        # The claim its run makes. Before the fix, a request from here on was accepted, and
        # that second job could never start.
        assert await service._claim_job(str(first.id), "worker-a") is not None
        assert await _refusal(service, sm, assembly) == 409  # while it is running

        assert await service._update_job(str(first.id), "worker-a", _DONE, {}) is True
        second = await _queue(service, sm, assembly)
        assert second.status == "queued" and second.id != first.id
        # Its own claim is not refused.
        assert await service._claim_job(str(second.id), "worker-b") is not None

        status = await _status(service, sm)
        assert status.active_job is not None and status.active_job.id == second.id
        assert status.active_job.status == "running"
        # A refused request started nothing.
        assert started == [str(first.id), str(second.id)]

    _with_assembly(test)


def test_two_requests_at_the_same_moment_get_one_job(kb) -> None:
    service, started = kb

    async def test(sm, assembly: str) -> None:
        # Each request has its own connection; the database, not a check before the insert,
        # decides which one wins.
        results = await asyncio.gather(
            _queue(service, sm, assembly),
            _queue(service, sm, assembly),
            return_exceptions=True,
        )
        jobs = [r for r in results if not isinstance(r, BaseException)]
        refusals = [r.status_code for r in results if isinstance(r, HTTPException)]
        assert len(jobs) == 1 and refusals == [409], results
        await asyncio.sleep(0)  # let the background start run
        assert started == [str(jobs[0].id)]

    _with_assembly(test)


# --- a job whose worker is gone -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "requested_ago", "started_ago", "heartbeat_ago"),
    [
        ("queued", _STALE, None, None),
        ("running", _STALE, _STALE, None),
        ("running", 6 * _STALE, 6 * _STALE, _STALE),
    ],
    ids=["queued-never-started", "running-from-before-heartbeats", "running-heartbeat-stopped"],
)
def test_a_job_whose_worker_is_gone_is_closed_and_a_new_rebuild_accepted(
    kb, status: str, requested_ago, started_ago, heartbeat_ago
) -> None:
    service, _started = kb

    async def test(sm, assembly: str) -> None:
        job_id = await _insert_job(
            sm,
            assembly,
            status=status,
            requested_ago=requested_ago,
            started_ago=started_ago,
            heartbeat_ago=heartbeat_ago,
        )
        # What the admin page polls: the job is no longer active, so the button is enabled.
        state = await _status(service, sm)
        assert state.active_job is None
        closed = next(job for job in state.recent_jobs if str(job.id) == job_id)
        assert closed.status == "failed" and (closed.error or "").startswith("Interrupted")
        assert closed.completed_at is not None

        job = await _queue(service, sm, assembly)
        assert job.status == "queued"

    _with_assembly(test)


def test_a_rebuild_request_closes_a_job_whose_worker_is_gone_itself(kb) -> None:
    service, _started = kb

    async def test(sm, assembly: str) -> None:
        # No status read in between: the request itself finds the job gone stale.
        stale = await _insert_job(sm, assembly, status="running", requested_ago=_STALE, started_ago=_STALE)
        job = await _queue(service, sm, assembly)
        assert job.status == "queued"
        assert (await _job_status(sm, stale))[0] == "failed"

    _with_assembly(test)


def test_a_job_that_still_heartbeats_is_kept(kb) -> None:
    service, _started = kb

    async def test(sm, assembly: str) -> None:
        # A long build: started an hour ago, its last heartbeat within the window.
        job_id = await _insert_job(
            sm,
            assembly,
            status="running",
            requested_ago=timedelta(hours=1),
            started_ago=timedelta(hours=1),
            heartbeat_ago=_FRESH,
        )
        state = await _status(service, sm)
        assert state.active_job is not None and str(state.active_job.id) == job_id
        assert await _refusal(service, sm, assembly) == 409
        assert (await _job_status(sm, job_id))[0] == "running"

    _with_assembly(test)


def test_a_closed_job_is_no_longer_its_old_workers(kb) -> None:
    service, _started = kb

    async def test(sm, assembly: str) -> None:
        job = await _queue(service, sm, assembly)
        job_id = str(job.id)
        assert await service._claim_job(job_id, "worker-a") is not None
        assert await service._claim_job(job_id, "worker-b") is None  # only a queued job is taken
        assert await service._update_job(job_id, "worker-b", "heartbeat_at = now()", {}) is False
        assert await service._update_job(job_id, "worker-a", "heartbeat_at = now()", {}) is True

        # Its worker falls silent past the window, and the status closes the job.
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE clinical_cnv_kb_jobs SET heartbeat_at = now() - CAST(:ago AS interval) "
                    "WHERE id = CAST(:j AS uuid)"
                ),
                {"ago": _STALE, "j": job_id},
            )
            await s.commit()
        assert (await _status(service, sm)).active_job is None

        # Should it come back, it finds the job no longer its own and changes nothing.
        assert await service._update_job(job_id, "worker-a", "heartbeat_at = now()", {}) is False
        assert await service._update_job(job_id, "worker-a", _DONE, {}) is False
        # Without an owner, only a job still queued can be closed.
        assert await service._update_job(job_id, None, "status = 'failed'", {}) is False
        status, error = await _job_status(sm, job_id)
        assert status == "failed" and (error or "").startswith("Interrupted")

    _with_assembly(test)


# --- the schema: upgrading an existing database, and a fresh one ------------------------------


@pytest.mark.parametrize(
    "left_behind",
    [("running", "queued"), ("queued",)],
    ids=["queued-beside-a-running-build", "queued-alone"],
)
def test_a_database_with_the_old_index_is_upgraded_in_place(kb, left_behind: tuple[str, ...]) -> None:
    service, _started = kb

    async def test(sm, assembly: str) -> None:
        # The table as it was before the fix, holding what the defect leaves behind: a job
        # queued while another build ran, whose switch to running then failed (alone once that
        # build has finished).
        async with sm() as s:
            await s.execute(text(f"DROP INDEX IF EXISTS {_NEW_INDEX}"))
            await s.execute(
                text(
                    "ALTER TABLE clinical_cnv_kb_jobs "
                    "DROP COLUMN IF EXISTS worker_id, DROP COLUMN IF EXISTS heartbeat_at"
                )
            )
            await s.execute(text(_OLD_INDEX_DDL))
            await s.commit()
        job_ids = {
            status: await _insert_job(
                sm,
                assembly,
                status=status,
                requested_ago=timedelta(minutes=2 if status == "running" else 1),
                started_ago=timedelta(minutes=2) if status == "running" else None,
            )
            for status in left_behind
        }

        from backend.app.core.postgres import init_postgres_schema

        await init_postgres_schema()  # the upgrade

        indexes = await _indexes(sm)
        assert _OLD_INDEX not in indexes
        assert indexes[_NEW_INDEX].startswith(f"CREATE UNIQUE INDEX {_NEW_INDEX} ")
        assert "((true))" in indexes[_NEW_INDEX]
        assert {"worker_id", "heartbeat_at"} <= await _columns(sm)

        status, error = await _job_status(sm, job_ids["queued"])
        assert status == "failed" and (error or "").startswith("Closed on upgrade")
        if "running" in job_ids:
            # A running build may still be building: it is kept, and still refuses others.
            assert (await _job_status(sm, job_ids["running"]))[0] == "running"
            assert await _refusal(service, sm, assembly) == 409
            async with sm() as s:
                await s.execute(
                    text(f"UPDATE clinical_cnv_kb_jobs SET {_DONE} WHERE id = CAST(:j AS uuid)"),
                    {"j": job_ids["running"]},
                )
                await s.commit()

        # Rebuilds are accepted again, and a later load leaves an active job alone.
        job = await _queue(service, sm, assembly)
        await init_postgres_schema()
        assert (await _job_status(sm, str(job.id)))[0] == "queued"

    _with_assembly(test)


def test_a_fresh_table_gets_the_new_index_and_the_heartbeat_columns() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                await s.execute(text("DROP TABLE clinical_cnv_kb_jobs"))
                await s.commit()
            await init_postgres_schema()  # creates it again, as on a new database

            indexes = await _indexes(sm)
            assert set(indexes) == {
                "clinical_cnv_kb_jobs_pkey",
                "idx_clinical_cnv_kb_jobs_requested_at",
                _NEW_INDEX,
            }
            assert "((true))" in indexes[_NEW_INDEX]
            assert {"worker_id", "heartbeat_at"} <= await _columns(sm)
        finally:
            await init_postgres_schema()
            await close_postgres_engine()

    asyncio.run(_run())
