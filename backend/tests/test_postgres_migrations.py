"""The Postgres schema migration's ledger and revisions, without a database (ENG-3).

The integration tests (``integration/test_postgres_migrations.py``) run the migration on real
Postgres; these pin what can be read without one: the revisions form one forward-only line,
each revision's check is the baseline's, the ledger table and its REVOKE are where Alembic and
05_grants.sql need them, Alembic refuses to run outside CoGA's migration step, and the step
refuses the runtime role and a newer ledger before anything runs. A start that does not migrate
checks the ledger instead, and starts only on this version's own revision (OPS-23).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.app.core import postgres_migrations
from backend.app.core.postgres import _schema_files, _split_sql_script
from backend.app.core.postgres_migrations import (
    MIGRATIONS_DIR,
    SchemaMigrationRefused,
    SchemaRevision,
    SchemaRevisionMismatch,
    apply_postgres_schema,
    check_schema_revision,
    head_revision,
    migration_script,
)

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "db" / "schema" / "postgres"


def _revisions():
    """The revisions from the first to the newest."""

    return list(reversed(list(migration_script().walk_revisions())))


def _statements(filename: str) -> list[str]:
    return _split_sql_script((_SCHEMA_DIR / filename).read_text())


def test_the_revisions_form_one_line_numbered_from_0001() -> None:
    revisions = _revisions()

    assert [revision.revision for revision in revisions] == [
        f"{number:04d}" for number in range(1, len(revisions) + 1)
    ]
    assert revisions[0].down_revision is None
    for earlier, later in zip(revisions, revisions[1:]):
        assert later.down_revision == earlier.revision
    assert migration_script().get_heads() == [revisions[-1].revision] == [head_revision()]
    for revision in revisions:
        assert Path(revision.path).name.startswith(f"{revision.revision}_"), revision.path
        assert Path(revision.path).parent == MIGRATIONS_DIR / "versions"


def test_every_revision_is_forward_only() -> None:
    for revision in _revisions():
        with pytest.raises(NotImplementedError, match="restore the database from the backup"):
            revision.module.downgrade()


def _baseline_check(name: str) -> str:
    """A check constraint as 03_assay.sql defines it, from CHECK to its closing parenthesis."""

    match = re.search(rf"CONSTRAINT {name} (CHECK \(.*\)),?$", (_SCHEMA_DIR / "03_assay.sql").read_text(), re.M)
    assert match, name
    return match.group(1)


def test_the_revisions_give_the_tables_the_baselines_checks() -> None:
    script = migration_script()
    repeat_status = script.get_revision("0002").module
    relationship_and_track = script.get_revision("0003").module

    assert repeat_status._STATUS_CHECK == _baseline_check("repeat_expansions_status_check")
    assert "'not_assessed'::text" in repeat_status._STATUS_CHECK
    assert relationship_and_track._RELATIONSHIP_TYPE_CHECK == _baseline_check(
        "family_relationships_relationship_type_check"
    )
    assert relationship_and_track._TRACK_TYPE_CHECK == _baseline_check(
        "sample_interval_track_sources_track_type_check"
    )


def test_revision_0004_runs_the_baselines_own_statements_for_the_structure_versions_and_the_ui_event_log() -> None:
    # SEC-7: the structure versions' chain and both tables' append-only guards, brought to a
    # database the baselines created before them by the baselines' own statements.
    from backend.app.core.postgres_migrations import baseline_statements

    module = migration_script().get_revision("0004").module
    selected = module.selected_statements(baseline_statements)

    every = [statement for name in ("03_assay.sql", "04_traceability.sql", "05_grants.sql") for statement in _statements(name)]
    assert all(statement in every for statement in selected)
    prefixes = [
        "ALTER TABLE family_structure_versions ADD COLUMN IF NOT EXISTS family_identifier ",
        "ALTER TABLE family_structure_versions ADD COLUMN IF NOT EXISTS row_hash ",
        "ALTER TABLE family_structure_versions ADD COLUMN IF NOT EXISTS prev_hash ",
        "ALTER TABLE family_structure_versions ALTER COLUMN family_id DROP NOT NULL",
        "DO $$",
        "CREATE INDEX IF NOT EXISTS idx_family_structure_versions_chain ",
        "CREATE OR REPLACE FUNCTION ui_events_block_mutation() ",
        "DROP TRIGGER IF EXISTS ui_events_immutable ON ui_events",
        "CREATE TRIGGER ui_events_immutable",
        "CREATE OR REPLACE FUNCTION family_structure_versions_block_mutation() ",
        "DROP TRIGGER IF EXISTS family_structure_versions_immutable ON family_structure_versions",
        "CREATE TRIGGER family_structure_versions_immutable",
        "REVOKE UPDATE, DELETE, TRUNCATE ON",
    ]
    assert len(selected) == len(prefixes)
    assert [statement for statement, prefix in zip(selected, prefixes) if not statement.startswith(prefix)] == []
    assert "family_structure_versions_family_id_fkey" in selected[4]
    assert "family_structure_versions, ui_events" in " ".join(selected[-1].split())
    # A baseline that no longer holds one of them stops the revision.
    with pytest.raises(RuntimeError, match="the baseline changed under it"):
        module.selected_statements(lambda _name: [])


def test_revision_0005_runs_the_access_baselines_own_upgrade_of_the_project_memberships() -> None:
    # SEC-1: a membership's access level, brought to a database made before it by the
    # statement 01_access.sql adds it with.
    from backend.app.core.postgres_migrations import baseline_statements

    module = migration_script().get_revision("0005").module
    selected = module.selected_statements(baseline_statements)

    upgrades = [statement for statement in _statements("01_access.sql") if statement.startswith("ALTER TABLE project_users")]
    assert selected == upgrades
    assert len(selected) == 1
    flat = " ".join(selected[0].split())
    assert flat == (
        "ALTER TABLE project_users ADD COLUMN IF NOT EXISTS access_level TEXT NOT NULL DEFAULT 'analyst' "
        "CHECK (access_level IN ('analyst', 'reader'))"
    )
    # The column a new database's CREATE TABLE gives, with the same default and check.
    create = next(s for s in _statements("01_access.sql") if s.startswith("CREATE TABLE IF NOT EXISTS project_users"))
    assert "access_level TEXT NOT NULL DEFAULT 'analyst' CHECK (access_level IN ('analyst', 'reader'))" in " ".join(
        create.split()
    )
    # A baseline that no longer holds it stops the revision.
    with pytest.raises(RuntimeError, match="the baseline changed under it"):
        module.selected_statements(lambda _name: [])


def test_revision_0006_runs_the_access_baselines_own_upgrade_of_the_accounts() -> None:
    # SEC-4: the Microsoft object ID an account signs in with, brought to a database made
    # before it by the statement 01_access.sql adds it with.
    from backend.app.core.postgres_migrations import baseline_statements

    module = migration_script().get_revision("0006").module
    selected = module.selected_statements(baseline_statements)

    upgrades = [statement for statement in _statements("01_access.sql") if statement.startswith("ALTER TABLE users")]
    assert selected == upgrades
    assert len(selected) == 1
    assert " ".join(selected[0].split()) == "ALTER TABLE users ADD COLUMN IF NOT EXISTS azure_object_id UUID UNIQUE"
    # The column a new database's CREATE TABLE gives, with the same type and uniqueness.
    create = next(s for s in _statements("01_access.sql") if s.startswith("CREATE TABLE IF NOT EXISTS users"))
    assert "azure_object_id UUID UNIQUE" in " ".join(create.split())
    # A baseline that no longer holds it stops the revision.
    with pytest.raises(RuntimeError, match="the baseline changed under it"):
        module.selected_statements(lambda _name: [])


def test_the_ledger_table_is_alembics_own_and_its_revoke_follows_the_broad_grant() -> None:
    # Alembic finds the table the baseline created and uses it as its own.
    context = MigrationContext.configure(dialect_name="postgresql")
    alembics = " ".join(str(CreateTable(context._version).compile(dialect=postgresql.dialect())).split())
    ledger = next(s for s in _statements("04_traceability.sql") if "alembic_version" in s)
    assert " ".join(ledger.replace("IF NOT EXISTS ", "").split()) == alembics

    # 04 runs before 05, and in 05 the REVOKE comes after the GRANT that it narrows.
    names = [path.name for path in _schema_files()]
    assert names.index("04_traceability.sql") < names.index("05_grants.sql")
    grants = _statements("05_grants.sql")
    broad = next(i for i, s in enumerate(grants) if s.startswith("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES"))
    revoke = grants.index("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM coga_app")
    assert broad < revoke


def test_alembic_runs_only_inside_the_migration_step() -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))

    with pytest.raises(RuntimeError, match="python -m app.db_migrate"):
        command.upgrade(config, "head")
    with pytest.raises(RuntimeError, match="python -m app.db_migrate"):
        command.upgrade(config, "head", sql=True)


class _Result:
    def __init__(self, value: str) -> None:
        self._value = value

    def scalar_one(self) -> str:
        return self._value


class _Connection:
    """An AsyncConnection stand-in: the role it reports, and what ran on it."""

    def __init__(self, role: str) -> None:
        self.role = role
        self.statements: list[str] = []
        self.ran: list[object] = []

    async def execute(self, statement, parameters=None):
        self.statements.append(str(statement))
        return _Result(self.role)

    async def run_sync(self, fn):
        self.ran.append(fn)
        return SchemaRevision(before="0002", after="0003")


def test_the_runtime_role_is_refused_before_anything_runs() -> None:
    import asyncio

    connection = _Connection("coga_app")

    with pytest.raises(SchemaMigrationRefused, match="not as coga_app"):
        asyncio.run(apply_postgres_schema(connection))  # type: ignore[arg-type]

    assert connection.ran == []
    assert "pg_advisory_xact_lock" in connection.statements[0]


def test_the_owner_migrates_under_the_lock() -> None:
    import asyncio

    connection = _Connection("coga")

    revision = asyncio.run(apply_postgres_schema(connection))  # type: ignore[arg-type]

    assert revision == SchemaRevision(before="0002", after="0003")
    assert "pg_advisory_xact_lock" in connection.statements[0]
    assert connection.ran == [postgres_migrations._migrate]


@pytest.fixture
def _recorded(monkeypatch):
    """Record the migration's steps instead of running them."""

    steps: list[str] = []
    ledger = iter(["0002", "0003"])
    monkeypatch.setattr(postgres_migrations, "ledger_revision", lambda connection: next(ledger))
    monkeypatch.setattr(postgres_migrations, "apply_baselines", lambda connection: steps.append("baselines"))
    monkeypatch.setattr(
        postgres_migrations.command, "upgrade", lambda config, target: steps.append(f"upgrade to {target}")
    )
    return steps


