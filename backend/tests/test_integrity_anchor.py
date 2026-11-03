"""Signed chain-head anchor (#263): signing, canonicalisation, and the writer and verifiers.

The pure parts (key loading, the signed core, the head order, the structural walk of the
anchor chain), then ``create_integrity_anchor``, ``verify_against_latest_anchor`` and
``verify_anchor_chain`` against a stand-in for the database: every verdict a verifier can
give (ok, diverged, chain_broken, signature_invalid, unknown_key, unverifiable_unsigned,
no_anchor), and no unsigned anchor written outside development (#522). End-to-end anchoring
against real Postgres (create → verify → detect re-chain/truncation) lives in
``backend/tests/integration/test_integrity_anchor_integration.py`` (smoke job).
"""

from __future__ import annotations

import asyncio
import base64
import json
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


# --- an anchor written and verified through its database (create, verify, verify the chain) ---
#
# The writer and the two verifiers read and write through a session: here a stand-in that keeps
# ``integrity_anchors`` (heads decoded from JSONB, as Postgres gives them back) and each chained
# table's row hashes per family, in chain order. Real Postgres, with a privileged re-chain and
# truncation, is in ``integration/test_integrity_anchor_integration.py``.


class _Rows:
    def __init__(self, rows: list[dict] | None = None, scalar: object = None) -> None:
        self._rows, self._scalar = rows or [], scalar

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict]:
        return list(self._rows)

    def scalar_one_or_none(self) -> object:
        return self._scalar


class _AnchorDatabase:
    def __init__(self, chains: dict[str, dict[str | None, list[str]]]) -> None:
        self.chains = chains  # table -> family identifier -> chained row hashes, in order
        self.anchors: list[dict] = []
        self.statements: list[str] = []
        self.commits = 0

    async def execute(self, statement, params=None) -> _Rows:
        sql = " ".join(str(statement).split())
        params = dict(params or {})
        self.statements.append(sql)
        if "pg_advisory_xact_lock" in sql:
            return _Rows()
        if sql.startswith("SELECT anchor_seq, anchor_hash FROM integrity_anchors"):
            return _Rows(self.anchors[-1:])
        if sql.startswith("SELECT family_identifier AS fid"):
            table = sql.split(" FROM ")[1].split()[0]
            heads = [
                {"fid": fid, "height": len(hashes), "head_row_hash": hashes[-1]}
                for fid, hashes in self.chains.get(table, {}).items()
                if hashes
            ]
            return _Rows(heads)
        if sql.startswith("INSERT INTO integrity_anchors"):
            self.anchors.append({**params, "heads": json.loads(params["heads"])})
            return _Rows()
        if "FROM integrity_anchors ORDER BY anchor_seq DESC LIMIT 1" in sql:
            return _Rows(self.anchors[-1:])
        if "FROM integrity_anchors ORDER BY anchor_seq ASC" in sql:
            return _Rows(list(self.anchors))
        if sql.startswith("SELECT row_hash FROM"):
            table = sql.split(" FROM ")[1].split()[0]
            hashes = self.chains.get(table, {}).get(params.get("fid"), [])
            return _Rows(scalar=hashes[params["off"]] if params["off"] < len(hashes) else None)
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self) -> None:
        self.commits += 1


def _chains() -> dict[str, dict[str | None, list[str]]]:
    return {
        "report_signouts": {"FAM1": ["s1", "s2"], "FAM2": ["t1"]},
        "clinical_audit_events": {"FAM1": ["a1", "a2", "a3"], None: ["o1"]},
        "family_structure_versions": {"FAM1": ["v1"]},
    }


def _anchored(monkeypatch, key_b64: str = "", *, app_env: str = "production") -> _AnchorDatabase:
    """A database whose chains were just anchored, signed with ``key_b64`` (or unsigned)."""
    monkeypatch.setattr(settings, "integrity_anchor_signing_key", key_b64)
    monkeypatch.setattr(settings, "app_env", app_env)
    database = _AnchorDatabase(_chains())
    asyncio.run(ias.create_integrity_anchor(database))  # type: ignore[arg-type]
    return database


def _latest(database: _AnchorDatabase) -> ias.AnchorVerification:
    return asyncio.run(ias.verify_against_latest_anchor(database))  # type: ignore[arg-type]


def _whole_chain(database: _AnchorDatabase) -> ias.AnchorVerification:
    return asyncio.run(ias.verify_anchor_chain(database))  # type: ignore[arg-type]


def _signed_core_of(anchor: dict) -> dict:
    return ias._signed_core(
        anchor_seq=anchor["anchor_seq"],
        created_at=anchor["created_at"],
        prev_anchor_hash=anchor["prev_anchor_hash"],
        anchor_root=anchor["anchor_root"],
        chain_count=anchor["chain_count"],
        key_id=anchor["key_id"],
        algo=anchor["algo"],
        heads=anchor["heads"],
    )


