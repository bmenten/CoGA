"""A monogenic NIPT pair's CRAMs reach the genome browser (E2E, real Postgres and ClickHouse).

The NIPT-M pipeline writes each parent's aligned reads beside its VCF, named after the sample
with the pipeline's tags after an underscore (``<sample>_consensus.minreads2.mapped.filtered.cram``
with its ``.crai``). Over the real API (an in-process ``httpx.ASGITransport`` client), with a
synthetic pair added to Deliveries by hand (the package CoGA makes from the folder with Discover,
as for a minimal package file of a watched folder):

* the package names the plasma's and the father's CRAM, each with its index, under
  ``datasets.alignments``, and the fetus none;
* approved and imported (the job run by the worker's ``run_family_import_job``), the genome
  browser's manifest lists both parents' reads under the pregnancy's NIPT
  (``monogenic_nipt:ZW1``), and serves each CRAM and its index as the folder holds them.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import capture
from backend.tests.e2e._import_jobs import acknowledged, claim_import_job, job_outcome

pytestmark = pytest.mark.integration

_WORKER = "e2e-nipt-alignments-worker"
_TAGS = "_consensus.minreads2.mapped.filtered"
_ANALYSIS = "monogenic_nipt:ZW1"


def _one_sample_vcf(sample: str, *, ad: str) -> str:
    """A one-sample Mutect2 VCF with one call (synthetic)."""
    return (
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr7>\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        f"chr7\t1000\t.\tA\tG\t.\tPASS\tTLOD=300\tGT:AD:DP\t0/1:{ad}:1000\n"
    )


def _pair(root: Path, family_id: str) -> dict[str, str]:
    """The pair's folder: the PED, each parent's VCF, and each parent's CRAM with its index
    (bytes of their own, so what is served can be told apart)."""
    father, plasma, fetus = (f"{role}_{family_id}" for role in ("FATHER", "CFDNA", "FETUS"))
    root.mkdir(parents=True)
    (root / f"{family_id}.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{plasma}\t0\t0\t2\t1\n"
        f"{family_id}\t{fetus}\t{father}\t{plasma}\t0\t1\n",
        encoding="utf-8",
    )
    for sample_id, ad in ((plasma, "900,100"), (father, "500,500")):
        with gzip.open(root / f"{sample_id}.mutect2.vcf.gz", "wt", encoding="utf-8") as handle:
            handle.write(_one_sample_vcf(sample_id, ad=ad))
        (root / f"{sample_id}{_TAGS}.cram").write_text(f"CRAM of {sample_id}", encoding="utf-8")
        (root / f"{sample_id}{_TAGS}.cram.crai").write_text(f"index of {sample_id}", encoding="utf-8")
    return {"father": father, "plasma": plasma, "fetus": fetus}


async def _collect(base: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await _harness.ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    family_id = f"NIPTCRAM{uuid4().hex[:8].upper()}"
    samples = _pair(base / family_id, family_id)
    out: dict[str, Any] = {"family_id": family_id, **samples}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        out["added"] = added = capture(
            await ac.post(
                "/api/family-imports/deliveries",
                json={
                    "folder_path": str(base / family_id),
                    "project_id": project_id,
                    "analysis_type": "monogenic_nipt",
                    "pregnancy_id": "ZW1",
                },
            )
        )
        assert added["status"] == 200, added
        delivery = f"/api/family-imports/deliveries/{added['json']['_id']}"
        out["package"] = capture(await ac.get(f"{delivery}/package"))
        approved = capture(await ac.post(f"{delivery}/approve", json=await acknowledged(added["json"]["_id"])))
        assert approved["status"] == 200, approved
        job_id = str(approved["json"]["job"]["_id"])
        await claim_import_job(job_id, _WORKER)
        await family_package_import.run_family_import_job(job_id=job_id, worker_id=_WORKER)
        out["job"] = await job_outcome(job_id)

        manifest = await ac.get(
            f"/api/cram/{family_id}/manifest",
            params=[("sample", samples["plasma"]), ("sample", samples["father"]), ("sample", samples["fetus"])],
        )
        out["manifest_status"] = manifest.status_code
        out["served"] = [
            (
                entry["sample_id"],
                entry["analysis_type"],
                (await ac.get(f"/api{entry['url']}")).text,
                (await ac.get(f"/api{entry['index_url']}")).text,
            )
            for entry in manifest.json()
        ]
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("nipt_pair_alignments")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    return _harness.run_async(lambda: _collect(base))


def test_the_package_names_each_parents_cram_with_its_index(ran) -> None:
    datasets = ran["package"]["json"]["package"]["datasets"]
    alignments = {key: value for key, value in datasets["alignments"].items() if key != "enabled"}
    assert alignments == {
        "per_sample": {
            sample: {"file": f"{sample}{_TAGS}.cram", "index": f"{sample}{_TAGS}.cram.crai"}
            for sample in (ran["plasma"], ran["father"])
        }
    }


def test_the_genome_browser_reads_both_parents_crams_under_the_pregnancys_nipt(ran) -> None:
    assert ran["job"]["status"] == "completed", ran["job"]
    assert ran["manifest_status"] == 200
    # The fetus has no reads of its own: the manifest lists the two parents only.
    assert sorted(ran["served"]) == sorted(
        (sample, _ANALYSIS, f"CRAM of {sample}", f"index of {sample}") for sample in (ran["plasma"], ran["father"])
    )
