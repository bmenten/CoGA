"""The mtDNA coverage and the NIPT coverage check read only a track of read depth (CLIN-46), E2E.

A sample's ``coverage`` track can come from several callers, each under its own source, and each
loader keeps the others' tracks (SAFE-22): a coverage BED (``web``), WisecondorX's and QDNAseq's
bins (log2 ratios) and HiFiCNV's depth, stored as a log2 ratio to the sample's autosomal median,
or as read when its file has no autosomal depth (its record names ``none``, TRACE-7). The mtDNA
coverage summary, with its ``mtdna.mean_depth`` verdict, and the NIPT coverage check without a
per-target table read every source's rows merged, as depth.

Here two packages (families of their own, new ids each run) go through the package import into
real Postgres and ClickHouse:

* a trio with chrM calls for every member. The proband has a coverage BED of chrM depths (120x),
  WisecondorX's and QDNAseq's bins and HiFiCNV's depth with autosomal depth (a log2 ratio); the
  mother WisecondorX's bins only; the father HiFiCNV's chrM depth without autosomal depth (stored
  as read, 60x and 80x) and QDNAseq's bins. With an ``mtdna.mean_depth`` limit configured for the
  family (warn below 60x, fail below 40x), ``GET /families/{id}/mitochondrial-dna`` reads the
  proband's BED, the father's HiFiCNV depths and the mother's calls' depth, and every sample
  passes (before: a blend of depths and log2 ratios for each, and each failed);
* a monogenic NIPT pair whose plasma has a coverage BED of 80x over 8 kb of the 10 kb region of
  interest, and WisecondorX's bins across it. ``GET /families/{id}/nipt/coverage`` reads the
  BED: an 80x median and 80% of the region covered, flagged as partial coverage (before: the
  bins' 0.1 as the median, flagged as low depth, and the region covered whole).

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

MT_LENGTH = 16_569

_CNV_VCF = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type of the SV.">\n'
    '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">\n'
    '##FORMAT=<ID=CN,Number=1,Type=Float,Description="Copy number">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
    "chr1\t1000001\t.\tN\t<DEL>\t57\tPASS\tSVTYPE=DEL;END=1050000\tGT:CN\t0/1:1\n"
)

_MITO_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##DeepVariant_version=1.10.0\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
    '##FORMAT=<ID=VAF,Number=A,Type=Float,Description="Variant allele fraction">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
)


def _mito_record(pos: int, ref: str, alt: str, gt: str, alt_depth: int) -> str:
    """A chrM call at 500 reads."""
    return (
        f"chrM\t{pos}\t.\t{ref}\t{alt}\t30\tPASS\t.\tGT:GQ:DP:AD:VAF\t"
        f"{gt}:30:500:{500 - alt_depth},{alt_depth}:{round(alt_depth / 500, 4)}\n"
    )


# Every member's chrM calls, each at 500 reads: the depth a member without a depth track has.
_MITO_CALLS = {
    "PROBAND": _mito_record(73, "A", "G", "1/1", 500) + _mito_record(3243, "A", "G", "0/1", 225),
    "MOTHER": _mito_record(73, "A", "G", "1/1", 500) + _mito_record(3243, "A", "G", "0/1", 75),
    "FATHER": _mito_record(73, "A", "G", "1/1", 500),
}


def _write(root: Path, name: str, text: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _write_bigwig(path: Path, contigs: dict[str, list[float]]) -> None:
    """A bigWig of 1 kb bins on each contig, one per value."""
    import pyBigWig  # the importer's own reader, a pinned dependency

    path.parent.mkdir(parents=True, exist_ok=True)
    lengths = {"chr1": 100_000, "chrM": MT_LENGTH}
    handle = pyBigWig.open(str(path), "w")
    handle.addHeader([(contig, lengths[contig]) for contig in contigs], maxZooms=0)
    for contig, bins in contigs.items():
        handle.addEntries(
            [contig] * len(bins),
            [index * 1000 for index in range(len(bins))],
            ends=[(index + 1) * 1000 for index in range(len(bins))],
            values=bins,
        )
    handle.close()


def _mito_package(root: Path, family_id: str) -> Path:
    """The trio: chrM calls for every member, and each member's coverage tracks (module doc)."""
    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    root.mkdir(parents=True)
    _write(
        root,
        "family.ped",
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t2\t2\n",
    )
    names = {"PROBAND": proband, "MOTHER": mother, "FATHER": father}
    mito = {
        role: _write(root, f"mito/{names[role]}.vcf", _MITO_HEADER + calls)
        for role, calls in _MITO_CALLS.items()
    }
    # One record over the whole of chrM per file: the BED's depth, the bins' log2 ratios.
    bed = _write(root, f"coverage/{proband}.bed", f"chrM\t0\t{MT_LENGTH}\t120\n")
    # WisecondorX's entries name their segments too (they land on the segments track).
    wcx = {
        role: (
            _write(root, f"wisecondorx/{names[role]}_bins.bed", f"chrM\t0\t{MT_LENGTH}\t0.1\n"),
            _write(root, f"wisecondorx/{names[role]}_segments.bed", f"chrM\t0\t{MT_LENGTH}\t0.1\n"),
        )
        for role in ("PROBAND", "MOTHER")
    }
    qdnaseq = {
        "PROBAND": _write(root, f"qdnaseq/{proband}_bins.bed", f"chrM\t0\t{MT_LENGTH}\t-0.1\n"),
        "FATHER": _write(root, f"qdnaseq/{father}_bins.bed", f"chrM\t0\t{MT_LENGTH}\t-0.2\n"),
    }
    # The proband's depth has autosomal depth (median 20): her chrM's 2000x is stored as
    # log2(100). The father's has chrM only: no autosomal median, so his depths are stored as read.
    _write_bigwig(
        root / "cnv" / f"{proband}.depth.bw", {"chr1": [20.0, 20.0, 20.0], "chrM": [2000.0] * 16}
    )
    _write_bigwig(root / "cnv" / f"{father}.depth.bw", {"chrM": [60.0, 80.0]})
    for sample in (proband, father):
        _write(root, f"cnv/{sample}.vcf", _CNV_VCF)
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  mito:",
        "    per_sample:",
        *(f"      {names[role]}: {{vcf: {path}}}" for role, path in mito.items()),
        "  wisecondorx:",
        "    per_sample:",
        *(
            f"      {names[role]}: {{bins: {bins}, segments: {segments}}}"
            for role, (bins, segments) in wcx.items()
        ),
        "  qdnaseq:",
        "    per_sample:",
        *(f"      {names[role]}: {{bins: {path}}}" for role, path in qdnaseq.items()),
        "  coverage:",
        "    per_sample:",
        f"      {proband}: {{bed: {bed}}}",
        "  cnv:",
        "    per_sample:",
        *(
            f"      {sample}: {{vcf: cnv/{sample}.vcf, depth_bigwig: cnv/{sample}.depth.bw}}"
            for sample in (proband, father)
        ),
    ]
    _write(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


def _nipt_package(root: Path, family_id: str) -> Path:
    """A monogenic NIPT pair: the plasma's coverage BED (80x over 1:10000-18000) and its
    WisecondorX bin across chr1:0-100000, with the region of interest 1:10000-20000."""
    father, plasma, fetus = (f"{role}_{family_id}" for role in ("FATHER", "CFDNA", "FETUS"))
    root.mkdir(parents=True)
    _write(
        root,
        "family.ped",
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{plasma}\t0\t0\t2\t1\n"
        f"{family_id}\t{fetus}\t{father}\t{plasma}\t0\t2\n",
    )
    bed = _write(root, f"coverage/{plasma}.bed", "1\t10000\t18000\t80\n")
    bins = _write(root, f"wisecondorx/{plasma}_bins.bed", "1\t0\t100000\t0.1\n")
    segments = _write(root, f"wisecondorx/{plasma}_segments.bed", "1\t0\t100000\t0.1\n")
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "analysis_type: monogenic_nipt",
        "ped: family.ped",
        "roi: 1:10000-20000",
        "samples:",
        f"  {plasma}:",
        "    assay: nipt_cfdna",
        "datasets:",
        "  wisecondorx:",
        "    per_sample:",
        f"      {plasma}: {{bins: {bins}, segments: {segments}}}",
        "  coverage:",
        "    per_sample:",
        f"      {plasma}: {{bed: {bed}}}",
    ]
    _write(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


async def _exercise(base: Path, mito_family: str, nipt_family: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_interval_tracks import get_interval_track_sources_by_sample
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    out: dict[str, Any] = {"imports": {}}
    for family_id, root in (
        (mito_family, _mito_package(base / mito_family, mito_family)),
        (nipt_family, _nipt_package(base / nipt_family, nipt_family)),
    ):
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(root),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode="cancel",
            )
        out["imports"][family_id] = {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        }
    if not all(item["completed"] for item in out["imports"].values()):
        return out  # the first test names the import that failed

    # What the store holds: each member's coverage sources with rows on chrM (or chr1, for the
    # plasma), and the transform each coverage track's record names.
    stored: dict[str, Any] = {}
    for family_id, chromosome in ((mito_family, "chrM"), (nipt_family, "1")):
        async with sessionmaker() as session:
            family_uuid = (
                await session.execute(text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id})
            ).scalar_one()
            names = {
                str(row["id"]): str(row["sample_id"])
                for row in (
                    await session.execute(
                        text("SELECT id::text AS id, sample_id FROM samples WHERE family_id = CAST(:f AS uuid)"),
                        {"f": family_uuid},
                    )
                ).mappings()
            }
            records = {
                (names[str(row["sample_uuid"])], str(row["source"])): row["normalization"]
                for row in (
                    await session.execute(
                        text(
                            """
                            SELECT sample_id::text AS sample_uuid, source,
                                   metadata ->> 'normalization' AS normalization
                            FROM sample_interval_track_sources
                            WHERE family_id = CAST(:f AS uuid) AND track_type = 'coverage'
                            """
                        ),
                        {"f": family_uuid},
                    )
                ).mappings()
            }
        stored[family_id] = {
            "sources": await get_interval_track_sources_by_sample(
                _harness.ASSEMBLY,
                family_uuid=family_uuid,
                sample_uuid_to_name=names,
                track_type="coverage",
                chromosomes=[chromosome],
            ),
            "records": records,
        }
    out["stored"] = stored

    profile = f"e2e_mtdna_{uuid4().hex[:8]}"
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        # The family's mtDNA depth limits: warn below 60x, fail below 40x.
        created = await ac.post(
            "/api/admin/qc-thresholds/profiles", json={"label": f"E2E mtDNA {profile}", "key": profile}
        )
        configured = await ac.put(
            f"/api/admin/qc-thresholds/{profile}",
            json={
                "metric_key": "mtdna.mean_depth",
                "warn_value": 60.0,
                "error_value": 40.0,
                "reason": "E2E: CLIN-46 coverage readers",
            },
        )
        out["profile"] = (created.status_code, configured.status_code)
        async with sessionmaker() as session:
            await session.execute(
                text(
                    "UPDATE families SET metadata = COALESCE(metadata, '{}'::jsonb) "
                    "|| jsonb_build_object('qc_profile', CAST(:p AS text)) WHERE family_id = :f"
                ),
                {"p": profile, "f": mito_family},
            )
            await session.commit()

        response = await ac.get(f"/api/families/{mito_family}/mitochondrial-dna")
        out["mtdna"] = {"status": response.status_code, "json": response.json()}
        response = await ac.get(f"/api/families/{mito_family}/mitochondrial-dna", params={"count_only": "true"})
        out["mtdna_count_only"] = {"status": response.status_code, "json": response.json()}
        response = await ac.get(f"/api/families/{nipt_family}/nipt/coverage")
        out["nipt_coverage"] = {"status": response.status_code, "json": response.json()}
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("coverage_readers")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    suffix = uuid4().hex[:8].upper()
    try:
        out = _harness.run_async(lambda: _exercise(base, f"MTDEPTH_{suffix}", f"NIPTDEPTH_{suffix}"))
    finally:
        mp.undo()
    out["mito_family"], out["nipt_family"] = f"MTDEPTH_{suffix}", f"NIPTDEPTH_{suffix}"
    return out


