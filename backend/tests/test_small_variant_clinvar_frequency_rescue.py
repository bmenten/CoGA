from __future__ import annotations

from backend.app.services.clickhouse_family_variants import _annotation_matches_normal
from backend.app.services.family_variant_filters import SmallVariantQueryFilters


def _filters(**overrides) -> SmallVariantQueryFilters:
    base = dict(page=1, page_size=100, max_gnomad_af=0.01, max_gnomad_hom_count=10)
    base.update(overrides)
    return SmallVariantQueryFilters(**base)


def test_common_pathogenic_kept_when_rescue_enabled() -> None:
    annotation = {"gnomad_af": 0.2, "gnomad_hom_count": 5000, "clinvar": "Pathogenic"}
    assert _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=True))


def test_common_likely_pathogenic_kept_when_rescue_enabled() -> None:
    annotation = {"gnomad_af": 0.2, "clinvar": "Likely_pathogenic"}
    assert _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=True))


def test_common_pathogenic_filtered_without_rescue() -> None:
    annotation = {"gnomad_af": 0.2, "clinvar": "Pathogenic"}
    assert not _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=False))


def test_rescue_does_not_keep_common_benign() -> None:
    annotation = {"gnomad_af": 0.2, "clinvar": "Benign"}
    assert not _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=True))


def test_rescue_does_not_keep_common_variant_without_clinvar() -> None:
    annotation = {"gnomad_af": 0.2}
    assert not _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=True))


def test_rare_pathogenic_passes_regardless() -> None:
    annotation = {"gnomad_af": 0.0001, "clinvar": "Pathogenic"}
    assert _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=False))


def test_rescue_only_bypasses_frequency_not_hom_count_independently() -> None:
    # A P/LP variant that is common by hom_count is also rescued (rescue covers
    # the whole frequency/hom/hemi/AC block).
    annotation = {"gnomad_af": 0.0001, "gnomad_hom_count": 9000, "clinvar": "Pathogenic"}
    assert not _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=False))
    assert _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=True))


# --- #534: the ClickHouse filter must apply the same rescue ------------------------------
# The candidate fetch filters on population frequency in SQL before any row reaches the
# Python matcher above, so a rescue that exists only in Python never runs.

from backend.app.services.clickhouse_variant_queries import _small_detail_filter_clauses  # noqa: E402
from backend.app.services.clickhouse_variant_records import (  # noqa: E402
    _flexible_status_match,
    _status_terms,
)


def _sql(**overrides):
    base = dict(page=1, page_size=100, max_gnomad_af=0.01, max_gnomad_popmax_af=0.01)
    base.update(overrides)
    clauses, params = _small_detail_filter_clauses(SmallVariantQueryFilters(**base))
    return " ".join(clauses), params


def test_sql_frequency_block_is_rescued_by_clinvar_when_enabled() -> None:
    sql, params = _sql(clinvar_overrides_frequency=True)
    # The whole frequency block is OR-ed with the ClinVar P/LP terms, as in Python.
    assert "ifNull(a.gnomad_af, 0) <= %(detail_max_gnomad_af)s" in sql
    assert "ifNull(a.gnomad_popmax_af, 0) <= %(detail_max_gnomad_popmax_af)s" in sql
    assert ") OR hasAny(a.clinvar_terms, %(detail_clinvar_rescue_terms)s))" in sql
    assert params["detail_clinvar_rescue_terms"] == [
        "pathogenic",
        "likely pathogenic",
        # rows stored before "/" split ClinVar's aggregate form keep it as one term
        "pathogenic/likely pathogenic",
    ]


def test_sql_frequency_block_is_strict_without_rescue() -> None:
    sql, params = _sql(clinvar_overrides_frequency=False)
    assert "hasAny(a.clinvar_terms" not in sql
    assert "detail_clinvar_rescue_terms" not in params
    assert "ifNull(a.gnomad_af, 0) <= %(detail_max_gnomad_af)s" in sql


def test_sql_rescue_covers_counts_as_well_as_frequencies() -> None:
    sql, _params = _sql(
        clinvar_overrides_frequency=True,
        max_gnomad_af=None,
        max_gnomad_popmax_af=None,
        max_gnomad_hom_count=10,
    )
    assert "ifNull(a.gnomad_hom_count, 0) <= %(detail_max_gnomad_hom_count)s" in sql
    assert "OR hasAny(a.clinvar_terms, %(detail_clinvar_rescue_terms)s)" in sql


def test_sql_adds_no_rescue_clause_without_a_frequency_ceiling() -> None:
    sql, params = _sql(clinvar_overrides_frequency=True, max_gnomad_af=None, max_gnomad_popmax_af=None)
    assert "hasAny(a.clinvar_terms" not in sql
    assert "detail_clinvar_rescue_terms" not in params


def test_clinvar_aggregate_significance_is_split_into_its_terms() -> None:
    # ClinVar's own CLNSIG aggregate forms.
    assert _status_terms("Pathogenic/Likely_pathogenic") == {"pathogenic", "likely pathogenic"}
    assert _status_terms("Benign/Likely_benign") == {"benign", "likely benign"}
    # VEP's CLIN_SIG joins with "&"; still split as before.
    assert _status_terms("pathogenic&likely_pathogenic") == {"pathogenic", "likely pathogenic"}


def test_aggregate_pathogenic_matches_a_pathogenic_filter_and_the_rescue() -> None:
    assert _flexible_status_match("Pathogenic/Likely_pathogenic", ["Pathogenic"])
    annotation = {"gnomad_af": 0.2, "clinvar": "Pathogenic/Likely_pathogenic"}
    assert _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=True))
    assert not _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=False))
