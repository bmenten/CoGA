"""Every reader of a small variant's ClinVar reads CoGA's release alone (the owner's decision of
2026-11-27): an allele's classification is the release's where it holds the allele, none where
it does not (or holds it only as part of a combination), whatever the annotation's ClinVar says.
Here the ranking (scored for the ALT the variant is shown with), the mtDNA status (beside
MITOMAP's), the monogenic NIPT's readers (de novo triage, artefact protection, the diagnostic
candidates' strict call) and the artefact list's protection. Without a release no variant has
ClinVar.

The filters' SQL is in ``test_clinvar_coga.py``. ClickHouse and the release are stood in for.
Synthetic alleles only.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from backend.app.schemas import ClinvarCogaOut
from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services.analysis_scope import WHOLE_FAMILY_SCOPE
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.clinvar_coga import clinvar_classification, clinvar_coga_of
from backend.app.services.family_metadata_context import FamilyMetadataContext

RELEASE = date(2026, 11, 22)


def _value(alt: str, status: str = "in_release", classification: str | None = "Pathogenic") -> ClinvarCogaOut:
    return ClinvarCogaOut(alt=alt, status=status, release=RELEASE, classification=classification)  # type: ignore[arg-type]


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="F1",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_uuid": "uuid-S1", "sample_id": "S1", "role": "proband", "affected": True}],
        sample_uuid_to_name={"uuid-S1": "S1"},
        sample_name_to_uuid={"S1": "uuid-S1"},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        scope=WHOLE_FAMILY_SCOPE,
    )


# --- an allele's classification ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("coga", "read"),
    [
        (_value("A"), "Pathogenic"),
        (_value("A", classification="Benign"), "Benign"),
        # Held only as part of a combination: no classification there, none read.
        (_value("A", classification=None), None),
        (_value("A", status="not_in_release", classification=None), None),
        (_value("A", status="no_release", classification=None), None),
        (_value("A", status="not_available", classification=None), None),
        (None, None),
    ],
    ids=["pathogenic", "benign", "combination-only", "not-in-release", "no-release", "not-available", "no-value"],
)
def test_an_alleles_classification_is_the_releases_or_none(coga: ClinvarCogaOut | None, read: str | None) -> None:
    assert clinvar_classification(coga) == read


def test_the_value_read_is_the_shown_alts() -> None:
    values = [_value("A"), _value("AT", classification="Benign")]

    assert clinvar_coga_of("A,AT", "AT", values) is values[1]
    assert clinvar_coga_of("A", None, values[:1]) is values[0]
    assert clinvar_coga_of("A,AT", None, values) is None


# --- the ranking --------------------------------------------------------------------------------


def _record(variant_id: str, clinvar: str) -> SmallVariantRecord:
    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=int(pos),
        end=int(pos),
        ref=ref,
        alt=alt,
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=["SYNG1"],
        annotations=[{"gene": "SYNG1", "impact": "LOW", "clinvar": clinvar}],
        calls=[SmallVariantCall(sample="S1", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
    )


def test_the_ranking_scores_the_releases_clinvar_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each record's annotation names a ClinVar classification the ranking does not read.
    records = [
        _record("1-100-G-A", "Benign"),  # CoGA: pathogenic
        _record("1-200-G-A", "Pathogenic"),  # CoGA: benign
        _record("1-300-G-A", "Pathogenic"),  # not in CoGA's release: no ClinVar
    ]
    coga = {
        100: [_value("A")],
        200: [_value("A", classification="Benign")],
        300: [_value("A", status="not_in_release", classification=None)],
    }
    asked: list[Any] = []

    async def values(read: Any, loci: Any) -> list[list[ClinvarCogaOut]]:
        asked.append(read.release)
        return [coga[pos] for _chrom, pos, _ref, _alt in loci]

    async def no_constraint(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(cfv, "clinvar_coga_values", values)
    monkeypatch.setattr(cfv, "_fetch_gene_constraint_metric_map", no_constraint)

    def scores(release: date | None) -> dict[str, float]:
        ranked = asyncio.run(
            cfv._rank_small_variants(
                object(),  # type: ignore[arg-type]
                context=_context(),
                records=records,
                segregation_modes={},
                patient_terms=[],
                term_labels={},
                clinvar_coga_release=release,
            )
        )
        return {variant_id: priority.pathogenicity_score for variant_id, priority in ranked.items()}

    unclassified = scores(None)["1-300-G-A"]
    assert unclassified not in {1.0, 0.05}
    assert scores(RELEASE) == {"1-100-G-A": 1.0, "1-200-G-A": 0.05, "1-300-G-A": unclassified}
    # Without a release no variant has ClinVar, and nothing is looked up.
    assert scores(None) == dict.fromkeys(["1-100-G-A", "1-200-G-A", "1-300-G-A"], unclassified)
    assert asked == [RELEASE]


# --- the mtDNA status --------------------------------------------------------------------------


def _mt_record(clinvar: str, mitomap: str) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id="chrM-3243-A-G",
        chr="chrM",
        start=3243,
        end=3243,
        ref="A",
        alt="G",
        source="mutserve",
        rsid=None,
        filters=[],
        gene_symbols=["MT-TL1"],
        annotations=[{"gene": "MT-TL1", "clinvar": clinvar, "mitomap_status": mitomap}],
        calls=[],
    )


@pytest.mark.parametrize(
    ("coga", "status"),
    [
        (_value("G", classification="Benign"), "benign"),
        (_value("G", classification="Likely_pathogenic"), "likely_pathogenic"),
        # Without CoGA's ClinVar, MITOMAP's status alone; the annotation's "Pathogenic" unread.
        (_value("G", status="not_in_release", classification=None), "reported"),
        (None, "reported"),
    ],
    ids=["benign", "likely-pathogenic", "not-in-release", "no-release"],
)
def test_the_mtdna_status_reads_the_releases_clinvar_beside_mitomap(coga: ClinvarCogaOut | None, status: str) -> None:
    from backend.app.services import mitochondrial_analysis as mito

    annotation = mito._annotation_for_record(_mt_record("Pathogenic", "Reported"), clinvar_coga=coga)

    assert (annotation.clinical_significance, annotation.clinvar_coga) == (status, coga)
    # The annotation's ClinVar is no source the variant's annotation names.
    assert annotation.source_keys == ["mitomap_status"]


def test_the_mtdna_page_reads_each_variants_value_in_the_release(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.services import mitochondrial_analysis as mito
    from backend.app.services.clinvar_coga import ClinvarCogaRead

    async def records(_context: Any) -> list[SmallVariantRecord]:
        return [_mt_record("Pathogenic", "")]

    async def release(_session: Any, assembly: Any) -> ClinvarCogaRead:
        return ClinvarCogaRead("GRCh38", RELEASE, True)

    async def values(read: Any, loci: Any) -> list[list[ClinvarCogaOut]]:
        assert [locus[:2] for locus in loci] == [("chrM", 3243)]
        return [[_value("G", classification="Likely_benign")]]

    async def nothing(*_args: Any, **_kwargs: Any) -> Any:
        return {}

    async def no_thresholds(_session: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"profiles": [], "thresholds": {}}

    async def no_reviews(_session: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(mito, "_fetch_mt_records", records)
    monkeypatch.setattr(mito, "clinvar_coga_read", release)
    monkeypatch.setattr(mito, "clinvar_coga_values", values)
    monkeypatch.setattr(mito, "_coverage_by_sample", lambda *_a, **_k: _coverage())
    monkeypatch.setattr(mito, "resolve_analysis_qc_thresholds", no_thresholds)
    monkeypatch.setattr(mito, "_attach_reviews", no_reviews)
    monkeypatch.setattr(mito, "_fetch_mt_structural_variants", lambda _context: _no_svs())

    response = asyncio.run(mito.get_family_mitochondrial_analysis_response(object(), context=_context()))  # type: ignore[arg-type]

    [variant] = response.variants
    assert variant.annotation.clinical_significance == "likely_benign"
    assert variant.annotation.clinvar_coga is not None and variant.annotation.clinvar_coga.release == RELEASE


async def _coverage() -> tuple[dict[str, Any], list[str]]:
    return {}, []


async def _no_svs() -> list[Any]:
    return []


# --- the monogenic NIPT ------------------------------------------------------------------------


def _nipt_record(clinvar: str) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id="7-100-G-A",
        chr="7",
        start=100,
        end=100,
        ref="G",
        alt="A",
        source="mutect2",
        rsid=None,
        filters=[],
        gene_symbols=["SYNG7"],
        annotations=[{"gene": "SYNG7", "impact": "MODERATE", "clinvar": clinvar}],
        calls=[],
    )


@pytest.mark.parametrize(
    ("annotated", "coga", "read", "may_assert", "protection"),
    [
        ("Benign", _value("A"), ["Pathogenic"], True, "clinvar"),
        ("Pathogenic", _value("A", classification="Benign"), ["Benign"], False, None),
        (
            "Benign",
            _value("A", classification="Conflicting_classifications_of_pathogenicity"),
            ["Conflicting_classifications_of_pathogenicity"],
            True,
            "clinvar",
        ),
        ("Pathogenic", _value("A", status="not_in_release", classification=None), [], False, None),
        ("Pathogenic", None, [], False, None),
    ],
    ids=["coga-pathogenic", "coga-benign", "coga-conflict", "not-in-release", "no-release"],
)
def test_the_nipt_readers_read_the_releases_clinvar_alone(
    annotated: str, coga: ClinvarCogaOut | None, read: list[str], may_assert: bool, protection: str | None
) -> None:
    from backend.app.services import nipt_service

    record = _nipt_record(annotated)

    assert nipt_service.clinvar_read(coga) == read
    assert nipt_service.triage_annotation(record, coga).clinvar_pathogenic is may_assert
    assert nipt_service.artifact_protection(record, coga) == protection


def test_the_diagnostic_candidates_read_a_strict_pathogenic_classification_in_the_release() -> None:
    from backend.app.services import nipt_service

    def strict(annotated: str, coga: ClinvarCogaOut | None) -> bool:
        item = nipt_service.NiptClassifiedVariant(
            record=_nipt_record(annotated), classification=None, clinvar_coga=coga  # type: ignore[arg-type]
        )
        return nipt_service.diagnostic_annotation(item).clinvar_pathogenic

    assert strict("Benign", _value("A", classification="Likely_pathogenic")) is True
    assert strict("Pathogenic", _value("A", classification="Uncertain_significance")) is False
    # A conflicting record may assert pathogenic, but is not strictly pathogenic.
    assert strict("Benign", _value("A", classification="Conflicting_classifications_of_pathogenicity")) is False
    # The annotation's pathogenic call alone is none.
    assert strict("Pathogenic", None) is False
    assert strict("Pathogenic", _value("A", status="not_in_release", classification=None)) is False


def test_the_artifact_lists_protection_reads_the_release_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.services.clinvar_coga import ClinvarCogaRead

    asked: list[Any] = []

    async def values(read: Any, variant_ids: Any) -> dict[str, ClinvarCogaOut]:
        asked.append((read.release, list(variant_ids)))
        # 1-100 pathogenic, 1-200 benign, 1-500 conflicting; 1-300 not held; 1-400 held only
        # in a combination.
        return {
            "1-100-G-A": _value("A"),
            "1-200-G-A": _value("A", classification="Benign"),
            "1-300-G-A": _value("A", status="not_in_release", classification=None),
            "1-400-G-A": _value("A", classification=None),
            "1-500-G-A": _value("A", classification="Conflicting_classifications_of_pathogenicity"),
        }

    monkeypatch.setattr(cfv, "clinvar_coga_by_variant_id", values)
    variant_ids = ["1-100-G-A", "1-200-G-A", "1-300-G-A", "1-400-G-A", "1-500-G-A", "1-600-G-A"]

    protected = asyncio.run(cfv._clinvar_protected(ClinvarCogaRead("GRCh38", RELEASE, True), variant_ids))

    assert protected == {"1-100-G-A", "1-500-G-A"}
    assert asked == [(RELEASE, variant_ids)]


def test_a_variant_id_names_its_locus_unless_a_dash_makes_it_ambiguous() -> None:
    from backend.app.services.clinvar_coga import locus_of_variant_id

    assert locus_of_variant_id("chrM-3243-A-G") == ("chrM", 3243, "A", "G")
    assert locus_of_variant_id("HLA-A*01:01-100-A-G") is None
    assert locus_of_variant_id("1-x-A-G") is None