def test_both_packages_are_imported_with_every_callers_coverage(run) -> None:
    mito, nipt = run["mito_family"], run["nipt_family"]
    for family_id, datasets in (
        (mito, ("mito", "wisecondorx", "qdnaseq", "coverage", "cnv")),
        (nipt, ("wisecondorx", "coverage")),
    ):
        imported = run["imports"][family_id]
        assert imported["completed"] is True, imported
        assert {dataset: imported["datasets"][dataset][0] for dataset in datasets} == {
            dataset: "imported" for dataset in datasets
        }, imported["datasets"]
    # Every caller's track is stored beside the BED (SAFE-22), each with its recorded transform.
    assert run["stored"][mito]["sources"] == {
        f"PROBAND_{mito}": ["hificnv", "qdnaseq", "web", "wisecondorx"],
        f"MOTHER_{mito}": ["wisecondorx"],
        f"FATHER_{mito}": ["hificnv", "qdnaseq"],
    }
    assert run["stored"][mito]["records"] == {
        (f"PROBAND_{mito}", "web"): None,
        (f"PROBAND_{mito}", "wisecondorx"): None,
        (f"PROBAND_{mito}", "qdnaseq"): None,
        (f"PROBAND_{mito}", "hificnv"): "log2_ratio_to_autosomal_median",
        (f"MOTHER_{mito}", "wisecondorx"): None,
        (f"FATHER_{mito}", "qdnaseq"): None,
        (f"FATHER_{mito}", "hificnv"): "none",
    }
    assert run["stored"][nipt]["sources"] == {f"CFDNA_{nipt}": ["web", "wisecondorx"]}


