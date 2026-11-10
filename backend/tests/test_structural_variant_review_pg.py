"""Structural-variant reviews and their CNV (ClinGen 2019) classification.

The client sends the criteria it applied; the server validates them and recomputes the
points and the class, so a stored classification cannot drift from its criteria.

Every save that changes the classification, the CNV scoring, the tags or the note is
recorded in the hash-chained clinical audit trail, in the same transaction, like a
small-variant review save.

The SV search's classification filter keeps an SV by the class its review shows on the SV page:
a bare class word in its text, else its most severe class tag, else its text (CLIN-77).
"""

from __future__ import annotations

import asyncio
import json
import math
import types
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend.app.schemas import CnvAcmgClassificationPayload, CnvAcmgCriterion, StructuralVariantReviewUpdate
from backend.app.services import clinical_audit_service as cas
from backend.app.services import structural_variant_review_pg as svr
from backend.app.services.access_control import CurrentUser
from backend.app.services.hash_chain import verify_chain

FAMILY = "00000000-0000-0000-0000-00000000f001"
CONTEXT = types.SimpleNamespace(family_uuid=FAMILY, family_id="FAM1", project_ids=["p1"])
USER = CurrentUser(
    id="u1",
    username="reviewer",
    email="reviewer@example.org",
    role="viewer",
    created_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
)


def _payload(kind: str, *criteria: tuple[str, float, bool], **derived) -> CnvAcmgClassificationPayload:
    return CnvAcmgClassificationPayload(
        kind=kind,
        criteria=[CnvAcmgCriterion(code=code, points=points, accepted=accepted) for code, points, accepted in criteria],
        **derived,
    )


# --- the classification is recomputed server-side -----------------------------------------


def test_the_client_total_and_class_are_ignored_and_recomputed() -> None:
    blob, total, class_key = svr._normalize_cnv_acmg_payload(
        _payload("loss", ("2A", 1.0, True), point_total=-2.0, classification="Benign - class 1")
    )
    assert (total, class_key) == (1.0, "cnv_class_5")
    assert blob["point_total"] == 1.0
    assert blob["classification"] == "Pathogenic - class 5"


def test_points_are_clamped_to_the_criterion_range() -> None:
    # 2B (partial overlap of an HI region) allows 0 to 0.90 points.
    blob, total, class_key = svr._normalize_cnv_acmg_payload(_payload("loss", ("2B", 5.0, True)))
    assert blob["criteria"][0]["points"] == 0.9
    assert (total, class_key) == (0.9, "cnv_class_4")


def test_only_accepted_criteria_count_but_all_are_kept() -> None:
    blob, total, class_key = svr._normalize_cnv_acmg_payload(
        _payload("loss", ("2A", 1.0, False), ("3B", 0.45, True))
    )
    assert (total, class_key) == (0.45, "cnv_class_3")
    assert [c["code"] for c in blob["criteria"]] == ["2A", "3B"]


def test_a_gain_is_scored_on_the_gain_criteria() -> None:
    # Gain 2C (identical to an established benign gain) is worth -1.0: benign.
    _blob, total, class_key = svr._normalize_cnv_acmg_payload(_payload("gain", ("2C", -1.0, True)))
    assert (total, class_key) == (-1.0, "cnv_class_1")


def test_an_unknown_criterion_code_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        svr._normalize_cnv_acmg_payload(_payload("loss", ("9Z", 1.0, True)))
    assert refused.value.status_code == 400


def test_a_repeated_criterion_code_is_refused() -> None:
    # CLIN-3: the dialog holds one entry per criterion, and the range caps that entry. Counted
    # twice, 2B at its 0.90 maximum would score 1.80: Pathogenic instead of Likely pathogenic.
    with pytest.raises(HTTPException) as refused:
        svr._normalize_cnv_acmg_payload(_payload("loss", ("2B", 0.9, True), ("2B", 0.9, True)))
    assert refused.value.status_code == 400
    assert refused.value.detail == "Repeated CNV criterion: 2B"

    # Accepted once and not the other time is still the criterion twice.
    with pytest.raises(HTTPException) as refused:
        svr._normalize_cnv_acmg_payload(_payload("gain", ("2A", 1.0, True), ("3A", 0.0, True), ("2A", 1.0, False)))
    assert refused.value.detail == "Repeated CNV criterion: 2A"


