"""A review save that breaks up a compound-het pair stores what it audits.

Breaking up a pair clears it from each review of the pair and deletes a review that held
nothing else. The save that breaks it up then writes the variant's own review (and,
re-pairing, the partner's). Written over the copy it had read before the clear, a review the
clear had just deleted would be gone: the save's update would match no row, its tags and note
would be lost, yet its clinical audit events would be committed with the deletion.

The pair clear leaves the reviews the save writes itself; an update that matches no row
raises; and the save reads its review back before it commits, so a save that did not store
it commits nothing. The store is a fake kept by the session: the real repository code (the
pair clear, the row update with its guard, insert and delete) runs over it, as does the real
clinical audit diff. The values are synthetic.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.schemas import SmallVariantReviewUpdate
from app.services import clinical_audit_service, small_variant_review_pg, small_variant_review_repository
from app.services.small_variant_review_repository import ReviewRowNotStored

A, B, X, Y = "1-4000-T-C", "1-5000-A-T", "1-6000-G-A", "1-7000-C-G"

# The columns of a review row: what the fake store keeps of an INSERT's or UPDATE's parameters.
_COLUMNS = (
    "variant_key",
    "variant_id",
    "classification",
    "tags",
    "tag_metadata",
    "note",
    *small_variant_review_repository._compound_het_field_names(),
    "acmg",
    "acmg_point_total",
    "acmg_class",
    "acmg_evidence_snapshot",
    "updated_by",
    "updated_at",
)


def _row(variant_id: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"review-{variant_id}",
        "variant_key": int(variant_id.split("-")[1]),
        "variant_id": variant_id,
        "classification": None,
        "tags": [],
        "tag_metadata": {},
        "note": None,
        **small_variant_review_repository._compound_het_clear_payload(),
        "acmg": None,
        "acmg_point_total": None,
        "acmg_class": None,
        "acmg_evidence_snapshot": None,
        "updated_by": "analyst-b",
        "updated_at": None,
    }
    row.update(fields)
    # Who added each tag, as every save records it beside the tags.
    for tags_key, metadata_key in (("tags", "tag_metadata"), ("compound_het_tags", "compound_het_tag_metadata")):
        if metadata_key not in fields:
            row[metadata_key] = {tag: {"updated_by": "analyst-b", "updated_at": None} for tag in row[tags_key]}
    return row


def _paired(variant_id: str, partner: str, group: str, **fields: Any) -> dict[str, Any]:
    return _row(
        variant_id,
        compound_het_group_id=group,
        compound_het_partner_variant_ids=[partner],
        compound_het_gene="GENE_CH",
        compound_het_phase_status="unknown",
        compound_het_updated_by="analyst-b",
        **fields,
    )


class _Store:
    """The review rows by variant, written through the statements the repository sends.

    An UPDATE or DELETE matches the row by its id, as Postgres does, and reports how many rows
    it matched. ``lose_writes`` makes every INSERT and UPDATE store nothing (an UPDATE then
    matches no row), as a write that did not reach the store.
    """

    def __init__(self, rows: dict[str, dict[str, Any]], *, lose_writes: bool = False) -> None:
        self.rows = copy.deepcopy(rows)
        self.lose_writes = lose_writes
        self.commits = 0
        self.events: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        params = params or {}
        matched = 0
        if sql.startswith("UPDATE small_variant_reviews"):
            for variant_id, row in list(self.rows.items()):
                if row["id"] == params["review_id"] and not self.lose_writes:
                    self.rows.pop(variant_id)
                    self.rows[params["variant_id"]] = {"id": row["id"], **{key: params.get(key) for key in _COLUMNS}}
                    matched = 1
        elif sql.startswith("DELETE FROM small_variant_reviews"):
            for variant_id, row in list(self.rows.items()):
                if row["id"] == params["review_id"]:
                    self.rows.pop(variant_id)
                    matched = 1
        elif sql.startswith("INSERT INTO small_variant_reviews"):
            assert params["variant_id"] not in self.rows, "a second review row for one variant"
            if not self.lose_writes:
                self.rows[params["variant_id"]] = {
                    "id": f"review-{params['variant_id']}",
                    **{key: params.get(key) for key in _COLUMNS},
                }
            matched = 1
        return SimpleNamespace(
            rowcount=matched,
            mappings=lambda: SimpleNamespace(first=lambda: None, all=lambda: []),
            scalar_one_or_none=lambda: None,
        )

    async def commit(self) -> None:
        self.commits += 1


def _save(monkeypatch, store: _Store, payload: SmallVariantReviewUpdate, variant_id: str = A):
    """Save ``payload`` on ``variant_id`` over ``store``; return the review the save returns."""

    async def variant(*, variant_id: str, **_kwargs: Any) -> Any:
        return SimpleNamespace(variant_key=int(variant_id.split("-")[1]), gene_symbols=["GENE_CH"])

    async def fetch_rows(session: _Store, *, where_clause: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "group_id" in params:
            found = [row for row in session.rows.values() if row["compound_het_group_id"] == params["group_id"]]
        else:
            found = [session.rows[params["variant_id"]]] if params["variant_id"] in session.rows else []
        return copy.deepcopy(found)

    async def tag_definitions(*_args: Any, **_kwargs: Any) -> list[Any]:
        return [SimpleNamespace(key=key) for key in ("review", "report")]

    async def record(_session: Any, **event: Any) -> None:
        store.events.append({**event, "commits_before": store.commits})

    monkeypatch.setattr(small_variant_review_pg, "get_small_variant_family_record", variant)
    monkeypatch.setattr(small_variant_review_pg, "variants_share_gene", lambda *_args: True)
    monkeypatch.setattr(small_variant_review_pg, "has_affected_het_call", lambda *_args: True)
    monkeypatch.setattr(small_variant_review_pg, "list_small_variant_tag_definitions", tag_definitions)
    monkeypatch.setattr(small_variant_review_repository, "_fetch_review_rows", fetch_rows)
    monkeypatch.setattr(clinical_audit_service, "record_clinical_event", record)
    context = SimpleNamespace(
        family_uuid="family-uuid",
        family_id="FAM-PAIR",
        assembly_name="GRCh38",
        assembly_id=None,
        project_ids=[],
        affected_sample_names=["PROBAND"],
    )
    user = SimpleNamespace(id=None, username="analyst-c", email="c@example.org")
    return asyncio.run(
        small_variant_review_pg.upsert_small_variant_review(
            store, context=context, variant_id=variant_id, payload=payload, user=user  # type: ignore[arg-type]
        )
    )


def _summaries(store: _Store) -> list[tuple[str, str]]:
    return [(event["variant_id"], event["summary"]) for event in store.events]


# ----------------------------------------------------------------- dropping the pair


@pytest.mark.parametrize(
    "compound_het",
    [None, {"partner_variant_id": ""}],
    ids=["compound_het null", "no partner"],
)
def test_a_save_that_drops_the_pair_stores_the_tags_and_note_it_audits(monkeypatch, compound_het) -> None:
    # The confirmed case: both reviews held only the pair.
    store = _Store({A: _paired(A, B, "g1"), B: _paired(B, A, "g1")})
    payload = SmallVariantReviewUpdate(tags=["review"], note="own note", compound_het=compound_het)

    saved = _save(monkeypatch, store, payload)

    assert (saved.tags, saved.note, saved.compound_het) == (["review"], "own note", None)
    assert set(store.rows) == {A}  # the partner's review held only the pair: deleted
    stored = store.rows[A]
    assert (stored["tags"], stored["note"]) == (["review"], "own note")
    assert stored["compound_het_group_id"] is None and stored["compound_het_partner_variant_ids"] == []
    # What the trail records is what is stored, in the save's one commit.
    assert sorted(_summaries(store)) == sorted(
        [(A, "Tags added review"), (A, "Note added"), (A, f"Compound-het pair with {B} removed")]
    )
    assert {event["commits_before"] for event in store.events} == {0}
    assert store.commits == 1


def test_dropping_the_pair_keeps_the_partners_own_review_without_it(monkeypatch) -> None:
    store = _Store(
        {A: _paired(A, B, "g1"), B: _paired(B, A, "g1", tags=["report"], note="partner note")}
    )

    _save(monkeypatch, store, SmallVariantReviewUpdate(tags=["review"], note="own note", compound_het=None))

    assert set(store.rows) == {A, B}
    partner = store.rows[B]
    assert (partner["tags"], partner["note"]) == (["report"], "partner note")
    assert partner["compound_het_group_id"] is None and partner["compound_het_partner_variant_ids"] == []
    assert store.rows[A]["note"] == "own note"


def test_a_save_that_drops_the_pair_and_leaves_nothing_deletes_both_reviews(monkeypatch) -> None:
    store = _Store({A: _paired(A, B, "g1"), B: _paired(B, A, "g1")})

    saved = _save(monkeypatch, store, SmallVariantReviewUpdate(compound_het=None))

    assert (saved.tags, saved.note, saved.compound_het) == ([], None, None)
    assert store.rows == {}
    assert _summaries(store) == [(A, f"Compound-het pair with {B} removed")]
    assert store.commits == 1


# ------------------------------------------------------------------------- re-pairing


def test_re_pairing_a_variant_whose_review_held_only_its_old_pair(monkeypatch) -> None:
    store = _Store({A: _paired(A, X, "g-ax"), X: _paired(X, A, "g-ax")})

    saved = _save(monkeypatch, store, SmallVariantReviewUpdate(compound_het={"partner_variant_id": B, "tags": []}))

    assert saved.compound_het is not None and saved.compound_het.partner_variant_ids == [B]
    assert set(store.rows) == {A, B}  # X's review held only the old pair
    group = store.rows[A]["compound_het_group_id"]
    assert group not in (None, "g-ax") and store.rows[B]["compound_het_group_id"] == group
    assert store.rows[A]["compound_het_partner_variant_ids"] == [B]
    assert store.rows[B]["compound_het_partner_variant_ids"] == [A]
    assert _summaries(store) == [
        (A, f"Compound-het pair with {X} removed"),
        (A, f"Compound-het pair with {B} recorded: gene GENE_CH"),
    ]
    assert store.commits == 1


def test_pairing_with_a_partner_whose_review_held_only_its_old_pair(monkeypatch) -> None:
    # The partner's review is not deleted with its old pair and then lost: the save keeps it
    # with the new pair, so both variants hold the pair they are in.
    store = _Store({B: _paired(B, Y, "g-by"), Y: _paired(Y, B, "g-by")})

    _save(monkeypatch, store, SmallVariantReviewUpdate(compound_het={"partner_variant_id": B, "tags": []}))

    assert set(store.rows) == {A, B}
    assert store.rows[A]["compound_het_partner_variant_ids"] == [B]
    assert store.rows[B]["compound_het_partner_variant_ids"] == [A]
    assert store.rows[A]["compound_het_group_id"] == store.rows[B]["compound_het_group_id"]
    assert _summaries(store) == [
        (B, f"Compound-het pair with {Y} removed"),
        (A, f"Compound-het pair with {B} recorded: gene GENE_CH"),
    ]


# --------------------------------------------------------------- a save not stored


def test_an_update_that_matches_no_row_is_refused() -> None:
    store = _Store({})
    with pytest.raises(ReviewRowNotStored):
        asyncio.run(
            small_variant_review_repository._update_review_row(store, review_id="review-gone", fields=_row(A))  # type: ignore[arg-type]
        )
    stored = _Store({A: _row(A)})
    asyncio.run(
        small_variant_review_repository._update_review_row(
            stored, review_id="review-" + A, fields=_row(A, note="kept")  # type: ignore[arg-type]
        )
    )
    assert stored.rows[A]["note"] == "kept"


def test_a_save_whose_update_stores_nothing_audits_and_commits_nothing(monkeypatch) -> None:
    store = _Store({A: _row(A, tags=["report"])}, lose_writes=True)

    with pytest.raises(ReviewRowNotStored):
        _save(monkeypatch, store, SmallVariantReviewUpdate(tags=["report"], note="own note"))

    assert (store.events, store.commits) == ([], 0)


def test_a_save_whose_review_is_not_stored_commits_nothing(monkeypatch) -> None:
    # A first review whose insert did not reach the store: the read-back, in the save's
    # transaction, finds no review, and the save is refused before it commits.
    store = _Store({}, lose_writes=True)

    with pytest.raises(HTTPException) as refused:
        _save(monkeypatch, store, SmallVariantReviewUpdate(tags=["review"], note="own note"))

    assert (refused.value.status_code, refused.value.detail) == (500, "Review update failed")
    assert store.events and store.commits == 0  # written in the transaction, never committed
