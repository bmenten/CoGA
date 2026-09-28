"""count_only presence mode for the repeat-expansion and Paraphase family tables
(#12): the landing page only needs a presence count, not the full table payload."""
from types import SimpleNamespace

import pytest

from backend.app.services import paraphase_pg as para
from backend.app.services import repeat_expansion_pg as rep


class _CountSession:
    """AsyncSession stand-in returning a single scalar count for the COUNT query."""

    def __init__(self, count: int) -> None:
        self._count = count
        self.calls: list[tuple[str, object]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return SimpleNamespace(scalar=lambda: self._count)


def _ctx():
    return SimpleNamespace(
        sample_uuid_to_name={"11111111-1111-1111-1111-111111111111": "S1"},
        family_uuid="22222222-2222-2222-2222-222222222222",
        sample_rows=[],
    )


@pytest.mark.asyncio
async def test_repeat_count_only_returns_loci_count_without_lateral_join():
    session = _CountSession(3)
    result = await rep.get_family_repeat_expansion_table_response(
        session, context=_ctx(), count_only=True
    )
    assert result.loci_count == 3
    assert result.loci == []
    assert result.samples == []
    # Exactly one cheap query; the expensive catalog LATERAL join is skipped.
    assert len(session.calls) == 1
    sql = session.calls[0][0]
    assert "count(DISTINCT locus_id)" in sql
    assert "repeat_loci" not in sql


@pytest.mark.asyncio
async def test_repeat_count_only_no_samples_runs_no_query():
    session = _CountSession(0)
    ctx = SimpleNamespace(sample_uuid_to_name={}, family_uuid="fam", sample_rows=[])
    result = await rep.get_family_repeat_expansion_table_response(
        session, context=ctx, count_only=True
    )
    assert result.loci_count == 0
    assert session.calls == []


@pytest.mark.asyncio
async def test_paraphase_count_only_returns_genes_count():
    session = _CountSession(2)
    result = await para.get_family_paraphase_table_response(
        session, context=_ctx(), count_only=True
    )
    assert result.genes_count == 2
    assert result.genes == []
    assert len(session.calls) == 1
    assert "count(DISTINCT gene_symbol)" in session.calls[0][0]
