"""The monogenic and the conventional NIPT of one pregnancy, imported into one family, E2E.

Both packages name the same pregnancy (``pregnancy_id: ZW1``): they are one pregnancy, two
analyses (``monogenic_nipt:ZW1``, ``conventional_nipt:ZW1``). Two pregnancies of one woman are
test_e2e_nipt_pregnancies.py's.

A synthetic family is imported from two package folders of its own (the owner's decision of
2026-11-19), into real Postgres and ClickHouse, a new family each run:

1. a monogenic NIPT package (``analysis_type: monogenic_nipt``): the father, the maternal plasma
   and the fetus, with their callset (one variant) and the plasma's coverage BED;
2. a conventional NIPT package (``analysis_type: conventional_nipt``) of the same plasma, which
   lists that plasma alone, imported with update.

Then:

* the family holds both analysis types, and the plasma both assays;
* the family keeps one package record per analysis, each naming its own folder and the members
  its package listed;
* the conventional NIPT's import, which lists the plasma alone, leaves the father and the fetus
  active: they are the monogenic NIPT's members;
* it keeps every value the family knows of the plasma (the owner's decision of 2026-11-20): its
  package gives no phenotype, and the plasma stays unaffected; the family's PED text stays the
  monogenic NIPT's; the capture panel it names is kept as the conventional NIPT's, beside the
  monogenic NIPT's none;
* a third package, of the conventional NIPT again, that says the plasma is affected is refused,
  with overwrite too, before anything of the family is written, naming the plasma and both values;
* each analysis's data is kept: the plasma's coverage BED (monogenic) and its ccnv tracks
  (conventional);
* the family's raw files are read in both folders;
* the cNIPT result is served, and the Sample QC runs the monogenic NIPT's checks.

Each analysis is then signed out on its own (the owner's decision of 2026-11-19):

* a sign-out that names no analysis is refused: the family holds two;
* the conventional NIPT's record names it and holds its own data only: its results, its ccnv
  tracks, its Sample QC (which runs no check), no reported variant;
* the analyst then tags the monogenic NIPT's variant for the report: the conventional NIPT's
  record still matches its check;
* the monogenic NIPT's record holds that variant, its own tracks and NIPT section, and no
  conventional NIPT result; the list of signed versions gives the latest of each.

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

from backend.app.services.family_package_records import suffixed_source
from backend.tests._ccnv_output import write_ccnv_output
from backend.tests._kept_manifest import kept_manifest
from backend.tests.e2e._harness import ensure_postgres_schema

pytestmark = pytest.mark.integration


# The monogenic NIPT's one variant (synthetic), which the analyst tags for the report.
VARIANT = "7-1000-A-G"
# The two analyses of the pregnancy ZW1, by their keys, and the sources they store under.
MNIPT, CNIPT = "monogenic_nipt:ZW1", "conventional_nipt:ZW1"
BED, CCNV = suffixed_source("web", "ZW1"), suffixed_source("ccnv", "ZW1")
CNIPT_SIGN_OUT = {
    "analysis_type": CNIPT,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: the synthetic ccnv output",
}
MNIPT_SIGN_OUT = {
    "analysis_type": MNIPT,
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": "e2e: the reported variant holds no ACMG classification",
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": "e2e: one variant is too few for the NIPT checks",
}


def _mnipt_package(root: Path, family_id: str, father: str, plasma: str, fetus: str) -> Path:
    root.mkdir(parents=True)
    (root / "nipt_combined.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr7>\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{father}\t{plasma}\n"
        "chr7\t1000\t.\tA\tG\t.\tPASS\tTLOD=300\tGT:AD:DP\t0/1:500,500:1000\t0/1:900,100:1000\n",
        encoding="utf-8",
    )
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{plasma}\t0\t0\t2\t1\n"
        f"{family_id}\t{fetus}\t{father}\t{plasma}\t0\t2\trole=fetus\n",
        encoding="utf-8",
    )
    (root / "coverage").mkdir()
    (root / "coverage" / f"{plasma}.bed").write_text("1\t10000\t18000\t80\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "analysis_type": "monogenic_nipt",
        "pregnancy_id": "ZW1",
        "ped": "family.ped",
        "samples": {plasma: {"assay": "nipt_cfdna"}},
        "datasets": {
            "snv": {"family_vcf": "nipt_combined.vcf", "source_format": "mutect2"},
            "coverage": {"per_sample": {plasma: {"bed": f"coverage/{plasma}.bed", "content": "read_depth"}}},
        },
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def _cnipt_package(root: Path, family_id: str, plasma: str, phenotype: str = "-9") -> Path:
    root.mkdir(parents=True)
    entry = write_ccnv_output(root, plasma)
    (root / "family.ped").write_text(
        f"{family_id}\t{plasma}\t0\t0\t2\t{phenotype}\trole=mother\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "ped": "family.ped",
        "analysis_type": "conventional_nipt",
        "pregnancy_id": "ZW1",
        # The panel the conventional NIPT names: its own, which the monogenic NIPT's artefact list
        # never reads.
        "samples": {plasma: {"assay": "cnipt_cfdna", "assay_panel": "LOWPASS"}},
        "datasets": {"ccnv": {"per_sample": {plasma: entry}}},
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.app.services.raw_import_files_pg import list_raw_import_files
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # Sample IDs are unique across families.
    father, plasma, fetus = (f"{role}_{family_id}" for role in ("FATHER", "CFDNA", "FETUS"))
    folders = {
        MNIPT: _mnipt_package(base / family_id / "mnipt", family_id, father, plasma, fetus),
        CNIPT: _cnipt_package(base / family_id / "cnipt", family_id, plasma),
    }
    out: dict[str, Any] = {"members": (father, plasma, fetus), "folders": {k: str(v) for k, v in folders.items()}}
    out["imports"] = {}
    for analysis, mode in ((MNIPT, "cancel"), (CNIPT, "update")):
        root = folders[analysis]
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
    if not all(item["completed"] for item in out["imports"].values()):
        return out  # the first test names the import that failed

    # The conventional NIPT again, from a package that says the plasma is affected: the monogenic
    # NIPT's plasma is unaffected in CoGA, so the import is refused before it writes anything.
    conflict_root = _cnipt_package(base / family_id / "cnipt_affected", family_id, plasma, phenotype="2")
    async with sessionmaker() as session:
        before = (
            await session.execute(text("SELECT metadata FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one()
        try:
            refused = await package_import.execute_family_package_import(
                session,
                folder_path=str(conflict_root),
                manifest_text=kept_manifest(conflict_root),
                project_id=project_id,
                dry_run=False,
                user=admin,
                conflict_mode="overwrite",
            )
            out["conflict"] = {"refused": None, "completed": refused.completed, "error": refused.error}
        except Exception as exc:  # noqa: BLE001 - the refusal is what the test reads
            await session.rollback()
            out["conflict"] = {"refused": type(exc).__name__, "message": str(exc)}
    async with sessionmaker() as session:
        out["conflict"]["metadata_unchanged"] = (
            await session.execute(text("SELECT metadata FROM families WHERE family_id = :f"), {"f": family_id})
        ).scalar_one() == before

    async with sessionmaker() as session:
        family = (
            await session.execute(
                text("SELECT id::text AS id, metadata FROM families WHERE family_id = :f"), {"f": family_id}
            )
        ).mappings().one()
        out["family_metadata"] = family["metadata"]
        out["members_state"] = {
            str(row["sample_id"]): (bool(row["active"]), row["role"], row["metadata"], row["clinical_status"])
            for row in (
                await session.execute(
                    text(
                        "SELECT s.sample_id, fm.active, fm.role, s.metadata, fm.clinical_status "
                        "FROM family_members fm JOIN samples s ON s.id = fm.sample_id "
                        "WHERE fm.family_id = CAST(:f AS uuid)"
                    ),
                    {"f": family["id"]},
                )
            ).mappings()
        }
        out["pedigree_text"] = (
            await session.execute(text("SELECT pedigree FROM families WHERE id = CAST(:f AS uuid)"), {"f": family["id"]})
        ).scalar_one()
        out["track_sources"] = sorted(
            (str(row["track_type"]), str(row["source"]))
            for row in (
                await session.execute(
                    text(
                        "SELECT DISTINCT track_type, source FROM sample_interval_track_sources "
                        "WHERE family_id = CAST(:f AS uuid)"
                    ),
                    {"f": family["id"]},
                )
            ).mappings()
        )
        out["raw_files"] = [
            (str(record.get("storage_path")), bool(record.get("exists")), bool(record.get("outside_read_folders")))
            for record in await list_raw_import_files(session, family["id"])
        ]

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        cnipt = await ac.get(f"/api/families/{family_id}/cnipt")
        out["cnipt"] = {"status": cnipt.status_code, "json": cnipt.json()}
        qc = await ac.get(f"/api/families/{family_id}/qc/sample-integrity")
        out["sample_qc"] = {"status": qc.status_code, "json": qc.json()}
        out["signouts"] = await _sign_out_each(ac, f"/api/families/{family_id}")
    return out


async def _sign_out_each(ac: Any, family: str) -> dict[str, Any]:
    """Sign out the conventional NIPT, tag the monogenic NIPT's variant for the report, check
    the conventional NIPT's record, sign out the monogenic NIPT, and read both back."""
    from backend.tests.e2e import _harness

    def captured(response: Any) -> dict[str, Any]:
        return {"status": response.status_code, "json": response.json()}

    out: dict[str, Any] = {}
    report = f"{family}/report"
    out["unnamed"] = captured(await ac.post(f"{report}/sign-out", json={}))
    out["cnipt"] = captured(await _harness.sign_out(ac, f"{report}/sign-out", CNIPT_SIGN_OUT))
    out["tagged"] = captured(await ac.put(f"{family}/small-variants/{VARIANT}/review", json={"tags": ["report"]}))
    check = f"{report}/sign-out-check"
    out["cnipt_check"] = captured(await ac.get(check, params={"analysis_type": CNIPT}))
    out["mnipt"] = captured(await _harness.sign_out(ac, f"{report}/sign-out", MNIPT_SIGN_OUT))
    out["cnipt_check_after"] = captured(await ac.get(check, params={"analysis_type": CNIPT}))
    out["mnipt_check"] = captured(await ac.get(check, params={"analysis_type": MNIPT}))
    out["list"] = captured(await ac.get(f"{report}/sign-outs"))
    for analysis in ("cnipt", "mnipt"):
        version = (out[analysis]["json"] or {}).get("version")
        if version is not None:
            out[f"{analysis}_record"] = captured(await ac.get(f"{report}/sign-outs/{version}"))
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("two_nipts")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"NIPT2_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_both_packages_are_imported_into_the_one_family(run) -> None:
    for analysis, item in run["imports"].items():
        assert item["completed"] is True, (analysis, item)


