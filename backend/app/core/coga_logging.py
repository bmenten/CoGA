from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import json
import logging
import re
import sys
import textwrap
import traceback
from datetime import datetime, timezone
from typing import Any

# C0 control characters + DEL. CR/LF here are what let an attacker forge extra log
# lines (CWE-117); tab/other control chars can corrupt log parsing too.
_LOG_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# A ClickHouse error name, as clickhouse-connect reads it from the server's reply.
_CLICKHOUSE_ERROR_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")

_CAUSE_LINK = "\nThe above exception was the direct cause of the following exception:\n\n"
_CONTEXT_LINK = "\nDuring handling of the above exception, another exception occurred:\n\n"


def scrub_log(value: Any, *, max_len: int = 512) -> str:
    """Neutralize control characters before a value is interpolated into a log message,
    defeating log forging. Returns a plain ``str``; oversized values are truncated.

    The explicit ``replace("\\n", ...)`` changes nothing the regex would not, but it is the
    form CodeQL's py/log-injection query recognizes as a sanitizer; the regex alone is not,
    so without it every call site stays flagged."""
    scrubbed = _LOG_CONTROL_RE.sub(" ", str(value).replace("\n", " "))
    return scrubbed if len(scrubbed) <= max_len else scrubbed[: max_len - 3] + "..."


def describe_error(exc: BaseException) -> str:
    """Name an exception for a log line without its message, e.g. ``DBAPIError
    (UntranslatableCharacterError, SQLSTATE 22P05)`` or ``DatabaseError (ClickHouse 241
    MEMORY_LIMIT_EXCEEDED)``: its type and, for a database error, the driver's error type and
    SQLSTATE, or ClickHouse's error code and name.

    A failed statement's message quotes the values it was given (SQLAlchemy appends the
    parameters, Postgres quotes the JSON it could not read, ClickHouse a value it could not
    convert). Those are the request's values: its user, path and body for the audit writers,
    whatever a handler bound for a request that fails. The log must not copy them."""
    name = type(exc).__name__
    orig = getattr(exc, "orig", None)
    if isinstance(orig, BaseException):
        # SQLAlchemy's asyncpg adapter raises its own error from asyncpg's, which names the kind.
        driver_error = orig.__cause__ if orig.__cause__ is not None else orig
        detail = type(driver_error).__name__
        sqlstate = getattr(orig, "sqlstate", None)
        if isinstance(sqlstate, str) and sqlstate:
            detail += f", SQLSTATE {scrub_log(sqlstate, max_len=16)}"
        return f"{name} ({detail})"
    if type(exc).__module__.startswith("clickhouse_connect."):
        # The driver's errors carry the server's error code and name; a transport error has
        # neither. Matched by module, so that this module need not import the driver.
        code = getattr(exc, "code", None)
        error_name = getattr(exc, "name", None)
        parts = [str(code)] if isinstance(code, int) and not isinstance(code, bool) else []
        if isinstance(error_name, str) and _CLICKHOUSE_ERROR_NAME_RE.fullmatch(error_name):
            parts.append(error_name)
        if parts:
            return f"{name} (ClickHouse {' '.join(parts)})"
    return name


def describe_traceback(exc: BaseException) -> str:
    """A traceback of ``exc`` for a log line without any exception's message: each exception
    of its chain, oldest first as Python prints them, with its frames (file, line, function and
    source line) and its :func:`describe_error` name.

    The frames are code. The messages, and any notes, are where a failed statement's values
    are, and are left out."""
    return _describe_traceback(exc, set())


