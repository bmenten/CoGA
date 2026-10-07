"""A renamed or deleted variant tag stays on the reviews that hold it (E2E, Postgres + ClickHouse).

A label edit derived a new key from the new label, so the reviews kept the old one, and a
deleted tag left its key on them too. A review save refused every key outside the family's
tag definitions with 400, and the quick tag toggle and the review dialog send the stored
tags back: every later save of such a variant failed, the one removing the tag included,
and the review-tag filters lost the variants tagged before a rename.

A tag's key is now its identity: a label edit changes the label only, and a review save
checks only the tags it adds. Over the golden trio (FAM_TRIO) the test creates two custom
tags and puts both on a small variant's review, on a compound-het pair and on an SV's
review, then:

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

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_ADMIN_TAGS = "/api/admin/variant-tags"
_TAGS = f"/api/families/{FAMILY}/small-variant-tags"
_SMALL = f"/api/families/{FAMILY}/small-variants"
_SVS = f"/api/families/{FAMILY}/structural-variants"
# The GENE_BENIGN SNV; the GENE_CH pair, both heterozygous in the affected proband; and an
# SNV no save may tag (each attempt is refused, so nothing is stored for it).
_TAGGED = "1-1000-A-G"
_PAIR = ("1-4000-T-C", "1-5000-A-T")
_UNTAGGED = "1-8000-AC-GT"


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400 and resp.content:
        out["json"] = resp.json()
    return out


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


async def _exercise(root: Path, run: str) -> dict:
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
        sv_ids = _ids(_cap(await ac.get(_SVS, params={"page_size": 50})))
        out["sv_ids"] = sv_ids
        await _remove_what_the_test_wrote(family_uuid, sv_ids, run)
        try:
            # Two global custom tags.
            for name, label in (("renamed", renamed_label), ("deleted", deleted_label)):
                r[f"create_{name}"] = _cap(
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
            r["tag_small"] = _cap(
                await ac.put(f"{_SMALL}/{_TAGGED}/review", json={"tags": tagged, "note": "E2E: tagged"})
            )
            r["tag_pair"] = _cap(
                await ac.put(
                    f"{_SMALL}/{_PAIR[0]}/review",
                    json={"compound_het": {"partner_variant_id": _PAIR[1], "tags": both}},
                )
            )
            r["tag_sv"] = _cap(await ac.put(f"{_SVS}/{sv}/review", json={"tags": both, "note": "E2E: tagged"}))
            out["stored_before_rename"] = await _stored_tags(family_uuid, sv_ids)

            # The rename.
            r["rename"] = _cap(await ac.put(f"{_ADMIN_TAGS}/{renamed}", json={"label": f"Probe renamed {run}"}))
            r["rename_onto_other_label"] = _cap(
                await ac.put(f"{_ADMIN_TAGS}/{renamed}", json={"label": deleted_label.upper()})
            )
            r["rename_onto_built_in_label"] = _cap(
                await ac.put(f"{_ADMIN_TAGS}/{renamed}", json={"label": "Pathogenic - class 5"})
            )
            r["listed_after_rename"] = _cap(await ac.get(_TAGS))
            out["stored_after_rename"] = await _stored_tags(family_uuid, sv_ids)
            # The filters search by the key the tag list now serves for the tag, as the
            # filter forms do.
            served = (r["rename"].get("json") or {}).get("key", "")
            r["filter_small_renamed"] = _cap(await ac.get(_SMALL, params={"review_tag": served, "page_size": 50}))
            r["filter_sv_renamed"] = _cap(await ac.get(_SVS, params={"review_tag": served, "page_size": 50}))
            # What the quick toggle sends: the stored tags, one built-in toggled.
            r["toggle_after_rename"] = _cap(
                await ac.put(
                    f"{_SMALL}/{_TAGGED}/review",
                    json={"tags": sorted([*tagged, "excluded"]), "note": "E2E: tagged"},
                )
            )

            # The delete.
            r["delete"] = _cap(await ac.delete(f"{_ADMIN_TAGS}/{deleted}"))
            r["listed_after_delete"] = _cap(await ac.get(_TAGS))
            r["listed_inactive"] = _cap(await ac.get(_TAGS, params={"include_inactive": True}))
            r["explorer_inactive"] = _cap(
                await ac.get("/api/variant-explorer/small-variant-tags", params={"include_inactive": True})
            )
            r["admin_after_delete"] = _cap(await ac.get(_ADMIN_TAGS))
            # Each review holding the deleted tag saves, sending the stored tags back...
            r["toggle_after_delete"] = _cap(
                await ac.put(
                    f"{_SMALL}/{_TAGGED}/review",
                    json={"tags": tagged, "note": "E2E: tagged, saved again"},
                )
            )
            r["pair_after_delete"] = _cap(
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
            r["sv_after_delete"] = _cap(
                await ac.put(f"{_SVS}/{sv}/review", json={"tags": both, "note": "E2E: tagged, saved again"})
            )
            out["stored_after_delete"] = await _stored_tags(family_uuid, sv_ids)
            # ...but it is added to no review: not anew, and not again once removed.
            r["add_deleted_small"] = _cap(
                await ac.put(f"{_SMALL}/{_UNTAGGED}/review", json={"tags": [deleted], "note": "E2E"})
            )
            r["add_deleted_sv"] = _cap(
                await ac.put(f"{_SVS}/{other_sv}/review", json={"tags": [deleted], "note": "E2E"})
            )
            r["remove_deleted_small"] = _cap(
                await ac.put(
                    f"{_SMALL}/{_TAGGED}/review",
                    json={"tags": sorted([renamed, "review"]), "note": "E2E: tagged, saved again"},
                )
            )
            r["re_add_deleted_small"] = _cap(
                await ac.put(f"{_SMALL}/{_TAGGED}/review", json={"tags": tagged, "note": "E2E: tagged, saved again"})
            )
            out["stored_at_end"] = await _stored_tags(family_uuid, sv_ids)

            # New tags with the renamed tag's old label and the deleted tag's label get keys
            # of their own; one that reads as an active tag's label is refused.
            r["create_old_label"] = _cap(
                await ac.post(_ADMIN_TAGS, json={"label": renamed_label, "scope": "global", "group": "custom"})
            )
            r["create_deleted_label"] = _cap(
                await ac.post(_ADMIN_TAGS, json={"label": deleted_label, "scope": "global", "group": "custom"})
            )
            r["create_active_label"] = _cap(
                await ac.post(
                    _ADMIN_TAGS, json={"label": f"probe RENAMED {run}!", "scope": "global", "group": "custom"}
                )
            )
            reused = (r["create_deleted_label"].get("json") or {}).get("key", "")
            r["filter_small_reused"] = _cap(await ac.get(_SMALL, params={"review_tag": reused, "page_size": 50}))
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
    out = _harness.run_async(lambda: _exercise(root, run))
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