def test_an_unknown_kind_is_refused_even_past_the_schema() -> None:
    payload = CnvAcmgClassificationPayload.model_construct(
        kind="duplication", criteria=[CnvAcmgCriterion(code="2A", points=1.0, accepted=True)]
    )
    with pytest.raises(HTTPException) as refused:
        svr._normalize_cnv_acmg_payload(payload)
    assert refused.value.status_code == 400


def test_no_criteria_clears_the_classification() -> None:
    assert svr._normalize_cnv_acmg_payload(None) == (None, None, None)
    assert svr._normalize_cnv_acmg_payload(_payload("loss")) == (None, None, None)


# --- stored classifications are read back, or flagged ---------------------------------------


def test_an_unreadable_stored_classification_is_flagged_not_dropped() -> None:
    review = svr._serialize_review({"variant_id": "sv1", "cnv_acmg": "{not json"})
    assert review.cnv_acmg is None
    assert review.acmg_unreadable is True


def test_a_stored_json_null_is_no_classification() -> None:
    review = svr._serialize_review({"variant_id": "sv1", "cnv_acmg": "null"})
    assert review.cnv_acmg is None
    assert review.acmg_unreadable is False


# --- the reads: a review selection, the summary, the reviews of the SVs listed ------------------
# The report lists its SVs with a review-tag search (``list_matching_structural_variant_review_ids``,
# whose ids the SV search narrows its SQL to: test_sv_filter_before_cap.py); the SV page shows the
# family's review counts and each listed SV's review.


