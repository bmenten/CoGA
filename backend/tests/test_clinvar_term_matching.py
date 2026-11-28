"""ClinVar is compared in the terms CoGA's release stores (``classification_terms``, as
``clinvar_vcf`` stores them: ``_status_terms`` of the classification): lower case, spaces for
underscores, a value split on its separators, ClinVar's aggregate ``/`` form included.

The ClinVar options of the filter form keep, or leave out, an allele whose classification in
the release names that option; *Conflicting classifications* keeps ClinVar's conflict in either
wording ClinVar uses. The family search, the Variant Explorer and the monogenic NIPT page share
the two conditions (``hasAny`` on the release's ``classification_terms``,
``clinvar_release_store.clinvar_allele_condition``); each case reads the condition's terms
against the stored terms as ``hasAny`` does: one term in common.

Synthetic values only.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from backend.app.services.clickhouse_variant_queries import _small_annotation_filter_clauses
from backend.app.services.clickhouse_variant_records import _status_terms
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

_RELEASE = date(2026, 11, 22)
_CONTEXT = FamilyMetadataContext(
    family_uuid="terms-family",
    family_id="TERMS",
    project_ids=["terms-project"],
    sample_rows=[{"sample_id": "PROBAND"}],
    sample_uuid_to_name={},
    sample_name_to_uuid={"PROBAND": "terms-sample"},
    affected_sample_names=["PROBAND"],
    assembly_id="terms-assembly",
    assembly_name="GRCh38",
)


def _terms_asked(field: str, option: str, prefix: str) -> list[str]:
    """The terms the ClinVar filter ``field`` with ``option`` asks of the release, after checking
    that its one clause reads them on the release's stored terms."""
    params: dict[str, Any] = {}
    [clause] = _small_annotation_filter_clauses(
        _CONTEXT,
        SmallVariantQueryFilters(page=1, page_size=1, clinvar_coga_release=_RELEASE, **{field: [option]}),
        params=params,
    )
    assert f"hasAny(classification_terms, %({prefix}_terms)s)" in clause
    return list(params[f"{prefix}_terms"])


def _stored(classification: str) -> set[str]:
    """An allele's ``classification_terms`` as the release stores them."""
    return _status_terms(classification)


def _included(option: str, classification: str) -> bool:
    """Whether the ClinVar filter ``option`` keeps an allele the release classifies so."""
    return not set(_terms_asked("clinvar", option, "clinvar_status")).isdisjoint(_stored(classification))


def _excluded(option: str, classification: str) -> bool:
    """Whether the excluded ClinVar ``option`` counts an allele the release classifies so as
    excluded (the row is left out where every allele is)."""
    return not set(_terms_asked("exclude_clinvar", option, "clinvar_excluded")).isdisjoint(_stored(classification))


# Each option of the filter form (SmallVariantFilterForm's CLINVAR_OPTIONS), a classification
# ClinVar's VCF gives that names it, and one that does not.
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
def test_a_clinvar_option_keeps_the_alleles_whose_classification_names_it(option: str, named: str, other: str) -> None:
    assert _included(option, named)
    assert not _included(option, other)


@pytest.mark.parametrize(("option", "named", "other"), _OPTIONS)
def test_an_excluded_clinvar_option_counts_the_alleles_whose_classification_names_it(
    option: str, named: str, other: str
) -> None:
    assert _excluded(option, named)
    assert not _excluded(option, other)
