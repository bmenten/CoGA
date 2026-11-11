"""Integration test: the Postgres schema migration, its ledger and the baselines' digest.

Each test runs on a database of its own, created and dropped here on the Postgres the other
integration tests use (``CREATE DATABASE`` needs the superuser that the CI service container
provides). Only Postgres is needed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py);
the CI ``smoke`` job sets it.

- An empty database ends with exactly the schema the five baseline files create: the catalogue
  fingerprint of a database the migration made (columns with type, nullability and default;
  constraints; indexes; triggers; functions; sequences; views; table, column, function,
  schema and default privileges; extensions; owners) equals that of a database where only
  the baseline files ran. The ledger is at the newest revision, the digest of the baselines is
  recorded, the runtime role may only read both, and a later start changes nothing.
- CoGA migrates no database it did not make: one with tables but no ledger, one whose ledger
  names a revision this version does not have, and one made from other baselines (its digest
  another, or none) are refused, and nothing changes.
- The restricted runtime role cannot run the migration; two instances that start together
  both come up, one after the other.
- A start that does not migrate reads the ledger and the digest as the restricted runtime role
  and accepts only this version's own revision and baselines: other baselines, a revision this
  version does not have, an empty ledger and none at all are each refused, and the database
  stays as it was.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from sqlalchemy import text

from backend.tests.integration._datastores import create_database, database_engine, drop_database

pytestmark = pytest.mark.integration

# Postgres catalogue queries whose rows, together, describe the schema in ``public``. Column
# order is left out: nothing in CoGA reads columns by position.
_FINGERPRINT_QUERIES = {
    "extensions": "SELECT extname, extversion FROM pg_extension",
    "relations": """
        SELECT c.relname, c.relkind::text, c.relpersistence::text, c.relrowsecurity,
               c.relforcerowsecurity, coalesce(array_to_string(c.reloptions, ','), ''),
               pg_get_userbyid(c.relowner)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
    """,
    "columns": """
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               coalesce(pg_get_expr(d.adbin, d.adrelid), ''), a.attidentity::text,
               a.attgenerated::text, coalesce(co.collname, '')
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        LEFT JOIN pg_collation co ON co.oid = a.attcollation
        WHERE n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
    """,
    "constraints": """
        SELECT c.relname, con.conname, con.contype::text, pg_get_constraintdef(con.oid),
               con.convalidated
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
    """,
    "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
    "triggers": """
        SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid), t.tgenabled::text
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT t.tgisinternal
    """,
    "functions": """
        SELECT p.proname, pg_get_function_identity_arguments(p.oid), p.prokind::text,
               CASE WHEN p.prokind IN ('f', 'p') THEN pg_get_functiondef(p.oid) ELSE '' END,
               pg_get_userbyid(p.proowner)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
    """,
    "sequences": """
        SELECT sequencename, data_type::text, start_value, min_value, max_value, increment_by,
               cycle, cache_size
        FROM pg_sequences WHERE schemaname = 'public'
    """,
    "views": "SELECT viewname, definition FROM pg_views WHERE schemaname = 'public'",
    "types": """
        SELECT t.typname, t.typtype::text, coalesce(string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder), '')
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        LEFT JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd')
        GROUP BY t.typname, t.typtype
    """,
    "policies": """
        SELECT tablename, policyname, permissive, array_to_string(roles, ','), cmd,
               coalesce(qual, ''), coalesce(with_check, '')
        FROM pg_policies WHERE schemaname = 'public'
    """,
    "comments": """
        SELECT c.relname, d.objsubid, d.description
        FROM pg_description d
        JOIN pg_class c ON c.oid = d.objoid AND d.classoid = 'pg_class'::regclass
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
    """,
    "table_privileges": """
        SELECT c.relname,
               CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
               a.privilege_type, a.is_grantable, pg_get_userbyid(a.grantor)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) a
        WHERE n.nspname = 'public'
    """,
    "column_privileges": """
        SELECT c.relname, att.attname,
               CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
               a.privilege_type, a.is_grantable
        FROM pg_attribute att
        JOIN pg_class c ON c.oid = att.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(att.attacl) a
        WHERE n.nspname = 'public' AND att.attnum > 0
    """,
    "function_privileges": """
        SELECT p.proname, pg_get_function_identity_arguments(p.oid),
               CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
               a.privilege_type, a.is_grantable
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        CROSS JOIN LATERAL aclexplode(p.proacl) a
        WHERE n.nspname = 'public'
    """,
    "schema_privileges": """
        SELECT n.nspname, pg_get_userbyid(n.nspowner),
               CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
               a.privilege_type, a.is_grantable
        FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
        WHERE n.nspname = 'public'
    """,
    "default_privileges": """
        SELECT pg_get_userbyid(d.defaclrole), coalesce(n.nspname, ''), d.defaclobjtype::text,
               CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
               a.privilege_type, a.is_grantable
        FROM pg_default_acl d
        LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
        CROSS JOIN LATERAL aclexplode(d.defaclacl) a
    """,
    "runtime_role": """
        SELECT rolname, rolcanlogin, rolinherit, rolcreaterole, rolcreatedb, rolsuper
        FROM pg_roles WHERE rolname = 'coga_app'
    """,
}

_engine = database_engine

# The configured database, where the test databases are created and dropped from. Read before
# a test points the settings at a database of its own (``_run``).
_ADMIN_DATABASE: list[str] = []


async def _create_database() -> str:
    return await create_database(_ADMIN_DATABASE[-1])


async def _drop_database(name: str) -> None:
    await drop_database(_ADMIN_DATABASE[-1], name)


async def _apply_baseline_files(database: str) -> None:
    """What the five baseline files alone create: each statement, in name order, once."""

    from backend.app.core.postgres import _schema_files, _split_sql_script

    engine = _engine(database)
    try:
        async with engine.begin() as conn:
            for path in _schema_files():
                for statement in _split_sql_script(path.read_text()):
                    await conn.exec_driver_sql(statement)
    finally:
        await engine.dispose()


async def _fingerprint(database: str) -> dict[str, list[tuple]]:
    engine = _engine(database)
    try:
        async with engine.connect() as conn:
            return {
                name: sorted(tuple(row) for row in (await conn.execute(text(query))).all())
                for name, query in _FINGERPRINT_QUERIES.items()
            }
    finally:
        await engine.dispose()


def _differences(expected: dict[str, list[tuple]], actual: dict[str, list[tuple]]) -> dict:
    return {
        name: {"missing": sorted(set(rows) - set(actual[name])), "extra": sorted(set(actual[name]) - set(rows))}
        for name, rows in expected.items()
        if rows != actual[name]
    }


async def _ledger(database: str) -> list[str]:
    engine = _engine(database)
    try:
        async with engine.connect() as conn:
            return [str(row[0]) for row in (await conn.execute(text("SELECT version_num FROM alembic_version"))).all()]
    finally:
        await engine.dispose()


async def _table_contents(database: str) -> dict[str, tuple[int, str]]:
    """Per table in ``public``: its row count and a digest of every row, to show nothing changed."""

    engine = _engine(database)
    try:
        async with engine.connect() as conn:
            tables = [
                str(row[0])
                for row in (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
                    )
                ).all()
            ]
            contents = {}
            for table in tables:
                assert re.fullmatch(r"[a-z0-9_]+", table)
                row = (
                    await conn.execute(
                        text(
                            f"SELECT count(*), coalesce(md5(string_agg(to_jsonb(t)::text, ',' "
                            f"ORDER BY to_jsonb(t)::text)), '') FROM {table} t"
                        )
                    )
                ).one()
                contents[table] = (int(row[0]), str(row[1]))
            return contents
    finally:
        await engine.dispose()


def _run(coroutine_factory):
    """Run one test's body on a database of its own, dropped afterwards."""

    from backend.app.core.config import settings

    _ADMIN_DATABASE.append(settings.postgres_db)

    async def _main():
        database = await _create_database()
        try:
            return await coroutine_factory(database)
        finally:
            await _drop_database(database)

    return asyncio.run(_main())