class _ReviewRows:
    """A session holding the family's ``structural_variant_reviews`` rows; it records each
    statement with its parameters."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.executed.append((" ".join(str(statement).split()), dict(params or {})))
        rows = self.rows
        return types.SimpleNamespace(mappings=lambda: types.SimpleNamespace(all=lambda: list(rows)))


_REVIEWED = [
    {"variant_id": "sv-a", "classification": "pathogenic", "tags": ["report"], "note": ""},
    {"variant_id": "sv-b", "classification": None, "tags": [], "note": "Seen in a relative."},
    {"variant_id": "sv-c", "classification": " benign ", "tags": ["artefact", " report "], "note": "  "},
    {"variant_id": "sv-d", "classification": "", "tags": [" "], "note": None},
    # A row without a variant id names no SV: never matched, never counted.
    {"variant_id": "", "classification": "pathogenic", "tags": ["report"], "note": "orphan"},
]


def _matching(session: _ReviewRows, **selection) -> set[str]:
    return asyncio.run(svr.list_matching_structural_variant_review_ids(session, family_uuid=FAMILY, **selection))


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ({"classifications": ["pathogenic "]}, {"sv-a"}),
        ({"classifications": ["benign"]}, {"sv-c"}),
        ({"tags": ["report"]}, {"sv-a", "sv-c"}),
        ({"has_notes": True}, {"sv-b"}),
        # Every part of the selection must hold.
        ({"classifications": ["pathogenic"], "tags": ["artefact"]}, set()),
        ({"tags": ["report"], "has_notes": True}, set()),
        ({"classifications": ["pathogenic", "benign"], "tags": ["report"]}, {"sv-a", "sv-c"}),
    ],
    ids=[
        "classification-asked-with-a-space",
        "classification-stored-with-spaces",
        "tag",
        "notes",
        "classification-and-tag",
        "tag-and-notes",
        "classifications-and-tag",
    ],
)
def test_a_review_selection_matches_the_svs_whose_review_meets_all_of_it(selection: dict, expected: set[str]) -> None:
    session = _ReviewRows(_REVIEWED)
    assert _matching(session, **selection) == expected
    [(sql, params)] = session.executed
    assert "FROM structural_variant_reviews WHERE family_id = CAST(:family_id AS uuid)" in sql
    assert params == {"family_id": FAMILY}


def test_a_blank_review_selection_selects_nothing_and_reads_nothing() -> None:
    session = _ReviewRows(_REVIEWED)
    assert _matching(session, classifications=[" "], tags=[""], has_notes=False) == set()
    assert session.executed == []


def _sv_review(variant_id: str, classification=None, tags=(), cnv_class=None) -> dict:
    return {
        "variant_id": variant_id,
        "classification": classification,
        "tags": list(tags),
        "note": None,
        "cnv_class": cnv_class,
    }


# The reviews of the owner's table of cases (CLIN-77), one per SV, with the class the SV table
# and cards show for each (normalizeReviewClassification of its text and its tags).
_SHOWN_CLASS_SV_REVIEWS = [
    _sv_review("sv-tag", tags=["acmg_class_4", "report"]),  # a class tag alone: LP
    _sv_review("sv-word", classification="VUS"),  # a bare class word: VUS
    _sv_review("sv-text-tag", classification="VUS - class 3", tags=["acmg_class_4"]),  # text and tag apart: LP
    _sv_review("sv-word-tag", classification="VUS", tags=["acmg_class_4"]),  # a class word before a tag: VUS
    # Scored in the CNV dialog, which writes its class label as the text: LP.
    _sv_review("sv-cnv", classification="Likely pathogenic - class 4", cnv_class="cnv_class_4"),
    # Scored VUS in the CNV dialog, then tagged class 5: P.
    _sv_review("sv-cnv-tag", classification="VUS - class 3", tags=["acmg_class_5"], cnv_class="cnv_class_3"),
    # Scored, its text cleared afterwards: the SV page shows no class, so no class filter keeps it.
    _sv_review("sv-cnv-only", cnv_class="cnv_class_4"),
    _sv_review("sv-free", classification="Some free text"),  # free text
    _sv_review("sv-free-tag", classification="Some free text", tags=["acmg_class_1"]),  # free text, tag: B
    _sv_review("sv-old-case", classification="Likely Pathogenic - class 4"),  # a label in an older case
]


@pytest.mark.parametrize(
    ("wanted", "kept"),
    [
        ("Pathogenic - class 5", {"sv-cnv-tag"}),
        ("Likely pathogenic - class 4", {"sv-tag", "sv-text-tag", "sv-cnv", "sv-old-case"}),
        ("VUS - class 3", {"sv-word", "sv-word-tag"}),
        ("Likely benign - class 2", set()),
        ("Benign - class 1", {"sv-free-tag"}),
        # A bare class word asks for its class, as a review's text does.
        ("VUS", {"sv-word", "sv-word-tag"}),
        (" likely PATHOGENIC ", {"sv-tag", "sv-text-tag", "sv-cnv", "sv-old-case"}),
        # Free text keeps the reviews that show it: not one whose class tag shows a class.
        ("some free TEXT", {"sv-free"}),
        # A CNV class key or a tag key is no class the page shows.
        ("cnv_class_4", set()),
        ("acmg_class_4", set()),
    ],
)
def test_a_classification_filter_keeps_an_sv_by_the_class_its_review_shows(wanted: str, kept: set[str]) -> None:
    # CLIN-77 (the owner's decision of 2026-11-10, as CLIN-75 for small variants): the filter keeps
    # an SV by the class its review shows (review_class_label), in any case, not by its
    # classification text alone: "Likely pathogenic - class 4" keeps an SV the SV page shows with
    # that class by its class tag, and drops one shown as class 5 for text saying class 3. The CNV
    # scoring's class counts as the page shows it: through the text the CNV dialog writes, never on
    # its own.
    session = _ReviewRows(_SHOWN_CLASS_SV_REVIEWS)
    assert _matching(session, classifications=[wanted]) == kept
    [(sql, params)] = session.executed
    assert "SELECT variant_id, classification, tags, note FROM structural_variant_reviews" in sql
    assert params == {"family_id": FAMILY}


def test_an_sv_classification_filter_with_several_classes_or_a_tag_filter() -> None:
    session = _ReviewRows(_SHOWN_CLASS_SV_REVIEWS)
    assert _matching(
        session, classifications=["Pathogenic - class 5", "Likely pathogenic - class 4", "VUS - class 3"]
    ) == {"sv-tag", "sv-word", "sv-text-tag", "sv-word-tag", "sv-cnv", "sv-cnv-tag", "sv-old-case"}
    # Every part of the selection must hold.
    assert _matching(session, classifications=["Likely pathogenic - class 4"], tags=["report"]) == {"sv-tag"}
    # The tag filters read the tags: the class tag finds the SVs that hold it.
    assert _matching(session, tags=["acmg_class_4"]) == {"sv-tag", "sv-text-tag", "sv-word-tag"}


def test_the_review_summary_counts_the_reviewed_svs_their_notes_and_tags() -> None:
    session = _ReviewRows(_REVIEWED)

    summary = asyncio.run(svr.get_structural_variant_review_summary(session, family_uuid=FAMILY))

    # sv-d holds nothing but blanks: not reviewed. The row without an id is not counted.
    assert (summary.reviewed_variant_count, summary.note_count) == (3, 1)
    assert summary.tag_counts == {"artefact": 1, "report": 2}
    assert session.executed[0][1] == {"family_id": FAMILY}


def test_the_reviews_of_the_listed_svs_are_read_with_their_ids_bound_as_one_array() -> None:
    session = _ReviewRows(
        [
            {"variant_id": "sv-a", "classification": "pathogenic", "tags": ["report"], "note": None},
            {"variant_id": None, "classification": "benign", "tags": [], "note": None},
        ]
    )

    reviews = asyncio.run(
        svr.get_structural_variant_review_map(session, family_uuid=FAMILY, variant_ids=[" sv-a ", "sv-b", " "])
    )

    assert list(reviews) == ["sv-a"]
    assert (reviews["sv-a"].classification, reviews["sv-a"].tags) == ("pathogenic", ["report"])
    # One text[] parameter, not one per id: an export reads up to 50,001 rows (DATA-3).
    [(sql, params)] = session.executed
    assert "variant_id = ANY(CAST(:variant_ids AS text[]))" in sql
    assert params == {"family_id": FAMILY, "variant_ids": ["sv-a", "sv-b"]}


def test_no_listed_sv_reads_no_review() -> None:
    session = _ReviewRows(_REVIEWED)
    assert asyncio.run(svr.get_structural_variant_review_map(session, family_uuid=FAMILY, variant_ids=["", " "])) == {}
    assert session.executed == []


# --- the save path ----------------------------------------------------------------------------


class _Result:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _ReviewTable:
    """An in-memory structural_variant_reviews table behind the queries the service runs,
    with the clinical_audit_events rows the save appends (and their chain head)."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.statements: list[str] = []
        self.audit: list[dict] = []

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = params or {}
        self.statements.append(sql.split(" ")[0] + (" LOCK" if "pg_advisory_xact_lock" in sql else ""))
        if sql.startswith("SELECT id::text AS id"):
            return _Result(self.rows.get(params["variant_id"]))
        if sql.startswith("SELECT created_at, row_hash FROM clinical_audit_events"):
            return _Result(self.audit[-1] if self.audit else None)
        if sql.startswith("INSERT INTO clinical_audit_events"):
            self.audit.append(dict(params))
        elif sql.startswith("INSERT INTO structural_variant_reviews"):
            self.rows[params["variant_id"]] = self._row(params, review_id=f"r-{len(self.rows) + 1}")
        elif sql.startswith("UPDATE structural_variant_reviews"):
            existing = next(r for r in self.rows.values() if r["id"] == params["review_id"])
            self.rows[existing["variant_id"]] = self._row(params, review_id=existing["id"])
        elif sql.startswith("DELETE FROM structural_variant_reviews"):
            self.rows = {k: r for k, r in self.rows.items() if r["id"] != params["review_id"]}
        return _Result()

    @staticmethod
    def _row(params: dict, *, review_id: str) -> dict:
        return {
            "id": review_id,
            "variant_id": params["variant_id"],
            "classification": params["classification"],
            "tags": json.loads(params["tags_json"]),
            "tag_metadata": json.loads(params["tag_metadata_json"]),
            "note": params["note"],
            "cnv_acmg": json.loads(params["cnv_acmg_json"]) if params["cnv_acmg_json"] else None,
            "cnv_point_total": params["cnv_point_total"],
            "cnv_class": params["cnv_class"],
            "updated_by": params["updated_by"],
            "updated_at": params["updated_at"],
        }

    async def commit(self) -> None:
        return None


