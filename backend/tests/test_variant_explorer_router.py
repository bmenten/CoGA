from __future__ import annotations

from backend.app.routers.variant_explorer import (
    _EXPORT_COLUMNS,
    _export_cell,
    _parse_sample_genotype_filters,
)
from backend.app.schemas import GlobalVariantRowOut


def test_export_cell_formats_scalars_lists_and_none() -> None:
    row = GlobalVariantRowOut(
        key="1",
        variant_id="1-100-A-T",
        chr="1",
        pos=100,
        ref="A",
        alt="T",
        rsid=None,
        gene="BRCA1",
        gene_symbols=["BRCA1", "RND1"],
        effects=["missense_variant"],
        tags=["candidate", "review"],
        total_samples=3,
    )
    # Scalars stringify, None becomes empty, lists join on "; ".
    assert _export_cell(row, "gene") == "BRCA1"
    assert _export_cell(row, "pos") == "100"
    assert _export_cell(row, "total_samples") == "3"
    assert _export_cell(row, "rsid") == ""
    assert _export_cell(row, "gene_symbols") == "BRCA1; RND1"
    assert _export_cell(row, "tags") == "candidate; review"


def test_export_columns_cover_every_row_field() -> None:
    # Every export field must exist on the row model so headers never desync.
    valid_fields = set(GlobalVariantRowOut.model_fields)
    for field, label in _EXPORT_COLUMNS:
        assert field in valid_fields, f"unknown export field {field!r}"
        assert label.strip()


def test_sample_genotype_values_allow_a_colon_in_the_sample_id() -> None:
    # The page sends "<sample>:<mode>". Splitting on the first ":" read "LAB:1:hom" as
    # sample "LAB" (#526); the mode is the text after the last ":", when it is one.
    assert _parse_sample_genotype_filters(["LAB:1:hom", "S1:het", "S2:het_hom"]) == [
        ("LAB:1", "hom"),
        ("S1", "het"),
        ("S2", "het_hom"),
    ]
    # Without a mode, the whole value is the sample id and both genotypes count.
    assert _parse_sample_genotype_filters(["LAB:1", "S3", "S4:", " S5 : HOM ", ""]) == [
        ("LAB:1", "het_hom"),
        ("S3", "het_hom"),
        ("S4", "het_hom"),
        ("S5", "hom"),
    ]
