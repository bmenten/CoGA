"""End-to-end signed chain-head anchor (real Postgres, smoke job).

Drives the real writer + the real anchor service against Postgres: a signed anchor over
the live chain heads VERIFIES; a subsequent owner-bypass re-chain (recompute a head's
row_hash) or truncation (delete a head) DIVERGES from the signed anchor; tampering an
anchor's signature is caught; anchors chain; and the unsigned / unknown-key modes report
their distinct statuses. This is the evidence that the anchor closes the hash chains'
owner-re-chain / truncation gap that the in-DB chain alone cannot detect.

Assertions are scoped to THIS test's families (the whole-system anchor also captures other
tests' chains in the shared DB, so a bare ``status == "ok"`` would be brittle). The unsigned
and unknown-key modes, whose verdict is on every chain, run on a database of their own.

The admin's check of the WHOLE anchor chain (``verify_anchor_chain``, which
``/api/admin/integrity/anchor/verify-chain`` serves) runs on a database of its own, where the anchor
chain and the live chains are this test's alone: two signed anchors verify (``ok``), and so do
the latest anchor's heads; the anchors' append-only trigger refuses an UPDATE and a DELETE
(fired here, where it breaks no other test's chain); another configured key cannot check them
(``unknown_key``); the latest anchor's root, hash or heads edited behind the trigger are each
caught by both checks (``signature_invalid`` for the latest anchor, ``chain_broken`` for the
chain) and verify again once put back; an unsigned anchor in the chain makes it
``unverifiable_unsigned``; an interior anchor carrying another anchor's signature is caught by
the chain check alone (the latest anchor's check reads only the latest); and an interior anchor
deleted leaves a gap in the sequence (``chain_broken``).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it. Pure
signing/canonicalisation logic is unit-tested in ``backend/tests/test_integrity_anchor.py``.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app.core.config import settings
from backend.tests.integration._datastores import (
    anchor_signing_seed,
    create_database,
    drop_database,
    fresh_family,
    run_closing_stores,
    tamper_behind_the_trigger,
)

pytestmark = pytest.mark.integration


async def _chain(session, family_uuid: str, label: str, n: int) -> None:
    from backend.app.services.clinical_audit_service import record_clinical_event

    for i in range(n):
        await record_clinical_event(
            session, family_uuid=family_uuid, family_identifier=label,
            variant_id=f"1-{i}-A-G", actor="alice", actor_id=None,
            action="classification", summary=f"e{i}", metadata={},
        )
    await session.commit()


async def _head_id(session, label: str) -> str:
    return (
        await session.execute(
            text(
                "SELECT id::text FROM clinical_audit_events WHERE family_identifier = :f "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"f": label},
        )
    ).scalar_one()


def test_integrity_anchor_signs_verifies_and_detects_rechain_and_truncation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "integrity_anchor_signing_key", anchor_signing_seed())
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.integrity_anchor_service import (
        create_integrity_anchor,
        verify_against_latest_anchor,
    )

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            label_x = f"p1-anchor-x-{uuid4()}"

            async with sm() as s:
                fam_x = await fresh_family(s, label_x)
                await _chain(s, fam_x, label_x, 3)
            async with sm() as s:
                a1 = await create_integrity_anchor(s)
                assert a1["algo"] == "ed25519" and a1["signature"] and a1["anchor_seq"] >= 1, a1

            # A signed anchor over the untouched chain: our family is not flagged.
            async with sm() as s:
                v = await verify_against_latest_anchor(s)
                assert not any(d["family_identifier"] == label_x for d in v.diverged), v.diverged

            # Owner-bypass RE-CHAIN of family X's head (recompute its row_hash) → divergence.
            async with sm() as s:
                head = await _head_id(s, label_x)
            async with sm() as s:
                await tamper_behind_the_trigger(
                    s, "clinical_audit_events",
                    "UPDATE clinical_audit_events SET row_hash = 're-chained-by-owner' "
                    "WHERE id = CAST(:id AS uuid)",
                    {"id": head},
                )
                v = await verify_against_latest_anchor(s)
                assert v.status == "diverged", v
                assert any(
                    d["family_identifier"] == label_x and d["issue"] == "rechained"
                    for d in v.diverged
                ), v.diverged

            # Fresh anchor A2 (captures current state, chains to A1); then TRUNCATE a family.
            label_y = f"p1-anchor-y-{uuid4()}"
            async with sm() as s:
                fam_y = await fresh_family(s, label_y)
                await _chain(s, fam_y, label_y, 3)
            async with sm() as s:
                a2 = await create_integrity_anchor(s)
                assert a2["prev_anchor_hash"] == a1["anchor_hash"], (a2, a1)  # anchors chain
            async with sm() as s:
                v = await verify_against_latest_anchor(s)
                assert not any(d["family_identifier"] in (label_x, label_y) for d in v.diverged), v.diverged
            async with sm() as s:
                head_y = await _head_id(s, label_y)
                await tamper_behind_the_trigger(
                    s, "clinical_audit_events",
                    "DELETE FROM clinical_audit_events WHERE id = CAST(:id AS uuid)",
                    {"id": head_y},
                )
                v = await verify_against_latest_anchor(s)
                assert v.status == "diverged", v
                assert any(
                    d["family_identifier"] == label_y and d["issue"] == "truncated_or_shrunk"
                    for d in v.diverged
                ), v.diverged

            # Tampering the latest anchor's signature is caught before the prefix check.
            async with sm() as s:
                await tamper_behind_the_trigger(
                    s, "integrity_anchors",
                    "UPDATE integrity_anchors SET signature = 'AAAA' WHERE anchor_seq = :seq",
                    {"seq": a2["anchor_seq"]},
                )
                v = await verify_against_latest_anchor(s)
                assert v.status == "signature_invalid", v
        finally:
            await close_postgres_engine()

    asyncio.run(_run())


def test_integrity_anchor_unsigned_and_unknown_key_modes(monkeypatch) -> None:
    from backend.app.core import postgres
    from backend.app.services.integrity_anchor_service import (
        create_integrity_anchor,
        verify_against_latest_anchor,
    )

    admin_database = settings.postgres_db

    async def _run() -> None:
        database = await create_database(admin_database, prefix="coga_anchor_test")
        try:
            # A database of its own: the anchor's verdict is on every chain it holds, and
            # another test's rows (a marker row without a hash) would decide it.
            monkeypatch.setattr(settings, "postgres_db", database)
            await postgres.close_postgres_engine()
            await postgres.init_postgres_schema()
            sm = postgres.get_postgres_sessionmaker()
            label = f"p1-anchor-u-{uuid4()}"
            async with sm() as s:
                fam = await fresh_family(s, label)
                await _chain(s, fam, label, 2)

            # UNSIGNED: no key configured ⇒ anchor written 'unsigned'; verify surfaces it
            # as unverifiable (never a silent pass) while the heads still match.
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", "")
            async with sm() as s:
                rec = await create_integrity_anchor(s)
                assert rec["algo"] == "unsigned" and rec["signature"] is None, rec
            async with sm() as s:
                v = await verify_against_latest_anchor(s)
                assert v.status == "unverifiable_unsigned", v

            # UNKNOWN KEY: an anchor signed by key A cannot be verified once the configured
            # key is rotated to key B (no keyring) ⇒ unknown_key (not a false pass).
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", anchor_signing_seed())
            async with sm() as s:
                await create_integrity_anchor(s)  # signed by key A, now the latest anchor
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", anchor_signing_seed())  # key B
            async with sm() as s:
                v = await verify_against_latest_anchor(s)
                assert v.status == "unknown_key", v
        finally:
            await postgres.close_postgres_engine()
            await drop_database(admin_database, database)

    run_closing_stores(_run)


def test_the_whole_anchor_chain_is_verified_anchor_by_anchor(monkeypatch) -> None:
    from backend.app.core import postgres
    from backend.app.services.integrity_anchor_service import (
        create_integrity_anchor,
        verify_against_latest_anchor,
        verify_anchor_chain,
    )

    key_a = anchor_signing_seed()
    admin_database = settings.postgres_db

    async def _verdicts(sm):
        async with sm() as s:
            return await verify_anchor_chain(s), await verify_against_latest_anchor(s)

    async def _edit_anchor(sm, sql: str, params: dict) -> None:
        async with sm() as s:
            await tamper_behind_the_trigger(s, "integrity_anchors", sql, params)

    async def _run() -> None:
        database = await create_database(admin_database, prefix="coga_anchor_test")
        try:
            # A database of its own: the anchor chain holds this test's anchors only.
            monkeypatch.setattr(settings, "postgres_db", database)
            await postgres.close_postgres_engine()
            await postgres.init_postgres_schema()
            sm = postgres.get_postgres_sessionmaker()
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", key_a)

            chain, latest = await _verdicts(sm)
            assert (chain.status, latest.status) == ("no_anchor", "no_anchor"), (chain, latest)

            # Two signed anchors, a classification between them.
            label = f"anchor-chain-{uuid4()}"
            async with sm() as s:
                family = await fresh_family(s, label)
                await _chain(s, family, label, 2)
            async with sm() as s:
                await create_integrity_anchor(s)
            async with sm() as s:
                await _chain(s, family, label, 1)
            async with sm() as s:
                second = await create_integrity_anchor(s)
            chain, latest = await _verdicts(sm)
            assert (chain.status, chain.anchor_seq, chain.chain_count) == ("ok", 2, 2), chain
            assert (latest.status, latest.anchor_seq, latest.diverged) == ("ok", 2, []), latest

            # Here, where no other test's chain can break, the anchors' append-only trigger is
            # fired too: an anchor's UPDATE and DELETE are refused, and the chain still verifies.
            for statement, refusal in (
                ("UPDATE integrity_anchors SET key_id = 'tampered' WHERE anchor_seq = 1", "UPDATE"),
                ("DELETE FROM integrity_anchors WHERE anchor_seq = 1", "DELETE"),
            ):
                with pytest.raises(DBAPIError) as refused:
                    async with sm() as s:
                        await s.execute(text(statement))
                        await s.commit()
                assert f"integrity_anchors is append-only; {refusal} is not permitted" in str(refused.value)
            chain, _latest = await _verdicts(sm)
            assert (chain.status, chain.chain_count) == ("ok", 2), chain

            # Another key configured: no anchor's signature can be checked, never a pass.
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", anchor_signing_seed())
            chain, latest = await _verdicts(sm)
            assert (chain.status, chain.anchor_seq) == ("unknown_key", 1), chain
            assert latest.status == "unknown_key", latest
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", key_a)

            # The latest anchor's root, hash or heads edited behind the trigger: both checks
            # refuse it; put back, it verifies again.
            async with sm() as s:
                stored = (
                    await s.execute(
                        text(
                            "SELECT anchor_root, anchor_hash, heads::text AS heads "
                            "FROM integrity_anchors WHERE anchor_seq = 2"
                        )
                    )
                ).mappings().one()
            edits = {
                "anchor_root does not match stored heads": (
                    "UPDATE integrity_anchors SET anchor_root = :v WHERE anchor_seq = 2",
                    "0" * 64,
                    "UPDATE integrity_anchors SET anchor_root = :v WHERE anchor_seq = 2",
                    stored["anchor_root"],
                ),
                "anchor_hash does not match anchor content": (
                    "UPDATE integrity_anchors SET anchor_hash = :v WHERE anchor_seq = 2",
                    "0" * 64,
                    "UPDATE integrity_anchors SET anchor_hash = :v WHERE anchor_seq = 2",
                    stored["anchor_hash"],
                ),
                "heads is not a JSON array": (
                    "UPDATE integrity_anchors SET heads = CAST(:v AS jsonb) WHERE anchor_seq = 2",
                    "null",
                    "UPDATE integrity_anchors SET heads = CAST(:v AS jsonb) WHERE anchor_seq = 2",
                    stored["heads"],
                ),
            }
            for reason, (tamper, tampered, restore, original) in edits.items():
                await _edit_anchor(sm, tamper, {"v": tampered})
                chain, latest = await _verdicts(sm)
                assert (latest.status, latest.anchor_seq, latest.reason) == ("signature_invalid", 2, reason), latest
                assert (chain.status, chain.anchor_seq, chain.reason) == ("chain_broken", 2, reason), chain
                await _edit_anchor(sm, restore, {"v": original})
                chain, latest = await _verdicts(sm)
                assert (chain.status, latest.status) == ("ok", "ok"), (reason, chain, latest)

            # An unsigned anchor in the chain (no key configured, as development allows): the
            # chain is unverifiable, not a pass.
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", "")
            async with sm() as s:
                third = await create_integrity_anchor(s)
            assert (third["anchor_seq"], third["algo"]) == (3, "unsigned"), third
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", key_a)
            chain, latest = await _verdicts(sm)
            assert (chain.status, chain.anchor_seq, chain.chain_count, chain.reason) == (
                "unverifiable_unsigned", 3, 3, "1 anchor(s) in the chain are unsigned",
            ), chain
            assert latest.status == "unverifiable_unsigned", latest

            # An interior anchor carrying another anchor's (valid) signature: only the check of
            # the whole chain sees it; the latest anchor's check reads the latest alone.
            await _edit_anchor(
                sm, "UPDATE integrity_anchors SET signature = :v WHERE anchor_seq = 1", {"v": second["signature"]}
            )
            chain, latest = await _verdicts(sm)
            assert (chain.status, chain.anchor_seq) == ("signature_invalid", 1), chain
            assert latest.status == "unverifiable_unsigned", latest

            # An interior anchor deleted: the sequence has a gap where it was.
            await _edit_anchor(sm, "DELETE FROM integrity_anchors WHERE anchor_seq = 2", {})
            chain, _latest = await _verdicts(sm)
            assert (chain.status, chain.anchor_seq) == ("chain_broken", 3), chain
            assert "not contiguous" in (chain.reason or ""), chain
        finally:
            await postgres.close_postgres_engine()
            await drop_database(admin_database, database)

    run_closing_stores(_run)
