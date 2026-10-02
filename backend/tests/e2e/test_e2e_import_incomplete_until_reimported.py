"""A family flagged import-incomplete stays flagged until what failed is imported again (E2E).

A package import that fails for some datasets keeps those that imported and flags the
family ``import_incomplete`` with what failed; sign-out refuses it unless acknowledged with
a reason (REQ-TRACE-013, TF-06 H16). Any later import that completed used to clear the flag,
whatever it imported, so the family read as complete while the failed dataset was still
missing; and a later failure's flag replaced an earlier one, forgetting what that one said
had failed.

Over one new family this runs real import jobs (``POST /api/family-imports``, the worker's
``run_family_import_job``) and signs out through ``POST /api/families/<id>/report/sign-out``:

* package 1 (cancel): its SNV callset fails (no readable record), its coverage imports: the
  family is flagged with the SNV callset failed, and the sign-out is refused;
* package 2 (update, coverage only) completes, but the SNV callset is still missing: the
  flag stays (before: cleared), the job says why, and the sign-out is still refused;
* package 3 (update, a Paraphase file that is not an object) fails: the flag names both the
  SNV callset and Paraphase, each with the job that holds its error (before: Paraphase only);
* package 4 (update, a readable SNV callset and Paraphase file) imports both: the flag goes,
  and the report signs out with no import acknowledgement.

Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py). The family is new and uniquely named; the job rows end completed
or failed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
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
_PARAPHASE = {
    "GBA": {
        "final_haplotypes": {"h1": "GBA_hap1"},
        "gene_cn": None,
        "genome_depth": 33.5,
        "highest_total_cn": 2,
        "phase_region": "38:chr1:10-20",
        "region_depth": {"median": 42},
        "sample_sex": "male",
        "total_cn": 2,
    }
}


def _cap(resp) -> dict:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


def _write_package(root: Path, family_id: str, **datasets: str) -> Path:
    """A one-sample package with the datasets given: ``snv`` ("good" or "unreadable"),
    ``coverage`` ("good"), ``paraphase`` ("good", or "not_an_object": valid JSON that is
    not the object the importer reads)."""
    sample = f"{family_id}_S1"  # sample ids are unique across families
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    manifest = [f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\ndatasets:\n"]
    if "snv" in datasets:
        (root / "snv").mkdir(exist_ok=True)
        # An unreadable POS on every record: it validates, and imports no variant.
        pos = ["NOT_A_NUMBER", "NOT_A_NUMBER"] if datasets["snv"] == "unreadable" else ["100", "200"]
        (root / "snv" / "family.vcf").write_text(
            "##fileformat=VCFv4.2\n"
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
            + "".join(f"1\t{p}\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n" for p in pos),
            encoding="utf-8",
        )
        manifest.append("  snv:\n    family_vcf: snv/family.vcf\n")
    if "coverage" in datasets:
        (root / "coverage").mkdir(exist_ok=True)
        (root / "coverage" / "S1.bed").write_text("1\t0\t1000\t31.0\n1\t1000\t2000\t29.5\n", encoding="utf-8")
        manifest.append(f"  coverage:\n    per_sample:\n      {sample}:\n        bed: coverage/S1.bed\n")
    if "paraphase" in datasets:
        (root / "paraphase").mkdir(exist_ok=True)
        payload: Any = _PARAPHASE if datasets["paraphase"] == "good" else [_PARAPHASE]
        (root / "paraphase" / "S1.json").write_text(json.dumps(payload), encoding="utf-8")
        manifest.append(f"  paraphase:\n    per_sample:\n      {sample}:\n        json: paraphase/S1.json\n")
    (root / "manifest.yaml").write_text("".join(manifest), encoding="utf-8")
    return root


async def _collect(base: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    family_id = f"FAM_FLAG_{uuid4().hex[:8].upper()}"
    out: dict[str, Any] = {"family_id": family_id, "steps": {}}
    jobs: list[str] = []

    async def flag() -> Any:
        async with sm() as s:
            return (
                await s.execute(
                    text("SELECT metadata -> 'import_incomplete' FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).scalar_one()

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text("SELECT status, error, logs FROM family_import_jobs WHERE id = CAST(:j AS uuid)"),
                    {"j": job_id},
                )
            ).mappings().one()
        return dict(row)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def run_import(name: str, conflict_mode: str, **datasets: str) -> str:
                root = _write_package(base / name / family_id, family_id, **datasets)
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
                worker = f"e2e-flag-{uuid4().hex[:8]}"
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
                step: dict[str, Any] = {"job_id": job_id, "job": await job(job_id), "flag": await flag()}
                step["signout"] = _cap(
                    await ac.post(f"/api/families/{family_id}/report/sign-out", json=_OTHER_GATES)
                )
                out["steps"][name] = step
                return job_id

            await run_import("1_partial", "cancel", snv="unreadable", coverage="good")
            await run_import("2_without_snv", "update", coverage="good")
            await run_import("3_another_failure", "update", paraphase="not_an_object")
            await run_import("4_reimported", "update", snv="good", paraphase="good")
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

    base = tmp_path_factory.mktemp("import_flag")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base))


def _refused(step: dict) -> dict:
    resp = step["signout"]
    assert resp["status"] == 409, resp["text"]
    detail = resp["json"]["detail"]
    assert detail["gate"] == "import_incomplete", detail
    return detail


def test_a_partly_failed_import_flags_what_failed(snap) -> None:
    step = snap["steps"]["1_partial"]
    assert step["job"]["status"] == "failed"
    assert step["flag"]["failed_datasets"] == ["snv"]
    assert step["flag"]["imported_datasets"] == ["coverage"]
    assert step["flag"]["failed_jobs"] == {"snv": step["job_id"]}
    assert _refused(step)["import_incomplete"]["failed_datasets"] == ["snv"]


def test_an_import_that_completes_without_the_failed_dataset_leaves_it_flagged(snap) -> None:
    first, step = snap["steps"]["1_partial"], snap["steps"]["2_without_snv"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    # Before: the flag was cleared here, the SNV callset still missing, and this signed out.
    assert step["flag"]["failed_datasets"] == ["snv"]
    assert step["flag"]["failed_jobs"] == {"snv": first["job_id"]}
    note = next(line for line in step["job"]["logs"] if "stays flagged import-incomplete" in line)
    assert f"snv in import job {first['job_id']}" in note
    assert _refused(step)["import_incomplete"]["failed_datasets"] == ["snv"]


def test_a_later_failure_keeps_the_earlier_one_with_its_job(snap) -> None:
    first, step = snap["steps"]["1_partial"], snap["steps"]["3_another_failure"]
    assert step["job"]["status"] == "failed"
    # Before: this failure's flag replaced the earlier one, and named Paraphase only.
    assert step["flag"]["failed_datasets"] == ["paraphase", "snv"]
    assert step["flag"]["failed_jobs"] == {"paraphase": step["job_id"], "snv": first["job_id"]}
    message = _refused(step)["message"]
    assert f"snv in import job {first['job_id']}" in message
    assert f"paraphase in import job {step['job_id']}" in message


def test_an_import_of_what_failed_completes_the_family(snap) -> None:
    step = snap["steps"]["4_reimported"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["flag"] is None
    resp = step["signout"]
    assert resp["status"] == 200, resp["text"]
    assert resp["json"]["snapshot"]["import_incomplete"] is None
