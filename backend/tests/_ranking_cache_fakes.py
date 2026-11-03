"""The prioritised ranking's storage faked at its edge, shared by the ranked-search tests.

``install_ranking_storage`` stands in for what a ranked family search reads and writes besides
the family's variants: the ranking-cache rows (``family_variant_ranking_cache``, in a dict keyed
by the real ``inputs_hash``, each row through the JSON round trip its jsonb columns make), the
superset candidates read without their rankings as ``find_superset_candidates`` reads them
(ENG-13), the other inputs of the cache key (the pedigree, the panel's version, the Monarch and
reference releases), a steady small-variant data version, no gene constraint metrics and no
hydration. ``panel_sql_match`` is the panel part of the family search's SQL filter.

Import as ``from backend.tests._ranking_cache_fakes import ...``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from backend.app.services import variant_ranking_cache as vrc
from backend.app.services.clickhouse_variant_queries import (
    _small_record_hits_gene_terms,
    _variant_overlaps_regions,
)
from backend.app.services.clickhouse_variant_records import PanelFilterConstraints, SmallVariantRecord

_SEARCH = "backend.app.services.clickhouse_family_variants."


def panel_sql_match(record: SmallVariantRecord, constraints: PanelFilterConstraints | None) -> bool:
    """The panel part of the SQL filter (``_small_panel_filter_condition``): the variant's gene
    symbols, or its position, whatever the panel's size. The real condition is checked in
    test_clickhouse_family_variants.py (``test_small_panel_filter_keeps_the_regions_of_large_gene_panels``,
    ``test_small_panel_filter_keeps_regions_for_normal_panels``) and, on ClickHouse, in
    integration/test_small_variant_filter_parity_clickhouse.py."""
    if constraints is None or not (constraints.genes or constraints.regions):
        return True
    if constraints.genes and _small_record_hits_gene_terms(record, constraints.genes):
        return True
    return _variant_overlaps_regions(record.chr, record.start, record.end, constraints.regions)


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
