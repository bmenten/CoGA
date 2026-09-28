"""Variant tag definitions: built-in and custom tags, their scope and their lifecycle (#526).

Tags decide what a review is marked with, including the report tag a sign-out collects,
so what may be created, seen and deleted is pinned here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend.app.schemas import SmallVariantTagDefinitionCreate
from backend.app.services import small_variant_review_tags as tags
from backend.app.services.access_control import CurrentUser

P1 = "00000000-0000-0000-0000-0000000000p1".replace("p", "a")
P2 = "00000000-0000-0000-0000-0000000000p2".replace("p", "b")


def _user(role: str, projects: list[str] | None = None) -> CurrentUser:
    return CurrentUser(
        id="u1",
        username="lab.admin",
        email="lab.admin@example.org",
        role=role,
        metadata_project_ids=projects or [],
        created_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar, self._rows = scalar, rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Answers each statement by the first matching fragment; records every call."""

    def __init__(self, answers: dict[str, _Result] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[str, object]] = []
        self.committed = False

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params))
        for fragment, result in self.answers.items():
            if fragment in sql:
                return result
        return _Result()

    async def commit(self) -> None:
        self.committed = True


def _create(session: _Session, user: CurrentUser, **payload):
    body = {"label": "Needs segregation", "color": "#AA3366", "group": "custom", **payload}
    return asyncio.run(
        tags.create_small_variant_tag_definition(
            session, family_uuid="f1", payload=SmallVariantTagDefinitionCreate(**body), user=user
        )
    )


# --- validation helpers ------------------------------------------------------------------------


def test_a_label_becomes_a_key_and_an_unusable_label_is_refused() -> None:
    assert tags._slugify_tag("  Send to Sanger!  ") == "send_to_sanger"
    with pytest.raises(HTTPException) as refused:
        tags._slugify_tag("!!!")
    assert refused.value.status_code == 400


def test_a_colour_must_be_a_six_digit_hex_code() -> None:
    assert tags._normalize_hex_color(" #AA3366 ") == "#aa3366"
    for bad in ("#abc", "red", "", None):
        with pytest.raises(HTTPException):
            tags._normalize_hex_color(bad)


def test_project_ids_are_validated_and_deduplicated() -> None:
    assert tags._normalize_project_scope_ids([P1, " ", P1, P2]) == [P1, P2]
    with pytest.raises(HTTPException) as refused:
        tags._normalize_project_scope_ids(["not-a-uuid"])
    assert refused.value.status_code == 400


# --- creating a tag --------------------------------------------------------------------------------


def test_only_an_admin_creates_tags() -> None:
    with pytest.raises(HTTPException) as refused:
        _create(_Session(), _user("viewer"))
    assert refused.value.status_code == 403


def test_a_label_that_collides_with_a_built_in_tag_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        _create(_Session(), _user("admin"), label="Review")
    assert refused.value.status_code == 409


def test_a_label_already_in_use_is_refused() -> None:
    session = _Session({"FROM small_variant_tag_definitions WHERE key": _Result(scalar="existing-id")})
    with pytest.raises(HTTPException) as refused:
        _create(session, _user("admin"))
    assert refused.value.status_code == 409


def test_a_project_scoped_tag_needs_a_project() -> None:
    with pytest.raises(HTTPException) as refused:
        _create(_Session(), _user("admin"), scope="project")
    assert refused.value.status_code == 400


def test_a_project_scoped_tag_for_an_unknown_project_is_refused() -> None:
    session = _Session({"FROM projects": _Result(rows=[])})
    with pytest.raises(HTTPException) as refused:
        _create(session, _user("admin"), scope="project", project_id=P1)
    assert refused.value.status_code == 400
    assert session.committed is False


def test_a_global_tag_is_created_with_its_colour_normalised() -> None:
    session = _Session({"INSERT INTO small_variant_tag_definitions": _Result(scalar="new-id")})
    created = _create(session, _user("admin"), scope="global", description="  Check both parents  ")

    assert (created.key, created.color, created.scope, created.description) == (
        "needs_segregation",
        "#aa3366",
        "global",
        "Check both parents",
    )
    insert = next(params for sql, params in session.calls if sql.startswith("INSERT INTO small_variant_tag_definitions"))
    assert insert["project_id"] is None and insert["created_by"] == "lab.admin"
    assert session.committed is True


