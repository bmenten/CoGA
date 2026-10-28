"""The reviews of repeat loci and Paraphase regions (docs/repeat-paraphase-review-design.md).

What a review rests on and what the sign-out reads of it, without a database: the two
classification scales (B4), the calls a review freezes (a repeat locus's record entry, a
Paraphase region's without its load times), whether they are still the family's (B5), which
reviews the record holds and the drift gate counts (B13), which abnormal calls the gate of
unreviewed findings lists (B8, B12), and the clinical audit events of a save.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, get_args

import pytest
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
    assert RepeatExpansionReviewUpdate(classification="reduced_penetrance").classification == "reduced_penetrance"
    assert ParaphaseReviewUpdate(classification="risk_allele").classification == "risk_allele"
    # Another type's value, an ACMG class, or a word: refused (422 at the API).
    for bad in ("carrier", "pathogenic", "acmg_class_5", "Pathogenic expansion"):
        with pytest.raises(ValidationError):
            RepeatExpansionReviewUpdate(classification=bad)
    for bad in ("premutation", "pathogenic_expansion", "likely_pathogenic"):
        with pytest.raises(ValidationError):
            ParaphaseReviewUpdate(classification=bad)
    # No classification is no judgement, not a refusal.
    assert RepeatExpansionReviewUpdate().classification is None


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
