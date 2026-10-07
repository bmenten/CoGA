"""scrub_log control-character neutralization (CWE-117 log forging); describe_error and
describe_traceback, which name an exception and give its frames without quoting the values in
its text; the JSON formatter, which writes every log line that carries an exception that way;
and the filter that does the same for uvicorn's traceback of an unhandled error."""
from __future__ import annotations

import io
import json
import logging
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError as ClickHouseDatabaseError
from clickhouse_connect.driver.exceptions import OperationalError as ClickHouseOperationalError
from sqlalchemy.exc import DBAPIError
from uvicorn.logging import DefaultFormatter

from backend.app.core import coga_logging
from backend.app.core.coga_logging import (
    JsonLogFormatter,
    RedactServerErrorFilter,
    describe_error,
    describe_traceback,
    install_server_error_redaction,
    scrub_log,
)


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


def test_describe_error_names_a_clickhouse_error_by_its_code_and_name() -> None:
    # The server's text quotes the value it could not convert, a filter value from the request.
    error = ClickHouseDatabaseError(
        "Received ClickHouse exception, code: 6, server response: Code: 6. DB::Exception: "
        "Cannot parse string 'Jane Doe' as UInt32. (CANNOT_PARSE_TEXT)",
        code=6,
        name="CANNOT_PARSE_TEXT",
    )
    assert describe_error(error) == "DatabaseError (ClickHouse 6 CANNOT_PARSE_TEXT)"
    # Without the server's detail only the code is known; a name that is not one is left out.
    assert describe_error(ClickHouseDatabaseError("Jane Doe", code=241)) == "DatabaseError (ClickHouse 241)"
    assert describe_error(ClickHouseDatabaseError("x", code=6, name="Jane Doe")) == "DatabaseError (ClickHouse 6)"
    # A transport error has neither; its text can hold the URL, with the bound parameters.
    transport = ClickHouseOperationalError("Network Error: url='http://ch:8123/?param_note=Jane+Doe'")
    assert describe_error(transport) == "OperationalError"


def test_describe_error_reads_a_code_and_name_only_from_the_clickhouse_driver() -> None:
    class _ErrorWithCode(Exception):
        code = 6
        name = "CANNOT_PARSE_TEXT"

    assert describe_error(_ErrorWithCode("Jane Doe")) == "_ErrorWithCode"
    assert describe_error(SystemExit(2)) == "SystemExit"


# The value a request carried. Never written in a raising line: a frame's source line is
# kept, and would quote it as code.
_VALUE = "Jane Doe"


def _lookup(family_id: str) -> None:
    raise LookupError(f"no family {family_id}")


def _query(family_id: str) -> None:
    try:
        _lookup(family_id)
    except LookupError as exc:
        raise _failed_insert() from exc


def _chained_failure() -> RuntimeError:
    try:
        try:
            _query(_VALUE)
        except DBAPIError:
            raise RuntimeError(f"while handling {_VALUE}")  # noqa: B904 - the implicit chain is the case
    except RuntimeError as exc:
        return exc
    raise AssertionError("not raised")


def test_describe_traceback_keeps_the_frames_and_kinds_of_the_chain_but_no_message() -> None:
    failure = _chained_failure()
    assert "Jane Doe" in "".join(traceback.format_exception(failure))  # Python's own quotes it

    text = describe_traceback(failure)

    assert "Jane Doe" not in text
    assert "[parameters:" not in text
    # Oldest first, as Python prints a chain: the lookup, the failed statement it caused, and
    # the error raised while handling that.
    kinds = [
        line
        for line in text.splitlines()
        if line and not line.startswith((" ", "Traceback", "The above", "During"))
    ]
    assert kinds == ["LookupError", "DBAPIError (_AsyncpgError, SQLSTATE 22P05)", "RuntimeError"]
    assert "The above exception was the direct cause of the following exception:" in text
    assert "During handling of the above exception, another exception occurred:" in text
    # The frames are kept, source lines included: they are code, not values.
    assert ", in _lookup\n" in text
    assert 'raise LookupError(f"no family {family_id}")' in text


def test_describe_traceback_follows_python_on_a_suppressed_context_and_a_cycle() -> None:
    try:
        try:
            raise KeyError("Jane Doe")
        except KeyError:
            raise ValueError("Jane Doe") from None
    except ValueError as exc:
        suppressed = exc
    assert describe_traceback(suppressed).splitlines()[-1] == "ValueError"
    assert "KeyError" not in describe_traceback(suppressed)

    first, second = ValueError("Jane Doe"), KeyError("Jane Doe")
    first.__context__, second.__context__ = second, first
    assert describe_traceback(first) == (
        "KeyError\n\nDuring handling of the above exception, another exception occurred:\n\nValueError"
    )


