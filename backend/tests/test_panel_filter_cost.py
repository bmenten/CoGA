"""Applying a gene panel costs the panel once per search, not once per row (ENG-15).

The family search applied a panel's regions in SQL by testing each row against every region
(``arrayExists``), and its record filter (``_kept_small_candidates`` → ``_small_record_matches``)
casefolded the panel's gene names again for every candidate and scanned every region even
after a gene had matched: 1.5 to 4.4 s of a fresh Mendeliome ranking, which a compound-het,
recessive or carrier-screening search paid on an exact cache hit too.

* The record filter casefolds the panel's genes once per search, reads no region of a record a
  panel gene names, and keeps exactly what the per-row rule kept, over a grid of chromosome
  spellings, positions, gene spellings and annotation keys, and random cases
  (``_SmallPanelMatch``, ``_RegionLookup``).
* The family page's panel-region condition is a join on 64-kb windows over the family's own
  rows (``_small_family_region_condition``), narrowed by nothing but the search's own scope
  and locus clauses, so it reads every row the search reads. That it keeps the same rows is
  shown in ClickHouse by integration/test_family_panel_regions_integration.py and, on every
  path of the family search, by e2e/test_e2e_family_panel_region_join.py.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import replace
from typing import Any

import pytest

from backend.app.services import clickhouse_variant_queries as queries
from backend.app.services.clickhouse_family_variants import _kept_small_candidates, _SmallVariantScope
from backend.app.services.clickhouse_variant_queries import (
    _panel_regions_apply,
    _small_query_filter_parts,
    _small_record_hits_gene_terms,
    _small_record_matches,
    _small_variant_where_clauses,
    _variant_overlaps_regions,
)
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    SmallVariantCall,
    SmallVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

_FILTERS = SmallVariantQueryFilters(page=1, page_size=100)
_CONTEXT = FamilyMetadataContext(
    family_uuid="eng15-family",
    family_id="ENG15",
    project_ids=["eng15-project-a", "eng15-project-b"],
    sample_rows=[{"sample_id": "PROBAND"}],
    sample_uuid_to_name={},
    sample_name_to_uuid={"PROBAND": "eng15-sample"},
    affected_sample_names=["PROBAND"],
    assembly_id="eng15-assembly",
    assembly_name="GRCh38",
)
_ENTRIES = "coga.`GRCh38/SNV_INDEL/entries`"


def _record(
    chrom: str,
    start: int,
    end: int | None = None,
    *,
    symbols: tuple[str, ...] = (),
    annotations: tuple[dict[str, Any], ...] = (),
    variant_id: str | None = None,
) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id or f"{chrom}-{start}-{end}-{'.'.join(symbols)}",
        chr=chrom,
        start=start,
        end=start if end is None else end,
        ref="C",
        alt="T",
        source="deepvariant",
        rsid=None,
        filters=["PASS"],
        gene_symbols=list(symbols),
        annotations=[dict(annotation) for annotation in annotations],
        calls=[SmallVariantCall(sample="PROBAND", gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
    )


def _scope(panel: PanelFilterConstraints) -> _SmallVariantScope:
    return _SmallVariantScope(
        panel_constraints=panel,
        review_variant_ids=None,
        excluded_review_variant_ids=set(),
        include_review_filter_active=False,
        include_regions=[],
        exclude_regions=[],
        exclude_gene_regions=[],
        exclude_gene_terms=[],
    )


def _rule(record: SmallVariantRecord, panel: PanelFilterConstraints) -> bool:
    """The panel check as the record filter made it before ENG-15: a gene of the panel among
    the record's gene names, or the record in one of the panel's regions; a panel of genes
    with more regions than the SQL inlines is matched on its genes alone
    (``_panel_regions_apply``, ENG-1)."""
    if not (panel.genes or panel.regions):
        return True
    gene_match = bool(panel.genes) and _small_record_hits_gene_terms(record, panel.genes)
    region_match = (
        bool(panel.regions)
        and _panel_regions_apply(panel)
        and _variant_overlaps_regions(record.chr, record.start, record.end, panel.regions)
    )
    return gene_match or region_match


# --------------------------------------------------------------------------- #
# The record filter: the panel prepared once per search                        #
# --------------------------------------------------------------------------- #


def _panel_and_records(genes: int, records: int) -> tuple[PanelFilterConstraints, list[SmallVariantRecord]]:
    """A panel of ``genes`` genes, each with a locus, and ``records`` records annotated with
    one of them, spelled in other case, each in its gene's locus."""
    panel = PanelFilterConstraints(
        genes=tuple(f"PANELGENE{index:04d}" for index in range(genes)),
        regions=tuple(Region(chr="chr2", start=10_000 * index, end=10_000 * index + 500) for index in range(genes)),
    )
    kept = [
        _record(
            "2",
            10_000 * (index % genes) + 100,
            symbols=(f"Panelgene{index % genes:04d}",),
            annotations=({"gene": f"panelGENE{index % genes:04d}", "impact": "MODERATE"},),
            variant_id=f"v{index}",
        )
        for index in range(records)
    ]
    return panel, kept


