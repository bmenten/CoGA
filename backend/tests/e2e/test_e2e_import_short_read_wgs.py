"""A short-read (Illumina) WGS family is delivered and imported as the laboratory's folder holds it (E2E, real Postgres and ClickHouse).

The folder holds the family's PED at the top, its joint GATK callset unannotated and
VEP-annotated (``<family>.GRCh38.annotated.vcf.gz``) and a folder per sample with its CRAM
(``backend/tests/_short_read_wgs_package.py``). Through the app over HTTP, as the e2e
administrator, in the e2e project's folder of a watched import folder:

- added by hand as an SR-WGS analysis, CoGA's package takes the annotated callset as the family's
  SNV callset, declared the primary callset of direct calls (its first record is physically
  phased, which the loader alone reads as imputed genotypes), and each sample's CRAM;
- approved, its import stores the three records as the primary callset with every sample's call,
  the stored popmax read from VEP's frequency under the annotation's own name for it
  (``MAX_AF_VEP_ALL_POPULATIONS``), ClinVar's classification from the ClinVar file, not VEP's
  cache, and records each sample's CRAM for the genome browser;
- the same folder with a minimal package file of its own (no members, no datasets) is a watched
  delivery whose package Discover completes the same way.

The values are synthetic: the families are new and uniquely named. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests._short_read_wgs_package import MAX_AF, members, write_short_read_wgs_folder
from backend.tests.e2e._harness import capture, ensure_postgres_schema
from backend.tests.e2e._import_jobs import claim_import_job, job_outcome
from backend.tests.e2e.test_e2e_import_watcher import write_package

pytestmark = pytest.mark.integration

# The annotated record whose frequencies and ClinVar classification are checked.
MISSENSE = "2-1000100-A-G"


async def _exercise(base: Path, run: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import execute_clickhouse, init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services import import_watcher
    from backend.app.services.clickhouse_variant_ids import _small_table_name
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
    family = f"E2E_SRWGS_{run}"
    watched = f"E2E_SRWGS_WATCH_{run}"
    folder = write_short_read_wgs_folder(base / project / family, family)
    watched_folder = write_short_read_wgs_folder(base / project / watched, watched)
    write_package(watched_folder, project=project, family_id=watched, full=False, analysis_type="short_read_wgs")

    out: dict[str, Any] = {"family": family, "watched": watched}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        client.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(client)}"
        out["added"] = capture(
            await client.post(
                "/api/family-imports/deliveries",
                json={"project_id": project_id, "folder_path": str(folder), "analysis_type": "short_read_wgs"},
            )
        )
        delivery_id = out["added"]["json"]["_id"]
        url = f"/api/family-imports/deliveries/{delivery_id}"
        out["package"] = capture(await client.get(f"{url}/package"))
        out["approved"] = capture(await client.post(f"{url}/approve", json={"revision": 0, "acknowledge_warnings": True}))
        assert out["approved"]["status"] == 200, out["approved"]
        job_id = out["approved"]["json"]["job"]["_id"]
        worker = f"e2e-srwgs-{uuid4().hex[:8]}"
        await claim_import_job(job_id, worker)
        await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
        out["job"] = await job_outcome(job_id)

        await import_watcher.run_watch_round(sessionmaker, [base.resolve()])
        listed = capture(await client.get("/api/family-imports/deliveries", params={"limit": 200}))
        [out["watched_delivery"]] = [item for item in listed["json"] if item["folder_name"] == watched]
        out["watched_package"] = capture(
            await client.get(f"/api/family-imports/deliveries/{out['watched_delivery']['_id']}/package")
        )

    async with sessionmaker() as session:
        family_uuid = (
            await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family})
        ).scalar_one()
        out["alignments"] = {
            row.sample_id: row.metadata
            for row in (
                await session.execute(
                    text("SELECT sample_id, metadata::text AS metadata FROM samples WHERE sample_id = ANY(:s)"),
                    {"s": list(members(family))},
                )
            ).all()
        }
    entries = _small_table_name(_harness.ASSEMBLY, "entries")
    out["entries"] = await execute_clickhouse(
        f"SELECT variantId, source, `calls.sampleId`, `calls.gt` FROM {entries} "
        "WHERE family_guid = %(family)s AND sign = 1 ORDER BY variantId",
        {"family": family_uuid},
    )
    index = _small_table_name(_harness.ASSEMBLY, "variants/annotation_index")
    out["annotation"] = await execute_clickhouse(
        f"SELECT max_gnomad_popmax_af, clinvar_terms FROM {index} WHERE variantId = %(variant)s",
        {"variant": MISSENSE},
    )
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_short_read_wgs")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(patch.undo)
    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(base, run))


def _expected_datasets(family: str) -> dict[str, Any]:
    return {
        "snv": {
            "source_format": "clair3",
            "family_vcf": f"{family}.GRCh38.annotated.vcf.gz",
            "index": f"{family}.GRCh38.annotated.vcf.gz.tbi",
        },
        "alignments": {
            "per_sample": {
                sample: {"file": f"{sample}/{sample}.cram", "index": f"{sample}/{sample}.cram.crai"}
                for sample in members(family)
            }
        },
    }


def _datasets(package: dict[str, Any]) -> dict[str, Any]:
    datasets = package["json"]["package"]["datasets"]
    return {
        name: {key: value for key, value in entry.items() if key != "enabled"}
        for name, entry in datasets.items()
        if name in ("snv", "alignments")
    }


def test_added_by_hand_its_package_takes_the_annotated_callset_and_the_crams(ran) -> None:
    added = ran["added"]
    assert added["status"] == 200, added
    assert (added["json"]["origin"], added["json"]["state"]) == ("manual", "ready")
    package = ran["package"]["json"]["package"]
    assert package["analysis_type"] == "short_read_wgs"
    assert sorted(member["sample_id"] for member in package["members"]) == sorted(members(ran["family"]))
    assert _datasets(ran["package"]) == _expected_datasets(ran["family"])
    assert set(package["datasets"]) == {"snv", "alignments"}


def test_its_import_stores_the_callset_as_the_primary_one_with_its_annotation(ran) -> None:
    assert ran["job"]["status"] == "completed", ran["job"]
    entries = ran["entries"]
    assert [variant_id for variant_id, *_rest in entries] == ["1-2000200-C-T", MISSENSE, "Y-2800415-T-C"]
    assert {source for _variant, source, *_rest in entries} == {"clair3"}
    proband, mother, father = members(ran["family"])
    calls = {variant_id: dict(zip(samples, gts)) for variant_id, _source, samples, gts in entries}
    assert calls[MISSENSE] == {proband: "0|1", mother: "0|1", father: "0/0"}
    assert calls["Y-2800415-T-C"][mother] == "."
    # Stored once per annotation set of the variant: every one reads the same.
    assert ran["annotation"]
    for popmax, clinvar_terms in ran["annotation"]:
        assert popmax == pytest.approx(MAX_AF)
        assert "benign" not in " ".join(clinvar_terms).lower()


def test_its_import_records_each_samples_cram(ran) -> None:
    for sample in members(ran["family"]):
        assert f"{sample}/{sample}.cram" in ran["alignments"][sample]


def test_with_a_minimal_package_file_it_is_a_watched_delivery_discover_completes(ran) -> None:
    delivery = ran["watched_delivery"]
    assert (delivery["origin"], delivery["state"]) == ("watched", "ready"), delivery
    assert {"package_members_proposed", "package_datasets_proposed"} <= {item["code"] for item in delivery["warnings"]}
    assert _datasets(ran["watched_package"]) == _expected_datasets(ran["watched"])
