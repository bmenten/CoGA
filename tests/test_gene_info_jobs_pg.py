from __future__ import annotations

from uuid import UUID

import pytest

from backend.app.services import gene_info_jobs_pg


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


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
