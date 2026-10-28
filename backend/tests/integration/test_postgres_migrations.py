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
- A database at revision 0003 from before SEC-7 gets the structure versions' chain columns and
  index, the family link a deletion nulls, and both tables' append-only guards and REVOKE
  (revision 0004), keeps its structure version (outside the chain), and ends at the same
  fingerprint.
- A database at revision 0004 from before SEC-1 gets the project memberships' access level
  (revision 0005), each stored membership an analyst's, with the baseline's check, and ends at
  the same fingerprint.
- A database at revision 0006 from before TRACE-13 gets a family ID's sign-out versions unique
  (revision 0007: a unique index, which replaces the chain's plain one), keeps every sign-out,
  a deleted family's too, and ends at the same fingerprint; one where a family ID already holds
  a version twice is refused with a message that names no family, and nothing changes.
- A database at revision 0007 from before SEC-32 gets an import job's requester and a filter
  preset's owner kept by the account's id (revision 0008): each job and preset linked to the
  account its e-mail address or user name finds, a job of an address no account has linked to
  none, a preset of a user name no account has removed; a deleted account's presets go with
  it, its job stays linked to none, and a later start does not link it to the account that
  took the address since. It ends at the same fingerprint.
- A database at revision 0008 from before SEC-17 gets the event id on the request audit and the
  UI-event log (revision 0009: a unique column, empty in the rows stored before), keeps every
  row, and ends at the same fingerprint; the writers' INSERT stores an event's id once, and a
  plain second INSERT of it is refused.
- A database at revision 0009 from before SEC-35 gets a gene-reference refresh's and a
  knowledgebase rebuild's requester kept by the account's id (revision 0010): each job linked
  to the account its e-mail address finds, one of an address no account has, and the refresh
  a start queues, linked to none; a deleted account's jobs stay, linked to none, and a later
  start does not link them to the account that took the address since. It keeps every row and
  ends at the same fingerprint.
- A ledger naming a revision this version does not have is refused, and nothing changes.
- The restricted runtime role cannot run the migration; two instances that start together
  both come up, one after the other.
- A start that does not migrate (OPS-23) reads the ledger as the restricted runtime role and
  accepts only this version's own revision: an older one, an empty ledger or none at all
  (the migration job has not run) and one this version does not have (a newer version
  migrated the database) are each refused, and the ledger stays as it was.
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
    # Each revision after 0002, in order, and none before it.
    from backend.app.core.postgres_migrations import migration_script

    line = [script.revision for script in reversed(list(migration_script().walk_revisions()))]
    after_0002 = line[line.index("0002") :]
    assert [message.split(",")[0] for message in messages if message.startswith("Running upgrade")] == [
        f"Running upgrade {earlier} -> {later}" for earlier, later in zip(after_0002, after_0002[1:])
    ]
    assert any(message.startswith("Running upgrade 0002 -> 0003") for message in messages)
    assert "'relative'::text" in checks["family_relationships_relationship_type_check"]
    assert "'target_coverage'::text" in checks["sample_interval_track_sources_track_type_check"]
    assert "'not_assessed'::text" in checks["repeat_expansions_status_check"]


# The structure versions and the UI-event log as a database had them before SEC-7 (revision
# 0004): no chain columns or index, family_id NOT NULL and deleted with its family, neither
# table guarded by a trigger, and coga_app still holding UPDATE and DELETE on both.
_BEFORE_0004 = (
    "DROP TRIGGER IF EXISTS family_structure_versions_immutable ON family_structure_versions",
    "DROP FUNCTION IF EXISTS family_structure_versions_block_mutation()",
    "DROP TRIGGER IF EXISTS ui_events_immutable ON ui_events",
    "DROP FUNCTION IF EXISTS ui_events_block_mutation()",
    "GRANT UPDATE, DELETE ON family_structure_versions, ui_events TO coga_app",
    "DROP INDEX IF EXISTS idx_family_structure_versions_chain",
    "ALTER TABLE family_structure_versions DROP COLUMN family_identifier, DROP COLUMN row_hash, "
    "DROP COLUMN prev_hash",
    "ALTER TABLE family_structure_versions DROP CONSTRAINT family_structure_versions_family_id_fkey, "
    "ADD CONSTRAINT family_structure_versions_family_id_fkey FOREIGN KEY (family_id) "
    "REFERENCES families(id) ON DELETE CASCADE",
    "ALTER TABLE family_structure_versions ALTER COLUMN family_id SET NOT NULL",
)


@_MODES
def test_a_database_from_before_sec7_gets_the_structure_versions_chain_and_the_append_only_logs(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import DBAPIError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0003 from before SEC-7, holding a structure version.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0003'"))
                for statement in _BEFORE_0004:
                    await conn.exec_driver_sql(statement)
                family = (
                    await conn.execute(
                        text("INSERT INTO families (family_id) VALUES ('FAM-SEC7') RETURNING id::text")
                    )
                ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO family_structure_versions (family_id, version, structure_hash, snapshot) "
                        "VALUES (CAST(:f AS uuid), 1, 'stored-before', '{}'::jsonb)"
                    ),
                    {"f": family},
                )
                await conn.execute(text("INSERT INTO ui_events (event_type, label) VALUES ('click', 'stored-before')"))
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                kept = [
                    tuple(row)
                    for row in (
                        await conn.execute(
                            text(
                                "SELECT version, structure_hash, family_identifier, row_hash "
                                "FROM family_structure_versions WHERE family_id = CAST(:f AS uuid)"
                            ),
                            {"f": family},
                        )
                    ).all()
                ]
            refusals = []
            for statement in (
                "UPDATE family_structure_versions SET structure_hash = 'edited'",
                "DELETE FROM ui_events",
            ):
                try:
                    async with engine.begin() as conn:
                        await conn.exec_driver_sql(statement)
                except DBAPIError as exc:
                    refusals.append(str(exc))
            # The family's deletion keeps its version now, unlinked from it.
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM families WHERE id = CAST(:f AS uuid)"), {"f": family})
            async with engine.connect() as conn:
                unlinked = (
                    await conn.execute(
                        text("SELECT count(*) FROM family_structure_versions WHERE structure_hash = 'stored-before' AND family_id IS NULL")
                    )
                ).scalar_one()
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return revision, kept, refusals, unlinked, fingerprints

    revision, kept, refusals, unlinked, (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0003", head_revision())
    # The version stored before stays, outside the chain (no identifier, no hash).
    assert kept == [(1, "stored-before", None, None)]
    assert len(refusals) == 2
    assert "family_structure_versions is append-only; UPDATE is not permitted" in refusals[0]
    assert "ui_events is append-only; DELETE is not permitted" in refusals[1]
    assert unlinked == 1
    # The schema is the baselines': columns, foreign key, index, triggers, functions, grants.
    assert actual == expected, _differences(expected, actual)


# The project memberships as a database had them before SEC-1 (revision 0005): no access level.
_BEFORE_0005 = ("ALTER TABLE project_users DROP COLUMN access_level",)


@_MODES
def test_a_database_from_before_sec1_gets_the_access_level_with_every_member_an_analyst(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import DBAPIError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0004 from before SEC-1, with a member in a project.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0004'"))
                for statement in _BEFORE_0005:
                    await conn.exec_driver_sql(statement)
                species = (
                    await conn.execute(
                        text(
                            "INSERT INTO species (name, common_name, tax_id) "
                            "VALUES ('Organism SEC-1', 'Test organism', 999000001) RETURNING id::text"
                        )
                    )
                ).scalar_one()
                assembly = (
                    await conn.execute(
                        text(
                            "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                            "VALUES (CAST(:s AS uuid), 'SEC1TEST', 'test', DATE '2026-01-01') RETURNING id::text"
                        ),
                        {"s": species},
                    )
                ).scalar_one()
                project = (
                    await conn.execute(
                        text(
                            "INSERT INTO projects (name, species_id, assembly_id) "
                            "VALUES ('project-sec1', CAST(:s AS uuid), CAST(:a AS uuid)) RETURNING id::text"
                        ),
                        {"s": species, "a": assembly},
                    )
                ).scalar_one()
                member = (
                    await conn.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES ('member-sec1@example.org', '', 'viewer', 'member-sec1@example.org') "
                            "RETURNING id::text"
                        )
                    )
                ).scalar_one()
                await conn.execute(
                    text("INSERT INTO project_users (project_id, user_id) VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"),
                    {"p": project, "u": member},
                )
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                levels = [
                    str(row[0]) for row in (await conn.execute(text("SELECT access_level FROM project_users"))).all()
                ]
            refused = ""
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("UPDATE project_users SET access_level = 'admin'"))
            except DBAPIError as exc:
                refused = str(exc)
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE project_users SET access_level = 'reader'"))
            async with engine.connect() as conn:
                changed = [
                    str(row[0]) for row in (await conn.execute(text("SELECT access_level FROM project_users"))).all()
                ]
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return revision, levels, refused, changed, fingerprints

    revision, levels, refused, changed, (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0004", head_revision())
    # The member from before the level is an analyst, as every member could change the project's
    # records until then; another level than analyst or reader is refused, reader is not.
    assert levels == ["analyst"]
    assert "project_users_access_level_check" in refused
    assert changed == ["reader"]
    # The schema is the baselines': the column with its default, its check and its name.
    assert actual == expected, _differences(expected, actual)


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


def test_a_start_that_does_not_migrate_serves_only_on_its_own_revision() -> None:
    # The API as the restricted runtime role (POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false):
    # the migration job runs before the rollout, and the start only reads the ledger (OPS-23).
    from backend.app.core.postgres_migrations import (
        SchemaRevisionMismatch,
        apply_postgres_schema,
        check_schema_revision,
        head_revision,
        migration_script,
    )

    previous = migration_script().get_revision(head_revision()).down_revision
    assert isinstance(previous, str) and re.fullmatch(r"\d{4}", previous)  # safe to write into SQL
    # Each ledger the owner leaves, from the migrated database on, and what coga_app's start reads.
    ledgers: dict[str, list[str]] = {
        "own revision": [],
        "previous revision": [f"UPDATE alembic_version SET version_num = '{previous}'"],
        "newer version's revision": ["UPDATE alembic_version SET version_num = '9999'"],
        "empty ledger": ["DELETE FROM alembic_version"],
        "no ledger": ["DROP TABLE alembic_version"],
    }

    async def _body(database: str):
        engine = _engine(database)
        outcomes = {}
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            for case, statements in ledgers.items():
                async with engine.begin() as conn:
                    for statement in statements:
                        await conn.exec_driver_sql(statement)
                async with engine.begin() as conn:
                    await conn.execute(text("SET LOCAL ROLE coga_app"))
                    role = (await conn.execute(text("SELECT current_user"))).scalar_one()
                    try:
                        outcome = await conn.run_sync(check_schema_revision)
                    except SchemaRevisionMismatch as refused:
                        outcome = f"refused: {refused}"
                async with engine.connect() as conn:
                    exists = (await conn.execute(text("SELECT to_regclass('public.alembic_version')"))).scalar_one()
                ledger = await _ledger(database) if exists else None
                outcomes[case] = (role, outcome, ledger)
            return outcomes
        finally:
            await engine.dispose()

    outcomes = _run(_body)

    assert {role for role, _outcome, _ledger_rows in outcomes.values()} == {"coga_app"}
    assert outcomes["own revision"][1:] == (head_revision(), [head_revision()])
    behind = f"and this version of CoGA needs revision {head_revision()}"
    assert outcomes["previous revision"][1].startswith(f"refused: The database is at schema revision {previous}, {behind}")
    assert outcomes["empty ledger"][1].startswith(f"refused: The database is at schema revision none (no ledger), {behind}")
    assert outcomes["no ledger"][1].startswith(f"refused: The database is at schema revision none (no ledger), {behind}")
    for case in ("previous revision", "empty ledger", "no ledger"):
        assert "Run the migration job first" in outcomes[case][1], case
    newer = outcomes["newer version's revision"][1]
    assert newer.startswith("refused: The database is at schema revision 9999, which this version of CoGA does not have")
    assert "this version is older than the database" in newer
    # The start only read the ledger.
    assert [rows for _role, _outcome, rows in outcomes.values()] == [
        [head_revision()],
        [previous],
        ["9999"],
        [],
        None,
    ]


# The accounts as a database had them before SEC-4 (revision 0006): no Microsoft object ID.
_BEFORE_0006 = ("ALTER TABLE users DROP COLUMN azure_object_id",)


@_MODES
def test_a_database_from_before_sec4_gets_the_microsoft_object_id_with_its_accounts_kept(
    monkeypatch, reapply_baselines: bool
) -> None:
    import uuid

    from sqlalchemy.exc import DBAPIError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)
    object_id = str(uuid.uuid4())

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0005 from before SEC-4, with two password accounts.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0005'"))
                for statement in _BEFORE_0006:
                    await conn.exec_driver_sql(statement)
                for name in ("first-sec4@example.org", "second-sec4@example.org"):
                    await conn.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:n, '', 'viewer', :n)"
                        ),
                        {"n": name},
                    )
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                kept = sorted(
                    (str(row[0]), row[1])
                    for row in (
                        await conn.execute(
                            text("SELECT email, azure_object_id FROM users WHERE email LIKE '%-sec4@example.org'")
                        )
                    ).all()
                )
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE users SET azure_object_id = CAST(:o AS uuid) WHERE email = 'first-sec4@example.org'"),
                    {"o": object_id},
                )
            refused = ""
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("UPDATE users SET azure_object_id = CAST(:o AS uuid) WHERE email = 'second-sec4@example.org'"),
                        {"o": object_id},
                    )
            except DBAPIError as exc:
                refused = str(exc)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return revision, kept, refused, fingerprints

    revision, kept, refused, (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0005", head_revision())
    # The accounts from before Microsoft sign-in are kept, without an object ID.
    assert kept == [("first-sec4@example.org", None), ("second-sec4@example.org", None)]
    # One object ID belongs to one account.
    assert "users_azure_object_id_key" in refused
    # The schema is the baselines': the column with its type and its unique constraint.
    assert actual == expected, _differences(expected, actual)


# The sign-outs as a database had them before TRACE-13 (revision 0007): a family ID's versions
# not unique, its chain read through a plain index.
_BEFORE_0007 = (
    "DROP INDEX IF EXISTS uq_report_signouts_family_identifier_version",
    "CREATE INDEX IF NOT EXISTS idx_report_signouts_chain ON report_signouts (family_identifier, version DESC)",
)
_TRACE13_FAMILY = "FAM-TRACE13"


async def _family_signed_out(conn: AsyncConnection, versions: tuple[int, ...]) -> str:
    """A family made under ``_TRACE13_FAMILY`` and signed out as ``versions``: its id."""

    family = (
        await conn.execute(
            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": _TRACE13_FAMILY}
        )
    ).scalar_one()
    for version in versions:
        await conn.execute(
            text(
                "INSERT INTO report_signouts (family_id, family_identifier, version, signed_out_by, "
                "content_hash, snapshot) VALUES (CAST(:f AS uuid), :l, :v, 'trace13', :h, '{}'::jsonb)"
            ),
            {"f": family, "l": _TRACE13_FAMILY, "v": version, "h": f"stored-{family}-{version}"},
        )
    return family


async def _indexes_of_the_sign_outs(conn: AsyncConnection) -> list[str]:
    return [
        str(row[0])
        for row in (
            await conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'report_signouts' ORDER BY 1")
            )
        ).all()
    ]


