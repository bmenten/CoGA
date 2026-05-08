from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.services import small_variant_review_pg


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _RecordingSession:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.sql: str | None = None
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return _FakeResult(self.rows)


@pytest.mark.asyncio
async def test_get_small_variant_review_map_binds_variant_ids_expanding() -> None:
    timestamp = datetime.now(timezone.utc)
    session = _RecordingSession(
        [
            {
                "variant_id": "var-1",
                "classification": "review",
                "tags": ["validated"],
                "tag_metadata": {},
                "note": "kept",
                "compound_het_group_id": None,
                "compound_het_partner_variant_ids": [],
                "compound_het_gene": None,
                "compound_het_gene_id": None,
                "compound_het_classification": None,
                "compound_het_tags": [],
                "compound_het_tag_metadata": {},
                "compound_het_note": None,
                "compound_het_phase_status": None,
                "compound_het_updated_by": None,
                "compound_het_updated_at": None,
                "updated_by": "admin",
                "updated_at": timestamp,
            }
        ]
    )

    result = await small_variant_review_pg.get_small_variant_review_map(
        session,
        family_uuid="family-uuid",
        variant_ids=["var-1", ""],
    )

    assert session.sql is not None
    assert "variant_id IN" in session.sql
    assert session.params == {"family_id": "family-uuid", "variant_ids": ["var-1"]}
    assert result["var-1"].variant_id == "var-1"
    assert result["var-1"].tags == ["validated"]
