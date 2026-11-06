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
- A database at revision 0010 from before ENG-6 loses the three columns no code read
  (``auth_login_attempts.last_success_at``,
  ``small_variant_reviews.compound_het_partner_variant_keys`` and
  ``structural_variant_reviews.variant_key`` with its unique index) and the families' metadata
  key ``unresolved_roi`` (revision 0011), keeps every row with its other values and a family's
  other metadata, and ends at the same fingerprint; a later start changes nothing.
- A database at revision 0012 from before the class labels were put in sentence case (ENG-6)
  gets each class label its reviews, their ACMG and CNV scorings and its filter presets hold in
  another case written as the code writes it now (revision 0013), every other value and each
  review's and preset's ``updated_at`` kept; the clinical audit trail, the signed records, the
  request audit and the UI-event log keep the label as it was, and every other table is as it
  was. It ends at the same fingerprint; a later start changes nothing.
- A database at revision 0013 from before CLIN-57 gets QA's count correction per repeat locus
  (``repeat_locus_count_units.count_offset``, revision 0014) with the baseline's default and
  check: a stored record is kept with no correction, a correction beyond 20 either way is
  refused, it ends at the same fingerprint, and a later start changes nothing.
- A database at revision 0014 from before the analyst and supervisor roles gets the accounts'
  check with the five roles (revision 0015): each viewer becomes an analyst, a superuser and an
  admin keep their role, a role CoGA does not know is refused, it ends at the same fingerprint,
  and a viewer made since stays one at a later start.
- A database at revision 0016 from before accounts were archived gets when each account was
  first active and when it was archived (revision 0017): an active account, and an inactive one
  with a request of its own in the audit, were active since their creation; an inactive one
  without waits for approval; none is archived. An archived account that is active is refused,
  it ends at the same fingerprint, and an account waiting for approval made since still waits at
  a later start.
- A database at revision 0017 from before an import job could keep its manifest gets the job's
  ``manifest_text`` and ``manifest_sha256`` with the baseline's check (revision 0018): each job
  stored before keeps its row and neither, a job that keeps one but not the other is refused, it
  ends at the same fingerprint, and a later start changes nothing.
- A database at revision 0018 from before an import job kept its package's fingerprint gets the
  job's ``package_fingerprint`` with the baseline's check (revision 0019): each job stored before
  keeps its row and none, a fingerprint that is no SHA-256 is refused, it ends at the same
  fingerprint, and a later start changes nothing.
- A database at revision 0019 from before a PanelApp panel could be imported once per choice of
  confidence levels gets ``gene_panels.external_selection`` and the unique index on source,
  external id and selection (revision 0020): each PanelApp panel gets the levels its metadata
  records, lowest first, one without them none, every other panel none; the index it replaces
  is gone, so the same PanelApp panel with other levels is stored, while the same selection
  twice, or a second Mendeliome, is refused; it ends at the same fingerprint, and a later start
  changes nothing.
- A database at revision 0020 from before a logout ended an account's sessions on the server
  gets ``users.sessions_ended_at`` (revision 0021): each account keeps its row and none, it ends
  at the same fingerprint, and a later start changes nothing.
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

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.tests.integration._datastores import (
    TRGT_VCF_HEADER,
    create_database,
    database_engine,
    drop_database,
    trgt_sample_context,
)

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

# A TRID no catalogue row names: a locus without thresholds.
_UNCATALOGUED_TRID = "chr2_1000000_1000030_CAG"


