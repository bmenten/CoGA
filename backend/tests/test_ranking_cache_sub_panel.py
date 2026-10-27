"""A narrower panel served from a broader cached ranking (``_serve_subpanel_from_superset``).

CLIN-27: only while that ranking's panel holds what it held when the ranking was computed.
On an exact miss, a broader cached ranking is restricted to the requested panel's
variants. That can only drop variants from the cached ranking, never add any, so a
ranking computed before its panel gained a gene served the panel afterwards without that
gene's variants. Each cached ranking now records the genes and regions its panel filter
applied (``panel_members_digest``): a broader ranking is used only while its panel holds
exactly those, and one that recorded none is not used.

The keys also cover the regions the query applied: a gene's loci are read from the gene
reference when the query runs, so a reload that moves a gene changes a panel's reach
without any version or filter changing; and a ranking computed without the query's
intervals (the background warm's replay) is not served for the query. A gene filter reads
no loci: it keeps the variants annotated with the gene, wherever the reference places it
(ENG-1).

CLIN-34 and CLIN-35: a served panel is what a fresh computation of that panel gives. The
broader ranking must hold every variant the panel reaches. Every panel is matched by its
genes and its regions, the Mendeliome too (owner decision 2026-10-14: a panel of more than
1,000 regions was matched on gene symbols alone, so its ranking lacked a variant a smaller
panel reaches by position only), so the Mendeliome's ranking now holds the variants a
sub-panel reaches through a gene's locus; it still lacks one a sub-panel reaches by a region
of its own only (coordinates PanelApp gave for a gene). The ranks are numbered within the
panel, and a compound-het pair whose partner the panel leaves out is no pair: such a ranking
is not used, and the panel is computed afresh. These tests compare a served panel with a
fresh computation of the same panel.

Drives ``_prioritized_small_variants_page`` with the real cache keys, the real superset
path and the real record filter, over an in-memory stand-in for the cache rows, the panel
tables, the gene reference and the family's variants.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services import variant_ranking_cache as vrc
from backend.app.services.clickhouse_variant_queries import (
    _parse_interval_regions,
    _segregation_modes_by_variant,
    _small_record_hits_gene_terms,
    _small_record_matches,
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
from backend.app.services.variant_prioritization import MODE_COMPOUND_HET

BROAD ="0b5e1c1e-0000-4000-8000-000000000001"
NARROW = "0b5e1c1e-0000-4000-8000-000000000002"
OTHER = "0b5e1c1e-0000-4000-8000-000000000003"
MENDELIOME = "0b5e1c1e-0000-4000-8000-000000000004"

# Genes of the Mendeliome stand-in besides GENE1-3, one locus each on chromosome 2, where the
# family has no variant: with them it has more than 1,000 regions, as the real Mendeliome
# has, above which a panel used to be matched on gene symbols alone.
FILLER_GENES = tuple(f"FILLER{index:04d}" for index in range(1_000))


def _variant(
    variant_id: str,
    gene: str,
    *,
    start: int,
    other_genes: tuple[str, ...] = (),
    calls: dict[str, str] | None = None,
) -> SmallVariantRecord:
    """A variant annotated with ``gene`` (its primary annotation, which a compound-het pair
    is grouped on) and with ``other_genes``, called ``calls`` (sample -> genotype)."""
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr="1",
        start=start,
        end=start,
        ref="A",
        alt="G",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=[gene, *other_genes],
        annotations=[{"gene": gene}, *({"gene": other} for other in other_genes)],
        calls=[
            SmallVariantCall(sample=sample, gt=gt, gq=None, dp=None, af=[], ad=[], ps=None)
            for sample, gt in (calls or {"PROBAND": "0/1"}).items()
        ],
    )


GENE1_A = _variant("1-1500-A-G", "GENE1", start=1500)
GENE2_A = _variant("1-3500-A-G", "GENE2", start=3500)
GENE3_A = _variant("1-7500-A-G", "GENE3", start=7500)


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM_SUBPANEL",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": "PROBAND", "role": "proband", "affected": True, "sex": "female"}],
        sample_uuid_to_name={"sample-proband": "PROBAND"},
        sample_name_to_uuid={"PROBAND": "sample-proband"},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _trio_context() -> FamilyMetadataContext:
    """An affected proband and the two unaffected parents the pedigree links to her."""
    members = {
        "PROBAND": ("proband", "affected", "female"),
        "MOTHER": ("mother", "unaffected", "female"),
        "FATHER": ("father", "unaffected", "male"),
    }
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM_SUBPANEL",
        project_ids=["project-uuid"],
        sample_rows=[
            {
                "sample_id": name,
                "role": role,
                "affected": status == "affected",
                "clinical_status": status,
                "sex": sex,
            }
            for name, (role, status, sex) in members.items()
        ],
        sample_uuid_to_name={f"sample-{name.lower()}": name for name in members},
        sample_name_to_uuid={name: f"sample-{name.lower()}" for name in members},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        relationship_rows=[
            {
                "relationship_type": "parent_child",
                "sample_id_a": parent,
                "role_a": parent.lower(),
                "sample_id_b": "PROBAND",
                "role_b": "child",
            }
            for parent in ("MOTHER", "FATHER")
        ],
    )


def _panel_sql_match(record: SmallVariantRecord, constraints: PanelFilterConstraints | None) -> bool:
    """The panel part of the SQL filter (``_small_panel_filter_condition``): the variant's
    gene symbols, or its position, whatever the panel's size. The real condition is checked
    in test_clickhouse_family_variants.py (``test_small_panel_filter_keeps_the_regions_of_large_gene_panels``,
    ``test_small_panel_filter_keeps_regions_for_normal_panels``) and, on ClickHouse, in
    integration/test_small_variant_filter_parity_clickhouse.py."""
    if constraints is None or not (constraints.genes or constraints.regions):
        return True
    if constraints.genes and _small_record_hits_gene_terms(record, constraints.genes):
        return True
    return _variant_overlaps_regions(record.chr, record.start, record.end, constraints.regions)


class _World:
    """The panel tables, the gene reference, the family's variants and the cache rows."""

    def __init__(self) -> None:
        # panel id -> its version and genes, as gene_panels and gene_panel_genes hold them
        self.panels: dict[str, dict[str, Any]] = {}
        # gene -> its loci, as the genes table holds them for the family's assembly
        self.loci: dict[str, list[Region]] = {
            "GENE1": [Region(chr="1", start=1000, end=2000)],
            "GENE2": [Region(chr="1", start=3000, end=4000)],
            "GENE3": [Region(chr="1", start=7000, end=8000)],
            **{
                gene: [Region(chr="2", start=10_000 * (index + 1), end=10_000 * (index + 1) + 500)]
                for index, gene in enumerate(FILLER_GENES)
            },
        }
        self.records = [GENE1_A, GENE2_A, GENE3_A]
        # inputs_hash -> cache row, as family_variant_ranking_cache holds it
        self.cache: dict[str, dict[str, Any]] = {}
        # Each cache row whose ranking was read, by its inputs_hash, in order.
        self.reads: list[str] = []

    def set_panel(
        self, panel_id: str, *genes: str, version: int = 1, regions: tuple[Region, ...] = ()
    ) -> None:
        """A panel of ``genes``, with ``regions`` stored for it (as PanelApp's coordinates
        are stored) besides its genes' loci in the gene reference."""
        self.panels[panel_id] = {"version": version, "genes": list(genes), "regions": list(regions)}

    def row_for(self, panel_id: str | None) -> dict[str, Any]:
        rows = [row for row in self.cache.values() if row["panel_id"] == panel_id]
        assert len(rows) == 1, rows
        return rows[0]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    state = _World()
    m = "backend.app.services.clickhouse_family_variants."

    async def _ped(*_a, **_k):
        return {"structure_hash": "ped-1"}

    async def _panel_version(_session, panel_id):
        panel = state.panels.get(panel_id) if panel_id else None
        return f"{panel['version']}:" if panel else None

    async def _none(*_a, **_k):
        return None

    async def _empty(*_a, **_k):
        return {}

    async def _refs(*_a, **_k):
        return {"hpo_release": "2026-04-01", "hpo_imported_at": None, "gene_info_updated_at": None}

    async def _no_hpo(*_a, **_k):
        return ([], {})

    async def _data_version(*_a, **_k):
        return "1:111"

    async def _fetch_panel_constraints(_session, panel_id, *, assembly_id=None):
        panel = state.panels.get(panel_id)
        if panel is None:
            raise HTTPException(status_code=404, detail="Panel not found")
        regions = [
            *panel["regions"],
            *(region for gene in panel["genes"] for region in state.loci.get(gene, [])),
        ]
        return PanelFilterConstraints(genes=tuple(panel["genes"]), regions=tuple(regions))

    async def _fetch_gene_regions(_session, *, gene_query, assembly_id):
        return list(state.loci.get(gene_query, []))

    async def _fetch_rows(
        _context,
        filters,
        *,
        include_variant_ids=None,
        panel_constraints=None,
        include_regions=(),
        exclude_regions=(),
        exclude_gene_regions=(),
        **_k,
    ):
        wanted = None if include_variant_ids is None else {str(v) for v in include_variant_ids}
        return [
            record
            for record in state.records
            if (wanted is None or record.variant_id in wanted)
            and _panel_sql_match(record, panel_constraints)
            and _small_record_matches(
                record,
                filters,
                list(include_regions),
                list(exclude_regions),
                list(exclude_gene_regions),
                panel_constraints=panel_constraints,
            )
        ]

    async def _get_cached(_session, *, family_uuid, inputs_hash):
        state.reads.append(inputs_hash)
        return state.cache.get(inputs_hash)

    async def _store(
        _session,
        *,
        family_uuid,
        inputs_hash,
        total,
        total_is_estimated,
        ranking_truncated,
        ranking,
        provenance=None,
        base_hash=None,
        panel_id=None,
    ):
        # The JSON round trip the jsonb columns make.
        state.cache[inputs_hash] = {
            "inputs_hash": inputs_hash,
            "base_hash": base_hash,
            "panel_id": panel_id,
            "total": total,
            "total_is_estimated": total_is_estimated,
            "ranking_truncated": ranking_truncated,
            "ranking": json.loads(json.dumps(ranking)),
            "provenance": json.loads(json.dumps(provenance or {})),
            "computed_at": None,
        }

    async def _supersets(_session, *, family_uuid, base_hash):
        # Without their rankings, as find_superset_candidates reads them (ENG-13).
        rows = [
            {key: value for key, value in row.items() if key != "ranking"}
            for row in state.cache.values()
            if row["base_hash"] == base_hash and not row["ranking_truncated"]
        ]
        return sorted(rows, key=lambda row: row["total"], reverse=True)

    monkeypatch.setattr(vrc, "_pedigree_signature", _ped)
    monkeypatch.setattr(vrc, "_panel_version", _panel_version)
    monkeypatch.setattr(vrc, "_monarch_release", _none)
    monkeypatch.setattr(vrc, "_reference_versions", _refs)
    monkeypatch.setattr(m + "_affected_present_hpo", _no_hpo)
    monkeypatch.setattr(m + "_family_small_variant_data_version", _data_version)
    monkeypatch.setattr(m + "_fetch_panel_constraints", _fetch_panel_constraints)
    monkeypatch.setattr(m + "_fetch_gene_regions", _fetch_gene_regions)
    monkeypatch.setattr(m + "_fetch_small_variant_rows", _fetch_rows)
    monkeypatch.setattr(m + "get_cached_ranking", _get_cached)
    monkeypatch.setattr(m + "store_ranking", _store)
    monkeypatch.setattr(m + "find_superset_candidates", _supersets)
    monkeypatch.setattr(m + "_fetch_gene_constraint_metric_map", _empty)
    monkeypatch.setattr(m + "_segregation_modes_by_variant", lambda *a, **k: {})
    monkeypatch.setattr(m + "_hydrate_small_variant_outs", _none)
    return state


