"""The background warm of the ranking cache ranks a view as a live request does (ENG-13).

After an HPO or pedigree edit, ``precompute_family_ranking_safe`` replays the family's most
recent prioritised query, so that the next open of that view is a cache hit. It replayed
the filters stored with that ranking as they were: without the intervals, excluded intervals
and excluded gene they name (a live request resolves those into the regions it ranks within
and the key it stores under), and with the SV hit genes the stored ranking had resolved,
not the family's genes now (#845). So it computed a ranking no live request asked for, and
stored it under a key none would read, in one of the family's few cache rows.

The live request's own steps are faked at their edge: the stored query, the family's SV
index and the gene reference. What the warm hands the ranking is what is checked.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services.clickhouse_variant_records import Region
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters
from backend.app.services.variant_ranking_cache import canonical_filters

M = "backend.app.services.clickhouse_family_variants."

# The gene the query leaves out, where the gene reference places it.
_EXCLUDED_GENE_LOCUS = Region(chr="7", start=5_000, end=6_000)


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="F1",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_uuid": "uuid-S1", "sample_id": "S1", "role": "proband", "affected": True}],
        sample_uuid_to_name={"uuid-S1": "S1"},
        sample_name_to_uuid={"S1": "uuid-S1"},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


class _Session:
    """The warm's own session: its one read is the family's most recent cached query."""

    def __init__(self, stored_filters: dict[str, Any]) -> None:
        self.stored_filters = stored_filters

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, statement: Any, params: Any = None) -> Any:
        assert "FROM family_variant_ranking_cache" in str(statement), str(statement)
        row = {"provenance": {"filters": self.stored_filters}}
        return types.SimpleNamespace(mappings=lambda: types.SimpleNamespace(first=lambda: row))


@pytest.fixture
def warm(monkeypatch: pytest.MonkeyPatch):
    """``warm(stored_filters)``: run the warm over a family whose most recent cached query
    has ``stored_filters``; returns what it handed the ranking."""
    from backend.app.core import postgres
    from backend.app.services import family_metadata_context

    ranked: dict[str, Any] = {}

    async def rank(_session: Any, **kwargs: Any) -> None:
        ranked.update(kwargs)

    async def context(*_args: Any, **_kwargs: Any) -> FamilyMetadataContext:
        return _context()

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def sv_hit_genes_now(_session: Any, *, family_uuid: str) -> list[str]:
        return ["GENENOW"]

    async def gene_regions(_session: Any, *, gene_query: str, assembly_id: str | None) -> list[Region]:
        assert gene_query == "GENEX"
        return [_EXCLUDED_GENE_LOCUS]

    monkeypatch.setattr(M + "_prioritized_small_variants_page", rank)
    monkeypatch.setattr(M + "_ensure_family_sv_gene_index", nothing)
    monkeypatch.setattr(M + "get_sv_hit_genes", sv_hit_genes_now)
    monkeypatch.setattr(M + "_fetch_gene_regions", gene_regions)
    monkeypatch.setattr(postgres, "get_postgres_engine", lambda: None)
    monkeypatch.setattr(family_metadata_context, "build_family_metadata_context", context)

    async def run(stored_filters: dict[str, Any]) -> dict[str, Any]:
        monkeypatch.setattr(postgres, "get_postgres_sessionmaker", lambda: lambda: _Session(stored_filters))
        ranked.clear()
        await cfv.precompute_family_ranking_safe("F1", user=None)
        return dict(ranked)

    return run


@pytest.mark.asyncio
async def test_the_warm_ranks_within_the_query_s_intervals_and_without_its_excluded_gene(warm) -> None:
    stored = canonical_filters(
        SmallVariantQueryFilters(
            page=1,
            page_size=100,
            intervals="chr1:1000-2000",
            exclude_intervals="chr2:3000-4000",
            exclude_gene="GENEX",
        )
    )

    ranked = await warm(stored)

    # Before: none of them, the ranking stored under a key no live request reads.
    assert ranked["include_regions"] == cfv._parse_interval_regions("chr1:1000-2000")
    assert ranked["exclude_regions"] == cfv._parse_interval_regions("chr2:3000-4000", label="Excluded interval")
    assert ranked["exclude_gene_regions"] == [_EXCLUDED_GENE_LOCUS]
    assert ranked["exclude_gene_terms"] == ["GENEX"]
    # The no-review default view, as before.
    assert (ranked["review_variant_ids"], ranked["include_review_filter_active"]) == (None, False)


@pytest.mark.asyncio
async def test_the_warm_resolves_the_sv_hit_genes_again(warm) -> None:
    stored = canonical_filters(
        SmallVariantQueryFilters(page=1, page_size=100, require_sv_second_hit=True, sv_hit_genes=["GENEOLD"])
    )

    ranked = await warm(stored)

    # Before: the stored ranking's genes, replayed (#845).
    assert ranked["filters"].sv_hit_genes == ["GENENOW"]

    # A query that asks for no second hit takes none, whatever was stored.
    stored = canonical_filters(SmallVariantQueryFilters(page=1, page_size=100, sv_hit_genes=["GENEOLD"]))
    assert (await warm(stored))["filters"].sv_hit_genes == []
