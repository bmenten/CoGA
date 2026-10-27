"""A restore that fails part-way keeps the family marked until it is imported again in full
(E2E, SAFE-13, SAFE-16, SAFE-19, SAFE-20 and SAFE-23).

An overwrite of an existing family that fails puts the family back from the backup it made
first, table by table: the family's rows deleted, then the backup's re-inserted. A restore
that fails part-way may already have deleted the family's rows of any dataset, whichever
import wrote them. Its import-incomplete flag named only the dataset that had failed, so an
import of that dataset alone cleared the flag, and the family signed out without the
incomplete-import gate while its small variants were gone (TF-06 H16, REQ-TRACE-013). The
owner's decision (2026-10-09): the flag names every dataset of the import as failed. That
still left out a dataset the import did not carry (SAFE-16), whose rows the restore removes
too; the owner's decision (2026-10-13): the import's ``import_unfinished`` entry stays,
naming every dataset of the family, until an overwrite import of the whole family completes.
A dataset only a single-file upload can bring was not in it (SAFE-19): uploads now record
what they brought, and an upload with overwrite brings it again. An SNV dataset whose
``source_format`` is ``auto`` was recorded as ``auto`` whatever callset its file held, so
two such imports brought each other's callset again (SAFE-23): each now records the callset
it stored.

Over six new one-sample families this runs real import jobs (``POST /api/family-imports``,
then the worker's ``run_family_import_job``) and structural-variant uploads
(``POST /api/structural-variants/upload/<sample>``) against Postgres and ClickHouse, and signs
out through ``POST /api/families/<id>/report/sign-out`` after each re-import.

The first family (the restore fails in the small variants):

* package 1 (cancel): 6 small variants and coverage: the family is complete;
* package 2 (overwrite): 4 other small variants, which replace the 6, and coverage, which
  fails; ClickHouse then fails the restore's re-insert of the small variants after their
  delete, so the family has none left. The flag names coverage and snv as failed and none as
  imported (before #807: coverage alone), the import's entry stays naming both (before:
  removed), the job's log says why, and the sign-out is refused;
* package 3 (overwrite, coverage alone) completes: the flag and the entry still name snv
  (before #807: cleared, and the family signed out with no small variants), the job's log
  says why, and the sign-out is still refused;
* package 4 (overwrite, the 6 small variants) completes: the family held nothing else, so
  the flag and the entry go, and the report signs out with no import acknowledgement.

The second family (the restore fails in the tracks, which the failing import did not carry):

* package 1 (cancel): 6 small variants and coverage: the family is complete;
* package 2 (overwrite, small variants alone), which fail; ClickHouse then fails the
  restore's re-insert of the tracks after their delete: the small variants are put back,
  the coverage is gone. The flag names snv alone, the entry snv and coverage;
* package 3 (overwrite, small variants alone: the dataset that failed) completes: the flag
  goes, but the entry keeps coverage and the sign-out is still refused (before: the family
  signed out with its coverage gone);
* package 4 (overwrite, coverage alone) completes: the whole family has been imported again,
  the entry goes, and the report signs out with no import acknowledgement.

The third and fourth families (SAFE-20: an update after such a restore):

* package 1 (cancel): 6 small variants and coverage: the family is complete;
* package 2 (overwrite): 4 other small variants, and coverage, which fails; ClickHouse fails
  the restore's re-insert of the small variants (third family) or of the tracks (fourth).
  The third family keeps its coverage rows, the fourth loses them; in both, Postgres still
  records the coverage track (its restore never ran). The flags name coverage and snv;
* package 3 (update, coverage alone) completes. The third family's coverage rows are there:
  the update skips coverage, which ends ``skipped``, and the flag still names it (before:
  it read ``imported`` and coverage left the flag, though nothing of it was imported
  again). The fourth's are gone: the update imports coverage again, and only then does
  coverage leave the flag (before: skipped as there, from Postgres's record, and coverage
  left the flag all the same, its rows still gone). The restore's entry refuses the
  sign-out in both: an update removes nothing from it.

The fifth family (the restore removes SV calls a single-file upload loaded):

* package 1 (cancel): 6 small variants; then an upload of the sample's SV calls (a manual
  TSV, which no package dataset brings): the family records them (``sv_manual``);
* package 2 (overwrite, small variants alone), which fail; ClickHouse then fails the
  restore's re-insert of the SV calls after their delete: the small variants are put back,
  the uploaded SV calls are gone. The entry names snv and sv_manual (before: snv alone);
* package 3 (overwrite, small variants alone) completes: the flag goes, but the entry keeps
  sv_manual and the sign-out is still refused (before: the family signed out with its SV
  calls gone);
* the SV calls uploaded again with overwrite: the entry goes, and the report signs out with no
  import acknowledgement.

The sixth family (the restore removes the direct calls an import stored, every package's
``source_format`` left at ``auto``):

* package 1 (cancel): 6 small variants (direct calls) and coverage: the family records the
  callset as ``clair3`` (before: ``auto``);
* package 2 (overwrite, coverage alone), which fails; ClickHouse then fails the restore's
  re-insert of the small variants after their delete: the direct calls are gone, and the
  entry names them;
* package 3 (overwrite): 6 imputed genotypes, stored as ``glimpse2``, and coverage complete:
  the entry keeps the direct calls and the sign-out is still refused (before: both scopes
  read ``auto``, the entry went, and the family signed out with its direct calls gone);
* package 4 (overwrite, the direct calls) completes: the entry goes, and the report signs out
  with no import acknowledgement.

The dataset failures and the restores' failures are injected (the import's dataset step,
and the restore's ``execute_clickhouse``); everything else runs as in production.
Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py). The families are new and uniquely named; the job rows end
completed or failed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e``
job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_VARIANTS = 6
_OVERWRITE_VARIANTS = 4
# The third family's SV calls, uploaded as a manual TSV (ID, CHROM, POS, END, REF, ALT,
# SVTYPE, GT): no package dataset brings them.
_UPLOADED_SVS = b"SVDEL1\t1\t10000\t20000\tN\t<DEL>\tDEL\t0/1\nSVDUP1\t1\t50000\t60000\tN\t<DUP>\tDUP\t0/1\n"
_ACK = "e2e validation"
# The drift and Sample-QC gates come first; they are acknowledged in every attempt here.
_OTHER_GATES = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": _ACK,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": _ACK,
}


def _cap(resp) -> dict:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


def _write_package(
    root: Path,
    family_id: str,
    *,
    variants: int | None = _VARIANTS,
    first_position: int = 100,
    coverage: bool = True,
    imputed: bool = False,
) -> Path:
    """A one-sample package: a family VCF of ``variants`` SNVs (none: no SNV dataset),
    direct calls or, with ``imputed``, imputed genotypes with their posteriors (its
    source_format left at auto either way) and, with ``coverage``, a coverage BED."""
    sample = f"{family_id}_S1"  # sample ids are unique across families
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    manifest = [f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\ndatasets:\n"]
    if variants is not None:
        (root / "snv").mkdir(exist_ok=True)
        call = "GT:GP\t0|1:0.05,0.9,0.05" if imputed else "GT\t0/1"
        posteriors = '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Genotype posteriors">\n'
        (root / "snv" / "family.vcf").write_text(
            "##fileformat=VCFv4.2\n"
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            + (posteriors if imputed else "")
            + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
            + "".join(
                f"1\t{first_position + 100 * i}\t.\tA\tG\t60\tPASS\t.\t{call}\n"
                for i in range(variants)
            ),
            encoding="utf-8",
        )
        manifest.append("  snv:\n    family_vcf: snv/family.vcf\n")
    if coverage:
        (root / "coverage").mkdir(exist_ok=True)
        (root / "coverage" / "S1.bed").write_text(
            "1\t0\t1000\t31.0\n1\t1000\t2000\t29.5\n1\t2000\t3000\t30.2\n", encoding="utf-8"
        )
        manifest.append(f"  coverage:\n    per_sample:\n      {sample}:\n        bed: coverage/S1.bed\n")
    (root / "manifest.yaml").write_text("".join(manifest), encoding="utf-8")
    return root


async def _collect(base: Path, mp: pytest.MonkeyPatch) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import clickhouse_family_snapshot as snapshots
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_family_snapshot import list_import_backup_tables
    from backend.app.services.clickhouse_interval_tracks import (
        count_interval_track_source_rows,
        fetch_interval_track_rows,
    )
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        count_family_structural_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # The failures a failing package injects: its ``dataset`` fails before it writes
    # anything, and ClickHouse fails its restore's re-insert of the ``restore`` table from
    # the backup, once, after that table's delete has run.
    failing: dict[str, str | None] = {"dataset": None, "restore": None}
    reinserts_failed: list[str] = []
    real_import_dataset = package_import._import_dataset

    async def import_dataset(*args, **kwargs):
        if failing["dataset"] and kwargs["summary"].dataset_type == failing["dataset"]:
            raise RuntimeError(f"e2e: the {failing['dataset']} file could not be read")
        return await real_import_dataset(*args, **kwargs)

    real_execute = snapshots.execute_clickhouse

    async def execute(query: str, parameters: Any = None) -> Any:
        sql = " ".join(query.split())
        table = failing["restore"]
        if (
            table
            and sql.startswith("INSERT INTO")
            and "/SNAPSHOT/" in sql.partition(" FROM ")[2]
            and sql.partition(" SELECT ")[0].endswith(f"/{table}`")
        ):
            failing["restore"] = None  # once: the restore stops here
            reinserts_failed.append(sql.split()[2])
            raise RuntimeError("e2e: ClickHouse failed the restore's re-insert")
        return await real_execute(query, parameters)

    mp.setattr(package_import, "_import_dataset", import_dataset)
    mp.setattr(snapshots, "execute_clickhouse", execute)

    suffix = uuid4().hex[:8].upper()
    families = {
        "snv": f"FAM_PARTWAY_{suffix}",
        "tracks": f"FAM_PARTWAY_T_{suffix}",
        "kept": f"FAM_PARTWAY_K_{suffix}",
        "removed": f"FAM_PARTWAY_R_{suffix}",
        "upload": f"FAM_PARTWAY_U_{suffix}",
        "callset": f"FAM_PARTWAY_C_{suffix}",
    }
    out: dict[str, Any] = {"families": families, "steps": {}}
    jobs: list[str] = []

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT status, error, logs, dataset_summaries FROM family_import_jobs "
                        "WHERE id = CAST(:j AS uuid)"
                    ),
                    {"j": job_id},
                )
            ).mappings().one()
        return dict(row)

    async def family(family_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text("SELECT id::text AS uuid, metadata FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).mappings().one()
            coverage = await count_interval_track_source_rows(
                s, family_uuid=row["uuid"], track_type="coverage"
            )
        metadata = row["metadata"] or {}
        return {
            "unfinished": metadata.get("import_unfinished"),
            "incomplete": metadata.get("import_incomplete"),
            "imported_scopes": metadata.get("imported_scopes"),
            "snvs": await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id]
            ),
            # By callset: direct calls, and imputed genotypes (the sixth family).
            "clair3_snvs": await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id], source="clair3"
            ),
            "glimpse2_snvs": await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id], source="glimpse2"
            ),
            "svs": await count_family_structural_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id]
            ),
            # The coverage source as Postgres registers it, and its rows in ClickHouse,
            # which a restore rewrites.
            "coverage_rows": coverage,
            "coverage_track_rows": len(
                await fetch_interval_track_rows(
                    _harness.ASSEMBLY, family_uuid=row["uuid"], track_type="coverage", chromosomes=["1"]
                )
            ),
        }

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def run_import(
                scenario: str, name: str, conflict_mode: str, **package: Any
            ) -> dict[str, Any]:
                family_id = families[scenario]
                root = _write_package(base / scenario / name / family_id, family_id, **package)
                body: dict[str, Any] = {
                    "folder_path": str(root),
                    "project_id": project_id,
                    "dry_run": False,
                    "conflict_mode": conflict_mode,
                }
                if conflict_mode != "cancel":
                    body["family_id"] = family_id
                resp = await ac.post("/api/family-imports", json=body)
                assert resp.status_code == 200, resp.text
                job_id = str(resp.json()["_id"])
                jobs.append(job_id)
                worker = f"e2e-restore-{uuid4().hex[:8]}"
                async with sm() as s:
                    await s.execute(
                        text(
                            "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                            "started_at = now(), heartbeat_at = now() "
                            "WHERE id = CAST(:j AS uuid) AND status = 'queued'"
                        ),
                        {"j": job_id, "w": worker},
                    )
                    await s.commit()
                await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
                step: dict[str, Any] = {
                    "job_id": job_id,
                    "job": await job(job_id),
                    "family": await family(family_id),
                    "backups": [table.name for table in await list_import_backup_tables(owner=job_id)],
                    "reinserts_failed": list(reinserts_failed),
                }
                reinserts_failed.clear()
                out["steps"][f"{scenario}/{name}"] = step
                return step

            async def sign_out(scenario: str, step: dict[str, Any]) -> None:
                step["signout"] = _cap(
                    await ac.post(
                        f"/api/families/{families[scenario]}/report/sign-out", json=_OTHER_GATES
                    )
                )

            async def upload_svs(scenario: str, name: str, *, overwrite: bool) -> dict[str, Any]:
                family_id = families[scenario]
                step: dict[str, Any] = {
                    "upload": _cap(
                        await ac.post(
                            f"/api/structural-variants/upload/{family_id}_S1",
                            params={"overwrite": str(overwrite).lower(), "source_format": "manual"},
                            files={"file": ("svs.tsv", _UPLOADED_SVS, "text/plain")},
                        )
                    ),
                    "family": await family(family_id),
                }
                out["steps"][f"{scenario}/{name}"] = step
                return step

            # The first family: the restore fails in the small variants.
            await run_import("snv", "1_complete", "cancel")
            failing.update(dataset="coverage", restore="SNV_INDEL/entries")
            try:
                step = await run_import(
                    "snv",
                    "2_overwrite_restore_fails",
                    "overwrite",
                    variants=_OVERWRITE_VARIANTS,
                    first_position=10_000,
                )
            finally:
                failing.update(dataset=None, restore=None)
            await sign_out("snv", step)
            await sign_out("snv", await run_import("snv", "3_coverage_again", "overwrite", variants=None))
            await sign_out("snv", await run_import("snv", "4_snv_again", "overwrite", coverage=False))

            # The second family: the restore fails in the tracks, which the overwrite that
            # failed did not carry.
            await run_import("tracks", "1_complete", "cancel")
            failing.update(dataset="snv", restore="INTERVAL/entries")
            try:
                step = await run_import(
                    "tracks",
                    "2_overwrite_restore_fails",
                    "overwrite",
                    variants=_OVERWRITE_VARIANTS,
                    first_position=10_000,
                    coverage=False,
                )
            finally:
                failing.update(dataset=None, restore=None)
            await sign_out("tracks", step)
            await sign_out("tracks", await run_import("tracks", "3_snv_again", "overwrite", coverage=False))
            await sign_out("tracks", await run_import("tracks", "4_coverage_again", "overwrite", variants=None))

            # The third and fourth families: an update after the restore failed part-way,
            # its coverage rows left by it, or removed (SAFE-20).
            for scenario, table in (("kept", "SNV_INDEL/entries"), ("removed", "INTERVAL/entries")):
                await run_import(scenario, "1_complete", "cancel")
                failing.update(dataset="coverage", restore=table)
                try:
                    await run_import(
                        scenario,
                        "2_overwrite_restore_fails",
                        "overwrite",
                        variants=_OVERWRITE_VARIANTS,
                        first_position=10_000,
                    )
                finally:
                    failing.update(dataset=None, restore=None)
                await sign_out(scenario, await run_import(scenario, "3_coverage_update", "update", variants=None))

            # The fifth family: the restore removes SV calls a single-file upload loaded,
            # which no package dataset brings (SAFE-19).
            await run_import("upload", "1_complete", "cancel", coverage=False)
            await upload_svs("upload", "1_sv_upload", overwrite=False)
            failing.update(dataset="snv", restore="SV/entries")
            try:
                step = await run_import(
                    "upload",
                    "2_overwrite_restore_fails",
                    "overwrite",
                    variants=_OVERWRITE_VARIANTS,
                    first_position=10_000,
                    coverage=False,
                )
            finally:
                failing.update(dataset=None, restore=None)
            await sign_out("upload", step)
            await sign_out("upload", await run_import("upload", "3_snv_again", "overwrite", coverage=False))
            await sign_out("upload", await upload_svs("upload", "4_sv_upload_again", overwrite=True))

            # The sixth family: the restore removes the direct calls an import stored, its
            # source_format left at auto; an import of imputed genotypes, auto too, does not
            # bring them again (SAFE-23).
            await run_import("callset", "1_complete", "cancel")
            failing.update(dataset="coverage", restore="SNV_INDEL/entries")
            try:
                step = await run_import("callset", "2_overwrite_restore_fails", "overwrite", variants=None)
            finally:
                failing.update(dataset=None, restore=None)
            await sign_out("callset", step)
            await sign_out(
                "callset",
                await run_import("callset", "3_imputed", "overwrite", imputed=True, first_position=50_000),
            )
            await sign_out("callset", await run_import("callset", "4_direct_again", "overwrite", coverage=False))
    finally:
        async with sm() as s:
            await s.execute(
                text(
                    "UPDATE family_import_jobs SET status = 'failed', worker_id = NULL, "
                    "completed_at = now(), error = 'e2e: left unfinished' "
                    "WHERE id::text = ANY(:jobs) AND status IN ('queued', 'validating', 'running')"
                ),
                {"jobs": jobs},
            )
            await s.commit()
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("import_restore_fails_part_way")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base, mp))


def _refused(step: dict) -> dict:
    resp = step["signout"]
    assert resp["status"] == 409, resp["text"]
    detail = resp["json"]["detail"]
    assert detail["gate"] == "import_incomplete", detail
    return detail


def _signed(step: dict) -> dict:
    resp = step["signout"]
    assert resp["status"] == 200, resp["text"]
    snapshot = resp["json"]["snapshot"]
    assert snapshot["import_incomplete"] is None and snapshot["import_unfinished"] == {}
    return snapshot


# --- the first family: the restore fails in the small variants ----------------------------


def test_the_family_was_complete_before_the_overwrite(snap) -> None:
    step = snap["steps"]["snv/1_complete"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["family"]["snvs"] == _VARIANTS
    assert step["family"]["coverage_rows"] > 0
    assert step["family"]["incomplete"] is None and step["family"]["unfinished"] is None
    # What it imported is recorded for the family: what a restore may remove.
    assert sorted(step["family"]["imported_scopes"]) == ["coverage", "snv"]


def test_the_restore_failed_part_way_and_removed_the_small_variants(snap) -> None:
    step = snap["steps"]["snv/2_overwrite_restore_fails"]
    assert step["job"]["status"] == "failed"
    # The job's record says what the flag says (SAFE-18; before, it named coverage alone, and
    # its dataset table showed snv imported): both failed.
    assert step["job"]["error"] == "Family package import failed for dataset(s): coverage, snv"
    summaries = {item["dataset_type"]: item for item in step["job"]["dataset_summaries"]}
    assert (summaries["coverage"]["status"], summaries["snv"]["status"]) == ("failed", "failed")
    assert summaries["snv"]["message"].startswith("Its loader imported, but the import failed and the restore")
    # The overwrite had replaced the 6 small variants with its 4; the restore deleted those
    # and failed before it put the 6 back.
    assert len(step["reinserts_failed"]) == 1 and "SNV_INDEL/entries" in step["reinserts_failed"][0]
    assert step["family"]["snvs"] == 0
    # Its backup is dropped all the same.
    assert step["backups"] == []


def test_its_flag_names_every_dataset_of_the_import_and_its_entry_stays(snap) -> None:
    step = snap["steps"]["snv/2_overwrite_restore_fails"]
    flag = step["family"]["incomplete"]
    # Before #807: coverage alone, so an import of coverage cleared the flag.
    assert flag["failed_datasets"] == ["coverage", "snv"]
    assert flag["imported_datasets"] == []
    assert flag["failed_jobs"] == {"coverage": step["job_id"], "snv": step["job_id"]}
    # Before: the entry made way for the flag. Now it stays, every dataset of the family
    # pending: this family held nothing but what this import carried.
    entry = step["family"]["unfinished"][step["job_id"]]
    assert (entry["datasets"], entry["finished_datasets"]) == (["coverage", "snv"], [])
    (line,) = [line for line in step["job"]["logs"] if "restore also failed" in line]
    assert "the flag names every dataset of this import as failed, those that imported too (coverage, snv)" in line
    assert "Import the whole family again with overwrite, not only the datasets that failed" in line
    detail = _refused(step)
    assert detail["import_incomplete"]["failed_datasets"] == ["coverage", "snv"]
    assert detail["import_unfinished"][step["job_id"]]["datasets"] == ["coverage", "snv"]


def test_an_import_of_the_dataset_that_failed_alone_leaves_the_gate_shut(snap) -> None:
    flagged, step = snap["steps"]["snv/2_overwrite_restore_fails"], snap["steps"]["snv/3_coverage_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["family"]["coverage_rows"] > 0
    assert step["family"]["snvs"] == 0
    # Before #807: the flag was cleared here, and the family signed out with no small variants.
    flag = step["family"]["incomplete"]
    assert flag["failed_datasets"] == ["snv"]
    assert flag["failed_jobs"] == {"snv": flagged["job_id"]}
    assert step["family"]["unfinished"][flagged["job_id"]]["datasets"] == ["snv"]
    note = next(line for line in step["job"]["logs"] if "stays flagged import-incomplete" in line)
    assert f"snv in import job {flagged['job_id']}" in note
    detail = _refused(step)
    assert detail["import_incomplete"]["failed_datasets"] == ["snv"]
    assert "failed for snv" in detail["message"]


def test_an_import_of_every_dataset_completes_the_family(snap) -> None:
    step = snap["steps"]["snv/4_snv_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["family"]["snvs"] == _VARIANTS
    assert step["family"]["incomplete"] is None and step["family"]["unfinished"] is None
    _signed(step)


# --- the second family: the restore removes a dataset the import did not carry ------------


def test_the_restore_failed_in_the_tracks_the_overwrite_did_not_carry(snap) -> None:
    before, step = snap["steps"]["tracks/1_complete"], snap["steps"]["tracks/2_overwrite_restore_fails"]
    assert before["job"]["status"] == "completed", before["job"]["error"]
    assert before["family"]["coverage_track_rows"] > 0
    assert step["job"]["status"] == "failed"
    assert step["job"]["error"] == "Family package import failed for dataset(s): snv"
    assert len(step["reinserts_failed"]) == 1 and "INTERVAL/entries" in step["reinserts_failed"][0]
    # The small variants were put back; the coverage, which this import never carried, is gone.
    assert step["family"]["snvs"] == _VARIANTS
    assert step["family"]["coverage_track_rows"] == 0
    assert step["backups"] == []


def test_the_entry_names_the_dataset_the_import_did_not_carry(snap) -> None:
    step = snap["steps"]["tracks/2_overwrite_restore_fails"]
    # The flag names what this import carried (#807), not the coverage.
    assert step["family"]["incomplete"]["failed_datasets"] == ["snv"]
    # The entry stays and names the coverage too (before: removed, nothing named it).
    entry = step["family"]["unfinished"][step["job_id"]]
    assert (entry["datasets"], entry["finished_datasets"]) == (["coverage", "snv"], [])
    (line,) = [line for line in step["job"]["logs"] if "restore also failed" in line]
    assert "imported again with overwrite (coverage, snv)" in line
    detail = _refused(step)
    assert detail["import_unfinished"][step["job_id"]]["datasets"] == ["coverage", "snv"]


def test_an_import_of_the_dataset_that_failed_again_leaves_the_gate_shut(snap) -> None:
    flagged, step = snap["steps"]["tracks/2_overwrite_restore_fails"], snap["steps"]["tracks/3_snv_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["family"]["snvs"] == _VARIANTS
    assert step["family"]["coverage_track_rows"] == 0
    # The flag goes: every dataset it named has been imported again.
    assert step["family"]["incomplete"] is None
    # Before: so did the gate, and the family signed out with its coverage gone. Now the
    # entry keeps the coverage, and the sign-out is refused.
    assert step["family"]["unfinished"][flagged["job_id"]]["datasets"] == ["coverage"]
    note = next(line for line in step["job"]["logs"] if "stays marked import-incomplete" in line)
    assert f"(import job {flagged['job_id']}, still to import: coverage)" in note
    detail = _refused(step)
    assert detail["import_incomplete"] is None
    assert detail["import_unfinished"][flagged["job_id"]]["datasets"] == ["coverage"]
    assert "coverage may be partly written or missing" in detail["message"]


def test_an_import_of_the_rest_of_the_family_completes_it(snap) -> None:
    step = snap["steps"]["tracks/4_coverage_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["family"]["coverage_track_rows"] > 0 and step["family"]["snvs"] == _VARIANTS
    assert step["family"]["incomplete"] is None and step["family"]["unfinished"] is None
    _signed(step)


# --- the third and fourth families: an update after the restore (SAFE-20) ----------------


def _coverage_summary(step: dict) -> dict:
    return next(dataset for dataset in step["job"]["dataset_summaries"] if dataset["dataset_type"] == "coverage")


def test_the_restores_left_the_coverage_rows_or_removed_them_while_postgres_records_them(snap) -> None:
    for scenario, rows in (("kept", 3), ("removed", 0)):
        before, step = snap["steps"][f"{scenario}/1_complete"], snap["steps"][f"{scenario}/2_overwrite_restore_fails"]
        assert before["job"]["status"] == "completed", before["job"]["error"]
        assert step["job"]["status"] == "failed"
        assert step["family"]["incomplete"]["failed_datasets"] == ["coverage", "snv"]
        # What ClickHouse holds, and what Postgres records: its restore never ran.
        assert step["family"]["coverage_track_rows"] == rows, scenario
        assert step["family"]["coverage_rows"] == 3, scenario


def test_an_update_skips_the_coverage_the_restore_left_and_keeps_it_flagged(snap) -> None:
    flagged, step = snap["steps"]["kept/2_overwrite_restore_fails"], snap["steps"]["kept/3_coverage_update"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    coverage = _coverage_summary(step)
    # Before: "imported", and coverage left the flag, though nothing of it was imported again.
    assert coverage["status"] == "skipped"
    sample = f"{snap['families']['kept']}_S1"
    assert coverage["message"] == f"Skipped coverage import in update mode because coverage already exists for {sample}"
    assert step["family"]["coverage_track_rows"] == 3
    assert step["family"]["incomplete"]["failed_datasets"] == ["coverage", "snv"]
    assert step["family"]["incomplete"]["failed_jobs"] == {"coverage": flagged["job_id"], "snv": flagged["job_id"]}
    note = next(line for line in step["job"]["logs"] if "stays flagged import-incomplete" in line)
    assert f"coverage, snv in import job {flagged['job_id']}" in note
    detail = _refused(step)
    assert detail["import_incomplete"]["failed_datasets"] == ["coverage", "snv"]


def test_an_update_imports_the_coverage_the_restore_removed_again(snap) -> None:
    flagged, step = snap["steps"]["removed/2_overwrite_restore_fails"], snap["steps"]["removed/3_coverage_update"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    coverage = _coverage_summary(step)
    # Before: skipped from Postgres's record, the rows still gone, and coverage left the flag
    # all the same.
    assert (coverage["status"], coverage["message"]) == ("imported", "Imported coverage into interval tracks")
    assert step["family"]["coverage_track_rows"] == 3
    assert step["family"]["incomplete"]["failed_datasets"] == ["snv"]
    # The restore's entry still lists coverage, as an update removes nothing from it: the
    # sign-out stays refused until an overwrite imports the family again.
    assert step["family"]["unfinished"][flagged["job_id"]]["datasets"] == ["coverage", "snv"]
    detail = _refused(step)
    assert detail["import_unfinished"][flagged["job_id"]]["datasets"] == ["coverage", "snv"]


# --- the fifth family: the restore removes SV calls a single-file upload loaded ------------


def test_an_upload_records_what_it_brought_for_the_family(snap) -> None:
    imported, step = snap["steps"]["upload/1_complete"], snap["steps"]["upload/1_sv_upload"]
    assert imported["job"]["status"] == "completed", imported["job"]["error"]
    assert step["upload"]["status"] == 200, step["upload"]["text"]
    assert step["family"]["svs"] == 2
    # Before (SAFE-19): the family recorded the package's small variants alone. Their
    # callset is the one the package's file holds, its source_format being auto (SAFE-23).
    sample = f"{snap['families']['upload']}_S1"
    assert step["family"]["imported_scopes"] == {
        "snv": [{"source": "clair3", "samples": None}],
        "sv_manual": [{"source": None, "samples": [sample]}],
    }


def test_the_entry_names_the_uploaded_calls_the_restore_removed(snap) -> None:
    step = snap["steps"]["upload/2_overwrite_restore_fails"]
    assert step["job"]["status"] == "failed"
    assert step["job"]["error"] == "Family package import failed for dataset(s): snv"
    assert len(step["reinserts_failed"]) == 1 and "SV/entries" in step["reinserts_failed"][0]
    # The small variants were put back; the uploaded SV calls, which no package carried, are gone.
    assert step["family"]["snvs"] == _VARIANTS
    assert step["family"]["svs"] == 0
    assert step["family"]["incomplete"]["failed_datasets"] == ["snv"]
    # Before: the entry named snv alone.
    entry = step["family"]["unfinished"][step["job_id"]]
    assert (entry["datasets"], entry["finished_datasets"]) == (["snv", "sv_manual"], [])
    detail = _refused(step)
    assert detail["import_unfinished"][step["job_id"]]["datasets"] == ["snv", "sv_manual"]


def test_an_import_of_the_package_again_leaves_the_gate_shut(snap) -> None:
    flagged, step = snap["steps"]["upload/2_overwrite_restore_fails"], snap["steps"]["upload/3_snv_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert step["family"]["snvs"] == _VARIANTS and step["family"]["svs"] == 0
    assert step["family"]["incomplete"] is None
    # Before: the entry went too, and the family signed out with its uploaded SV calls gone.
    assert step["family"]["unfinished"][flagged["job_id"]]["datasets"] == ["sv_manual"]
    note = next(line for line in step["job"]["logs"] if "stays marked import-incomplete" in line)
    assert f"(import job {flagged['job_id']}, still to import: sv_manual)" in note
    detail = _refused(step)
    assert detail["import_incomplete"] is None
    assert detail["import_unfinished"][flagged["job_id"]]["datasets"] == ["sv_manual"]


def test_the_calls_uploaded_again_with_overwrite_complete_the_family(snap) -> None:
    step = snap["steps"]["upload/4_sv_upload_again"]
    assert step["upload"]["status"] == 200, step["upload"]["text"]
    assert step["family"]["svs"] == 2 and step["family"]["snvs"] == _VARIANTS
    assert step["family"]["incomplete"] is None and step["family"]["unfinished"] is None
    _signed(step)


# --- the sixth family: the restore removes the callset an auto import stored (SAFE-23) -----

_DIRECT_CALLSET = {"source": "clair3", "samples": None}


def test_the_restore_removed_the_direct_calls_an_auto_import_stored(snap) -> None:
    first, step = snap["steps"]["callset/1_complete"], snap["steps"]["callset/2_overwrite_restore_fails"]
    assert first["job"]["status"] == "completed", first["job"]["error"]
    # The package leaves source_format at auto; its file holds direct calls, which the family
    # records as such (before: "auto").
    assert first["family"]["imported_scopes"]["snv"] == [_DIRECT_CALLSET]
    assert step["job"]["status"] == "failed"
    assert len(step["reinserts_failed"]) == 1 and "SNV_INDEL/entries" in step["reinserts_failed"][0]
    assert step["family"]["clair3_snvs"] == 0
    entry = step["family"]["unfinished"][step["job_id"]]
    assert {"dataset": "snv", "scope": _DIRECT_CALLSET} in entry["whole_family"]
    _refused(step)


def test_an_auto_import_of_imputed_genotypes_does_not_bring_the_direct_calls_again(snap) -> None:
    flagged, step = snap["steps"]["callset/2_overwrite_restore_fails"], snap["steps"]["callset/3_imputed"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    # Stored as the callset its file holds: imputed genotypes, and still no direct calls.
    assert (step["family"]["glimpse2_snvs"], step["family"]["clair3_snvs"]) == (_VARIANTS, 0)
    assert step["family"]["incomplete"] is None
    # Before: both imports' scopes read "auto", so this one struck the direct calls off the
    # entry, and the family signed out with them gone.
    detail = _refused(step)
    assert detail["import_unfinished"][flagged["job_id"]]["datasets"] == ["snv"]
    entry = step["family"]["unfinished"][flagged["job_id"]]
    assert entry["whole_family"] == [{"dataset": "snv", "scope": _DIRECT_CALLSET}]
    assert step["family"]["imported_scopes"]["snv"] == [_DIRECT_CALLSET, {"source": "glimpse2", "samples": None}]


def test_an_auto_import_of_the_direct_calls_completes_the_family(snap) -> None:
    step = snap["steps"]["callset/4_direct_again"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert (step["family"]["clair3_snvs"], step["family"]["glimpse2_snvs"]) == (_VARIANTS, _VARIANTS)
    assert step["family"]["incomplete"] is None and step["family"]["unfinished"] is None
    _signed(step)
