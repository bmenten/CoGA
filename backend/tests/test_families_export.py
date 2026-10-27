from __future__ import annotations

import pytest

from backend.app.routers.families_small_variants import (
    _FAMILY_EXPORT_COLUMNS,
    _family_export_cell,
)
from backend.app.schemas import (
    GenotypeOut,
    SmallVariantFilterTranscriptOut,
    SmallVariantReviewOut,
    VariantOut,
)


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


def test_family_export_names_the_transcript_the_filters_held_on() -> None:
    # CLIN-51: the row describes the variant's primary transcript; when the annotation
    # filters held on another one, the last columns name it with its own values. A variant
    # kept by SIFT deleterious on its second transcript read "tolerated" and nothing else.
    row = _variant().model_copy(
        update={
            "transcript_id": "ENST_SYN51_1",
            "sift": "tolerated(0.2)",
            "polyphen": "benign(0.1)",
            "filter_transcript": SmallVariantFilterTranscriptOut(
                gene="BRCA1",
                transcript_id="ENST_SYN51_2",
                impact="MODERATE",
                effect="missense_variant",
                hgvsc="ENST_SYN51_2:c.100C>T",
                hgvsp="ENSP_SYN51_2:p.Arg34Trp",
                cadd_phred=25.1,
                revel=0.71,
                spliceai_max=0.02,
                sift="deleterious(0.02)",
                polyphen="probably_damaging(0.95)",
            ),
        }
    )
    labels = [label for _field, label in _FAMILY_EXPORT_COLUMNS]
    cells = dict(zip(labels, (_family_export_cell(row, field) for field, _label in _FAMILY_EXPORT_COLUMNS)))

    # The row's own columns stay the primary transcript's...
    assert (cells["Transcript"], cells["SIFT"], cells["PolyPhen"]) == (
        "ENST_SYN51_1",
        "tolerated(0.2)",
        "benign(0.1)",
    )
    # ...and the filter-matched transcript follows, after every column there was before.
    assert labels[: labels.index("Genotypes") + 1] == [
        "Chromosome", "Start", "End", "Type", "Ref", "Alt", "rsID", "Gene", "Gene ID",
        "Transcript", "Impact", "Effect", "HGVSc", "HGVSp", "ClinVar", "gnomAD AF",
        "gnomAD hom", "CADD", "REVEL", "SpliceAI", "SIFT", "PolyPhen", "Classification",
        "Tags", "Genotypes",
    ]
    assert {label: cells[label] for label in labels[labels.index("Genotypes") + 1 :]} == {
        "Filter-matched transcript": "ENST_SYN51_2",
        "Filter-matched gene": "BRCA1",
        "Filter-matched impact": "MODERATE",
        "Filter-matched effect": "missense_variant",
        "Filter-matched HGVSc": "ENST_SYN51_2:c.100C>T",
        "Filter-matched HGVSp": "ENSP_SYN51_2:p.Arg34Trp",
        "Filter-matched CADD": "25.1",
        "Filter-matched REVEL": "0.71",
        "Filter-matched SpliceAI": "0.02",
        "Filter-matched SIFT": "deleterious(0.02)",
        "Filter-matched PolyPhen": "probably_damaging(0.95)",
    }


def test_family_export_leaves_the_filter_columns_empty_when_the_row_s_transcript_met_them() -> None:
    row = _variant()
    assert row.filter_transcript is None
    filter_columns = [(field, label) for field, label in _FAMILY_EXPORT_COLUMNS if label.startswith("Filter-matched")]
    assert len(filter_columns) == 11
    for field, label in filter_columns:
        assert _family_export_cell(row, field) == "", label


@pytest.mark.asyncio
async def test_a_variant_in_two_compound_het_pairs_is_one_row_of_the_export(monkeypatch) -> None:
    # The export flattened each pair into its rows, so a variant in two pairs was listed
    # twice; the file says nothing of pairs, so the second row only repeated the first
    # (CLIN-52).
    from types import SimpleNamespace

    from backend.app.schemas import SmallVariantGroupOut, VariantPage
    from backend.app.services import clickhouse_family_variants

    def snv(position: int) -> VariantOut:
        return VariantOut(id=f"1-{position}-A-T", chr="1", start=position, end=position, length=1, type="SNV")

    async def page(*_args, **_kwargs) -> VariantPage:
        return VariantPage(
            total=2,
            variant_groups=[
                SmallVariantGroupOut(group_key="g1", gene="GENE_CH", variants=[snv(100), snv(200)]),
                SmallVariantGroupOut(group_key="g2", gene="GENE_CH", variants=[snv(100), snv(300)]),
            ],
        )

    monkeypatch.setattr(clickhouse_family_variants, "get_family_small_variants_page", page)

    export = await clickhouse_family_variants.export_family_small_variants(
        None,  # type: ignore[arg-type]
        context=SimpleNamespace(),  # type: ignore[arg-type]
        inheritance="compound_het",
    )

    assert [str(row.id) for row in export.rows] == ["1-100-A-T", "1-200-A-T", "1-300-A-T"]
