"""Every embryo's APCAD file and every sample's coverage file are checked before any sample's
track is replaced (E2E, SAFE-3).

A dataset of one track file per sample checked each file as it wrote it: the `apcad` dataset's
file per embryo, and the `coverage` dataset's BED (or per-target table) per sample. A later
sample's file without a record failed the dataset after the earlier samples' tracks had been
replaced. An overwrite's backup then put them back; one without a backup (it could not be
taken) left them replaced, and the family flagged.

Here a family of two embryos (a new id each run) goes through real import jobs
(``POST /api/family-imports``, then the worker's ``run_family_import_job``) into Postgres and
ClickHouse:

* package 1 (cancel): each embryo's APCAD VCF (2 sites) and coverage BED (2 bins) are stored;
* a dry run of package 2 (``POST /api/family-imports/validate``) warns of the second embryo's
  APCAD VCF and coverage BED, which hold nothing after their header (before: no warning);
* package 2 (overwrite; the family's backup fails, so nothing puts the family back): the
  first embryo's new APCAD VCF (3 sites) and coverage BED (3 bins), the second embryo's
  holding nothing after their header. Both datasets fail and the job ends failed; every
  embryo's tracks are as package 1 left them, rows and records (before: the first embryo's
  replaced by its new files), and the family is flagged import-incomplete for both datasets,
  naming the job.

The backup's failure is injected by failing the import's Postgres snapshot, taken first, so
no ClickHouse backup is made either (as test_e2e_import_auto_snv_callsets.py does).
Everything runs in ONE event loop via an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py). Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_APCAD_HEADER = (
    "##fileformat=VCFv4.2\n"
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{embryo}\n"
)


def _apcad_vcf(embryo: str, sites: int) -> str:
    """The embryo's trio-filtered VCF, its column alone: ``sites`` heterozygous sites on chr1."""
    return _APCAD_HEADER.format(embryo=embryo) + "".join(
        f"chr1\t{1000 * (index + 1)}\t.\tA\tG\t50\tPASS\t.\tGT:AD\t0/1:10,{10 + index}\n" for index in range(sites)
    )


def _coverage_bed(bins: int) -> str:
    return "".join(f"chr1\t{index * 1000}\t{(index + 1) * 1000}\t{30 + index}\n" for index in range(bins))


def _write(root: Path, name: str, content: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return name


def _package(root: Path, family_id: str, files: dict[str, tuple[str, str]]) -> Path:
    """A package of the two embryos: each embryo's (APCAD VCF, coverage BED) contents."""
    root.mkdir(parents=True)
    embryos = list(files)
    _write(root, "family.ped", "".join(f"{family_id}\t{embryo}\t0\t0\t2\t1\n" for embryo in embryos))
    lines = ["schema_version: 1", f"family_id: {family_id}", "ped: family.ped", "datasets:"]
    lines += ["  apcad:", "    per_sample:"]
    for embryo, (apcad, _coverage) in files.items():
        lines.append(f"      {embryo}: {{vcf: {_write(root, f'apcad/{embryo}.vcf', apcad)}}}")
    lines += ["  coverage:", "    per_sample:"]
    for embryo, (_apcad, coverage) in files.items():
        name = _write(root, f"coverage/{embryo}.bed", coverage)
        lines.append(f"      {embryo}: {{bed: {name}, content: read_depth}}")
    _write(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


async def _collect(base: Path, mp: pytest.MonkeyPatch) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_interval_tracks import count_interval_track_rows
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sm = get_postgres_sessionmaker()
    async with sm() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    # The family's backup fails while a step says so: an overwrite then has nothing to put
    # the family back from.
    failing = {"backup": False}
    real_snapshot = package_import.snapshot_family_postgres_state

    async def snapshot(*args: Any, **kwargs: Any) -> Any:
        if failing["backup"]:
            raise RuntimeError("e2e: the family's backup could not be taken")
        return await real_snapshot(*args, **kwargs)

    mp.setattr(package_import, "snapshot_family_postgres_state", snapshot)

    family_id = f"FAM_FIRST_{uuid4().hex[:8].upper()}"
    embryos = (f"{family_id}_E1", f"{family_id}_E2")  # sample ids are unique across families
    out: dict[str, Any] = {"family_id": family_id, "embryos": embryos, "steps": {}}
    jobs: list[str] = []

    async def job(job_id: str) -> dict[str, Any]:
        async with sm() as s:
            row = (
                await s.execute(
                    text("SELECT status, error, dataset_summaries FROM family_import_jobs WHERE id = CAST(:j AS uuid)"),
                    {"j": job_id},
                )
            ).mappings().one()
        return {
            "status": row["status"],
            "error": row["error"],
            "datasets": {
                item["dataset_type"]: (item["status"], item.get("message"))
                for item in row["dataset_summaries"] or []
                if item.get("dataset_type") in ("apcad", "coverage")
            },
        }

    async def family() -> dict[str, Any]:
        """Each embryo's APCAD and coverage tracks, (embryo, track type) -> rows: in ClickHouse
        and as Postgres records them; and the family's import-incomplete flag."""
        async with sm() as s:
            members = (
                await s.execute(
                    text(
                        "SELECT s.sample_id, s.id::text AS sample_uuid, f.id::text AS family_uuid, "
                        "f.metadata -> 'import_incomplete' AS flag "
                        "FROM samples s JOIN families f ON f.id = s.family_id WHERE f.family_id = :f"
                    ),
                    {"f": family_id},
                )
            ).mappings().all()
            records = {
                (str(r["sample_id"]), str(r["track_type"])): int(r["rows"])
                for r in (
                    await s.execute(
                        text(
                            "SELECT s.sample_id, t.track_type, SUM(t.row_count) AS rows "
                            "FROM sample_interval_track_sources t JOIN samples s ON s.id = t.sample_id "
                            "WHERE s.sample_id = ANY(:embryos) GROUP BY s.sample_id, t.track_type"
                        ),
                        {"embryos": list(embryos)},
                    )
                ).mappings()
            }
        rows: dict[tuple[str, str], int] = {}
        for member in members:
            for track_type, source in (("apcad", "apcad"), ("coverage", "web")):
                rows[(str(member["sample_id"]), track_type)] = await count_interval_track_rows(
                    _harness.ASSEMBLY,
                    family_uuid=member["family_uuid"],
                    sample_uuid=member["sample_uuid"],
                    track_type=track_type,
                    source=source,
                )
        return {"rows": rows, "records": records, "flag": members[0]["flag"] if members else None}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            async def run_import(name: str, root: Path, conflict_mode: str, *, backup_fails: bool = False) -> None:
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
                worker = f"e2e-first-{uuid4().hex[:8]}"
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
                failing["backup"] = backup_fails
                try:
                    await package_import.run_family_import_job(job_id=job_id, worker_id=worker)
                finally:
                    failing["backup"] = False
                out["steps"][name] = {"job_id": job_id, "job": await job(job_id), **await family()}

            first, second = embryos
            await run_import(
                "1_both_embryos",
                _package(
                    base / "p1" / family_id,
                    family_id,
                    {embryo: (_apcad_vcf(embryo, 2), _coverage_bed(2)) for embryo in embryos},
                ),
                "cancel",
            )
            package_2 = _package(
                base / "p2" / family_id,
                family_id,
                {
                    first: (_apcad_vcf(first, 3), _coverage_bed(3)),
                    # Nothing after the header: a failed embryo's output.
                    second: (_APCAD_HEADER.format(embryo=second), ""),
                },
            )
            resp = await ac.post(
                "/api/family-imports/validate", json={"folder_path": str(package_2), "family_id": family_id}
            )
            assert resp.status_code == 200, resp.text
            out["steps"]["2_dry_run"] = {
                "valid": resp.json()["valid"],
                "warnings": sorted(
                    (warning["code"], warning.get("dataset"), warning.get("sample_id"))
                    for warning in resp.json()["warnings"]
                ),
            }
            await run_import("3_second_embryo_empty", package_2, "overwrite", backup_fails=True)
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
def snap(tmp_path_factory, request) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("tracks_checked_first")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base, mp))


