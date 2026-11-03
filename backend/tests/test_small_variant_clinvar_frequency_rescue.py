"""*ClinVar P/LP overrules the frequency filter*: a pathogenic or likely pathogenic variant is
kept past the frequency ceilings when the search asks for it (``clinvar_overrides_frequency``).

The rescue lifts the whole frequency block (the population frequencies and the gnomAD
homozygote, hemizygote and allele counts), for a ClinVar pathogenic or likely pathogenic record,
ClinVar's aggregate form included; never for a benign record or a variant without ClinVar, and
not at all when the search does not ask for it. The Python matcher (``_annotation_matches_normal``)
and the ClickHouse condition (``_small_detail_filter_clauses``, #534) apply the same rule.
"""

from __future__ import annotations

import pytest

from backend.app.services.clickhouse_variant_queries import _small_detail_filter_clauses
from backend.app.services.clickhouse_variant_records import (
    _annotation_matches_normal,
    _flexible_status_match,
    _status_terms,
)
from backend.app.services.family_variant_filters import SmallVariantQueryFilters


def _filters(**overrides) -> SmallVariantQueryFilters:
    base = dict(page=1, page_size=100, max_gnomad_af=0.01, max_gnomad_hom_count=10)
    base.update(overrides)
    return SmallVariantQueryFilters(**base)


@pytest.mark.parametrize(
    ("annotation", "rescue", "kept"),
    [
        ({"gnomad_af": 0.2, "gnomad_hom_count": 5000, "clinvar": "Pathogenic"}, True, True),
        ({"gnomad_af": 0.2, "clinvar": "Likely_pathogenic"}, True, True),
        ({"gnomad_af": 0.2, "clinvar": "Pathogenic"}, False, False),
        ({"gnomad_af": 0.2, "clinvar": "Benign"}, True, False),
        ({"gnomad_af": 0.2}, True, False),
        ({"gnomad_af": 0.0001, "clinvar": "Pathogenic"}, False, True),
        # Common by its homozygote count alone: the rescue covers the whole
        # frequency/hom/hemi/AC block, not the frequencies alone.
        ({"gnomad_af": 0.0001, "gnomad_hom_count": 9000, "clinvar": "Pathogenic"}, False, False),
        ({"gnomad_af": 0.0001, "gnomad_hom_count": 9000, "clinvar": "Pathogenic"}, True, True),
    ],
    ids=[
        "common pathogenic, rescued",
        "common likely pathogenic, rescued",
        "common pathogenic, without the rescue",
        "common benign is not rescued",
        "common without ClinVar is not rescued",
        "rare pathogenic passes regardless",
        "common by hom count, without the rescue",
        "common by hom count, rescued",
    ],
)
def test_the_python_matcher_lifts_the_frequency_block_for_a_pathogenic_clinvar_record(
    annotation: dict, rescue: bool, kept: bool
) -> None:
    assert _annotation_matches_normal(annotation, _filters(clinvar_overrides_frequency=rescue)) is kept


# --- #534: the ClickHouse filter must apply the same rescue ------------------------------
# The candidate fetch filters on population frequency in SQL before any row reaches the
# Python matcher above, so a rescue that exists only in Python never runs.


def _sql(**overrides):
    base = dict(page=1, page_size=100, max_gnomad_af=0.01, max_gnomad_popmax_af=0.01)
    base.update(overrides)
    clauses, params = _small_detail_filter_clauses(SmallVariantQueryFilters(**base))
    return " ".join(clauses), params


def test_sql_frequency_block_is_rescued_by_clinvar_when_enabled() -> None:
    sql, params = _sql(clinvar_overrides_frequency=True)
    # The whole frequency block is OR-ed with the ClinVar P/LP terms, as in Python.
    assert "ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s)" in sql
    assert "ifNull(a.gnomad_popmax_af, 0) <= toFloat32(%(detail_max_gnomad_popmax_af)s)" in sql
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
    assert "ifNull(a.gnomad_af, 0) <= toFloat32(%(detail_max_gnomad_af)s)" in sql


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
