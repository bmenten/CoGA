"""The design's one rule for the ClinVar readers of phase 2 (B8): CoGA's ClinVar release where it
holds the allele, else the annotation. Here the *ClinVar status* filter, its exclusion and the
ranking.

A row CoGA's release of the request holds whole is kept, or left out, by the statuses there
alone; any other row by its annotation, as without a release. A multi-allelic row the release
holds in part is read both ways, but its annotation leaves it out only when the release holds
none of its alleles (likely) pathogenic: the annotation does not say which allele an excluded
status is of. The ranking scores each variant's ClinVar by the same rule, for
the ALT the variant is shown with. Without a release nothing changes.

ClickHouse and the release are stood in for; ``integration/test_clinvar_coga_filter_clickhouse.py``
runs the filters on real ClickHouse. Synthetic alleles only.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from backend.app.schemas import ClinvarCogaOut
from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services.analysis_scope import WHOLE_FAMILY_SCOPE
from backend.app.services.clickhouse_variant_queries import _small_annotation_filter_clauses
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.clinvar_coga import clinvar_by_the_rule, clinvar_coga_of
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

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


# --- the rule ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("annotated", "coga", "read"),
    [
        ("Benign", _value("A"), "Pathogenic"),
        ("Pathogenic", _value("A", classification="Benign"), "Benign"),
        # Held only as part of a combination: no classification there, none read.
        ("Pathogenic", _value("A", classification=None), None),
        ("Pathogenic", _value("A", status="not_in_release", classification=None), "Pathogenic"),
        ("Pathogenic", _value("A", status="no_release", classification=None), "Pathogenic"),
        ("Pathogenic", None, "Pathogenic"),
    ],
    ids=["coga", "coga-benign", "combination-only", "not-in-release", "no-release", "no-value"],
)
def test_coga_where_it_holds_the_allele_else_the_annotation(
    annotated: str, coga: ClinvarCogaOut | None, read: str | None
) -> None:
    assert clinvar_by_the_rule(annotated, coga) == read


def test_the_value_read_is_the_shown_alts() -> None:
    values = [_value("A"), _value("AT", classification="Benign")]

    assert clinvar_coga_of("A,AT", "AT", values) is values[1]
    assert clinvar_coga_of("A", None, values[:1]) is values[0]
    assert clinvar_coga_of("A,AT", None, values) is None


# --- the filters, in SQL -----------------------------------------------------------------------


def _clauses(**values: Any) -> tuple[list[str], dict[str, Any]]:
    params: dict[str, Any] = {}
    filters = SmallVariantQueryFilters(page=1, page_size=10, **values)
    return _small_annotation_filter_clauses(_context(), filters, params=params), params


def test_the_clinvar_filter_reads_the_release_for_a_row_it_holds_else_the_annotation() -> None:
    [clause], params = _clauses(clinvar=["Pathogenic"], impact=["HIGH"], clinvar_coga_release=RELEASE)

    coga, annotated = clause.split(") OR (NOT ", 1)
    # Held: the release's statuses, and the other transcript filters without ClinVar's.
    assert coga.startswith("((arrayExists(k -> k IN (SELECT allele_key FROM ") and "`GRCh38/SNV_INDEL/clinvar`" in coga
    assert "hasAny(a.clinvar_terms" not in coga and "a.impact IN" in coga
    # Not held whole: the annotation, every transcript filter together.
    assert annotated.startswith("(notEmpty(e.allele_keys) AND arrayAll(k -> k IN")
    assert "hasAny(a.clinvar_terms, %(detail_clinvar_terms)s)" in annotated
    assert params["coga_clinvar_terms"] == ["pathogenic"] and params["coga_held_release"] == RELEASE


def test_the_clinvar_filter_alone_asks_nothing_else_of_a_held_row() -> None:
    [clause], _params = _clauses(clinvar=["Pathogenic"], clinvar_coga_release=RELEASE)

    assert clause.startswith("((arrayExists(k -> k IN (SELECT allele_key FROM ")
    assert clause.split(") OR (NOT ", 1)[0].endswith("e.allele_keys)")


def test_the_excluded_statuses_read_the_release_for_a_row_it_holds_else_the_annotation() -> None:
    [clause], params = _clauses(exclude_clinvar=["Benign"], clinvar_coga_release=RELEASE)

    # No allele the release excludes; and the row held whole, or one of its alleles (likely)
    # pathogenic there, or no transcript of its annotation excluded.
    excluded, kept = clause.split(" AND ((notEmpty(e.allele_keys) AND arrayAll(", 1)
    assert excluded.startswith("(NOT arrayExists(k -> k IN (SELECT allele_key FROM ")
    assert "%(coga_exclude_terms)s" in excluded
    held, pathogenic, annotated = kept.split(" OR ", 2)
    assert "%(coga_held_release)s" in held
    assert "%(coga_exclude_kept_terms)s" in pathogenic and pathogenic.startswith("arrayExists(k -> k IN")
    assert annotated.startswith("NOT arrayExists(a -> (hasAny(a.clinvar_terms") and clause.endswith(")))")
    assert params["coga_exclude_terms"] == ["benign"]
    assert params["coga_exclude_kept_terms"] == ["pathogenic", "likely pathogenic"]


@pytest.mark.parametrize("values", [{"clinvar": ["Pathogenic"]}, {"exclude_clinvar": ["Benign"]}])
def test_without_a_release_the_filters_read_the_annotation(values: dict[str, Any]) -> None:
    [clause], _params = _clauses(**values)

    assert "SNV_INDEL/clinvar" not in clause


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


def test_the_ranking_scores_clinvar_by_the_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        _record("1-100-G-A", "Benign"),  # CoGA: pathogenic
        _record("1-200-G-A", "Pathogenic"),  # CoGA: benign
        _record("1-300-G-A", "Pathogenic"),  # not in CoGA's release
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

    assert scores(RELEASE) == {"1-100-G-A": 1.0, "1-200-G-A": 0.05, "1-300-G-A": 1.0}
    # Without a release: the annotation, and no lookup.
    assert scores(None) == {"1-100-G-A": 0.05, "1-200-G-A": 1.0, "1-300-G-A": 1.0}
    assert asked == [RELEASE]


# --- the mtDNA status (B12) ----------------------------------------------------------------------


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
        (_value("G", status="not_in_release", classification=None), "pathogenic"),
        (None, "pathogenic"),
    ],
    ids=["coga", "not-in-release", "no-release"],
)
def test_the_mtdna_status_reads_clinvar_by_the_rule_beside_mitomap(coga: ClinvarCogaOut | None, status: str) -> None:
    from backend.app.services import mitochondrial_analysis as mito

    annotation = mito._annotation_for_record(_mt_record("Pathogenic", "Reported"), clinvar_coga=coga)

    assert (annotation.clinical_significance, annotation.clinvar_coga) == (status, coga)


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


# --- the monogenic NIPT (B9, B11) ----------------------------------------------------------------


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
    ("annotated", "coga", "may_assert", "protection"),
    [
        ("Benign", _value("A"), True, "clinvar"),
        ("Pathogenic", _value("A", classification="Benign"), False, None),
        ("Benign", _value("A", classification="Conflicting_classifications_of_pathogenicity"), True, "clinvar"),
        ("Pathogenic", _value("A", status="not_in_release", classification=None), True, "clinvar"),
        ("Pathogenic", None, True, "clinvar"),
    ],
    ids=["coga-pathogenic", "coga-benign", "coga-conflict", "not-in-release", "no-release"],
)
def test_the_nipt_readers_read_clinvar_by_the_rule(
    annotated: str, coga: ClinvarCogaOut | None, may_assert: bool, protection: str | None
) -> None:
    from backend.app.services import nipt_service

    record = _nipt_record(annotated)

    assert nipt_service.triage_annotation(record, coga).clinvar_pathogenic is may_assert
    assert nipt_service.artifact_protection(record, coga) == protection


def test_the_diagnostic_candidates_read_a_strict_pathogenic_classification_by_the_rule() -> None:
    from backend.app.services import nipt_service

    def strict(annotated: str, coga: ClinvarCogaOut | None) -> bool:
        item = nipt_service.NiptClassifiedVariant(
            record=_nipt_record(annotated),
            classification=None,  # type: ignore[arg-type]
            clinvar_coga=coga,
            recurrence=nipt_service.NiptRecurrence(
                other_samples=0,
                counts=nipt_service.CfdnaRecurrence(),
                protection=None,
                plasma_calls=True,
                father_carries=False,
            ),
        )
        return nipt_service.diagnostic_annotation(item).clinvar_pathogenic

    assert strict("Benign", _value("A", classification="Likely_pathogenic")) is True
    assert strict("Pathogenic", _value("A", classification="Uncertain_significance")) is False
    # A conflicting record may assert pathogenic, but is not strictly pathogenic.
    assert strict("Benign", _value("A", classification="Conflicting_classifications_of_pathogenicity")) is False
    assert strict("Pathogenic", None) is True


def test_the_artifact_lists_protection_reads_the_release_for_the_alleles_it_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.services.clinvar_coga import ClinvarCogaRead

    pooled = {
        "1-100-G-A": cfv._PooledAnnotations(population_af=0.0, clinvar_terms=["benign"]),
        "1-200-G-A": cfv._PooledAnnotations(population_af=0.0, clinvar_terms=["pathogenic"]),
        "1-300-G-A": cfv._PooledAnnotations(population_af=0.0, clinvar_terms=["pathogenic"]),
    }

    async def values(_read: Any, variant_ids: Any) -> dict[str, ClinvarCogaOut]:
        # 1-100 newly pathogenic, 1-200 now benign; 1-300 not held, and 1-400 held by no family.
        return {
            "1-100-G-A": _value("A"),
            "1-200-G-A": _value("A", classification="Benign"),
            "1-400-G-A": _value("A", classification="Likely_pathogenic"),
        }

    monkeypatch.setattr(cfv, "clinvar_coga_by_variant_id", values)

    protected = asyncio.run(
        cfv._clinvar_protected(
            ClinvarCogaRead("GRCh38", RELEASE, True), ["1-100-G-A", "1-200-G-A", "1-300-G-A", "1-400-G-A"], pooled
        )
    )

    assert protected == {"1-100-G-A", "1-300-G-A", "1-400-G-A"}


def test_a_variant_id_names_its_locus_unless_a_dash_makes_it_ambiguous() -> None:
    from backend.app.services.clinvar_coga import locus_of_variant_id

    assert locus_of_variant_id("chrM-3243-A-G") == ("chrM", 3243, "A", "G")
    assert locus_of_variant_id("HLA-A*01:01-100-A-G") is None
    assert locus_of_variant_id("1-x-A-G") is None
