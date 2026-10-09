from __future__ import annotations

import asyncio
import re
import types

from clickhouse_connect.driver.binding import bind_query

from backend.app.services.clickhouse_variant_queries import _small_detail_filter_clauses
from backend.app.services.family_variant_filters import SmallVariantQueryFilters
from backend.app.services.variant_explorer_service import (
    _CLASSIFICATION_RANK,
    _CLINVAR_RANK,
    _IMPACT_RANK,
    ExplorerScope,
    GlobalVariantFilters,
    _annotation_index_clauses,
    _classify_type,
    _entries_where,
    _first_non_empty,
    _most_severe,
    _small_table_name,
    _split_terms,
)


def test_classify_type() -> None:
    assert _classify_type("A", "G") == "SNV"
    assert _classify_type("AC", "GT") == "MNV"
    assert _classify_type("A", "AG") == "INDEL"
    assert _classify_type("ACG", "A") == "INDEL"


def test_most_severe_classification_and_impact() -> None:
    assert _most_severe(["acmg_class_2", "acmg_class_5", "acmg_class_3"], _CLASSIFICATION_RANK) == "acmg_class_5"
    assert _most_severe(["MODIFIER", "HIGH", "LOW"], _IMPACT_RANK) == "HIGH"
    assert _most_severe(["likely_benign", "pathogenic"], _CLINVAR_RANK) == "pathogenic"
    assert _most_severe([], _IMPACT_RANK) is None


def test_first_non_empty() -> None:
    assert _first_non_empty(["", "  ", "BRCA1"]) == "BRCA1"
    assert _first_non_empty([]) is None


def test_split_terms() -> None:
    assert _split_terms("BRCA1, SCN1A  KMT2D;TP53") == ["BRCA1", "SCN1A", "KMT2D", "TP53"]
    assert _split_terms(None) == []


def test_small_table_name_normalizes_assembly() -> None:
    name = _small_table_name("GRCh38", "entries")
    assert name.endswith("`GRCh38/SNV_INDEL/entries`")
    # A messy name is normalized to a ClickHouse-safe dataset key (shared with
    # ingestion), not rejected — so e.g. T2T can be queried.
    normalized = _small_table_name("bad assembly!", "entries")
    assert normalized.endswith("`bad_assembly_/SNV_INDEL/entries`")


def test_annotation_index_clauses_build_params() -> None:
    filters = GlobalVariantFilters(
        gene="BRCA1, SCN1A",
        impacts=["high", "moderate"],
        clinvar=["Pathogenic"],
        max_gnomad_af=0.01,
        min_cadd=20.0,
        canonical_only=True,
    )
    params: dict = {}
    clauses = _annotation_index_clauses(filters, params)
    joined = " AND ".join(clauses)
    assert "ai.gene_symbols" in joined
    assert "ai.impacts" in joined
    assert "ai.clinvar_terms" in joined
    assert "ai.max_gnomad_af" in joined
    assert "ai.max_cadd_phred" in joined
    assert "ai.has_canonical" in joined
    # Gene/impact terms are normalised for matching.
    assert params["ann_genes"] == ["brca1", "scn1a"]
    assert params["ann_impacts"] == ["HIGH", "MODERATE"]
    assert params["ann_clinvar"] == ["pathogenic"]


def test_entries_where_binds_as_valid_clickhouse_query() -> None:
    scope = ExplorerScope(
        assembly_id="a1",
        assembly_name="GRCh38",
        project_ids=["11111111-1111-1111-1111-111111111111"],
    )
    filters = GlobalVariantFilters(gene="BRCA1", variant_type="SNV", impacts=["high"])
    params: dict = {"gt_ref_missing": ("0/0",), "gt_hom": ("1/1",)}
    clauses = _entries_where(scope, filters, params, tag_variant_ids=["1-100-A-T"])
    where_sql = " AND ".join(clauses)

    assert "project_guid IN %(project_guids)s" in where_sql
    assert "variantId IN %(tag_variant_ids)s" in where_sql
    assert "SELECT DISTINCT ai.key FROM" in where_sql  # annotation subquery embedded
    assert "length(ref) = 1 AND length(alt) = 1" in where_sql  # SNV type filter

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
    clauses = _entries_where(scope, GlobalVariantFilters(), params, tag_variant_ids=None)
    where_sql = " AND ".join(clauses)
    assert "SELECT DISTINCT ai.key" not in where_sql
    assert "tag_variant_ids" not in params


def test_imputed_excluded_by_default_and_included_on_opt_in() -> None:
    scope = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=["p1"])

    default_params: dict = {}
    default_clauses = _entries_where(
        scope, GlobalVariantFilters(), default_params, tag_variant_ids=None
    )
    assert "lowerUTF8(source) NOT IN %(imputed_sources)s" in " AND ".join(default_clauses)
    assert default_params["imputed_sources"] == ("glimpse2", "shapeit")

    opt_in_params: dict = {}
    opt_in_clauses = _entries_where(
        scope, GlobalVariantFilters(include_imputed=True), opt_in_params, tag_variant_ids=None
    )
    assert "imputed_sources" not in opt_in_params
    assert all("source" not in clause for clause in opt_in_clauses)


