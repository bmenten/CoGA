"""A gene panel of any size keeps an SV named by one of its genes or in one of its regions.

The owner decided on 2026-10-20 that the structural-variant page follows the rule of the
small-variant page (#897): a gene panel of any size, the Mendeliome included, keeps an SV
whose annotation names one of its genes (the entry's gene symbols, in any capitals) or that
overlaps one of its regions (PanelApp's regions for the assembly and each gene's loci in the
gene reference), by the SV region rule: on the same chromosome, the SV's start at or before
the region's end and its end at or after the region's start; its breakend mate is not read.

Until then a panel of more than 1,000 regions (``_PANEL_REGION_INLINE_LIMIT``) was matched on
the genes an SV's annotation names alone, and only in Python, after the 50,000-SV candidate
cap; a smaller panel on its regions alone. The Mendeliome, the page's default, missed an SV in
one of its genes whose annotation names the gene otherwise, and a smaller panel an SV whose
annotation names one of its genes beside it (VEP's upstream and downstream genes).

Here, on synthetic SVs: each path of the SV search (the list, ranked or not, the genome track,
the CSV export) keeps the same SVs for a panel of three regions and for one of more than
1,000; the SQL holds the panel's regions, bound once as array parameters and joined to the
family's own rows, and its genes; the track availability offers a member's SV track for the
SVs the panel keeps; a panel and a gene filter both apply. The regions the SQL binds leave
out only the regions inside another, which keeps every answer, an SV stored with its END
before its POS included; merging the regions that share a base, as the small-variant
condition does, would not.
"""

from __future__ import annotations

import random
from typing import Any
from uuid import UUID

import pytest
from clickhouse_connect.driver.binding import bind_query

from backend.app.services import clickhouse_family_variants, family_service
from backend.app.services.clickhouse_family_variants import (
    export_family_structural_variants,
    get_family_structural_variants_page,
)
from backend.app.services.clickhouse_variant_queries import (
    _chromosome_match_key,
    _small_region_bounds,
    _structural_record_matches,
)
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_service import get_family_track_availability_for_user
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

_PANEL_ID = str(UUID(int=20261020))
_M = "backend.app.services.clickhouse_family_variants."

# The panel's genes and the loci the gene reference gives them: SVPANA two transcripts in a
# chain on chromosome 1, SVPANB one on chromosome 2.
_SVPANA_LOCI = [Region(chr="1", start=1_000_000, end=1_060_000), Region(chr="chr1", start=1_040_000, end=1_100_000)]
_SVPANB_LOCUS = Region(chr="2", start=5_000_000, end=5_050_000)
_SMALL_PANEL = PanelFilterConstraints(genes=("SVPANA", "SVPANB"), regions=(*_SVPANA_LOCI, _SVPANB_LOCUS))
# The same genes and 1,000 more, each with a locus on chromosome 21, where the family has no
# SV: a panel of more than 1,000 regions, as the Mendeliome is.
_FILLERS = tuple(f"SVFILL{index:04d}" for index in range(1_000))
_LARGE_PANEL = PanelFilterConstraints(
    genes=(*_SMALL_PANEL.genes, *_FILLERS),
    regions=(
        *_SMALL_PANEL.regions,
        *(Region(chr="21", start=30_000_000 + 1_000 * index, end=30_000_000 + 1_000 * index + 500) for index in range(1_000)),
    ),
)
_PANELS = {"three regions": _SMALL_PANEL, "more than 1,000 regions": _LARGE_PANEL}


def _sv(
    variant_id: str,
    chrom: str,
    start: int,
    end: int,
    genes: list[str],
    *,
    sv_type: str = "DEL",
    sample: str = "PROBAND",
) -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=start,
        end=end,
        sv_type=sv_type,
        source="hificnv",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=end - start,
        filters=["PASS"],
        gene_symbols=genes,
        annotations=[{}],
        calls=[StructuralVariantCall(sample=sample, gt="0/1", qual=None, read_support=None, filter="PASS")],
    )