def _save(table: _ReviewTable, payload: StructuralVariantReviewUpdate, variant_id: str = "sv1"):
    return asyncio.run(
        svr.upsert_structural_variant_review(
            table, context=CONTEXT, variant_id=variant_id, payload=payload, user=USER
        )
    )


def test_a_new_review_stores_the_recomputed_classification() -> None:
    table = _ReviewTable()
    review = _save(table, StructuralVariantReviewUpdate(cnv_acmg=_payload("loss", ("2A", 1.0, True), point_total=0.0)))

    stored = table.rows["sv1"]
    assert (stored["cnv_point_total"], stored["cnv_class"]) == (1.0, "cnv_class_5")
    assert review.cnv_acmg is not None and review.cnv_acmg.classification == "Pathogenic - class 5"
    # The save is serialised on an advisory lock taken before the current review is read.
    assert table.statements[:2] == ["SELECT LOCK", "SELECT"]


def test_a_second_save_updates_the_review_in_place() -> None:
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(note="first look"))
    review = _save(table, StructuralVariantReviewUpdate(note="second look", classification="likely_pathogenic"))

    assert len(table.rows) == 1
    assert "UPDATE" in table.statements
    assert (review.note, review.classification) == ("second look", "likely_pathogenic")


def test_a_class_label_saved_in_another_case_is_stored_as_the_label_is_now_and_audits_no_change() -> None:
    # ENG-6: a page that sends "Likely Pathogenic - class 4" back has it stored as the label in
    # sentence case, so the trail records no class change.
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(classification="Likely pathogenic - class 4", note="first look"))
    audited = len(table.audit)
    review = _save(table, StructuralVariantReviewUpdate(classification="Likely Pathogenic - class 4", note="first look"))

    assert table.rows["sv1"]["classification"] == review.classification == "Likely pathogenic - class 4"
    assert len(table.audit) == audited


