"""External signed chain-head anchor (P1-4 follow-up).

Periodically snapshot every per-family hash-chain HEAD (across ``report_signouts`` and
``clinical_audit_events``) and seal the snapshot with an Ed25519 signature whose private
key lives in app config/env — never in the database. This closes the part of the P1-4
gap the in-DB chain cannot: an OWNER who DISABLEs the trigger can re-chain an interior
edit or truncate a chain into a self-consistent state that ``verify_*_chain`` accepts, but
they cannot mint a matching signed anchor (no key), so the divergence between the live
chain and the last *signed* head becomes detectable by a verifier that does not trust the
database.

HONEST TRUST BOUNDARY (do not overclaim — this is tamper-EVIDENT, not tamper-proof):
- Detects, against a database-only adversary (SQL/DBA/backup-restore, or a credential that
  is not the app's signing key): interior re-chaining and truncation/shrink of a chain that
  occur BETWEEN a valid signed anchor and a later retained anchor; forging a new anchor over
  doctored state (signature fails); editing/deleting an INTERIOR anchor (anchor chain breaks).
- Does NOT defend against an adversary who holds the signing key (host/app compromise) — only
  an HSM/external signer closes that (deferred).
- Does NOT by itself detect deletion of the most-recent (TAIL) anchors: the DB owner can
  DISABLE the append-only trigger and delete anchor rows, then re-link. Only an OUT-OF-BAND
  retained copy of the latest anchor (the deferred export seam ``export_anchor``) makes that
  evident. Until the P1-3 DSN flip the app connects as owner, so this residual is live.
- Data written before a chain's first anchor is "unanchored" — no external assurance.
- KEY ROTATION: verify trusts the single CONFIGURED key; an anchor signed by a retired key
  returns ``unknown_key`` (runtime monitoring keeps working once a fresh anchor is cut, but
  audit-time re-verification of pre-rotation anchors needs the prior public key). A
  configured keyring (verify by matching key_id) is the deferred fix; retain prior public
  keys until then.
Regulator-safe phrasing: "tamper-EVIDENT against a database-only adversary, between retained
anchors". Never "tamper-proof" / "immutable" / "non-repudiation of the database".
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from . import hash_chain

logger = logging.getLogger(__name__)

# The two append-only tables that carry per-family hash chains, mapped to the canonical
# chain-order COLUMNS (ascending). The head is the LAST row in this order; the prefix check
# reads the height-th row ascending. (Fixed set — never interpolate untrusted table names.)
_CHAIN_ORDER_COLS = {
    "report_signouts": ["version"],
    "clinical_audit_events": ["created_at", "id"],
}
_ANCHOR_LOCK_KEY = "integrity_anchor"


def _order_by(table: str, direction: str) -> str:
    """Build the chain ORDER BY for a table in the given direction ('ASC' | 'DESC')."""
    return ", ".join(f"{col} {direction}" for col in _CHAIN_ORDER_COLS[table])


@dataclass(slots=True)
class _SigningKey:
    private: Ed25519PrivateKey
    public_b64: str
    key_id: str


def _load_signing_key() -> _SigningKey | None:
    """Load the Ed25519 signing key from config (base64 of the 32-byte seed). Returns
    None when unset — anchors are then written 'unsigned' (a non-owner-only control)."""
    raw = (settings.integrity_anchor_signing_key or "").strip()
    if not raw:
        return None
    seed = base64.b64decode(raw)
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _SigningKey(
        private=private,
        public_b64=base64.b64encode(public).decode(),
        # 64-bit digest of the public key — wide enough that distinct keys get distinct
        # ids (so the unknown_key vs signature_invalid distinction stays accurate).
        key_id="ed25519:" + hashlib.sha256(public).hexdigest()[:16],
    )


def _signed_core(
    *,
    anchor_seq: int,
    created_at: datetime,
    prev_anchor_hash: str | None,
    anchor_root: str,
    chain_count: int,
    key_id: str,
    algo: str,
    heads: list[dict[str, Any]],
) -> dict[str, Any]:
    """The exact object that is hashed (anchor_hash) AND signed — reconstructed
    identically at create and verify time."""
    return {
        "anchor_seq": anchor_seq,
        # Normalise to UTC so the signed/hashed bytes are identical at create and verify
        # regardless of the DB session timezone (asyncpg returns tz-aware timestamptz).
        "created_at": (
            created_at.astimezone(timezone.utc).isoformat()
            if hasattr(created_at, "astimezone")
            else str(created_at)
        ),
        "prev_anchor_hash": prev_anchor_hash,
        "anchor_root": anchor_root,
        "chain_count": chain_count,
        "key_id": key_id,
        "algo": algo,
        "heads": heads,
    }


def _sort_heads(heads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic order: by table, then family_identifier (NULL/orphan last)."""
    return sorted(heads, key=lambda h: (h["table"], h["family_identifier"] is None, h["family_identifier"] or ""))


