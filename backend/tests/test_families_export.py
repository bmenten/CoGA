from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.routers.families_small_variants import (
    _FAMILY_EXPORT_COLUMNS,
    PRIORITY_EXPORT_COLUMNS,
    _family_export_cell,
)
from backend.app.routers.families_structural_variants import (
    _FAMILY_SV_EXPORT_COLUMNS,
    _family_sv_export_cell,
)
from backend.app.routers.variant_explorer import _EXPORT_COLUMNS as _EXPLORER_EXPORT_COLUMNS
from backend.app.routers.variant_explorer import _export_cell as _explorer_export_cell
from backend.app.schemas import (
    GenotypeOut,
    GlobalVariantRowOut,
    SmallVariantFilterTranscriptOut,
    SmallVariantReviewOut,
    VariantOut,
    VariantPriorityOut,
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
    # Review fields are pulled from the nested review object; the classification as the class
    # the review shows, a class word as its label.
    assert _family_export_cell(row, "classification") == "Pathogenic - class 5"
    # Beside it the classification text as stored.
    assert _family_export_cell(row, "classification_text") == "pathogenic"
    assert _family_export_cell(row, "tags") == "candidate; confirmed"
    # Genotypes are flattened to "sample=gt" pairs.
    assert _family_export_cell(row, "genotypes") == "proband=0/1; mother=0/0"


def test_family_export_cell_handles_missing_review() -> None:
    row = VariantOut(id="1-1-A-T", chr="1", start=1, end=1, length=1, type="SNV")
    assert _family_export_cell(row, "classification") == ""
    assert _family_export_cell(row, "classification_text") == ""
    assert _family_export_cell(row, "tags") == ""
    assert _family_export_cell(row, "genotypes") == ""


def test_family_export_columns_are_resolvable() -> None:
    # Every export field must resolve on a real row without raising, as text. An unknown
    # field reads as an empty cell (getattr's default), so each one must name what a row
    # holds: a field of the variant, of the filter-matched transcript, or a computed one.
    row = _variant()
    computed = {"classification", "classification_text", "tags", "genotypes"}
    for field, label in _FAMILY_EXPORT_COLUMNS:
        assert label.strip()
        assert isinstance(_family_export_cell(row, field), str)
        if field.startswith("filter_transcript."):
            assert field.removeprefix("filter_transcript.") in SmallVariantFilterTranscriptOut.model_fields, field
        else:
            assert field in computed or field in VariantOut.model_fields, field
    # Each column says what it is once.
    labels = [label for _field, label in _FAMILY_EXPORT_COLUMNS]
    assert len(labels) == len(set(labels))


def test_a_prioritized_export_writes_both_scores_and_the_rank() -> None:
    # A ranked file carries the score its order follows, whichever score it was ranked by:
    # ranked by the variant score, the combined score alone would not explain the order.
    row = _variant().model_copy(
        update={
            "priority": VariantPriorityOut(
                combined_score=0.45,
                variant_score=0.9,
                pathogenicity_score=0.9,
                frequency_score=1.0,
                segregation_weight=1.0,
                rank=3,
            )
        }
    )
    cells = {label: _family_export_cell(row, field) for field, label in PRIORITY_EXPORT_COLUMNS}
    assert cells == {"Priority": "0.4500", "Variant score": "0.9000", "Rank": "3"}
    # An unranked row leaves them empty.
    assert {_family_export_cell(_variant(), field) for field, _label in PRIORITY_EXPORT_COLUMNS} == {""}
    # Each says what it is once, and none repeats a column of the rest of the file.
    labels = [label for _field, label in [*PRIORITY_EXPORT_COLUMNS, *_FAMILY_EXPORT_COLUMNS]]
    assert len(labels) == len(set(labels))


def test_family_export_names_the_transcript_the_filters_held_on() -> None:
    # The row describes the variant's primary transcript; when the annotation
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
    # ...and the filter-matched transcript follows, after every other column.
    assert labels[: labels.index("Genotypes") + 1] == [
        "Chromosome", "Start", "End", "Type", "Ref", "Alt", "rsID", "Gene", "Gene ID",
        "Transcript", "Impact", "Effect", "HGVSc", "HGVSp", "ClinVar", "gnomAD AF",
        "gnomAD hom", "CADD", "REVEL", "SpliceAI", "SIFT", "PolyPhen", "Classification",
        "Classification text", "Tags", "Genotypes",
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
    # The file says nothing of pairs, so a variant in two pairs is one row: a second row
    # would only repeat the first.
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


_REVIEW_CLASS_CASES = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/pages/families/__tests__/fixtures/review-class-cases.json"
)


def test_the_export_writes_the_class_each_review_shows_on_the_family_page() -> None:
    # The Classification column held the classification text as stored, where the family
    # page and the Variant Explorer's export show the class by one rule (review_class_label): text
    # "VUS" beside the tag of class 4 exported as "VUS", a review classified by its class tag alone
    # as nothing, and "VUS - class 3" beside the tag of class 4 as class 3 where the page shows 4.
    cases = json.loads(_REVIEW_CLASS_CASES.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 10
    for case in cases:
        row = _variant()
        assert row.review is not None
        row.review.classification = case["classification"]
        row.review.tags = case["tags"]
        assert _family_export_cell(row, "classification") == case["shown"], case["why"]
        # The tags stay as the review holds them.
        assert _family_export_cell(row, "tags") == "; ".join(case["tags"]), case["why"]


def _sv_variant() -> VariantOut:
    return VariantOut(
        id="SV-1-1000-5000-DEL",
        chr="1",
        start=1000,
        end=5000,
        length=-4000,
        type="DEL",
        source="sniffles",
        gene="GENE1",
        genotypes=[GenotypeOut(sample="PROBAND", gt="0/1")],
        review=SmallVariantReviewOut(variant_id="SV-1-1000-5000-DEL"),
    )


@pytest.mark.parametrize(
    ("columns", "export"),
    [
        (_FAMILY_EXPORT_COLUMNS, "small variants"),
        (_FAMILY_SV_EXPORT_COLUMNS, "structural variants"),
        (_EXPLORER_EXPORT_COLUMNS, "Variant Explorer"),
    ],
)
def test_each_export_writes_the_classification_text_next_to_the_class_shown(columns, export) -> None:
    # The owner's decision of 2026-11-10: the Classification column holds the class the review
    # shows, and a Classification text column beside it the classification as
    # written, so the file keeps what the reviewer typed where the rule shows another class.
    labels = [label for _field, label in columns]
    assert labels.count("Classification") == 1, export
    assert labels.count("Classification text") == 1, export
    at = labels.index("Classification")
    assert labels[at : at + 3] == ["Classification", "Classification text", "Tags"], export
    assert dict(columns)["classification_text"] == "Classification text", export


def test_the_family_exports_write_the_class_shown_and_the_text_as_stored() -> None:
    # The small-variant and the SV export (which shares its review cells) write, for every
    # case of the shared review-class-cases.json, the class shown under Classification and the
    # text as stored under Classification text: "VUS" beside the tag of class 4 is "VUS - class 3"
    # and "VUS", a review classified by its class tag alone the tag's class and nothing.
    cases = json.loads(_REVIEW_CLASS_CASES.read_text(encoding="utf-8"))["cases"]
    for case in cases:
        for row, cell in ((_variant(), _family_export_cell), (_sv_variant(), _family_sv_export_cell)):
            assert row.review is not None
            row.review.classification = case["classification"]
            row.review.tags = case["tags"]
            assert cell(row, "classification") == case["shown"], case["why"]
            assert cell(row, "classification_text") == (case["classification"] or ""), case["why"]
            assert cell(row, "tags") == "; ".join(case["tags"]), case["why"]
    # An SV without a review writes neither.
    bare = _sv_variant()
    bare.review = None
    assert _family_sv_export_cell(bare, "classification") == ""
    assert _family_sv_export_cell(bare, "classification_text") == ""


def test_the_explorer_export_writes_the_class_shown_and_its_text() -> None:
    # The Explorer row carries the class shown (the most severe its reviews show) and the
    # text of the review that shows it; the export writes both, each in its column.
    row = GlobalVariantRowOut(
        key="1",
        variant_id="1-100-A-T",
        chr="1",
        pos=100,
        classification="VUS - class 3",
        classification_text="VUS",
        tags=["acmg_class_4"],
    )
    assert _explorer_export_cell(row, "classification") == "VUS - class 3"
    assert _explorer_export_cell(row, "classification_text") == "VUS"
    assert _explorer_export_cell(row.model_copy(update={"classification_text": None}), "classification_text") == ""
