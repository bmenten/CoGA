from __future__ import annotations

from typing import Iterable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import bindparam
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import DBAPIError

_UUID_TYPE = PG_UUID(as_uuid=True)


def uuid_value(value: str | UUID) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def require_uuid(value: str, detail: str) -> None:
    """Refuse (400) an identifier from the request that is not a UUID."""
    try:
        UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


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
