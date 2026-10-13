"""A ranked search keeps what the same search keeps unranked (CLIN-44).

*Phenotype prioritization* orders a search; it must not change which variants, and which
compound-het pairs, the search keeps. The ranked path (``_prioritized_small_variants_page``)
answered before the rules the unranked search applies in Python had run: a compound-het or
recessive search listed every variant its SQL prefilter let through (heterozygous, or
carrying the allele, in every affected member), not only the pairs and the homozygous and
X-linked variants; expanded carrier screening listed every variant either partner carries,
not only the couple's at-risk genes and the female partner's X-linked variants
(REQ-CARR-002).

Each search is opened twice through ``get_family_small_variants_page``, unranked and ranked,
over one trio, and the two are compared on what they keep: the single variants, the pairs
(their variants, phase and phase evidence) and the total. The ranked search must also list
its items in the ranking's order, a pair at its better-ranked variant; keep its cache, exact
and narrowed to a sub-panel, correct; and keep the candidate window's signals honest.

The fakes stand in for ClickHouse and Postgres. The row fetch models the SQL: the
inheritance and carrier prefilters (``_small_native_inheritance_clauses``, here through the
record matchers it mirrors), the panel and record filter, and the window. The ranking-cache
rows live in memory, keyed by the real hashes; the phenotype scores are given per gene. All
samples and values are synthetic.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.services import variant_ranking_cache as vrc
from backend.app.services.clickhouse_family_variants import (
    _PRIORITIZE_CANDIDATE_LIMIT,
    export_family_small_variants,
    get_family_small_variants_page,
)
from backend.app.services.clickhouse_variant_queries import (
    _PANEL_REGION_INLINE_LIMIT,
    _SMALL_INHERITANCE_MAX_CANDIDATE_ROWS,
    _call_has_alt,
    _call_is_het,
    _carrier_partner_names,
    _family_affected_unaffected_sample_names,
    _is_mitochondrial_chromosome,
    _record_matches_de_novo_dominant,
    _record_matches_homozygous_recessive,
    _record_matches_x_linked_recessive,
    _sample_sex_map,
    _small_call_map,
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
from backend.app.services.monarch_phenotype_score import GenePhenotypeScore
from backend.app.services.variant_prioritization import (
    MODE_COMPOUND_HET,
    MODE_HOM_RECESSIVE,
    MODE_X_LINKED,
)

PANEL_BC = "c1a44000-0000-4000-8000-000000000001"
PANEL_B_PART = "c1a44000-0000-4000-8000-000000000002"

# chrX outside the pseudo-autosomal regions of GRCh38.
_X_NON_PAR = 31_000_000


def _variant(
    variant_id: str,
    gene: str,
    *,
    start: int,
    chr: str = "1",
    proband: str,
    mother: str = "0/0",
    father: str = "0/0",
    impact: str = "MODERATE",
    clinvar: str | None = None,
) -> SmallVariantRecord:
    annotation: dict[str, Any] = {"gene": gene, "impact": impact, "effect": "missense_variant"}
    if clinvar:
        annotation["clinvar"] = clinvar
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chr,
        start=start,
        end=start,
        ref="A",
        alt="G",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=[gene],
        annotations=[annotation],
        calls=[
            SmallVariantCall(sample=sample, gt=gt, gq=40.0, dp=30, af=[], ad=[], ps=None)
            for sample, gt in (("PROBAND", proband), ("MOTHER", mother), ("FATHER", father))
        ],
    )


# The trio's variants. Each id sorts after the one before it in its gene, so equal scores
# rank the later one first (the ranking breaks ties on the variant id, descending).
A1 = _variant("v-a1", "GENE_A", start=1_000, proband="0/1", mother="0/1")
A2 = _variant("v-a2", "GENE_A", start=1_100, proband="0/1", father="0/1")
# GENE_B: one maternal variant pairs with each of two paternal ones; the two paternal ones
# trace to one parent, in cis, and form no pair.
B1 = _variant("v-b1", "GENE_B", start=2_000, proband="0/1", mother="0/1")
B2 = _variant("v-b2", "GENE_B", start=2_100, proband="0/1", father="0/1")
B3 = _variant("v-b3", "GENE_B", start=2_200, proband="0/1", father="0/1")
# Homozygous in the proband, both parents carriers: recessive.
C1 = _variant("v-c1", "GENE_C", start=3_000, proband="1/1", mother="0/1", father="0/1")
# De novo, pathogenic, in a gene matching the phenotype: ranked first wherever it is kept.
# No pair, and neither parent carries it.
D1 = _variant("v-d1", "GENE_D", start=4_000, proband="0/1", impact="HIGH", clinvar="Pathogenic")
# The mother's alone, the only variant in its gene: no pair, and no couple finding.
E1 = _variant("v-e1", "GENE_E", start=5_000, proband="0/1", mother="0/1")
# The son's hemizygous call from his mother: X-linked recessive, and a female partner's
# X-linked carrier finding on its own.
X1 = _variant("v-x1", "GENE_X", chr="X", start=_X_NON_PAR, proband="1", mother="0/1")

TRIO = [A1, A2, B1, B2, B3, C1, D1, E1, X1]

# The phenotype score of each gene against the proband's HPO term: GENE_B and GENE_D match
# best, then GENE_C and GENE_X; GENE_A and GENE_E have no phenotype data.
PHENOTYPE = {"GENE_B": 0.9, "GENE_D": 0.9, "GENE_C": 0.6, "GENE_X": 0.3}

GENE_LOCI = {
    "GENE_A": [Region(chr="1", start=900, end=1_200)],
    "GENE_B": [Region(chr="1", start=1_900, end=2_300)],
    "GENE_C": [Region(chr="1", start=2_900, end=3_100)],
    # A gene the gene reference places over B1 and B2, not B3; no variant is annotated with
    # it, so a panel holding it reaches those two by position only.
    "GENE_B_PART": [Region(chr="1", start=1_950, end=2_150)],
}


def _trio() -> FamilyMetadataContext:
    """An affected son and his unaffected parents, linked to him in the pedigree."""
    members = {
        "PROBAND": ("proband", "affected", "male"),
        "MOTHER": ("mother", "unaffected", "female"),
        "FATHER": ("father", "unaffected", "male"),
    }
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM_CLIN44",
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


def _sql_prefilter(record: SmallVariantRecord, filters, context: FamilyMetadataContext) -> bool:
    """The inheritance and carrier part of the SQL filter (``_small_native_inheritance_clauses``),
    through the record matchers it mirrors: the per-variant modes in full, the pairing modes
    by what each variant of a pair must show (heterozygous, or carrying the allele, in every
    affected member), and carrier screening by a partner carrying the allele in a gene,
    outside chrM."""
    affected, unaffected = _family_affected_unaffected_sample_names(context)
    calls = _small_call_map(record)
    if filters.expanded_carrier_screening:
        partners = _carrier_partner_names(context.sample_rows, context.relationship_rows)
        if partners is None or not any(_call_has_alt(calls.get(partner)) for partner in partners):
            return False
        if not record.gene_symbols or _is_mitochondrial_chromosome(record.chr):
            return False
    mode = filters.inheritance
    if mode is None:
        return True
    if mode == "compound_het":
        return all(_call_is_het(calls.get(sample)) for sample in affected)
    if mode == "recessive":
        return all(_call_has_alt(calls.get(sample)) for sample in affected)
    if mode == "de_novo_dominant":
        return _record_matches_de_novo_dominant(
            record,
            affected_samples=affected,
            unaffected_samples=unaffected,
            sample_sex=_sample_sex_map(context.sample_rows),
            assembly_name=context.assembly_name,
        )
    if mode == "recessive_homozygous":
        return _record_matches_homozygous_recessive(
            record, affected_samples=affected, unaffected_samples=unaffected
        )
    if mode == "x_linked":
        return _record_matches_x_linked_recessive(
            record,
            affected_samples=affected,
            unaffected_samples=unaffected,
            sample_sex=_sample_sex_map(context.sample_rows),
        )
    raise AssertionError(mode)


def _panel_sql_match(record: SmallVariantRecord, constraints: PanelFilterConstraints | None) -> bool:
    """The panel part of the SQL filter (``_small_panel_filter_condition``), as
    test_ranking_cache_sub_panel.py models it."""
    if constraints is None or not (constraints.genes or constraints.regions):
        return True
    if constraints.genes and _small_record_hits_gene_terms(record, constraints.genes):
        return True
    if constraints.genes and len(constraints.regions) > _PANEL_REGION_INLINE_LIMIT:
        return False
    return _variant_overlaps_regions(record.chr, record.start, record.end, constraints.regions)


class _World:
    """The family's variants, its HPO terms, the panels and the ranking-cache rows."""

    def __init__(self) -> None:
        self.records: list[SmallVariantRecord] = list(TRIO)
        self.hpo = ["HP:0001250"]
        self.panels: dict[str, list[str]] = {}
        # inputs_hash -> cache row, as family_variant_ranking_cache holds it
        self.cache: dict[str, dict[str, Any]] = {}
        self.fetch_limits: list[int | None] = []
        self.counts = 0

    def matching(self, context, filters, kwargs) -> list[SmallVariantRecord]:
        wanted = kwargs.get("include_variant_ids")
        wanted = None if wanted is None else {str(v) for v in wanted}
        excluded = {str(v) for v in kwargs.get("exclude_variant_ids") or ()}
        ordered = sorted(
            self.records, key=lambda r: (r.chr != "1", r.chr, r.start, r.variant_id)
        )
        return [
            record
            for record in ordered
            if (wanted is None or record.variant_id in wanted)
            and record.variant_id not in excluded
            and _sql_prefilter(record, filters, context)
            and _panel_sql_match(record, kwargs.get("panel_constraints"))
            and _small_record_matches(
                record,
                filters,
                list(kwargs.get("include_regions") or ()),
                list(kwargs.get("exclude_regions") or ()),
                list(kwargs.get("exclude_gene_regions") or ()),
                panel_constraints=kwargs.get("panel_constraints"),
            )
        ]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    state = _World()
    m = "backend.app.services.clickhouse_family_variants."

    async def _ped(*_a, **_k):
        return {"structure_hash": "ped-1"}

    async def _panel_version(_session, panel_id):
        return "1:" if panel_id in state.panels else None

    async def _none(*_a, **_k):
        return None

    async def _empty(*_a, **_k):
        return {}

    async def _refs(*_a, **_k):
        return {"hpo_release": "2026-04-01", "hpo_imported_at": None, "gene_info_updated_at": None}

    async def _hpo(*_a, **_k):
        return list(state.hpo), {term: "Seizure" for term in state.hpo}

    async def _data_version(*_a, **_k):
        return "1:111"

    async def _phenotype(_session, *, gene_symbols, patient_hpo_ids):
        return {
            gene.upper(): GenePhenotypeScore(
                score=PHENOTYPE[gene], matched=[{"hpo_id": hpo_id} for hpo_id in patient_hpo_ids]
            )
            for gene in gene_symbols
            if gene in PHENOTYPE
        }

    async def _fetch_panel_constraints(_session, panel_id, *, assembly_id=None):
        genes = state.panels.get(panel_id)
        if genes is None:
            raise HTTPException(status_code=404, detail="Panel not found")
        regions = [region for gene in genes for region in GENE_LOCI.get(gene, [])]
        return PanelFilterConstraints(genes=tuple(genes), regions=tuple(regions))

    async def _fetch_gene_regions(_session, *, gene_query, assembly_id):
        return list(GENE_LOCI.get(gene_query, []))

    async def _fetch_rows(context, filters, *, limit=None, offset=0, **kwargs):
        state.fetch_limits.append(limit)
        rows = state.matching(context, filters, kwargs)
        return rows[offset:] if limit is None else rows[offset : offset + limit]

    async def _count(context, filters, *, count_limit=10_001, **kwargs):
        state.counts += 1
        found = len(state.matching(context, filters, kwargs))
        return min(found, count_limit), found >= count_limit

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
    monkeypatch.setattr(m + "_affected_present_hpo", _hpo)
    monkeypatch.setattr(m + "score_genes_for_hpo", _phenotype)
    monkeypatch.setattr(m + "_family_small_variant_data_version", _data_version)
    monkeypatch.setattr(m + "_fetch_small_variant_summary", _none)
    monkeypatch.setattr(m + "_fetch_panel_constraints", _fetch_panel_constraints)
    monkeypatch.setattr(m + "_fetch_gene_regions", _fetch_gene_regions)
    monkeypatch.setattr(m + "_fetch_small_variant_rows", _fetch_rows)
    monkeypatch.setattr(m + "_count_small_variant_rows_bounded", _count)
    monkeypatch.setattr(m + "get_cached_ranking", _get_cached)
    monkeypatch.setattr(m + "store_ranking", _store)
    monkeypatch.setattr(m + "find_superset_candidates", _supersets)
    monkeypatch.setattr(m + "_fetch_gene_constraint_metric_map", _empty)
    monkeypatch.setattr(m + "_hydrate_small_variant_outs", _none)
    return state


