"""P2-1a: the aggregated small-variant presence query (track availability).

Exercises the real query construction (via the real ``_small_query_filter_parts``) and the
per-sample presence logic, mocking only the ClickHouse execute. A sample is "present" iff a
matching variant has, for that sample, its own explicit sample-filter (already in the WHERE)
OR a non-ref genotype — replacing the former N per-sample limit=1 probes with one aggregate.
End-to-end behaviour against real ClickHouse is in the integration test of the same name.
"""

from __future__ import annotations

import asyncio


from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services.clickhouse_family_variants import _small_variant_present_sample_names
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters


def _context() -> FamilyMetadataContext:
    name_to_uuid = {"PROBAND": "u-proband", "MOTHER": "u-mother", "FATHER": "u-father"}
    return FamilyMetadataContext(
        family_uuid="fam-uuid",
        family_id="FAM1",
        project_ids=["proj-uuid"],
        sample_rows=[{"sample_id": name} for name in name_to_uuid],
        sample_uuid_to_name={uuid: name for name, uuid in name_to_uuid.items()},
        sample_name_to_uuid=name_to_uuid,
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _filters(**kwargs) -> SmallVariantQueryFilters:
    base = dict(page=1, page_size=1, chromosome="1", sample_filters=[], overlap=False)
    base.update(kwargs)
    return SmallVariantQueryFilters(**base)


def _patch_execute(monkeypatch, rows):
    captured: dict = {}

    async def fake(query, params=None, data=None):
        captured["query"] = query
        captured["params"] = params or {}
        return rows

    monkeypatch.setattr(cfv, "_execute_clickhouse", fake)
    return captured


def test_presence_maps_ids_to_names_and_filters_to_family(monkeypatch):
    cap = _patch_execute(monkeypatch, [("PROBAND",), ("u-mother",), ("stranger",)])
    present = asyncio.run(_small_variant_present_sample_names(_context(), _filters()))
    # PROBAND (by name) + MOTHER (by uuid) map in; "stranger" is not a family sample.
    assert present == {"PROBAND", "MOTHER"}
    # The query is an ARRAY JOIN aggregate over an inner filtered subquery.
    assert "ARRAY JOIN" in cap["query"] and "GROUP BY sid" in cap["query"]
    assert cap["params"]["track_nonref_gts"] == ("0/1", "1/0", "0|1", "1|0", "1/1", "1|1")


def test_no_explicit_sample_filters_uses_nonref_only(monkeypatch):
    cap = _patch_execute(monkeypatch, [])
    asyncio.run(_small_variant_present_sample_names(_context(), _filters(sample_filters=[])))
    # No explicit-filter samples: presence reduces to the non-ref gt test (one query).
    assert "gt IN %(track_nonref_gts)s" in cap["query"]
    assert "PROBAND" in cap["params"]["track_visible_ids"]


def test_explicit_reference_parent_present_iff_base_matches(monkeypatch):
    # MOTHER:0/0 (reference-parent / include_absent): present iff the base query matches at
    # all — even if MOTHER never surfaces in a matching variant's calls (ARRAY JOIN empty).
    queries: list[str] = []

    async def fake(query, params=None, data=None):
        queries.append(query)
        return [(1,)] if "LIMIT 1" in query else []  # ARRAY JOIN empty; base-match has a row

    monkeypatch.setattr(cfv, "_execute_clickhouse", fake)
    present = asyncio.run(
        _small_variant_present_sample_names(_context(), _filters(sample_filters=["MOTHER:0/0"]))
    )
    assert present == {"MOTHER"}  # via the base-match probe, not the ARRAY JOIN
    assert any("LIMIT 1" in q for q in queries)  # the base-match probe ran


def test_explicit_sample_absent_when_base_does_not_match(monkeypatch):
    async def fake(query, params=None, data=None):
        return []  # both the ARRAY JOIN and the base-match probe are empty

    monkeypatch.setattr(cfv, "_execute_clickhouse", fake)
    present = asyncio.run(
        _small_variant_present_sample_names(_context(), _filters(sample_filters=["MOTHER:0/0"]))
    )
    assert present == set()


# --------------------------------------------------------------------------- #
# Structural-variant presence aggregate (P2-1b). Present iff the sample has a call
# in a matching variant — any genotype (no non-ref requirement, unlike small variants).
# --------------------------------------------------------------------------- #
from backend.app.services.clickhouse_family_variants import _structural_present_sample_names
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters


def _sv_filters(**kwargs) -> StructuralVariantQueryFilters:
    base = dict(page=1, page_size=1, chromosome="1")
    base.update(kwargs)
    return StructuralVariantQueryFilters(**base)


def test_structural_presence_maps_ids_to_names(monkeypatch):
    cap = _patch_execute(monkeypatch, [("PROBAND",), ("u-father",), ("ghost",)])
    present = asyncio.run(_structural_present_sample_names(_context(), _sv_filters()))
    assert present == {"PROBAND", "FATHER"}  # ghost is not a family sample
    assert "ARRAY JOIN sample_ids AS sid" in cap["query"] and "GROUP BY sid" in cap["query"]
    # The per-variant collapse (CollapsingMergeTree sign) must be preserved.
    assert "GROUP BY e.key, e.variantId" in cap["query"] and "any(e.calls.sampleId)" in cap["query"]
    assert "PROBAND" in cap["params"]["track_visible_ids"]


def test_structural_presence_empty_context_short_circuits(monkeypatch):
    called = {"n": 0}

    async def fake(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(cfv, "_execute_clickhouse", fake)
    no_assembly = _context()
    no_assembly.assembly_name = None
    assert asyncio.run(_structural_present_sample_names(no_assembly, _sv_filters())) == set()
    assert called["n"] == 0


def test_empty_context_short_circuits(monkeypatch):
    called = {"n": 0}

    async def fake(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(cfv, "_execute_clickhouse", fake)
    no_assembly = _context()
    no_assembly.assembly_name = None
    assert asyncio.run(_small_variant_present_sample_names(no_assembly, _filters())) == set()
    no_samples = _context()
    no_samples.sample_name_to_uuid = {}
    assert asyncio.run(_small_variant_present_sample_names(no_samples, _filters())) == set()
    assert called["n"] == 0  # never hits ClickHouse
