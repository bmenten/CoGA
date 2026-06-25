from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

from backend.app.services import classification_drift_service as cds
from backend.app.services.small_variant_review_pg import build_evidence_snapshot

_WHEN = datetime(2026, 6, 25, tzinfo=timezone.utc)


def _record(hash_: str | None, clinvar: str | None = None, version: str = "v1"):
    return types.SimpleNamespace(
        annotation_version=version,
        annotation_set_hash=hash_,
        annotations=[{"clinvar": clinvar}] if clinvar is not None else [],
    )


def test_build_evidence_snapshot_extracts_identity_and_clinvar() -> None:
    snap = build_evidence_snapshot(_record("123", "Pathogenic"), _WHEN)
    assert snap["annotation_set_hash"] == "123"
    assert snap["annotation_version"] == "v1"
    assert snap["clinvar"] == "Pathogenic"
    assert snap["captured_at"].startswith("2026-06-25")


def test_build_evidence_snapshot_treats_placeholder_as_no_clinvar() -> None:
    assert build_evidence_snapshot(_record("123", "-"), _WHEN)["clinvar"] is None
    assert build_evidence_snapshot(_record("123", "."), _WHEN)["clinvar"] is None


def test_diff_unchanged_is_current() -> None:
    snap = {"annotation_set_hash": "h1", "clinvar": "VUS", "annotation_version": "v1"}
    assert cds._diff(snap, dict(snap))["status"] == "current"


def test_diff_changed_hash_is_drifted_with_from_to() -> None:
    snap = {"annotation_set_hash": "h1", "clinvar": "VUS", "annotation_version": "v1"}
    cur = {"annotation_set_hash": "h2", "clinvar": "Pathogenic", "annotation_version": "v2"}
    diff = cds._diff(snap, cur)
    assert diff["status"] == "drifted"
    assert diff["clinvar_from"] == "VUS" and diff["clinvar_to"] == "Pathogenic"
    assert diff["annotation_version_from"] == "v1" and diff["annotation_version_to"] == "v2"


def test_diff_missing_variant() -> None:
    snap = {"annotation_set_hash": "h1", "clinvar": "VUS", "annotation_version": "v1"}
    diff = cds._diff(snap, None)
    assert diff["status"] == "variant_missing" and diff["clinvar_to"] is None


def test_diff_unknown_hash_is_not_false_drift() -> None:
    # A missing hash on either side is "unknown", not "changed".
    snap = {"annotation_set_hash": None, "clinvar": "VUS", "annotation_version": "v1"}
    cur = {"annotation_set_hash": "h2", "clinvar": "VUS", "annotation_version": "v1"}
    assert cds._diff(snap, cur)["status"] == "current"


def test_evaluate_classification_drift_reports_only_changed(monkeypatch) -> None:
    async def _context(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_name="GRCh38")

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "variant_id": "1-100-A-G",
                    "acmg_class": "acmg_class_4",
                    "acmg_evidence_snapshot": {
                        "annotation_set_hash": "OLD",
                        "clinvar": "Uncertain significance",
                        "annotation_version": "v1",
                    },
                    "updated_by": "alice",
                    "updated_at": _WHEN,
                },
                {
                    "variant_id": "2-200-C-T",  # unchanged -> excluded from the drifted list
                    "acmg_class": "acmg_class_3",
                    "acmg_evidence_snapshot": {
                        "annotation_set_hash": "SAME",
                        "clinvar": None,
                        "annotation_version": "v1",
                    },
                    "updated_by": "bob",
                    "updated_at": _WHEN,
                },
            ]

    class _Session:
        async def execute(self, *args, **kwargs):
            return _Result()

    current_by_variant = {
        "1-100-A-G": _record("NEW", "Pathogenic", version="v2"),
        "2-200-C-T": _record("SAME", None, version="v1"),
    }

    async def _current(*, assembly_name, family_guid, variant_id):
        return current_by_variant[variant_id]

    monkeypatch.setattr(cds, "build_family_metadata_context", _context)
    monkeypatch.setattr(cds, "get_small_variant_family_record", _current)

    out = asyncio.run(
        cds.evaluate_classification_drift(_Session(), family_id="FAM1", user=None)
    )
    assert out["family_id"] == "FAM1"
    assert out["checked"] == 2
    assert out["drifted_count"] == 1
    item = out["drifted"][0]
    assert item["variant_id"] == "1-100-A-G" and item["status"] == "drifted"
    assert item["clinvar_from"] == "Uncertain significance" and item["clinvar_to"] == "Pathogenic"
    assert item["classified_by"] == "alice"
