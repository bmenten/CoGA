"""A prioritised ranking ordered by the variant score (``rank_by``).

By the combined score, the default, a variant in a gene without Monarch phenotype data scores
0 on phenotype and sinks below an equally deleterious variant in a phenotype-matched gene.
Ordered by the variant score, such a novel-gene candidate ranks by its own evidence. Either
order ranks the same variants with the same scores; only their order and rank differ, over
the whole ranking, not the page.

The ranking is computed and cached in the combined order; the variant order is put on it as
the page is served, whether the ranking was computed, read from its own cache row, or narrowed
from a broader panel's. These tests drive ``_prioritized_small_variants_page`` with the real
scoring, cache keys and superset path, over an in-memory stand-in for the cache rows, the panel
tables, the gene reference, the phenotype scores and the family's variants; and
``_prioritized_structural_variants_page`` over stubbed reads.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.schemas import VariantPage
from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services.clickhouse_variant_records import (
    PanelFilterConstraints,
    Region,
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import (
    SmallVariantQueryFilters,
    StructuralVariantQueryFilters,
)
from backend.app.services.monarch_phenotype_score import GenePhenotypeScore
from backend.app.services.variant_prioritization import (
    RANK_BY_COMBINED_SCORE,
    RANK_BY_VARIANT_SCORE,
    RankBy,
    priority_order_key,
)
from backend.tests._ranking_cache_fakes import install_ranking_storage, sql_keeps

BROAD = "0b5e1c1e-0000-4000-8000-0000000000b1"
NARROW = "0b5e1c1e-0000-4000-8000-0000000000b2"
SEIZURE = "HP:0001250"


def _variant(variant_id: str, gene: str, impact: str, *, start: int) -> SmallVariantRecord:
    """A heterozygous variant of the proband, annotated with ``gene`` at ``impact``."""
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
        annotations=[{"gene": gene, "impact": impact}],
        calls=[SmallVariantCall(sample="PROBAND", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
    )


# KNOWN matches the proband's phenotype; NOVEL has no Monarch phenotype data; OTHER is a third
# gene only the broad panel holds. By the combined score the KNOWN variants lead; by the variant
# score the high-impact NOVEL variant does, and of two variants with the same variant score the
# better combined score goes first.
KNOWN_MODERATE = _variant("1-1500-A-G", "KNOWN", "MODERATE", start=1500)
KNOWN_LOW = _variant("1-1600-A-G", "KNOWN", "LOW", start=1600)
NOVEL_HIGH = _variant("1-3500-A-G", "NOVEL", "HIGH", start=3500)
NOVEL_MODERATE = _variant("1-3600-A-G", "NOVEL", "MODERATE", start=3600)
OTHER_HIGH = _variant("1-7500-A-G", "OTHER", "HIGH", start=7500)

COMBINED_ORDER = [KNOWN_MODERATE, KNOWN_LOW, NOVEL_HIGH, NOVEL_MODERATE]
VARIANT_ORDER = [NOVEL_HIGH, KNOWN_MODERATE, NOVEL_MODERATE, KNOWN_LOW]


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM_RANK_BY",
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
        self.panels: dict[str, list[str]] = {}
        self.loci: dict[str, list[Region]] = {
            "KNOWN": [Region(chr="1", start=1000, end=2000)],
            "NOVEL": [Region(chr="1", start=3000, end=4000)],
            "OTHER": [Region(chr="1", start=7000, end=8000)],
        }
        self.records = [KNOWN_MODERATE, KNOWN_LOW, NOVEL_HIGH, NOVEL_MODERATE]
        self.cache: dict[str, dict[str, Any]] = {}


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    state = _World()
    m = "backend.app.services.clickhouse_family_variants."

    async def _panel_version(_session: Any, panel_id: str | None) -> str | None:
        return "1:" if panel_id in state.panels else None

    async def _seizures(*_a: Any, **_k: Any) -> tuple[list[str], dict[str, str]]:
        return [SEIZURE], {SEIZURE: "Seizure"}

    async def _phenotype_scores(
        _session: Any, *, gene_symbols: set[str], patient_hpo_ids: list[str]
    ) -> dict[str, GenePhenotypeScore]:
        # Only KNOWN has Monarch phenotype data; it matches the seizures.
        return {
            gene.upper(): GenePhenotypeScore(score=0.9, matched=[{"hpo_id": SEIZURE}])
            for gene in gene_symbols
            if gene.upper() == "KNOWN"
        }

    async def _fetch_panel_constraints(
        _session: Any, panel_id: str, *, assembly_id: str | None = None
    ) -> PanelFilterConstraints:
        genes = state.panels.get(panel_id)
        if genes is None:
            raise HTTPException(status_code=404, detail="Panel not found")
        regions = [region for gene in genes for region in state.loci[gene]]
        return PanelFilterConstraints(genes=tuple(genes), regions=tuple(regions))

    async def _fetch_rows(
        _context: Any,
        filters: SmallVariantQueryFilters,
        *,
        include_variant_ids: Any = None,
        panel_constraints: PanelFilterConstraints | None = None,
        **_k: Any,
    ) -> list[SmallVariantRecord]:
        wanted = None if include_variant_ids is None else {str(v) for v in include_variant_ids}
        return [
            record
            for record in state.records
            if (wanted is None or record.variant_id in wanted)
            and sql_keeps(record, filters, panel_constraints=panel_constraints)
        ]

    install_ranking_storage(monkeypatch, state.cache, panel_version=_panel_version)
    monkeypatch.setattr(m + "_affected_present_hpo", _seizures)
    monkeypatch.setattr(m + "score_genes_for_hpo", _phenotype_scores)
    monkeypatch.setattr(m + "_fetch_panel_constraints", _fetch_panel_constraints)
    monkeypatch.setattr(m + "_fetch_small_variant_rows", _fetch_rows)
    monkeypatch.setattr(m + "_segregation_modes_by_variant", lambda *a, **k: {})
    return state


def _open(
    rank_by: RankBy, *, panel_id: str | None = None, page: int = 1, page_size: int = 50
) -> VariantPage:
    """Open the prioritised view as a request does, in the order ``rank_by``."""

    async def _run() -> VariantPage:
        constraints = (
            await cfv._fetch_panel_constraints(None, panel_id, assembly_id="assembly-uuid")
            if panel_id
            else PanelFilterConstraints()
        )
        return await cfv._prioritized_small_variants_page(
            None,  # type: ignore[arg-type]
            context=_context(),
            filters=SmallVariantQueryFilters(page=page, page_size=page_size, panel_id=panel_id),
            page=page,
            page_size=page_size,
            panel_constraints=constraints,
            review_variant_ids=None,
            excluded_review_variant_ids=set(),
            include_review_filter_active=False,
            include_regions=[],
            exclude_regions=[],
            exclude_gene_regions=[],
            exclude_gene_terms=[],
            small_variant_summary=None,
            rank_by=rank_by,
        )

    return asyncio.run(_run())


def _order(page: VariantPage) -> list[str]:
    return [str(variant.id) for variant in page.variants]


def _ids(records: list[SmallVariantRecord]) -> list[str]:
    return [record.variant_id for record in records]


def _ranks(page: VariantPage) -> list[int | None]:
    return [variant.priority.rank if variant.priority else None for variant in page.variants]


def _scores(page: VariantPage) -> dict[str, tuple[float, float]]:
    """Each variant's (combined, variant) score, whatever its place."""
    return {
        str(variant.id): (variant.priority.combined_score, variant.priority.variant_score)
        for variant in page.variants
        if variant.priority
    }


