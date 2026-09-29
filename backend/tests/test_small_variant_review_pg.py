from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

import pytest

from backend.app.schemas import AcmgClassificationPayload, SmallVariantReviewUpdate
from backend.app.services import clinical_audit_service
from backend.app.services import small_variant_review_pg
from backend.app.services import small_variant_review_tags


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _RecordingSession:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.sql: str | None = None
        self.params = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params
        return _FakeResult(self.rows)


@pytest.mark.asyncio
async def test_get_small_variant_review_map_binds_variant_ids_expanding() -> None:
    timestamp = datetime.now(timezone.utc)
    session = _RecordingSession(
        [
            {
                "variant_id": "var-1",
                "classification": "review",
                "tags": ["validated"],
                "tag_metadata": {},
                "note": "kept",
                "compound_het_group_id": None,
                "compound_het_partner_variant_ids": [],
                "compound_het_gene": None,
                "compound_het_gene_id": None,
                "compound_het_classification": None,
                "compound_het_tags": [],
                "compound_het_tag_metadata": {},
                "compound_het_note": None,
                "compound_het_phase_status": None,
                "compound_het_updated_by": None,
                "compound_het_updated_at": None,
                "updated_by": "admin",
                "updated_at": timestamp,
            }
        ]
    )

    result = await small_variant_review_pg.get_small_variant_review_map(
        session,
        family_uuid="family-uuid",
        variant_ids=["var-1", ""],
    )

    assert session.sql is not None
    assert "variant_id IN" in session.sql
    assert session.params == {"family_id": "family-uuid", "variant_ids": ["var-1"]}
    assert result["var-1"].variant_id == "var-1"
    assert result["var-1"].tags == ["validated"]


def test_report_tag_registered_as_default_collaboration_tag() -> None:
    report = next(
        (tag for tag in small_variant_review_tags.DEFAULT_SMALL_VARIANT_TAGS if tag["key"] == "report"),
        None,
    )
    assert report is not None, "the 'report' default tag should be registered"
    assert report["group"] == "collaboration"
    assert report["label"] == "Report"
    assert "report" in small_variant_review_tags.DEFAULT_SMALL_VARIANT_TAG_KEYS


# --- clearing a review is a clinical change too -------------------------------------------------

_CLEARED = {"acmg_class": None, "acmg": None, "tags": [], "note": None}


class _SaveSession:
    def __init__(self) -> None:
        self.commits = 0

    async def execute(self, statement, params=None):
        return types.SimpleNamespace(
            mappings=lambda: types.SimpleNamespace(first=lambda: None, all=lambda: []),
            scalar_one_or_none=lambda: None,
        )

    async def commit(self) -> None:
        self.commits += 1


def _stored_review(**fields) -> dict:
    review = {
        "id": "review-1",
        "variant_id": "1-100-A-G",
        "classification": "Pathogenic",
        "tags": ["acmg_class_5", "report"],
        "tag_metadata": {},
        "note": "reported",
        "acmg": {"criteria": [{"code": "PVS1", "strength": "very_strong", "accepted": True}]},
        "acmg_point_total": 8,
        "acmg_class": "acmg_class_5",
        "acmg_evidence_snapshot": None,
        "updated_by": "alice",
        "updated_at": datetime(2026, 9, 28, tzinfo=timezone.utc),
    }
    review.update(fields)
    return review


def _clear(monkeypatch, existing: dict, payload: SmallVariantReviewUpdate):
    """Save ``payload`` over ``existing``; return the audit calls and the writes made."""
    session = _SaveSession()
    audits: list[dict] = []
    writes: list[str] = []

    async def _variant(**kwargs):
        return types.SimpleNamespace(variant_key=1, gene_symbols=[])

    async def _fetch(session_, *, family_uuid, variant_id):
        return existing

    async def _update(session_, **kwargs):
        writes.append("update")

    async def _delete(session_, review_id):
        writes.append("delete")

    async def _audit(session_, **kwargs):
        audits.append({**kwargs, "commits_before": session.commits})

    monkeypatch.setattr(small_variant_review_pg, "get_small_variant_family_record", _variant)
    monkeypatch.setattr(small_variant_review_pg, "_fetch_review_row", _fetch)
    monkeypatch.setattr(small_variant_review_pg, "_update_review_row", _update)
    monkeypatch.setattr(small_variant_review_pg, "_delete_review_row", _delete)
    monkeypatch.setattr(small_variant_review_pg, "record_review_changes", _audit)
    context = types.SimpleNamespace(
        family_uuid="family-uuid",
        family_id="FAM1",
        assembly_name="GRCh38",
        project_ids=[],
        affected_sample_names=[],
    )
    user = types.SimpleNamespace(id=None, username="carol", email="carol@x.org", role="admin")
    asyncio.run(
        small_variant_review_pg.upsert_small_variant_review(
            session, context=context, variant_id="1-100-A-G", payload=payload, user=user
        )
    )
    return audits, writes, session


def test_deleting_a_cleared_review_is_audited(monkeypatch) -> None:
    existing = _stored_review()
    audits, writes, session = _clear(monkeypatch, existing, SmallVariantReviewUpdate(acmg=None))

    assert writes == ["delete"] and session.commits == 1
    [audit] = audits
    assert (audit["existing"], audit["new_state"]) == (existing, _CLEARED)
    assert (audit["family_identifier"], audit["variant_id"]) == ("FAM1", "1-100-A-G")
    assert audit["commits_before"] == 0  # in the same transaction as the delete
    events = clinical_audit_service.diff_review_changes(audit["existing"], audit["new_state"])
    assert [event["summary"] for event in events] == [
        "Classification Pathogenic (class 5) → unclassified",
        "Tags removed acmg_class_5, report",
        "Note removed",
    ]


def test_clearing_a_review_that_keeps_its_compound_het_pairing_is_audited(monkeypatch) -> None:
    existing = _stored_review(
        compound_het_group_id="group-1",
        compound_het_partner_variant_ids=["1-200-C-T"],
    )
    payload = SmallVariantReviewUpdate(acmg=AcmgClassificationPayload(criteria=[]))
    audits, writes, session = _clear(monkeypatch, existing, payload)

    assert writes == ["update"] and session.commits == 1
    [audit] = audits
    assert (audit["existing"], audit["new_state"]) == (existing, _CLEARED)
    assert audit["commits_before"] == 0


def test_an_empty_save_of_no_review_audits_nothing(monkeypatch) -> None:
    audits, writes, session = _clear(monkeypatch, None, SmallVariantReviewUpdate())
    assert (audits, writes, session.commits) == ([], [], 0)
