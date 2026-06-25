from __future__ import annotations

from backend.app.services.clickhouse_family_variants import (
    StructuralVariantRecord,
    _structural_record_matches,
)
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters


def _sv_record(genes: list[str]) -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=1,
        variant_id="DEL-1",
        chr="chr1",
        start=100,
        end=200,
        sv_type="DEL",
        source=None,
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=100,
        filters=[],
        gene_symbols=genes,
        annotations=[],
        calls=[],
    )


def _filters() -> StructuralVariantQueryFilters:
    return StructuralVariantQueryFilters(page=1, page_size=100)


def test_panel_gene_terms_match_by_gene_symbol() -> None:
    # A large gene panel (Mendeliome) is matched by gene symbol, case-insensitively.
    record = _sv_record(["BRCA1", "NBR2"])
    assert _structural_record_matches(record, _filters(), [], [], panel_gene_terms={"brca1"}) is True
    assert _structural_record_matches(record, _filters(), [], [], panel_gene_terms={"tp53"}) is False


def test_no_panel_gene_terms_is_unconstrained_by_gene() -> None:
    record = _sv_record(["BRCA1"])
    assert _structural_record_matches(record, _filters(), [], []) is True


def test_panel_gene_terms_with_no_record_genes_excluded() -> None:
    record = _sv_record([])
    assert _structural_record_matches(record, _filters(), [], [], panel_gene_terms={"brca1"}) is False