# In SVPANA's locus, its annotation naming the gene otherwise (an earlier symbol).
_POSITION_ONLY = _sv("sv-position-only", "1", 1_050_000, 1_055_000, ["OLDSVPANA"])
# 5 kb before SVPANB's locus, its annotation naming SVPANB (VEP's upstream gene).
_NAME_ONLY = _sv("sv-name-only", "2", 4_990_000, 4_995_000, ["SVPANB"], sample="MOTHER")
# In SVPANB's locus and naming it, in lower case.
_BOTH = _sv("sv-both", "2", 5_010_000, 5_020_000, ["svpanb"], sv_type="DUP")
# A breakend on SVPANB's last base, and one a base beyond it, naming no gene.
_ON_LAST_BASE = _sv("sv-on-last-base", "2", 5_050_000, 5_050_000, [], sv_type="BND")
_A_BASE_BEYOND = _sv("sv-a-base-beyond", "2", 5_050_001, 5_050_001, [], sv_type="BND")
# An SV stored with its END before its POS, over the two SVPANA loci where they chain: by the
# rule it overlaps a region that holds both its END and its POS, and neither locus does.
_END_BEFORE_POS = _sv("sv-end-before-pos", "1", 1_070_000, 1_030_000, [])
# Elsewhere, naming another gene.
_NEITHER = _sv("sv-neither", "3", 7_000_000, 7_100_000, ["UNRELATED"], sample="FATHER")

_CALLSET = [_POSITION_ONLY, _END_BEFORE_POS, _NAME_ONLY, _BOTH, _ON_LAST_BASE, _A_BASE_BEYOND, _NEITHER]
_KEPT = {"sv-position-only", "sv-name-only", "sv-both", "sv-on-last-base"}


def _family() -> FamilyMetadataContext:
    names = {"PROBAND": "sample-proband", "MOTHER": "sample-mother", "FATHER": "sample-father"}
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": name} for name in names],
        sample_uuid_to_name={uuid: name for name, uuid in names.items()},
        sample_name_to_uuid=dict(names),
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _stored_row(record: StructuralVariantRecord, key: int) -> tuple[Any, ...]:
    """``record`` as ClickHouse returns it to the SV candidate read."""
    return (
        key,
        record.variant_id,
        record.chr,
        record.start,
        record.end,
        record.sv_type,
        record.source,
        None,
        None,
        record.sv_len,
        list(record.filters),
        "",
        list(record.gene_symbols),
        [call.sample for call in record.calls],
        [call.gt for call in record.calls],
        [call.qual for call in record.calls],
        [call.read_support for call in record.calls],
        [call.filter for call in record.calls],
        [call.copy_number for call in record.calls],
    )