def test_an_anchor_signs_every_chains_head_and_verifies_against_the_live_chains(monkeypatch) -> None:
    database = _anchored(monkeypatch, _gen_key_b64())

    [anchor] = database.anchors
    # Taken under the anchors' own lock, before the previous anchor is read; committed once.
    assert "pg_advisory_xact_lock" in database.statements[0] and database.commits == 1
    assert (anchor["anchor_seq"], anchor["prev_anchor_hash"], anchor["algo"]) == (1, None, "ed25519")
    # Every family's head of every chained table, the orphan partition last within its table.
    assert [
        (head["table"], head["family_identifier"], head["height"], head["head_row_hash"]) for head in anchor["heads"]
    ] == [
        ("clinical_audit_events", "FAM1", 3, "a3"),
        ("clinical_audit_events", None, 1, "o1"),
        ("family_structure_versions", "FAM1", 1, "v1"),
        ("report_signouts", "FAM1", 2, "s2"),
        ("report_signouts", "FAM2", 1, "t1"),
    ]
    assert anchor["chain_count"] == 5 and anchor["anchor_root"] == hash_chain.canonical_hash(anchor["heads"])
    # The configured key signed the core, which the stored public key verifies, and the hash
    # binds the core to the anchor before it.
    core = _signed_core_of(anchor)
    Ed25519PublicKey.from_public_bytes(base64.b64decode(anchor["public_key"])).verify(
        base64.b64decode(anchor["signature"]), hash_chain.canonical_json(core).encode("utf-8")
    )
    assert anchor["anchor_hash"] == hash_chain.chain_row_hash(None, core)

    latest = _latest(database)
    assert (latest.status, latest.anchor_seq, latest.chain_count, latest.diverged) == ("ok", 1, 5, [])
    # The next anchor links to this one, and the whole anchor chain verifies.
    asyncio.run(ias.create_integrity_anchor(database))  # type: ignore[arg-type]
    assert (database.anchors[1]["anchor_seq"], database.anchors[1]["prev_anchor_hash"]) == (2, anchor["anchor_hash"])
    whole = _whole_chain(database)
    assert (whole.status, whole.anchor_seq, whole.chain_count) == ("ok", 2, 2)


def test_a_chain_rechained_or_truncated_since_its_anchor_is_named(monkeypatch) -> None:
    database = _anchored(monkeypatch, _gen_key_b64())
    # A privileged writer re-chains FAM1's audit trail at its anchored head, and cuts FAM2's
    # sign-outs short: each is named with what the live chain holds at the anchored height.
    database.chains["clinical_audit_events"]["FAM1"][2] = "a3-rechained"
    database.chains["report_signouts"]["FAM2"] = []

    verdict = _latest(database)

    assert verdict.status == "diverged"
    assert [(d["table"], d["family_identifier"], d["issue"], d["live_head_row_hash"]) for d in verdict.diverged] == [
        ("clinical_audit_events", "FAM1", "rechained", "a3-rechained"),
        ("report_signouts", "FAM2", "truncated_or_shrunk", None),
    ]
    # Rows appended after the anchor are no divergence: the anchored height still holds its head.
    database.chains["clinical_audit_events"]["FAM1"][2] = "a3"
    database.chains["report_signouts"]["FAM2"] = ["t1", "t2"]
    assert _latest(database).status == "ok"


def _rehash(anchor: dict) -> None:
    """Re-mint an anchor's root and hash over its (edited) content, as an owner without the key can."""
    anchor["anchor_root"] = hash_chain.canonical_hash(anchor["heads"])
    anchor["anchor_hash"] = hash_chain.chain_row_hash(anchor["prev_anchor_hash"], _signed_core_of(anchor))


def _heads_not_an_array(anchor: dict) -> None:
    anchor["heads"] = None


def _a_head_edited(anchor: dict) -> None:
    anchor["heads"][0]["head_row_hash"] = "a3-rechained"


def _created_at_edited(anchor: dict) -> None:
    anchor["created_at"] = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _a_head_edited_and_re_minted(anchor: dict) -> None:
    _a_head_edited(anchor)
    _rehash(anchor)


def _signature_not_base64(anchor: dict) -> None:
    anchor["signature"] = "not base64!"


def _no_signature(anchor: dict) -> None:
    anchor["signature"] = None


@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        (_heads_not_an_array, "heads is not a JSON array"),
        (_a_head_edited, "anchor_root does not match stored heads"),
        (_created_at_edited, "anchor_hash does not match anchor content"),
        # An owner can recompute the root and the hash, but cannot sign without the key.
        (_a_head_edited_and_re_minted, "Ed25519 signature verification failed"),
        (_signature_not_base64, "Ed25519 signature verification failed"),
        (_no_signature, "Ed25519 signature verification failed"),
    ],
    ids=[
        "heads-not-an-array",
        "head-edited",
        "created-at-edited",
        "re-minted-without-the-key",
        "signature-not-base64",
        "no-signature",
    ],
)
def test_an_anchor_edited_in_the_database_is_refused_never_trusted(monkeypatch, tamper, reason) -> None:
    database = _anchored(monkeypatch, _gen_key_b64())
    tamper(database.anchors[-1])

    verdict = _latest(database)

    assert (verdict.status, verdict.anchor_seq, verdict.reason) == ("signature_invalid", 1, reason)
    # The walk of the whole anchor chain refuses it too: by its structure, or by its signature.
    whole = _whole_chain(database)
    assert whole.status in ("chain_broken", "signature_invalid") and whole.anchor_seq == 1, whole


