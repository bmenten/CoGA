"""scrub_log control-character neutralization (CWE-117 log forging), and describe_error,
which names an exception without quoting the values in its text."""
from __future__ import annotations

from sqlalchemy.exc import DBAPIError

from backend.app.core.coga_logging import describe_error, scrub_log


def test_scrub_log_neutralizes_crlf_forging() -> None:
    forged = "family-123\r\nERROR forged admin login by attacker"
    out = scrub_log(forged)
    assert "\n" not in out and "\r" not in out
    # content is preserved; only the line breaks that would forge a new record are gone
    assert out == "family-123  ERROR forged admin login by attacker"


def test_scrub_log_passes_clean_value_through() -> None:
    assert scrub_log("FAM0001") == "FAM0001"


def test_scrub_log_coerces_non_str() -> None:
    assert scrub_log(42) == "42"


def test_scrub_log_strips_tab_and_del() -> None:
    assert scrub_log("a\tb\x7fc") == "a b c"


def test_scrub_log_truncates_oversized_value() -> None:
    out = scrub_log("x" * 1000, max_len=100)
    assert len(out) == 100
    assert out.endswith("...")


class _AsyncpgError(Exception):
    """Stands in for an asyncpg error, e.g. UntranslatableCharacterError."""


class _AdaptedError(Exception):
    """Stands in for SQLAlchemy's asyncpg adapter error: raised from asyncpg's, with its SQLSTATE."""

    sqlstate = "22P05"


def _failed_insert() -> DBAPIError:
    orig = _AdaptedError('unsupported Unicode escape sequence, JSON data: {"note": "Jane Doe')
    orig.__cause__ = _AsyncpgError("unsupported Unicode escape sequence")
    return DBAPIError("INSERT INTO audit_log_events ...", {"request_body": "Jane Doe"}, orig)


def test_describe_error_names_a_database_error_without_its_text() -> None:
    error = _failed_insert()
    assert "Jane Doe" in str(error)  # SQLAlchemy's text quotes the parameters
    assert describe_error(error) == "DBAPIError (_AsyncpgError, SQLSTATE 22P05)"


def test_describe_error_names_the_adapter_error_when_nothing_caused_it() -> None:
    error = DBAPIError("SELECT 1", None, _AdaptedError("Jane Doe"))
    assert describe_error(error) == "DBAPIError (_AdaptedError, SQLSTATE 22P05)"


def test_describe_error_leaves_out_a_missing_sqlstate() -> None:
    error = DBAPIError("SELECT 1", None, ConnectionRefusedError("Jane Doe"))
    assert describe_error(error) == "DBAPIError (ConnectionRefusedError)"


def test_describe_error_gives_only_the_type_of_any_other_error() -> None:
    assert describe_error(ValueError("bad family id Jane Doe")) == "ValueError"

    class _NotADatabaseError(Exception):
        orig = "Jane Doe"

    assert describe_error(_NotADatabaseError()) == "_NotADatabaseError"