def test_describe_traceback_lists_the_members_of_an_exception_group() -> None:
    try:
        raise ExceptionGroup(_VALUE, [ValueError(_VALUE), _failed_insert()])
    except ExceptionGroup as exc:
        text = describe_traceback(exc)

    assert "Jane Doe" not in text
    assert (
        "ExceptionGroup\nSub-exception 1 of 2:\n    ValueError\n"
        "Sub-exception 2 of 2:\n    DBAPIError (_AsyncpgError, SQLSTATE 22P05)"
    ) in text


def _member_raised_from_its_group() -> ValueError:
    # How a task group's one error is unwrapped: the member, raised while its group is handled,
    # gets that group, which holds it, as its context.
    try:
        try:
            raise ExceptionGroup(_VALUE, [ValueError(_VALUE)])
        except ExceptionGroup as group:
            raise group.exceptions[0]  # noqa: B904 - the implicit context is the case
    except ValueError as exc:
        return exc
    raise AssertionError("not raised")


def test_describe_traceback_ends_where_python_does_on_a_member_raised_from_its_group() -> None:
    member = _member_raised_from_its_group()
    group = member.__context__
    assert isinstance(group, ExceptionGroup) and member in group.exceptions
    assert "Jane Doe" in "".join(traceback.format_exception(member))  # Python's own ends

    text = describe_traceback(member)

    assert "Jane Doe" not in text
    # The group, with the member in it, then the member raised while the group was handled.
    kinds = [line.strip() for line in text.splitlines()]
    kinds = [kind for kind in kinds if kind in {"ExceptionGroup", "ValueError"}]
    assert kinds == ["ExceptionGroup", "ValueError", "ValueError"]
    assert "\nExceptionGroup\nSub-exception 1 of 1:\n" in text
    assert text.count("During handling of the above exception, another exception occurred:") == 1
    assert text.endswith("\nValueError")


class _JsonLog(logging.StreamHandler):
    """The handler configure_json_logging puts on the root logger, writing to a buffer."""

    def __init__(self) -> None:
        super().__init__(io.StringIO())
        self.setFormatter(JsonLogFormatter())
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        super().emit(record)

    @property
    def text(self) -> str:
        return self.stream.getvalue()

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.text.splitlines()]


@contextmanager
def _json_log(name: str) -> Iterator[tuple[logging.Logger, _JsonLog]]:
    logger = logging.getLogger(name)
    handler = _JsonLog()
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield logger, handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def test_a_logged_exception_is_named_with_its_frames_and_without_its_message() -> None:
    # logger.exception() used to write the message alone: the formatter dropped exc_info.
    with _json_log("app.services.report_signout_service") as (logger, log):
        try:
            _query(_VALUE)
        except DBAPIError:
            logger.exception("Could not resolve QC thresholds for the report snapshot")

    (line,) = log.lines()
    assert list(line) == ["timestamp", "severity", "message", "error", "traceback"]
    assert line["severity"] == "ERROR"
    assert line["message"] == "Could not resolve QC thresholds for the report snapshot"
    assert line["error"] == "DBAPIError (_AsyncpgError, SQLSTATE 22P05)"
    # The frames and kinds of the whole chain: the lookup, and the statement it caused to fail.
    assert line["traceback"].startswith("Traceback (most recent call last):\n")
    assert ", in _lookup\n" in line["traceback"]
    assert "\nLookupError\n" in line["traceback"]
    assert "The above exception was the direct cause of the following exception:" in line["traceback"]
    assert line["traceback"].endswith("\nDBAPIError (_AsyncpgError, SQLSTATE 22P05)")
    assert "Jane Doe" not in log.text
    # Python's formatter writes the messages, the statement's parameters among them, and caches
    # them on the record (exc_text) for the next handler; the JSON line does not read that.
    record = log.records[0]
    assert "Jane Doe" in logging.Formatter().format(record)
    assert "Jane Doe" not in JsonLogFormatter().format(record)


