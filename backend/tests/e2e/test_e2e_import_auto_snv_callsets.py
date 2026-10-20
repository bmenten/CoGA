"""An `auto` SNV import reads the callset it stores, and the import-incomplete flag keeps
each callset that failed (SAFE-27, SAFE-26), E2E.

With ``source_format: auto`` (the default) the SNV importer's update check counted the
family's direct calls (clair3), and its clean-up after a failure deleted them, whatever the
family VCF held (SAFE-27): an update of imputed genotypes was skipped when the family held
direct calls, and a failed import of imputed genotypes, its overwrite without a backup to
put the family back from, removed the direct calls, which its flag did not name. And the
flag kept one scope per failed dataset (SAFE-26): a later failure of the SNV dataset for
another callset replaced the earlier one, so importing the later callset again cleared the
flag while the earlier callset was still missing.

Over one new one-sample family this runs real import jobs (``POST /api/family-imports``,
the worker's ``run_family_import_job``) into Postgres and ClickHouse, the SNV dataset's
``source_format`` left at auto throughout, and signs out after the later ones through
``POST /api/families/<id>/report/sign-out`` (the drift and QC gates acknowledged):

* 1 (cancel): 4 direct calls, stored as clair3;
* 2 (update): 4 imputed genotypes with their posteriors are imported, stored as glimpse2,
  beside the direct calls (before: skipped, as the family held direct calls);
* 3 (update): the same imputed genotypes are skipped, the family holding them, and the
  job's dataset says which callset it found;
* 4 (overwrite; the family's backup fails, and ClickHouse fails the write of the imputed
  genotypes): the import fails, the imputed genotypes are gone and the direct calls stay
  (before: deleted too); the flag names the SNV dataset for the imputed genotypes, with this
  job, and the sign-out is refused;
* 5 (overwrite; the backup and ClickHouse fail again, for the direct calls): the import
  fails, the direct calls gone too; the flag keeps both callsets, each with its job
  (before: the direct calls replaced the imputed genotypes);
* 6 (update): the direct calls imported again complete, but the family stays flagged for
  the imputed genotypes, naming job 4, its log says so, and the sign-out is refused
  (before: the flag was cleared, and the family signed out without its imputed genotypes);
* 7 (update): the imputed genotypes imported again complete the family, and the report
  signs out with no import acknowledgement.

The failures are injected: the backup by failing the import's Postgres snapshot (taken
first, so no ClickHouse backup is made), the write by failing the small-variant insert.
Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
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

_ACK = "e2e validation"
# The drift and Sample-QC gates come first; they are acknowledged in every attempt here.
_OTHER_GATES = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": _ACK,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": _ACK,
}
_VARIANTS = 4
_DIRECT = {"source": "clair3", "samples": None}
_IMPUTED = {"source": "glimpse2", "samples": None}


def _cap(resp) -> dict:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


def _write_package(root: Path, family_id: str, *, imputed: bool) -> Path:
    """A one-sample package whose SNV dataset leaves source_format at auto: a family VCF of
    direct calls or, with ``imputed``, of imputed genotypes with their posteriors, on
    positions of their own."""
    sample = f"{family_id}_S1"  # sample ids are unique across families
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    call = "GT:GP\t0|1:0.05,0.9,0.05" if imputed else "GT\t0/1"
    first = 50_000 if imputed else 100
    posteriors = '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Genotype posteriors">\n'
    (root / "snv").mkdir(exist_ok=True)
    (root / "snv" / "family.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        + (posteriors if imputed else "")
        + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        + "".join(f"1\t{first + 100 * i}\t.\tA\tG\t60\tPASS\t.\t{call}\n" for i in range(_VARIANTS)),
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\n"
        "datasets:\n  snv:\n    family_vcf: snv/family.vcf\n",
        encoding="utf-8",
    )
    return root


async def _collect(base: Path, mp: pytest.MonkeyPatch) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import variant_upload_service
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

    # What a step injects: the family's backup fails (an overwrite then has nothing to put
    # the family back from), and ClickHouse fails the write of the small variants.
    failing = {"backup": False, "write": False}
    real_snapshot = package_import.snapshot_family_postgres_state
    real_insert = variant_upload_service.insert_small_variant_records

    async def snapshot(*args, **kwargs):
        if failing["backup"]:
            raise RuntimeError("e2e: the family's backup could not be taken")
        return await real_snapshot(*args, **kwargs)

    async def insert(*args, **kwargs):
        if failing["write"]:
            raise RuntimeError("e2e: ClickHouse failed the write")
        return await real_insert(*args, **kwargs)

    mp.setattr(package_import, "snapshot_family_postgres_state", snapshot)
    mp.setattr(variant_upload_service, "insert_small_variant_records", insert)

    family_id = f"FAM_AUTO_{uuid4().hex[:8].upper()}"
    out: dict[str, Any] = {"family_id": family_id, "steps": {}}
    jobs: list[str] = []

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT status, error, logs, dataset_summaries FROM family_import_jobs "
                        "WHERE id = CAST(:j AS uuid)"
                    ),
                    {"j": job_id},
                )
            ).mappings().one()
        summaries = row["dataset_summaries"] or []
        return {
            "status": row["status"],
            "error": row["error"],
            "logs": row["logs"] or [],
            "snv": next((item for item in summaries if item.get("dataset_type") == "snv"), None),
        }

    async def family() -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text("SELECT id::text AS uuid, metadata FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).mappings().one()
        counts = {
            source: await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id], source=source
            )
            for source in ("clair3", "glimpse2")
        }
        return {"flag": (row["metadata"] or {}).get("import_incomplete"), **counts}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def run_import(
                name: str,
                conflict_mode: str,
                *,
                imputed: bool,
                fails: bool = False,
                sign_out: bool = True,
            ) -> None:
                root = _write_package(base / name / family_id, family_id, imputed=imputed)
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
                worker = f"e2e-auto-{uuid4().hex[:8]}"
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
                failing["backup"] = failing["write"] = fails
                try:
                    await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
                finally:
                    failing["backup"] = failing["write"] = False
                step: dict[str, Any] = {"job_id": job_id, "job": await job(job_id), "family": await family()}
                if sign_out:
                    step["signout"] = _cap(
                        await ac.post(f"/api/families/{family_id}/report/sign-out", json=_OTHER_GATES)
                    )
                out["steps"][name] = step

            await run_import("1_direct", "cancel", imputed=False, sign_out=False)
            await run_import("2_imputed_update", "update", imputed=True, sign_out=False)
            await run_import("3_imputed_update_again", "update", imputed=True, sign_out=False)
            await run_import("4_imputed_fails", "overwrite", imputed=True, fails=True)
            await run_import("5_direct_fails", "overwrite", imputed=False, fails=True)
            await run_import("6_direct_again", "update", imputed=False)
            await run_import("7_imputed_again", "update", imputed=True)
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

    base = tmp_path_factory.mktemp("auto_snv_callsets")
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


def test_an_update_of_imputed_genotypes_imports_them_beside_the_direct_calls(snap) -> None:
    first, step = snap["steps"]["1_direct"], snap["steps"]["2_imputed_update"]
    assert first["job"]["status"] == "completed", first["job"]["error"]
    assert (first["family"]["clair3"], first["family"]["glimpse2"]) == (_VARIANTS, 0)
    # Before: skipped, as the family held direct calls; the imputed genotypes never came in.
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["job"]["snv"]["status"] == "imported", step["job"]["snv"]
    assert (step["family"]["clair3"], step["family"]["glimpse2"]) == (_VARIANTS, _VARIANTS)
    assert step["family"]["flag"] is None


def test_an_update_skips_imputed_genotypes_the_family_holds(snap) -> None:
    step = snap["steps"]["3_imputed_update_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["job"]["snv"]["status"] == "skipped"
    assert "small variants of the glimpse2 callset already exist" in step["job"]["snv"]["message"]
    assert (step["family"]["clair3"], step["family"]["glimpse2"]) == (_VARIANTS, _VARIANTS)


def test_a_failed_import_of_imputed_genotypes_leaves_the_direct_calls(snap) -> None:
    step = snap["steps"]["4_imputed_fails"]
    assert step["job"]["status"] == "failed"
    assert step["job"]["error"] == "Family package import failed for dataset(s): snv"
    # No backup to put the family back from: the overwrite replaced the imputed genotypes
    # and failed. Its clean-up removed them and nothing else (before: the direct calls too).
    assert (step["family"]["clair3"], step["family"]["glimpse2"]) == (_VARIANTS, 0)
    flag = step["family"]["flag"]
    assert flag["failed_datasets"] == ["snv"]
    assert flag["failed_scopes"] == {"snv": [{"scope": _IMPUTED, "job_id": step["job_id"]}]}
    assert _refused(step)["import_incomplete"]["failed_datasets"] == ["snv"]


def test_a_later_failure_for_the_other_callset_keeps_both(snap) -> None:
    imputed, step = snap["steps"]["4_imputed_fails"], snap["steps"]["5_direct_fails"]
    assert step["job"]["status"] == "failed"
    assert (step["family"]["clair3"], step["family"]["glimpse2"]) == (0, 0)
    flag = step["family"]["flag"]
    # Before: the direct calls' scope replaced the imputed genotypes'.
    assert flag["failed_scopes"] == {
        "snv": [
            {"scope": _IMPUTED, "job_id": imputed["job_id"]},
            {"scope": _DIRECT, "job_id": step["job_id"]},
        ]
    }
    assert flag["failed_jobs"] == {"snv": step["job_id"]}
    _refused(step)


def test_the_direct_calls_imported_again_leave_the_imputed_genotypes_flagged(snap) -> None:
    imputed, step = snap["steps"]["4_imputed_fails"], snap["steps"]["6_direct_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["job"]["snv"]["status"] == "imported"
    assert (step["family"]["clair3"], step["family"]["glimpse2"]) == (_VARIANTS, 0)
    # Before: the flag was cleared here, and this signed out without the imputed genotypes.
    flag = step["family"]["flag"]
    assert flag["failed_scopes"] == {"snv": [{"scope": _IMPUTED, "job_id": imputed["job_id"]}]}
    # The job that holds the error left, as the pages and the sign-out name it.
    assert (flag["failed_jobs"], flag["job_id"]) == ({"snv": imputed["job_id"]}, imputed["job_id"])
    note = next(line for line in step["job"]["logs"] if "stays flagged import-incomplete" in line)
    assert f"snv in import job {imputed['job_id']}" in note
    detail = _refused(step)
    assert detail["import_incomplete"]["failed_jobs"] == {"snv": imputed["job_id"]}
    assert f"recorded in import job {imputed['job_id']}" in detail["message"]


def test_the_imputed_genotypes_imported_again_complete_the_family(snap) -> None:
    step = snap["steps"]["7_imputed_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert (step["family"]["clair3"], step["family"]["glimpse2"]) == (_VARIANTS, _VARIANTS)
    assert step["family"]["flag"] is None
    resp = step["signout"]
    assert resp["status"] == 200, resp["text"]
    assert resp["json"]["snapshot"]["import_incomplete"] is None