@_MODES
def test_a_database_from_before_trace13_gets_a_family_ids_sign_out_versions_unique(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import DBAPIError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0006 from before TRACE-13: a family signed out twice and
            # deleted, and the family made again under its ID, signed out as version 3.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0006'"))
                for statement in _BEFORE_0007:
                    await conn.exec_driver_sql(statement)
                first = await _family_signed_out(conn, (1, 2))
                await conn.execute(text("DELETE FROM families WHERE id = CAST(:f AS uuid)"), {"f": first})
                await _family_signed_out(conn, (3,))
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
                indexes = await _indexes_of_the_sign_outs(conn)
            kept = await _table_contents(database)
            # A version the family ID holds is refused, also one the deleted family signed.
            refused = ""
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO report_signouts (family_identifier, version, signed_out_by, "
                            "content_hash, snapshot) VALUES (:l, 2, 'trace13', 'again', '{}'::jsonb)"
                        ),
                        {"l": _TRACE13_FAMILY},
                    )
            except DBAPIError as exc:
                refused = str(exc)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return revision, contents, kept, indexes, refused, fingerprints

    revision, contents, kept, indexes, refused, (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0006", head_revision())
    # Every stored row stays as it was, the sign-outs of both families among them; only the
    # ledger moves on.
    assert {table: rows for table, rows in kept.items() if table != "alembic_version"} == {
        table: rows for table, rows in contents.items() if table != "alembic_version"
    }
    assert kept["report_signouts"][0] == 3
    # The unique index replaces the plain one.
    assert "uq_report_signouts_family_identifier_version" in indexes
    assert "idx_report_signouts_chain" not in indexes
    assert "uq_report_signouts_family_identifier_version" in refused
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)