def _describe_traceback(exc: BaseException, seen: set[int]) -> str:
    # ``seen`` is shared with the members of exception groups, as in Python's own traceback: an
    # exception raised while its group was handled (``raise group.exceptions[0]``) has that
    # group, which holds it, as its context, and would otherwise be written without end.
    seen.add(id(exc))
    chain = [exc]  # newest first
    links: list[str] = []  # links[i] stands between chain[i + 1] and chain[i]
    while True:
        newest = chain[-1]
        if newest.__cause__ is not None:
            earlier, link = newest.__cause__, _CAUSE_LINK
        elif newest.__context__ is not None and not newest.__suppress_context__:
            earlier, link = newest.__context__, _CONTEXT_LINK
        else:
            break
        if id(earlier) in seen:
            break
        seen.add(id(earlier))
        chain.append(earlier)
        links.append(link)

    parts: list[str] = []
    for index in range(len(chain) - 1, -1, -1):
        error = chain[index]
        if error.__traceback__ is not None:
            parts.append("Traceback (most recent call last):\n")
            parts.extend(traceback.format_tb(error.__traceback__))
        parts.append(f"{describe_error(error)}\n")
        if isinstance(error, BaseExceptionGroup):
            for number, member in enumerate(error.exceptions, start=1):
                parts.append(f"Sub-exception {number} of {len(error.exceptions)}:\n")
                parts.append(textwrap.indent(_describe_traceback(member, seen), "    ") + "\n")
        if index:
            parts.append(links[index - 1])
    return "".join(parts).rstrip("\n")


def _describe_logged_exception(exc: BaseException) -> tuple[str, str]:
    """:func:`describe_error` and :func:`describe_traceback` of an exception a log record
    carries.

    Neither may fail here. ``logging`` prints an error a formatter raises to stderr, with the
    exception being logged as its context, message and all. An error a logger's filter raises,
    and a RecursionError from a formatter (``StreamHandler`` raises it again), escape the log
    call itself."""
    error = type(exc).__name__
    try:
        error = describe_error(exc)
        return error, describe_traceback(exc)
    except Exception as problem:  # noqa: BLE001 - a log line never fails; see the docstring
        return error, f"Traceback not written ({type(problem).__name__})\n{error}"


class JsonLogFormatter(logging.Formatter):
    """CoGA JSON formatter for all backend logs.

    A record that carries an exception (``logger.exception()``, ``exc_info=True``), from any
    logger and at any level, gets the exception's kind in ``error`` (:func:`describe_error`)
    and the frames of its chain in ``traceback`` (:func:`describe_traceback`), never its
    message, unless the call passed a ``traceback`` of its own."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
        }

        message = record.getMessage()
        if message:
            payload["message"] = message

        user = getattr(record, "user", None)
        if user is not None:
            if isinstance(user, dict):
                payload["user"] = user.get("email") or user.get("id")
            elif hasattr(user, "email"):
                payload["user"] = getattr(user, "email")
            elif hasattr(user, "id"):
                payload["user"] = str(getattr(user, "id"))
        elif getattr(record, "user_email", None):
            payload["user"] = getattr(record, "user_email")

        if hasattr(record, "http_request_json"):
            payload["httpRequest"] = getattr(record, "http_request_json")
            if getattr(record, "request_body", None) is not None:
                payload["requestBody"] = getattr(record, "request_body")

        if hasattr(record, "db_update"):
            payload["dbUpdate"] = getattr(record, "db_update")
        failure = record.exc_info[1] if record.exc_info else None
        if getattr(record, "traceback", None):
            payload["traceback"] = getattr(record, "traceback")
        elif failure is not None:
            # An exception's message quotes the values it was raised with: for a failed
            # statement its SQL and parameters, values from a request or read for one.
            payload["error"], payload["traceback"] = _describe_logged_exception(failure)
        if getattr(record, "detail", None):
            payload["detail"] = getattr(record, "detail")

        return json.dumps(payload, ensure_ascii=True)


class CoGALogger:
    """Logger adapter that accepts user metadata on log calls."""

    def __init__(self, name: str | None = None) -> None:
        self._logger = logging.getLogger(name)

    def _log(self, level: int, message: str, user: Any = None, **kwargs: Any) -> None:
        self._logger.log(level, message, extra={"user": user, **kwargs})

    def info(self, message: str, user: Any = None, **kwargs: Any) -> None:
        self._log(logging.INFO, message, user, **kwargs)

    def warning(self, message: str, user: Any = None, **kwargs: Any) -> None:
        self._log(logging.WARNING, message, user, **kwargs)

    def error(self, message: str, user: Any = None, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, user, **kwargs)


# A link token in a query string (the QC-report link, #522). The access log records
# the full request line; the token is a bearer credential for a few minutes.
_QUERY_TOKEN_RE = re.compile(r"(?i)([?&](?:token|access_token)=)[^&\s\"]+")


def redact_query_tokens(value: str) -> str:
    return _QUERY_TOKEN_RE.sub(r"\1***", value)


class RedactQueryTokenFilter(logging.Filter):
    """Mask link tokens in log records, e.g. uvicorn's access-log request line."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_query_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_query_tokens(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


