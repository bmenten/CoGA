from __future__ import annotations

import asyncio
import types

from backend.app.services import variant_ranking_cache as vrc
from backend.app.services.family_variant_filters import SmallVariantQueryFilters


def _context():
    return types.SimpleNamespace(
        assembly_name="GRCh38",
        family_uuid="u1",
        affected_sample_names=["S1"],
        sample_rows=[],
        relationship_rows=[],
    )


def _patch_db(monkeypatch):
    async def _ped(session, context):
        return {"structure_hash": "ped-1"}

    async def _panel(session, panel_id):
        return "1:2026-06-08" if panel_id else None

    async def _monarch(session):
        return "2026-06-08"

    monkeypatch.setattr(vrc, "_pedigree_signature", _ped)
    monkeypatch.setattr(vrc, "_panel_version", _panel)
    monkeypatch.setattr(vrc, "_monarch_release", _monarch)


def _default_filters(**overrides):
    base = dict(page=1, page_size=100, panel_id="p1", impact=["HIGH", "MODERATE"])
    base.update(overrides)
    return SmallVariantQueryFilters(**base)


def _hash(monkeypatch, *, filters=None, hpo=(), rev=None, exc=None, active=False):
    _patch_db(monkeypatch)
    return asyncio.run(
        vrc.compute_inputs_hash(
            None,
            context=_context(),
            filters=filters or _default_filters(),
            patient_terms=list(hpo),
            review_variant_ids=rev,
            excluded_review_variant_ids=exc,
            include_review_filter_active=active,
        )
    )


def test_canonical_filters_drops_pagination() -> None:
    cf = vrc.canonical_filters(SmallVariantQueryFilters(page=3, page_size=50, impact=["HIGH"]))
    assert "page" not in cf and "page_size" not in cf
    assert cf["impact"] == ["HIGH"]


def test_hash_is_stable_and_hpo_order_independent(monkeypatch) -> None:
    h1 = _hash(monkeypatch, hpo=["HP:0000001", "HP:0000002"])
    h2 = _hash(monkeypatch, hpo=["HP:0000002", "HP:0000001"])
    assert h1 == h2
    assert len(h1) == 64


def test_hash_changes_when_hpo_changes(monkeypatch) -> None:
    assert _hash(monkeypatch, hpo=["HP:0000001"]) != _hash(
        monkeypatch, hpo=["HP:0000001", "HP:0000002"]
    )


def test_hash_changes_when_filters_change(monkeypatch) -> None:
    assert _hash(monkeypatch, filters=_default_filters(impact=["HIGH"])) != _hash(
        monkeypatch, filters=_default_filters(impact=["HIGH", "MODERATE"])
    )


def test_hash_changes_with_review_filter(monkeypatch) -> None:
    # A review-tag-filtered prioritised query must not collide with the unfiltered view.
    assert _hash(monkeypatch, rev=["1-1-A-G"], active=True) != _hash(
        monkeypatch, rev=None, active=False
    )
    # Excluded variants are part of the family state and change the ranking.
    assert _hash(monkeypatch, exc=["2-2-C-T"]) != _hash(monkeypatch, exc=None)


def _hashes(monkeypatch, *, filters):
    _patch_db(monkeypatch)
    return asyncio.run(
        vrc.compute_ranking_hashes(
            None, context=_context(), filters=filters, patient_terms=[]
        )
    )


def test_base_hash_is_panel_independent(monkeypatch) -> None:
    # Two different panels over the same other inputs share a base_hash (so one can serve
    # the other from a superset) but get distinct exact hashes.
    inputs_a, base_a = _hashes(monkeypatch, filters=_default_filters(panel_id="panel-a"))
    inputs_b, base_b = _hashes(monkeypatch, filters=_default_filters(panel_id="panel-b"))
    assert base_a == base_b
    assert inputs_a != inputs_b


def test_base_hash_changes_with_a_non_panel_filter(monkeypatch) -> None:
    _, base_a = _hashes(monkeypatch, filters=_default_filters(panel_id="p", impact=["HIGH"]))
    _, base_b = _hashes(
        monkeypatch, filters=_default_filters(panel_id="p", impact=["HIGH", "MODERATE"])
    )
    assert base_a != base_b