def _serve(
    monkeypatch: pytest.MonkeyPatch, panel: PanelFilterConstraints, records: list[StructuralVariantRecord]
) -> list[tuple[str, dict[str, Any]]]:
    """ClickHouse holding ``records`` (every query gets all their rows: the record filter
    decides), the panel resolved as ``panel``, and Postgres without reviews, cytobands or
    phenotypes. Returns the queries sent, with their parameters."""
    sent: list[tuple[str, dict[str, Any]]] = []
    rows = [_stored_row(record, key) for key, record in enumerate(records, start=1)]

    async def execute(query: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        sent.append((query, dict(params)))
        return rows

    async def resolve(_session: Any, panel_id: str, *, assembly_id: str | None = None) -> PanelFilterConstraints:
        assert panel_id == _PANEL_ID and assembly_id == "assembly-uuid"
        return panel

    async def no_review_ids(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_phenotypes(*_args: Any, **_kwargs: Any) -> tuple[list[str], dict[str, str]]:
        return [], {}

    async def gene_loci(_session: Any, *, gene_query: str, assembly_id: str | None) -> list[Region]:
        return {"OTHERGENE": [Region(chr="3", start=6_900_000, end=7_200_000)], "SVPANB": [_SVPANB_LOCUS]}[gene_query]

    monkeypatch.setattr(_M + "_execute_clickhouse", execute)
    monkeypatch.setattr(clickhouse_family_variants, "_fetch_panel_constraints", resolve)
    monkeypatch.setattr(family_service, "_fetch_panel_constraints", resolve, raising=False)
    monkeypatch.setattr(_M + "list_matching_structural_variant_review_ids", no_review_ids)
    monkeypatch.setattr(_M + "get_structural_variant_review_map", nothing)
    monkeypatch.setattr(_M + "_fetch_structural_cytoband_map", nothing)
    monkeypatch.setattr(_M + "_affected_present_hpo", no_phenotypes)
    monkeypatch.setattr(_M + "_fetch_gene_regions", gene_loci)
    return sent


async def _kept_by(path: str, **filters: Any) -> set[str]:
    query = StructuralVariantQueryFilters(page=1, page_size=100, panel_id=_PANEL_ID, **filters)
    if path == "export":
        export = await export_family_structural_variants(None, context=_family(), filters=query)  # type: ignore[arg-type]
        return {str(row.id) for row in export.rows}
    page = await get_family_structural_variants_page(
        None,  # type: ignore[arg-type]
        context=_family(),
        page=1,
        page_size=100,
        filters=query,
        prioritize=path == "ranked",
        track_mode=path == "track",
    )
    return {str(variant.id) for variant in page.variants}


# --------------------------------------------------------------------------- #
# Every path of the SV search, for a small and a Mendeliome-sized panel
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["list", "ranked", "track", "export"])
@pytest.mark.parametrize("panel", list(_PANELS))
async def test_a_panel_of_any_size_keeps_the_svs_named_by_its_genes_or_in_its_regions(
    monkeypatch: pytest.MonkeyPatch, panel: str, path: str
) -> None:
    # Before: the panel of three regions left out the SV its annotation links to SVPANB
    # beside the gene, and the panel of more than 1,000 the SV in SVPANA's locus that names
    # the gene otherwise and the breakend on SVPANB's last base.
    _serve(monkeypatch, _PANELS[panel], _CALLSET)

    assert await _kept_by(path) == _KEPT


@pytest.mark.asyncio
@pytest.mark.parametrize("panel", list(_PANELS))
async def test_the_track_draws_a_members_svs_the_panel_keeps(monkeypatch: pytest.MonkeyPatch, panel: str) -> None:
    _serve(monkeypatch, _PANELS[panel], _CALLSET)

    assert await _kept_by("track", selected_samples=["MOTHER"]) == {"sv-name-only"}
    assert await _kept_by("track", selected_samples=["PROBAND"]) == _KEPT - {"sv-name-only"}


@pytest.mark.asyncio
@pytest.mark.parametrize("prioritize", [False, True])
@pytest.mark.parametrize("panel", list(_PANELS))
async def test_the_sql_holds_the_panels_regions_and_genes_whatever_its_size(
    monkeypatch: pytest.MonkeyPatch, panel: str, prioritize: bool
) -> None:
    # Before: a panel of more than 1,000 regions reached no SQL. The candidate read took the
    # first 50,000 SVs of the callset and only then kept the panel's, so the cap decided which
    # SVs the panel was matched against.
    sent = _serve(monkeypatch, _PANELS[panel], _CALLSET)

    await _kept_by("ranked" if prioritize else "list")

    candidate_reads = [(query, params) for query, params in sent if "any(d.annotationsJson)" in query]
    assert len(candidate_reads) == 1
    query, params = candidate_reads[0]
    # The family's own SV rows joined to the regions on windows, or the gene names.
    assert "(e.chrom, e.start, e.end) IN (SELECT v.chrom, v.sv_start, v.sv_end FROM (" in query
    assert "INNER JOIN (SELECT region.1 AS region_chrom" in query
    assert "arrayExists(gene -> lower(gene) IN %(sv_panel_gene_terms)s, e.gene_symbols)" in query
    assert set(params["sv_panel_gene_terms"]) == {gene.casefold() for gene in _PANELS[panel].genes}
    # Each region inside no other, bound once as an array parameter.
    from backend.app.services.clickhouse_variant_queries import _structural_region_bounds

    expected = _structural_region_bounds(_PANELS[panel].regions)
    assert (params["sv_panel_region_chromosomes"], params["sv_panel_region_starts"], params["sv_panel_region_ends"]) == expected
    assert query.count("%(sv_panel_region_starts)s") == 1
    assert params["sv_panel_region_family_guid"] == "family-uuid"
    assert params["sv_panel_region_project_ids"] == ("project-uuid",)


@pytest.mark.asyncio
async def test_a_search_of_one_chromosome_binds_only_the_regions_on_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # A genome track asks for one chromosome: no region on another can overlap a row it reads.
    sent = _serve(monkeypatch, _LARGE_PANEL, _CALLSET)

    kept = await _kept_by(
        "track", chromosome="chr2", start=4_900_000, end=5_100_000, overlap=True, selected_samples=["PROBAND"]
    )

    assert kept == {"sv-both", "sv-on-last-base"}
    params = next(params for _query, params in sent if "sv_panel_region_starts" in params)
    assert (params["sv_panel_region_chromosomes"], params["sv_panel_region_starts"], params["sv_panel_region_ends"]) == (
        ["2"],
        [5_000_000],
        [5_050_000],
    )
    assert len(params["sv_panel_gene_terms"]) == len(_LARGE_PANEL.genes)


@pytest.mark.asyncio
async def test_a_mendeliome_sized_panel_is_bound_not_written_into_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    hostile = "SVX') OR 1=1 --"
    panel = PanelFilterConstraints(genes=(*_LARGE_PANEL.genes, hostile), regions=_LARGE_PANEL.regions)
    sent = _serve(monkeypatch, panel, _CALLSET)

    await _kept_by("list")

    query, params = next((query, params) for query, params in sent if "any(d.annotationsJson)" in query)
    # No coordinate and no gene is written into the query: they are parameters.
    assert "30999000" not in query and "SVFILL0999" not in query.upper()
    assert hostile not in query
    rendered, _ = bind_query(query, params)
    assert "'svx\\') or 1=1 --'" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("panel", list(_PANELS))
async def test_a_panel_and_a_gene_filter_both_apply(monkeypatch: pytest.MonkeyPatch, panel: str) -> None:
    # A gene outside the panel, with an SV in its locus that names it: the panel leaves it
    # out. Before, a panel of up to 1,000 regions was read with the gene's loci as one list of
    # regions, so an SV in either was kept: the gene filter set aside the panel.
    other = _sv("sv-other-gene", "3", 7_000_000, 7_100_000, ["OTHERGENE"])
    _serve(monkeypatch, _PANELS[panel], [*_CALLSET, other])

    assert await _kept_by("list", gene="OTHERGENE") == set()
    # A gene of the panel: its SVs in its locus that name it.
    assert await _kept_by("list", gene="SVPANB") == {"sv-both"}


@pytest.mark.asyncio
async def test_a_panel_without_genes_or_regions_keeps_no_sv(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _serve(monkeypatch, PanelFilterConstraints(), _CALLSET)

    assert await _kept_by("list") == set()
    assert sent == []


# --------------------------------------------------------------------------- #
# The track availability offers a member's SV track for the SVs the panel keeps
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("panel", list(_PANELS))
async def test_the_track_availability_applies_the_panel_as_the_track_does(
    monkeypatch: pytest.MonkeyPatch, panel: str
) -> None:
    # The mother's only SV lies beside SVPANB, its annotation naming the gene. Before, the
    # availability matched a panel's regions alone, so her SV track was not offered although
    # the track drew that SV with a panel of more than 1,000 regions (and, with this change,
    # with every panel).
    sent = _serve(monkeypatch, _PANELS[panel], _CALLSET)
    service = "backend.app.services.family_service."

    async def family(*_args: Any, **_kwargs: Any) -> FamilyMetadataContext:
        return _family()

    async def no_sources(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_presence(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def presence_aggregate(*_args: Any, **_kwargs: Any) -> set[str]:
        raise AssertionError("a panel is applied to the SV rows, not by the presence aggregate")

    monkeypatch.setattr(service + "build_family_metadata_context", family)
    monkeypatch.setattr(service + "get_track_sources_by_sample", no_sources)
    monkeypatch.setattr(service + "get_track_presence_by_sample", no_presence)
    monkeypatch.setattr(service + "_repeat_expansion_presence_by_sample", no_presence)
    monkeypatch.setattr(service + "_structural_present_sample_names", presence_aggregate)

    availability = await get_family_track_availability_for_user(
        None,  # type: ignore[arg-type]
        family_id="FAM001",
        user=None,  # type: ignore[arg-type]
        chromosomes=["1", "2", "3", "21"],
        panel_id=_PANEL_ID,
        include_small_variants=False,
    )

    assert {name: sample.variants for name, sample in availability.samples.items()} == {
        "PROBAND": True,
        "MOTHER": True,
        "FATHER": False,
    }
    # The SV read applies the panel in SQL too.
    assert any("%(sv_panel_gene_terms)s" in query for query, _params in sent)


# --------------------------------------------------------------------------- #
# The rule, SV by SV
# --------------------------------------------------------------------------- #


def _kept(panel: PanelFilterConstraints, records: list[StructuralVariantRecord]) -> set[str]:
    from backend.app.services.clickhouse_variant_queries import _StructuralPanelMatch

    query = StructuralVariantQueryFilters(page=1, page_size=100)
    match = _StructuralPanelMatch(panel)
    return {record.variant_id for record in records if _structural_record_matches(record, query, [], [], panel=match)}


@pytest.mark.parametrize("panel", list(_PANELS))
def test_the_record_filter_keeps_an_sv_named_by_a_panel_gene_or_in_a_panel_region(panel: str) -> None:
    assert _kept(_PANELS[panel], _CALLSET) == _KEPT


def test_the_genes_are_matched_in_any_capitals_and_only_whole() -> None:
    panel = PanelFilterConstraints(genes=("BRCA1",))
    named = [
        _sv("upper", "17", 1, 2, ["BRCA1", "NBR2"]),
        _sv("lower", "17", 1, 2, ["brca1"]),
        _sv("part", "17", 1, 2, ["BRCA1-AS1"]),
        _sv("none", "17", 1, 2, []),
    ]
    assert _kept(panel, named) == {"upper", "lower"}


def test_a_panel_of_regions_alone_keeps_the_svs_in_them() -> None:
    panel = PanelFilterConstraints(regions=(_SVPANB_LOCUS,))
    assert _kept(panel, _CALLSET) == {"sv-both", "sv-on-last-base"}


def test_a_panel_without_genes_or_regions_keeps_none() -> None:
    assert _kept(PanelFilterConstraints(), _CALLSET) == set()


# --------------------------------------------------------------------------- #
# The regions the SQL binds keep every answer
# --------------------------------------------------------------------------- #


def test_the_bound_regions_leave_out_those_inside_another() -> None:
    from backend.app.services.clickhouse_variant_queries import _structural_region_bounds

    regions = [
        Region(chr="chr1", start=900, end=1_200),
        Region(chr="1", start=950, end=1_000),  # inside the first
        Region(chr="1", start=1_150, end=1_100),  # inside the first, reversed
        Region(chr="1", start=900, end=1_200),  # repeated
        Region(chr="1", start=1_100, end=1_500),  # a chain: kept
        Region(chr="1", start=1_501, end=1_600),  # touching only: kept
        Region(chr="chrM", start=10, end=20),
        Region(chr="MT", start=10, end=20),  # chrM by another name, repeated
        Region(chr="", start=1, end=10),  # no chromosome: left out
    ]
    assert _structural_region_bounds(regions) == (
        ["1", "1", "1", "MT"],
        [900, 1_100, 1_501, 10],
        [1_200, 1_500, 1_600, 20],
    )


def _overlaps_a_region(record: StructuralVariantRecord, regions: list[Region]) -> bool:
    """The SV region rule tested against each region on its own (reversed ones read in order)."""
    return any(
        _chromosome_match_key(record.chr) == _chromosome_match_key(region.chr)
        and record.start <= max(region.start, region.end)
        and record.end >= min(region.start, region.end)
        for region in regions
        if _chromosome_match_key(region.chr)
    )


def test_the_record_filter_answers_what_each_region_answers_on_its_own() -> None:
    # Regions in nests and chains, as a gene's transcript loci lie, touching and reversed
    # ones, on several spellings of a chromosome; SVs of every span, an END before its POS
    # included.
    from backend.app.services.clickhouse_variant_queries import _StructuralPanelMatch

    rng = random.Random(20261020)
    regions: list[Region] = []
    for _gene in range(60):
        chrom = rng.choice(["1", "chr1", "X", "chrX"])
        start = rng.randint(1, 50_000)
        for _transcript in range(rng.randint(1, 6)):
            first = start + rng.randint(0, 400)
            last = first + rng.randint(0, 900)
            regions.append(Region(chr=chrom, start=last, end=first) if rng.random() < 0.1 else Region(chr=chrom, start=first, end=last))
    records = []
    for index in range(6_000):
        position = rng.randint(1, 52_000)
        span = rng.choice([0, 0, 1, 5, 50, 500, 5_000, -1, -2, -30, -700])
        records.append(_sv(f"sv-{index}", rng.choice(["1", "X", "2"]), position, max(position + span, 0), []))
    match = _StructuralPanelMatch(PanelFilterConstraints(regions=tuple(regions)))

    assert [match.keeps(record) for record in records] == [_overlaps_a_region(record, regions) for record in records]
    # Not vacuous: SVs on either side, ENDs before their POS among those kept.
    kept = [record for record in records if match.keeps(record)]
    assert 0 < len(kept) < len(records)
    assert any(record.end < record.start - 1 for record in kept)


def test_merging_the_regions_that_share_a_base_would_change_an_answer() -> None:
    # Why the SV condition does not bind the regions merged, as the small-variant condition
    # does: the two SVPANA loci merge into one region holding both the END and the POS of
    # the SV stored with its END before its POS, and neither locus holds both.
    merged = PanelFilterConstraints(
        regions=tuple(Region(chr=c, start=s, end=e) for c, s, e in zip(*_small_region_bounds(_SVPANA_LOCI)))
    )
    assert len(merged.regions) == 1
    assert _kept(merged, [_END_BEFORE_POS]) == {"sv-end-before-pos"}
    assert _kept(PanelFilterConstraints(regions=tuple(_SVPANA_LOCI)), [_END_BEFORE_POS]) == set()


@pytest.mark.asyncio
async def test_without_an_assembly_the_regions_are_tested_row_by_row(monkeypatch: pytest.MonkeyPatch) -> None:
    # No entries table to join: the read answers nothing (no SV table), and the condition it
    # would send tests each region on its own.
    from backend.app.services.clickhouse_family_variants import _fetch_structural_variant_rows
    from backend.app.services.clickhouse_variant_queries import (
        _structural_region_bounds,
        _structural_variant_where_clauses,
    )

    context = _family()
    context.assembly_name = None
    clauses, params = _structural_variant_where_clauses(
        context, StructuralVariantQueryFilters(page=1, page_size=10), panel=_SMALL_PANEL
    )
    panel_clause = clauses[-1]
    assert "arrayExists((region_chrom, region_start, region_end)" in panel_clause
    assert "INNER JOIN" not in panel_clause
    assert params["sv_panel_region_starts"] == _structural_region_bounds(_SMALL_PANEL.regions)[1]
    assert await _fetch_structural_variant_rows(context, StructuralVariantQueryFilters(page=1, page_size=10), panel=_SMALL_PANEL) == []
