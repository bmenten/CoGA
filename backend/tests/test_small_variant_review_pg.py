from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

import pytest

from backend.app.schemas import AcmgClassificationPayload, AcmgCriterionSelection, SmallVariantReviewUpdate
from backend.app.services import clinical_audit_service
from backend.app.services import small_variant_review_pg
from backend.app.services import small_variant_review_tags
from backend.app.services.small_variant_review_acmg import _normalize_acmg_payload


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


# A stored review as a single-review read returns it: an ACMG record with an analyst's
# own criterion, and a compound-het pairing, so every field the serializer reads is set.
_STORED_REVIEW = {
    "id": "00000000-0000-0000-0000-000000000001",
    "family_id": "family-uuid",
    "variant_key": 7,
    "variant_id": "var-1",
    "classification": "Likely Pathogenic - class 4",
    "tags": ["acmg_class_4", "report"],
    "tag_metadata": {"report": {"updated_by": "admin", "updated_at": "2026-09-01T10:00:00+00:00"}},
    "note": "kept",
    "compound_het_group_id": "group-1",
    "compound_het_partner_variant_ids": ["var-2"],
    "compound_het_gene": "GENE1",
    "compound_het_gene_id": "ENSG1",
    "compound_het_classification": "Pathogenic - class 5",
    "compound_het_tags": ["report"],
    "compound_het_tag_metadata": {},
    "compound_het_note": "pair",
    "compound_het_phase_status": "trans",
    "compound_het_updated_by": "admin",
    "compound_het_updated_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    "acmg": {
        "criteria": [
            {"code": "PS3", "strength": "strong", "accepted": True, "evidence": "RNA assay", "auto_suggested": False},
            {"code": "PM2", "strength": "supporting", "accepted": True, "evidence": None, "auto_suggested": True},
        ],
        "point_total": 5,
        "classification": "VUS - class 3",
        "vus_tier": "hot",
    },
    "acmg_point_total": 5,
    "acmg_class": "acmg_class_3",
    "acmg_evidence_snapshot": {"annotation_set_hash": "h"},
    "updated_by": "admin",
    "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    "updated_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
}


class _ReadRecording(dict):
    """A review row that records which fields are read from it."""

    def __init__(self, row: dict) -> None:
        super().__init__(row)
        self.read: set[str] = set()

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)


def _selected_columns(sql: str) -> set[str]:
    select_list = sql.split("SELECT", 1)[1].split("FROM", 1)[0]
    columns = set()
    for item in (part.strip() for part in select_list.split(",")):
        columns.add(item.rsplit(" AS ", 1)[-1].strip() if " AS " in item else item)
    return columns


@pytest.mark.asyncio
async def test_a_review_list_selects_every_field_a_review_is_served_with() -> None:
    # A list serves each review as a read of that one review does. Its query once left out
    # the ACMG record, so every list served a classified variant with acmg = None, and the
    # ACMG dialog opened from a list was seeded without the saved criteria.
    session = _RecordingSession([_STORED_REVIEW])

    served = await small_variant_review_pg.get_small_variant_review_map(
        session, family_uuid="family-uuid", variant_ids=["var-1"]
    )

    recording = _ReadRecording(_STORED_REVIEW)
    small_variant_review_pg._serialize_review(recording)
    assert recording.read - _selected_columns(session.sql) == set()
    assert served["var-1"] == small_variant_review_pg._serialize_review(dict(_STORED_REVIEW))
    assert served["var-1"].acmg is not None
    assert [(c.code, c.strength, c.accepted, c.evidence) for c in served["var-1"].acmg.criteria] == [
        ("PS3", "strong", True, "RNA assay"),
        ("PM2", "supporting", True, None),
    ]
    assert served["var-1"].acmg_unreadable is False


class _ProjectingSession:
    """Answers a query as Postgres would: the stored rows, cut down to the selected columns."""

    def __init__(self, rows) -> None:
        self.rows = rows
        self.sql: str | None = None

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        selected = _selected_columns(self.sql)
        return _FakeResult([{key: value for key, value in row.items() if key in selected} for row in self.rows])


@pytest.mark.asyncio
async def test_a_review_holding_only_an_acmg_record_counts_as_reviewed() -> None:
    # A classification saved with no tag, class label or note is still a review. The
    # summary's query once selected neither the ACMG record nor the compound-het partners,
    # so such a review was not counted.
    acmg_only = {
        **{key: None for key in _STORED_REVIEW},
        "variant_id": "var-1",
        "tags": [],
        "compound_het_tags": [],
        "compound_het_partner_variant_ids": [],
        "acmg": _STORED_REVIEW["acmg"],
    }
    paired_only = {**acmg_only, "variant_id": "var-2", "acmg": None, "compound_het_partner_variant_ids": ["var-9"]}

    summary = await small_variant_review_pg.get_small_variant_review_summary(
        _ProjectingSession([acmg_only, paired_only]), family_uuid="family-uuid"
    )

    assert summary.reviewed_variant_count == 2


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


# --- a re-classification that keeps the class is audited ------------------------------------------


def _classified(*criteria: tuple[str, str]):
    payload = AcmgClassificationPayload(
        criteria=[AcmgCriterionSelection(code=code, strength=strength, accepted=True) for code, strength in criteria]
    )
    blob, _points, class_key = _normalize_acmg_payload(payload)
    return payload, blob, class_key


def test_a_strength_only_reclassification_is_audited(monkeypatch) -> None:
    _, stored, stored_class = _classified(("PS3", "strong"), ("PM2", "moderate"), ("PP3", "supporting"))
    existing = _stored_review(
        classification=None, tags=[], note=None, acmg=stored, acmg_class=stored_class,
        acmg_point_total=stored["point_total"],
    )
    payload, _, new_class = _classified(("PS3", "strong"), ("PM2", "supporting"), ("PP3", "supporting"))
    assert new_class == stored_class  # the class stays; only PM2's strength changes

    audits, writes, _session = _clear(monkeypatch, existing, SmallVariantReviewUpdate(acmg=payload))

    assert writes == ["update"]
    [audit] = audits
    [event] = clinical_audit_service.diff_review_changes(audit["existing"], audit["new_state"])
    assert event["action"] == "classification"
    assert "PM2 moderate → supporting" in event["summary"]
