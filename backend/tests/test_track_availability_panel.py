"""The chromosome view offers a member's small-variant track under a gene panel as the list keeps it (CLIN-62).

The chromosome view first asks which tracks each member has (``GET /families/{family_id}/
track-availability``), with the filters it came with, the small-variant page's gene panel among
them, and draws a member's small-variant track only when the answer says so. The small-variant
presence (``_small_variant_present_sample_names``) applied a panel's regions alone, each row
tested against every region (``arrayExists``), while the list and the member's track keep a
variant annotated with one of the panel's genes or in one of its regions, whatever the panel's
size (#897), its regions joined to the family's own rows on 64-kb windows and bound once as
parameters (ENG-15). A member whose only variants under the panel are named by one of its genes
outside its regions (VEP's upstream and downstream genes) was offered no track although the list
showed them, and the Mendeliome's regions were tested row by row.

Here, with ClickHouse faked at its edge and its queries recorded: the presence applies the panel
with the very clause the member's track and the list send, the same text and the same bound
values, for a panel of two genes and one of the Mendeliome's size, in a window, over a whole
chromosome and over every chromosome; a Mendeliome-sized panel is bound once, never written into
the query, and its regions joined rather than tested row by row; a panel that resolves to neither
genes nor regions offers no member a track and sends no query, as the list then shows nothing;
without a panel no panel clause is sent. That the same SQL offers exactly the members the list
shows, on real ClickHouse, is shown by e2e/test_e2e_track_availability_panel.py.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import pytest
from clickhouse_connect.driver.binding import bind_query

from backend.app.services import clickhouse_family_variants, family_service
from backend.app.services.clickhouse_family_variants import get_family_small_variants_page
from backend.app.services.clickhouse_variant_records import PanelFilterConstraints, Region
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_service import get_family_track_availability_for_user

_PANEL_ID = str(UUID(int=2026102062))
_M = "backend.app.services.clickhouse_family_variants."
_SERVICE = "backend.app.services.family_service."

# The panel's genes and the loci the gene reference gives them: TAVPNLA one, TAVPNLB two
# transcripts that chain.
_TWO_GENES = PanelFilterConstraints(
    genes=("TAVPNLA", "TAVPNLB"),
    regions=(
        Region(chr="7", start=30_000_000, end=30_050_000),
        Region(chr="7", start=30_200_000, end=30_240_000),
        Region(chr="chr7", start=30_230_000, end=30_260_000),
    ),
)


def _mendeliome_sized(extra_genes: tuple[str, ...] = ()) -> PanelFilterConstraints:
    """The two genes and 5,298 more, 5,300 as the Mendeliome has, each with eight transcript
    loci nested in its span and PanelApp's locus written end first: 47,700 regions, which bind
    as one span per gene (``_small_region_bounds``)."""
    loci: list[Region] = list(_TWO_GENES.regions)
    for index in range(5_298):
        chrom = str(index % 22 + 1)
        start = 40_000_000 + 10_000 * (index // 22) + 1
        for transcript in range(8):
            loci.append(Region(chr=chrom, start=start + 100 * transcript, end=start + 5_000 - 50 * transcript))
        loci.append(Region(chr=f"chr{chrom}", start=start + 5_000, end=start))
    genes = (*_TWO_GENES.genes, *(f"TAVFILL{index:04d}" for index in range(5_298)), *extra_genes)
    return PanelFilterConstraints(genes=genes, regions=tuple(loci))


_PANELS = {"two genes": _TWO_GENES, "Mendeliome-sized": _mendeliome_sized()}

# Where the chromosome view looks: (chromosomes, start, end), as it asks for them.
_VIEWS: dict[str, tuple[list[str], int | None, int | None]] = {
    "a window": (["7"], 29_500_000, 31_500_000),
    "a whole chromosome": (["7"], 0, 159_345_973),
    "every chromosome": ([str(c) for c in range(1, 23)] + ["X", "Y", "MT"], None, None),
}

_PARAMETER = re.compile(r"%\((\w+)\)s")


def _family() -> FamilyMetadataContext:
    names = {"PROBAND": "sample-proband", "MOTHER": "sample-mother", "FATHER": "sample-father"}
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM062",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": name} for name in names],
        sample_uuid_to_name={uuid: name for name, uuid in names.items()},
        sample_name_to_uuid=dict(names),
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


class _ClickHouse:
    """ClickHouse faked at its edge: every query is recorded with its parameters and answered
    with no rows, but for the presence's per-sample aggregate, which answers ``present``."""

    def __init__(self, present: tuple[str, ...]) -> None:
        self.present = present
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, query: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        self.sent.append((query, dict(params)))
        if "ARRAY JOIN sids AS sid" in query:
            return [(sample,) for sample in self.present]
        return []

    def presence_queries(self) -> list[tuple[str, dict[str, Any]]]:
        return [(query, params) for query, params in self.sent if "ARRAY JOIN sids AS sid" in query]

    def small_variant_queries(self) -> list[str]:
        return [query for query, _params in self.sent if "SNV_INDEL" in query]


