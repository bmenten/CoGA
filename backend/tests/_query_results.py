"""A query result answered from fixed rows, for the fake sessions of the unit tests.

``MappingRows(rows)`` stands in for what ``AsyncSession.execute`` returns when the code under
test reads it as ``result.mappings().all()`` or ``result.mappings().first()``: the rows as
given (a copy of the list), or the first of them, or None when there is none. Each test file
keeps its own fake session, which decides which rows a statement gets.
"""

from __future__ import annotations

from typing import Any


class MappingRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "MappingRows":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None
