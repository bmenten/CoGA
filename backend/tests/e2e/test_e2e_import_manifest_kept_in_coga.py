"""A delivered package in a folder CoGA cannot write imports with the manifest CoGA keeps (E2E, real Postgres and ClickHouse).

The test deployment reads its packages from a share mounted read-only. CoGA writes nothing into a
package folder: the import reads the manifest an approved delivery's package makes
(``coga_package_manifest.package_manifest``), which the job keeps with its SHA-256 and the import
reads instead of any file in the folder; the family's package record names it, and the family's
raw files hold a managed copy of it.

Through the app over HTTP (an in-process ``httpx.ASGITransport`` client on one event loop, see
test_e2e_api_contract.py), as the e2e administrator, for a one-sample package whose
``coga-package.json`` names its member and its SNV callset, in a watched folder made read-only:
the watcher's round offers it as a ready delivery; approved, its import is queued with the
manifest the delivery kept, and the worker runs it (``run_family_import_job``, which reads the
kept text from the job): the family and its variants are imported. The folder holds no manifest
afterwards.

The values are synthetic: the family is new and uniquely named, the job rows end completed.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, one_sample_package
from backend.tests.e2e.test_e2e_import_watcher import write_package

pytestmark = pytest.mark.integration

_VARIANTS = 3


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import import_watcher
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
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

    family_id = f"E2E_KEPT_MANIFEST_{run}"
    # In the folder of its project in the watched folder.
    package = one_sample_package(base / _harness.PROJECT_NAME / family_id, family_id, variants=_VARIANTS, datasets=("snv",))
    # No manifest in the folder, which CoGA cannot write: as a package on a read-only share.
    (package / "manifest.yaml").unlink()
    write_package(package, project=project, family_id=family_id)
    out: dict[str, Any] = {"family_id": family_id, "package": str(package)}
    jobs: list[str] = []
    os.chmod(package, 0o555)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        await import_watcher.run_watch_round(sessionmaker, [base.resolve()])
        async with AsyncClient(transport=transport, base_url="http://e2e") as client:
            headers = {"Authorization": f"Bearer {await _harness.login_admin_token(client)}"}
            listed = await client.get("/api/family-imports/deliveries", params={"limit": 200}, headers=headers)
            [delivery] = [item for item in listed.json() if item["family_id"] == family_id]
            out["delivery"] = delivery
            approved = await client.post(
                f"/api/family-imports/deliveries/{delivery['_id']}/approve",
                json={"revision": 0, "acknowledge_warnings": True},
                headers=headers,
            )
            out["approved"] = capture(approved)
            job_id = str(approved.json()["job"]["_id"])
            jobs.append(job_id)
            worker = f"e2e-kept-manifest-{uuid4().hex[:8]}"
            await claim_import_job(job_id, worker)
            await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
            out["job"] = capture(await client.get(f"/api/family-imports/{job_id}", headers=headers))
        async with sessionmaker() as session:
            out["kept"] = (
                await session.execute(
                    text("SELECT manifest_text, manifest_sha256 FROM family_import_jobs WHERE id = CAST(:j AS uuid)"),
                    {"j": job_id},
                )
            ).mappings().one()
            family = (
                await session.execute(
                    text("SELECT id::text AS uuid, metadata -> 'package_import' AS record FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).mappings().one()
            out["package_import"] = family["record"]
            out["raw_manifest"] = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            "SELECT scope, file_name, managed, sha256, file_size, storage_path, source "
                            "FROM raw_import_files WHERE family_id = CAST(:f AS uuid) AND dataset = 'manifest'"
                        ),
                        {"f": family["uuid"]},
                    )
                ).mappings().all()
            ]
        out["variants"] = await count_family_small_variants(
            _harness.ASSEMBLY, family["uuid"], project_ids=[project_id]
        )
        out["kept_copy"] = [Path(row["storage_path"]).read_text(encoding="utf-8") for row in out["raw_manifest"]]
        out["folder_manifest"] = sorted(path.name for path in package.glob("manifest.*"))
    finally:
        os.chmod(package, 0o755)
        async with sessionmaker() as session:
            await session.execute(
                text(
                    "UPDATE family_import_jobs SET status = 'failed', worker_id = NULL, "
                    "completed_at = now(), error = 'e2e: left unfinished' "
                    "WHERE id::text = ANY(:jobs) AND status IN ('queued', 'validating', 'running')"
                ),
                {"jobs": jobs},
            )
            await session.commit()
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_manifest_kept")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    # The managed copy of the kept manifest lands in this run's own data folder.
    patch.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("import_manifest_kept_data"))
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, run))


def test_the_read_only_package_is_a_ready_delivery(ran) -> None:
    assert ran["delivery"]["state"] == "ready", ran["delivery"]
    assert ran["approved"]["status"] == 200, ran["approved"]


def test_the_job_keeps_the_manifest_and_imports_the_family_with_it(ran) -> None:
    kept = ran["kept"]
    sha256 = hashlib.sha256(kept["manifest_text"].encode("utf-8")).hexdigest()
    assert kept["manifest_sha256"] == sha256
    job = ran["job"]["json"]
    assert job["status"] == "completed", (job["error"], job["validation_errors"])
    assert job["family_id"] == ran["family_id"]
    assert job["manifest_sha256"] == sha256
    assert job["metadata"]["manifest_source"] == "coga"
    assert ran["variants"] == _VARIANTS


def test_the_family_records_the_kept_manifest_and_holds_its_copy(ran) -> None:
    record = ran["package_import"]
    kept = ran["kept"]
    assert record["folder_path"] == str(Path(ran["package"]).resolve())
    assert "manifest_path" not in record and "ped_path" not in record
    assert (record["manifest_source"], record["manifest_sha256"]) == ("coga", kept["manifest_sha256"])
    content = kept["manifest_text"].encode("utf-8")
    assert [
        {key: row[key] for key in ("scope", "file_name", "managed", "sha256", "file_size", "source")}
        for row in ran["raw_manifest"]
    ] == [
        {
            "scope": "family",
            "file_name": "manifest.yaml",
            "managed": True,
            "sha256": kept["manifest_sha256"],
            "file_size": len(content),
            "source": "family_package",
        }
    ]
    assert ran["kept_copy"] == [kept["manifest_text"]]
    # CoGA wrote nothing into the folder.
    assert ran["folder_manifest"] == []