def _open(
    panel_id: str | None = None,
    *,
    gene: str | None = None,
    intervals: str | None = None,
    resolve_intervals: bool = True,
    context: FamilyMetadataContext | None = None,
    page: int = 1,
    page_size: int = 50,
):
    """Open the prioritised view as a request does: the panel's genes and regions, and the
    intervals, are resolved first, then handed to the ranking. ``resolve_intervals=False``
    hands over none, as the background warm does when it replays a stored query."""

    async def _run():
        filters = SmallVariantQueryFilters(
            page=page, page_size=page_size, panel_id=panel_id, gene=gene, intervals=intervals
        )
        constraints = (
            await cfv._fetch_panel_constraints(None, panel_id, assembly_id="assembly-uuid")
            if panel_id
            else PanelFilterConstraints()
        )
        return await cfv._prioritized_small_variants_page(
            None,  # type: ignore[arg-type]
            context=context or _context(),
            filters=filters,
            page=page,
            page_size=page_size,
            panel_constraints=constraints,
            review_variant_ids=None,
            excluded_review_variant_ids=set(),
            include_review_filter_active=False,
            include_regions=_parse_interval_regions(intervals) if resolve_intervals else [],
            exclude_regions=[],
            exclude_gene_regions=[],
            exclude_gene_terms=[],
            small_variant_summary=None,
        )

    return asyncio.run(_run())


