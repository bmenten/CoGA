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