def test_the_record_filter_casefolds_the_panel_s_genes_once_per_search(monkeypatch: pytest.MonkeyPatch) -> None:
    panel, records = _panel_and_records(genes=200, records=60)
    panel_genes = set(panel.genes)
    casefolded: Counter[str] = Counter()
    casefold = queries._casefold

    def counting_casefold(value: Any) -> str:
        if value in panel_genes:
            casefolded[value] += 1
        return casefold(value)

    monkeypatch.setattr(queries, "_casefold", counting_casefold)
    kept = _kept_small_candidates(records, _FILTERS, _scope(panel), _CONTEXT)

    assert [record.variant_id for record in kept] == [record.variant_id for record in records]
    # Each of the panel's gene names once for the whole search; before ENG-15, once per record
    # (60 times each here, 5,300 x the candidates for the Mendeliome).
    assert casefolded == Counter(panel_genes)


def test_the_record_filter_reads_no_region_of_a_record_a_panel_gene_names(monkeypatch: pytest.MonkeyPatch) -> None:
    panel, records = _panel_and_records(genes=200, records=60)
    normalized: list[Any] = []
    normalize = queries.normalize_chromosome

    def counting_normalize(value: Any) -> str:
        normalized.append(value)
        return normalize(value)

    monkeypatch.setattr(queries, "normalize_chromosome", counting_normalize)
    kept = _kept_small_candidates(records, _FILTERS, _scope(panel), _CONTEXT)

    assert len(kept) == len(records)
    # Every record is named by a panel gene: no region was read. Before ENG-15 each record
    # read every region (200 regions x 60 records here).
    assert normalized == []


