"""A project still linked to families is not deleted (409, naming how many).

ClickHouse stores a family's small and structural variant rows once per project it is linked
to, and the family's reads look under its projects only. Deleting a project dropped its
families' links (``ON DELETE CASCADE``) and left their rows under the deleted project: a family
in no other project showed none, and one with other projects kept rows no reader looks at. The
owner chose (2026-11-10) to refuse the deletion while a family is linked: a change of the
family's projects moves its rows, so each family is moved, or deleted, first.

Over a fake session: the refusal comes before the review tags are deactivated and the project
deleted, with nothing committed; the project's row is locked before its families are counted;
a project without families is deleted as before. Real Postgres and ClickHouse:
``e2e/test_e2e_project_delete_variants.py``. The IDs are synthetic.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.services import metadata_service

PROJECT = "00000000-0000-4000-8000-00000000d001"


class _Result:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _Session:
    """Answers the family count with ``families``; records every statement."""

    def __init__(self, families: int) -> None:
        self.families = families
        self.calls: list[tuple[str, Any]] = []
        self.committed = False

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params))
        if sql.startswith("SELECT count(*) FROM family_projects"):
            return _Result(self.families)
        return _Result()

    async def commit(self) -> None:
        self.committed = True

    def index(self, prefix: str) -> int:
        return next(i for i, (sql, _params) in enumerate(self.calls) if sql.startswith(prefix))

    def ran(self, prefix: str) -> bool:
        return any(sql.startswith(prefix) for sql, _params in self.calls)


@pytest.fixture(autouse=True)
def _project_found(monkeypatch) -> None:
    async def _found(session, project_id):
        return {"id": project_id}

    monkeypatch.setattr(metadata_service, "_fetch_project_mapping", _found)


def _delete(session: _Session) -> None:
    asyncio.run(metadata_service.delete_project_record(session, project_id=PROJECT))


@pytest.mark.parametrize(
    ("families", "said"),
    [
        (1, "1 family is linked to this project. Move it to other projects, or to none, or delete it in"),
        (3, "3 families are linked to this project. Move each to other projects, or to none, or delete them in"),
    ],
)
def test_a_project_with_families_is_refused_naming_how_many_and_nothing_changes(families: int, said: str) -> None:
    session = _Session(families)
    with pytest.raises(HTTPException) as refused:
        _delete(session)

    assert refused.value.status_code == 409
    assert refused.value.detail.startswith(said)
    assert "Admin → Family & sample data first" in refused.value.detail
    assert "variants are stored per project" in refused.value.detail
    # Refused before anything is written: its tags stay active, the project stays.
    assert not session.ran("UPDATE small_variant_tag_definitions")
    assert not session.ran("DELETE FROM projects")
    assert session.committed is False


def test_the_projects_row_is_locked_before_its_families_are_counted() -> None:
    # A link written meanwhile holds a share lock on the project's row (its foreign-key check):
    # the lock waits for it to commit, so the count sees it.
    session = _Session(0)
    _delete(session)

    memberships = next(i for i, (_sql, params) in enumerate(session.calls) if params == {"k": "project-memberships"})
    lock = session.index("SELECT 1 FROM projects WHERE id = CAST(:project_id AS uuid) FOR UPDATE")
    count = session.index("SELECT count(*) FROM family_projects WHERE project_id = CAST(:project_id AS uuid)")
    assert memberships < lock < count
    assert session.calls[lock][1] == {"project_id": PROJECT}
    assert session.calls[count][1] == {"project_id": PROJECT}


def test_a_project_without_families_is_deleted_with_its_tags_deactivated() -> None:
    session = _Session(0)
    _delete(session)

    count = session.index("SELECT count(*) FROM family_projects")
    deactivate = session.index("UPDATE small_variant_tag_definitions")
    delete = session.index("DELETE FROM projects")
    assert count < deactivate < delete
    assert session.calls[delete][1] == {"project_id": PROJECT}
    assert session.committed is True
