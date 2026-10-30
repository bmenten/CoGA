"""Concurrent review saves no longer overwrite each other silently (#513).

The review PUT was an unconditional upsert: when two reviewers saved the same variant, the
last write won and the first reviewer was never told. The client now sends the review's
``updated_at`` as it loaded it; a save against a review that has changed since is refused
with 409 and the current review, and saves of one variant are serialized by a
transaction-scoped advisory lock so the check sees every earlier save.
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend.app.schemas import SmallVariantReviewUpdate
from backend.app.services import small_variant_review_pg as svr
from backend.app.services import structural_variant_review_pg as strv
from backend.app.services.review_pg_utils import _raise_on_stale_review

T1 = datetime(2026, 9, 28, 10, 0, 0, 123456, tzinfo=timezone.utc)
T2 = T1 + timedelta(minutes=3)


def _existing(updated_at=T1, updated_by="alice") -> dict:
    return {
        "id": "review-1",
        "variant_id": "1-100-A-G",
        "classification": "acmg_class_4",
        "tags": [],
        "tag_metadata": {},
        "note": "first reviewer's note",
        "updated_by": updated_by,
        "updated_at": updated_at,
    }


def _serialize(document: dict) -> dict:
    return {"variant_id": document["variant_id"], "updated_at": document["updated_at"]}


def _conflict(payload, existing):
    with pytest.raises(HTTPException) as excinfo:
        _raise_on_stale_review(payload, existing, _serialize)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "review_conflict"
    return excinfo.value.detail


def test_a_save_against_the_loaded_version_goes_ahead() -> None:
    _raise_on_stale_review(SmallVariantReviewUpdate(expected_updated_at=T1), _existing(), _serialize)
    # Naive and aware timestamps for the same instant are the same version.
    naive = SmallVariantReviewUpdate(expected_updated_at=T1.replace(tzinfo=None))
    _raise_on_stale_review(naive, _existing(), _serialize)


def test_a_first_write_needs_no_review_to_exist() -> None:
    _raise_on_stale_review(SmallVariantReviewUpdate(expected_updated_at=None), None, _serialize)


def test_a_save_against_an_older_version_is_refused_with_the_current_review() -> None:
    detail = _conflict(SmallVariantReviewUpdate(expected_updated_at=T1), _existing(updated_at=T2, updated_by="bob"))
    assert "changed by bob" in detail["message"]
    assert detail["current"]["updated_at"] == T2.isoformat()


def test_a_first_write_racing_another_first_write_is_refused() -> None:
    # The client loaded no review, but one has been saved since.
    detail = _conflict(SmallVariantReviewUpdate(expected_updated_at=None), _existing())
    assert detail["current"]["variant_id"] == "1-100-A-G"


def test_a_save_against_a_review_removed_since_is_refused() -> None:
    detail = _conflict(SmallVariantReviewUpdate(expected_updated_at=T1), None)
    assert detail["current"] is None
    assert "removed" in detail["message"]


def test_a_client_that_does_not_send_a_version_keeps_the_unconditional_write() -> None:
    _raise_on_stale_review(SmallVariantReviewUpdate(note="x"), _existing(updated_at=T2), _serialize)


class _Session:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return types.SimpleNamespace(
            mappings=lambda: types.SimpleNamespace(first=lambda: None, all=lambda: []),
            scalar_one_or_none=lambda: None,
        )

    async def commit(self) -> None:
        self.commits += 1


def _context():
    return types.SimpleNamespace(
        family_uuid="family-1",
        family_id="FAM1",
        assembly_name="GRCh38",
        project_ids=["p1"],
        affected_sample_names=[],
    )


def _user():
    return types.SimpleNamespace(id=None, username="carol", email="carol@x.org", role="admin")


def _patch_small_variant_review(monkeypatch, existing):
    writes: list[str] = []
    order: list[str] = []

    async def _variant(**kwargs):
        return types.SimpleNamespace(variant_key=1, gene_symbols=[])

    async def _fetch(session, *, family_uuid, variant_id):
        order.append("fetch")
        return existing

    async def _write(session, **kwargs):
        writes.append("write")

    async def _audit(*args, **kwargs):
        return None

    monkeypatch.setattr(svr, "get_small_variant_family_record", _variant)
    monkeypatch.setattr(svr, "_fetch_review_row", _fetch)
    monkeypatch.setattr(svr, "_update_review_row", _write)
    monkeypatch.setattr(svr, "_insert_review_row", _write)
    monkeypatch.setattr(svr, "record_review_changes", _audit)
    return writes, order


def test_a_stale_small_variant_save_writes_nothing(monkeypatch) -> None:
    writes, _ = _patch_small_variant_review(monkeypatch, _existing(updated_at=T2, updated_by="bob"))
    session = _Session()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            svr.upsert_small_variant_review(
                session,
                context=_context(),
                variant_id="1-100-A-G",
                payload=SmallVariantReviewUpdate(note="second reviewer", expected_updated_at=T1),
                user=_user(),
            )
        )
    assert excinfo.value.status_code == 409
    assert writes == [] and session.commits == 0
    # The per-variant lock is taken before the stored version is read.
    assert "pg_advisory_xact_lock" in session.statements[0]


def test_a_current_small_variant_save_is_written(monkeypatch) -> None:
    writes, _ = _patch_small_variant_review(monkeypatch, _existing())
    session = _Session()
    asyncio.run(
        svr.upsert_small_variant_review(
            session,
            context=_context(),
            variant_id="1-100-A-G",
            payload=SmallVariantReviewUpdate(note="an update", expected_updated_at=T1),
            user=_user(),
        )
    )
    assert writes == ["write"] and session.commits == 1


def test_a_stale_structural_variant_save_writes_nothing(monkeypatch) -> None:
    async def _fetch(session, *, family_uuid, variant_id):
        return _existing(updated_at=T2, updated_by="bob")

    monkeypatch.setattr(strv, "_fetch_review_row", _fetch)
    session = _Session()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            strv.upsert_structural_variant_review(
                session,
                context=_context(),
                variant_id="DEL-1-1000-2000",
                payload=SmallVariantReviewUpdate(note="second reviewer", expected_updated_at=T1),
                user=_user(),
            )
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "review_conflict"
    assert session.commits == 0
    assert "pg_advisory_xact_lock" in session.statements[0]
    assert not any("UPDATE structural_variant_reviews" in s or "INSERT INTO structural_variant_reviews" in s for s in session.statements)


# ------------------------------------------------- the compound-het pair save and its partner
#
# A pair save (``compound_het`` with a partner) writes the partner's review as well as the
# variant's. It read the partner's review without its lock and wrote it back whole, stamped
# with a time taken before the save began: a partner review saved in between (class 5 with a
# new note) went back to the copy read earlier (class 2, no note), and its ``updated_at``
# went backwards. The partner's review is now locked like the variant's, read under the
# lock, and checked against ``compound_het.partner_expected_updated_at``.

PAIR_A, PAIR_B, PAIR_C, PAIR_D, PAIR_E = "1-100-A-G", "1-200-C-T", "1-300-G-A", "1-400-T-C", "1-500-C-G"


def _review_row(variant_id: str, **fields) -> dict:
    row = {
        "id": f"review-{variant_id}",
        "variant_id": variant_id,
        "variant_key": int(variant_id.split("-")[1]),
        "classification": None,
        "tags": [],
        "tag_metadata": {},
        "note": None,
        "acmg": None,
        "acmg_point_total": None,
        "acmg_class": None,
        "acmg_evidence_snapshot": None,
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
        "updated_by": "analyst-a",
        "updated_at": T1,
    }
    row.update(fields)
    return row


def _paired(variant_id: str, partner: str, group: str, **fields) -> dict:
    return _review_row(
        variant_id,
        compound_het_group_id=group,
        compound_het_partner_variant_ids=[partner],
        compound_het_gene="GENE1",
        compound_het_classification="Likely pathogenic",
        compound_het_phase_status="unknown",
        **fields,
    )


class _PairStore:
    """The family's review rows, behind a session whose advisory locks are recorded.

    ``on_lock`` maps a variant to a change of the rows made when its lock is taken: another
    save of that review, committed while this one waited for the lock.
    """

    def __init__(self, rows: dict, on_lock: dict | None = None) -> None:
        self.rows = {variant_id: dict(row) for variant_id, row in rows.items()}
        self.on_lock = dict(on_lock or {})
        self.locks: list[str] = []
        self.writes: list[str] = []
        self.audited: list[str] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        if "pg_advisory_xact_lock" in str(statement):
            variant_id = params["k"].rsplit(":", 1)[1]
            self.locks.append(variant_id)
            change = self.on_lock.pop(variant_id, None)
            if change is not None:
                change(self.rows)
        return types.SimpleNamespace(
            mappings=lambda: types.SimpleNamespace(first=lambda: None, all=lambda: []),
            scalar_one_or_none=lambda: None,
        )

    async def commit(self) -> None:
        self.commits += 1


def _patch_pair_store(monkeypatch, store: _PairStore) -> None:
    async def _variant(*, variant_id, **kwargs):
        return types.SimpleNamespace(variant_key=int(variant_id.split("-")[1]), gene_symbols=["GENE1"])

    async def _fetch(session, *, family_uuid, variant_id):
        row = store.rows.get(variant_id)
        return dict(row) if row is not None else None

    async def _group(session, *, family_uuid, group_id):
        return [dict(row) for row in store.rows.values() if row.get("compound_het_group_id") == group_id]

    async def _clear(session, *, family_uuid, group_id):
        for row in await _group(session, family_uuid=family_uuid, group_id=group_id):
            store.writes.append(row["variant_id"])
            store.rows[row["variant_id"]] = {**row, "compound_het_group_id": None, "compound_het_partner_variant_ids": []}

    async def _update(session, *, review_id, fields):
        store.writes.append(fields["variant_id"])
        store.rows[fields["variant_id"]] = {**fields, "id": review_id}

    async def _insert(session, *, family_uuid, fields, created_at):
        store.writes.append(fields["variant_id"])
        store.rows[fields["variant_id"]] = {"id": f"review-{fields['variant_id']}", **fields}

    async def _delete(session, review_id):
        store.writes.append(review_id)

    async def _audit(session, **kwargs):
        store.audited.append(kwargs.get("variant_id") or "pair")

    monkeypatch.setattr(svr, "get_small_variant_family_record", _variant)
    monkeypatch.setattr(svr, "variants_share_gene", lambda *args: True)
    monkeypatch.setattr(svr, "has_affected_het_call", lambda *args: True)
    monkeypatch.setattr(svr, "_fetch_review_row", _fetch)
    monkeypatch.setattr(svr, "_fetch_compound_het_group_rows", _group)
    monkeypatch.setattr(svr, "_clear_compound_het_group", _clear)
    monkeypatch.setattr(svr, "_update_review_row", _update)
    monkeypatch.setattr(svr, "_insert_review_row", _insert)
    monkeypatch.setattr(svr, "_delete_review_row", _delete)
    monkeypatch.setattr(svr, "record_review_changes", _audit)
    monkeypatch.setattr(svr, "record_compound_het_pair_changes", _audit)


def _pair_save(monkeypatch, store: _PairStore, *, variant_id: str = PAIR_A, **compound_het):
    _patch_pair_store(monkeypatch, store)
    payload = SmallVariantReviewUpdate(
        expected_updated_at=None if store.rows.get(variant_id) is None else store.rows[variant_id]["updated_at"],
        compound_het=compound_het,
    )
    return asyncio.run(
        svr.upsert_small_variant_review(
            store, context=_context(), variant_id=variant_id, payload=payload, user=_user()
        )
    )


def _partner_saved_as_class_5(rows: dict) -> None:
    rows[PAIR_B] = _review_row(
        PAIR_B,
        classification="Pathogenic - class 5",
        note="Segregates with the phenotype in the family",
        updated_by="bob",
        updated_at=T2,
    )


def test_a_pair_save_against_a_partner_review_saved_since_is_refused(monkeypatch) -> None:
    # The pair was opened when the partner was class 2; the partner was saved as class 5
    # with a note since. The pair save names the version it loaded, and is refused.
    store = _PairStore({PAIR_B: _review_row(PAIR_B, classification="Likely benign - class 2")})
    _partner_saved_as_class_5(store.rows)
    partner_before = dict(store.rows[PAIR_B])
    with pytest.raises(HTTPException) as excinfo:
        _pair_save(monkeypatch, store, partner_variant_id=PAIR_B, partner_expected_updated_at=T1, tags=[])
    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["code"] == "review_conflict"
    assert detail["variant_id"] == PAIR_B
    assert f"compound-het partner {PAIR_B} was changed by bob" in detail["message"]
    assert detail["current"]["classification"] == "Pathogenic - class 5"
    # Nothing written, nothing audited, nothing committed; the partner keeps its review.
    assert store.writes == [] and store.audited == [] and store.commits == 0
    assert store.rows[PAIR_B] == partner_before
    assert PAIR_A not in store.rows


def test_a_pair_save_against_a_partner_review_removed_since_is_refused(monkeypatch) -> None:
    store = _PairStore({})
    with pytest.raises(HTTPException) as excinfo:
        _pair_save(monkeypatch, store, partner_variant_id=PAIR_B, partner_expected_updated_at=T1, tags=[])
    assert excinfo.value.status_code == 409
    assert "was removed" in excinfo.value.detail["message"]
    assert store.writes == [] and store.commits == 0


def test_a_pair_save_against_the_partner_review_it_loaded_goes_ahead(monkeypatch) -> None:
    store = _PairStore({PAIR_B: _review_row(PAIR_B, classification="Pathogenic - class 5", updated_at=T2)})
    saved = _pair_save(
        monkeypatch, store, partner_variant_id=PAIR_B, partner_expected_updated_at=T2, tags=[], note="in trans"
    )
    assert saved.compound_het is not None and saved.compound_het.partner_variant_ids == [PAIR_B]
    assert store.rows[PAIR_B]["classification"] == "Pathogenic - class 5"
    assert store.rows[PAIR_B]["compound_het_partner_variant_ids"] == [PAIR_A]
    assert store.commits == 1


def test_a_partner_review_saved_while_the_pair_save_waits_for_its_lock_is_kept(monkeypatch) -> None:
    # The owner's experiment: the partner (class 2) is saved as class 5 with a new note while
    # the pair save is under way. The pair save waits for the partner's lock and reads the
    # partner's review after it: class 5 and the note stay, and its time moves forward.
    store = _PairStore(
        {PAIR_B: _review_row(PAIR_B, classification="Likely benign - class 2")},
        on_lock={PAIR_B: _partner_saved_as_class_5},
    )
    _pair_save(monkeypatch, store, partner_variant_id=PAIR_B, tags=[], classification="Pathogenic")
    partner = store.rows[PAIR_B]
    assert partner["classification"] == "Pathogenic - class 5"
    assert partner["note"] == "Segregates with the phenotype in the family"
    assert partner["compound_het_partner_variant_ids"] == [PAIR_A]
    assert partner["compound_het_classification"] == "Pathogenic"
    assert partner["updated_at"] > T2
    # The partner's review is read only after its lock is taken.
    assert store.locks == [PAIR_A, PAIR_B]


def test_the_partner_version_is_checked_against_the_review_read_under_its_lock(monkeypatch) -> None:
    # The same race, with the version the pair was opened against: refused, not written.
    store = _PairStore(
        {PAIR_B: _review_row(PAIR_B, classification="Likely benign - class 2")},
        on_lock={PAIR_B: _partner_saved_as_class_5},
    )
    with pytest.raises(HTTPException) as excinfo:
        _pair_save(monkeypatch, store, partner_variant_id=PAIR_B, partner_expected_updated_at=T1, tags=[])
    assert excinfo.value.status_code == 409
    assert store.writes == [] and store.commits == 0
    assert store.rows[PAIR_B]["classification"] == "Pathogenic - class 5"


def test_a_pair_save_locks_every_review_it_writes_in_one_order(monkeypatch) -> None:
    # A is paired with C and B with D: pairing A with B breaks both pairs up, so the save
    # writes all four reviews. Each is locked, sorted, before any is read for the write, so
    # two pair saves cannot each hold a lock the other waits for.
    store = _PairStore(
        {
            PAIR_D: _paired(PAIR_D, PAIR_B, "g2"),
            PAIR_C: _paired(PAIR_C, PAIR_A, "g1"),
            PAIR_B: _paired(PAIR_B, PAIR_D, "g2"),
            PAIR_A: _paired(PAIR_A, PAIR_C, "g1"),
        }
    )
    _pair_save(monkeypatch, store, partner_variant_id=PAIR_B, tags=[])
    assert store.locks == sorted([PAIR_A, PAIR_B, PAIR_C, PAIR_D])
    assert set(store.writes) == {PAIR_A, PAIR_B, PAIR_C, PAIR_D}


def test_breaking_up_a_pair_locks_the_partner_review_it_rewrites(monkeypatch) -> None:
    store = _PairStore({PAIR_A: _paired(PAIR_A, PAIR_C, "g1"), PAIR_C: _paired(PAIR_C, PAIR_A, "g1")})
    _patch_pair_store(monkeypatch, store)
    asyncio.run(
        svr.upsert_small_variant_review(
            store,
            context=_context(),
            variant_id=PAIR_A,
            payload=SmallVariantReviewUpdate(compound_het=None, note="kept"),
            user=_user(),
        )
    )
    assert store.locks == [PAIR_A, PAIR_C]
    assert store.rows[PAIR_C]["compound_het_group_id"] is None


def test_a_pair_changed_between_the_lookup_and_the_locks_is_refused(monkeypatch) -> None:
    # While the save waited for A's lock, another pair save paired A with E: this save would
    # now break up a pair whose review it did not lock, so it is refused and writes nothing.
    def _repaired(rows: dict) -> None:
        rows[PAIR_A] = _paired(PAIR_A, PAIR_E, "g3", updated_at=T2)
        rows[PAIR_E] = _paired(PAIR_E, PAIR_A, "g3", updated_at=T2)

    store = _PairStore({}, on_lock={PAIR_A: _repaired})
    _patch_pair_store(monkeypatch, store)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            svr.upsert_small_variant_review(
                store,
                context=_context(),
                variant_id=PAIR_A,
                payload=SmallVariantReviewUpdate(compound_het={"partner_variant_id": PAIR_B, "tags": []}),
                user=_user(),
            )
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "review_conflict"
    assert store.writes == [] and store.commits == 0


def test_a_save_is_never_stamped_before_the_review_it_replaces() -> None:
    from backend.app.services.review_pg_utils import _save_time

    ahead = datetime.now(timezone.utc) + timedelta(minutes=5)  # a clock that stepped back
    assert _save_time({"updated_at": ahead}, None) == ahead + timedelta(microseconds=1)
    assert _save_time({"updated_at": ahead.replace(tzinfo=None)}) == ahead + timedelta(microseconds=1)
    before = datetime.now(timezone.utc)
    assert before <= _save_time({"updated_at": T1}, {"updated_at": None}) <= datetime.now(timezone.utc)
