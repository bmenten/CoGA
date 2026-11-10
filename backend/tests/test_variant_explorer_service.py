"""The Variant Explorer's search across every family of the caller's projects
(``variant_explorer_service``).

Its helpers (the variant type, the most severe class, the term lists, the table names), the
WHERE it builds over the entries (each filter it shares with the family search the family
search's own clause, read on each row's own annotation: the location, type, callset, gene,
rsID, excluded ClinVar and the per-transcript filters in one subquery, CLIN-29; the ClinVar
rescue of the frequency ceilings, #526; imputed calls left out by default; per-sample genotype
classes, #511, read by variant), the count cap, the cursor and its fingerprint, the review-tag filter's
cap (DATA-2), and the gene panel read as the family page reads it (CLIN-48). A variant's
carriers (the row limit and its truncation flag) and the annotation a row shows (one
transcript's consequence and HGVS, CLIN-50).
"""

from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest
from clickhouse_connect.driver.binding import bind_query
from fastapi import HTTPException

from backend.app.services.clickhouse_variant_queries import (
    _small_detail_filter_clauses,
    _small_native_sample_filter_clauses,
    _small_panel_filter_condition,
    _small_query_filter_parts,
    _small_region_filter_condition,
)
from backend.app.services.clickhouse_variant_records import PanelFilterConstraints, Region
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters
from backend.app.services import variant_explorer_service as ves
from backend.app.services.variant_explorer_service import (
    _classification_rank,
    _CLINVAR_RANK,
    _IMPACT_RANK,
    ExplorerScope,
    GlobalVariantFilters,
    _classify_type,
    _entries_where,
    _first_non_empty,
    _most_severe,
    _small_table_name,
)


def test_classify_type() -> None:
    assert _classify_type("A", "G") == "SNV"
    assert _classify_type("AC", "GT") == "MNV"
    assert _classify_type("A", "AG") == "INDEL"
    assert _classify_type("ACG", "A") == "INDEL"


def test_most_severe_classification_and_impact() -> None:
    assert _most_severe(["acmg_class_2", "acmg_class_5", "acmg_class_3"], _classification_rank) == "acmg_class_5"
    assert _most_severe(["MODIFIER", "HIGH", "LOW"], _IMPACT_RANK) == "HIGH"
    assert _most_severe(["likely_benign", "pathogenic"], _CLINVAR_RANK) == "pathogenic"
    assert _most_severe([], _IMPACT_RANK) is None


def test_first_non_empty() -> None:
    assert _first_non_empty(["", "  ", "BRCA1"]) == "BRCA1"
    assert _first_non_empty([]) is None


def test_small_table_name_normalizes_assembly() -> None:
    name = _small_table_name("GRCh38", "entries")
    assert name.endswith("`GRCh38/SNV_INDEL/entries`")
    # A messy name is normalized to a ClickHouse-safe dataset key (shared with
    # ingestion), not rejected — so e.g. T2T can be queried.
    normalized = _small_table_name("bad assembly!", "entries")
    assert normalized.endswith("`bad_assembly_/SNV_INDEL/entries`")


def test_the_explorer_reads_excluded_clinvar_statuses_and_the_rsid_as_the_family_search_does() -> None:
    # An API value with an underscore excluded nothing: the stored terms read
    # "likely benign" (CLIN-50). The rsID is trimmed, as the family search trims it.
    _clauses, params = _explorer_where(exclude_clinvar=["Likely_benign", " Benign ", ""], rsid="  rs123 ")
    assert params["detail_exclude_clinvar_terms"] == ["likely benign", "benign"]
    assert params["rsid"] == "rs123"


def test_entries_where_binds_as_valid_clickhouse_query() -> None:
    scope = ExplorerScope(
        assembly_id="a1",
        assembly_name="GRCh38",
        project_ids=["11111111-1111-1111-1111-111111111111"],
    )
    filters = GlobalVariantFilters(gene="BRCA1", variant_type="SNV", impacts=["high"])
    params: dict = {"gt_ref_missing": ("0/0",), "gt_hom": ("1/1",)}
    clauses = _entries_where(scope, filters, params, tag_variant_ids=["1-100-A-T"], shown_members=_MEMBERS)
    where_sql = " AND ".join(clauses)

    assert "e.project_guid IN %(project_guids)s" in where_sql
    assert "e.variantId IN %(tag_variant_ids)s" in where_sql
    # The gene and the impact: on the row's own annotation.
    assert "(e.key, e.annotationSetHash) IN (SELECT gi.key" in where_sql
    assert "(e.key, e.annotationSetHash) IN (SELECT a.key" in where_sql
    assert "length(e.ref) = 1 AND length(e.alt) = 1" in where_sql  # SNV type filter

    # The generated WHERE (with the genotype params) must bind cleanly via the
    # same clickhouse-connect path the service uses (%(name)s substitution).
    query = (
        f"SELECT uniqExact(key) FROM db.`t` WHERE {where_sql} "
        "AND arrayExists(g -> g NOT IN %(gt_ref_missing)s, `calls.gt`)"
    )
    rendered_query, _ = bind_query(query, params)
    assert "%(project_guids)s" not in rendered_query
    assert "%(tag_variant_ids)s" not in rendered_query


def test_entries_where_without_filters_has_no_annotation_subquery() -> None:
    scope = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=["p1"])
    params: dict = {}
    clauses = _entries_where(scope, GlobalVariantFilters(), params, tag_variant_ids=None, shown_members=_MEMBERS)
    where_sql = " AND ".join(clauses)
    for table in ("annotation_index", "annotations", "gene_index"):
        assert f"variants/{table}`" not in where_sql
    assert "tag_variant_ids" not in params


def test_imputed_excluded_by_default_and_included_on_opt_in() -> None:
    scope = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=["p1"])

    default_params: dict = {}
    default_clauses = _entries_where(
        scope, GlobalVariantFilters(), default_params, tag_variant_ids=None, shown_members=_MEMBERS
    )
    assert "lowerUTF8(e.source) NOT IN %(imputed_sources)s" in " AND ".join(default_clauses)
    assert default_params["imputed_sources"] == ("glimpse2", "shapeit")

    opt_in_params: dict = {}
    opt_in_clauses = _entries_where(
        scope,
        GlobalVariantFilters(include_imputed=True),
        opt_in_params,
        tag_variant_ids=None,
        shown_members=_MEMBERS,
    )
    assert "imputed_sources" not in opt_in_params
    assert all("source" not in clause for clause in opt_in_clauses)


def test_sample_genotype_filters_build_per_sample_subqueries() -> None:
    scope = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=["p1"])
    params: dict = {}
    clauses = _entries_where(
        scope,
        GlobalVariantFilters(sample_genotype_filters=[("S1", "hom"), ("S2", "het"), ("S3", "het_hom")]),
        params,
        tag_variant_ids=None,
        shown_members=_MEMBERS,
    )
    where_sql = " AND ".join(clauses)
    # One membership subquery per sample (AND-ed): a variant the sample carries in one of its
    # rows, imputed callsets left out, read by variant (the owner's decision of 2026-11-06).
    assert where_sql.count("e.key IN (SELECT gs.key FROM") == 3
    assert where_sql.count("lowerUTF8(gs.source) NOT IN %(imputed_sources)s") == 3
    # Each sample's calls under its name or its UUID, in the rows of the families that show
    # it (the owner's decision of 2026-11-06).
    assert where_sql.count("gs.family_guid IN %(explorer_sample_") == 3
    assert params["explorer_sample_0_samples"] == ("S1", "u1")
    assert params["explorer_sample_1_samples"] == ("S2", "u2")
    assert params["explorer_sample_2_samples"] == ("S3", "u3")
    assert [params[f"explorer_sample_{index}_families"] for index in range(3)] == [("f1",), ("f1",), ("f2",)]
    # The family page's sample filter reads each sample's call, with its genotype classes
    # (#511): hom includes a haploid "1", het a "1/2", het_hom both.
    family_params = _small_native_sample_filter_clauses(
        _FAMILY,
        SmallVariantQueryFilters(page=1, page_size=1, sample_filters=["S1:hom", "S2:het", "S3:het|hom"]),
    )[1]
    for index in range(3):
        assert params[f"explorer_sample_{index}_gts"] == family_params[f"sample_filter_{index}_gts"]
    assert "1" in params["explorer_sample_0_gts"] and "1/2" in params["explorer_sample_1_gts"]
    assert set(params["explorer_sample_0_gts"]) | set(params["explorer_sample_1_gts"]) <= set(
        params["explorer_sample_2_gts"]
    )

    rendered_query, _ = bind_query(f"SELECT key FROM db.`t` AS e WHERE {where_sql}", params)
    assert "%(explorer_sample_0_samples)s" not in rendered_query