def _uncatalogued_call_vcf(column: str) -> str:
    return TRGT_VCF_HEADER.format(column=column) + (
        f"chr2\t1000000\t.\tCAG\t<TR>\t.\t.\tTRID={_UNCATALOGUED_TRID};END=1000900;MOTIFS=CAG;"
        f"STRUC=<TR>\tGT:AL:MC\t1/2:36,900:12,300\n"
    )


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
                    sample_context=trgt_sample_context(
                        samples["FAM-OLD-MOTHER"], "FAM-OLD-MOTHER", family, "FAM-OLD", "female"
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
    # Revision 0012: an embryo's segregation table, for the parental-origin view.
    assert "'apcad_segregation'::text" in checks["sample_interval_track_sources_track_type_check"]
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


# The columns as a database had them before ENG-6 (revision 0011): three that no code read, and
# the structural-variant reviews' unique index on the key nothing wrote.
_BEFORE_0011 = (
    "ALTER TABLE auth_login_attempts ADD COLUMN last_success_at TIMESTAMPTZ",
    "ALTER TABLE small_variant_reviews ADD COLUMN compound_het_partner_variant_keys jsonb "
    "DEFAULT '[]'::jsonb NOT NULL",
    "ALTER TABLE structural_variant_reviews ADD COLUMN variant_key bigint",
    "CREATE UNIQUE INDEX idx_structural_variant_reviews_family_variant_key ON structural_variant_reviews "
    "USING btree (family_id, variant_key) WHERE (variant_key IS NOT NULL)",
)
# Each table with a column the revision drops, and that column.
_DROPPED_BY_0011 = {
    "auth_login_attempts": "last_success_at",
    "small_variant_reviews": "compound_het_partner_variant_keys",
    "structural_variant_reviews": "variant_key",
}


async def _rows_without(conn: AsyncConnection, table: str, column: str) -> list[str]:
    """``table``'s rows, every column but ``column``, as JSON text."""

    assert _DROPPED_BY_0011.get(table) == column  # names from this file, safe in the statement
    result = await conn.execute(text(f"SELECT (to_jsonb(t) - '{column}')::text FROM {table} t"))
    return sorted(str(row[0]) for row in result.all())


async def _families_metadata(conn: AsyncConnection) -> dict[str, dict]:
    rows = (await conn.execute(text("SELECT family_id, metadata FROM families"))).all()
    return {str(family_id): metadata for family_id, metadata in rows}


@_MODES
def test_a_database_from_before_eng6_loses_the_unused_columns_and_the_unresolved_roi_key(
    monkeypatch, reapply_baselines: bool
) -> None:
    import json

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)
    unresolved = {"query": "NOT_A_GENE", "source": "manifest"}
    pipeline = {"reference": "GRCh38"}

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0010 from before ENG-6, with a row in each table it changes:
            # a family whose import could not resolve its manifest's ROI, and one without the key.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0010'"))
                for statement in _BEFORE_0011:
                    await conn.exec_driver_sql(statement)
                families = {}
                for family_id, metadata in (
                    ("FAM-ENG6-ROI", {"unresolved_roi": unresolved, "pipeline": pipeline}),
                    ("FAM-ENG6", {"pipeline": pipeline}),
                ):
                    families[family_id] = (
                        await conn.execute(
                            text(
                                "INSERT INTO families (family_id, metadata) VALUES (:f, CAST(:m AS jsonb)) "
                                "RETURNING id::text"
                            ),
                            {"f": family_id, "m": json.dumps(metadata)},
                        )
                    ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO auth_login_attempts (scope_type, scope_value, failure_count, last_failure_at, "
                        "last_success_at) VALUES ('email', 'analyst@example.org', 2, now(), now())"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO small_variant_reviews (family_id, variant_key, variant_id, tags, "
                        "compound_het_partner_variant_keys, updated_by) VALUES (CAST(:f AS uuid), 11, '1-3000-G-A', "
                        "'[\"report\"]'::jsonb, '[12]'::jsonb, 'analyst')"
                    ),
                    {"f": families["FAM-ENG6"]},
                )
                await conn.execute(
                    text(
                        "INSERT INTO structural_variant_reviews (family_id, variant_id, classification, tags, "
                        "updated_by) VALUES (CAST(:f AS uuid), '1-30000-31000-DEL---', 'Pathogenic - class 5', "
                        "'[\"report\"]'::jsonb, 'analyst')"
                    ),
                    {"f": families["FAM-ENG6"]},
                )
                before = {table: await _rows_without(conn, table, column) for table, column in _DROPPED_BY_0011.items()}
                metadata_before = await _families_metadata(conn)
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                after = {table: await _rows_without(conn, table, column) for table, column in _DROPPED_BY_0011.items()}
                metadata_after = await _families_metadata(conn)
                left = (
                    await conn.execute(
                        text(
                            "SELECT table_name, column_name FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND (table_name, column_name) IN "
                            "(('auth_login_attempts', 'last_success_at'), "
                            "('small_variant_reviews', 'compound_het_partner_variant_keys'), "
                            "('structural_variant_reviews', 'variant_key'))"
                        )
                    )
                ).all()
                index = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_indexes "
                            "WHERE indexname = 'idx_structural_variant_reviews_family_variant_key'"
                        )
                    )
                ).scalar_one()
            # The next start finds nothing left to do.
            async with engine.begin() as conn:
                again = await apply_postgres_schema(conn)
            unchanged = await _table_contents(database)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return (
            (revision, again),
            (before, after),
            (metadata_before, metadata_after),
            (contents, kept, unchanged),
            (left, index),
            fingerprints,
        )

    (
        (revision, again),
        (before, after),
        (metadata_before, metadata_after),
        (contents, kept, unchanged),
        (left, index),
        (expected, actual),
    ) = _run(_body)

    assert (revision.before, revision.after) == ("0010", head_revision())
    assert (again.before, again.after) == (head_revision(), head_revision())
    # The three columns are gone, the reviews' index with its column; every row is kept with
    # every other value as it was.
    assert left == [] and index == 0
    assert after == before
    assert [len(before[table]) for table in _DROPPED_BY_0011] == [1, 1, 1]
    # The family whose ROI did not resolve keeps its other metadata; the other family is as it was.
    assert metadata_before["FAM-ENG6-ROI"] == {"unresolved_roi": unresolved, "pipeline": pipeline}
    assert metadata_after == {"FAM-ENG6-ROI": {"pipeline": pipeline}, "FAM-ENG6": {"pipeline": pipeline}}
    # Every other table is as it was, and a later start changes nothing.
    changed = {"alembic_version", "families", *_DROPPED_BY_0011}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    assert unchanged == kept
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)


