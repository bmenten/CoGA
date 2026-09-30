"""P2-6: the scheduled ClickHouse integrity monitor sweeps, escalates, and records its result.

Each sweep records, per assembly, when it checked and what it found: the integrity report,
or that the check could not run. A failed check replaces an earlier good result, so the
record never shows a stale "healthy" as the latest word. A sweep that cannot list the
assemblies says so and keeps the earlier per-assembly results, each dated.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import pytest

from backend.app.core.config import settings
from backend.app.services import clickhouse_integrity_monitor as mon


@pytest.fixture(autouse=True)
def _reset():
    mon._last_results.clear()
    mon._last_sweep.clear()
    yield
    mon._last_results.clear()
    mon._last_sweep.clear()


def _patch(monkeypatch, assemblies, results):
    async def fake_list():
        return assemblies

    async def fake_check(name):
        value = results[name]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(mon, "list_clickhouse_variant_assemblies", fake_list)
    monkeypatch.setattr(mon, "check_clickhouse_variant_integrity", fake_check)


def test_sweep_records_and_escalates_by_status(monkeypatch, caplog):
    _patch(
        monkeypatch,
        ["GRCh38", "GRCh37"],
        {
            "GRCh38": {"status": "ok", "notes": []},
            "GRCh37": {"status": "corrupt", "notes": ["bad part"]},
        },
    )
    with caplog.at_level(logging.INFO, logger=mon.logger.name):
        out = asyncio.run(mon.run_integrity_sweep())
    assert out["GRCh37"]["report"]["status"] == "corrupt"
    recorded = mon.last_integrity_results()["GRCh37"]
    assert recorded["report"] == {"status": "corrupt", "notes": ["bad part"]}
    assert recorded["error"] is None
    assert isinstance(recorded["checked_at"], datetime) and recorded["checked_at"].tzinfo is not None
    assert isinstance(mon._last_sweep["at"], datetime)
    assert mon._last_sweep["error"] is None
    # corrupt -> ERROR; ok -> INFO
    assert any(r.levelno == logging.ERROR and "GRCh37" in r.getMessage() for r in caplog.records)
    assert any(r.levelno == logging.INFO and "GRCh38" in r.getMessage() for r in caplog.records)


def test_sweep_warns_on_degraded(monkeypatch, caplog):
    _patch(monkeypatch, ["GRCh38"], {"GRCh38": {"status": "degraded", "notes": ["detached"]}})
    with caplog.at_level(logging.WARNING, logger=mon.logger.name):
        asyncio.run(mon.run_integrity_sweep())
    assert any(r.levelno == logging.WARNING and "degraded" in r.getMessage() for r in caplog.records)


def test_a_check_that_fails_is_recorded_as_failed_not_as_the_earlier_result(monkeypatch, caplog):
    _patch(monkeypatch, ["GRCh38"], {"GRCh38": {"status": "ok", "notes": []}})
    asyncio.run(mon.run_integrity_sweep())
    earlier = mon.last_integrity_results()["GRCh38"]["checked_at"]

    _patch(monkeypatch, ["GRCh38"], {"GRCh38": RuntimeError("boom")})
    with caplog.at_level(logging.ERROR, logger=mon.logger.name):
        out = asyncio.run(mon.run_integrity_sweep())  # never raises

    failed = out["GRCh38"]
    assert failed["report"] is None
    assert failed["error"] == mon.CHECK_FAILED_MESSAGE
    assert failed["checked_at"] >= earlier
    # The exception itself goes to the log, not into the result an endpoint serves.
    assert "boom" not in failed["error"]
    assert any(r.levelno == logging.ERROR and "GRCh38" in r.getMessage() for r in caplog.records)


def test_a_sweep_that_cannot_list_the_assemblies_says_so(monkeypatch):
    _patch(monkeypatch, ["GRCh38"], {"GRCh38": {"status": "ok", "notes": []}})
    asyncio.run(mon.run_integrity_sweep())

    async def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(mon, "list_clickhouse_variant_assemblies", boom)
    out = asyncio.run(mon.run_integrity_sweep())  # never raises

    assert mon._last_sweep["error"] == mon.LIST_FAILED_MESSAGE
    # The earlier result is kept, dated by its own check.
    assert out["GRCh38"]["report"] == {"status": "ok", "notes": []}


def test_the_monitor_state_names_the_setting_the_interval_and_the_results(monkeypatch):
    monkeypatch.setattr(settings, "clickhouse_integrity_monitor_enabled", True)
    monkeypatch.setattr(settings, "clickhouse_integrity_interval_seconds", 21_600)
    assert mon.integrity_monitor_state() == {
        "enabled": True,
        "interval_seconds": 21_600,
        "last_sweep_at": None,
        "last_sweep_error": None,
        "results": [],
    }

    _patch(
        monkeypatch,
        ["T2T-CHM13", "GRCh38"],
        {"GRCh38": {"status": "ok", "notes": []}, "T2T-CHM13": RuntimeError("down")},
    )
    asyncio.run(mon.run_integrity_sweep())
    state = mon.integrity_monitor_state()

    assert state["last_sweep_at"] == mon._last_sweep["at"]
    assert [row["assembly_name"] for row in state["results"]] == ["GRCh38", "T2T-CHM13"]
    assert state["results"][0]["report"] == {"status": "ok", "notes": []}
    assert state["results"][1]["error"] == mon.CHECK_FAILED_MESSAGE


def test_start_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "clickhouse_integrity_monitor_enabled", False)

    async def _go():
        await mon.start_clickhouse_integrity_monitor()
        assert mon._worker_task is None
        await mon.stop_clickhouse_integrity_monitor()

    asyncio.run(_go())


def test_worker_sweeps_then_stops_cleanly(monkeypatch):
    monkeypatch.setattr(settings, "clickhouse_integrity_monitor_enabled", True)
    monkeypatch.setattr(settings, "clickhouse_integrity_startup_delay_seconds", 0)
    monkeypatch.setattr(settings, "clickhouse_integrity_interval_seconds", 60)
    sweeps = {"n": 0}

    async def fake_sweep():
        sweeps["n"] += 1
        return {}

    monkeypatch.setattr(mon, "run_integrity_sweep", fake_sweep)

    async def _go():
        await mon.start_clickhouse_integrity_monitor()
        await asyncio.sleep(0.1)  # let the initial (0-delay) sweep run
        await mon.stop_clickhouse_integrity_monitor()
        assert mon._worker_task is None

    asyncio.run(_go())
    assert sweeps["n"] >= 1
