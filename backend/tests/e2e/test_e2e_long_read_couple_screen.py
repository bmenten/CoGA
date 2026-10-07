"""A long-read couple screened for carriership, from the pipeline's folder to the screen (E2E).

nf-core/lrsvar writes no PED and no joint callset for a couple: each partner has an
annotated SNV VCF (DeepVariant, VEP), a NeedlR SV VCF and a TRGT VCF of their own. Such a
package could not be imported: the scanner did not list it, Discover stopped without a
PED, and validation refused a per-sample SNV callset outside monogenic NIPT. Here it goes
through the front door end to end:

* Discover proposes the couple from the per-sample folders, each partner sexed by the
  karyotype TRGT ran with, and drafts the per-sample callsets; the import needs no PED;
* the two SNV files are one callset: a site both partners carry is one row with both
  calls, a partner's DeepVariant reference record is kept as their call where the other
  has a variant, and reference-only sites are left out;
* the couple's carrier screen keeps the gene both partners carry a variant in and the
  female partner's X-linked variant, and nothing else;
* sample-integrity QC reads the couple as a couple, sexes both partners, and says that
  the call set was made one sample at a time.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for
the API (see test_e2e_api_contract.py for why). All samples and values are synthetic.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import gzip
import json
import random
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_CSQ_FORMAT = "Allele|Consequence|IMPACT|SYMBOL|Gene|MAX_AF|CLIN_SIG"


def _header(column: str) -> str:
    return (
        "##fileformat=VCFv4.2\n"
        '##FILTER=<ID=PASS,Description="All filters passed">\n'
        '##FILTER=<ID=RefCall,Description="Genotyping model thinks this site is reference.">\n'
        '##FILTER=<ID=NoCall,Description="Site has depth=0 resulting in no call.">\n'
        "##DeepVariant_version=1.10.0\n"
        + "".join(f"##contig=<ID=chr{name}>\n" for name in [*range(1, 23), "X", "Y"])
        + f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {_CSQ_FORMAT}">\n'
        + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
    )


def _record(
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    gt: str,
    *,
    filt: str = "PASS",
    gene: str = "",
    impact: str = "MODIFIER",
    consequence: str = "intergenic_variant",
    max_af: str = "0.3",
) -> tuple[int, int, str]:
    csq = f"CSQ={alt}|{consequence}|{impact}|{gene}|{'ENSG_' + gene if gene else ''}|{max_af}|"
    order = 23 if chrom == "X" else int(chrom)
    return order, pos, f"chr{chrom}\t{pos}\t.\t{ref}\t{alt}\t30\t{filt}\t{csq}\tGT:GQ:DP:AD:VAF\t{gt}:30:30:15,15:0.5"


def _vcf(column: str, records: list[tuple[int, int, str]]) -> str:
    return _header(column) + "".join(f"{line}\n" for _order, _pos, line in sorted(records))


def _qc_background(rng: random.Random, *, female: bool) -> list[tuple[int, int, str]]:
    """Common variants enough for the QC to read: autosomal genotypes drawn independently
    (an unrelated couple), and chrX ones heterozygous only in the female partner."""
    records = []
    for index in range(1500):
        chrom = str(1 + index % 22)
        pos = 1_000_000 + index * 1_000
        gt = rng.choice(["0/1", "0/1", "1/1", None])
        if gt is not None:
            records.append(_record(chrom, pos, "A", "G", gt))
    for index in range(400):
        pos = 10_000_000 + index * 5_000
        gt = rng.choice(["0/1", "1/1"]) if female else "1/1"
        records.append(_record("X", pos, "C", "T", gt))
    return records


def _write_package(root: Path, mother: str, father: str) -> None:
    rng = random.Random(7)
    mother_snvs = [
        # GENE1: each partner carries a different rare missense variant -> at risk.
        _record("7", 117_500_100, "G", "A", "0/1", gene="GENE1", impact="MODERATE", consequence="missense_variant", max_af="0.001"),
        # GENE2: only the mother -> no finding.
        _record("2", 50_000_100, "C", "T", "0/1", gene="GENE2", impact="HIGH", consequence="stop_gained", max_af="0.0005"),
        # GENEX on chrX outside the PARs: the female partner's variant alone is a finding.
        _record("X", 31_000_100, "T", "C", "0/1", gene="GENEX", impact="MODERATE", consequence="missense_variant", max_af="0.0002"),
        # The father has a DeepVariant reference record here: kept as his call.
        _record("2", 50_000_500, "A", "C", "0/1", gene="GENE2", impact="MODERATE", consequence="missense_variant", max_af="0.0004"),
        # A reference record in both files: no variant, no row.
        _record("3", 60_000_000, "G", "T", "0/0", filt="RefCall"),
        *_qc_background(rng, female=True),
    ]
    father_snvs = [
        _record("7", 117_600_200, "C", "T", "0/1", gene="GENE1", impact="MODERATE", consequence="missense_variant", max_af="0.002"),
        _record("2", 50_000_500, "A", "C", "0/0", filt="RefCall"),
        _record("3", 60_000_000, "G", "T", "./.", filt="NoCall"),
        # GENEY on chrX: a male partner's hemizygous variant is no finding on its own.
        _record("X", 40_000_100, "A", "G", "1/1", gene="GENEY", impact="HIGH", consequence="stop_gained", max_af="0.0001"),
        *_qc_background(rng, female=False),
    ]
    for sample, karyotype, snvs in ((mother, "XX", mother_snvs), (father, "XY", father_snvs)):
        snv = root / "snv" / sample / "annotation" / f"{sample}_annot.vcf.gz"
        snv.parent.mkdir(parents=True)
        with gzip.open(snv, "wt") as handle:
            # The pipeline names the column after its input, not the sample.
            handle.write(_vcf(f"{sample}_3500_4000", snvs))
        (snv.parent / f"{snv.name}.tbi").write_text("index", encoding="utf-8")
        repeats = root / "repeats" / sample / f"{sample}_tr.vcf"
        repeats.parent.mkdir(parents=True)
        repeats.write_text(
            "##fileformat=VCFv4.2\n"
            f"##trgtCommand=trgt genotype --genome ref.fna --reads {sample}_sort.bam --repeats catalog.bed "
            f"--karyotype {karyotype} --threads 4 --output-prefix {sample}\n"
            f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}_sort\n",
            encoding="utf-8",
        )
        needlr = root / "sv" / sample / "needlr" / f"{sample}_sv_phased.needLR.4.0.vcf.gz"
        needlr.parent.mkdir(parents=True)
        with gzip.open(needlr, "wt") as handle:
            handle.write(
                "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
                f"chrX\t31500000\t.\tN\t<DEL>\t.\t.\tSVLEN=-5000;SVTYPE=DEL;End_Pos=31505000;"
                f"Query_ID={sample}_3500_4000_sv_phased;Genotype={'0/1' if karyotype == 'XX' else '1/1'};"
                "Alt_Reads=9;Genes=GENEX\n"
            )
        (needlr.parent / f"{needlr.name}.tbi").write_text("index", encoding="utf-8")
        paraphase = root / "paraphase" / sample / f"{sample}.paraphase.json"
        paraphase.parent.mkdir(parents=True)
        paraphase.write_text(json.dumps({"smn1": {"smn1_cn": 1, "smn2_cn": 2, "phase_region": "38:chr5:70917100-70961220"}}), encoding="utf-8")


async def _exercise(base: Path, suffix: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import execute_clickhouse, init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.schemas import FamilyPackageManifestBuildRequest
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.family_package_discovery import (
        discover_family_package_manifest,
        write_family_package_manifest,
    )
    from backend.app.services.family_package_source import scan_family_import_packages
    from backend.tests.e2e import _harness

    family_id = f"COUPLE_{suffix}"
    mother, father = f"MOTHER_{suffix}", f"FATHER_{suffix}"
    root = base / family_id
    _write_package(root, mother, father)
    out: dict = {"samples": {"mother": mother, "father": father}, "family_id": family_id}

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    out["listed"] = any(package["name"] == family_id for package in scan_family_import_packages())
    draft = discover_family_package_manifest(
        FamilyPackageManifestBuildRequest(folder_path=str(root), naming_scheme="standard_v1")
    )
    out["discover"] = {"valid": draft.valid, "errors": [issue.message for issue in draft.errors], "ped_path": draft.ped_path}
    written = write_family_package_manifest(folder_path=root, manifest_yaml=draft.manifest_yaml, overwrite=False)
    out["validation_valid"] = written.validation.valid

    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    out["import"] = {
        "completed": result.completed,
        "error": result.error,
        "datasets": {
            dataset.dataset_type: {"status": dataset.status, "summary": dataset.summary}
            for dataset in result.datasets
            if dataset.enabled
        },
    }

    async with sessionmaker() as session:
        from sqlalchemy import text

        family_uuid = (
            await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()
    rows = await execute_clickhouse(
        f"SELECT variantId, `calls.sampleId`, `calls.gt`, `calls.filters` "
        f"FROM {_small_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(family_guid)s AND sign = 1 AND chrom IN ('2', '3', '7') AND pos >= 50000000 "
        "ORDER BY variantId",
        {"family_guid": family_uuid},
    )
    out["rows"] = {
        str(variant_id): {str(sample): (str(gt), list(filters)) for sample, gt, filters in zip(samples, gts, filters_)}
        for variant_id, samples, gts, filters_ in rows
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e", timeout=120) as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        family = await ac.get(f"/api/families/{family_id}")
        out["family"] = family.json() if family.status_code == 200 else {"status": family.status_code}
        screen = await ac.get(
            f"/api/families/{family_id}/small-variants",
            params=[
                ("expanded_carrier_screening", "true"),
                ("impact", "HIGH"),
                ("impact", "MODERATE"),
                ("max_gnomad_af", "0.01"),
                ("max_gnomad_popmax_af", "0.01"),
                ("clinvar_overrides_frequency", "true"),
                ("page_size", "100"),
            ],
        )
        out["screen"] = {"status": screen.status_code, "json": screen.json()}
        qc = await ac.get(f"/api/families/{family_id}/qc/sample-integrity")
        out["qc"] = {"status": qc.status_code, "json": qc.json()}
        svs = await ac.get(f"/api/families/{family_id}/structural-variants", params=[("page_size", "10")])
        out["svs"] = {"status": svs.status_code, "json": svs.json()}
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("long_read_couple")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, uuid4().hex[:8].upper()))
    finally:
        mp.undo()


def test_the_package_is_listed_and_discovered_without_a_ped(run) -> None:
    assert run["listed"] is True
    assert run["discover"] == {"valid": True, "errors": [], "ped_path": None}
    assert run["validation_valid"] is True


def test_the_import_completes_with_every_dataset(run) -> None:
    imported = run["import"]
    assert imported["completed"] is True, imported
    statuses = {name: dataset["status"] for name, dataset in imported["datasets"].items()}
    assert statuses == {"snv": "imported", "sv_needlr": "imported", "repeats_trgt": "imported", "paraphase": "imported"}


def test_the_couple_is_recorded_with_each_partners_sex(run) -> None:
    mother, father = run["samples"]["mother"], run["samples"]["father"]
    members = {member["sample_id"]: (member["sex"], member["role"]) for member in run["family"]["members"]}
    assert members == {mother: ("female", "mother"), father: ("male", "father")}
    couples = [
        {relationship["sample_id_a"], relationship["sample_id_b"]}
        for relationship in run["family"]["relationships"]
        if relationship["relationship_type"] == "couple"
    ]
    assert couples == [{mother, father}]


def test_the_two_files_are_one_callset(run) -> None:
    mother, father = run["samples"]["mother"], run["samples"]["father"]
    rows = run["rows"]
    assert rows == {
        "2-50000100-C-T": {mother: ("0/1", ["PASS"])},
        # The father's reference record is his call where the mother has a variant.
        "2-50000500-A-C": {father: ("0/0", ["RefCall"]), mother: ("0/1", ["PASS"])},
        "7-117500100-G-A": {mother: ("0/1", ["PASS"])},
        "7-117600200-C-T": {father: ("0/1", ["PASS"])},
    }
    snv = run["import"]["datasets"]["snv"]["summary"]
    assert snv["source_format"] == "clair3"
    # chr3:60000000 is reference in one file and uncalled in the other: left out.
    assert snv["skipped_filtered"] == 2
    assert snv["excluded_calls_kept"] == 1


def test_the_carrier_screen_keeps_the_shared_gene_and_the_female_partners_x_linked_variant(run) -> None:
    screen = run["screen"]
    assert screen["status"] == 200, screen["json"]
    found = sorted(
        f"{variant['chr']}-{variant['start']}-{variant['ref']}-{variant['alt']}" for variant in screen["json"]["variants"]
    )
    assert found == ["7-117500100-G-A", "7-117600200-C-T", "X-31000100-T-C"]
    assert not screen["json"].get("candidates_capped")


def test_qc_reads_a_couple_from_one_file_per_partner(run) -> None:
    qc = run["qc"]
    assert qc["status"] == 200, qc["json"]
    body = qc["json"]
    mother, father = run["samples"]["mother"], run["samples"]["father"]
    assert body["application"] == "couple"
    assert {check["sample_id"]: (check["inferred_sex"], check["status"]) for check in body["sex_checks"]} == {
        mother: ("female", "pass"),
        father: ("male", "pass"),
    }
    [relatedness] = body["relatedness_checks"]
    assert relatedness["inferred_relationship"] == "unrelated"
    assert any("made one sample at a time" in note for note in body["notes"])


def test_each_partners_needlr_calls_are_theirs(run) -> None:
    svs = run["svs"]
    assert svs["status"] == 200, svs["json"]
    mother, father = run["samples"]["mother"], run["samples"]["father"]
    [deletion] = svs["json"]["variants"]
    called = {genotype["sample"]: genotype["gt"] for genotype in deletion["genotypes"]}
    assert called == {mother: "0/1", father: "1/1"}