_OLD_LP = "Likely Pathogenic - class 4"
_NEW_LP = "Likely pathogenic - class 4"
# The mutable tables revision 0013 rewrites.
_LABELLED_BY_0013 = (
    "small_variant_reviews",
    "structural_variant_reviews",
    "small_variant_filter_presets",
    "structural_variant_filter_presets",
)


async def _labelled_rows(conn: AsyncConnection) -> dict[str, dict]:
    """What revision 0013 may rewrite, by row: the reviews' classifications and scorings, with
    their ``updated_at``, and the presets' filters."""

    queries = {
        "small": (
            "SELECT variant_id, classification, compound_het_classification, acmg, updated_at::text "
            "FROM small_variant_reviews"
        ),
        "structural": "SELECT variant_id, classification, cnv_acmg, updated_at::text FROM structural_variant_reviews",
        "small_presets": "SELECT name, filters, updated_at::text FROM small_variant_filter_presets",
        "structural_presets": "SELECT name, filters, updated_at::text FROM structural_variant_filter_presets",
    }
    return {
        name: {row[0]: tuple(row[1:]) for row in (await conn.execute(text(query))).all()}
        for name, query in queries.items()
    }


@_MODES
def test_a_database_from_before_eng6_gets_its_class_labels_in_sentence_case_and_its_records_kept(
    monkeypatch, reapply_baselines: bool
) -> None:
    import json
    from datetime import datetime, timezone

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)
    saved = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    async def _insert(conn: AsyncConnection, statement: str, **params) -> None:
        # A dict or list goes as JSON text, for the statement to cast.
        encoded = {key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in params.items()}
        await conn.execute(text(statement), encoded)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0012 from before the labels were put in sentence case: the
            # reviews and presets the code wrote then, and the records and logs it signed and wrote.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0012'"))
                account = (
                    await conn.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES ('analyst-eng6', '', 'viewer', 'analyst-eng6@example.org') RETURNING id::text"
                        )
                    )
                ).scalar_one()
                family = (
                    await conn.execute(
                        text("INSERT INTO families (family_id) VALUES ('FAM-ENG6-LABELS') RETURNING id::text")
                    )
                ).scalar_one()
                small = (
                    "INSERT INTO small_variant_reviews (family_id, variant_id, classification, "
                    "compound_het_classification, acmg, updated_by, updated_at) VALUES (CAST(:f AS uuid), :v, :c, "
                    ":p, CAST(:a AS jsonb), 'analyst', CAST(:t AS timestamptz))"
                )
                for variant_id, classification, pair, acmg in (
                    # The dialog's label of the time, its pair's in another case, and its scoring's.
                    ("1-100-A-G", _OLD_LP, "likely pathogenic - CLASS 4", {"criteria": [], "point_total": 7, "classification": _OLD_LP}),
                    # A label already as the code writes it, and text that is no class label.
                    ("1-200-A-G", "Pathogenic - class 5", None, None),
                    ("1-300-A-G", "Likely pathogenic, see note", "VUS - class 3", "null"),
                ):
                    await _insert(conn, small, f=family, v=variant_id, c=classification, p=pair, a=acmg, t=saved)
                await _insert(
                    conn,
                    "INSERT INTO structural_variant_reviews (family_id, variant_id, classification, cnv_acmg, "
                    "updated_by, updated_at) VALUES (CAST(:f AS uuid), '1-1000-5000-DEL---', 'VUS - CLASS 3', "
                    "CAST(:a AS jsonb), 'analyst', CAST(:t AS timestamptz))",
                    f=family,
                    a={"kind": "loss", "criteria": [], "point_total": 0.9, "classification": _OLD_LP},
                    t=saved,
                )
                small_preset = (
                    "INSERT INTO small_variant_filter_presets (owner, owner_id, name, filters, updated_at) "
                    "VALUES ('analyst-eng6', CAST(:o AS uuid), :n, CAST(:fl AS jsonb), CAST(:t AS timestamptz))"
                )
                structural_preset = (
                    "INSERT INTO structural_variant_filter_presets (scope, owner, owner_id, name, filters, updated_at) "
                    "VALUES ('global', 'analyst-eng6', CAST(:o AS uuid), :n, CAST(:fl AS jsonb), CAST(:t AS timestamptz))"
                )
                for statement, name, filters in (
                    # A small variant preset's list, and the comma-separated text a link or an
                    # older client may have stored; a preset with no classification filter.
                    (small_preset, "eng6 list", {"classification": ["Pathogenic - class 5", _OLD_LP, "Custom words"], "impact": ["HIGH"]}),
                    (small_preset, "eng6 text", {"classification": f"{_OLD_LP},, vus - class 3"}),
                    (small_preset, "eng6 none", {"impact": ["HIGH"]}),
                    # A structural variant preset's comma-separated text, and one already right.
                    (structural_preset, "eng6 text", {"classification": f"Pathogenic - class 5, {_OLD_LP}"}),
                    (structural_preset, "eng6 current", {"classification": f"Pathogenic - class 5, {_NEW_LP}"}),
                ):
                    await _insert(conn, statement, o=account, n=name, fl=filters, t=saved)
                # The append-only records of the time hold the label as it was then.
                await _insert(
                    conn,
                    "INSERT INTO clinical_audit_events (family_id, family_identifier, variant_id, actor, action, "
                    "summary, before, after) VALUES (CAST(:f AS uuid), 'FAM-ENG6-LABELS', '1-100-A-G', 'analyst', "
                    "'classification', :s, CAST(:b AS jsonb), CAST(:a AS jsonb))",
                    f=family,
                    s=f"Classification: unclassified → {_OLD_LP}",
                    b={"classification": None},
                    a={"classification": _OLD_LP},
                )
                await _insert(
                    conn,
                    "INSERT INTO report_signouts (family_id, family_identifier, version, signed_out_by, content_hash, "
                    "snapshot) VALUES (CAST(:f AS uuid), 'FAM-ENG6-LABELS', 1, 'analyst', :h, CAST(:s AS jsonb))",
                    f=family,
                    h="0" * 64,
                    s={"reported_variants": [{"variant_id": "1-100-A-G", "classification": _OLD_LP}]},
                )
                await _insert(
                    conn,
                    "INSERT INTO audit_log_events (method, path, query_string, status_code) VALUES "
                    "('GET', '/api/families/FAM-ENG6-LABELS/small-variants', :q, 200)",
                    q="classification=Likely+Pathogenic+-+class+4",
                )
                await _insert(
                    conn,
                    "INSERT INTO ui_events (event_type, label, detail) VALUES ('click', :l, CAST(:d AS jsonb))",
                    l=_OLD_LP,
                    d={"classification": _OLD_LP},
                )
                before = await _labelled_rows(conn)
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                after = await _labelled_rows(conn)
                signed = (await conn.execute(text("SELECT snapshot FROM report_signouts"))).scalar_one()
                audited = (await conn.execute(text("SELECT summary, after FROM clinical_audit_events"))).one()
            # The next start finds nothing left to do.
            async with engine.begin() as conn:
                again = await apply_postgres_schema(conn)
            unchanged = await _table_contents(database)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return (revision, again), (before, after), (signed, tuple(audited)), (contents, kept, unchanged), fingerprints

    (revision, again), (before, after), (signed, audited), (contents, kept, unchanged), (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0012", head_revision())
    assert (again.before, again.after) == (head_revision(), head_revision())
    # The reviews: each class label as the code writes it now, its scoring's too; text that is
    # no class label, a label already right, a scoring that is JSON null, and when each review
    # was saved, as they were.
    lp_scoring = {"criteria": [], "point_total": 7, "classification": _NEW_LP}
    assert after["small"] == {
        "1-100-A-G": (_NEW_LP, _NEW_LP, lp_scoring, before["small"]["1-100-A-G"][3]),
        "1-200-A-G": before["small"]["1-200-A-G"],
        "1-300-A-G": before["small"]["1-300-A-G"],
    }
    assert before["small"]["1-100-A-G"][:3] == (
        _OLD_LP,
        "likely pathogenic - CLASS 4",
        {"criteria": [], "point_total": 7, "classification": _OLD_LP},
    )
    assert after["structural"] == {
        "1-1000-5000-DEL---": (
            "VUS - class 3",
            {"kind": "loss", "criteria": [], "point_total": 0.9, "classification": _NEW_LP},
            before["structural"]["1-1000-5000-DEL---"][2],
        )
    }
    # The presets: each class label in a list, or in comma-separated text joined as the form
    # joins it; any other filter value, and a preset without a class label to rewrite, as it was.
    assert after["small_presets"] == {
        "eng6 list": (
            {"classification": ["Pathogenic - class 5", _NEW_LP, "Custom words"], "impact": ["HIGH"]},
            before["small_presets"]["eng6 list"][1],
        ),
        "eng6 text": ({"classification": f"{_NEW_LP}, VUS - class 3"}, before["small_presets"]["eng6 text"][1]),
        "eng6 none": before["small_presets"]["eng6 none"],
    }
    assert after["structural_presets"] == {
        "eng6 text": (
            {"classification": f"Pathogenic - class 5, {_NEW_LP}"},
            before["structural_presets"]["eng6 text"][1],
        ),
        "eng6 current": before["structural_presets"]["eng6 current"],
    }
    # The signed record and the audit trail keep the label as it was then, and so do the request
    # audit and the UI-event log: every table but the four, and the ledger, is as it was.
    assert signed == {"reported_variants": [{"variant_id": "1-100-A-G", "classification": _OLD_LP}]}
    assert audited == (f"Classification: unclassified → {_OLD_LP}", {"classification": _OLD_LP})
    changed = {"alembic_version", *_LABELLED_BY_0013}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    for table in ("clinical_audit_events", "report_signouts", "audit_log_events", "ui_events"):
        assert kept[table][0] == 1, table
    assert unchanged == kept
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)