def _tracks(snap: dict[str, Any], apcad: tuple[int, int], coverage: tuple[int, int]) -> dict[tuple[str, str], int]:
    """(embryo, track type) -> rows, the first embryo's and the second's of each."""
    first, second = snap["embryos"]
    return {
        (first, "apcad"): apcad[0],
        (second, "apcad"): apcad[1],
        (first, "coverage"): coverage[0],
        (second, "coverage"): coverage[1],
    }


def test_the_first_package_stores_each_embryos_tracks(snap) -> None:
    step = snap["steps"]["1_both_embryos"]
    assert step["job"]["status"] == "completed", step["job"]["error"]
    assert {dataset: status for dataset, (status, _message) in step["job"]["datasets"].items()} == {
        "apcad": "imported",
        "coverage": "imported",
    }
    stored = _tracks(snap, apcad=(2, 2), coverage=(2, 2))
    assert (step["rows"], step["records"]) == (stored, stored)
    assert step["flag"] is None


def test_a_dry_run_warns_of_the_second_embryos_files_without_a_record(snap) -> None:
    step = snap["steps"]["2_dry_run"]
    second = snap["embryos"][1]
    # Before: no warning; the import then failed both datasets.
    assert step["valid"] is True
    assert ("dataset_file_without_record", "apcad", second) in step["warnings"]
    assert ("dataset_file_without_record", "coverage", second) in step["warnings"]


def test_a_later_embryos_empty_files_fail_the_datasets_with_every_track_as_it_was(snap) -> None:
    step = snap["steps"]["3_second_embryo_empty"]
    assert step["job"]["status"] == "failed"
    assert step["job"]["error"] == "Family package import failed for dataset(s): apcad, coverage"
    # The second embryo's VCF holds no record, and is read as a BED, which holds none either;
    # its coverage BED holds nothing.
    assert step["job"]["datasets"] == {
        "apcad": ("failed", "400: No valid BED records found"),
        "coverage": ("failed", "400: No valid BED records found"),
    }
    # No backup put the family back: every track is as package 1 left it, rows and records.
    # Before: the first embryo's APCAD and coverage were replaced by its new files (3 each),
    # then the second embryo's files failed the datasets.
    stored = _tracks(snap, apcad=(2, 2), coverage=(2, 2))
    assert (step["rows"], step["records"]) == (stored, stored)


def test_the_family_is_flagged_for_both_datasets_naming_the_job(snap) -> None:
    step = snap["steps"]["3_second_embryo_empty"]
    flag = step["flag"]
    assert flag is not None
    assert sorted(flag["failed_datasets"]) == ["apcad", "coverage"]
    assert flag["failed_jobs"] == {"apcad": step["job_id"], "coverage": step["job_id"]}
