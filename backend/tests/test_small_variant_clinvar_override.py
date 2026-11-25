"""*ClinVar pathogenic / likely pathogenic overrules the frequency, consequence and in-silico
filters*: a pathogenic or likely pathogenic variant is kept past the consequence filter, the
in-silico filter and the frequency ceilings when the search asks for it
(``clinvar_pathogenic_override``).

The override lifts the whole consequence block (the impacts, the consequences and the SpliceAI
minimum, any of which keeps a transcript), the whole in-silico block (CADD, REVEL, SIFT,
PolyPhen, SpliceAI, any of which keeps it) and the whole frequency block (the population
frequencies and the gnomAD homozygote, hemizygote and allele counts), for a ClinVar pathogenic
or likely pathogenic record, ClinVar's aggregate form included; never for a benign record or a
variant without ClinVar, never the other per-transcript filters (the transcript flags, the
ClinVar statuses), and not at all when the search does not ask for it. The transcript a
variant names as the one its filters held on reads the consequence and the scores strictly. The ClickHouse
condition applies it (``_small_annotation_filter_condition``); the real-ClickHouse filter spec
holds it to the rule (integration/test_small_variant_filter_parity_clickhouse.py).
"""

from __future__ import annotations

from backend.app.services.clickhouse_variant_queries import (
    _small_annotation_filter_condition,
    _small_detail_filter_clauses,
)
from backend.app.services.clickhouse_variant_records import (
    CLINVAR_OVERRIDE_TERMS,
    _status_filter_terms,
    _status_terms,
)
from backend.app.services.clickhouse_variant_rows import _annotation_columns
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

_OVERRIDE = "hasAny(a.clinvar_terms, %(detail_clinvar_override_terms)s)"


def _sql(**overrides):
    base = dict(page=1, page_size=100, max_gnomad_af=0.01, max_gnomad_popmax_af=0.01)
    base.update(overrides)
    clauses, params = _small_detail_filter_clauses(SmallVariantQueryFilters(**base))
    return " ".join(clauses), params


# --- The frequency block ----------------------------------------------------------------


def test_sql_frequency_block_is_overridden_by_clinvar_when_enabled() -> None:
    sql, params = _sql(clinvar_pathogenic_override=True)
    # The whole frequency block is OR-ed with the ClinVar P/LP terms.
    assert (
        "((ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s)"
        " AND ifNull(a.gnomad_popmax_af, 0) <= toFloat32(%(detail_max_gnomad_popmax_af)s))"
        f" OR {_OVERRIDE})"
    ) in sql
    assert params["detail_clinvar_override_terms"] == ["pathogenic", "likely pathogenic"]


def test_sql_frequency_block_is_strict_without_the_override() -> None:
    sql, params = _sql(clinvar_pathogenic_override=False)
    assert "hasAny(a.clinvar_terms" not in sql
    assert "detail_clinvar_override_terms" not in params
    assert "ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s)" in sql


def test_sql_override_covers_counts_as_well_as_frequencies() -> None:
    sql, _params = _sql(
        clinvar_pathogenic_override=True,
        max_gnomad_af=None,
        max_gnomad_popmax_af=None,
        max_gnomad_hom_count=10,
    )
    assert f"((ifNull(a.gnomad_hom_count, 0) <= %(detail_max_gnomad_hom_count)s) OR {_OVERRIDE})" in sql


def test_sql_adds_no_override_clause_without_a_filter_it_lifts() -> None:
    sql, params = _sql(
        clinvar_pathogenic_override=True, max_gnomad_af=None, max_gnomad_popmax_af=None, canonical_only=True
    )
    assert "hasAny(a.clinvar_terms" not in sql
    assert "detail_clinvar_override_terms" not in params


# --- The consequence block --------------------------------------------------------------


def test_sql_impact_is_overridden_by_clinvar_when_enabled() -> None:
    # A synonymous or splice-region variant ClinVar holds pathogenic passes HIGH/MODERATE.
    sql, params = _sql(
        clinvar_pathogenic_override=True,
        max_gnomad_af=None,
        max_gnomad_popmax_af=None,
        impact=["HIGH", "MODERATE"],
    )
    assert sql == f"(((a.impact IN %(detail_impact_terms)s) OR {_OVERRIDE}))"
    assert params["detail_impact_terms"] == ("high", "moderate")
    assert params["detail_clinvar_override_terms"] == ["pathogenic", "likely pathogenic"]


def test_sql_override_lifts_the_whole_consequence_block() -> None:
    # Impact, consequence and the SpliceAI minimum are one unit: any of them keeps a
    # transcript, and the override keeps a pathogenic one that meets none.
    sql, _params = _sql(
        clinvar_pathogenic_override=True,
        max_gnomad_af=None,
        max_gnomad_popmax_af=None,
        impact=["HIGH"],
        effect=["stop_gained"],
        min_spliceai=0.5,
    )
    assert (
        "((a.impact IN %(detail_impact_terms)s OR hasAny(a.effects, %(detail_effect_terms)s)"
        " OR ifNull(a.spliceai_max, -1) >= toFloat32(%(detail_min_spliceai)s))"
        f" OR {_OVERRIDE})"
    ) in sql


def test_sql_consequence_block_is_strict_without_the_override() -> None:
    sql, params = _sql(max_gnomad_af=None, max_gnomad_popmax_af=None, impact=["HIGH"], effect=["stop_gained"])
    assert "hasAny(a.clinvar_terms" not in sql
    assert "detail_clinvar_override_terms" not in params
    assert sql == "((a.impact IN %(detail_impact_terms)s OR hasAny(a.effects, %(detail_effect_terms)s)))"