def test_the_explorer_reads_the_calls_their_family_shows() -> None:
    # The owner's decision of 2026-11-06: in each family's rows, the calls of its active
    # members alone, each under its name or its UUID, as the family search reads them. A
    # sample shown by two families is read in both; it is not read in a third family's rows.
    members = (*_MEMBERS, ves._ShownMember(family_uuid="f3", sample_id="S1", sample_uuid="u1"))
    assert ves._shown_calls(members) == (
        ("f1", "S1"), ("f1", "u1"), ("f1", "S2"), ("f1", "u2"),
        ("f2", "S3"), ("f2", "u3"),
        ("f3", "S1"), ("f3", "u1"),
    )
    params: dict[str, Any] = {}
    clauses = _entries_where(_SCOPE, GlobalVariantFilters(), params, tag_variant_ids=None, shown_members=members)
    # A row is kept when a member its family shows carries the variant.
    assert ves._shown_carrier_condition({}) in clauses
    assert "(e.family_guid, call_sample) IN %(shown_calls)s" in ves._shown_carrier_condition({})
    assert params["shown_calls"] == ves._shown_calls(members)
    bind_query("SELECT 1 FROM t AS e WHERE " + " AND ".join(clauses), params)
    # Without a member, a pair no row holds.
    no_member: dict[str, Any] = {}
    _entries_where(_SCOPE, GlobalVariantFilters(), no_member, tag_variant_ids=None, shown_members=())
    assert no_member["shown_calls"] == (("", ""),)


def test_a_sample_no_family_shows_keeps_no_variant() -> None:
    # A member taken out of its family: its calls stay in the family's rows, but no family of
    # the scope shows it, so the genotype filter on it keeps nothing.
    params: dict[str, Any] = {}
    clauses = _entries_where(
        _SCOPE,
        GlobalVariantFilters(sample_genotype_filters=[("S1", "het"), ("TAKEN-OUT", "het_hom")]),
        params,
        tag_variant_ids=None,
        shown_members=_MEMBERS,
    )
    assert "0" in clauses
    assert "explorer_sample_1_samples" not in params
    # A sample named by its UUID is read as by its name.
    by_uuid: dict[str, Any] = {}
    _entries_where(
        _SCOPE,
        GlobalVariantFilters(sample_genotype_filters=[("u3", "het")]),
        by_uuid,
        tag_variant_ids=None,
        shown_members=_MEMBERS,
    )
    assert (by_uuid["explorer_sample_0_samples"], by_uuid["explorer_sample_0_families"]) == (("S3", "u3"), ("f2",))


def test_a_rows_counts_read_the_calls_their_family_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_execute(query, params):
        seen["query"] = query
        return []

    async def no_annotation(assembly_name, keys):
        return {}

    monkeypatch.setattr(ves, "execute_clickhouse", fake_execute)
    monkeypatch.setattr(ves, "_fetch_annotation_display", no_annotation)
    asyncio.run(
        ves._fetch_variant_rows(
            _RecordingSession(),
            scope=types.SimpleNamespace(project_ids=[_PROJECT], assembly_name="GRCh38"),
            entries_table="db.entries",
            where_sql="1",
            params={},
            sort_expr="total_samples",
            direction="DESC",
            limit=10,
        )
    )
    # Each call of the row, after the ARRAY JOIN, is counted when its family shows it.
    assert "ARRAY JOIN `calls.sampleId` AS sample_id" in seen["query"]
    assert "AND (e.family_guid, sample_id) IN %(shown_calls)s" in seen["query"]


def test_without_a_member_the_search_and_export_ask_clickhouse_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[str] = []

    async def fake_scope(session, user, assembly_id):
        return _SCOPE

    async def no_members(session, project_ids):
        return []

    async def fake_clickhouse(query, params=None):
        queries.append(query)
        return [[1]]

    monkeypatch.setattr(ves, "resolve_scope", fake_scope)
    monkeypatch.setattr(ves, "_shown_members", no_members)
    monkeypatch.setattr(ves, "execute_clickhouse", fake_clickhouse)
    page = asyncio.run(ves.search_global_small_variants(None, user=None, filters=GlobalVariantFilters()))
    export = asyncio.run(ves.export_global_small_variants(None, user=None, filters=GlobalVariantFilters()))
    assert (page.total, page.variants, export.rows, queries) == (0, [], [], [])


def test_bounded_total_caps_count_and_flags_estimate() -> None:
    from backend.app.services.variant_explorer_service import _EXPLORER_COUNT_CAP, _bounded_total

    assert _bounded_total(0) == (0, False)
    assert _bounded_total(42) == (42, False)
    # Exactly the cap is still exact (the count query fetches cap+1 to disambiguate).
    assert _bounded_total(_EXPLORER_COUNT_CAP) == (_EXPLORER_COUNT_CAP, False)
    # Past the cap the true total is unknown -> report the cap, flagged as an estimate.
    assert _bounded_total(_EXPLORER_COUNT_CAP + 1) == (_EXPLORER_COUNT_CAP, True)
    assert _bounded_total(9_999_999) == (_EXPLORER_COUNT_CAP, True)


def test_cursor_roundtrips_and_rejects_stale_or_garbage() -> None:
    from backend.app.services.variant_explorer_service import _decode_cursor, _encode_cursor

    cursor = _encode_cursor("total_samples", "desc", "fp1", 5, 123456, 99)
    assert _decode_cursor(cursor, "total_samples", "desc", "fp1") == (5, 123456, 99)
    # Stale: the sort / order / filter-set it was issued for no longer matches -> None.
    assert _decode_cursor(cursor, "position", "desc", "fp1") is None
    assert _decode_cursor(cursor, "total_samples", "asc", "fp1") is None
    assert _decode_cursor(cursor, "total_samples", "desc", "fp2") is None  # filters changed
    # Garbage in -> None, never an exception.
    assert _decode_cursor("not-valid-base64!!", "total_samples", "desc", "fp1") is None
    assert _decode_cursor("", "total_samples", "desc", "fp1") is None


def test_filters_fingerprint_is_stable_and_sensitive() -> None:
    from backend.app.services.variant_explorer_service import (
        GlobalVariantFilters,
        _filters_fingerprint,
    )

    base = _filters_fingerprint(GlobalVariantFilters(), "asm1")
    assert base == _filters_fingerprint(GlobalVariantFilters(), "asm1")  # stable
    assert base != _filters_fingerprint(GlobalVariantFilters(gene="BRCA1"), "asm1")  # filter
    assert base != _filters_fingerprint(GlobalVariantFilters(include_imputed=True), "asm1")
    assert base != _filters_fingerprint(GlobalVariantFilters(), "asm2")  # assembly


def test_seek_having_is_direction_aware_and_degenerate_safe() -> None:
    from backend.app.services.variant_explorer_service import _seek_having

    desc = _seek_having("total_samples", "DESC")
    assert "total_samples < %(cur_sort)s" in desc  # DESC seeks strictly-less
    assert "xpos > %(cur_xpos)s" in desc
    assert "key > %(cur_key)s" in desc

    asc = _seek_having("total_samples", "ASC")
    assert "total_samples > %(cur_sort)s" in asc  # ASC seeks strictly-greater

    # When the sort column IS xpos the predicate still has a unique key tiebreaker.
    pos = _seek_having("xpos", "ASC")
    assert "xpos > %(cur_sort)s" in pos and "key > %(cur_key)s" in pos


_PROJECT = "11111111-1111-1111-1111-111111111111"
_SCOPE = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=[_PROJECT])
# The samples the scope's families show: S1 and S2 in family f1, S3 in family f2.
_MEMBERS = (
    ves._ShownMember(family_uuid="f1", sample_id="S1", sample_uuid="u1"),
    ves._ShownMember(family_uuid="f1", sample_id="S2", sample_uuid="u2"),
    ves._ShownMember(family_uuid="f2", sample_id="S3", sample_uuid="u3"),
)
_ANNOTATIONS = _small_table_name("GRCh38", "variants/annotations")
_ANNOTATION_INDEX = _small_table_name("GRCh38", "variants/annotation_index")