# QA's repeat record as a database had it before CLIN-57 (revision 0014): no count correction.
_BEFORE_0014 = (
    "ALTER TABLE repeat_locus_count_units DROP CONSTRAINT repeat_locus_count_units_count_offset_check",
    "ALTER TABLE repeat_locus_count_units DROP COLUMN count_offset",
)


@_MODES
def test_a_database_from_before_clin57_gets_the_count_correction_with_every_record_kept_without_one(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import IntegrityError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0013 from before CLIN-57, with QA's record of one locus.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0013'"))
                for statement in _BEFORE_0014:
                    await conn.exec_driver_sql(statement)
                await conn.execute(
                    text(
                        "INSERT INTO repeat_loci (locus_id, gene, display_name, disease) "
                        "VALUES ('HD_HTT', 'HTT', 'HTT', 'Huntington disease')"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO repeat_locus_count_units (locus_id, validated, reason, updated_by_name) "
                        "VALUES ('HD_HTT', true, 'Validation report 7', 'qa-lead')"
                    )
                )
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                record = dict(
                    (
                        await conn.execute(
                            text(
                                "SELECT locus_id, validated, count_offset, reason, updated_by_name "
                                "FROM repeat_locus_count_units"
                            )
                        )
                    ).mappings().one()
                )
                check = await _check_definition(
                    conn, "repeat_locus_count_units", "repeat_locus_count_units_count_offset_check"
                )
            # The check holds the bound on the migrated database.
            refused = 0
            for offset in (21, -21):
                try:
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("UPDATE repeat_locus_count_units SET count_offset = :o"), {"o": offset}
                        )
                except IntegrityError:
                    refused += 1
            # The next start finds nothing left to do.
            async with engine.begin() as conn:
                again = await apply_postgres_schema(conn)
            unchanged = await _table_contents(database)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return (revision, again), record, check, refused, (contents, kept, unchanged), fingerprints

    (revision, again), record, check, refused, (contents, kept, unchanged), (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0013", head_revision())
    assert (again.before, again.after) == (head_revision(), head_revision())
    # The record stored before is kept, with no correction.
    assert record == {
        "locus_id": "HD_HTT",
        "validated": True,
        "count_offset": 0,
        "reason": "Validation report 7",
        "updated_by_name": "qa-lead",
    }
    assert check == "CHECK (((count_offset >= '-20'::integer) AND (count_offset <= 20)))"
    assert refused == 2
    # Every other table is as it was, and a later start changes nothing.
    changed = {"alembic_version", "repeat_locus_count_units"}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    assert kept["repeat_locus_count_units"][0] == contents["repeat_locus_count_units"][0] == 1
    assert unchanged == kept
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)


