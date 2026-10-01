"""``GET /metrics`` reads the active import jobs from a real Postgres.

The unit tests fake the query; this one runs it against the real ``family_import_jobs`` table:
jobs that are queued, validating or running are counted per status, finished ones are not,
and each status reports how long since its least recent sign of life (heartbeat, start or
request). Other tests may leave jobs behind in the shared database, so it checks what its own
jobs add, then removes them.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import text

pytestmark = pytest.mark.integration


def _jobs(body: bytes) -> dict[tuple[str, str], float]:
    return {
        (sample.name, sample.labels["status"]): sample.value
        for family in text_string_to_metric_families(body.decode())
        for sample in family.samples
        if sample.name.startswith("coga_family_import_job")
    }


def test_active_import_jobs_are_read_from_postgres() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.operational_metrics import render_metrics

    marker = f"/imports/metrics-integration-{uuid4()}"
    now = datetime.now(timezone.utc)
    # (status, requested ago, started ago, heartbeat ago)
    jobs = [
        ("queued", timedelta(minutes=5), None, None),
        ("validating", timedelta(minutes=3), timedelta(minutes=2), timedelta(minutes=1)),
        ("running", timedelta(hours=1), timedelta(minutes=50), timedelta(minutes=20)),
        ("completed", timedelta(days=2), timedelta(days=2), timedelta(days=2)),
        ("failed", timedelta(days=3), timedelta(days=3), timedelta(days=3)),
    ]

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            before = _jobs(await render_metrics())
            async with sm() as session:
                for status, requested_ago, started_ago, heartbeat_ago in jobs:
                    await session.execute(
                        text(
                            "INSERT INTO family_import_jobs "
                            "(submitted_path, status, requested_by, requested_at, started_at, heartbeat_at) "
                            "VALUES (:path, :status, 'metrics-integration-test', :requested, :started, :heartbeat)"
                        ),
                        {
                            "path": marker,
                            "status": status,
                            "requested": now - requested_ago,
                            "started": now - started_ago if started_ago else None,
                            "heartbeat": now - heartbeat_ago if heartbeat_ago else None,
                        },
                    )
                await session.commit()
            try:
                after = _jobs(await render_metrics())
            finally:
                async with sm() as session:
                    await session.execute(
                        text("DELETE FROM family_import_jobs WHERE submitted_path = :path"), {"path": marker}
                    )
                    await session.commit()

            for status in ("queued", "validating", "running"):
                assert after[("coga_family_import_jobs", status)] == before.get(("coga_family_import_jobs", status), 0.0) + 1
            assert ("coga_family_import_jobs", "completed") not in after
            assert ("coga_family_import_jobs", "failed") not in after
            # The least recent sign of life: a queued job's request, a running job's heartbeat.
            assert after[("coga_family_import_job_staleness_seconds", "queued")] >= timedelta(minutes=5).total_seconds()
            assert after[("coga_family_import_job_staleness_seconds", "running")] >= timedelta(minutes=20).total_seconds()
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