def _serve(
    monkeypatch: pytest.MonkeyPatch, panel: PanelFilterConstraints, *, present: tuple[str, ...] = ()
) -> tuple[_ClickHouse, list[tuple[list[str], dict[str, Any]]]]:
    """The family, the panel resolved as ``panel``, its other tracks absent, and ClickHouse
    faked. Returns the fake and the WHERE clauses and parameters of every small-variant query
    built (``_small_query_filter_parts``), in the order they were built."""
    clickhouse = _ClickHouse(present)
    built: list[tuple[list[str], dict[str, Any]]] = []
    build = clickhouse_family_variants._small_query_filter_parts

    def recording_build(*args: Any, **kwargs: Any) -> tuple[list[str], dict[str, Any], bool]:
        clauses, params, detail = build(*args, **kwargs)
        built.append((list(clauses), dict(params)))
        return clauses, params, detail

    async def resolve(_session: Any, panel_id: str, *, assembly_id: str | None = None) -> PanelFilterConstraints:
        assert panel_id == _PANEL_ID and assembly_id == "assembly-uuid"
        return panel

    async def family(*_args: Any, **_kwargs: Any) -> FamilyMetadataContext:
        return _family()

    async def no_sources(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_presence(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    monkeypatch.setattr(_M + "_execute_clickhouse", clickhouse.execute)
    monkeypatch.setattr(_M + "_small_query_filter_parts", recording_build)
    monkeypatch.setattr(clickhouse_family_variants, "_fetch_panel_constraints", resolve)
    monkeypatch.setattr(family_service, "_fetch_panel_constraints", resolve)
    monkeypatch.setattr(_SERVICE + "build_family_metadata_context", family)
    monkeypatch.setattr(_SERVICE + "get_track_sources_by_sample", no_sources)
    monkeypatch.setattr(_SERVICE + "get_track_presence_by_sample", no_presence)
    monkeypatch.setattr(_SERVICE + "_repeat_expansion_presence_by_sample", no_presence)
    return clickhouse, built


async def _availability(view: str, *, panel_id: str | None = _PANEL_ID) -> dict[str, bool]:
    chromosomes, start, end = _VIEWS[view]
    availability = await get_family_track_availability_for_user(
        None,  # type: ignore[arg-type]
        family_id="FAM062",
        user=None,  # type: ignore[arg-type]
        chromosomes=chromosomes,
        start=start,
        end=end,
        panel_id=panel_id,
        include_small_variants=True,
    )
    return {name: sample.small_variants for name, sample in availability.samples.items()}


async def _list(view: str, **request: Any) -> Any:
    """The small-variant list over the view's locus with the panel, as the chromosome view
    sends it: overlapping the locus when it has one."""
    chromosomes, start, end = _VIEWS[view]
    locus: dict[str, Any] = {}
    if len(chromosomes) == 1:
        locus = {"chr": chromosomes[0], "start": start, "end": end, "overlap": True}
    return await get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_family(),
        panel_id=_PANEL_ID,
        **locus,
        **request,
    )


async def _members_track(view: str, member: str) -> Any:
    """A member's small-variant track (``SmallVariantTrack.tsx``): the list in track mode,
    with the member's own non-reference calls."""
    return await _list(
        view,
        page=1,
        page_size=9_999,
        sample_filters=[f"{member}:het|hom"],
        track_mode=True,
        track_result_limit=10_000,
    )


def _panel_part(clauses: list[str], params: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The WHERE clauses of a query that apply the panel, and the value bound for every
    parameter they read (the panel's own, and the locus they share with the query)."""
    panel_clauses = [clause for clause in clauses if "%(panel_" in clause]
    names = sorted({name for clause in panel_clauses for name in _PARAMETER.findall(clause)})
    return panel_clauses, {name: params[name] for name in names}


# --------------------------------------------------------------------------- #
# The presence applies the panel as the member's track and the list apply it
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("view", list(_VIEWS))
@pytest.mark.parametrize("panel", list(_PANELS))
async def test_the_presence_applies_the_panel_with_the_clause_of_the_members_track_and_the_list(
    monkeypatch: pytest.MonkeyPatch, panel: str, view: str
) -> None:
    # Before: the presence read the panel's regions alone, each row tested against every
    # region (include_region_*), and none of its genes; the track and the list read both.
    clickhouse, built = _serve(monkeypatch, _PANELS[panel], present=("sample-father",))

    offered = await _availability(view)
    (presence,) = built
    await _members_track(view, "FATHER")
    (track,) = built[1:]
    await _list(view, page=1, page_size=100)
    page, count = built[2:]

    assert offered == {"PROBAND": False, "MOTHER": False, "FATHER": True}
    clauses, values = _panel_part(*presence)
    assert len(clauses) == 1, presence[0]
    for query in (track, page, count):
        assert _panel_part(*query) == (clauses, values)
    # The panel's genes, in the entry's own symbols and the gene index, and its regions
    # joined to the family's rows; nothing else of the panel, and no region tested per row.
    (clause,) = clauses
    assert "arrayExists(gene -> lower(gene) IN %(panel_gene_terms)s, e.gene_symbols)" in clause
    assert "gi.gene_term IN %(panel_annotation_gene_terms)s" in clause
    assert "AS v INNER JOIN (SELECT region.1 AS region_chrom" in clause
    assert set(values["panel_gene_terms"]) == {gene.casefold() for gene in _PANELS[panel].genes}
    query, _params = clickhouse.presence_queries()[0]
    assert "arrayExists((region_chrom" not in query
    assert "include_region" not in query


@pytest.mark.asyncio
async def test_a_mendeliome_sized_panel_is_bound_once_and_joined_not_written_into_the_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = "TAVX') OR 1=1 --"
    clickhouse, _built = _serve(monkeypatch, _mendeliome_sized((hostile,)), present=("sample-mother",))

    assert await _availability("a window") == {"PROBAND": False, "MOTHER": True, "FATHER": False}

    ((query, params),) = clickhouse.presence_queries()
    # The 47,700 loci bound as three arrays of one span per gene, each array once.
    assert len(params["panel_region_starts"]) == 5_300
    for name in ("panel_region_chromosomes", "panel_region_starts", "panel_region_ends"):
        assert query.count(f"%({name})s") == 1
    assert query.count("AS v INNER JOIN (SELECT region.1 AS region_chrom") == 1
    assert "arrayExists((region_chrom" not in query
    # The 5,301 genes bound, in the entry's symbols and in the gene index.
    assert len(params["panel_gene_terms"]) == len(params["panel_annotation_gene_terms"]) == 5_301
    # No coordinate and no gene is written into the query: they are parameters.
    assert "40000001" not in query and "TAVFILL" not in query.upper()
    assert hostile not in query
    rendered, _ = bind_query(query, params)
    assert "'tavx\\') or 1=1 --'" in rendered


# --------------------------------------------------------------------------- #
# A panel that keeps nothing offers no track; no panel, no panel clause
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("view", list(_VIEWS))
async def test_a_panel_without_genes_or_regions_offers_no_member_a_small_variant_track(
    monkeypatch: pytest.MonkeyPatch, view: str
) -> None:
    # A panel whose genes and regions the family's assembly does not resolve keeps no
    # variant: the list answers an empty page without asking ClickHouse. Before, the presence
    # read such a panel as no panel at all, and offered every member with a variant a track.
    clickhouse, _built = _serve(
        monkeypatch, PanelFilterConstraints(), present=("sample-proband", "sample-mother", "sample-father")
    )

    assert await _availability(view) == {"PROBAND": False, "MOTHER": False, "FATHER": False}
    assert clickhouse.small_variant_queries() == []
    for member in ("PROBAND", "MOTHER", "FATHER"):
        track = await _members_track(view, member)
        assert (track.total, track.variants) == (0, [])
    assert clickhouse.small_variant_queries() == []


@pytest.mark.asyncio
async def test_without_a_panel_the_presence_reads_no_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    clickhouse, built = _serve(monkeypatch, _TWO_GENES, present=("PROBAND", "sample-mother", "sample-father"))

    async def no_panel(*_args: Any, **_kwargs: Any) -> PanelFilterConstraints:
        raise AssertionError("no panel is resolved without a panel_id")

    monkeypatch.setattr(family_service, "_fetch_panel_constraints", no_panel)

    assert await _availability("a window", panel_id=None) == {"PROBAND": True, "MOTHER": True, "FATHER": True}
    ((clauses, params),) = built
    assert _panel_part(clauses, params) == ([], {})
    assert not [name for name in params if name.startswith(("panel_", "include_region"))]
    assert len(clickhouse.presence_queries()) == 1
