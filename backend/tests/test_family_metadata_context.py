from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.services.family_metadata_context import build_family_metadata_context
from backend.app.services.metadata_service import CurrentUser


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _FakeResult:
        self.statements.append(str(statement))
        if "project_id" in params:
            return _FakeResult(
                [{"assembly_id": "assembly-uuid", "assembly_name": "GRCh38"}]
            )
        return _FakeResult(
            [
                {
                    "sample_uuid": "sample-proband",
                    "sample_id": "PROBAND",
                    "sex": "male",
                    "role": "proband",
                    "affected": True,
                }
            ]
        )


def _viewer(project_ids: list[str]) -> CurrentUser:
    return CurrentUser(
        id="user-1",
        username="viewer",
        email="viewer@example.com",
        role="viewer",
        metadata_project_ids=project_ids,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_family_metadata_context_uses_family_members_for_project_scoped_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "11111111-1111-1111-1111-111111111111"
    other_project_id = "22222222-2222-2222-2222-222222222222"

    async def fake_family_mapping(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "id": "family-uuid",
            "family_id": "F1",
            "project_ids": [project_id, other_project_id],
        }

    monkeypatch.setattr(
        "backend.app.services.family_metadata_context.get_accessible_family_mapping",
        fake_family_mapping,
    )

    session = _FakeSession()
    context = await build_family_metadata_context(
        session,  # type: ignore[arg-type]
        family_identifier="F1",
        user=_viewer([project_id, other_project_id]),
        project_id=project_id,
    )

    assert context.project_ids == [project_id, other_project_id]
    assert context.sample_name_to_uuid == {"PROBAND": "sample-proband"}
    assert "sample_projects" not in session.statements[0]
