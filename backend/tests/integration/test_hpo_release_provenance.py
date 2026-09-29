"""Integration test: a signed record names the HPO release of the latest import (real Postgres).

The annotation manifest freezes the HPO release into every signed report, on the reference
layer beside the assembly, the gene loci and the Monarch release. No table holds the
release: an import writes it onto every term it contains, and a term the new release no
longer lists keeps the release it came with. So the loaded release is the one on the most
recently written term. It is not the highest release string (a re-import of an older
release is what is loaded), nor the latest release that has one (an import without a
release must read as unknown, never as the release before it). The unit tests in
``test_annotation_manifest.py`` pin how each answer is recorded; this test runs the lookup
against the real schema, inside the SAVEPOINT it uses at sign-out.

Each case writes its terms in a transaction that is rolled back, with timestamps later than
any real import, so it reads only its own rows and leaves the database as it found it.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_OLDER_IMPORT = datetime(2998, 1, 1, tzinfo=timezone.utc)
_LATEST_IMPORT = datetime(2999, 1, 1, tzinfo=timezone.utc)
# Written by the older import and not by the latest one: its release is no longer loaded.
_LEFT_BEHIND = ("HP:9999902", "hp/releases/2026-09-01", date(2026, 9, 1), _OLDER_IMPORT)


def test_the_manifest_reads_the_release_of_the_latest_hpo_import() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.annotation_manifest_service import (
        UNAVAILABLE_MODULE_VERSION,
        _platform_modules,
    )

    async def _hpo_module(terms: list[tuple]) -> dict | None:
        async with get_postgres_sessionmaker()() as session:
            try:
                for hpo_id, release_version, release_date, updated_at in terms:
                    await session.execute(
                        text(
                            "INSERT INTO hpo_term (hpo_id, label, release_version, release_date, updated_at) "
                            "VALUES (:hpo_id, 'Integration-test term', :release_version, :release_date, "
                            ":updated_at) "
                            "ON CONFLICT (hpo_id) DO UPDATE SET release_version = EXCLUDED.release_version, "
                            "release_date = EXCLUDED.release_date, updated_at = EXCLUDED.updated_at"
                        ),
                        {
                            "hpo_id": hpo_id,
                            "release_version": release_version,
                            "release_date": release_date,
                            "updated_at": updated_at,
                        },
                    )
                modules = await _platform_modules(session, None)
                # The lookup ran in its own savepoint: the transaction around it goes on.
                written = (
                    await session.execute(
                        text("SELECT count(*) FROM hpo_term WHERE hpo_id = ANY(:ids)"),
                        {"ids": [term[0] for term in terms]},
                    )
                ).scalar_one()
                assert written == len(terms)
                return modules.get("hpo")
            finally:
                await session.rollback()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            # A re-import of an earlier release: the loaded release is the one it wrote,
            # not the later-dated string still on a term it did not list.
            downgrade = await _hpo_module(
                [("HP:9999901", "hp/releases/2026-06-06", date(2026, 6, 6), _LATEST_IMPORT), _LEFT_BEHIND]
            )
            assert downgrade == {"version": "hp/releases/2026-06-06", "detail": "2026-06-06"}

            # An import from a file without a release: unknown, never the release before it.
            unversioned = await _hpo_module([("HP:9999901", None, None, _LATEST_IMPORT), _LEFT_BEHIND])
            assert unversioned == {"version": UNAVAILABLE_MODULE_VERSION, "detail": "release not recorded"}
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