def _open(*, prioritize: bool, page: int = 1, page_size: int = 100, **filters: Any):
    return asyncio.run(
        get_family_small_variants_page(
            None,  # type: ignore[arg-type]
            context=_trio(),
            page=page,
            page_size=page_size,
            prioritize=prioritize,
            **filters,
        )
    )


def _kept(page) -> dict[str, Any]:
    """What a page keeps: its single variants, its pairs and its total."""
    return {
        "variants": sorted(str(variant.id) for variant in page.variants),
        "pairs": sorted(
            (
                group.group_key,
                tuple(str(variant.id) for variant in group.variants),
                group.phase,
                group.phase_evidence,
            )
            for group in page.variant_groups
        ),
        "total": page.total,
        "total_is_estimated": page.total_is_estimated,
    }


def _pair(left: SmallVariantRecord, right: SmallVariantRecord) -> tuple[str, tuple[str, str], str, str]:
    """A pair as _kept lists it: traced through the parents, one hit to each, so in trans."""
    return (
        f"{left.variant_id}::{right.variant_id}",
        (left.variant_id, right.variant_id),
        "trans",
        "segregation",
    )


# Each search, and what the unranked search keeps of the trio.
SEARCHES = {
    "compound_het": (
        {"inheritance": "compound_het"},
        {"variants": [], "pairs": [_pair(A1, A2), _pair(B1, B2), _pair(B1, B3)]},
    ),
    "recessive": (
        {"inheritance": "recessive"},
        {"variants": [C1.variant_id, X1.variant_id], "pairs": [_pair(A1, A2), _pair(B1, B2), _pair(B1, B3)]},
    ),
    "expanded_carrier_screening": (
        {"expanded_carrier_screening": True},
        {
            "variants": sorted(r.variant_id for r in (A1, A2, B1, B2, B3, C1, X1)),
            "pairs": [],
        },
    ),
    "carrier_screening_and_compound_het": (
        {"expanded_carrier_screening": True, "inheritance": "compound_het"},
        {"variants": [], "pairs": [_pair(A1, A2), _pair(B1, B2), _pair(B1, B3)]},
    ),
    "carrier_screening_and_recessive": (
        {"expanded_carrier_screening": True, "inheritance": "recessive"},
        {"variants": [C1.variant_id, X1.variant_id], "pairs": [_pair(A1, A2), _pair(B1, B2), _pair(B1, B3)]},
    ),
    # A mode SQL decides for each variant, applied in Python after the couple's rule (a
    # guard: on this trio the SQL prefilters alone keep the same).
    "carrier_screening_and_x_linked": (
        {"expanded_carrier_screening": True, "inheritance": "x_linked"},
        {"variants": [X1.variant_id], "pairs": []},
    ),
    # The modes the SQL decides for each variant, and no mode: guards.
    "de_novo_dominant": ({"inheritance": "de_novo_dominant"}, {"variants": [D1.variant_id], "pairs": []}),
    "recessive_homozygous": ({"inheritance": "recessive_homozygous"}, {"variants": [C1.variant_id], "pairs": []}),
    "x_linked": ({"inheritance": "x_linked"}, {"variants": [X1.variant_id], "pairs": []}),
    "no_inheritance": ({}, {"variants": sorted(r.variant_id for r in TRIO), "pairs": []}),
}