@pytest.mark.parametrize(
    ("logger_name", "level", "exc_info"),
    [
        # A best-effort failure CoGA logs at WARNING, with exc_info=True.
        ("app.services.annotation_manifest_service", logging.WARNING, "true"),
        # A library's own logging: the exception itself, or the tuple asyncio passes.
        ("sqlalchemy.pool", logging.ERROR, "exception"),
        ("asyncio", logging.CRITICAL, "tuple"),
    ],
)
def test_every_logger_names_the_exception_its_line_carries_at_every_level(
    logger_name: str, level: int, exc_info: str
) -> None:
    with _json_log(logger_name) as (logger, log):
        try:
            _query(_VALUE)
        except DBAPIError as exc:
            given: Any = {"true": True, "exception": exc, "tuple": (type(exc), exc, exc.__traceback__)}
            logger.log(level, "Gene-locus provenance lookup failed", exc_info=given[exc_info])

    (line,) = log.lines()
    assert line["severity"] == logging.getLevelName(level)
    assert line["error"] == "DBAPIError (_AsyncpgError, SQLSTATE 22P05)"
    assert ", in _lookup\n" in line["traceback"]
    assert line["traceback"].endswith("\nDBAPIError (_AsyncpgError, SQLSTATE 22P05)")
    assert "Jane Doe" not in log.text


def test_a_traceback_the_call_passes_is_written_as_given() -> None:
    # The request middleware writes its 500 line's traceback itself.
    with _json_log("app.middleware.request_logging") as (logger, log):
        try:
            _query(_VALUE)
        except DBAPIError:
            logger.error("Unhandled server error", exc_info=True, extra={"traceback": "given"})

    (line,) = log.lines()
    assert line["traceback"] == "given"
    assert "error" not in line
    assert "Jane Doe" not in log.text


def test_a_line_without_an_exception_has_no_error_and_no_traceback() -> None:
    with _json_log("app.services.metadata_service") as (logger, log):
        logger.warning("Sequencing-QC threshold evaluation skipped")
        logger.error("Nothing is being handled", exc_info=True)  # exc_info is (None, None, None)

    assert [sorted(line) for line in log.lines()] == [["message", "severity", "timestamp"]] * 2


def test_a_logged_member_raised_from_its_group_is_written_whole() -> None:
    member = _member_raised_from_its_group()
    with _json_log("app.services.family_package_import") as (logger, log):
        logger.error("Family package import job failed", exc_info=member)

    (line,) = log.lines()
    assert line["error"] == "ValueError"
    assert line["traceback"] == describe_traceback(member)
    assert "Jane Doe" not in log.text


def test_uvicorns_traceback_of_an_unhandled_error_keeps_frames_and_kinds_only() -> None:
    # What uvicorn logs for an exception that escaped a request, Starlette having answered it
    # with a 500: "Exception in ASGI application" and the exception, in its default format.
    try:
        _query(_VALUE)
    except DBAPIError as exc:
        failure = exc
    record = logging.LogRecord(
        "uvicorn.error", logging.ERROR, __file__, 1, "Exception in ASGI application\n", None,
        (type(failure), failure, failure.__traceback__),
    )
    formatter = DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False)
    assert "Jane Doe" in formatter.format(logging.makeLogRecord(record.__dict__))  # unfiltered

    assert RedactServerErrorFilter().filter(record) is True
    line = formatter.format(record)

    assert line.startswith("ERROR:    Exception in ASGI application\nTraceback (most recent call last):\n")
    assert ", in _lookup\n" in line
    assert line.endswith("\nDBAPIError (_AsyncpgError, SQLSTATE 22P05)")
    assert "Jane Doe" not in line


def test_the_app_installs_the_filter_on_uvicorns_error_logger_once() -> None:
    import backend.app.main  # noqa: F401 - installs the log filters as it is imported

    install_server_error_redaction()
    filters = logging.getLogger("uvicorn.error").filters
    assert sum(isinstance(existing, RedactServerErrorFilter) for existing in filters) == 1


def test_a_line_is_written_and_nothing_raised_when_its_traceback_cannot_be(monkeypatch, capsys) -> None:
    # logging prints a formatter's error to stderr with the logged exception, message and all,
    # as its context; a RecursionError, and any error of a logger's filter, escape the log call.
    def _cannot(_exc: BaseException) -> str:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(coga_logging, "describe_traceback", _cannot)
    with _json_log("app.services.family_package_import") as (logger, log):
        try:
            _query(_VALUE)
        except DBAPIError:
            logger.exception("Family package import job failed")

    (line,) = log.lines()
    assert line["message"] == "Family package import job failed"
    assert line["error"] == "DBAPIError (_AsyncpgError, SQLSTATE 22P05)"
    assert line["traceback"] == (
        "Traceback not written (RecursionError)\nDBAPIError (_AsyncpgError, SQLSTATE 22P05)"
    )
    # uvicorn's filter writes the same, and lets the record through.
    record = log.records[0]
    assert RedactServerErrorFilter().filter(record) is True
    assert record.exc_info is None
    assert record.exc_text == line["traceback"]
    assert "Jane Doe" not in capsys.readouterr().err
