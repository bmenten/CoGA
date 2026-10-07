"""A variant tag edit is saved, through the admin route and the family route (E2E, real Postgres).

``PUT /api/admin/variant-tags/{tag_key}`` and ``PUT /api/families/{family_id}/small-variant-tags/{tag_key}``
answered 500 whatever the request held. The lookup of the tag to edit selected ``project_id`` from the
tag definitions joined to their project links without naming the table, the links have a
``project_id`` too, and Postgres refused the statement as ambiguous. The unit tests answer every
statement from a fake session, so none ran it.

Each request runs over HTTP through an in-process ``httpx.ASGITransport`` client, as the seeded admin,
on one event loop (see test_e2e_api_contract.py for why). The test creates a project-scoped tag shared
with a second project, edits it through the admin route with the body the admin page sends on Save,
then through the family route with only the fields that change, and reads each edit back through
both list routes and from Postgres. An unknown tag is not found, and an edit with no field refused, on
both routes. The projects, the family and the tag are the test's own, with synthetic values, and are
dropped at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ADMIN_TAGS = "/api/admin/variant-tags"


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


def _listed(resp, key: str) -> dict | None:
    """The tag as a list route serves it, or the failed response."""
    if resp.status_code != 200:
        return {"status": resp.status_code, "text": resp.text}
    return next((tag for tag in resp.json() if tag["key"] == key), None)


async def _stored(session, key: str) -> dict | None:
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                """
                SELECT
                    d.label,
                    d.description,
                    d.scope,
                    d.project_id::text AS project_id,
                    d."group",
                    d.color,
                    d.is_active,
                    ARRAY(
                        SELECT l.project_id::text
                        FROM small_variant_tag_definition_project_links l
                        WHERE l.tag_id = d.id
                        ORDER BY 1
                    ) AS shared_project_ids
                FROM small_variant_tag_definitions d
                WHERE d.key = :key
                """
            ),
            {"key": key},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def _exercise(run: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    family_id = f"E2E_TAGS_{run}"
    async with sessionmaker() as session:
        _admin, e2e_project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        project_ids = [
            (
                await session.execute(
                    text(
                        """
                        INSERT INTO projects (name, description, species_id, assembly_id, metadata)
                        SELECT :name, 'variant tag edits', species_id, assembly_id, '{}'::jsonb
                        FROM projects WHERE id = CAST(:e2e AS uuid)
                        RETURNING id::text
                        """
                    ),
                    {"name": f"tag-edit-{role}-{run}", "e2e": e2e_project_id},
                )
            ).scalar_one()
            for role in ("home", "shared")
        ]
        family_uuid = (
            await session.execute(
                text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": family_id}
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO family_projects (family_id, project_id) "
                "VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"
            ),
            {"f": family_uuid, "p": project_ids[0]},
        )
        await session.commit()

    home, shared = project_ids
    key = f"e2e_tag_{run}"
    label = f"E2E tag {run}"
    admin_path = f"{_ADMIN_TAGS}/{key}"
    family_tags = f"/api/families/{family_id}/small-variant-tags"
    family_path = f"{family_tags}/{key}"
    out: dict = {"run": run, "key": key, "home": home, "shared": shared}
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            out["created"] = _cap(
                await ac.post(
                    _ADMIN_TAGS,
                    json={
                        "label": label,
                        "description": "Synthetic tag before its edits.",
                        "scope": "project",
                        "project_id": home,
                        "shared_project_ids": [shared],
                        "group": "custom",
                        "color": "#AA3366",
                    },
                )
            )

            # The body the admin page sends on Save: every field, here with the label unchanged.
            out["admin_edit"] = _cap(
                await ac.put(
                    admin_path,
                    json={
                        "label": label,
                        "description": "Edited through the admin route.",
                        "scope": "project",
                        "project_id": home,
                        "shared_project_ids": [shared],
                        "group": "collaboration",
                        "color": "#112233",
                    },
                )
            )
            out["admin_listed"] = _listed(await ac.get(_ADMIN_TAGS), key)
            async with sessionmaker() as session:
                out["stored_after_admin_edit"] = await _stored(session, key)

            # Only the fields that change: a label whose key stays the same, and the colour.
            # What the edit leaves out, the shared project included, comes from the lookup.
            out["family_edit"] = _cap(
                await ac.put(
                    family_path,
                    params={"project_id": home},
                    json={"label": f"e2e Tag {run}", "color": "#445566"},
                )
            )
            out["family_listed"] = _listed(await ac.get(family_tags, params={"project_id": home}), key)
            async with sessionmaker() as session:
                out["stored_after_family_edit"] = await _stored(session, key)

            unknown = f"e2e_unknown_{run}"
            out["unknown"] = {
                "admin": _cap(await ac.put(f"{_ADMIN_TAGS}/{unknown}", json={"color": "#000000"})),
                "family": _cap(await ac.put(f"{family_tags}/{unknown}", json={"color": "#000000"})),
            }
            out["no_field"] = {
                "admin": _cap(await ac.put(admin_path, json={})),
                "family": _cap(await ac.put(family_path, json={})),
            }
    finally:
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM small_variant_tag_definitions WHERE key = :key"), {"key": key}
            )
            await session.execute(
                text("DELETE FROM families WHERE id = CAST(:id AS uuid)"), {"id": family_uuid}
            )
            for project_id in project_ids:
                await session.execute(
                    text("DELETE FROM projects WHERE id = CAST(:id AS uuid)"), {"id": project_id}
                )
            await session.commit()
    return out


@pytest.fixture(scope="module")
def edited() -> dict:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(run))


def _fields(tag: dict | None, expected: dict) -> dict | None:
    if tag is None:
        return None
    return {name: tag.get(name) for name in expected}


def test_an_edit_through_the_admin_route_is_saved(edited) -> None:
    assert edited["created"]["status"] == 200, edited["created"]
    assert edited["created"]["json"]["key"] == edited["key"]
    edit = edited["admin_edit"]
    assert edit["status"] == 200, edit
    expected = {
        "key": edited["key"],
        "label": f"E2E tag {edited['run']}",
        "description": "Edited through the admin route.",
        "group": "collaboration",
        "color": "#112233",
        "scope": "project",
        "project_id": edited["home"],
        "shared_project_ids": [edited["shared"]],
        "is_custom": True,
    }
    assert _fields(edit["json"], expected) == expected
    assert _fields(edited["admin_listed"], expected) == expected, edited["admin_listed"]
    assert edited["stored_after_admin_edit"] == {
        "label": f"E2E tag {edited['run']}",
        "description": "Edited through the admin route.",
        "scope": "project",
        "project_id": edited["home"],
        "group": "collaboration",
        "color": "#112233",
        "is_active": True,
        "shared_project_ids": [edited["shared"]],
    }


def test_an_edit_through_the_family_route_keeps_what_it_leaves_out(edited) -> None:
    edit = edited["family_edit"]
    assert edit["status"] == 200, edit
    expected = {
        "key": edited["key"],
        "label": f"e2e Tag {edited['run']}",
        "description": "Edited through the admin route.",
        "group": "collaboration",
        "color": "#445566",
        "scope": "project",
        "project_id": edited["home"],
        "shared_project_ids": [edited["shared"]],
        "is_custom": True,
    }
    assert _fields(edit["json"], expected) == expected
    assert _fields(edited["family_listed"], expected) == expected, edited["family_listed"]
    assert edited["stored_after_family_edit"] == {
        "label": f"e2e Tag {edited['run']}",
        "description": "Edited through the admin route.",
        "scope": "project",
        "project_id": edited["home"],
        "group": "collaboration",
        "color": "#445566",
        "is_active": True,
        "shared_project_ids": [edited["shared"]],
    }


def test_an_unknown_tag_is_not_found_on_either_route(edited) -> None:
    for route, resp in edited["unknown"].items():
        assert resp["status"] == 404, (route, resp)
        assert "Variant tag not found" in resp["text"], (route, resp)


def test_an_edit_with_no_field_is_refused_on_either_route(edited) -> None:
    for route, resp in edited["no_field"].items():
        assert resp["status"] == 400, (route, resp)
        assert "No tag fields were provided" in resp["text"], (route, resp)