@pytest.mark.parametrize("name", list(SEARCHES))
def test_a_ranked_search_keeps_what_the_unranked_search_keeps(world, name: str) -> None:
    filters, expected = SEARCHES[name]
    unranked = _kept(_open(prioritize=False, **filters))
    assert {key: unranked[key] for key in ("variants", "pairs")} == expected

    ranked = _open(prioritize=True, **filters)
    assert ranked.ranking_cached is False
    assert _kept(ranked) == unranked
    # And again, served from the cache.
    again = _open(prioritize=True, **filters)
    assert again.ranking_cached is True
    assert _kept(again) == unranked


def _item(page) -> tuple[str, tuple[str, ...], tuple[int, ...]]:
    """The one item of a page of one item: its kind, its variants and their ranks."""
    entries = [
        ("pair", tuple(str(v.id) for v in group.variants), tuple(v.priority.rank for v in group.variants))
        for group in page.variant_groups
    ] + [("variant", (str(v.id),), (v.priority.rank,)) for v in page.variants]
    assert len(entries) == 1, entries
    return entries[0]


def _items_in_order(**filters: Any) -> list[tuple[str, tuple[str, ...], tuple[int, ...]]]:
    """The ranked search's items in the order it lists them, read one page at a time."""
    first = _open(prioritize=True, page_size=1, **filters)
    return [_item(first)] + [
        _item(_open(prioritize=True, page=page, page_size=1, **filters))
        for page in range(2, first.total + 1)
    ]


