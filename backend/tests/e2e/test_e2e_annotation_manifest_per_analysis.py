"""Each analysis of one family keeps its own annotation manifest, E2E.

A family sequenced short-read and then long-read on the same DNA holds two analyses, each imported
from a package of its own and signed out on its own. Each import records the tool and database
versions of its own analysis (VEP, ClinVar, the caller, the repeat caller), and keeps the other
analysis's. Over real Postgres and ClickHouse, a new family each run:

1. a short-read WGS package (``analysis_type: short_read_wgs``): the trio's joint GATK callset,
   annotated with VEP 110 and a ClinVar release of 2024;
2. the short-read WGS is signed out (the family's only analysis: whole), and its check matches;
3. a long-read WGS package (``analysis_type: long_read_wgs``) of the same members, imported with
   update: a joint DeepVariant callset annotated with VEP 112 and a ClinVar release of 2026, and
   the proband's TRGT call.

Then:

* the short-read WGS's signed record keeps its own versions, and its check still matches: the
  long-read import changed neither its manifest nor its repeat and Paraphase sections, whose calls
  are the long-read WGS's;
* the long-read WGS's record carries the long-read versions, the TRGT version among them, and
  none of the short-read ones;
* the family's manifest lists each analysis's versions apart;
* the clinical audit event of each sign-out names its analysis.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

from backend.tests._kept_manifest import kept_manifest

pytestmark = pytest.mark.integration

_COLUMNS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{}\t{}\t{}\n"
# Each analysis's caller and annotation, as its VCF header states them.
_HEADERS = {
    "short_read_wgs": (
        '##GATKCommandLine=<ID=HaplotypeCaller,CommandLine="HaplotypeCaller -R ref.fa",Version="4.5.0.0">\n'
        '##VEP="v110" cache="homo_sapiens/110_GRCh38" ClinVar="20240107"\n'
    ),
    "long_read_wgs": (
        "##DeepVariant_version=1.6.1\n"
        '##VEP="v112" cache="homo_sapiens/112_GRCh38" ClinVar="20260105"\n'
    ),
}
# Positions no other module's family holds: the e2e project's cohort counts (the Variant
# Explorer's) read every family's calls.
_RECORDS = {
    "short_read_wgs": [(71234, "A", "G"), (72345, "C", "T")],
    "long_read_wgs": [(71234, "A", "G"), (73456, "G", "A")],
}
# The proband's TRGT call at FMR1, in the normal range: a call no gate holds back.
_TRGT = (
    "##fileformat=VCFv4.2\n"
    "##trgtVersion=5.0.0\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
    "chrX\t147912049\t.\tCGG\t<TR>\t.\t.\tTRID=FXS_FMR1;END=147912111;MOTIFS=CGG;STRUC=<TR>"
    "\tGT:AL:MC\t1/2:90,93:30,31\n"
)
SIGN_OUT = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: no reported variant holds an ACMG classification",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: two variants are too few for the trio's QC",
    "acknowledge_unreviewed_findings": True,
    "unreviewed_findings_acknowledgement_reason": "e2e: the synthetic repeat call needs no review",
}


def _package(root: Path, family_id: str, analysis: str, trio: tuple[str, str, str]) -> Path:
    proband, mother, father = trio
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{proband}\t{father}\t{mother}\t2\t2\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{father}\t0\t0\t1\t1\n",
        encoding="utf-8",
    )
    lines = ["##fileformat=VCFv4.2\n", "##contig=<ID=chr1>\n", _HEADERS[analysis], _COLUMNS.format(proband, mother, father)]
    lines += [f"chr1\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t.\tGT:DP\t0/1:30\t0/1:30\t0/0:30\n" for pos, ref, alt in _RECORDS[analysis]]
    (root / "family.vcf").write_text("".join(lines), encoding="utf-8")
    datasets: dict[str, Any] = {"snv": {"family_vcf": "family.vcf"}}
    if analysis == "long_read_wgs":
        (root / "trgt").mkdir()
        (root / "trgt" / f"{proband}.vcf").write_text(_TRGT.format(column=f"{proband}_sort"), encoding="utf-8")
        datasets["repeats_trgt"] = {"per_sample": {proband: {"file": f"trgt/{proband}.vcf"}}}
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "analysis_type": analysis,
        "ped": "family.ped",
        "datasets": datasets,
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def _versions(modules: Any) -> dict[str, str | None]:
    return {str(module["key"]): module.get("version") for module in modules or []}


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.repeat_expansion_pg import seed_builtin_repeat_catalog
    from backend.tests.e2e import _harness
    from backend.tests.e2e._harness import ensure_postgres_schema

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        await seed_builtin_repeat_catalog(session)

    # Sample IDs are unique across families: the same DNA numbers in both packages.
    trio = tuple(f"{role}_{family_id}" for role in ("PROBAND", "MOTHER", "FATHER"))
    out: dict[str, Any] = {"imports": {}}

    async def _import(analysis: str, mode: str) -> None:
        root = _package(base / family_id / analysis, family_id, analysis, trio)  # type: ignore[arg-type]
        async with sessionmaker() as session:
            result = await package_import.execute_family_package_import(
                session,
                folder_path=str(root),
                manifest_text=kept_manifest(root),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode=mode,
            )
        out["imports"][analysis] = {
            "completed": result.completed,
            "error": result.error,
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        api = f"/api/families/{family_id}"

        async def _signed(analysis: str | None) -> dict[str, Any]:
            body = dict(SIGN_OUT)
            if analysis is not None:
                body["analysis_type"] = analysis
            signed = await _harness.sign_out(ac, f"{api}/report/sign-out", body)
            item: dict[str, Any] = {"status": signed.status_code, "json": signed.json()}
            version = (item["json"] or {}).get("version")
            if version is not None:
                item["snapshot"] = (await ac.get(f"{api}/report/sign-outs/{version}")).json().get("snapshot")
            return item

        async def _check(analysis: str) -> dict[str, Any]:
            response = await ac.get(f"{api}/report/sign-out-check", params={"analysis_type": analysis})
            return {"status": response.status_code, "json": response.json()}

        await _import("short_read_wgs", "cancel")
        if not out["imports"]["short_read_wgs"]["completed"]:
            return out
        # The family's only analysis: signed out whole, naming it.
        out["short_read"] = await _signed(None)
        out["short_read_check_before"] = await _check("short_read_wgs")

        await _import("long_read_wgs", "update")
        if not out["imports"]["long_read_wgs"]["completed"]:
            return out
        out["short_read_check_after"] = await _check("short_read_wgs")
        out["long_read"] = await _signed("long_read_wgs")
        out["long_read_check"] = await _check("long_read_wgs")
        manifest = await ac.get(f"{api}/annotation-manifest")
        out["manifest"] = {"status": manifest.status_code, "json": manifest.json()}

    async with sessionmaker() as session:
        out["audit"] = [
            dict(row)
            for row in (
                await session.execute(
                    text(
                        "SELECT summary, after ->> 'analysis_type' AS analysis_type, after ->> 'version' AS version "
                        "FROM clinical_audit_events WHERE family_identifier = :f AND action = 'sign_out' "
                        "ORDER BY created_at"
                    ),
                    {"f": family_id},
                )
            ).mappings()
        ]
        out["manifest_rows"] = sorted(
            str(value)
            for value in (
                await session.execute(
                    text(
                        "SELECT m.analysis_type FROM family_annotation_manifest m "
                        "JOIN families f ON f.id = m.family_id WHERE f.family_id = :f"
                    ),
                    {"f": family_id},
                )
            ).scalars()
        )
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.app.services import repeat_expansion_pg
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("manifest_per_analysis")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    mp.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    try:
        return _harness.run_async(lambda: _exercise(base, f"MANI_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_both_packages_are_imported(run) -> None:
    for analysis, item in run["imports"].items():
        assert item["completed"] is True, (analysis, item)
        assert item["datasets"]["snv"][0] == "imported", (analysis, item)
    assert run["imports"]["long_read_wgs"]["datasets"]["repeats_trgt"][0] == "imported"
    # One manifest row per analysis.
    assert run["manifest_rows"] == ["long_read_wgs", "short_read_wgs"]


def test_the_short_read_record_states_its_own_versions(run) -> None:
    short_read = run["short_read"]
    assert short_read["status"] == 200, short_read
    snapshot = short_read["snapshot"]
    assert snapshot["analysis_type"] == "short_read_wgs"
    versions = _versions(snapshot["modules"])
    assert versions["vep"] == "110"
    assert versions["clinvar"] == "20240107"
    assert versions["gatk"] == "4.5.0.0"
    assert "deepvariant" not in versions and "trgt" not in versions
    # The record holds the repeat and Paraphase sections, without a call: the family had none.
    assert snapshot["repeat_expansions"]["loci"] == [] and snapshot["paraphase"]["genes"] == []


def test_the_long_read_import_leaves_the_short_read_records_check_matching(run) -> None:
    before, after = run["short_read_check_before"], run["short_read_check_after"]
    assert before["status"] == 200 and before["json"]["matches"] is True, before
    assert after["status"] == 200, after
    assert after["json"]["version"] == run["short_read"]["json"]["version"]
    assert after["json"]["changed_sections"] == [], after["json"]
    assert after["json"]["matches"] is True


def test_the_long_read_record_states_its_own_versions(run) -> None:
    long_read = run["long_read"]
    assert long_read["status"] == 200, long_read
    snapshot = long_read["snapshot"]
    assert snapshot["analysis_type"] == "long_read_wgs"
    versions = _versions(snapshot["modules"])
    assert versions["vep"] == "112"
    assert versions["clinvar"] == "20260105"
    assert versions["deepvariant"] == "1.6.1"
    assert versions["trgt"] == "5.0.0"
    assert "gatk" not in versions
    # Its repeat section holds the long-read WGS's TRGT call.
    assert [locus["locus_id"] for locus in snapshot["repeat_expansions"]["loci"]] == ["FXS_FMR1"]
    assert run["long_read_check"]["json"]["matches"] is True, run["long_read_check"]


def test_the_family_manifest_lists_each_analysis_apart(run) -> None:
    assert run["manifest"]["status"] == 200, run["manifest"]
    analyses = {entry["analysis_type"]: _versions(entry["modules"]) for entry in run["manifest"]["json"]["analyses"]}
    assert list(analyses) == ["long_read_wgs", "short_read_wgs"]
    assert (analyses["short_read_wgs"]["vep"], analyses["long_read_wgs"]["vep"]) == ("110", "112")
    assert "deepvariant" not in analyses["short_read_wgs"] and "gatk" not in analyses["long_read_wgs"]


def test_each_sign_out_names_its_analysis_on_the_clinical_audit_trail(run) -> None:
    events = run["audit"]
    assert [event["analysis_type"] for event in events] == ["short_read_wgs", "long_read_wgs"]
    assert events[0]["summary"].startswith("Report of the short-read WGS signed out (v")
    assert events[1]["summary"].startswith("Report of the long-read WGS signed out (v")
