from __future__ import annotations

import asyncio
import types

from backend.app.services import variant_ranking_cache as vrc
from backend.app.services.clickhouse_variant_records import PanelFilterConstraints, Region
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
    """The exact cache key (``inputs_hash``)."""
    _patch_db(monkeypatch)
    inputs_hash, _base_hash = asyncio.run(
        vrc.compute_ranking_hashes(
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
    return inputs_hash


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


def _hashes(monkeypatch, *, filters, data="1:1", **scope):
    _patch_db(monkeypatch)
    return asyncio.run(
        vrc.compute_ranking_hashes(
            None,
            context=_context(),
            filters=filters,
            patient_terms=[],
            variant_data_version=data,
            **scope,
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


def _members(genes=(), regions=()):
    return PanelFilterConstraints(
        genes=tuple(genes),
        regions=tuple(Region(chr=chrom, start=start, end=end) for chrom, start, end in regions),
    )


def test_the_panel_members_digest_ignores_order_and_is_none_without_a_panel() -> None:
    # The gene loci come from the gene reference in no fixed order; the same members read
    # in another order, or twice, are the same panel.
    digest = vrc.panel_members_digest(_members(["GENE1", "GENE2"], [("1", 10, 20), ("2", 5, 9)]))
    assert digest == vrc.panel_members_digest(
        _members(["GENE2", "GENE1", "GENE1"], [("2", 5, 9), ("1", 10, 20), ("1", 10, 20)])
    )
    assert digest is not None and len(digest) == 64
    assert vrc.panel_members_digest(PanelFilterConstraints()) is None
    assert vrc.panel_members_digest(None) is None


def test_the_panel_members_digest_moves_with_a_gene_or_a_region() -> None:
    digest = vrc.panel_members_digest(_members(["GENE1"], [("1", 10, 20)]))
    assert vrc.panel_members_digest(_members(["GENE1", "GENE2"], [("1", 10, 20)])) != digest
    assert vrc.panel_members_digest(_members(["GENE1"], [("1", 10, 30)])) != digest
    assert vrc.panel_members_digest(_members(["GENE1"], [("1", 10, 20), ("1", 50, 60)])) != digest


def test_the_exact_key_covers_the_panel_members_and_the_base_key_leaves_them_out(monkeypatch) -> None:
    # One panel version, other members: a gene-reference reload moved one of its genes, or
    # an edit landed between the read of its genes and the read of its version.
    inputs_a, base_a = _hashes(monkeypatch, filters=_default_filters(), panel_members="members-a")
    inputs_b, base_b = _hashes(monkeypatch, filters=_default_filters(), panel_members="members-b")
    assert inputs_a != inputs_b
    # The base key leaves the panel out, so a broader ranking can serve a narrower panel.
    assert base_a == base_b


def test_both_keys_cover_the_regions_and_genes_the_query_applied(monkeypatch) -> None:
    # A gene filter's loci are read from the gene reference when the query runs: a reload
    # that moves the gene changes them, and the filter's text stays as it was.
    gene_filter = _default_filters(gene="GENE1")
    before = _hashes(monkeypatch, filters=gene_filter, include_regions=[Region(chr="1", start=1000, end=2000)])
    moved = _hashes(monkeypatch, filters=gene_filter, include_regions=[Region(chr="1", start=1000, end=3000)])
    assert before[0] != moved[0] and before[1] != moved[1]
    # The same regions read in another order are the same keys.
    two = [Region(chr="1", start=1000, end=2000), Region(chr="1", start=5000, end=6000)]
    assert _hashes(monkeypatch, filters=gene_filter, include_regions=two) == _hashes(
        monkeypatch, filters=gene_filter, include_regions=list(reversed(two))
    )

    # What the query left out counts too: a ranking computed without the excluded gene's
    # loci or name applied is not one with them.
    excluded = _default_filters(exclude_gene="GENE2")
    applied = _hashes(
        monkeypatch,
        filters=excluded,
        exclude_gene_regions=[Region(chr="1", start=3000, end=4000)],
        exclude_gene_terms=["GENE2"],
    )
    for other in (
        _hashes(monkeypatch, filters=excluded, exclude_gene_terms=["GENE2"]),
        _hashes(monkeypatch, filters=excluded, exclude_gene_regions=[Region(chr="1", start=3000, end=4000)]),
        _hashes(
            monkeypatch,
            filters=excluded,
            exclude_regions=[Region(chr="1", start=3000, end=4000)],
            exclude_gene_terms=["GENE2"],
        ),
    ):
        assert other[0] != applied[0] and other[1] != applied[1]


def test_both_keys_cover_the_inheritance_mode_and_carrier_screening(monkeypatch) -> None:
    # CLIN-44: what a ranked search keeps depends on its inheritance mode (pairing) and on
    # expanded carrier screening (the couple's genes), so no ranking of one search serves
    # another, exactly or narrowed to a sub-panel.
    searches = [
        {},
        {"inheritance": "compound_het"},
        {"inheritance": "recessive"},
        {"inheritance": "recessive_homozygous"},
        {"inheritance": "de_novo_dominant"},
        {"inheritance": "x_linked"},
        {"expanded_carrier_screening": True},
        {"expanded_carrier_screening": True, "inheritance": "compound_het"},
    ]
    keys = [_hashes(monkeypatch, filters=_default_filters(**search)) for search in searches]
    assert len({inputs_hash for inputs_hash, _base in keys}) == len(searches)
    assert len({base_hash for _inputs, base_hash in keys}) == len(searches)


class _NoStructureVersionSession:
    """A family without a recorded structure version: the pedigree is read from the context."""

    async def execute(self, statement, params=None):
        assert "family_structure_versions" in str(statement), str(statement)
        return types.SimpleNamespace(scalar=lambda: None)


def test_the_pedigree_read_from_the_context_covers_each_members_sex() -> None:
    # The structure hash covers each member's sex; the fallback did not. The X-linked rules
    # read it: the segregation of a son's hemizygous call, and which chrX variants a carrier
    # screen keeps (a female partner's on their own, REQ-CARR-002).
    def context(sex: str):
        return types.SimpleNamespace(
            family_uuid="u1",
            affected_sample_names=[],
            sample_rows=[
                {"sample_id": "PARTNER1", "role": "mother", "sex": sex},
                {"sample_id": "PARTNER2", "role": "father", "sex": "male"},
            ],
            relationship_rows=[],
        )

    session = _NoStructureVersionSession()
    female = asyncio.run(vrc._pedigree_signature(session, context("female")))
    unknown = asyncio.run(vrc._pedigree_signature(session, context("")))
    assert female != unknown
    assert asyncio.run(vrc._pedigree_signature(session, context("female"))) == female


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return types.SimpleNamespace(mappings=lambda: types.SimpleNamespace(all=lambda: []))


def test_superset_candidates_carry_what_their_panel_held() -> None:
    # The superset path compares a row's record of its panel with the panel as it is now.
    session = _RecordingSession()
    assert asyncio.run(vrc.find_superset_candidates(session, family_uuid="f", base_hash="b")) == []
    assert "provenance" in session.statements[0]
