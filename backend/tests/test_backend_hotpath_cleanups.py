"""Tests for the Batch-1 hot-path cleanups: interval-table DDL memoization and
HPO table-availability probe caching. (The process caches are reset between tests
by the autouse fixture in the root conftest.)"""
import pytest

from backend.app.services import clickhouse_interval_tracks as cit
from backend.app.services import hpo_service


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def one(self):
        return self._row


class _ProbeSession:
    def __init__(self, row):
        self._row = row
        self.count = 0

    async def execute(self, sql, params=None):
        self.count += 1
        return _Result(self._row)


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


@pytest.mark.asyncio
async def test_postgres_tables_available_caches_positive():
    session = _ProbeSession({"hpo_term": True})

    assert await hpo_service._postgres_tables_available(session, ("hpo_term",)) is True
    assert session.count == 1
    # Second call is served from the cache — no second probe.
    assert await hpo_service._postgres_tables_available(session, ("hpo_term",)) is True
    assert session.count == 1


@pytest.mark.asyncio
async def test_postgres_tables_available_reprobes_when_absent():
    session = _ProbeSession({"hpo_missing": False})

    assert await hpo_service._postgres_tables_available(session, ("hpo_missing",)) is False
    assert session.count == 1
    # Absent tables are not cached (they may appear after schema init) -> re-probe.
    assert await hpo_service._postgres_tables_available(session, ("hpo_missing",)) is False
    assert session.count == 2
