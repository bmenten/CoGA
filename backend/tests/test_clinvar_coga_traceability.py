"""ClinVar (CoGA) in the clinical trail: what a classification freezes, how the drift check
compares it, and what the signed record names (the owner's decision B5).

A classification freezes each ALT's ClinVar in CoGA's current release beside the annotation's.
The drift check reads the release current now: another classification of an ALT (one on one
side and none on the other included) is drift naming ``clinvar_coga``; another review status or
other stars of the same classification is ``noted``, listed and never counted at sign-out;
another release alone is no change; a snapshot without the value, or not as a list of values,
cannot be verified. The signed record names the release it was signed with.

``integration/test_clinvar_coga_drift_integration.py`` runs the save, the drift check and the
sign-out on real Postgres and ClickHouse. Synthetic values only.
"""

from __future__ import annotations

import asyncio
import types
from datetime import date, datetime, timezone
from typing import Any

import pytest

from backend.app.schemas import ClinvarCogaOut
from backend.app.services import classification_drift_service as cds
from backend.app.services import signed_record_sections as sections
from backend.app.services import small_variant_review_acmg as acmg
from backend.app.services.clinvar_coga import ClinvarCogaRead
from backend.app.services.clinvar_releases import ClinvarRelease
from backend.app.services.finding_review_pg import FINDING_DRIFT_SECTION
from backend.app.services.report_signout_service import STRUCTURAL_DRIFT_SECTION, _drift_concerns

_WHEN = datetime(2026, 11, 27, tzinfo=timezone.utc)


def _value(**overrides: Any) -> ClinvarCogaOut:
    values: dict[str, Any] = {
        "alt": "A",
        "status": "in_release",
        "release": date(2026, 11, 22),
        "classification": "Pathogenic",
        "review_status": "criteria provided, multiple submitters, no conflicts",
        "stars": 2,
        "conditions": ["Synthetic disorder 1"],
        "variation_id": 900001,
        "included_only": False,
    }
    return ClinvarCogaOut(**{**values, **overrides})


def _record() -> Any:
    return types.SimpleNamespace(
        chrom="1", pos=1000100, ref="G", alt="A", annotation_version="v1", annotation_set_hash="h1", annotations=[]
    )


def _snapshot(*values: ClinvarCogaOut) -> dict[str, Any]:
    return acmg.build_evidence_snapshot(_record(), _WHEN, gene_missense_z=None, clinvar_coga=list(values))


def test_a_classification_freezes_each_alts_value_with_its_release() -> None:
    not_held = _value(
        alt="T", status="not_in_release", classification=None, review_status=None, stars=None, conditions=[],
        variation_id=None,
    )
    snapshot = _snapshot(_value(), not_held)

    assert snapshot["clinvar_coga"] == [
        {
            "alt": "A",
            "status": "in_release",
            "release": "2026-11-22",
            "classification": "Pathogenic",
            "review_status": "criteria provided, multiple submitters, no conflicts",
            "stars": 2,
            "conditions": ["Synthetic disorder 1"],
            "variation_id": 900001,
            "included_only": False,
        },
        {
            "alt": "T",
            "status": "not_in_release",
            "release": "2026-11-22",
            "classification": None,
            "review_status": None,
            "stars": None,
            "conditions": [],
            "variation_id": None,
            "included_only": False,
        },
    ]


def test_a_save_freezes_the_value_in_the_assemblys_current_release(monkeypatch: pytest.MonkeyPatch) -> None:
    read = ClinvarCogaRead("GRCh38", date(2026, 11, 22), True)
    asked: list[Any] = []

    async def current(_session: Any, assembly_name: Any) -> ClinvarCogaRead:
        asked.append(assembly_name)
        return read

    async def values(release_read: ClinvarCogaRead, records: Any) -> dict[str, list[ClinvarCogaOut]]:
        assert release_read is read
        return {key: [_value()] for key in records}

    async def no_missense_z(_session: Any, records: Any, *, assembly_id: Any) -> dict[str, None]:
        return {key: None for key in records}

    monkeypatch.setattr(acmg, "clinvar_coga_read", current)
    monkeypatch.setattr(acmg, "clinvar_coga_by_id", values)
    monkeypatch.setattr(acmg, "evidence_gene_missense_z", no_missense_z)

    snapshot = asyncio.run(
        acmg.freeze_evidence_snapshot(object(), _record(), _WHEN, assembly_id="a1", assembly_name="GRCh38")  # type: ignore[arg-type]
    )

    assert asked == ["GRCh38"]
    assert [value["classification"] for value in snapshot["clinvar_coga"]] == ["Pathogenic"]