def _explorer_where(**filters: Any) -> tuple[list[str], dict[str, Any]]:
    """The explorer's WHERE clauses and parameters for these filters."""
    params: dict[str, Any] = {}
    clauses = _entries_where(
        _SCOPE, GlobalVariantFilters(**filters), params, tag_variant_ids=None, shown_members=_MEMBERS
    )
    return clauses, params


def _transcript_subqueries(clauses: list[str]) -> list[str]:
    """The clauses that read the per-transcript ``annotations`` table."""
    return [clause for clause in clauses if f"FROM {_ANNOTATIONS} AS a " in clause]


def _transcript_subquery(condition: str, *, locus: str = "") -> str:
    """The clause "one transcript of the row's own annotation meets ``condition``", the
    family search's (``_small_annotation_row_membership_condition``)."""
    return (
        "(e.key, e.annotationSetHash) IN (SELECT a.key, a.annotationSetHash"
        f" FROM {_ANNOTATIONS} AS a WHERE {locus}({condition}))"
    )


def _index_subqueries(clauses: list[str]) -> list[str]:
    """The clauses that read the per-variant ``annotation_index``."""
    return [clause for clause in clauses if f"FROM {_ANNOTATION_INDEX} AS ai " in clause]


def _family_search_condition(**explorer_filters: Any) -> tuple[str, dict[str, Any]]:
    """The condition the family search puts on one transcript for the same filters, with
    its parameters. The explorer names the impact and consequence lists in the plural."""
    renamed = {"impacts": "impact", "effects": "effect"}
    family_filters = {renamed.get(name, name): value for name, value in explorer_filters.items()}
    clauses, params = _small_detail_filter_clauses(
        SmallVariantQueryFilters(page=1, page_size=100, **family_filters)
    )
    assert len(clauses) == 1
    return clauses[0], params


def test_clinvar_rescue_keeps_pathogenic_variants_past_the_frequency_ceilings() -> None:
    # The explorer page offers "ClinVar P/LP overrules the frequency filter", on by
    # default, and the service ignored it: a pathogenic variant above a cut-off was
    # dropped although the page said it was kept (#526). The rescue is the family search's,
    # on the transcript that meets the other annotation filters (CLIN-29).
    clauses, params = _explorer_where(
        max_gnomad_af=0.01, max_gnomad_hom_count=2, clinvar_overrides_frequency=True
    )
    [transcript] = _transcript_subqueries(clauses)
    assert (
        "((ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s)"
        " AND ifNull(a.gnomad_hom_count, 0) <= %(detail_max_gnomad_hom_count)s)"
        " OR hasAny(a.clinvar_terms, %(detail_clinvar_rescue_terms)s))"
    ) in transcript
    # The frequency ceilings sit only inside the rescued block, not also on their own.
    assert transcript.count("a.gnomad_af") == 1
    assert not _index_subqueries(clauses)
    # The family search's terms, including ClinVar's aggregate form.
    assert params["detail_clinvar_rescue_terms"] == [
        "pathogenic",
        "likely pathogenic",
        "pathogenic/likely pathogenic",
    ]
    # The query binds (the terms are an array parameter).
    bind_query("SELECT 1 FROM t WHERE " + " AND ".join(clauses), params)


def test_frequency_ceilings_apply_to_every_variant_without_the_rescue() -> None:
    clauses, params = _explorer_where(max_gnomad_af=0.01)
    assert _transcript_subqueries(clauses) == [
        _transcript_subquery("(ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s))")
    ]
    assert "detail_clinvar_rescue_terms" not in params
    # The rescue alone, with no ceiling to lift, adds nothing.
    clauses, params = _explorer_where(clinvar_overrides_frequency=True)
    assert not _transcript_subqueries(clauses) and not _index_subqueries(clauses)
    assert not [name for name in params if name.startswith(("detail_", "ann_"))]


@pytest.mark.parametrize(
    "filters",
    [
        pytest.param({"mane_only": True, "impacts": ["HIGH"]}, id="mane-only-and-high"),
        pytest.param({"canonical_only": True, "impacts": ["HIGH"]}, id="canonical-only-and-high"),
        pytest.param({"lof_only": True, "impacts": ["MODERATE"]}, id="lof-only-and-moderate"),
        pytest.param({"mane_only": True, "min_revel": 0.5}, id="mane-only-and-revel"),
    ],
)
def test_per_transcript_filters_hold_on_one_transcript_together(filters: dict[str, Any]) -> None:
    # CLIN-29: the family search needs one transcript to meet every annotation filter: with
    # MANE only and impact HIGH it drops a variant whose HIGH transcript is not MANE and
    # whose MANE transcript is MODIFIER. The explorer tested each filter against the
    # annotation index, which pools the transcripts, and kept that variant. Now both
    # filters sit in one subquery over the transcript rows, and none on the pooled index.
    clauses, _params = _explorer_where(**filters)
    condition, _family_params = _family_search_condition(**filters)
    assert _transcript_subqueries(clauses) == [_transcript_subquery(condition)]
    assert not _index_subqueries(clauses)


@pytest.mark.parametrize(
    "filters",
    [
        pytest.param({"impacts": ["HIGH", "MODERATE"]}, id="impact"),
        pytest.param({"effects": ["missense_variant", "stop_gained"]}, id="consequence"),
        pytest.param({"clinvar": ["Pathogenic", "Likely_pathogenic"]}, id="clinvar"),
        pytest.param({"hgvsc": "c.100"}, id="hgvsc"),
        pytest.param({"hgvsp": "p.Arg34"}, id="hgvsp"),
        pytest.param({"canonical_only": True}, id="canonical-only"),
        pytest.param({"mane_only": True}, id="mane-only"),
        pytest.param({"lof_only": True}, id="lof-only"),
        pytest.param(
            {
                "max_gnomad_af": 0.01,
                "max_gnomad_exomes_af": 0.01,
                "max_gnomad_genomes_af": 0.02,
                "max_gnomad_popmax_af": 0.03,
                "max_topmed_af": 0.04,
                "max_gnomad_ac": 50,
                "max_gnomad_hom_count": 10,
                "max_gnomad_hemi_count": 10,
            },
            id="frequency",
        ),
        pytest.param({"max_gnomad_af": 0.01, "clinvar_overrides_frequency": True}, id="frequency-rescue"),
        pytest.param({"min_cadd": 20.0}, id="cadd"),
        pytest.param({"min_revel": 0.5}, id="revel"),
        pytest.param({"min_spliceai": 0.2}, id="spliceai"),
        pytest.param({"sift": "deleterious"}, id="sift"),
        pytest.param({"polyphen": "probably_damaging"}, id="polyphen"),
        pytest.param({"min_cadd": 25.0, "min_revel": 0.5}, id="any-in-silico-predictor"),
        pytest.param({"impacts": ["HIGH"], "min_spliceai": 0.5}, id="impact-or-spliceai"),
        pytest.param({"lof_only": True, "impacts": ["HIGH"]}, id="lof-only-and-high"),
        pytest.param(
            {
                "impacts": ["HIGH", "MODERATE"],
                "max_gnomad_exomes_af": 0.01,
                "max_gnomad_genomes_af": 0.01,
                "max_gnomad_popmax_af": 0.01,
                "max_gnomad_hom_count": 10,
                "max_gnomad_hemi_count": 10,
                "clinvar_overrides_frequency": True,
                "mane_only": True,
                "min_cadd": 20.0,
            },
            id="phenotype-priority-filters-with-mane",
        ),
    ],
)
def test_the_explorer_applies_the_family_search_annotation_condition(filters: dict[str, Any]) -> None:
    # One rule (CLIN-29): for the same annotation filters the explorer's transcript
    # condition is the family search's, with the same parameters, so the two pages keep and
    # drop the same variants: one in-silico predictor at its minimum is enough, a SpliceAI
    # score at its minimum also meets the impact and consequence filter, and ClinVar terms
    # are read as the family search reads them ("Likely_pathogenic" = "Likely pathogenic").
    clauses, params = _explorer_where(**filters)
    condition, family_params = _family_search_condition(**filters)
    assert _transcript_subqueries(clauses) == [_transcript_subquery(condition)]
    assert {name: params.get(name) for name in family_params} == family_params
    bind_query("SELECT 1 FROM t WHERE " + " AND ".join(clauses), params)


