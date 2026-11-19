"""A conventional NIPT's ccnv output through the package import, E2E.

A synthetic plasma's ccnv output (``_ccnv_output.py``: bins with one masked bin, three segments
with a chr21 gain, the calls VCF holding that gain, and the QC file) goes through the package
import into real Postgres and ClickHouse, as a family of its own (a new id each run). Then:

* the family is a conventional NIPT and its plasma is tagged as one;
* the bins are the plasma's ``coverage`` track and the segments its ``segments`` track, source
  ``ccnv``, each row with ccnv's values beside it, and the masked bin left out;
* the gain is a structural variant of the plasma, source ``ccnv``, read back by the family's
  structural-variant list as a gain;
* the QC file is kept with the plasma (``samples.metadata.ccnv``), and each of the four files is
  recorded with its SHA-256;
* ``GET /families/{family_id}/track-availability`` offers the ccnv coverage and segments, serves
  the coverage as a log2 ratio, and serves the plasma's fetal fraction (ccnv's effective one,
  beside the active one), fetal sex and sex-chromosome call, which the views draw the plasma's
  track with;
* a segments read serves each ccnv segment with ccnv's values (its call, expected ratio, implied
  fetal fraction, discordance, origin), which the views colour it by and show on hover.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from backend.tests._ccnv_output import qc_payload, write_ccnv_output
from backend.tests._kept_manifest import kept_manifest
from backend.tests.e2e._harness import ensure_postgres_schema

pytestmark = pytest.mark.integration


def _write_package(root: Path, family_id: str, sample: str) -> None:
    entry = write_ccnv_output(root, sample)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t2\t-9\trole=mother\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "ped": "family.ped",
        "analysis_type": "conventional_nipt",
        "samples": {sample: {"assay": "cnipt_cfdna"}},
        "datasets": {"ccnv": {"per_sample": {sample: entry}}},
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")


async def _exercise(base: Path, family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_interval_tracks import fetch_interval_track_rows
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    sample = f"CFDNA_{family_id}"  # sample ids are unique across families
    root = base / family_id
    _write_package(root, family_id, sample)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(root),
            manifest_text=kept_manifest(root),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    out: dict = {
        "sample": sample,
        "import": {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        },
    }

    async with sessionmaker() as session:
        family = (
            await session.execute(
                text("SELECT id::text AS id, metadata FROM families WHERE family_id = :f"), {"f": family_id}
            )
        ).mappings().one()
        out["family_metadata"] = family["metadata"]
        out["sample_metadata"] = (
            await session.execute(
                text("SELECT metadata FROM samples WHERE family_id = CAST(:f AS uuid) AND sample_id = :s"),
                {"f": family["id"], "s": sample},
            )
        ).scalar_one()
        out["raw_files"] = sorted(
            (str(row["dataset"]), Path(str(row["storage_path"])).name, len(str(row["sha256"] or "")))
            for row in (
                await session.execute(
                    text(
                        "SELECT dataset, storage_path, sha256 FROM raw_import_files "
                        "WHERE family_id = CAST(:f AS uuid) AND dataset = 'ccnv'"
                    ),
                    {"f": family["id"]},
                )
            ).mappings()
        )
    rows: dict[str, list[tuple[str, int, float, dict]]] = {}
    for track_type in ("coverage", "segments"):
        rows[track_type] = sorted(
            (str(row["chr"]), int(row["start"]), float(row["value"]), json.loads(row["metadata_json"]))
            for row in await fetch_interval_track_rows(
                _harness.ASSEMBLY,
                family_uuid=family["id"],
                track_type=track_type,
                chromosomes=[],
                source="ccnv",
                include_metadata=True,
            )
        )
    out["rows"] = rows

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        availability = await ac.get(f"/api/families/{family_id}/track-availability")
        out["availability"] = {"status": availability.status_code, "json": availability.json()}
        svs = await ac.get(f"/api/families/{family_id}/structural-variants", params={"source": "ccnv"})
        out["svs"] = {"status": svs.status_code, "json": svs.json()}
        segments = await ac.get(
            f"/api/bed/{sample}/segments/batch", params={"chrom": ["21", "X"], "source": "ccnv"}
        )
        out["segments_read"] = {"status": segments.status_code, "json": segments.json()}
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("ccnv_import")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"CNIPT_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_the_ccnv_output_is_imported_for_a_conventional_nipt(run) -> None:
    assert run["import"]["completed"] is True, run["import"]
    assert run["import"]["datasets"]["ccnv"][0] == "imported", run["import"]["datasets"]
    assert run["family_metadata"]["analysis_type"] == "conventional_nipt"
    assert run["sample_metadata"]["assay"] == "cnipt_cfdna"


def test_the_bins_and_segments_are_the_plasmas_log2_ratio_tracks_with_ccnvs_values(run) -> None:
    coverage, segments = run["rows"]["coverage"], run["rows"]["segments"]
    # Six bins: the masked one is not stored.
    assert [(chrom, start, value) for chrom, start, value, _kept in coverage] == [
        ("1", 100000, 0.01),
        ("1", 200000, -0.02),
        ("1", 300000, 0.005),
        ("21", 0, 0.09),
        ("21", 100000, 0.08),
        ("X", 0, 0.001),
    ]
    assert coverage[-1][3]["branch"] == "female_sex" and coverage[0][3]["zscore"] == 0.2
    gain = next(kept for chrom, _start, _value, kept in segments if chrom == "21")
    assert (gain["call"], gain["implied_ff"], gain["origin_call"], gain["discordant"]) == (
        "gain",
        0.118,
        "FETAL_CONSTITUTIONAL",
        False,
    )


def test_the_gain_is_a_structural_variant_of_the_plasma(run) -> None:
    assert run["svs"]["status"] == 200, run["svs"]
    (variant,) = run["svs"]["json"]["variants"]
    assert (variant["chr"], variant["start"], variant["end"], variant["type"], variant["source"]) == (
        "21",
        1,
        200000,
        "DUP",
        "ccnv",
    )
    assert variant["copy_change"] == "gain"
    # The plasma carries it, at no stated zygosity: the SV tracks draw it.
    assert [(call["sample"], call["gt"]) for call in variant["genotypes"]] == [(run["sample"], "./1")]


def test_the_qc_file_and_the_four_files_are_kept(run) -> None:
    sample = run["sample"]
    kept = run["sample_metadata"]["ccnv"]
    assert kept == {"qc": qc_payload(sample), "qc_file": f"{sample}_qc.json"}
    assert run["raw_files"] == sorted(
        ("ccnv", f"{sample}{suffix}", 64)
        for suffix in ("_bins.bed.gz", "_segments.bed.gz", "_calls.vcf.gz", "_qc.json")
    )


def test_the_viewers_are_offered_the_ccnv_tracks_as_log2_ratios(run) -> None:
    assert run["availability"]["status"] == 200, run["availability"]
    served = run["availability"]["json"]["samples"][run["sample"]]
    assert "ccnv" in served["coverage_sources"] and "ccnv" in served["segments_sources"]
    assert served["coverage_contents"]["ccnv"] == "log2_ratio"


def test_the_viewers_are_served_the_plasmas_fetal_fraction_and_calls(run) -> None:
    served = run["availability"]["json"]["samples"][run["sample"]]["ccnv"]
    # The expected lines are drawn at ccnv's effective fetal fraction; the others beside it.
    assert (served["fetal_fraction"], served["active_ff"], served["active_ff_source"]) == (0.121111, 0.081234, "FUSED")
    assert (served["fetal_sex"], served["sca_call"], served["low_ff_warning"]) == ("F", "XX", False)


def test_each_ccnv_segment_is_read_with_ccnvs_values(run) -> None:
    assert run["segments_read"]["status"] == 200, run["segments_read"]
    body = run["segments_read"]["json"]
    assert body["bed_type"] == "segments"
    by_chrom = {item["chr"]: item for item in body["items"]}
    assert by_chrom["21"]["details"] == {
        "zscore": 5.6,
        "weight": 282.0,
        "bin_count": 2,
        "call": "gain",
        "expected_ratio": 0.08482,
        "implied_ff": 0.118,
        "discordant": False,
        "frag_short_ratio": 0.205,
        "frag_llr_size": 120.5,
        "origin_call": "FETAL_CONSTITUTIONAL",
    }
    # A segment ccnv did not call has no call.
    assert "call" not in by_chrom["X"]["details"]
