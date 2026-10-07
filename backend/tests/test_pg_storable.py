"""The values Postgres refuses are escaped, visibly, and the escaped columns are named.

Postgres stores no NUL in TEXT and no ``\\u0000``, lone surrogate, NaN or Infinity in JSONB; one
such value used to fail the INSERT of a request's audit row. The real refusals are checked on
Postgres in backend/tests/e2e/test_e2e_request_audit_unstorable_characters.py.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from app.core.pg_storable import storable_columns


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _refuse_constant(token: str) -> None:
    pytest.fail(f"JSONB refuses the token {token}")


def test_a_nul_is_written_as_its_escape_and_the_column_named() -> None:
    stored, escaped = storable_columns({"path": "/api/families/FAM\x00X", "method": "GET"})
    assert stored == {"path": "/api/families/FAM\\x00X", "method": "GET"}
    assert escaped == ["path"]


def test_half_a_surrogate_pair_is_written_as_its_escape() -> None:
    stored, escaped = storable_columns({"high": "a\ud800b", "low": "\udfff"})
    assert stored == {"high": "a\\ud800b", "low": "\\udfff"}
    assert escaped == ["high", "low"]
    for value in stored.values():
        value.encode("utf-8")  # storable as UTF-8 TEXT now


def test_non_finite_numbers_become_the_strings_json_needs() -> None:
    stored, escaped = storable_columns(
        {"body": {"ratio": float("nan"), "high": float("inf"), "low": float("-inf"), "ok": 1.5}}
    )
    assert stored["body"] == {"ratio": "NaN", "high": "Infinity", "low": "-Infinity", "ok": 1.5}
    assert escaped == ["body"]


def test_nested_values_and_keys_are_escaped() -> None:
    body = {"events": [{"label": "Save\x00", "detail": {"k\x00ey": ["a", "\x00"]}}], "n": 3}
    stored, escaped = storable_columns({"request_body": body})
    assert stored["request_body"] == {
        "events": [{"label": "Save\\x00", "detail": {"k\\x00ey": ["a", "\\x00"]}}],
        "n": 3,
    }
    assert escaped == ["request_body"]
    # The caller's value is left as it was.
    assert body["events"][0]["label"] == "Save\x00"


def test_everything_postgres_stores_is_left_as_it_came() -> None:
    columns = {
        "text": "tab\t, newline\n, other controls \x01\x1f\x7f, U+FFFD \ufffd, U+FFFE \ufffe",
        "pair": "a whole pair \U0001f600",
        "json": {"none": None, "flag": True, "int": 10**40, "float": 5e-324, "list": [1, "x"]},
        "number": 200,
        "missing": None,
    }
    stored, escaped = storable_columns(columns)
    assert stored == columns
    assert escaped == []


def test_the_text_a_request_sent_itself_reads_the_same() -> None:
    # The escape is not unique: a request can send the characters \x00 itself, and then
    # nothing is escaped (the row says so: the column is not named).
    stored, escaped = storable_columns({"path": "/api/a\\x00"})
    assert stored == {"path": "/api/a\\x00"}
    assert escaped == []


def test_a_tuple_is_stored_as_a_list() -> None:
    stored, escaped = storable_columns({"fields": ("a", "b\x00")})
    assert stored == {"fields": ["a", "b\\x00"]}
    assert escaped == ["fields"]


def test_nothing_refused_is_left_in_the_serialised_row() -> None:
    nasty = {
        "path": "/a\x00/\ud800",
        "request_body": {
            "a\x00": "\x00\udc00\x00",
            "n": [float("nan"), float("inf"), float("-inf")],
            "pair": "\U0001f600",
        },
    }
    stored, escaped = storable_columns(nasty)
    assert escaped == ["path", "request_body"]
    # Read back the JSON text the writer sends, the way JSONB reads it: no NaN or Infinity
    # token, and no string with a NUL or a surrogate (a lone half fails to encode).
    serialised = json.dumps(stored["request_body"], ensure_ascii=True)
    document = json.loads(serialised, parse_constant=_refuse_constant)
    for text in [stored["path"], *_strings(document)]:
        assert "\x00" not in text
        text.encode("utf-8")
    # A whole pair is kept: JSON writes it as two escapes, and JSONB joins them.
    assert document["pair"] == "\U0001f600"
