"""The monogenic and the conventional NIPT of one pregnancy, imported into one family, E2E.

A synthetic family is imported from two package folders of its own (the owner's decision of
2026-11-19), into real Postgres and ClickHouse, a new family each run:

1. a monogenic NIPT package (``analysis_type: monogenic_nipt``): the father, the maternal plasma
   and the fetus, with the plasma's coverage BED;
2. a conventional NIPT package (``analysis_type: conventional_nipt``) of the same plasma, which
   lists that plasma alone, imported with update.

Then:

* the family holds both analysis types, and the plasma both assays;
* the family keeps one package record per analysis, each naming its own folder and the members
  its package listed;
* the conventional NIPT's import, which lists the plasma alone, leaves the father and the fetus
  active: they are the monogenic NIPT's members;
* each analysis's data is kept: the plasma's coverage BED (monogenic) and its ccnv tracks
  (conventional);
* the family's raw files are read in both folders;
* the cNIPT result is served, and the Sample QC runs the monogenic NIPT's checks.

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

from backend.tests._ccnv_output import write_ccnv_output
from backend.tests._kept_manifest import kept_manifest
from backend.tests.e2e._harness import ensure_postgres_schema

pytestmark = pytest.mark.integration


def _mnipt_package(root: Path, family_id: str, father: str, plasma: str, fetus: str) -> Path:
    root.mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{plasma}\t0\t0\t2\t1\n"
        f"{family_id}\t{fetus}\t{father}\t{plasma}\t0\t2\n",
        encoding="utf-8",
    )
    (root / "coverage").mkdir()
    (root / "coverage" / f"{plasma}.bed").write_text("1\t10000\t18000\t80\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "analysis_type": "monogenic_nipt",
        "ped": "family.ped",
        "samples": {plasma: {"assay": "nipt_cfdna"}},
        "datasets": {
            "coverage": {"per_sample": {plasma: {"bed": f"coverage/{plasma}.bed", "content": "read_depth"}}}
        },
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def _cnipt_package(root: Path, family_id: str, plasma: str) -> Path:
    root.mkdir(parents=True)
    entry = write_ccnv_output(root, plasma)
    (root / "family.ped").write_text(f"{family_id}\t{plasma}\t0\t0\t2\t-9\trole=mother\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "ped": "family.ped",
        "analysis_type": "conventional_nipt",
        "samples": {plasma: {"assay": "cnipt_cfdna"}},
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
        "monogenic_nipt": _mnipt_package(base / family_id / "mnipt", family_id, father, plasma, fetus),
        "conventional_nipt": _cnipt_package(base / family_id / "cnipt", family_id, plasma),
    }
    out: dict[str, Any] = {"members": (father, plasma, fetus), "folders": {k: str(v) for k, v in folders.items()}}
    out["imports"] = {}
    for analysis, mode in (("monogenic_nipt", "cancel"), ("conventional_nipt", "update")):
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

    async with sessionmaker() as session:
        family = (
            await session.execute(
                text("SELECT id::text AS id, metadata FROM families WHERE family_id = :f"), {"f": family_id}
            )
        ).mappings().one()
        out["family_metadata"] = family["metadata"]
        out["members_state"] = {
            str(row["sample_id"]): (bool(row["active"]), row["role"], row["metadata"])
            for row in (
                await session.execute(
                    text(
                        "SELECT s.sample_id, fm.active, fm.role, s.metadata FROM family_members fm "
                        "JOIN samples s ON s.id = fm.sample_id WHERE fm.family_id = CAST(:f AS uuid)"
                    ),
                    {"f": family["id"]},
                )
            ).mappings()
        }
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
    assert run["family_metadata"]["analysis_types"] == ["conventional_nipt", "monogenic_nipt"]
    _father, plasma, _fetus = run["members"]
    assert run["members_state"][plasma][2]["assays"] == ["cnipt_cfdna", "nipt_cfdna"]


def test_each_analysis_keeps_its_own_package_record(run) -> None:
    records = run["family_metadata"]["package_imports"]
    assert sorted(records) == ["conventional_nipt", "monogenic_nipt"]
    father, plasma, fetus = run["members"]
    for analysis, folder in run["folders"].items():
        assert Path(records[analysis]["folder_path"]).resolve() == Path(folder).resolve(), analysis
    assert records["monogenic_nipt"]["members"] == sorted([father, plasma, fetus])
    assert records["conventional_nipt"]["members"] == [plasma]
    # The update kept the record of the analysis it did not import.
    assert records["monogenic_nipt"]["import_mode"] == "new_family"
    assert records["conventional_nipt"]["import_mode"] == "update"


def test_the_conventional_nipt_leaves_the_monogenic_nipts_members_active(run) -> None:
    father, plasma, fetus = run["members"]
    assert {sample: state[0] for sample, state in run["members_state"].items()} == {
        father: True,
        plasma: True,
        fetus: True,
    }
    assert run["members_state"][plasma][1] == "mother"


def test_each_analysiss_data_is_kept(run) -> None:
    assert ("coverage", "web") in run["track_sources"] or any(
        track == "coverage" and source != "ccnv" for track, source in run["track_sources"]
    ), run["track_sources"]
    assert ("coverage", "ccnv") in run["track_sources"] and ("segments", "ccnv") in run["track_sources"]


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
