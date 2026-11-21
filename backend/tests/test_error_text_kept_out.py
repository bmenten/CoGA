"""No error's text is stored, answered or logged unless CoGA wrote it.

AGENTS.md: logs carry no values. An error's text quotes what was being read or sent: a failed
statement's parameters, a line of a file, the address a source was fetched from. A log line, an
HTTP answer, an error CoGA raises in its own words and a stored failure name a caught error
through ``coga_logging``: ``error_text`` (CoGA's own sentence, ``own_error_message``, else the
error's kind, ``describe_error``), or ``describe_error`` and ``describe_traceback`` themselves.
A log record may carry the exception (``exc_info``): the JSON log writes it by its kind and
frames.

The guard reads every ``except ... as name`` handler in ``backend/app`` and refuses a read of
the error, other than through those, in a logging call, an ``HTTPException`` or a response, an
error raised, or as text (``str``, ``repr``, an f-string) in a call's argument or a stored
value. Reading the error to decide (``isinstance``, ``"UNKNOWN_TABLE" in str(exc)``) is left
alone. The runtime tests beside each module drive one failure of each kind with a sentinel in
the error's text and read it nowhere.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
# The calls that read an error without its text, or only to decide.
_READERS = {"error_text", "own_error_message", "describe_error", "describe_traceback", "isinstance", "type"}
# What answers a request.
_ANSWERS = {"HTTPException", "JSONResponse", "PlainTextResponse", "Response"}


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _is_logging_call(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in _LEVELS
        and "log" in ast.unparse(call.func.value).lower()
    )


def _as_text(node: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether the error is turned into its text here: ``str(exc)``, ``repr(exc)`` or an
    f-string's ``{exc}``."""
    parent = parents.get(node)
    if isinstance(parent, ast.FormattedValue):
        return True
    return isinstance(parent, ast.Call) and _call_name(parent) in {"str", "repr"} and node in parent.args


def _read_whole(node: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether the error itself is read here, not through a reader, its type or an attribute
    (``exc.status_code``, ``exc.lineno``), its arguments (``exc.args``) excepted."""
    parent = parents.get(node)
    if isinstance(parent, ast.Call) and _call_name(parent) in _READERS:
        return False
    if isinstance(parent, ast.Attribute):
        return parent.attr == "args"
    if isinstance(parent, ast.keyword) and parent.arg == "exc_info":
        return False  # the JSON log writes it by its kind and frames
    return True


def _sink(node: ast.Name, parents: dict[ast.AST, ast.AST], handler: ast.ExceptHandler) -> str | None:
    """Where the error's text would go from ``node``, or None."""
    whole = _read_whole(node, parents)
    text = _as_text(node, parents)
    if not whole and not text:
        return None
    child: ast.AST = node
    parent = parents.get(child)
    if text and isinstance(parent, ast.Call):
        child, parent = parent, parents.get(parent)  # from the str() or repr() on
    in_a_mapping = False
    while parent is not None and child is not handler:
        if isinstance(parent, ast.Call):
            name = _call_name(parent)
            if name in _READERS:
                return None
            if _is_logging_call(parent):
                return "a log line"
            if name in _ANSWERS:
                return "an answer"
            raised = parents.get(parent)
            if isinstance(raised, ast.Raise) and raised.exc is parent:
                return "an error raised"
            if text and child is not parent.func:
                return "a call's argument"
        elif isinstance(parent, ast.Compare):
            return None  # read to decide
        elif isinstance(parent, (ast.Assign, ast.AnnAssign)) and child is parent.value:
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            if text and any(isinstance(target, (ast.Subscript, ast.Attribute)) for target in targets):
                return "a stored value"
            break
        elif isinstance(parent, ast.Dict) and text:
            in_a_mapping = True
        elif isinstance(parent, ast.Raise):
            return None  # ``raise exc`` / ``raise ... from exc``
        elif isinstance(parent, ast.stmt):
            break
        child, parent = parent, parents.get(parent)
    return "a stored value" if in_a_mapping else None


def _error_text_reads(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    parents = _parents(tree)
    found: list[tuple[int, str]] = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        for statement in handler.body:
            for node in ast.walk(statement):
                if isinstance(node, ast.Name) and node.id == handler.name and isinstance(node.ctx, ast.Load):
                    sink = _sink(node, parents, handler)
                    if sink is not None:
                        found.append((node.lineno, sink))
    return found


def test_no_error_text_is_stored_answered_raised_or_logged() -> None:
    reads: list[str] = []
    for path in sorted(_APP.rglob("*.py")):
        relative = path.relative_to(_APP).as_posix()
        reads.extend(
            f"{relative}:{line} reads an error's text into {sink}"
            for line, sink in _error_text_reads(path.read_text(encoding="utf-8"))
        )
    assert reads == []


def test_the_guard_finds_an_errors_text_in_each_place() -> None:
    source = '''
try:
    pass
except Exception as exc:
    logger.warning("failed: %s", exc)
    logger.error(f"failed: {exc}")
    log.info("failed: %s", scrub_log(exc))
    raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise HTTPException(status_code=502, detail={"message": f"{exc}"}) from exc
    raise SourceError(f"could not be read: {exc}") from exc
    raise SourceError(str(exc)) from exc
    await record_failure(job_id, str(exc)[:2000])
    result["error"] = str(exc)
    errors.append(f"{target}: {exc}")
    logger.warning("failed: %s", exc.args[0])
'''
    assert [sink for _line, sink in _error_text_reads(source)] == [
        "a log line",
        "a log line",
        "a log line",
        "an answer",
        "an answer",
        "an error raised",
        "an error raised",
        "a call's argument",
        "a stored value",
        "a call's argument",
        "a log line",
    ]


def test_the_guard_leaves_a_reader_a_decision_and_a_re_raise_alone() -> None:
    source = '''
try:
    pass
except Exception as exc:
    logger.warning("failed: %s", error_text(exc))
    logger.warning("failed (%s)", describe_error(exc), exc_info=exc)
    logger.exception("failed")
    raise HTTPException(status_code=400, detail=own_error_message(exc)) from exc
    raise SourceError(f"could not be read ({describe_error(exc)})") from exc
    if "UNKNOWN_TABLE" in str(exc) or isinstance(exc, KeyError):
        raise
    message = str(exc)
    status = exc.response.status_code
    failure = exc
    raise exc
'''
    assert _error_text_reads(source) == []