def test_a_ranked_recessive_search_lists_its_pairs_and_variants_in_the_ranking_s_order(world) -> None:
    # Every kept variant is a moderate missense of the same rarity and fits a strong mode
    # (compound het, homozygous or X-linked), so the phenotype orders them: GENE_B, then
    # GENE_C, GENE_X and GENE_A. Equal scores rank by variant id, descending. The ranks
    # count the kept variants only: the de novo variant D1, which ranks first in a search
    # that keeps it, is not one of them.
    items = _items_in_order(inheritance="recessive")
    assert items == [
        # A pair is placed at its better-ranked variant: B3 is first.
        ("pair", (B1.variant_id, B3.variant_id), (3, 1)),
        ("pair", (B1.variant_id, B2.variant_id), (3, 2)),
        ("variant", (C1.variant_id,), (4,)),
        ("variant", (X1.variant_id,), (5,)),
        ("pair", (A1.variant_id, A2.variant_id), (7, 6)),
    ]
    whole = _open(prioritize=True, inheritance="recessive")
    modes = {
        str(v.id): v.priority.segregation_modes
        for v in [*whole.variants, *(v for group in whole.variant_groups for v in group.variants)]
    }
    assert modes == {
        A1.variant_id: [MODE_COMPOUND_HET],
        A2.variant_id: [MODE_COMPOUND_HET],
        B1.variant_id: [MODE_COMPOUND_HET],
        B2.variant_id: [MODE_COMPOUND_HET],
        B3.variant_id: [MODE_COMPOUND_HET],
        C1.variant_id: [MODE_HOM_RECESSIVE],
        X1.variant_id: [MODE_X_LINKED],
    }