def test_an_empty_save_deletes_the_review() -> None:
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(note="to be cleared"))
    review = _save(table, StructuralVariantReviewUpdate(note="  "))

    assert table.rows == {}
    assert (review.variant_id, review.tags, review.note) == ("sv1", [], None)


def test_an_empty_save_of_an_sv_without_a_review_writes_and_records_nothing() -> None:
    table = _ReviewTable()
    review = _save(table, StructuralVariantReviewUpdate(note="  "))

    assert (review.variant_id, review.tags, review.note) == ("sv1", [], None)
    assert table.rows == {} and table.audit == []
    assert table.statements == ["SELECT LOCK", "SELECT"]


# --- a save keeps the CNV scoring unless it sends one ---------------------------------------
# As a small-variant save keeps ``acmg``: left out, the stored scoring stays; sent, it
# replaces it; sent as null or with no criteria, it clears it.


def _scored(table: _ReviewTable) -> dict:
    _save(
        table,
        StructuralVariantReviewUpdate(
            classification="Pathogenic - class 5",
            note="scored",
            cnv_acmg=_payload("loss", ("2A", 1.0, True)),
        ),
    )
    return dict(table.rows["sv1"])


def _scoring(row: dict) -> tuple:
    return row["cnv_acmg"], row["cnv_point_total"], row["cnv_class"]


def test_a_save_without_cnv_acmg_keeps_the_stored_scoring() -> None:
    table = _ReviewTable()
    scored = _scored(table)
    # What the tag toggle and the review dialog send: no cnv_acmg.
    review = _save(table, StructuralVariantReviewUpdate(classification="Pathogenic - class 5", note="and noted"))

    assert _scoring(table.rows["sv1"]) == _scoring(scored)
    assert table.rows["sv1"]["cnv_class"] == "cnv_class_5"
    assert review.cnv_acmg is not None and review.cnv_acmg.classification == "Pathogenic - class 5"
    assert review.note == "and noted"


def test_emptying_the_review_fields_keeps_a_stored_scoring() -> None:
    table = _ReviewTable()
    scored = _scored(table)
    review = _save(table, StructuralVariantReviewUpdate())  # the review dialog, everything cleared

    assert "sv1" in table.rows  # not deleted: the scoring is still a classification
    assert _scoring(table.rows["sv1"]) == _scoring(scored)
    assert (table.rows["sv1"]["classification"], table.rows["sv1"]["note"]) == (None, None)
    assert review.cnv_acmg is not None


def test_an_explicit_null_clears_the_scoring() -> None:
    table = _ReviewTable()
    _scored(table)
    _save(table, StructuralVariantReviewUpdate(note="scored", cnv_acmg=None))

    assert _scoring(table.rows["sv1"]) == (None, None, None)
    assert table.rows["sv1"]["note"] == "scored"


def test_a_scoring_without_criteria_clears_it() -> None:
    table = _ReviewTable()
    _scored(table)
    _save(table, StructuralVariantReviewUpdate(note="scored", cnv_acmg=_payload("loss")))

    assert _scoring(table.rows["sv1"]) == (None, None, None)


def test_clearing_the_scoring_and_the_rest_deletes_the_review() -> None:
    table = _ReviewTable()
    _scored(table)
    _save(table, StructuralVariantReviewUpdate(cnv_acmg=None))

    assert table.rows == {}


def test_a_sent_scoring_replaces_the_stored_one() -> None:
    table = _ReviewTable()
    _scored(table)
    _save(table, StructuralVariantReviewUpdate(note="scored", cnv_acmg=_payload("gain", ("2C", -1.0, True))))

    stored = table.rows["sv1"]
    assert (stored["cnv_point_total"], stored["cnv_class"]) == (-1.0, "cnv_class_1")
    assert stored["cnv_acmg"]["kind"] == "gain"


