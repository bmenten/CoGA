"""The package-import workers in a process of their own (``backend/app/import_worker.py``).

A backfill's imports used one core between them, and the API's event loop with it: every
worker ran in the API's process. ``python -m app.import_worker`` runs them without the API,
and ``FAMILY_IMPORT_WORKER_COUNT=0`` leaves the API without any. These tests run the
process's start and stop with its datastore steps recorded; the import run end to end by
such a process, against Postgres and ClickHouse, is in
``e2e/test_e2e_import_worker_process.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app import import_worker
from backend.app import main as app_main
from backend.app.core.config import Settings


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def step(self, name: str, *, raises: BaseException | None = None, blocks: bool = False):
        async def _step(*_args: Any, **_kwargs: Any) -> None:
            self.calls.append(name)
            if raises is not None:
                raise raises
            if blocks:
                await asyncio.Event().wait()

        return _step


_START = (
    "wait_for_postgres",
    "verify_postgres_schema_revision",
    "wait_for_clickhouse",
    "init_clickhouse_schema",
    "verify_clickhouse_variant_storage_identity",
    "drop_orphaned_import_backups",
)


def _patch_process(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    *,
    failing: str | None = None,
    blocking: str | None = None,
    worker: Any = None,
) -> list[asyncio.Event]:
    """The process's datastore steps recorded; each worker waits for its stop event, which
    the returned list collects, unless ``worker`` replaces it."""
    for name in _START:
        monkeypatch.setattr(
            import_worker,
            name,
            recorder.step(
                name,
                raises=RuntimeError("test: not reachable") if name == failing else None,
                blocks=name == blocking,
            ),
        )
    for name in ("close_clickhouse_client", "close_postgres_engine"):
        monkeypatch.setattr(import_worker, name, recorder.step(name))
    stop_events: list[asyncio.Event] = []

    async def waits_for_its_stop(stop_event: asyncio.Event | None = None) -> None:
        recorder.calls.append("family_package_import_worker")
        assert stop_event is not None
        stop_events.append(stop_event)
        await stop_event.wait()

    monkeypatch.setattr(import_worker, "family_package_import_worker", worker or waits_for_its_stop)
    return stop_events


def test_the_process_checks_what_the_api_checks_and_never_migrates(monkeypatch) -> None:
    recorder = _Recorder()
    _patch_process(monkeypatch, recorder)

    async def run() -> int:
        stop = asyncio.Event()
        running = asyncio.create_task(import_worker.run_import_workers(3, stop))
        while recorder.calls.count("family_package_import_worker") < 3:
            await asyncio.sleep(0)
        stop.set()
        return await running

    assert asyncio.run(run()) == import_worker.EXIT_STOPPED
    # The schema is read, never migrated: the API, or the migration job, migrates it as the
    # tables' owner. The workers claim nothing before every check has passed.
    assert recorder.calls == [
        *_START,
        "family_package_import_worker",
        "family_package_import_worker",
        "family_package_import_worker",
        "close_clickhouse_client",
        "close_postgres_engine",
    ]
    assert not hasattr(import_worker, "init_postgres_schema")


def test_a_start_that_fails_runs_no_worker_and_closes_the_clients(monkeypatch) -> None:
    recorder = _Recorder()
    _patch_process(monkeypatch, recorder, failing="verify_postgres_schema_revision")

    with pytest.raises(RuntimeError):
        asyncio.run(import_worker.run_import_workers(1, asyncio.Event()))

    assert recorder.calls == [
        "wait_for_postgres",
        "verify_postgres_schema_revision",
        "close_clickhouse_client",
        "close_postgres_engine",
    ]


def test_a_stop_during_the_start_ends_the_process_before_any_worker_runs(monkeypatch) -> None:
    recorder = _Recorder()
    _patch_process(monkeypatch, recorder, blocking="wait_for_clickhouse")

    async def run() -> int:
        stop = asyncio.Event()
        running = asyncio.create_task(import_worker.run_import_workers(2, stop))
        while "wait_for_clickhouse" not in recorder.calls:
            await asyncio.sleep(0)
        stop.set()
        return await asyncio.wait_for(running, timeout=5)

    assert asyncio.run(run()) == import_worker.EXIT_STOPPED
    assert "family_package_import_worker" not in recorder.calls
    assert recorder.calls[-2:] == ["close_clickhouse_client", "close_postgres_engine"]


def test_a_worker_that_ends_by_itself_stops_the_process_so_it_is_started_again(
    monkeypatch, caplog
) -> None:
    recorder = _Recorder()
    stopped: list[str] = []
    started = 0

    async def worker(stop_event: asyncio.Event | None = None) -> None:
        nonlocal started
        started += 1
        if started == 1:
            raise RuntimeError("test: the worker loop ended")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.append("the other worker")
            raise

    _patch_process(monkeypatch, recorder, worker=worker)
    caplog.set_level(logging.ERROR, logger=import_worker.logger.name)

    assert asyncio.run(import_worker.run_import_workers(2, asyncio.Event())) == import_worker.EXIT_FAILED
    assert stopped == ["the other worker"]
    assert [record.getMessage() for record in caplog.records] == [
        "A package-import worker ended by itself; the import worker process stops"
    ]
    assert recorder.calls[-2:] == ["close_clickhouse_client", "close_postgres_engine"]


def test_sigterm_stops_the_workers_and_the_process_exits_0(monkeypatch) -> None:
    recorder = _Recorder()
    stop_events = _patch_process(monkeypatch, recorder)

    async def serve_then_terminate() -> int:
        serving = asyncio.create_task(import_worker._serve(2))
        while len(stop_events) < 2:
            await asyncio.sleep(0)
        os.kill(os.getpid(), signal.SIGTERM)
        return await asyncio.wait_for(serving, timeout=5)

    assert asyncio.run(serve_then_terminate()) == import_worker.EXIT_STOPPED
    assert all(event.is_set() for event in stop_events)
    assert recorder.calls[-2:] == ["close_clickhouse_client", "close_postgres_engine"]
    # The handlers go with the loop: a later SIGINT is Python's own again.
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_a_failed_start_exits_1(monkeypatch) -> None:
    recorder = _Recorder()
    _patch_process(monkeypatch, recorder, failing="wait_for_postgres")

    assert asyncio.run(import_worker._serve(1)) == import_worker.EXIT_FAILED


def test_the_process_refuses_to_run_no_worker(monkeypatch, caplog) -> None:
    monkeypatch.setattr(import_worker.settings, "family_import_worker_count", 0)
    monkeypatch.setattr(import_worker, "configure_json_logging", lambda: None)
    monkeypatch.setattr(
        import_worker.asyncio, "run", lambda *_args, **_kwargs: pytest.fail("nothing may run")
    )
    caplog.set_level(logging.ERROR, logger=import_worker.logger.name)

    assert import_worker.main() == import_worker.EXIT_REFUSED
    assert "FAMILY_IMPORT_WORKER_COUNT is 0" in caplog.text


@pytest.mark.parametrize("count", ["0", "1", "8"])
def test_the_worker_count_takes_0_to_8(count: str) -> None:
    settings = Settings(_env_file=None, FAMILY_IMPORT_WORKER_COUNT=count)
    assert settings.family_import_worker_count == int(count)


@pytest.mark.parametrize("count", ["-1", "9"])
def test_the_worker_count_refuses_values_outside_0_to_8(count: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, FAMILY_IMPORT_WORKER_COUNT=count)


# --- The API with no import worker of its own ---------------------------------------------


class _App:
    class state:  # FastAPI's app.state, as the lifespan reads it
        skip_startup_tasks = False


class _Session:
    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


@pytest.mark.parametrize("count", [0, 2])
def test_the_api_starts_as_many_import_workers_as_it_is_given(monkeypatch, caplog, count: int) -> None:
    recorder = _Recorder()
    for name in (
        "wait_for_postgres",
        "init_postgres_schema",
        "init_postgres_admin_user",
        "start_audit_log_worker",
        "start_ui_event_worker",
        "seed_builtin_repeat_catalog",
        "ensure_human_grch38_reference_on_startup",
        "ensure_human_t2t_reference_on_startup",
        "ensure_hpo_ontology_on_startup",
        "seed_builtin_reference_tracks",
        "queue_startup_gene_reference_refresh_if_needed",
        "wait_for_clickhouse",
        "init_clickhouse_schema",
        "verify_clickhouse_variant_storage_identity",
        "drop_orphaned_import_backups",
        "start_clickhouse_integrity_monitor",
        "gene_reference_refresh_worker",
        "family_package_import_worker",
        "stop_gene_reference_worker",
        "stop_family_package_import_worker",
        "stop_clickhouse_integrity_monitor",
        "stop_audit_log_worker",
        "stop_ui_event_worker",
        "close_clickhouse_client",
        "close_postgres_engine",
    ):
        monkeypatch.setattr(app_main, name, recorder.step(name))
    monkeypatch.setattr(app_main, "get_postgres_sessionmaker", lambda: _Session)
    monkeypatch.setattr(app_main.settings, "postgres_run_schema_migrations_on_startup", True)
    monkeypatch.setattr(app_main.settings, "family_import_worker_count", count)
    caplog.set_level(logging.INFO, logger=app_main.logger.name)

    async def serve() -> None:
        async with app_main.lifespan(_App()):
            await asyncio.sleep(0)

    asyncio.run(serve())

    assert recorder.calls.count("family_package_import_worker") == count
    assert recorder.calls.count("stop_family_package_import_worker") == count
    # The rest of the start runs as before: the API still migrates and serves.
    assert "init_postgres_schema" in recorder.calls
    assert "gene_reference_refresh_worker" in recorder.calls
    said = "No package-import workers in the API (FAMILY_IMPORT_WORKER_COUNT=0)" in caplog.text
    assert said is (count == 0)
