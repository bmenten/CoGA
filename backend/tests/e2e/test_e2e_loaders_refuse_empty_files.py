"""A file without a record replaces nothing (SAFE-29), E2E.

Most loaders replace what they load: they delete the stored data of their source, then read
the file and write its records. A file without a record (a header alone, a file cut short
before its first record) was read as a result with nothing in it: a TRGT upload with
overwrite answered 200 with the sample's calls gone, and a package dataset deleted the
sample's track, stored none and ended ``imported``, so the import completed and nothing
marked the family; an ``update`` imported nothing and counted the dataset as imported.

Here one sample's family (a new id each run) goes through the package import and the TRGT
upload route into real Postgres and ClickHouse:

* package 1 (cancel): WisecondorX bins (3) and segments (1) and the sample's TRGT VCF (1 call)
  are stored;
* package 2 (overwrite): a WisecondorX bins file with its header alone fails the dataset and
  the import, and the bins stay, rows and record (before: the import completed with the bins
  gone);
* a TRGT VCF with its header alone, uploaded with overwrite, is refused (400) and the call
  stays (before: 200 and the call gone);
* package 3 (update): a QDNAseq bins file with its header alone, for a sample without QDNAseq
  bins, fails the dataset, and the family is flagged import-incomplete for it (before: the
  dataset imported nothing and read as imported, the family unflagged).

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

_WCX_HEADER = "chr\tstart\tend\tratio\tzscore\n"
_TRGT_HEADER = (
    "##fileformat=VCFv4.2\n##source=TRGT\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
)
_TRGT_CALL = "chr4\t3074876\t.\tA\t<STR>\t.\tPASS\tEND=3074933;TRID=HD_HTT;MOTIFS=CAG\tGT:MC\t0/1:18_40\n"


def _write(root: Path, name: str, text: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _package(root: Path, family_id: str, sample: str, datasets: dict[str, dict[str, str]]) -> Path:
    """A package of the one-sample family with ``datasets``: dataset -> {role: (name, text)}."""
    root.mkdir(parents=True)
    _write(root, "family.ped", f"{family_id}\t{sample}\t0\t0\t2\t2\n")
    lines = ["schema_version: 1", f"family_id: {family_id}", "ped: family.ped", "datasets:"]
    for dataset, files in datasets.items():
        entry = ", ".join(f"{role}: {_write(root, name, text)}" for role, (name, text) in files.items())
        lines += [f"  {dataset}:", "    per_sample:", f"      {sample}: {{{entry}}}"]
    _write(root, "manifest.yaml", "\n".join(lines) + "\n")
    return root


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

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
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    sample = f"{family_id}_S1"  # sample ids are unique across families
    out: dict[str, Any] = {"sample": sample, "steps": {}}
    ids: dict[str, str] = {}

    async def run_import(root: Path, conflict_mode: str) -> dict[str, Any]:
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

    async def state() -> dict[str, Any]:
        """The sample's WisecondorX and QDNAseq bins (rows in ClickHouse, rows Postgres records
        of them), its TRGT calls, and the family's import-incomplete flag."""
        async with sessionmaker() as session:
            if not ids:
                row = (
                    await session.execute(
                        text(
                            "SELECT s.id::text AS sample_uuid, s.family_id::text AS family_uuid "
                            "FROM samples s WHERE s.sample_id = :s"
                        ),
                        {"s": sample},
                    )
                ).mappings().first()
                if row is None:  # the import stored no family: its result says why
                    return {}
                ids.update(row)
            records = {
                str(r["source"]): int(r["rows"])
                for r in (
                    await session.execute(
                        text(
                            "SELECT source, SUM(row_count) AS rows FROM sample_interval_track_sources "
                            "WHERE sample_id = CAST(:s AS uuid) AND track_type = 'coverage' GROUP BY source"
                        ),
                        {"s": ids["sample_uuid"]},
                    )
                ).mappings()
            }
            calls = int(
                (
                    await session.execute(
                        text("SELECT COUNT(*) FROM repeat_expansions WHERE sample_id = CAST(:s AS uuid)"),
                        {"s": ids["sample_uuid"]},
                    )
                ).scalar_one()
            )
            flag = (
                await session.execute(
                    text("SELECT metadata -> 'import_incomplete' FROM families WHERE id = CAST(:f AS uuid)"),
                    {"f": ids["family_uuid"]},
                )
            ).scalar_one()
        rows = {
            source: await count_interval_track_rows(
                _harness.ASSEMBLY,
                family_uuid=ids["family_uuid"],
                sample_uuid=ids["sample_uuid"],
                track_type="coverage",
                source=source,
            )
            for source in ("wisecondorx", "qdnaseq")
        }
        return {"rows": rows, "records": records, "trgt_calls": calls, "flag": flag}

    bins = "".join(f"1\t{i * 1000}\t{(i + 1) * 1000}\t-0.1\t0.2\n" for i in range(3))
    segment = "1\t0\t3000\t-0.1\t0.2\n"
    out["steps"]["package_1"] = {
        "import": await run_import(
            _package(
                base / "p1" / family_id,
                family_id,
                sample,
                {
                    "wisecondorx": {
                        "bins": (f"wcx/{sample}_bins.bed", _WCX_HEADER + bins),
                        "segments": (f"wcx/{sample}_segments.bed", _WCX_HEADER + segment),
                    },
                    "repeats_trgt": {
                        "file": (f"trgt/{sample}.vcf", _TRGT_HEADER.format(column=f"{sample}_sort") + _TRGT_CALL)
                    },
                },
            ),
            "cancel",
        ),
        **await state(),
    }
    out["steps"]["package_2"] = {
        "import": await run_import(
            _package(
                base / "p2" / family_id,
                family_id,
                sample,
                {
                    "wisecondorx": {
                        "bins": (f"wcx/{sample}_bins.bed", _WCX_HEADER),
                        "segments": (f"wcx/{sample}_segments.bed", _WCX_HEADER + segment),
                    }
                },
            ),
            "overwrite",
        ),
        **await state(),
    }

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        response = await ac.post(
            f"/api/repeat-expansions/upload/{sample}",
            params={"overwrite": "true"},
            files={"file": (f"{sample}.vcf", _TRGT_HEADER.format(column=f"{sample}_sort").encode(), "text/plain")},
        )
        out["steps"]["trgt_upload"] = {"status": response.status_code, "json": response.json(), **await state()}

    out["steps"]["package_3"] = {
        "import": await run_import(
            _package(
                base / "p3" / family_id,
                family_id,
                sample,
                {"qdnaseq": {"bins": (f"qdnaseq/{sample}_bins.csv", "chromosome,start,end,ratio\n")}},
            ),
            "update",
        ),
        **await state(),
    }
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("empty_files")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"EMPTY_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_the_first_package_stores_the_bins_and_the_trgt_call(run) -> None:
    step = run["steps"]["package_1"]
    assert step["import"]["completed"] is True, step["import"]
    assert (step["rows"], step["records"], step["trgt_calls"]) == (
        {"wisecondorx": 3, "qdnaseq": 0},
        {"wisecondorx": 3},
        1,
    )