def test_the_family_holds_both_analyses_and_the_plasma_both_assays(run) -> None:
    assert run["family_metadata"]["analyses"] == [CNIPT, MNIPT]
    _father, plasma, _fetus = run["members"]
    assert run["members_state"][plasma][2]["assays"] == ["cnipt_cfdna", "nipt_cfdna"]


def test_each_analysis_keeps_its_own_package_record(run) -> None:
    records = run["family_metadata"]["package_imports"]
    assert sorted(records) == [CNIPT, MNIPT]
    father, plasma, fetus = run["members"]
    for analysis, folder in run["folders"].items():
        assert Path(records[analysis]["folder_path"]).resolve() == Path(folder).resolve(), analysis
    assert records[MNIPT]["members"] == sorted([father, plasma, fetus])
    assert records[CNIPT]["members"] == [plasma]
    # The update kept the record of the analysis it did not import.
    assert records[MNIPT]["import_mode"] == "new_family"
    assert records[CNIPT]["import_mode"] == "update"


def test_the_conventional_nipt_leaves_the_monogenic_nipts_members_active(run) -> None:
    father, plasma, fetus = run["members"]
    assert {sample: state[0] for sample, state in run["members_state"].items()} == {
        father: True,
        plasma: True,
        fetus: True,
    }
    assert run["members_state"][plasma][1] == "mother"


