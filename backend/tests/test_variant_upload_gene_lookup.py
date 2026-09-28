import pytest

from backend.app.services import variant_upload_service as vus


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RecordingSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, object]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return _FakeResult(self.rows)


def test_gene_symbols_for_window_returns_distinct_sorted_overlaps() -> None:
    genes = {
        "1": [(100, 200, "B"), (150, 300, "A"), (400, 500, "C"), (100, 200, "A")],
    }
    # Overlaps B, A, A -> distinct + sorted.
    assert vus._gene_symbols_for_window(genes, chrom="1", start=180, end=250) == ["A", "B"]
    # Half-open: a gene ending exactly at the window start does not overlap.
    assert vus._gene_symbols_for_window(genes, chrom="1", start=200, end=250) == ["A"]
    # Unknown chromosome -> no symbols.
    assert vus._gene_symbols_for_window(genes, chrom="9", start=0, end=10) == []


@pytest.mark.asyncio
async def test_fetch_genes_for_chroms_issues_one_query_grouped_by_chrom() -> None:
    rows = [("1", 100, 200, "A"), ("1", 150, 300, "B"), ("2", 50, 80, "C")]
    session = _RecordingSession(rows)

    by_chrom = await vus._fetch_genes_for_chroms(
        session, assembly_id="a-uuid", chroms=["1", "2"]
    )

    assert len(session.calls) == 1
    sql = session.calls[0][0]
    assert "FROM genes" in sql and "hgnc_symbol" in sql
    assert by_chrom["1"] == [(100, 200, "A"), (150, 300, "B")]
    assert by_chrom["2"] == [(50, 80, "C")]


@pytest.mark.asyncio
async def test_fetch_genes_for_chroms_skips_query_for_empty_inputs() -> None:
    session = _RecordingSession([])
    assert await vus._fetch_genes_for_chroms(session, assembly_id=None, chroms=["1"]) == {}
    assert await vus._fetch_genes_for_chroms(session, assembly_id="a", chroms=[]) == {}
    assert session.calls == []
