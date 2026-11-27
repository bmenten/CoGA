"""A small variant's ClinVar: its value in the release CoGA loads itself, served with the page,
and the ClinVar filters and the P/LP override on it, in SQL.

A request reads the assembly's current release once and serves it with its page; each ALT of a
variant gets its value from that release, by its allele key, or the reason it has none (no
ClinVar VCF for the assembly, no release loaded, not in the release), and the variant's own
ClinVar fields (classification, review status, stars, conditions, VariationID) are its shown
ALT's, none where the release does not hold that allele: the annotation's ClinVar is neither
shown nor used. The *ClinVar status* filter keeps a row one of whose alleles the release
classifies with a status asked for; the excluded statuses leave a row out only when every allele
has one, so a pathogenic allele is never hidden for a benign sibling ALT. Without a release no
allele has ClinVar: the status filter keeps nothing and the exclusion leaves nothing out. With
the ClinVar P/LP override, an allele the release holds (likely) pathogenic lifts the
consequence, in-silico and frequency filters on every transcript of the row; the other
transcript filters still hold, and without a release, or without the override, nothing changes.
The family export writes the shown ALT's classification and stars.

The release, the page's rows and ClickHouse are stood in for;
``integration/test_clinvar_coga_filter_clickhouse.py`` runs the conditions on real ClickHouse.
Synthetic alleles only.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.app.schemas import VariantOut, VariantPage
from backend.app.services import clinvar_coga as coga
from backend.app.services.analysis_scope import WHOLE_FAMILY_SCOPE
from backend.app.services.clickhouse_variant_queries import (
    _clinvar_override,
    _small_annotation_filter_clauses,
    _small_annotation_filter_condition,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters
from backend.tests._clinvar_release_fakes import READ, RELEASE, stored_allele, wire_clinvar_alleles


@pytest.fixture()
def release(monkeypatch: pytest.MonkeyPatch) -> list[set[int]]:
    """The release, holding 1-100-AT-A and M-3243-A-G; each lookup's keys, as asked."""
    return wire_clinvar_alleles(
        monkeypatch,
        {
            "1-100-AT-A": stored_allele(
                "1-100-AT-A",
                "Likely_pathogenic",
                review_status="reviewed by expert panel",
                variation_id=900002,
                conditions=("Synthetic disorder 1",),
            ),
            "M-3243-A-G": "Pathogenic",
        },
    )


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


def _deletion(alt_allele: str | None = None, **annotated: Any) -> VariantOut:
    return VariantOut(
        _id="1-100-ATT-AT,ATTT",
        chr="1",
        start=100,
        end=102,
        type="INDEL",
        ref="ATT",
        alt="AT,ATTT",
        alt_allele=alt_allele,
        **annotated,
    )


def _clinvar_fields(variant: VariantOut) -> tuple[Any, ...]:
    return (
        variant.clinvar,
        variant.clinvar_review_status,
        variant.clinvar_stars,
        variant.clinvar_conditions,
        variant.clinvar_variation_id,
    )


def test_a_page_names_its_release_and_serves_each_variants_value(release: list[set[int]]) -> None:
    page = VariantPage(total=1, variants=[_deletion("AT")])

    asyncio.run(coga.serve_clinvar_coga(READ, page))

    assert page.clinvar_coga_release == RELEASE
    [variant] = page.variants
    assert [(value.alt, value.status) for value in variant.clinvar_coga] == [
        ("AT", "in_release"),
        ("ATTT", "not_in_release"),
    ]
    # The variant's own ClinVar fields: its shown ALT's in the release.
    assert _clinvar_fields(variant) == (
        "Likely_pathogenic",
        "reviewed by expert panel",
        3,
        ["Synthetic disorder 1"],
        "900002",
    )


def test_a_page_without_variants_names_its_release_and_looks_nothing_up(release: list[set[int]]) -> None:
    page = VariantPage(total=0, variants=[])

    asyncio.run(coga.serve_clinvar_coga(READ, page))

    assert (page.clinvar_coga_release, release) == (RELEASE, [])


@pytest.mark.parametrize(
    "alt_allele",
    ["ATTT", None],
    ids=["shown-alt-not-in-release", "no-shown-alt-of-two"],
)
def test_the_annotations_clinvar_is_never_served(release: list[set[int]], alt_allele: str | None) -> None:
    # The annotation's ClinVar of the variant, as the import would have parsed it: replaced by
    # the release's value of the shown ALT, here none (ATTT is not in the release; without a
    # shown ALT a multi-allelic variant names no single one).
    variant = _deletion(
        alt_allele,
        clinvar="Pathogenic",
        clinvar_review_status="criteria provided, single submitter",
        clinvar_stars=1,
        clinvar_conditions=["Annotated disorder"],
        clinvar_variation_id="12345",
    )

    asyncio.run(coga.attach_clinvar_coga(READ, [variant]))

    assert _clinvar_fields(variant) == (None, None, None, [], None)
    assert [value.status for value in variant.clinvar_coga] == ["in_release", "not_in_release"]