def test_the_conventional_nipt_keeps_what_the_family_knows_of_the_plasma(run) -> None:
    father, plasma, fetus = run["members"]
    # Its package gives no phenotype for the plasma: the monogenic NIPT's "unaffected" stays.
    assert {sample: state[3] for sample, state in run["members_state"].items()} == {
        father: "unaffected",
        plasma: "unaffected",
        fetus: "affected",
    }
    # The family's PED text is not replaced by the package's, which lists the plasma alone.
    rows = {line.split()[1] for line in run["pedigree_text"].splitlines() if line.strip()}
    assert rows == {father, plasma, fetus}, run["pedigree_text"]
    # Each analysis's capture panel is its own: the monogenic NIPT names none.
    assert run["members_state"][plasma][2]["assay_panels"] == {CNIPT: "LOWPASS"}


def test_a_package_that_says_otherwise_of_the_plasma_is_refused_before_anything_is_written(run) -> None:
    _father, plasma, _fetus = run["members"]
    conflict = run["conflict"]
    assert conflict["refused"] == "ImportRefused", conflict
    assert f"{plasma}'s clinical status is affected in the package, unaffected in CoGA" in conflict["message"]
    assert f"({MNIPT})" in conflict["message"]
    # Nothing of the family was written: no mark, no flag, no record of the refused package.
    assert conflict["metadata_unchanged"] is True


