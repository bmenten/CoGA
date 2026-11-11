"""Applying a gene panel costs the panel once per search, not once per row.

The family search applied a panel's regions in SQL by testing each row against every region
(``arrayExists``), and its record filter (``_kept_small_candidates`` → ``_small_record_matches``)
casefolded the panel's gene names again for every candidate and scanned every region even
after a gene had matched: 1.5 to 4.4 s of a fresh Mendeliome ranking, which a compound-het,
recessive or carrier-screening search paid on an exact cache hit too.

* The region lookup the structural-variant panel filter reads (``_RegionLookup``) answers
  what testing every region answers. (The small-variant record filter, which applied the panel
  again in Python, is gone: the SQL is the one definition, the roadmap's *One filter
  definition*.)
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
from backend.app.services.clickhouse_variant_queries import (
    _small_query_filter_parts,
    _small_variant_where_clauses,
    _variant_overlaps_regions,
)
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
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
    # The panel's genes are read too, beside the regions.
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
    # its genes, whatever its size (owner decision 2026-10-14). A panel of genes with more than
    # 1,000 regions, such as the Mendeliome, has its regions joined to the family's rows on windows
    # as a smaller panel's are: once, the region arrays bound as parameters of the join, not tested
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
    # The genes too, beside the regions: the row's own symbols and the gene index.
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