def test_sample_genotype_filters_build_per_sample_subqueries() -> None:
    scope = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=["p1"])
    params: dict = {"gt_ref_missing": ("0/0",), "gt_hom": ("1/1",)}
    clauses = _entries_where(
        scope,
        GlobalVariantFilters(sample_genotype_filters=[("S1", "hom"), ("S2", "het")]),
        params,
        tag_variant_ids=None,
    )
    where_sql = " AND ".join(clauses)
    # One membership subquery per sample (AND-ed).
    assert where_sql.count("key IN (SELECT key FROM") == 2
    assert "s_id = %(sample_gt_0)s" in where_sql
    assert "s_id = %(sample_gt_1)s" in where_sql
    assert "(s_gt IN %(gt_hom)s" in where_sql  # S1 hom
    assert "(s_gt IN %(gt_het)s" in where_sql  # S2 het
    # Classes, not literals (#511): hom includes a haploid "1", het a "1/2".
    assert "1" in params["gt_hom"] and "1/2" in params["gt_het"]
    assert params["sample_gt_0"] == "S1"
    assert params["sample_gt_1"] == "S2"

    rendered_query, _ = bind_query(f"SELECT key FROM db.`t` WHERE {where_sql}", params)
    assert "%(sample_gt_0)s" not in rendered_query


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


def test_clinvar_rescue_keeps_pathogenic_variants_past_the_frequency_ceilings() -> None:
    # The explorer page offers "ClinVar P/LP overrules the frequency filter", on by
    # default, and the service ignored it: a pathogenic variant above a cut-off was
    # dropped although the page said it was kept (#526).
    params: dict = {}
    clauses = _annotation_index_clauses(
        GlobalVariantFilters(max_gnomad_af=0.01, max_gnomad_hom_count=2, clinvar_overrides_frequency=True),
        params,
    )
    rescue = [clause for clause in clauses if "ann_clinvar_rescue_terms" in clause]
    assert rescue == [
        "(((ai.max_gnomad_af IS NULL OR ai.max_gnomad_af <= %(ann_max_gnomad_af)s)"
        " AND (ai.max_gnomad_hom_count IS NULL OR ai.max_gnomad_hom_count <= %(ann_max_gnomad_hom_count)s))"
        " OR hasAny(ai.clinvar_terms, %(ann_clinvar_rescue_terms)s))"
    ]
    # The frequency ceilings sit only inside the rescued block, not also on their own.
    assert not [c for c in clauses if "ai.max_gnomad_af" in c and c not in rescue]
    # The family search's terms, including ClinVar's aggregate form.
    assert params["ann_clinvar_rescue_terms"] == [
        "pathogenic",
        "likely pathogenic",
        "pathogenic/likely pathogenic",
    ]
    # The query binds (the terms are an array parameter).
    bind_query(
        "SELECT 1 FROM t AS ai WHERE " + " AND ".join(clauses), params
    )


def test_frequency_ceilings_apply_to_every_variant_without_the_rescue() -> None:
    params: dict = {}
    clauses = _annotation_index_clauses(GlobalVariantFilters(max_gnomad_af=0.01), params)
    assert "(ai.max_gnomad_af IS NULL OR ai.max_gnomad_af <= %(ann_max_gnomad_af)s)" in clauses
    assert "ann_clinvar_rescue_terms" not in params
    # The rescue alone, with no ceiling to lift, adds nothing.
    params = {}
    assert _annotation_index_clauses(GlobalVariantFilters(clinvar_overrides_frequency=True), params) == []
    assert params == {}


_PROJECT = "11111111-1111-1111-1111-111111111111"


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


def test_mane_only_keeps_a_mane_select_or_a_mane_plus_clinical_transcript() -> None:
    # CLIN-5: the explorer's *MANE only* read has_mane_select alone, so a variant whose MANE
    # transcript is MANE Plus Clinical was dropped, while the family search keeps it.
    params: dict = {}
    clauses = _annotation_index_clauses(GlobalVariantFilters(mane_only=True), params)
    assert clauses == ["(ai.has_mane_select OR ai.has_mane_plus_clinical)"]
    assert params == {}
    # Off, it adds nothing.
    assert _annotation_index_clauses(GlobalVariantFilters(), {}) == []


def test_mane_only_reads_the_transcript_flags_the_family_search_reads() -> None:
    # The family search tests each transcript (annotations, alias a); the explorer each
    # variant (annotation_index, alias ai, one has_<flag> per transcript flag). Both read
    # the same flags.
    explorer = " ".join(_annotation_index_clauses(GlobalVariantFilters(mane_only=True), {}))
    family_clauses, _params = _small_detail_filter_clauses(
        SmallVariantQueryFilters(page=1, page_size=100, mane_only=True)
    )
    family = " ".join(family_clauses)
    assert re.findall(r"\bai\.has_(\w+)", explorer) == ["mane_select", "mane_plus_clinical"]
    assert re.findall(r"\ba\.(\w+)", family) == ["mane_select", "mane_plus_clinical"]