def test_each_analysiss_data_is_kept(run) -> None:
    # Each under its pregnancy's own source (family_package_records.suffixed_source).
    assert ("coverage", BED) in run["track_sources"], run["track_sources"]
    assert ("coverage", CCNV) in run["track_sources"] and ("segments", CCNV) in run["track_sources"]


def test_the_raw_files_of_both_folders_are_read(run) -> None:
    folders = [Path(folder).resolve() for folder in run["folders"].values()]
    for folder in folders:
        in_folder = [
            (exists, outside)
            for path, exists, outside in run["raw_files"]
            if Path(path).resolve().is_relative_to(folder)
        ]
        assert in_folder, (folder, run["raw_files"])
        assert all(exists and not outside for exists, outside in in_folder), (folder, in_folder)


def test_the_cnipt_result_and_the_monogenic_nipts_sample_qc_are_served(run) -> None:
    assert run["cnipt"]["status"] == 200, run["cnipt"]
    _father, plasma, _fetus = run["members"]
    assert [sample["sample_id"] for sample in run["cnipt"]["json"]["samples"]] == [plasma]
    assert run["sample_qc"]["status"] == 200, run["sample_qc"]
    assert run["sample_qc"]["json"]["application"] == "nipt"


def _snapshot(run, analysis: str) -> dict[str, Any]:
    record = run["signouts"][f"{analysis}_record"]
    assert record["status"] == 200, record
    return record["json"]["snapshot"]


def test_a_sign_out_that_names_no_analysis_is_refused(run) -> None:
    unnamed = run["signouts"]["unnamed"]
    assert unnamed["status"] == 422, unnamed
    assert f"several analyses ({CNIPT}, {MNIPT})" in str(unnamed["json"]["detail"])


def test_the_conventional_nipt_s_record_holds_its_own_data_only(run) -> None:
    assert run["signouts"]["cnipt"]["status"] == 200, run["signouts"]["cnipt"]
    snapshot = _snapshot(run, "cnipt")
    _father, plasma, _fetus = run["members"]
    assert snapshot["analysis_type"] == CNIPT
    assert [sample["sample_id"] for sample in snapshot["cnipt"]["samples"]] == [plasma]
    assert "nipt" not in snapshot
    assert {track["source"] for track in snapshot["coverage_tracks"]["tracks"]} == {CCNV}
    assert snapshot["sample_qc"]["application"] == "cnipt"
    assert snapshot["reported_variants"] == []
    assert [entry["analysis_type"] for entry in snapshot["package_intake"]["deliveries"]] in ([], [CNIPT])


def test_a_variant_tagged_for_the_monogenic_nipt_leaves_the_conventional_nipt_s_record_matching(run) -> None:
    assert run["signouts"]["tagged"]["status"] == 200, run["signouts"]["tagged"]
    for key in ("cnipt_check", "cnipt_check_after"):
        check = run["signouts"][key]
        assert check["status"] == 200, check
        assert (check["json"]["analysis_type"], check["json"]["matches"]) == (CNIPT, True), check
        assert check["json"]["changed_sections"] == []


def test_the_monogenic_nipt_s_record_holds_its_variant_and_no_conventional_nipt_result(run) -> None:
    assert run["signouts"]["mnipt"]["status"] == 200, run["signouts"]["mnipt"]
    snapshot = _snapshot(run, "mnipt")
    assert snapshot["analysis_type"] == MNIPT
    assert [entry["variant_id"] for entry in snapshot["reported_variants"]] == [VARIANT]
    assert "cnipt" not in snapshot and "nipt" in snapshot
    assert CCNV not in {track["source"] for track in snapshot["coverage_tracks"]["tracks"]}
    assert snapshot["sample_qc"]["application"] == "nipt"
    check = run["signouts"]["mnipt_check"]
    assert (check["status"], check["json"]["matches"]) == (200, True), check


def test_the_list_of_signed_versions_gives_the_latest_of_each_analysis(run) -> None:
    listed = run["signouts"]["list"]
    assert listed["status"] == 200, listed
    versions = {run["signouts"][analysis]["json"]["version"] for analysis in ("cnipt", "mnipt")}
    assert {entry["version"] for entry in listed["json"]["signouts"]} == versions
    assert [
        (entry["analysis_type"], entry["version"]) for entry in listed["json"]["latest_per_analysis"]
    ] == [
        (CNIPT, run["signouts"]["cnipt"]["json"]["version"]),
        (MNIPT, run["signouts"]["mnipt"]["json"]["version"]),
    ]