async def _capture_heads(session: AsyncSession) -> list[dict[str, Any]]:
    """Snapshot every non-empty chain's head: (family_identifier, table, height, head_row_hash)."""
    heads: list[dict[str, Any]] = []
    for table in _CHAIN_ORDER_COLS:
        rows = (
            await session.execute(
                text(
                    f"SELECT family_identifier AS fid, count(*) AS height, "
                    f"(array_agg(row_hash ORDER BY {_order_by(table, 'DESC')}))[1] AS head_row_hash "
                    f"FROM {table} WHERE row_hash IS NOT NULL GROUP BY family_identifier"
                )
            )
        ).mappings().all()
        for row in rows:
            heads.append(
                {
                    "family_identifier": row["fid"],
                    "table": table,
                    "height": int(row["height"]),
                    "head_row_hash": row["head_row_hash"],
                }
            )
    return _sort_heads(heads)


async def create_integrity_anchor(session: AsyncSession) -> dict[str, Any]:
    """Capture all chain heads, sign them, and append one anchor. Commits the caller's tx."""
    key = _load_signing_key()
    if key is None and not settings.is_development:
        # An unsigned anchor carries NO anti-owner tamper-evidence (the owner can re-mint
        # it over doctored state), so a real environment does not write one (#522). The
        # startup check already refuses to run there without a signing key; this keeps
        # the anchor honest even if that check is ever bypassed.
        raise RuntimeError(
            "Refusing to write an UNSIGNED integrity anchor outside development "
            f"(APP_ENV={settings.app_env}): configure INTEGRITY_ANCHOR_SIGNING_KEY."
        )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": _ANCHOR_LOCK_KEY}
    )
    prev = (
        await session.execute(
            text("SELECT anchor_seq, anchor_hash FROM integrity_anchors ORDER BY anchor_seq DESC LIMIT 1")
        )
    ).mappings().first()
    anchor_seq = (prev["anchor_seq"] + 1) if prev else 1
    prev_anchor_hash = prev["anchor_hash"] if prev else None

    heads = await _capture_heads(session)
    anchor_root = hash_chain.canonical_hash(heads)
    created_at = datetime.now(timezone.utc)

    algo = "ed25519" if key else "unsigned"
    key_id = key.key_id if key else "unsigned"
    public_b64 = key.public_b64 if key else None

    core = _signed_core(
        anchor_seq=anchor_seq,
        created_at=created_at,
        prev_anchor_hash=prev_anchor_hash,
        anchor_root=anchor_root,
        chain_count=len(heads),
        key_id=key_id,
        algo=algo,
        heads=heads,
    )
    anchor_hash = hash_chain.chain_row_hash(prev_anchor_hash, core)
    signature = (
        base64.b64encode(key.private.sign(hash_chain.canonical_json(core).encode("utf-8"))).decode()
        if key
        else None
    )

    await session.execute(
        text(
            """
            INSERT INTO integrity_anchors
                (anchor_seq, created_at, prev_anchor_hash, anchor_root, anchor_hash,
                 heads, chain_count, key_id, algo, public_key, signature)
            VALUES
                (:anchor_seq, :created_at, :prev_anchor_hash, :anchor_root, :anchor_hash,
                 CAST(:heads AS jsonb), :chain_count, :key_id, :algo, :public_key, :signature)
            """
        ),
        {
            "anchor_seq": anchor_seq,
            "created_at": created_at,
            "prev_anchor_hash": prev_anchor_hash,
            "anchor_root": anchor_root,
            "anchor_hash": anchor_hash,
            "heads": hash_chain.canonical_json(heads),
            "chain_count": len(heads),
            "key_id": key_id,
            "algo": algo,
            "public_key": public_b64,
            "signature": signature,
        },
    )
    await session.commit()
    record = {
        "anchor_seq": anchor_seq,
        "created_at": created_at,
        "prev_anchor_hash": prev_anchor_hash,
        "anchor_root": anchor_root,
        "anchor_hash": anchor_hash,
        "chain_count": len(heads),
        "key_id": key_id,
        "algo": algo,
        "public_key": public_b64,
        "signature": signature,
        "signed_core": core,
    }
    export_anchor(record)
    return record


