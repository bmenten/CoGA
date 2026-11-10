"""Tamper-evidence hash-chain primitives: pure chain math.

Pins the pure ``hash_chain`` primitives — canonical determinism, genesis anchoring,
and that ``verify_chain`` flags content tampering, deletion, reordering and a deleted
head, each at the first row it broke. The end-to-end chain integrity against real Postgres
(the real writers + privileged trigger-bypass tampering) lives in
``backend/tests/integration/test_hash_chain_integration.py`` (CI smoke job).
"""

from __future__ import annotations

import pytest

from backend.app.services.hash_chain import (
    GENESIS,
    canonical_json,
    chain_row_hash,
    jsonb_float,
    verify_chain,
)


def test_a_snapshot_float_is_one_jsonb_holds_and_hashes_alike_after_its_round_trip() -> None:
    # The small-variant and the CNV evidence snapshots kept a copy each.
    assert jsonb_float(0.25) == 0.25
    assert jsonb_float(None) is None
    for not_stored in (float("nan"), float("inf"), float("-inf")):
        assert jsonb_float(not_stored) is None
    zero = jsonb_float(-0.0)
    assert zero == 0.0 and canonical_json(zero) == "0.0"


def test_canonical_json_is_order_independent_and_value_sensitive() -> None:
    assert canonical_json({"b": 1, "a": [2, 1]}) == canonical_json({"a": [2, 1], "b": 1})
    assert canonical_json({"a": 1}) != canonical_json({"a": 2})


def test_chain_row_hash_is_genesis_anchored_and_prev_sensitive() -> None:
    first = chain_row_hash(None, {"v": 1})
    # A missing prev and the explicit GENESIS sentinel anchor identically.
    assert first == chain_row_hash(GENESIS, {"v": 1})
    assert len(first) == 64  # sha256 hex
    # The same payload under a different predecessor yields a different hash.
    assert chain_row_hash(first, {"v": 2}) != chain_row_hash("other-prev", {"v": 2})


def test_canonical_json_normalizes_only_large_integral_floats() -> None:
    # >= 2**53: Postgres JSONB renders a large integral float as an integer numeric, so
    # canonical_json coerces both float and int to the SAME token — a row written with a
    # float re-hashes identically after the JSONB round-trip returns an int (no false alarm).
    assert canonical_json({"x": 1e16}) == canonical_json({"x": 10 ** 16})
    assert canonical_json(1.75e18) == canonical_json(1_750_000_000_000_000_000)
    # < 2**53: left untouched (already round-trips), so existing content hashes are unchanged.
    assert canonical_json(5.0) == "5.0"
    assert canonical_json(5) == "5"
    assert canonical_json(0.9876) == "0.9876"


def _payload_of(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in ("id", "row_hash", "prev_hash")}


def _build_chain(payloads: list[dict]) -> list[dict]:
    rows, prev = [], None
    for index, payload in enumerate(payloads):
        row_hash = chain_row_hash(prev, payload)
        rows.append({"id": str(index), "row_hash": row_hash, "prev_hash": prev, **payload})
        prev = row_hash
    return rows


def test_verify_chain_accepts_a_valid_chain_and_the_empty_chain() -> None:
    rows = _build_chain([{"x": 1}, {"x": 2}, {"x": 3}])
    result = verify_chain(rows, _payload_of)
    assert result.verified and result.rows_checked == 3 and result.first_bad_row is None
    # Empty chain is verified=True, rows_checked=0 (pinned: an auditor must cross-check
    # rows_checked against an expected/anchored count to detect whole-chain truncation —
    # the deferred external-anchor gap, see hash_chain module docstring).
    empty = verify_chain([], _payload_of)
    assert empty.verified and empty.rows_checked == 0


def _edit_the_middle_rows_content(rows: list[dict]) -> None:
    rows[1]["x"] = 99  # edit content without recomputing its hash


def _delete_the_middle_row(rows: list[dict]) -> None:
    del rows[1]  # the successor's prev_hash now dangles


def _swap_the_last_two_rows(rows: list[dict]) -> None:
    rows[1], rows[2] = rows[2], rows[1]


def _delete_the_first_row(rows: list[dict]) -> None:
    del rows[0]  # the chain now starts at a row that links to a predecessor, not to genesis


_UNLINKED = "prev_hash does not match the predecessor row_hash (row deleted or reordered)"


@pytest.mark.parametrize(
    ("tamper", "rows_checked", "first_bad_row", "reason"),
    [
        (_edit_the_middle_rows_content, 1, "1", "row_hash mismatch (row content tampered)"),
        (_delete_the_middle_row, 1, "2", _UNLINKED),
        (_swap_the_last_two_rows, 1, "2", _UNLINKED),
        (_delete_the_first_row, 0, "1", "first chained row is not a genesis link"),
    ],
    ids=["content-tampered", "row-deleted", "rows-reordered", "head-deleted"],
)
def test_verify_chain_names_the_first_row_a_tampering_broke(tamper, rows_checked, first_bad_row, reason) -> None:
    # Each tampering is found at the first row it broke, with the rows before it counted as
    # intact and the reason it was refused: an edit by its recomputed hash, a deletion or a
    # reordering by the link it left dangling, and a deleted head by its successor, which now
    # opens the chain without a genesis link.
    rows = _build_chain([{"x": 1}, {"x": 2}, {"x": 3}])
    tamper(rows)
    result = verify_chain(rows, _payload_of)
    assert (result.verified, result.rows_checked, result.first_bad_row, result.reason) == (
        False,
        rows_checked,
        first_bad_row,
        reason,
    )
