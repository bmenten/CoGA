"""An import imports nothing of a package that changed since it was checked (E2E, real Postgres and ClickHouse).

An import is validated, queued and run at different moments, and the folder it reads is a
pipeline's output on a share other hosts write. The package fingerprint (``package_fingerprint``,
over each file's path, size and modification time and the manifest CoGA keeps) is taken before
the validation reads the folder: a dry run reports it, the job keeps the one it was queued with,
and the family's package record the one it was imported with.

As the e2e administrator, for a one-sample package, its jobs queued as an approved delivery
queues them and read over HTTP (an in-process ``httpx.ASGITransport`` client on one event loop,
see test_e2e_api_contract.py): validation reports the fingerprint; a dry run keeps it and the
worker finds the folder as it was. A file is then added to the folder, and an import is queued
with the folder's fingerprint as it is then; the pipeline rewrites a file before the worker
starts the job, and the job ends failed having written nothing: no family. Queued again, the
import runs: the family's package record keeps the fingerprint, and the job says the folder did
not change while it was imported. (A delivery whose folder changed since it was checked is
refused at its approval: test_e2e_import_delivery_approval.py.)

The values are synthetic: the family is new and uniquely named, the job rows end completed or
failed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, one_sample_package, queue_import

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
    from backend.app.services.family_package_fingerprint import package_fingerprint
    from backend.app.services.family_package_validation import load_validated_family_package
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    family_id = f"E2E_PACKAGE_FINGERPRINT_{run}"
    package = one_sample_package(base / family_id, family_id, variants=_VARIANTS, datasets=("snv",))
    out: dict[str, Any] = {"family_id": family_id, "package": str(package)}
    jobs: list[str] = []

    def fingerprint() -> str:
        return package_fingerprint(package, kept_manifest_sha256=None).sha256

    async def run_job(client: AsyncClient, headers: dict[str, str], job_id: str) -> dict[str, Any]:
        worker = f"e2e-fingerprint-{uuid4().hex[:8]}"
        await claim_import_job(job_id, worker)
        await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
        return capture(await client.get(f"/api/family-imports/{job_id}", headers=headers))

    async def families() -> int:
        async with sessionmaker() as session:
            return (
                await session.execute(text("SELECT count(*) FROM families WHERE family_id = :f"), {"f": family_id})
            ).scalar_one()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as client:
            headers = {"Authorization": f"Bearer {await _harness.login_admin_token(client)}"}
            out["fingerprint_before"] = fingerprint()
            validation, _bundle = load_validated_family_package(package)
            out["validated"] = {"valid": validation.valid, "package_fingerprint": validation.package_fingerprint}

            jobs.append(await queue_import(package, project_id, dry_run=True))
            out["dry_run"] = await run_job(client, headers, jobs[-1])

            # The pipeline adds a file after the dry run; the import is queued as the folder is
            # now, and the pipeline rewrites a file before the job starts.
            (package / "snv" / "family.vcf.md5").write_text("0" * 32 + "  family.vcf\n", encoding="utf-8")
            out["fingerprint_queued"] = fingerprint()
            jobs.append(await queue_import(package, project_id))
            (package / "snv" / "family.vcf.md5").write_text("1" * 32 + "  family.vcf\n", encoding="utf-8")
            out["fingerprint_changed"] = fingerprint()
            out["changed"] = await run_job(client, headers, jobs[-1])
            out["families_after_changed"] = await families()

            # Queued again, as the folder is now: it imports.
            jobs.append(await queue_import(package, project_id))
            out["imported"] = await run_job(client, headers, jobs[-1])
        async with sessionmaker() as session:
            family = (
                await session.execute(
                    text("SELECT id::text AS uuid, metadata -> 'package_import' AS record FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).mappings().one()
            out["package_import"] = family["record"]
            out["stored"] = [
                tuple(row)
                for row in (
                    await session.execute(
                        text(
                            "SELECT id::text, package_fingerprint FROM family_import_jobs "
                            "WHERE id::text = ANY(:jobs)"
                        ),
                        {"jobs": jobs},
                    )
                ).all()
            ]
        out["variants"] = await count_family_small_variants(
            _harness.ASSEMBLY, family["uuid"], project_ids=[project_id]
        )
    finally:
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
    out["jobs"] = jobs
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_package_fingerprint")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, run))


def test_validation_and_the_dry_run_report_the_folders_fingerprint(ran) -> None:
    assert ran["validated"] == {"valid": True, "package_fingerprint": ran["fingerprint_before"]}
    dry_run = ran["dry_run"]["json"]
    assert dry_run["package_fingerprint"] == ran["fingerprint_before"]
    assert dry_run["status"] == "completed", (dry_run["error"], dry_run["validation_errors"])
    assert f"Package fingerprint {ran['fingerprint_before'][:12]}: the folder is as it was when the job was queued." in dry_run["logs"]


def test_a_job_whose_folder_changed_after_it_was_queued_writes_nothing(ran) -> None:
    from backend.app.services.family_package_import import PACKAGE_CHANGED_SINCE_QUEUED

    assert ran["fingerprint_queued"] != ran["fingerprint_before"]
    changed = ran["changed"]["json"]
    assert changed["package_fingerprint"] == ran["fingerprint_queued"]
    assert changed["status"] == "failed", changed
    assert [(issue["code"], issue["message"]) for issue in changed["validation_errors"]] == [
        ("package_changed_since_queued", PACKAGE_CHANGED_SINCE_QUEUED)
    ]
    assert (
        f"Package fingerprint {ran['fingerprint_changed'][:12]} differs from {ran['fingerprint_queued'][:12]}, "
        "taken when the job was queued."
    ) in changed["logs"]
    assert "Package validation failed; no data were imported." in changed["logs"]
    assert ran["families_after_changed"] == 0


def test_the_import_queued_again_imports_and_the_family_keeps_its_fingerprint(ran) -> None:
    imported = ran["imported"]["json"]
    assert imported["status"] == "completed", (imported["error"], imported["validation_errors"])
    assert imported["package_fingerprint"] == ran["fingerprint_changed"]
    assert (
        f"The package folder did not change while it was imported (fingerprint {ran['fingerprint_changed'][:12]})."
        in imported["logs"]
    )
    assert "package_changed_during_import" not in [issue["code"] for issue in imported["validation_warnings"]]
    assert ran["package_import"]["package_fingerprint"] == ran["fingerprint_changed"]
    assert ran["package_import"]["folder_path"] == str(Path(ran["package"]).resolve())
    assert ran["variants"] == _VARIANTS
    dry_run, changed, again = ran["jobs"]
    assert dict(ran["stored"]) == {
        dry_run: ran["fingerprint_before"],
        changed: ran["fingerprint_queued"],
        again: ran["fingerprint_changed"],
    }