def export_anchor(record: dict[str, Any]) -> None:
    """Seam for the deferred out-of-band export — shipping each signed anchor to an
    append-only store the DB owner cannot reach is the ONLY thing that makes deletion of
    the latest anchors detectable. Intentionally a no-op here (no deployment coupling)."""
    return None


@dataclass(slots=True)
class AnchorVerification:
    status: str  # ok | diverged | chain_broken | signature_invalid | unknown_key | unverifiable_unsigned | no_anchor
    anchor_seq: int | None = None
    chain_count: int = 0
    diverged: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None


async def _head_row_hash_at_height(
    session: AsyncSession, table: str, family_identifier: str | None, height: int
) -> str | None:
    """The row_hash of the ``height``-th chained row in canonical order, or None if the
    live chain has fewer than ``height`` chained rows (truncated/shrunk)."""
    fid_clause = "family_identifier IS NULL" if family_identifier is None else "family_identifier = :fid"
    params: dict[str, Any] = {"off": max(height - 1, 0)}
    if family_identifier is not None:
        params["fid"] = family_identifier
    return (
        await session.execute(
            text(
                f"SELECT row_hash FROM {table} WHERE {fid_clause} AND row_hash IS NOT NULL "
                f"ORDER BY {_order_by(table, 'ASC')} OFFSET :off LIMIT 1"
            ),
            params,
        )
    ).scalar_one_or_none()


