"""A project's deletion keeps its review tags, inactive and detached; a family lists the tags its
reviews hold (SAFE-37, real Postgres).

The deletion of a project deleted the review tags defined for it (``ON DELETE CASCADE``), while
the families, their reviews, the signed records and the clinical audit trail, which hold a
tag's key, all stayed: the reviews showed the bare key, and a new tag with the old label got
the same key back, so every record that held it read it as the new tag.

Now ``delete_project_record`` deactivates the project's tags, under the definitions lock, and
deletes the project in the same transaction; the foreign key sets ``project_id`` to NULL. The
test makes, with synthetic names, project A (deleted), project B and project C, and the tags:

* ``a_shared``, A's, shared with B; ``a_only``, A's alone; ``b_shared``, B's, shared with A;
  ``c_held``, C's, held by a review of a family that left C; ``c_unheld``, C's, held by none;
  ``everywhere``, a global tag;
* family DEL, in A alone, whose small-variant review holds ``a_only`` and its compound-het pair
  ``a_shared``; family LEFT, now in B alone, whose repeat review holds ``c_held``, its Paraphase
  review ``everywhere`` and its SV review ``b_shared``.

A project that a family is linked to is not deleted (409): the family's variants are stored per
project (the owner's decision, 2026-11-10). So the deletion of A, with DEL linked to it, is
refused and changes nothing; DEL is then moved to no project, as an administrator's change of
its projects does, and A is deleted. The test checks that:

* the deletion of A while DEL is linked to it is refused (409, naming 1 family) and leaves A's
  tags active;
* ``a_shared`` and ``a_only`` are kept, inactive, project tags without a project; ``a_shared``
  keeps its link to B; ``b_shared`` stays active and loses only its link to A;
* no list reads them as global or active: the global list, the administration's list, B's
  active list; B's list with the inactive tags shows ``a_shared`` inactive, as after a delete;
* a new tag with ``a_only``'s label gets a numbered key, never ``a_only``'s;
* the check refuses a global tag with a project and an active project tag without one, and a
  project deleted without deactivating its active tag first (the deletion fails, nothing goes);
* DEL's tag list (a family in no project, which a superuser opens) holds ``a_only`` and
  ``a_shared``, inactive, with label and colour and no project; LEFT's (an analyst of B) holds
  ``c_held`` inactive and ``b_shared`` active; neither holds ``c_unheld``; without the inactive
  tags neither lists a held tag.

Everything the test made is removed at the end. Skipped unless ``RUN_INTEGRATION=1`` (see
conftest.py).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

_COLOURS = {
    "a_shared": "#aa0011",
    "a_only": "#aa0022",
    "b_shared": "#bb0033",
    "c_held": "#cc0044",
    "c_unheld": "#cc0055",
    "everywhere": "#dd0066",
}


def _user(role: str, projects: list[str] | None = None) -> Any:
    from backend.app.services.access_control import CurrentUser

    return CurrentUser(
        id=str(uuid4()),
        username=f"{role}.safe37",
        email=f"{role}.safe37@example.org",
        role=role,
        metadata_project_ids=projects or [],
        created_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
    )


async def _stored(session: Any, keys: list[str]) -> dict[str, dict[str, Any]]:
    rows = await session.execute(
        text(
            """
            SELECT d.key, d.scope, d.project_id::text AS project_id, d.is_active,
                   COALESCE(ARRAY_AGG(l.project_id::text ORDER BY l.project_id)
                            FILTER (WHERE l.project_id IS NOT NULL), '{}') AS links
            FROM small_variant_tag_definitions d
            LEFT JOIN small_variant_tag_definition_project_links l ON l.tag_id = d.id
            WHERE d.key = ANY(:keys)
            GROUP BY d.id
            """
        ),
        {"keys": keys},
    )
    return {str(row["key"]): dict(row) for row in rows.mappings().all()}


def _listed(tags: list[Any]) -> dict[str, dict[str, Any]]:
    return {
        tag.key: {
            "label": tag.label,
            "color": tag.color,
            "is_active": tag.is_active,
            "scope": tag.scope,
            "project_id": tag.project_id,
            "shared_project_ids": tag.shared_project_ids,
        }
        for tag in tags
        if tag.is_custom
    }


_CHECK = "small_variant_tag_definitions_scope_project_check"


async def _refused(sm: Any, statement: str, params: dict[str, Any]) -> str | None:
    """What refused the statement (the check, by name, or the error), or None when it was stored."""
    try:
        async with sm() as session:
            await session.execute(text(statement), params)
            await session.commit()
    except IntegrityError as error:
        return _CHECK if _CHECK in str(error.orig) else str(error.orig)
    return None


async def _run() -> dict[str, Any]:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.routers.families_small_variants import list_small_variant_tags
    from backend.app.schemas import SmallVariantTagDefinitionCreate
    from backend.app.services.metadata_service import delete_project_record
    from backend.app.services.small_variant_review_tags import (
        create_small_variant_tag_definition,
        list_small_variant_tag_definitions,
    )

    run = uuid4().hex[:10]
    admin = _user("admin")
    out: dict[str, Any] = {"run": run}
    made: dict[str, Any] = {"projects": [], "families": [], "keys": []}
    try:
        await init_postgres_schema()
        sm = get_postgres_sessionmaker()
        async with sm() as session:
            made["species"] = (
                await session.execute(
                    text(
                        "INSERT INTO species (name, common_name, tax_id) "
                        "VALUES (:n, 'Test organism', :t) RETURNING id::text"
                    ),
                    {"n": f"safe37-{run}", "t": 800_000_000 + int(run[:6], 16) % 99_999_999},
                )
            ).scalar_one()
            made["assembly"] = (
                await session.execute(
                    text(
                        "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                        "VALUES (CAST(:s AS uuid), :n, 'test', DATE '2026-01-01') RETURNING id::text"
                    ),
                    {"s": made["species"], "n": f"SAFE37{run}"},
                )
            ).scalar_one()
            for name in ("A", "B", "C", "D"):
                made["projects"].append(
                    (
                        await session.execute(
                            text(
                                "INSERT INTO projects (name, species_id, assembly_id) "
                                "VALUES (:n, CAST(:s AS uuid), CAST(:a AS uuid)) RETURNING id::text"
                            ),
                            {"n": f"safe37-{name}-{run}", "s": made["species"], "a": made["assembly"]},
                        )
                    ).scalar_one()
                )
            await session.commit()
        project_a, project_b, project_c, project_d = made["projects"]
        out["projects"] = {"A": project_a, "B": project_b, "C": project_c}

        keys: dict[str, str] = {}
        for name, scope, primary, shared in (
            ("a_shared", "project", project_a, [project_b]),
            ("a_only", "project", project_a, []),
            ("b_shared", "project", project_b, [project_a]),
            ("c_held", "project", project_c, []),
            ("c_unheld", "project", project_c, []),
            ("everywhere", "global", None, []),
            ("d_active", "project", project_d, []),
        ):
            async with sm() as session:
                created = await create_small_variant_tag_definition(
                    session,
                    family_uuid="",
                    payload=SmallVariantTagDefinitionCreate(
                        label=f"Safe37 {name} {run}",
                        color=_COLOURS.get(name, "#123456"),
                        scope=scope,
                        project_id=primary,
                        shared_project_ids=shared,
                    ),
                    user=admin,
                )
            keys[name] = created.key
            made["keys"].append(created.key)
        out["keys"] = keys

        async with sm() as session:
            family_uuids = {}
            for name, project in (("DEL", project_a), ("LEFT", project_c)):
                family_uuids[name] = (
                    await session.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": f"SAFE37_{name}_{run}"},
                    )
                ).scalar_one()
                made["families"].append(family_uuids[name])
                await session.execute(
                    text(
                        "INSERT INTO family_projects (family_id, project_id) "
                        "VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"
                    ),
                    {"f": family_uuids[name], "p": project},
                )
            # LEFT moves from C to B: C's tags are no longer the family's to use.
            await session.execute(
                text("DELETE FROM family_projects WHERE family_id = CAST(:f AS uuid)"),
                {"f": family_uuids["LEFT"]},
            )
            await session.execute(
                text(
                    "INSERT INTO family_projects (family_id, project_id) "
                    "VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"
                ),
                {"f": family_uuids["LEFT"], "p": project_b},
            )
            await session.execute(
                text(
                    "INSERT INTO small_variant_reviews (family_id, variant_id, tags, compound_het_tags, updated_by) "
                    "VALUES (CAST(:f AS uuid), '1-1000-A-G', CAST(:t AS jsonb), CAST(:c AS jsonb), 'analyst')"
                ),
                {
                    "f": family_uuids["DEL"],
                    "t": f'["{keys["a_only"]}", "report"]',
                    "c": f'["{keys["a_shared"]}"]',
                },
            )
            await session.execute(
                text(
                    "INSERT INTO repeat_expansion_reviews (family_id, assembly_id, locus_id, tags, updated_by) "
                    "VALUES (CAST(:f AS uuid), CAST(:a AS uuid), 'HTT', CAST(:t AS jsonb), 'analyst')"
                ),
                {"f": family_uuids["LEFT"], "a": made["assembly"], "t": f'["{keys["c_held"]}"]'},
            )
            await session.execute(
                text(
                    "INSERT INTO paraphase_reviews (family_id, assembly_id, gene_symbol, tags, updated_by) "
                    "VALUES (CAST(:f AS uuid), CAST(:a AS uuid), 'SMN1', CAST(:t AS jsonb), 'analyst')"
                ),
                {"f": family_uuids["LEFT"], "a": made["assembly"], "t": f'["{keys["everywhere"]}"]'},
            )
            await session.execute(
                text(
                    "INSERT INTO structural_variant_reviews (family_id, variant_id, tags, updated_by) "
                    "VALUES (CAST(:f AS uuid), 'sv-1', CAST(:t AS jsonb), 'analyst')"
                ),
                {"f": family_uuids["LEFT"], "t": f'["{keys["b_shared"]}"]'},
            )
            await session.commit()

        # Refused while DEL is linked to A, with nothing changed; DEL then leaves A for no project.
        async with sm() as session:
            try:
                await delete_project_record(session, project_id=project_a)
            except HTTPException as refused:
                out["refused_with_family"] = (refused.status_code, refused.detail)
            await session.rollback()
        async with sm() as session:
            out["while_refused"] = await _stored(session, [keys["a_only"], keys["a_shared"]])
            await session.execute(
                text("DELETE FROM family_projects WHERE family_id = CAST(:f AS uuid)"),
                {"f": family_uuids["DEL"]},
            )
            await session.commit()
        async with sm() as session:
            await delete_project_record(session, project_id=project_a)
        async with sm() as session:
            out["stored"] = await _stored(session, list(keys.values()))
            out["project_a_left"] = (
                await session.execute(
                    text("SELECT count(*) FROM projects WHERE id = CAST(:p AS uuid)"), {"p": project_a}
                )
            ).scalar_one()
            out["lists"] = {
                "global": _listed(await list_small_variant_tag_definitions(session, family_uuid="", project_ids=[])),
                "global_inactive": _listed(
                    await list_small_variant_tag_definitions(
                        session, family_uuid="", project_ids=[], include_inactive=True
                    )
                ),
                "admin": _listed(
                    await list_small_variant_tag_definitions(
                        session, family_uuid="", project_ids=[], include_all_project_tags=True
                    )
                ),
                "b": _listed(await list_small_variant_tag_definitions(session, family_uuid="", project_ids=[project_b])),
                "b_inactive": _listed(
                    await list_small_variant_tag_definitions(
                        session, family_uuid="", project_ids=[project_b], include_inactive=True
                    )
                ),
            }

        # The family tag lists, through the route the review dialogs and cards read.
        analyst_b = _user("analyst", [project_b])
        family_lists: dict[str, Any] = {}
        for name, user in (("DEL", admin), ("LEFT", analyst_b)):
            for include_inactive in (True, False):
                async with sm() as session:
                    family_lists[(name, include_inactive)] = _listed(
                        await list_small_variant_tags(
                            family_id=f"SAFE37_{name}_{run}",
                            project_id=None,
                            include_inactive=include_inactive,
                            session=session,
                            user=user,
                        )
                    )
        out["family_lists"] = family_lists

        # A new tag with the label of a tag the deletion kept gets a key of its own.
        async with sm() as session:
            again = await create_small_variant_tag_definition(
                session,
                family_uuid="",
                payload=SmallVariantTagDefinitionCreate(
                    label=f"Safe37 a_only {run}", color="#010203", scope="project", project_id=project_b
                ),
                user=admin,
            )
        made["keys"].append(again.key)
        out["again_key"] = again.key

        # The check: a global tag has no project, an active project tag has one.
        out["refused"] = {
            "global_with_project": await _refused(
                sm,
                "INSERT INTO small_variant_tag_definitions (key, label, scope, project_id, created_by) "
                "VALUES (:k, 'Safe37 refused', 'global', CAST(:p AS uuid), 'test')",
                {"k": f"safe37_refused_global_{run}", "p": project_b},
            ),
            "active_project_tag_without_project": await _refused(
                sm,
                "INSERT INTO small_variant_tag_definitions (key, label, scope, project_id, created_by) "
                "VALUES (:k, 'Safe37 refused', 'project', NULL, 'test')",
                {"k": f"safe37_refused_project_{run}"},
            ),
            "detached_tag_made_active": await _refused(
                sm,
                "UPDATE small_variant_tag_definitions SET is_active = TRUE WHERE key = :k",
                {"k": keys["a_only"]},
            ),
            # A deletion that skips the deactivation fails whole: the project and its tag stay.
            "raw_project_delete_with_active_tag": await _refused(
                sm, "DELETE FROM projects WHERE id = CAST(:p AS uuid)", {"p": project_d}
            ),
        }
        async with sm() as session:
            out["after_raw_delete"] = (
                (
                    await session.execute(
                        text("SELECT count(*) FROM projects WHERE id = CAST(:p AS uuid)"), {"p": project_d}
                    )
                ).scalar_one(),
                (await _stored(session, [keys["d_active"]]))[keys["d_active"]],
            )
    finally:
        sm = get_postgres_sessionmaker()
        async with sm() as session:
            for family_uuid in made["families"]:
                await session.execute(
                    text("DELETE FROM families WHERE id = CAST(:f AS uuid)"), {"f": family_uuid}
                )
            if made["keys"]:
                await session.execute(
                    text("DELETE FROM small_variant_tag_definitions WHERE key = ANY(:k)"), {"k": made["keys"]}
                )
            for project_id in made["projects"]:
                await session.execute(
                    text("DELETE FROM projects WHERE id = CAST(:p AS uuid)"), {"p": project_id}
                )
            if made.get("assembly"):
                await session.execute(
                    text("DELETE FROM assemblies WHERE id = CAST(:a AS uuid)"), {"a": made["assembly"]}
                )
            if made.get("species"):
                await session.execute(
                    text("DELETE FROM species WHERE id = CAST(:s AS uuid)"), {"s": made["species"]}
                )
            await session.commit()
        await close_postgres_engine()
    return out


@pytest.fixture(scope="module")
def deleted() -> dict[str, Any]:
    return asyncio.run(_run())


def test_a_project_a_family_is_linked_to_is_refused_and_keeps_its_tags_active(deleted) -> None:
    status, detail = deleted["refused_with_family"]
    assert status == 409
    assert detail.startswith("1 family is linked to this project.")
    for name in ("a_only", "a_shared"):
        assert deleted["while_refused"][deleted["keys"][name]]["is_active"] is True
        assert deleted["while_refused"][deleted["keys"][name]]["project_id"] == deleted["projects"]["A"]


def test_the_deleted_projects_tags_are_kept_inactive_without_a_project(deleted) -> None:
    keys, stored, projects = deleted["keys"], deleted["stored"], deleted["projects"]

    assert deleted["project_a_left"] == 0
    for name in ("a_shared", "a_only"):
        assert stored[keys[name]] == {
            "key": keys[name],
            "scope": "project",
            "project_id": None,
            "is_active": False,
            "links": [projects["B"]] if name == "a_shared" else [],
        }
    # A tag of another project loses only its link to the deleted one.
    assert stored[keys["b_shared"]] == {
        "key": keys["b_shared"],
        "scope": "project",
        "project_id": projects["B"],
        "is_active": True,
        "links": [],
    }
    assert stored[keys["c_held"]]["is_active"] is True
    assert stored[keys["everywhere"]]["is_active"] is True


def test_no_list_reads_a_kept_tag_as_global_or_active(deleted) -> None:
    keys, lists = deleted["keys"], deleted["lists"]
    kept = {keys["a_shared"], keys["a_only"]}

    for name in ("global", "global_inactive", "admin", "b"):
        assert not kept & set(lists[name]), name
    assert keys["everywhere"] in lists["global"]
    # Still listed, inactive, in the project it was shared with, as after a delete.
    assert lists["b_inactive"][keys["a_shared"]]["is_active"] is False
    assert lists["b_inactive"][keys["a_shared"]]["scope"] == "project"
    assert keys["a_only"] not in lists["b_inactive"]


def test_a_new_tag_with_a_kept_tags_label_gets_a_key_of_its_own(deleted) -> None:
    keys = deleted["keys"]

    assert deleted["again_key"] != keys["a_only"]
    assert deleted["again_key"] == f"{keys['a_only']}_2"


def test_the_check_ties_a_global_tag_to_no_project_and_an_active_project_tag_to_one(deleted) -> None:
    refused = deleted["refused"]
    check = _CHECK

    assert refused["global_with_project"] == check
    assert refused["active_project_tag_without_project"] == check
    assert refused["detached_tag_made_active"] == check
    # The deletion that did not deactivate first is refused whole: the project and its tag stay.
    assert refused["raw_project_delete_with_active_tag"] == check
    count, tag = deleted["after_raw_delete"]
    assert count == 1
    assert tag["is_active"] is True and tag["project_id"] is not None


def test_a_family_lists_the_tags_its_reviews_hold_inactive_with_their_label_and_colour(deleted) -> None:
    keys, run, lists = deleted["keys"], deleted["run"], deleted["family_lists"]

    # DEL lost its only project with the deletion; a superuser opens it.
    family = lists[("DEL", True)]
    for name in ("a_only", "a_shared"):
        assert family[keys[name]] == {
            "label": f"Safe37 {name} {run}",
            "color": _COLOURS[name],
            "is_active": False,
            "scope": "project",
            "project_id": None,
            "shared_project_ids": [],
        }
    # LEFT left C for B: C's tag its review holds is listed inactive; B's stays active.
    family = lists[("LEFT", True)]
    assert family[keys["c_held"]] == {
        "label": f"Safe37 c_held {run}",
        "color": _COLOURS["c_held"],
        "is_active": False,
        "scope": "project",
        "project_id": None,
        "shared_project_ids": [],
    }
    assert family[keys["b_shared"]]["is_active"] is True
    assert family[keys["b_shared"]]["project_id"] == deleted["projects"]["B"]
    assert family[keys["everywhere"]]["is_active"] is True


def test_a_family_lists_no_tag_its_reviews_do_not_hold(deleted) -> None:
    keys, lists = deleted["keys"], deleted["family_lists"]

    for listed in lists.values():
        assert keys["c_unheld"] not in listed
    assert keys["c_held"] not in lists[("DEL", True)]
    assert keys["a_only"] not in lists[("LEFT", True)]
    # Without the inactive tags, a held tag is not listed either: none is offered for new use.
    assert keys["a_only"] not in lists[("DEL", False)] and keys["a_shared"] not in lists[("DEL", False)]
    assert keys["c_held"] not in lists[("LEFT", False)]
    for name in ("DEL", "LEFT"):
        assert all(tag["is_active"] for tag in lists[(name, False)].values()), name
