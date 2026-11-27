"""Each import's ClinVar concordance with CoGA's release, and the allele keys it reads.

The import writes, on each ``entries`` row, the allele key of each of the record's ALTs
(``record_allele_keys``): a multi-allelic record stays one row with one key per ALT. It notes
the ClinVar file's values of each allele it stores (the classification, review status and
VariationID of the allele's first annotation; never VEP's cache's), and once the callset is
stored compares them with CoGA's release of the same ClinVar release, when CoGA keeps it: an
allele agrees when both name the same classification terms, review status and VariationID;
every other allele either side classifies is a difference, with its reason. A callset whose
release CoGA does not keep, or that names none, is not compared, and says why; a comparison
that fails never fails the import. The package import lifts the loader's report into the
dataset's own field.

Synthetic alleles only; ``integration/test_clinvar_concordance_clickhouse.py`` reads the
release and the stored keys on real ClickHouse.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any

import pytest

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import clinvar_concordance as cc
from backend.app.services.clickhouse_variant_ids import record_allele_keys, small_allele_key
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.clickhouse_variant_rows import _small_variant_entry_rows
from backend.app.services.clickhouse_variant_storage import SMALL_VARIANT_ENTRY_COLUMNS
from backend.app.services.clinvar_release_store import ClinvarStoredAllele
from backend.app.services.clinvar_releases import ClinvarRelease

RELEASE = date(2026, 11, 1)
MODULES = {"clinvar": {"version": "20261101", "detail": "ClinVar file clinvar_20261101.vcf.gz"}}


def _record(variant_id: str, annotations: list[dict[str, Any]]) -> SmallVariantRecord:
    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=int(pos),
        end=int(pos),
        ref=ref,
        alt=alt,
        source="haplotypecaller",
        rsid=None,
        filters=[],
        gene_symbols=[],
        annotations=annotations,
        calls=[SmallVariantCall(sample="PROBAND", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
    )


def _clinvar(clinvar: str, status: str = "criteria provided, single submitter", vid: str = "900001", **more: Any) -> dict[str, Any]:
    return {"clinvar": clinvar, "clinvar_review_status": status, "clinvar_variation_id": vid, **more}


def _stored(allele: str, classification: str, status: str = "criteria provided, single submitter", vid: int = 900001) -> ClinvarStoredAllele:
    from backend.app.services.clickhouse_variant_records import _status_terms

    return ClinvarStoredAllele(
        variant_id=allele,
        classification=classification,
        classification_terms=tuple(sorted(_status_terms(classification))),
        review_status=status,
        variation_id=vid,
        included_only=False,
    )


def _key(allele: str) -> int:
    return small_allele_key("GRCh38", allele)


# --- the allele keys an import writes ----------------------------------------------------------


def test_each_entries_row_holds_the_allele_key_of_each_of_its_alts() -> None:
    record = _record("chr1-100-AT-A,ATT", [])

    _details, entries, *_rest = _small_variant_entry_rows("GRCh38", "family-1", ["project-1"], [record])

    [row] = entries
    stored = dict(zip(SMALL_VARIANT_ENTRY_COLUMNS, row))
    assert stored["alt"] == "A,ATT"
    assert stored["allele_keys"] == [_key("1-100-AT-A"), _key("1-100-A-AT")]
    assert stored["allele_keys"] == record_allele_keys("GRCh38", "chr1", 100, "AT", "A,ATT")


def test_an_alt_that_names_no_allele_has_no_key_on_its_row() -> None:
    assert record_allele_keys("GRCh38", "1", 100, "A", "G,*") == [_key("1-100-A-G")]
    assert record_allele_keys("GRCh38", "1", 100, "A", "<DEL>") == []


# --- what an import notes ----------------------------------------------------------------------


def test_the_clinvar_files_values_of_each_allele_are_noted_once() -> None:
    concordance = cc.ClinvarConcordance("GRCh38")
    concordance.observe(
        [
            _record("1-100-G-A", [_clinvar("Pathogenic"), _clinvar("Pathogenic", gene="OTHER")]),
            # A multi-allelic record: each ALT's own first annotation.
            _record(
                "2-200-AT-A,ATT",
                [
                    _clinvar("Benign", alt_allele="A", vid="900002"),
                    _clinvar("Likely_pathogenic", alt_allele="ATT", vid="900003"),
                    _clinvar("Uncertain_significance", alt_allele="ATT", vid="900004"),
                ],
            ),
            # VEP's cache: ClinVar at rsID level, not compared.
            _record("3-300-C-T", [_clinvar("Pathogenic", clinvar_from_vep_cache=True)]),
            _record("4-400-C-T", [{"gene": "SYNG4"}]),
        ]
    )

    annotated = concordance._annotated
    assert set(annotated) == {_key("1-100-G-A"), _key("2-200-AT-A"), _key("2-200-A-AT")}
    assert annotated[_key("2-200-A-AT")].terms == frozenset({"likely pathogenic"})
    assert annotated[_key("2-200-A-AT")].variation_id == 900003
    assert annotated[_key("2-200-A-AT")].variant_id == "2-200-AT-A,ATT"


@pytest.mark.parametrize(
    ("modules", "release"),
    [
        (MODULES, RELEASE),
        ({"clinvar": {"version": "2026-11-01"}}, RELEASE),
        ({"clinvar": {"detail": "ClinVar file; the header names none"}}, None),
        ({"clinvar": {"version": "v2"}}, None),
        ({"clinvar": {"version": "20261341"}}, None),
        ({}, None),
    ],
)
def test_the_annotations_release_is_its_clinvar_modules_date(modules: dict, release: date | None) -> None:
    assert cc.annotated_clinvar_release(modules) == release


# --- the comparison ----------------------------------------------------------------------------


def _annotated(*records: SmallVariantRecord) -> Any:
    concordance = cc.ClinvarConcordance("GRCh38")
    concordance.observe(records)
    return concordance._annotated


def test_alleles_agree_and_each_difference_names_its_reason() -> None:
    annotated = _annotated(
        _record("1-100-G-A", [_clinvar("Pathogenic/Likely_pathogenic")]),
        _record("1-200-G-A", [_clinvar("Benign")]),
        _record("1-300-G-A", [_clinvar("Pathogenic", status="reviewed by expert panel")]),
        _record("1-400-G-A", [_clinvar("Pathogenic", vid="1")]),
        _record("1-500-GTT-GT", [_clinvar("Likely_benign")]),
    )
    stored = {
        _key("1-100-G-A"): _stored("1-100-G-A", "Likely_pathogenic/Pathogenic"),
        _key("1-200-G-A"): _stored("1-200-G-A", "Likely_benign"),
        _key("1-300-G-A"): _stored("1-300-G-A", "Pathogenic"),
        _key("1-400-G-A"): _stored("1-400-G-A", "Pathogenic"),
        # Only in CoGA's release; one it holds without a classification is no difference.
        _key("1-600-C-T"): _stored("1-600-C-T", "Pathogenic"),
        _key("1-700-C-T"): _stored("1-700-C-T", ""),
    }

    result = cc.concordance(RELEASE, annotated, stored)

    assert (result.compared, result.annotated_release, result.agree, result.differ) == (True, RELEASE, 1, 5)
    assert [(d.variant_id, d.allele, d.reason) for d in result.differences] == [
        (None, "1-600-C-T", "not in the annotation"),
        ("1-200-G-A", "1-200-G-A", "classification"),
        ("1-300-G-A", "1-300-G-A", "review status"),
        ("1-400-G-A", "1-400-G-A", "VariationID"),
        ("1-500-GTT-GT", "1-500-GT-G", "not in CoGA's release"),
    ]
    classification = result.differences[1]
    assert classification.annotated == "Benign; criteria provided, single submitter; VariationID 900001"
    assert classification.coga == "Likely benign; criteria provided, single submitter; VariationID 900001"


def test_the_differences_kept_are_capped_and_counted_whole() -> None:
    annotated = _annotated(*(_record(f"1-{1000 + index}-G-A", [_clinvar("Benign")]) for index in range(150)))

    result = cc.concordance(RELEASE, annotated, {})

    assert result.differ == 150 and len(result.differences) == cc.MAX_DIFFERENCES


def _kept(*days: date) -> list[ClinvarRelease]:
    return [
        ClinvarRelease(
            release=day,
            records=8,
            source_url=None,
            source_version=day.isoformat(),
            loaded_at=datetime(2026, 11, 27, tzinfo=timezone.utc),
            loaded_by=None,
        )
        for day in days
    ]


def test_a_release_coga_does_not_keep_is_not_compared() -> None:
    concordance = cc.ClinvarConcordance("GRCh38")

    result = asyncio.run(
        concordance.compare(modules=MODULES, kept=_kept(date(2026, 11, 22), date(2026, 11, 15)), family_uuid="f", source="s")
    )

    assert not result.compared and result.annotated_release == RELEASE
    assert result.reason == "Not compared: ClinVar 2026-11-01, the annotation's release, is not loaded in CoGA."


def test_an_annotation_without_a_clinvar_release_is_not_compared() -> None:
    result = asyncio.run(cc.ClinvarConcordance("GRCh38").compare(modules={}, kept=_kept(RELEASE), family_uuid="f", source="s"))

    assert not result.compared and result.annotated_release is None
    assert result.reason == "Not compared: the annotation names no release of a ClinVar file."


def test_the_kept_release_of_the_annotation_is_compared_on_the_callsets_alleles(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[Any, ...]] = []

    async def alleles(assembly: str, release: date, family_uuid: str, source: str) -> dict[int, ClinvarStoredAllele]:
        asked.append((assembly, release, family_uuid, source))
        return {_key("1-100-G-A"): _stored("1-100-G-A", "Pathogenic")}

    monkeypatch.setattr(cc, "clinvar_alleles_of_callset", alleles)
    concordance = cc.ClinvarConcordance("GRCh38")
    concordance.observe([_record("1-100-G-A", [_clinvar("Pathogenic")])])

    result = asyncio.run(
        concordance.compare(modules=MODULES, kept=_kept(date(2026, 11, 8), RELEASE), family_uuid="f", source="haplotypecaller")
    )

    assert asked == [("GRCh38", RELEASE, "f", "haplotypecaller")]
    assert (result.compared, result.agree, result.differ) == (True, 1, 0)


def test_a_comparison_that_fails_never_fails_the_import(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(_session: Any, _assembly: str) -> list[ClinvarRelease]:
        raise ConnectionError("SECRET-VALUE")

    monkeypatch.setattr(cc, "recorded_clinvar_releases", failing)
    reported = asyncio.run(
        cc.ClinvarConcordance("GRCh38").report(object(), modules=MODULES, family_uuid="f", source="s")  # type: ignore[arg-type]
    )

    assert reported["compared"] is False
    assert reported["reason"] == "Not compared: the comparison failed (ConnectionError)."


def test_the_package_import_keeps_the_loaders_report_as_the_datasets_field() -> None:
    reported = cc.concordance(RELEASE, {}, {}).model_dump(mode="json")
    summary = FamilyImportDatasetSummary(
        dataset_type="snv", status="imported", summary={"inserted": 3, cc.CONCORDANCE_KEY: reported}
    )

    lifted = cc.with_clinvar_concordance(summary)

    assert lifted.summary == {"inserted": 3}
    assert lifted.clinvar_concordance is not None and lifted.clinvar_concordance.compared
    untouched = FamilyImportDatasetSummary(dataset_type="pedigree", status="imported")
    assert cc.with_clinvar_concordance(untouched) is untouched
