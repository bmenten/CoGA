"""An import whose package changed while it was read is undone, and its folder offered again (E2E).

The owner's decision of 2026-11-05: what an import read of a package that changed while it was
read may be partly the package as it was and partly as it is now, so the import is undone and the
package offered again. After each dataset the import takes the package folder's fingerprint
again; when it differs from the one its validation took, the import stops and is undone.

Through the app over HTTP (an in-process ``httpx.ASGITransport`` client on one event loop, see
test_e2e_api_contract.py) and the worker's own run (``run_family_import_job``), as the e2e
administrator, against real Postgres and ClickHouse, with the pipeline simulated by a file
written into the package folder once a chosen dataset has imported:

- a new family (SNV and coverage, with the manifest CoGA keeps), the folder changed after its SNV
  dataset: the job ends failed with ``package_changed_during_import``, both datasets failed (the
  coverage never ran), the family is gone with its variants and the managed copy of its manifest,
  and the package list offers the folder again;
- an existing family updated with its coverage, the folder changed after the coverage imported:
  the job ends failed, the coverage rows and their record are gone again, the family keeps its
  SNVs, its package record is the one from before, and the list offers the folder again although
  a family was imported from it.

The values are synthetic: the families are new and uniquely named, the job rows end failed.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import one_sample_package, queue_import, run_import_job

pytestmark = pytest.mark.integration

_VARIANTS = 3


async def _exercise(base: Path, data_dir: Path, run: str, patch: pytest.MonkeyPatch) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_rows
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness
    from backend.tests.e2e._import_jobs import claim_import_job, job_outcome

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # The pipeline writes into the folder once the dataset named here has imported.
    change: dict[str, Any] = {"after": None, "folder": None, "family_uuid": None}
    real_import_dataset = package_import._import_dataset

    async def import_dataset(*args: Any, **kwargs: Any) -> Any:
        imported = await real_import_dataset(*args, **kwargs)
        change["family_uuid"] = kwargs["family_context"].family_uuid
        if change["after"] == kwargs["summary"].dataset_type:
            (change["folder"] / "late-output.txt").write_text("written while imported\n", encoding="utf-8")
        return imported

    patch.setattr(package_import, "_import_dataset", import_dataset)

    async def record_of(family_id: str) -> dict[str, Any] | None:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text("SELECT id::text AS uuid, metadata -> 'package_import' AS record FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).mappings().first()
        return dict(row) if row is not None else None

    async def track_sources(family_uuid: str) -> int:
        async with sessionmaker() as session:
            return (
                await session.execute(
                    text("SELECT count(*) FROM sample_interval_track_sources WHERE family_id = CAST(:f AS uuid)"),
                    {"f": family_uuid},
                )
            ).scalar_one()

    out: dict[str, Any] = {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"

        # A new family, imported with the manifest CoGA keeps: changed after its SNVs, before its
        # coverage.
        new_id = f"E2E_UNDO_NEW_{run}"
        new = one_sample_package(base / new_id, new_id, variants=_VARIANTS, datasets=("snv", "coverage"))
        manifest_yaml = (new / "manifest.yaml").read_text(encoding="utf-8")
        (new / "manifest.yaml").unlink()
        change.update(after="snv", folder=new)
        new_job_id = await queue_import(new, project_id, manifest_text=manifest_yaml)
        worker = f"e2e-undo-{uuid4().hex[:8]}"
        await claim_import_job(new_job_id, worker)
        await package_import.run_family_import_job(job_id=new_job_id, worker_id=worker)
        out["new_job"] = await job_outcome(new_job_id)
        out["new_job_record"] = capture(await client.get(f"/api/family-imports/{new_job_id}"))
        out["kept_copies_left"] = sorted(
            str(path.relative_to(data_dir)) for path in data_dir.rglob("*") if path.is_file()
        )
        out["new_family"] = await record_of(new_id)
        out["new_variants"] = await count_family_small_variants(
            _harness.ASSEMBLY, change["family_uuid"], project_ids=[project_id]
        )

        # An existing family with its SNVs, updated with its coverage: changed after the coverage.
        existing_id = f"E2E_UNDO_EXISTING_{run}"
        existing = one_sample_package(base / existing_id, existing_id, variants=_VARIANTS, datasets=("snv",))
        change.update(after=None, folder=existing)
        out["first_job"] = await run_import_job(existing, project_id, f"e2e-undo-{uuid4().hex[:8]}")
        out["record_before"] = await record_of(existing_id)
        one_sample_package(base / existing_id, existing_id, variants=_VARIANTS, datasets=("snv", "coverage"))
        change.update(after="coverage", folder=existing)
        job_id = await queue_import(existing, project_id, conflict_mode="update")
        worker = f"e2e-undo-{uuid4().hex[:8]}"
        await claim_import_job(job_id, worker)
        await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
        out["update_job"] = await job_outcome(job_id)
        out["update_job_record"] = capture(await client.get(f"/api/family-imports/{job_id}"))
        out["record_after"] = await record_of(existing_id)
        family_uuid = out["record_after"]["uuid"]
        out["existing_variants"] = await count_family_small_variants(
            _harness.ASSEMBLY, family_uuid, project_ids=[project_id]
        )
        out["coverage_rows"] = await count_interval_track_rows(
            _harness.ASSEMBLY, family_uuid=family_uuid, sample_uuid=None, track_type="coverage"
        )
        out["coverage_sources"] = await track_sources(family_uuid)

        out["listed"] = capture(await client.get("/api/family-imports/packages"))
    out["new"], out["existing"] = new_id, existing_id
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    from backend.app.services import raw_import_files_pg

    base = tmp_path_factory.mktemp("import_undone_when_package_changes")
    # The managed copy of a kept manifest lands in this run's own data folder.
    data_dir = tmp_path_factory.mktemp("import_undone_when_package_changes_data")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    patch.setattr(raw_import_files_pg, "DATA_DIR", data_dir)
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, data_dir, run, patch))


def test_a_new_family_whose_package_changed_while_it_was_imported_is_removed(ran) -> None:
    from backend.app.services.family_package_import import PACKAGE_CHANGED_DURING_IMPORT

    job = ran["new_job"]
    assert job["status"] == "failed", job
    assert job["error"] == PACKAGE_CHANGED_DURING_IMPORT
    assert "package_changed_during_import" in [issue["code"] for issue in job["validation_errors"]]
    assert ran["new_family"] is None
    assert ran["new_variants"] == 0
    # The managed copy of the manifest CoGA kept went with the family.
    assert ran["kept_copies_left"] == []
    datasets = {dataset["dataset_type"]: dataset["status"] for dataset in ran["new_job_record"]["json"]["datasets"]}
    assert (datasets["snv"], datasets["coverage"]) == ("failed", "failed")
    assert (
        "The package changed while it was imported; removed the newly-created family with everything "
        "the import wrote of it."
    ) in ran["new_job_record"]["json"]["logs"]


def test_every_dataset_of_the_undone_import_is_failed_and_the_log_says_why(ran) -> None:
    from backend.app.services.family_package_import import _UNDONE_DATASET_MESSAGE

    record = ran["update_job_record"]
    assert record["status"] == 200, record
    datasets = {dataset["dataset_type"]: dataset for dataset in record["json"]["datasets"]}
    # The SNVs were there: the update skipped them, and wrote nothing of them.
    assert datasets["snv"]["status"] == "skipped"
    assert (datasets["coverage"]["status"], datasets["coverage"]["message"]) == ("failed", _UNDONE_DATASET_MESSAGE)
    logs = record["json"]["logs"]
    assert any(line.startswith("The package folder changed while it was imported: its fingerprint is ") for line in logs)
    assert (
        "The package changed while it was imported; restored the family's data, and its package "
        "record, to their state before the import."
    ) in logs


def test_an_existing_family_is_put_back_as_it_was_before_the_update(ran) -> None:
    from backend.app.services.family_package_import import PACKAGE_CHANGED_DURING_IMPORT

    assert ran["first_job"]["status"] == "completed", ran["first_job"]
    job = ran["update_job"]
    assert (job["status"], job["error"]) == ("failed", PACKAGE_CHANGED_DURING_IMPORT)
    assert ran["existing_variants"] == _VARIANTS
    assert (ran["coverage_rows"], ran["coverage_sources"]) == (0, 0)
    assert ran["record_after"]["record"] == ran["record_before"]["record"]


def test_the_package_list_offers_both_folders_again(ran) -> None:
    listed = ran["listed"]
    assert listed["status"] == 200, listed
    by_name = {item["name"]: item for item in listed["json"]}
    assert (by_name[ran["new"]]["changed_during_import"], by_name[ran["new"]]["imported_families"]) == (True, [])
    existing = by_name[ran["existing"]]
    assert existing["changed_during_import"] is True
    assert [family["family_id"] for family in existing["imported_families"]] == [ran["existing"]]
