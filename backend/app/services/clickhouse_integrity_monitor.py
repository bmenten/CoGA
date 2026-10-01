"""Scheduled ClickHouse variant-integrity monitor (#269).

Runs ``check_clickhouse_variant_integrity`` over every variant assembly shortly after
startup and then on a fixed interval, logging the per-assembly result and escalating to
ERROR on ``corrupt``/``missing`` so operations alerting can fire BEFORE the corruption
surfaces as gene/panel query 500s.

The last result per assembly is kept in memory, dated, and served to admins by
``GET /api/admin/clickhouse/variants/integrity-monitor`` (``integrity_monitor_state``): the
report, or that the check could not run. A failed check replaces the assembly's earlier
result, so a stale "ok" is never the latest word. The state belongs to this server process
and starts empty with it.

``GET /metrics`` exposes the same state as ``coga_clickhouse_integrity_status`` per assembly
and outcome (services/operational_metrics.py), beside the structured ERROR log.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from ..core.config import settings
from .clickhouse_variant_storage import (
    check_clickhouse_variant_integrity,
    list_clickhouse_variant_assemblies,
)

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
# Per assembly: {assembly_name, checked_at, report, error}; exactly one of report/error set.
_last_results: dict[str, dict[str, Any]] = {}
# The latest sweep: {"at": when it ran, "error": why it could not list the assemblies}.
_last_sweep: dict[str, Any] = {}

# Statuses that should page someone (a clinical datastore is damaged or absent).
_ALERT_STATUSES = {"corrupt", "missing"}

# What an admin is told when a check or the sweep fails. The exception itself goes to the
# log only: it can carry server details that do not belong in a response.
CHECK_FAILED_MESSAGE = "The integrity check could not run; the backend log has the error."
LIST_FAILED_MESSAGE = "The variant assemblies could not be listed; the backend log has the error."


def last_integrity_results() -> dict[str, dict[str, Any]]:
    """The most recent scheduled-sweep result per assembly."""
    return dict(_last_results)


def integrity_monitor_state() -> dict[str, Any]:
    """What the admin endpoint serves: the setting, the interval, the last sweep, and the
    last result per assembly (sorted by name)."""
    return {
        "enabled": settings.clickhouse_integrity_monitor_enabled,
        "interval_seconds": settings.clickhouse_integrity_interval_seconds,
        "last_sweep_at": _last_sweep.get("at"),
        "last_sweep_error": _last_sweep.get("error"),
        "results": [dict(_last_results[name]) for name in sorted(_last_results)],
    }


async def run_integrity_sweep() -> dict[str, dict[str, Any]]:
    """Check every variant assembly once, logging + recording each result. Never raises."""
    try:
        assemblies = await list_clickhouse_variant_assemblies()
    except Exception:
        logger.exception("ClickHouse integrity sweep could not list assemblies")
        _last_sweep.update(at=datetime.now(timezone.utc), error=LIST_FAILED_MESSAGE)
        return dict(_last_results)
    for assembly in assemblies:
        try:
            result = await check_clickhouse_variant_integrity(assembly)
        except Exception:
            logger.exception("ClickHouse integrity check failed for assembly %s", assembly)
            _last_results[assembly] = {
                "assembly_name": assembly,
                "checked_at": datetime.now(timezone.utc),
                "report": None,
                "error": CHECK_FAILED_MESSAGE,
            }
            continue
        _last_results[assembly] = {
            "assembly_name": assembly,
            "checked_at": datetime.now(timezone.utc),
            "report": result,
            "error": None,
        }
        status = result.get("status")
        extra = {"assembly": assembly, "integrity_status": status, "notes": result.get("notes")}
        if status in _ALERT_STATUSES:
            logger.error("ClickHouse variant integrity %s for %s", status, assembly, extra=extra)
        elif status == "degraded":
            logger.warning("ClickHouse variant integrity degraded for %s", assembly, extra=extra)
        else:
            logger.info("ClickHouse variant integrity ok for %s", assembly, extra=extra)
    _last_sweep.update(at=datetime.now(timezone.utc), error=None)
    return dict(_last_results)


async def _integrity_monitor_worker() -> None:
    assert _stop_event is not None
    # Let the app settle before the first sweep; bail out if shutdown beats the delay.
    try:
        await asyncio.wait_for(
            _stop_event.wait(), timeout=settings.clickhouse_integrity_startup_delay_seconds
        )
        return
    except asyncio.TimeoutError:
        pass
    while not _stop_event.is_set():
        try:
            await run_integrity_sweep()
        except Exception:
            logger.exception("ClickHouse integrity sweep failed")
        try:
            await asyncio.wait_for(
                _stop_event.wait(), timeout=settings.clickhouse_integrity_interval_seconds
            )
            return
        except asyncio.TimeoutError:
            continue


async def start_clickhouse_integrity_monitor() -> None:
    global _worker_task, _stop_event
    if not settings.clickhouse_integrity_monitor_enabled or _worker_task is not None:
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_integrity_monitor_worker())


async def stop_clickhouse_integrity_monitor() -> None:
    global _worker_task, _stop_event
    if _worker_task is None:
        _stop_event = None
        return
    if _stop_event is not None:
        _stop_event.set()
    try:
        await _worker_task
    except Exception:
        logger.exception("ClickHouse integrity monitor failed during shutdown")
    finally:
        _worker_task = None
        _stop_event = None
