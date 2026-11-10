"""Small-variant review persistence (``services/small_variant_review_pg.py``).

A review list selects every field a review is served with and binds its variant ids as one
array (DATA-3); the summary counts a review that holds only an ACMG record; clearing a review,
and a re-classification that changes only a criterion's strength, are on the clinical audit
trail in the save's transaction; a tag the review holds is kept whatever became of its
definition, and only a tag the save adds is checked. A classification filter, here and on the
structural-variant page, matches a review's label whatever its case (ENG-6); the small-variant
one keeps a review, or its pair review, by the class it shows: its class tag, a bare class word
or its text (CLIN-75).
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend.app.schemas import AcmgClassificationPayload, AcmgCriterionSelection, SmallVariantReviewUpdate
from backend.app.services import clinical_audit_service
from backend.app.services import small_variant_review_pg
from backend.app.services import small_variant_review_tags
from backend.app.services import structural_variant_review_pg
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


@pytest.mark.parametrize(
    "lister",
    [
        small_variant_review_pg.list_matching_small_variant_review_ids,
        structural_variant_review_pg.list_matching_structural_variant_review_ids,
    ],
    ids=["small", "structural"],
)
def test_a_classification_filter_matches_a_review_whatever_the_case_of_its_label(lister) -> None:
    # ENG-6: the class labels are in sentence case ("Likely pathogenic - class 4"). A review or a
    # link that holds "Likely Pathogenic - class 4" and one that holds the label find each other.
    session = _RecordingSession(
        [
            {"variant_id": "1-100-A-G", "classification": "Likely Pathogenic - class 4", "tags": [], "note": None},
            {"variant_id": "1-200-A-G", "classification": "Likely pathogenic - class 4", "tags": [], "note": None},
            {"variant_id": "1-300-A-G", "classification": "VUS - class 3", "tags": [], "note": None},
            {"variant_id": "1-400-A-G", "classification": None, "tags": [], "note": None},
        ]
    )
    for wanted in ("Likely pathogenic - class 4", "Likely Pathogenic - class 4", " likely PATHOGENIC - class 4 "):
        assert asyncio.run(lister(session, family_uuid="f1", classifications=[wanted])) == {
            "1-100-A-G",
            "1-200-A-G",
        }, wanted
    assert asyncio.run(lister(session, family_uuid="f1", classifications=["vus - CLASS 3"])) == {"1-300-A-G"}


def test_a_compound_het_pair_classification_matches_whatever_its_case() -> None:
    session = _RecordingSession(
        [
            {
                "variant_id": "1-100-A-G",
                "classification": None,
                "tags": [],
                "compound_het_classification": "Likely Pathogenic - class 4",
                "compound_het_tags": [],
            }
        ]
    )
    assert asyncio.run(
        small_variant_review_pg.list_matching_small_variant_review_ids(
            session, family_uuid="f1", classifications=["Likely pathogenic - class 4"]
        )
    ) == {"1-100-A-G"}


def _review(variant_id: str, classification=None, tags=(), pair_classification=None, pair_tags=()):
    return {
        "variant_id": variant_id,
        "classification": classification,
        "tags": list(tags),
        "note": None,
        "compound_het_classification": pair_classification,
        "compound_het_tags": list(pair_tags),
        "compound_het_note": None,
    }


# A review of each shape the classification filter reads (CLIN-75), one per variant.
_SHOWN_CLASS_REVIEWS = [
    _review("1-100-A-G", tags=["acmg_class_4", "report"]),  # a class tag alone
    _review("1-200-A-G", classification="VUS"),  # a bare class word
    _review("1-300-A-G", classification="VUS - class 3", tags=["acmg_class_4"]),  # text and tag apart
    _review("1-400-A-G", pair_tags=["acmg_class_5"]),  # the pair review's class tag alone
    _review("1-500-A-G", classification="Some free text"),  # free text
    _review("1-600-A-G", classification="Some free text", tags=["acmg_class_1"]),  # free text, class tag
    _review("1-700-A-G", classification="Likely Pathogenic - class 4"),  # a label in another case
]


@pytest.mark.parametrize(
    ("wanted", "kept"),
    [
        ("Pathogenic - class 5", {"1-400-A-G"}),
        ("Likely pathogenic - class 4", {"1-100-A-G", "1-300-A-G", "1-700-A-G"}),
        ("VUS - class 3", {"1-200-A-G"}),
        ("Benign - class 1", {"1-600-A-G"}),
        # A bare class word asks for its class, as a review's text does.
        ("VUS", {"1-200-A-G"}),
        (" likely PATHOGENIC ", {"1-100-A-G", "1-300-A-G", "1-700-A-G"}),
        # Free text keeps the reviews that show it: not one whose class tag shows a class.
        ("some free TEXT", {"1-500-A-G"}),
        ("acmg_class_4", set()),
    ],
)
def test_a_classification_filter_keeps_a_review_by_the_class_it_shows(wanted, kept) -> None:
    # CLIN-75 (the owner's decision of 2026-11-10): the filter keeps a review, or its pair
    # review, by the class it shows (review_class_label), in any case, not by its classification
    # text alone: "Likely pathogenic - class 4" keeps a variant the family page shows with that
    # class by its class tag, and "VUS - class 3" leaves out one shown as class 4.
    session = _RecordingSession(_SHOWN_CLASS_REVIEWS)
    assert asyncio.run(
        small_variant_review_pg.list_matching_small_variant_review_ids(
            session, family_uuid="f1", classifications=[wanted]
        )
    ) == kept


def test_a_classification_filter_with_several_classes_keeps_a_review_showing_any_of_them() -> None:
    session = _RecordingSession(_SHOWN_CLASS_REVIEWS)
    assert asyncio.run(
        small_variant_review_pg.list_matching_small_variant_review_ids(
            session,
            family_uuid="f1",
            # The P/LP/VUS quick choice.
            classifications=["Pathogenic - class 5", "Likely pathogenic - class 4", "VUS - class 3"],
        )
    ) == {"1-100-A-G", "1-200-A-G", "1-300-A-G", "1-400-A-G", "1-700-A-G"}


def test_a_classification_filter_with_a_tag_filter_needs_both() -> None:
    session = _RecordingSession(_SHOWN_CLASS_REVIEWS)
    assert asyncio.run(
        small_variant_review_pg.list_matching_small_variant_review_ids(
            session, family_uuid="f1", classifications=["Likely pathogenic - class 4"], tags=["report"]
        )
    ) == {"1-100-A-G"}


@pytest.mark.asyncio
async def test_get_small_variant_review_map_binds_variant_ids_as_one_array() -> None:
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
    # One text[] parameter for every id, not one parameter per id (DATA-3).
    assert "variant_id = ANY(CAST(:variant_ids AS text[]))" in session.sql
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
    "classification": "Likely pathogenic - class 4",
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
    # A list serves each review as a read of that one review does. Its query selects the ACMG
    # record too: without it every list would serve a classified variant with acmg = None, and
    # the ACMG dialog opened from a list would be seeded without the saved criteria.
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
    # A classification saved with no tag, class label or note is still a review: the
    # summary's query selects the ACMG record and the compound-het partners, so such a
    # review is counted.
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

_CLEARED = {"acmg_class": None, "acmg": None, "classification": None, "tags": [], "note": None}


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
        session.written = kwargs.get("fields")

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
        assembly_id="assembly-uuid",
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
        "Classification Pathogenic (class 5) → unclassified; classification text Pathogenic → none",
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


# --- the classification text is audited, however the save changed it (TRACE-20) -----------------


def test_a_classification_text_changed_through_the_api_alone_is_audited(monkeypatch) -> None:
    existing = _stored_review()
    payload = SmallVariantReviewUpdate(
        classification="  Likely pathogenic - class 4 ",
        tags=["acmg_class_5", "report"],
        note="reported",
    )

    audits, writes, session = _clear(monkeypatch, existing, payload)

    assert writes == ["update"]
    assert session.written["classification"] == "Likely pathogenic - class 4"
    [audit] = audits
    assert audit["new_state"]["classification"] == "Likely pathogenic - class 4"
    [event] = clinical_audit_service.diff_review_changes(audit["existing"], audit["new_state"])
    assert event["action"] == "classification"
    assert event["summary"] == "Classification text Pathogenic → Likely pathogenic - class 4"
    assert (event["before"]["classification"], event["after"]["classification"]) == (
        "Pathogenic",
        "Likely pathogenic - class 4",
    )
    # The ACMG record the save kept is recorded unchanged on both sides.
    assert event["before"]["criteria"] == event["after"]["criteria"] == ["PVS1"]


def test_a_save_that_leaves_the_classification_text_out_records_its_removal(monkeypatch) -> None:
    existing = _stored_review()
    payload = SmallVariantReviewUpdate(tags=["acmg_class_5", "report"], note="reported")

    audits, _writes, session = _clear(monkeypatch, existing, payload)

    assert session.written["classification"] is None
    [audit] = audits
    [event] = clinical_audit_service.diff_review_changes(audit["existing"], audit["new_state"])
    assert event["summary"] == "Classification text Pathogenic → none"


def test_a_re_save_with_the_same_classification_text_audits_nothing(monkeypatch) -> None:
    existing = _stored_review()
    payload = SmallVariantReviewUpdate(
        classification="Pathogenic", tags=["acmg_class_5", "report"], note="reported"
    )

    audits, writes, _session = _clear(monkeypatch, existing, payload)

    assert writes == ["update"]
    [audit] = audits
    assert clinical_audit_service.diff_review_changes(audit["existing"], audit["new_state"]) == []


# --- a quick tag toggle changes the tags alone (CLIN-76) -------------------------------------------
# The quick tag toggle (quickTagTogglePayload in smallVariantReview.ts) sends the classification
# text back as stored, as these saves do: a tags event alone. Sending the class the review shows,
# read from its class tag, would store "Likely pathogenic - class 4" when toggling Report on a
# review of text "VUS - class 3" beside the tag of class 4, and the trail would record a change of
# classification text no one made (TRACE-20).


@pytest.mark.parametrize(
    ("classification", "tags", "shown"),
    [
        ("VUS - class 3", ["acmg_class_4"], "Likely pathogenic - class 4"),
        ("VUS", ["acmg_class_4"], "VUS - class 3"),
        (None, ["acmg_class_4"], "Likely pathogenic - class 4"),
        ("Some free text", ["acmg_class_1", "acmg_vus_hot"], "Benign - class 1"),
    ],
)
def test_a_quick_tag_toggle_records_the_tag_and_no_classification_change(
    monkeypatch, classification, tags, shown
) -> None:
    existing = _stored_review(classification=classification, tags=tags)
    toggled = sorted([*tags, "report"])

    # The toggle as it is sent: the classification text as stored.
    payload = SmallVariantReviewUpdate(classification=classification, tags=toggled, note="reported")
    audits, writes, _reads = _save_tags(monkeypatch, existing, payload, allowed=["report"])

    assert writes == ["update"]
    [audit] = audits
    assert audit["new_state"]["classification"] == classification
    events = clinical_audit_service.diff_review_changes(audit["existing"], audit["new_state"])
    assert [(event["action"], event["summary"]) for event in events] == [("tags", "Tags added report")]

    # Sent with the class shown instead: recorded as a classification change.
    payload = SmallVariantReviewUpdate(classification=shown, tags=toggled, note="reported")
    audits, _writes, _reads = _save_tags(monkeypatch, existing, payload, allowed=["report"])
    [audit] = audits
    events = clinical_audit_service.diff_review_changes(audit["existing"], audit["new_state"])
    assert [event["action"] for event in events] == ["classification", "tags"]
    assert events[0]["summary"] == f"Classification text {classification or 'none'} → {shown}"


# --- a tag the review holds is kept, whatever became of its definition ----------------------------
# The quick tag toggle and the review dialog send the stored tags back with every save. A
# tag deleted since (or one outside the family's projects) refuses no later save of the
# review, the one removing it included: only a tag the save adds is checked.


def _save_tags(monkeypatch, existing: dict | None, payload: SmallVariantReviewUpdate, allowed: list[str]):
    """Save ``payload`` over ``existing`` with ``allowed`` as the family's usable tags.

    Returns the audit calls, the writes made and the family ids the usable tags were read for.
    """
    reads: list[str] = []

    async def _definitions(session_, *, family_uuid, project_ids):
        reads.append(family_uuid)
        return [types.SimpleNamespace(key=key) for key in allowed]

    monkeypatch.setattr(small_variant_review_pg, "list_small_variant_tag_definitions", _definitions)
    audits, writes, _session = _clear(monkeypatch, existing, payload)
    return audits, writes, reads


def test_a_save_keeps_a_tag_the_review_holds_after_its_definition_is_gone(monkeypatch) -> None:
    existing = _stored_review(tags=["acmg_class_5", "probe_x", "report"])
    payload = SmallVariantReviewUpdate(
        classification="Pathogenic", tags=["acmg_class_5", "probe_x", "report"], note="reported, noted again"
    )
    audits, writes, reads = _save_tags(monkeypatch, existing, payload, allowed=["acmg_class_5", "report"])

    assert writes == ["update"]
    assert reads == []  # the save adds no tag: the family's tags are not even read
    [audit] = audits
    assert audit["new_state"]["tags"] == ["acmg_class_5", "probe_x", "report"]


def test_a_save_removing_a_tag_whose_definition_is_gone_goes_ahead(monkeypatch) -> None:
    existing = _stored_review(tags=["probe_x", "report"])
    payload = SmallVariantReviewUpdate(classification="Pathogenic", tags=["report"], note="reported")
    audits, writes, reads = _save_tags(monkeypatch, existing, payload, allowed=["report"])

    assert (writes, reads) == (["update"], [])
    assert audits[0]["new_state"]["tags"] == ["report"]


def test_a_class_label_saved_in_another_case_is_stored_as_the_label_is_now(monkeypatch) -> None:
    # ENG-6: a client may send "Likely Pathogenic - class 4" back with a save that leaves the
    # class alone. It is stored as the label is, so the review keeps the label's sentence case;
    # text that names no class label is stored as sent.
    from backend.app.services.review_pg_utils import _stored_classification

    existing = _stored_review(classification="Likely pathogenic - class 4", tags=["acmg_class_4", "report"])
    payload = SmallVariantReviewUpdate(
        classification=" Likely Pathogenic - class 4 ", tags=["acmg_class_4", "report"], note="reported"
    )
    _audits, writes, session = _clear(monkeypatch, existing, payload)

    assert writes == ["update"]
    assert session.written["classification"] == "Likely pathogenic - class 4"

    assert _stored_classification("vus - CLASS 3") == "VUS - class 3"
    assert _stored_classification("Likely pathogenic, see note") == "Likely pathogenic, see note"
    assert _stored_classification("   ") is None and _stored_classification(None) is None


def test_a_save_may_add_a_usable_tag_beside_one_whose_definition_is_gone(monkeypatch) -> None:
    existing = _stored_review(tags=["probe_x"])
    payload = SmallVariantReviewUpdate(classification="Pathogenic", tags=["probe_x", "review"], note="reported")
    _audits, writes, reads = _save_tags(monkeypatch, existing, payload, allowed=["review"])

    assert (writes, reads) == (["update"], ["family-uuid"])


def test_a_save_adding_a_tag_the_family_cannot_use_is_refused(monkeypatch) -> None:
    # A deleted tag included: once removed from a review, it cannot be added back.
    existing = _stored_review(tags=["probe_x"])
    for added in ("probe_y", "gone_tag"):
        with pytest.raises(HTTPException) as refused:
            _save_tags(
                monkeypatch, existing, SmallVariantReviewUpdate(tags=["probe_x", added]), allowed=["review"]
            )
        # Named alone: the held tag is not what the save got wrong.
        assert (refused.value.status_code, refused.value.detail) == (
            400,
            f"Unknown small-variant tag(s): {added}",
        )


def test_a_pair_save_keeps_a_pair_tag_the_review_holds(monkeypatch) -> None:
    existing = _stored_review(
        compound_het_group_id="group-1",
        compound_het_partner_variant_ids=["1-200-C-T"],
        compound_het_tags=["probe_x"],
    )
    writes: list[str] = []

    async def _noop(*args, **kwargs):
        writes.append("pair")

    monkeypatch.setattr(small_variant_review_pg, "variants_share_gene", lambda *args: True)
    monkeypatch.setattr(small_variant_review_pg, "has_affected_het_call", lambda *args: True)
    monkeypatch.setattr(small_variant_review_pg, "_clear_compound_het_group", _noop)
    monkeypatch.setattr(small_variant_review_pg, "_insert_review_row", _noop)

    def pair(tags: list[str]) -> SmallVariantReviewUpdate:
        return SmallVariantReviewUpdate(
            classification="Pathogenic",
            tags=["acmg_class_5", "report"],
            note="reported",
            compound_het={"partner_variant_id": "1-200-C-T", "tags": tags},
        )

    _audits, saved, reads = _save_tags(monkeypatch, existing, pair(["probe_x"]), allowed=["acmg_class_5", "report"])
    assert "update" in saved and reads == []

    with pytest.raises(HTTPException) as refused:
        _save_tags(monkeypatch, existing, pair(["probe_x", "probe_y"]), allowed=["acmg_class_5", "report"])
    assert refused.value.detail == "Unknown small-variant tag(s): probe_y"