# The accounts' roles as a database had them before the analyst and supervisor roles (revision
# 0015): viewer, superuser and admin only.
_BEFORE_0015 = (
    "ALTER TABLE users DROP CONSTRAINT users_role_check",
    "ALTER TABLE users ADD CONSTRAINT users_role_check "
    "CHECK (role = ANY (ARRAY['admin'::text, 'superuser'::text, 'viewer'::text]))",
)


@_MODES
def test_a_database_from_before_the_analyst_role_makes_each_viewer_an_analyst_and_keeps_the_rest(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import IntegrityError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)
    accounts = {"viewer-roles": "viewer", "superuser-roles": "superuser", "admin-roles": "admin"}

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0014, with the check from before and an account of each role.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0014'"))
                for statement in _BEFORE_0015:
                    await conn.exec_driver_sql(statement)
                for username, role in accounts.items():
                    await conn.execute(
                        text(
                            "INSERT INTO users (username, hashed_password, role, email) "
                            "VALUES (:u, '', :r, :e)"
                        ),
                        {"u": username, "r": role, "e": f"{username}@example.org"},
                    )
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                roles = {
                    str(row[0]): str(row[1])
                    for row in (
                        await conn.execute(
                            text("SELECT username, role FROM users WHERE username = ANY(:names)"),
                            {"names": list(accounts)},
                        )
                    ).all()
                }
                check = await _check_definition(conn, "users", "users_role_check")
            # The check holds the five roles and no other.
            stored, refused = [], 0
            for role in ("analyst", "supervisor", "viewer", "signer"):
                try:
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("UPDATE users SET role = :r WHERE username = 'viewer-roles'"), {"r": role}
                        )
                    stored.append(role)
                except IntegrityError:
                    refused += 1
            # A viewer made after the revision stays a viewer at the next start.
            async with engine.begin() as conn:
                again = await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                after_again = (
                    await conn.execute(text("SELECT role FROM users WHERE username = 'viewer-roles'"))
                ).scalar_one()
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return (revision, again), roles, check, (stored, refused, after_again), (contents, kept), fingerprints

    (revision, again), roles, check, (stored, refused, after_again), (contents, kept), (expected, actual) = _run(
        _body
    )

    assert (revision.before, revision.after) == ("0014", head_revision())
    assert (again.before, again.after) == (head_revision(), head_revision())
    # The viewer from before becomes an analyst; the superuser and the admin keep their role.
    assert roles == {"viewer-roles": "analyst", "superuser-roles": "superuser", "admin-roles": "admin"}
    assert check == (
        "CHECK ((role = ANY (ARRAY['viewer'::text, 'analyst'::text, 'supervisor'::text, "
        "'superuser'::text, 'admin'::text])))"
    )
    assert (stored, refused) == (["analyst", "supervisor", "viewer"], 1)
    assert after_again == "viewer"
    # Every other table is as it was.
    changed = {"alembic_version", "users"}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    assert kept["users"][0] == contents["users"][0]
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)