def test_the_gene_rsid_and_excluded_clinvar_read_the_rows_own_annotation() -> None:
    # As on the family page, each row is read with its own annotation (its key and annotation
    # set): the gene in the row's symbols or its gene index, the rsID
    # in the row's own or its annotation index row, the excluded ClinVar classes on no
    # transcript of it. The explorer read the annotation index by key, so any family's
    # annotation of a variant let every family's rows through (the owner's decision of
    # 2026-11-06).
    clauses, _params = _explorer_where(gene="BRCA1", rsid="rs80357", exclude_clinvar=["Benign"])
    where_sql = " AND ".join(clauses)
    assert "arrayExists(gene -> lower(gene) IN %(entry_gene_terms)s, e.gene_symbols)" in where_sql
    assert "positionCaseInsensitive(ifNull(e.rsid, ''), %(rsid)s) > 0" in where_sql
    assert where_sql.count("(e.key, e.annotationSetHash) IN (") == 2
    assert where_sql.count("(e.key, e.annotationSetHash) NOT IN (") == 1
    assert "SELECT DISTINCT ai.key" not in where_sql


def test_the_locus_narrows_the_transcript_scan() -> None:
    # As in the family search, the locus also bounds the per-transcript subquery, so a
    # regional search does not read every transcript row of the assembly. A variant's
    # position is the same in every table, so the result does not change.
    clauses, params = _explorer_where(chromosome="17", start=43_000_000, end=43_200_000, impacts=["HIGH"])
    condition, _family_params = _family_search_condition(impacts=["HIGH"])
    assert _transcript_subqueries(clauses) == [
        _transcript_subquery(
            condition, locus="a.chrom IN %(chromosomes)s AND a.pos >= %(start)s AND a.pos <= %(end)s AND "
        )
    ]
    assert clauses[4:8] == [
        "e.chrom IN %(chromosomes)s",
        "e.pos >= %(start)s",
        "e.pos <= %(end)s",
        "e.xpos BETWEEN %(xpos_start)s AND %(xpos_end)s",
    ]
    assert (params["chromosomes"], params["start"], params["end"]) == (("17", "chr17"), 43_000_000, 43_200_000)


@pytest.mark.parametrize(
    ("chromosome", "names"),
    [
        ("17", ("17", "chr17")),
        ("chr17", ("17", "chr17")),
        ("M", ("M", "chrM", "MT", "chrMT")),
        ("chrM", ("M", "chrM", "MT", "chrMT")),
        ("MT", ("MT", "chrMT", "M", "chrM")),
    ],
)
def test_the_chromosome_is_read_with_or_without_chr_and_chrm_by_any_name(
    chromosome: str, names: tuple[str, ...]
) -> None:
    # The explorer compared the chromosome as written: "chrM:1-16569" (sent as M) found
    # nothing of a family whose VCF names the mitochondrion MT, which its family page finds.
    clauses, params = _explorer_where(chromosome=chromosome)
    assert "e.chrom IN %(chromosomes)s" in clauses
    assert params["chromosomes"] == names


def _without_family_keys(clause: str) -> str:
    """A family search clause less what bounds its annotation subqueries to the family's own
    variants (``_small_family_key_condition``): the explorer reads every family's at once."""
    head = " AND identity("
    while (start := clause.find(head)) != -1:
        index = clause.index(" IN (SELECT fk.key FROM ", start) + len(" IN ")
        depth = 0
        while True:
            depth += {"(": 1, ")": -1}.get(clause[index], 0)
            index += 1
            if depth == 0:
                break
        clause = clause[:start] + clause[index:]
    return clause


# The family search's clauses that are a family's alone.
_FAMILY_ONLY_CLAUSES = {"e.family_guid = %(family_guid)s", "e.sign = 1", "e.project_guid IN %(project_ids)s", "0"}


@pytest.mark.parametrize(
    ("explorer_filters", "family_filters"),
    [
        pytest.param({}, {}, id="imputed-callsets"),
        pytest.param({"chromosome": "chrM"}, {"chromosome": "chrM"}, id="chromosome"),
        pytest.param({"chromosome": "X", "start": 100, "end": 200}, {"chromosome": "X", "start": 100, "end": 200}, id="location"),
        pytest.param({"start": 43_000_000}, {"start": 43_000_000}, id="start-alone"),
        pytest.param({"variant_type": "mnv"}, {"variant_type": "mnv"}, id="type"),
        pytest.param({"gene": "BRCA1, ENSG0001"}, {"gene": "BRCA1, ENSG0001"}, id="gene"),
        pytest.param({"rsid": " rs123 "}, {"rsid": " rs123 "}, id="rsid"),
        pytest.param({"exclude_clinvar": ["Likely_benign"]}, {"exclude_clinvar": ["Likely_benign"]}, id="excluded-clinvar"),
        pytest.param(
            {"chromosome": "17", "start": 1, "end": 2, "impacts": ["HIGH"], "mane_only": True, "max_gnomad_af": 0.01},
            {"chromosome": "17", "start": 1, "end": 2, "impact": ["HIGH"], "mane_only": True, "max_gnomad_af": 0.01},
            id="per-transcript-at-a-locus",
        ),
        pytest.param(
            {"gene": "BRCA1", "rsid": "rs1", "exclude_clinvar": ["Benign"], "variant_type": "SNV", "chromosome": "1"},
            {"gene": "BRCA1", "rsid": "rs1", "exclude_clinvar": ["Benign"], "variant_type": "SNV", "chromosome": "1"},
            id="together",
        ),
    ],
)
def test_the_explorer_reads_each_shared_filter_with_the_family_search_clause(
    explorer_filters: dict[str, Any], family_filters: dict[str, Any]
) -> None:
    # One definition (the roadmap's *Variant Explorer filters*): every clause the explorer
    # puts on a row for a filter it shares with the family search is the clause the family
    # search puts on its rows, with the same parameters, less the bound of the family's own
    # variants; and the family search puts no other clause for these filters.
    clauses, params = _explorer_where(**explorer_filters)
    # The explorer's own: its projects, and a carrier the row's family shows.
    assert clauses[:3] == [
        "e.sign = 1",
        "e.project_guid IN %(project_guids)s",
        ves._shown_carrier_condition({}),
    ]
    family_clauses, family_params, _ = _small_query_filter_parts(
        _FAMILY, SmallVariantQueryFilters(page=1, page_size=100, **family_filters), exclude_imputed=True
    )
    shared = [_without_family_keys(clause) for clause in family_clauses if clause not in _FAMILY_ONLY_CLAUSES]
    assert shared and clauses[3:] == shared
    explorer_params = {
        name: value for name, value in params.items() if name not in {"project_guids", "shown_calls", "gt_alt"}
    }
    assert explorer_params == {name: family_params[name] for name in explorer_params}
    bind_query("SELECT 1 FROM t AS e WHERE " + " AND ".join(clauses), params)


