"""*ClinVar (CoGA)*: each small variant's ClinVar in CoGA's own release, the filter on it, and
the P/LP override's reading of it.

A request reads the assembly's current release once and serves it with its page; each ALT of a
variant gets its value from that release, by its allele key, or the reason it has none (no
ClinVar VCF for the assembly, no release loaded, not in the release). The *ClinVar (CoGA)*
filter keeps a row one of whose alleles the release classifies with a term asked for; without a
release it keeps none. With the ClinVar P/LP override, an allele the release holds (likely)
pathogenic lifts the consequence, in-silico and frequency filters on every transcript of the
row, as the annotation's classification lifts them on its transcript; the other transcript
filters still hold, and without a release, or without the override, nothing changes.

The release, the page's rows and ClickHouse are stood in for;
``integration/test_clinvar_coga_filter_clickhouse.py`` runs the conditions on real ClickHouse.
Synthetic alleles only.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from backend.app.schemas import VariantOut, VariantPage
from backend.app.services import clinvar_coga as coga
from backend.app.services.analysis_scope import WHOLE_FAMILY_SCOPE
from backend.app.services.clickhouse_variant_ids import small_allele_key
from backend.app.services.clickhouse_variant_queries import (
    _clinvar_coga_lifted,
    _small_annotation_filter_clauses,
    _small_annotation_filter_condition,
)
from backend.app.services.clinvar_release_store import ClinvarStoredAllele
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

RELEASE = date(2026, 11, 22)
READ = coga.ClinvarCogaRead("GRCh38", RELEASE, True)


def _stored(allele: str, classification: str = "Likely_pathogenic") -> ClinvarStoredAllele:
    return ClinvarStoredAllele(
        variant_id=allele,
        classification=classification,
        classification_terms=("likely pathogenic",),
        review_status="reviewed by expert panel",
        variation_id=900002,
        included_only=False,
        conditions=("Synthetic disorder 1",),
    )


@pytest.fixture()
def release(monkeypatch: pytest.MonkeyPatch) -> list[set[int]]:
    """The release, holding 1-100-AT-A and M-3243-A-G; each lookup's keys, as asked."""
    asked: list[set[int]] = []
    held = {
        small_allele_key("GRCh38", "1-100-AT-A"): _stored("1-100-AT-A"),
        small_allele_key("GRCh38", "M-3243-A-G"): _stored("M-3243-A-G", "Pathogenic"),
    }

    async def alleles(assembly: str, release: date, keys: Any) -> dict[int, ClinvarStoredAllele]:
        assert (assembly, release) == ("GRCh38", RELEASE)
        asked.append(set(keys))
        return {key: held[key] for key in keys if key in held}

    monkeypatch.setattr(coga, "clinvar_alleles", alleles)
    return asked


def test_each_alt_gets_its_value_in_the_release_read(release: list[set[int]]) -> None:
    [multi, mito, snv] = asyncio.run(
        coga.clinvar_coga_values(READ, [("chr1", 100, "ATT", "AT,ATTT"), ("chrM", 3243, "A", "G"), ("2", 5, "C", "T")])
    )

    deletion, insertion = multi
    assert (deletion.alt, deletion.status, deletion.release) == ("AT", "in_release", RELEASE)
    assert (deletion.classification, deletion.stars, deletion.conditions, deletion.variation_id) == (
        "Likely_pathogenic",
        3,
        ["Synthetic disorder 1"],
        900002,
    )
    assert (insertion.alt, insertion.status, insertion.classification) == ("ATTT", "not_in_release", None)
    assert mito[0].status == "in_release" and mito[0].classification == "Pathogenic"
    assert snv[0].status == "not_in_release"
    # One lookup for the page's alleles alone.
    assert len(release) == 1 and len(release[0]) == 4


@pytest.mark.parametrize(
    ("read", "status"),
    [
        (coga.ClinvarCogaRead("GRCh38", None, True), "no_release"),
        (coga.ClinvarCogaRead("T2T-CHM13", None, False), "not_available"),
    ],
)
def test_a_variant_without_a_release_to_read_says_why(release: list[set[int]], read: Any, status: str) -> None:
    [[value]] = asyncio.run(coga.clinvar_coga_values(read, [("1", 100, "G", "A")]))

    assert (value.status, value.release, value.classification) == (status, None, None)
    assert release == []


def test_the_release_read_is_the_assemblys_current_one(monkeypatch: pytest.MonkeyPatch) -> None:
    async def current(_session: Any, assembly: str) -> Any:
        return type("Release", (), {"release": RELEASE})()

    monkeypatch.setattr(coga, "current_clinvar_release", current)

    assert asyncio.run(coga.clinvar_coga_read(object(), "GRCh38")) == READ  # type: ignore[arg-type]


