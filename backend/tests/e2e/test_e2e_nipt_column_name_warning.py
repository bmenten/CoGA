"""A NIPT pair's file whose one column is named after another tube is warned of, and still read
as its entry's sample (E2E, CLIN-13).

The import reads a monogenic NIPT pair's per-sample SNV file's one sample column as its
entry's sample, whatever the column is named: the laboratory maps its genetically verified
tubes to the files they are in, and a file, and its column, can be named after another tube.
Nothing said so, so a swapped file was read as the father's calls without a word. The owner
decided on 2026-10-28 to warn, not to refuse.

Over the real API (an in-process ``httpx.ASGITransport`` client, see test_e2e_api_contract.py),
Postgres and ClickHouse, with a synthetic pair: the plasma's file, and the father's, named
after him, whose one column is named after another tube (``TUBE9``):

* Discover drafts the NIPT manifest and warns (``nipt_vcf_column_named_otherwise``), naming the
  father, his file, the column and the ``vcf_sample`` that would silence it; writing the
  manifest returns the warning with its validation;
* a dry-run job and an import job (``POST /api/family-imports``, then the worker's
  ``run_family_import_job``) complete and store the warning with their validation warnings;
  the import stores the father's call as his, and the dataset's summary records the column
  each sample's calls were read from;
* the same pair whose manifest names the column in the father's entry
  (``vcf_sample: TUBE9``) is not warned of, by Discover nor by a dry run.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
import yaml

pytestmark = pytest.mark.integration

_WORKER = "e2e-nipt-column-worker"
_CODE = "nipt_vcf_column_named_otherwise"
_COLUMN = "TUBE9"


def _one_sample_vcf(column: str, *, ad: str) -> str:
    """A one-sample Mutect2 VCF with one call (synthetic)."""
    return (
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr7>\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
        f"chr7\t1000\t.\tA\tG\t.\tPASS\tTLOD=300\tGT:AD:DP\t0/1:{ad}:1000\n"
    )


def _pair(root: Path, family_id: str, *, manifest: str | None = None) -> Path:
    """The pair's folder: the PED, the plasma's VCF, and the father's VCF, named after him,
    whose one column is ``TUBE9``; a manifest when one is given."""
    father, plasma, fetus = (f"{role}_{family_id}" for role in ("FATHER", "CFDNA", "FETUS"))
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{plasma}\t0\t0\t2\t1\n"
        f"{family_id}\t{fetus}\t{father}\t{plasma}\t0\t2\n",
        encoding="utf-8",
    )
    for sample_id, column, ad in ((plasma, plasma, "900,100"), (father, _COLUMN, "500,500")):
        with gzip.open(root / f"{sample_id}.mutect2.vcf.gz", "wt", encoding="utf-8") as handle:
            handle.write(_one_sample_vcf(column, ad=ad))
    if manifest is not None:
        (root / "manifest.yaml").write_text(manifest, encoding="utf-8")
    return root


def _manifest_naming_the_column(family_id: str) -> str:
    """The pair's manifest, the father's entry naming his file's column (``vcf_sample``)."""
    father, plasma = f"FATHER_{family_id}", f"CFDNA_{family_id}"
    return (
        "schema_version: 1\n"
        f"family_id: {family_id}\n"
        "analysis_type: monogenic_nipt\n"
        "ped: family.ped\n"
        "samples:\n"
        f"  {plasma}: {{assay: nipt_cfdna}}\n"
        "datasets:\n"
        "  snv:\n"
        "    per_sample:\n"
        f"      {plasma}: {{vcf: {plasma}.mutect2.vcf.gz}}\n"
        f"      {father}: {{vcf: {father}.mutect2.vcf.gz, vcf_sample: {_COLUMN}}}\n"
    )


def _cap(resp: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # a 500 answers in text
        out["json"] = None
    return out


async def _job(job_id: str) -> dict[str, Any]:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, error, validation_errors, validation_warnings, dataset_summaries "
                    "FROM family_import_jobs WHERE id = CAST(:j AS uuid)"
                ),
                {"j": job_id},
            )
        ).mappings().one()
    return dict(row)


