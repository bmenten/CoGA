"""The analyses a family holds and the assays a sample was taken for (``family_analysis_types``):
lists an import adds to, never overwrites, so one family holds the monogenic and the conventional
NIPT of each pregnancy, each by its key, and one plasma the assays of both."""

from __future__ import annotations

from backend.app.services.family_analysis_types import (
    added_to,
    family_analysis_types,
    sample_assays,
)


def test_a_family_holds_every_analysis_type_its_metadata_lists() -> None:
    # The NIPTs of two pregnancies: three analyses, two types.
    metadata = {"analyses": ["conventional_nipt:ZW1", "monogenic_nipt:ZW1", "monogenic_nipt:ZW2"]}
    assert family_analysis_types(metadata) == {"conventional_nipt", "monogenic_nipt"}
    # Nothing listed, or not a list: none.
    for value in (None, {}, {"analyses": "monogenic_nipt"}, {"analyses": [None, 3, ""]}):
        assert family_analysis_types(value) == frozenset()


def test_a_sample_holds_every_assay_its_metadata_lists() -> None:
    assert sample_assays({"assays": ["cnipt_cfdna", "nipt_cfdna"]}) == {"cnipt_cfdna", "nipt_cfdna"}
    assert sample_assays({"assay": "nipt_cfdna"}) == frozenset()


def test_an_import_adds_its_value_sorted_and_once() -> None:
    assert added_to(["monogenic_nipt:ZW1"], "monogenic_nipt:ZW2") == ["monogenic_nipt:ZW1", "monogenic_nipt:ZW2"]
    assert added_to(["monogenic_nipt:ZW1"], " monogenic_nipt:ZW1 ") == ["monogenic_nipt:ZW1"]
    # A package that names none takes nothing away.
    assert added_to(["monogenic_nipt"], None) == ["monogenic_nipt"]
    assert added_to([], "") == []