async def _baseline_digest_row(database: str) -> list[str]:
    engine = _engine(database)
    try:
        async with engine.connect() as conn:
            return [str(row[0]) for row in (await conn.execute(text("SELECT digest FROM schema_baselines"))).all()]
    finally:
        await engine.dispose()


async def _migrate(database: str) -> None:
    from backend.app.core.postgres_migrations import apply_postgres_schema

    engine = _engine(database)
    try:
        async with engine.begin() as conn:
            await apply_postgres_schema(conn)
    finally:
        await engine.dispose()


async def _refused(database: str) -> str:
    """The migration's refusal on ``database``."""

    from backend.app.core.postgres_migrations import SchemaMigrationRefused

    with pytest.raises(SchemaMigrationRefused) as refused:
        await _migrate(database)
    return str(refused.value)


def test_an_empty_database_ends_with_exactly_the_schema_the_baseline_files_create(monkeypatch) -> None:
    from backend.app.core import postgres
    from backend.app.core.config import settings
    from backend.app.core.postgres_migrations import baseline_digest, head_revision

    async def _body(migrated: str):
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            # The real start: init_postgres_schema on the configured database.
            monkeypatch.setattr(settings, "postgres_db", migrated)
            await postgres.close_postgres_engine()
            try:
                await postgres.init_postgres_schema()
                made = await _table_contents(migrated)
                await postgres.init_postgres_schema()  # every later start: nothing to do
            finally:
                await postgres.close_postgres_engine()
            return (
                await _fingerprint(reference),
                await _fingerprint(migrated),
                await _ledger(migrated),
                await _baseline_digest_row(migrated),
                made,
                await _table_contents(migrated),
            )
        finally:
            await _drop_database(reference)

    expected, actual, ledger, digest, made, after_a_later_start = _run(_body)

    assert actual == expected, _differences(expected, actual)
    assert ledger == [head_revision()]
    assert digest == [baseline_digest()]
    assert after_a_later_start == made
    # The fingerprint is not empty: the comparison covers the whole schema.
    assert len(expected["columns"]) > 500 and len(expected["triggers"]) >= 5
    for table in ("alembic_version", "schema_baselines"):
        assert (table, "coga_app", "SELECT", False) in {row[:4] for row in expected["table_privileges"]}
        assert not {
            row[2] for row in expected["table_privileges"] if row[0] == table and row[1] == "coga_app"
        } - {"SELECT"}, table


