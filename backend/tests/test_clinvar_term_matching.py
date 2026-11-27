"""ClinVar is compared in the terms the import stores (``_status_terms``): lower case, spaces
for underscores, a value split on its separators.

The Variant Explorer's ClinVar column shows a variant's most severe stored term
(``_CLINVAR_RANK``), so a likely pathogenic term outranks a benign one beside it. The ClinVar
options of the filter form keep, or leave out, a variant whose ClinVar names that
classification; *Conflicting classifications* keeps ClinVar's conflict in either wording
ClinVar uses. The family search, the Variant Explorer and the monogenic NIPT page share the
two ClickHouse conditions (``hasAny`` on ``clinvar_terms``); each case reads the condition's
terms against the stored terms as ``hasAny`` does: one term in common.

Synthetic values only.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.services.clickhouse_variant_queries import (
    _small_annotation_exclude_filter_condition,
    _small_annotation_filter_condition,
)
from backend.app.services.clickhouse_variant_records import _status_terms
from backend.app.services.family_variant_filters import SmallVariantQueryFilters
from backend.app.services.variant_explorer_service import _CLINVAR_RANK, _most_severe


def _stored_terms(*clinvar_values: str) -> list[str]:
    """A variant's ClinVar terms as the Explorer reads them from the annotation index, in the
    order of the values given: the less severe first, so that two terms ranked alike show the
    first."""
    terms: dict[str, None] = {}
    for value in clinvar_values:
        terms.update(dict.fromkeys(sorted(_status_terms(value))))
    return list(terms)


@pytest.mark.parametrize(
    ("clinvar_values", "shown"),
    [
        (("Benign", "Likely_pathogenic"), "likely pathogenic"),
        (("Benign", "Uncertain_significance"), "uncertain significance"),
        (("Benign", "Likely_benign"), "likely benign"),
        (
            ("Uncertain_significance", "Conflicting_classifications_of_pathogenicity"),
            "conflicting classifications of pathogenicity",
        ),
        (
            ("Uncertain_significance", "Conflicting_interpretations_of_pathogenicity"),
            "conflicting interpretations of pathogenicity",
        ),
        (("Conflicting_classifications_of_pathogenicity", "drug_response"), "drug response"),
        (("Likely_pathogenic", "Pathogenic/Likely_pathogenic"), "pathogenic"),
    ],
)
def test_the_explorer_shows_the_most_severe_stored_clinvar_term(
    clinvar_values: tuple[str, ...], shown: str
) -> None:
    assert _most_severe(_stored_terms(*clinvar_values), _CLINVAR_RANK) == shown


def _included(option: str, clinvar: str) -> bool:
    """Whether the ClinVar filter ``option`` keeps a transcript whose ClinVar is ``clinvar``."""
    params: dict[str, Any] = {}
    condition = _small_annotation_filter_condition(
        SmallVariantQueryFilters(page=1, page_size=1, clinvar=[option]), params=params
    )
    assert condition == "(hasAny(a.clinvar_terms, %(detail_clinvar_terms)s))"
    return not set(params["detail_clinvar_terms"]).isdisjoint(_status_terms(clinvar))


def _excluded(option: str, clinvar: str) -> bool:
    """Whether the excluded ClinVar ``option`` leaves out a variant whose ClinVar is ``clinvar``."""
    params: dict[str, Any] = {}
    condition = _small_annotation_exclude_filter_condition(
        SmallVariantQueryFilters(page=1, page_size=1, exclude_clinvar=[option]), params=params
    )
    assert condition == "hasAny(a.clinvar_terms, %(detail_exclude_clinvar_terms)s)"
    return not set(params["detail_exclude_clinvar_terms"]).isdisjoint(_status_terms(clinvar))


# Each option of the filter form (SmallVariantFilterForm's CLINVAR_OPTIONS), the ClinVar value
# it names as an annotated VCF carries it, and one it does not name.
_OPTIONS = [
    ("Pathogenic", "Pathogenic", "Conflicting_classifications_of_pathogenicity"),
    ("Pathogenic", "Pathogenic/Likely_pathogenic", "Likely_pathogenic"),
    ("Likely pathogenic", "Likely_pathogenic", "Pathogenic"),
    ("Uncertain significance", "Uncertain_significance", "Conflicting_classifications_of_pathogenicity"),
    ("Likely benign", "Likely_benign", "Benign"),
    ("Benign", "Benign|risk_factor", "Likely_benign"),
    ("Conflicting classifications", "Conflicting_classifications_of_pathogenicity", "Uncertain_significance"),
    ("Conflicting classifications", "Conflicting_interpretations_of_pathogenicity", "Pathogenic"),
    ("Conflicting classifications", "Likely_benign&Conflicting_classifications_of_pathogenicity", "Benign"),
]


@pytest.mark.parametrize(("option", "named", "other"), _OPTIONS)
def test_a_clinvar_option_keeps_the_variants_whose_clinvar_names_it(option: str, named: str, other: str) -> None:
    assert _included(option, named)
    assert not _included(option, other)


@pytest.mark.parametrize(("option", "named", "other"), _OPTIONS)
def test_an_excluded_clinvar_option_leaves_out_the_variants_whose_clinvar_names_it(
    option: str, named: str, other: str
) -> None:
    assert _excluded(option, named)
    assert not _excluded(option, other)