def test_a_project_tag_links_its_shared_projects_but_not_its_own() -> None:
    session = _Session(
        {
            "FROM projects": _Result(rows=[{"id": P1}, {"id": P2}]),
            "INSERT INTO small_variant_tag_definitions": _Result(scalar="new-id"),
        }
    )
    created = _create(session, _user("admin"), scope="project", project_id=P1, shared_project_ids=[P1, P2])

    assert (created.project_id, created.shared_project_ids) == (P1, [P2])
    links = next(params for sql, params in session.calls if "INSERT INTO small_variant_tag_definition_project_links" in sql)
    assert links == [{"tag_id": "new-id", "project_id": P2}]


# --- listing ---------------------------------------------------------------------------------------


def _custom_row(key: str, scope: str = "global", project_id: str | None = None) -> dict:
    return {
        "key": key,
        "label": key.title(),
        "description": None,
        "scope": scope,
        "project_id": project_id,
        "group": "custom",
        "color": "#123456",
        "sort_order": 500,
        "shared_project_ids": [],
    }


def test_the_built_in_tags_come_first_then_the_visible_custom_ones() -> None:
    session = _Session({"FROM small_variant_tag_definitions d": _Result(rows=[_custom_row("mine", "project", P1)])})
    listed = asyncio.run(tags.list_small_variant_tag_definitions(session, family_uuid="f1", project_ids=[P1]))

    built_in = [t.key for t in listed if not t.is_custom]
    assert built_in == [entry["key"] for entry in tags.DEFAULT_SMALL_VARIANT_TAGS]
    assert [t.key for t in listed if t.is_custom] == ["mine"]
    # The custom tags are filtered to the family's projects.
    sql, params = session.calls[-1]
    assert "d.project_id IN" in sql and params == {"project_ids": [P1]}


def test_without_projects_only_global_custom_tags_are_listed() -> None:
    session = _Session({"FROM small_variant_tag_definitions d": _Result(rows=[_custom_row("everywhere")])})
    listed = asyncio.run(tags.list_small_variant_tag_definitions(session, family_uuid="f1", project_ids=[]))

    assert [t.key for t in listed if t.is_custom] == ["everywhere"]
    assert "d.scope = 'global'" in session.calls[-1][0]


# --- deleting --------------------------------------------------------------------------------------


def _delete(session: _Session, user: CurrentUser, key: str) -> None:
    asyncio.run(tags.delete_small_variant_tag_definition(session, family_uuid="f1", tag_key=key, user=user))


def test_a_built_in_tag_cannot_be_deleted() -> None:
    with pytest.raises(HTTPException) as refused:
        _delete(_Session(), _user("admin"), "Review")
    assert refused.value.status_code == 400


def test_deleting_an_unknown_tag_is_not_found() -> None:
    with pytest.raises(HTTPException) as refused:
        _delete(_Session({"FROM small_variant_tag_definitions": _Result(rows=[])}), _user("admin"), "gone")
    assert refused.value.status_code == 404


def test_a_deleted_tag_is_deactivated_not_removed() -> None:
    session = _Session({"SELECT id::text AS id FROM small_variant_tag_definitions": _Result(rows=[{"id": "t1"}])})
    _delete(session, _user("admin"), "needs_segregation")

    statements = [sql for sql, _ in session.calls]
    assert any(sql.startswith("UPDATE small_variant_tag_definitions SET is_active = FALSE") for sql in statements)
    assert not any(sql.startswith("DELETE FROM small_variant_tag_definitions ") for sql in statements)


def test_only_an_admin_deletes_tags() -> None:
    with pytest.raises(HTTPException) as refused:
        _delete(_Session(), _user("viewer"), "needs_segregation")
    assert refused.value.status_code == 403
