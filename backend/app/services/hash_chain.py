"""Tamper-evidence hash chaining for the append-only clinical tables.

Each row's ``row_hash`` binds its immutable content to the previous row's hash, so
deleting, reordering or editing any row in a chain becomes *detectable* — the
recomputed chain diverges at the first bad row. This complements the append-only
DB triggers (which *prevent* the application's INSERT-only path from mutating) by
making tampering by a privileged credential (which can disable a trigger) *evident*.

Design notes:
- Chains are scoped **per family** so concurrent families never contend; within a
  family the writer holds a transaction-scoped advisory lock while it reads the head
  and inserts, keeping the chain serial and consistent.
- The hashed payload EXCLUDES the FK columns the append-only triggers allow to be
  nulled by the ``ON DELETE SET NULL`` cascade (``actor_id`` / ``family_id`` /
  ``signed_out_by_id``) and binds the **denormalised** identity instead (``actor`` /
  ``family_identifier`` / ``signed_out_by``). The chain is also PARTITIONED on the
  immutable ``family_identifier`` (not the mutable ``family_id``, which the cascade
  nulls), so a legitimate account/family deletion neither breaks an existing chain nor
  makes it unverifiable.
- SCOPE: the chain makes tampering evident only against a principal who *cannot
  recompute it*. Every non-owner DB role is blocked from writing ``row_hash``/``prev_hash``
  by the append-only trigger, so their edits/deletes are detected; careless owner
  tampering (mutating a column without recomputing the hashes) is detected too. But a
  principal who can DISABLE the trigger — the table OWNER, which is the application's own
  DB role until the API runs as ``coga_app`` (docs/db-runtime-role-runbook.md) — can edit or delete an INTERIOR
  row and then recompute ``row_hash``/``prev_hash`` for it and every successor, yielding a
  self-consistent chain that verifies. Detecting a determined owner who re-chains (and
  whole-chain truncation) needs an EXTERNAL signed anchor of each chain head that the DB
  role cannot forge: the signed anchor (``integrity_anchor_service``, #263), together with
  the restricted runtime role (#262).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Sequence

GENESIS = "GENESIS"

# Above 2**53 every finite float is an exact integer, and Python renders some of them
# in exponential form ('1e+16') while Postgres JSONB stores + returns them as a plain
# integer numeric (decoded back as a Python int). That makes the SAME value hash
# differently pre-store (float) vs post-read (int). Coercing such floats to int on BOTH
# sides collapses the two encodings. Values below 2**53 already round-trip identically,
# so they are left untouched (existing content hashes are unchanged).
_INT_FLOAT_THRESHOLD = 2 ** 53


def _normalize_numbers(value: Any) -> Any:
    """Mirror Postgres JSONB's large-integral-float normalization (see note above)."""
    if isinstance(value, dict):
        return {k: _normalize_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_numbers(v) for v in value]
    if isinstance(value, float) and math.isfinite(value) and abs(value) >= _INT_FLOAT_THRESHOLD:
        return int(value)
    return value


def canonical_json(payload: Any) -> str:
    """Deterministic JSON encoding — the single source of truth for all hashing."""
    return json.dumps(
        _normalize_numbers(payload), sort_keys=True, separators=(",", ":"), default=str
    )


def canonical_hash(payload: Any) -> str:
    """SHA-256 over the canonical encoding of a payload."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def chain_row_hash(prev_hash: str | None, payload: Any) -> str:
    """row_hash = SHA-256( (prev_hash or GENESIS) ‖ '\\n' ‖ canonical(payload) )."""
    base = f"{prev_hash or GENESIS}\n{canonical_json(payload)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ChainVerification:
    verified: bool
    rows_checked: int
    first_bad_row: str | None = None
    reason: str | None = None


def verify_chain(
    rows: Sequence[dict[str, Any]],
    payload_of: Callable[[dict[str, Any]], Any],
) -> ChainVerification:
    """Walk already-chained rows in order, recompute each ``row_hash`` and check links.

    ``rows`` are the chained rows (``row_hash IS NOT NULL``) in chain order; each row
    dict carries ``id``, ``row_hash`` and ``prev_hash``. ``payload_of(row)`` returns the
    immutable payload that was hashed at insert time (must match the writer exactly).

    Detects content tampering (recomputed ``row_hash`` mismatch) and deletion/reordering
    (a row's ``prev_hash`` no longer points at its predecessor's ``row_hash``).
    """
    prev_row_hash: str | None = None
    for index, row in enumerate(rows):
        stored_prev = row.get("prev_hash")
        if index == 0:
            # Chain origin: prev points at genesis (or the last pre-chain row, NULL).
            if stored_prev not in (None, GENESIS):
                return ChainVerification(
                    False, index, str(row.get("id")), "first chained row is not a genesis link"
                )
        elif stored_prev != prev_row_hash:
            return ChainVerification(
                False,
                index,
                str(row.get("id")),
                "prev_hash does not match the predecessor row_hash (row deleted or reordered)",
            )
        recomputed = chain_row_hash(stored_prev, payload_of(row))
        if row.get("row_hash") != recomputed:
            return ChainVerification(
                False, index, str(row.get("id")), "row_hash mismatch (row content tampered)"
            )
        prev_row_hash = row.get("row_hash")
    return ChainVerification(True, len(rows), None, None)
