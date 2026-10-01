"""``GET /metrics``: off without a token, bearer-guarded, and free of identifiers.

The endpoint exposes what a deployment's monitoring alerts on: requests by route template,
the scheduled ClickHouse integrity check per assembly, the audit pipelines' backlog and lost
events, the active import jobs and the running build. These tests read the exposition with
prometheus_client's own parser, so they check what a scraper would see.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from backend.app.core.config import settings
from backend.app.main import app
from backend.app.services import audit_log_pg, clickhouse_integrity_monitor, event_pipeline, operational_metrics

REPO = Path(__file__).resolve().parents[2]
TOKEN = "metrics-" + "t" * 40


def _samples(body: str) -> dict[tuple[str, frozenset], float]:
    return {
        (sample.name, frozenset(sample.labels.items())): sample.value
        for family in text_string_to_metric_families(body)
        for sample in family.samples
    }


def _value(samples, name: str, **labels) -> float | None:
    return samples.get((name, frozenset(labels.items())))


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "metrics_token", TOKEN)

    async def no_jobs():
        return []

    monkeypatch.setattr(operational_metrics, "_fetch_active_import_jobs", no_jobs)
    app.state.skip_startup_tasks = True
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.state.skip_startup_tasks = False


def _scrape(client) -> dict[tuple[str, frozenset], float]:
    response = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    return _samples(response.text)


# --- access ---------------------------------------------------------------------------


def test_the_endpoint_is_off_without_a_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "metrics_token", "")
    assert client.get("/metrics").status_code == 404
    assert client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
        {"Authorization": f"Bearer {TOKEN}x"},
    ],
)
def test_a_scrape_needs_the_bearer_token(client, headers) -> None:
    response = client.get("/metrics", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "coga_" not in response.text


def test_a_scrape_with_the_token_gets_the_exposition(client) -> None:
    response = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.headers["content-type"] == operational_metrics.CONTENT_TYPE
    assert response.headers["cache-control"] == "no-store"
    # The Python collectors are there too, and the process collector where /proc exists
    # (Linux: the container and CI).
    assert "python_info" in response.text
    if Path("/proc/self/stat").exists():
        assert "process_resident_memory_bytes" in response.text


def test_the_endpoint_is_outside_the_api_schema(client) -> None:
    assert "/metrics" not in app.openapi()["paths"]


# --- request metrics: route templates, never paths -----------------------------------


def test_requests_are_labelled_by_route_template_never_by_their_path(client) -> None:
    # The template as the request audit log records it: relative to the /api router.
    client.get("/api/families/FAM-PRIVATE-4711")  # unauthenticated: 401, but routed
    client.get("/api/no-such-route/SAMPLE-PRIVATE-0815")  # no route: 404
    response = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    samples = _samples(response.text)

    assert "FAM-PRIVATE-4711" not in response.text
    assert "SAMPLE-PRIVATE-0815" not in response.text
    assert _value(
        samples, "coga_http_requests_total", method="GET", route="/families/{family_id}", status="401"
    ) >= 1
    assert _value(samples, "coga_http_requests_total", method="GET", route="unmatched", status="404") >= 1
    assert _value(
        samples, "coga_http_request_duration_seconds_count", method="GET", route="/families/{family_id}"
    ) >= 1


def test_an_unusual_method_is_labelled_other(client) -> None:
    client.request("PROPFIND", "/api/health")
    samples = _scrape(client)
    assert any(
        name == "coga_http_requests_total" and dict(labels).get("method") == "OTHER"
        for name, labels in samples
    )


def test_recording_a_request_never_raises() -> None:
    operational_metrics.record_request("GET", "/api/health", "not-a-status", 0.01)  # type: ignore[arg-type]


def test_the_scrape_is_counted_like_any_request(client) -> None:
    _scrape(client)
    samples = _scrape(client)
    assert _value(samples, "coga_http_requests_total", method="GET", route="/metrics", status="200") >= 1


# --- the running build ----------------------------------------------------------------


def test_the_running_build_is_named(client) -> None:
    samples = _scrape(client)
    assert _value(samples, "coga_build_info", version=settings.app_version, git_sha=settings.git_sha) == 1.0


# --- the scheduled ClickHouse integrity check -----------------------------------------


def test_each_assembly_reports_the_outcome_of_its_last_check(client, monkeypatch) -> None:
    checked = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        clickhouse_integrity_monitor,
        "_last_results",
        {
            "GRCh38": {"assembly_name": "GRCh38", "checked_at": checked, "report": {"status": "corrupt"}, "error": None},
            "GRCh37": {"assembly_name": "GRCh37", "checked_at": checked, "report": None, "error": "could not run"},
        },
    )
    monkeypatch.setattr(clickhouse_integrity_monitor, "_last_sweep", {"at": checked, "error": None})
    samples = _scrape(client)

    for outcome in operational_metrics.INTEGRITY_OUTCOMES:
        assert _value(samples, "coga_clickhouse_integrity_status", assembly="GRCh38", status=outcome) == (
            1.0 if outcome == "corrupt" else 0.0
        )
        assert _value(samples, "coga_clickhouse_integrity_status", assembly="GRCh37", status=outcome) == (
            1.0 if outcome == "check_failed" else 0.0
        )
    assert _value(samples, "coga_clickhouse_integrity_checked_timestamp_seconds", assembly="GRCh38") == checked.timestamp()
    assert _value(samples, "coga_clickhouse_integrity_sweep_failed") == 0.0
    assert _value(samples, "coga_clickhouse_integrity_sweep_timestamp_seconds") == checked.timestamp()


def test_a_sweep_that_could_not_list_the_assemblies_is_flagged(client, monkeypatch) -> None:
    monkeypatch.setattr(clickhouse_integrity_monitor, "_last_results", {})
    monkeypatch.setattr(
        clickhouse_integrity_monitor,
        "_last_sweep",
        {"at": datetime(2026, 10, 1, tzinfo=timezone.utc), "error": clickhouse_integrity_monitor.LIST_FAILED_MESSAGE},
    )
    assert _value(_scrape(client), "coga_clickhouse_integrity_sweep_failed") == 1.0


def test_an_outcome_outside_the_known_set_is_still_shown(client, monkeypatch) -> None:
    checked = datetime(2026, 10, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(
        clickhouse_integrity_monitor,
        "_last_results",
        {"GRCh38": {"assembly_name": "GRCh38", "checked_at": checked, "report": {"status": "odd"}, "error": None}},
    )
    samples = _scrape(client)
    assert _value(samples, "coga_clickhouse_integrity_status", assembly="GRCh38", status="odd") == 1.0
    assert _value(samples, "coga_clickhouse_integrity_status", assembly="GRCh38", status="ok") == 0.0


# --- the audit pipelines --------------------------------------------------------------


def test_the_audit_pipelines_report_their_backlog_and_lost_events(client, monkeypatch) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    for item in range(3):
        queue.put_nowait(item)
    monkeypatch.setattr(audit_log_pg, "_audit_log_queue", queue)
    monkeypatch.setattr(event_pipeline, "_dropped_counts", {"audit_log": 2})
    samples = _scrape(client)

    assert _value(samples, "coga_audit_events_queued", pipeline="audit_log") >= 3
    assert _value(samples, "coga_audit_events_not_persisted_total", pipeline="audit_log") == 2.0
    assert _value(samples, "coga_audit_events_not_persisted_total", pipeline="ui_event") == 0.0


# --- the active import jobs -----------------------------------------------------------


def test_active_import_jobs_are_counted_with_their_staleness(client, monkeypatch) -> None:
    async def jobs():
        return [("queued", 2, 30.0), ("running", 1, 700.5)]

    monkeypatch.setattr(operational_metrics, "_fetch_active_import_jobs", jobs)
    samples = _scrape(client)

    assert _value(samples, "coga_family_import_jobs", status="queued") == 2.0
    assert _value(samples, "coga_family_import_jobs", status="validating") == 0.0
    assert _value(samples, "coga_family_import_jobs", status="running") == 1.0
    assert _value(samples, "coga_family_import_job_staleness_seconds", status="running") == 700.5
    assert _value(samples, "coga_family_import_job_staleness_seconds", status="validating") == 0.0


def test_a_database_fault_leaves_the_import_jobs_out_and_is_counted(client, monkeypatch) -> None:
    before = _value(_scrape(client), "coga_metrics_collection_failures_total", source="postgres") or 0.0

    async def broken():
        raise RuntimeError("database down")

    monkeypatch.setattr(operational_metrics, "_fetch_active_import_jobs", broken)
    response = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    samples = _samples(response.text)

    assert not any(name.startswith("coga_family_import_job") for name, _labels in samples)
    assert _value(samples, "coga_metrics_collection_failures_total", source="postgres") == before + 1
    assert _value(samples, "coga_build_info", version=settings.app_version, git_sha=settings.git_sha) == 1.0


# --- where the endpoint can be reached -----------------------------------------------


def test_neither_the_load_balancer_nor_the_frontend_server_routes_it() -> None:
    # The load balancer sends only /api and /api/* to the backend, and the frontend server
    # proxies only /api: /metrics never reaches the internet.
    urlmap = (REPO / "terraform" / "loadbalancer.tf").read_text()
    backend_rules = [line.strip() for line in urlmap.splitlines() if line.strip().startswith("paths")]
    assert backend_rules == ['paths   = ["/api", "/api/*"]']
    server = (REPO / "frontend" / "server.mjs").read_text()
    assert "app.use('/api'," in server
    assert "/metrics" not in server