class _RecordingSession:
    """Records each statement with its parameters and answers with ``rows``."""

    def __init__(self, rows=()) -> None:
        self.rows = list(rows)
        self.calls: list[tuple[object, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append((statement, dict(params or {})))
        rows = self.rows
        return types.SimpleNamespace(
            all=lambda: list(rows),
            mappings=lambda: types.SimpleNamespace(all=lambda: list(rows)),
        )


def test_review_filter_reads_one_id_past_its_cap_in_variant_id_order() -> None:
    # The tag / classification filter takes at most `limit` ids. Postgres returns them in
    # variant_id order with one more, so a capped list is the same on every request and the
    # caller knows more matched (DATA-2; before, an arbitrary subset was kept silently).
    from backend.app.services.variant_explorer_service import _variant_ids_matching_reviews

    session = _RecordingSession(rows=[("1-100-A-G",), ("1-200-A-G",), ("1-300-A-G",)])
    variant_ids, capped = asyncio.run(
        _variant_ids_matching_reviews(
            session, project_ids=[_PROJECT], classifications=[], tags=["report"], limit=2
        )
    )
    assert (variant_ids, capped) == (["1-100-A-G", "1-200-A-G"], True)
    statement, params = session.calls[0]
    assert "ORDER BY r.variant_id" in str(statement)
    assert "LIMIT :limit" in str(statement)
    assert params["limit"] == 3

    within = _RecordingSession(rows=[("1-100-A-G",), ("1-200-A-G",)])
    assert asyncio.run(
        _variant_ids_matching_reviews(
            within, project_ids=[_PROJECT], classifications=["pathogenic"], tags=[], limit=2
        )
    ) == (["1-100-A-G", "1-200-A-G"], False)


def test_the_classification_filter_matches_a_review_whatever_the_case_of_its_label() -> None:
    # ENG-6: the class labels are in sentence case ("Likely pathogenic - class 4"); a review or
    # a filter saved before holds "Likely Pathogenic - class 4". Both sides are lowercased, as
    # the family page compares them.
    from backend.app.services.variant_explorer_service import _variant_ids_matching_reviews

    session = _RecordingSession(rows=[("1-100-A-G",)])
    asyncio.run(
        _variant_ids_matching_reviews(
            session,
            project_ids=[_PROJECT],
            classifications=["Likely Pathogenic - class 4", " likely pathogenic - class 4", "VUS - class 3"],
            tags=[],
            limit=10,
        )
    )
    statement, params = session.calls[0]
    assert params["classifications"] == ["likely pathogenic - class 4", "vus - class 3"]
    assert "lower((CASE" in str(statement) and ") IN (__[POSTCOMPILE_classifications])" in str(statement)


def test_review_display_map_binds_any_number_of_variant_ids_as_one_parameter() -> None:
    # An export hydrates up to 50,001 rows. One bind parameter per id (an expanding IN) put
    # more parameters in the statement than the 32,767 asyncpg takes, so every export of a
    # result larger than that failed (DATA-2).
    from sqlalchemy.dialects.postgresql.asyncpg import dialect as asyncpg_dialect

    from backend.app.services.variant_explorer_service import _review_display_map

    session = _RecordingSession()
    variant_ids = [f"1-{100 + index}-A-G" for index in range(50_001)]
    asyncio.run(_review_display_map(session, project_ids=[_PROJECT], variant_ids=variant_ids))
    statement, params = session.calls[0]
    expanded = statement.compile(dialect=asyncpg_dialect()).construct_expanded_state(params)
    # One parameter for the project and one array holding every id.
    assert len(expanded.positiontup) == 2
    assert expanded.parameters["variant_ids"] == variant_ids


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        # The labels the review dialog writes, ACMG and CNV alike.
        (
            ["Likely benign - class 2", "VUS - class 3", "Pathogenic - class 5", "Likely Pathogenic - class 4"],
            "Pathogenic - class 5",
        ),
        # The same labels in sentence case, and in any other case.
        (["likely benign - class 2", "Likely pathogenic - class 4", "VUS - class 3"], "Likely pathogenic - class 4"),
        (["BENIGN - CLASS 1", "likely benign - class 2"], "likely benign - class 2"),
        # The ACMG and CNV class keys.
        (["acmg_class_3", "cnv_class_5", "acmg_class_4"], "cnv_class_5"),
        # The bare class words the review API also takes (free text), in any case or spelling;
        # a class word the family page reads as its class shows as the class label (CLIN-71).
        (["VUS", "Likely_Pathogenic", "benign"], "Likely_Pathogenic"),
        (["uncertain significance", "likely benign", "Pathogenic"], "Pathogenic - class 5"),
        # Text that names no class loses to any class.
        (["Not a class", "Benign - class 1"], "Benign - class 1"),
    ],
)
def test_the_explorer_shows_the_most_severe_classification_whatever_form_it_is_stored_in(
    monkeypatch, stored, shown
) -> None:
    # CLIN-69: the rank read the stored label lowercased ("likely pathogenic - class 4"),
    # which no rank key matched, so every label ranked 0 and the first review read won: a
    # variant classified pathogenic in one family and likely benign in another could show
    # "Likely benign - class 2".
    # One review row per family; the labels alternate between the variant's own
    # classification and its compound-het pair's.
    review_rows = [
        _review_row(
            classification=None if index % 2 else label,
            compound_het_classification=label if index % 2 else None,
        )
        for index, label in enumerate(stored)
    ]
    assert _shown_row(monkeypatch, review_rows).classification == shown


def _review_row(**fields: Any) -> dict[str, Any]:
    """A review row as the Explorer's review lookup reads it: one family's review of the variant."""
    return {
        "variant_id": "1-100-A-G",
        "family_id": "00000000-0000-0000-0000-00000000000a",
        "classification": None,
        "tags": [],
        "compound_het_classification": None,
        "compound_het_tags": [],
        "updated_at": None,
        **fields,
    }


def _shown_row(monkeypatch: pytest.MonkeyPatch, review_rows: list[dict[str, Any]], session=None):
    """The Explorer row of variant 1-100-A-G, its reviews read as ``review_rows``."""

    async def fake_execute(query, params):
        # key, variant_id, chrom, pos, ref, alt, rsid, xpos, families, hom, het, total, genes
        return [(7, "1-100-A-G", "1", 100, "A", "G", None, 1_000_000_100, 2, 0, 2, 2, ["BRCA1"])]

    async def no_annotation(assembly_name, keys):
        return {}

    monkeypatch.setattr(ves, "execute_clickhouse", fake_execute)
    monkeypatch.setattr(ves, "_fetch_annotation_display", no_annotation)
    scope = types.SimpleNamespace(project_ids=[_PROJECT], assembly_name="GRCh38")
    rows, _cursors = asyncio.run(
        ves._fetch_variant_rows(
            session or _RecordingSession(rows=review_rows),
            scope=scope,
            entries_table="db.entries",
            where_sql="1",
            params={},
            sort_expr="total_samples",
            direction="DESC",
            limit=10,
        )
    )
    return rows[0]


@pytest.mark.parametrize(
    ("reviews", "shown"),
    [
        # A review classified by its class tag alone, with no classification text: the quick
        # tag toggle and a client of the review API can store one. The family page shows its
        # class; the Explorer showed none.
        ([{"tags": ["acmg_class_4", "report"]}], "Likely pathogenic - class 4"),
        ([{"compound_het_tags": ["acmg_class_5"]}], "Pathogenic - class 5"),
        # A class tag outranks another family's classification text, and loses to a more
        # severe one.
        (
            [{"classification": "Likely benign - class 2"}, {"tags": ["acmg_class_4"]}],
            "Likely pathogenic - class 4",
        ),
        (
            [{"classification": "Pathogenic - class 5"}, {"tags": ["acmg_class_3"]}],
            "Pathogenic - class 5",
        ),
        # Text and tag together, as the family page reads them: a class tag before a class
        # label, a class word before a class tag (normalizeReviewClassification).
        ([{"classification": "VUS - class 3", "tags": ["acmg_class_4"]}], "Likely pathogenic - class 4"),
        ([{"classification": "VUS", "tags": ["acmg_class_4"]}], "VUS - class 3"),
        (
            [{"compound_het_classification": "Likely benign - class 2", "compound_het_tags": ["acmg_class_5"]}],
            "Pathogenic - class 5",
        ),
        # Of two class tags of one review, the more severe.
        ([{"tags": ["acmg_class_1", "acmg_class_4"]}], "Likely pathogenic - class 4"),
        # A VUS tier is no class tag.
        ([{"tags": ["acmg_vus_hot", "report"]}], None),
    ],
)
def test_the_explorer_shows_the_class_of_a_review_as_the_family_page_reads_it(
    monkeypatch, reviews, shown
) -> None:
    # CLIN-71: the Explorer read a review's classification text alone. A review classified by
    # its class tag (no text) showed no class, though the family page shows one.
    review_rows = [
        _review_row(family_id=f"00000000-0000-0000-0000-00000000000{index}", **fields)
        for index, fields in enumerate(reviews)
    ]
    assert _shown_row(monkeypatch, review_rows).classification == shown


def test_the_shown_class_and_review_time_do_not_depend_on_the_order_the_reviews_are_read(monkeypatch) -> None:
    # CLIN-71: the review lookup had no ORDER BY, and of two reviews of the same class the first
    # read was shown. Of two reviews of one class the one saved last is shown, then the one of
    # the lower family id; the review time is the latest, whatever the order.
    from datetime import datetime, timezone

    earlier = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    later = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    by_key = _review_row(family_id="00000000-0000-0000-0000-00000000000a", classification="acmg_class_4", updated_at=earlier)
    by_label = _review_row(
        family_id="00000000-0000-0000-0000-00000000000b", classification="Likely pathogenic - class 4", updated_at=later
    )
    benign = _review_row(family_id="00000000-0000-0000-0000-00000000000c", classification="Benign - class 1", updated_at=later)
    for rows in ([by_key, by_label, benign], [benign, by_label, by_key]):
        row = _shown_row(monkeypatch, rows)
        assert row.classification == "Likely pathogenic - class 4"
        assert row.last_seen == later

    # The same with the later review classified by its class tag alone.
    by_tag = {**by_label, "classification": None, "tags": ["acmg_class_4"]}
    for rows in ([by_key, by_tag, benign], [benign, by_tag, by_key]):
        row = _shown_row(monkeypatch, rows)
        assert row.classification == "Likely pathogenic - class 4"
        assert row.last_seen == later

    # Saved at the same time: the review of the lower family id.
    same_time = {**by_key, "updated_at": later}
    for rows in ([same_time, by_tag], [by_tag, same_time]):
        assert _shown_row(monkeypatch, rows).classification == "acmg_class_4"

    # Postgres reads the reviews in that order too.
    session = _RecordingSession(rows=[by_key])
    _shown_row(monkeypatch, [], session=session)
    statement = " ".join(str(session.calls[0][0]).split())
    assert "r.family_id::text AS family_id" in statement
    assert "ORDER BY r.variant_id, r.updated_at DESC, r.family_id" in statement