def test_an_unreadable_stored_scoring_survives_a_save_that_leaves_it_out() -> None:
    table = _ReviewTable()
    _scored(table)
    table.rows["sv1"]["cnv_acmg"] = "{not json"  # stored, but no longer readable
    review = _save(table, StructuralVariantReviewUpdate(classification="Pathogenic - class 5", note="noted"))

    assert table.rows["sv1"]["cnv_acmg"] == "{not json"
    assert review.acmg_unreadable is True


def test_a_scoring_with_a_repeated_criterion_is_refused_and_writes_nothing() -> None:
    table = _ReviewTable()
    scored = _scored(table)
    audited = len(table.audit)
    repeated = _payload("loss", ("2B", 0.9, True), ("2B", 0.9, True))
    with pytest.raises(HTTPException) as refused:
        _save(table, StructuralVariantReviewUpdate(classification="Pathogenic - class 5", note="scored", cnv_acmg=repeated))
    assert refused.value.status_code == 400
    assert table.rows["sv1"] == scored
    assert len(table.audit) == audited


def test_a_stored_scoring_with_a_repeated_criterion_is_still_served_and_kept() -> None:
    # Only a save that sends a scoring is checked: one stored with a repeated code is read back
    # as stored, and a save that leaves the scoring out keeps it.
    table = _ReviewTable()
    _scored(table)
    written_before = {
        "kind": "loss",
        "criteria": [
            {"code": "2B", "points": 0.9, "accepted": True},
            {"code": "2B", "points": 0.9, "accepted": True},
        ],
        "point_total": 1.8,
        "classification": "Pathogenic - class 5",
    }
    table.rows["sv1"]["cnv_acmg"] = written_before
    review = _save(table, StructuralVariantReviewUpdate(classification="Pathogenic - class 5", note="noted"))

    assert table.rows["sv1"]["cnv_acmg"] == written_before
    assert review.acmg_unreadable is False and review.cnv_acmg is not None
    assert [(c.code, c.points) for c in review.cnv_acmg.criteria] == [("2B", 0.9), ("2B", 0.9)]


def test_a_save_against_a_changed_review_is_refused_and_writes_nothing() -> None:
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(note="another reviewer's note"))
    stale = StructuralVariantReviewUpdate(
        note="mine", expected_updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    with pytest.raises(HTTPException) as refused:
        _save(table, stale)
    assert refused.value.status_code == 409
    assert table.rows["sv1"]["note"] == "another reviewer's note"
    # The refused save leaves no trace in the clinical audit trail either.
    assert [event["action"] for event in table.audit] == ["note"]


def test_an_unknown_tag_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def definitions(session, *, family_uuid, project_ids):
        return [types.SimpleNamespace(key="candidate")]

    monkeypatch.setattr(svr, "list_small_variant_tag_definitions", definitions)
    with pytest.raises(HTTPException) as refused:
        _save(_ReviewTable(), StructuralVariantReviewUpdate(tags=["candidate", "not-a-tag"]))
    assert refused.value.status_code == 400
    assert "not-a-tag" in refused.value.detail


def test_a_tag_the_review_holds_is_kept_after_its_definition_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    # A deleted tag stays on the reviews that hold it. The tag toggle and the review dialog
    # send it back with every save, so refusing it refused every later save of the review,
    # the one removing it included. Only a tag the save adds is checked.
    allowed = ["probe_x", "report"]
    reads: list[str] = []

    async def definitions(session, *, family_uuid, project_ids):
        reads.append(family_uuid)
        return [types.SimpleNamespace(key=key) for key in allowed]

    monkeypatch.setattr(svr, "list_small_variant_tag_definitions", definitions)
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(tags=["probe_x"], note="tagged"))
    allowed = ["report"]  # probe_x is deleted
    reads.clear()

    review = _save(table, StructuralVariantReviewUpdate(tags=["probe_x"], note="noted again"))
    assert (review.tags, review.note) == (["probe_x"], "noted again")
    assert reads == []  # the save adds no tag: the family's tags are not even read

    review = _save(table, StructuralVariantReviewUpdate(tags=["probe_x", "report"], note="noted again"))
    assert review.tags == ["probe_x", "report"] and len(reads) == 1

    review = _save(table, StructuralVariantReviewUpdate(tags=["report"], note="noted again"))
    assert table.rows["sv1"]["tags"] == ["report"]
    # Once removed, the deleted tag cannot be added back.
    with pytest.raises(HTTPException) as refused:
        _save(table, StructuralVariantReviewUpdate(tags=["probe_x", "report"], note="noted again"))
    assert (refused.value.status_code, refused.value.detail) == (
        400,
        "Unknown structural-variant tag(s): probe_x",
    )