def _ids(page) -> list[str]:
    return sorted(str(variant.id) for variant in page.variants)


def _ranked(page) -> list[tuple[str, dict[str, Any] | None]]:
    """The page as ranked: each variant in order, with its rank, scores and segregation."""
    return [
        (str(variant.id), variant.priority.model_dump() if variant.priority else None)
        for variant in page.variants
    ]


def _fresh(world: _World, panel_id: str, **open_kwargs: Any):
    """The same panel computed afresh: opened over an empty cache, then the cache rows are
    put back as they were."""
    kept = dict(world.cache)
    world.cache.clear()
    try:
        page = _open(panel_id, **open_kwargs)
    finally:
        world.cache.clear()
        world.cache.update(kept)
    assert page.ranking_cached is False
    return page


def test_a_ranking_cached_before_its_panel_gained_a_gene_is_not_served_for_it(world) -> None:
    world.set_panel(BROAD, "GENE1")
    first = _open(BROAD)
    assert first.ranking_cached is False
    assert _ids(first) == [GENE1_A.variant_id]

    # An edit adds GENE2: the panel's version moves, so the exact key misses. The ranking
    # cached for the panel's first version must not serve it either: it was computed
    # without GENE2, and restricting it to the panel can only drop variants.
    world.set_panel(BROAD, "GENE1", "GENE2", version=2)
    after = _open(BROAD)
    assert after.ranking_cached is False
    assert _ids(after) == [GENE1_A.variant_id, GENE2_A.variant_id]

    # The recomputed ranking is then served as an exact hit.
    again = _open(BROAD)
    assert again.ranking_cached is True
    assert _ids(again) == [GENE1_A.variant_id, GENE2_A.variant_id]


