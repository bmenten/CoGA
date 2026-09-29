"""Integration test: every reader names the HPO release of the latest import (real Postgres).

Three places name the loaded HPO release: the annotation manifest, which freezes it into
every signed report; the HPO admin summary; and the key of the cached phenotype ranking,
which must change when the ontology the scores read changes. No table holds the release:
an import writes it onto every term it contains, and a term the new release no longer lists
keeps the release it came with. So the loaded release is the one on the most recently
written term. It is not the highest release string (a re-import of an older release is what
is loaded), nor the latest release that has one (an import without a release must read as
unknown, never as the release before it). The unit tests pin how each reader uses the
answer; this test runs the lookups against the real schema, the manifest's inside the
SAVEPOINT it uses at sign-out.

Each case writes its terms in a transaction that is rolled back, with timestamps later than
any real import, so it reads only its own rows and leaves the database as it found it.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_OLDER_IMPORT = datetime(2998, 1, 1, tzinfo=timezone.utc)
_LATEST_IMPORT = datetime(2999, 1, 1, tzinfo=timezone.utc)
# Written by the older import and not by the latest one: its release is no longer loaded.
_LEFT_BEHIND = ("HP:9999902", "hp/releases/2026-09-01", date(2026, 9, 1), _OLDER_IMPORT)
# A re-import of an earlier release: the loaded release is the one it wrote, not the
# later-dated string still on a term it did not list.
_DOWNGRADE = [("HP:9999901", "hp/releases/2026-06-06", date(2026, 6, 6), _LATEST_IMPORT), _LEFT_BEHIND]
# An import from a file without a release, after one with a release.
_UNVERSIONED = [("HP:9999901", None, None, _LATEST_IMPORT), _LEFT_BEHIND]


async def _write_terms(session: Any, terms: list[tuple]) -> None:
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


def test_the_admin_summary_and_the_ranking_key_name_the_loaded_release() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services import variant_ranking_cache as vrc
    from backend.app.services.hpo_service import get_hpo_admin_summary

    async def _read(terms: list[tuple]) -> tuple[dict, dict]:
        async with get_postgres_sessionmaker()() as session:
            try:
                await _write_terms(session, terms)
                summary = await get_hpo_admin_summary(session)
                reference = await vrc._reference_versions(session, SimpleNamespace(assembly_id=None))
                return summary, reference
            finally:
                await session.rollback()

    async def _run() -> None:
        try:
            await init_postgres_schema()
            summary, reference = await _read(_DOWNGRADE)
            # The page's release, its date and the last sync all describe the latest import.
            assert (summary["release_version"], summary["release_date"]) == (
                "hp/releases/2026-06-06",
                date(2026, 6, 6),
            )
            assert summary["last_sync_date"] == _LATEST_IMPORT
            # The ranking key follows the ontology the scores read, so rankings computed on
            # the later release are not served after the downgrade.
            assert reference["hpo_release"] == "hp/releases/2026-06-06"
            assert reference["hpo_imported_at"] == _LATEST_IMPORT

            summary, reference = await _read(_UNVERSIONED)
            assert (summary["release_version"], summary["release_date"]) == (None, None)
            assert reference["hpo_release"] is None
            # Its import time tells this ontology apart from any other without a release.
            assert reference["hpo_imported_at"] == _LATEST_IMPORT
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


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
                await _write_terms(session, terms)
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
            downgrade = await _hpo_module(_DOWNGRADE)
            assert downgrade == {"version": "hp/releases/2026-06-06", "detail": "2026-06-06"}

            # Without a release: unknown, never the release before it.
            unversioned = await _hpo_module(_UNVERSIONED)
            assert unversioned == {"version": UNAVAILABLE_MODULE_VERSION, "detail": "release not recorded"}
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
