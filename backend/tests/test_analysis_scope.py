"""Which of a family's analyses a reading or a review of its variants is about
(``analysis_scope``), and how the small-variant readers apply it: short-read and then long-read
WGS of the same DNA, each with a callset of its own, are read one analysis at a time (the
owner's decisions of 2026-11-20). All values are synthetic."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import sample_integrity_service
from backend.app.services.analysis_scope import (
    NO_ANALYSIS,
    WHOLE_FAMILY_SCOPE,
    AnalysisNotNamed,
    analyses_with_small_variants,
    analysis_holding,
    analysis_scope,
    review_belongs,
)
from backend.app.services.clickhouse_variant_queries import (
    _family_affected_unaffected_sample_names,
    _small_variant_where_clauses,
)
from backend.app.services.family_package_records import package_sources
from backend.app.services.family_variant_filters import SmallVariantQueryFilters


def _record(datasets: list[str], callset: str | None, members: list[str], at: str) -> dict[str, Any]:
    return {
        "datasets": {name: {} for name in datasets},
        "sources": package_sources(datasets, callset),
        "members": members,
        "registered_at": at,
    }


# Short-read WGS of a trio, then long-read WGS of the proband's second sample (linked as the same
# individual) with the mtDNA calls.
_TWO_WGS = {
    "analyses": ["long_read_wgs", "short_read_wgs"],
    "package_imports": {
        "short_read_wgs": _record(["snv"], "haplotypecaller", ["PROBAND", "MOTHER", "FATHER1"], "2026-11-01T10:00:00"),
        "long_read_wgs": _record(["snv", "mito"], "deepvariant", ["PROBAND2"], "2026-11-02T10:00:00"),
    },
}
# A monogenic and a conventional NIPT of one pregnancy: only the monogenic NIPT holds small variants.
_TWO_NIPTS = {
    "analyses": ["conventional_nipt", "monogenic_nipt"],
    "package_imports": {
        "monogenic_nipt": _record(["snv"], "nipt", ["CFDNA1", "FATHER1", "FETUS1"], "2026-11-01T10:00:00"),
        "conventional_nipt": _record(["ccnv"], None, ["CFDNA1"], "2026-11-02T10:00:00"),
    },
}


def test_the_analyses_with_small_variants_are_read_in_the_order_they_were_imported() -> None:
    assert analyses_with_small_variants(_TWO_WGS) == ["short_read_wgs", "long_read_wgs"]
    assert analyses_with_small_variants(_TWO_NIPTS) == ["monogenic_nipt"]
    assert analyses_with_small_variants({}) == []


def test_a_family_with_two_analyses_with_small_variants_names_the_one_read() -> None:
    unnamed = analysis_scope(_TWO_WGS)
    assert unnamed.unnamed_among == ("short_read_wgs", "long_read_wgs")
    with pytest.raises(AnalysisNotNamed, match="name the analysis"):
        unnamed.require_named()
    long_read = analysis_scope(_TWO_WGS, "long_read_wgs")
    assert long_read.require_named() is long_read
    assert long_read.review_analysis == "long_read_wgs"
    # The other analysis's callset is left out, and the members only its package lists.
    assert long_read.left_out_sources("small_variants") == frozenset({"haplotypecaller"})
    assert long_read.left_out_members == frozenset({"PROBAND", "MOTHER", "FATHER1"})
    short_read = analysis_scope(_TWO_WGS, "short_read_wgs")
    assert short_read.left_out_sources("small_variants") == frozenset({"deepvariant", "mito"})
    assert short_read.left_out_sources("structural_variants") == frozenset({"mito_sv"})
    assert short_read.left_out_members == frozenset({"PROBAND2"})


def test_a_family_whose_one_analysis_holds_small_variants_is_read_as_it() -> None:
    scope = analysis_scope(_TWO_NIPTS)
    assert (scope.analysis, scope.unnamed_among) == ("monogenic_nipt", ())
    assert scope.left_out_sources("small_variants") == frozenset()
    assert scope.left_out_members == frozenset()
    # A family of one analysis, and one of none.
    one = {"analyses": ["pgt"], "package_imports": {"pgt": _record(["haplotypes"], None, ["S1"], "t")}}
    assert analysis_scope(one).analysis == "pgt"
    assert analysis_scope({}) == WHOLE_FAMILY_SCOPE
    assert WHOLE_FAMILY_SCOPE.review_analysis == NO_ANALYSIS


def test_an_analysis_the_family_does_not_hold_is_refused() -> None:
    with pytest.raises(AnalysisNotNamed, match="no such analysis"):
        analysis_scope(_TWO_NIPTS, "long_read_wgs")


def test_the_mtdna_page_reads_the_analysis_that_holds_the_mtdna_calls() -> None:
    assert analysis_holding(_TWO_WGS, "small_variants", "mito") == "long_read_wgs"
    assert analysis_holding(_TWO_NIPTS, "small_variants", "mito") is None
    scope = analysis_scope(_TWO_WGS, holding=("small_variants", "mito"))
    assert scope.analysis == "long_read_wgs" and scope.unnamed_among == ()
    # Without an analysis that holds it, a reading still names one.
    assert analysis_scope(_TWO_WGS, holding=("small_variants", "glimpse2")).unnamed_among


def test_a_review_is_its_analysis_s_or_one_made_under_none_the_family_holds() -> None:
    held = frozenset({"short_read_wgs", "long_read_wgs"})
    assert review_belongs("long_read_wgs", held, "long_read_wgs")
    assert not review_belongs("long_read_wgs", held, "short_read_wgs")
    assert review_belongs("long_read_wgs", held, NO_ANALYSIS)
    assert review_belongs(None, held, "short_read_wgs")


def _context(scope: Any) -> SimpleNamespace:
    return SimpleNamespace(
        family_uuid="family-uuid",
        project_ids=[],
        assembly_name="GRCh38",
        sample_name_to_uuid={"PROBAND": "u1", "MOTHER": "u2", "PROBAND2": "u3"},
        sample_uuid_to_name={"u1": "PROBAND", "u2": "MOTHER", "u3": "PROBAND2"},
        affected_sample_names=["PROBAND", "PROBAND2"],
        sample_rows=[
            {"sample_id": "PROBAND", "clinical_status": "affected", "sex": "male"},
            {"sample_id": "MOTHER", "clinical_status": "unaffected", "sex": "female"},
            {"sample_id": "PROBAND2", "clinical_status": "affected", "sex": "male"},
        ],
        relationship_rows=[],
        scope=scope,
    )


def test_the_search_reads_the_analysis_s_callsets_alone() -> None:
    filters = SmallVariantQueryFilters(page=1, page_size=10)
    clauses, params = _small_variant_where_clauses(_context(analysis_scope(_TWO_WGS, "short_read_wgs")), filters)
    assert "e.source NOT IN %(scope_left_out_sources)s" in clauses
    assert params["scope_left_out_sources"] == ("deepvariant", "mito")
    # A family of one analysis leaves nothing out.
    clauses, params = _small_variant_where_clauses(_context(analysis_scope(_TWO_NIPTS)), filters)
    assert "scope_left_out_sources" not in params


def test_a_search_of_a_family_with_two_analyses_that_names_none_is_refused() -> None:
    with pytest.raises(AnalysisNotNamed):
        _small_variant_where_clauses(_context(analysis_scope(_TWO_WGS)), SmallVariantQueryFilters(page=1, page_size=10))
    # A reading of one named callset is one callset's, whatever the family holds.
    clauses, _params = _small_variant_where_clauses(
        _context(analysis_scope(_TWO_WGS)), SmallVariantQueryFilters(page=1, page_size=10, source="deepvariant")
    )
    assert "lowerUTF8(e.source) = %(source)s" in clauses


def test_an_inheritance_mode_reads_the_members_of_the_analysis() -> None:
    # The long-read analysis reads its proband's second sample; the short-read one its trio.
    affected, unaffected = _family_affected_unaffected_sample_names(_context(analysis_scope(_TWO_WGS, "long_read_wgs")))
    assert (affected, unaffected) == (["PROBAND2"], [])
    affected, unaffected = _family_affected_unaffected_sample_names(_context(analysis_scope(_TWO_WGS, "short_read_wgs")))
    assert (affected, unaffected) == (["PROBAND"], ["MOTHER"])


def test_the_sample_qc_reads_the_callset_that_covers_most_of_its_members(monkeypatch: pytest.MonkeyPatch) -> None:
    # A short-read trio callset and a long-read callset of the proband alone: the trio's is read,
    # though the known callers' order puts DeepVariant first.
    called = {"haplotypecaller": ["PROBAND", "MOTHER", "FATHER1"], "deepvariant": ["PROBAND"]}

    async def sites(context: Any, *, scope: str, limit: int, source: str | None) -> list[Any]:
        assert scope == "autosomes" and source is not None
        return [("1", 100, "A", "G", called[source], ["0/1"] * len(called[source]))]

    monkeypatch.setattr(sample_integrity_service, "fetch_genotype_site_sample", sites)
    choose = sample_integrity_service._choose_covering_source
    trio = ["FATHER1", "MOTHER", "PROBAND"]
    assert asyncio.run(choose(None, ["deepvariant", "haplotypecaller"], trio)) == "haplotypecaller"
    # Of one callset per kind nothing is probed; direct calls before imputed genotypes.
    assert asyncio.run(choose(None, ["glimpse2", "deepvariant"], trio)) == "deepvariant"
    assert asyncio.run(choose(None, [], trio)) is None


def test_the_sv_list_of_a_named_analysis_leaves_out_the_other_s_sv_sources() -> None:
    from backend.app.services.clickhouse_variant_queries import _structural_variant_scope_clauses
    from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

    filters = StructuralVariantQueryFilters(page=1, page_size=10)
    clauses, params = _structural_variant_scope_clauses(_context(analysis_scope(_TWO_WGS, "short_read_wgs")), filters)
    assert "e.source NOT IN %(scope_left_out_sv_sources)s" in clauses
    assert params["scope_left_out_sv_sources"] == ("mito_sv",)
    # A family read without naming an analysis (a monogenic and a conventional NIPT) keeps every
    # SV: the conventional NIPT's ccnv calls stay on its SV list.
    _clauses, params = _structural_variant_scope_clauses(_context(analysis_scope(_TWO_NIPTS)), filters)
    assert "scope_left_out_sv_sources" not in params