def test_an_interior_anchor_re_minted_without_the_key_fails_the_chains_signature_check(monkeypatch) -> None:
    database = _anchored(monkeypatch, _gen_key_b64())
    asyncio.run(ias.create_integrity_anchor(database))  # type: ignore[arg-type]
    # Anchor 1 re-minted over an edited head, and anchor 2 re-linked to it: the structure holds,
    # so only the signatures can tell (verify_anchor_chain's signature loop).
    _a_head_edited_and_re_minted(database.anchors[0])
    database.anchors[1]["prev_anchor_hash"] = database.anchors[0]["anchor_hash"]
    _rehash(database.anchors[1])
    assert ias._check_anchor_chain(database.anchors).status == "ok"

    whole = _whole_chain(database)

    assert (whole.status, whole.anchor_seq, whole.reason) == (
        "signature_invalid",
        1,
        "Ed25519 signature verification failed",
    )


@pytest.mark.parametrize("configured", ["another-key", "no-key"])
def test_an_anchor_no_configured_key_matches_is_unknown_key_not_valid(monkeypatch, configured) -> None:
    database = _anchored(monkeypatch, _gen_key_b64())
    monkeypatch.setattr(settings, "integrity_anchor_signing_key", _gen_key_b64() if configured == "another-key" else "")

    latest, whole = _latest(database), _whole_chain(database)

    assert (latest.status, latest.anchor_seq) == ("unknown_key", 1)
    assert (whole.status, whole.anchor_seq) == ("unknown_key", 1)


def test_an_unsigned_anchor_of_a_development_stack_verifies_only_as_unverifiable(monkeypatch) -> None:
    database = _anchored(monkeypatch, "", app_env="development")

    [anchor] = database.anchors
    assert (anchor["algo"], anchor["key_id"], anchor["signature"], anchor["public_key"]) == (
        "unsigned",
        "unsigned",
        None,
        None,
    )
    latest = _latest(database)
    assert (latest.status, latest.diverged) == ("unverifiable_unsigned", [])
    whole = _whole_chain(database)
    assert (whole.status, whole.reason) == ("unverifiable_unsigned", "1 anchor(s) in the chain are unsigned")
    # Unsigned or not, a chain that diverged from its anchor is named.
    database.chains["report_signouts"]["FAM1"] = ["s1"]
    assert _latest(database).status == "diverged"


def test_no_unsigned_anchor_is_written_outside_development(monkeypatch) -> None:
    # An unsigned anchor carries no tamper-evidence against the owner (#522): refused before
    # anything is locked, read or written.
    monkeypatch.setattr(settings, "integrity_anchor_signing_key", "")
    monkeypatch.setattr(settings, "app_env", "production")
    database = _AnchorDatabase(_chains())

    with pytest.raises(RuntimeError, match="Refusing to write an UNSIGNED integrity anchor"):
        asyncio.run(ias.create_integrity_anchor(database))  # type: ignore[arg-type]

    assert (database.statements, database.anchors, database.commits) == ([], [], 0)


def test_a_head_of_a_table_that_is_no_chain_is_named_not_skipped(monkeypatch) -> None:
    database = _anchored(monkeypatch, "", app_env="development")
    anchor = database.anchors[0]
    anchor["heads"].append({"table": "dropped_table", "family_identifier": "FAM1", "height": 1, "head_row_hash": "x"})
    anchor["chain_count"] += 1
    _rehash(anchor)

    verdict = _latest(database)

    assert verdict.status == "diverged"
    assert [(d["table"], d["issue"]) for d in verdict.diverged] == [("dropped_table", "unknown_table")]


def test_a_database_without_an_anchor_says_so() -> None:
    database = _AnchorDatabase(_chains())
    latest = _latest(database)
    assert (latest.status, latest.reason) == ("no_anchor", "no anchor has been created yet")
    assert _whole_chain(database).status == "no_anchor"


def test_an_interior_anchor_deleted_breaks_the_chain_before_any_signature_is_read(monkeypatch) -> None:
    database = _anchored(monkeypatch, _gen_key_b64())
    for _ in range(2):
        asyncio.run(ias.create_integrity_anchor(database))  # type: ignore[arg-type]
    del database.anchors[1]
    monkeypatch.setattr(settings, "integrity_anchor_signing_key", "")  # no key is needed to see it

    whole = _whole_chain(database)

    assert (whole.status, whole.anchor_seq) == ("chain_broken", 3)
    assert "not contiguous" in (whole.reason or "")
