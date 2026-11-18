"""The Variant Explorer's gene, transcript and cohort-frequency views
(``variant_explorer_views``), on a fake of ClickHouse: the genomic windows a view reads, the
query of one window over the explorer's own WHERE, the windows' rows summed, and the order and
page of the rows. Their sums against the variant view are compared on real stores in
``integration/test_variant_carrier_summary_clickhouse.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from clickhouse_connect.driver.binding import bind_query

from backend.app.services import variant_explorer_service as explorer
from backend.app.services import variant_explorer_views as views
from backend.app.services.variant_explorer_service import ExplorerScope, GlobalVariantFilters

_SCOPE = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=["p1"])


# The summary's extent as the windows query answers it: (rank, names, first, last position).
_Extent = list[tuple[int, list[str], int, int]]


class _ClickHouse:
    """Answers the windows query with ``extent`` and each window's query with
    ``by_window[(its first chromosome name, its start)]`` (None for an open start); records
    each query and its parameters."""

    def __init__(self, extent: _Extent, by_window: dict[tuple[str, int | None], list[Any]]) -> None:
        self.extent = extent
        self.by_window = by_window
        self.queries: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, query: str, params: Any = None, **_kwargs: Any) -> Any:
        self.queries.append((" ".join(query.split()), dict(params or {})))
        if "intDiv(xpos, 1000000000) AS rank" in query:
            return self.extent
        chromosome = params["window_chromosomes"][0]
        return self.by_window.get((chromosome, params.get("window_start")), [])


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(extent: _Extent, by_window: dict[tuple[str, int | None], list[Any]]) -> _ClickHouse:
        fake = _ClickHouse(extent, by_window)

        async def scope(*_args: Any) -> Any:
            return _SCOPE

        async def nothing(*_args: Any, **_kwargs: Any) -> Any:
            return None

        async def no_review_filter(*_args: Any) -> tuple[None, bool]:
            return None, False

        async def members(*_args: Any) -> list[Any]:
            return [explorer._ShownMember(family_uuid="f1", sample_id="PROBAND", sample_uuid="u1")]

        async def pending(*_args: Any) -> list[str]:
            return ["f9"]

        monkeypatch.setattr(views, "execute_clickhouse", fake.execute)
        monkeypatch.setattr(explorer, "execute_clickhouse", fake.execute)
        monkeypatch.setattr(views, "resolve_scope", scope)
        monkeypatch.setattr(views, "_panel_constraints", nothing)
        monkeypatch.setattr(views, "_review_filter_variant_ids", no_review_filter)
        monkeypatch.setattr(views, "_shown_members", members)
        monkeypatch.setattr(views, "_pending_families", pending)
        return fake

    return install


def test_the_windows_cover_the_summarys_variants_within_the_filters_locus(stores: Any) -> None:
    fake = stores(
        [
            (1, ["chr1", "1"], 10, 120_000_000),
            (23, ["chrX"], 5, 6),
            # Every other contig shares rank 99; its variants are counted too.
            (99, ["chrUn_KI270302v1", "chr1_KI270706v1_random"], 1, 1),
        ],
        {},
    )
    windows = asyncio.run(explorer.explorer_windows(_SCOPE, GlobalVariantFilters()))
    # Every 50 Mb, the first and last window of a chromosome open, each read under every name
    # the summary holds for its chromosome.
    assert [(window.chromosome, window.chromosomes, window.start, window.end) for window in windows] == [
        ("1", ("1", "chr1"), None, 49_999_999),
        ("1", ("1", "chr1"), 50_000_000, 99_999_999),
        ("1", ("1", "chr1"), 100_000_000, None),
        ("X", ("chrX",), None, None),
        ("chr1_KI270706v1_random", ("chr1_KI270706v1_random", "chrUn_KI270302v1"), None, None),
    ]
    assert [(window.first_xpos, window.last_xpos) for window in windows[:3]] == [
        (1_000_000_000, 1_049_999_999),
        (1_050_000_000, 1_099_999_999),
        (1_100_000_000, 1_999_999_999),
    ]
    # Under the chromosome filter, the names it reads, within its bounds.
    located = asyncio.run(
        explorer.explorer_windows(_SCOPE, GlobalVariantFilters(chromosome="chr1", start=60_000_000, end=70_000_000))
    )
    assert [(window.chromosome, window.chromosomes, window.start, window.end) for window in located] == [
        ("chr1", ("1", "chr1"), 60_000_000, 70_000_000)
    ]
    assert asyncio.run(explorer.explorer_windows(_SCOPE, GlobalVariantFilters(chromosome="2"))) == []
    assert fake.queries[0][0].startswith("SELECT intDiv(xpos, 1000000000) AS rank, groupUniqArray(chrom)")


def test_a_gene_row_sums_its_variants_over_the_windows(stores: Any) -> None:
    # gene, variants, high, moderate, low, modifier, carriers, het, hom
    fake = stores(
        [(1, ["1"], 1, 60_000_000)],
        {
            ("1", None): [("SYNGENE1", 2, 1, 1, 0, 0, 5, 3, 2), ("SYNGENE2", 1, 0, 0, 1, 0, 1, 1, 0)],
            ("1", 50_000_000): [("SYNGENE1", 1, 0, 0, 0, 1, 2, 2, 0)],
        },
    )
    page = asyncio.run(views.explorer_gene_view(None, user=None, filters=GlobalVariantFilters(impacts=["HIGH"])))  # type: ignore[arg-type]
    assert (page.total, page.pending_families, page.sort, page.order) == (2, 1, "variants", "desc")
    assert [(row.gene, row.variants, row.high, row.modifier, row.carriers, row.het, row.hom) for row in page.genes] == [
        ("SYNGENE1", 3, 1, 1, 7, 5, 2),
        ("SYNGENE2", 1, 0, 0, 1, 1, 0),
    ]
    query, params = fake.queries[1]
    # The explorer's own WHERE and the window, on the summary's rows joined to their own
    # annotation; a variant counted with a carrier in the rows naming the gene.
    assert "hasAny(e.projects, %(scope_projects)s)" in query
    assert "e.xpos BETWEEN %(xpos_start)s AND %(xpos_end)s" in query
    assert "ON part.key = e.key AND part.annotationSetHash = e.annotationSetHash" in query
    assert "a.chrom IN %(window_chromosomes)s AND a.pos <= %(window_end)s AND a.gene_symbol" in query
    assert "GROUP BY e.key, part.gene HAVING total_samples > 0" in query
    assert "(e.key, e.annotationSetHash) IN (SELECT a.key" in query
    bind_query(query, params)
    # The summary's rows and the annotation filter's are read under the window's names.
    assert params["chromosomes"] == params["window_chromosomes"] == ("1",)
    later, _later_params = fake.queries[2]
    assert "a.chrom IN %(window_chromosomes)s AND a.pos >= %(window_start)s AND a.gene_symbol" in later


@pytest.mark.parametrize(
    ("sort", "order", "first"),
    [("carriers", "desc", "SYNGENE2"), ("gene", "asc", "SYNGENE1"), ("variants", "asc", "SYNGENE2"), ("unknown", "desc", "SYNGENE1")],
)
def test_the_genes_are_sorted_and_paged_as_asked(stores: Any, sort: str, order: str, first: str) -> None:
    stores(
        [(1, ["1"], 1, 10)],
        {("1", None): [("SYNGENE1", 3, 0, 0, 0, 3, 3, 3, 0), ("SYNGENE2", 1, 0, 0, 0, 1, 9, 9, 0)]},
    )
    page = asyncio.run(
        views.explorer_gene_view(None, user=None, filters=GlobalVariantFilters(), sort=sort, order=order, page_size=1)  # type: ignore[arg-type]
    )
    assert [row.gene for row in page.genes] == [first]
    assert page.total == 2


def test_a_transcript_row_holds_its_gene_flags_and_consequence_classes(stores: Any) -> None:
    # transcript, gene, mane, canonical, variants, lof, missense, splice, synonymous, other, carriers
    fake = stores(
        [(17, ["17"], 1, 10)],
        {("17", None): [("ENST00000600001", "SYNGENE1", True, False, 2, 1, 1, 0, 0, 0, 4)]},
    )
    page = asyncio.run(
        views.explorer_transcript_view(None, user=None, filters=GlobalVariantFilters(), transcripts="mane")  # type: ignore[arg-type]
    )
    (row,) = page.rows
    assert (row.transcript_id, row.gene, row.mane, row.canonical, row.lof, row.missense, row.carriers) == (
        "ENST00000600001",
        "SYNGENE1",
        True,
        False,
        1,
        1,
        4,
    )
    assert page.transcripts == "mane"
    query, _params = fake.queries[1]
    assert "AND (a.mane_select OR a.mane_plus_clinical)" in query
    assert "hasAny(a.effects, ['transcript_ablation'" in query


def test_an_unknown_transcript_selection_lists_every_transcript(stores: Any) -> None:
    fake = stores([(1, ["1"], 1, 10)], {})
    page = asyncio.run(views.explorer_transcript_view(None, user=None, filters=GlobalVariantFilters(), transcripts="x"))  # type: ignore[arg-type]
    assert page.transcripts == "all"
    assert "mane_select OR" not in fake.queries[1][0].split("GROUP BY key, annotationSetHash, transcript")[0].split("FROM")[-1]


def test_the_frequency_view_lists_every_cell_zeros_included(stores: Any) -> None:
    # carrier class, gnomAD class, variants: summed over the windows.
    fake = stores([(1, ["1"], 1, 60_000_000)], {("1", None): [(1, 0, 3)], ("1", 50_000_000): [(1, 0, 2), (4, 4, 1)]})
    frequency = asyncio.run(views.explorer_frequency_view(None, user=None, filters=GlobalVariantFilters()))  # type: ignore[arg-type]
    assert frequency.carrier_classes == ["1", "2-5", "6-20", "21-100", ">100"]
    assert frequency.gnomad_af_classes == ["absent", "below_0.1", "0.1_1", "1_5", "above_5"]
    assert len(frequency.cells) == 25
    cells = {(cell.carrier_class, cell.gnomad_af_class): cell for cell in frequency.cells}
    assert cells[("2-5", "absent")].variants == 5
    assert (cells[("2-5", "absent")].carrier_min, cells[("2-5", "absent")].carrier_max) == (2, 5)
    assert cells[(">100", "above_5")].variants == 1 and cells[(">100", "above_5")].carrier_max is None
    query, _params = fake.queries[1]
    assert "multiIf(total_samples >= 1 AND total_samples <= 1, 0," in query
    assert "ai.max_gnomad_af IS NULL, 0" in query


def test_a_cell_is_read_back_by_the_variant_views_two_filters() -> None:
    params: dict[str, Any] = {}
    clauses = explorer._carriers_where(
        _SCOPE,
        GlobalVariantFilters(min_total_samples=2, max_total_samples=5, gnomad_af_class="0.1_1"),
        params,
        tag_variant_ids=None,
        shown_members=(),
    )
    assert any("ai.max_gnomad_af >= 0.001 AND ai.max_gnomad_af < 0.01" in clause for clause in clauses)
    having = explorer._total_samples_having(
        GlobalVariantFilters(min_total_samples=2, max_total_samples=5), params
    )
    assert having == " AND total_samples >= %(min_total_samples)s AND total_samples <= %(max_total_samples)s"
    assert (params["min_total_samples"], params["max_total_samples"]) == (2, 5)
    # An unknown class keeps nothing.
    unknown = explorer._carriers_where(
        _SCOPE, GlobalVariantFilters(gnomad_af_class="rare"), {}, tag_variant_ids=None, shown_members=()
    )
    assert unknown[-1] == "0"


def test_a_view_without_a_scope_or_members_asks_clickhouse_nothing(stores: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = stores([(1, ["1"], 1, 10)], {})

    async def no_members(*_args: Any) -> list[Any]:
        return []

    monkeypatch.setattr(views, "_shown_members", no_members)
    page = asyncio.run(views.explorer_gene_view(None, user=None, filters=GlobalVariantFilters()))  # type: ignore[arg-type]
    assert (page.total, page.pending_families, fake.queries) == (0, 1, [])

    async def no_scope(*_args: Any) -> None:
        return None

    monkeypatch.setattr(views, "resolve_scope", no_scope)
    frequency = asyncio.run(views.explorer_frequency_view(None, user=None, filters=GlobalVariantFilters()))  # type: ignore[arg-type]
    assert sum(cell.variants for cell in frequency.cells) == 0 and fake.queries == []


def test_a_gene_is_shown_by_its_symbol_in_the_gene_reference() -> None:
    # The annotation tables store gene symbols casefolded; the reference keeps their case.
    class _Session:
        def __init__(self) -> None:
            self.params: dict[str, Any] = {}

        async def execute(self, statement: Any, params: Any = None) -> Any:
            self.params = dict(params)
            return type("R", (), {"all": staticmethod(lambda: [("c1orf112", "C1orf112")])})()

    session = _Session()
    assembly = "00000000-0000-0000-0000-0000000000a1"
    symbols = asyncio.run(views._reference_symbols(session, assembly, {"c1orf112", "notagene"}))  # type: ignore[arg-type]
    assert symbols == {"c1orf112": "C1orf112"}
    assert session.params == {"assembly_id": assembly, "symbols": ["c1orf112", "notagene"]}
    # No lookup for an assembly that is no UUID, or no gene.
    assert asyncio.run(views._reference_symbols(None, "a1", {"x"})) == {}  # type: ignore[arg-type]
    assert asyncio.run(views._reference_symbols(None, assembly, set())) == {}  # type: ignore[arg-type]