def test_a_ranked_compound_het_search_ranks_its_pairs_variants_only(world) -> None:
    items = _items_in_order(inheritance="compound_het")
    assert items == [
        ("pair", (B1.variant_id, B3.variant_id), (3, 1)),
        ("pair", (B1.variant_id, B2.variant_id), (3, 2)),
        ("pair", (A1.variant_id, A2.variant_id), (5, 4)),
    ]


def test_a_ranked_carrier_screen_ranks_the_couple_s_findings_only(world) -> None:
    ranked = _open(prioritize=True, expanded_carrier_screening=True)
    order = [(str(v.id), v.priority.rank) for v in ranked.variants]
    assert order == [
        (B3.variant_id, 1),
        (B2.variant_id, 2),
        (B1.variant_id, 3),
        (C1.variant_id, 4),
        (X1.variant_id, 5),
        (A2.variant_id, 6),
        (A1.variant_id, 7),
    ]
    # E1, the mother's alone in its gene, is no finding, and is not ranked.
    assert E1.variant_id not in {variant_id for variant_id, _rank in order}


def _without_a1(ranking: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in ranking if entry["variant_id"] != A1.variant_id]


def _a1_unpaired(ranking: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**entry, "priority": {**entry["priority"], "segregation_modes": []}}
        if entry["variant_id"] == A1.variant_id
        else entry
        for entry in ranking
    ]


