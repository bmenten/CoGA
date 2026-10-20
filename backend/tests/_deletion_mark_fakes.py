"""A deletion of a family's data without its mark, for the tests of what else a deletion does.

A deletion that deletes or rewrites the family's ClickHouse rows marks the family before its
first write there (``family_package_registration.FamilyDeletion``, SAFE-31): it reads the
family's ``imported_scopes`` in the request's transaction, writes the mark on a connection of
its own, and removes it with its commit. Tests of a deletion's locks, its audit events or the
rows it writes back run it against fakes of the request's transaction that answer only the
statements they are about; they put ``UnmarkedDeletion`` in its place, which reads and writes
nothing of the family row. ``test_deletions_mark_the_family.py`` tests the mark itself.

Import as ``from backend.tests._deletion_mark_fakes import UnmarkedDeletion``, and patch it
over ``FamilyDeletion`` in the module tree the test imports (``app.`` or ``backend.app.``):
the admin and family-structure services import the name, and the PED service reads it from
``family_package_registration`` when it runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any


class UnmarkedDeletion:
    """``FamilyDeletion``'s interface, writing no mark; ``reached`` records what each write
    reached, in order."""

    def __init__(self, *, family_uuid: str, family_id: str) -> None:
        self.family_uuid = family_uuid
        self.family_id = family_id
        self.reached: list[Any] = []

    @classmethod
    async def begin(
        cls, _session: Any, *, family_uuid: str, family_id: str, reaches: Iterable[Any]
    ) -> "UnmarkedDeletion":
        return cls(family_uuid=family_uuid, family_id=family_id)

    def reach(self, reach: Any) -> None:
        self.reached.append(reach)

    @asynccontextmanager
    async def rewrite(self, *, failed: Any, done: Any = None) -> AsyncIterator[None]:
        yield

    async def finished(self, _session: Any, *, deleted_sample: str | None = None) -> None:
        return None

    async def failed(self, _session: Any) -> None:
        return None