def test_a_ranking_of_another_panel_that_gained_the_gene_since_is_not_used(world) -> None:
    # The broader panel gains the narrower panel's gene after its ranking was computed:
    # its genes now cover the request, but its cached ranking holds none of that gene's
    # variants, and served from it the narrower panel would come out empty.
    world.set_panel(BROAD, "GENE1")
    _open(BROAD)
    world.set_panel(BROAD, "GENE1", "GENE3", version=2)
    world.set_panel(NARROW, "GENE3")

    narrow = _open(NARROW)
    assert narrow.ranking_cached is False
    assert _ids(narrow) == [GENE3_A.variant_id]


def test_a_broader_ranking_is_read_only_once_its_panel_passes(world) -> None:
    # ENG-13 (#858). Before: every candidate's whole ranking was read with the candidate
    # list, before its panel was checked. The broader panel changed since its ranking, so
    # that ranking is never read; the narrower panel is computed live.
    world.set_panel(BROAD, "GENE1", "GENE3")
    _open(BROAD)
    broad = world.row_for(BROAD)["inputs_hash"]
    world.set_panel(BROAD, "GENE1", "GENE3", "GENE2", version=2)
    world.set_panel(NARROW, "GENE3")
    world.reads.clear()

    narrow = _open(NARROW)

    assert narrow.ranking_cached is False
    assert broad not in world.reads

    # Unchanged, it is read, once, and serves the panel.
    world.set_panel(BROAD, "GENE1", "GENE3", version=3)
    _open(BROAD)
    broad = world.row_for(BROAD)["inputs_hash"]
    world.set_panel(NARROW, "GENE3", version=2)
    world.reads.clear()
    served = _open(NARROW)
    assert served.ranking_cached is True
    assert world.reads.count(broad) == 1


