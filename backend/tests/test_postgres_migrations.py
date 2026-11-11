"""The Postgres schema migration's ledger, revision and baselines' digest, without a database.

The integration tests (``integration/test_postgres_migrations.py``) run the migration on real
Postgres; these pin what can be read without one: the revisions form one forward-only line,
revision 0001 applies the baselines and records their digest, the ledger and digest tables and
their REVOKE are where Alembic and 05_grants.sql need them, Alembic refuses to run outside
CoGA's migration step, and the step refuses the runtime role and any database this version did
not make before anything runs: tables without a ledger, a ledger naming a revision this version
does not have, a database made from other baselines. A start that does not migrate checks the
ledger and the digest instead, and starts only on this version's own revision and baselines.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace

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
    baseline_digest,
    check_schema_revision,
    head_revision,
    migration_script,
)
from backend.tests._lifespan import LifespanApp

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


def test_until_the_release_candidate_revision_0001_is_the_only_revision() -> None:
    # A schema change before the release candidate edits a baseline; a database made from other
    # baselines is made again from empty, never migrated (docs/database.md, "Schema Migrations").
    assert [revision.revision for revision in _revisions()] == ["0001"]


def test_every_revision_is_forward_only() -> None:
    for revision in _revisions():
        with pytest.raises(NotImplementedError, match="restore the database from the backup"):
            revision.module.downgrade()


def test_revision_0001_applies_the_baselines_then_records_their_digest(monkeypatch) -> None:
    module = migration_script().get_revision("0001").module
    connection = object()
    steps: list[tuple[str, object]] = []
    attributes = {
        "apply_baselines": lambda bind: steps.append(("baselines", bind)),
        "record_baseline_digest": lambda bind: steps.append(("digest", bind)),
    }
    monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: connection))
    monkeypatch.setattr(module, "context", SimpleNamespace(config=SimpleNamespace(attributes=attributes)))

    module.upgrade()

    assert steps == [("baselines", connection), ("digest", connection)]


def test_the_migration_step_hands_revision_0001_its_own_functions() -> None:
    config = postgres_migrations._alembic_config(object())  # type: ignore[arg-type]

    assert config.attributes["apply_baselines"] is postgres_migrations.apply_baselines
    assert config.attributes["record_baseline_digest"] is postgres_migrations.record_baseline_digest


def _baselines_in(tmp_path: Path, monkeypatch, contents: dict[str, bytes]) -> None:
    for name, body in contents.items():
        (tmp_path / name).write_bytes(body)
    monkeypatch.setattr(postgres_migrations, "_schema_files", lambda: sorted(tmp_path.glob("*.sql")))


def test_the_digest_covers_every_baseline_files_name_and_bytes(tmp_path, monkeypatch) -> None:
    real = baseline_digest()
    assert len(real) == 64 and int(real, 16) >= 0
    expected = hashlib.sha256()
    for path in _schema_files():
        expected.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    assert real == expected.hexdigest()

    _baselines_in(tmp_path, monkeypatch, {"01_a.sql": b"SELECT 1;\n", "02_b.sql": b"SELECT 2;\n"})
    first = baseline_digest()
    assert baseline_digest() == first
    (tmp_path / "02_b.sql").write_bytes(b"SELECT 2; \n")
    assert baseline_digest() != first
    (tmp_path / "02_b.sql").write_bytes(b"SELECT 2;\n")
    (tmp_path / "02_b.sql").rename(tmp_path / "03_b.sql")
    assert baseline_digest() != first


def test_the_ledger_and_digest_tables_are_read_only_to_the_runtime_role() -> None:
    # Alembic finds the ledger the baseline created and uses it as its own.
    context = MigrationContext.configure(dialect_name="postgresql")
    alembics = " ".join(str(CreateTable(context._version).compile(dialect=postgresql.dialect())).split())
    traceability = _statements("04_traceability.sql")
    ledger = next(s for s in traceability if "TABLE IF NOT EXISTS alembic_version" in s)
    assert " ".join(ledger.replace("IF NOT EXISTS ", "").split()) == alembics
    digest = next(s for s in traceability if "TABLE IF NOT EXISTS schema_baselines" in s)
    assert "CHECK (singleton)" in digest and "PRIMARY KEY (singleton)" in digest

    # 04 runs before 05, and in 05 the REVOKE comes after the GRANT that it narrows.
    names = [path.name for path in _schema_files()]
    assert names.index("04_traceability.sql") < names.index("05_grants.sql")
    grants = _statements("05_grants.sql")
    broad = next(i for i, s in enumerate(grants) if s.startswith("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES"))
    revoke = grants.index("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version, schema_baselines FROM coga_app")
    assert broad < revoke


def test_the_baselines_hold_no_upgrade_of_an_existing_table() -> None:
    # They make an empty database, so no statement alters a table one of them created.
    for path in _schema_files():
        for statement in _split_sql_script(path.read_text()):
            assert not statement.upper().startswith("ALTER TABLE"), (path.name, statement[:80])
            assert "ADD COLUMN" not in statement.upper(), (path.name, statement[:80])
            assert "DROP NOT NULL" not in statement.upper(), (path.name, statement[:80])


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
        return SchemaRevision(before=None, after="0001")


def test_the_runtime_role_is_refused_before_anything_runs() -> None:
    connection = _Connection("coga_app")

    with pytest.raises(SchemaMigrationRefused, match="not as coga_app"):
        asyncio.run(apply_postgres_schema(connection))  # type: ignore[arg-type]

    assert connection.ran == []
    assert "pg_advisory_xact_lock" in connection.statements[0]


def test_the_owner_migrates_under_the_lock(caplog) -> None:
    connection = _Connection("coga")

    with caplog.at_level(logging.INFO, logger=postgres_migrations.__name__):
        revision = asyncio.run(apply_postgres_schema(connection))  # type: ignore[arg-type]

    assert revision == SchemaRevision(before=None, after="0001")
    assert "pg_advisory_xact_lock" in connection.statements[0]
    assert connection.ran == [postgres_migrations._migrate]
    assert "Postgres schema made at revision 0001" in caplog.text


@pytest.fixture
def _database(monkeypatch):
    """A database stand-in for ``_migrate``: its ledger, tables and digest, and what ran on it."""

    state = SimpleNamespace(ledger=[None, "0001"], tables=0, digest=None, steps=[])
    ledger = iter(state.ledger)
    monkeypatch.setattr(postgres_migrations, "ledger_revision", lambda connection: next(ledger))
    monkeypatch.setattr(postgres_migrations, "_public_table_count", lambda connection: state.tables)
    monkeypatch.setattr(postgres_migrations, "stored_baseline_digest", lambda connection: state.digest)
    monkeypatch.setattr(
        postgres_migrations.command, "upgrade", lambda config, target: state.steps.append(f"upgrade to {target}")
    )

    def _set_ledger(*revisions):
        nonlocal ledger
        ledger = iter(revisions)

    state.set_ledger = _set_ledger
    return state


def test_an_empty_database_is_made_by_the_revisions(_database) -> None:
    revision = postgres_migrations._migrate(object())  # type: ignore[arg-type]

    assert _database.steps == ["upgrade to head"]
    assert revision == SchemaRevision(before=None, after="0001")


def test_a_database_with_tables_but_no_ledger_is_refused_before_anything_runs(_database) -> None:
    _database.tables = 3

    with pytest.raises(SchemaMigrationRefused, match="holds 3 table.*no schema ledger.*point it at an empty one"):
        postgres_migrations._migrate(object())  # type: ignore[arg-type]

    assert _database.steps == []


def test_a_database_made_from_these_baselines_runs_the_revisions_it_lacks(_database) -> None:
    _database.set_ledger("0001", "0001")
    _database.tables = 60
    _database.digest = baseline_digest()

    revision = postgres_migrations._migrate(object())  # type: ignore[arg-type]

    assert _database.steps == ["upgrade to head"]
    assert revision == SchemaRevision(before="0001", after="0001")


@pytest.mark.parametrize("stored", [None, "0" * 64])
def test_a_database_made_from_other_baselines_is_refused_before_anything_runs(_database, stored) -> None:
    _database.set_ledger("0001")
    _database.tables = 60
    _database.digest = stored

    with pytest.raises(SchemaMigrationRefused) as refused:
        postgres_migrations._migrate(object())  # type: ignore[arg-type]

    message = str(refused.value)
    assert "made from other baseline files than this version's" in message
    assert f"schema_baselines records {stored or 'none'}, this version has {baseline_digest()}" in message
    assert 'docs/database.md, "Schema Migrations"' in message
    assert _database.steps == []


def test_a_ledger_this_version_does_not_have_is_refused_before_anything_runs(_database) -> None:
    _database.set_ledger("9999")

    with pytest.raises(SchemaMigrationRefused, match="schema revision 9999.*another version made or migrated it"):
        postgres_migrations._migrate(object())  # type: ignore[arg-type]

    assert _database.steps == []


# ---- A start that does not migrate ---------------------------------------------------------------
#
# With POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false (the restricted runtime role) the migration
# job runs before the rollout, and the API starts only on a database whose ledger names exactly
# its own newest revision and whose digest is its own baselines'.


def _check_with_ledger(monkeypatch, ledger, digest: str | None = None) -> str:
    """check_schema_revision on a ledger that names ``ledger`` (or, an exception, raises it) and
    a database made from the baselines of ``digest`` (this version's when omitted)."""

    def _read(connection):
        if isinstance(ledger, Exception):
            raise ledger
        return ledger

    monkeypatch.setattr(postgres_migrations, "ledger_revision", _read)
    monkeypatch.setattr(
        postgres_migrations, "stored_baseline_digest", lambda connection: digest or baseline_digest()
    )
    return check_schema_revision(object())  # type: ignore[arg-type]


def _logged_errors(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


def test_a_start_that_does_not_migrate_accepts_its_own_revision(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=postgres_migrations.__name__):
        assert _check_with_ledger(monkeypatch, head_revision()) == head_revision()

    assert _logged_errors(caplog) == []
    assert f"Postgres schema at revision {head_revision()}" in caplog.text


def test_a_start_that_does_not_migrate_refuses_a_database_the_migration_job_has_not_made(
    monkeypatch, caplog
) -> None:
    with caplog.at_level(logging.ERROR, logger=postgres_migrations.__name__):
        with pytest.raises(SchemaRevisionMismatch) as refused:
            _check_with_ledger(monkeypatch, None)

    message = str(refused.value)
    assert f"at schema revision none (no ledger), and this version of CoGA needs revision {head_revision()}" in message
    assert "the backend does not start" in message
    assert "Run the migration job first, python -m app.db_migrate as the owner" in message
    assert "gcloud run jobs execute coga-db-migrate" in message
    # Logged whole, because a failed start's traceback is logged without the message.
    assert _logged_errors(caplog) == [message]


def test_a_start_that_does_not_migrate_refuses_a_revision_this_version_does_not_have(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.ERROR, logger=postgres_migrations.__name__):
        with pytest.raises(SchemaRevisionMismatch) as refused:
            _check_with_ledger(monkeypatch, "9999")

    message = str(refused.value)
    assert "at schema revision 9999, which this version of CoGA does not have" in message
    assert f"(its newest is {head_revision()})" in message
    assert "another version made or migrated it" in message
    assert "migration job" not in message
    assert _logged_errors(caplog) == [message]


def test_a_start_that_does_not_migrate_refuses_a_database_made_from_other_baselines(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.ERROR, logger=postgres_migrations.__name__):
        with pytest.raises(SchemaRevisionMismatch) as refused:
            _check_with_ledger(monkeypatch, head_revision(), digest="0" * 64)

    message = str(refused.value)
    assert "made from other baseline files than this version's" in message
    assert "the backend does not start" in message
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

    async def _start() -> None:
        async with app_main.lifespan(LifespanApp()):
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
    # The owner's start migrates to this version's revision, or refuses the database itself.
    assert _start_until_the_audit_writer(monkeypatch, migrates=True) == [
        "wait_for_postgres",
        "init_postgres_schema",
        "init_postgres_admin_user",
        "start_audit_log_worker",
    ]
