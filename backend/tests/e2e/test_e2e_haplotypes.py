"""Optional C — end-to-end haplotype / lineage stage (real stack).

The golden trio keeps this stage on its own family for isolation. (Before #329 a
`haplotypes` family-VCF imported via the small-variant loader with overwrite=True
would also have clobbered its clair3 SNVs; imports are now scoped by callset
`source`, so the two coexist.) This imports a phased GLIMPSE2 trio, which the
loader ingests as small variants AND derives haplotype blocks from, then:

* assert haplotype interval-track rows are produced;
* run the genome-wide lineage precompute (the genome-overview cache) end-to-end;
* serve the family haplotypes endpoint and assert it returns phased segments.

Assertions are robust (blocks exist, precompute runs, endpoint serves segments)
rather than exact IBD colours — exact lineage colouring is unit-tested in
test_haplotype_lineage_service / test_bed_service_lineage_precompute.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_FORMAT = "GT:DP:AD:AF:GP:PS"


def _cell(gt: str) -> str:
    # phased GT + GP (forces/marks glimpse2) + a single phase set so each sample
    # forms a contiguous block.
    return f"{gt}:30:15,15:0.5:0.9,0.05,0.05:1"


def _write_hap_package(root: Path, family_id: str) -> tuple[str, str, str]:
    """Write a minimal package with a phased GLIMPSE2 trio (haplotypes dataset).
    Returns the (father, mother, proband) sample ids."""
    father, mother, proband = (f"{s}_{family_id}" for s in ("FATHER", "MOTHER", "PROBAND"))
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(
        "\n".join(
            [
                f"{family_id}\t{father}\t0\t0\t1\t1",
                f"{family_id}\t{mother}\t0\t0\t2\t1",
                f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        "schema_version: 1\n"
        f"family_id: {family_id}\n"
        "ped: family.ped\n"
        "datasets:\n"
        "  haplotypes:\n"
        "    family_vcf: haplotypes/family.glimpse2.vcf\n"
        "    source_format: glimpse2\n",
        encoding="utf-8",
    )
    header = [
        "##fileformat=VCFv4.2",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">',
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">',
        '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Allele frequency">',
        '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Genotype posteriors">',
        '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">',
        "#" + "\t".join(["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", father, mother, proband]),
    ]
    # 10 phased sites on chr1; parents alternately informative so the proband's
    # transmitted homologs are determinable.
    rows = []
    for i in range(10):
        pos = 1000 + i * 100
        f_gt = "0|1" if i % 2 == 0 else "0|0"
        m_gt = "0|0" if i % 2 == 0 else "0|1"
        p_gt = "0|1"
        rows.append(
            "\t".join(
                ["1", str(pos), ".", "A", "G", "60", "PASS", ".", _FORMAT, _cell(f_gt), _cell(m_gt), _cell(p_gt)]
            )
        )
    vcf = root / "haplotypes" / "family.glimpse2.vcf"
    vcf.parent.mkdir(parents=True, exist_ok=True)
    vcf.write_text("\n".join(header + rows) + "\n", encoding="utf-8")
    return father, mother, proband


async def _collect(root: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.bed_service import precompute_family_haplotype_lineage
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_source_rows
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.family_metadata_context import build_family_metadata_context
    from backend.tests.e2e import _harness
    from sqlalchemy import text

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)

    sm = get_postgres_sessionmaker()
    async with sm() as s:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(s)

    async with sm() as s:
        res = await package_import.execute_family_package_import(
            s, folder_path=str(root), project_id=project_id, dry_run=False, user=admin, conflict_mode="cancel"
        )
    out: dict = {
        "completed": res.completed,
        "error": res.error,
        "statuses": {d.dataset_type: d.status for d in res.datasets},
    }

    async with sm() as s:
        family_uuid = (
            await s.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one_or_none()
        out["family_uuid"] = family_uuid
        out["haplotype_rows"] = (
            await count_interval_track_source_rows(s, family_uuid=family_uuid, track_type="haplotype")
            if family_uuid
            else 0
        )
        # Genome-wide lineage precompute (the genome-overview cache).
        context = await build_family_metadata_context(
            session=s, family_identifier=family_id, user=admin, project_id=project_id
        )
        out["lineage_blocks"] = await precompute_family_haplotype_lineage(context)

    # Endpoint serves the phased segments.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        resp = await ac.get(f"/api/families/{family_id}/haplotypes", params={"chr": "1"})
        out["hap_status"] = resp.status_code
        out["hap_body"] = resp.json() if resp.status_code == 200 else resp.text
    return out


@pytest.fixture(scope="module")
def hap(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("e2e_hap")
    family_id = f"FAM_HAP_{uuid4().hex[:8]}"
    _write_hap_package(base / family_id, family_id)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(base / family_id, family_id))
    out["family_id"] = family_id
    return out


def test_haplotypes_dataset_imports(hap):
    assert hap["completed"] is True, hap
    assert hap["statuses"].get("haplotypes") == "imported", hap


def test_haplotype_blocks_ingested(hap):
    assert hap["family_uuid"] is not None
    assert hap["haplotype_rows"] > 0, hap["haplotype_rows"]


def test_lineage_precompute_runs(hap):
    # Returns the number of stored genome-wide lineage blocks; the trio has phased
    # genotypes, so the core lineage precompute produces blocks.
    assert isinstance(hap["lineage_blocks"], int), hap["lineage_blocks"]
    assert hap["lineage_blocks"] > 0, hap["lineage_blocks"]


def test_haplotypes_endpoint_serves_segments(hap):
    assert hap["hap_status"] == 200, hap["hap_body"]
    body = hap["hap_body"]
    assert body["chr"] == "1", body
    samples = {s["sample"] for s in body["samples"]}
    assert any(s.startswith("PROBAND_") for s in samples), samples
    # at least one sample carries phased segments
    assert any(s["segments"] for s in body["samples"]), body
