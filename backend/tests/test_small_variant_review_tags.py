"""Variant tag definitions: built-in and custom tags, their scope and their lifecycle (#526).

Tags decide what a review is marked with, including the report tag a sign-out collects,
so what may be created, seen, renamed and deleted is pinned here. A tag's key is its
identity: an edit changes its label only, and a deleted tag stays listable for the
reviews that hold it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend.app.schemas import SmallVariantTagDefinitionCreate, SmallVariantTagDefinitionUpdate
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
    # By a built-in's key, and by a built-in's label whose slug is not its key.
    for label in ("Review", "Pathogenic - class 5", "vus  class 3"):
        with pytest.raises(HTTPException) as refused:
            _create(_Session(), _user("admin"), label=label)
        assert refused.value.status_code == 409, label


_ACTIVE_LABELS = "SELECT id::text AS id, label FROM small_variant_tag_definitions WHERE is_active = TRUE"
_KEYS_IN_USE = "SELECT key FROM small_variant_tag_definitions"


def test_a_label_another_active_tag_reads_as_is_refused() -> None:
    # Compared by slug: case, spacing and punctuation do not make two labels differ.
    session = _Session({_ACTIVE_LABELS: _Result(rows=[{"id": "t1", "label": "needs  SEGREGATION!"}])})
    with pytest.raises(HTTPException) as refused:
        _create(session, _user("admin"))
    assert refused.value.status_code == 409
    assert not any(sql.startswith("INSERT") for sql, _ in session.calls)


def test_a_new_tag_whose_slug_another_tag_holds_as_its_key_gets_a_numbered_key() -> None:
    # A tag since renamed, or deleted, keeps its key: the new tag must not take it over,
    # with the reviews that hold it.
    session = _Session(
        {
            _KEYS_IN_USE: _Result(rows=[{"key": "needs_segregation"}, {"key": "needs_segregation_2"}]),
            "INSERT INTO small_variant_tag_definitions": _Result(scalar="new-id"),
        }
    )
    created = _create(session, _user("admin"), scope="global")

    assert created.key == "needs_segregation_3"
    insert = next(params for sql, params in session.calls if sql.startswith("INSERT INTO small_variant_tag_definitions"))
    assert insert["key"] == "needs_segregation_3"


def test_a_numbered_key_is_never_a_built_in_key() -> None:
    session = _Session(
        {
            _KEYS_IN_USE: _Result(rows=[{"key": "acmg_class"}]),
            "INSERT INTO small_variant_tag_definitions": _Result(scalar="new-id"),
        }
    )
    created = _create(session, _user("admin"), scope="global", label="ACMG class")

    # acmg_class_1 to acmg_class_5 are the built-in classification tags.
    assert created.key == "acmg_class_6"


def test_a_tag_is_created_under_the_definitions_lock() -> None:
    session = _Session({"INSERT INTO small_variant_tag_definitions": _Result(scalar="new-id")})
    _create(session, _user("admin"), scope="global")

    statements = [sql for sql, _ in session.calls]
    assert "pg_advisory_xact_lock" in statements[0]
    assert statements.index(_ACTIVE_LABELS) < next(
        index for index, sql in enumerate(statements) if sql.startswith("INSERT")
    )


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


def _custom_row(key: str, scope: str = "global", project_id: str | None = None, *, is_active: bool = True) -> dict:
    return {
        "key": key,
        "label": key.title(),
        "description": None,
        "scope": scope,
        "project_id": project_id,
        "group": "custom",
        "color": "#123456",
        "sort_order": 500,
        "is_active": is_active,
        "shared_project_ids": [],
    }


def test_the_built_in_tags_come_first_then_the_visible_custom_ones() -> None:
    session = _Session({"FROM small_variant_tag_definitions d": _Result(rows=[_custom_row("mine", "project", P1)])})
    listed = asyncio.run(tags.list_small_variant_tag_definitions(session, family_uuid="f1", project_ids=[P1]))

    built_in = [t.key for t in listed if not t.is_custom]
    assert built_in == [entry["key"] for entry in tags.DEFAULT_SMALL_VARIANT_TAGS]
    assert [t.key for t in listed if t.is_custom] == ["mine"]
    assert all(t.is_active for t in listed)
    # The custom tags are filtered to the family's projects, and to the active ones.
    sql, params = session.calls[-1]
    assert "d.project_id IN" in sql and params == {"project_ids": [P1], "include_inactive": False}


def test_without_projects_only_global_custom_tags_are_listed() -> None:
    session = _Session({"FROM small_variant_tag_definitions d": _Result(rows=[_custom_row("everywhere")])})
    listed = asyncio.run(tags.list_small_variant_tag_definitions(session, family_uuid="f1", project_ids=[]))

    assert [t.key for t in listed if t.is_custom] == ["everywhere"]
    assert "d.scope = 'global'" in session.calls[-1][0]


def test_deleted_tags_are_listed_on_request_and_flagged_inactive() -> None:
    # So a review that still holds one can show it by its label (it can no longer be added).
    for project_ids, all_projects in (([P1], False), ([], False), ([], True)):
        session = _Session(
            {"FROM small_variant_tag_definitions d": _Result(rows=[_custom_row("gone", is_active=False)])}
        )
        listed = asyncio.run(
            tags.list_small_variant_tag_definitions(
                session,
                family_uuid="f1",
                project_ids=project_ids,
                include_all_project_tags=all_projects,
                include_inactive=True,
            )
        )
        [gone] = [t for t in listed if t.is_custom]
        assert (gone.key, gone.is_active) == ("gone", False)
        sql, params = session.calls[-1]
        assert "d.is_active OR CAST(:include_inactive AS boolean)" in sql
        assert params["include_inactive"] is True


# --- editing ---------------------------------------------------------------------------------------


def _stored_tag(**fields) -> dict:
    return {
        "id": "t1",
        "key": "probe_rename_x",
        "label": "Probe rename X",
        "description": None,
        "scope": "global",
        "project_id": None,
        "group": "custom",
        "color": "#123456",
        "sort_order": 500,
        "shared_project_ids": [],
        **fields,
    }


def _edit(session: _Session, key: str = "probe_rename_x", **payload):
    return asyncio.run(
        tags.update_small_variant_tag_definition(
            session,
            family_uuid="f1",
            tag_key=key,
            payload=SmallVariantTagDefinitionUpdate(**payload),
            user=_user("admin"),
        )
    )


_LOOKUP = "FROM small_variant_tag_definitions d LEFT JOIN"


def test_a_label_edit_keeps_the_key() -> None:
    # Reviews, saved filters and the audit trail hold the key: a rename must not strand it.
    session = _Session({_LOOKUP: _Result(rows=[_stored_tag()])})
    edited = _edit(session, label="Probe renamed X")

    assert (edited.key, edited.label) == ("probe_rename_x", "Probe renamed X")
    update_sql, update_params = next(
        (sql, params) for sql, params in session.calls if sql.startswith("UPDATE small_variant_tag_definitions")
    )
    assert "key =" not in update_sql and "key" not in update_params
    assert update_params["label"] == "Probe renamed X"
    assert session.committed is True


def test_a_label_edit_onto_another_active_tags_label_is_refused() -> None:
    session = _Session(
        {
            _LOOKUP: _Result(rows=[_stored_tag()]),
            _ACTIVE_LABELS: _Result(
                rows=[{"id": "t1", "label": "Probe rename X"}, {"id": "t2", "label": "Probe renamed X"}]
            ),
        }
    )
    with pytest.raises(HTTPException) as refused:
        _edit(session, label="probe renamed x")
    assert refused.value.status_code == 409
    assert session.committed is False


def test_a_label_edit_onto_a_built_in_label_is_refused() -> None:
    for label in ("Report", "VUS - class 3"):
        session = _Session({_LOOKUP: _Result(rows=[_stored_tag()])})
        with pytest.raises(HTTPException) as refused:
            _edit(session, label=label)
        assert refused.value.status_code == 409, label


def test_a_tag_may_keep_its_own_label_in_another_case() -> None:
    session = _Session(
        {
            _LOOKUP: _Result(rows=[_stored_tag()]),
            _ACTIVE_LABELS: _Result(rows=[{"id": "t1", "label": "Probe rename X"}]),
        }
    )
    assert _edit(session, label="PROBE rename x").label == "PROBE rename x"


def test_a_tag_is_edited_under_the_definitions_lock() -> None:
    session = _Session({_LOOKUP: _Result(rows=[_stored_tag()])})
    _edit(session, color="#654321")

    statements = [sql for sql, _ in session.calls]
    assert "pg_advisory_xact_lock" in statements[0]
    assert _LOOKUP in statements[1]


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
    assert "pg_advisory_xact_lock" in statements[0]
    assert any(sql.startswith("UPDATE small_variant_tag_definitions SET is_active = FALSE") for sql in statements)
    assert not any(sql.startswith("DELETE FROM small_variant_tag_definitions ") for sql in statements)
    # Its project links stay, so the reviews that hold it still show it in those projects.
    assert not any("small_variant_tag_definition_project_links" in sql for sql in statements)


def test_only_an_admin_deletes_tags() -> None:
    with pytest.raises(HTTPException) as refused:
        _delete(_Session(), _user("viewer"), "needs_segregation")
    assert refused.value.status_code == 403