def test_a_narrower_panel_is_served_from_a_broader_ranking_whose_panel_is_unchanged(world) -> None:
    world.set_panel(BROAD, "GENE1", "GENE2")
    world.set_panel(NARROW, "GENE2")
    broad = _open(BROAD)
    assert _ids(broad) == [GENE1_A.variant_id, GENE2_A.variant_id]

    narrow = _open(NARROW)
    assert narrow.ranking_cached is True
    assert _ids(narrow) == [GENE2_A.variant_id]
    assert narrow.variants[0].priority == next(
        variant.priority for variant in broad.variants if str(variant.id) == GENE2_A.variant_id
    )


def test_a_version_change_that_leaves_the_genes_as_they_were_still_serves_the_panel(world) -> None:
    # A new version with the same genes and regions (an edit of the description) misses
    # the exact key, but the panel's own earlier ranking still holds every variant it can.
    world.set_panel(BROAD, "GENE1", "GENE2")
    _open(BROAD)
    world.set_panel(BROAD, "GENE1", "GENE2", version=2)

    after = _open(BROAD)
    assert after.ranking_cached is True
    assert _ids(after) == [GENE1_A.variant_id, GENE2_A.variant_id]


def test_a_narrower_panel_is_served_from_a_ranking_without_a_panel(world) -> None:
    everything = _open(None)
    assert _ids(everything) == [GENE1_A.variant_id, GENE2_A.variant_id, GENE3_A.variant_id]
    world.set_panel(NARROW, "GENE3")

    narrow = _open(NARROW)
    assert narrow.ranking_cached is True
    assert _ids(narrow) == [GENE3_A.variant_id]


def test_a_ranking_cached_without_a_record_of_its_panel_is_not_used(world) -> None:
    # Rows cached before rankings recorded their panel's genes and regions cannot show
    # that the panel is unchanged since.
    world.set_panel(BROAD, "GENE1", "GENE2")
    world.set_panel(NARROW, "GENE2")
    _open(BROAD)
    world.row_for(BROAD)["provenance"].pop("panel_members", None)

    narrow = _open(NARROW)
    assert narrow.ranking_cached is False
    assert _ids(narrow) == [GENE2_A.variant_id]


def test_a_ranking_whose_panel_was_deleted_is_passed_over(world) -> None:
    # The deleted panel's id stays in its cached rows. Looking its genes up answered 404,
    # which failed the request for another panel.
    world.set_panel(BROAD, "GENE1", "GENE2")
    world.set_panel(NARROW, "GENE2")
    _open(BROAD)
    del world.panels[BROAD]

    narrow = _open(NARROW)
    assert narrow.ranking_cached is False
    assert _ids(narrow) == [GENE2_A.variant_id]


