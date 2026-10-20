"""Integration test: the Postgres schema migration, its ledger and its revisions (ENG-3).

Each test runs on a database of its own, created and dropped here on the Postgres the other
integration tests use (``CREATE DATABASE`` needs the superuser that the CI service container
provides). Only Postgres is needed. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py);
the CI ``smoke`` job sets it.

- A new database ends with exactly the schema the five baseline files create: the catalogue
  fingerprint of a database the migration built (columns with type, nullability and default;
  constraints; indexes; triggers; functions; sequences; views; table, column, function,
  schema and default privileges; extensions; owners) equals that of a database where only
  the baseline files ran, and the ledger is at the newest revision.
- A database the baselines created before the ledger existed, with the checks from before
  #747, #749 and #826, is brought under the ledger at the next start, keeps every row, ends at
  the same fingerprint, and then stores a TRGT call at a locus without thresholds.
- Both hold until the release candidate, when every run re-applies the baselines first, and
  after its freeze, when only the revisions run and revision 0001 applies the baselines.
- A database at an older revision gets only the revisions it lacks.
- A ledger naming a revision this version does not have is refused, and nothing changes.
- The restricted runtime role cannot run the migration; two instances that start together
  both come up, one after the other.
"""

from __future__ import annotations

import asyncio
import logging
import re
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

pytestmark = pytest.mark.integration

# Postgres catalogue queries whose rows, together, describe the schema in ``public``. Column
# order is left out: a column a revision adds goes last, where the baseline may place it
# elsewhere, and nothing in CoGA reads columns by position.
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

# The checks as a database created before #747, #749 and #826 has them.
_OLD_CHECKS = {
    ("repeat_expansions", "repeat_expansions_status_check"): (
        "CHECK (status = ANY (ARRAY['normal'::text, 'review'::text, 'intermediate'::text, "
        "'premutation'::text, 'pathogenic'::text, 'unknown'::text]))"
    ),
    ("family_relationships", "family_relationships_relationship_type_check"): (
        "CHECK (relationship_type = ANY (ARRAY['parent_child'::text, 'couple'::text]))"
    ),
    ("sample_interval_track_sources", "sample_interval_track_sources_track_type_check"): (
        "CHECK (track_type = ANY (ARRAY['coverage'::text, 'apcad'::text, 'apcad_pcf'::text, "
        "'segments'::text, 'haplotype'::text]))"
    ),
}

_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##trgtVersion=5.0.0\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
)
# A TRID no catalogue row names: a locus without thresholds.
_UNCATALOGUED_TRID = "chr2_1000000_1000030_CAG"


def _uncatalogued_call_vcf(column: str) -> str:
    return _HEADER.format(column=column) + (
        f"chr2\t1000000\t.\tCAG\t<TR>\t.\t.\tTRID={_UNCATALOGUED_TRID};END=1000900;MOTIFS=CAG;"
        f"STRUC=<TR>\tGT:AL:MC\t1/2:36,900:12,300\n"
    )


def _database_url(database: str):
    from backend.app.core.config import settings

    return settings.postgres_dsn.set(database=database)


def _engine(database: str, **kwargs):
    from backend.app.core.config import settings

    return create_async_engine(
        _database_url(database), connect_args=settings.postgres_connect_args, **kwargs
    )


# The configured database, where the test databases are created and dropped from. Read before
# a test points the settings at a database of its own (``_run``).
_ADMIN_DATABASE: list[str] = []


