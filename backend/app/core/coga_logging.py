from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

# C0 control characters + DEL. CR/LF here are what let an attacker forge extra log
# lines (CWE-117); tab/other control chars can corrupt log parsing too.
_LOG_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def scrub_log(value: Any, *, max_len: int = 512) -> str:
    """Neutralize control characters before a value is interpolated into a log message,
    defeating log forging. Returns a plain ``str``; oversized values are truncated."""
    scrubbed = _LOG_CONTROL_RE.sub(" ", str(value))
    return scrubbed if len(scrubbed) <= max_len else scrubbed[: max_len - 3] + "..."


class JsonLogFormatter(logging.Formatter):
    """CoGA JSON formatter for all backend logs."""

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
        if getattr(record, "traceback", None):
            payload["traceback"] = getattr(record, "traceback")
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
