"""Gate for end-to-end tests that need real Postgres + ClickHouse.

A sibling of ``backend/tests/integration``; pytest collection hooks only apply to
their own directory subtree, so this gate must be duplicated here (it is NOT
inherited from ``integration/conftest.py``). E2E tests are skipped unless
``RUN_INTEGRATION=1`` so the normal backend job (and local runs) stay
self-contained. The CI ``e2e`` job sets the flag and provides the service
containers.
"""

from __future__ import annotations

import asyncio
import os

import pytest

# The one species the end-to-end harness makes (``_harness.SPECIES``).
_E2E_SPECIES = "Homo sapiens"


async def _species_of_another_suite() -> int:
    """How many species the database holds that the end-to-end suite does not make: none on an
    empty instance, or on one this suite alone has used. A database not migrated yet holds no
    ``species`` table, and none."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from backend.app.core.config import settings

    engine = create_async_engine(
        settings.postgres_dsn, connect_args=settings.postgres_connect_args, poolclass=NullPool
    )
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT count(*) FROM species WHERE name <> :name"), {"name": _E2E_SPECIES}
            )
            return int(result.scalar_one())
    except DBAPIError:
        return 0
    finally:
        await engine.dispose()


def pytest_sessionstart(session: pytest.Session) -> None:
    """The end-to-end suite verifies the pipeline from an instance no other suite has used, as CI
    runs it (each job its own service containers): it reads results of the whole database (the
    ClickHouse integrity sweep, the ancestry reference, the Variant Explorer's cohort) and makes
    the GRCh38 assembly under its own species. A database the integration suite has used holds
    species of other names; the run stops there, with one message, instead of failing module by
    module (docs/testing.md, "How to run")."""
    if os.environ.get("RUN_INTEGRATION") != "1":
        return
    if asyncio.run(_species_of_another_suite()):
        pytest.exit(
            "The end-to-end suite starts from databases no other suite has used, and this Postgres "
            "holds the integration suite's data: start Postgres and ClickHouse afresh "
            "(docs/testing.md, How to run).",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(
        reason="e2e test; set RUN_INTEGRATION=1 with Postgres + ClickHouse available"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