def test_the_mtdna_coverage_reads_each_members_track_of_read_depth(run) -> None:
    mito = run["mito_family"]
    assert run["mtdna"]["status"] == 200, run["mtdna"]
    coverage = {sample["sample_id"]: sample["coverage"] for sample in run["mtdna"]["json"]["samples"]}
    summary = {
        name: (item["mean_depth"], item["min_depth"], item["max_depth"], item["source"])
        for name, item in coverage.items()
    }
    # Before: the proband's 120x BED blended with her bins and HiFiCNV's log2 ratio (about 32x,
    # minimum -0.1), the mother's bins as her depth (0.1x), the father's depths with his QDNAseq
    # bins (about 7x, minimum -0.2).
    assert summary[f"PROBAND_{mito}"] == (120.0, 120.0, 120.0, "coverage")
    assert coverage[f"PROBAND_{mito}"]["breadth"] == 1.0
    # The mother has no track of read depth: her calls' depth, as without a coverage track.
    assert summary[f"MOTHER_{mito}"] == (500.0, 500.0, 500.0, "variant calls")
    # The father's HiFiCNV depths stored as read: 60x over 1-1000 and 80x over 1000-2000.
    mean, minimum, maximum, _source = summary[f"FATHER_{mito}"]
    assert (round(mean, 2), minimum, maximum) == (70.0, 60.0, 80.0)
    assert round(coverage[f"FATHER_{mito}"]["breadth"], 3) == 0.121


