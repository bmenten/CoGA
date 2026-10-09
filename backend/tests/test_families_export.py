from __future__ import annotations

from backend.app.routers.families_small_variants import (
    _FAMILY_EXPORT_COLUMNS,
    _family_export_cell,
)
from backend.app.schemas import GenotypeOut, SmallVariantReviewOut, VariantOut


def _variant() -> VariantOut:
    return VariantOut(
        id="1-100-A-T",
        chr="1",
        start=100,
        end=100,
        length=1,
        type="SNV",
        ref="A",
        alt="T",
        gene="BRCA1",
        impact="HIGH",
        gnomad_af=0.0001,
        genotypes=[
            GenotypeOut(sample="proband", gt="0/1"),
            GenotypeOut(sample="mother", gt="0/0"),
        ],
        review=SmallVariantReviewOut(
            variant_id="1-100-A-T",
            classification="pathogenic",
            tags=["candidate", "confirmed"],
        ),
    )


def test_family_export_cell_scalars_review_and_genotypes() -> None:
    row = _variant()
    assert _family_export_cell(row, "gene") == "BRCA1"
    assert _family_export_cell(row, "start") == "100"
    assert _family_export_cell(row, "gnomad_af") == "0.0001"
    assert _family_export_cell(row, "rsid") == ""  # None -> empty
    # Review fields are pulled from the nested review object.
    assert _family_export_cell(row, "classification") == "pathogenic"
    assert _family_export_cell(row, "tags") == "candidate; confirmed"
    # Genotypes are flattened to "sample=gt" pairs.
    assert _family_export_cell(row, "genotypes") == "proband=0/1; mother=0/0"


def test_family_export_cell_handles_missing_review() -> None:
    row = VariantOut(id="1-1-A-T", chr="1", start=1, end=1, length=1, type="SNV")
    assert _family_export_cell(row, "classification") == ""
    assert _family_export_cell(row, "tags") == ""
    assert _family_export_cell(row, "genotypes") == ""


def test_family_export_columns_are_resolvable() -> None:
    # Every export field must resolve on a real row without raising.
    row = _variant()
    for field, label in _FAMILY_EXPORT_COLUMNS:
        assert label.strip()
        _family_export_cell(row, field)  # must not raise
