"""The Postgres schema migration: the Alembic revisions, of which revision 0001 makes an empty
database from the five baseline files.

The API runs it at startup when it connects as the table owner
(``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP``, the default), and ``python -m app.db_migrate``
runs it before a rollout when the API connects as the restricted runtime role ``coga_app``.
Both call ``init_postgres_schema`` (core/postgres.py), which calls ``apply_postgres_schema``
here. It runs in one transaction, so it applies whole or not at all:

1. a transaction-level advisory lock, so that two instances starting together migrate one
   after the other;
2. a refusal when connected as ``coga_app``: migrations run as the owner, and ``coga_app``
   may neither run DDL nor write the ledger;
3. a refusal of any database this version did not make: it migrates only an empty database
   (an empty ledger and no table in the public schema) or one whose ledger (``alembic_version``)
   names one of its revisions and whose baselines' digest (``schema_baselines``) equals its
   own (``baseline_digest``). Tables without a ledger, a ledger naming a revision this version
   does not have, and a database made from other baselines are refused before anything runs;
4. the Alembic revisions (``backend/db/migrations/versions/``) the ledger does not list yet,
   recorded there as they run. Revision 0001 applies the baseline files
   (``backend/db/schema/postgres/``) to the empty database and records their digest.

Until the release candidate a schema change is an edit of a baseline and revision 0001 is the
only revision, so a database made from other baselines is made again from empty, never
migrated. The release candidate freezes the baselines: from then on every schema change is a
revision of its own, and the digest stays that of the frozen baselines (docs/database.md,
"Schema Migrations").

A start that does not migrate (``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false``, as with
``coga_app``) runs ``check_schema_revision`` instead: it only reads the ledger and the digest,
and refuses to start unless they are this version's newest revision and its baselines.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncConnection

from .postgres import _schema_files, _split_sql_script

logger = logging.getLogger(__name__)

# The Alembic script directory: env.py, the revision template and versions/.
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

# The restricted runtime role (05_grants.sql). It never runs a migration.
APP_ROLE = "coga_app"

_LOCK_KEY = "coga:postgres-schema-migration"

# Where a database made from other baselines, or by another version, is sent.
_SCHEMA_DOC = 'docs/database.md, "Schema Migrations"'


class SchemaMigrationRefused(RuntimeError):
    """The migration did not run: the wrong role, or a database this version did not make."""


class SchemaRevisionMismatch(RuntimeError):
    """A start that does not migrate found a database this version did not bring to its schema."""


@dataclass(frozen=True)
class SchemaRevision:
    """The ledger's revision before and after a migration run.

    ``before`` is None for an empty database, which the run made.
    """

    before: str | None
    after: str


def apply_baselines(connection: Connection) -> None:
    """Run every statement of the five baseline files, in name order, on ``connection``."""

    for path in _schema_files():
        for statement in _split_sql_script(path.read_text()):
            connection.exec_driver_sql(statement)


def baseline_digest() -> str:
    """The SHA-256 over the five baseline files, each file's name and bytes in name order."""

    digest = hashlib.sha256()
    for path in _schema_files():
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def record_baseline_digest(connection: Connection) -> None:
    """Record which baselines made the database (revision 0001, right after it applied them)."""

    connection.execute(
        text("INSERT INTO schema_baselines (digest) VALUES (:digest)"),
        {"digest": baseline_digest()},
    )


def stored_baseline_digest(connection: Connection) -> str | None:
    """The digest of the baselines the database was made from, or None when it records none."""

    if connection.execute(text("SELECT to_regclass('public.schema_baselines')")).scalar() is None:
        return None
    return connection.execute(text("SELECT digest FROM schema_baselines")).scalar()


def _public_table_count(connection: Connection) -> int:
    return int(
        connection.execute(
            text("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public'")
        ).scalar_one()
    )


def migration_script() -> ScriptDirectory:
    return ScriptDirectory(str(MIGRATIONS_DIR))


def head_revision() -> str:
    """The newest revision this version of CoGA has."""

    head = migration_script().get_current_head()
    if head is None:  # pragma: no cover - the directory always holds revision 0001
        raise SchemaMigrationRefused(f"No schema revisions in {MIGRATIONS_DIR}.")
    return head


def ledger_revision(connection: Connection) -> str | None:
    """The revision the ledger records, or None when it is empty or missing."""

    heads = MigrationContext.configure(connection).get_current_heads()
    if len(heads) > 1:
        raise SchemaMigrationRefused(
            "The schema ledger (alembic_version) names more than one revision: "
            f"{', '.join(sorted(heads))}. CoGA's revisions form one line."
        )
    return heads[0] if heads else None


def refuse_unknown_revision(revision: str | None, script: ScriptDirectory) -> None:
    """Refuse a ledger that names a revision this version of CoGA does not have."""

    if revision is None:
        return
    known = {script_revision.revision for script_revision in script.walk_revisions()}
    if revision not in known:
        raise SchemaMigrationRefused(
            f"The database is at schema revision {revision}, which this version of CoGA "
            f"does not have (its newest is {script.get_current_head()}): another version made "
            "or migrated it. Run that version, or restore the database from the backup taken "
            f"before that version migrated it ({_SCHEMA_DOC})."
        )


