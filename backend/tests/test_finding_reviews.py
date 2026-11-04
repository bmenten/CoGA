"""The reviews of repeat loci and Paraphase regions (docs/repeat-paraphase-review-design.md).

What a review rests on and what the sign-out reads of it, without a database: the two
classification scales (B4), the calls a review freezes (a repeat locus's record entry, a
Paraphase region's without its load times), whether they are still the family's (B5), which
reviews the record holds and the drift gate counts (B13), which abnormal calls the gate of
unreviewed findings lists (B8, B12), and the clinical audit events of a save.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from typing import Any, get_args

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app.schemas import ParaphaseReviewUpdate, RepeatExpansionReviewUpdate
from backend.app.schemas.reviews import ParaphaseReviewClassification, RepeatReviewClassification
from backend.app.services import clinical_audit_service as audit
from backend.app.services import finding_review_pg as frp


def _call(sample: str, status: str, counts: list[int], warning: dict | None = None) -> dict[str, Any]:
    return {
        "sample": sample,
        "genotype": "0/1",
        "status": status,
        "note": None,
        "alleles": [
            {
                "repeat_count": count,
                "bp_length": count * 3,
                "support_reads": 20,
                "interruption_label": None,
                "status": status,
                "count_unit_warning": warning if index == len(counts) - 1 else None,
            }
            for index, count in enumerate(counts)
        ],
    }


def _locus(locus_id: str, gene: str, status: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "locus_id": locus_id,
        "gene": gene,
        "disease": "A disease",
        "inheritance": "AD",
        "chrom": "4",
        "start": 100,
        "end": 160,
        "motif": "CAG",
        "thresholds": {"intermediate_min": 27, "premutation_min": None, "pathogenic_min": 36},
        "status": status,
        "count_units": {"validated": False},
        "calls": calls,
    }


_WARNING = {"count": 34, "nearest": {"above": 36}, "margin": 3}


def _repeat_section(*loci: dict[str, Any]) -> dict[str, Any]:
    return {"catalogue": {"loci": 2, "digest": "d"}, "count_unit_margin": 3, "loci": list(loci)}


def _region(gene: str, statuses: dict[str, str]) -> dict[str, Any]:
    return {
        "gene": gene,
        "region_id": gene,
        "region": gene.upper(),
        "medically_relevant": True,
        "status_rule": None,
        "expected_copy_number": None,
        "samples": [
            {"sample": sample, "status": status, "total_cn": 2, "gene_cn": 1, "loaded_at": "2026-10-01T10:00:00+00:00"}
            for sample, status in statuses.items()
        ],
    }


def _paraphase_section(*genes: dict[str, Any]) -> dict[str, Any]:
    return {"catalogue": {"regions": 1, "digest": "d"}, "genes": list(genes)}


# --- The scales -------------------------------------------------------------------------


def test_each_type_takes_its_own_scale_and_refuses_any_other_value() -> None:
    assert RepeatExpansionReviewUpdate(classification="reduced_penetrance", expected_updated_at=None).classification == "reduced_penetrance"
    assert ParaphaseReviewUpdate(classification="risk_allele", expected_updated_at=None).classification == "risk_allele"
    # Another type's value, an ACMG class, or a word: refused (422 at the API).
    for bad in ("carrier", "pathogenic", "acmg_class_5", "Pathogenic expansion"):
        with pytest.raises(ValidationError):
            RepeatExpansionReviewUpdate(classification=bad, expected_updated_at=None)
    for bad in ("premutation", "pathogenic_expansion", "likely_pathogenic"):
        with pytest.raises(ValidationError):
            ParaphaseReviewUpdate(classification=bad, expected_updated_at=None)
    # No classification is no judgement, not a refusal.
    assert RepeatExpansionReviewUpdate(expected_updated_at=None).classification is None


def test_every_value_of_each_scale_has_its_label_and_the_tables_check_takes_it() -> None:
    assert set(frp.REPEAT_CLASSIFICATION_LABELS) == set(get_args(RepeatReviewClassification))
    assert set(frp.PARAPHASE_CLASSIFICATION_LABELS) == set(get_args(ParaphaseReviewClassification))
    from pathlib import Path

    baseline = (Path(__file__).resolve().parents[1] / "db/schema/postgres/03_assay.sql").read_text()
    for table, values in (
        ("repeat_expansion_reviews", get_args(RepeatReviewClassification)),
        ("paraphase_reviews", get_args(ParaphaseReviewClassification)),
    ):
        check = next(line for line in baseline.splitlines() if f"{table}_classification_check" in line)
        assert sorted(value for value in values if f"'{value}'::text" in check) == sorted(values)


# --- What a review rests on ----------------------------------------------------------------


def test_a_repeat_review_rests_on_its_locus_entry_alone() -> None:
    htt = _locus("HTT", "HTT", "pathogenic", [_call("PROBAND", "pathogenic", [18, 42])])
    fmr1 = _locus("FXS_FMR1", "FMR1", "normal", [_call("PROBAND", "normal", [29, 30])])
    section = _repeat_section(htt, fmr1)
    assert frp.repeat_locus_evidence(section, "HTT") == htt
    assert frp.repeat_locus_evidence(section, "ATXN1") is None
    # A copy: freezing it never ties it to the section it came from.
    frozen = frp.repeat_locus_evidence(section, "HTT")
    assert frozen is not None
    frozen["calls"][0]["status"] = "normal"
    assert htt["calls"][0]["status"] == "pathogenic"


def test_a_paraphase_review_rests_on_its_region_without_the_load_times() -> None:
    section = _paraphase_section(_region("smn1", {"PROBAND": "carrier"}))
    entry = frp.paraphase_region_evidence(section, "smn1")
    assert entry is not None and "loaded_at" not in entry["samples"][0]
    assert entry["samples"][0]["total_cn"] == 2
    # The same result loaded again changes no call: the evidence is the same.
    again = _paraphase_section(_region("smn1", {"PROBAND": "carrier"}))
    again["genes"][0]["samples"][0]["loaded_at"] = "2026-10-28T09:00:00+00:00"
    assert frp.paraphase_region_evidence(again, "smn1") == entry


def test_the_drift_of_a_review_says_whether_its_calls_are_the_familys() -> None:
    frozen = _locus("HTT", "HTT", "intermediate", [_call("PROBAND", "intermediate", [18, 34])])
    snapshot = {"captured_at": "2026-10-28T10:00:00+00:00", "evidence": frozen}
    assert frp.finding_drift(snapshot, frozen) == {"status": "unchanged", "changes": []}
    assert frp.finding_drift(None, frozen) == {"status": "no_snapshot", "changes": []}
    assert frp.finding_drift({"captured_at": "x"}, frozen)["status"] == "no_snapshot"
    assert frp.finding_drift(snapshot, None) == {"status": "not_found", "changes": []}
    assert frp.finding_drift(snapshot, frozen, readable=False) == {"status": "unverifiable", "changes": []}
    # A re-import that reads 38 where the review froze 34: changed, naming the member's allele.
    now = _locus("HTT", "HTT", "pathogenic", [_call("PROBAND", "pathogenic", [18, 38])])
    drift = frp.finding_drift(snapshot, now)
    assert drift["status"] == "changed"
    assert "calls[PROBAND].alleles[1].repeat_count: 34 → 38" in drift["changes"]
    assert "calls[PROBAND].status: intermediate → pathogenic" in drift["changes"]
    assert "status: intermediate → pathogenic" in drift["changes"]
    assert drift["changes"] == sorted(drift["changes"])


def test_a_member_added_or_gone_is_named_as_absent() -> None:
    before = _locus("HTT", "HTT", "normal", [_call("PROBAND", "normal", [18, 20])])
    after = _locus("HTT", "HTT", "normal", [_call("PROBAND", "normal", [18, 20]), _call("MOTHER", "normal", [17, 19])])
    changes = frp.evidence_changes(before, after)
    assert "calls[MOTHER].status: absent → normal" in changes
    assert frp.evidence_changes(after, before)[0].endswith("→ absent")


def test_a_drift_names_twenty_differences_and_counts_the_rest() -> None:
    before = {"values": {str(index): index for index in range(30)}}
    after = {"values": {str(index): index + 1 for index in range(30)}}
    changes = frp.evidence_changes(before, after)
    assert len(changes) == 21 and changes[-1] == "and 10 other differences"


def test_only_a_classification_or_the_report_tag_bears_a_judgement() -> None:
    assert frp.bears_judgement({"classification": "premutation", "tags": []})
    assert frp.bears_judgement({"classification": None, "tags": ["report"]})
    assert not frp.bears_judgement({"classification": None, "tags": ["review"], "note": "Checked"})
    assert frp.has_review_content({"classification": None, "tags": [], "note": "Checked in IGV"})
    assert frp.has_review_content({"tags": ["excluded"]})
    assert not frp.has_review_content({"classification": None, "tags": [], "note": "  "})
    assert not frp.has_review_content(None)


def test_the_evidence_digest_is_the_canonical_hash_of_the_frozen_calls() -> None:
    entry = _locus("HTT", "HTT", "normal", [_call("PROBAND", "normal", [18, 20])])
    reordered = dict(reversed(list(entry.items())))
    assert frp.evidence_digest({"evidence": entry}) == frp.evidence_digest({"evidence": reordered})
    assert frp.evidence_digest(None) is None and frp.evidence_digest({"captured_at": "x"}) is None


# --- What the sign-out reads -----------------------------------------------------------


def _context(assembly_id: str | None = "a1") -> Any:
    return SimpleNamespace(family_uuid="f1", family_id="FAM1", assembly_id=assembly_id)


def _rows(monkeypatch, repeat: dict[str, dict], paraphase: dict[str, dict]) -> None:
    async def _review_rows(session, kind, *, family_uuid, assembly_id):
        if not assembly_id:
            return {}
        return repeat if kind is frp.REPEAT else paraphase

    monkeypatch.setattr(frp, "finding_review_rows", _review_rows)


def _row(key: str, **fields: Any) -> dict[str, Any]:
    return {
        "key": key,
        "classification": None,
        "tags": [],
        "note": None,
        "evidence_snapshot": None,
        "updated_by": "analyst",
        "updated_at": "2026-10-28T10:00:00+00:00",
        **fields,
    }


def test_the_workspace_counts_the_reviews_that_say_anything(monkeypatch) -> None:
    _rows(
        monkeypatch,
        {
            "HTT": _row("HTT", classification="pathogenic_expansion", tags=["report", "review"]),
            "FXS_FMR1": _row("FXS_FMR1", note="Looked at it", tags=["review"]),
            # An empty review counts for nothing.
            "SCA1": _row("SCA1", note="  "),
        },
        {"smn1": _row("smn1", classification="carrier")},
    )
    summary = asyncio.run(frp.finding_review_summary(None, _context()))  # type: ignore[arg-type]
    assert summary.repeat.model_dump() == {
        "reviewed_count": 2,
        "classified_count": 1,
        "note_count": 1,
        "tag_counts": {"report": 1, "review": 2},
    }
    assert (summary.paraphase.reviewed_count, summary.paraphase.classified_count) == (1, 1)
    # Without an assembly the family holds no reviews.
    empty = asyncio.run(frp.finding_review_summary(None, _context(None)))  # type: ignore[arg-type]
    assert empty.repeat.reviewed_count == 0 and empty.paraphase.tag_counts == {}


def test_the_record_holds_the_reviews_that_bear_a_judgement_with_their_drift(monkeypatch) -> None:
    htt = _locus("HTT", "HTT", "pathogenic", [_call("PROBAND", "pathogenic", [18, 42])])
    frozen_old = _locus("HTT", "HTT", "intermediate", [_call("PROBAND", "intermediate", [18, 34])])
    _rows(
        monkeypatch,
        {
            "HTT": _row(
                "HTT",
                classification="pathogenic_expansion",
                tags=["report"],
                note="Confirmed",
                evidence_snapshot={"captured_at": "2026-10-20T10:00:00+00:00", "evidence": frozen_old},
            ),
            # A note alone: not in the record's list, and not checked for drift.
            "FXS_FMR1": _row("FXS_FMR1", note="Looked at it"),
        },
        {"smn1": _row("smn1", classification="carrier", evidence_snapshot=None)},
    )
    sections = {
        "repeat_expansions": _repeat_section(htt),
        "paraphase": _paraphase_section(_region("smn1", {"PROBAND": "carrier"})),
    }
    record = asyncio.run(frp.finding_reviews_for_record(None, _context(), sections))  # type: ignore[arg-type]
    [repeat] = record["repeat_expansion_reviews"]
    assert repeat["key"] == "HTT" and repeat["classification_label"] == "Pathogenic expansion"
    assert repeat["reported"] is True and repeat["reviewed_at"] == "2026-10-28T10:00:00+00:00"
    assert repeat["drift"]["status"] == "changed"
    [paraphase] = record["paraphase_reviews"]
    assert paraphase["drift"] == {"status": "no_snapshot", "changes": []}
    drift = record["finding_review_drift"]
    assert drift["checked"] == 2 and drift["drifted_count"] == 2
    assert [(item["kind"], item["key"], item["status"]) for item in drift["drifted"]] == [
        ("paraphase", "smn1", "no_snapshot"),
        ("repeat", "HTT", "changed"),
    ]
    # Nothing of when the review was saved is in what the gate lists.
    assert all("reviewed_at" not in item for item in drift["drifted"])
    # Both findings have a review: none is listed as without one.
    assert record["unreviewed_findings"] == []


def test_the_gate_lists_each_abnormal_call_without_a_review(monkeypatch) -> None:
    _rows(monkeypatch, {"HTT": _row("HTT", tags=["excluded"])}, {})
    sections = {
        "repeat_expansions": _repeat_section(
            # Reviewed (excluded): not listed.
            _locus("HTT", "HTT", "pathogenic", [_call("PROBAND", "pathogenic", [18, 42])]),
            # A grey zone alone is not abnormal (B12)...
            _locus("ATXN2", "ATXN2", "intermediate", [_call("PROBAND", "intermediate", [22, 30])]),
            # ...but a count near a boundary at a locus not validated is, whatever its status.
            _locus("ATXN1", "ATXN1", "normal", [_call("MOTHER", "normal", [30, 34], warning=_WARNING)]),
            _locus("FXS_FMR1", "FMR1", "premutation", [_call("MOTHER", "premutation", [30, 60]), _call("PROBAND", "normal", [29, 30])]),
        ),
        "paraphase": _paraphase_section(
            _region("smn1", {"PROBAND": "carrier", "FATHER": "normal"}),
            _region("gba", {"PROBAND": "normal"}),
        ),
    }
    record = asyncio.run(frp.finding_reviews_for_record(None, _context(), sections))  # type: ignore[arg-type]
    assert record["unreviewed_findings"] == [
        {"kind": "paraphase", "key": "smn1", "gene": "SMN1", "samples": [{"sample": "PROBAND", "status": "carrier"}]},
        {
            "kind": "repeat",
            "key": "ATXN1",
            "gene": "ATXN1",
            "status": "normal",
            "samples": [{"sample": "MOTHER", "status": "normal", "near_boundary": True}],
        },
        {
            "kind": "repeat",
            "key": "FXS_FMR1",
            "gene": "FMR1",
            "status": "premutation",
            "samples": [{"sample": "MOTHER", "status": "premutation", "near_boundary": False}],
        },
    ]
    assert frp.unreviewed_finding_names(record["unreviewed_findings"]) == [
        "SMN1 (Paraphase region): PROBAND carrier",
        "ATXN1 (repeat locus): MOTHER normal (near a boundary)",
        "FMR1 (repeat locus): MOTHER premutation",
    ]


def test_calls_that_could_not_be_read_are_listed_as_such(monkeypatch) -> None:
    frozen = _locus("HTT", "HTT", "normal", [_call("PROBAND", "normal", [18, 20])])
    _rows(
        monkeypatch,
        {"HTT": _row("HTT", classification="normal", evidence_snapshot={"captured_at": "x", "evidence": frozen})},
        {},
    )
    sections = {
        "repeat_expansions": {"unavailable": "The repeat-expansion calls could not be read."},
        "paraphase": _paraphase_section(),
    }
    record = asyncio.run(frp.finding_reviews_for_record(None, _context(), sections))  # type: ignore[arg-type]
    assert record["repeat_expansion_reviews"][0]["drift"]["status"] == "unverifiable"
    assert record["unreviewed_findings"] == [{"kind": "repeat", "key": None, "status": "unreadable", "samples": []}]
    assert frp.unreviewed_finding_names(record["unreviewed_findings"]) == ["the repeat locus calls could not be read"]


def test_a_family_without_an_assembly_has_no_reviews_but_its_abnormal_calls_are_listed(monkeypatch) -> None:
    _rows(monkeypatch, {"HTT": _row("HTT", classification="normal")}, {})
    sections = {
        "repeat_expansions": _repeat_section(_locus("HTT", "HTT", "pathogenic", [_call("PROBAND", "pathogenic", [18, 42])])),
        "paraphase": _paraphase_section(),
    }
    record = asyncio.run(frp.finding_reviews_for_record(None, _context(assembly_id=None), sections))  # type: ignore[arg-type]
    assert record["repeat_expansion_reviews"] == [] and record["finding_review_drift"]["checked"] == 0
    assert [finding["key"] for finding in record["unreviewed_findings"]] == ["HTT"]


def test_identical_content_gives_an_identical_record(monkeypatch) -> None:
    _rows(monkeypatch, {}, {})
    loci = [
        _locus("ATXN1", "ATXN1", "pathogenic", [_call("PROBAND", "pathogenic", [30, 50])]),
        _locus("HTT", "HTT", "pathogenic", [_call("PROBAND", "pathogenic", [18, 42])]),
    ]
    one = asyncio.run(
        frp.finding_reviews_for_record(None, _context(), {"repeat_expansions": _repeat_section(*loci), "paraphase": _paraphase_section()})  # type: ignore[arg-type]
    )
    two = asyncio.run(
        frp.finding_reviews_for_record(None, _context(), {"repeat_expansions": _repeat_section(*reversed(loci)), "paraphase": _paraphase_section()})  # type: ignore[arg-type]
    )
    assert one == two


# --- The audit events ----------------------------------------------------------------------


def test_a_save_audits_the_classification_with_its_label_and_the_tags_and_note() -> None:
    events = audit.diff_finding_review_changes(
        {"classification": "intermediate", "tags": ["review"], "note": None},
        {"classification": "reduced_penetrance", "tags": ["report", "review"], "note": "Confirmed by PCR"},
        labels=frp.REPEAT_CLASSIFICATION_LABELS,
    )
    assert [event["action"] for event in events] == ["classification", "tags", "note"]
    assert events[0]["summary"] == "Classification Intermediate (grey zone) → Reduced penetrance"
    assert events[0]["before"] == {"classification": "intermediate"}
    assert events[0]["after"] == {"classification": "reduced_penetrance"}
    assert events[1]["summary"] == "Tags added report"
    assert events[2]["summary"] == "Note added"
    # A deleted review.
    deleted = audit.diff_finding_review_changes(
        {"classification": "carrier", "tags": [], "note": None}, None, labels=frp.PARAPHASE_CLASSIFICATION_LABELS
    )
    assert deleted[0]["summary"] == "Classification Carrier → unclassified"


def test_the_events_name_the_finding_its_modality_assembly_and_evidence(monkeypatch) -> None:
    recorded: list[dict[str, Any]] = []

    async def _record(session, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(audit, "record_clinical_event", _record)
    asyncio.run(
        audit.record_finding_review_changes(
            None,  # type: ignore[arg-type]
            family_uuid="f1",
            family_identifier="FAM1",
            modality="repeat",
            key="FXS_FMR1",
            assembly_id="a1",
            evidence_digest="abc",
            labels=frp.REPEAT_CLASSIFICATION_LABELS,
            user=SimpleNamespace(id="u1", username="analyst"),  # type: ignore[arg-type]
            existing=None,
            new_state={"classification": "premutation", "tags": [], "note": None},
        )
    )
    [event] = recorded
    assert event["variant_id"] == "repeat:FXS_FMR1" and event["action"] == "classification"
    assert event["metadata"] == {"modality": "repeat", "assembly_id": "a1", "evidence_digest": "abc"}
    assert event["actor"] == "analyst" and event["family_identifier"] == "FAM1"


# --- A save (upsert_finding_review) ----------------------------------------------------------
#
# The family's calls, the review row, the lock, the tag catalogue and the audit event are
# faked at the module; the session records each statement it is given. A review that bears a
# judgement freezes the calls it rests on when it first does so, when its classification
# changes or when it gains the Report tag, and keeps them otherwise, so a later change of the
# calls reads as drift (B5, B13). Real Postgres is in e2e/test_e2e_repeat_paraphase_reviews.py.

_CALLS = {"locus_id": "FXS_FMR1", "calls": [{"sample": "MOTHER", "repeat_counts": [30, 60]}]}
def test_a_save_must_say_which_version_of_the_review_it_was_made_against() -> None:
    # REQ-TRACE-010: left out, the save is refused (422 at the API) instead of overwriting
    # unchecked; null says the client loaded no review.
    for model in (RepeatExpansionReviewUpdate, ParaphaseReviewUpdate):
        with pytest.raises(ValidationError) as refused:
            model(note="Seen")
        assert [error["loc"] for error in refused.value.errors()] == [("expected_updated_at",)]
        assert model(note="Seen", expected_updated_at=None).expected_updated_at is None


_CALLS_SINCE = {"locus_id": "FXS_FMR1", "calls": [{"sample": "MOTHER", "repeat_counts": [30, 62]}]}
_FROZEN = {"captured_at": "2026-10-28T10:00:00+00:00", "evidence": _CALLS}


# The stored review's version, as the client loaded it.
_LOADED = datetime(2026, 10, 28, 10, tzinfo=timezone.utc)


def _stored(**fields: Any) -> dict[str, Any]:
    return {
        **_row("FXS_FMR1", classification="premutation", evidence_snapshot=_FROZEN),
        "id": "r1",
        "updated_at": _LOADED,
        **fields,
    }


class _SaveSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append((" ".join(str(statement).split()), dict(params or {})))
        return None

    async def commit(self) -> None:
        self.commits += 1

    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        return [(sql.split()[0], params) for sql, params in self.statements if sql.split()[0] in ("INSERT", "UPDATE", "DELETE")]


def _save(
    monkeypatch,
    payload: RepeatExpansionReviewUpdate,
    *,
    existing: dict[str, Any] | None,
    current: dict[str, Any] | None = _CALLS,
    key: str = "FXS_FMR1",
    assembly_id: str | None = "a1",
    session: _SaveSession | None = None,
) -> tuple[Any, _SaveSession, list[dict[str, Any]]]:
    session = session or _SaveSession()
    audited: list[dict[str, Any]] = []

    async def _evidence(session_, kind, context, key_):
        assert kind is frp.REPEAT and key_ == key.strip()
        return current

    async def _review_row(session_, kind, *, family_uuid, assembly_id, key):
        return dict(existing) if existing is not None else None

    async def _lock(session_, **kwargs):
        session.statements.append((f"lock {kwargs['scope']} {kwargs['variant_id']}", {}))

    async def _catalogue(session_, *, family_uuid, project_ids):
        return [SimpleNamespace(key=tag) for tag in ("report", "review")]

    async def _audit(session_, **kwargs):
        audited.append({**kwargs, "commits_before": session.commits})

    monkeypatch.setattr(frp, "_current_evidence", _evidence)
    monkeypatch.setattr(frp, "_review_row", _review_row)
    monkeypatch.setattr(frp, "_lock_review", _lock)
    monkeypatch.setattr(frp, "list_small_variant_tag_definitions", _catalogue)
    monkeypatch.setattr(frp, "record_finding_review_changes", _audit)
    context = SimpleNamespace(family_uuid="f1", family_id="FAM1", assembly_id=assembly_id, project_ids=[])
    saved = asyncio.run(
        frp.upsert_finding_review(
            session,  # type: ignore[arg-type]
            kind=frp.REPEAT,
            context=context,  # type: ignore[arg-type]
            key=key,
            payload=payload,
            user=SimpleNamespace(id="u1", username="analyst"),  # type: ignore[arg-type]
        )
    )
    return saved, session, audited


def _snapshot_written(session: _SaveSession) -> Any:
    [(_verb, params)] = session.writes()
    return json.loads(params["snapshot_json"]) if params["snapshot_json"] else None


def test_a_re_save_that_keeps_the_classification_updates_the_review_and_keeps_its_frozen_calls(monkeypatch) -> None:
    # The calls changed since the review was saved: a note added keeps what it rests on, so
    # the review now reads as drifted.
    saved, session, audited = _save(
        monkeypatch,
        RepeatExpansionReviewUpdate(classification="premutation", note="Confirmed by PCR", expected_updated_at=_LOADED),
        existing=_stored(),
        current=_CALLS_SINCE,
    )

    [(verb, params)] = session.writes()
    assert (verb, params["review_id"], params["classification"], params["note"]) == (
        "UPDATE",
        "r1",
        "premutation",
        "Confirmed by PCR",
    )
    assert _snapshot_written(session) == _FROZEN
    # Under the review's lock, before anything is written; audited before the one commit.
    assert session.statements[0][0] == "lock rptr a1:FXS_FMR1"
    [event] = audited
    assert (event["existing"]["note"], event["new_state"]["note"]) == (None, "Confirmed by PCR")
    assert event["evidence_digest"] == frp.evidence_digest(_FROZEN)
    assert (event["commits_before"], session.commits) == (0, 1)
    assert saved.drift is not None and saved.drift.status == "changed"


@pytest.mark.parametrize(
    "payload",
    [
        RepeatExpansionReviewUpdate(classification="pathogenic_expansion", expected_updated_at=_LOADED),
        RepeatExpansionReviewUpdate(classification="premutation", tags=["report"], expected_updated_at=_LOADED),
    ],
    ids=["classification-changed", "report-tag-added"],
)
def test_a_changed_classification_or_a_report_tag_added_freezes_the_calls_again(monkeypatch, payload) -> None:
    saved, session, audited = _save(monkeypatch, payload, existing=_stored(), current=_CALLS_SINCE)

    frozen = _snapshot_written(session)
    assert frozen["evidence"] == _CALLS_SINCE and frozen != _FROZEN
    assert audited[0]["evidence_digest"] == frp.evidence_digest(frozen)
    assert saved.drift is not None and saved.drift.status == "unchanged"


def test_a_first_review_that_bears_a_judgement_is_inserted_with_the_calls_it_rests_on(monkeypatch) -> None:
    saved, session, audited = _save(
        monkeypatch, RepeatExpansionReviewUpdate(classification="premutation", expected_updated_at=None), existing=None
    )

    [(verb, params)] = session.writes()
    assert (verb, params["key"], params["assembly_id"]) == ("INSERT", "FXS_FMR1", "a1")
    assert json.loads(params["snapshot_json"])["evidence"] == _CALLS
    assert audited[0]["existing"] is None and session.commits == 1
    # A review of a note alone freezes nothing: it bears no judgement.
    _saved, session, _audited = _save(monkeypatch, RepeatExpansionReviewUpdate(note="Seen", expected_updated_at=None), existing=None)
    assert _snapshot_written(session) is None


def test_a_save_that_says_nothing_deletes_the_review_with_its_audit_event(monkeypatch) -> None:
    saved, session, audited = _save(monkeypatch, RepeatExpansionReviewUpdate(expected_updated_at=_LOADED), existing=_stored())

    assert session.writes() == [("DELETE", {"review_id": "r1"})]
    [event] = audited
    assert (event["new_state"], event["evidence_digest"], event["commits_before"]) == (None, None, 0)
    assert (saved.key, saved.classification, session.commits) == ("FXS_FMR1", None, 1)
    # Nothing to delete: nothing written, audited or committed.
    _saved, session, audited = _save(monkeypatch, RepeatExpansionReviewUpdate(expected_updated_at=None), existing=None)
    assert (session.writes(), audited, session.commits) == ([], [], 0)


@pytest.mark.parametrize(
    ("save", "status", "detail"),
    [
        ({"key": "  "}, 400, "A repeat locus is required"),
        (
            {"assembly_id": None},
            422,
            "This family has no reference assembly, so a review of a repeat locus cannot say which calls it is "
            "of. Link the family to a project with an assembly.",
        ),
        ({"current": None}, 404, "This family holds no call at that repeat locus"),
        ({"payload": RepeatExpansionReviewUpdate(tags=["report", "probe_x"], expected_updated_at=None)}, 400, "Unknown review tag(s): probe_x"),
    ],
    ids=["no-locus", "no-assembly", "no-call-at-the-locus", "unknown-tag-added"],
)
def test_a_save_that_cannot_say_what_it_is_of_is_refused_and_writes_nothing(monkeypatch, save, status, detail) -> None:
    payload = save.pop("payload", RepeatExpansionReviewUpdate(classification="premutation", expected_updated_at=None))
    session = _SaveSession()
    with pytest.raises(HTTPException) as refused:
        _save(monkeypatch, payload, existing=None, session=session, **save)

    assert (refused.value.status_code, refused.value.detail) == (status, detail)
    assert (session.writes(), session.commits) == ([], 0)


@pytest.mark.parametrize(
    "loaded",
    [None, datetime(2026, 10, 27, 9, tzinfo=timezone.utc)],
    ids=["loaded-no-review", "loaded-an-older-version"],
)
def test_a_save_made_against_another_version_is_refused_and_writes_nothing(monkeypatch, loaded) -> None:
    # REQ-TRACE-010: another reviewer saved in between; the 409 carries their review.
    session = _SaveSession()
    with pytest.raises(HTTPException) as refused:
        _save(
            monkeypatch,
            RepeatExpansionReviewUpdate(classification="pathogenic_expansion", expected_updated_at=loaded),
            existing=_stored(),
            session=session,
        )

    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "review_conflict"
    assert refused.value.detail["current"]["classification"] == "premutation"
    assert (session.writes(), session.commits) == ([], 0)