def test_until_the_rc_every_run_reapplies_the_baselines_before_the_revisions(_recorded) -> None:
    assert postgres_migrations.REAPPLY_BASELINES_ON_EVERY_RUN is True

    revision = postgres_migrations._migrate(object())  # type: ignore[arg-type]

    assert _recorded == ["baselines", "upgrade to head"]
    assert revision == SchemaRevision(before="0002", after="0003")


def test_after_the_freeze_only_the_revisions_run(_recorded, monkeypatch) -> None:
    monkeypatch.setattr(postgres_migrations, "REAPPLY_BASELINES_ON_EVERY_RUN", False)

    postgres_migrations._migrate(object())  # type: ignore[arg-type]

    assert _recorded == ["upgrade to head"]


def test_a_ledger_from_a_newer_version_is_refused_before_the_baselines_run(_recorded, monkeypatch) -> None:
    monkeypatch.setattr(postgres_migrations, "ledger_revision", lambda connection: "9999")

    with pytest.raises(SchemaMigrationRefused, match="schema revision 9999.*a newer version migrated it"):
        postgres_migrations._migrate(object())  # type: ignore[arg-type]

    assert _recorded == []


# ---- A start that does not migrate (OPS-23) -----------------------------------------------------
#
# With POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false (the restricted runtime role) the migration
# job runs before the rollout, and the API starts only on a database whose ledger names exactly
# its own newest revision.


