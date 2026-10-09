"""A restore that fails part-way flags every dataset of its import as failed (E2E, SAFE-13).

An overwrite of an existing family that fails puts the family back from the backup it made
first, table by table: the family's rows deleted, then the backup's re-inserted. A restore
that fails part-way may already have deleted the family's rows of any dataset. Its
import-incomplete flag named only the dataset that had failed, so an import of that dataset
alone cleared the flag, and the family signed out without the incomplete-import gate while
its small variants were gone (TF-06 H16, REQ-TRACE-013). The owner's decision (2026-10-09):
the flag names every dataset of the import as failed, so the gate holds until an import has
imported each of them again.

Over one new one-sample family this runs real import jobs (``POST /api/family-imports``,
then the worker's ``run_family_import_job``) against Postgres and ClickHouse, and signs out
through ``POST /api/families/<id>/report/sign-out`` after each re-import:

* package 1 (cancel): 6 small variants and coverage: the family is complete;
* package 2 (overwrite): 4 other small variants, which replace the 6, and coverage, which
  fails; ClickHouse then fails the restore's re-insert of the small variants after their
  delete, so the family has none left. The flag names coverage and snv as failed and none as
  imported (before: coverage alone), the job's log says why, and the sign-out is refused;
* package 3 (overwrite, coverage alone) completes: the flag still names snv (before: it was
  cleared, and the family signed out with no small variants), the job's log says why, and
  the sign-out is still refused;
* package 4 (overwrite, the 6 small variants) completes: the flag goes, and the report signs
  out with no import acknowledgement.

The coverage failure and the restore's failure are injected (the import's dataset step, and
the restore's ``execute_clickhouse``); everything else runs as in production. Everything runs
in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py). The family is new and uniquely named; the job rows end completed
or failed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_VARIANTS = 6
_OVERWRITE_VARIANTS = 4
_ACK = "e2e validation"
# The drift and Sample-QC gates come first; they are acknowledged in every attempt here.
_OTHER_GATES = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": _ACK,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": _ACK,
}


def _cap(resp) -> dict:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


def _write_package(
    root: Path,
    family_id: str,
    *,
    variants: int | None = _VARIANTS,
    first_position: int = 100,
    coverage: bool = True,
) -> Path:
    """A one-sample package: a family VCF of ``variants`` SNVs (none: no SNV dataset) and,
    with ``coverage``, a coverage BED."""
    sample = f"{family_id}_S1"  # sample ids are unique across families
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    manifest = [f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\ndatasets:\n"]
    if variants is not None:
        (root / "snv").mkdir(exist_ok=True)
        (root / "snv" / "family.vcf").write_text(
            "##fileformat=VCFv4.2\n"
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
            + "".join(
                f"1\t{first_position + 100 * i}\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n"
                for i in range(variants)
            ),
            encoding="utf-8",
        )
        manifest.append("  snv:\n    family_vcf: snv/family.vcf\n")
    if coverage:
        (root / "coverage").mkdir(exist_ok=True)
        (root / "coverage" / "S1.bed").write_text(
            "1\t0\t1000\t31.0\n1\t1000\t2000\t29.5\n1\t2000\t3000\t30.2\n", encoding="utf-8"
        )
        manifest.append(f"  coverage:\n    per_sample:\n      {sample}:\n        bed: coverage/S1.bed\n")
    (root / "manifest.yaml").write_text("".join(manifest), encoding="utf-8")
    return root


async def _collect(base: Path, mp: pytest.MonkeyPatch) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import clickhouse_family_snapshot as snapshots
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_family_snapshot import list_import_backup_tables
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_source_rows
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # Package 2's failures: its coverage dataset fails before it writes anything, and
    # ClickHouse fails its restore's first re-insert from a backup, after that table's
    # delete has run.
    failing = {"coverage": False, "restore": False}
    reinserts_failed: list[str] = []
    real_import_dataset = package_import._import_dataset

    async def import_dataset(*args, **kwargs):
        if failing["coverage"] and kwargs["summary"].dataset_type == "coverage":
            raise RuntimeError("e2e: the coverage file could not be read")
        return await real_import_dataset(*args, **kwargs)

    real_execute = snapshots.execute_clickhouse

    async def execute(query: str, parameters: Any = None) -> Any:
        sql = " ".join(query.split())
        if (
            failing["restore"]
            and sql.startswith("INSERT INTO")
            and "/SNAPSHOT/" in sql.partition(" FROM ")[2]
        ):
            failing["restore"] = False  # once: the restore stops here
            reinserts_failed.append(sql.split()[2])
            raise RuntimeError("e2e: ClickHouse failed the restore's re-insert")
        return await real_execute(query, parameters)

    mp.setattr(package_import, "_import_dataset", import_dataset)
    mp.setattr(snapshots, "execute_clickhouse", execute)

    family_id = f"FAM_PARTWAY_{uuid4().hex[:8].upper()}"
    out: dict[str, Any] = {"family_id": family_id, "steps": {}, "reinserts_failed": reinserts_failed}
    jobs: list[str] = []

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT status, error, logs FROM family_import_jobs "
                        "WHERE id = CAST(:j AS uuid)"
                    ),
                    {"j": job_id},
                )
            ).mappings().one()
        return dict(row)

    async def family() -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text("SELECT id::text AS uuid, metadata FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).mappings().one()
            coverage = await count_interval_track_source_rows(
                s, family_uuid=row["uuid"], track_type="coverage"
            )
        metadata = row["metadata"] or {}
        return {
            "unfinished": metadata.get("import_unfinished"),
            "incomplete": metadata.get("import_incomplete"),
            "snvs": await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id]
            ),
            "coverage_rows": coverage,
        }

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def run_import(name: str, conflict_mode: str, **package: Any) -> dict[str, Any]:
                root = _write_package(base / name / family_id, family_id, **package)
                body: dict[str, Any] = {
                    "folder_path": str(root),
                    "project_id": project_id,
                    "dry_run": False,
                    "conflict_mode": conflict_mode,
                }
                if conflict_mode != "cancel":
                    body["family_id"] = family_id
                resp = await ac.post("/api/family-imports", json=body)
                assert resp.status_code == 200, resp.text
                job_id = str(resp.json()["_id"])
                jobs.append(job_id)
                worker = f"e2e-restore-{uuid4().hex[:8]}"
                async with sm() as s:
                    await s.execute(
                        text(
                            "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                            "started_at = now(), heartbeat_at = now() "
                            "WHERE id = CAST(:j AS uuid) AND status = 'queued'"
                        ),
                        {"j": job_id, "w": worker},
                    )
                    await s.commit()
                await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
                step: dict[str, Any] = {
                    "job_id": job_id,
                    "job": await job(job_id),
                    "family": await family(),
                    "backups": [table.name for table in await list_import_backup_tables(owner=job_id)],
                }
                out["steps"][name] = step
                return step

            async def sign_out(step: dict[str, Any]) -> None:
                step["signout"] = _cap(
                    await ac.post(f"/api/families/{family_id}/report/sign-out", json=_OTHER_GATES)
                )

            await run_import("1_complete", "cancel")

            failing.update(coverage=True, restore=True)
            try:
                step = await run_import(
                    "2_overwrite_restore_fails",
                    "overwrite",
                    variants=_OVERWRITE_VARIANTS,
                    first_position=10_000,
                )
            finally:
                failing.update(coverage=False, restore=False)
            await sign_out(step)

            await sign_out(await run_import("3_coverage_again", "overwrite", variants=None))
            await sign_out(await run_import("4_snv_again", "overwrite", coverage=False))
    finally:
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE family_import_jobs SET status = 'failed', worker_id = NULL, "
                    "completed_at = now(), error = 'e2e: left unfinished' "
                    "WHERE id::text = ANY(:jobs) AND status IN ('queued', 'validating', 'running')"
                ),
                {"jobs": jobs},
            )
            await s.commit()
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_restore_fails_part_way")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base, mp))


def _refused(step: dict) -> dict:
    resp = step["signout"]
    assert resp["status"] == 409, resp["text"]
    detail = resp["json"]["detail"]
    assert detail["gate"] == "import_incomplete", detail
    return detail


def test_the_family_was_complete_before_the_overwrite(snap) -> None:
    step = snap["steps"]["1_complete"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["family"]["snvs"] == _VARIANTS
    assert step["family"]["coverage_rows"] > 0
    assert step["family"]["incomplete"] is None


def test_the_restore_failed_part_way_and_removed_the_small_variants(snap) -> None:
    step = snap["steps"]["2_overwrite_restore_fails"]
    assert step["job"]["status"] == "failed"
    assert step["job"]["error"] == "Family package import failed for dataset(s): coverage"
    # The overwrite had replaced the 6 small variants with its 4; the restore deleted those
    # and failed before it put the 6 back.
    assert len(snap["reinserts_failed"]) == 1 and "SNV_INDEL/entries" in snap["reinserts_failed"][0]
    assert step["family"]["snvs"] == 0
    # Its backup is dropped all the same.
    assert step["backups"] == []


def test_its_flag_names_every_dataset_of_the_import_as_failed(snap) -> None:
    step = snap["steps"]["2_overwrite_restore_fails"]
    flag = step["family"]["incomplete"]
    # Before: coverage alone, so an import of coverage cleared the flag.
    assert flag["failed_datasets"] == ["coverage", "snv"]
    assert flag["imported_datasets"] == []
    assert flag["failed_jobs"] == {"coverage": step["job_id"], "snv": step["job_id"]}
    assert step["family"]["unfinished"] is None
    (line,) = [line for line in step["job"]["logs"] if "restore also failed" in line]
    assert "names every one of them as failed, those that imported too: coverage, snv" in line
    assert _refused(step)["import_incomplete"]["failed_datasets"] == ["coverage", "snv"]


def test_an_import_of_the_dataset_that_failed_alone_leaves_the_gate_shut(snap) -> None:
    flagged, step = snap["steps"]["2_overwrite_restore_fails"], snap["steps"]["3_coverage_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["family"]["coverage_rows"] > 0
    assert step["family"]["snvs"] == 0
    # Before: the flag was cleared here, and the family signed out with no small variants.
    flag = step["family"]["incomplete"]
    assert flag["failed_datasets"] == ["snv"]
    assert flag["failed_jobs"] == {"snv": flagged["job_id"]}
    note = next(line for line in step["job"]["logs"] if "stays flagged import-incomplete" in line)
    assert f"snv in import job {flagged['job_id']}" in note
    detail = _refused(step)
    assert detail["import_incomplete"]["failed_datasets"] == ["snv"]
    assert "failed for snv" in detail["message"]


def test_an_import_of_every_dataset_completes_the_family(snap) -> None:
    step = snap["steps"]["4_snv_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["family"]["snvs"] == _VARIANTS
    assert step["family"]["incomplete"] is None and step["family"]["unfinished"] is None
    resp = step["signout"]
    assert resp["status"] == 200, resp["text"]
    assert resp["json"]["snapshot"]["import_incomplete"] is None
