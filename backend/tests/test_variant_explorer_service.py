from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest
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


def test_annotation_index_clauses_hold_the_per_variant_filters_only() -> None:
    # The gene, rsID and excluded ClinVar filters hold for a variant as a whole, as on the
    # family page, so they read the annotation index, which pools its transcripts. The
    # annotation filters the family page applies per transcript are not read there
    # (CLIN-29), nor is the panel, which is the family page's own condition (CLIN-48).
    filters = GlobalVariantFilters(
        gene="BRCA1, SCN1A",
        panel_id="00000000-0000-4000-8000-000000000048",
        rsid="rs123",
        exclude_clinvar=["Benign"],
        impacts=["high", "moderate"],
        effects=["missense_variant"],
        clinvar=["Pathogenic"],
        hgvsc="c.100",
        max_gnomad_af=0.01,
        min_cadd=20.0,
        canonical_only=True,
        mane_only=True,
        lof_only=True,
        sift="deleterious",
        polyphen="probably_damaging",
    )
    params: dict = {}
    joined = " AND ".join(_annotation_index_clauses(filters, params))
    assert "ai.gene_symbols" in joined
    assert "ai.rsid" in joined
    assert "NOT arrayExists(x -> lowerUTF8(x) IN %(ann_exclude_clinvar)s, ai.clinvar_terms)" in joined
    for pooled in ("ai.impacts", "ai.effects", "ai.hgvs", "ai.max_", "ai.has_", "ai.sift", "ai.polyphen"):
        assert pooled not in joined
    # Gene terms are normalised for matching.
    assert params == {
        "ann_genes": ["brca1", "scn1a"],
        "ann_exclude_clinvar": ["benign"],
        "ann_rsid": "rs123",
    }


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
    assert "SELECT DISTINCT ai.key FROM" in where_sql  # the gene: per variant
    assert "SELECT a.key FROM" in where_sql  # the impact: per transcript
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
    assert "SELECT a.key" not in where_sql
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


_PROJECT = "11111111-1111-1111-1111-111111111111"
_SCOPE = ExplorerScope(assembly_id="a1", assembly_name="GRCh38", project_ids=[_PROJECT])
_ANNOTATIONS = _small_table_name("GRCh38", "variants/annotations")
_ANNOTATION_INDEX = _small_table_name("GRCh38", "variants/annotation_index")


def _explorer_where(**filters: Any) -> tuple[list[str], dict[str, Any]]:
    """The explorer's WHERE clauses and parameters for these filters."""
    params: dict[str, Any] = {}
    clauses = _entries_where(_SCOPE, GlobalVariantFilters(**filters), params, tag_variant_ids=None)
    return clauses, params


def _transcript_subqueries(clauses: list[str]) -> list[str]:
    """The clauses that read the per-transcript ``annotations`` table."""
    return [clause for clause in clauses if f"FROM {_ANNOTATIONS} AS a " in clause]


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
        f"key IN (SELECT a.key FROM {_ANNOTATIONS} AS a"
        " WHERE (ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s)))"
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
    assert _transcript_subqueries(clauses) == [
        f"key IN (SELECT a.key FROM {_ANNOTATIONS} AS a WHERE {condition})"
    ]
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
    assert _transcript_subqueries(clauses) == [
        f"key IN (SELECT a.key FROM {_ANNOTATIONS} AS a WHERE {condition})"
    ]
    assert {name: params.get(name) for name in family_params} == family_params
    bind_query("SELECT 1 FROM t WHERE " + " AND ".join(clauses), params)


def test_per_variant_filters_stay_on_the_annotation_index() -> None:
    # The gene, the rsID and the excluded ClinVar significances hold for the variant as a
    # whole, on the family page too: they need no transcript of their own.
    clauses, _params = _explorer_where(gene="BRCA1", rsid="rs80357", exclude_clinvar=["Benign"])
    assert len(_index_subqueries(clauses)) == 1
    assert not _transcript_subqueries(clauses)


def test_the_locus_narrows_the_transcript_scan() -> None:
    # As in the family search, the locus also bounds the per-transcript subquery, so a
    # regional search does not read every transcript row of the assembly. A variant's
    # position is the same in every table, so the result does not change; the locus
    # clauses on the entries are as before.
    clauses, params = _explorer_where(chromosome="17", start=43_000_000, end=43_200_000, impacts=["HIGH"])
    condition, _family_params = _family_search_condition(impacts=["HIGH"])
    assert _transcript_subqueries(clauses) == [
        f"key IN (SELECT a.key FROM {_ANNOTATIONS} AS a WHERE a.chrom = %(chromosome)s"
        f" AND a.pos >= %(start)s AND a.pos <= %(end)s AND {condition})"
    ]
    assert clauses[-3:] == ["chrom = %(chromosome)s", "pos >= %(start)s", "pos <= %(end)s"]
    assert (params["chromosome"], params["start"], params["end"]) == ("17", 43_000_000, 43_200_000)


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
    # transcript is MANE Plus Clinical was dropped, while the family search keeps it. It
    # reads both flags of a transcript, the family search's condition (CLIN-29).
    clauses, params = _explorer_where(mane_only=True)
    assert _transcript_subqueries(clauses) == [
        f"key IN (SELECT a.key FROM {_ANNOTATIONS} AS a WHERE ((a.mane_select OR a.mane_plus_clinical)))"
    ]
    assert not [name for name in params if name.startswith(("detail_", "ann_"))]
    # Off, it adds nothing.
    clauses, _params = _explorer_where()
    assert not _transcript_subqueries(clauses) and not _index_subqueries(clauses)


# --- the gene panel, as the family page applies it (CLIN-48) ------------------------------

from fastapi import HTTPException  # noqa: E402

from backend.app.services.clickhouse_variant_queries import (  # noqa: E402
    _small_panel_filter_condition,
    _small_region_filter_condition,
)
from backend.app.services.clickhouse_variant_records import (  # noqa: E402
    PanelFilterConstraints,
    Region,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext  # noqa: E402

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
        _SCOPE, GlobalVariantFilters(**filters), params, tag_variant_ids=None, panel_constraints=panel
    )
    return clauses, params


def _family_panel(panel: PanelFilterConstraints) -> tuple[str | None, dict[str, Any]]:
    """The family page's panel condition, with its parameters, for the same panel."""
    params: dict[str, Any] = {}
    condition = _small_panel_filter_condition(
        _FAMILY, SmallVariantQueryFilters(page=1, page_size=100), panel, params=params
    )
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

    monkeypatch.setattr(explorer, "resolve_scope", fake_scope)
    monkeypatch.setattr(explorer, "_fetch_panel_constraints", fake_panel)
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
        assert "(e.key, e.annotation_version, e.annotationSetHash) IN" in query


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
