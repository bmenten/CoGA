"""A start on a database without Monarch data loads Monarch's newest release.

A new instance had no Monarch associations until an administrator loaded them, so phenotype
matching and the phenotype ranking had nothing to match against (owner decision, 2026-11-05:
a new instance starts on the newest reference data). The start now runs the admin page's
refresh (``refresh_monarch``, tested in test_monarch_ingest.py) when both Monarch tables are
empty, and never stops on a failure. A database with Monarch data keeps it, and the start builds
no Mendeliome panel: a panel is created by an account, on the Gene panels page. Nothing here
goes to the network: the refresh is replaced.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from backend.app import main as app_main
from backend.app.services import monarch_ingest
from backend.tests._lifespan import LifespanApp, stub_lifespan_steps

_SUMMARY = {
    "release_version": "2026-10-07",
    "gene_disease_pairs": 12_345,
    "disease_phenotype_pairs": 234_567,
}


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _Session:
    """Answers whether Monarch data is loaded; counts the statements and rollbacks."""

    def __init__(self, *, loaded: bool = False, fail: Exception | None = None) -> None:
        self.loaded = loaded
        self.fail = fail
        self.statements: list[str] = []
        self.rollbacks = 0

    async def execute(self, statement: Any, *_args: Any, **_kwargs: Any) -> _Result:
        self.statements.append(str(statement))
        if self.fail is not None:
            raise self.fail
        return _Result(self.loaded)

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _bootstrap_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monarch_ingest.settings, "reference_bootstrap_enabled", True)
    monkeypatch.setattr(monarch_ingest.settings, "reference_bootstrap_monarch", True)


def _refresh(monkeypatch: pytest.MonkeyPatch, outcome: dict[str, Any] | Exception = _SUMMARY) -> list[Any]:
    """Replace the refresh; the list collects each session it was given."""
    calls: list[Any] = []

    async def refresh(session: Any) -> dict[str, Any]:
        calls.append(session)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(monarch_ingest, "refresh_monarch", refresh)
    return calls


@pytest.mark.asyncio
async def test_an_empty_database_loads_monarchs_newest_release(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    session = _Session(loaded=False)
    calls = _refresh(monkeypatch)
    caplog.set_level(logging.INFO, logger=monarch_ingest.logger.name)

    result = await monarch_ingest.ensure_monarch_on_startup(session)  # type: ignore[arg-type]

    assert calls == [session] and result == _SUMMARY
    # Both tables are looked at: either one holding rows means Monarch is loaded.
    assert "monarch_gene_disease" in session.statements[0] and "monarch_disease_phenotype" in session.statements[0]
    [info] = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert "2026-10-07" in info and "Mendeliome" in info


@pytest.mark.asyncio
async def test_a_database_with_monarch_data_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _refresh(monkeypatch)

    assert await monarch_ingest.ensure_monarch_on_startup(_Session(loaded=True)) is None  # type: ignore[arg-type]
    # A newer release stays the administrator's refresh.
    assert calls == []


@pytest.mark.parametrize("switch", ["reference_bootstrap_enabled", "reference_bootstrap_monarch"])
@pytest.mark.asyncio
async def test_either_switch_off_loads_nothing_and_reads_nothing(monkeypatch: pytest.MonkeyPatch, switch: str) -> None:
    monkeypatch.setattr(monarch_ingest.settings, switch, False)
    session = _Session(loaded=False)
    calls = _refresh(monkeypatch)

    assert await monarch_ingest.ensure_monarch_on_startup(session) is None  # type: ignore[arg-type]
    assert calls == [] and session.statements == []


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("no route to host"),
        monarch_ingest.MonarchSourceError("Refusing to load Monarch data with an unknown release version"),
    ],
    ids=["unreachable", "refused"],
)
@pytest.mark.asyncio
async def test_a_failed_load_is_rolled_back_and_the_start_goes_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    session = _Session(loaded=False)
    _refresh(monkeypatch, failure)
    caplog.set_level(logging.INFO, logger=monarch_ingest.logger.name)

    assert await monarch_ingest.ensure_monarch_on_startup(session) is None  # type: ignore[arg-type]

    assert session.rollbacks == 1
    # An expected failure: one warning that says what failed and where to load it instead.
    [warning] = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert warning.levelno == logging.WARNING
    assert str(failure) in warning.getMessage() and "Monarch data" in warning.getMessage()


@pytest.mark.asyncio
async def test_a_defect_is_logged_with_its_traceback_and_the_start_goes_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    session = _Session(loaded=False)
    _refresh(monkeypatch, RuntimeError("a bug"))
    caplog.set_level(logging.INFO, logger=monarch_ingest.logger.name)

    assert await monarch_ingest.ensure_monarch_on_startup(session) is None  # type: ignore[arg-type]

    assert session.rollbacks == 1
    [error] = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert error.exc_info is not None


@pytest.mark.asyncio
async def test_a_failed_read_loads_nothing_and_logs_no_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    session = _Session(fail=RuntimeError("relation monarch_gene_disease does not exist: SECRET-VALUE"))
    calls = _refresh(monkeypatch)
    caplog.set_level(logging.INFO, logger=monarch_ingest.logger.name)

    assert await monarch_ingest.ensure_monarch_on_startup(session) is None  # type: ignore[arg-type]

    assert calls == [] and session.rollbacks == 1
    [error] = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    # Named by its type, never by its text (which can repeat a statement's values).
    assert "RuntimeError" in error and "SECRET-VALUE" not in error


@pytest.mark.asyncio
async def test_the_start_runs_it_after_the_hpo_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session(loaded=False)
    calls = _refresh(monkeypatch)

    class _SessionFactory:
        async def __aenter__(self) -> _Session:
            return session

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

    steps = stub_lifespan_steps(monkeypatch, keep=("ensure_monarch_on_startup",))
    monkeypatch.setattr(app_main, "get_postgres_sessionmaker", lambda: _SessionFactory)
    monkeypatch.setattr(app_main.settings, "family_import_worker_count", 1)
    monkeypatch.setattr(app_main.settings, "hpo_bootstrap_on_startup", False)

    async with app_main.lifespan(LifespanApp()):
        pass

    assert calls == [session]
    # Between the other reference loads of the start and the reference tracks.
    assert steps.index("queue_startup_reference_loads") < steps.index("seed_builtin_reference_tracks")