@_MODES
def test_a_database_where_a_family_id_holds_a_sign_out_version_twice_is_refused_and_kept(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import DBAPIError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0006 where a family made again under its ID signed
            # version 1 again, as the sign-out numbered per family row before TRACE-13.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0006'"))
                for statement in _BEFORE_0007:
                    await conn.exec_driver_sql(statement)
                first = await _family_signed_out(conn, (1,))
                await conn.execute(text("DELETE FROM families WHERE id = CAST(:f AS uuid)"), {"f": first})
                await _family_signed_out(conn, (1,))
            contents = await _table_contents(database)
            with pytest.raises(DBAPIError) as stopped:
                async with engine.begin() as conn:
                    await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                indexes = await _indexes_of_the_sign_outs(conn)
            return str(stopped.value), contents, await _table_contents(database), indexes, await _ledger(database)
        finally:
            await engine.dispose()

    message, contents, kept, indexes, ledger = _run(_body)

    # The migration stops with a message that counts the family IDs and names none.
    assert "report_signouts holds 1 family ID(s) with a sign-out version taken twice" in message
    assert _TRACE13_FAMILY not in message
    # Nothing changed: the rows, the plain index, and the ledger at 0006.
    assert kept == contents
    assert "idx_report_signouts_chain" in indexes
    assert "uq_report_signouts_family_identifier_version" not in indexes
    assert ledger == ["0006"]


# The import jobs and the filter presets as a database had them before SEC-32 (revision 0008):
# a job's requester and a preset's owner kept only by an e-mail address and a user name.
_BEFORE_0008 = (
    "ALTER TABLE family_import_jobs DROP COLUMN requested_by_id",
    "DROP INDEX idx_small_variant_filter_presets_unique",
    "ALTER TABLE small_variant_filter_presets DROP COLUMN owner_id",
    "CREATE UNIQUE INDEX idx_small_variant_filter_presets_unique ON small_variant_filter_presets "
    "USING btree (owner, name)",
    "DROP INDEX idx_structural_variant_filter_presets_unique",
    "ALTER TABLE structural_variant_filter_presets DROP COLUMN owner_id",
    "CREATE UNIQUE INDEX idx_structural_variant_filter_presets_unique ON structural_variant_filter_presets "
    "USING btree (COALESCE(family_id, '00000000-0000-0000-0000-000000000000'::uuid), scope, owner, name)",
)
_SEC32_USER = "analyst-sec32@example.org"


async def _sec32_records(conn: AsyncConnection) -> dict[str, list[tuple]]:
    """The jobs and presets the SEC-32 test stores, each with the account it is kept by."""

    queries = {
        "jobs": (
            "SELECT j.requested_by, u.email FROM family_import_jobs j "
            "LEFT JOIN users u ON u.id = j.requested_by_id WHERE j.submitted_path LIKE '/sec32/%'"
        ),
        "small": (
            "SELECT p.name, p.owner, u.email FROM small_variant_filter_presets p "
            "JOIN users u ON u.id = p.owner_id WHERE p.name LIKE 'sec32 %'"
        ),
        "sv": (
            "SELECT p.name, p.owner, u.email FROM structural_variant_filter_presets p "
            "JOIN users u ON u.id = p.owner_id WHERE p.name LIKE 'sec32 %'"
        ),
    }
    return {
        name: sorted(tuple(row) for row in (await conn.execute(text(query))).all())
        for name, query in queries.items()
    }


@_MODES
def test_a_database_from_before_sec32_keeps_each_job_and_preset_by_its_account(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import DBAPIError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0007 from before SEC-32: a job and presets of an account,
            # and a job and presets of an address and a user name no account has.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0007'"))
                for statement in _BEFORE_0008:
                    await conn.exec_driver_sql(statement)
                account = (
                    await conn.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:n, '', 'admin', :n) RETURNING id::text"
                        ),
                        {"n": _SEC32_USER},
                    )
                ).scalar_one()
                for requester in (_SEC32_USER, "gone-sec32@example.org"):
                    await conn.execute(
                        text(
                            "INSERT INTO family_import_jobs (submitted_path, status, requested_by) "
                            "VALUES (:p, 'completed', :r)"
                        ),
                        {"p": f"/sec32/{requester}", "r": requester},
                    )
                for owner, name in ((_SEC32_USER, "sec32 mine"), ("gone-sec32", "sec32 orphan")):
                    await conn.execute(
                        text("INSERT INTO small_variant_filter_presets (owner, name) VALUES (:o, :n)"),
                        {"o": owner, "n": name},
                    )
                    await conn.execute(
                        text(
                            "INSERT INTO structural_variant_filter_presets (scope, owner, name) "
                            "VALUES ('global', :o, :n)"
                        ),
                        {"o": owner, "n": name},
                    )
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                kept = await _sec32_records(conn)
            # A second preset of the account under the same name is refused, whatever owner
            # name it is stored with: the account is its owner.
            refused = ""
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO small_variant_filter_presets (owner, owner_id, name) "
                            "VALUES ('another-name-sec32', CAST(:a AS uuid), 'sec32 mine')"
                        ),
                        {"a": account},
                    )
            except DBAPIError as exc:
                refused = str(exc)
            # The account is deleted, and another takes its address: the job keeps no requester,
            # and a later start, which re-applies the baselines until the RC, gives it none.
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM users WHERE id = CAST(:a AS uuid)"), {"a": account})
                await conn.execute(
                    text(
                        "INSERT INTO users (username, hashed_password, role, email) "
                        "VALUES ('successor-sec32', '', 'viewer', :n)"
                    ),
                    {"n": _SEC32_USER},
                )
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                after_deletion = await _sec32_records(conn)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return revision, kept, refused, after_deletion, fingerprints

    revision, kept, refused, after_deletion, (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0007", head_revision())
    # Each job is kept, linked to the account its address found; one whose address no account
    # has is linked to none (it cannot run, and only an administrator sees it).
    assert kept["jobs"] == [(_SEC32_USER, _SEC32_USER), ("gone-sec32@example.org", None)]
    # A preset is kept by the account its user name found, with the name it was stored with;
    # one whose user name no account has would be nobody's, and is removed.
    assert kept["small"] == [("sec32 mine", _SEC32_USER, _SEC32_USER)]
    assert kept["sv"] == [("sec32 mine", _SEC32_USER, _SEC32_USER)]
    assert "idx_small_variant_filter_presets_unique" in refused
    # The deleted account's presets went with it; its job stayed, linked to no account.
    assert after_deletion == {
        "jobs": [(_SEC32_USER, None), ("gone-sec32@example.org", None)],
        "small": [],
        "sv": [],
    }
    # The schema is the baselines': the columns, their foreign keys and the unique indexes.
    assert actual == expected, _differences(expected, actual)


# The two event logs as a database had them before SEC-17 (revision 0009): no event id.
_BEFORE_0009 = (
    "ALTER TABLE audit_log_events DROP COLUMN event_id",
    "ALTER TABLE ui_events DROP COLUMN event_id",
)
_EVENT_LOGS = ("audit_log_events", "ui_events")


async def _event_log_rows(conn: AsyncConnection) -> dict[str, list[str]]:
    """Each event log's rows, every column but the event id, as JSON text."""

    rows = {}
    for table in _EVENT_LOGS:
        result = await conn.execute(text(f"SELECT (to_jsonb(t) - 'event_id')::text FROM {table} t"))
        rows[table] = sorted(str(row[0]) for row in result.all())
    return rows


async def _count(conn: AsyncConnection, table: str, where: str, parameters: dict | None = None) -> int:
    assert table in _EVENT_LOGS  # a name from this file, safe to write into the statement
    return int((await conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), parameters or {})).scalar_one())


