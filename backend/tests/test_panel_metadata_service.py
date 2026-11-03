from __future__ import annotations

import pytest

from backend.app.services.panel_metadata_service import _ensure_panel_name_available


class _ScalarResult:
    def scalar_one_or_none(self) -> None:
        return None


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement, params: dict[str, object]):
        self.calls.append((str(statement), params))
        return _ScalarResult()


@pytest.mark.asyncio
async def test_ensure_panel_name_available_new_panel_avoids_untyped_null_parameter() -> None:
    session = _RecordingSession()

    await _ensure_panel_name_available(session, "PanelApp 285: Intellectual disability")

    # No `:existing_panel_id IS NULL` test: asyncpg cannot type a NULL it is given alone.
    assert [(" ".join(sql.split()), params) for sql, params in session.calls] == [
        (
            "SELECT id::text FROM gene_panels WHERE name = :name",
            {"name": "PanelApp 285: Intellectual disability"},
        )
    ]


@pytest.mark.asyncio
async def test_ensure_panel_name_available_existing_panel_uses_explicit_id_filter() -> None:
    session = _RecordingSession()

    await _ensure_panel_name_available(
        session,
        "PanelApp 285: Intellectual disability",
        existing_panel_id="d67e635c-7d98-4495-8b3c-153f5007561b",
    )

    sql, params = session.calls[0]
    assert "id::text <> :existing_panel_id" in sql
    assert params == {
        "name": "PanelApp 285: Intellectual disability",
        "existing_panel_id": "d67e635c-7d98-4495-8b3c-153f5007561b",
    }
