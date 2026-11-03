"""A Postgres session for the PGT analysis's unit tests (``pgt_analysis.family_pgt_analysis``
reads the family's ROI and inheritance model, and the record of each member's haplotype
blocks): it answers each statement by a fragment of its SQL and records what ran. Used by
``test_pgt_analysis.py``, ``test_outdated_haplotype_blocks.py`` and
``test_haplotype_block_builder_chrx.py``. Synthetic rows only.
"""

from __future__ import annotations

from typing import Any

from backend.tests._query_results import MappingRows

__all__ = ["AnsweringSession", "MappingRows"]


class AnsweringSession:
    """Answers each statement with the rows of the first fragment of ``answers`` its SQL
    holds, else none; ``executed`` holds each statement's SQL and parameters."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.executed: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> MappingRows:
        sql = str(statement)
        self.executed.append((sql, params))
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return MappingRows(rows)
        return MappingRows([])