def test_a_save_adding_an_unknown_tag_is_refused_beside_a_held_deleted_one(monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = ["probe_x"]

    async def definitions(session, *, family_uuid, project_ids):
        return [types.SimpleNamespace(key=key) for key in allowed]

    monkeypatch.setattr(svr, "list_small_variant_tag_definitions", definitions)
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(tags=["probe_x"]))
    allowed = ["report"]  # probe_x is deleted

    with pytest.raises(HTTPException) as refused:
        _save(table, StructuralVariantReviewUpdate(tags=["probe_x", "probe_y"]))
    # Named alone: the held tag is not what the save got wrong.
    assert refused.value.detail == "Unknown structural-variant tag(s): probe_y"
    assert table.rows["sv1"]["tags"] == ["probe_x"]


def test_a_blank_variant_id_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        _save(_ReviewTable(), StructuralVariantReviewUpdate(note="x"), variant_id="  ")
    assert refused.value.status_code == 400


# --- every change is recorded in the clinical audit trail -------------------------------------


def _audit_rows(table: _ReviewTable) -> list[dict]:
    """The recorded events as the chain verifier reads them back (JSON columns decoded)."""
    return [
        {
            **event,
            "before": json.loads(event["before"]) if event["before"] else None,
            "after": json.loads(event["after"]) if event["after"] else None,
            "metadata": json.loads(event["metadata"]),
        }
        for event in table.audit
    ]


def test_a_cnv_classification_is_recorded_in_the_audit_trail() -> None:
    table = _ReviewTable()
    _save(
        table,
        StructuralVariantReviewUpdate(
            classification="Pathogenic - class 5",
            cnv_acmg=_payload("loss", ("2A", 1.0, True), ("3A", 0.0, False)),
        ),
    )

    [event] = _audit_rows(table)
    assert (event["action"], event["variant_id"], event["family_identifier"]) == ("classification", "sv1", "FAM1")
    assert (event["actor"], event["actor_id"]) == ("reviewer", "u1")
    assert event["summary"] == (
        "Classification unclassified → Pathogenic - class 5; "
        "CNV classification unclassified → Pathogenic - class 5"
    )
    # The criteria the class rests on, with their points, and the whole stored record.
    assert event["after"] == {
        "classification": "Pathogenic - class 5",
        "cnv_class": "cnv_class_5",
        "cnv_kind": "loss",
        "cnv_point_total": 1.0,
        "cnv_criteria": [{"code": "2A", "points": 1.0}],
        "cnv_criteria_detail": [
            {"code": "2A", "points": 1.0, "accepted": True, "evidence": None, "auto_suggested": False},
            {"code": "3A", "points": 0.0, "accepted": False, "evidence": None, "auto_suggested": False},
        ],
    }
    assert event["before"]["cnv_class"] is None and event["before"]["cnv_criteria"] == []
    # Marked as a structural variant, so its id is never read as a small variant's.
    assert event["metadata"] == {"modality": "sv"}


def test_tag_and_note_changes_are_recorded_as_separate_events() -> None:
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(note="first look"))
    _save(table, StructuralVariantReviewUpdate(note="second look", tags=[]))
    table.audit.clear()

    async def definitions(session, *, family_uuid, project_ids):
        return [types.SimpleNamespace(key="report")]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(svr, "list_small_variant_tag_definitions", definitions)
        _save(table, StructuralVariantReviewUpdate(note="second look", tags=["report"]))

    [event] = _audit_rows(table)
    assert (event["action"], event["summary"]) == ("tags", "Tags added report")
    assert (event["before"], event["after"]) == ({"tags": []}, {"tags": ["report"]})


def test_an_unchanged_save_records_nothing() -> None:
    table = _ReviewTable()
    payload = StructuralVariantReviewUpdate(note="same", cnv_acmg=_payload("gain", ("2C", -1.0, True)))
    _save(table, payload)
    recorded = len(table.audit)
    _save(table, payload)
    assert len(table.audit) == recorded == 2  # classification + note, once


def test_cleared_cnv_scoring_is_recorded() -> None:
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(note="scored", cnv_acmg=_payload("loss", ("2A", 1.0, True))))
    _save(table, StructuralVariantReviewUpdate(note="scored", cnv_acmg=_payload("loss")))

    event = _audit_rows(table)[-1]
    assert event["action"] == "classification"
    assert event["summary"] == "CNV classification Pathogenic - class 5 → unclassified"
    assert event["after"]["cnv_criteria"] == [] and event["after"]["cnv_point_total"] is None