_BEFORE_0017 = (
    "ALTER TABLE users DROP CONSTRAINT users_archived_inactive_check",
    "ALTER TABLE users DROP COLUMN archived_at",
    "ALTER TABLE users DROP COLUMN activated_at",
)


@_MODES
def test_a_database_from_before_the_archive_tells_each_account_waiting_for_approval_from_the_others(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import IntegrityError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)
    # Each account's activation, and whether it made a request of its own (an audit row).
    accounts = {"active-0017": (True, False), "seen-0017": (False, True), "waiting-0017": (False, False)}

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0016, from before the two columns and their check.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0016'"))
                for statement in _BEFORE_0017:
                    await conn.exec_driver_sql(statement)
                for username, (active, audited) in accounts.items():
                    user_id = (
                        await conn.execute(
                            text(
                                "INSERT INTO users (username, hashed_password, role, email, is_active, created_at) "
                                "VALUES (:u, '', 'viewer', :e, :a, '2026-01-02T03:04:05Z') RETURNING id"
                            ),
                            {"u": username, "e": f"{username}@example.org", "a": active},
                        )
                    ).scalar_one()
                    if audited:
                        await conn.execute(
                            text(
                                "INSERT INTO audit_log_events (method, path, status_code, user_id) "
                                "VALUES ('GET', '/api/auth/me', 200, :id)"
                            ),
                            {"id": user_id},
                        )
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                stored = {
                    str(row.username): (
                        None if row.activated_at is None else row.activated_at == row.created_at,
                        row.archived_at,
                    )
                    for row in (
                        await conn.execute(
                            text(
                                "SELECT username, created_at, activated_at, archived_at FROM users "
                                "WHERE username = ANY(:names)"
                            ),
                            {"names": list(accounts)},
                        )
                    ).all()
                }
                check = await _check_definition(conn, "users", "users_archived_inactive_check")
            # An archived account that is active is refused.
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("UPDATE users SET archived_at = now() WHERE username = 'active-0017'"))
                refused = False
            except IntegrityError:
                refused = True
            # An account waiting for approval made since still waits at the next start.
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO users (username, hashed_password, role, email, is_active, activated_at) "
                        "VALUES ('later-0017', '', 'viewer', 'later-0017@example.org', false, NULL)"
                    )
                )
            async with engine.begin() as conn:
                again = await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                later = (
                    await conn.execute(text("SELECT activated_at FROM users WHERE username = 'later-0017'"))
                ).scalar_one()
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return (revision, again), stored, check, refused, later, (contents, kept), fingerprints

    (revision, again), stored, check, refused, later, (contents, kept), (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0016", head_revision())
    assert (again.before, again.after) == (head_revision(), head_revision())
    # Active, or once signed in: active since its creation. Never signed in: waits for approval.
    assert stored == {"active-0017": (True, None), "seen-0017": (True, None), "waiting-0017": (None, None)}
    assert check == "CHECK (((archived_at IS NULL) OR (NOT is_active)))"
    assert refused is True
    assert later is None
    # Every other table is as it was; the accounts keep their rows.
    changed = {"alembic_version", "users"}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    assert kept["users"][0] == contents["users"][0]
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)


_BEFORE_0018 = (
    "ALTER TABLE family_import_jobs DROP CONSTRAINT family_import_jobs_manifest_check",
    "ALTER TABLE family_import_jobs DROP COLUMN manifest_sha256",
    "ALTER TABLE family_import_jobs DROP COLUMN manifest_text",
)


