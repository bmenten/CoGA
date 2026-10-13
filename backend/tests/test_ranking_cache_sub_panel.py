"""CLIN-27: a narrower panel is served from a broader cached ranking only while that
ranking's panel holds what it held when the ranking was computed.

On an exact miss, ``_serve_subpanel_from_superset`` restricts a broader cached ranking to
the requested panel's variants. That check can only drop variants from the cached
ranking, never add any, so a ranking computed before its panel gained a gene served the
panel afterwards without that gene's variants. Each cached ranking now records the genes
and regions its panel filter applied (``panel_members_digest``): a broader ranking is used
only while its panel holds exactly those, and one that recorded none is not used.

The keys also cover the regions the query applied: a gene's loci are read from the gene
reference when the query runs, so a reload that moves a gene changes a panel's or a gene
filter's reach without any version or filter changing; and a ranking computed without the
query's intervals (the background warm's replay) is not served for the query.

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
    _small_record_matches,
)
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    SmallVariantCall,
    SmallVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

BROAD = "0b5e1c1e-0000-4000-8000-000000000001"
NARROW = "0b5e1c1e-0000-4000-8000-000000000002"
OTHER = "0b5e1c1e-0000-4000-8000-000000000003"


def _variant(variant_id: str, gene: str, *, start: int) -> SmallVariantRecord:
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
        gene_symbols=[gene],
        annotations=[{"gene": gene}],
        calls=[SmallVariantCall(sample="PROBAND", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
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
        }
        self.records = [GENE1_A, GENE2_A, GENE3_A]
        # inputs_hash -> cache row, as family_variant_ranking_cache holds it
        self.cache: dict[str, dict[str, Any]] = {}

    def set_panel(self, panel_id: str, *genes: str, version: int = 1) -> None:
        self.panels[panel_id] = {"version": version, "genes": list(genes)}

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
        regions = [region for gene in panel["genes"] for region in state.loci.get(gene, [])]
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
        rows = [
            row
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
):
    """Open the prioritised view as a request does: the panel's genes and regions, and the
    intervals, are resolved first, then handed to the ranking. ``resolve_intervals=False``
    hands over none, as the background warm does when it replays a stored query."""

    async def _run():
        filters = SmallVariantQueryFilters(
            page=1, page_size=50, panel_id=panel_id, gene=gene, intervals=intervals
        )
        constraints = (
            await cfv._fetch_panel_constraints(None, panel_id, assembly_id="assembly-uuid")
            if panel_id
            else PanelFilterConstraints()
        )
        return await cfv._prioritized_small_variants_page(
            None,  # type: ignore[arg-type]
            context=_context(),
            filters=filters,
            page=1,
            page_size=50,
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


def test_a_ranking_from_before_the_gene_reference_moved_the_gene_filtered_on_is_not_served(
    world,
) -> None:
    # The gene filter keeps the variants annotated with the gene inside its loci: a
    # variant just upstream is outside them until a reload extends the gene over it. The
    # filter's text is unchanged, so only the loci the query applied tell the two apart.
    upstream = _variant("1-2500-A-G", "GENE1", start=2500)
    world.records.append(upstream)
    first = _open(None, gene="GENE1")
    assert first.ranking_cached is False
    assert _ids(first) == [GENE1_A.variant_id]

    world.loci["GENE1"] = [Region(chr="1", start=1000, end=3000)]
    after = _open(None, gene="GENE1")
    assert after.ranking_cached is False
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