def test_ranked_by_the_variant_score_a_novel_gene_candidate_leads(world: _World) -> None:
    combined = _open(RANK_BY_COMBINED_SCORE)
    world.cache.clear()
    by_variant = _open(RANK_BY_VARIANT_SCORE)

    assert (combined.ranking_cached, by_variant.ranking_cached) == (False, False)
    assert _order(combined) == _ids(COMBINED_ORDER)
    assert _order(by_variant) == _ids(VARIANT_ORDER)
    # The novel gene's variant scores 0 on phenotype: its combined score is half its variant score.
    novel = next(variant for variant in by_variant.variants if str(variant.id) == NOVEL_HIGH.variant_id)
    assert novel.priority is not None and novel.priority.phenotype_score is None
    # The same variants with the same scores, each numbered by its place in its own order.
    assert _scores(by_variant) == _scores(combined)
    assert _ranks(combined) == _ranks(by_variant) == [1, 2, 3, 4]
    assert (combined.total, by_variant.total) == (4, 4)


def test_the_cache_holds_the_combined_order_and_serves_either(world: _World) -> None:
    fresh = _open(RANK_BY_VARIANT_SCORE)
    assert fresh.ranking_cached is False

    # One cache row, in the combined order whatever order the page that computed it took.
    (row,) = world.cache.values()
    assert [entry["variant_id"] for entry in row["ranking"]] == _ids(COMBINED_ORDER)
    assert [entry["priority"]["rank"] for entry in row["ranking"]] == [1, 2, 3, 4]

    # Served from that row in either order, as a fresh computation of that order gives it.
    cached = _open(RANK_BY_VARIANT_SCORE)
    assert cached.ranking_cached is True
    assert _order(cached) == _ids(VARIANT_ORDER)
    assert [variant.priority.model_dump() for variant in cached.variants if variant.priority] == [
        variant.priority.model_dump() for variant in fresh.variants if variant.priority
    ]
    cached_combined = _open(RANK_BY_COMBINED_SCORE)
    assert cached_combined.ranking_cached is True
    assert _order(cached_combined) == _ids(COMBINED_ORDER)
    assert _ranks(cached_combined) == [1, 2, 3, 4]
    assert len(world.cache) == 1


