"""Make the values Postgres refuses storable, without hiding them (the request audit and the
UI-event log).

Postgres stores no NUL character in TEXT and no ``\\u0000`` in JSONB. JSONB also refuses half of
a UTF-16 surrogate pair, and the numbers ``NaN`` and ``Infinity``, which Python's JSON parser
accepts. A request can carry any of them (``%00`` in its path or query string, the escape in a
JSON field the endpoint ignores), and one such value used to fail the INSERT of the request's
audit row, and with it every row of the same batch.

The writers pass each column through :func:`storable_columns` first. A NUL is written as the
four characters ``\\x00`` and a lone surrogate as ``\\udXXX`` (their Python escapes); a
non-finite number becomes the string JSON would need (``"NaN"``, ``"Infinity"``,
``"-Infinity"``); a key is treated like a value. Everything else is stored as it came. The
writers record the columns this happened in under :data:`ESCAPED_KEY`, so a row says when it is
not verbatim.
"""

from __future__ import annotations

import math
import re
from typing import Any

# The key, in ``audit_log_events.request_meta`` and ``ui_events.detail``, that lists the columns
# in which a value was escaped. Only the writers set it.
ESCAPED_KEY = "_escaped"

# A NUL, or a surrogate. A Python string holds a surrogate only as half of a pair (json.loads
# joins a whole pair into one character), and UTF-8 cannot encode it.
_UNSTORABLE_RE = re.compile(r"[\x00\ud800-\udfff]")


def _escape(match: re.Match[str]) -> str:
    char = match.group()
    return "\\x00" if char == "\x00" else f"\\u{ord(char):04x}"


def _storable(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        escaped, count = _UNSTORABLE_RE.subn(_escape, value)
        return escaped, count > 0
    if isinstance(value, float) and not math.isfinite(value):
        return ("NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"), True
    if isinstance(value, dict):
        stored: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            stored_key, key_changed = _storable(key)
            stored[stored_key], item_changed = _storable(item)
            changed = changed or key_changed or item_changed
        return stored, changed
    if isinstance(value, (list, tuple)):
        items = [_storable(item) for item in value]
        return [item for item, _ in items], any(changed for _, changed in items)
    return value, False


def storable_columns(columns: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return ``columns`` with every value Postgres refuses escaped, and the sorted names of the
    columns in which something was."""
    stored: dict[str, Any] = {}
    escaped: list[str] = []
    for name, value in columns.items():
        stored[name], changed = _storable(value)
        if changed:
            escaped.append(name)
    return stored, sorted(escaped)
