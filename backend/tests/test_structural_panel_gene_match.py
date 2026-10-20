from __future__ import annotations

from backend.app.services.clickhouse_variant_queries import _structural_record_matches, _StructuralPanelMatch
from backend.app.services.clickhouse_variant_records import PanelFilterConstraints, StructuralVariantRecord
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


def _panel(*genes: str) -> _StructuralPanelMatch:
    """A gene panel without regions: it keeps the SVs whose annotation names one of its genes."""
    return _StructuralPanelMatch(PanelFilterConstraints(genes=genes))


def test_panel_genes_match_by_gene_symbol() -> None:
    # A gene panel keeps an SV whose annotation names one of its genes, in any capitals.
    record = _sv_record(["BRCA1", "NBR2"])
    assert _structural_record_matches(record, _filters(), [], [], panel=_panel("brca1")) is True
    assert _structural_record_matches(record, _filters(), [], [], panel=_panel("TP53")) is False


def test_no_panel_is_unconstrained_by_gene() -> None:
    record = _sv_record(["BRCA1"])
    assert _structural_record_matches(record, _filters(), [], []) is True


def test_panel_genes_with_no_record_genes_excluded() -> None:
    record = _sv_record([])
    assert _structural_record_matches(record, _filters(), [], [], panel=_panel("BRCA1")) is False
