"""A compound-het pair save keeps a partner review saved in the meantime (real Postgres).

A pair save writes the partner's review as well as the variant's. It read the partner's review
without its lock and wrote it back whole, stamped with a time taken before the save began. The
owner's experiment showed the lost update: the partner was saved as class 5 with a new note, a
concurrent pair save wrote back the partner review it had read earlier, and the partner was
class 2 again, without the note, its modification time gone backwards.

The pair save now locks the partner's review as a save of it does, reads it under that lock,
and checks it against ``compound_het.partner_expected_updated_at``. This runs the real writer
(``upsert_small_variant_review``) against real Postgres, the clinical audit included, in two
ways: the owner's three steps one after the other, with the versions the client loaded; and
the race itself, the pair save running while the partner's save holds its transaction open.
Only the variants' ClickHouse records and the pairing rules read from them are stubbed: the
reviews live in Postgres. Synthetic data only.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

pytestmark = pytest.mark.integration

_VARIANT = "2-47000100-C-T"
_PARTNER = "2-47000200-G-A"
_NOTE = "Segregates with the phenotype in the family"


def _stub_variant_records(monkeypatch, svr) -> None:
    async def _variant_record(*, assembly_name, family_guid, variant_id):
        return SimpleNamespace(
            variant_key=int(variant_id.split("-")[1]),
            gene_symbols=["GENE1"],
            annotations=[{"clinvar": "Pathogenic"}],
            annotation_version="v1",
            annotation_set_hash="set-1",
        )

    monkeypatch.setattr(svr, "get_small_variant_family_record", _variant_record)
    monkeypatch.setattr(svr, "variants_share_gene", lambda *args: True)
    monkeypatch.setattr(svr, "has_affected_het_call", lambda *args: True)


async def _new_family(sm, label: str) -> SimpleNamespace:
    async with sm() as s:
        family_uuid = (
            await s.execute(
                text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                {"f": label},
            )
        ).scalar_one()
        await s.commit()
    return SimpleNamespace(
        family_uuid=family_uuid,
        family_id=label,
        project_ids=[],
        assembly_name="GRCh38",
        assembly_id=None,
        affected_sample_names=["PROBAND"],
    )


def _class_5_payload(expected_updated_at: Any):
    from backend.app.schemas import (
        AcmgClassificationPayload,
        AcmgCriterionSelection,
        SmallVariantReviewUpdate,
    )

    return SmallVariantReviewUpdate(
        expected_updated_at=expected_updated_at,
        classification="Pathogenic - class 5",
        tags=["acmg_class_5"],
        note=_NOTE,
        acmg=AcmgClassificationPayload(
            criteria=[
                AcmgCriterionSelection(code="PVS1", strength="very_strong", accepted=True),
                AcmgCriterionSelection(code="PS3", strength="strong", accepted=True, evidence="RNA assay"),
            ]
        ),
    )


async def _stored(sm, context, variant_id: str) -> dict[str, Any] | None:
    from backend.app.services import small_variant_review_pg as svr

    async with sm() as s:
        return await svr._fetch_review_row(s, family_uuid=context.family_uuid, variant_id=variant_id)


async def _audit_events(sm, context) -> list[tuple[str | None, str]]:
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT variant_id, action FROM clinical_audit_events "
                    "WHERE family_identifier = :f ORDER BY created_at, id"
                ),
                {"f": context.family_id},
            )
        ).all()
    return [(row[0], row[1]) for row in rows]


async def _chain_verifies(sm, context) -> bool:
    from backend.app.services.clinical_audit_service import verify_clinical_audit_chain

    async with sm() as s:
        return (await verify_clinical_audit_chain(s, context.family_id)).verified


def test_a_pair_save_against_a_partner_review_saved_since_is_refused(monkeypatch) -> None:
    # The owner's three steps, one after the other, with the versions the client loaded.
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import SmallVariantReviewUpdate
    from backend.app.services import small_variant_review_pg as svr

    _stub_variant_records(monkeypatch, svr)
    analyst_a = SimpleNamespace(id=None, username="analyst-a", email="analyst-a@example.org")
    analyst_b = SimpleNamespace(id=None, username="analyst-b", email="analyst-b@example.org")

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            context = await _new_family(sm, f"pair-race-seq-{uuid4()}")
            async with sm() as s:
                class_2 = await svr.upsert_small_variant_review(
                    s,
                    context=context,
                    variant_id=_PARTNER,
                    payload=SmallVariantReviewUpdate(
                        expected_updated_at=None, classification="Likely benign - class 2", tags=["acmg_class_2"]
                    ),
                    user=analyst_a,
                )
            # Analyst B opens the pair: the partner is class 2 (this version).
            loaded = class_2.updated_at

            # 1. A new review of the partner is saved: ACMG class 5, with a note.
            async with sm() as s:
                class_5 = await svr.upsert_small_variant_review(
                    s, context=context, variant_id=_PARTNER, payload=_class_5_payload(loaded), user=analyst_a
                )
            assert class_5.acmg is not None and class_5.acmg.classification == "Pathogenic - class 5"
            events_before = await _audit_events(sm, context)

            # 2. Analyst B saves the pair, made against the class-2 partner: refused.
            async with sm() as s:
                with pytest.raises(HTTPException) as excinfo:
                    await svr.upsert_small_variant_review(
                        s,
                        context=context,
                        variant_id=_VARIANT,
                        payload=SmallVariantReviewUpdate(
                            expected_updated_at=None,
                            compound_het={
                                "partner_variant_id": _PARTNER,
                                "partner_expected_updated_at": loaded,
                                "classification": "Pathogenic",
                                "note": "In trans by parental genotypes",
                            },
                        ),
                        user=analyst_b,
                    )
                await s.rollback()
            assert excinfo.value.status_code == 409
            assert excinfo.value.detail["code"] == "review_conflict"
            assert excinfo.value.detail["variant_id"] == _PARTNER
            assert excinfo.value.detail["current"]["classification"] == "Pathogenic - class 5"

            # 3. The partner keeps class 5, its note and its time; no pair, no audit event.
            partner = await _stored(sm, context, _PARTNER)
            assert partner is not None
            assert (partner["classification"], partner["acmg_class"], partner["note"]) == (
                "Pathogenic - class 5",
                "acmg_class_5",
                _NOTE,
            )
            assert partner["updated_at"] == class_5.updated_at
            assert partner["compound_het_group_id"] is None
            assert await _stored(sm, context, _VARIANT) is None
            assert await _audit_events(sm, context) == events_before

            # Reloaded, the pair is saved against the current partner, which it keeps.
            async with sm() as s:
                await svr.upsert_small_variant_review(
                    s,
                    context=context,
                    variant_id=_VARIANT,
                    payload=SmallVariantReviewUpdate(
                        expected_updated_at=None,
                        compound_het={
                            "partner_variant_id": _PARTNER,
                            "partner_expected_updated_at": class_5.updated_at,
                            "classification": "Pathogenic",
                        },
                    ),
                    user=analyst_b,
                )
            partner = await _stored(sm, context, _PARTNER)
            assert partner is not None
            assert (partner["classification"], partner["acmg_class"], partner["note"]) == (
                "Pathogenic - class 5",
                "acmg_class_5",
                _NOTE,
            )
            assert partner["compound_het_partner_variant_ids"] == [_VARIANT]
            assert partner["updated_at"] > class_5.updated_at
            assert await _chain_verifies(sm, context)
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_a_partner_review_saved_during_a_pair_save_is_kept(monkeypatch) -> None:
    # The race itself: the partner's class-5 save has written its row and holds its
    # transaction open when the pair save (sending no partner version) runs. The pair save
    # waits for the partner's lock, then reads and keeps what that save committed.
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import SmallVariantReviewUpdate
    from backend.app.services import small_variant_review_pg as svr

    _stub_variant_records(monkeypatch, svr)
    analyst_a = SimpleNamespace(id=None, username="analyst-a", email="analyst-a@example.org")
    analyst_b = SimpleNamespace(id=None, username="analyst-b", email="analyst-b@example.org")

    record_review_changes = svr.record_review_changes
    armed = False
    partner_written = asyncio.Event()
    release = asyncio.Event()

    async def _held_open(session, **kwargs):
        # The partner's class-5 save pauses after its row is written, before its audit and
        # commit (the class-2 save before it runs through).
        if armed and kwargs.get("variant_id") == _PARTNER and not partner_written.is_set():
            partner_written.set()
            await release.wait()
        await record_review_changes(session, **kwargs)

    monkeypatch.setattr(svr, "record_review_changes", _held_open)

    async def _waiting_on_a_lock(sm, pid: int) -> bool:
        async with sm() as s:
            return bool(
                (
                    await s.execute(
                        text("SELECT count(*) FROM pg_locks WHERE pid = :pid AND NOT granted"),
                        {"pid": pid},
                    )
                ).scalar_one()
            )

    async def _run() -> None:
        nonlocal armed
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            context = await _new_family(sm, f"pair-race-live-{uuid4()}")
            async with sm() as s:
                class_2 = await svr.upsert_small_variant_review(
                    s,
                    context=context,
                    variant_id=_PARTNER,
                    payload=SmallVariantReviewUpdate(
                        expected_updated_at=None, classification="Likely benign - class 2", tags=["acmg_class_2"]
                    ),
                    user=analyst_a,
                )

            async def _partner_save():
                async with sm() as s:
                    return await svr.upsert_small_variant_review(
                        s,
                        context=context,
                        variant_id=_PARTNER,
                        payload=_class_5_payload(class_2.updated_at),
                        user=analyst_a,
                    )

            pair_session = sm()
            pair_pid = (await pair_session.execute(text("SELECT pg_backend_pid()"))).scalar_one()

            async def _pair_save():
                try:
                    return await svr.upsert_small_variant_review(
                        pair_session,
                        context=context,
                        variant_id=_VARIANT,
                        payload=SmallVariantReviewUpdate(
                            compound_het={"partner_variant_id": _PARTNER, "classification": "Pathogenic"},
                        ),
                        user=analyst_b,
                    )
                finally:
                    await pair_session.close()

            armed = True
            partner_task = asyncio.create_task(_partner_save())
            await asyncio.wait_for(partner_written.wait(), timeout=10)
            pair_task = asyncio.create_task(_pair_save())
            # The pair save must reach the partner (and wait on it) before the partner's
            # save commits: that is the window in which the lost update happened.
            for _ in range(200):
                if pair_task.done() or await _waiting_on_a_lock(sm, pair_pid):
                    break
                await asyncio.sleep(0.05)
            assert not pair_task.done(), "the pair save did not wait for the partner's save"
            release.set()
            class_5 = await asyncio.wait_for(partner_task, timeout=10)
            await asyncio.wait_for(pair_task, timeout=10)

            partner = await _stored(sm, context, _PARTNER)
            assert partner is not None
            assert (partner["classification"], partner["acmg_class"], partner["note"]) == (
                "Pathogenic - class 5",
                "acmg_class_5",
                _NOTE,
            )
            assert partner["compound_het_partner_variant_ids"] == [_VARIANT]
            # The partner's time moves forward, past the class-5 save.
            assert partner["updated_at"] > class_5.updated_at
            variant = await _stored(sm, context, _VARIANT)
            assert variant is not None and variant["compound_het_partner_variant_ids"] == [_PARTNER]
            # Both saves are on the hash-chained trail, in the order they committed.
            events = await _audit_events(sm, context)
            assert events[-1] == (_VARIANT, "compound_het_pair")
            assert (_PARTNER, "classification") in events
            assert await _chain_verifies(sm, context)
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
