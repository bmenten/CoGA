"""The family row's import state in Postgres, as the single-file uploads read and write it.

The state is ``flag`` (``import_incomplete``), ``unfinished`` (``import_unfinished``,
where an upload marks the family before its first ClickHouse write) and ``imported``
(``imported_scopes``). A write through the request's transaction is the transaction's until
it commits; a rollback discards it. The upload writes its mark on a connection of its own
(``family_package_registration.mark_family_upload``), committed at once, so the upload's
transaction sees it and its rollback keeps it: ``mark_connection()``.

Import as ``from backend.tests._family_import_state_fakes import FamilyImportState``. It
imports no app module, so it serves the tests of the ``app.`` module tree and of the
``backend.app.`` one alike. Subclass it to keep more of the family's state (``committed``
takes extra keys) or to record the mark (``marked``).
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any


class FamilyImportState:
    """The family row: its committed state, and the request's transaction's ``working``
    copy of it; ``commits`` counts the transaction's commits."""

    def __init__(self, *, flag: Any = None, unfinished: Any = None, imported: Any = None, **more: Any) -> None:
        self.committed: dict[str, Any] = {"flag": flag, "unfinished": unfinished, "imported": imported, **more}
        self.working = copy.deepcopy(self.committed)
        self.commits = 0

    @staticmethod
    def apply(state: dict[str, Any], params: dict[str, Any]) -> None:
        """An ``UPDATE families`` with ``params`` applied to ``state``."""
        if "entry" in params:  # a mark written
            state["unfinished"] = {**(state["unfinished"] or {}), params["import_key"]: json.loads(params["entry"])}
            return
        if set(params) == {"family_uuid", "import_key"}:  # an entry removed alone
            left = dict(state["unfinished"] or {})
            left.pop(params["import_key"], None)
            state["unfinished"] = left or None
            return
        recorded = json.loads(params.get("imported_scopes", "null"))
        if recorded is not None:  # a JSON null leaves it as it is
            state["imported"] = recorded
        if "unfinished" in params:
            state["unfinished"] = json.loads(params["unfinished"])
        if "flag" in params:
            state["flag"] = json.loads(params["flag"])

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT") and "FROM families" in sql:
            row = {
                "family_uuid": "family-uuid",
                "flag": copy.deepcopy(self.working["flag"]),
                "unfinished": copy.deepcopy(self.working["unfinished"]),
                "imported_scopes": copy.deepcopy(self.working["imported"]),
            }
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        assert sql.startswith("UPDATE families"), sql
        self.apply(self.working, dict(params or {}))
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        self.committed = copy.deepcopy(self.working)
        self.commits += 1

    async def rollback(self) -> None:
        self.working = copy.deepcopy(self.committed)

    def marked(self) -> None:
        """Called as a mark is committed on its own connection; a subclass records it."""

    def mark_connection(self) -> "_MarkConnection":
        """The connection the upload writes its mark on."""
        return _MarkConnection(self)


class _MarkConnection:
    """Committed at once: the write is in the committed state and the transaction's."""

    def __init__(self, database: FamilyImportState) -> None:
        self.database = database

    async def __aenter__(self) -> "_MarkConnection":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        assert " ".join(str(statement).split()).startswith("UPDATE families")
        for state in (self.database.committed, self.database.working):
            self.database.apply(state, dict(params or {}))
        self.database.marked()
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        return None
