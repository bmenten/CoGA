"""The SV search keeps, for a gene panel of any size, the SVs named by its genes or in its regions.

The owner decided on 2026-10-20 that the structural-variant page follows the small-variant
rule (#897): a gene panel of any size, the Mendeliome included, keeps an SV whose annotation
names one of its genes or that overlaps one of its regions. Until then a panel of more than
1,000 regions was matched on the genes an SV's annotation names alone, in Python, after the
50,000-SV candidate cap; a smaller panel on its regions alone.

Two checks, in ClickHouse:

* The panel's condition (``_structural_panel_filter_condition``: the family's own SV rows joined
  to the panel's regions on 64-kb windows, or the gene names) keeps exactly the rows that
  testing each region on its own (``arrayExists``), or the genes, keeps, and the rows the
  record filter (``_StructuralPanelMatch``) keeps, on synthetic SV rows: every chromosome
  spelling a store may hold, spans of one base to several windows on each side of window
  boundaries, ENDs before their POS, and regions nested, chained, touching, reversed, repeated
  and without a chromosome. The rows lie in the family's two projects, beside rows of another
  family, of a project the search does not read, and collapsed (``sign = -1``); without a
  locus and within several. The regions are bound with those inside another left out
  (``_structural_region_bounds``); merged as the small-variant condition merges them, they
  would keep an SV stored with its END before its POS that no region holds. The rows are a
  subquery in place of the entries table, so no table is written here.
* The SV search itself (``get_family_structural_variants_page``) over rows written to the SV
  tables, with the panel of two genes and with the same genes and 1,000 more: unranked, ranked
  and as the genome track, it keeps the SV named by a panel gene beside its locus, the SV in
  a gene's locus named otherwise and the SV named and in it, and the panel narrows the read in
  SQL, so a candidate cap (lowered here to three) counts the panel's SVs, not the callset's.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke``
job sets it.
"""

from __future__ import annotations

import asyncio
from itertools import product
from typing import Any
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.integration

_WINDOW = 1 << 16
_CHROMOSOMES = ["1", "chr1", "X", "chrX", "M", "MT", "chrM", "GL000192.1"]
_FAMILY = "sv-panel-family"
_PROJECTS = ["sv-panel-project-a", "sv-panel-project-b"]
_GENES = ("SVPGENE", "SvpOther")


def _positions() -> list[int]:
    """Positions on each side of two window boundaries, and a few in between."""
    positions: set[int] = {1, 2, 950, 1_000, 1_160, 30_000}
    for boundary in (_WINDOW, 2 * _WINDOW):
        positions.update(boundary + offset for offset in range(-5, 6, 2))
    return sorted(positions)


def _spans() -> list[int]:
    """END minus POS: one base, short and long spans, a span over several windows, and ENDs
    one, two and many bases before their POS."""
    return [0, 1, 40, 3 * _WINDOW, -1, -2, -90]


def _names(index: int) -> list[str]:
    return [[], ["svpgene"], ["SVPOTHER", "X1"], ["SVPGENE-AS1"], ["UNRELATED"]][index % 5]


def _rows() -> list[tuple[int, str, int, int, list[str], str, str, int]]:
    """(key, chromosome, start, end, gene symbols, family, project, sign): each shape once for
    the family, in one of its two projects in turn, and once as another family's row, as a
    row of a project the search does not read, and as a collapsed row of the family."""
    placements = [
        *((_FAMILY, project, 1) for project in _PROJECTS),
        ("sv-panel-other-family", _PROJECTS[0], 1),
        (_FAMILY, "sv-panel-other-project", 1),
        (_FAMILY, _PROJECTS[0], -1),
    ]
    rows = []
    for index, (chromosome, position, span) in enumerate(product(_CHROMOSOMES, _positions(), _spans())):
        end = max(position + span, 0)
        for family, project, sign in [placements[index % 2], *placements[2:]]:
            rows.append((len(rows) + 1, chromosome, position, end, _names(index), family, project, sign))
    # An SV stored with its END before its POS over two loci that chain on chromosome 2: no
    # region holds both its END and its POS, the two merged would.
    for family, project, sign in placements:
        rows.append((len(rows) + 1, "2", 1_150, 1_050, [], family, project, sign))
    return rows