@pytest.mark.parametrize("tables", ["the baselines' own", "another application's"])
def test_a_database_with_tables_but_no_ledger_is_refused_and_nothing_changes(tables: str) -> None:
    async def _body(database: str):
        if tables == "the baselines' own":
            await _apply_baseline_files(database)  # every table, an empty ledger and no digest
        else:
            engine = _engine(database)
            try:
                async with engine.begin() as conn:
                    await conn.exec_driver_sql("CREATE TABLE stray (id integer PRIMARY KEY)")
                    await conn.exec_driver_sql("INSERT INTO stray VALUES (1)")
            finally:
                await engine.dispose()
        before = await _table_contents(database)
        message = await _refused(database)
        return message, before, await _table_contents(database)

    message, before, after = _run(_body)

    assert "table(s) in its public schema but no schema ledger (alembic_version)" in message
    assert "point it at an empty one" in message
    assert after == before


@pytest.mark.parametrize("digest", ["another", "none"])
def test_a_database_made_from_other_baselines_is_refused_and_nothing_changes(digest: str) -> None:
    from backend.app.core.postgres_migrations import baseline_digest

    async def _body(database: str):
        await _migrate(database)
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                if digest == "another":
                    await conn.execute(text("UPDATE schema_baselines SET digest = repeat('0', 64)"))
                else:
                    await conn.execute(text("DELETE FROM schema_baselines"))
                # Standing in for the other baselines' tables: one this version would make.
                await conn.exec_driver_sql("DROP INDEX idx_repeat_expansions_locus")
        finally:
            await engine.dispose()
        before = await _table_contents(database)
        message = await _refused(database)
        return message, before, await _table_contents(database)

    message, before, after = _run(_body)

    recorded = "0" * 64 if digest == "another" else "none"
    assert f"made from other baseline files than this version's (schema_baselines records {recorded}" in message
    assert f"this version has {baseline_digest()}" in message
    assert after == before


