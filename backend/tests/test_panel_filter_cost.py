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
    the record's gene names, or the record in one of the panel's regions, whatever the
    panel's size. (A panel of genes with more than 1,000 regions was matched on its genes
    alone until the owner's decision of 2026-10-14.)"""
    if not (panel.genes or panel.regions):
        return True
    gene_match = bool(panel.genes) and _small_record_hits_gene_terms(record, panel.genes)
    region_match = bool(panel.regions) and _variant_overlaps_regions(
        record.chr, record.start, record.end, panel.regions
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
    # Half of them annotated with another symbol: kept by position in their gene's locus,
    # left out past it. A panel this size was matched on its genes alone, and left them all
    # out (owner decision 2026-10-14).
    renamed_in_locus: list[str] = []
    renamed_past_locus: list[str] = []
    for record in records[::2]:
        record.gene_symbols = ["RENAMED"]
        record.annotations = [{"gene": "RENAMED"}]
        if rng.random() < 0.5:
            record.start = record.end = record.start + 1_000  # past the gene's locus
            renamed_past_locus.append(record.variant_id)
        else:
            renamed_in_locus.append(record.variant_id)
    expected = [record.variant_id for record in records if _rule(record, panel)]
    kept = [record.variant_id for record in _kept_small_candidates(records, _FILTERS, _scope(panel), _CONTEXT)]
    assert kept == expected
    assert renamed_in_locus and set(renamed_in_locus) <= set(kept)
    assert renamed_past_locus and not set(renamed_past_locus) & set(kept)


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


def test_a_mendeliome_sized_gene_panel_joins_its_regions_beside_its_genes() -> None:
    # Every panel keeps a variant in one of its regions as well as one annotated with one of
    # its genes, whatever its size (owner decision 2026-10-14). A panel of genes with more
    # than 1,000 regions, such as the Mendeliome, was matched on gene symbols alone and read
    # no region. Its regions are now joined to the family's rows on windows as a smaller
    # panel's are: once, the region arrays bound as parameters of the join, not tested
    # against each row nor written into the query region by region.
    genes = tuple(f"PANELGENE{index:04d}" for index in range(5_300))
    regions = tuple(Region(chr="2", start=10_000 * index, end=10_000 * index + 500) for index in range(5_300))
    clause, params = _panel_clause(_FILTERS, PanelFilterConstraints(genes=genes, regions=regions))
    assert clause.count("AS v INNER JOIN (SELECT region.1 AS region_chrom") == 1
    assert "arrayExists((region_chrom" not in clause
    for name in ("panel_region_chromosomes", "panel_region_starts", "panel_region_ends"):
        assert clause.count(f"%({name})s") == 1
        assert len(params[name]) == 5_300
    assert not [name for name in params if name.startswith("panel_region_chromosomes_")]
    assert _join_rows_where(clause) == (
        "fe.family_guid = %(panel_region_family_guid)s AND fe.sign = 1"
        " AND fe.project_guid IN %(panel_region_project_ids)s"
    )
    # Beside the genes, read as before: the row's own symbols and the gene index.
    assert "arrayExists(gene -> lower(gene) IN %(panel_gene_terms)s, e.gene_symbols)" in clause
    assert len(params["panel_annotation_gene_terms"]) == 5_300


def _transcript_loci(genes: int) -> tuple[PanelFilterConstraints, list[Region]]:
    """A panel of ``genes`` genes 10 kb apart on chromosome 2, each with eight transcript
    loci nested in its span (a gene reference of one row per transcript), and the spans."""
    loci: list[Region] = []
    spans: list[Region] = []
    for index in range(genes):
        start = 10_000 * index + 1
        spans.append(Region(chr="2", start=start, end=start + 5_000))
        for transcript in range(8):
            loci.append(Region(chr="chr2", start=start + 100 * transcript, end=start + 5_000 - 50 * transcript))
        loci.append(Region(chr="2", start=start + 5_000, end=start))  # PanelApp's, reversed
    panel = PanelFilterConstraints(genes=tuple(f"PANELGENE{index:04d}" for index in range(genes)), regions=tuple(loci))
    return panel, spans


def test_a_mendeliome_sized_panel_binds_one_region_per_gene_not_one_per_transcript() -> None:
    # The Mendeliome resolves to some 42,000 loci in a gene reference of one row per
    # transcript. Bound one by one they made the family page's query 1.2 MB and its join's
    # added time two to four times longer (docs/variant-ranking-cache.md); the loci of a gene
    # share bases, and merge into its span.
    panel, spans = _transcript_loci(5_300)
    assert len(panel.regions) == 47_700
    clause, params = _panel_clause(_FILTERS, panel)
    assert params["panel_region_chromosomes"] == ["2"] * 5_300
    assert params["panel_region_starts"] == [span.start for span in spans]
    assert params["panel_region_ends"] == [span.end for span in spans]
    assert "AS v INNER JOIN (SELECT region.1 AS region_chrom" in clause


def _rule_on_bounds(chrom: str, pos: int, ref_length: int, bounds: tuple[list[str], list[int], list[int]]) -> bool:
    """The SQL overlap rule (``_small_region_filter_condition``) on bound regions: the same
    chromosome by match key, the REF's first base at or before a region's end and its last
    (``pos + length(ref) - 1``) at or after its start."""
    key = queries._chromosome_match_key(chrom)
    ref_end = pos + ref_length - 1
    return any(key == c and pos <= e and ref_end >= s for c, s, e in zip(*bounds))


def _unmerged_bounds(regions: list[Region]) -> tuple[list[str], list[int], list[int]]:
    """The regions as given, each read on its own (match key, start and end in order)."""
    kept = [
        (queries._chromosome_match_key(region.chr), min(region.start, region.end), max(region.start, region.end))
        for region in regions
        if queries._chromosome_match_key(region.chr)
    ]
    return [c for c, _s, _e in kept], [s for _c, s, _e in kept], [e for _c, _s, e in kept]


def test_regions_that_share_a_base_are_merged_and_keep_what_each_kept() -> None:
    from backend.app.services.clickhouse_variant_queries import _small_region_bounds

    # Nested, overlapping in a chain, and repeated: one region. Touching only: two.
    assert _small_region_bounds(
        [Region("1", 100, 300), Region("chr1", 150, 200), Region("1", 300, 250), Region("1", 260, 400)]
    ) == (["1"], [100], [400])
    assert _small_region_bounds([Region("1", 100, 200), Region("1", 201, 300)]) == (["1", "1"], [100, 201], [200, 300])
    assert _small_region_bounds([Region("chrM", 5, 10), Region("MT", 8, 20), Region("", 1, 9)]) == (["MT"], [5], [20])

    rng = random.Random(42_000)
    chromosomes = ["1", "chr1", "CHR1", "2", "X", "x", "chrM", "MT", "M", ""]
    checked: Counter[bool] = Counter()
    for _case in range(400):
        regions: list[Region] = []
        for _ in range(rng.randint(0, 30)):
            start = rng.randint(0, 300)
            length = rng.choice([0, 0, 1, 3, 10, 60])
            region = Region(rng.choice(chromosomes), start, start + length)
            regions.append(region if rng.random() < 0.8 else Region(region.chr, region.end, region.start))
            if rng.random() < 0.2:  # one that touches the region just made
                regions.append(Region(region.chr, max(region.start, region.end) + 1, max(region.start, region.end) + 5))
        merged = _small_region_bounds(regions)
        # Sorted, and no two regions of a chromosome share a base.
        pairs = list(zip(merged[0], merged[1], merged[2]))
        assert pairs == sorted(pairs)
        assert all(not (a[0] == b[0] and b[1] <= a[2]) for a, b in zip(pairs, pairs[1:]))
        unmerged = _unmerged_bounds(regions)
        for _row in range(60):
            chrom = rng.choice(chromosomes[:-1])
            pos = rng.randint(0, 320)
            ref_length = rng.choice([0, 1, 1, 2, 5, 40])  # no base: no VCF record has one
            expected = _rule_on_bounds(chrom, pos, ref_length, unmerged)
            assert _rule_on_bounds(chrom, pos, ref_length, merged) is expected, (chrom, pos, ref_length, regions)
            checked[expected] += 1
    assert checked[True] > 1_000 and checked[False] > 1_000


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