def test_the_classification_filter_keeps_a_review_by_the_class_it_shows_as_the_family_search_does() -> None:
    # CLIN-75 (the owner's decision of 2026-11-10): the filter matched the classification text
    # alone, so a review classified by its class tag was shown with its class (CLIN-71) and not
    # found by it. It now keeps a review, or its pair review, by the class it shows, in SQL:
    # review_class_label_sql over the review's own text and tags and over the pair's, the rule of
    # the family search's filter (review_shows_class). A bare class word asks for its class.
    # integration/test_variant_explorer_export_integration.py evaluates the SQL on Postgres
    # beside the Python rule, and compares this filter with the family search's.
    from backend.app.services.review_pg_utils import review_class_label_sql
    from backend.app.services.variant_explorer_service import _variant_ids_matching_reviews

    session = _RecordingSession(rows=[("1-100-A-G",)])
    asyncio.run(
        _variant_ids_matching_reviews(
            session,
            project_ids=[_PROJECT],
            classifications=["Likely pathogenic - class 4", "VUS"],
            tags=[],
            limit=10,
        )
    )
    statement, params = session.calls[0]
    own, own_params = review_class_label_sql("r.classification", "r.tags")
    pair, pair_params = review_class_label_sql("r.compound_het_classification", "r.compound_het_tags")
    assert own_params == pair_params
    sql = str(statement)
    assert f"(lower({own}) IN (__[POSTCOMPILE_classifications])" in sql
    assert f"OR lower({pair}) IN (__[POSTCOMPILE_classifications]))" in sql
    assert params["classifications"] == ["likely pathogenic - class 4", "vus - class 3"]
    assert {name: params[name] for name in own_params} == own_params


def test_the_sql_class_rule_reads_the_python_rule_s_tables() -> None:
    # The SQL rule binds the class words, the class tags in their order and the labels from the
    # Python tables, and trims what str.strip() trims: the two cannot read a class apart.
    import sys

    from backend.app.services import acmg_points
    from backend.app.services.review_pg_utils import (
        CLASS_WORD_TAG_KEYS,
        _STRIPPED_CHARACTERS,
        review_class_label_sql,
    )

    sql, params = review_class_label_sql("r.classification", "r.tags")
    words = [params[f"rcl_word_{index}"] for index in range(len(CLASS_WORD_TAG_KEYS))]
    assert words == list(CLASS_WORD_TAG_KEYS)
    assert [params[f"rcl_word_label_{index}"] for index in range(len(words))] == [
        acmg_points.CLASS_LABELS[tag] for tag in CLASS_WORD_TAG_KEYS.values()
    ]
    tags = [params[f"rcl_tag_{index}"] for index in range(len(acmg_points.CLASS_LABELS))]
    assert tags == list(acmg_points.CLASS_LABELS)
    assert [params[f"rcl_tag_label_{index}"] for index in range(len(tags))] == list(
        acmg_points.CLASS_LABELS.values()
    )
    # The class words come first, then the tags, most severe first, then the text.
    assert sql.index("rcl_word_4") < sql.index("rcl_tag_0") < sql.index("rcl_tag_4") < sql.index("ELSE NULLIF")
    assert params["rcl_strip"] == _STRIPPED_CHARACTERS
    assert set(_STRIPPED_CHARACTERS) == {
        chr(code) for code in range(sys.maxunicode + 1) if chr(code).isspace()
    }
    # Only a column name goes into the SQL.
    for column in ("r.classification; DROP TABLE x", "lower(r.classification)", ""):
        with pytest.raises(ValueError):
            review_class_label_sql(column, "r.tags")
        with pytest.raises(ValueError):
            review_class_label_sql("r.classification", column)


def test_a_classification_ranks_by_the_class_it_names() -> None:
    rank = ves._classification_rank
    for label, expected in [
        ("Pathogenic - class 5", 5),
        ("Likely Pathogenic - class 4", 4),
        ("Likely pathogenic - class 4", 4),
        ("VUS - class 3", 3),
        ("Likely benign - class 2", 2),
        ("Benign - class 1", 1),
        ("acmg_class_5", 5),
        ("cnv_class_1", 1),
        ("PATHOGENIC", 5),
        ("likely_pathogenic", 4),
        ("Uncertain significance", 3),
        ("vus", 3),
        ("Likely Benign", 2),
        ("benign", 1),
    ]:
        assert rank(label) == expected, label
    # Text that names no class ranks below every class, so a class always wins over it.
    for label in ("", "   ", "Not a class", "Pathogenic variant", "class 6"):
        assert rank(label) == 0, label


def test_mane_only_keeps_a_mane_select_or_a_mane_plus_clinical_transcript() -> None:
    # CLIN-5: the explorer's *MANE only* read has_mane_select alone, so a variant whose MANE
    # transcript is MANE Plus Clinical was dropped, while the family search keeps it. It
    # reads both flags of a transcript, the family search's condition (CLIN-29).
    clauses, params = _explorer_where(mane_only=True)
    assert _transcript_subqueries(clauses) == [_transcript_subquery("((a.mane_select OR a.mane_plus_clinical))")]
    assert not [name for name in params if name.startswith(("detail_", "ann_"))]
    # Off, it adds nothing.
    clauses, _params = _explorer_where()
    assert not _transcript_subqueries(clauses) and not _index_subqueries(clauses)


# --- the gene panel, as the family page applies it (CLIN-48) ------------------------------

_FAMILY = FamilyMetadataContext(
    family_uuid="f1",
    family_id="F1",
    project_ids=[_PROJECT],
    sample_rows=[],
    sample_uuid_to_name={},
    sample_name_to_uuid={},
    affected_sample_names=[],
    assembly_id="a1",
    assembly_name="GRCh38",
)
_PANEL_ID = "00000000-0000-4000-8000-000000000048"


def _panel(genes: tuple[str, ...] = (), regions: tuple[tuple[str, int, int], ...] = ()) -> PanelFilterConstraints:
    return PanelFilterConstraints(
        genes=genes, regions=tuple(Region(chr=chrom, start=start, end=end) for chrom, start, end in regions)
    )


def _explorer_panel(panel: PanelFilterConstraints, **filters: Any) -> tuple[list[str], dict[str, Any]]:
    """The explorer's WHERE clauses and parameters for this panel, read as the family page
    reads it."""
    params: dict[str, Any] = {}
    clauses = _entries_where(
        _SCOPE,
        GlobalVariantFilters(**filters),
        params,
        tag_variant_ids=None,
        shown_members=_MEMBERS,
        panel_constraints=panel,
    )
    return clauses, params


def _family_panel(panel: PanelFilterConstraints) -> tuple[str | None, dict[str, Any]]:
    """The family page's panel condition, with its parameters, for the same panel, less what
    narrows it to the family's own variants: the explorer reads every family's at once.

    The family page reads the gene index for the keys of the family's rows only
    (``_small_family_key_condition``); that bound, and its parameters, are taken out here
    after checking that the family page has it."""
    params: dict[str, Any] = {}
    condition = _small_panel_filter_condition(
        _FAMILY, SmallVariantQueryFilters(page=1, page_size=100), panel, params=params
    )
    if condition is not None and panel.genes:
        head = " AND identity(gi.key) IN (SELECT fk.key FROM "
        start = condition.index(head)
        depth, index = 1, start + len(head)
        while depth:
            depth += {"(": 1, ")": -1}.get(condition[index], 0)
            index += 1
        bound = condition[start:index]
        assert "fk.family_guid = %(annotation_family_guid)s" in bound
        condition = condition.replace(bound, "")
        assert params.pop("annotation_family_guid") == _FAMILY.family_uuid
        assert params.pop("annotation_project_ids") == tuple(_FAMILY.project_ids)
    return condition, params


def _region_join(panel: PanelFilterConstraints) -> str:
    from backend.app.services.variant_explorer_service import _region_key_condition

    condition = _region_key_condition("GRCh38", panel.regions, prefix="panel_region", params={})
    assert condition is not None
    return condition


