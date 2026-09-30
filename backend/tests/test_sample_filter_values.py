"""Sample-filter values (#686): a GQ, DP, AF, AD-alt or QUAL minimum that cannot be read
is refused with a 422 that names it. An unreadable AF or AD-alt minimum used to be
dropped, which widened the search without saying so; an unreadable GQ or DP minimum
surfaced as a 500."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.app import main
from backend.app.services.family_variant_filters import (
    SampleFilterError,
    SmallVariantQueryFilters,
    StructuralVariantQueryFilters,
    parse_small_variant_sample_filter,
    parse_structural_sample_filter,
)


def test_readable_small_variant_values_are_applied() -> None:
    parsed = parse_small_variant_sample_filter("MOTHER:het:20:10.7:0.25:3")

    assert parsed is not None
    assert parsed.minimum_genotype_quality == 20.0
    assert parsed.minimum_depth == 10  # a depth is a whole number of reads
    assert parsed.minimum_allele_frequency == 0.25
    assert parsed.minimum_alt_depth == 3


def test_blank_values_restrict_nothing() -> None:
    parsed = parse_small_variant_sample_filter("MOTHER:het: : :")

    assert parsed is not None
    assert parsed.minimum_genotype_quality is None
    assert parsed.minimum_depth is None
    assert parsed.minimum_allele_frequency is None
    assert parsed.minimum_alt_depth is None
    structural = parse_structural_sample_filter("MOTHER:het::")
    assert structural is not None and structural.minimum_quality is None


def test_an_entry_without_a_sample_restricts_nothing() -> None:
    assert parse_small_variant_sample_filter(":het:abc") is None
    assert parse_structural_sample_filter(":het:abc") is None


@pytest.mark.parametrize(
    ("entry", "label", "value"),
    [
        ("MOTHER:het:abc", "GQ", "abc"),
        ("MOTHER:het::ten", "DP", "ten"),
        ("MOTHER:het:::0,2", "AF", "0,2"),
        ("MOTHER:het::::3x", "AD alt", "3x"),
        ("MOTHER:het:nan", "GQ", "nan"),
        ("MOTHER:het:::inf", "AF", "inf"),
    ],
)
def test_an_unreadable_small_variant_value_is_refused_and_named(
    entry: str, label: str, value: str
) -> None:
    with pytest.raises(SampleFilterError) as refused:
        parse_small_variant_sample_filter(entry)

    assert str(refused.value) == f"Sample filter for MOTHER: {label} {value!r} is not a number."


def test_an_unreadable_structural_quality_is_refused_and_named() -> None:
    with pytest.raises(SampleFilterError) as refused:
        parse_structural_sample_filter("FATHER:het:high")

    assert str(refused.value) == "Sample filter for FATHER: QUAL 'high' is not a number."


def test_the_query_filters_refuse_an_unreadable_value_before_any_search() -> None:
    # Read on construction, so no search path can drop or crash on it later.
    with pytest.raises(SampleFilterError):
        SmallVariantQueryFilters(
            page=1, page_size=10, sample_filters=["PROBAND:het", "MOTHER:ref:::abc"]
        )
    with pytest.raises(SampleFilterError):
        StructuralVariantQueryFilters(page=1, page_size=10, sample_filters=["MOTHER:het:abc"])

    kept = SmallVariantQueryFilters(page=1, page_size=10, sample_filters=["MOTHER:het:20"])
    assert kept.sample_filters == ["MOTHER:het:20"]


def test_the_app_answers_an_unreadable_value_with_a_422_that_names_it() -> None:
    handler = main.app.exception_handlers[SampleFilterError]
    error = SampleFilterError("Sample filter for MOTHER: AF 'abc' is not a number.")

    response = asyncio.run(handler(None, error))  # type: ignore[arg-type]

    assert response.status_code == 422
    assert json.loads(response.body) == {
        "detail": "Sample filter for MOTHER: AF 'abc' is not a number."
    }
