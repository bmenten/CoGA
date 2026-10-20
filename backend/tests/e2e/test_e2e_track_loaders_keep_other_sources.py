"""A track loader replaces only its own source's track of a sample (SAFE-22), E2E.

A sample can carry several callers' tracks of one type, each under its own source, and the
track viewers draw one track per caller. The coverage dataset's BED and the BED upload
(source ``web``) deleted, with overwrite, every source's track of their type, and the APCAD
import every source's APCAD: a package with WisecondorX and QDNAseq bins and a coverage BED
for one sample lost the bins, which import first, and a later import of coverage or APCAD
removed HiFiCNV's depth track and minor allele fraction. A BED upload with overwrite deleted
the tracks before it read the file, so an invalid file answered 400 with the rows gone while
Postgres still recorded them; and its 409 counted every source's records, so an upload was
refused for another caller's track.

Here one sample's family (a new id each run) goes through the package import and the BED
upload route into real Postgres and ClickHouse:

* package 1 (cancel): WisecondorX bins and segments, QDNAseq bins, a coverage BED, an APCAD
  file, and HiFiCNV's calls with its depth, copy-number and minor-allele-fraction files. Each
  caller's tracks are stored, rows and records (before: the WisecondorX and QDNAseq bins
  were gone);
* package 2 (overwrite): a new coverage BED and a new APCAD file replace the BED's coverage
  and the APCAD; HiFiCNV's depth and minor allele fraction and the other bins stay (before:
  gone);
* an invalid coverage BED uploaded with overwrite is refused (400) and every track stays as
  it was, rows and records (before: the coverage rows were gone and Postgres still recorded
  them);
* a segments BED uploaded without overwrite is stored beside the WisecondorX and HiFiCNV
  segments (before: refused, 409);
* a coverage BED uploaded with overwrite replaces the BED's coverage alone;
* the track availability lists every caller's track.

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

_CNV_VCF = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type of the SV.">\n'
    '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">\n'
    '##FORMAT=<ID=CN,Number=1,Type=Float,Description="Copy number">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
    "chr1\t1000001\t.\tN\t<DEL>\t57\tPASS\tSVTYPE=DEL;END=1050000\tGT:CN\t0/1:1\n"
)


def _bed(rows: int, *, value: float = 0.1, columns: int = 4) -> str:
    """``rows`` 1 kb records on chr1: a value per record, or (``columns`` 6) an APCAD record
    with its id, alternate-allele fraction and parent of origin."""
    if columns == 6:
        return "".join(f"1\t{i * 1000}\t{i * 1000 + 1}\trs{i}\t0.5\tpaternal\n" for i in range(rows))
    return "".join(f"1\t{i * 1000}\t{(i + 1) * 1000}\t{value}\n" for i in range(rows))


def _write_bigwig(path: Path, values: list[float]) -> None:
    """A bigWig of 1 kb bins on chr1, one per value."""
    import pyBigWig  # the importer's own reader, a pinned dependency

    handle = pyBigWig.open(str(path), "w")
    handle.addHeader([("chr1", 100_000)], maxZooms=0)
    handle.addEntries(
        ["chr1"] * len(values),
        [index * 1000 for index in range(len(values))],
        ends=[(index + 1) * 1000 for index in range(len(values))],
        values=values,
    )
    handle.close()


def _write(root: Path, name: str, text: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _package_1(root: Path, family_id: str, sample: str) -> Path:
    """Every caller's tracks of the sample: WisecondorX bins (3) and segments (1), QDNAseq bins
    (4), a coverage BED (2), an APCAD file (2), and HiFiCNV's depth (3 bins), copy number (1)
    and minor allele fraction (5)."""
    root.mkdir(parents=True)
    _write(root, "family.ped", f"{family_id}\t{sample}\t0\t0\t2\t2\n")
    wcx_bins = _write(root, f"wisecondorx/{sample}_bins.bed", _bed(3, value=-0.1))
    wcx_segments = _write(root, f"wisecondorx/{sample}_segments.bed", _bed(1, value=0.0))
    qdnaseq_bins = _write(root, f"qdnaseq/{sample}_bins.bed", _bed(4, value=0.2))
    coverage = _write(root, f"coverage/{sample}.bed", _bed(2))
    apcad = _write(root, f"apcad/{sample}.apcad.bed", _bed(2, columns=6))
    vcf = _write(root, f"cnv/{sample}.vcf", _CNV_VCF)
    copy_number = _write(root, f"cnv/{sample}.copynum.bedgraph", "chr1\t0\t3000\t2\n")
    _write_bigwig(root / "cnv" / f"{sample}.depth.bw", [20.0, 10.0, 40.0])
    _write_bigwig(root / "cnv" / f"{sample}.maf.bw", [0.1, 0.4, 0.25, 0.5, 0.3])
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  wisecondorx:",
        "    per_sample:",
        f"      {sample}: {{bins: {wcx_bins}, segments: {wcx_segments}}}",
        "  qdnaseq:",
        "    per_sample:",
        f"      {sample}: {{bins: {qdnaseq_bins}}}",
        "  coverage:",
        "    per_sample:",
        f"      {sample}: {{bed: {coverage}}}",
        "  apcad:",
        "    per_sample:",
        f"      {sample}: {{bed: {apcad}}}",
        "  cnv:",
        "    per_sample:",
        f"      {sample}: {{vcf: {vcf}, copy_number_bedgraph: {copy_number},"
        f" depth_bigwig: cnv/{sample}.depth.bw, maf_bigwig: cnv/{sample}.maf.bw}}",
    ]
    _write(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


def _package_2(root: Path, family_id: str, sample: str) -> Path:
    """A new coverage BED (5) and a new APCAD file (3) of the sample."""
    root.mkdir(parents=True)
    _write(root, "family.ped", f"{family_id}\t{sample}\t0\t0\t2\t2\n")
    coverage = _write(root, f"coverage/{sample}.bed", _bed(5, value=0.3))
    apcad = _write(root, f"apcad/{sample}.apcad.bed", _bed(3, columns=6))
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  coverage:",
        "    per_sample:",
        f"      {sample}: {{bed: {coverage}}}",
        "  apcad:",
        "    per_sample:",
        f"      {sample}: {{bed: {apcad}}}",
    ]
    _write(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_interval_tracks import (
        count_interval_track_rows,
        get_interval_track_sources_by_sample,
    )
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    sample = f"{family_id}_S1"  # sample ids are unique across families
    out: dict[str, Any] = {"sample": sample, "steps": {}}
    ids: dict[str, str] = {}

    async def run_import(name: str, root: Path, conflict_mode: str) -> dict[str, Any]:
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(root),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode=conflict_mode,
            )
        return {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        }

    async def tracks() -> dict[str, dict[tuple[str, str], int]]:
        """The sample's tracks by (track type, source): their rows in ClickHouse and the rows
        Postgres records of them."""
        if not ids:
            async with sessionmaker() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT s.id::text AS sample_uuid, s.family_id::text AS family_uuid "
                            "FROM samples s WHERE s.sample_id = :s"
                        ),
                        {"s": sample},
                    )
                ).mappings().one()
            ids.update(row)
        rows: dict[tuple[str, str], int] = {}
        for track_type in ("coverage", "segments", "apcad", "apcad_pcf"):
            sources = await get_interval_track_sources_by_sample(
                _harness.ASSEMBLY,
                family_uuid=ids["family_uuid"],
                sample_uuid_to_name={ids["sample_uuid"]: sample},
                track_type=track_type,
                chromosomes=[],
            )
            for source in sources.get(sample, []):
                rows[(track_type, source)] = await count_interval_track_rows(
                    _harness.ASSEMBLY,
                    family_uuid=ids["family_uuid"],
                    sample_uuid=ids["sample_uuid"],
                    track_type=track_type,
                    source=source,
                )
        async with sessionmaker() as session:
            records = {
                (str(r["track_type"]), str(r["source"])): int(r["rows"])
                for r in (
                    await session.execute(
                        text(
                            "SELECT track_type, source, SUM(row_count) AS rows "
                            "FROM sample_interval_track_sources "
                            "WHERE sample_id = CAST(:s AS uuid) GROUP BY track_type, source"
                        ),
                        {"s": ids["sample_uuid"]},
                    )
                ).mappings()
            }
        return {"rows": rows, "records": records}

    out["steps"]["package_1"] = {
        "import": await run_import("package_1", _package_1(base / "p1" / family_id, family_id, sample), "cancel"),
        **await tracks(),
    }
    out["steps"]["package_2"] = {
        "import": await run_import(
            "package_2", _package_2(base / "p2" / family_id, family_id, sample), "overwrite"
        ),
        **await tracks(),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

        async def upload(name: str, bed_type: str, body: bytes, *, overwrite: bool) -> None:
            # A coverage BED declares what it holds (TRACE-9): these hold log2 ratios.
            declared = {"content": "log2_ratio"} if bed_type == "coverage" else {}
            response = await ac.post(
                f"/api/bed/upload/{sample}/{bed_type}",
                params={"overwrite": str(overwrite).lower(), **declared},
                files={"file": (f"{name}.bed", body, "text/plain")},
            )
            out["steps"][name] = {
                "status": response.status_code,
                "json": response.json(),
                **await tracks(),
            }

        await upload("invalid_bed", "coverage", b"track name=coverage\nnot a record\n", overwrite=True)
        await upload("segments_bed", "segments", _bed(2, value=0.5).encode(), overwrite=False)
        await upload("coverage_bed", "coverage", _bed(1, value=0.4).encode(), overwrite=True)

        response = await ac.get(f"/api/families/{family_id}/track-availability")
        out["availability"] = {"status": response.status_code, "json": response.json()}
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("track_loaders")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"TRACKS_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


# Every caller's tracks after package 1: (track type, source) -> rows.
_PACKAGE_1 = {
    ("coverage", "web"): 2,
    ("coverage", "wisecondorx"): 3,
    ("coverage", "qdnaseq"): 4,
    ("coverage", "hificnv"): 3,
    ("segments", "wisecondorx"): 1,
    ("segments", "hificnv"): 1,
    ("apcad", "apcad"): 2,
    ("apcad", "hificnv"): 5,
}
# After package 2: the BED's coverage and the APCAD replaced.
_PACKAGE_2 = {**_PACKAGE_1, ("coverage", "web"): 5, ("apcad", "apcad"): 3}


def test_each_dataset_of_both_packages_is_imported(run) -> None:
    for name, datasets in (
        ("package_1", ("wisecondorx", "qdnaseq", "coverage", "apcad", "cnv")),
        ("package_2", ("coverage", "apcad")),
    ):
        imported = run["steps"][name]["import"]
        assert imported["completed"] is True, imported
        assert {dataset: imported["datasets"][dataset][0] for dataset in datasets} == {
            dataset: "imported" for dataset in datasets
        }, imported["datasets"]


def test_a_coverage_bed_keeps_the_bins_wisecondorx_and_qdnaseq_imported_before_it(run) -> None:
    step = run["steps"]["package_1"]
    # Before: the coverage BED deleted the WisecondorX and QDNAseq bins, rows and records.
    assert step["rows"] == _PACKAGE_1
    assert step["records"] == _PACKAGE_1


def test_coverage_and_apcad_imported_again_keep_hificnvs_tracks(run) -> None:
    step = run["steps"]["package_2"]
    # Before: HiFiCNV's depth went with the coverage, its minor allele fraction with the
    # APCAD, and every other caller's bins with them.
    assert step["rows"] == _PACKAGE_2
    assert step["records"] == _PACKAGE_2


def test_an_invalid_bed_upload_with_overwrite_leaves_every_track_and_its_record(run) -> None:
    step = run["steps"]["invalid_bed"]
    assert step["status"] == 400, step["json"]
    assert step["json"]["detail"] == "No valid BED records found"
    # Before: every coverage track's rows were deleted before the file was read, and Postgres
    # still recorded them.
    assert step["rows"] == _PACKAGE_2
    assert step["records"] == _PACKAGE_2


def test_a_bed_upload_is_not_refused_for_another_callers_track(run) -> None:
    step = run["steps"]["segments_bed"]
    # Before: 409, for the WisecondorX and HiFiCNV segments.
    assert (step["status"], step["json"]) == (200, {"inserted": 2})
    expected = {**_PACKAGE_2, ("segments", "web"): 2}
    assert step["rows"] == expected
    assert step["records"] == expected


def test_a_bed_upload_with_overwrite_replaces_only_the_track_a_bed_brought(run) -> None:
    step = run["steps"]["coverage_bed"]
    assert (step["status"], step["json"]) == (200, {"inserted": 1})
    expected = {**_PACKAGE_2, ("segments", "web"): 2, ("coverage", "web"): 1}
    assert step["rows"] == expected
    assert step["records"] == expected


def test_the_track_availability_lists_every_callers_track(run) -> None:
    assert run["availability"]["status"] == 200, run["availability"]
    served = run["availability"]["json"]["samples"][run["sample"]]
    assert (served["coverage_sources"], served["segments_sources"], served["apcad_sources"]) == (
        ["hificnv", "qdnaseq", "web", "wisecondorx"],
        ["hificnv", "web", "wisecondorx"],
        ["apcad", "hificnv"],
    )