async def _create_database() -> str:
    name = f"coga_migration_test_{uuid4().hex[:12]}"
    assert re.fullmatch(r"[a-z0-9_]+", name)  # an identifier, safe to write into the DDL
    admin = _engine(_ADMIN_DATABASE[-1], isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        await admin.dispose()
    return name


async def _drop_database(name: str) -> None:
    assert re.fullmatch(r"[a-z0-9_]+", name)
    admin = _engine(_ADMIN_DATABASE[-1], isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.dispose()


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


async def _check_definition(conn: AsyncConnection, table: str, name: str) -> str:
    return str(
        (
            await conn.execute(
                text(
                    "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
                    "JOIN pg_class c ON c.oid = con.conrelid "
                    "WHERE c.relname = :t AND con.conname = :n"
                ),
                {"t": table, "n": name},
            )
        ).scalar_one()
    )


async def _set_old_checks(conn: AsyncConnection) -> None:
    for (table, name), definition in _OLD_CHECKS.items():
        await conn.exec_driver_sql(
            f"ALTER TABLE {table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} {definition}"
        )


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


# Until the release candidate every run re-applies the baselines first; after its freeze only
# the revisions run, and revision 0001 applies the baselines to a new database. Both must end at
# the same schema.
_MODES = pytest.mark.parametrize("reapply_baselines", [True, False], ids=["until-rc", "frozen"])


@_MODES
def test_a_new_database_ends_with_exactly_the_schema_the_baseline_files_create(
    monkeypatch, reapply_baselines: bool
) -> None:
    from backend.app.core import postgres, postgres_migrations
    from backend.app.core.config import settings
    from backend.app.core.postgres_migrations import head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(migrated: str):
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            # The real start: init_postgres_schema on the configured database.
            monkeypatch.setattr(settings, "postgres_db", migrated)
            await postgres.close_postgres_engine()
            try:
                await postgres.init_postgres_schema()
                await postgres.init_postgres_schema()  # every later start: nothing to do
            finally:
                await postgres.close_postgres_engine()
            return await _fingerprint(reference), await _fingerprint(migrated), await _ledger(migrated)
        finally:
            await _drop_database(reference)

    expected, actual, ledger = _run(_body)

    assert actual == expected, _differences(expected, actual)
    assert ledger == [head_revision()]
    # The fingerprint is not empty: the comparison covers the whole schema.
    assert len(expected["columns"]) > 500 and len(expected["triggers"]) >= 5
    assert ("alembic_version", "coga_app", "SELECT", False) in {
        row[:4] for row in expected["table_privileges"]
    }
    assert not {
        row[2] for row in expected["table_privileges"] if row[0] == "alembic_version" and row[1] == "coga_app"
    } - {"SELECT"}


@_MODES
def test_a_database_from_before_the_ledger_is_brought_under_it_keeps_its_rows_and_stores_a_call_without_thresholds(
    monkeypatch, reapply_baselines: bool
) -> None:
    from backend.app.core import postgres
    from backend.app.core.config import settings
    from backend.app.services.family_metadata_context import SampleMetadataContext
    from backend.app.services.repeat_expansion_pg import ingest_trgt_text

    try:  # a version without the ledger has no such module; its start is tested the same way
        from backend.app.core import postgres_migrations
    except ImportError:
        pass
    else:
        monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        # The database as the baselines left it before the ledger existed: no alembic_version,
        # and the three checks from before #747, #749 and #826, with rows in its tables.
        await _apply_baseline_files(database)
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
                await _set_old_checks(conn)
                family = (
                    await conn.execute(
                        text("INSERT INTO families (family_id) VALUES ('FAM-OLD') RETURNING id::text")
                    )
                ).scalar_one()
                samples = {}
                for sample_id, sex in (("FAM-OLD-MOTHER", "female"), ("FAM-OLD-CHILD", "male")):
                    samples[sample_id] = (
                        await conn.execute(
                            text(
                                "INSERT INTO samples (sample_id, family_id, sex) "
                                "VALUES (:s, CAST(:f AS uuid), :sex) RETURNING id::text"
                            ),
                            {"s": sample_id, "f": family, "sex": sex},
                        )
                    ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO family_relationships (family_id, sample_id_a, sample_id_b, relationship_type) "
                        "VALUES (CAST(:f AS uuid), CAST(:a AS uuid), CAST(:b AS uuid), 'parent_child')"
                    ),
                    {"f": family, "a": samples["FAM-OLD-MOTHER"], "b": samples["FAM-OLD-CHILD"]},
                )
                await conn.execute(
                    text(
                        "INSERT INTO repeat_expansions (family_id, sample_id, locus_id, gene, display_name, "
                        "disease, chr, start, \"end\", status) VALUES (CAST(:f AS uuid), CAST(:s AS uuid), "
                        "'FMR1', 'FMR1', 'FMR1', 'Fragile X syndrome', 'chrX', 147912049, 147912111, 'review')"
                    ),
                    {"f": family, "s": samples["FAM-OLD-CHILD"]},
                )
                await conn.execute(
                    text(
                        "INSERT INTO clinical_audit_events (family_id, actor, action, summary) "
                        "VALUES (CAST(:f AS uuid), 'analyst', 'test', 'written before the ledger')"
                    ),
                    {"f": family},
                )
        finally:
            await engine.dispose()
        before = await _table_contents(database)

        # The next start.
        monkeypatch.setattr(settings, "postgres_db", database)
        await postgres.close_postgres_engine()
        try:
            await postgres.init_postgres_schema()
            migrated = await _table_contents(database)
            # A TRGT call at a locus without thresholds: refused with the whole dataset by the
            # old check (#826), stored as not assessed now.
            sessionmaker = postgres.get_postgres_sessionmaker()
            async with sessionmaker() as session:
                await ingest_trgt_text(
                    session,
                    sample_context=SampleMetadataContext(
                        sample_uuid=samples["FAM-OLD-MOTHER"],
                        sample_id="FAM-OLD-MOTHER",
                        family_uuid=family,
                        family_id="FAM-OLD",
                        sex="female",
                        project_ids=[],
                        assembly_id=None,
                        assembly_name="GRCh38",
                    ),
                    text_value=_uncatalogued_call_vcf("FAM-OLD-MOTHER"),
                    metadata={"source": "trgt", "filename": "mother.vcf"},
                )
                stored = (
                    await session.execute(
                        text(
                            "SELECT status FROM repeat_expansions "
                            "WHERE sample_id = CAST(:s AS uuid) AND locus_id = :l"
                        ),
                        {"s": samples["FAM-OLD-MOTHER"], "l": _UNCATALOGUED_TRID},
                    )
                ).scalar_one()
        finally:
            await postgres.close_postgres_engine()

        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return stored, before, migrated, fingerprints, await _ledger(database)

    stored, before, migrated, (expected, actual), ledger = _run(_body)

    assert stored == "not_assessed"
    from backend.app.core.postgres_migrations import head_revision

    assert ledger == [head_revision()]
    # The migration kept every row of every table as it was, and added only the ledger's.
    assert {table: rows for table, rows in migrated.items() if table != "alembic_version"} == before
    assert migrated["alembic_version"][0] == 1
    assert before["repeat_expansions"][0] == 1 and before["clinical_audit_events"][0] == 1
    # The schema is the baselines', the three checks included.
    assert actual == expected, _differences(expected, actual)


def test_a_database_at_an_older_revision_gets_only_the_revisions_it_lacks(caplog) -> None:
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0002 from before 0003: the two checks 0003 widens are old.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0002'"))
                for (table, name), definition in _OLD_CHECKS.items():
                    if table != "repeat_expansions":
                        await conn.exec_driver_sql(
                            f"ALTER TABLE {table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} {definition}"
                        )
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
                async with engine.begin() as conn:
                    revision = await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                checks = {
                    name: await _check_definition(conn, table, name) for table, name in _OLD_CHECKS
                }
            return revision, checks, [record.getMessage() for record in caplog.records]
        finally:
            await engine.dispose()

    revision, checks, messages = _run(_body)

    assert (revision.before, revision.after) == ("0002", head_revision())
    assert [message for message in messages if message.startswith("Running upgrade")] == [
        message for message in messages if message.startswith("Running upgrade 0002 -> 0003")
    ]
    assert any(message.startswith("Running upgrade 0002 -> 0003") for message in messages)
    assert "'relative'::text" in checks["family_relationships_relationship_type_check"]
    assert "'target_coverage'::text" in checks["sample_interval_track_sources_track_type_check"]
    assert "'not_assessed'::text" in checks["repeat_expansions_status_check"]


def test_a_ledger_from_a_newer_version_is_refused_and_nothing_changes() -> None:
    from backend.app.core.postgres_migrations import SchemaMigrationRefused, apply_postgres_schema

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # As a newer version left it: a revision this one does not have, and (standing in
            # for its other changes) an index the baselines would create again.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '9999'"))
                await conn.exec_driver_sql("DROP INDEX idx_repeat_expansions_locus")
            with pytest.raises(SchemaMigrationRefused) as refused:
                async with engine.begin() as conn:
                    await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                ledger = [str(row[0]) for row in (await conn.execute(text("SELECT version_num FROM alembic_version"))).all()]
                index = (
                    await conn.execute(text("SELECT to_regclass('public.idx_repeat_expansions_locus')"))
                ).scalar_one()
            return str(refused.value), ledger, index
        finally:
            await engine.dispose()

    message, ledger, index = _run(_body)

    assert "schema revision 9999" in message and "a newer version migrated it" in message
    assert ledger == ["9999"]
    assert index is None  # the baselines did not run


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
