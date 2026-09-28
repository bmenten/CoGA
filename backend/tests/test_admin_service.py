from __future__ import annotations

from uuid import UUID

import pytest

from backend.app.services import admin_service
from backend.app.services.family_metadata_context import FamilyMetadataContext


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


class _RecordingSession:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.sql: str | None = None
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return _FakeExecuteResult(self.rows)


@pytest.mark.asyncio
async def test_assembly_project_rows_orders_by_selected_alias() -> None:
    session = _RecordingSession(
        [
            {
                "project_id": "project-1",
                "assembly_id": "assembly-1",
                "assembly_name": "GRCh38",
            }
        ]
    )

    rows = await admin_service._assembly_project_rows(session, "family-uuid")

    assert rows == [
        {
            "project_id": "project-1",
            "assembly_id": "assembly-1",
            "assembly_name": "GRCh38",
        }
    ]
    assert session.sql is not None
    assert "SELECT DISTINCT" in session.sql
    assert "ORDER BY a.assembly_name, project_id" in session.sql
    assert session.params == {"family_uuid": "family-uuid"}


@pytest.mark.asyncio
async def test_sample_rows_by_family_uses_uuid_family_filter() -> None:
    family_uuid = "11111111-1111-4111-8111-111111111111"
    session = _RecordingSession(
        [
            {
                "family_uuid": family_uuid,
                "sample_uuid": "sample-1",
                "sample_id": "proband",
                "sex": "female",
                "role": "proband",
                "affected": True,
            }
        ]
    )

    rows = await admin_service._sample_rows_by_family(session, [family_uuid])

    assert rows[family_uuid][0]["sample_id"] == "proband"
    assert session.sql is not None
    assert "fm.family_id IN (" in session.sql
    assert "POSTCOMPILE_family_uuids" in session.sql
    assert "fm.family_id::text IN :family_uuids" not in session.sql
    assert session.params == {"family_uuids": [UUID(family_uuid)]}


@pytest.mark.asyncio
async def test_structural_sample_counts_uses_clickhouse_aggregate_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_count_by_sample(assembly_name, family_uuid, *, sample_ids, project_ids=None, source=None):
        assert assembly_name == "GRCh38"
        assert family_uuid == "family-uuid"
        assert list(sample_ids) == ["son", "mother"]
        assert project_ids == ["project-uuid"]
        return {"son": 4, "mother": 2}

    monkeypatch.setattr(
        admin_service,
        "count_family_structural_variants_by_sample",
        fake_count_by_sample,
    )

    counts = await admin_service._structural_sample_counts(
        [
            FamilyMetadataContext(
                family_uuid="family-uuid",
                family_id="demo_family",
                project_ids=["project-uuid"],
                sample_rows=[],
                sample_uuid_to_name={"sample-son": "son", "sample-mother": "mother"},
                sample_name_to_uuid={"son": "sample-son", "mother": "sample-mother"},
                affected_sample_names=[],
                assembly_id="assembly-uuid",
                assembly_name="GRCh38",
            )
        ]
    )

    assert counts == {"sample-son": 4, "sample-mother": 2}
