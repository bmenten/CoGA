"""Variant tags: an edit is saved, and a renamed or deleted tag stays on the reviews that hold it
(E2E, real Postgres; the renames over the golden trio, with ClickHouse).

**An edit is saved, through the admin route and the family route.**
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

**A renamed or deleted tag stays on the reviews that hold it.**
A label edit derived a new key from the new label, so the reviews kept the old one, and a
deleted tag left its key on them too. A review save refused every key outside the family's
tag definitions with 400, and the quick tag toggle and the review dialog send the stored
tags back: every later save of such a variant failed, the one removing the tag included,
and the review-tag filters lost the variants tagged before a rename.

A tag's key is now its identity: a label edit changes the label only, and a review save
checks only the tags it adds. Over the golden trio (FAM_TRIO) the test creates two custom
tags and puts both on a small variant's review, on a compound-het pair and on an SV's
review (the family's SV review summary, the SV page's counts, counts that SV, its note and
each tag once), then:

* renames the first: its key stays, the stored reviews are untouched, the review-tag
  filters still find the tagged variants, and a label that reads as another active tag's
  or a built-in's is refused;
* deletes the second: it leaves the default tag list and is listed, inactive, on request;
  each review holding it still saves, with it and without it, while adding it to a review,
  again or anew, is refused;
* creates tags with the renamed tag's old label and the deleted tag's label: each gets a
  numbered key of its own and takes over no review.

Each request runs over HTTP through an in-process ``httpx.ASGITransport`` client, as the
seeded admin, on one event loop (see test_e2e_api_contract.py for why). The tags are the
test's own, with synthetic labels; they and the reviews the test wrote are removed at the
end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from backend.tests.e2e._harness import capture, ensure_postgres_schema

pytestmark = pytest.mark.integration

_ADMIN_TAGS = "/api/admin/variant-tags"
_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_TAGS = f"/api/families/{FAMILY}/small-variant-tags"
_SMALL = f"/api/families/{FAMILY}/small-variants"
_SVS = f"/api/families/{FAMILY}/structural-variants"
# The GENE_BENIGN SNV; the GENE_CH pair, both heterozygous in the affected proband; and an
# SNV no save may tag (each attempt is refused, so nothing is stored for it).
_TAGGED = "1-1000-A-G"
_PAIR = ("1-4000-T-C", "1-5000-A-T")
_UNTAGGED = "1-8000-AC-GT"


# --- An edit is saved (the admin route and the family route) ---------------------------------


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


async def _edit_a_shared_tag(run: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.main import app
    from backend.tests.e2e import _harness

    await ensure_postgres_schema()
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

            out["created"] = capture(
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
            out["admin_edit"] = capture(
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
            out["family_edit"] = capture(
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
                "admin": capture(await ac.put(f"{_ADMIN_TAGS}/{unknown}", json={"color": "#000000"})),
                "family": capture(await ac.put(f"{family_tags}/{unknown}", json={"color": "#000000"})),
            }
            out["no_field"] = {
                "admin": capture(await ac.put(admin_path, json={})),
                "family": capture(await ac.put(family_path, json={})),
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
    return _harness.run_async(lambda: _edit_a_shared_tag(run))


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


# --- A renamed or deleted tag stays on the reviews that hold it ---------------------------------


def _by_key(resp: dict) -> dict[str, dict]:
    return {tag["key"]: tag for tag in resp.get("json") or []}


def _ids(resp: dict) -> list[str]:
    return sorted(variant["_id"] for variant in (resp.get("json") or {}).get("variants", []))


async def _stored_tags(family_uuid: str, sv_ids: list[str]) -> dict:
    """The tag lists the reviews hold in Postgres, by variant."""
    from sqlalchemy import bindparam, text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        small = (
            await session.execute(
                text(
                    """
                    SELECT variant_id, tags, compound_het_tags
                    FROM small_variant_reviews
                    WHERE family_id = CAST(:family AS uuid) AND variant_id IN :ids
                    """
                ).bindparams(bindparam("ids", expanding=True)),
                {"family": family_uuid, "ids": [_TAGGED, *_PAIR, _UNTAGGED]},
            )
        ).mappings().all()
        structural = (
            await session.execute(
                text(
                    """
                    SELECT variant_id, tags
                    FROM structural_variant_reviews
                    WHERE family_id = CAST(:family AS uuid) AND variant_id IN :ids
                    """
                ).bindparams(bindparam("ids", expanding=True)),
                {"family": family_uuid, "ids": sv_ids or [""]},
            )
        ).mappings().all()
    return {
        "small": {
            row["variant_id"]: {"tags": row["tags"], "compound_het_tags": row["compound_het_tags"]}
            for row in small
        },
        "sv": {row["variant_id"]: row["tags"] for row in structural},
    }


async def _remove_what_the_test_wrote(family_uuid: str, sv_ids: list[str], run: str) -> None:
    """The reviews of the variants the test tags, and its tags. Idempotent."""
    from sqlalchemy import bindparam, text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text(
                "DELETE FROM small_variant_reviews "
                "WHERE family_id = CAST(:family AS uuid) AND variant_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"family": family_uuid, "ids": [_TAGGED, *_PAIR, _UNTAGGED]},
        )
        if sv_ids:
            await session.execute(
                text(
                    "DELETE FROM structural_variant_reviews "
                    "WHERE family_id = CAST(:family AS uuid) AND variant_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"family": family_uuid, "ids": sv_ids},
            )
        await session.execute(
            text("DELETE FROM small_variant_tag_definitions WHERE key LIKE :pattern"),
            {"pattern": f"probe_%_{run}%"},
        )
        await session.commit()


async def _rename_and_delete_tags(root: Path, run: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    out: dict = {"facts": facts, "run": run}
    r: dict = {}
    out["responses"] = r
    renamed_label = f"Probe rename {run}"
    deleted_label = f"Probe delete {run}"
    sv_ids: list[str] = []
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        sv_ids = _ids(capture(await ac.get(_SVS, params={"page_size": 50})))
        out["sv_ids"] = sv_ids
        await _remove_what_the_test_wrote(family_uuid, sv_ids, run)
        try:
            # Two global custom tags.
            for name, label in (("renamed", renamed_label), ("deleted", deleted_label)):
                r[f"create_{name}"] = capture(
                    await ac.post(
                        _ADMIN_TAGS,
                        json={"label": label, "scope": "global", "group": "custom", "color": "#AA3366"},
                    )
                )
            renamed = (r["create_renamed"].get("json") or {}).get("key", "")
            deleted = (r["create_deleted"].get("json") or {}).get("key", "")
            out["keys"] = {"renamed": renamed, "deleted": deleted}
            both = sorted([renamed, deleted])
            sv = sv_ids[0] if sv_ids else ""
            other_sv = sv_ids[-1] if len(sv_ids) > 1 else ""

            # Both on a small variant's review (with a built-in tag), on a compound-het pair
            # and on an SV's review.
            tagged = sorted([*both, "review"])
            r["tag_small"] = capture(
                await ac.put(f"{_SMALL}/{_TAGGED}/review", json={"tags": tagged, "note": "E2E: tagged"})
            )
            r["tag_pair"] = capture(
                await ac.put(
                    f"{_SMALL}/{_PAIR[0]}/review",
                    json={"compound_het": {"partner_variant_id": _PAIR[1], "tags": both}},
                )
            )
            r["tag_sv"] = capture(await ac.put(f"{_SVS}/{sv}/review", json={"tags": both, "note": "E2E: tagged"}))
            out["stored_before_rename"] = await _stored_tags(family_uuid, sv_ids)
            # The family's SV review summary (its counts on the SV page) counts the one
            # review the family's SVs hold now.
            r["sv_review_summary"] = capture(
                await ac.get(f"/api/families/{FAMILY}/structural-variant-review-summary")
            )

            # The rename.
            r["rename"] = capture(await ac.put(f"{_ADMIN_TAGS}/{renamed}", json={"label": f"Probe renamed {run}"}))
            r["rename_onto_other_label"] = capture(
                await ac.put(f"{_ADMIN_TAGS}/{renamed}", json={"label": deleted_label.upper()})
            )
            r["rename_onto_built_in_label"] = capture(
                await ac.put(f"{_ADMIN_TAGS}/{renamed}", json={"label": "Pathogenic - class 5"})
            )
            r["listed_after_rename"] = capture(await ac.get(_TAGS))
            out["stored_after_rename"] = await _stored_tags(family_uuid, sv_ids)
            # The filters search by the key the tag list now serves for the tag, as the
            # filter forms do.
            served = (r["rename"].get("json") or {}).get("key", "")
            r["filter_small_renamed"] = capture(await ac.get(_SMALL, params={"review_tag": served, "page_size": 50}))
            r["filter_sv_renamed"] = capture(await ac.get(_SVS, params={"review_tag": served, "page_size": 50}))
            # What the quick toggle sends: the stored tags, one built-in toggled.
            r["toggle_after_rename"] = capture(
                await ac.put(
                    f"{_SMALL}/{_TAGGED}/review",
                    json={"tags": sorted([*tagged, "excluded"]), "note": "E2E: tagged"},
                )
            )

            # The delete.
            r["delete"] = capture(await ac.delete(f"{_ADMIN_TAGS}/{deleted}"))
            r["listed_after_delete"] = capture(await ac.get(_TAGS))
            r["listed_inactive"] = capture(await ac.get(_TAGS, params={"include_inactive": True}))
            r["explorer_inactive"] = capture(
                await ac.get("/api/variant-explorer/small-variant-tags", params={"include_inactive": True})
            )
            r["admin_after_delete"] = capture(await ac.get(_ADMIN_TAGS))
            # Each review holding the deleted tag saves, sending the stored tags back...
            r["toggle_after_delete"] = capture(
                await ac.put(
                    f"{_SMALL}/{_TAGGED}/review",
                    json={"tags": tagged, "note": "E2E: tagged, saved again"},
                )
            )
            r["pair_after_delete"] = capture(
                await ac.put(
                    f"{_SMALL}/{_PAIR[0]}/review",
                    json={
                        "compound_het": {
                            "partner_variant_id": _PAIR[1],
                            "tags": both,
                            "note": "E2E: pair saved again",
                        }
                    },
                )
            )
            r["sv_after_delete"] = capture(
                await ac.put(f"{_SVS}/{sv}/review", json={"tags": both, "note": "E2E: tagged, saved again"})
            )
            out["stored_after_delete"] = await _stored_tags(family_uuid, sv_ids)
            # ...but it is added to no review: not anew, and not again once removed.
            r["add_deleted_small"] = capture(
                await ac.put(f"{_SMALL}/{_UNTAGGED}/review", json={"tags": [deleted], "note": "E2E"})
            )
            r["add_deleted_sv"] = capture(
                await ac.put(f"{_SVS}/{other_sv}/review", json={"tags": [deleted], "note": "E2E"})
            )
            r["remove_deleted_small"] = capture(
                await ac.put(
                    f"{_SMALL}/{_TAGGED}/review",
                    json={"tags": sorted([renamed, "review"]), "note": "E2E: tagged, saved again"},
                )
            )
            r["re_add_deleted_small"] = capture(
                await ac.put(f"{_SMALL}/{_TAGGED}/review", json={"tags": tagged, "note": "E2E: tagged, saved again"})
            )
            out["stored_at_end"] = await _stored_tags(family_uuid, sv_ids)

            # New tags with the renamed tag's old label and the deleted tag's label get keys
            # of their own; one that reads as an active tag's label is refused.
            r["create_old_label"] = capture(
                await ac.post(_ADMIN_TAGS, json={"label": renamed_label, "scope": "global", "group": "custom"})
            )
            r["create_deleted_label"] = capture(
                await ac.post(_ADMIN_TAGS, json={"label": deleted_label, "scope": "global", "group": "custom"})
            )
            r["create_active_label"] = capture(
                await ac.post(
                    _ADMIN_TAGS, json={"label": f"probe RENAMED {run}!", "scope": "global", "group": "custom"}
                )
            )
            reused = (r["create_deleted_label"].get("json") or {}).get("key", "")
            r["filter_small_reused"] = capture(await ac.get(_SMALL, params={"review_tag": reused, "page_size": 50}))
        finally:
            await _remove_what_the_test_wrote(family_uuid, sv_ids, run)
    return out


@pytest.fixture(scope="module")
def tagged(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_tag_keys") / FAMILY
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    run = uuid4().hex[:8]
    out = _harness.run_async(lambda: _rename_and_delete_tags(root, run))
    assert out["facts"]["completed"] is True, out["facts"]
    return out


def _resp(tagged: dict, name: str) -> dict:
    return tagged["responses"][name]


def _ok(tagged: dict, *names: str) -> None:
    for name in names:
        resp = _resp(tagged, name)
        assert resp["status"] in (200, 204), (name, resp)


def test_the_tags_are_created_with_keys_from_their_labels(tagged) -> None:
    _ok(tagged, "create_renamed", "create_deleted", "tag_small", "tag_pair", "tag_sv")
    run = tagged["run"]
    assert tagged["keys"] == {"renamed": f"probe_rename_{run}", "deleted": f"probe_delete_{run}"}
    assert len(tagged["sv_ids"]) == 2, tagged["sv_ids"]


def test_the_sv_review_summary_counts_the_tagged_svs_review(tagged) -> None:
    # The SV page's counts: one SV reviewed, with a note and both tags.
    summary = _resp(tagged, "sv_review_summary")
    assert summary["status"] == 200, summary["text"]
    keys = tagged["keys"]
    assert summary["json"] == {
        "reviewed_variant_count": 1,
        "note_count": 1,
        "tag_counts": {keys["renamed"]: 1, keys["deleted"]: 1},
    }


def test_a_rename_keeps_the_key_and_the_reviews_holding_it(tagged) -> None:
    _ok(tagged, "rename")
    renamed = tagged["keys"]["renamed"]
    run = tagged["run"]
    assert (_resp(tagged, "rename")["json"]["key"], _resp(tagged, "rename")["json"]["label"]) == (
        renamed,
        f"Probe renamed {run}",
    )
    listed = _by_key(_resp(tagged, "listed_after_rename"))
    assert listed[renamed]["label"] == f"Probe renamed {run}"
    assert not any(key.startswith(f"probe_renamed_{run}") for key in listed)
    # Nothing a review stores changes.
    assert tagged["stored_after_rename"] == tagged["stored_before_rename"]
    stored = tagged["stored_after_rename"]
    assert renamed in stored["small"][_TAGGED]["tags"]
    assert all(renamed in stored["small"][variant]["compound_het_tags"] for variant in _PAIR)
    assert renamed in stored["sv"][tagged["sv_ids"][0]]


def test_the_review_tag_filters_find_the_variants_tagged_before_the_rename(tagged) -> None:
    assert _resp(tagged, "filter_small_renamed")["status"] == 200, _resp(tagged, "filter_small_renamed")
    assert _ids(_resp(tagged, "filter_small_renamed")) == sorted([_TAGGED, *_PAIR])
    assert _resp(tagged, "filter_sv_renamed")["status"] == 200, _resp(tagged, "filter_sv_renamed")
    assert _ids(_resp(tagged, "filter_sv_renamed")) == [tagged["sv_ids"][0]]


def test_a_label_that_reads_as_another_tags_is_refused(tagged) -> None:
    for name in ("rename_onto_other_label", "rename_onto_built_in_label", "create_active_label"):
        resp = _resp(tagged, name)
        assert resp["status"] == 409, (name, resp)


def test_a_review_holding_a_renamed_tag_saves(tagged) -> None:
    _ok(tagged, "toggle_after_rename")
    saved = _resp(tagged, "toggle_after_rename")["json"]["tags"]
    assert saved == sorted([*tagged["keys"].values(), "excluded", "review"])


def test_a_deleted_tag_is_listed_only_on_request_and_marked_inactive(tagged) -> None:
    _ok(tagged, "delete", "listed_after_delete", "listed_inactive", "explorer_inactive", "admin_after_delete")
    deleted = tagged["keys"]["deleted"]
    renamed = tagged["keys"]["renamed"]
    assert deleted not in _by_key(_resp(tagged, "listed_after_delete"))
    assert deleted not in _by_key(_resp(tagged, "admin_after_delete"))
    for name in ("listed_inactive", "explorer_inactive"):
        listed = _by_key(_resp(tagged, name))
        assert (listed[deleted]["label"], listed[deleted]["is_active"]) == (
            f"Probe delete {tagged['run']}",
            False,
        ), name
        assert listed[renamed]["is_active"] is True
        assert all(tag["is_active"] for key, tag in listed.items() if not tag["is_custom"])


def test_every_review_holding_a_deleted_tag_still_saves(tagged) -> None:
    # The bug: each of these answered 400 "Unknown … tag(s)" naming the deleted key.
    _ok(tagged, "toggle_after_delete", "pair_after_delete", "sv_after_delete")
    deleted = tagged["keys"]["deleted"]
    stored = tagged["stored_after_delete"]
    assert deleted in stored["small"][_TAGGED]["tags"]
    assert all(deleted in stored["small"][variant]["compound_het_tags"] for variant in _PAIR)
    assert deleted in stored["sv"][tagged["sv_ids"][0]]


def test_a_deleted_tag_is_removed_from_a_review_and_cannot_be_added_back(tagged) -> None:
    _ok(tagged, "remove_deleted_small")
    deleted = tagged["keys"]["deleted"]
    for name, kind in (
        ("add_deleted_small", "small-variant"),
        ("add_deleted_sv", "structural-variant"),
        ("re_add_deleted_small", "small-variant"),
    ):
        resp = _resp(tagged, name)
        assert resp["status"] == 400, (name, resp)
        assert f"Unknown {kind} tag(s): {deleted}" in resp["text"], (name, resp)
    stored = tagged["stored_at_end"]
    assert stored["small"][_TAGGED]["tags"] == sorted([tagged["keys"]["renamed"], "review"])
    assert _UNTAGGED not in stored["small"]
    assert tagged["sv_ids"][-1] not in stored["sv"]


def test_a_new_tag_never_takes_over_an_old_tags_key(tagged) -> None:
    _ok(tagged, "create_old_label", "create_deleted_label")
    keys = tagged["keys"]
    assert _resp(tagged, "create_old_label")["json"]["key"] == f"{keys['renamed']}_2"
    assert _resp(tagged, "create_deleted_label")["json"]["key"] == f"{keys['deleted']}_2"
    # The new tag carries none of the deleted tag's reviews.
    assert _resp(tagged, "filter_small_reused")["status"] == 200, _resp(tagged, "filter_small_reused")
    assert _ids(_resp(tagged, "filter_small_reused")) == []