def test_an_overwrite_with_an_empty_bins_file_fails_and_keeps_the_bins(run) -> None:
    step = run["steps"]["package_2"]
    imported = step["import"]
    # Before: the import completed, the dataset imported, and the bins were gone.
    assert imported["completed"] is False, imported
    status, message = imported["datasets"]["wisecondorx"]
    assert status == "failed"
    assert f"{run['sample']}_bins.bed holds no record of the coverage track" in message
    assert (step["rows"], step["records"]) == ({"wisecondorx": 3, "qdnaseq": 0}, {"wisecondorx": 3})


def test_a_trgt_upload_without_a_record_is_refused_and_keeps_the_call(run) -> None:
    step = run["steps"]["trgt_upload"]
    # Before: 200, and the sample's call gone.
    assert step["status"] == 400, step["json"]
    assert step["json"]["detail"] == "No valid TRGT records found"
    assert step["trgt_calls"] == 1


def test_an_update_with_an_empty_bins_file_fails_and_flags_the_family(run) -> None:
    step = run["steps"]["package_3"]
    imported = step["import"]
    # Before: the dataset imported nothing and read as imported, and nothing flagged the family.
    assert imported["completed"] is False, imported
    assert imported["datasets"]["qdnaseq"][0] == "failed"
    assert step["rows"] == {"wisecondorx": 3, "qdnaseq": 0}
    assert step["flag"]["failed_datasets"] == ["qdnaseq"]
