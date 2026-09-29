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

    async def _refs(session, context):
        return dict(REFERENCE_VERSIONS)

    monkeypatch.setattr(vrc, "_pedigree_signature", _ped)
    monkeypatch.setattr(vrc, "_panel_version", _panel)
    monkeypatch.setattr(vrc, "_monarch_release", _monarch)
    monkeypatch.setattr(vrc, "_reference_versions", _refs)


REFERENCE_VERSIONS = {
    "hpo_release": "2026-04-01",
    "hpo_imported_at": "2026-04-02T08:00:00+00:00",
    "gene_info_updated_at": "2026-06-08T10:00:00",
}


def _default_filters(**overrides):
    base = dict(page=1, page_size=100, panel_id="p1", impact=["HIGH", "MODERATE"])
    base.update(overrides)
    return SmallVariantQueryFilters(**base)


def _hash(monkeypatch, *, filters=None, hpo=(), rev=None, exc=None, active=False, data="1:1"):
    _patch_db(monkeypatch)
    return asyncio.run(
        vrc.compute_inputs_hash(
            None,
            context=_context(),
            filters=filters or _default_filters(),
            patient_terms=list(hpo),
            variant_data_version=data,
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


def test_hash_changes_when_the_family_variant_data_changes(monkeypatch) -> None:
    # #509: an insert/delete/re-import moves the storage-level data version, whichever
    # code path made it — the ranking over the old variants must not be served.
    assert _hash(monkeypatch, data="1:111") != _hash(monkeypatch, data="2:222")
    assert _hash(monkeypatch, data="0:0") != _hash(monkeypatch, data="1:111")


def test_hash_changes_when_scoring_reference_data_changes(monkeypatch) -> None:
    before = _hash(monkeypatch)
    monkeypatch.setitem(REFERENCE_VERSIONS, "hpo_release", "2026-09-01")
    after_hpo = _hash(monkeypatch)
    monkeypatch.setitem(REFERENCE_VERSIONS, "gene_info_updated_at", "2026-09-28T09:00:00")
    after_gene_info = _hash(monkeypatch)
    assert len({before, after_hpo, after_gene_info}) == 3


def test_hash_changes_when_an_ontology_without_a_release_is_imported_again(monkeypatch) -> None:
    # Two imports from files without a release both leave the release unknown. The
    # rankings computed on the first must not be served on the second.
    monkeypatch.setitem(REFERENCE_VERSIONS, "hpo_release", None)
    first = _hash(monkeypatch)
    monkeypatch.setitem(REFERENCE_VERSIONS, "hpo_imported_at", "2026-10-01T08:00:00+00:00")
    assert _hash(monkeypatch) != first


def test_a_ranking_cached_under_the_earlier_key_is_not_a_hit(monkeypatch) -> None:
    # The key gained the ontology's import time. A ranking cached under the earlier payload,
    # even with the same HPO release string, may have been computed on another ontology, so
    # it must miss. The payload change retires those rows; _ALGORITHM_VERSION is left for
    # scoring changes, and a bump of it combines with this (either change alone moves the key).
    current = _hash(monkeypatch)
    monkeypatch.delitem(REFERENCE_VERSIONS, "hpo_imported_at")
    assert _hash(monkeypatch) != current


class _GeneInfoSession:
    """Answers the gene-constraint freshness lookup."""

    def __init__(self) -> None:
        self.params: list = []

    async def execute(self, statement, params=None):
        assert "FROM gene_info" in str(statement), str(statement)
        self.params.append(params)
        return types.SimpleNamespace(scalar=lambda: "2026-06-08T10:00:00")


def test_the_reference_versions_name_the_loaded_hpo_ontology(monkeypatch) -> None:
    # The same reader as the signed record and the admin summary: the release of the
    # latest import, and when it was imported. `max(release_version)` named a release no
    # longer loaded after a downgrade, and could not tell apart two imports without one.
    imported_at = "2026-09-01T08:00:00+00:00"

    async def _loaded(session):
        return {"release_version": "hp/releases/2026-06-06", "release_date": None, "imported_at": imported_at}

    monkeypatch.setattr(vrc, "get_loaded_hpo_release", _loaded)
    session = _GeneInfoSession()
    context = types.SimpleNamespace(assembly_id="a1")
    assert asyncio.run(vrc._reference_versions(session, context)) == {
        "hpo_release": "hp/releases/2026-06-06",
        "hpo_imported_at": imported_at,
        "gene_info_updated_at": "2026-06-08T10:00:00",
    }
    assert session.params == [{"assembly_id": "a1"}]

    async def _none_loaded(session):
        return None

    monkeypatch.setattr(vrc, "get_loaded_hpo_release", _none_loaded)
    assert asyncio.run(vrc._reference_versions(_GeneInfoSession(), context)) == {
        "hpo_release": None,
        "hpo_imported_at": None,
        "gene_info_updated_at": "2026-06-08T10:00:00",
    }


def _hashes(monkeypatch, *, filters, data="1:1"):
    _patch_db(monkeypatch)
    return asyncio.run(
        vrc.compute_ranking_hashes(
            None,
            context=_context(),
            filters=filters,
            patient_terms=[],
            variant_data_version=data,
        )
    )


def test_base_hash_covers_the_variant_data_version(monkeypatch) -> None:
    # The superset (base_hash) path must not serve a sub-panel from a ranking computed
    # over different variant data either.
    _, base_old = _hashes(monkeypatch, filters=_default_filters(), data="1:111")
    _, base_new = _hashes(monkeypatch, filters=_default_filters(), data="2:222")
    assert base_old != base_new


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