def install_access_log_redaction() -> None:
    """Keep QC-report link tokens out of uvicorn's access log (#522)."""
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, RedactQueryTokenFilter) for existing in access_logger.filters):
        access_logger.addFilter(RedactQueryTokenFilter())


class RedactServerErrorFilter(logging.Filter):
    """Write the traceback a log record carries by its frames and kinds only
    (:func:`describe_traceback`).

    An exception that escapes a request is answered by Starlette with a 500 and raised again,
    and uvicorn logs it ("Exception in ASGI application") with its full traceback, whose
    messages quote a failed statement's SQL and parameters: values from the request."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = _describe_logged_exception(record.exc_info[1])[1]
            record.exc_info = None
        return True


def install_server_error_redaction() -> None:
    """Keep request values out of uvicorn's traceback of an unhandled exception."""
    error_logger = logging.getLogger("uvicorn.error")
    if not any(isinstance(existing, RedactServerErrorFilter) for existing in error_logger.filters):
        error_logger.addFilter(RedactServerErrorFilter())


_ASYNCIO_LOGGER = logging.getLogger("asyncio")

# What asyncio, uvloop and the libraries the backend runs give an event loop's exception handler
# as its message, up to the first object it quotes. A line writes the longest of these that the
# message starts with: the message itself when it is fixed text, its fixed part when it goes on
# to quote the callback with its arguments, an asynchronous generator, a file object by its name
# or a connection.
_EVENT_LOOP_MESSAGES = (
    # asyncio and uvloop
    "Task exception was never retrieved",
    "Future exception was never retrieved",
    "Task was destroyed but it is pending!",
    "Exception in callback",
    "unhandled exception during asyncio.run() shutdown",  # uvicorn's, anyio's and aiohttp's too
    "an error occurred during closing of asynchronous generator",
    "socket.accept() out of system resource",
    "Error on transport creation for incoming connection",
    "Unhandled exception in client_connected_cb",
    "An open stream was garbage collected prior to establishing network connection; "
    'call "stream.close()" explicitly.',
    "Fatal error on transport",  # uvloop's goes on with the transport's class and a reason
    "Fatal error on server",  # uvloop's, likewise
    "Fatal read error on socket transport",
    "Fatal write error on socket transport",
    "Fatal read error on datagram transport",
    "Fatal write error on datagram transport",
    "Fatal error on pipe transport",
    "Fatal read error on pipe transport",
    "Fatal write error on pipe transport",
    "Fatal error: protocol.data_received() call failed.",
    "Fatal error: protocol.eof_received() call failed.",
    "Fatal error: protocol.get_buffer() call failed.",
    "Fatal error: protocol.buffer_updated() call failed.",
    "Fatal error on SSL protocol",
    "SSL handshake failed",
    "SSL handshake failed on verifying the certificate",
    "Error calling eof_received()",
    "Error occurred during shutdown",
    "protocol.pause_writing() failed",
    "protocol.resume_writing() failed",
    "Unknown exception in SIGCHLD handler",
    "could not close attached file object",  # uvloop's
    "exception in Task.__repr__",  # uvloop's
    # asyncpg's (under SQLAlchemy) and aiohttp's (under clickhouse-connect)
    "Resetting connection with an active transaction",
    "Unclosed client session",
    "Unclosed connector",
    "Unclosed connection",
    "Unclosed response",
)


def _event_loop_message(context: dict[str, Any]) -> str:
    """The message of an event loop's exception context as fixed text (see
    :data:`_EVENT_LOOP_MESSAGES`); one it does not know is not written."""
    message = context.get("message")
    if not isinstance(message, str) or not message:
        return "Unhandled exception in event loop"  # asyncio's words for a context without one
    known = [text for text in _EVENT_LOOP_MESSAGES if message.startswith(text)]
    if known:
        return max(known, key=len)
    if message.endswith(" exception was never retrieved"):
        # Named after the class of the future that held it, a subclass of Future.
        return "Future exception was never retrieved"
    if message.startswith("Task ") and " has errored out but its parent task " in message:
        # A task group's, which quotes both tasks by their reprs: the first with its exception.
        return "Task has errored out but its parent task is already completed"
    return "Event loop error (message not written)"


