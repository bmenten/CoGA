"""Operational metrics for ``GET /metrics``, in the Prometheus text format.

What a deployment's monitoring reads to alert on (TF-13): request rates, errors and latency
per route; the scheduled ClickHouse integrity check per assembly; the audit pipelines'
backlog and the accountability events they could not persist; the active package-import
jobs and how long since each showed a sign of life; and the running build.

No label carries clinical data or an identifier. A request is labelled by the template of
the route it matched, as the request audit log records it (relative to the ``/api`` router:
``/families/{family_id}``; ``/metrics`` itself at the root), never by the path it carried; a
request that matched no route by ``unmatched``; an unusual HTTP method by ``OTHER``. That
also bounds the number of series, whatever a client sends.

One registry per process: the backend runs one uvicorn process per instance, and each
instance answers for itself. Recording a request never raises into it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    GCCollector,
    Histogram,
    Info,
    PlatformCollector,
    ProcessCollector,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from sqlalchemy import text

from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from .audit_log_pg import audit_log_queue_depth
from .clickhouse_integrity_monitor import integrity_monitor_state, last_integrity_results
from .event_pipeline import dropped_event_count
from .ui_event_pg import ui_event_queue_depth

logger = logging.getLogger(__name__)

CONTENT_TYPE = CONTENT_TYPE_LATEST
UNMATCHED_ROUTE = "unmatched"
_KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
# Outcomes of the scheduled integrity check (clickhouse_variant_storage), plus a check
# that could not run. Each assembly reports 1 for its outcome and 0 for the others, so an
# alert can match `status=~"corrupt|missing|check_failed"` without knowing the assemblies.
INTEGRITY_OUTCOMES = ("ok", "degraded", "corrupt", "missing", "check_failed")
ACTIVE_IMPORT_STATUSES = ("queued", "validating", "running")
_AUDIT_PIPELINES = (("audit_log", audit_log_queue_depth), ("ui_event", ui_event_queue_depth))

# A counter's `_created` series (when it was first incremented) says nothing an alert reads,
# and Managed Service for Prometheus bills every series it stores.
disable_created_metrics()

REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

HTTP_REQUESTS = Counter(
    "coga_http_requests",
    "Requests answered, by HTTP method, route template and status code.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION = Histogram(
    "coga_http_request_duration_seconds",
    "Time to answer a request, by HTTP method and route template.",
    ["method", "route"],
    # A family search can take tens of seconds on ClickHouse; the top buckets catch it.
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=REGISTRY,
)
METRICS_COLLECTION_FAILURES = Counter(
    "coga_metrics_collection_failures",
    "Scrapes that could not read one of their sources (it is then left out of the scrape).",
    ["source"],
    registry=REGISTRY,
)
BUILD = Info("coga_build", "The running build: the version and the commit it was made from.", registry=REGISTRY)
BUILD.info({"version": settings.app_version, "git_sha": settings.git_sha})


def record_request(method: str, route: str | None, status_code: int, duration_seconds: float) -> None:
    """Count one answered request and its duration. Never raises."""
    try:
        method_label = method.upper() if method.upper() in _KNOWN_METHODS else "OTHER"
        route_label = route or UNMATCHED_ROUTE
        HTTP_REQUESTS.labels(method=method_label, route=route_label, status=str(int(status_code))).inc()
        HTTP_REQUEST_DURATION.labels(method=method_label, route=route_label).observe(max(duration_seconds, 0.0))
    except Exception:  # a metrics fault must never fail the request it describes
        logger.exception("Could not record request metrics")


class _StateCollector(Collector):
    """Reads, at scrape time, state the backend already keeps in memory."""

    def collect(self) -> Iterable[Metric]:
        yield from _integrity_metrics()
        yield from _audit_pipeline_metrics()


def _integrity_metrics() -> Iterable[Metric]:
    status = GaugeMetricFamily(
        "coga_clickhouse_integrity_status",
        "The last scheduled ClickHouse integrity check per assembly: 1 for its outcome "
        "(ok, degraded, corrupt, missing, check_failed), 0 for the others.",
        labels=["assembly", "status"],
    )
    checked = GaugeMetricFamily(
        "coga_clickhouse_integrity_checked_timestamp_seconds",
        "When the last scheduled integrity check of the assembly ran (Unix time).",
        labels=["assembly"],
    )
    for assembly, result in sorted(last_integrity_results().items()):
        outcome = "check_failed" if result.get("error") else str((result.get("report") or {}).get("status") or "")
        outcomes = INTEGRITY_OUTCOMES if outcome in INTEGRITY_OUTCOMES else (*INTEGRITY_OUTCOMES, outcome or "unknown")
        for candidate in outcomes:
            status.add_metric([assembly, candidate], 1.0 if candidate == (outcome or "unknown") else 0.0)
        checked_at = result.get("checked_at")
        if checked_at is not None:
            checked.add_metric([assembly], checked_at.timestamp())
    yield status
    yield checked

    state = integrity_monitor_state()
    sweep_failed = GaugeMetricFamily(
        "coga_clickhouse_integrity_sweep_failed",
        "1 when the last scheduled sweep could not list the variant assemblies, else 0.",
        value=1.0 if state.get("last_sweep_error") else 0.0,
    )
    yield sweep_failed
    last_sweep_at = state.get("last_sweep_at")
    if last_sweep_at is not None:
        yield GaugeMetricFamily(
            "coga_clickhouse_integrity_sweep_timestamp_seconds",
            "When the last scheduled integrity sweep ran (Unix time).",
            value=last_sweep_at.timestamp(),
        )


def _audit_pipeline_metrics() -> Iterable[Metric]:
    queued = GaugeMetricFamily(
        "coga_audit_events_queued",
        "Events waiting in the pipeline's queue for the database (audit_log: requests; ui_event: UI events).",
        labels=["pipeline"],
    )
    not_persisted = CounterMetricFamily(
        "coga_audit_events_not_persisted",
        "Accountability events the pipeline could not persist since the process started; "
        "each is also logged at ERROR with its payload.",
        labels=["pipeline"],
    )
    for pipeline, depth in _AUDIT_PIPELINES:
        queued.add_metric([pipeline], float(depth()))
        not_persisted.add_metric([pipeline], float(dropped_event_count(pipeline)))
    yield queued
    yield not_persisted


class _ImportJobCollector(Collector):
    """The active package-import jobs, as read from Postgres just before the scrape."""

    def __init__(self) -> None:
        self.snapshot: dict[str, tuple[int, float]] | None = None

    def collect(self) -> Iterable[Metric]:
        if self.snapshot is None:
            return
        jobs = GaugeMetricFamily(
            "coga_family_import_jobs",
            "Package-import jobs by status, for the statuses that are still active.",
            labels=["status"],
        )
        staleness = GaugeMetricFamily(
            "coga_family_import_job_staleness_seconds",
            "Seconds since the least recent sign of life (heartbeat, start or request) among "
            "the active jobs of the status; 0 when there are none.",
            labels=["status"],
        )
        for job_status in ACTIVE_IMPORT_STATUSES:
            count, oldest = self.snapshot.get(job_status, (0, 0.0))
            jobs.add_metric([job_status], float(count))
            staleness.add_metric([job_status], oldest)
        yield jobs
        yield staleness


_IMPORT_JOBS = _ImportJobCollector()
REGISTRY.register(_StateCollector())
REGISTRY.register(_IMPORT_JOBS)


def _import_job_snapshot(rows: Sequence[Any]) -> dict[str, tuple[int, float]]:
    snapshot: dict[str, tuple[int, float]] = {}
    for job_status, count, oldest_seconds in rows:
        if job_status in ACTIVE_IMPORT_STATUSES:
            snapshot[str(job_status)] = (int(count or 0), max(float(oldest_seconds or 0.0), 0.0))
    return snapshot


async def _fetch_active_import_jobs() -> Sequence[Any]:
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            text(
                """
                SELECT status,
                       count(*),
                       EXTRACT(EPOCH FROM (now() - min(COALESCE(heartbeat_at, started_at, requested_at))))
                FROM family_import_jobs
                WHERE status IN ('queued', 'validating', 'running')
                GROUP BY status
                """
            )
        )
        return result.all()


async def render_metrics() -> bytes:
    """The exposition for one scrape. A source that cannot be read is left out of it and
    counted (``coga_metrics_collection_failures_total``); the rest is still served."""
    try:
        _IMPORT_JOBS.snapshot = _import_job_snapshot(await _fetch_active_import_jobs())
    except Exception:  # a database fault must not take the other metrics down
        logger.exception("Could not read the active import jobs for /metrics")
        _IMPORT_JOBS.snapshot = None
        METRICS_COLLECTION_FAILURES.labels(source="postgres").inc()
    return generate_latest(REGISTRY)
