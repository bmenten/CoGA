"""A Paraphase copy number that no Postgres ``integer`` holds fails its dataset cleanly (DATA-7), E2E.

The Paraphase copy numbers (``total_cn``, ``gene_cn``, ``highest_total_cn``) are ``integer``
columns of ``sample_paraphase_results``, filled from each sample's JSON. One beyond them
failed the insert with a database error, after the sample's results were deleted: the
dataset's message was the driver's error, with the statement and its parameters. Such a JSON
is now refused as a TRGT value that cannot be read as a number is (#985): the dataset fails
before any sample's results are replaced, with a message that names the field and the gene.

Here one sample's family (a new id each run) goes through the package import into real
Postgres and ClickHouse:

* package 1 (cancel): a Paraphase JSON with GBA's copy numbers (2) is stored;
* package 2 (overwrite): GBA's ``total_cn`` is 2,147,483,648, one beyond ``integer``: the
  dataset fails with the refusal, and GBA's stored result is as it was (before: the driver's
  error, "value out of int32 range", with the SQL);
* package 3 (overwrite): GBA's ``total_cn`` is 2,147,483,647, the column's largest: stored.

Everything runs in ONE event loop (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_INTEGER_MAX = 2**31 - 1


def _package(root: Path, family_id: str, sample: str, total_cn: int) -> Path:
    """The one-sample family's package with a Paraphase JSON whose GBA ``total_cn`` is
    ``total_cn``."""
    (root / "paraphase").mkdir(parents=True)
    (root / "family.ped").write_text(f"{family_id}\t{sample}\t0\t0\t2\t2\n", encoding="utf-8")
    result = {
        "total_cn": total_cn,
        "gene_cn": 2,
        "highest_total_cn": 2,
        "sample_sex": "female",
        "genome_depth": 30.5,
        "final_haplotypes": {"h1": "GBA_hap1"},
    }
    (root / "paraphase" / f"{sample}.json").write_text(json.dumps({"GBA": result}), encoding="utf-8")
    (root / "manifest.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                f"family_id: {family_id}",
                "ped: family.ped",
                "datasets:",
                "  paraphase:",
                "    per_sample:",
                f"      {sample}: {{json: paraphase/{sample}.json}}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return root


async def _exercise(base: Path, family_id: str) -> dict[str, Any]:
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import family_package_import as package_import
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
            "datasets": {d.dataset_type: (d.status, d.message) for d in result.datasets},
        }

    async def results() -> dict[str, list[Any]]:
        """The sample's stored Paraphase results: gene -> its three copy numbers."""
        async with sessionmaker() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT pr.gene_symbol, pr.total_cn, pr.gene_cn, pr.highest_total_cn "
                        "FROM sample_paraphase_results pr JOIN samples s ON s.id = pr.sample_id "
                        "WHERE s.sample_id = :s"
                    ),
                    {"s": sample},
                )
            ).mappings().all()
        return {str(r["gene_symbol"]): [r["total_cn"], r["gene_cn"], r["highest_total_cn"]] for r in rows}

    for step, conflict_mode, total_cn in (
        ("package_1", "cancel", 2),
        ("package_2", "overwrite", _INTEGER_MAX + 1),
        ("package_3", "overwrite", _INTEGER_MAX),
    ):
        root = _package(base / step / family_id, family_id, sample, total_cn)
        out["steps"][step] = {"import": await run_import(root, conflict_mode), "results": await results()}
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("paraphase_copy_numbers")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base)])
    try:
        return _harness.run_async(lambda: _exercise(base, f"PARA_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_the_first_package_stores_the_copy_numbers(run) -> None:
    step = run["steps"]["package_1"]
    assert step["import"]["completed"] is True, step["import"]
    assert step["import"]["datasets"]["paraphase"][0] == "imported"
    assert step["results"] == {"GBA": [2, 2, 2]}


def test_a_copy_number_no_integer_holds_fails_the_dataset_with_the_results_as_they_were(run) -> None:
    step = run["steps"]["package_2"]
    imported = step["import"]
    assert imported["completed"] is False, imported
    status, message = imported["datasets"]["paraphase"]
    # Before: the driver's error, "invalid input for query argument … (value out of int32
    # range)", with the statement and its parameters.
    assert (status, message) == (
        "failed",
        f"Paraphase JSON for {run['sample']} holds a value that cannot be read where a number is expected "
        "(total_cn of GBA); nothing was stored",
    )
    assert step["results"] == {"GBA": [2, 2, 2]}


def test_a_copy_number_at_the_columns_largest_is_stored(run) -> None:
    step = run["steps"]["package_3"]
    assert step["import"]["completed"] is True, step["import"]
    assert step["results"] == {"GBA": [_INTEGER_MAX, 2, 2]}