@pytest.mark.parametrize("tamper", [_without_a1, _a1_unpaired])
def test_a_cached_pairing_ranking_is_used_only_while_it_holds_the_kept_variants_as_they_pair(
    world, tamper
) -> None:
    # The cache holds the order of the variants, not the pairs: a search that pairs works
    # its pairs out again, and uses the cached order only when it holds every variant kept,
    # with the segregation modes they have now. Otherwise the search is ranked again.
    first = _open(prioritize=True, inheritance="compound_het")
    [row] = world.cache.values()
    row["ranking"] = tamper(row["ranking"])
    again = _open(prioritize=True, inheritance="compound_het")
    assert again.ranking_cached is False
    assert _kept(again) == _kept(first)


def test_the_ranked_csv_export_holds_what_the_unranked_export_holds(world) -> None:
    async def export(prioritize: bool):
        return await export_family_small_variants(
            None,  # type: ignore[arg-type]
            context=_trio(),
            prioritize=prioritize,
            inheritance="recessive",
        )

    unranked = asyncio.run(export(False))
    ranked = asyncio.run(export(True))
    assert sorted(str(v.id) for v in ranked.rows) == sorted(str(v.id) for v in unranked.rows)
    assert ranked.truncated is unranked.truncated is False
    assert all(v.priority is not None for v in ranked.rows)


def test_a_sub_panel_of_a_ranked_pairing_search_is_served_as_its_own_computation_ranks_it(world) -> None:
    # The ranking without a panel holds every variant the panel keeps, with the same pairs,
    # so it serves the panel, numbered within the panel.
    world.panels[PANEL_BC] = ["GENE_B", "GENE_C"]
    _open(prioritize=True, inheritance="recessive")
    served = _open(prioritize=True, inheritance="recessive", panel_id=PANEL_BC)
    assert served.ranking_cached is True

    kept_cache = dict(world.cache)
    world.cache.clear()
    fresh = _open(prioritize=True, inheritance="recessive", panel_id=PANEL_BC)
    world.cache.update(kept_cache)
    assert fresh.ranking_cached is False

    assert _kept(served) == _kept(fresh)
    assert _kept(served)["pairs"] == [_pair(B1, B2), _pair(B1, B3)]
    assert _kept(served)["variants"] == [C1.variant_id]

    def ranked(page):
        return [
            (str(v.id), v.priority.model_dump())
            for v in [*(v for group in page.variant_groups for v in group.variants), *page.variants]
        ]

    assert ranked(served) == ranked(fresh)
    # Numbered within the panel: B3, B2, B1 and C1 (B1 is in both pairs).
    assert sorted(priority["rank"] for _id, priority in ranked(served)) == [1, 2, 3, 3, 4]