def _code_name(value: Any) -> str:
    """The qualified name of a function, coroutine or asynchronous generator (of the function a
    partial calls), or else of its type: code, never a value."""
    while isinstance(value, functools.partial):
        value = value.func
    name = getattr(value, "__qualname__", None)
    if not isinstance(name, str) or not name:
        name = type(value).__qualname__
    return scrub_log(name, max_len=256)


def _event_loop_detail(context: dict[str, Any]) -> dict[str, str]:
    """The objects of an event loop's exception context by name, never by repr: a task by its
    name and coroutine, a callback and an asynchronous generator by their qualified names, the
    frames where an object was created (in debug mode) as frames, anything else by its type."""
    detail: dict[str, str] = {}
    for key, value in context.items():
        if key in ("message", "exception") or not isinstance(key, str):
            continue
        if isinstance(value, asyncio.Task):
            detail["task"] = scrub_log(value.get_name(), max_len=256)
            detail["coroutine"] = _code_name(value.get_coro())
        elif inspect.isasyncgen(value):
            detail[key] = _code_name(value)
        elif isinstance(value, traceback.StackSummary):
            # Each frame by file, line, function and source line; any locals it captured are left out.
            frames = [(frame.filename, frame.lineno, frame.name, frame.line) for frame in value]
            detail[key] = "".join(traceback.format_list(frames)).rstrip("\n")
        else:
            detail[key] = type(value).__qualname__
            # asyncio's handle names its callback; uvloop's does not, but the traceback has its frame.
            callback = getattr(value, "_callback", None) if isinstance(value, asyncio.Handle) else None
            if callback is not None:
                detail["callback"] = _code_name(callback)
    return detail


def log_event_loop_exception(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """An event loop's exception handler that logs what the loop reports without the values in
    it: a task's exception that nobody retrieved, a callback that raised, a transport's error.

    asyncio's default handler, and uvloop's, write the context's message and the repr of each
    object in it on the ``asyncio`` logger. A task's repr holds its exception's message
    (``exception=ValueError('...')``), a callback's its arguments, and the message itself quotes
    them: "Exception in callback" names the callback with its arguments (uvloop; asyncio before
    3.13). This one writes the message as fixed text (:func:`_event_loop_message`), the objects
    by name (:func:`_event_loop_detail`) in ``detail``, and the exception as ``exc_info``, which
    the JSON formatter writes by its kind and frames.

    It may not raise: the loop would then log the context with its default handler, reprs and
    all ("Unhandled error in exception handler")."""
    exception = context.get("exception")
    exc_info = (
        (type(exception), exception, exception.__traceback__)
        if isinstance(exception, BaseException)
        else None
    )
    try:
        message, detail = _event_loop_message(context), _event_loop_detail(context)
    except Exception as problem:  # noqa: BLE001 - a handler that raises logs the reprs; see above
        message, detail = f"Event loop error not described ({type(problem).__name__})", {}
    try:
        _ASYNCIO_LOGGER.error(message, exc_info=exc_info, extra={"detail": detail})
    except Exception as problem:  # noqa: BLE001 - a logger's filter or a RecursionError
        with contextlib.suppress(Exception):
            sys.stderr.write(f"Event loop error not logged ({type(problem).__name__})\n")


def install_event_loop_exception_handler(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Have the running event loop, or ``loop``, log what it reports value-free
    (:func:`log_event_loop_exception`).

    uvicorn creates the loop the app runs on, so the lifespan installs this as it starts. It is
    not taken off at shutdown: tasks are still reported as they are destroyed, and as
    ``asyncio.run`` closes the loop."""
    (loop if loop is not None else asyncio.get_running_loop()).set_exception_handler(
        log_event_loop_exception
    )


def configure_json_logging(level: int = logging.INFO) -> None:
    """Install JSON logging on the root logger if not configured yet."""

    root = logging.getLogger()
    if any(getattr(handler, "_coga_json_handler", False) for handler in root.handlers):
        return

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler._coga_json_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