def test_the_panel_is_the_family_page_condition_with_its_regions_joined() -> None:
    # CLIN-48: the explorer read the panel's gene symbols only, on the annotation index. A
    # variant in one of the panel's regions (a PanelApp region, or a gene's locus in the
    # gene reference) was left out, which the family page keeps. Now the explorer's panel
    # condition is the family page's own, with the same parameters: the regions, the row's
    # gene symbols, the gene index. Only the regions are tested as a join, which reads every
    # family's variants at once with the same answer.
    panel = _panel(genes=("SYN48A",), regions=(("chr9", 137_101_312, 137_101_900), ("9", 100, 200)))
    clauses, params = _explorer_panel(panel)
    family_condition, family_params = _family_panel(panel)
    family_regions = _small_region_filter_condition(panel.regions, prefix="panel_region", params={})
    assert family_condition is not None and family_regions is not None
    assert family_regions in family_condition
    assert family_condition.replace(family_regions, _region_join(panel)) in clauses
    assert {name: params.get(name) for name in family_params} == family_params
    # The genes are read as the family page reads them: the row's own symbols and the gene
    # index (its symbols and gene ids), not the annotation index's symbols alone.
    assert params["panel_gene_terms"] == ("syn48a",)
    assert params["panel_annotation_gene_terms"] == ("syn48a",)
    assert not [clause for clause in clauses if "ai.gene_symbols" in clause]
    bind_query("SELECT 1 FROM t AS e WHERE " + " AND ".join(clauses), params)


def test_a_panel_of_regions_only_keeps_the_variants_in_its_regions() -> None:
    # A panel of PanelApp regions has no gene: the explorer applied no panel at all and
    # listed every variant. The family page keeps the variants in its regions.
    panel = _panel(regions=(("9", 137_101_312, 137_101_900),))
    clauses, params = _explorer_panel(panel)
    family_condition, family_params = _family_panel(panel)
    assert family_condition == f"({_small_region_filter_condition(panel.regions, prefix='panel_region', params={})})"
    assert f"({_region_join(panel)})" in clauses
    assert params == {**params, **family_params}
    assert "panel_gene_terms" not in params


def test_a_large_gene_panel_is_matched_by_its_genes_and_its_regions() -> None:
    # As on the family page: a panel of genes with more than 1,000 regions, such as the
    # Mendeliome, keeps the variants in its regions as well as its genes' (owner decision
    # 2026-10-14). It was matched on its genes' symbols alone, and its regions not read.
    regions = tuple(("2", 10_000 * index, 10_000 * index + 500) for index in range(1_001))
    panel = _panel(genes=("SYN48A", "SYN48B"), regions=regions)
    clauses, params = _explorer_panel(panel)
    family_condition, family_params = _family_panel(panel)
    family_regions = _small_region_filter_condition(panel.regions, prefix="panel_region", params={})
    assert family_condition is not None and family_regions is not None
    assert family_condition.replace(family_regions, _region_join(panel)) in clauses
    assert {name: params.get(name) for name in family_params} == family_params
    assert len(params["panel_region_chromosomes"]) == 1_001
    assert params["panel_gene_terms"] == ("syn48a", "syn48b")
    # The regions are joined to the annotation index once, not tested against each row.
    assert len([clause for clause in clauses if "variant_window" in clause]) == 1
    assert not [clause for clause in clauses if "arrayExists((region_chrom" in clause]
    bind_query("SELECT 1 FROM t AS e WHERE " + " AND ".join(clauses), params)


def test_the_region_join_reads_the_regions_as_the_family_page_does() -> None:
    # One reading of a region (_small_region_bounds): the chromosome by its match key ("chr"
    # dropped, M read as MT), start and end in order, each region once, one without a
    # chromosome left out.
    from backend.app.services.variant_explorer_service import _region_key_condition

    panel = _panel(
        regions=(("chr1", 200, 100), ("1", 100, 200), ("chrM", 5, 10), ("", 1, 2), ("X", 7, 7))
    )
    explorer_params: dict[str, Any] = {}
    family_params: dict[str, Any] = {}
    condition = _region_key_condition("GRCh38", panel.regions, prefix="panel_region", params=explorer_params)
    _small_region_filter_condition(panel.regions, prefix="panel_region", params=family_params)
    assert explorer_params == family_params == {
        "panel_region_chromosomes": ["1", "MT", "X"],
        "panel_region_starts": [100, 5, 7],
        "panel_region_ends": [200, 10, 7],
    }
    assert condition is not None
    # The family page's overlap: the same chromosome, the REF's first base at or before the
    # region's end and its last base (pos + length(ref) - 1) at or after its start.
    assert "ai.pos + length(ai.ref) - 1 AS ref_end" in condition
    assert "WHERE v.pos <= r.region_end AND v.ref_end >= r.region_start" in condition
    assert "v.chrom_key = r.region_chrom" in condition
    # A REF is listed under the windows of its first and its last base, so a deletion that
    # starts before a region and ends in it meets the region in the region's window.
    assert "range(intDiv(least(toInt64(ai.pos), toInt64(ai.pos) + length(ai.ref) - 1), 65536)" in condition
    assert f"FROM {_ANNOTATION_INDEX} AS ai" in condition
    bind_query(f"SELECT 1 FROM t AS e WHERE {condition}", explorer_params)
    # No region: no condition.
    assert _region_key_condition("GRCh38", (Region(chr="", start=1, end=2),), prefix="p", params={}) is None


def test_a_panel_with_neither_genes_nor_regions_keeps_nothing() -> None:
    # The family page answers an empty page for a panel with no genes and no regions for
    # its assembly (a PanelApp panel whose regions are stored for another assembly only).
    # The explorer listed every variant.
    clauses, _params = _explorer_panel(PanelFilterConstraints())
    assert "0" in clauses


def _explorer_search(monkeypatch: pytest.MonkeyPatch, panel: PanelFilterConstraints | Exception) -> dict[str, Any]:
    """Run the explorer's search and export with ``panel`` as the family page's reading of
    the panel; record what was asked."""
    import backend.app.services.variant_explorer_service as explorer

    seen: dict[str, Any] = {"panel_reads": [], "queries": []}

    async def fake_scope(session, user, assembly_id):
        return _SCOPE

    async def fake_panel(session, panel_id, *, assembly_id=None):
        seen["panel_reads"].append((panel_id, assembly_id))
        if isinstance(panel, Exception):
            raise panel
        return panel

    async def fake_clickhouse(query, params=None):
        seen["queries"].append(query)
        # The search's count finds nothing; the export's rows are none.
        return [[0]] if "SELECT count()" in query else []

    async def fake_members(session, project_ids):
        return list(_MEMBERS)

    monkeypatch.setattr(explorer, "resolve_scope", fake_scope)
    monkeypatch.setattr(explorer, "_fetch_panel_constraints", fake_panel)
    monkeypatch.setattr(explorer, "_shown_members", fake_members)
    monkeypatch.setattr(explorer, "execute_clickhouse", fake_clickhouse)
    filters = GlobalVariantFilters(panel_id=_PANEL_ID)
    seen["page"] = asyncio.run(
        explorer.search_global_small_variants(None, user=None, filters=filters, assembly_id="a1")
    )
    seen["export"] = asyncio.run(
        explorer.export_global_small_variants(None, user=None, filters=filters, assembly_id="a1")
    )
    return seen


def test_the_panel_is_read_for_the_explorers_assembly(monkeypatch: pytest.MonkeyPatch) -> None:
    # The family page's reading of the panel, for the assembly the explorer searches: the
    # regions stored for that assembly and its genes' loci in that assembly (#515).
    seen = _explorer_search(monkeypatch, _panel(genes=("SYN48A",), regions=(("9", 100, 200),)))
    assert seen["panel_reads"] == [(_PANEL_ID, _SCOPE.assembly_id), (_PANEL_ID, _SCOPE.assembly_id)]
    # The search's count and the export's rows apply it, on the entry rows as alias "e".
    count, export = seen["queries"]
    for query in (count, export):
        assert "AS e" in query
        assert "variant_window" in query
        assert "e.gene_symbols" in query
        assert "(e.key, e.annotationSetHash) IN" in query


def test_a_panel_that_keeps_nothing_asks_clickhouse_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _explorer_search(monkeypatch, PanelFilterConstraints())
    assert seen["queries"] == []
    assert (seen["page"].total, seen["page"].variants) == (0, [])
    assert (seen["export"].rows, seen["export"].truncated_reason) == ([], None)