@_MODES
def test_a_database_from_before_kept_manifests_gives_each_import_job_none(monkeypatch, reapply_baselines: bool) -> None:
    from sqlalchemy.exc import IntegrityError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0017, from before the two columns and their check, with a
            # job that read its folder's manifest.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0017'"))
                for statement in _BEFORE_0018:
                    await conn.exec_driver_sql(statement)
                await conn.execute(
                    text(
                        "INSERT INTO family_import_jobs (submitted_path, status, requested_by) "
                        "VALUES ('/data/families/FAM1', 'completed', 'superuser@example.org')"
                    )
                )
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                stored = (
                    await conn.execute(
                        text("SELECT manifest_text, manifest_sha256 FROM family_import_jobs")
                    )
                ).all()
                check = await _check_definition(conn, "family_import_jobs", "family_import_jobs_manifest_check")
            # A job that keeps a manifest without its SHA-256 is refused.
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("UPDATE family_import_jobs SET manifest_text = 'family_id: FAM1'"))
                refused = False
            except IntegrityError:
                refused = True
            async with engine.begin() as conn:
                again = await apply_postgres_schema(conn)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return (revision, again), stored, check, refused, (contents, kept), fingerprints

    (revision, again), stored, check, refused, (contents, kept), (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0017", head_revision())
    assert (again.before, again.after) == (head_revision(), head_revision())
    assert [tuple(row) for row in stored] == [(None, None)]
    assert check == "CHECK (((manifest_text IS NULL) = (manifest_sha256 IS NULL)))"
    assert refused is True
    # Every other table is as it was; the job keeps its row.
    changed = {"alembic_version", "family_import_jobs"}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    assert kept["family_import_jobs"][0] == contents["family_import_jobs"][0]
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)


_BEFORE_0019 = (
    "ALTER TABLE family_import_jobs DROP CONSTRAINT family_import_jobs_package_fingerprint_check",
    "ALTER TABLE family_import_jobs DROP COLUMN package_fingerprint",
)


@_MODES
def test_a_database_from_before_package_fingerprints_gives_each_import_job_none(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import IntegrityError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0018, from before the column and its check, with a job
            # queued without a fingerprint.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0018'"))
                for statement in _BEFORE_0019:
                    await conn.exec_driver_sql(statement)
                await conn.execute(
                    text(
                        "INSERT INTO family_import_jobs (submitted_path, status, requested_by) "
                        "VALUES ('/data/families/FAM1', 'completed', 'superuser@example.org')"
                    )
                )
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                stored = (await conn.execute(text("SELECT package_fingerprint FROM family_import_jobs"))).all()
                check = await _check_definition(
                    conn, "family_import_jobs", "family_import_jobs_package_fingerprint_check"
                )
            # A fingerprint that is no SHA-256 is refused; one that is, is kept.
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("UPDATE family_import_jobs SET package_fingerprint = 'ABC'"))
                refused = False
            except IntegrityError:
                refused = True
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE family_import_jobs SET package_fingerprint = :fingerprint"),
                    {"fingerprint": "0" * 64},
                )
            async with engine.begin() as conn:
                again = await apply_postgres_schema(conn)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return (revision, again), stored, check, refused, (contents, kept), fingerprints

    (revision, again), stored, check, refused, (contents, kept), (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0018", head_revision())
    assert (again.before, again.after) == (head_revision(), head_revision())
    assert [tuple(row) for row in stored] == [(None,)]
    assert check == "CHECK (((package_fingerprint IS NULL) OR (package_fingerprint ~ '^[0-9a-f]{64}$'::text)))"
    assert refused is True
    # Every other table is as it was; the job keeps its row.
    changed = {"alembic_version", "family_import_jobs"}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    assert kept["family_import_jobs"][0] == contents["family_import_jobs"][0]
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)


_BEFORE_0020 = (
    "DROP INDEX idx_gene_panels_source_external_selection",
    "ALTER TABLE gene_panels DROP COLUMN external_selection",
    "CREATE UNIQUE INDEX idx_gene_panels_source_external ON gene_panels USING btree (source, external_id) "
    "WHERE (external_id IS NOT NULL)",
)


