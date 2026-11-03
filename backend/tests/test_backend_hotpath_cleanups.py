"""The interval-track table's DDL runs once per assembly per process (a Batch-1 hot-path cleanup).

``ensure_clickhouse_interval_table`` memoizes the assemblies whose table it has created, so a
read or write path that calls it on every request sends its ``CREATE TABLE IF NOT EXISTS``
once; another assembly still gets its own. (The process caches are reset between tests by the
autouse fixture in the root conftest.) The same cleanup's HPO table-probe caching is tested in
``test_hpo_service.py``.
"""

import pytest

from backend.app.services import clickhouse_interval_tracks as cit


@pytest.mark.asyncio
async def test_ensure_interval_table_runs_ddl_once(monkeypatch):
    calls: list[str] = []

    async def fake_execute(query, params=None):
        calls.append(query)
        return []

    monkeypatch.setattr(cit, "execute_clickhouse", fake_execute)

    await cit.ensure_clickhouse_interval_table("GRCh38")
    await cit.ensure_clickhouse_interval_table("GRCh38")
    await cit.ensure_clickhouse_interval_table("GRCh38")

    create_calls = [q for q in calls if "CREATE TABLE IF NOT EXISTS" in q]
    assert len(create_calls) == 1
    # A different assembly still gets its own DDL.
    await cit.ensure_clickhouse_interval_table("GRCh37")
    assert len([q for q in calls if "CREATE TABLE IF NOT EXISTS" in q]) == 2