def _regions() -> list[Any]:
    from backend.app.services.clickhouse_variant_records import Region

    return [
        Region(chr="chr1", start=_WINDOW, end=_WINDOW + 2),  # starts on a boundary
        Region(chr="1", start=2 * _WINDOW - 3, end=2 * _WINDOW - 1),  # ends before one
        Region(chr="1", start=2 * _WINDOW - 1, end=2 * _WINDOW - 1),  # one base
        Region(chr="1", start=_WINDOW + 2, end=_WINDOW),  # reversed
        Region(chr="chr1", start=_WINDOW, end=_WINDOW + 2),  # repeated
        Region(chr="X", start=2 * _WINDOW - 4, end=2 * _WINDOW + 1),  # across a boundary
        Region(chr="chrM", start=900, end=1_100),  # M, read as MT
        Region(chr="MT", start=_WINDOW - 1, end=_WINDOW - 1),
        Region(chr="GL000192.1", start=1, end=3 * _WINDOW + 5),  # several windows
        Region(chr="", start=1, end=10),  # no chromosome: left out
        # A gene's transcript loci: nested, and in a chain across a window boundary.
        Region(chr="1", start=900, end=1_200),
        Region(chr="chr1", start=950, end=1_000),  # nested
        Region(chr="1", start=1_150, end=1_100),  # nested, reversed
        Region(chr="X", start=_WINDOW - 6, end=_WINDOW - 3),
        Region(chr="x", start=_WINDOW - 3, end=_WINDOW + 1),
        Region(chr="chrX", start=_WINDOW + 1, end=_WINDOW + 4),
        Region(chr="MT", start=29_990, end=29_999),  # touching only
        Region(chr="chrM", start=30_000, end=30_001),
        # Two loci that chain on chromosome 2, around the SV with its END before its POS.
        Region(chr="2", start=1_000, end=1_100),
        Region(chr="2", start=1_080, end=1_200),
    ]


def _rows_subquery(params: dict[str, Any]) -> str:
    """The synthetic rows, typed as the SV entries table types them."""
    for name, column in zip(
        ("keys", "chromosomes", "starts", "ends", "genes", "families", "projects", "signs"), zip(*_rows())
    ):
        params[f"rows_{name}"] = list(column)
    return (
        "(SELECT toUInt64(row.1) AS key, toLowCardinality(row.2) AS chrom, toUInt32(row.3) AS start,"
        " toUInt32(row.4) AS `end`, CAST(row.5, 'Array(String)') AS gene_symbols, row.6 AS family_guid,"
        " toLowCardinality(row.7) AS project_guid, toInt8(row.8) AS sign"
        " FROM (SELECT arrayJoin(arrayZip(%(rows_keys)s, %(rows_chromosomes)s, %(rows_starts)s,"
        " %(rows_ends)s, %(rows_genes)s, %(rows_families)s, %(rows_projects)s, %(rows_signs)s)) AS row))"
    )


def _each_region_bounds(regions: Any) -> tuple[list[str], list[int], list[int]]:
    """The regions each read on their own (match key, start and end in order, each once): the
    reference the bound regions must answer alike."""
    from backend.app.services.clickhouse_variant_queries import _chromosome_match_key

    kept = dict.fromkeys(
        (_chromosome_match_key(region.chr), min(region.start, region.end), max(region.start, region.end))
        for region in regions
        if _chromosome_match_key(region.chr)
    )
    return [c for c, _s, _e in kept], [s for _c, s, _e in kept], [e for _c, _s, e in kept]


# The searches: none narrowed to a locus, and loci that cut through regions and rows, with and
# without the overlap rule a locus can apply.
_SEARCHES: dict[str, dict[str, Any]] = {
    "no locus": {},
    "chromosome 1, overlapping a window boundary": {
        "chromosome": "1", "start": _WINDOW - 3, "end": _WINDOW + 4, "overlap": True,
    },
    "chr1, contained in a locus": {"chromosome": "chr1", "start": 900, "end": 2 * _WINDOW},
    "X, from a position on": {"chromosome": "X", "start": _WINDOW - 2},
    "MT, up to a position": {"chromosome": "MT", "end": _WINDOW},
    "the unplaced contig, overlapping": {
        "chromosome": "GL000192.1", "start": 2 * _WINDOW, "end": 2 * _WINDOW + 3, "overlap": True,
    },
}


