"""A member or structure edit refuses an ID no sample can be stored under, and writes nothing (E2E).

A family or sample ID is printable text without spaces (``family_identifiers.py``). The member
edits and the structure edit take sample IDs from the request: a member's new ID, its father
and mother, the members the structure edit adds. Before, these were only stripped: a rename to
an ID with a tab or a space, a batch rename with a line break, a member added with an escape or
a space was stored, and one holding a NUL failed (500) when Postgres refused it.

Over the real API (an in-process ``httpx.ASGITransport`` client that answers an app failure
with 500, see test_e2e_import_refuses_unstorable_ids.py), Postgres and ClickHouse, on a trio
built with the Family Builder (synthetic IDs):

* renaming a member (``PUT /api/families/{family}/members/{sample}``) to an ID with a tab, a
  NUL or a space, or naming a father with an escape, is refused (400);
* a batch edit (``PUT /api/families/{family}/members/batch``) whose second rename holds a line
  break is refused (400), and its first, valid, rename is not written;
* a structure edit (``PUT /api/families/{family}/structure``) adding a member with an escape,
  a NUL or a space is refused (400);
* the family's members, sample IDs, relationships, pedigree and structure versions are what
  they were, and no sample was added; the same routes then store a valid rename and a valid
  added member, each without the whitespace around its ID, so the refusals are the IDs'.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

NUL, TAB, LF, ESC = "\x00", "\t", "\n", "\x1b"
# IDs no other test uses: a sample ID is unique across families.
FAMILY = "FAMEDIT01"
FATHER, MOTHER, CHILD = "EDITFATHER01", "EDITMOTHER01", "EDITCHILD01"


def _cap(resp: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


async def _state() -> dict[str, Any]:
    """What a refused edit may not change: the family's members and their sample IDs, its
    relationships (inactive ones too), its pedigree and structure versions, the samples."""
    from backend.app.core.postgres import get_postgres_sessionmaker

    family = {"family_id": FAMILY}
    async with get_postgres_sessionmaker()() as session:
        members = (
            await session.execute(
                text(
                    "SELECT s.sample_id, fm.role, fm.active FROM family_members fm "
                    "JOIN samples s ON s.id = fm.sample_id JOIN families f ON f.id = fm.family_id "
                    "WHERE f.family_id = :family_id ORDER BY s.sample_id"
                ),
                family,
            )
        ).all()
        relationships = (
            await session.execute(
                text(
                    "SELECT fr.relationship_type, sa.sample_id, sb.sample_id, fr.role_a, fr.active "
                    "FROM family_relationships fr JOIN families f ON f.id = fr.family_id "
                    "JOIN samples sa ON sa.id = fr.sample_id_a JOIN samples sb ON sb.id = fr.sample_id_b "
                    "WHERE f.family_id = :family_id ORDER BY 1, 2, 3, 5"
                ),
                family,
            )
        ).all()
        row = (
            await session.execute(
                text(
                    "SELECT f.pedigree, (SELECT count(*) FROM family_structure_versions v "
                    "WHERE v.family_id = f.id) AS versions, (SELECT count(*) FROM samples) AS samples "
                    "FROM families f WHERE f.family_id = :family_id"
                ),
                family,
            )
        ).mappings().one()
    return {
        "members": [tuple(member) for member in members],
        "relationships": [tuple(relationship) for relationship in relationships],
        **dict(row),
    }


async def _collect() -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    # A rename first asks ClickHouse whether variant calls still name the member, and is
    # refused when it cannot tell: the valid rename below needs the tables.
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    out: dict[str, Any] = {}
    # A request the app fails on is answered 500, as a server answers it, so that every step
    # below is taken and asserted on its own.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        # overwrite: a second run against the same database starts from the same trio.
        out["created"] = _cap(
            await ac.post(
                "/api/ped/manual",
                params={"overwrite": "true"},
                json={
                    "family_id": FAMILY,
                    "project_id": project_id,
                    "members": [
                        {"sample_id": FATHER, "sex": "male"},
                        {"sample_id": MOTHER, "sex": "female"},
                        {"sample_id": CHILD, "sex": "female", "father_id": FATHER, "mother_id": MOTHER, "is_proband": True},
                    ],
                },
            )
        )
        out["before"] = await _state()

        member = f"/api/families/{FAMILY}/members/{CHILD}"
        batch = f"/api/families/{FAMILY}/members/batch"
        structure = f"/api/families/{FAMILY}/structure"
        out["rename_tab"] = _cap(await ac.put(member, json={"sample_id": f"EDITCHILD{TAB}02"}))
        out["rename_nul"] = _cap(await ac.put(member, json={"sample_id": f"EDITCHILD{NUL}02"}))
        out["rename_space"] = _cap(await ac.put(member, json={"sample_id": "EDITCHILD 02"}))
        out["father_esc"] = _cap(await ac.put(member, json={"father_id": f"EDITFATHER{ESC}01"}))
        out["batch_lf"] = _cap(
            await ac.put(
                batch,
                json={
                    "updates": [
                        {"sample_id": FATHER, "new_sample_id": "EDITFATHER02"},
                        {"sample_id": MOTHER, "new_sample_id": f"EDITMOTHER{LF}02"},
                    ]
                },
            )
        )
        for name, sample_id in (("add_esc", f"EDITSIB{ESC}01"), ("add_nul", f"EDITSIB{NUL}01"), ("add_space", "EDITSIB 01")):
            out[name] = _cap(
                await ac.put(structure, json={"add_members": [{"sample_id": sample_id, "sex": "female", "role": "sibling"}]})
            )
        out["after"] = await _state()

        out["valid_rename"] = _cap(await ac.put(batch, json={"updates": [{"sample_id": CHILD, "new_sample_id": " EDITCHILD02 "}]}))
        out["valid_add"] = _cap(
            await ac.put(structure, json={"add_members": [{"sample_id": " EDITSIB01 ", "sex": "female", "role": "sibling"}]})
        )
        out["valid"] = await _state()
    return out


@pytest.fixture(scope="module")
def snap() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    return _harness.run_async(_collect)


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("rename_tab", "Sample ID 'EDITCHILD\\t02' contains a control character (\\t)."),
        ("rename_nul", "Sample ID 'EDITCHILD\\x0002' contains a control character (\\x00)."),
        ("rename_space", "Sample ID 'EDITCHILD 02' contains a space."),
        ("father_esc", "Father ID 'EDITFATHER\\x1b01' contains a control character (\\x1b)."),
        ("batch_lf", "Sample ID 'EDITMOTHER\\n02' contains a control character (\\n)."),
        ("add_esc", "Sample ID 'EDITSIB\\x1b01' contains a control character (\\x1b)."),
        ("add_nul", "Sample ID 'EDITSIB\\x0001' contains a control character (\\x00)."),
        ("add_space", "Sample ID 'EDITSIB 01' contains a space."),
    ],
)
def test_an_edit_giving_a_member_an_id_no_sample_can_have_is_refused(snap: dict[str, Any], name: str, shown: str) -> None:
    from backend.app.services.family_identifiers import IDENTIFIER_RULE

    assert snap["created"]["status"] == 200, snap["created"]["text"]
    response = snap[name]
    assert response["status"] == 400, (name, response["text"])
    assert response["json"]["detail"] == f"{shown} {IDENTIFIER_RULE}", (name, response["json"])


def test_a_refused_edit_writes_nothing(snap: dict[str, Any]) -> None:
    # The batch's first rename (EDITFATHER02) is not written either.
    assert snap["after"] == snap["before"]
    assert [member[0] for member in snap["before"]["members"]] == [CHILD, FATHER, MOTHER]


def test_the_same_routes_store_a_valid_id_without_the_whitespace_around_it(snap: dict[str, Any]) -> None:
    for name in ("valid_rename", "valid_add"):
        assert snap[name]["status"] == 200, (name, snap[name]["text"])
    valid = snap["valid"]
    assert [member[0] for member in valid["members"]] == ["EDITCHILD02", FATHER, MOTHER, "EDITSIB01"]
    assert valid["samples"] == snap["before"]["samples"] + 1
    lines = valid["pedigree"].splitlines()
    assert f"{FAMILY} EDITCHILD02 {FATHER} {MOTHER} 2 0" in lines
    assert f"{FAMILY} EDITSIB01 0 0 2 0" in lines