def test_the_mtdna_depth_verdict_judges_each_members_depth(run) -> None:
    mito = run["mito_family"]
    assert run["profile"] == (201, 200), run["profile"]
    verdicts = {
        sample["sample_id"]: (sample["qc"]["status"], sample["qc"]["notes"])
        for sample in run["mtdna"]["json"]["samples"]
    }
    # Before: each failed, "Mean mtDNA depth is below the configured limit of 40.0x."
    assert verdicts == {
        f"PROBAND_{mito}": ("pass", []),
        f"MOTHER_{mito}": ("pass", []),
        f"FATHER_{mito}": ("pass", []),
    }
    assert run["mtdna_count_only"]["status"] == 200, run["mtdna_count_only"]
    assert run["mtdna_count_only"]["json"]["has_coverage"] is True


def test_the_nipt_coverage_check_reads_the_plasmas_coverage_bed(run) -> None:
    assert run["nipt_coverage"]["status"] == 200, run["nipt_coverage"]
    summary = run["nipt_coverage"]["json"]
    assert summary["targets"] is None  # no per-target table: the coverage track over the ROI
    # Before: the bin's 0.1 was the median, and its rows covered the BED's uncovered 2 kb.
    assert summary["overall_median_on_target"] == 80.0
    [region] = summary["per_region"]
    assert (region["chr"], region["start"], region["end"]) == ("1", 10000, 20000)
    assert (region["median_coverage"], region["covered_bases"], region["target_bases"]) == (80.0, 8000, 10000)
    [low] = summary["low_coverage_regions"]
    assert (low["reason"], low["covered_fraction"], low["median_coverage"]) == ("partial_coverage", 0.8, 80.0)