def test_a_ledger_this_version_does_not_have_is_refused_and_nothing_changes() -> None:
    async def _body(database: str):
        await _migrate(database)
        engine = _engine(database)
        try:
            # As another version left it: a revision this one does not have, and (standing in
            # for its other changes) an index without the one the baselines make.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '9999'"))
                await conn.exec_driver_sql("DROP INDEX idx_repeat_expansions_locus")
        finally:
            await engine.dispose()
        before = await _table_contents(database)
        message = await _refused(database)
        return message, before, await _table_contents(database), await _ledger(database)

    message, before, after, ledger = _run(_body)

    assert "schema revision 9999" in message and "another version made or migrated it" in message
    assert ledger == ["9999"]
    assert after == before


def test_the_runtime_role_cannot_run_the_migration() -> None:
    from backend.app.core.postgres_migrations import SchemaMigrationRefused, apply_postgres_schema

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            with pytest.raises(SchemaMigrationRefused) as refused:
                async with engine.begin() as conn:
                    await conn.execute(text("SET LOCAL ROLE coga_app"))
                    await apply_postgres_schema(conn)
            return str(refused.value)
        finally:
            await engine.dispose()

    message = _run(_body)

    assert "not as coga_app" in message


def test_two_instances_starting_together_both_come_up() -> None:
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    async def _body(database: str):
        engines = [_engine(database), _engine(database)]

        async def _start(engine):
            async with engine.begin() as conn:
                return await apply_postgres_schema(conn)

        try:
            revisions = await asyncio.gather(*(_start(engine) for engine in engines))
        finally:
            for engine in engines:
                await engine.dispose()
        return revisions, await _ledger(database)

    revisions, ledger = _run(_body)

    # One migrated the new database; the other waited for it, and found nothing left to do.
    assert len(revisions) == 2
    assert {(revision.before, revision.after) for revision in revisions} == {
        (None, head_revision()),
        (head_revision(), head_revision()),
    }
    assert ledger == [head_revision()]


def test_a_start_that_does_not_migrate_serves_only_on_its_own_revision_and_baselines() -> None:
    # The API as the restricted runtime role (POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false):
    # the migration job runs before the rollout, and the start only reads the ledger and digest.
    from backend.app.core.postgres_migrations import SchemaRevisionMismatch, check_schema_revision, head_revision

    # Each state the owner leaves, from the migrated database on, and what coga_app's start reads.
    # They follow one another on the same database.
    states: dict[str, list[str]] = {
        "own revision and baselines": [],
        "other baselines": ["UPDATE schema_baselines SET digest = repeat('0', 64)"],
        "a revision this version does not have": ["UPDATE alembic_version SET version_num = '9999'"],
        "empty ledger": ["DELETE FROM alembic_version"],
        "no ledger": ["DROP TABLE alembic_version"],
    }

    async def _body(database: str):
        await _migrate(database)
        engine = _engine(database)
        outcomes = {}
        try:
            for case, statements in states.items():
                async with engine.begin() as conn:
                    for statement in statements:
                        await conn.exec_driver_sql(statement)
                before = await _table_contents(database)
                async with engine.begin() as conn:
                    await conn.execute(text("SET LOCAL ROLE coga_app"))
                    role = (await conn.execute(text("SELECT current_user"))).scalar_one()
                    try:
                        outcome = await conn.run_sync(check_schema_revision)
                    except SchemaRevisionMismatch as refused:
                        outcome = f"refused: {refused}"
                outcomes[case] = (role, outcome, before == await _table_contents(database))
            return outcomes
        finally:
            await engine.dispose()

    outcomes = _run(_body)

    assert {role for role, _outcome, _kept in outcomes.values()} == {"coga_app"}
    # The start only read the database.
    assert all(kept for _role, _outcome, kept in outcomes.values())
    assert outcomes["own revision and baselines"][1] == head_revision()
    assert outcomes["other baselines"][1].startswith(
        "refused: The database was made from other baseline files than this version's (schema_baselines), "
        "so the backend does not start"
    )
    other = outcomes["a revision this version does not have"][1]
    assert other.startswith("refused: The database is at schema revision 9999, which this version of CoGA does not have")
    assert "another version made or migrated it" in other
    behind = f"and this version of CoGA needs revision {head_revision()}"
    for case in ("empty ledger", "no ledger"):
        assert outcomes[case][1].startswith(f"refused: The database is at schema revision none (no ledger), {behind}"), case
        assert "Run the migration job first" in outcomes[case][1], case
