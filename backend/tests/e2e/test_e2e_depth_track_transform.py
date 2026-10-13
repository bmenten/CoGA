"""A depth track's record, and the track availability, say what its values are (TRACE-7), E2E.

The package import stores a HiFiCNV depth bigWig on the ``coverage`` track as log2(depth / the
sample's autosomal median), the scale of the coverage chart's axis. A file without autosomal
depth gives no median, and its depths are stored as read. Its track's record said
``log2_ratio_to_autosomal_median`` all the same, with a null median, and the viewers, which
assumed every coverage track a log2 ratio, drew the raw depths on that axis: clamped at its top
and coloured as a gain across the genome.

Here a trio's package (a family of its own, a new id each run) goes through the package import
into real Postgres and ClickHouse. The proband's depth file has autosomal depth (median 20); the
mother's has depth on chrX only, with zero bins on chr1; the father has a coverage BED instead,
whose values are stored as the file holds them. Then:

* the proband's depths are stored as log2 ratios, and her track's record names
  ``log2_ratio_to_autosomal_median`` with the median, as before;
* the mother's depths are stored as read, and her record names ``none``, with a null median;
* ``GET /families/{family_id}/track-availability`` serves each offered coverage track's recorded
  transform (``coverage_normalizations``), which the viewers read before drawing a track on
  their log2-ratio axis; the father's BED track, whose record names none, is served without one.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
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

# Each depth file's 1 kb bins per contig. The proband's autosomal median is 20, so her bins
# are stored as log2 ratios 0, -1 and +1. The mother's chr1 bins are zero (skipped, as gaps
# are) and the median leaves chrX out, so she has no autosomal median: her chrX depths are
# stored as read.
_DEPTH = {
    "PROBAND": {"chr1": [20.0, 10.0, 40.0]},
    "MOTHER": {"chr1": [0.0, 0.0], "chrX": [15.0, 30.0]},
}


def _ped(family_id: str) -> str:
    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    return (
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t2\t2\n"
    )


def _write_bigwig(path: Path, contigs: dict[str, list[float]]) -> None:
    """A bigWig of 1 kb bins on each contig, one per value."""
    import pyBigWig  # the importer's own reader, a pinned dependency

    handle = pyBigWig.open(str(path), "w")
    handle.addHeader([(contig, 100_000) for contig in contigs], maxZooms=0)
    for contig, bins in contigs.items():
        handle.addEntries(
            [contig] * len(bins),
            [index * 1000 for index in range(len(bins))],
            ends=[(index + 1) * 1000 for index in range(len(bins))],
            values=bins,
        )
    handle.close()


def _write_package(root: Path, family_id: str) -> None:
    """The trio's PED, a HiFiCNV VCF and depth bigWig for the proband and the mother, and a
    coverage BED for the father."""
    root.mkdir(parents=True)
    (root / "family.ped").write_text(_ped(family_id), encoding="utf-8")
    (root / "cnv").mkdir()
    (root / "coverage").mkdir()
    father = f"FATHER_{family_id}"
    (root / "coverage" / f"{father}.coverage.bed").write_text(
        "1\t0\t1000\t0.1\n1\t1000\t2000\t-0.2\n", encoding="utf-8"
    )
    entries = []
    for role, contigs in _DEPTH.items():
        sample = f"{role}_{family_id}"
        (root / "cnv" / f"{sample}.vcf").write_text(_CNV_VCF, encoding="utf-8")
        _write_bigwig(root / "cnv" / f"{sample}.depth.bw", contigs)
        entries.append(f"      {sample}: {{vcf: cnv/{sample}.vcf, depth_bigwig: cnv/{sample}.depth.bw}}")
    lines = [
        "schema_version: 1",
        f"family_id: {family_id}",
        "ped: family.ped",
        "datasets:",
        "  cnv:",
        "    per_sample:",
        *entries,
        "  coverage:",
        "    per_sample:",
        f"      {father}: {{bed: coverage/{father}.coverage.bed}}",
    ]
    (root / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _exercise(base: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_interval_tracks import fetch_interval_track_rows
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    root = base / family_id
    _write_package(root, family_id)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    out: dict = {
        "samples": {role: f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND")},
        "import": {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        },
    }

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
        # Each coverage track's record, without the time it was registered.
        out["records"] = {
            names[row["sample_uuid"]]: {
                key: value for key, value in row["metadata"].items() if key != "registered_at"
            }
            for row in (
                await session.execute(
                    text(
                        """
                        SELECT sample_id::text AS sample_uuid, metadata
                        FROM sample_interval_track_sources
                        WHERE family_id = CAST(:f AS uuid) AND track_type = 'coverage'
                        """
                    ),
                    {"f": family_uuid},
                )
            ).mappings()
        }

    # The stored coverage rows, per member: (chromosome, start, value).
    stored: dict[str, list[tuple[str, int, float]]] = {}
    for row in await fetch_interval_track_rows(
        _harness.ASSEMBLY, family_uuid=family_uuid, track_type="coverage", chromosomes=[]
    ):
        stored.setdefault(names[str(row["sample_uuid"])], []).append(
            (str(row["chr"]), int(row["start"]), float(row["value"]))
        )
    out["stored"] = {name: sorted(rows) for name, rows in stored.items()}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        response = await ac.get(f"/api/families/{family_id}/track-availability")
        out["availability"] = {"status": response.status_code, "json": response.json()}
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("depth_track_transform")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"DEPTH_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_the_depth_files_are_imported(run) -> None:
    assert run["import"]["completed"] is True, run["import"]
    for dataset in ("cnv", "coverage"):
        assert run["import"]["datasets"][dataset][0] == "imported", run["import"]["datasets"]


def test_a_depth_track_with_an_autosomal_median_is_stored_and_recorded_as_a_log2_ratio(run) -> None:
    proband = run["samples"]["PROBAND"]
    assert run["stored"][proband] == [("1", 0, 0.0), ("1", 1000, -1.0), ("1", 2000, 1.0)]
    assert run["records"][proband] == {
        "source": "hificnv",
        "filename": f"{proband}.depth.bw",
        "uploaded_from": "family_package",
        "format": "bigwig",
        "normalization": "log2_ratio_to_autosomal_median",
        "autosomal_median_depth": 20.0,
    }


def test_a_depth_track_without_an_autosomal_median_is_recorded_as_stored_as_read(run) -> None:
    mother = run["samples"]["MOTHER"]
    # Her chrX depths as the file holds them; the zero chr1 bins are not stored.
    assert run["stored"][mother] == [("X", 0, 15.0), ("X", 1000, 30.0)]
    # The record said log2_ratio_to_autosomal_median over these raw depths.
    assert run["records"][mother] == {
        "source": "hificnv",
        "filename": f"{mother}.depth.bw",
        "uploaded_from": "family_package",
        "format": "bigwig",
        "normalization": "none",
        "autosomal_median_depth": None,
    }


def test_the_track_availability_serves_each_coverage_track_s_recorded_transform(run) -> None:
    assert run["availability"]["status"] == 200, run["availability"]
    samples = run["availability"]["json"]["samples"]
    served = {
        name: (samples[name]["coverage_sources"], samples[name]["coverage_normalizations"])
        for name in run["samples"].values()
    }
    assert served == {
        run["samples"]["PROBAND"]: (["hificnv"], {"hificnv": ["log2_ratio_to_autosomal_median"]}),
        run["samples"]["MOTHER"]: (["hificnv"], {"hificnv": ["none"]}),
        # The BED's values are its file's: its record names no transform, and none is served.
        run["samples"]["FATHER"]: (["web"], {}),
    }
    assert "normalization" not in run["records"][run["samples"]["FATHER"]]