# A row of each event log naming an event id, as the writers insert it.
_INSERT_WITH_EVENT_ID = {
    "audit_log_events": (
        "INSERT INTO audit_log_events (event_id, method, path, status_code) "
        "VALUES (CAST(:e AS uuid), 'GET', '/api/stored-after-sec17', 200)"
    ),
    "ui_events": (
        "INSERT INTO ui_events (event_id, event_type, label) "
        "VALUES (CAST(:e AS uuid), 'click', 'stored-after-sec17')"
    ),
}


@_MODES
def test_a_database_from_before_sec17_gets_the_event_ids_with_its_rows_kept(
    monkeypatch, reapply_baselines: bool
) -> None:
    import uuid

    from sqlalchemy.exc import DBAPIError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)
    event_id = str(uuid.uuid4())

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0008 from before SEC-17, with a request row and a UI event.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0008'"))
                for statement in _BEFORE_0009:
                    await conn.exec_driver_sql(statement)
                await conn.execute(
                    text(
                        "INSERT INTO audit_log_events (method, path, status_code) "
                        "VALUES ('GET', '/api/stored-before-sec17', 200)"
                    )
                )
                await conn.execute(text("INSERT INTO ui_events (event_type, label) VALUES ('click', 'stored-before-sec17')"))
                before = await _event_log_rows(conn)
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                after = await _event_log_rows(conn)
                without_id = [await _count(conn, table, "event_id IS NULL") for table in _EVENT_LOGS]
            # The writers' INSERT, twice, stores the event once; a plain second INSERT is refused.
            refused = {}
            for table, insert in _INSERT_WITH_EVENT_ID.items():
                for _write in range(2):
                    async with engine.begin() as conn:
                        await conn.execute(text(f"{insert} ON CONFLICT (event_id) DO NOTHING"), {"e": event_id})
                try:
                    async with engine.begin() as conn:
                        await conn.execute(text(insert), {"e": event_id})
                except DBAPIError as exc:
                    refused[table] = str(exc)
            async with engine.connect() as conn:
                stored_once = [
                    await _count(conn, table, "event_id = CAST(:e AS uuid)", {"e": event_id}) for table in _EVENT_LOGS
                ]
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return revision, (before, after, without_id), (contents, kept), refused, stored_once, fingerprints

    revision, (before, after, without_id), (contents, kept), refused, stored_once, (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0008", head_revision())
    # The rows stored before keep every value, without an event id (the tables are append-only,
    # so nothing fills it in); every other table is as it was.
    assert after == before
    assert [len(before[table]) for table in _EVENT_LOGS] == without_id
    assert all(count >= 1 for count in without_id)
    changed = {"alembic_version", *_EVENT_LOGS}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    # One row per event id: the writers' INSERT stores it once, and a plain one is refused.
    assert stored_once == [1, 1]
    assert "audit_log_events_event_id_key" in refused["audit_log_events"]
    assert "ui_events_event_id_key" in refused["ui_events"]
    # The schema is the baselines': the column with its type and its unique constraint.
    assert actual == expected, _differences(expected, actual)


# The gene-reference refresh and knowledgebase rebuild jobs as a database had them before SEC-35
# (revision 0010): a requester kept only by an e-mail address.
_BEFORE_0010 = (
    "ALTER TABLE gene_info_refresh_jobs DROP COLUMN requested_by_id",
    "ALTER TABLE clinical_cnv_kb_jobs DROP COLUMN requested_by_id",
)
_SEC35_USER = "admin-sec35@example.org"
_SEC35_GONE = "gone-sec35@example.org"


async def _sec35_jobs(conn: AsyncConnection) -> dict[str, list[tuple]]:
    """The refresh and rebuild jobs the SEC-35 test stores, each with the account it is kept by."""

    queries = {
        "refresh": (
            "SELECT j.requested_by, u.email FROM gene_info_refresh_jobs j "
            "LEFT JOIN users u ON u.id = j.requested_by_id WHERE j.symbol LIKE 'SEC35%'"
        ),
        "rebuild": (
            "SELECT j.requested_by, u.email FROM clinical_cnv_kb_jobs j "
            "LEFT JOIN users u ON u.id = j.requested_by_id WHERE j.assembly_name = 'SEC35TEST'"
        ),
    }
    return {
        name: sorted(tuple(row) for row in (await conn.execute(text(query))).all())
        for name, query in queries.items()
    }


@_MODES
def test_a_database_from_before_sec35_keeps_each_refresh_and_rebuild_by_its_account(
    monkeypatch, reapply_baselines: bool
) -> None:
    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0009 from before SEC-35: a refresh and a rebuild of an
            # account, of an address no account has, and the refresh a start queues.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0009'"))
                for statement in _BEFORE_0010:
                    await conn.exec_driver_sql(statement)
                account = (
                    await conn.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:n, '', 'admin', :n) RETURNING id::text"
                        ),
                        {"n": _SEC35_USER},
                    )
                ).scalar_one()
                species = (
                    await conn.execute(
                        text(
                            "INSERT INTO species (name, common_name, tax_id) "
                            "VALUES ('Organism SEC-35', 'Test organism', 999000035) RETURNING id::text"
                        )
                    )
                ).scalar_one()
                assembly = (
                    await conn.execute(
                        text(
                            "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                            "VALUES (CAST(:s AS uuid), 'SEC35TEST', 'test', DATE '2026-01-01') RETURNING id::text"
                        ),
                        {"s": species},
                    )
                ).scalar_one()
                for symbol, requester in (
                    ("SEC35A", _SEC35_USER),
                    ("SEC35B", _SEC35_GONE),
                    ("SEC35C", "startup-bootstrap"),
                ):
                    await conn.execute(
                        text(
                            "INSERT INTO gene_info_refresh_jobs (scope, symbol, status, requested_by) "
                            "VALUES ('symbol', :g, 'completed', :r)"
                        ),
                        {"g": symbol, "r": requester},
                    )
                for requester in (_SEC35_USER, _SEC35_GONE):
                    await conn.execute(
                        text(
                            "INSERT INTO clinical_cnv_kb_jobs (assembly_id, assembly_name, status, requested_by) "
                            "VALUES (CAST(:a AS uuid), 'SEC35TEST', 'completed', :r)"
                        ),
                        {"a": assembly, "r": requester},
                    )
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                linked = await _sec35_jobs(conn)
            # The account is deleted, and another takes its address: its jobs keep no
            # requester, and a later start, which re-applies the baselines until the RC, gives
            # them none.
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM users WHERE id = CAST(:a AS uuid)"), {"a": account})
                await conn.execute(
                    text(
                        "INSERT INTO users (username, hashed_password, role, email) "
                        "VALUES ('successor-sec35', '', 'admin', :n)"
                    ),
                    {"n": _SEC35_USER},
                )
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                after_deletion = await _sec35_jobs(conn)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return revision, (contents, kept), linked, after_deletion, fingerprints

    revision, (contents, kept), linked, after_deletion, (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0009", head_revision())
    # Each job is kept, linked to the account its address found; one whose address no account
    # has, and the refresh a start queues, are linked to none.
    assert linked == {
        "refresh": [(_SEC35_USER, _SEC35_USER), (_SEC35_GONE, None), ("startup-bootstrap", None)],
        "rebuild": [(_SEC35_USER, _SEC35_USER), (_SEC35_GONE, None)],
    }
    # Every row stays; only the two job tables gained their column, and the ledger moved.
    changed = {"alembic_version", "gene_info_refresh_jobs", "clinical_cnv_kb_jobs"}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    assert [kept[table][0] for table in ("gene_info_refresh_jobs", "clinical_cnv_kb_jobs")] == [
        contents[table][0] for table in ("gene_info_refresh_jobs", "clinical_cnv_kb_jobs")
    ]
    # The deleted account's jobs stayed, linked to no account, also after a later start.
    assert after_deletion == {
        "refresh": [(_SEC35_USER, None), (_SEC35_GONE, None), ("startup-bootstrap", None)],
        "rebuild": [(_SEC35_USER, None), (_SEC35_GONE, None)],
    }
    # The schema is the baselines': the columns and their foreign keys.
    assert actual == expected, _differences(expected, actual)
