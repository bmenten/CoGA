"""scrub_log control-character neutralization (CWE-117 log forging); describe_error and
describe_traceback, which name an exception and give its frames without quoting the values in
its text; the JSON formatter, which writes every log line that carries an exception that way;
the filter that does the same for uvicorn's traceback of an unhandled error; and the event loop
exception handler, which logs what asyncio reports without the values its reprs quote."""
from __future__ import annotations

import asyncio
import functools
import gc
import io
import json
import logging
import sys
import threading
import time
import traceback
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
import uvloop
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
    install_event_loop_exception_handler,
    install_server_error_redaction,
    log_event_loop_exception,
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


# What an event loop reports to its exception handler. uvicorn runs the app on uvloop
# (uvicorn[standard]); asyncio's own loop runs the scripts and the tests.
_EVENT_LOOPS = [
    pytest.param(asyncio.new_event_loop, id="asyncio"),
    pytest.param(uvloop.new_event_loop, id="uvloop"),
]


async def _fail(value: str) -> None:
    raise ValueError(value)


async def _fail_when_cancelled(value: str) -> None:
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError as cancelled:
        raise ValueError(value) from cancelled


def _fail_now(value: str) -> None:
    raise ValueError(value)


async def _task_nobody_awaits() -> None:
    task = asyncio.get_running_loop().create_task(_fail(_VALUE), name="family import")
    await asyncio.sleep(0)
    assert task.done()
    del task  # the loop reports the exception as the task is destroyed


async def _future_nobody_awaits() -> None:
    future = asyncio.get_running_loop().create_future()
    future.set_exception(ValueError(_VALUE))
    del future


async def _gather_nobody_awaits() -> None:
    gathered = asyncio.gather(_fail(_VALUE))
    await asyncio.sleep(0)  # the task fails
    await asyncio.sleep(0)  # and gather takes its exception
    assert gathered.done()
    del gathered


async def _task_left_at_shutdown() -> asyncio.Task[None]:
    # Returned so that it outlives the run: closing the loop cancels it, and it fails.
    task = asyncio.get_running_loop().create_task(
        _fail_when_cancelled(_VALUE), name="integrity monitor"
    )
    await asyncio.sleep(0)
    return task


async def _failing_callback() -> None:
    asyncio.get_running_loop().call_soon(_fail_now, _VALUE)
    await asyncio.sleep(0)


async def _failing_partial_callback() -> None:
    asyncio.get_running_loop().call_soon(functools.partial(_fail_now, _VALUE))
    await asyncio.sleep(0)


def _report(
    scenario: Callable[[], Coroutine[Any, Any, Any]],
    new_loop: Callable[[], asyncio.AbstractEventLoop],
    *,
    handler: bool = True,
) -> _JsonLog:
    """Run ``scenario`` on a new loop, with the handler the app installs unless told otherwise,
    and give what the ``asyncio`` logger wrote, through the JSON formatter."""
    gc.collect()  # what an earlier test left is reported now, not among this test's lines
    with _json_log("asyncio") as (_logger, log):
        with asyncio.Runner(loop_factory=new_loop) as runner:
            if handler:
                install_event_loop_exception_handler(runner.get_loop())
            kept = runner.run(scenario())
        del kept
        gc.collect()
    return log


@pytest.mark.parametrize("new_loop", _EVENT_LOOPS)
def test_asyncio_writes_the_value_in_its_message_without_the_handler(new_loop) -> None:
    # The finding: asyncio's default handler, and uvloop's, write the task's repr, which holds
    # its exception's message, into the line's message. The formatter cannot tell it apart.
    log = _report(_task_nobody_awaits, new_loop, handler=False)

    (line,) = log.lines()
    assert line["message"].startswith("Task exception was never retrieved\n")
    assert "exception=ValueError('Jane Doe')" in line["message"]


@pytest.mark.parametrize("new_loop", _EVENT_LOOPS)
@pytest.mark.parametrize(
    ("scenario", "message", "detail"),
    [
        (
            _task_nobody_awaits,
            "Task exception was never retrieved",
            {"task": "family import", "coroutine": "_fail"},
        ),
        (_future_nobody_awaits, "Future exception was never retrieved", {"future": "Future"}),
        # asyncio names it after the class of the future: "_GatheringFuture exception ...".
        (_gather_nobody_awaits, "Future exception was never retrieved", {"future": "_GatheringFuture"}),
        (
            _task_left_at_shutdown,
            "unhandled exception during asyncio.run() shutdown",
            {"task": "integrity monitor", "coroutine": "_fail_when_cancelled"},
        ),
    ],
    ids=["task", "future", "gather", "shutdown"],
)
def test_an_exception_nobody_retrieved_is_logged_by_name_kind_and_frames(
    scenario, message: str, detail: dict[str, str], new_loop
) -> None:
    log = _report(scenario, new_loop)

    (line,) = log.lines()
    assert line["severity"] == "ERROR"
    assert line["message"] == message
    assert line["detail"] == detail
    assert line["error"] == "ValueError"
    if scenario is _future_nobody_awaits:
        assert line["traceback"] == "ValueError"  # set, never raised: no frames
    else:
        assert ", in _fail" in line["traceback"]
        assert line["traceback"].endswith("\nValueError")
    assert "Jane Doe" not in log.text


