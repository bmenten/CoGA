from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from backend.app.services import gene_info_jobs_pg


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _MappingsResult:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class _JobInsertResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return _MappingsResult(self._row)


class _RecordingSession:
    def __init__(self, value=0) -> None:
        self.value = value
        self.sql: str | None = None
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return _ScalarResult(self.value)


@pytest.mark.asyncio
async def test_count_distinct_human_gene_symbols_uses_uuid_assembly_filter() -> None:
    assembly_uuid = "11111111-1111-4111-8111-111111111111"
    session = _RecordingSession(value=7)

    count = await gene_info_jobs_pg._count_distinct_human_gene_symbols(
        session,
        assembly_ids=[assembly_uuid],
    )

    assert count == 7
    assert session.sql is not None
    assert "assembly_id IN (" in session.sql
    assert "POSTCOMPILE_assembly_ids" in session.sql
    assert "assembly_id::text IN :assembly_ids" not in session.sql
    assert session.params == {"assembly_ids": [UUID(assembly_uuid)]}


@pytest.mark.asyncio
async def test_queue_startup_gene_reference_refresh_if_needed_uses_local_dbnsfp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembly_uuid = "11111111-1111-4111-8111-111111111111"
    now = datetime.now(timezone.utc)
    job_row = {
        "id": "22222222-2222-4222-8222-222222222222",
        "scope": "all_human",
        "symbol": None,
        "status": "queued",
        "active_slot": gene_info_jobs_pg.ACTIVE_GENE_REFERENCE_SLOT,
        "worker_id": None,
        "requested_by": "startup-bootstrap",
        "requested_at": now,
        "started_at": None,
        "heartbeat_at": None,
        "completed_at": None,
        "total_symbols": 0,
        "completed_symbols": 0,
        "updated_records": 0,
        "human_assemblies": 0,
        "current_symbol": None,
        "error": None,
        "metadata": {},
    }

    class _StartupSession:
        def __init__(self) -> None:
            self.calls = 0
            self.commits = 0

        async def execute(self, statement, params=None):
            self.calls += 1
            sql = str(statement)
            if "COUNT(*) FROM gene_info" in sql:
                return _ScalarResult(0)
            if "FROM gene_info_refresh_jobs" in sql and "LIMIT 1" in sql:
                return _ScalarResult(None)
            if "INSERT INTO gene_info_refresh_jobs" in sql:
                return _JobInsertResult(job_row)
            raise AssertionError(f"Unexpected SQL: {sql}")

        async def commit(self):
            self.commits += 1

    async def fake_get_human_context(session):
        return gene_info_jobs_pg.HumanGeneContext(
            species={"name": "Homo sapiens"},
            assemblies=[{"id": assembly_uuid, "assembly_name": "GRCh38", "version": "hg38"}],
        )

    async def fake_count_symbols(session, *, assembly_ids):
        assert assembly_ids == [assembly_uuid]
        return 3

    monkeypatch.setattr(
        gene_info_jobs_pg,
        "find_local_dbnsfp_gene_path",
        lambda: Path("/data/ref-data/dbNSFP5.4_gene.gz"),
    )
    monkeypatch.setattr(gene_info_jobs_pg, "_get_human_context", fake_get_human_context)
    monkeypatch.setattr(gene_info_jobs_pg, "_count_distinct_human_gene_symbols", fake_count_symbols)

    session = _StartupSession()
    job = await gene_info_jobs_pg.queue_startup_gene_reference_refresh_if_needed(session)

    assert job is not None
    assert job.scope == "all_human"
    assert job.requested_by == "startup-bootstrap"
    assert session.commits == 1
