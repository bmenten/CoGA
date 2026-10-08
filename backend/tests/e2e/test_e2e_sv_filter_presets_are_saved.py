"""A structural-variant filter preset is saved, listed and deleted (E2E, real Postgres).

``POST /api/families/{family_id}/structural-variant-filter-presets`` answered 500 for every
family and every body. The route read its body as a small-variant preset, whose ``scope`` went
when small-variant presets became their owner's, reusable in every family (#681), while an SV
preset kept the choice between one family and all of them and the service still read the
scope. The list served the presets without their scope, so the SV page labelled each one
reusable. No test saved an SV preset.

Each request runs over HTTP through an in-process ``httpx.ASGITransport`` client, on one event
loop (see test_e2e_api_contract.py for why), as the seeded admin, who owns the presets, and as
a viewer of the same project. Two families of the test's own, with synthetic values, are in
the e2e project. The admin saves with the body the SV page sends on Save (a preset for the
family), saves again under the same name, saves a reusable preset under that name and one
without a scope, and each save is read back through the list of both families and from
Postgres. A scope the API does not know and a blank name are refused and store nothing. The
viewer lists none of the admin's presets and may not delete one. A family's preset is deleted
through its own family only, a reusable one through any. The families, the viewer and the
presets are dropped at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

# The search the SV page saves, as buildStructuralPresetPayload serialises it
# (frontend/src/pages/families/structuralVariantSearch.ts).
_SEARCH = {
    "filters": {"type": "DEL", "minLength": "1000", "max_population_af": "0.01", "inheritance": "dominant"},
    "sample_filters": {
        "E2E_SV_PROBAND": {"gt": ["0/1", "1/0", "0|1", "1|0"], "qual": "", "read_support": "4", "filter": ""}
    },
    "sample_templates": {"E2E_SV_PROBAND": "proband", "E2E_SV_MOTHER": "mother"},
}
_CHANGED_SEARCH = {
    "filters": {"type": "DUP"},
    "sample_filters": {},
    "sample_templates": {"E2E_SV_PROBAND": "proband"},
}


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400 and resp.content:
        out["json"] = resp.json()
    return out


async def _stored(session, prefix: str) -> list[dict]:
    from sqlalchemy import text

    rows = await session.execute(
        text(
            """
            SELECT
                p.id::text AS id,
                f.family_id,
                p.scope,
                p.owner,
                p.name,
                p.description,
                p.filters,
                p.sample_filters,
                p.sample_templates
            FROM structural_variant_filter_presets p
            LEFT JOIN families f ON f.id = p.family_id
            WHERE p.name LIKE :prefix
            ORDER BY p.name, p.scope, f.family_id
            """
        ),
        {"prefix": f"{prefix}%"},
    )
    return [dict(row) for row in rows.mappings().all()]


async def _exercise(run: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.dependencies import get_password_hash
    from backend.app.main import app
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    families = {"a": f"E2E_SVPRESET_A_{run}", "b": f"E2E_SVPRESET_B_{run}"}
    viewer_email, viewer_password = f"e2e-svpreset-viewer-{run}@example.com", f"viewer-password-{run}"
    prefix = f"E2E SV preset {run}"
    family_uuids: dict[str, str] = {}
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)
        for key, family_id in families.items():
            family_uuids[key] = (
                await session.execute(
                    text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": family_id}
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO family_projects (family_id, project_id) "
                    "VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"
                ),
                {"f": family_uuids[key], "p": project_id},
            )
        viewer_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO users (username, hashed_password, role, email, metadata, created_at)
                    VALUES (:email, :hashed, 'viewer', :email, '{}'::jsonb, :created_at)
                    RETURNING id::text
                    """
                ),
                {
                    "email": viewer_email,
                    "hashed": get_password_hash(viewer_password),
                    "created_at": datetime.now(timezone.utc),
                },
            )
        ).scalar_one()
        await session.execute(
            text("INSERT INTO project_users (project_id, user_id) VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"),
            {"p": project_id, "u": viewer_id},
        )
        await session.commit()

    presets = {key: f"/api/families/{family}/structural-variant-filter-presets" for key, family in families.items()}
    names = {"shared": f"{prefix} dominant", "default": f"{prefix} default", "refused": f"{prefix} refused"}
    out: dict = {
        "run": run,
        "owner": settings.admin_username,
        "families": families,
        "family_uuids": family_uuids,
        "names": names,
    }
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac, AsyncClient(
            transport=transport, base_url="http://e2e"
        ) as viewer:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

            # The SV page's Save: the name and description typed, the scope the form sets.
            out["family_saved"] = _cap(
                await ac.post(
                    presets["a"],
                    json={"name": names["shared"], "description": "Rare deletions", "scope": "family", **_SEARCH},
                )
            )
            out["family_resaved"] = _cap(
                await ac.post(presets["a"], json={"name": f" {names['shared']} ", "scope": "family", **_CHANGED_SEARCH})
            )
            out["global_saved"] = _cap(
                await ac.post(
                    presets["a"],
                    json={"name": names["shared"], "description": "Every family", "scope": "global", **_SEARCH},
                )
            )
            out["default_saved"] = _cap(await ac.post(presets["b"], json={"name": names["default"], **_SEARCH}))
            out["refused"] = {
                "unknown_scope": _cap(
                    await ac.post(presets["a"], json={"name": names["refused"], "scope": "project", **_SEARCH})
                ),
                "blank_name": _cap(await ac.post(presets["a"], json={"name": "   ", "scope": "family", **_SEARCH})),
            }
            out["listed"] = {key: _cap(await ac.get(path)) for key, path in presets.items()}
            async with sessionmaker() as session:
                out["stored"] = await _stored(session, prefix)
                out["blank_stored"] = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM structural_variant_filter_presets "
                            "WHERE owner = :owner AND btrim(name) = ''"
                        ),
                        {"owner": settings.admin_username},
                    )
                ).scalar_one()

            login = await viewer.post("/api/auth/login", json={"email": viewer_email, "password": viewer_password})
            assert login.status_code == 200, login.text
            viewer.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            family_preset = (out["family_saved"].get("json") or {}).get("_id", str(uuid4()))
            global_preset = (out["global_saved"].get("json") or {}).get("_id", str(uuid4()))
            default_preset = (out["default_saved"].get("json") or {}).get("_id", str(uuid4()))
            out["viewer"] = {
                "listed": _cap(await viewer.get(presets["a"])),
                "delete": _cap(await viewer.delete(f"{presets['a']}/{family_preset}")),
            }

            out["deleted"] = {
                "family_through_other_family": _cap(await ac.delete(f"{presets['b']}/{family_preset}")),
                "family": _cap(await ac.delete(f"{presets['a']}/{family_preset}")),
                "family_again": _cap(await ac.delete(f"{presets['a']}/{family_preset}")),
                "global_through_other_family": _cap(await ac.delete(f"{presets['b']}/{global_preset}")),
                "default": _cap(await ac.delete(f"{presets['b']}/{default_preset}")),
            }
            out["listed_after"] = {key: _cap(await ac.get(path)) for key, path in presets.items()}
            async with sessionmaker() as session:
                out["stored_after"] = await _stored(session, prefix)
    finally:
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM structural_variant_filter_presets WHERE name LIKE :prefix"),
                {"prefix": f"{prefix}%"},
            )
            for family_uuid in family_uuids.values():
                await session.execute(
                    text("DELETE FROM families WHERE id = CAST(:id AS uuid)"), {"id": family_uuid}
                )
            await session.execute(text("DELETE FROM users WHERE email = :email"), {"email": viewer_email})
            await session.commit()
    return out