def test_an_assembly_clinvar_publishes_no_vcf_for_reads_no_release() -> None:
    read = asyncio.run(coga.clinvar_coga_read(object(), "T2T-CHM13"))  # type: ignore[arg-type]

    assert (read.release, read.available) == (None, False)


def test_a_page_names_both_releases_and_each_variants_value(
    release: list[set[int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def annotated(_session: Any, _context: Any) -> date:
        return date(2026, 5, 1)

    monkeypatch.setattr(coga, "analysis_clinvar_release", annotated)
    page = VariantPage(
        total=1,
        variants=[VariantOut(_id="1-100-ATT-AT", chr="1", start=100, end=102, type="DEL", ref="ATT", alt="AT")],
    )

    asyncio.run(coga.serve_clinvar_coga(object(), object(), READ, page))  # type: ignore[arg-type]

    assert (page.clinvar_annotation_release, page.clinvar_coga_release) == (date(2026, 5, 1), RELEASE)
    assert [value.status for value in page.variants[0].clinvar_coga] == ["in_release"]


# --- the filter and the override, in SQL -------------------------------------------------------


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


def _filters(**values: Any) -> SmallVariantQueryFilters:
    return SmallVariantQueryFilters(page=1, page_size=10, **values)


def test_the_clinvar_coga_filter_reads_the_rows_allele_keys_in_the_release() -> None:
    params: dict[str, Any] = {}
    clauses = _small_annotation_filter_clauses(
        _context(), _filters(clinvar_coga=["Pathogenic", "Likely pathogenic"], clinvar_coga_release=RELEASE), params=params
    )

    [clause] = clauses
    assert clause.startswith("arrayExists(k -> k IN (SELECT allele_key FROM ")
    assert "`GRCh38/SNV_INDEL/clinvar`" in clause and clause.endswith(", e.allele_keys)")
    assert params["coga_filter_release"] == RELEASE
    assert params["coga_filter_terms"] == ["pathogenic", "likely pathogenic"]


def test_without_a_release_the_clinvar_coga_filter_keeps_nothing() -> None:
    assert _small_annotation_filter_clauses(_context(), _filters(clinvar_coga=["Pathogenic"]), params={}) == ["0"]


def _lifted(**values: Any) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    filters = _filters(**values)
    condition = _small_annotation_filter_condition(filters, params=params) or ""
    return _clinvar_coga_lifted(_context(), filters, condition, "MEMBERSHIP", params=params, reads_text=False), params


def test_the_override_lifts_the_frequency_filter_for_cogas_pathogenic_allele() -> None:
    condition, params = _lifted(clinvar_pathogenic_override=True, max_gnomad_af=0.01, clinvar_coga_release=RELEASE)

    assert condition.startswith("(MEMBERSHIP OR arrayExists(k -> k IN (SELECT allele_key FROM ")
    assert params["coga_override_terms"] == ["pathogenic", "likely pathogenic"]


def test_the_filters_the_override_does_not_lift_still_hold_on_a_transcript() -> None:
    condition, _params = _lifted(
        clinvar_pathogenic_override=True, max_gnomad_af=0.01, canonical_only=True, clinvar_coga_release=RELEASE
    )

    assert condition.startswith("(MEMBERSHIP OR (arrayExists(k -> k IN (SELECT allele_key FROM ")
    assert condition.endswith(" AND arrayExists(a -> ((a.canonical)), e.transcripts)))")


@pytest.mark.parametrize(
    "values",
    [
        {"clinvar_pathogenic_override": False, "max_gnomad_af": 0.01, "clinvar_coga_release": RELEASE},
        {"clinvar_pathogenic_override": True, "max_gnomad_af": 0.01},
        {"clinvar_pathogenic_override": True, "canonical_only": True, "clinvar_coga_release": RELEASE},
    ],
    ids=["no-override", "no-release", "nothing-to-lift"],
)
def test_the_override_changes_nothing_else(values: dict[str, Any]) -> None:
    assert _lifted(**values)[0] == "MEMBERSHIP"


def test_the_export_writes_each_alts_value_with_its_release(release: list[set[int]]) -> None:
    from backend.app.routers.families_small_variants import _family_export_cell

    variant = VariantOut(_id="1-100-ATT-AT,ATTT", chr="1", start=100, end=102, type="INDEL", ref="ATT", alt="AT,ATTT")
    [variant.clinvar_coga] = asyncio.run(coga.clinvar_coga_values(READ, [("1", 100, "ATT", "AT,ATTT")]))

    assert _family_export_cell(variant, "clinvar_coga") == (
        "AT: Likely_pathogenic (2026-11-22); ATTT: not_in_release (2026-11-22)"
    )
