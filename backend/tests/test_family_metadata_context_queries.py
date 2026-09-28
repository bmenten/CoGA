from datetime import datetime, timezone

import pytest

from backend.app.services.family_metadata_context import build_family_metadata_context
from backend.app.services.metadata_service import CurrentUser
from backend.app.services import family_metadata_context


class _FakeMappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM family_relationships" in sql:
            return _FakeMappingResult([])
        return _FakeMappingResult(
            [
                {
                    "sample_uuid": "sample-1",
                    "sample_id": "proband",
                    "sex": "female",
                    "role": "proband",
                    "affected": True,
                }
            ]
        )


@pytest.mark.asyncio
async def test_build_family_metadata_context_orders_distinct_sample_query_by_selected_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_accessible_family_mapping(session, family_identifier, user):
        return {
            "id": "family-uuid",
            "family_id": "demo_family",
            "project_ids": ["project-uuid"],
        }

    async def fake_resolve_family_assembly(session, **kwargs):
        return "assembly-uuid", "GRCh38"

    monkeypatch.setattr(
        family_metadata_context,
        "get_accessible_family_mapping",
        fake_get_accessible_family_mapping,
    )
    monkeypatch.setattr(
        family_metadata_context,
        "_resolve_family_assembly",
        fake_resolve_family_assembly,
    )

    session = _RecordingSession()
    user = CurrentUser(
        id="user-uuid",
        username="demo",
        email="demo@example.com",
        role="user",
        metadata_project_ids=["project-uuid"],
        created_at=datetime.now(timezone.utc),
    )

    context = await build_family_metadata_context(
        session,
        family_identifier="demo_family",
        user=user,
        project_id="project-uuid",
    )

    sample_sql = session.calls[0][0]
    assert "SELECT DISTINCT" not in sample_sql
    assert "FROM family_members" in sample_sql
    assert "sample_projects" not in sample_sql
    assert "ORDER BY lower(s.sample_id)" in sample_sql
    assert context.sample_rows == [
        {
            "sample_uuid": "sample-1",
            "sample_id": "proband",
            "sex": "female",
            "role": "proband",
            "affected": True,
        }
    ]


@pytest.mark.asyncio
async def test_build_family_metadata_context_uses_uuid_project_filter_for_visible_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_uuid = "11111111-1111-4111-8111-111111111111"

    async def fake_get_accessible_family_mapping(session, family_identifier, user):
        return {
            "id": "family-uuid",
            "family_id": "demo_family",
            "project_ids": [project_uuid],
        }

    async def fake_resolve_family_assembly(session, **kwargs):
        return "assembly-uuid", "GRCh38"

    monkeypatch.setattr(
        family_metadata_context,
        "get_accessible_family_mapping",
        fake_get_accessible_family_mapping,
    )
    monkeypatch.setattr(
        family_metadata_context,
        "_resolve_family_assembly",
        fake_resolve_family_assembly,
    )

    session = _RecordingSession()
    user = CurrentUser(
        id="user-uuid",
        username="demo",
        email="demo@example.com",
        role="user",
        metadata_project_ids=[project_uuid],
        created_at=datetime.now(timezone.utc),
    )

    await build_family_metadata_context(
        session,
        family_identifier="demo_family",
        user=user,
    )

    sample_sql, sample_params = session.calls[0]
    assert "FROM family_members" in sample_sql
    assert "sample_projects" not in sample_sql
    assert "sp.project_id" not in sample_sql
    assert sample_params == {"family_uuid": "family-uuid"}
