"""The prioritised ranking's storage faked at its edge, shared by the ranked-search tests.

``install_ranking_storage`` stands in for what a ranked family search reads and writes besides
the family's variants: the ranking-cache rows (``family_variant_ranking_cache``, in a dict keyed
by the real ``inputs_hash``, each row through the JSON round trip its jsonb columns make), the
superset candidates read without their rankings as ``find_superset_candidates`` reads them,
the other inputs of the cache key (the pedigree, the panel's version, the Monarch and
reference releases), a steady small-variant data version, no gene constraint metrics and no
hydration. ``panel_sql_match`` is the panel part of the family search's SQL filter, and
``sql_keeps`` the parts these tests set; each refuses to stand in for a filter it does not
model, which a test then sets on real ClickHouse (the filter spec).

Import as ``from backend.tests._ranking_cache_fakes import ...``.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import pytest

from backend.app.services import variant_ranking_cache as vrc
from backend.app.services.clickhouse_variant_queries import _split_gene_terms, _variant_overlaps_regions
from backend.app.services.clickhouse_variant_records import PanelFilterConstraints, Region, SmallVariantRecord
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

_SEARCH = "backend.app.services.clickhouse_family_variants."


def panel_sql_match(record: SmallVariantRecord, constraints: PanelFilterConstraints | None) -> bool:
    """The panel part of the SQL filter (``_small_panel_filter_condition``): the variant's gene
    symbols, or its position, whatever the panel's size. The real condition is checked in
    test_clickhouse_family_variants.py (``test_small_panel_filter_keeps_the_regions_of_large_gene_panels``,
    ``test_small_panel_filter_keeps_regions_for_normal_panels``) and, on ClickHouse, in
    integration/test_small_variant_filter_parity_clickhouse.py."""
    if constraints is None or not (constraints.genes or constraints.regions):
        return True
    if constraints.genes and _names_a_gene(record, constraints.genes):
        return True
    return _variant_overlaps_regions(record.chr, record.start, record.end, constraints.regions)


def _names_a_gene(record: SmallVariantRecord, genes: Sequence[str]) -> bool:
    """The variant names one of ``genes``, in any case: as an entry gene symbol, or as an
    annotation's gene or gene id (what the gene index holds)."""
    wanted = {str(gene).casefold() for gene in genes if str(gene).strip()}
    names = {str(symbol).casefold() for symbol in record.gene_symbols}
    for annotation in record.annotations:
        names.update(str(annotation[key]).casefold() for key in ("gene", "gene_id") if annotation.get(key))
    return not wanted.isdisjoint(names)


# The filter fields ``sql_keeps`` stands in for; the scope's own fields (the panel, the
# intervals, the excluded genes) arrive resolved, as its keyword arguments.
_MODELLED_FIELDS = frozenset(
    {"page", "page_size", "inheritance", "expanded_carrier_screening", "gene", "panel_id", "intervals",
     "exclude_intervals", "exclude_gene"}
)


def sql_keeps(
    record: SmallVariantRecord,
    filters: SmallVariantQueryFilters,
    *,
    include_regions: Sequence[Region] = (),
    exclude_regions: Sequence[Region] = (),
    exclude_gene_regions: Sequence[Region] = (),
    panel_constraints: PanelFilterConstraints | None = None,
) -> bool:
    """What the family search's SQL filter keeps, for the fields these tests set: the gene,
    the panel, the intervals and the excluded loci. Any other field set is refused: the real
    SQL is held to the rule on ClickHouse (the filter spec), and a fake must not stand in
    for it."""
    defaults = SmallVariantQueryFilters(page=1, page_size=1)
    unmodelled = [
        field.name
        for field in dataclasses.fields(filters)
        if field.name not in _MODELLED_FIELDS and getattr(filters, field.name) != getattr(defaults, field.name)
    ]
    if unmodelled:
        raise NotImplementedError(f"the fake SQL filter does not model {unmodelled}")
    if filters.gene and not _names_a_gene(record, _split_gene_terms(filters.gene)):
        return False
    span = (record.chr, record.start, record.end)
    if include_regions and not _variant_overlaps_regions(*span, include_regions):
        return False
    if any(_variant_overlaps_regions(*span, regions) for regions in (exclude_regions, exclude_gene_regions) if regions):
        return False
    return panel_sql_match(record, panel_constraints)


def install_ranking_storage(
    monkeypatch: pytest.MonkeyPatch,
    cache: dict[str, dict[str, Any]],
    *,
    panel_version: Callable[[Any, str | None], Awaitable[str | None]],
    reads: list[str] | None = None,
) -> None:
    """Fake the ranking's storage over ``cache`` (inputs_hash -> cache row). ``panel_version``
    answers the panel's part of the key; ``reads``, when given, records the inputs_hash of each
    cache row whose ranking was read, in order."""

    async def pedigree(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"structure_hash": "ped-1"}

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def no_metrics(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def references(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"hpo_release": "2026-04-01", "hpo_imported_at": None, "gene_info_updated_at": None}

    async def data_version(*_args: Any, **_kwargs: Any) -> str:
        return "1:111"

    async def get_cached(_session: Any, *, family_uuid: str, inputs_hash: str) -> dict[str, Any] | None:
        if reads is not None:
            reads.append(inputs_hash)
        return cache.get(inputs_hash)

    async def store(
        _session: Any,
        *,
        family_uuid: str,
        inputs_hash: str,
        total: int,
        total_is_estimated: bool,
        ranking_truncated: bool,
        ranking: list[dict[str, Any]],
        provenance: dict[str, Any] | None = None,
        base_hash: str | None = None,
        panel_id: str | None = None,
    ) -> None:
        cache[inputs_hash] = {
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

    async def supersets(_session: Any, *, family_uuid: str, base_hash: str) -> list[dict[str, Any]]:
        rows = [
            {key: value for key, value in row.items() if key != "ranking"}
            for row in cache.values()
            if row["base_hash"] == base_hash and not row["ranking_truncated"]
        ]
        return sorted(rows, key=lambda row: row["total"], reverse=True)

    monkeypatch.setattr(vrc, "_pedigree_signature", pedigree)
    monkeypatch.setattr(vrc, "_panel_version", panel_version)
    monkeypatch.setattr(vrc, "_monarch_release", nothing)
    monkeypatch.setattr(vrc, "_reference_versions", references)
    monkeypatch.setattr(_SEARCH + "_family_small_variant_data_version", data_version)
    monkeypatch.setattr(_SEARCH + "get_cached_ranking", get_cached)
    monkeypatch.setattr(_SEARCH + "store_ranking", store)
    monkeypatch.setattr(_SEARCH + "find_superset_candidates", supersets)
    monkeypatch.setattr(_SEARCH + "_fetch_gene_constraint_metric_map", no_metrics)
    monkeypatch.setattr(_SEARCH + "_hydrate_small_variant_outs", nothing)
