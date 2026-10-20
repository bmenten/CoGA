"""A compound-heterozygous pair review is a clinical audit event (SEC-7).

The review of a compound-het pair (its classification, tags and note, given on one variant's
review with its partner) is a clinical decision like a single variant's review. It wrote no
clinical audit event: only the HTTP audit log saw it. Each save that records a pair, changes
it or breaks it up now writes a ``compound_het_pair`` event on the family's hash-chained
trail, in the save's transaction, with the pair as it was and as it is: one event per pair
that changed, filed under the variant whose review held the pair, naming the partner. An
unchanged re-save writes none.

The review store is a fake keeping each variant's review row; the values are synthetic.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any

from app.schemas import SmallVariantReviewUpdate
from app.services import clinical_audit_service, small_variant_review_pg

A, B, X, Y = "1-100-A-G", "1-200-C-T", "1-300-G-A", "1-400-T-C"


def _pair_row(variant_id: str, partner: str, group: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"review-{variant_id}",
        "variant_id": variant_id,
        "classification": None,
        "tags": [],
        "tag_metadata": {},
        "note": None,
        "acmg": None,
        "acmg_point_total": None,
        "acmg_class": None,
        "acmg_evidence_snapshot": None,
        "compound_het_group_id": group,
        "compound_het_partner_variant_ids": [partner],
        "compound_het_gene": "GENE1",
        "compound_het_gene_id": None,
        "compound_het_classification": "Likely pathogenic",
        "compound_het_tags": [],
        "compound_het_tag_metadata": {},
        "compound_het_note": None,
        "compound_het_phase_status": "unknown",
        "compound_het_updated_by": "analyst-b",
        "compound_het_updated_at": None,
        "updated_by": "analyst-b",
        "updated_at": None,
    }
    row.update(fields)
    return row


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(first=lambda: None, all=lambda: []),
            scalar_one_or_none=lambda: None,
        )

    async def commit(self) -> None:
        self.commits += 1


def _save(monkeypatch, rows: dict[str, dict[str, Any]], payload: SmallVariantReviewUpdate, variant_id: str = A):
    """Save ``payload`` on ``variant_id`` over the stored review ``rows``; return the clinical
    audit events written (each with the commits made before it) and the groups cleared."""
    session = _Session()
    rows = copy.deepcopy(rows)
    events: list[dict[str, Any]] = []
    cleared: list[str] = []

    async def variant(*, variant_id: str, **_kwargs: Any) -> Any:
        return SimpleNamespace(variant_key=int(variant_id.split("-")[1]), gene_symbols=["GENE1"])

    async def fetch(_session: Any, *, family_uuid: str, variant_id: str) -> dict[str, Any] | None:
        row = rows.get(variant_id)
        return copy.deepcopy(row) if row is not None else None

    async def clear_group(_session: Any, *, family_uuid: str, group_id: str) -> None:
        cleared.append(group_id)

    async def insert(_session: Any, *, family_uuid: str, fields: dict[str, Any], created_at: Any) -> None:
        rows[fields["variant_id"]] = {"id": f"review-{fields['variant_id']}", **fields}

    async def update(_session: Any, *, review_id: str, fields: dict[str, Any]) -> None:
        rows[fields["variant_id"]] = {**fields, "id": review_id}

    async def remove(_session: Any, review_id: str) -> None:
        for variant_id in [key for key, row in rows.items() if row["id"] == review_id]:
            rows.pop(variant_id)

    async def record(_session: Any, **kwargs: Any) -> None:
        events.append({**kwargs, "commits_before": session.commits})

    monkeypatch.setattr(small_variant_review_pg, "get_small_variant_family_record", variant)
    monkeypatch.setattr(small_variant_review_pg, "variants_share_gene", lambda *_args: True)
    monkeypatch.setattr(small_variant_review_pg, "has_affected_het_call", lambda *_args: True)
    monkeypatch.setattr(small_variant_review_pg, "_fetch_review_row", fetch)
    monkeypatch.setattr(small_variant_review_pg, "_clear_compound_het_group", clear_group)
    monkeypatch.setattr(small_variant_review_pg, "_insert_review_row", insert)
    monkeypatch.setattr(small_variant_review_pg, "_update_review_row", update)
    monkeypatch.setattr(small_variant_review_pg, "_delete_review_row", remove)
    monkeypatch.setattr(clinical_audit_service, "record_clinical_event", record)
    context = SimpleNamespace(
        family_uuid="family-uuid",
        family_id="FAM-PAIR",
        assembly_name="GRCh38",
        project_ids=[],
        affected_sample_names=["PROBAND"],
    )
    user = SimpleNamespace(id="22222222-2222-2222-2222-222222222222", username="analyst-c", email="c@example.org")
    asyncio.run(
        small_variant_review_pg.upsert_small_variant_review(
            session, context=context, variant_id=variant_id, payload=payload, user=user  # type: ignore[arg-type]
        )
    )
    return [event for event in events if event["action"] == "compound_het_pair"], cleared


def _pair_payload(partner: str, **pair: Any) -> SmallVariantReviewUpdate:
    return SmallVariantReviewUpdate(compound_het={"partner_variant_id": partner, "tags": [], **pair})


# ------------------------------------------------------------------------------------ the diff


def test_the_diff_names_a_pair_recorded_changed_or_removed() -> None:
    before = clinical_audit_service.compound_het_pair_state(_pair_row(A, B, "g1", compound_het_note="trans"))
    assert before == {
        "partner_variant_ids": [B],
        "gene": "GENE1",
        "classification": "Likely pathogenic",
        "tags": [],
        "note": "trans",
        "phase_status": "unknown",
    }
    after = {**before, "classification": "Pathogenic", "tags": ["report"], "note": "trans, confirmed"}

    recorded = clinical_audit_service.diff_compound_het_pair(None, before)
    changed = clinical_audit_service.diff_compound_het_pair(before, after)
    removed = clinical_audit_service.diff_compound_het_pair(after, None)

    assert recorded is not None and recorded["summary"] == (
        f"Compound-het pair with {B} recorded: gene GENE1; classification Likely pathogenic; note added"
    )
    assert changed is not None and changed["summary"] == (
        f"Compound-het pair with {B}: classification Likely pathogenic → Pathogenic; tags added report; "
        "note updated"
    )
    assert (changed["action"], changed["before"], changed["after"]) == ("compound_het_pair", before, after)
    assert removed is not None and removed["summary"] == f"Compound-het pair with {B} removed"
    assert clinical_audit_service.diff_compound_het_pair(before, dict(before)) is None
    assert clinical_audit_service.compound_het_pair_state(_pair_row(A, B, None)) is None
    assert clinical_audit_service.compound_het_pair_state(None) is None


# ------------------------------------------------------------------------------- the review save


def test_recording_a_pair_is_a_clinical_audit_event_in_the_saves_transaction(monkeypatch) -> None:
    payload = _pair_payload(B, classification="Likely pathogenic", tags=["report"], note="in trans")

    events, cleared = _save(monkeypatch, {}, payload)

    [event] = events
    assert (event["variant_id"], event["family_identifier"], event["actor"]) == (A, "FAM-PAIR", "analyst-c")
    assert event["summary"] == (
        f"Compound-het pair with {B} recorded: gene GENE1; classification Likely pathogenic; tags report; "
        "note added"
    )
    assert event["before"] is None
    assert event["after"] == {
        "partner_variant_ids": [B],
        "gene": "GENE1",
        "classification": "Likely pathogenic",
        "tags": ["report"],
        "note": "in trans",
        "phase_status": "unknown",
    }
    assert set(event["metadata"]) == {"compound_het_group_id"}
    assert event["commits_before"] == 0  # written before the save commits
    assert cleared == []


def test_changing_a_pair_records_what_changed(monkeypatch) -> None:
    rows = {A: _pair_row(A, B, "g1"), B: _pair_row(B, A, "g1")}
    payload = _pair_payload(B, classification="Pathogenic", tags=["report"])

    events, cleared = _save(monkeypatch, rows, payload)

    [event] = events
    assert event["summary"] == f"Compound-het pair with {B}: classification Likely pathogenic → Pathogenic; tags added report"
    assert event["before"]["classification"] == "Likely pathogenic"
    assert event["after"]["classification"] == "Pathogenic"
    assert event["metadata"] == {"compound_het_group_id": "g1"}
    assert cleared == []


def test_an_unchanged_re_save_of_a_pair_writes_no_event(monkeypatch) -> None:
    rows = {A: _pair_row(A, B, "g1"), B: _pair_row(B, A, "g1")}

    events, cleared = _save(monkeypatch, rows, _pair_payload(B, classification="Likely pathogenic"))

    assert (events, cleared) == ([], [])


def test_pairing_with_another_variant_records_each_pair_it_breaks_up(monkeypatch) -> None:
    rows = {
        A: _pair_row(A, X, "g-ax"),
        X: _pair_row(X, A, "g-ax"),
        B: _pair_row(B, Y, "g-by", compound_het_classification="VUS"),
        Y: _pair_row(Y, B, "g-by", compound_het_classification="VUS"),
    }

    events, cleared = _save(monkeypatch, rows, _pair_payload(B, classification="Pathogenic"))

    assert cleared == ["g-ax", "g-by"]  # each group once
    assert [(event["variant_id"], event["summary"]) for event in events] == [
        (A, f"Compound-het pair with {X} removed"),
        (B, f"Compound-het pair with {Y} removed"),
        (A, f"Compound-het pair with {B} recorded: gene GENE1; classification Pathogenic"),
    ]
    assert events[1]["before"]["classification"] == "VUS" and events[1]["after"] is None
    assert [event["metadata"]["compound_het_group_id"] for event in events[:2]] == ["g-ax", "g-by"]


def test_removing_a_pair_records_its_removal(monkeypatch) -> None:
    rows = {A: _pair_row(A, B, "g1", compound_het_note="trans"), B: _pair_row(B, A, "g1")}

    for payload in (SmallVariantReviewUpdate(compound_het=None), _pair_payload("")):
        events, cleared = _save(monkeypatch, rows, payload)

        [event] = events
        assert (event["variant_id"], event["summary"]) == (A, f"Compound-het pair with {B} removed")
        assert event["before"]["note"] == "trans" and event["after"] is None
        assert cleared == ["g1"]


def test_a_save_that_leaves_the_pair_alone_records_no_pair_event(monkeypatch) -> None:
    rows = {A: _pair_row(A, B, "g1"), B: _pair_row(B, A, "g1")}

    events, cleared = _save(monkeypatch, rows, SmallVariantReviewUpdate(classification="VUS", tags=[]))

    assert (events, cleared) == ([], [])
