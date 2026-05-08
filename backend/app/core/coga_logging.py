from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


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

    def debug(self, message: str, user: Any = None, **kwargs: Any) -> None:
        self._log(logging.DEBUG, message, user, **kwargs)

    def info(self, message: str, user: Any = None, **kwargs: Any) -> None:
        self._log(logging.INFO, message, user, **kwargs)

    def warning(self, message: str, user: Any = None, **kwargs: Any) -> None:
        self._log(logging.WARNING, message, user, **kwargs)

    def error(self, message: str, user: Any = None, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, user, **kwargs)


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