@pytest.fixture(scope="module")
def presets() -> dict:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    return _harness.run_async(lambda: _exercise(run))


def _served(preset: dict | None) -> dict | None:
    """The fields the SV page reads from a preset."""
    if preset is None:
        return None
    return {
        name: preset.get(name)
        for name in (
            "family_id",
            "scope",
            "owner",
            "name",
            "description",
            "filters",
            "sample_filters",
            "sample_templates",
        )
    }


def test_the_body_the_sv_page_sends_saves_a_preset_for_the_family(presets) -> None:
    saved = presets["family_saved"]
    assert saved["status"] == 200, saved
    assert _served(saved["json"]) == {
        "family_id": presets["family_uuids"]["a"],
        "scope": "family",
        "owner": presets["owner"],
        "name": presets["names"]["shared"],
        "description": "Rare deletions",
        **_SEARCH,
    }


def test_a_save_under_a_name_in_use_replaces_that_preset(presets) -> None:
    resaved = presets["family_resaved"]
    assert resaved["status"] == 200, resaved
    assert resaved["json"]["_id"] == presets["family_saved"]["json"]["_id"]
    # The name is trimmed; what the save leaves out, the description, is cleared.
    assert _served(resaved["json"]) == {
        "family_id": presets["family_uuids"]["a"],
        "scope": "family",
        "owner": presets["owner"],
        "name": presets["names"]["shared"],
        "description": None,
        **_CHANGED_SEARCH,
    }


