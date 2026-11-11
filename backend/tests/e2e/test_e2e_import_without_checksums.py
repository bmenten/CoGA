"""A package file without checksums is delivered, warned of, approved and imported (E2E, real Postgres and ClickHouse).

The owner's decision of 2026-11-11: a checksum per file in ``coga-package.json`` is optional.
Through the watcher's round, the app over HTTP (an in-process ``httpx.ASGITransport`` client, see
test_e2e_api_contract.py) as the e2e administrator, and the import job as a worker runs it, for a
synthetic one-sample package whose package file lists its files with their sizes only:

- the delivery is ready, with the warning that the copy is unchecked
  (``package_files_without_checksum``), and keeps the SHA-256 CoGA computed for each file;
- approval without acknowledging the warning is refused (400); acknowledged, the import runs;
- the family's raw-file rows hold each file's SHA-256, the one the delivery's check computed.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, job_outcome
from backend.tests.e2e.test_e2e_import_watcher import package_folder, write_package

pytestmark = pytest.mark.integration


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import import_watcher
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        project = (
            await session.execute(text("SELECT name FROM projects WHERE id = CAST(:p AS uuid)"), {"p": project_id})
        ).scalar_one()
    family = f"E2E_NO_CHECKSUMS_{run}"
    folder = package_folder(base, family, project)
    write_package(folder, project=project, family_id=family, checksums=False)
    await import_watcher.run_watch_round(sessionmaker, [base.resolve()])
    out: dict[str, Any] = {
        "family": family,
        "files": {
            path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*"))
            if path.is_file() and path.name != "coga-package.json"
        },
    }
    async with sessionmaker() as session:
        delivery = dict(
            (
                await session.execute(
                    text(
                        "SELECT id::text AS id, state, warnings, package_check FROM import_deliveries "
                        "WHERE folder_name = :n ORDER BY first_seen_at DESC LIMIT 1"
                    ),
                    {"n": family},
                )
            ).mappings().one()
        )
    out["delivery"] = delivery
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"
        url = f"/api/family-imports/deliveries/{delivery['id']}/approve"
        out["unacknowledged"] = capture(await client.post(url, json={"revision": 0}))
        approved = capture(await client.post(url, json={"revision": 0, "acknowledge_warnings": True}))
        out["approved"] = approved
    job_id = approved["json"]["job"]["_id"]
    worker = f"e2e-no-checksums-{uuid4().hex[:8]}"
    await claim_import_job(job_id, worker)
    await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
    out["job"] = await job_outcome(job_id)
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT r.storage_path, r.sha256 FROM raw_import_files r JOIN families f ON f.id = r.family_id "
                    "WHERE f.family_id = :f AND r.dataset <> 'manifest'"
                ),
                {"f": family},
            )
        ).mappings().all()
    out["recorded"] = {Path(row["storage_path"]).relative_to(folder.resolve()).as_posix(): row["sha256"] for row in rows}
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("without_checksums")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    return _harness.run_async(lambda: _exercise(base, uuid4().hex[:10].upper()))


def test_the_delivery_is_ready_with_the_warning_and_keeps_the_computed_sha256(ran) -> None:
    delivery = ran["delivery"]
    assert delivery["state"] == "ready"
    assert "package_files_without_checksum" in {warning["code"] for warning in delivery["warnings"]}
    assert delivery["package_check"]["sha256"] == ran["files"]
    assert delivery["package_check"]["without_checksum"] == sorted(ran["files"])


def test_its_approval_needs_the_warning_acknowledged(ran) -> None:
    assert ran["unacknowledged"]["status"] == 400, ran["unacknowledged"]
    assert ran["approved"]["status"] == 200, ran["approved"]


def test_the_import_records_each_files_sha256(ran) -> None:
    assert ran["job"]["status"] == "completed", ran["job"]
    assert ran["recorded"] and ran["recorded"].items() <= ran["files"].items()
