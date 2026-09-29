"""P2-6: scheduled ClickHouse variant-integrity monitor.

Runs ``check_clickhouse_variant_integrity`` over every variant assembly shortly after
startup and then on a fixed interval, logging the per-assembly result and escalating to
ERROR on ``corrupt``/``missing`` so operations alerting can fire BEFORE the corruption
surfaces as gene/panel query 500s. The last result per assembly is cached for surfacing.

(Emitting the result as a ``/metrics`` gauge is deferred until a metrics endpoint exists —
see docs/ROADMAP.md; the structured ERROR log is the alert hook in the meantime.)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..core.config import settings
from .clickhouse_variant_storage import (
    check_clickhouse_variant_integrity,
    list_clickhouse_variant_assemblies,
)

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_last_results: dict[str, dict[str, Any]] = {}

# Statuses that should page someone (a clinical datastore is damaged or absent).
_ALERT_STATUSES = {"corrupt", "missing"}


def last_integrity_results() -> dict[str, dict[str, Any]]:
    """The most recent scheduled-sweep result per assembly (for admin/health surfacing)."""
    return dict(_last_results)


async def run_integrity_sweep() -> dict[str, dict[str, Any]]:
    """Check every variant assembly once, logging + caching each result. Never raises."""
    try:
        assemblies = await list_clickhouse_variant_assemblies()
    except Exception:
        logger.exception("ClickHouse integrity sweep could not list assemblies")
        return dict(_last_results)
    for assembly in assemblies:
        try:
            result = await check_clickhouse_variant_integrity(assembly)
        except Exception:
            logger.exception("ClickHouse integrity check failed for assembly %s", assembly)
            continue
        _last_results[assembly] = result
        status = result.get("status")
        extra = {"assembly": assembly, "integrity_status": status, "notes": result.get("notes")}
        if status in _ALERT_STATUSES:
            logger.error("ClickHouse variant integrity %s for %s", status, assembly, extra=extra)
        elif status == "degraded":
            logger.warning("ClickHouse variant integrity degraded for %s", assembly, extra=extra)
        else:
            logger.info("ClickHouse variant integrity ok for %s", assembly, extra=extra)
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