def test_a_ranking_from_before_the_gene_reference_moved_a_panel_gene_is_not_served(world) -> None:
    # A gene-reference reload moves GENE1 over a variant annotated with another gene. No
    # panel changes version, but a panel with GENE1 now reaches that variant by position.
    beyond = _variant("1-2600-A-G", "OUTSIDE", start=2600)
    world.records.append(beyond)
    world.set_panel(BROAD, "GENE1")
    world.set_panel(NARROW, "GENE1")
    first = _open(BROAD)
    assert _ids(first) == [GENE1_A.variant_id]

    world.loci["GENE1"] = [Region(chr="1", start=1000, end=2800)]

    # Not served from the ranking computed on the old loci, for another panel...
    narrow = _open(NARROW)
    assert narrow.ranking_cached is False
    assert _ids(narrow) == [GENE1_A.variant_id, beyond.variant_id]
    # ...nor for its own panel, whose exact key covers the regions it applied.
    broad = _open(BROAD)
    assert _ids(broad) == [GENE1_A.variant_id, beyond.variant_id]


def test_the_gene_filter_keeps_the_genes_variants_wherever_the_reference_places_the_gene(
    world,
) -> None:
    # The gene filter keeps the variants annotated with the gene, as the unranked search
    # keeps them: one just upstream, outside the gene's loci, too. The ranked search kept
    # those inside the loci alone (ENG-1), and a reload that extended the gene over the
    # variant changed what it kept. Now the loci change nothing, nor the cache key.
    upstream = _variant("1-2500-A-G", "GENE1", start=2500)
    world.records.append(upstream)
    first = _open(None, gene="GENE1")
    assert first.ranking_cached is False
    assert _ids(first) == [GENE1_A.variant_id, upstream.variant_id]

    world.loci["GENE1"] = [Region(chr="1", start=1000, end=3000)]
    after = _open(None, gene="GENE1")
    assert after.ranking_cached is True
    assert _ids(after) == [GENE1_A.variant_id, upstream.variant_id]


def test_a_ranking_computed_without_the_query_s_intervals_is_not_served_for_it(world) -> None:
    # The background warm replays the family's last query without resolving its intervals
    # (nor its excluded intervals and genes), so it ranks more than the query keeps to. The
    # key covered the filters' text alone, so that ranking was served to the query's next
    # open as if it had kept to its intervals.
    replayed = _open(None, intervals="1:1000-2000", resolve_intervals=False)
    assert _ids(replayed) == [GENE1_A.variant_id, GENE2_A.variant_id, GENE3_A.variant_id]

    opened = _open(None, intervals="1:1000-2000")
    assert opened.ranking_cached is False
    assert _ids(opened) == [GENE1_A.variant_id]


# CLIN-34 and CLIN-35: a panel served from a broader ranking is what a fresh computation of
# that panel gives: the same variants, in the same order, with the same ranks and scores.


def _mendeliome(world: _World) -> None:
    """The Mendeliome stand-in: GENE1-3 and the fillers, more than 1,000 regions, as the
    real Mendeliome has."""
    world.set_panel(MENDELIOME, "GENE1", "GENE2", "GENE3", *FILLER_GENES)
    regions = [region for gene in world.panels[MENDELIOME]["genes"] for region in world.loci[gene]]
    assert len(regions) > 1_000


def test_the_mendeliome_reaches_a_variant_by_position_and_serves_the_panel_that_does(world) -> None:
    # The annotation names the gene by another symbol than the gene reference does (here a
    # previous symbol of GENE2), so a panel with GENE2 reaches the variant only by its
    # position in GENE2's locus. The Mendeliome was matched on gene symbols alone: its
    # ranking lacked the variant, and the panel had to be computed for itself (CLIN-34).
    # Matched by position too, the Mendeliome keeps the variant, and its ranking serves the
    # panel as the panel's own computation ranks it (owner decision 2026-10-14).
    previous_symbol = _variant("1-3600-A-G", "GENE2_PREVIOUS", start=3600)
    world.records.append(previous_symbol)
    _mendeliome(world)
    world.set_panel(NARROW, "GENE2")
    mendeliome = _open(MENDELIOME)
    assert _ids(mendeliome) == [
        GENE1_A.variant_id,
        GENE2_A.variant_id,
        previous_symbol.variant_id,
        GENE3_A.variant_id,
    ]

    narrow = _open(NARROW)
    fresh = _fresh(world, NARROW)
    assert _ids(fresh) == [GENE2_A.variant_id, previous_symbol.variant_id]
    assert narrow.ranking_cached is True
    assert _ranked(narrow) == _ranked(fresh)
    assert narrow.total == fresh.total == 2


