"""Each import's ClinVar concordance with CoGA's release, and the allele keys it reads.

The import writes, on each ``entries`` row, the allele key of each of the record's ALTs
(``record_allele_keys``): a multi-allelic record stays one row with one key per ALT. It notes
the ClinVar file's values of each allele it stores (the classification, review status and
VariationID of the allele's first annotation; never VEP's cache's), and once the callset is
stored compares them with CoGA's release of the same ClinVar release, when CoGA keeps it, on the
load's own alleles (a load of one sample's file, its sample's): an allele agrees when both name
the same classification terms, review status and VariationID; every other allele either side
classifies is a difference, with its reason. A callset whose release CoGA does not keep, or that
names none, is not compared, and says why; a comparison that fails never fails the import, and
leaves its transaction usable. The package import lifts each load's report into the dataset's
own field, each sample's of a dataset loaded one sample at a time.

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
from backend.app.services.clinvar_releases import ClinvarRelease, annotated_clinvar_release

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
        conditions=(),
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
    assert annotated_clinvar_release(modules) == release


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

    result = cc.concordance(RELEASE, annotated, stored, samples=["PROBAND"])

    assert (result.compared, result.annotated_release, result.agree, result.differ) == (True, RELEASE, 1, 5)
    assert result.samples == ["PROBAND"]
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

    result = cc.concordance(RELEASE, annotated, {}, samples=["PROBAND"])

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
        concordance.compare(
            modules=MODULES,
            kept=_kept(date(2026, 11, 22), date(2026, 11, 15)),
            family_uuid="f",
            source="s",
            samples=["PROBAND"],
        )
    )

    assert not result.compared and result.annotated_release == RELEASE and result.samples == ["PROBAND"]
    assert result.reason == "Not compared: ClinVar 2026-11-01, the annotation's release, is not loaded in CoGA."


def test_an_annotation_without_a_clinvar_release_is_not_compared() -> None:
    result = asyncio.run(
        cc.ClinvarConcordance("GRCh38").compare(
            modules={}, kept=_kept(RELEASE), family_uuid="f", source="s", samples=["PROBAND"]
        )
    )

    assert not result.compared and result.annotated_release is None
    assert result.reason == "Not compared: the annotation names no release of a ClinVar file."


def test_the_kept_release_of_the_annotation_is_compared_on_the_loads_own_alleles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The release is read for the alleles the load stored: those of the callset's rows that
    hold a call of a sample of its file (``integration/test_clinvar_concordance_clickhouse.py``
    reads them on real ClickHouse), so a load of the father's file alone is compared on his."""
    asked: list[tuple[Any, ...]] = []

    async def alleles(
        assembly: str, release: date, family_uuid: str, source: str, samples: list[str]
    ) -> dict[int, ClinvarStoredAllele]:
        asked.append((assembly, release, family_uuid, source, samples))
        return {_key("1-100-G-A"): _stored("1-100-G-A", "Pathogenic")}

    monkeypatch.setattr(cc, "clinvar_alleles_of_callset", alleles)
    concordance = cc.ClinvarConcordance("GRCh38")
    concordance.observe([_record("1-100-G-A", [_clinvar("Pathogenic")])])

    result = asyncio.run(
        concordance.compare(
            modules=MODULES,
            kept=_kept(date(2026, 11, 8), RELEASE),
            family_uuid="f",
            source="nipt",
            samples=["FATHER1"],
        )
    )

    assert asked == [("GRCh38", RELEASE, "f", "nipt", ["FATHER1"])]
    assert (result.compared, result.agree, result.differ, result.samples) == (True, 1, 0, ["FATHER1"])


class _ImportSession:
    """The import's session as the comparison uses it: each savepoint it opens, and how it
    ended."""

    def __init__(self) -> None:
        self.savepoints: list[str] = []

    def begin_nested(self) -> "_Savepoint":
        return _Savepoint(self)


class _Savepoint:
    def __init__(self, session: _ImportSession) -> None:
        self._session = session

    async def __aenter__(self) -> "_Savepoint":
        self._session.savepoints.append("open")
        return self

    async def __aexit__(self, exc_type: Any, *_exc: Any) -> bool:
        self._session.savepoints[-1] = "rolled back" if exc_type else "released"
        return False


def test_a_comparison_that_fails_never_fails_the_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its read of Postgres runs in a savepoint, rolled back when it fails: the import's
    transaction is left usable (``integration/test_clinvar_concordance_clickhouse.py`` shows it
    on real Postgres)."""

    async def failing(_session: Any, _assembly: str) -> list[ClinvarRelease]:
        raise ConnectionError("SECRET-VALUE")

    monkeypatch.setattr(cc, "recorded_clinvar_releases", failing)
    session = _ImportSession()
    reported = asyncio.run(
        cc.ClinvarConcordance("GRCh38").report(
            session,  # type: ignore[arg-type]
            modules=MODULES,
            family_uuid="f",
            source="s",
            samples=["PROBAND"],
        )
    )

    assert reported["compared"] is False and reported["samples"] == ["PROBAND"]
    assert reported["reason"] == "Not compared: the comparison failed (ConnectionError)."
    assert session.savepoints == ["rolled back"]


def test_the_package_import_keeps_the_loaders_report_as_the_datasets_field() -> None:
    reported = cc.concordance(RELEASE, {}, {}, samples=["PROBAND", "MOTHER"]).model_dump(mode="json")
    summary = FamilyImportDatasetSummary(
        dataset_type="snv",
        status="imported",
        summary={"inserted": 3, "annotation_provenance": {"clinvar": {"version": "20261101"}}, cc.CONCORDANCE_KEY: reported},
    )

    lifted = cc.with_clinvar_concordances(summary)

    assert lifted.summary == {"inserted": 3, "annotation_provenance": {"clinvar": {"version": "20261101"}}}
    [concordance] = lifted.clinvar_concordances
    assert concordance.compared and concordance.samples == ["PROBAND", "MOTHER"]
    untouched = FamilyImportDatasetSummary(dataset_type="pedigree", status="imported")
    assert cc.with_clinvar_concordances(untouched) is untouched


def test_a_dataset_loaded_one_sample_at_a_time_keeps_each_samples_report() -> None:
    """The monogenic NIPT and the mtDNA load each sample's file in turn, and hold each load's
    result under the sample's ID: each sample's concordance is the dataset's, in load order."""

    def report(sample: str) -> dict[str, Any]:
        return cc.concordance(RELEASE, {}, {}, samples=[sample]).model_dump(mode="json")

    summary = FamilyImportDatasetSummary(
        dataset_type="snv",
        status="imported",
        summary={
            "CFDNA1": {"inserted": 2, "role": "cfdna", cc.CONCORDANCE_KEY: report("CFDNA1")},
            "FATHER1": {"inserted": 3, "role": "paired_sample", cc.CONCORDANCE_KEY: report("FATHER1")},
            "structural_variants": {"processed": 0},
        },
    )

    lifted = cc.with_clinvar_concordances(summary)

    assert [concordance.samples for concordance in lifted.clinvar_concordances] == [["CFDNA1"], ["FATHER1"]]
    assert lifted.summary == {
        "CFDNA1": {"inserted": 2, "role": "cfdna"},
        "FATHER1": {"inserted": 3, "role": "paired_sample"},
        "structural_variants": {"processed": 0},
    }
