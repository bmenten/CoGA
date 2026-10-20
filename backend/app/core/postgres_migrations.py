"""The Postgres schema migration: the baseline files, then the Alembic revisions.

The API runs it at startup when it connects as the table owner
(``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP``, the default), and ``python -m app.db_migrate``
runs it before a rollout when the API connects as the restricted runtime role ``coga_app``.
Both call ``init_postgres_schema`` (core/postgres.py), which calls ``apply_postgres_schema``
here. It runs in one transaction, so it applies whole or not at all:

1. a transaction-level advisory lock, so that two instances starting together migrate one
   after the other;
2. a refusal when connected as ``coga_app``: migrations run as the owner, and ``coga_app``
   may neither run DDL nor write the ledger. A ledger naming a revision this version does
   not have is refused here too, before anything runs (below);
3. until the release candidate, the five idempotent baseline files
   (``backend/db/schema/postgres/``), re-applied on every run, so that a pull request that
   edits a baseline still reaches every existing database. ``CREATE TABLE IF NOT EXISTS``
   leaves a table that exists as it is, so a change to an existing table needs a revision;
4. the Alembic revisions (``backend/db/migrations/versions/``) that the ledger
   (``alembic_version``) does not list yet, recorded there as they run.

A database the baselines created before the ledger existed has an empty ledger: it gets every
revision, which each end at the baseline's schema whatever they start from, and keeps its
rows. A ledger naming a revision this version does not know was written by a newer version:
the migration refuses it rather than run older code on a newer schema.

A start that does not migrate (``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false``, as with
``coga_app``) runs ``check_schema_revision`` instead: it only reads the ledger, and refuses to
start unless it names exactly this version's newest revision. Behind it, the migration job has
not run for this version; ahead of it, a newer version migrated the database.

The release candidate freezes the baselines: ``REAPPLY_BASELINES_ON_EVERY_RUN`` becomes
``False``, revision 0001 is then the only step that applies them (to a new database), and every
schema change is a revision of its own (docs/database.md, "Schema Migrations").
"""

from __future__ import annotations

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

# Until the release candidate every run re-applies the baselines before the revisions (step 3
# above). The RC sets this to False: docs/database.md, "Freezing the baselines at the release
# candidate".
REAPPLY_BASELINES_ON_EVERY_RUN = True

_LOCK_KEY = "coga:postgres-schema-migration"


class SchemaMigrationRefused(RuntimeError):
    """The migration did not run: the wrong role, or a ledger written by a newer version."""


class SchemaRevisionMismatch(RuntimeError):
    """A start that does not migrate found the database at another revision than its own."""


@dataclass(frozen=True)
class SchemaRevision:
    """The ledger's revision before and after a migration run.

    ``before`` is None for a new database and for one created before the ledger existed.
    """

    before: str | None
    after: str


def apply_baselines(connection: Connection) -> None:
    """Run every statement of the five baseline files, in name order, on ``connection``."""

    for path in _schema_files():
        for statement in _split_sql_script(path.read_text()):
            connection.exec_driver_sql(statement)


def baseline_statements(filename: str) -> list[str]:
    """The statements of one baseline file, split as ``apply_baselines`` runs them. A revision
    that brings an existing table to its baseline's definition runs the baseline's own
    statements (revision 0004)."""

    path = next(path for path in _schema_files() if path.name == filename)
    return _split_sql_script(path.read_text())


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
            f"does not have (its newest is {script.get_current_head()}): a newer version "
            "migrated it. Run that version, or restore the database from the backup taken "
            "before that upgrade (docs/database.md, \"Schema Migrations\")."
        )


def _refuse_start(message: str) -> SchemaRevisionMismatch:
    # Logged as well as raised: the log writes a failed start's traceback without the
    # exception's message (core/coga_logging.py), and the operator needs this one.
    logger.error("%s", message)
    return SchemaRevisionMismatch(message)


def check_schema_revision(connection: Connection) -> str:
    """This version's newest revision, when the ledger on ``connection`` names exactly that one.

    For a start that does not migrate (``POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false``, as
    with the restricted runtime role ``coga_app``): the migration step runs separately, as the
    owner, before the rollout. Any other ledger raises ``SchemaRevisionMismatch``, and the
    message is logged. An older revision or none means the migration did not run for this
    version; a revision this version does not have means a newer version migrated the
    database. It only reads the ledger, which ``coga_app`` may.
    """

    head = head_revision()
    try:
        current = ledger_revision(connection)
    except SchemaMigrationRefused as exc:
        raise _refuse_start(f"{exc} The backend does not start.") from exc
    if current == head:
        logger.info("Postgres schema at revision %s, this version's own", head)
        return head
    known = {script_revision.revision for script_revision in migration_script().walk_revisions()}
    if current is None or current in known:
        raise _refuse_start(
            f"The database is at schema revision {current or 'none (no ledger)'}, and this "
            f"version of CoGA needs revision {head}. It does not migrate at start "
            "(POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false), so the backend does not start. "
            "Run the migration job first, python -m app.db_migrate as the owner of the tables "
            "(on Google Cloud: gcloud run jobs execute coga-db-migrate --region <region> --wait), "
            "then start this version again (docs/database.md, \"Schema Migrations\")."
        )
    raise _refuse_start(
        f"The database is at schema revision {current}, which this version of CoGA does not "
        f"have (its newest is {head}): this version is older than the database, which a newer "
        "version migrated. The backend does not start. Roll out the version that migrated it "
        "again, or restore the database from the backup taken before that migration "
        "(docs/database.md, \"Schema Migrations\")."
    )


def _alembic_config(connection: Connection) -> Config:
    config = Config()
    # Config values go through configparser interpolation, where % starts a reference.
    config.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    # env.py runs only with this connection, inside the transaction opened below.
    config.attributes["connection"] = connection
    # Revision 0001 applies the baselines with the same function as step 3.
    config.attributes["apply_baselines"] = apply_baselines
    # A revision that runs some of a baseline's own statements reads them split the same way.
    config.attributes["baseline_statements"] = baseline_statements
    return config


def _migrate(connection: Connection) -> SchemaRevision:
    script = migration_script()
    before = ledger_revision(connection)
    # Before anything runs: this version's baselines must not touch a newer schema.
    refuse_unknown_revision(before, script)
    if REAPPLY_BASELINES_ON_EVERY_RUN:
        apply_baselines(connection)
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
    if revision.before == revision.after:
        logger.info("Postgres schema at revision %s", revision.after)
    else:
        logger.info(
            "Postgres schema migrated from revision %s to %s",
            revision.before or "none (no ledger)",
            revision.after,
        )
    return revision
