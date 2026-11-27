"""The re-analysis signal after sign-out (the ClinVar design's phase 3, B10): a project's signed
analyses one of whose small variants CoGA's current ClinVar release classifies otherwise than
it was frozen. Read only; nothing of the analysis changes, and no mail goes out.

Each variant is read at the ClinVar (CoGA) value the signed version saw: the value its drift
section names at sign-out, else the one frozen with the classification. The variants are those
the record reports and those the analysis's review classifies and the record does not report:
the review the analysis reads (its own, else the shared one, picked as the drift check picks
it), where it holds an ACMG classification frozen before the sign-out. A variant is listed for
another classification of an ALT, not for another review status or other stars of the same one
(B5), and not where its assembly reads no release. A project outside the caller's answers 404,
as an unknown one does.

Postgres, the release and its lookup are stood in for;
``integration/test_clinvar_coga_drift_integration.py`` reads the list on real Postgres and
ClickHouse. Synthetic values only.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.schemas import ClinvarCogaOut
from backend.app.services import clinvar_reanalysis as reanalysis
from backend.app.services.clinvar_coga import ClinvarCogaRead

PROJECT = "00000000-0000-4000-8000-00000000000a"
FAMILY = "00000000-0000-4000-8000-0000000000f1"
FROZEN, NOW = date(2026, 11, 15), date(2026, 11, 22)
CLASSIFIED_AT = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
SIGNED_AT = datetime(2026, 11, 16, tzinfo=timezone.utc)


def _value(classification: str | None, *, release: date = FROZEN, review: str = "criteria provided, single submitter",
           stars: int = 1, status: str = "in_release") -> dict[str, Any]:
    return ClinvarCogaOut(
        alt="A", status=status, release=release, classification=classification,  # type: ignore[arg-type]
        review_status=review, stars=stars,
    ).model_dump(mode="json")


def _reported(variant_id: str, clinvar_coga: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {
        "variant_id": variant_id,
        "class_shown": "VUS - class 3",
        "evidence_snapshot": None if clinvar_coga is None else {"clinvar_coga": clinvar_coga},
    }


def _signed(analysis: str | None, reported: list[dict[str, Any]], drifted: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "family_uuid": FAMILY,
        "family_identifier": "FAM1",
        "version": 2,
        "signed_out_at": SIGNED_AT,
        "content_hash": "ab" * 32,
        "analysis": analysis,
        "assembly": "GRCh38",
        "reported_variants": reported,
        "drifted": drifted,
    }


def _review(
    variant_id: str,
    analysis: str,
    classification: str | None,
    clinvar_coga: list[dict[str, Any]] | None,
    *,
    captured_at: datetime = CLASSIFIED_AT,
) -> dict[str, Any]:
    """A review as Postgres holds it; ``clinvar_coga`` None for one without an ACMG classification."""
    snapshot = None if clinvar_coga is None else {"clinvar_coga": clinvar_coga, "captured_at": captured_at.isoformat()}
    return {
        "family_uuid": FAMILY,
        "analysis": analysis,
        "variant_id": variant_id,
        "classification": classification,
        "tags": [],
        "acmg_evidence_snapshot": snapshot,
        "updated_by": "analyst",
        "updated_at": captured_at,
        "metadata": {"analyses": ["long_read_wgs", "short_read_wgs"]},
    }


class _Session:
    """Postgres as the list reads it: the project's existence, its signed analyses, their reviews."""

    def __init__(self, *, known: bool = True, signed: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> None:
        self.known, self.signed, self.reviews = known, signed, reviews

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        if "FROM projects" in sql:
            return SimpleNamespace(scalar_one=lambda: self.known)
        rows = self.signed if "FROM report_signouts" in sql else self.reviews
        if "acmg_evidence_snapshot IS NOT NULL" in sql:
            # A query that left out the reviews without ACMG would let a shared review stand in
            # for the analysis's own: what Postgres would return for it.
            rows = [row for row in rows if row["acmg_evidence_snapshot"] is not None]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


@pytest.fixture()
def release(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """CoGA's current release: each variant's value now, by its id."""
    now: dict[str, list[dict[str, Any]]] = {}

    async def read(_session: Any, assembly: str) -> ClinvarCogaRead:
        return ClinvarCogaRead(assembly, NOW, True)

    async def values(_read: Any, loci: Any) -> list[list[ClinvarCogaOut]]:
        return [
            [ClinvarCogaOut.model_validate(value) for value in now[f"{chrom}-{pos}-{ref}-{alt}"]]
            for chrom, pos, ref, alt in loci
        ]

    monkeypatch.setattr(reanalysis, "clinvar_coga_read", read)
    monkeypatch.setattr(reanalysis, "clinvar_coga_values", values)
    return now


def _list(session: _Session, user: Any = None) -> Any:
    admin = SimpleNamespace(role="admin", metadata_project_ids=[])
    return asyncio.run(reanalysis.clinvar_reanalysis_list(session, project_id=PROJECT, user=user or admin))  # type: ignore[arg-type]


def test_a_reclassified_variant_of_a_signed_analysis_is_listed(release: dict[str, list[dict[str, Any]]]) -> None:
    release.update(
        {
            "1-100-G-A": [_value("Likely_pathogenic", release=NOW)],  # reported: VUS -> LP
            "1-200-G-A": [_value("Uncertain_significance", release=NOW, review="reviewed by expert panel", stars=3)],
            "1-300-G-A": [_value("Pathogenic", release=NOW)],  # classified, not reported: B -> P
            "1-400-G-A": [_value("Benign", release=NOW)],  # its signed value: drifted at sign-out
        }
    )
    signed = _signed(
        "long_read_wgs",
        [
            _reported("1-100-G-A", [_value("Uncertain_significance")]),
            # Only the review status and the stars moved: not a reclassification (B5).
            _reported("1-200-G-A", [_value("Uncertain_significance")]),
            # No evidence frozen: nothing to compare.
            _reported("1-500-G-A", None),
            # Frozen pathogenic, benign at sign-out (drifted and acknowledged): as signed, benign.
            _reported("1-400-G-A", [_value("Pathogenic")]),
        ],
        [{"variant_id": "1-400-G-A", "status": "drifted", "clinvar_coga_to": [_value("Benign", release=NOW)]}],
    )
    reviews = [
        _review("1-300-G-A", "long_read_wgs", "Likely benign - class 2", [_value("Benign")]),
        # Another analysis's review: not this analysis's.
        _review("1-300-G-A", "short_read_wgs", "VUS - class 3", [_value("Pathogenic")]),
        # Reported already: read from the record.
        _review("1-100-G-A", "long_read_wgs", "VUS - class 3", [_value("Pathogenic")]),
    ]

    out = _list(_Session(signed=[signed], reviews=reviews))

    assert out.releases == {"GRCh38": NOW}
    assert [(item.variant_id, item.reported, item.classification) for item in out.items] == [
        ("1-100-G-A", True, "VUS - class 3"),
        ("1-300-G-A", False, "Likely benign - class 2"),
    ]
    first = out.items[0]
    assert (first.family_id, first.analysis, first.version, first.signed_out_at) == ("FAM1", "long_read_wgs", 2, SIGNED_AT)
    assert first.report_code and first.clinvar_coga_frozen[0].classification == "Uncertain_significance"
    assert first.clinvar_coga_now[0].classification == "Likely_pathogenic"


def test_a_shared_review_counts_for_an_analysis_without_its_own(release: dict[str, list[dict[str, Any]]]) -> None:
    release["1-300-G-A"] = [_value("Pathogenic", release=NOW)]
    reviews = [_review("1-300-G-A", "", "Likely benign - class 2", [_value("Benign")])]

    out = _list(_Session(signed=[_signed("long_read_wgs", [], [])], reviews=reviews))

    assert [item.variant_id for item in out.items] == ["1-300-G-A"]


def test_a_classification_is_read_as_the_signed_version_saw_it(release: dict[str, list[dict[str, Any]]]) -> None:
    release.update(
        {
            "1-300-G-A": [_value("Pathogenic", release=NOW)],  # its drift noted at sign-out
            "1-310-G-A": [_value("Pathogenic", release=NOW)],  # its own review holds no ACMG
            "1-320-G-A": [_value("Pathogenic", release=NOW)],  # classified after the sign-out
        }
    )
    signed = _signed(
        "long_read_wgs",
        [],
        # Frozen benign, pathogenic at sign-out: the signed version saw it pathogenic.
        [{"variant_id": "1-300-G-A", "status": "drifted", "clinvar_coga_to": [_value("Pathogenic", release=NOW)]}],
    )
    reviews = [
        _review("1-300-G-A", "long_read_wgs", "Likely benign - class 2", [_value("Benign")]),
        # The analysis's own review without an ACMG classification hides the shared one, as
        # the drift check reads it.
        _review("1-310-G-A", "", "Likely benign - class 2", [_value("Benign")]),
        _review("1-310-G-A", "long_read_wgs", None, None),
        # Frozen after the sign-out: the next sign-out's, not this one's.
        _review(
            "1-320-G-A", "long_read_wgs", "Likely benign - class 2", [_value("Benign")],
            captured_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        ),
    ]

    out = _list(_Session(signed=[signed], reviews=reviews))

    assert out.items == []


def test_a_project_without_a_signed_analysis_lists_nothing(release: dict[str, list[dict[str, Any]]]) -> None:
    out = _list(_Session(signed=[], reviews=[]))

    assert (out.releases, out.items) == ({}, [])


def test_an_assembly_without_a_release_lists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(_session: Any, assembly: str) -> ClinvarCogaRead:
        return ClinvarCogaRead(assembly, None, True)

    async def values(_read: Any, loci: Any) -> list[list[ClinvarCogaOut]]:
        return [[ClinvarCogaOut(alt="A", status="no_release")] for _locus in loci]

    monkeypatch.setattr(reanalysis, "clinvar_coga_read", none)
    monkeypatch.setattr(reanalysis, "clinvar_coga_values", values)
    signed = _signed(None, [_reported("1-100-G-A", [_value("Pathogenic")])], [])

    out = _list(_Session(signed=[signed], reviews=[]))

    assert (out.releases, out.items) == ({"GRCh38": None}, [])


@pytest.mark.parametrize(
    ("known", "user"),
    [(False, None), (True, SimpleNamespace(role="analyst", metadata_project_ids=["another-project"]))],
    ids=["unknown", "not-the-callers"],
)
def test_a_project_outside_the_callers_answers_404_as_an_unknown_one(known: bool, user: Any) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _list(_Session(known=known, signed=[], reviews=[]), user=user)

    assert (refused.value.status_code, refused.value.detail) == (404, "Project not found")
