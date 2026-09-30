"""Signed chain-head anchor (#263): pure signing + canonicalisation logic.

End-to-end anchoring against real Postgres (create → verify → detect re-chain/truncation)
lives in ``backend/tests/integration/test_integrity_anchor_integration.py`` (smoke job).
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from backend.app.core.config import settings
from backend.app.services import hash_chain
from backend.app.services import integrity_anchor_service as ias


def _gen_key_b64() -> str:
    raw = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    return base64.b64encode(raw).decode()


def test_load_signing_key_unset_is_unsigned(monkeypatch) -> None:
    monkeypatch.setattr(settings, "integrity_anchor_signing_key", "")
    assert ias._load_signing_key() is None


def test_load_signing_key_and_sign_verify_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(settings, "integrity_anchor_signing_key", _gen_key_b64())
    key = ias._load_signing_key()
    assert key is not None and key.key_id.startswith("ed25519:")

    core = ias._signed_core(
        anchor_seq=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        prev_anchor_hash=None,
        anchor_root="root",
        chain_count=0,
        key_id=key.key_id,
        algo="ed25519",
        heads=[],
    )
    msg = hash_chain.canonical_json(core).encode("utf-8")
    sig = key.private.sign(msg)
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(key.public_b64))
    public.verify(sig, msg)  # genuine signature verifies
    with pytest.raises(InvalidSignature):
        public.verify(sig, msg + b"tampered")  # any change to the signed bytes fails


def test_sort_heads_is_deterministic_and_root_is_order_independent() -> None:
    heads = [
        {"family_identifier": "B", "table": "report_signouts", "height": 1, "head_row_hash": "h1"},
        {"family_identifier": None, "table": "clinical_audit_events", "height": 2, "head_row_hash": "h2"},
        {"family_identifier": "A", "table": "report_signouts", "height": 1, "head_row_hash": "h3"},
    ]
    ordered = ias._sort_heads(list(heads))
    assert [(h["table"], h["family_identifier"]) for h in ordered] == [
        ("clinical_audit_events", None),
        ("report_signouts", "A"),
        ("report_signouts", "B"),
    ]
    # The anchor_root is independent of the input order (sort then hash).
    assert hash_chain.canonical_hash(ordered) == hash_chain.canonical_hash(
        ias._sort_heads(list(reversed(heads)))
    )


def test_signed_core_renders_created_at_isoformat() -> None:
    core = ias._signed_core(
        anchor_seq=3,
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        prev_anchor_hash="prev",
        anchor_root="root",
        chain_count=1,
        key_id="k",
        algo="ed25519",
        heads=[{"a": 1}],
    )
    assert core["created_at"] == "2026-01-02T03:04:05+00:00"
    assert core["anchor_seq"] == 3 and core["prev_anchor_hash"] == "prev"


def _make_anchor(seq, prev_hash, *, algo="unsigned", key_id="unsigned"):
    heads = [
        {"table": "report_signouts", "family_identifier": "F", "height": seq, "head_row_hash": f"h{seq}"}
    ]
    created_at = datetime(2026, 1, min(seq, 28), tzinfo=timezone.utc)
    anchor_root = hash_chain.canonical_hash(heads)
    core = ias._signed_core(
        anchor_seq=seq, created_at=created_at, prev_anchor_hash=prev_hash,
        anchor_root=anchor_root, chain_count=len(heads), key_id=key_id, algo=algo, heads=heads,
    )
    return {
        "anchor_seq": seq, "created_at": created_at, "prev_anchor_hash": prev_hash,
        "anchor_root": anchor_root, "anchor_hash": hash_chain.chain_row_hash(prev_hash, core),
        "heads": heads, "chain_count": len(heads), "key_id": key_id, "algo": algo, "signature": None,
    }


def _valid_chain(n=3):
    anchors, prev = [], None
    for seq in range(1, n + 1):
        a = _make_anchor(seq, prev)
        anchors.append(a)
        prev = a["anchor_hash"]
    return anchors


def test_check_anchor_chain_accepts_valid_contiguous_chain() -> None:
    v = ias._check_anchor_chain(_valid_chain(3))
    assert v.status == "ok"
    assert v.anchor_seq == 3 and v.chain_count == 3


def test_check_anchor_chain_empty_is_no_anchor() -> None:
    assert ias._check_anchor_chain([]).status == "no_anchor"


def test_check_anchor_chain_detects_deleted_interior_anchor() -> None:
    chain = _valid_chain(3)
    del chain[1]  # remove anchor seq 2 -> [1, 3] leaves a sequence gap
    v = ias._check_anchor_chain(chain)
    assert v.status == "chain_broken"
    assert "contiguous" in (v.reason or "")


def test_check_anchor_chain_detects_relinked_prev_hash() -> None:
    chain = _valid_chain(3)
    chain[1]["prev_anchor_hash"] = "0" * 64  # re-linked / prior anchor swapped
    v = ias._check_anchor_chain(chain)
    assert v.status == "chain_broken"
    assert "prev_anchor_hash" in (v.reason or "")


def test_check_anchor_chain_detects_tampered_anchor_hash() -> None:
    chain = _valid_chain(2)
    chain[1]["anchor_hash"] = "de" * 32  # content edited without re-minting the hash
    v = ias._check_anchor_chain(chain)
    assert v.status == "chain_broken"
    assert "anchor_hash" in (v.reason or "")


def test_check_anchor_chain_rejects_non_list_heads() -> None:
    # A 'null'::jsonb scalar decodes to None; the walk must not raise (it never does).
    chain = _valid_chain(1)
    chain[0]["heads"] = None
    v = ias._check_anchor_chain(chain)
    assert v.status == "chain_broken"
    assert "JSON array" in (v.reason or "")