async def verify_against_latest_anchor(session: AsyncSession) -> AnchorVerification:
    """Verify the live chains against the latest signed anchor: signature + per-family
    prefix check. Never raises — always returns a verdict."""
    anchor = (
        await session.execute(
            text(
                "SELECT anchor_seq, created_at, prev_anchor_hash, anchor_root, anchor_hash, "
                "heads, chain_count, key_id, algo, signature FROM integrity_anchors "
                "ORDER BY anchor_seq DESC LIMIT 1"
            )
        )
    ).mappings().first()
    if anchor is None:
        return AnchorVerification(status="no_anchor", reason="no anchor has been created yet")

    if not isinstance(anchor["heads"], list):
        return AnchorVerification(
            status="signature_invalid", anchor_seq=anchor["anchor_seq"],
            reason="heads is not a JSON array",
        )
    heads = list(anchor["heads"])
    core = _signed_core(
        anchor_seq=anchor["anchor_seq"],
        created_at=anchor["created_at"],
        prev_anchor_hash=anchor["prev_anchor_hash"],
        anchor_root=anchor["anchor_root"],
        chain_count=anchor["chain_count"],
        key_id=anchor["key_id"],
        algo=anchor["algo"],
        heads=heads,
    )
    # Internal consistency: the stored root + anchor_hash must match the stored heads.
    if hash_chain.canonical_hash(heads) != anchor["anchor_root"]:
        return AnchorVerification(
            status="signature_invalid", anchor_seq=anchor["anchor_seq"],
            reason="anchor_root does not match stored heads",
        )
    if hash_chain.chain_row_hash(anchor["prev_anchor_hash"], core) != anchor["anchor_hash"]:
        return AnchorVerification(
            status="signature_invalid", anchor_seq=anchor["anchor_seq"],
            reason="anchor_hash does not match anchor content",
        )

    # Signature: verify with the CONFIGURED key (not any stored public_key column).
    if anchor["algo"] == "ed25519":
        key = _load_signing_key()
        if key is None or key.key_id != anchor["key_id"]:
            return AnchorVerification(
                status="unknown_key", anchor_seq=anchor["anchor_seq"],
                reason="no configured key matches the anchor's key_id; cannot verify signature",
            )
        try:
            Ed25519PublicKey.from_public_bytes(
                base64.b64decode(key.public_b64)
            ).verify(base64.b64decode(anchor["signature"] or ""), hash_chain.canonical_json(core).encode("utf-8"))
        except (InvalidSignature, ValueError, TypeError):
            return AnchorVerification(
                status="signature_invalid", anchor_seq=anchor["anchor_seq"],
                reason="Ed25519 signature verification failed",
            )

    # Prefix check: each anchored head must still be the row_hash at that height.
    diverged: list[dict[str, Any]] = []
    for head in heads:
        table = head["table"]
        if table not in _CHAIN_ORDER_COLS:
            diverged.append({**head, "issue": "unknown_table"})
            continue
        live = await _head_row_hash_at_height(session, table, head["family_identifier"], head["height"])
        if live is None:
            diverged.append({**head, "issue": "truncated_or_shrunk", "live_head_row_hash": None})
        elif live != head["head_row_hash"]:
            diverged.append({**head, "issue": "rechained", "live_head_row_hash": live})

    status = "ok" if not diverged else "diverged"
    if anchor["algo"] == "unsigned" and status == "ok":
        status = "unverifiable_unsigned"  # heads match, but the anchor itself is unsigned
    return AnchorVerification(
        status=status,
        anchor_seq=anchor["anchor_seq"],
        chain_count=anchor["chain_count"],
        diverged=diverged,
    )


def _check_anchor_chain(anchors: list[dict[str, Any]]) -> AnchorVerification:
    """Structural walk of the FULL anchor chain (ordered by anchor_seq ASC): contiguous
    sequence numbers, prev_anchor_hash continuity, and per-anchor hash/root recomputation.

    These checks are all keyless SHA-256, so they catch SLOPPY interior tampering (a
    deletion that leaves a sequence gap, or a re-link that leaves a dangling
    prev_anchor_hash). A competent owner who deletes an interior anchor and recomputes the
    whole suffix's hashes leaves NO structural gap — that is caught only by the per-anchor
    SIGNATURE check in verify_anchor_chain (the owner cannot re-sign without the key), or,
    if they downgrade the suffix to unsigned, surfaces as ``unverifiable_unsigned`` there.
    So this pure/DB-free helper (unit-testable) is one layer; the full guarantee needs
    verify_anchor_chain's signature loop, and verify_against_latest_anchor separately
    checks the latest anchor's heads against the live chains.
    """
    if not anchors:
        return AnchorVerification(status="no_anchor", reason="no anchor has been created yet")
    prev_hash: str | None = None
    for index, anchor in enumerate(anchors):
        seq = anchor["anchor_seq"]
        if seq != index + 1:
            return AnchorVerification(
                status="chain_broken",
                anchor_seq=seq,
                reason=(
                    f"anchor_seq is not contiguous (expected {index + 1}, found {seq}) — "
                    "an interior anchor was deleted"
                ),
            )
        if anchor["prev_anchor_hash"] != prev_hash:
            return AnchorVerification(
                status="chain_broken",
                anchor_seq=seq,
                reason=(
                    "prev_anchor_hash does not match the prior anchor's anchor_hash — the "
                    "anchor chain was re-linked or an interior anchor removed"
                ),
            )
        raw_heads = anchor["heads"]
        if not isinstance(raw_heads, list):
            # A JSONB scalar/null (e.g. 'null'::jsonb) decodes to a non-list; treat it as
            # a broken anchor rather than letting list() raise (this fn never raises).
            return AnchorVerification(
                status="chain_broken", anchor_seq=seq, reason="heads is not a JSON array",
            )
        heads = list(raw_heads)
        if hash_chain.canonical_hash(heads) != anchor["anchor_root"]:
            return AnchorVerification(
                status="chain_broken", anchor_seq=seq,
                reason="anchor_root does not match stored heads",
            )
        core = _signed_core(
            anchor_seq=seq,
            created_at=anchor["created_at"],
            prev_anchor_hash=anchor["prev_anchor_hash"],
            anchor_root=anchor["anchor_root"],
            chain_count=anchor["chain_count"],
            key_id=anchor["key_id"],
            algo=anchor["algo"],
            heads=heads,
        )
        if hash_chain.chain_row_hash(prev_hash, core) != anchor["anchor_hash"]:
            return AnchorVerification(
                status="chain_broken", anchor_seq=seq,
                reason="anchor_hash does not match anchor content",
            )
        prev_hash = anchor["anchor_hash"]
    return AnchorVerification(
        status="ok", anchor_seq=anchors[-1]["anchor_seq"], chain_count=len(anchors)
    )


