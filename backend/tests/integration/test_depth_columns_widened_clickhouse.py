"""A small-variant table created with the narrow depth columns is widened once (SAFE-30), real
ClickHouse.

``SNV_INDEL/entries`` stored a call's read depth (``calls.dp``) and allele depths
(``calls.ad``) as UInt16, and the insert of a whole upload or import failed on a depth above
65,535, which a mitochondrial or amplicon call reaches. The table is now created with UInt32,
and a table created before is altered the first time this version ensures it. Here such a
table, holding a call, is ensured under an assembly of its own: both columns are UInt32
afterwards, the call reads back as it was stored, before and after its parts are merged, a
call of 120,000 reads is written and read back, the same ALTER issued again by a second
process that read the narrow types first changes nothing, and a new process's ensure, finding
the wide types, alters nothing.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def _call(sample: str, *, dp: int, ad: list[int]) -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall

    return SmallVariantCall(sample=sample, gt="0/1", gq=99, dp=dp, af=[], ad=ad, ps=None)


def _record(pos: int, call: Any) -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantRecord

    return SmallVariantRecord(
        variant_key=None,
        variant_id=f"M-{pos}-A-G",
        chr="M",
        start=pos,
        end=pos,
        ref="A",
        alt="G",
        source="mito",
        rsid=None,
        filters=[],
        gene_symbols=[],
        annotations=[],
        calls=[call],
    )


async def _exercise(dataset: str) -> dict[str, Any]:
    from backend.app.core.clickhouse import close_clickhouse_client, execute_clickhouse, init_clickhouse_schema
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_variant_storage as cvs

    database = settings.clickhouse_database
    entries = f"{database}.`{dataset}/SNV_INDEL/entries`"
    out: dict[str, Any] = {}

    async def column_types() -> dict[str, str]:
        rows = await execute_clickhouse(
            "SELECT name, type FROM system.columns "
            "WHERE database = %(database)s AND table = %(table)s AND name IN ('calls.dp', 'calls.ad')",
            {"database": database, "table": f"{dataset}/SNV_INDEL/entries"},
        )
        return {str(name): str(column_type) for name, column_type in rows}

    async def stored_depths() -> dict[int, tuple[list[Any], list[Any]]]:
        rows = await execute_clickhouse(
            f"SELECT pos, `calls.dp`, `calls.ad` FROM {entries} WHERE sign = 1 ORDER BY pos"
        )
        return {int(pos): (list(dps), [list(ad) for ad in ads]) for pos, dps, ads in rows}

    issued: list[str] = []
    execute = cvs._execute

    async def recording(query: str, params: Any = None, data: Any = None) -> Any:
        issued.append(" ".join(query.split()))
        return await execute(query, params, data)

    mp = pytest.MonkeyPatch()
    try:
        await init_clickhouse_schema()
        await cvs.ensure_clickhouse_variant_tables("GRCh38")
        # The table as an earlier version created it: this version's, with the old types.
        await execute_clickhouse(f"CREATE TABLE {entries} AS {database}.`GRCh38/SNV_INDEL/entries`")
        await execute_clickhouse(
            f"ALTER TABLE {entries} "
            "MODIFY COLUMN `calls.dp` Array(Nullable(UInt16)), "
            "MODIFY COLUMN `calls.ad` Array(Array(Nullable(UInt16)))"
        )
        out["types_before"] = await column_types()
        stored = cvs.small_variant_record_entries(
            dataset, "family-old", ["project-old"], [_record(73, _call("S1", dp=500, ad=[250, 250]))]
        )
        columns = ", ".join(f"`{column}`" for column in cvs.SMALL_VARIANT_ENTRY_COLUMNS)
        await execute_clickhouse(
            f"INSERT INTO {entries} ({columns}) VALUES",
            [tuple(entry[column] for column in cvs.SMALL_VARIANT_ENTRY_COLUMNS) for entry in stored],
        )

        mp.setattr(cvs, "_execute", recording)
        await cvs.ensure_clickhouse_variant_tables(dataset)
        out["alters"] = [query for query in issued if "MODIFY COLUMN" in query]
        out["types_after"] = await column_types()
        out["stored_after_widening"] = await stored_depths()

        await cvs.insert_small_variant_records(
            dataset,
            "family-deep",
            ["project-deep"],
            [_record(3243, _call("S2", dp=120_000, ad=[60_000, 60_000]))],
        )
        await execute_clickhouse(f"OPTIMIZE TABLE {entries} FINAL")
        out["stored_after_deep_call"] = await stored_depths()

        # A second process that read the narrow types before the first altered the table
        # issues the same ALTER: it changes nothing.
        await execute_clickhouse(out["alters"][0])
        out["types_after_second_alter"] = await column_types()
        out["stored_after_second_alter"] = await stored_depths()

        # A new process: nothing ensured yet, and the table already wide.
        issued.clear()
        cvs._ensured_variant_table_assemblies.discard(dataset)
        await cvs.ensure_clickhouse_variant_tables(dataset)
        out["alters_again"] = [query for query in issued if "MODIFY COLUMN" in query]
        out["unfinished_mutations"] = (
            await execute_clickhouse(
                "SELECT count() FROM system.mutations "
                "WHERE database = %(database)s AND table = %(table)s AND NOT is_done",
                {"database": database, "table": f"{dataset}/SNV_INDEL/entries"},
            )
        )[0][0]
    finally:
        mp.undo()
        cvs._ensured_variant_table_assemblies.discard(dataset)
        tables = await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
            {"database": database, "prefix": f"{dataset}/"},
        )
        for (name,) in tables:
            await execute_clickhouse(f"DROP TABLE IF EXISTS {database}.`{name}` SYNC")
        await close_clickhouse_client()
    return out


@pytest.fixture(scope="module")
def run() -> dict[str, Any]:
    return asyncio.run(_exercise(f"SAFE30_{uuid4().hex[:8]}"))


def test_the_table_had_the_narrow_depth_columns(run) -> None:
    assert run["types_before"] == {
        "calls.dp": "Array(Nullable(UInt16))",
        "calls.ad": "Array(Array(Nullable(UInt16)))",
    }


def test_the_first_ensure_widens_both_depth_columns(run) -> None:
    assert len(run["alters"]) == 1, run["alters"]
    assert run["types_after"] == {
        "calls.dp": "Array(Nullable(UInt32))",
        "calls.ad": "Array(Array(Nullable(UInt32)))",
    }


def test_a_stored_call_keeps_its_depths(run) -> None:
    assert run["stored_after_widening"] == {73: ([500], [[250, 250]])}


def test_a_deep_call_is_written_and_read_back_after_the_widening(run) -> None:
    assert run["stored_after_deep_call"] == {
        73: ([500], [[250, 250]]),
        3243: ([120_000], [[60_000, 60_000]]),
    }


def test_the_same_alter_from_a_second_process_changes_nothing(run) -> None:
    assert run["types_after_second_alter"] == run["types_after"]
    assert run["stored_after_second_alter"] == run["stored_after_deep_call"]


def test_a_table_already_wide_is_not_altered_again(run) -> None:
    assert run["alters_again"] == []
    assert run["unfinished_mutations"] == 0