def refuse_unless_made_here(connection: Connection, revision: str | None) -> None:
    """Refuse a database this version did not make: with tables but no ledger, or made from
    other baselines than this version's."""

    if revision is None:
        tables = _public_table_count(connection)
        if tables:
            raise SchemaMigrationRefused(
                f"The database holds {tables} table(s) in its public schema but no schema "
                "ledger (alembic_version), so CoGA's migration did not make it. CoGA makes its "
                f"schema only in an empty database: point it at an empty one ({_SCHEMA_DOC})."
            )
        return
    stored = stored_baseline_digest(connection)
    if stored != baseline_digest():
        raise SchemaMigrationRefused(
            "The database was made from other baseline files than this version's "
            f"(schema_baselines records {stored or 'none'}, this version has "
            f"{baseline_digest()}), so this version does not migrate it ({_SCHEMA_DOC})."
        )


def _refuse_start(message: str) -> SchemaRevisionMismatch:
    # Logged as well as raised: the log writes a failed start's traceback without the
    # exception's message (core/coga_logging.py), and the operator needs this one.
    logger.error("%s", message)
    return SchemaRevisionMismatch(message)


def check_schema_revision(connection: Connection) -> str:
    """This version's newest revision, when the database on ``connection`` is at exactly that
    revision and was made from this version's baselines.

    For a start that does not migrate (``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false``, as
    with the restricted runtime role ``coga_app``): the migration step runs separately, as the
    owner, before the rollout. Any other database raises ``SchemaRevisionMismatch``, and the
    message is logged. An older revision or none means the migration did not run for this
    version; a revision this version does not have means another version made or migrated the
    database; other baselines mean it was made from them. It only reads the ledger and the
    digest, which ``coga_app`` may.
    """

    head = head_revision()
    try:
        current = ledger_revision(connection)
    except SchemaMigrationRefused as exc:
        raise _refuse_start(f"{exc} The backend does not start.") from exc
    known = {script_revision.revision for script_revision in migration_script().walk_revisions()}
    if current is not None and current not in known:
        raise _refuse_start(
            f"The database is at schema revision {current}, which this version of CoGA does not "
            f"have (its newest is {head}): another version made or migrated it. The backend "
            "does not start. Roll out that version again, or restore the database from the "
            f"backup taken before that version migrated it ({_SCHEMA_DOC})."
        )
    if current != head:
        raise _refuse_start(
            f"The database is at schema revision {current or 'none (no ledger)'}, and this "
            f"version of CoGA needs revision {head}. It does not migrate at start "
            "(POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false), so the backend does not start. "
            "Run the migration job first, python -m app.db_migrate as the owner of the tables "
            "(on Google Cloud: gcloud run jobs execute coga-db-migrate --region <region> --wait), "
            f"then start this version again ({_SCHEMA_DOC})."
        )
    if stored_baseline_digest(connection) != baseline_digest():
        raise _refuse_start(
            "The database was made from other baseline files than this version's "
            f"(schema_baselines), so the backend does not start ({_SCHEMA_DOC})."
        )
    logger.info("Postgres schema at revision %s, this version's own", head)
    return head


def _alembic_config(connection: Connection) -> Config:
    config = Config()
    # Config values go through configparser interpolation, where % starts a reference.
    config.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    # env.py runs only with this connection, inside the transaction opened below.
    config.attributes["connection"] = connection
    # Revision 0001 applies the baselines and records their digest with these.
    config.attributes["apply_baselines"] = apply_baselines
    config.attributes["record_baseline_digest"] = record_baseline_digest
    return config


def _migrate(connection: Connection) -> SchemaRevision:
    script = migration_script()
    before = ledger_revision(connection)
    # Before anything runs: only an empty database, or one this version made.
    refuse_unknown_revision(before, script)
    refuse_unless_made_here(connection, before)
    try:
        command.upgrade(_alembic_config(connection), "head")
    except CommandError as exc:
        raise SchemaMigrationRefused(f"The schema migration stopped: {exc}") from exc
    after = ledger_revision(connection)
    if after is None:  # pragma: no cover - upgrade("head") always records a revision
        raise SchemaMigrationRefused("The schema migration recorded no revision.")
    return SchemaRevision(before=before, after=after)


async def apply_postgres_schema(conn: AsyncConnection) -> SchemaRevision:
    """Bring the database on ``conn`` to this version's schema, inside its transaction.

    The caller commits (``init_postgres_schema`` opens the transaction with
    ``engine.begin()``); an exception rolls every step back.
    """

    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": _LOCK_KEY})
    role = (await conn.execute(text("SELECT current_user"))).scalar_one()
    if role == APP_ROLE:
        raise SchemaMigrationRefused(
            f"The schema migration runs as the owner of the tables, not as {APP_ROLE}, which "
            "may not change the schema or its ledger. Run python -m app.db_migrate with the "
            "owner's credentials, and set POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false on "
            "the API (docs/db-runtime-role-runbook.md)."
        )
    revision = await conn.run_sync(_migrate)
    if revision.before is None:
        logger.info("Postgres schema made at revision %s", revision.after)
    elif revision.before == revision.after:
        logger.info("Postgres schema at revision %s", revision.after)
    else:
        logger.info(
            "Postgres schema migrated from revision %s to %s", revision.before, revision.after
        )
    return revision
