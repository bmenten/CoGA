"""End-to-end signed chain-head anchor (real Postgres, smoke job).

Drives the real writer + the real anchor service against Postgres: a signed anchor over
the live chain heads VERIFIES; a subsequent owner-bypass re-chain (recompute a head's
row_hash) or truncation (delete a head) DIVERGES from the signed anchor; tampering an
anchor's signature is caught; anchors chain; and the unsigned / unknown-key modes report
their distinct statuses. This is the evidence that the anchor (#263) closes the hash chains'
owner-re-chain / truncation gap that the in-DB chain alone cannot detect.

Assertions are scoped to THIS test's families (the whole-system anchor also captures other
tests' chains in the shared DB, so a bare ``status == "ok"`` would be brittle).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it. Pure
signing/canonicalisation logic is unit-tested in ``backend/tests/test_integrity_anchor.py``.
"""

from __future__ import annotations

import asyncio
import base64
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from sqlalchemy import text

from backend.app.core.config import settings

pytestmark = pytest.mark.integration


def _gen_key_b64() -> str:
    raw = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    return base64.b64encode(raw).decode()


async def _bypass_trigger_mutate(session, table: str, sql: str, params: dict) -> None:
    await session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
    await session.execute(text(sql), params)
    await session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
    await session.commit()


async def _fresh_family(session, label: str) -> str:
    return (
        await session.execute(
            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
            {"f": label},
        )
    ).scalar_one()


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
                "AND row_hash IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"f": label},
        )
    ).scalar_one()


def test_integrity_anchor_signs_verifies_and_detects_rechain_and_truncation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "integrity_anchor_signing_key", _gen_key_b64())
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
                fam_x = await _fresh_family(s, label_x)
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
                await _bypass_trigger_mutate(
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
                fam_y = await _fresh_family(s, label_y)
                await _chain(s, fam_y, label_y, 3)
            async with sm() as s:
                a2 = await create_integrity_anchor(s)
                assert a2["prev_anchor_hash"] == a1["anchor_hash"], (a2, a1)  # anchors chain
            async with sm() as s:
                v = await verify_against_latest_anchor(s)
                assert not any(d["family_identifier"] in (label_x, label_y) for d in v.diverged), v.diverged
            async with sm() as s:
                head_y = await _head_id(s, label_y)
                await _bypass_trigger_mutate(
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
                await _bypass_trigger_mutate(
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
            label = f"p1-anchor-u-{uuid4()}"
            async with sm() as s:
                fam = await _fresh_family(s, label)
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
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", _gen_key_b64())
            async with sm() as s:
                await create_integrity_anchor(s)  # signed by key A, now the latest anchor
            monkeypatch.setattr(settings, "integrity_anchor_signing_key", _gen_key_b64())  # key B
            async with sm() as s:
                v = await verify_against_latest_anchor(s)
                assert v.status == "unknown_key", v
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