@pytest.mark.parametrize("cached", [False, True], ids=["computed", "from-cache"])
def test_each_page_is_cut_from_the_whole_ranking_in_the_order_asked_for(world: _World, cached: bool) -> None:
    # The order is the ranking's, not the page's: the best variant score leads page 1 even
    # where the combined order puts it on a later page.
    if cached:
        _open(RANK_BY_COMBINED_SCORE)
    pages = [_open(RANK_BY_VARIANT_SCORE, page=page, page_size=2) for page in (1, 2)]

    assert [page.ranking_cached for page in pages] == [cached, True]
    assert [_order(page) for page in pages] == [_ids(VARIANT_ORDER[:2]), _ids(VARIANT_ORDER[2:])]
    assert [_ranks(page) for page in pages] == [[1, 2], [3, 4]]


def test_a_panel_served_from_a_broader_ranking_takes_the_order_asked_for(world: _World) -> None:
    world.records = [*world.records, OTHER_HIGH]
    world.panels[BROAD] = ["KNOWN", "NOVEL", "OTHER"]
    world.panels[NARROW] = ["KNOWN", "NOVEL"]
    _open(RANK_BY_COMBINED_SCORE, panel_id=BROAD)

    served = _open(RANK_BY_VARIANT_SCORE, panel_id=NARROW)
    assert served.ranking_cached is True
    assert _order(served) == _ids(VARIANT_ORDER)
    assert _ranks(served) == [1, 2, 3, 4]

    # As a fresh computation of the narrower panel orders and numbers it.
    world.cache.clear()
    fresh = _open(RANK_BY_VARIANT_SCORE, panel_id=NARROW)
    assert fresh.ranking_cached is False
    assert [variant.priority.model_dump() for variant in served.variants if variant.priority] == [
        variant.priority.model_dump() for variant in fresh.variants if variant.priority
    ]


@pytest.mark.parametrize(
    ("rank_by", "key"),
    [(RANK_BY_COMBINED_SCORE, (0.3, 0.6)), (RANK_BY_VARIANT_SCORE, (0.6, 0.3))],
)
def test_the_order_key_takes_the_score_asked_for_first(rank_by: RankBy, key: tuple[float, float]) -> None:
    assert priority_order_key(combined_score=0.3, variant_score=0.6, rank_by=rank_by) == key


# --- structural variants ---------------------------------------------------------------------


def _sv(variant_id: str, sv_type: str, gene: str, *, pli: str | None, start: int) -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr="1",
        start=start,
        end=start + 100_000,
        sv_type=sv_type,
        source="test",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=100_000,
        filters=["PASS"],
        gene_symbols=[gene],
        annotations=[{"pLI": pli}] if pli else [{}],
        calls=[
            StructuralVariantCall(
                sample="PROBAND", gt="0/1", qual=None, read_support=None, filter="PASS", copy_number=None
            )
        ],
    )


# A duplication in the phenotype-matched gene, and a deletion of a LoF-intolerant novel gene.
SV_KNOWN_DUP = _sv("sv-known-dup", "DUP", "KNOWN", pli=None, start=1_000_000)
SV_NOVEL_DEL = _sv("sv-novel-del", "DEL", "NOVEL", pli="0.99", start=5_000_000)