def test_a_sub_panel_pairs_within_its_own_reach(world) -> None:
    # A panel reaching B1 and B2, not B3, keeps the pair B1 + B2 only, served from the
    # ranking without a panel or computed for itself, as the unranked search keeps it.
    world.panels[PANEL_B_PART] = ["GENE_B_PART"]
    _open(prioritize=True, inheritance="compound_het")
    served = _open(prioritize=True, inheritance="compound_het", panel_id=PANEL_B_PART)
    assert served.ranking_cached is True
    unranked = _open(prioritize=False, inheritance="compound_het", panel_id=PANEL_B_PART)
    assert _kept(served) == _kept(unranked)
    assert _kept(served)["pairs"] == [_pair(B1, B2)]
    [group] = served.variant_groups
    assert [(str(v.id), v.priority.rank) for v in group.variants] == [
        (B1.variant_id, 2),
        (B2.variant_id, 1),
    ]


# The candidate window: a search that pairs reads at most _PRIORITIZE_CANDIDATE_LIMIT
# candidates, the most the unranked search reads too.


def _fill_window(world: _World, *, beyond: int) -> None:
    """More candidates than the window holds: the trio's variants first, then fillers that
    pass every prefilter (the proband and the mother heterozygous), one gene each."""
    fillers = [
        _variant(f"v-fill{index:05d}", f"FILL{index:05d}", start=10_000 + index, proband="0/1", mother="0/1")
        for index in range(_PRIORITIZE_CANDIDATE_LIMIT + beyond)
    ]
    world.records = [*TRIO, *fillers]


def test_the_ranked_search_reads_the_largest_window_the_unranked_one_reads() -> None:
    assert _PRIORITIZE_CANDIDATE_LIMIT == _SMALL_INHERITANCE_MAX_CANDIDATE_ROWS


@pytest.mark.parametrize(
    "filters",
    [
        {"inheritance": "compound_het"},
        {"inheritance": "recessive"},
        {"expanded_carrier_screening": True},
    ],
)
def test_a_ranked_pairing_search_past_its_window_says_so_and_counts_what_it_found(
    world, filters: dict[str, Any]
) -> None:
    _fill_window(world, beyond=50)
    ranked = _open(prioritize=True, **filters)
    assert world.fetch_limits[-1] == _PRIORITIZE_CANDIDATE_LIMIT + 1
    # A pair or a couple's gene beyond the window is missing: the candidates were capped,
    # as the unranked search says, and the ranking covers the window only.
    assert ranked.candidates_capped is True
    assert ranked.ranking_truncated is True
    assert ranked.candidate_limit == _PRIORITIZE_CANDIDATE_LIMIT
    assert ranked.total_is_estimated is True
    # The count is what the search found within the window, a lower bound, never the SQL
    # count of the candidates before pairing.
    assert world.counts == 0
    assert ranked.total == len(ranked.variants) + len(ranked.variant_groups)
    unranked = _open(prioritize=False, page_size=200, **filters)
    assert unranked.candidates_capped is True
    assert _kept(ranked) == _kept(unranked)


def test_a_ranked_pairing_search_past_its_window_is_exported_as_truncated(world) -> None:
    _fill_window(world, beyond=50)
    export = asyncio.run(
        export_family_small_variants(
            None,  # type: ignore[arg-type]
            context=_trio(),
            prioritize=True,
            inheritance="compound_het",
        )
    )
    assert export.truncated is True
    assert export.truncated_reason == "candidate-limit"


def test_a_ranked_search_without_pairing_past_its_window_still_counts_every_match(world) -> None:
    # A guard: a search that keeps each variant for its own record still reports the SQL
    # count of its matches, and only its ranking as truncated.
    _fill_window(world, beyond=50)
    ranked = _open(prioritize=True)
    assert ranked.ranking_truncated is True
    assert ranked.candidates_capped is False
    assert world.counts == 1
    assert ranked.total == len(world.records)
