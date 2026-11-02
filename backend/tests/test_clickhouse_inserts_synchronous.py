"""Every ClickHouse insert is a synchronous insert: stored when it returns.

ClickHouse 26.8 buffers an insert by default (``async_insert = 1``) and, waiting for it,
answers only once a timer of 50 to 200 ms has flushed the buffer. Every writer sends its rows
in batches, one batch at a time, and each batch waited for that timer.
``core/clickhouse.insert_clickhouse`` now sends every insert with ``async_insert = 0``, and a
writer's own settings go over that default. These tests hold the default, each writer's
inserts to it (the variant tables' are in test_small_variant_import_reads_once.py), and every
write to the driver to that one function.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from backend.app.core import clickhouse
from backend.app.services import clickhouse_interval_tracks, family_package_tracks

_SYNCHRONOUS = {"async_insert": 0}
_QUERY = "INSERT INTO coga.`GRCh38/INTERVAL/entries` (family_guid, start) VALUES"


class _Client:
    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []

    async def insert(self, **kwargs: Any) -> str:
        self.inserts.append(kwargs)
        return "summary"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _Client:
    recording = _Client()

    async def get_client() -> _Client:
        return recording

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(clickhouse, "get_clickhouse_client", get_client)
    monkeypatch.setattr(clickhouse_interval_tracks, "ensure_clickhouse_interval_table", no_op)
    return recording


def _track_row(start: int) -> dict[str, Any]:
    return {
        "family_id": "family-uuid",
        "sample_id": "sample-uuid",
        "assembly_name": "GRCh38",
        "track_type": "coverage",
        "source": "web",
        "chr": "chr1",
        "start": start,
        "end": start + 1000,
        "value": 30.0,
        "metadata_json": "{}",
    }


@pytest.mark.asyncio
async def test_an_insert_is_sent_as_a_synchronous_insert(client: _Client) -> None:
    assert await clickhouse.insert_clickhouse(_QUERY, [("family-uuid", 1)]) == "summary"
    # The INSERT … VALUES with its rows apart, as the interval tracks send theirs.
    assert await clickhouse.execute_clickhouse(_QUERY, [("family-uuid", 2)]) == "summary"

    assert [insert["settings"] for insert in client.inserts] == [_SYNCHRONOUS, _SYNCHRONOUS]
    assert client.inserts[0]["column_names"] == ["family_guid", "start"]


@pytest.mark.asyncio
async def test_an_inserts_own_settings_go_over_the_synchronous_default(client: _Client) -> None:
    await clickhouse.insert_clickhouse(_QUERY, [("family-uuid", 1)], settings={"insert_deduplicate": 0})
    # A writer that wants ClickHouse to gather its inserts says so (none does).
    await clickhouse.insert_clickhouse(_QUERY, [("family-uuid", 2)], settings={"async_insert": 1})
    await clickhouse.insert_clickhouse(_QUERY, [("family-uuid", 3)])

    assert [insert["settings"] for insert in client.inserts] == [
        {"async_insert": 0, "insert_deduplicate": 0},
        {"async_insert": 1},
        _SYNCHRONOUS,
    ]
    assert clickhouse._SYNCHRONOUS_INSERT == _SYNCHRONOUS  # the default itself unchanged


@pytest.mark.asyncio
async def test_no_rows_send_no_insert(client: _Client) -> None:
    assert await clickhouse.insert_clickhouse(_QUERY, []) is None
    assert client.inserts == []


@pytest.mark.asyncio
async def test_the_interval_track_rows_are_written_as_synchronous_inserts(client: _Client) -> None:
    # A BED upload, the target coverage, the haplotype blocks and the lineage write theirs so.
    await clickhouse_interval_tracks.insert_interval_track_rows("GRCh38", [_track_row(0), _track_row(1000)])

    [insert] = client.inserts
    assert insert["table"] == "`coga`.`GRCh38/INTERVAL/entries`"
    assert len(insert["data"]) == 2
    assert insert["settings"] == _SYNCHRONOUS


@pytest.mark.asyncio
async def test_each_batch_of_a_package_track_is_a_synchronous_insert(
    client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Coverage, segments, APCAD, PCF and segregation tracks are stored a batch at a time.
    monkeypatch.setattr(family_package_tracks, "INTERVAL_INSERT_BATCH_ROWS", 2)

    result = await family_package_tracks._store_track_rows(
        session=None,  # type: ignore[arg-type]  # the batch writer does not use it
        rows=[_track_row(index * 1000) for index in range(5)],
    )

    assert result == {"processed": 5, "inserted": 5, "skipped": 0}
    assert [len(insert["data"]) for insert in client.inserts] == [2, 2, 1]
    assert all(insert["settings"] == _SYNCHRONOUS for insert in client.inserts)


def test_only_the_core_module_reaches_the_clickhouse_driver() -> None:
    """Every write goes through ``insert_clickhouse`` (or ``execute_clickhouse``, which hands
    an insert with rows to it): no other module takes the driver's client, which would send an
    insert that does not get the synchronous default."""
    app = Path(clickhouse.__file__).resolve().parents[1]
    core = Path(clickhouse.__file__).resolve()
    offenders: list[str] = []
    for path in sorted(app.rglob("*.py")):
        if path.resolve() == core:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # Its exception types are read elsewhere; nothing there can write.
                if module.startswith("clickhouse_connect") and module != "clickhouse_connect.driver.exceptions":
                    names = [module]
                names += [alias.name for alias in node.names if alias.name == "get_clickhouse_client"]
            elif isinstance(node, ast.Attribute) and node.attr == "get_clickhouse_client":
                names = [node.attr]
            offenders += [
                f"{path.relative_to(app)}:{node.lineno} {name}"
                for name in names
                if name == "get_clickhouse_client" or name == "clickhouse_connect" or name.startswith("clickhouse_connect.")
            ]
    assert offenders == []