@pytest.mark.parametrize(
    ("now", "status", "changed"),
    [
        (_value(), "current", []),
        # Another release that says the same.
        (_value(release=date(2026, 11, 29)), "current", []),
        # An aggregate that adds a term.
        (_value(classification="Pathogenic/Likely_pathogenic"), "drifted", ["clinvar_coga"]),
        (_value(classification="Likely_pathogenic"), "drifted", ["clinvar_coga"]),
        (_value(status="not_in_release", classification=None, review_status=None, stars=None), "drifted", ["clinvar_coga"]),
        (_value(review_status="reviewed by expert panel", stars=3), "noted", []),
        (_value(stars=1), "noted", []),
    ],
    ids=["same", "release-only", "aggregate", "reclassified", "gone", "review-status", "stars"],
)
def test_the_drift_check_counts_another_classification_and_notes_another_review(
    now: ClinvarCogaOut, status: str, changed: list[str]
) -> None:
    diff = cds._diff(_snapshot(_value()), _snapshot(now))

    assert (diff["status"], diff["changed"]) == (status, changed)
    assert diff["clinvar_coga_from"] == [_value().model_dump(mode="json")]
    assert diff["clinvar_coga_to"] == [now.model_dump(mode="json")]


def test_an_aggregate_in_another_order_is_the_same_classification() -> None:
    frozen = _snapshot(_value(classification="Likely_pathogenic/Pathogenic"))
    assert cds._diff(frozen, _snapshot(_value(classification="Pathogenic/Likely_pathogenic")))["status"] == "current"


def test_a_classification_where_there_was_none_is_drift() -> None:
    frozen = _snapshot(_value(status="no_release", release=None, classification=None, review_status=None, stars=None))

    assert cds._diff(frozen, _snapshot(_value()))["changed"] == ["clinvar_coga"]


def test_a_review_that_moved_beside_a_drift_leaves_it_a_drift() -> None:
    now = _snapshot(_value(review_status="reviewed by expert panel", stars=3))
    now["clinvar"] = "Pathogenic"

    diff = cds._diff(_snapshot(_value()), now)

    assert (diff["status"], diff["changed"]) == ("drifted", ["clinvar"])


@pytest.mark.parametrize("frozen", [None, "Pathogenic", ["Pathogenic"]], ids=["missing", "text", "not-mappings"])
def test_a_snapshot_without_a_readable_value_cannot_be_verified(frozen: Any) -> None:
    snapshot = _snapshot(_value())
    if frozen is None:
        del snapshot["clinvar_coga"]
    else:
        snapshot["clinvar_coga"] = frozen

    diff = cds._diff(snapshot, _snapshot(_value()))

    assert (diff["status"], diff["clinvar_coga_from"]) == ("unknown", None)


def test_a_noted_entry_is_not_held_against_the_sign_out() -> None:
    body = {
        "drift": {
            "drifted": [
                {"variant_id": "1-100-G-A", "status": "noted", "changed": [], "classified_by": "alice"},
                {"variant_id": "2-200-C-A", "status": "drifted", "changed": ["clinvar_coga"], "classified_by": "alice"},
            ]
        },
        STRUCTURAL_DRIFT_SECTION: {"drifted": []},
        FINDING_DRIFT_SECTION: {"drifted": []},
    }

    concerns = _drift_concerns(body)

    assert concerns["drifted"] == [{"variant_id": "2-200-C-A", "status": "drifted", "changed": ["clinvar_coga"]}]


def test_the_signed_record_names_the_release_it_was_signed_with(monkeypatch: pytest.MonkeyPatch) -> None:
    release = ClinvarRelease(
        release=date(2026, 11, 22),
        source_version="2026-11-22 (clinvar_20261122.vcf.gz, SHA-256 0123abcd)",
        source_url="https://clinvar.example/pub/clinvar/vcf_GRCh38/clinvar_20261122.vcf.gz",
        loaded_at=_WHEN,
        records=8,
        loaded_by="startup-bootstrap",
    )
    held = {"GRCh38": release}

    async def current(_session: Any, assembly_name: str) -> ClinvarRelease | None:
        return held.get(assembly_name)

    monkeypatch.setattr(sections, "current_clinvar_release", current)

    assert asyncio.run(sections.clinvar_coga_record(object(), "GRCh38")) == {  # type: ignore[arg-type]
        "release": "2026-11-22",
        "source_version": "2026-11-22 (clinvar_20261122.vcf.gz, SHA-256 0123abcd)",
        "source_url": "https://clinvar.example/pub/clinvar/vcf_GRCh38/clinvar_20261122.vcf.gz",
        "loaded_at": "2026-11-27T00:00:00+00:00",
        "records": 8,
    }
    # None loaded for the assembly, or a family without an assembly: recorded as none.
    assert asyncio.run(sections.clinvar_coga_record(object(), "GRCh37")) is None  # type: ignore[arg-type]
    assert asyncio.run(sections.clinvar_coga_record(object(), None)) is None  # type: ignore[arg-type]