def test_a_variant_a_panel_reaches_by_a_region_of_its_own_is_not_lost_to_a_broader_ranking(
    world,
) -> None:
    # A panel imported from PanelApp stores PanelApp's coordinates for GENE2, which reach
    # further than the gene reference's locus: over a variant annotated with another gene
    # that no gene or locus of the Mendeliome reaches. The Mendeliome holds GENE2, so it
    # covers the panel's genes, but its ranking lacks the variant: the panel is computed
    # for itself, with it, not served without it. Matching large panels by position does
    # not make this check redundant.
    beyond_the_locus = _variant("1-4500-A-G", "OTHER_GENE", start=4500)
    world.records.append(beyond_the_locus)
    _mendeliome(world)
    world.set_panel(NARROW, "GENE2", regions=(Region(chr="chr1", start=3000, end=4800),))
    mendeliome = _open(MENDELIOME)
    assert beyond_the_locus.variant_id not in _ids(mendeliome)

    narrow = _open(NARROW)
    fresh = _fresh(world, NARROW)
    assert _ids(fresh) == [GENE2_A.variant_id, beyond_the_locus.variant_id]
    assert narrow.ranking_cached is False
    assert _ranked(narrow) == _ranked(fresh)
    assert narrow.total == fresh.total == 2


def test_a_panel_whose_every_variant_the_mendeliome_ranking_holds_is_served_from_it(world) -> None:
    # The panel reaches GENE3_A by its gene symbol, and the Mendeliome's ranking holds it:
    # the narrowing stays a cache hit.
    _mendeliome(world)
    world.set_panel(NARROW, "GENE3")
    _open(MENDELIOME)

    narrow = _open(NARROW)
    assert narrow.ranking_cached is True
    assert _ranked(narrow) == _ranked(_fresh(world, NARROW))


def test_a_served_panel_is_numbered_within_the_panel(world) -> None:
    # Equal scores rank by variant id, descending: GENE3_A, GENE2_A, GENE1_A in the broader
    # ranking. Served from it, the narrower panel kept the broader ranks (#2 and #3), which
    # the card's "#N", the review dialog and the CSV's Rank column show.
    world.set_panel(BROAD, "GENE1", "GENE2", "GENE3")
    world.set_panel(NARROW, "GENE1", "GENE2")
    broad = _open(BROAD)
    assert [(str(v.id), v.priority.rank) for v in broad.variants] == [
        (GENE3_A.variant_id, 1),
        (GENE2_A.variant_id, 2),
        (GENE1_A.variant_id, 3),
    ]

    narrow = _open(NARROW)
    assert narrow.ranking_cached is True
    assert [(str(v.id), v.priority.rank) for v in narrow.variants] == [
        (GENE2_A.variant_id, 1),
        (GENE1_A.variant_id, 2),
    ]
    assert _ranked(narrow) == _ranked(_fresh(world, NARROW))

    # A later page goes on from the ranks of the pages before it.
    second = _open(NARROW, page=2, page_size=1)
    assert second.ranking_cached is True
    assert [(str(v.id), v.priority.rank) for v in second.variants] == [(GENE1_A.variant_id, 2)]
    assert _ranked(second) == _ranked(_fresh(world, NARROW, page=2, page_size=1))