async def _sv_page(monkeypatch: pytest.MonkeyPatch, rank_by: RankBy) -> VariantPage:
    m = "backend.app.services.clickhouse_family_variants."

    async def _fetch(*_a: Any, **_k: Any) -> list[StructuralVariantRecord]:
        return [SV_KNOWN_DUP, SV_NOVEL_DEL]

    async def _nothing(*_a: Any, **_k: Any) -> dict:
        return {}

    async def _no_ids(*_a: Any, **_k: Any) -> set:
        return set()

    async def _seizures(*_a: Any, **_k: Any) -> tuple[list[str], dict[str, str]]:
        return [SEIZURE], {SEIZURE: "Seizure"}

    async def _phenotype_scores(_session: Any, *, gene_symbols: set[str], patient_hpo_ids: list[str]) -> dict:
        return {"KNOWN": GenePhenotypeScore(score=0.9, matched=[{"hpo_id": SEIZURE}])}

    monkeypatch.setattr(m + "_fetch_structural_variant_rows", _fetch)
    monkeypatch.setattr(m + "_structural_record_matches", lambda *a, **k: True)
    monkeypatch.setattr(m + "_affected_present_hpo", _seizures)
    monkeypatch.setattr(m + "score_genes_for_hpo", _phenotype_scores)
    monkeypatch.setattr(m + "list_matching_structural_variant_review_ids", _no_ids)
    monkeypatch.setattr(m + "get_structural_variant_review_map", _nothing)
    monkeypatch.setattr(m + "_fetch_structural_cytoband_map", _nothing)
    return await cfv._prioritized_structural_variants_page(
        None,  # type: ignore[arg-type]
        context=_context(),
        filters=StructuralVariantQueryFilters(page=1, page_size=50),
        page=1,
        page_size=50,
        selected_samples=["PROBAND"],
        rank_by=rank_by,
    )


@pytest.mark.asyncio
async def test_the_sv_ranking_takes_the_order_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    combined = await _sv_page(monkeypatch, RANK_BY_COMBINED_SCORE)
    by_variant = await _sv_page(monkeypatch, RANK_BY_VARIANT_SCORE)

    # The phenotype match carries the duplication first by the combined score; the deletion
    # of the LoF-intolerant novel gene leads by the variant score.
    assert _order(combined) == ["sv-known-dup", "sv-novel-del"]
    assert _order(by_variant) == ["sv-novel-del", "sv-known-dup"]
    assert _scores(by_variant) == _scores(combined)
    assert _ranks(combined) == _ranks(by_variant) == [1, 2]


# --- what the page says of its order -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("rank_by", [RANK_BY_COMBINED_SCORE, RANK_BY_VARIANT_SCORE])
async def test_a_ranked_page_names_the_score_it_is_ranked_by(monkeypatch: pytest.MonkeyPatch, rank_by: RankBy) -> None:
    # The page says which score its order and ranks follow, so the list shows that score
    # beside each rank, as it shows the page it holds and not the search being typed.
    seen: dict[str, Any] = {}

    async def _ranked_small(*_a: Any, rank_by: RankBy, **_k: Any) -> VariantPage:
        seen["small"] = rank_by
        return VariantPage(total=0, variants=[])

    async def _ranked_sv(*_a: Any, rank_by: RankBy, **_k: Any) -> VariantPage:
        seen["sv"] = rank_by
        return VariantPage(total=0, variants=[])

    async def _no_summary(*_a: Any, **_k: Any) -> None:
        return None

    async def _scope(*_a: Any, **_k: Any) -> Any:
        return cfv._SmallVariantScope(
            panel_constraints=PanelFilterConstraints(),
            review_variant_ids=None,
            excluded_review_variant_ids=set(),
            include_review_filter_active=False,
            include_regions=[],
            exclude_regions=[],
            exclude_gene_regions=[],
            exclude_gene_terms=[],
        )

    async def _version(*_a: Any, **_k: Any) -> str:
        return "1:111"

    monkeypatch.setattr(cfv, "_prioritized_small_variants_page", _ranked_small)
    monkeypatch.setattr(cfv, "_prioritized_structural_variants_page", _ranked_sv)
    monkeypatch.setattr(cfv, "_fetch_small_variant_summary", _no_summary)
    monkeypatch.setattr(cfv, "_resolve_small_variant_scope", _scope)
    monkeypatch.setattr(cfv, "_family_small_variant_data_version", _version)
    monkeypatch.setattr(cfv, "_selected_structural_samples", lambda *_a, **_k: ["PROBAND"])

    small = await cfv.get_family_small_variants_page(
        None,  # type: ignore[arg-type]
        context=_context(),
        page=1,
        page_size=50,
        prioritize=True,
        rank_by=rank_by,
    )
    sv = await cfv.get_family_structural_variants_page(
        None,  # type: ignore[arg-type]
        context=_context(),
        page=1,
        page_size=50,
        prioritize=True,
        rank_by=rank_by,
    )

    assert seen == {"small": rank_by, "sv": rank_by}
    assert (small.ranked_by, sv.ranked_by) == (rank_by, rank_by)
