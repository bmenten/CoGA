"""A candidate cap must bound the ranking, not decide what the filter gets to see.

The prioritised SV page used to read the first N rows of the callset and only then apply
the gene/panel filter, so a gene-filtered search returned nothing whenever that gene's SV
sat outside the window — indistinguishable, in the UI, from "this gene has no SV".
"""

import pytest

from backend.app.services.clickhouse_variant_queries import (
    _structural_region_filter_condition,
    _structural_variant_where_clauses,
)
from backend.app.services.clickhouse_variant_records import Region
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters


class _Context:
    family_uuid = "fam-1"
    project_ids: list[str] = []
    sample_rows: list[dict] = []
    sample_name_to_uuid: dict[str, str] = {}

    def __init__(self, visible: list[str] | None = None):
        self._visible = visible or ["S1"]


@pytest.fixture()
def context(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "backend.app.services.clickhouse_variant_queries._visible_clickhouse_sample_ids",
        lambda _ctx: ["S1"],
    )
    return _Context()


def _clauses(context, regions):
    return _structural_variant_where_clauses(
        context, StructuralVariantQueryFilters(page=1, page_size=10), include_regions=regions
    )


class TestRegionCondition:
    def test_overlap_is_between_the_sv_span_and_the_region(self) -> None:
        params: dict = {}
        sql = _structural_region_filter_condition(
            [Region(chr="2", start=160099661, end=160657754)], prefix="p", params=params
        )
        # An SV overlaps when its own span crosses the region — not when its start alone
        # falls inside, which would miss an SV that begins before the gene and ends in it.
        assert "e.start <= region_end AND e.end >= region_start" in sql
        assert params["p_starts"] == [160099661]
        assert params["p_ends"] == [160657754]

    def test_reversed_coordinates_are_normalised(self) -> None:
        params: dict = {}
        _structural_region_filter_condition(
            [Region(chr="1", start=500, end=100)], prefix="p", params=params
        )
        assert params["p_starts"] == [100]
        assert params["p_ends"] == [500]

    def test_duplicate_regions_are_collapsed(self) -> None:
        params: dict = {}
        region = Region(chr="1", start=100, end=200)
        _structural_region_filter_condition([region, region], prefix="p", params=params)
        assert params["p_starts"] == [100]

    def test_no_regions_yields_no_condition(self) -> None:
        params: dict = {}
        assert _structural_region_filter_condition([], prefix="p", params=params) is None
        assert params == {}


class TestWhereClauses:
    def test_regions_reach_the_sql(self, context) -> None:
        where, params = _clauses(context, [Region(chr="2", start=160099661, end=160657754)])
        joined = " AND ".join(where)
        # Without this the row cap is applied before the gene filter ever runs.
        assert "sv_include_region_chromosomes" in joined
        assert params["sv_include_region_starts"] == [160099661]

    def test_no_regions_leaves_the_query_untouched(self, context) -> None:
        where, params = _clauses(context, [])
        assert not any("sv_include_region" in clause for clause in where)
        assert not any(key.startswith("sv_include_region") for key in params)

    def test_the_family_and_sign_guards_survive(self, context) -> None:
        where, _params = _clauses(context, [Region(chr="1", start=1, end=2)])
        assert "e.family_guid = %(family_guid)s" in where
        assert "e.sign = 1" in where


class TestSecondHitBoundingLocus:
    """The badge links by locus because gene symbol and gene coordinates disagree often
    enough to matter: SV annotation includes flanking genes, the SV search requires a
    real overlap with a stored transcript."""

    @staticmethod
    def _summary(svs):
        from backend.app.services.sv_gene_index_service import summarize_second_hit

        return summarize_second_hit(svs, ["S1"])

    def test_a_single_sv_bounds_itself(self) -> None:
        summary = self._summary([{"sv_type": "INS", "chr": "1", "start": 207494109, "end": 207494110, "gt": {}}])
        assert (summary["chr"], summary["start"], summary["end"]) == ("1", 207494109, 207494110)

    def test_several_svs_are_bounded_together(self) -> None:
        summary = self._summary(
            [
                {"sv_type": "DEL", "chr": "2", "start": 500, "end": 900, "gt": {}},
                {"sv_type": "DUP", "chr": "2", "start": 100, "end": 300, "gt": {}},
            ]
        )
        assert (summary["chr"], summary["start"], summary["end"]) == ("2", 100, 900)

    def test_reversed_coordinates_are_normalised(self) -> None:
        summary = self._summary([{"sv_type": "DEL", "chr": "3", "start": 900, "end": 100, "gt": {}}])
        assert (summary["start"], summary["end"]) == (100, 900)

    def test_a_symbol_spanning_chromosomes_bounds_only_the_common_one(self) -> None:
        # Repeat-family symbols (U6, Y_RNA) occur genome-wide; a span across chromosomes
        # would be meaningless, so the best-represented chromosome wins.
        summary = self._summary(
            [
                {"sv_type": "DEL", "chr": "5", "start": 100, "end": 200, "gt": {}},
                {"sv_type": "DEL", "chr": "5", "start": 300, "end": 400, "gt": {}},
                {"sv_type": "DEL", "chr": "9", "start": 999, "end": 1999, "gt": {}},
            ]
        )
        assert (summary["chr"], summary["start"], summary["end"]) == ("5", 100, 400)

    def test_missing_or_unparsable_coordinates_yield_no_locus(self) -> None:
        assert self._summary([{"sv_type": "DEL", "gt": {}}])["chr"] is None
        assert self._summary([{"sv_type": "DEL", "chr": "1", "start": None, "end": 5, "gt": {}}])["chr"] is None
        assert self._summary([{"sv_type": "DEL", "chr": "1", "start": "x", "end": 5, "gt": {}}])["chr"] is None

    def test_an_sv_without_coordinates_does_not_drag_the_span(self) -> None:
        summary = self._summary(
            [
                {"sv_type": "DEL", "chr": "7", "start": 100, "end": 200, "gt": {}},
                {"sv_type": "DEL", "gt": {}},
            ]
        )
        assert (summary["chr"], summary["start"], summary["end"]) == ("7", 100, 200)
        # The count still reflects every SV on the gene.
        assert summary["sv_count"] == 2
