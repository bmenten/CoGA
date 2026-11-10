"""The package list names the families imported from each folder (E2E, real Postgres and ClickHouse).

A package folder stays on the share after its import, so the import page's list kept every
folder ever imported. The list (``GET /family-imports/packages``) now gives each folder the
families whose package record names it (``families.metadata.package_import.folder_path``), and
the import page hides a folder whose families all imported completely.

Through the app over HTTP (an in-process ``httpx.ASGITransport`` client on one event loop, see
test_e2e_api_contract.py), as the e2e administrator, for two one-sample packages in an import
root: before any import neither names a family; once one is imported, its folder names that
family, with when it was registered, and the other folder none; flagged import-incomplete, the
family is told so; and a package record whose folder ends with a ``/`` still names the folder.

The values are synthetic: the family is new and uniquely named, the job rows end completed.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import one_sample_package, run_import_job

pytestmark = pytest.mark.integration


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    imported_id, waiting_id = f"E2E_SCAN_IMPORTED_{run}", f"E2E_SCAN_WAITING_{run}"
    imported = one_sample_package(base / imported_id, imported_id, variants=2, datasets=("snv",))
    one_sample_package(base / waiting_id, waiting_id, variants=2, datasets=("snv",))
    out: dict[str, Any] = {"imported": imported_id, "waiting": waiting_id}

    async def update_record(sql: str) -> None:
        async with sessionmaker() as session:
            await session.execute(text(sql), {"f": imported_id})
            await session.commit()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"
        out["before"] = capture(await client.get("/api/family-imports/packages"))
        out["job"] = await run_import_job(imported, project_id, f"e2e-scan-{uuid4().hex[:8]}")
        out["after"] = capture(await client.get("/api/family-imports/packages"))
        async with sessionmaker() as session:
            out["registered_at"] = (
                await session.execute(
                    text("SELECT metadata -> 'package_import' ->> 'registered_at' FROM families WHERE family_id = :f"),
                    {"f": imported_id},
                )
            ).scalar_one()
        await update_record(
            "UPDATE families SET metadata = metadata || jsonb_build_object('import_incomplete', "
            "jsonb_build_object('failed_datasets', jsonb_build_array('snv'))) WHERE family_id = :f"
        )
        out["incomplete"] = capture(await client.get("/api/family-imports/packages"))
        await update_record(
            "UPDATE families SET metadata = (metadata - 'import_incomplete') || jsonb_build_object("
            "'package_import', (metadata -> 'package_import') || jsonb_build_object('folder_path', "
            "(metadata -> 'package_import' ->> 'folder_path') || '/')) WHERE family_id = :f"
        )
        out["slash"] = capture(await client.get("/api/family-imports/packages"))
    out["folder"] = str(imported.resolve())
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_scan_names_imported")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, run))


def _families(listed: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    assert listed["status"] == 200, listed
    return {item["name"]: item["imported_families"] for item in listed["json"]}


def test_before_any_import_no_folder_names_a_family(ran) -> None:
    assert _families(ran["before"]) == {ran["imported"]: [], ran["waiting"]: []}


def test_the_imported_folder_names_its_family_and_the_other_none(ran) -> None:
    from datetime import datetime

    assert ran["job"]["status"] == "completed", ran["job"]
    families = _families(ran["after"])
    assert families[ran["waiting"]] == []
    [family] = families[ran["imported"]]
    assert (family["family_id"], family["import_incomplete"]) == (ran["imported"], False)
    assert datetime.fromisoformat(family["registered_at"]) == datetime.fromisoformat(ran["registered_at"])
    folders = {item["name"]: item["folder_path"] for item in ran["after"]["json"]}
    assert folders[ran["imported"]] == ran["folder"]


def test_a_family_flagged_import_incomplete_is_told_so(ran) -> None:
    [family] = _families(ran["incomplete"])[ran["imported"]]
    assert family["import_incomplete"] is True


def test_a_record_whose_folder_ends_with_a_slash_still_names_the_folder(ran) -> None:
    [family] = _families(ran["slash"])[ran["imported"]]
    assert (family["family_id"], family["import_incomplete"]) == (ran["imported"], False)
