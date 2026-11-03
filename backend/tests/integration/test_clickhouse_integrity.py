"""Integrity-check SQL validated against a real ClickHouse server.

The unit tests mock `_execute`; this confirms the system-table / CHECK TABLE /
detached-parts / gene-index SQL actually parses and runs on ClickHouse and that its verdict
follows the tables' state. It runs on an assembly of its own, so it does not depend on what
other tests stored: before its tables exist the check reports ``missing`` (every table named,
none judged); with the tables made and a variant with a gene stored it reports ``ok`` (every
table checked and passed, the gene index holding the key the annotation index has); and with
the gene index emptied behind the writer's back it reports ``degraded``, naming the drift.

Skipped unless RUN_INTEGRATION=1 (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.tests.integration._datastores import run_closing_stores, small_variant, vep_annotations

pytestmark = pytest.mark.integration


def _record_with_a_gene():
    """A variant annotated as the import parses VEP's CSQ, on one transcript of one gene."""
    return small_variant(
        "1-1000-A-G",
        vep_annotations(
            "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL",
            ["G|missense_variant|MODERATE|ITESTGENE|ENSGITEST|Transcript|ENSTITEST|YES"],
        ),
    )


def test_integrity_check_runs_against_real_clickhouse() -> None:
    from backend.app.core.clickhouse import execute_clickhouse, init_clickhouse_schema
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_variant_storage as cvs

    dataset = f"ITESTINTEGRITY_{uuid4().hex[:8]}"
    database = settings.clickhouse_database

    async def _run() -> dict[str, dict]:
        reports: dict[str, dict] = {}
        try:
            await init_clickhouse_schema()
            reports["missing"] = await cvs.check_clickhouse_variant_integrity(dataset)
            await cvs.ensure_clickhouse_variant_tables(dataset)
            await cvs.insert_small_variant_records(dataset, str(uuid4()), [str(uuid4())], [_record_with_a_gene()])
            reports["ok"] = await cvs.check_clickhouse_variant_integrity(dataset)
            # The gene index emptied behind the writer's back: stale against the annotation index.
            await execute_clickhouse(
                f"ALTER TABLE {database}.`{dataset}/SNV_INDEL/variants/gene_index` DELETE WHERE 1 "
                "SETTINGS mutations_sync = 1"
            )
            reports["degraded"] = await cvs.check_clickhouse_variant_integrity(dataset)
            return reports
        finally:
            cvs._ensured_variant_table_assemblies.discard(dataset)
            tables = await execute_clickhouse(
                "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
                {"database": database, "prefix": f"{dataset}/"},
            )
            for (name,) in tables:
                await execute_clickhouse(f"DROP TABLE IF EXISTS {database}.`{name}` SYNC")

    reports = run_closing_stores(_run)

    missing = reports["missing"]
    assert missing["status"] == "missing", missing
    assert missing["notes"] == ["No variant tables exist for this assembly."]
    assert missing["table_checks"] and all(
        (check["exists"], check["passed"]) == (False, None) for check in missing["table_checks"]
    ), missing["table_checks"]
    assert missing["detached_broken_parts"] == []
    assert missing["gene_index_consistency"]["checked"] is False

    ok = reports["ok"]
    assert ok["status"] == "ok", ok
    assert ok["notes"] == []
    # Every table the check names exists and passed CHECK TABLE, the same tables as before.
    assert [check["name"] for check in ok["table_checks"]] == [check["name"] for check in missing["table_checks"]]
    assert all(
        (check["exists"], check["passed"], check["failed_parts"]) == (True, True, 0) for check in ok["table_checks"]
    ), ok["table_checks"]
    assert ok["detached_broken_parts"] == []
    consistency = ok["gene_index_consistency"]
    assert (consistency["checked"], consistency["consistent"], consistency["drift"]) == (True, True, 0), consistency
    assert consistency["gene_index_keys"] == consistency["annotation_index_gene_keys"] == 1, consistency

    degraded = reports["degraded"]
    assert degraded["status"] == "degraded", degraded
    consistency = degraded["gene_index_consistency"]
    assert (consistency["checked"], consistency["consistent"]) == (True, False), consistency
    assert (consistency["gene_index_keys"], consistency["annotation_index_gene_keys"], consistency["drift"]) == (
        0, 1, -1,
    ), consistency
    assert degraded["notes"] == ["gene_index keys differ from annotation_index — rebuild the small-variant gene index."]