def test_the_sv_panel_join_keeps_what_each_region_or_gene_keeps() -> None:
    from backend.app.core.clickhouse import close_clickhouse_client, execute_clickhouse, init_clickhouse_schema
    from backend.app.services import clickhouse_variant_queries as queries
    from backend.app.services.clickhouse_variant_ids import _structural_table_name
    from backend.app.services.clickhouse_variant_queries import (
        _small_gene_filter_condition,
        _small_region_bounds,
        _structural_record_matches,
        _structural_region_filter_condition,
        _structural_variant_where_clauses,
        _StructuralPanelMatch,
    )
    from backend.app.services.clickhouse_variant_records import PanelFilterConstraints, StructuralVariantRecord
    from backend.app.services.data_scope import normalize_chromosome
    from backend.app.services.family_metadata_context import FamilyMetadataContext
    from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

    context = FamilyMetadataContext(
        family_uuid=_FAMILY,
        family_id="SVPANEL",
        project_ids=list(_PROJECTS),
        sample_rows=[{"sample_id": "PROBAND"}],
        sample_uuid_to_name={},
        sample_name_to_uuid={"PROBAND": "PROBAND"},
        affected_sample_names=["PROBAND"],
        assembly_id="sv-panel-assembly",
        assembly_name="GRCh38",
    )
    panel = PanelFilterConstraints(genes=_GENES, regions=tuple(_regions()))
    entries = _structural_table_name("GRCh38", "entries")
    rows_params: dict[str, Any] = {}
    rows = _rows_subquery(rows_params)

    def search_where(filters: Any, bounds: Any) -> tuple[str, dict[str, Any]]:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(queries, "_structural_region_bounds", bounds)
            clauses, params = _structural_variant_where_clauses(context, filters, panel=panel)
        # The calls are what a search reads besides the rows' place; these rows have none.
        clauses = [clause for clause in clauses if not clause.startswith("hasAny(e.calls.sampleId")]
        where = " AND ".join(clauses)
        assert f"FROM {entries} AS fe" in where
        return where.replace(f"FROM {entries} AS fe", f"FROM {rows} AS fe"), params

    searches: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {}
    for name, locus in _SEARCHES.items():
        filters = StructuralVariantQueryFilters(page=1, page_size=100, **locus)
        joined, joined_params = search_where(filters, queries._structural_region_bounds)
        merged, merged_params = search_where(filters, _small_region_bounds)
        # The reference: each region tested on its own, row by row, or the gene names.
        clauses, params = _structural_variant_where_clauses(context, filters)
        clauses = [clause for clause in clauses if not clause.startswith("hasAny(e.calls.sampleId")]
        with pytest.MonkeyPatch.context() as each:
            each.setattr(queries, "_structural_region_bounds", _each_region_bounds)
            per_region = _structural_region_filter_condition(_regions(), prefix="each_region", params=params)
        genes = _small_gene_filter_condition(_GENES, prefix="each_gene", params=params)
        assert len(joined_params["sv_panel_region_starts"]) < len(params["each_region_starts"])
        searches[name] = {
            "joined": (f"SELECT e.key FROM {rows} AS e WHERE {joined}", {**joined_params, **rows_params}),
            "merged": (f"SELECT e.key FROM {rows} AS e WHERE {merged}", {**merged_params, **rows_params}),
            "each region": (
                f"SELECT e.key FROM {rows} AS e WHERE {' AND '.join(clauses)} AND ({per_region} OR {genes})",
                {**params, **rows_params},
            ),
        }

    async def _run() -> dict[str, dict[str, set[int]]]:
        try:
            await init_clickhouse_schema()
            kept: dict[str, dict[str, set[int]]] = {}
            for name, built in searches.items():
                kept[name] = {}
                for label, (query, params) in built.items():
                    kept[name][label] = {int(row[0]) for row in await execute_clickhouse(query, params)}
            return kept
        finally:
            await close_clickhouse_client()

    kept = asyncio.run(_run())
    by_key = {row[0]: row for row in _rows()}
    family_rows = [row for row in _rows() if row[5] == _FAMILY and row[6] in _PROJECTS and row[7] == 1]
    records = {
        key: StructuralVariantRecord(
            variant_key=key, variant_id=str(key), chr=normalize_chromosome(chromosome), start=start, end=end,
            sv_type="DEL", source=None, remote_chr=None, remote_start=None, remote_end=None, sv_len=None,
            filters=[], gene_symbols=genes, annotations=[], calls=[],
        )
        for key, chromosome, start, end, genes, _family, _project, _sign in family_rows
    }
    no_locus = StructuralVariantQueryFilters(page=1, page_size=100)

    def kept_by(rule: PanelFilterConstraints) -> set[int]:
        match = _StructuralPanelMatch(rule)
        return {key for key, record in records.items() if _structural_record_matches(record, no_locus, [], [], panel=match)}

    for name, answers in kept.items():
        assert answers["joined"] == answers["each region"], name
        # Only the family's own rows, in its projects, are read.
        assert all(by_key[key][5:8] in {(_FAMILY, project, 1) for project in _PROJECTS} for key in answers["joined"]), name
        assert answers["joined"], name
    everything = kept["no locus"]["joined"]
    # The record filter keeps what the SQL keeps.
    assert kept_by(panel) == everything
    # Not a vacuous comparison: without a locus some of the family's rows are kept, by every
    # chromosome spelling, in both projects, by a gene alone and by a region alone, ENDs
    # before their POS among them; some are not.
    by_genes, by_regions = kept_by(PanelFilterConstraints(genes=_GENES)), kept_by(PanelFilterConstraints(regions=panel.regions))
    assert everything == by_genes | by_regions
    assert by_genes - by_regions and by_regions - by_genes
    assert 0 < len(everything) < len(family_rows)
    assert {by_key[key][1] for key in everything} == set(_CHROMOSOMES)
    assert {by_key[key][6] for key in everything} == set(_PROJECTS)
    assert any(by_key[key][3] < by_key[key][2] - 1 for key in by_regions)
    # The SV with its END before its POS over the two chained loci (a row in each of the
    # family's projects) is left out; the two merged into one region, as the small-variant
    # condition merges them, would keep it.
    chained = {row[0] for row in family_rows if row[1] == "2"}
    assert len(chained) == 2 and not chained & everything
    assert kept["no locus"]["merged"] == everything | chained


