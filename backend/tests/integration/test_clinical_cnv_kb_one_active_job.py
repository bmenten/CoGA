"""One clinical CNV knowledgebase rebuild at a time — real Postgres.

The rule is a partial unique index. It used to be keyed on the status, so a job could be
queued beside a running one: the second request was accepted, its switch to running then
hit the index outside the job's error handling, and it stayed queued for good, refusing
every later rebuild. Against the real schema, this checks that

* a rebuild is refused (409) while another is queued or running, and accepted once none is;
* two requests at the same moment get one job between them;
* a database that still has the old index is upgraded in place by the idempotent schema
  load: the jobs the old index let through are closed as failed, a running build is kept,
  the old index is replaced, and a later load leaves an active job alone;
* a table created fresh gets the new index only.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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


async def _indexes(sm) -> dict[str, str]:
    async with sm() as s:
        rows = await s.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = 'clinical_cnv_kb_jobs'"
            )
        )
        return {name: definition for name, definition in rows.all()}


def test_a_rebuild_is_refused_while_another_is_queued_or_running(kb) -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    service, started = kb

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            species, assembly = await _seed_assembly(sm)
            try:
                first = await _queue(service, sm, assembly)
                assert await _refusal(service, sm, assembly) == 409  # while it is queued

                # The switch the job's run makes. Before the fix, a request from here on was
                # accepted, and that second job could never start.
                await service._update_job(str(first.id), "status = 'running', started_at = now()", {})
                assert await _refusal(service, sm, assembly) == 409  # while it is running

                await service._update_job(str(first.id), "status = 'completed', completed_at = now()", {})
                second = await _queue(service, sm, assembly)
                assert second.status == "queued" and second.id != first.id
                # Its own switch to running is not refused.
                await service._update_job(str(second.id), "status = 'running', started_at = now()", {})

                async with sm() as s:
                    status = await service.get_clinical_cnv_kb_status(s)
                assert status.active_job is not None and status.active_job.id == second.id
                assert status.active_job.status == "running"
                # A refused request started nothing.
                assert started == [str(first.id), str(second.id)]
            finally:
                await _drop_species(sm, species)
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_two_requests_at_the_same_moment_get_one_job(kb) -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    service, started = kb

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            species, assembly = await _seed_assembly(sm)
            try:
                # Each request has its own connection; the database, not a check before the
                # insert, decides which one wins.
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
            finally:
                await _drop_species(sm, species)
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


@pytest.mark.parametrize(
    "left_behind",
    [("running", "queued"), ("queued",)],
    ids=["queued-beside-a-running-build", "queued-alone"],
)
def test_a_database_with_the_old_index_is_upgraded_in_place(kb, left_behind: tuple[str, ...]) -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )

    service, _started = kb

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            species, assembly = await _seed_assembly(sm)
            try:
                # The table as it was before the fix, holding what the defect leaves behind:
                # a job queued while another build ran, whose switch to running then failed
                # (alone once that build has finished).
                job_ids: dict[str, str] = {}
                async with sm() as s:
                    await s.execute(text(f"DROP INDEX {_NEW_INDEX}"))
                    await s.execute(text(_OLD_INDEX_DDL))
                    for status in left_behind:
                        job_ids[status] = (
                            await s.execute(
                                text(
                                    "INSERT INTO clinical_cnv_kb_jobs "
                                    "(assembly_id, assembly_name, status, started_at) "
                                    "SELECT id, assembly_name, :status, CAST(:started_at AS timestamptz) "
                                    "FROM assemblies WHERE assembly_name = :a RETURNING id::text"
                                ),
                                {
                                    "status": status,
                                    "started_at": datetime.now(timezone.utc) if status == "running" else None,
                                    "a": assembly,
                                },
                            )
                        ).scalar_one()
                    await s.commit()

                await init_postgres_schema()  # the upgrade

                indexes = await _indexes(sm)
                assert _OLD_INDEX not in indexes
                assert indexes[_NEW_INDEX].startswith(f"CREATE UNIQUE INDEX {_NEW_INDEX} ")
                assert "((true))" in indexes[_NEW_INDEX]

                async with sm() as s:
                    jobs = {
                        row["id"]: row
                        for row in (
                            await s.execute(
                                text(
                                    "SELECT id::text AS id, status, error, completed_at "
                                    "FROM clinical_cnv_kb_jobs WHERE assembly_name = :a"
                                ),
                                {"a": assembly},
                            )
                        ).mappings().all()
                    }
                orphan = jobs[job_ids["queued"]]
                assert orphan["status"] == "failed" and orphan["completed_at"] is not None
                assert orphan["error"].startswith("Closed on upgrade")
                if "running" in job_ids:
                    # A running build may still be building: it is kept, and still refuses others.
                    assert jobs[job_ids["running"]]["status"] == "running"
                    assert await _refusal(service, sm, assembly) == 409
                    await service._update_job(
                        job_ids["running"], "status = 'completed', completed_at = now()", {}
                    )

                # Rebuilds are accepted again, and a later load leaves an active job alone.
                job = await _queue(service, sm, assembly)
                await init_postgres_schema()
                async with sm() as s:
                    still = (
                        await s.execute(
                            text("SELECT status FROM clinical_cnv_kb_jobs WHERE id = CAST(:j AS uuid)"),
                            {"j": str(job.id)},
                        )
                    ).scalar_one()
                assert still == "queued"
            finally:
                await _drop_species(sm, species)
        finally:
            # Whatever happened above, leave the schema in its current shape for the tests
            # that follow.
            await init_postgres_schema()
            await close_postgres_engine()

    asyncio.run(_run())


def test_a_fresh_table_gets_the_new_index_only() -> None:
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
        finally:
            await init_postgres_schema()
            await close_postgres_engine()

    asyncio.run(_run())