def _compound_het_pair(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> tuple[SmallVariantRecord, SmallVariantRecord]:
    """A compound-het pair in GENE_CH, one variant from each unaffected parent. The maternal
    one also lies in GENE_OV, a gene overlapping GENE_CH, and is annotated with it. The
    segregation modes are the real ones, over the trio."""
    monkeypatch.setattr(cfv, "_segregation_modes_by_variant", _segregation_modes_by_variant)
    maternal = _variant(
        "1-5100-A-G",
        "GENE_CH",
        start=5100,
        other_genes=("GENE_OV",),
        calls={"PROBAND": "0/1", "MOTHER": "0/1", "FATHER": "0/0"},
    )
    paternal = _variant(
        "1-5900-A-G",
        "GENE_CH",
        start=5900,
        calls={"PROBAND": "0/1", "MOTHER": "0/0", "FATHER": "0/1"},
    )
    world.records = [maternal, paternal]
    world.loci["GENE_CH"] = [Region(chr="1", start=5000, end=6000)]
    world.loci["GENE_OV"] = [Region(chr="1", start=5050, end=5300)]
    world.set_panel(BROAD, "GENE_CH", "GENE_OV")
    return maternal, paternal


def test_a_compound_het_pair_the_panel_splits_is_not_carried_over(world, monkeypatch) -> None:
    # A panel with GENE_OV and not GENE_CH reaches the maternal variant alone. Ranked within
    # that panel it has no partner, so no compound-het mode, and a lower segregation weight
    # and score; served from the ranking of both, it kept the pair's.
    maternal, paternal = _compound_het_pair(world, monkeypatch)
    world.set_panel(NARROW, "GENE_OV")
    trio = _trio_context()
    broad = _open(BROAD, context=trio)
    paired = {str(v.id): v.priority for v in broad.variants}
    assert paired[maternal.variant_id].segregation_modes == [MODE_COMPOUND_HET]
    assert paired[paternal.variant_id].segregation_modes == [MODE_COMPOUND_HET]

    narrow = _open(NARROW, context=trio)
    fresh = _fresh(world, NARROW, context=trio)
    assert _ids(fresh) == [maternal.variant_id]
    alone = fresh.variants[0].priority
    assert alone.segregation_modes == []
    assert alone.segregation_weight < paired[maternal.variant_id].segregation_weight
    assert alone.combined_score < paired[maternal.variant_id].combined_score
    assert _ranked(narrow) == _ranked(fresh)
    # Not served from the broader ranking: computed for the panel itself.
    assert narrow.ranking_cached is False


def test_a_panel_is_served_from_another_ranking_that_does_not_hold_the_pair(world, monkeypatch) -> None:
    # The ranking of both variants is passed over, not the request: a ranking of a panel
    # with GENE_OV and GENE3 holds the maternal variant without its partner, as the panel
    # with GENE_OV alone ranks it, and serves that panel. The broader ranking is tried
    # first (the larger one is preferred).
    maternal, paternal = _compound_het_pair(world, monkeypatch)
    world.records.extend([GENE1_A, GENE3_A])
    world.set_panel(BROAD, "GENE_CH", "GENE_OV", "GENE1")
    world.set_panel(OTHER, "GENE_OV", "GENE3")
    world.set_panel(NARROW, "GENE_OV")
    trio = _trio_context()
    broad = _open(BROAD, context=trio)
    assert _ids(broad) == sorted([GENE1_A.variant_id, maternal.variant_id, paternal.variant_id])
    other = _open(OTHER, context=trio)
    assert _ids(other) == sorted([maternal.variant_id, GENE3_A.variant_id])

    narrow = _open(NARROW, context=trio)
    assert narrow.ranking_cached is True
    assert _ranked(narrow) == _ranked(_fresh(world, NARROW, context=trio))
    assert narrow.variants[0].priority.segregation_modes == []


def test_a_panel_that_keeps_a_compound_het_pair_whole_is_served_with_it(world, monkeypatch) -> None:
    maternal, paternal = _compound_het_pair(world, monkeypatch)
    world.set_panel(NARROW, "GENE_CH")
    trio = _trio_context()
    _open(BROAD, context=trio)

    narrow = _open(NARROW, context=trio)
    assert narrow.ranking_cached is True
    assert _ranked(narrow) == _ranked(_fresh(world, NARROW, context=trio))
    assert {str(v.id): v.priority.segregation_modes for v in narrow.variants} == {
        maternal.variant_id: [MODE_COMPOUND_HET],
        paternal.variant_id: [MODE_COMPOUND_HET],
    }