def test_a_points_only_change_is_recorded_as_a_criteria_update() -> None:
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(cnv_acmg=_payload("loss", ("2B", 0.15, True))))
    _save(table, StructuralVariantReviewUpdate(cnv_acmg=_payload("loss", ("2B", 0.30, True))))

    event = _audit_rows(table)[-1]
    assert event["summary"] == "CNV criteria updated (VUS - class 3): 2B 0.15 → 0.3 points; total 0.15 → 0.3"
    assert (event["before"]["cnv_point_total"], event["after"]["cnv_point_total"]) == (0.15, 0.3)


def test_an_evidence_only_rescoring_is_recorded() -> None:
    def scoring(evidence: str) -> CnvAcmgClassificationPayload:
        return CnvAcmgClassificationPayload(
            kind="loss",
            criteria=[
                CnvAcmgCriterion(code="2A", points=1.0, accepted=True, evidence=evidence),
                CnvAcmgCriterion(code="4D", points=0.0, accepted=False, auto_suggested=True),
            ],
        )

    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(cnv_acmg=scoring("HI gene")))
    table.audit.clear()
    _save(table, StructuralVariantReviewUpdate(cnv_acmg=scoring("HI gene, whole")))
    _save(table, StructuralVariantReviewUpdate(cnv_acmg=scoring("HI gene, whole")))  # unchanged

    [event] = _audit_rows(table)
    assert event["summary"] == "CNV criteria updated (Pathogenic - class 5): 2A evidence edited"
    assert event["before"]["cnv_criteria_detail"][0]["evidence"] == "HI gene"
    assert event["after"]["cnv_criteria_detail"][0]["evidence"] == "HI gene, whole"


def test_deleting_a_review_records_what_was_removed() -> None:
    async def definitions(session, *, family_uuid, project_ids):
        return [types.SimpleNamespace(key="report")]

    table = _ReviewTable()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(svr, "list_small_variant_tag_definitions", definitions)
        _save(
            table,
            StructuralVariantReviewUpdate(
                classification="Likely pathogenic", tags=["report"], note="to report"
            ),
        )
    table.audit.clear()
    _save(table, StructuralVariantReviewUpdate())  # an empty save deletes the review

    assert table.rows == {}
    events = _audit_rows(table)
    assert [event["action"] for event in events] == ["classification", "tags", "note"]
    assert [event["summary"] for event in events] == [
        "Classification Likely pathogenic → unclassified",
        "Tags removed report",
        "Note removed",
    ]


def test_a_save_that_keeps_the_scoring_records_only_what_changed() -> None:
    table = _ReviewTable()
    _save(
        table,
        StructuralVariantReviewUpdate(
            classification="Pathogenic - class 5",
            note="scored",
            cnv_acmg=_payload("loss", ("2A", 1.0, True)),
        ),
    )
    table.audit.clear()
    # The review dialog: no cnv_acmg, so the scoring is kept and not recorded as cleared.
    _save(table, StructuralVariantReviewUpdate(classification="Pathogenic - class 5", note="scored, noted"))

    assert [(event["action"], event["summary"]) for event in _audit_rows(table)] == [
        ("note", "Note updated")
    ]


def test_the_recorded_events_form_a_verifiable_chain() -> None:
    table = _ReviewTable()
    _save(table, StructuralVariantReviewUpdate(note="n1", cnv_acmg=_payload("loss", ("2A", 1.0, True))))
    # 0.30 + 0.15 - 0.45 is a hair below zero and rounds to a -0.0 point total.
    _save(
        table,
        StructuralVariantReviewUpdate(
            note="n2",
            cnv_acmg=_payload("loss", ("4C", 0.30, True), ("2H", 0.15, True), ("5D", -0.45, True)),
        ),
    )
    _save(table, StructuralVariantReviewUpdate(cnv_acmg=None))  # clears the scoring: the review is deleted

    rows = _audit_rows(table)
    assert len(rows) == 6
    assert rows[0]["prev_hash"] is None
    assert all(later["prev_hash"] == earlier["row_hash"] for earlier, later in zip(rows, rows[1:]))
    assert verify_chain(rows, cas._clinical_chain_payload).verified
    # The stored total is -0.0; the audit records 0.0, the value Postgres reads back.
    assert table.audit and math.copysign(1.0, rows[2]["after"]["cnv_point_total"]) == 1.0
