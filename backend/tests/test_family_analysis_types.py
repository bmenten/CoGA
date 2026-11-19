"""The analyses a family holds and the assays a sample was taken for (``family_analysis_types``):
lists an import adds to, never overwrites, so one family holds the monogenic and the conventional
NIPT of a pregnancy, and one plasma the assays of both."""

from __future__ import annotations

from backend.app.services.family_analysis_types import (
    added_to,
    family_analysis_types,
    sample_assays,
)


def test_a_family_holds_every_analysis_type_its_metadata_lists() -> None:
    metadata = {"analysis_types": ["conventional_nipt", "monogenic_nipt"]}
    assert family_analysis_types(metadata) == {"conventional_nipt", "monogenic_nipt"}
    # Nothing listed, or not a list: none.
    for value in (None, {}, {"analysis_types": "monogenic_nipt"}, {"analysis_types": [None, 3, ""]}):
        assert family_analysis_types(value) == frozenset()


def test_a_sample_holds_every_assay_its_metadata_lists() -> None:
    assert sample_assays({"assays": ["cnipt_cfdna", "nipt_cfdna"]}) == {"cnipt_cfdna", "nipt_cfdna"}
    assert sample_assays({"assay": "nipt_cfdna"}) == frozenset()


def test_an_import_adds_its_value_sorted_and_once() -> None:
    assert added_to(["monogenic_nipt"], "conventional_nipt") == ["conventional_nipt", "monogenic_nipt"]
    assert added_to(["monogenic_nipt"], " monogenic_nipt ") == ["monogenic_nipt"]
    # A package that names none takes nothing away.
    assert added_to(["monogenic_nipt"], None) == ["monogenic_nipt"]
    assert added_to([], "") == []
