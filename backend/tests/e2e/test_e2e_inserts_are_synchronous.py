"""ClickHouse stores the app's inserts when they return, never in its async-insert buffer (E2E).

ClickHouse 26.8 buffers an insert by default (``async_insert = 1``) and, waiting for it,
answers only once a timer of 50 to 200 ms has flushed the buffer. ``insert_clickhouse`` sends
every insert with ``async_insert = 0``. The unit tests hold the setting to the driver's call;
this holds the server to it, by its own ``system.query_log``.

Rows go in through the app's writers, into the tables of an assembly no other test uses: an
interval track (``insert_interval_track_rows``) and a variant-table write (the family's data
version). One more insert asks for the buffer (``async_insert = 1``) into a third table: the
control, which shows the log tells a buffered insert apart. The tables are dropped at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "E2E SYNC INSERT"  # dataset key E2E_SYNC_INSERT
_FAMILY = "e2e-synchronous-insert"


async def _exercise() -> dict[str, list[tuple[Any, ...]]]:
    from backend.app.core.clickhouse import clickhouse_dataset_key, execute_clickhouse, insert_clickhouse
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_interval_tracks as cit
    from backend.app.services import clickhouse_variant_storage as cvs
    from backend.app.services.clickhouse_variant_ids import _small_table_name

    dataset = clickhouse_dataset_key(_ASSEMBLY)
    database = settings.clickhouse_database
    await cvs.ensure_clickhouse_variant_tables(_ASSEMBLY)
    # The server's clock, so a log of an earlier run against the same server is not read.
    [(started,)] = await execute_clickhouse("SELECT toUnixTimestamp64Micro(now64(6))")
    try:
        await cit.insert_interval_track_rows(
            _ASSEMBLY,
            [
                {
                    "family_id": _FAMILY,
                    "sample_id": "sample-1",
                    "track_type": "coverage",
                    "source": "e2e",
                    "chr": "7",
                    "start": 1000 * index,
                    "end": 1000 * (index + 1),
                    "value": 30.0,
                    "metadata_json": "{}",
                }
                for index in range(3)
            ],
        )
        await cvs.bump_family_small_variant_data_version(_ASSEMBLY, _FAMILY)
        await insert_clickhouse(
            f"INSERT INTO {_small_table_name(_ASSEMBLY, 'entries')} (key, family_guid, variantId, sign) VALUES",
            [(1, _FAMILY, "7-1000-A-G", 1)],
            settings={"async_insert": 1},
        )
        await execute_clickhouse("SYSTEM FLUSH LOGS")
        # The log names a table as coga.`E2E_SYNC_INSERT/INTERVAL/entries`: read without the
        # backticks. AsyncInsertQuery counts an insert that went into the buffer.
        rows = await execute_clickhouse(
            """
            SELECT
                arrayFirst(
                    t -> startsWith(t, %(prefix)s), arrayMap(t -> replaceAll(t, '`', ''), tables)
                ) AS stored_in,
                query_kind,
                ProfileEvents['AsyncInsertQuery'] AS buffered
            FROM system.query_log
            WHERE type = 'QueryFinish'
              AND event_date >= yesterday()
              AND toUnixTimestamp64Micro(event_time_microseconds) >= %(started)s
              AND query_kind IN ('Insert', 'AsyncInsertFlush')
              AND arrayExists(t -> startsWith(replaceAll(t, '`', ''), %(prefix)s), tables)
            ORDER BY event_time_microseconds
            """,
            {"prefix": f"{database}.{dataset}/", "started": started},
        )
        by_table: dict[str, list[tuple[Any, ...]]] = {}
        for stored_in, kind, buffered in rows:
            by_table.setdefault(str(stored_in).split("/", 1)[1], []).append((str(kind), int(buffered)))
        return by_table
    finally:
        for (name,) in await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(db)s AND startsWith(name, %(prefix)s)",
            {"db": database, "prefix": f"{dataset}/"},
        ):
            await execute_clickhouse(f"DROP TABLE IF EXISTS {database}.`{name}` SYNC")
        cvs._ensured_variant_table_assemblies.discard(dataset)
        cit._ensured_interval_table_assemblies.discard(cit._interval_table_name(_ASSEMBLY))


@pytest.fixture(scope="module")
def logged() -> dict[str, list[tuple[Any, ...]]]:
    from backend.tests.e2e import _harness

    return _harness.run_async(_exercise)


def test_an_interval_tracks_rows_are_stored_by_a_synchronous_insert(logged) -> None:
    assert logged["INTERVAL/entries"] == [("Insert", 0)]


def test_a_variant_table_write_is_stored_by_a_synchronous_insert(logged) -> None:
    assert logged["SNV_INDEL/family_data_version"] == [("Insert", 0)]


def test_an_insert_that_asks_for_the_buffer_is_logged_as_buffered(logged) -> None:
    # The control: the same log shows a buffered insert, and the flush that stored it.
    assert sorted(logged["SNV_INDEL/entries"]) == [("AsyncInsertFlush", 0), ("Insert", 1)]
