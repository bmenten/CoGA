from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

_UUID_TYPE = PG_UUID(as_uuid=True)


def uuid_value(value: str | UUID) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def uuid_values(values: Iterable[str | UUID]) -> list[UUID]:
    return [uuid_value(value) for value in values]


def uuid_bindparam(name: str):
    return bindparam(name, type_=_UUID_TYPE)


def uuid_list_bindparam(name: str):
    return bindparam(name, expanding=True, type_=_UUID_TYPE)