@pytest.mark.parametrize("new_loop", _EVENT_LOOPS)
@pytest.mark.parametrize("scenario", [_failing_callback, _failing_partial_callback], ids=["call", "partial"])
def test_a_failing_callback_is_logged_without_its_arguments(scenario, new_loop) -> None:
    # The message itself quotes the arguments: asyncio's on Python 3.12 ("Exception in callback
    # _fail_now('...')"), uvloop's for a partial ("functools.partial(<function ...>, '...')").
    log = _report(scenario, new_loop)

    (line,) = log.lines()
    assert line["message"] == "Exception in callback"
    # asyncio's handle names its callback; uvloop's does not, and the frames show it.
    if new_loop is asyncio.new_event_loop:
        assert line["detail"] == {"handle": "Handle", "callback": "_fail_now"}
    else:
        assert line["detail"] == {"handle": "Handle"}
    assert line["error"] == "ValueError"
    assert ", in _fail_now\n" in line["traceback"]
    assert "Jane Doe" not in log.text


class _Quoting:
    """Stands in for an object whose repr quotes a value: an aiohttp response its URL with the
    query's parameters, a transport, a connection, a file object its name."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"<_Quoting {self.value}>"


def _call_handler(new_loop: Callable[[], asyncio.AbstractEventLoop], context: dict[str, Any]) -> _JsonLog:
    loop = new_loop()
    try:
        install_event_loop_exception_handler(loop)
        with _json_log("asyncio") as (_logger, log):
            loop.call_exception_handler(context)
    finally:
        loop.close()
    return log


_QUOTED = _Quoting(_VALUE)


@pytest.mark.parametrize("new_loop", _EVENT_LOOPS)
@pytest.mark.parametrize(
    ("message", "written"),
    [
        # Fixed text, written as it is.
        ("Task was destroyed but it is pending!", "Task was destroyed but it is pending!"),
        ("Unclosed response", "Unclosed response"),  # aiohttp's
        (
            "SSL handshake failed on verifying the certificate",
            "SSL handshake failed on verifying the certificate",
        ),
        # Fixed text that goes on with what it quotes: written up to it.
        (
            "Fatal error on transport TCPTransport (error status in uv_stream_t.read callback)",
            "Fatal error on transport",
        ),
        (f"could not close attached file object {_QUOTED!r}", "could not close attached file object"),
        (
            f"Resetting connection with an active transaction {_QUOTED!r}",
            "Resetting connection with an active transaction",
        ),
        (
            f"an error occurred during closing of asynchronous generator {_QUOTED!r}",
            "an error occurred during closing of asynchronous generator",
        ),
        (
            f"Task {_QUOTED!r} has errored out but its parent task {_QUOTED} is already completed",
            "Task has errored out but its parent task is already completed",
        ),
        # A message it does not know is not written; nor is there one to write.
        (f"Listener for {_VALUE} failed", "Event loop error (message not written)"),
        ("", "Unhandled exception in event loop"),
    ],
    ids=[
        "destroyed-pending",
        "unclosed-response",
        "ssl-certificate",
        "uvloop-transport",
        "file-object",
        "asyncpg-transaction",
        "asyncgen",
        "task-group",
        "unknown",
        "none",
    ],
)
def test_the_message_is_written_as_fixed_text_up_to_what_it_quotes(
    message: str, written: str, new_loop
) -> None:
    log = _call_handler(
        new_loop,
        {"message": message, "exception": ValueError(_VALUE), "transport": _QUOTED, "protocol": _QUOTED},
    )

    (line,) = log.lines()
    assert line["message"] == written
    assert line["detail"] == {"transport": "_Quoting", "protocol": "_Quoting"}
    assert line["error"] == "ValueError"
    assert "Jane Doe" not in log.text


async def _rows(value: str) -> AsyncIterator[str]:
    yield value


def _created_here(value: str) -> traceback.StackSummary:
    # Where, in debug mode, asyncio says an object was created; this frame kept its locals.
    frames = traceback.walk_stack(sys._getframe())
    return traceback.StackSummary.extract(frames, limit=1, capture_locals=True)


@pytest.mark.parametrize("new_loop", _EVENT_LOOPS)
def test_the_objects_a_report_holds_are_named_never_quoted(new_loop) -> None:
    async def finished_task() -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(_fail(_VALUE), name="sample QC")
        await asyncio.sleep(0)
        assert isinstance(task.exception(), ValueError)
        return task

    with asyncio.Runner(loop_factory=new_loop) as runner:
        task = runner.run(finished_task())
    rows = _rows(_VALUE)
    created = _created_here(_VALUE)
    with asyncio.Runner() as runner:
        handle = asyncio.Handle(functools.partial(_fail_now, _VALUE), (), runner.get_loop())
    context = {
        "message": "Unhandled exception in client_connected_cb",
        "exception": task.exception(),
        "task": task,
        "asyncgen": rows,
        "source_traceback": created,
        "handle": handle,
        "client_response": _QUOTED,
    }
    assert "Jane Doe" in repr(task) and "Jane Doe" in "".join(created.format())

    log = _call_handler(new_loop, context)

    (line,) = log.lines()
    assert line["message"] == "Unhandled exception in client_connected_cb"
    assert line["detail"]["task"] == "sample QC"
    assert line["detail"]["coroutine"] == "_fail"
    assert line["detail"]["asyncgen"] == "_rows"
    assert line["detail"]["source_traceback"].startswith('  File "')
    assert line["detail"]["source_traceback"].endswith(
        ", in _created_here\n    return traceback.StackSummary.extract(frames, limit=1, capture_locals=True)"
    )
    assert line["detail"]["handle"] == "Handle"
    assert line["detail"]["callback"] == "_fail_now"
    assert line["detail"]["client_response"] == "_Quoting"
    assert "Jane Doe" not in log.text


@pytest.mark.parametrize("new_loop", _EVENT_LOOPS)
def test_the_loop_never_falls_back_on_its_own_handler(new_loop, monkeypatch, capsys) -> None:
    # A handler that raises makes the loop log "Unhandled error in exception handler" with the
    # whole context, reprs and all.
    def _cannot(_context: dict[str, Any]) -> dict[str, str]:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(coga_logging, "_event_loop_detail", _cannot)
    log = _report(_task_nobody_awaits, new_loop)

    (line,) = log.lines()
    assert line["message"] == "Event loop error not described (RecursionError)"
    assert line["error"] == "ValueError"
    assert "detail" not in line
    assert "Jane Doe" not in log.text
    assert "Jane Doe" not in capsys.readouterr().err


@pytest.mark.parametrize("new_loop", _EVENT_LOOPS)
def test_a_report_that_cannot_be_logged_leaves_a_line_without_values(new_loop, capsys) -> None:
    class _Refusing(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            raise RuntimeError(record.getMessage())

    refusing = _Refusing()
    logging.getLogger("asyncio").addFilter(refusing)
    try:
        log = _report(_task_nobody_awaits, new_loop)
    finally:
        logging.getLogger("asyncio").removeFilter(refusing)

    assert log.lines() == []
    assert capsys.readouterr().err == "Event loop error not logged (RuntimeError)\n"


@pytest.mark.parametrize("new_loop", _EVENT_LOOPS)
def test_the_lifespan_installs_the_handler_on_the_loop_it_runs_on(new_loop) -> None:
    from backend.app.main import lifespan

    app = SimpleNamespace(state=SimpleNamespace(skip_startup_tasks=True))

    async def handler_while_serving() -> object:
        async with lifespan(app):
            return asyncio.get_running_loop().get_exception_handler()

    with asyncio.Runner(loop_factory=new_loop) as runner:
        assert runner.run(handler_while_serving()) is log_event_loop_exception
        # Left in place: tasks are still reported as the loop is closed.
        assert runner.get_loop().get_exception_handler() is log_event_loop_exception


def test_a_background_tasks_exception_on_uvicorns_own_loop_is_logged_without_its_value() -> None:
    # The app's lifespan, on the uvloop event loop uvicorn creates: a task a request started and
    # nobody awaits, as a knowledgebase rebuild is started, fails.
    import httpx
    import uvicorn
    from fastapi import FastAPI

    from backend.app.main import lifespan

    app = FastAPI(lifespan=lifespan)
    app.state.skip_startup_tasks = True  # no database: only what the lifespan does first

    @app.post("/rebuild")
    async def rebuild() -> dict[str, str]:
        asyncio.get_running_loop().create_task(_fail(_VALUE), name="knowledgebase rebuild")
        return {}

    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=0, loop="uvloop", lifespan="on", log_config=None, access_log=False
        )
    )
    gc.collect()
    with _json_log("asyncio") as (_logger, log):
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 20
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline
                time.sleep(0.01)
            port = server.servers[0].sockets[0].getsockname()[1]
            assert httpx.post(f"http://127.0.0.1:{port}/rebuild").status_code == 200
            while not log.text:
                assert time.monotonic() < deadline
                time.sleep(0.01)
        finally:
            server.should_exit = True
            thread.join(timeout=20)
    assert not thread.is_alive()

    (line,) = log.lines()
    assert line["message"] == "Task exception was never retrieved"
    assert line["detail"] == {"task": "knowledgebase rebuild", "coroutine": "_fail"}
    assert line["error"] == "ValueError"
    assert ", in _fail\n" in line["traceback"]
    assert "Jane Doe" not in log.text
