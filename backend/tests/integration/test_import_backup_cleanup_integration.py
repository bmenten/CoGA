"""The backup tables of an import that stopped part-way are dropped (real Postgres and
ClickHouse).

An ``overwrite`` import copies the family's ClickHouse rows into backup tables named after
it (``<assembly>/SNAPSHOT/<owner>/<table>``) and drops them when it ends; one whose process
stopped cannot. This drives the real statements against the real servers:

- the backups are made under their import's key and listed by it, from ``system.tables``;
- the import jobs whose import may still run are read from Postgres;
- at startup the backups of a job that has ended are dropped, those of a running job and
  a recent one made outside a job are kept, and an old one made outside a job goes;
- the worker that ends a stopped job drops that job's backups.

The unit tests (test_import_backup_cleanup.py) cover the rules; the e2e test
(test_e2e_import_crash_leaves_family_marked.py) an overwrite stopped part-way. Skipped
unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_backups_no_running_import_owns_are_dropped() -> None:
    from backend.app.core.clickhouse import close_clickhouse_client, init_clickhouse_schema
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.clickhouse_family_snapshot import (
        drop_import_backup_tables,
        list_import_backup_tables,
        orphaned_import_backups,
        snapshot_family_clickhouse_state,
    )
    from backend.app.services.family_package_import import (
        drop_orphaned_import_backups,
        handle_claimed_family_import_job,
    )
    from backend.app.services.family_package_jobs import running_family_import_job_ids

    family = str(uuid4())  # a backup of a family with no rows is still a backup
    run_owner = f"run-{uuid4().hex}"
    long_ago = datetime(1990, 1, 1, tzinfo=timezone.utc)

    async def owned(owner: str) -> list[str]:
        return [table.name for table in await list_import_backup_tables(owner=owner)]

    async def scenario() -> None:
        await init_postgres_schema()
        await init_clickhouse_schema()
        sm = get_postgres_sessionmaker()
        jobs: dict[str, str] = {}
        async with sm() as s:
            for status in ("running", "failed"):
                jobs[status] = (
                    await s.execute(
                        text(
                            "INSERT INTO family_import_jobs (submitted_path, status, requested_by, "
                            "requested_at, heartbeat_at, completed_at) VALUES (:p, :s, "
                            "'admin@example.com', :at, now(), CASE WHEN :s = 'failed' THEN now() END) "
                            "RETURNING id::text"
                        ),
                        {"p": f"/data/families/BACKUP_{status}", "s": status, "at": long_ago},
                    )
                ).scalar_one()
            await s.commit()
        running_job, ended_job = jobs["running"], jobs["failed"]
        try:
            for owner in (running_job, ended_job, run_owner):
                await snapshot_family_clickhouse_state("GRCh38", family, owner=owner)
                assert len(await owned(owner)) == 5, owner
                assert all(f"GRCh38/SNAPSHOT/{owner}/" in name for name in await owned(owner))

            async with sm() as s:
                assert await running_family_import_job_ids(
                    s, [running_job, ended_job, str(uuid4())]
                ) == {running_job}

            # Startup: the ended job's backups go (with any other leftover the database
            # holds); the running job's and the recent one made outside a job stay.
            ended_backups = await owned(ended_job)
            dropped = await drop_orphaned_import_backups()
            assert set(ended_backups) <= set(dropped)
            assert await owned(ended_job) == []
            assert len(await owned(running_job)) == 5
            assert len(await owned(run_owner)) == 5

            # A day later, the one made outside a job goes too.
            later = datetime.now(timezone.utc) + timedelta(days=2)
            orphaned = orphaned_import_backups(
                await list_import_backup_tables(owner=run_owner),
                running_jobs=set(),
                now=later,
                grace=timedelta(days=1),
            )
            assert len(await drop_import_backup_tables(orphaned)) == 5
            assert await owned(run_owner) == []

            # The worker ends the running job, as its claim does once its heartbeat is
            # stale, and drops its backups.
            await handle_claimed_family_import_job(
                {"id": running_job, "status": "failed"}, worker_id="integration"
            )
            assert await owned(running_job) == []
        finally:
            for owner in (running_job, ended_job, run_owner):
                await drop_import_backup_tables(await list_import_backup_tables(owner=owner))
            async with sm() as s:
                await s.execute(
                    text("DELETE FROM family_import_jobs WHERE id::text = ANY(:ids)"),
                    {"ids": list(jobs.values())},
                )
                await s.commit()

    async def run() -> None:
        try:
            await scenario()
        finally:
            await close_clickhouse_client()
            await close_postgres_engine()

    asyncio.run(run())
