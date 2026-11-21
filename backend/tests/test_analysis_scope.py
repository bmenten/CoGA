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
    analyses_with,
    analysis_holding,
    analysis_scope,
    review_belongs,
)
from backend.app.services.clickhouse_variant_queries import (
    _family_affected_unaffected_sample_names,
    _small_variant_where_clauses,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_package_records import package_sources
from backend.app.services.family_variant_filters import SmallVariantQueryFilters, StructuralVariantQueryFilters


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
    assert analyses_with(_TWO_WGS, "small_variants") == ["short_read_wgs", "long_read_wgs"]
    assert analyses_with(_TWO_NIPTS, "small_variants") == ["monogenic_nipt"]
    assert analyses_with({}, "small_variants") == []
    # The structural-variant callers: the long-read mtDNA SVs, the conventional NIPT's ccnv.
    assert analyses_with(_TWO_WGS, "structural_variants") == ["long_read_wgs"]
    assert analyses_with(_TWO_NIPTS, "structural_variants") == ["conventional_nipt"]


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
    # Naming a callset names no analysis: the reviews and members the search reads are an
    # analysis's, so it is refused as well.
    with pytest.raises(AnalysisNotNamed):
        _small_variant_where_clauses(
            _context(analysis_scope(_TWO_WGS)), SmallVariantQueryFilters(page=1, page_size=10, source="deepvariant")
        )


def test_a_named_callset_is_read_among_the_analysis_s_own() -> None:
    # The analysis's own callset: that callset alone, the other analysis's left out.
    clauses, params = _small_variant_where_clauses(
        _context(analysis_scope(_TWO_WGS, "long_read_wgs")),
        SmallVariantQueryFilters(page=1, page_size=10, source="deepvariant"),
    )
    assert "lowerUTF8(e.source) = %(source)s" in clauses and params["source"] == "deepvariant"
    assert "e.source NOT IN %(scope_left_out_sources)s" in clauses
    assert params["scope_left_out_sources"] == ("haplotypecaller",)
    # The other analysis's callset: both clauses hold, so the search reads nothing.
    clauses, params = _small_variant_where_clauses(
        _context(analysis_scope(_TWO_WGS, "short_read_wgs")),
        SmallVariantQueryFilters(page=1, page_size=10, source="deepvariant"),
    )
    assert params["source"] == "deepvariant" and "deepvariant" in params["scope_left_out_sources"]
    assert "e.source NOT IN %(scope_left_out_sources)s" in clauses


def test_the_whole_family_s_sample_qc_reads_the_callset_it_names(monkeypatch: pytest.MonkeyPatch) -> None:
    # The QC of a family read for none of its analyses reads the genotypes of the callset it
    # chose, whatever analysis lists it: a genotype read holds no review.
    from backend.app.services import clickhouse_family_variants as cfv

    queries: list[tuple[str, dict[str, Any]]] = []

    async def execute(query: str, params: dict[str, Any]) -> list[Any]:
        queries.append((query, dict(params)))
        return []

    monkeypatch.setattr(cfv, "_execute_clickhouse", execute)
    context = FamilyMetadataContext(
        **{key: value for key, value in vars(_context(analysis_scope(_TWO_WGS))).items()},
        family_id="FAM1",
        assembly_id="assembly-uuid",
    )
    asyncio.run(cfv.fetch_genotype_site_sample(context, scope="autosomes", limit=10, source="haplotypecaller"))
    query, params = queries[0]
    assert params["source"] == "haplotypecaller" and "scope_left_out_sources" not in params
    # The context it was given is unchanged: any other reading of it still names an analysis.
    assert context.scope.unnamed_among == ("short_read_wgs", "long_read_wgs")
    with pytest.raises(AnalysisNotNamed):
        asyncio.run(cfv.fetch_genotype_site_sample(context, scope="autosomes", limit=10, source=None))


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

    filters = StructuralVariantQueryFilters(page=1, page_size=10)
    clauses, params = _structural_variant_scope_clauses(_context(analysis_scope(_TWO_WGS, "short_read_wgs")), filters)
    assert "e.source NOT IN %(scope_left_out_sources)s" in clauses
    assert params["scope_left_out_sources"] == ("mito_sv",)
    # A reading whose analysis was chosen by its small variants (the genome view of a family
    # with a monogenic and a conventional NIPT) keeps every SV: the conventional NIPT's ccnv
    # calls stay on its SV track.
    _clauses, params = _structural_variant_scope_clauses(_context(analysis_scope(_TWO_NIPTS)), filters)
    assert "scope_left_out_sources" not in params


def _pregnancy_record(datasets: list[str], callset: str | None, members: list[str], at: str, pregnancy: str) -> dict[str, Any]:
    return {
        "datasets": {name: {} for name in datasets},
        "sources": package_sources(datasets, callset, pregnancy),
        "members": members,
        "registered_at": at,
    }


# The monogenic and the conventional NIPT of each of two pregnancies: each pregnancy's ccnv calls
# under a source of its own.
_TWO_PREGNANCIES = {
    "analyses": ["conventional_nipt:ZW1", "conventional_nipt:ZW2", "monogenic_nipt:ZW1", "monogenic_nipt:ZW2"],
    "package_imports": {
        "monogenic_nipt:ZW1": _pregnancy_record(["snv"], "nipt_p1", ["CFDNA1", "FATHER1"], "2026-11-01T10:00:00", "ZW1"),
        "conventional_nipt:ZW1": _pregnancy_record(["ccnv"], None, ["CFDNA1"], "2026-11-02T10:00:00", "ZW1"),
        "monogenic_nipt:ZW2": _pregnancy_record(["snv"], "nipt_p2", ["CFDNA2", "FATHER1"], "2026-11-03T10:00:00", "ZW2"),
        "conventional_nipt:ZW2": _pregnancy_record(["ccnv"], None, ["CFDNA2"], "2026-11-04T10:00:00", "ZW2"),
    },
}


def test_the_sv_list_of_a_family_with_several_conventional_nipts_names_one() -> None:
    from backend.app.services.clickhouse_variant_queries import _structural_variant_scope_clauses

    filters = StructuralVariantQueryFilters(page=1, page_size=10)
    sv = "structural_variants"
    assert analyses_with(_TWO_PREGNANCIES, sv) == ["conventional_nipt:ZW1", "conventional_nipt:ZW2"]
    ccnv = {
        key: next(iter(_TWO_PREGNANCIES["package_imports"][key]["sources"][sv]))
        for key in ("conventional_nipt:ZW1", "conventional_nipt:ZW2")
    }
    # Without one, the list, its review summary and a review save are refused.
    unnamed = analysis_scope(_TWO_PREGNANCIES, kind=sv)
    assert unnamed.unnamed_among == ("conventional_nipt:ZW1", "conventional_nipt:ZW2")
    with pytest.raises(AnalysisNotNamed):
        unnamed.require_named()
    # A pregnancy's conventional NIPT reads its own ccnv calls, the other pregnancy's left out.
    first = analysis_scope(_TWO_PREGNANCIES, "conventional_nipt:ZW1", kind=sv)
    clauses, params = _structural_variant_scope_clauses(_context(first), filters)
    assert "e.source NOT IN %(scope_left_out_sources)s" in clauses
    assert params["scope_left_out_sources"] == (ccnv["conventional_nipt:ZW2"],)
    # The family's one SV analysis, chosen by its SVs, is read as if named: nothing else to leave out.
    one = analysis_scope(_TWO_NIPTS, kind=sv)
    assert (one.analysis, one.asked, one.kind) == ("conventional_nipt", False, sv)
    assert one.left_out_sources(sv) == frozenset()
    # A reading that names its analysis leaves out the other analyses' SV sources whatever kind
    # it reads by (a sign-out of a pregnancy's monogenic NIPT).
    assert analysis_scope(_TWO_PREGNANCIES, "monogenic_nipt:ZW1").left_out_sources(sv) == frozenset(ccnv.values())


def test_the_sv_list_and_export_of_several_sv_analyses_name_one_before_anything_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # As the SV review summary and a review save: the list (its count and the genome track's
    # read included) and the export are refused before a panel is audited or a row is read.
    from backend.app.routers import families_structural_variants as structural

    steps: list[str] = []
    unnamed = analysis_scope(_TWO_PREGNANCIES, kind="structural_variants")

    async def context(session: Any, **kwargs: Any) -> Any:
        assert kwargs["variant_kind"] == "structural_variants"
        return _context(unnamed)

    async def step(*_args: Any, **kwargs: Any) -> Any:
        steps.append("ran")

    monkeypatch.setattr(structural, "build_family_metadata_context", context)
    monkeypatch.setattr(structural, "record_family_panel_use", step)
    monkeypatch.setattr(structural, "get_family_structural_variants_clickhouse", step)
    monkeypatch.setattr(structural, "export_family_structural_variants", step)
    filters = StructuralVariantQueryFilters(page=1, page_size=0)
    with pytest.raises(AnalysisNotNamed):
        asyncio.run(
            structural.get_family_structural_variants(
                "FAM1", page=1, page_size=10, filters=filters, project_id=None, overlap=False,
                prioritize=False, track_mode=True, count_only=False, session=object(), user=object(),
            )
        )
    with pytest.raises(AnalysisNotNamed):
        asyncio.run(
            structural.export_family_structural_variants_csv(
                "FAM1", project_id=None, prioritize=False, filters=filters, session=object(), user=object()
            )
        )
    assert steps == []
