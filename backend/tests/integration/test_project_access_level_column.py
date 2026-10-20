"""Every project membership has an access level, an analyst's unless set (SEC-1).

``project_users.access_level`` is ``analyst`` (read and change the project's records) or
``reader`` (read them only); a membership made without one is an analyst's, and any other
value is refused. The baselines are re-applied on every start, and their one upgrade statement
adds the column to a database made before it: each membership there becomes an analyst's, as
every member could change the project's records until then, with the same default and check
the ``CREATE TABLE`` gives a new database.

The test makes a user and a project of its own, reads the column as a new database has it,
then drops it, as a database made before it lacks it, re-applies the baselines and reads the
memberships again. It ends with the column in place and removes what it made.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the values are synthetic.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

_COLUMN = """
    SELECT column_default, is_nullable, data_type
    FROM information_schema.columns
    WHERE table_schema = current_schema() AND table_name = 'project_users' AND column_name = 'access_level'
"""
_CHECKS = """
    SELECT conname, pg_get_constraintdef(oid) AS definition
    FROM pg_constraint
    WHERE conrelid = 'project_users'::regclass AND contype = 'c'
    ORDER BY conname
"""


async def _shape(session: Any) -> dict[str, Any]:
    column = (await session.execute(text(_COLUMN))).mappings().first()
    checks = [tuple(row) for row in (await session.execute(text(_CHECKS))).all()]
    return {"column": dict(column) if column else None, "checks": checks}


async def _levels(session: Any, project_id: str) -> dict[str, str]:
    rows = await session.execute(
        text("SELECT user_id::text, access_level FROM project_users WHERE project_id = CAST(:p AS uuid)"),
        {"p": project_id},
    )
    return {str(user_id): str(level) for user_id, level in rows.all()}


async def _run() -> dict[str, Any]:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema

    run = uuid4().hex[:12]
    out: dict[str, Any] = {}
    made: dict[str, Any] = {}
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
                    {"n": f"access-level-{run}", "t": 900_000_000 + int(run[:6], 16) % 99_999_999},
                )
            ).scalar_one()
            made["assembly"] = (
                await session.execute(
                    text(
                        "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                        "VALUES (CAST(:s AS uuid), :n, 'test', DATE '2026-01-01') RETURNING id::text"
                    ),
                    {"s": made["species"], "n": f"ACCESS{run}"},
                )
            ).scalar_one()
            made["project"] = (
                await session.execute(
                    text(
                        "INSERT INTO projects (name, species_id, assembly_id) "
                        "VALUES (:n, CAST(:s AS uuid), CAST(:a AS uuid)) RETURNING id::text"
                    ),
                    {"n": f"access-level-{run}", "s": made["species"], "a": made["assembly"]},
                )
            ).scalar_one()
            made["users"] = [
                (
                    await session.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:e, 'not-a-hash', 'viewer', :e) RETURNING id::text"
                        ),
                        {"e": f"{who}-{run}@example.org"},
                    )
                ).scalar_one()
                for who in ("analyst", "reader")
            ]
            analyst, reader = made["users"]
            await session.execute(
                text("INSERT INTO project_users (project_id, user_id) VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"),
                {"p": made["project"], "u": analyst},
            )
            await session.execute(
                text(
                    "INSERT INTO project_users (project_id, user_id, access_level) "
                    "VALUES (CAST(:p AS uuid), CAST(:u AS uuid), 'reader')"
                ),
                {"p": made["project"], "u": reader},
            )
            await session.commit()
            out["new_shape"] = await _shape(session)
            out["new_levels"] = await _levels(session, made["project"])

        # Any other level is refused.
        with pytest.raises(DBAPIError) as refused:
            async with sm() as session:
                await session.execute(
                    text("UPDATE project_users SET access_level = 'admin' WHERE project_id = CAST(:p AS uuid)"),
                    {"p": made["project"]},
                )
                await session.commit()
        out["refused"] = "project_users_access_level_check" in str(refused.value)

        # A database made before the column: drop it, start again, read the memberships.
        async with sm() as session:
            await session.execute(text("ALTER TABLE project_users DROP COLUMN access_level"))
            await session.commit()
            out["dropped_shape"] = await _shape(session)
        await init_postgres_schema()
        async with sm() as session:
            out["upgraded_shape"] = await _shape(session)
            out["upgraded_levels"] = await _levels(session, made["project"])
        # Applied again, the baselines change nothing.
        await init_postgres_schema()
        async with sm() as session:
            out["reapplied_shape"] = await _shape(session)
    finally:
        # Whatever happened above, the column is back.
        await init_postgres_schema()
        sm = get_postgres_sessionmaker()
        async with sm() as session:
            if made.get("project"):
                await session.execute(text("DELETE FROM projects WHERE id = CAST(:p AS uuid)"), {"p": made["project"]})
            for user_id in made.get("users", []):
                await session.execute(text("DELETE FROM users WHERE id = CAST(:u AS uuid)"), {"u": user_id})
            if made.get("assembly"):
                await session.execute(text("DELETE FROM assemblies WHERE id = CAST(:a AS uuid)"), {"a": made["assembly"]})
            if made.get("species"):
                await session.execute(text("DELETE FROM species WHERE id = CAST(:s AS uuid)"), {"s": made["species"]})
            await session.commit()
        await close_postgres_engine()
    out["users"] = made["users"]
    return out


@pytest.fixture(scope="module")
def column() -> dict[str, Any]:
    return asyncio.run(_run())


_SHAPE = {
    "column": {"column_default": "'analyst'::text", "is_nullable": "NO", "data_type": "text"},
    "checks": [
        (
            "project_users_access_level_check",
            "CHECK ((access_level = ANY (ARRAY['analyst'::text, 'reader'::text])))",
        )
    ],
}


def test_a_membership_is_an_analysts_unless_it_says_reader(column: dict[str, Any]) -> None:
    analyst, reader = column["users"]
    assert column["new_shape"] == _SHAPE
    assert column["new_levels"] == {analyst: "analyst", reader: "reader"}


def test_any_other_level_is_refused(column: dict[str, Any]) -> None:
    assert column["refused"] is True


def test_a_database_made_before_the_column_gains_it_with_every_member_an_analyst(column: dict[str, Any]) -> None:
    analyst, reader = column["users"]
    assert column["dropped_shape"] == {"column": None, "checks": []}
    # The same column and check a new database has; each membership an analyst's.
    assert column["upgraded_shape"] == _SHAPE
    assert column["upgraded_levels"] == {analyst: "analyst", reader: "analyst"}
    assert column["reapplied_shape"] == _SHAPE
