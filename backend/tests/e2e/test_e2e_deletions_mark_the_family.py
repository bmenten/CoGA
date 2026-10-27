"""A deletion that fails mid-way leaves the family marked, and a refused SNV file costs no
callset (SAFE-31, SAFE-32), E2E.

ClickHouse has no transactions. Into real Postgres and ClickHouse:

* a trio (a new family id each run) goes through the PED, small-variant, structural-variant
  and BED upload routes; the admin then deletes the child with its data, and the rewrite of
  the family's SVs fails once (an insert error, simulated) after the rewrite of its small
  variants removed the child's calls. The SVs are put back and the request answers 500; the
  rollback brings the child back without its coverage track and its calls. Before, nothing
  marked the family, and its report signed out as complete. Now the deletion's mark
  (``deletion-…`` in ``import_unfinished``, written before its first ClickHouse write)
  stays, listing exactly the child's data -- its coverage track, and its part of the direct
  calls -- and sign-out is refused (409) with a message that names the deletion. Deleting
  the child again completes, and strikes the child's items off the mark, which goes;
* a one-sample family's direct calls are imported, then imported again with overwrite while
  its backup cannot be taken, from a package whose VEP table has no header: the loader
  refuses the table before it replaces anything, and the import fails. Before, the SNV
  importer's clean-up deleted the direct calls; now they stay, and the flag names the SNV
  dataset for the import that failed.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why); app exceptions come back as a 500 response.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ACK = "e2e: the import gate is under test"
_OTHER_GATES = {
    "acknowledge_drift": True,
    "drift_acknowledgement_reason": _ACK,
    "acknowledge_qc": True,
    "qc_acknowledgement_reason": _ACK,
}
_SNIFFLES_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    "##contig=<ID=1,length=248956422>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
)
_DEL_30000 = "1\t30000\tS.DEL.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT\t{gt}\n"
_SNIFFLES = {
    "child": _SNIFFLES_HEADER + _DEL_30000.format(gt="0/1"),
    "father": _SNIFFLES_HEADER + _DEL_30000.format(gt="1/1"),
}
_VCF_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{father}\t{mother}\t{child}\n"


def _calls(count: int) -> str:
    return "".join(
        f"1\t{10_000 + index * 100}\t.\tA\tG\t50\tPASS\t.\tGT:GQ:DP\t0/1:40:30\t0/0:40:31\t0/1:40:29\n"
        for index in range(count)
    )


async def _deletion(family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import clickhouse_variant_storage
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_rows
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        fetch_family_small_variant_entries,
        fetch_family_structural_variant_rows,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    names = {role: f"{role.upper()}_{family_id}" for role in ("father", "mother", "child")}
    ped = (
        f"{family_id}\t{names['father']}\t0\t0\t1\t1\n"
        f"{family_id}\t{names['mother']}\t0\t0\t2\t1\n"
        f"{family_id}\t{names['child']}\t{names['father']}\t{names['mother']}\t2\t2\n"
    )
    roles = {name: role for role, name in names.items()}
    out: dict[str, Any] = {"steps": {}, "names": names}

    async def state() -> dict[str, Any]:
        """The family's import state, its members, its direct calls (each row's members), its
        Sniffles calls (each SV's members) and the child's coverage rows."""
        async with sessionmaker() as session:
            family = (
                await session.execute(
                    text(
                        "SELECT id::text AS family_uuid, metadata -> 'import_unfinished' AS unfinished, "
                        "metadata -> 'import_incomplete' AS flag FROM families WHERE family_id = :f"
                    ),
                    {"f": family_id},
                )
            ).mappings().one()
            members = {
                str(row["sample_id"]): str(row["id"])
                for row in (
                    await session.execute(
                        text("SELECT id::text AS id, sample_id FROM samples WHERE family_id = CAST(:u AS uuid)"),
                        {"u": family["family_uuid"]},
                    )
                ).mappings()
            }
        uuid_role = {uuid: roles[name] for name, uuid in members.items()}
        entries = await fetch_family_small_variant_entries(_harness.ASSEMBLY, family["family_uuid"], source="clair3")
        rows = await fetch_family_structural_variant_rows(_harness.ASSEMBLY, family["family_uuid"], source="sniffles")
        child_uuid = out.setdefault("child_uuid", members.get(names["child"]))
        return {
            "family_uuid": family["family_uuid"],
            "members": sorted(roles[name] for name in members),
            "clair3": sorted(
                sorted(uuid_role.get(str(sample), roles.get(str(sample), str(sample))) for sample in entry["calls.sampleId"])
                for entry in entries
            ),
            "sniffles": sorted(
                sorted(uuid_role.get(call.sample, roles.get(call.sample, call.sample)) for call in row.record.calls)
                for row in rows
            ),
            "child_coverage": await count_interval_track_rows(
                _harness.ASSEMBLY,
                family_uuid=family["family_uuid"],
                sample_uuid=child_uuid,
                track_type="coverage",
            )
            if child_uuid
            else 0,
            "unfinished": family["unfinished"],
            "flag": family["flag"],
        }

    real_insert = clickhouse_variant_storage.insert_structural_variant_records

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

        async def step(name: str, response) -> None:
            out["steps"][name] = {"status": response.status_code, "text": response.text, **await state()}

        await step(
            "ped",
            await ac.post(
                "/api/ped/upload",
                params={"project_id": project_id, "overwrite": "false"},
                files={"file": (f"{family_id}.ped", ped.encode(), "text/plain")},
            ),
        )
        header = _VCF_HEADER.format(**names)
        await step(
            "clair3",
            await ac.post(
                f"/api/families/{family_id}/small-variants/upload",
                params={"overwrite": "false", "source_format": "clair3"},
                files={"file": (f"{family_id}.vcf", (header + _calls(4)).encode(), "text/plain")},
            ),
        )
        for role in ("child", "father"):
            await step(
                f"sv_{role}",
                await ac.post(
                    f"/api/structural-variants/upload/{names[role]}",
                    params={"overwrite": "false", "source_format": "sniffles"},
                    files={"file": (f"{role}.sniffles.vcf", _SNIFFLES[role].encode(), "text/plain")},
                ),
            )
        await step(
            "bed",
            await ac.post(
                f"/api/bed/upload/{names['child']}/coverage",
                params={"overwrite": "false", "content": "read_depth"},
                files={"file": ("coverage.bed", b"1\t0\t1000\tbin1\t31.0\n1\t1000\t2000\tbin2\t29.5\n", "text/plain")},
            ),
        )

        # The admin deletes the child with its data; the rewrite of the family's SVs fails once
        # after the rewrite of its small variants (the restore's insert works).
        failures = {"left": 1}

        async def insert_failing_once(*args: Any, **kwargs: Any) -> None:
            if failures["left"]:
                failures["left"] -= 1
                raise RuntimeError("ClickHouse insert failed")
            await real_insert(*args, **kwargs)

        mp = pytest.MonkeyPatch()
        mp.setattr(clickhouse_variant_storage, "insert_structural_variant_records", insert_failing_once)
        try:
            response = await ac.delete(f"/api/admin/samples/{names['child']}", params={"confirm": "true"})
        finally:
            mp.undo()
        await step("delete_fails", response)
        await step("signout_marked", await _harness.sign_out(ac, f"/api/families/{family_id}/report/sign-out", _OTHER_GATES))
        page = await ac.get(f"/api/families/{family_id}")
        out["family_page"] = {"status": page.status_code, "json": page.json()}

        await step("delete_again", await ac.delete(f"/api/admin/samples/{names['child']}", params={"confirm": "true"}))
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The uploads keep a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("deletions_mark_the_family"))
    try:
        return _harness.run_async(lambda: _deletion(f"DELMARK_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _mark(step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    [(key, entry)] = (step["unfinished"] or {}).items()
    return key, entry


def test_the_trio_and_its_data_are_uploaded(run) -> None:
    for name in ("ped", "clair3", "sv_child", "sv_father", "bed"):
        assert run["steps"][name]["status"] == 200, run["steps"][name]["text"]
    stored = run["steps"]["bed"]
    assert stored["clair3"] == [["child", "father", "mother"]] * 4
    assert stored["sniffles"] == [["child", "father"]]
    assert stored["child_coverage"] == 2 and stored["unfinished"] is None


def test_a_sample_deletion_that_fails_mid_way_leaves_the_family_marked_for_the_samples_data(run) -> None:
    failed = run["steps"]["delete_fails"]
    assert failed["status"] == 500
    # The child is back (the rollback), without its coverage rows and its direct calls,
    # which ClickHouse does not roll back; its SV calls were put back.
    assert failed["members"] == ["child", "father", "mother"]
    assert failed["child_coverage"] == 0
    assert failed["clair3"] == [["father", "mother"]] * 4
    assert failed["sniffles"] == [["child", "father"]]
    # Before: nothing marked the family. Now the deletion's mark lists exactly the child's
    # data: its coverage track, and its part of the direct calls.
    key, mark = _mark(failed)
    assert key.startswith("deletion-")
    assert (mark["job_id"], mark["datasets"], mark["finished_datasets"]) == (None, ["bed_coverage", "snv"], [])
    child = run["names"]["child"]
    assert mark["whole_family"] == [
        {"dataset": "bed_coverage", "scope": {"source": None, "samples": [child], "content": "read_depth"}},
        {"dataset": "snv", "scope": {"source": "clair3", "samples": None}, "sample": child},
    ]
    # Every family page reads it.
    page = run["family_page"]
    assert page["status"] == 200 and page["json"]["metadata"]["import_unfinished"] == failed["unfinished"]


def test_the_mark_refuses_sign_out_and_names_the_deletion(run) -> None:
    refused = run["steps"]["signout_marked"]
    assert refused["status"] == 409, refused["text"]
    detail = json.loads(refused["text"])["detail"]
    assert detail["gate"] == "import_incomplete"
    key, _entry = _mark(run["steps"]["delete_fails"])
    assert list(detail["import_unfinished"]) == [key]
    assert "a deletion of this family's data (" in detail["message"]
    assert "did not finish, so bed_coverage, snv may be partly written or missing" in detail["message"]


def test_the_deletion_again_completes_and_strikes_the_samples_items_off_the_mark(run) -> None:
    again = run["steps"]["delete_again"]
    assert again["status"] == 200, again["text"]
    assert again["members"] == ["father", "mother"]
    assert again["clair3"] == [["father", "mother"]] * 4
    assert again["sniffles"] == [["father"]]
    # Nothing of a sample that is gone can be missing: its items went, and the mark with them.
    assert again["unfinished"] is None and again["flag"] is None


# --- a refused SNV file costs no callset (SAFE-32) -------------------------------------------


_SNV_VARIANTS = 4


def _write_package(root: Path, family_id: str, *, annotation: str | None) -> Path:
    """A one-sample package of direct calls; with ``annotation``, a VEP table of that text."""
    sample = f"{family_id}_S1"
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t1\t2\n", encoding="utf-8")
    (root / "snv").mkdir(exist_ok=True)
    (root / "snv" / "family.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        + "".join(f"1\t{100 + 100 * i}\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n" for i in range(_SNV_VARIANTS)),
        encoding="utf-8",
    )
    snv = "  snv:\n    family_vcf: snv/family.vcf\n    source_format: clair3\n"
    if annotation is not None:
        (root / "snv" / "family.vep.tsv").write_text(annotation, encoding="utf-8")
        snv += "    annotation_tsv: snv/family.vep.tsv\n"
    (root / "manifest.yaml").write_text(
        f"schema_version: 1\nfamily_id: {family_id}\nped: family.ped\ndatasets:\n{snv}",
        encoding="utf-8",
    )
    return root


async def _refused_overwrite(base: Path, mp: pytest.MonkeyPatch) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_variant_storage import (
        count_family_small_variants,
        ensure_clickhouse_variant_tables,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # The family's backup cannot be taken: an overwrite then has nothing to put it back from.
    failing = {"backup": False}
    real_snapshot = package_import.snapshot_family_postgres_state

    async def snapshot(*args, **kwargs):
        if failing["backup"]:
            raise RuntimeError("e2e: the family's backup could not be taken")
        return await real_snapshot(*args, **kwargs)

    mp.setattr(package_import, "snapshot_family_postgres_state", snapshot)

    family_id = f"FAM_REFUSED_{uuid4().hex[:8].upper()}"
    out: dict[str, Any] = {"family_id": family_id, "steps": {}}
    jobs: list[str] = []

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text("SELECT status, error, dataset_summaries FROM family_import_jobs WHERE id = CAST(:j AS uuid)"),
                    {"j": job_id},
                )
            ).mappings().one()
        summaries = row["dataset_summaries"] or []
        return {
            "status": row["status"],
            "error": row["error"],
            "snv": next((item for item in summaries if item.get("dataset_type") == "snv"), None),
        }

    async def family() -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text("SELECT id::text AS uuid, metadata FROM families WHERE family_id = :f"),
                    {"f": family_id},
                )
            ).mappings().one()
        return {
            "flag": (row["metadata"] or {}).get("import_incomplete"),
            "clair3": await count_family_small_variants(
                _harness.ASSEMBLY, row["uuid"], project_ids=[project_id], source="clair3"
            ),
        }

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def run_import(name: str, conflict_mode: str, *, annotation: str | None, fails: bool) -> None:
                root = _write_package(base / name / family_id, family_id, annotation=annotation)
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
                worker = f"e2e-refused-{uuid4().hex[:8]}"
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
                failing["backup"] = fails
                try:
                    await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
                finally:
                    failing["backup"] = False
                out["steps"][name] = {"job_id": job_id, "job": await job(job_id), "family": await family()}

            await run_import("1_direct", "cancel", annotation=None, fails=False)
            # A VEP table with its leading block and no header row: refused before anything is
            # replaced.
            await run_import(
                "2_refused",
                "overwrite",
                annotation="## ENSEMBL VARIANT EFFECT PREDICTOR v110\n## Output produced at 2026-10-20\n",
                fails=True,
            )
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
def refused(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("refused_snv_overwrite")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _refused_overwrite(base, mp))


def test_an_snv_overwrite_whose_file_is_refused_without_a_backup_keeps_the_callset(refused) -> None:
    first, step = refused["steps"]["1_direct"], refused["steps"]["2_refused"]
    assert first["job"]["status"] == "completed", first["job"]["error"]
    assert first["family"]["clair3"] == _SNV_VARIANTS
    assert step["job"]["status"] == "failed"
    assert step["job"]["snv"]["status"] == "failed"
    assert "missing a header row" in step["job"]["snv"]["message"]
    # Refused before the loader replaced anything: the direct calls stay (before: the
    # importer's clean-up deleted them, with no backup to put them back from).
    assert step["family"]["clair3"] == _SNV_VARIANTS
    # The import failed for the dataset, and says so.
    flag = step["family"]["flag"]
    assert flag["failed_datasets"] == ["snv"]
    assert flag["failed_scopes"] == {
        "snv": [{"scope": {"source": "clair3", "samples": None}, "job_id": step["job_id"]}]
    }
