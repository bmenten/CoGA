from __future__ import annotations

import re
from typing import Iterable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import bindparam
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import DBAPIError

_UUID_TYPE = PG_UUID(as_uuid=True)

# 32 hex digits, hyphenated 8-4-4-4-12 or not at all; ASCII only.
_UUID_HEX = re.compile(r"[0-9a-fA-F]{8}(-?)[0-9a-fA-F]{4}\1[0-9a-fA-F]{4}\1[0-9a-fA-F]{4}\1[0-9a-fA-F]{12}")
_UUID_URN_PREFIX = "urn:uuid:"


def uuid_value(value: str | UUID) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def canonical_uuid(value: object) -> str | None:
    """The canonical text (lower case, hyphenated) of the UUID ``value`` spells, or None
    when it spells none.

    A request names a record by its UUID. Bind this text, never the value as received:
    asyncpg's uuid codec refuses anything but hex digits and hyphens, so a request with
    any other value failed with a 500 instead of finding no record. A UUID is spelled as
    32 hex digits, hyphenated 8-4-4-4-12 or not at all, in any case, and may be wrapped in
    braces or follow ``urn:uuid:``. What ``uuid.UUID`` merely tolerates spells none: an
    underscore, a space or a sign among the digits, ``0x``, a hyphen elsewhere, digits of
    another script; read as a number, such a value would name a different-looking record.
    """
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    hex_text = value
    if hex_text.startswith(_UUID_URN_PREFIX):
        hex_text = hex_text[len(_UUID_URN_PREFIX) :]
    elif hex_text.startswith("{") and hex_text.endswith("}"):
        hex_text = hex_text[1:-1]
    if _UUID_HEX.fullmatch(hex_text) is None:
        return None
    return str(UUID(hex_text))


def require_uuid(value: object, detail: str, *, status_code: int = 400) -> str:
    """The canonical text of the UUID a request names a record by (``canonical_uuid``).

    A value that spells no UUID names no record. It is refused with ``status_code`` and
    ``detail`` before any query: the route's own "not found" (404) where an unknown record
    is answered so, or the 400 a route already answers an invalid id with.
    """
    canonical = canonical_uuid(value)
    if canonical is None:
        raise HTTPException(status_code=status_code, detail=detail)
    return canonical


def uuid_values(values: Iterable[str | UUID]) -> list[UUID]:
    return [uuid_value(value) for value in values]


def uuid_list_bindparam(name: str):
    return bindparam(name, expanding=True, type_=_UUID_TYPE)


def is_missing_postgres_schema_error(exc: BaseException) -> bool:
    """Return true for Postgres table/column errors caused by unapplied schema."""
    if not isinstance(exc, DBAPIError):
        return False
    orig = getattr(exc, "orig", None)
    error_text = " ".join(
        str(part)
        for part in (
            type(exc).__name__,
            exc,
            type(orig).__name__ if orig is not None else "",
            orig or "",
        )
    ).lower()
    return (
        "undefinedtableerror" in error_text
        or "undefinedcolumnerror" in error_text
        or ("relation" in error_text and "does not exist" in error_text)
        or ("column" in error_text and "does not exist" in error_text)
    )
