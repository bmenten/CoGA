from __future__ import annotations

import pytest

from backend.app.services.family_service import (
    _build_family_roi_payload,
    _small_variant_presence_filters,
)


class _FakeMappingsResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeExecuteResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return _FakeMappingsResult(self._row)


class _RecordingSession:
    def __init__(self, row) -> None:
        self.row = row
        self.sql: str | None = None
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return _FakeExecuteResult(self.row)


@pytest.mark.asyncio
async def test_build_family_roi_payload_orders_gene_query_by_span_desc() -> None:
    session = _RecordingSession(
        {
            "hgnc_symbol": "BRCA1",
            "gene_id": "ENSG00000012048",
            "chr": "17",
            "start": 43_044_295,
            "end": 43_125_482,
        }
    )

    payload = await _build_family_roi_payload(
        session,
        assembly_id="assembly-uuid",
        query="BRCA1",
    )

    assert payload == {
        "query": "BRCA1",
        "label": "BRCA1",
        "source": "gene",
        "assembly_id": "assembly-uuid",
        "chr": "17",
        "start": 43_044_295,
        "end": 43_125_482,
    }
    assert session.sql is not None
    assert 'ORDER BY ("end" - start) DESC, hgnc_symbol' in session.sql
    assert session.params == {"assembly_id": "assembly-uuid", "query": "BRCA1"}


def test_small_variant_presence_filters_default_to_non_reference_genotypes() -> None:
    assert _small_variant_presence_filters("son", None) == [
        "son:0/1|1/0|0|1|1|0|1/1|1|1",
    ]


def test_small_variant_presence_filters_preserve_explicit_sample_filter() -> None:
    assert _small_variant_presence_filters("son", ["mother:0/1", "son:1/1"]) == ["son:1/1"]
