"""A package in a folder CoGA cannot write imports with the manifest CoGA keeps (E2E, real Postgres and ClickHouse).

The test deployment reads its packages from a share mounted read-only, as a second import root.
A package there loaded only when the pipeline itself wrote a CoGA manifest into the folder:
Write manifest could not add one, and validation and the import read the manifest from the
folder alone. The import request now takes the manifest (``manifest_yaml``), which the job
keeps with its SHA-256 and the import reads instead of a file in the folder; the family's
package record names it, and the family's raw files hold a managed copy of it.

Through the app over HTTP (an in-process ``httpx.ASGITransport`` client on one event loop, see
test_e2e_api_contract.py), as the e2e administrator, for a one-sample package without a
manifest, in a folder made read-only: Discover drafts the manifest; the draft, with the SNV
dataset added as a user edits it, cannot be written into the folder (409, saying to keep it in
CoGA instead, where the backend's user is refused the write); validation with it is valid and
names it; the import queued with it keeps it, and the worker runs it (``run_family_import_job``,
which reads the kept text from the job): the family and its variants are imported. The folder
holds no manifest afterwards. A request with a manifest that holds a NUL is refused (400)
before a job is written.

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
import yaml

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, one_sample_package

pytestmark = pytest.mark.integration

_VARIANTS = 3


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
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

    family_id = f"E2E_KEPT_MANIFEST_{run}"
    package = one_sample_package(base / family_id, family_id, variants=_VARIANTS, datasets=("snv",))
    # No manifest in the folder, which CoGA cannot write: as a package on a read-only share.
    (package / "manifest.yaml").unlink()
    out: dict[str, Any] = {"family_id": family_id, "package": str(package)}
    jobs: list[str] = []
    os.chmod(package, 0o555)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as client:
            headers = {"Authorization": f"Bearer {await _harness.login_admin_token(client)}"}
            discovered = await client.post(
                "/api/family-imports/manifest/discover",
                json={"folder_path": str(package)},
                headers=headers,
            )
            out["discovered"] = capture(discovered)
            draft = yaml.safe_load(discovered.json()["manifest_yaml"])
            # The user adds the callset Discover's naming scheme does not know.
            draft.setdefault("datasets", {})["snv"] = {"enabled": True, "family_vcf": "snv/family.vcf"}
            manifest_yaml = yaml.safe_dump(draft, sort_keys=False)
            out["manifest_sha256"] = hashlib.sha256(manifest_yaml.encode("utf-8")).hexdigest()

            out["written"] = capture(
                await client.post(
                    "/api/family-imports/manifest/write",
                    json={"folder_path": str(package), "manifest_yaml": manifest_yaml},
                    headers=headers,
                )
            )
            out["validated"] = capture(
                await client.post(
                    "/api/family-imports/validate",
                    json={"folder_path": str(package), "project_id": project_id, "manifest_yaml": manifest_yaml},
                    headers=headers,
                )
            )
            body = {
                "folder_path": str(package),
                "project_id": project_id,
                "dry_run": False,
                "conflict_mode": "cancel",
            }
            out["nul_refused"] = capture(
                await client.post(
                    "/api/family-imports", json={**body, "manifest_yaml": manifest_yaml + "# \x00\n"}, headers=headers
                )
            )
            queued = await client.post(
                "/api/family-imports", json={**body, "manifest_yaml": manifest_yaml}, headers=headers
            )
            out["queued"] = capture(queued)
            job_id = str(queued.json()["_id"])
            jobs.append(job_id)
            worker = f"e2e-kept-manifest-{uuid4().hex[:8]}"
            await claim_import_job(job_id, worker)
            await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
            out["job"] = capture(await client.get(f"/api/family-imports/{job_id}", headers=headers))
        async with sessionmaker() as session:
            out["jobs_of_the_folder"] = (
                await session.execute(
                    text("SELECT count(*) FROM family_import_jobs WHERE submitted_path = :p"),
                    {"p": str(package)},
                )
            ).scalar_one()
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
    out["manifest_yaml"] = manifest_yaml
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


def test_discover_drafts_the_manifest_of_a_folder_it_cannot_write(ran) -> None:
    discovered = ran["discovered"]
    assert discovered["status"] == 200, discovered
    assert discovered["json"]["family_id"] == ran["family_id"]
    assert discovered["json"]["manifest_yaml"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes a read-only folder")
def test_writing_the_manifest_into_the_folder_is_refused_saying_what_to_do(ran) -> None:
    # Before, the write failed with a 500.
    written = ran["written"]
    assert written["status"] == 409, written
    assert "kept in CoGA" in written["json"]["detail"]
    assert ran["folder_manifest"] == []


def test_validation_with_the_kept_manifest_is_valid_and_names_it(ran) -> None:
    validated = ran["validated"]
    assert validated["status"] == 200, validated
    report = validated["json"]
    assert report["valid"] is True, report["errors"]
    assert report["manifest_path"] is None
    assert report["metadata"]["manifest_source"] == "coga"
    assert report["metadata"]["manifest_sha256"] == ran["manifest_sha256"]


def test_a_manifest_with_a_nul_is_refused_before_a_job_is_written(ran) -> None:
    assert ran["nul_refused"]["status"] == 400, ran["nul_refused"]
    assert "NUL" in ran["nul_refused"]["json"]["detail"]
    # The one job of the folder is the import queued after it.
    assert ran["jobs_of_the_folder"] == 1


def test_the_job_keeps_the_manifest_and_imports_the_family_with_it(ran) -> None:
    queued = ran["queued"]
    assert queued["status"] == 200, queued
    assert queued["json"]["manifest_sha256"] == ran["manifest_sha256"]
    job = ran["job"]["json"]
    assert job["status"] == "completed", (job["error"], job["validation_errors"])
    assert job["family_id"] == ran["family_id"]
    assert job["manifest_sha256"] == ran["manifest_sha256"]
    assert job["metadata"]["manifest_source"] == "coga"
    assert dict(ran["kept"]) == {"manifest_text": ran["manifest_yaml"], "manifest_sha256": ran["manifest_sha256"]}
    assert ran["variants"] == _VARIANTS


def test_the_family_records_the_kept_manifest_and_holds_its_copy(ran) -> None:
    record = ran["package_import"]
    assert record["folder_path"] == str(Path(ran["package"]).resolve())
    assert record["manifest_path"] is None
    assert (record["manifest_source"], record["manifest_sha256"]) == ("coga", ran["manifest_sha256"])
    content = ran["manifest_yaml"].encode("utf-8")
    assert [
        {key: row[key] for key in ("scope", "file_name", "managed", "sha256", "file_size", "source")}
        for row in ran["raw_manifest"]
    ] == [
        {
            "scope": "family",
            "file_name": "manifest.yaml",
            "managed": True,
            "sha256": ran["manifest_sha256"],
            "file_size": len(content),
            "source": "family_package",
        }
    ]
    assert ran["kept_copy"] == [ran["manifest_yaml"]]
