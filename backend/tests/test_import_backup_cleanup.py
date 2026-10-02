"""The backup tables a stopped overwrite import leaves are dropped (TF-06 H16).

An ``overwrite`` import of an existing family copies the family's ClickHouse rows into
backup tables before it rewrites them, and drops them when it ends. When its process stops
part-way it cannot: the tables, a copy of the family's earlier variant rows, were named
after a random token only that process knew, so nothing ever dropped them. Now a backup is
named after the import that owns it (``<assembly>/SNAPSHOT/<job id>/<table>``), the worker
that ends a stopped job drops that job's backups, and startup drops every backup no running
import owns. These tests cover the naming, the listing, which backups count as orphaned,
the drop, and the worker's and startup's part; the statements run against ClickHouse in
integration/test_import_backup_cleanup_integration.py and the whole path in
e2e/test_e2e_import_crash_leaves_family_marked.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.services import clickhouse_family_snapshot as snapshots
from backend.app.services import family_package_import as package_import
from backend.app.services.clickhouse_family_snapshot import ImportBackupTable

JOB = "3f6c1a2e-8b4d-4e5f-9a7b-1c2d3e4f5a6b"
OTHER_JOB = "7a1d2c3b-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


class _ClickHouse:
    """Records each statement; answers a listing with ``tables``; fails ``failing`` drops."""

    def __init__(self, tables: list[tuple[str, Any]] | None = None, failing: set[str] | None = None) -> None:
        self.tables = tables or []
        self.failing = failing or set()
        self.statements: list[tuple[str, Any]] = []

    async def execute(self, query: str, parameters: Any = None) -> Any:
        sql = " ".join(query.split())
        self.statements.append((sql, parameters))
        if sql.startswith("SELECT name, metadata_modification_time FROM system.tables"):
            return list(self.tables)
        if sql.startswith("DROP TABLE") and any(name in sql for name in self.failing):
            raise RuntimeError("ClickHouse unavailable")
        return None

    def drops(self) -> list[str]:
        return [sql for sql, _ in self.statements if sql.startswith("DROP TABLE")]


@pytest.fixture
def clickhouse(monkeypatch: pytest.MonkeyPatch) -> _ClickHouse:
    fake = _ClickHouse()
    monkeypatch.setattr(snapshots, "execute_clickhouse", fake.execute)

    async def ensured(_assembly: str) -> None:
        return None

    monkeypatch.setattr(snapshots, "ensure_clickhouse_variant_tables", ensured)
    monkeypatch.setattr(snapshots, "ensure_clickhouse_interval_table", ensured)
    return fake


def _backup(owner: str, rel: str = "SNV_INDEL/entries", *, age: timedelta | None = timedelta(hours=1)) -> ImportBackupTable:
    return ImportBackupTable(
        name=f"GRCh38/SNAPSHOT/{owner}/{rel}",
        owner=owner,
        modified_at=None if age is None else NOW - age,
    )


# --- the name ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_backup_is_named_after_the_import_that_owns_it(clickhouse) -> None:
    snapshot = await snapshots.snapshot_family_clickhouse_state("GRCh38", "family-uuid", owner=JOB)

    assert snapshot.token == JOB
    created = [sql for sql, _ in clickhouse.statements if sql.startswith("CREATE TABLE")]
    assert len(created) == 5
    db = settings.clickhouse_database
    assert created[0] == f"CREATE TABLE {db}.`GRCh38/SNAPSHOT/{JOB}/SNV_INDEL/entries` AS {db}.`GRCh38/SNV_INDEL/entries`"


@pytest.mark.asyncio
async def test_a_backup_without_an_import_key_gets_a_random_token(clickhouse) -> None:
    snapshot = await snapshots.snapshot_family_clickhouse_state("GRCh38", "family-uuid")
    assert len(snapshot.token) == 32 and int(snapshot.token, 16) >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["a`b", "../x", "x/y", "", "-leading-dash"])
async def test_a_key_that_cannot_name_a_table_is_refused_before_anything_is_made(clickhouse, owner) -> None:
    if not owner:  # an empty key is no key: a random token
        await snapshots.snapshot_family_clickhouse_state("GRCh38", "family-uuid", owner=owner)
        return
    with pytest.raises(ValueError):
        await snapshots.snapshot_family_clickhouse_state("GRCh38", "family-uuid", owner=owner)
    assert clickhouse.statements == []


# --- the listing ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_listing_holds_only_backup_names_it_can_safely_drop(clickhouse) -> None:
    made = datetime(2026, 10, 1, 9, 0)  # ClickHouse hands DateTime back naive, in UTC
    clickhouse.tables = [
        (f"GRCh38/SNAPSHOT/{JOB}/SV/variants/details", made),
        (f"GRCh38/SNAPSHOT/{OTHER_JOB}/INTERVAL/entries", made),
        ("T2T_CHM13v2.0/SNAPSHOT/run-0a1b2c/SNV_INDEL/entries", None),
        ("GRCh38/SNAPSHOT/bad`owner/SNV_INDEL/entries", made),  # not an owner
        ("GRCh38/SNAPSHOT/x/NOT_A_BACKUP", made),  # not a backed-up table
        ("GRCh38/SNV_INDEL/entries", made),  # a live table
    ]

    tables = await snapshots.list_import_backup_tables()

    assert [table.name for table in tables] == [
        f"GRCh38/SNAPSHOT/{JOB}/SV/variants/details",
        f"GRCh38/SNAPSHOT/{OTHER_JOB}/INTERVAL/entries",
        "T2T_CHM13v2.0/SNAPSHOT/run-0a1b2c/SNV_INDEL/entries",
    ]
    assert tables[0].owner == JOB
    assert tables[0].modified_at == made.replace(tzinfo=timezone.utc)
    assert tables[2].modified_at is None
    sql, params = clickhouse.statements[0]
    assert params == {"database": settings.clickhouse_database, "pattern": "%/SNAPSHOT/%"}

    owned = await snapshots.list_import_backup_tables(owner=JOB)
    assert [table.owner for table in owned] == [JOB]


# --- which backups no import owns -------------------------------------------------------


def test_orphaned_backups_are_those_no_running_import_owns() -> None:
    running = _backup(JOB)
    ended = _backup(OTHER_JOB)
    young_run = _backup("run-0a1b2c", age=timedelta(hours=2))
    old_run = _backup("run-3d4e5f", age=timedelta(days=2))
    old_token = _backup("0123456789abcdef0123456789abcdef", age=timedelta(days=2))
    unknown_age = _backup("run-6a7b8c", age=None)

    orphaned = snapshots.orphaned_import_backups(
        [running, ended, young_run, old_run, old_token, unknown_age],
        running_jobs={JOB},
        now=NOW,
        grace=timedelta(days=1),
    )

    # A job's backup goes once the job has ended, however recent; one made outside a job
    # goes once it is older than the grace, as nothing records whether that import runs.
    assert orphaned == [ended, old_run, old_token]


# --- the drop ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_backup_that_cannot_be_dropped_is_left_for_the_next_attempt(clickhouse) -> None:
    first, second = _backup(JOB, "SV/entries"), _backup(JOB, "SNV_INDEL/entries")
    clickhouse.failing = {first.name}

    dropped = await snapshots.drop_import_backup_tables([first, second])

    assert dropped == [second.name]
    db = settings.clickhouse_database
    assert clickhouse.drops() == [
        f"DROP TABLE IF EXISTS {db}.`{first.name}` SYNC",
        f"DROP TABLE IF EXISTS {db}.`{second.name}` SYNC",
    ]


# --- the worker, and startup ------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_worker_drops_the_backups_of_a_job_it_ended_and_runs_the_others(monkeypatch) -> None:
    listed: list[Any] = []
    dropped: list[Any] = []
    ran: list[str] = []

    async def list_tables(owner: str | None = None) -> list[ImportBackupTable]:
        listed.append(owner)
        return [_backup(owner or "none")]

    async def drop(tables: Any) -> list[str]:
        names = [table.name for table in tables]
        dropped.extend(names)
        return names

    async def run(*, job_id: str, worker_id: str) -> None:
        ran.append(job_id)

    monkeypatch.setattr(package_import, "list_import_backup_tables", list_tables)
    monkeypatch.setattr(package_import, "drop_import_backup_tables", drop)
    monkeypatch.setattr(package_import, "run_family_import_job", run)

    # The claim ended this one: its import had begun writing when its process stopped.
    await package_import.handle_claimed_family_import_job({"id": JOB, "status": "failed"}, worker_id="w")
    await package_import.handle_claimed_family_import_job({"id": OTHER_JOB, "status": "validating"}, worker_id="w")

    assert listed == [JOB] and dropped == [f"GRCh38/SNAPSHOT/{JOB}/SNV_INDEL/entries"]
    assert ran == [OTHER_JOB]


@pytest.mark.asyncio
async def test_a_drop_that_fails_leaves_the_job_ended_and_the_backups_for_startup(monkeypatch) -> None:
    async def list_tables(owner: str | None = None) -> list[ImportBackupTable]:
        raise RuntimeError("ClickHouse unavailable")

    monkeypatch.setattr(package_import, "list_import_backup_tables", list_tables)

    # Logged, never raised: the worker goes on to its next job.
    await package_import.handle_claimed_family_import_job({"id": JOB, "status": "failed"}, worker_id="w")


@pytest.mark.asyncio
async def test_startup_drops_the_backups_no_running_import_owns(monkeypatch) -> None:
    tables = [_backup(JOB), _backup(OTHER_JOB), _backup("run-3d4e5f", age=timedelta(days=3))]
    looked_up: list[set[str]] = []
    dropped: list[str] = []

    async def list_tables(owner: str | None = None) -> list[ImportBackupTable]:
        assert owner is None
        return tables

    async def running(_session: Any, job_ids: Any) -> set[str]:
        looked_up.append(set(job_ids))
        return {JOB}

    async def drop(orphaned: Any) -> list[str]:
        names = [table.name for table in orphaned]
        dropped.extend(names)
        return names

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

    monkeypatch.setattr(package_import, "list_import_backup_tables", list_tables)
    monkeypatch.setattr(package_import, "running_family_import_job_ids", running)
    monkeypatch.setattr(package_import, "drop_import_backup_tables", drop)
    monkeypatch.setattr(package_import, "get_postgres_sessionmaker", lambda: _Session)

    assert await package_import.drop_orphaned_import_backups() == dropped

    # Only the jobs are looked up; the running job's backup stays.
    assert looked_up == [{JOB, OTHER_JOB}]
    assert dropped == [tables[1].name, tables[2].name]


@pytest.mark.asyncio
async def test_a_startup_sweep_that_fails_does_not_stop_the_start(monkeypatch) -> None:
    async def list_tables(owner: str | None = None) -> list[ImportBackupTable]:
        raise RuntimeError("ClickHouse unavailable")

    monkeypatch.setattr(package_import, "list_import_backup_tables", list_tables)
    assert await package_import.drop_orphaned_import_backups() == []