def test_a_panel_that_does_not_exist_is_answered_as_on_the_family_page(monkeypatch: pytest.MonkeyPatch) -> None:
    # The family page's lookup answers 404 for an unknown panel; the explorer listed every
    # variant instead.
    with pytest.raises(HTTPException) as missing:
        _explorer_search(monkeypatch, HTTPException(status_code=404, detail="Panel not found"))
    assert (missing.value.status_code, missing.value.detail) == (404, "Panel not found")


def test_the_cursor_belongs_to_the_panels_genes_and_regions() -> None:
    # A cursor issued before an edit of the panel restarts the list rather than paging on
    # with other genes or regions, as it did when the filters held the panel's genes.
    from backend.app.services.variant_explorer_service import _filters_fingerprint

    filters = GlobalVariantFilters(panel_id=_PANEL_ID)
    before = _panel(genes=("SYN48A",), regions=(("9", 100, 200),))
    assert _filters_fingerprint(filters, "a1", before) == _filters_fingerprint(filters, "a1", before)
    assert _filters_fingerprint(filters, "a1", before) != _filters_fingerprint(
        filters, "a1", _panel(genes=("SYN48A",), regions=(("9", 100, 300),))
    )
    assert _filters_fingerprint(filters, "a1", before) != _filters_fingerprint(filters, "a1")


# --- a variant's carriers, and the annotation a row shows ---------------------------------


def _patch_common(monkeypatch, *, rows, cap=2):
    monkeypatch.setattr(ves, "_VARIANT_CARRIER_ROW_LIMIT", cap)

    async def fake_scope(session, user, assembly_id):
        return types.SimpleNamespace(assembly_name="GRCh38", project_ids=["p1"])

    captured: dict = {}

    async def fake_execute(query, params):
        captured["query"] = query
        captured["params"] = params
        return rows

    async def fake_family_meta(session, project_ids, family_uuids):
        return {
            fam: {"family_name": fam.upper(), "project_id": "p1", "project_name": "Proj1"}
            for fam in family_uuids
        }

    async def fake_members(session, project_ids):
        return [
            ves._ShownMember(family_uuid="famA", sample_id="s1", sample_uuid="u1", role="proband"),
            ves._ShownMember(family_uuid="famA", sample_id="s2", sample_uuid="u2", role="mother"),
            ves._ShownMember(family_uuid="famB", sample_id="s3", sample_uuid="u3", role="proband"),
        ]

    monkeypatch.setattr(ves, "resolve_scope", fake_scope)
    monkeypatch.setattr(ves, "execute_clickhouse", fake_execute)
    monkeypatch.setattr(ves, "_fetch_family_meta", fake_family_meta)
    monkeypatch.setattr(ves, "_shown_members", fake_members)
    return captured


@pytest.mark.asyncio
async def test_get_variant_carriers_flags_truncation(monkeypatch) -> None:
    rows = [
        ("famA", "s1", "0/1", "vid"),
        ("famA", "s2", "0/1", "vid"),
        ("famB", "s3", "0/1", "vid"),  # beyond the cap of 2 -> truncated away
    ]
    captured = _patch_common(monkeypatch, rows=rows, cap=2)

    result = await ves.get_variant_carriers(None, user=types.SimpleNamespace(), variant_key=1)

    # The query is bounded with a LIMIT (fetching cap + 1).
    assert "LIMIT %(carrier_limit)s" in captured["query"]
    assert captured["params"]["carrier_limit"] == 3
    # Truncated to the first 2 rows -> 2 carriers, both in famA.
    assert result.truncated is True
    assert result.total_samples == 2
    assert result.total_families == 1
    assert {group.family_uuid for group in result.families} == {"famA"}


@pytest.mark.asyncio
async def test_get_variant_carriers_exact_under_cap(monkeypatch) -> None:
    rows = [
        ("famA", "s1", "0/1", "vid"),
        ("famB", "u3", "1/1", "vid"),
    ]
    captured = _patch_common(monkeypatch, rows=rows, cap=2)

    result = await ves.get_variant_carriers(None, user=types.SimpleNamespace(), variant_key=1)

    assert result.truncated is False
    assert result.total_samples == 2
    assert result.het_samples == 1
    assert result.hom_samples == 1
    assert result.total_families == 2
    # Only the calls the families show are read, each member under its name or its UUID in
    # its own family; a carrier shows its name, role and phenotype in that family.
    assert "(family_guid, sample_id) IN %(shown_calls)s" in captured["query"]
    assert captured["params"]["shown_calls"] == (
        ("famA", "s1"), ("famA", "u1"), ("famA", "s2"), ("famA", "u2"), ("famB", "s3"), ("famB", "u3"),
    )
    assert [
        (sample.family_uuid, sample.sample_id, sample.individual_name, sample.role)
        for group in result.families
        for sample in group.samples
    ] == [("famA", "s1", "s1", "proband"), ("famB", "u3", "s3", "proband")]


@pytest.mark.asyncio
async def test_without_a_member_the_carriers_ask_clickhouse_nothing(monkeypatch) -> None:
    captured = _patch_common(monkeypatch, rows=[("famA", "s1", "0/1", "vid")])

    async def no_members(session, project_ids):
        return []

    monkeypatch.setattr(ves, "_shown_members", no_members)
    result = await ves.get_variant_carriers(None, user=types.SimpleNamespace(), variant_key=1)

    assert "query" not in captured
    assert (result.total_samples, result.families) == (0, [])


@pytest.mark.asyncio
async def test_fetch_annotation_display_maps_columns_without_gene_symbols(monkeypatch) -> None:
    captured: dict = {}

    async def fake_execute(query, params):
        if "FROM db.annotations" in query:
            return []
        captured["query"] = query
        # key, gene_ids, impacts, effects, clinvar_terms, hgvsc_values, hgvsp_values
        return [(1, ["ENSG1"], ["HIGH"], ["missense_variant"], ["Pathogenic"], ["c.1A>T"], ["p.Met1?"])]

    monkeypatch.setattr(ves, "execute_clickhouse", fake_execute)
    monkeypatch.setattr(ves, "_small_table_name", lambda assembly, suffix: f"db.{suffix.rsplit('/', 1)[1]}")

    result = await ves._fetch_annotation_display("GRCh38", [1])

    # The never-read gene_symbols aggregate is no longer selected, and the
    # remaining columns map to the shifted indices.
    assert "gene_symbols" not in captured["query"]
    assert result[1] == {
        "gene_ids": ["ENSG1"],
        "impacts": ["HIGH"],
        "effects": ["missense_variant"],
        "clinvar_terms": ["Pathogenic"],
        "hgvsc_values": ["c.1A>T"],
        "hgvsp_values": ["p.Met1?"],
    }


@pytest.mark.asyncio
async def test_a_rows_consequence_and_hgvs_come_from_the_transcript_whose_impact_it_shows(monkeypatch) -> None:
    # The row showed the most severe impact but the first distinct consequence and HGVS,
    # so one transcript's consequence came with another's HGVS (CLIN-50).
    async def fake_execute(query, params):
        if "FROM db.annotations" in query:
            # key, impact, effects, hgvsc, hgvsp, mane_select, canonical
            return [
                (1, "LOW", ["synonymous_variant"], "ENST1:c.3G>A", "ENSP1:p.Leu1=", True, True),
                (1, "HIGH", ["stop_gained"], "ENST2:c.7C>T", "ENSP2:p.Gln3*", False, False),
                (1, "HIGH", ["stop_gained"], "ENST3:c.9C>T", "ENSP3:p.Gln3*", False, True),
            ]
        return [
            (1, ["ENSG1"], ["LOW", "HIGH"], ["synonymous_variant", "stop_gained"], [],
             ["ENST1:c.3G>A", "ENST2:c.7C>T", "ENST3:c.9C>T"], ["ENSP1:p.Leu1=", "ENSP2:p.Gln3*", "ENSP3:p.Gln3*"])
        ]

    monkeypatch.setattr(ves, "execute_clickhouse", fake_execute)
    monkeypatch.setattr(ves, "_small_table_name", lambda assembly, suffix: f"db.{suffix.rsplit('/', 1)[1]}")

    result = await ves._fetch_annotation_display("GRCh38", [1])

    # Of the two HIGH transcripts the canonical one, with its own consequence and HGVS.
    assert result[1]["shown"] == ["stop_gained", "ENST3:c.9C>T", "ENSP3:p.Gln3*"]