# --------------------------------------------------------------------------- #
# The SV search over the SV tables
# --------------------------------------------------------------------------- #

_PANEL_ID = str(UUID(int=20261020))


def _sv(variant_id: str, chrom: str, start: int, end: int, genes: list[str], sample: str = "PROBAND") -> Any:
    from backend.app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord

    return StructuralVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=start, end=end, sv_type="DEL",
        source="hificnv", remote_chr=None, remote_start=None, remote_end=None, sv_len=end - start,
        filters=["PASS"], gene_symbols=genes, annotations=[{}],
        calls=[StructuralVariantCall(sample=sample, gt="0/1", qual=50.0, read_support=None, filter="PASS")],
    )


def test_the_sv_search_applies_a_panel_of_any_size_in_sql_before_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.services import clickhouse_family_variants as hub
    from backend.app.services.clickhouse_family_variants import get_family_structural_variants_page
    from backend.app.services.clickhouse_variant_records import PanelFilterConstraints, Region
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_structural_variants,
        ensure_clickhouse_variant_tables,
        insert_structural_variant_records,
    )
    from backend.app.services.family_metadata_context import FamilyMetadataContext
    from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

    family_uuid = str(uuid4())
    project = str(uuid4())
    context = FamilyMetadataContext(
        family_uuid=family_uuid, family_id="FAM-SV-PANEL", project_ids=[project],
        sample_rows=[{"sample_id": "PROBAND"}, {"sample_id": "MOTHER"}],
        sample_uuid_to_name={"PROBAND": "PROBAND", "MOTHER": "MOTHER"},
        sample_name_to_uuid={"PROBAND": "PROBAND", "MOTHER": "MOTHER"},
        affected_sample_names=["PROBAND"], assembly_id=str(uuid4()), assembly_name="GRCh38",
    )
    loci = (Region(chr="2", start=5_000_000, end=5_050_000), Region(chr="chr3", start=8_000_000, end=8_100_000))
    small = PanelFilterConstraints(genes=("SVPANB", "SVPANC"), regions=loci)
    large = PanelFilterConstraints(
        genes=(*small.genes, *(f"SVFILL{index:04d}" for index in range(1_000))),
        regions=(*loci, *(Region(chr="21", start=30_000_000 + 1_000 * i, end=30_000_500 + 1_000 * i) for i in range(1_000))),
    )
    records = [
        # Six SVs of the callset outside the panel, first in the read's order (chromosome 1).
        *(_sv(f"sv-filler-{index}", "1", 1_000_000 + 10_000 * index, 1_005_000 + 10_000 * index, ["FILLER"]) for index in range(6)),
        # 5 kb before SVPANB's locus, its annotation naming SVPANB (VEP's upstream gene).
        _sv("sv-name-only", "2", 4_990_000, 4_995_000, ["SVPANB"], sample="MOTHER"),
        # In SVPANC's locus, its annotation naming the gene otherwise.
        _sv("sv-position-only", "3", 8_050_000, 8_060_000, ["OLDSVPANC"]),
        # In SVPANB's locus and naming it, in lower case.
        _sv("sv-both", "2", 5_010_000, 5_020_000, ["svpanb"]),
        # Elsewhere, naming another gene.
        _sv("sv-neither", "4", 1_000, 2_000, ["UNRELATED"]),
    ]
    panels = {"two genes": small, "more than 1,000 regions": large}
    chosen: dict[str, PanelFilterConstraints] = {}

    async def resolve(_session: Any, panel_id: str, *, assembly_id: str | None = None) -> PanelFilterConstraints:
        return chosen["panel"]

    async def no_review_ids(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_phenotypes(*_args: Any, **_kwargs: Any) -> tuple[list[str], dict[str, str]]:
        return [], {}

    monkeypatch.setattr(hub, "_fetch_panel_constraints", resolve)
    monkeypatch.setattr(hub, "list_matching_structural_variant_review_ids", no_review_ids)
    monkeypatch.setattr(hub, "get_structural_variant_review_map", nothing)
    monkeypatch.setattr(hub, "_fetch_structural_cytoband_map", nothing)
    monkeypatch.setattr(hub, "_affected_present_hpo", no_phenotypes)
    # A cap of three SVs: the six SVs outside the panel come first in the read's order.
    monkeypatch.setattr(hub, "_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP", 3)

    async def _run() -> dict[str, Any]:
        try:
            await ensure_clickhouse_variant_tables("GRCh38")
            await insert_structural_variant_records("GRCh38", family_uuid, [project], records)
            out: dict[str, Any] = {}
            for name, panel in panels.items():
                chosen["panel"] = panel
                for path in ("list", "ranked", "track"):
                    page = await get_family_structural_variants_page(
                        None,  # type: ignore[arg-type]
                        context=context,
                        page=1,
                        page_size=100,
                        filters=StructuralVariantQueryFilters(page=1, page_size=100, panel_id=_PANEL_ID),
                        prioritize=path == "ranked",
                        track_mode=path == "track",
                    )
                    out[(name, path)] = (
                        {str(variant.id) for variant in page.variants},
                        bool(page.candidates_capped or page.ranking_truncated or page.total_is_estimated),
                    )
            await delete_family_structural_variants("GRCh38", family_uuid)
            return out
        finally:
            await close_clickhouse_client()

    answers = asyncio.run(_run())
    # Before: the two-gene panel left out the SV beside SVPANB that names it; the panel of more
    # than 1,000 regions read the first three SVs of the callset, none of the panel's, and kept
    # none, flagged as capped (and left out the SV in SVPANC's locus named otherwise in any case).
    kept = {"sv-name-only", "sv-position-only", "sv-both"}
    assert answers == {
        (name, path): (kept, False) for name in panels for path in ("list", "ranked", "track")
    }