def test_an_allele_held_only_in_a_combination_has_no_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    combination = stored_allele("1-100-G-A", "")
    wire_clinvar_alleles(monkeypatch, {"1-100-G-A": combination})
    variant = VariantOut(_id="1-100-G-A", chr="1", start=100, end=100, type="SNV", ref="G", alt="A")

    asyncio.run(coga.attach_clinvar_coga(READ, [variant]))

    [value] = variant.clinvar_coga
    assert (value.status, value.classification) == ("in_release", None)
    assert variant.clinvar is None


def test_without_a_release_no_variant_has_clinvar(release: list[set[int]]) -> None:
    variant = _deletion("AT", clinvar="Pathogenic", clinvar_stars=2)

    asyncio.run(coga.attach_clinvar_coga(coga.ClinvarCogaRead("GRCh38", None, True), [variant]))

    assert _clinvar_fields(variant) == (None, None, None, [], None)
    assert [value.status for value in variant.clinvar_coga] == ["no_release", "no_release"]
    assert release == []


# --- the filters and the override, in SQL ------------------------------------------------------


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


_HELD = "k -> k IN (SELECT allele_key FROM coga.`GRCh38/SNV_INDEL/clinvar` WHERE release = %({prefix}_release)s AND hasAny(classification_terms, %({prefix}_terms)s))"


def test_the_clinvar_status_filter_keeps_a_row_with_an_allele_of_a_status_asked_for() -> None:
    params: dict[str, Any] = {}
    clauses = _small_annotation_filter_clauses(
        _context(), _filters(clinvar=["Pathogenic", "Likely pathogenic"], clinvar_coga_release=RELEASE), params=params
    )

    # One allele of the row in the release with a status asked for; no transcript's ClinVar.
    assert clauses == [f"arrayExists({_HELD.format(prefix='clinvar_status')}, e.allele_keys)"]
    assert params == {"clinvar_status_release": RELEASE, "clinvar_status_terms": ["pathogenic", "likely pathogenic"]}


def test_the_excluded_statuses_leave_a_row_out_only_where_every_allele_has_one() -> None:
    params: dict[str, Any] = {}
    clauses = _small_annotation_filter_clauses(
        _context(), _filters(exclude_clinvar=["Benign", "Likely_benign"], clinvar_coga_release=RELEASE), params=params
    )

    # A row without an allele key (a symbolic ALT) has no allele the release excludes: kept.
    assert clauses == [
        f"NOT (notEmpty(e.allele_keys) AND arrayAll({_HELD.format(prefix='clinvar_excluded')}, e.allele_keys))"
    ]
    assert params == {"clinvar_excluded_release": RELEASE, "clinvar_excluded_terms": ["benign", "likely benign"]}


@pytest.mark.parametrize(
    ("values", "clauses"),
    [
        ({"clinvar": ["Pathogenic"]}, ["0"]),
        ({"exclude_clinvar": ["Benign"]}, ["NOT 0"]),
    ],
    ids=["status-keeps-nothing", "exclusion-leaves-nothing-out"],
)
def test_without_a_release_no_allele_has_clinvar(values: dict[str, Any], clauses: list[str]) -> None:
    params: dict[str, Any] = {}

    assert _small_annotation_filter_clauses(_context(), _filters(**values), params=params) == clauses
    assert params == {}


def _overridden(**values: Any) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    filters = _filters(**values)
    condition = _small_annotation_filter_condition(filters, params=params) or ""
    return _clinvar_override(_context(), filters, condition, "MEMBERSHIP", params=params, reads_text=False), params


def test_the_override_lifts_the_frequency_filter_for_an_allele_the_release_holds_pathogenic() -> None:
    condition, params = _overridden(clinvar_pathogenic_override=True, max_gnomad_af=0.01, clinvar_coga_release=RELEASE)

    assert condition == f"(MEMBERSHIP OR arrayExists({_HELD.format(prefix='clinvar_override')}, e.allele_keys))"
    assert params["clinvar_override_terms"] == ["pathogenic", "likely pathogenic"]


def test_the_filters_the_override_does_not_lift_still_hold_on_a_transcript() -> None:
    condition, _params = _overridden(
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
    assert _overridden(**values)[0] == "MEMBERSHIP"


# --- the export ---------------------------------------------------------------------------------


def test_the_export_writes_the_shown_alts_clinvar_and_stars(release: list[set[int]]) -> None:
    from backend.app.routers.families_small_variants import _FAMILY_EXPORT_COLUMNS, _family_export_cell

    shown, not_held = _deletion("AT"), _deletion("ATTT", clinvar="Pathogenic", clinvar_stars=2)
    asyncio.run(coga.attach_clinvar_coga(READ, [shown, not_held]))

    assert [label for _field, label in _FAMILY_EXPORT_COLUMNS if label.startswith("ClinVar")] == [
        "ClinVar",
        "ClinVar stars",
    ]
    assert [_family_export_cell(shown, field) for field in ("clinvar", "clinvar_stars")] == ["Likely_pathogenic", "3"]
    assert [_family_export_cell(not_held, field) for field in ("clinvar", "clinvar_stars")] == ["", ""]