def _check_with_ledger(monkeypatch, ledger) -> str:
    """check_schema_revision on a ledger that names ``ledger`` (or, an exception, raises it)."""

    def _read(connection):
        if isinstance(ledger, Exception):
            raise ledger
        return ledger

    monkeypatch.setattr(postgres_migrations, "ledger_revision", _read)
    return check_schema_revision(object())  # type: ignore[arg-type]


def _logged_errors(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


def test_a_start_that_does_not_migrate_accepts_its_own_revision(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=postgres_migrations.__name__):
        assert _check_with_ledger(monkeypatch, head_revision()) == head_revision()

    assert _logged_errors(caplog) == []
    assert f"Postgres schema at revision {head_revision()}" in caplog.text


@pytest.mark.parametrize("behind", ["none", "first", "previous"])
def test_a_start_that_does_not_migrate_refuses_a_database_the_migration_job_has_not_brought_to_it(
    monkeypatch, caplog, behind: str
) -> None:
    revisions = [revision.revision for revision in _revisions()]
    ledger = {"none": None, "first": revisions[0], "previous": revisions[-2]}[behind]

    with caplog.at_level(logging.ERROR, logger=postgres_migrations.__name__):
        with pytest.raises(SchemaRevisionMismatch) as refused:
            _check_with_ledger(monkeypatch, ledger)

    message = str(refused.value)
    assert (
        f"at schema revision {ledger or 'none (no ledger)'}, and this version of CoGA needs revision "
        f"{head_revision()}" in message
    )
    assert "the backend does not start" in message
    assert "Run the migration job first, python -m app.db_migrate as the owner" in message
    assert "gcloud run jobs execute coga-db-migrate" in message
    # Logged whole, because a failed start's traceback is logged without the message.
    assert _logged_errors(caplog) == [message]


def test_a_start_that_does_not_migrate_refuses_a_database_a_newer_version_migrated(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.ERROR, logger=postgres_migrations.__name__):
        with pytest.raises(SchemaRevisionMismatch) as refused:
            _check_with_ledger(monkeypatch, "9999")

    message = str(refused.value)
    assert "at schema revision 9999, which this version of CoGA does not have" in message
    assert f"(its newest is {head_revision()})" in message
    assert "this version is older than the database" in message
    assert "restore the database from the backup taken before that migration" in message
    assert "migration job" not in message
    assert _logged_errors(caplog) == [message]


def test_a_start_that_does_not_migrate_refuses_a_ledger_of_two_revisions(monkeypatch, caplog) -> None:
    two = SchemaMigrationRefused("The schema ledger (alembic_version) names more than one revision: 0005, 0006.")

    with caplog.at_level(logging.ERROR, logger=postgres_migrations.__name__):
        with pytest.raises(SchemaRevisionMismatch, match="more than one revision: 0005, 0006. The backend does not"):
            _check_with_ledger(monkeypatch, two)

    assert len(_logged_errors(caplog)) == 1


class _StopHere(Exception):
    """Raised by the step after the Postgres schema, so a test runs the lifespan up to there."""


def _start_until_the_audit_writer(monkeypatch, *, migrates: bool, revision_error: Exception | None = None) -> list[str]:
    """The Postgres steps the backend's start ran, in order, up to the audit-log writer."""

    from backend.app import main as app_main

    calls: list[str] = []

    def _step(name: str, error: Exception | None = None):
        async def _run(*_args, **_kwargs):
            calls.append(name)
            if error is not None:
                raise error

        return _run

    for name in ("wait_for_postgres", "init_postgres_schema", "init_postgres_admin_user"):
        monkeypatch.setattr(app_main, name, _step(name))
    monkeypatch.setattr(
        app_main, "verify_postgres_schema_revision", _step("verify_postgres_schema_revision", revision_error)
    )
    monkeypatch.setattr(app_main, "start_audit_log_worker", _step("start_audit_log_worker", _StopHere()))
    monkeypatch.setattr(app_main.settings, "postgres_run_schema_migrations_on_startup", migrates)

    class _App:
        class state:  # FastAPI's app.state, as the lifespan reads it
            skip_startup_tasks = False

    async def _start() -> None:
        async with app_main.lifespan(_App()):
            pytest.fail("the start must stop before the app serves")  # pragma: no cover

    with pytest.raises((_StopHere, SchemaRevisionMismatch)) as stopped:
        asyncio.run(_start())
    if revision_error is not None:
        assert stopped.value is revision_error
    return calls


def test_a_start_that_does_not_migrate_checks_the_schema_revision_instead(monkeypatch) -> None:
    assert _start_until_the_audit_writer(monkeypatch, migrates=False) == [
        "wait_for_postgres",
        "verify_postgres_schema_revision",
        "start_audit_log_worker",
    ]


def test_a_start_on_another_schema_revision_stops_before_anything_else_runs(monkeypatch) -> None:
    refused = SchemaRevisionMismatch("The database is at schema revision 0005")

    calls = _start_until_the_audit_writer(monkeypatch, migrates=False, revision_error=refused)

    # No audit writer, seed, worker or ClickHouse step runs on it.
    assert calls == ["wait_for_postgres", "verify_postgres_schema_revision"]


def test_a_start_that_migrates_leaves_the_revision_to_the_migration(monkeypatch) -> None:
    # The owner's start migrates to this version's revision, or refuses a newer ledger itself.
    assert _start_until_the_audit_writer(monkeypatch, migrates=True) == [
        "wait_for_postgres",
        "init_postgres_schema",
        "init_postgres_admin_user",
        "start_audit_log_worker",
    ]