async def verify_anchor_chain(session: AsyncSession) -> AnchorVerification:
    """Verify the ENTIRE anchor chain — structural continuity (via _check_anchor_chain)
    plus every signed anchor's Ed25519 signature. Complements verify_against_latest_anchor
    (which checks the latest anchor's heads against the live chains). Never raises."""
    rows = (
        await session.execute(
            text(
                "SELECT anchor_seq, created_at, prev_anchor_hash, anchor_root, anchor_hash, "
                "heads, chain_count, key_id, algo, signature FROM integrity_anchors "
                "ORDER BY anchor_seq ASC"
            )
        )
    ).mappings().all()
    anchors = [dict(row) for row in rows]
    structural = _check_anchor_chain(anchors)
    if structural.status != "ok":
        return structural

    # Structure is intact; now confirm every signed anchor's signature with the CONFIGURED
    # key, and flag any unsigned anchor in the chain (not just the latest).
    key = _load_signing_key()
    unsigned = 0
    for anchor in anchors:
        if anchor["algo"] != "ed25519":
            unsigned += 1
            continue
        if key is None or key.key_id != anchor["key_id"]:
            return AnchorVerification(
                status="unknown_key", anchor_seq=anchor["anchor_seq"],
                reason="no configured key matches an anchor's key_id; cannot verify its signature",
            )
        core = _signed_core(
            anchor_seq=anchor["anchor_seq"],
            created_at=anchor["created_at"],
            prev_anchor_hash=anchor["prev_anchor_hash"],
            anchor_root=anchor["anchor_root"],
            chain_count=anchor["chain_count"],
            key_id=anchor["key_id"],
            algo=anchor["algo"],
            heads=list(anchor["heads"]),
        )
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key.public_b64)).verify(
                base64.b64decode(anchor["signature"] or ""),
                hash_chain.canonical_json(core).encode("utf-8"),
            )
        except (InvalidSignature, ValueError, TypeError):
            return AnchorVerification(
                status="signature_invalid", anchor_seq=anchor["anchor_seq"],
                reason="Ed25519 signature verification failed",
            )
    if unsigned:
        return AnchorVerification(
            status="unverifiable_unsigned",
            anchor_seq=anchors[-1]["anchor_seq"],
            chain_count=len(anchors),
            reason=f"{unsigned} anchor(s) in the chain are unsigned",
        )
    return AnchorVerification(
        status="ok", anchor_seq=anchors[-1]["anchor_seq"], chain_count=len(anchors)
    )