def test_a_reusable_preset_under_the_same_name_is_a_preset_of_its_own(presets) -> None:
    saved = presets["global_saved"]
    assert saved["status"] == 200, saved
    assert saved["json"]["_id"] != presets["family_saved"]["json"]["_id"]
    assert _served(saved["json"]) == {
        "family_id": None,
        "scope": "global",
        "owner": presets["owner"],
        "name": presets["names"]["shared"],
        "description": "Every family",
        **_SEARCH,
    }


def test_a_preset_saved_without_a_scope_is_for_its_family(presets) -> None:
    saved = presets["default_saved"]
    assert saved["status"] == 200, saved
    assert saved["json"]["scope"] == "family"
    assert saved["json"]["family_id"] == presets["family_uuids"]["b"]


def test_a_family_lists_its_own_presets_then_the_reusable_ones_with_their_scope(presets) -> None:
    ids = {key: presets[f"{key}_saved"]["json"]["_id"] for key in ("family", "global", "default")}
    listed = {}
    for key, answer in presets["listed"].items():
        assert answer["status"] == 200, (key, answer)
        listed[key] = [(preset["_id"], preset["scope"], preset["family_id"]) for preset in answer["json"]]
    assert listed == {
        "a": [(ids["family"], "family", presets["family_uuids"]["a"]), (ids["global"], "global", None)],
        "b": [(ids["default"], "family", presets["family_uuids"]["b"]), (ids["global"], "global", None)],
    }


def test_postgres_holds_one_row_per_preset_as_saved(presets) -> None:
    owner, names, families = presets["owner"], presets["names"], presets["families"]
    assert presets["stored"] == [
        {
            "id": presets["default_saved"]["json"]["_id"],
            "family_id": families["b"],
            "scope": "family",
            "owner": owner,
            "name": names["default"],
            "description": None,
            **_SEARCH,
        },
        {
            "id": presets["family_saved"]["json"]["_id"],
            "family_id": families["a"],
            "scope": "family",
            "owner": owner,
            "name": names["shared"],
            "description": None,
            **_CHANGED_SEARCH,
        },
        {
            "id": presets["global_saved"]["json"]["_id"],
            "family_id": None,
            "scope": "global",
            "owner": owner,
            "name": names["shared"],
            "description": "Every family",
            **_SEARCH,
        },
    ]


def test_an_unknown_scope_and_a_blank_name_are_refused_and_store_nothing(presets) -> None:
    refused = presets["refused"]
    assert refused["unknown_scope"]["status"] == 422, refused
    assert refused["blank_name"]["status"] == 400, refused
    assert "Preset name cannot be blank" in refused["blank_name"]["text"]
    assert presets["names"]["refused"] not in {row["name"] for row in presets["stored"]}
    assert presets["blank_stored"] == 0


def test_another_user_neither_lists_nor_deletes_the_owners_presets(presets) -> None:
    viewer = presets["viewer"]
    assert viewer["listed"]["status"] == 200, viewer
    assert viewer["listed"]["json"] == []
    assert viewer["delete"]["status"] == 403, viewer
    assert "Not authorized to delete this preset" in viewer["delete"]["text"]


def test_a_preset_is_deleted_through_a_family_that_lists_it(presets) -> None:
    deleted = presets["deleted"]
    # A family's preset is not listed in another family, and is not deleted through it.
    assert deleted["family_through_other_family"]["status"] == 404, deleted
    assert deleted["family"]["status"] == 204, deleted
    assert deleted["family_again"]["status"] == 404, deleted
    # A reusable preset is listed in every family, and deleted through any of them.
    assert deleted["global_through_other_family"]["status"] == 204, deleted
    assert deleted["default"]["status"] == 204, deleted
    for key, answer in presets["listed_after"].items():
        assert answer["status"] == 200, (key, answer)
        assert answer["json"] == [], (key, answer)
    assert presets["stored_after"] == []