def test_a_record_no_panel_gene_names_is_found_in_the_regions_by_bisection(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.services.clickhouse_variant_queries import _SmallPanelMatch

    panel, _records = _panel_and_records(genes=200, records=1)
    unnamed = [
        _record("chr2", 10_000 * index + 250, symbols=("OTHERGENE",), variant_id=f"u{index}") for index in range(60)
    ]
    outside = _record("2", 10_000 * 7 + 501, symbols=("OTHERGENE",), variant_id="outside")
    match = _SmallPanelMatch(panel)
    normalized: list[Any] = []
    normalize = queries.normalize_chromosome

    def counting_normalize(value: Any) -> str:
        normalized.append(value)
        return normalize(value)

    monkeypatch.setattr(queries, "normalize_chromosome", counting_normalize)
    assert all(match.keeps(record) for record in unnamed)
    assert not match.keeps(outside)
    # Each region's chromosome is read once, when the lookup is built for the first record
    # that needs it, and each record's own once.
    assert len(normalized) == len(panel.regions) + len(unnamed) + 1


_CHROMOSOMES = ["1", "chr1", "CHR1", "X", "x", "chrX", "M", "MT", "chrM", " 7 ", "7", "GL000192.1", "", "chr"]
_GENES = ["BRCA1", "brca1", "BRCA2", "TP53", "  ", "", "Gene With Space", "ENSG00000012048"]
_ANNOTATION_KEYS = ["gene", "gene_id", "geneSymbol", "geneId", "hgnc_symbol", "hgncSymbol", "symbol"]


def _grid() -> tuple[list[SmallVariantRecord], list[PanelFilterConstraints]]:
    """Records of every chromosome spelling, around and inside the regions below, with gene
    names in their symbols or under each annotation key; panels of genes, of regions, of
    both and of neither, with blank and differently cased genes, reversed, one-base,
    nested and repeated regions, and regions without a chromosome."""
    records: list[SmallVariantRecord] = []
    for chrom in _CHROMOSOMES:
        for start, end in [(99, 99), (100, 100), (150, 160), (200, 200), (201, 201), (90, 100), (199, 300),
                           (250, 260), (500, 499), (1_000, 1_000), (5, 4_000)]:
            records.append(_record(chrom, start, end))
            records.append(_record(chrom, start, end, symbols=("brca1",)))
            for key in _ANNOTATION_KEYS:
                records.append(_record(chrom, start, end, annotations=({key: "Brca1"},)))
    records.append(_record("1", 150, 150, symbols=("",)))
    records.append(_record("1", 150, 150, annotations=({"gene": "  "},)))

    region_sets = [
        (),
        (Region(chr="chr1", start=100, end=200),),
        (Region(chr="1", start=200, end=100), Region(chr="X", start=150, end=150)),  # reversed; one base
        (Region(chr="CHR1", start=1, end=1_000_000), Region(chr="1", start=150, end=155)),  # nested
        (Region(chr="chrM", start=100, end=200), Region(chr="MT", start=100, end=200)),
        (Region(chr="", start=1, end=300), Region(chr="x", start=100, end=200)),  # no chromosome; lower case
        (Region(chr=" 7 ", start=150, end=150), Region(chr="7", start=150, end=150)),  # repeated
        (Region(chr="GL000192.1", start=0, end=99), Region(chr="chr", start=1, end=10_000)),
    ]
    gene_sets: list[tuple[str, ...]] = [(), ("BRCA1",), ("brca1", "TP53"), ("  ",), ("", "BRCA2"), ("ENSG00000012048",)]
    panels = [PanelFilterConstraints(genes=genes, regions=regions) for genes in gene_sets for regions in region_sets]
    return records, panels


def test_the_prepared_panel_keeps_exactly_what_the_per_row_rule_kept() -> None:
    from backend.app.services.clickhouse_variant_queries import _SmallPanelMatch

    records, panels = _grid()
    outcomes: Counter[bool] = Counter()
    for panel in panels:
        match = _SmallPanelMatch(panel)
        for record in records:
            expected = _rule(record, panel)
            assert match.keeps(record) is expected, (record, panel)
            # The record filter itself, with the panel prepared once or given as constraints.
            assert _small_record_matches(record, _FILTERS, [], [], [], panel_constraints=match) is expected
            assert _small_record_matches(record, _FILTERS, [], [], [], panel_constraints=panel) is expected
            outcomes[expected] += 1
    assert outcomes[True] and outcomes[False]


def test_the_region_lookup_answers_what_testing_every_region_answers() -> None:
    from backend.app.services.clickhouse_variant_queries import _RegionLookup

    rng = random.Random(15)
    chromosomes = ["1", "chr1", "2", "X", "chrM", "MT", ""]
    checked = Counter()
    for _case in range(300):
        regions = [
            Region(chr=rng.choice(chromosomes), start=rng.randint(-50, 2_000), end=rng.randint(-50, 2_000))
            for _ in range(rng.randint(0, 25))
        ]
        lookup = _RegionLookup.of(regions)
        for _query in range(40):
            start = rng.randint(-60, 2_010)
            end = start + rng.choice([-3, 0, 0, 1, 5, 80, 600])
            chrom = rng.choice(chromosomes)
            expected = _variant_overlaps_regions(chrom, start, end, regions)
            assert lookup.overlaps(chrom, start, end) is expected, (chrom, start, end, regions)
            checked[expected] += 1
    assert checked[True] > 500 and checked[False] > 500


def test_the_kept_candidates_are_the_per_row_rule_s_on_a_mendeliome_sized_panel() -> None:
    panel, records = _panel_and_records(genes=5_300, records=400)
    rng = random.Random(5300)
    # Half of them annotated with another symbol. A panel this size is matched on its genes
    # alone (``_panel_regions_apply``, ENG-1), so these are left out, in their gene's locus or
    # past it.
    for record in records[::2]:
        record.gene_symbols = ["RENAMED"]
        record.annotations = [{"gene": "RENAMED"}]
        if rng.random() < 0.5:
            record.start = record.end = record.start + 1_000  # past the gene's locus
    expected = [record.variant_id for record in records if _rule(record, panel)]
    kept = _kept_small_candidates(records, _FILTERS, _scope(panel), _CONTEXT)
    assert [record.variant_id for record in kept] == expected
    assert 0 < len(expected) < len(records)


# --------------------------------------------------------------------------- #
# The family page's panel-region condition: a join over the family's own rows  #
# --------------------------------------------------------------------------- #


def _panel_clause(filters: SmallVariantQueryFilters, panel: PanelFilterConstraints) -> tuple[str, dict[str, Any]]:
    clauses, params, _detail = _small_query_filter_parts(_CONTEXT, filters, panel_constraints=panel)
    (clause,) = [clause for clause in clauses if "panel_" in clause]
    return clause, params


def _join_rows_where(clause: str) -> str:
    """The WHERE of the rows the join reads."""
    head = f"FROM {_ENTRIES} AS fe ARRAY JOIN "
    assert head in clause, clause
    rows = clause.split(head, 1)[1]
    return rows.split(" AS variant_window WHERE ", 1)[1].split(") AS v INNER JOIN (", 1)[0]


_PANEL = PanelFilterConstraints(
    genes=("BRCA1",), regions=(Region(chr="chr17", start=43_044_295, end=43_125_483), Region(chr="9", start=5, end=1))
)


def test_the_family_page_joins_the_panel_s_regions_to_its_own_rows() -> None:
    clause, params = _panel_clause(_FILTERS, _PANEL)
    # The rows whose chromosome, position and REF length a row of the family that overlaps a
    # region has, found by the window join; not each row tested against every region.
    assert "(e.chrom, e.pos, length(e.ref)) IN (SELECT v.chrom, v.pos, v.ref_length FROM (" in clause
    assert "arrayExists((region_chrom" not in clause
    assert "AS v INNER JOIN (SELECT region.1 AS region_chrom" in clause
    assert queries._SMALL_REGION_WINDOW_MATCH in clause
    # The regions as the per-row test reads them.
    assert params["panel_region_chromosomes"] == ["17", "9"]
    assert params["panel_region_starts"] == [43_044_295, 1]
    assert params["panel_region_ends"] == [43_125_483, 5]
    # The family's own rows, in its projects.
    assert _join_rows_where(clause) == (
        "fe.family_guid = %(panel_region_family_guid)s AND fe.sign = 1"
        " AND fe.project_guid IN %(panel_region_project_ids)s"
    )
    assert params["panel_region_family_guid"] == params["family_guid"] == "eng15-family"
    assert params["panel_region_project_ids"] == params["project_ids"] == ("eng15-project-a", "eng15-project-b")
    # The panel's genes are read as before, beside the regions.
    assert "arrayExists(gene -> lower(gene) IN %(panel_gene_terms)s, e.gene_symbols)" in clause
    assert params["panel_annotation_gene_terms"] == ("brca1",)


@pytest.mark.parametrize(
    "locus",
    [
        {"chromosome": "17"},
        {"chromosome": "chr17", "start": 43_000_000, "end": 43_100_000},
        {"chromosome": "17", "start": 43_000_000, "end": 43_100_000, "overlap": True},
        {"chromosome": "MT", "start": 100},
        {"chromosome": "X", "end": 2_000_000},
        {"start": 100},
        {"end": 100, "overlap": True},
    ],
)
def test_the_join_reads_every_row_the_search_reads_within_its_locus(locus: dict[str, Any]) -> None:
    # The join's rows must hold every row the search reads, or a row in a region would be
    # left out: they are narrowed by the family, its projects and the search's own locus
    # clauses, nothing else, and those as the search's WHERE has them.
    filters = SmallVariantQueryFilters(page=1, page_size=100, **locus)
    clause, params = _panel_clause(filters, _PANEL)
    search_clauses, _params = _small_variant_where_clauses(_CONTEXT, filters)
    locus_clauses = [
        search_clause.replace("e.", "fe.")
        for search_clause in search_clauses
        if search_clause.startswith(("e.chrom", "e.pos", "(e.pos", "e.xpos"))
    ]
    assert locus_clauses or "chromosome" not in locus
    assert _join_rows_where(clause) == " AND ".join(
        [
            "fe.family_guid = %(panel_region_family_guid)s",
            "fe.sign = 1",
            "fe.project_guid IN %(panel_region_project_ids)s",
            *locus_clauses,
        ]
    )


def test_a_family_without_projects_reads_its_rows_in_every_project() -> None:
    context = replace(_CONTEXT, project_ids=[])
    clauses, params, _detail = _small_query_filter_parts(context, _FILTERS, panel_constraints=_PANEL)
    (clause,) = [clause for clause in clauses if "panel_" in clause]
    assert _join_rows_where(clause) == "fe.family_guid = %(panel_region_family_guid)s AND fe.sign = 1"
    assert "panel_region_project_ids" not in params
    assert not any(clause.startswith("e.project_guid") for clause in clauses)


def test_without_an_assembly_the_rule_is_tested_per_row() -> None:
    # No entries table to join without an assembly (no search reaches the condition then):
    # the regions fall back to the rule itself, tested against each row.
    from backend.app.services.clickhouse_variant_queries import (
        _small_family_region_condition,
        _small_region_filter_condition,
    )

    context = replace(_CONTEXT, assembly_name=None)
    params: dict[str, Any] = {}
    condition = _small_family_region_condition(context, _FILTERS)(_PANEL.regions, prefix="panel_region", params=params)
    assert condition == _small_region_filter_condition(_PANEL.regions, prefix="panel_region", params={})
    assert "panel_region_family_guid" not in params


def test_a_large_gene_panel_reads_no_region() -> None:
    # As before (CLIN-34): a panel of genes with more than 1,000 regions, such as the
    # Mendeliome, is matched on gene symbols alone; a panel of regions only, on its regions.
    regions = tuple(
        Region(chr="2", start=10_000 * index, end=10_000 * index + 500)
        for index in range(queries._PANEL_REGION_INLINE_LIMIT + 1)
    )
    clauses, params, _detail = _small_query_filter_parts(
        _CONTEXT, _FILTERS, panel_constraints=PanelFilterConstraints(genes=("BRCA1",), regions=regions)
    )
    assert not any("variant_window" in clause for clause in clauses)
    assert not any(name.startswith("panel_region") for name in params)
    clauses, params, _detail = _small_query_filter_parts(
        _CONTEXT, _FILTERS, panel_constraints=PanelFilterConstraints(regions=regions)
    )
    assert any("variant_window" in clause for clause in clauses)
    assert len(params["panel_region_chromosomes"]) == queries._PANEL_REGION_INLINE_LIMIT + 1


def test_the_intervals_and_excluded_regions_are_still_tested_per_row() -> None:
    # Only the panel's regions, which can be a thousand, are joined; an interval list and the
    # excluded genes' and intervals' regions are short and keep their per-row test.
    clauses, _params, _detail = _small_query_filter_parts(
        _CONTEXT,
        _FILTERS,
        include_regions=[Region(chr="1", start=1, end=100)],
        exclude_regions=[Region(chr="2", start=1, end=100)],
    )
    joined = " AND ".join(clauses)
    assert joined.count("arrayExists((region_chrom") == 2
    assert "variant_window" not in joined