# --- The in-silico block ----------------------------------------------------------------


def test_sql_override_lifts_the_whole_in_silico_block() -> None:
    # Any one predictor keeps a transcript; the override keeps a pathogenic one that none does.
    sql, params = _sql(
        clinvar_pathogenic_override=True,
        max_gnomad_af=None,
        max_gnomad_popmax_af=None,
        min_cadd=20,
        min_revel=0.5,
        sift="deleterious",
        polyphen="probably_damaging",
    )
    assert sql == (
        "(((ifNull(a.cadd_phred, -1) >= toFloat32(%(detail_min_cadd)s)"
        " OR ifNull(a.revel, -1) >= toFloat32(%(detail_min_revel)s)"
        " OR match(a.sift, %(detail_sift_pattern)s)"
        " OR match(a.polyphen, %(detail_polyphen_pattern)s))"
        f" OR {_OVERRIDE}))"
    )
    assert params["detail_clinvar_override_terms"] == ["pathogenic", "likely pathogenic"]


def test_sql_in_silico_block_is_strict_without_the_override() -> None:
    sql, params = _sql(max_gnomad_af=None, max_gnomad_popmax_af=None, min_cadd=20)
    assert sql == "((ifNull(a.cadd_phred, -1) >= toFloat32(%(detail_min_cadd)s)))"
    assert "detail_clinvar_override_terms" not in params


def test_sql_spliceai_alone_is_overridden_in_both_blocks() -> None:
    # The SpliceAI minimum passes the consequence block and is one of the predictors: the
    # override lifts it in each.
    sql, _params = _sql(
        clinvar_pathogenic_override=True, max_gnomad_af=None, max_gnomad_popmax_af=None, min_spliceai=0.2
    )
    spliceai = "ifNull(a.spliceai_max, -1) >= toFloat32(%(detail_min_spliceai)s)"
    assert sql == f"((({spliceai}) OR {_OVERRIDE}) AND (({spliceai}) OR {_OVERRIDE}))"


# --- What the override leaves strict -----------------------------------------------------


def test_sql_override_leaves_the_transcript_flags_and_clinvar_statuses_strict() -> None:
    sql, _params = _sql(
        clinvar_pathogenic_override=True,
        impact=["HIGH"],
        min_cadd=20,
        clinvar=["Pathogenic"],
        canonical_only=True,
        mane_only=True,
        lof_only=True,
    )
    assert " AND hasAny(a.clinvar_terms, %(detail_clinvar_terms)s) AND a.canonical AND " in sql
    assert " AND (a.mane_select OR a.mane_plus_clinical) AND " in sql
    assert " AND a.lof NOT IN ('', '.', 'na', 'n/a') AND " in sql
    # Consequence, frequency and in silico: three blocks, each overridden.
    assert sql.count(_OVERRIDE) == 3


def test_filter_transcript_reads_the_consequence_and_scores_strictly() -> None:
    # The transcript a variant names as the one its filters held on meets the consequence and
    # in-silico filters itself; the frequency block keeps its override.
    filters = SmallVariantQueryFilters(
        page=1,
        page_size=100,
        impact=["HIGH"],
        min_revel=0.5,
        max_gnomad_af=0.01,
        clinvar_pathogenic_override=True,
    )
    params: dict[str, object] = {}
    condition = _small_annotation_filter_condition(filters, params=params, strict_transcript_filters=True)
    assert condition == (
        "((a.impact IN %(detail_impact_terms)s)"
        f" AND ((ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s)) OR {_OVERRIDE})"
        " AND (ifNull(a.revel, -1) >= toFloat32(%(detail_min_revel)s)))"
    )


# --- The ClinVar terms ------------------------------------------------------------------


def test_clinvar_aggregate_significance_is_split_into_its_terms() -> None:
    # ClinVar's own CLNSIG aggregate forms.
    assert _status_terms("Pathogenic/Likely_pathogenic") == {"pathogenic", "likely pathogenic"}
    assert _status_terms("Benign/Likely_benign") == {"benign", "likely benign"}
    # VEP's CLIN_SIG joins with "&"; split the same way.
    assert _status_terms("pathogenic&likely_pathogenic") == {"pathogenic", "likely pathogenic"}


def test_aggregate_pathogenic_matches_a_pathogenic_filter_and_the_override() -> None:
    # The stored terms of ClinVar's aggregate form meet a Pathogenic filter and the override's
    # terms, which the SQL compares them with (hasAny(a.clinvar_terms, …)).
    stored = _annotation_columns({"gnomad_af": 0.2, "clinvar": "Pathogenic/Likely_pathogenic"}).clinvar_terms
    assert not stored.isdisjoint(_status_filter_terms(["Pathogenic"]))
    assert not stored.isdisjoint(_status_filter_terms(list(CLINVAR_OVERRIDE_TERMS)))


def test_benign_and_conflicting_records_are_not_overridden() -> None:
    override_terms = set(_status_filter_terms(list(CLINVAR_OVERRIDE_TERMS)))
    for clinvar in ("Benign/Likely_benign", "Conflicting_classifications_of_pathogenicity", "Uncertain_significance"):
        stored = _annotation_columns({"clinvar": clinvar}).clinvar_terms
        assert stored.isdisjoint(override_terms), clinvar
