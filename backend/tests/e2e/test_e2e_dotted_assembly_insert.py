"""Inserting into the tables of an assembly whose name has a dot, on real ClickHouse (E2E).

The T2T assembly ("T2T CHM13v2.0") keeps its dot in its ClickHouse dataset key
(``T2T_CHM13v2.0``). clickhouse-connect takes a table name holding a dot for an already
qualified ``database.table`` and sent it unquoted, so every insert into such an assembly's
tables was a syntax error: a T2T family's import lost every ClickHouse dataset (small
variants, SVs, interval tracks), while the GRCh38 tables, whose names hold no dot, worked.

Rows go in here through the app's own insert path (``execute_clickhouse`` with an
``INSERT … VALUES`` and the rows apart), into the small-variant entries and the interval
tables of an assembly no other test uses, and are read back. The tables are dropped at
the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "E2E DOTTED v2.0"  # dataset key E2E_DOTTED_v2.0
_FAMILY = "e2e-dotted-assembly"


async def _exercise() -> dict[str, int]:
    from backend.app.core.clickhouse import clickhouse_dataset_key, execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_variant_storage as cvs
    from backend.app.services import clickhouse_interval_tracks as cit
    from backend.app.services.clickhouse_interval_tracks import (
        _interval_table_name,
        ensure_clickhouse_interval_table,
    )
    from backend.app.services.clickhouse_variant_ids import _small_table_name

    dataset = clickhouse_dataset_key(_ASSEMBLY)
    assert "." in dataset  # the case under test
    await cvs.ensure_clickhouse_variant_tables(_ASSEMBLY)
    await ensure_clickhouse_interval_table(_ASSEMBLY)
    entries = _small_table_name(_ASSEMBLY, "entries")
    intervals = _interval_table_name(_ASSEMBLY)
    try:
        await execute_clickhouse(
            f"INSERT INTO {entries} (key, family_guid, variantId, sign) VALUES",
            [(1, _FAMILY, "7-1000-A-G", 1), (2, _FAMILY, "7-2000-C-T", 1)],
        )
        await execute_clickhouse(
            f"INSERT INTO {intervals} (family_guid, sample_guid, track_type, source, filename, chrom, start, end, metadata_json) VALUES",
            [(_FAMILY, "sample-1", "coverage", "e2e", "e2e.bed", "7", 1000, 2000, "{}")],
        )
        [(small,)] = await execute_clickhouse(
            f"SELECT count() FROM {entries} WHERE family_guid = %(family)s", {"family": _FAMILY}
        )
        [(interval,)] = await execute_clickhouse(
            f"SELECT count() FROM {intervals} WHERE family_guid = %(family)s", {"family": _FAMILY}
        )
        return {"small": int(small), "interval": int(interval)}
    finally:
        for (name,) in await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(db)s AND name LIKE %(prefix)s",
            {"db": settings.clickhouse_database, "prefix": f"{dataset}/%"},
        ):
            await execute_clickhouse(f"DROP TABLE IF EXISTS {settings.clickhouse_database}.`{name}` SYNC")
        cvs._ensured_variant_table_assemblies.discard(dataset)
        cit._ensured_interval_table_assemblies.discard(intervals)


@pytest.fixture(scope="module")
def counts() -> dict[str, int]:
    from backend.tests.e2e import _harness

    return _harness.run_async(_exercise)


def test_small_variant_rows_go_into_a_dotted_assemblys_table(counts) -> None:
    assert counts["small"] == 2


def test_interval_rows_go_into_a_dotted_assemblys_table(counts) -> None:
    assert counts["interval"] == 1