@_MODES
def test_a_database_from_before_panelapp_selections_gives_each_panelapp_panel_its_levels(
    monkeypatch, reapply_baselines: bool
) -> None:
    from sqlalchemy.exc import IntegrityError

    from backend.app.core import postgres_migrations
    from backend.app.core.postgres_migrations import apply_postgres_schema, head_revision

    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", reapply_baselines)
    insert_panel = text(
        "INSERT INTO gene_panels (name, source, external_id, source_metadata, created_by) "
        "SELECT :name, :source, :external_id, CAST(:metadata AS jsonb), id FROM users"
    )

    async def _body(database: str):
        engine = _engine(database)
        try:
            async with engine.begin() as conn:
                await apply_postgres_schema(conn)
            # A database at revision 0019, one panel per external panel: a PanelApp panel whose
            # last import took green and amber, one whose metadata records no levels, the
            # Mendeliome and a local panel.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0019'"))
                for statement in _BEFORE_0020:
                    await conn.exec_driver_sql(statement)
                await conn.execute(
                    text(
                        "INSERT INTO users (username, hashed_password, role, email) "
                        "VALUES ('panels@example.org', '', 'admin', 'panels@example.org')"
                    )
                )
                for name, source, external_id, metadata in (
                    ("PanelApp 20", "panelapp", "20", '{"import_options": {"confidence_levels": ["3", "2"]}}'),
                    ("PanelApp 21", "panelapp", "21", "{}"),
                    ("Mendeliome", "mendeliome", "mendeliome", '{"import_options": {"confidence_levels": ["3"]}}'),
                    ("Local", "local", None, "{}"),
                ):
                    await conn.execute(
                        insert_panel,
                        {"name": name, "source": source, "external_id": external_id, "metadata": metadata},
                    )
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                stored = (
                    await conn.execute(text("SELECT name, external_selection FROM gene_panels ORDER BY name"))
                ).all()
                indexes = (
                    await conn.execute(
                        text("SELECT indexname FROM pg_indexes WHERE tablename = 'gene_panels' ORDER BY 1")
                    )
                ).scalars().all()
            # The same PanelApp panel with other levels is a panel of its own; the same
            # selection twice is refused, and so is a second Mendeliome (no selection).
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO gene_panels (name, source, external_id, external_selection, created_by) "
                        "SELECT 'PanelApp 20 green', 'panelapp', '20', '3', id FROM users"
                    )
                )
            refused = []
            for name, source, external_id, selection in (
                ("PanelApp 20 again", "panelapp", "20", "2,3"),
                ("Mendeliome again", "mendeliome", "mendeliome", None),
            ):
                try:
                    async with engine.begin() as conn:
                        await conn.execute(
                            text(
                                "INSERT INTO gene_panels "
                                "(name, source, external_id, external_selection, created_by) "
                                "SELECT :name, :source, :external_id, :selection, id FROM users"
                            ),
                            {"name": name, "source": source, "external_id": external_id, "selection": selection},
                        )
                    refused.append(False)
                except IntegrityError:
                    refused.append(True)
            async with engine.begin() as conn:
                again = await apply_postgres_schema(conn)
            async with engine.connect() as conn:
                after_again = (
                    await conn.execute(text("SELECT name, external_selection FROM gene_panels ORDER BY name"))
                ).all()
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return (revision, again), (stored, after_again), indexes, refused, (contents, kept), fingerprints

    (revision, again), (stored, after_again), indexes, refused, (contents, kept), (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0019", head_revision())
    assert (again.before, again.after) == (head_revision(), head_revision())
    assert [tuple(row) for row in stored] == [
        ("Local", None),
        ("Mendeliome", None),
        ("PanelApp 20", "2,3"),
        ("PanelApp 21", None),
    ]
    assert indexes == [
        "gene_panels_name_key",
        "gene_panels_pkey",
        "idx_gene_panels_source_external_selection",
    ]
    assert refused == [True, True]
    # A later start changes nothing: the panel made since keeps its selection.
    assert [tuple(row) for row in after_again] == [
        ("Local", None),
        ("Mendeliome", None),
        ("PanelApp 20", "2,3"),
        ("PanelApp 20 green", "3"),
        ("PanelApp 21", None),
    ]
    # Every other table is as it was; the panels keep their rows.
    changed = {"alembic_version", "gene_panels"}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    assert kept["gene_panels"][0] == contents["gene_panels"][0]
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)


_BEFORE_0021 = ("ALTER TABLE users DROP COLUMN sessions_ended_at",)


@_MODES
def test_a_database_from_before_server_side_logout_gives_each_account_no_logout_time(
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
            # A database at revision 0020, from before the column, with an account.
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE alembic_version SET version_num = '0020'"))
                for statement in _BEFORE_0021:
                    await conn.exec_driver_sql(statement)
                await conn.execute(
                    text(
                        "INSERT INTO users (username, hashed_password, role, email) "
                        "VALUES ('logout@example.org', '', 'analyst', 'logout@example.org')"
                    )
                )
            contents = await _table_contents(database)
            async with engine.begin() as conn:
                revision = await apply_postgres_schema(conn)
            kept = await _table_contents(database)
            async with engine.connect() as conn:
                stored = (
                    await conn.execute(text("SELECT email, sessions_ended_at FROM users ORDER BY email"))
                ).all()
            async with engine.begin() as conn:
                again = await apply_postgres_schema(conn)
        finally:
            await engine.dispose()
        reference = await _create_database()
        try:
            await _apply_baseline_files(reference)
            fingerprints = await _fingerprint(reference), await _fingerprint(database)
        finally:
            await _drop_database(reference)
        return (revision, again), stored, (contents, kept), fingerprints

    (revision, again), stored, (contents, kept), (expected, actual) = _run(_body)

    assert (revision.before, revision.after) == ("0020", head_revision())
    assert (again.before, again.after) == (head_revision(), head_revision())
    assert ("logout@example.org", None) in [tuple(row) for row in stored]
    assert all(ended is None for _email, ended in stored)
    # Every other table is as it was; each account keeps its row.
    changed = {"alembic_version", "users"}
    assert {table: rows for table, rows in kept.items() if table not in changed} == {
        table: rows for table, rows in contents.items() if table not in changed
    }
    assert kept["users"][0] == contents["users"][0]
    # The schema is the baselines'.
    assert actual == expected, _differences(expected, actual)