async def _run_job(ac: Any, folder: Path, project_id: str, *, dry_run: bool) -> dict[str, Any]:
    """Queue a job for ``folder`` as the page does, claim it as the worker does, run it."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import family_package_import

    resp = await ac.post(
        "/api/family-imports",
        json={"folder_path": str(folder), "project_id": project_id, "dry_run": dry_run, "conflict_mode": "cancel"},
    )
    assert resp.status_code == 200, resp.text
    job_id = str(resp.json()["_id"])
    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                "started_at = now(), heartbeat_at = now() WHERE id = CAST(:j AS uuid) AND status = 'queued'"
            ),
            {"j": job_id, "w": _WORKER},
        )
        await session.commit()
    try:
        await family_package_import.run_family_import_job(job_id=job_id, worker_id=_WORKER)
    except Exception as exc:  # noqa: BLE001 - the worker logs it and goes on
        return {**await _job(job_id), "raised": f"{type(exc).__name__}: {exc}"}
    return await _job(job_id)


async def _collect(base: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import execute_clickhouse, init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    suffix = uuid4().hex[:8].upper()
    warned_id, quiet_id = f"NIPTCOL{suffix}", f"NIPTNAMED{suffix}"
    warned = _pair(base / warned_id, warned_id)
    quiet = _pair(base / quiet_id, quiet_id, manifest=_manifest_naming_the_column(quiet_id))
    out: dict[str, Any] = {"father": f"FATHER_{warned_id}", "plasma": f"CFDNA_{warned_id}"}

    # A request the app fails on is answered 500, as a server answers it, so that every step
    # below is taken and asserted on its own.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        out["discover"] = _cap(
            await ac.post("/api/family-imports/manifest/discover", json={"folder_path": str(warned)})
        )
        drafted = (out["discover"]["json"] or {}).get("manifest_yaml") or ""
        out["write"] = _cap(
            await ac.post(
                "/api/family-imports/manifest/write",
                json={"folder_path": str(warned), "manifest_yaml": drafted, "overwrite": False},
            )
        )
        out["dry_run"] = await _run_job(ac, warned, project_id, dry_run=True)
        out["import"] = await _run_job(ac, warned, project_id, dry_run=False)

        out["named_discover"] = _cap(
            await ac.post("/api/family-imports/manifest/discover", json={"folder_path": str(quiet)})
        )
        out["named_dry_run"] = await _run_job(ac, quiet, project_id, dry_run=True)

    async with get_postgres_sessionmaker()() as session:
        family_uuid = (
            await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": warned_id})
        ).scalar_one_or_none()
    out["calls"] = []
    if family_uuid is not None:
        rows = await execute_clickhouse(
            "SELECT variantId, `calls.sampleId` "
            f"FROM {_small_table_name(_harness.ASSEMBLY, 'entries')} "
            "WHERE family_guid = %(family_guid)s AND source = 'nipt' AND sign = 1 "
            "ORDER BY variantId",
            {"family_guid": family_uuid},
        )
        out["calls"] = [sorted(str(sample) for sample in samples) for _variant_id, samples in rows]
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("nipt_column")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base))


def _column_warnings(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [issue for issue in issues if issue["code"] == _CODE]


def _message(snap: dict[str, Any]) -> str:
    father = snap["father"]
    return (
        f"The NIPT SNV file of {father} ({father}.mutect2.vcf.gz) names its one sample column 'TUBE9', "
        f"not {father}: the import reads that column as {father}'s calls. If the laboratory verified "
        f"that this file holds {father}'s tube, set the entry's vcf_sample to 'TUBE9'; otherwise "
        "correct the manifest"
    )


def test_discover_drafts_the_pair_and_warns_of_the_fathers_column(snap: dict[str, Any]) -> None:
    response = snap["discover"]
    assert response["status"] == 200, response["text"]
    body = response["json"]
    assert body["valid"], body["errors"]
    manifest = yaml.safe_load(body["manifest_yaml"])
    assert manifest["analysis_type"] == "monogenic_nipt"
    assert manifest["datasets"]["snv"]["per_sample"][snap["father"]] == {"vcf": f"{snap['father']}.mutect2.vcf.gz"}
    assert [(issue["dataset"], issue["sample_id"], issue["message"]) for issue in _column_warnings(body["warnings"])] == [
        ("snv", snap["father"], _message(snap))
    ]


def test_writing_the_manifest_returns_the_warning_with_its_validation(snap: dict[str, Any]) -> None:
    response = snap["write"]
    assert response["status"] == 200, response["text"]
    validation = response["json"]["validation"]
    assert validation["valid"], validation["errors"]
    assert [issue["message"] for issue in _column_warnings(validation["warnings"])] == [_message(snap)]


@pytest.mark.parametrize("run", ["dry_run", "import"])
def test_the_dry_run_and_the_import_complete_and_store_the_warning(snap: dict[str, Any], run: str) -> None:
    job = snap[run]
    assert "raised" not in job, job
    assert (job["status"], job["error"], job["validation_errors"]) == ("completed", None, []), job
    assert [(issue["sample_id"], issue["message"]) for issue in _column_warnings(job["validation_warnings"])] == [
        (snap["father"], _message(snap))
    ]


def test_the_import_reads_the_column_as_the_fathers_calls_and_records_it(snap: dict[str, Any]) -> None:
    snv = next(summary for summary in snap["import"]["dataset_summaries"] if summary["dataset_type"] == "snv")
    assert snv["status"] == "imported", snv
    father, plasma = snap["father"], snap["plasma"]
    assert {sample_id: snv["summary"][sample_id]["vcf_column"] for sample_id in (plasma, father)} == {
        plasma: plasma,
        father: "TUBE9",
    }
    # The one variant, one row holding both calls: the TUBE9 column's call stored as the father's.
    assert snap["calls"] == [sorted([father, plasma])]


def test_a_column_the_fathers_entry_names_is_not_warned_of(snap: dict[str, Any]) -> None:
    response = snap["named_discover"]
    assert response["status"] == 200, response["text"]
    assert response["json"]["valid"], response["json"]["errors"]
    assert _column_warnings(response["json"]["warnings"]) == []
    job = snap["named_dry_run"]
    assert "raised" not in job, job
    assert (job["status"], job["validation_errors"]) == ("completed", []), job
    assert _column_warnings(job["validation_warnings"]) == []
