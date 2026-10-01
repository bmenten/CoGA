# Monitoring

`GET /metrics` gives the backend's operational metrics in the Prometheus text format: what a
deployment alerts on when requests fail, a datastore is damaged, the audit trail backs up or an
import gets stuck. None of it is clinical data. The code is
[`backend/app/services/operational_metrics.py`](../backend/app/services/operational_metrics.py)
and [`backend/app/routers/metrics.py`](../backend/app/routers/metrics.py).

## Turning it on

- **Off until a token is set.** Without `METRICS_TOKEN` the endpoint answers `404`.
- **A scrape sends the token.** With `METRICS_TOKEN` set, a request must carry
  `Authorization: Bearer <token>`; anything else gets `401`. Outside development the token needs
  at least 32 characters and must differ from `SECRET_KEY` and `INTEGRITY_ANCHOR_SIGNING_KEY`, or
  the backend refuses to start. Generate one with
  `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
- **Never served to the internet.** The path is outside `/api`. The load balancer routes only
  `/api` and `/api/*` to the backend, and the frontend server proxies only `/api`, so a scraper
  reaches `/metrics` from inside the deployment: for example a Prometheus or OpenTelemetry
  collector running as a sidecar of the backend's Cloud Run service, scraping
  `localhost:8000/metrics` with the token. Locally the backend listens on `127.0.0.1:8000`:

  ```bash
  curl -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
  ```

- **One process per instance.** Each backend instance runs one uvicorn process and answers for
  itself, so every instance is scraped (a sidecar does that by construction).
- **A scrape is a request like any other.** The request audit log records it.

The deployment does not wire this up yet: Terraform sets no `METRICS_TOKEN` and runs no
collector, and no alert policy exists. Section 12.6 of
[deployment-gcp.md](deployment-gcp.md#126-logs--monitoring) says how to add them.

## What it exposes

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `coga_http_requests_total` | counter | `method`, `route`, `status` | Requests answered. |
| `coga_http_request_duration_seconds` | histogram | `method`, `route` | Time to answer, in buckets from 10 ms to 60 s. |
| `coga_clickhouse_integrity_status` | gauge | `assembly`, `status` | The last scheduled integrity check of each assembly: 1 for its outcome (`ok`, `degraded`, `corrupt`, `missing`, `check_failed`), 0 for the others. |
| `coga_clickhouse_integrity_checked_timestamp_seconds` | gauge | `assembly` | When that check ran (Unix time). |
| `coga_clickhouse_integrity_sweep_failed` | gauge | | 1 when the last sweep could not list the variant assemblies. |
| `coga_clickhouse_integrity_sweep_timestamp_seconds` | gauge | | When the last sweep ran (Unix time). |
| `coga_audit_events_queued` | gauge | `pipeline` | Events waiting in the queue for the database: `audit_log` (requests) and `ui_event` (UI events). |
| `coga_audit_events_not_persisted_total` | counter | `pipeline` | Accountability events the pipeline could not persist since the process started. Each is also logged at ERROR with its payload. |
| `coga_family_import_jobs` | gauge | `status` | Package-import jobs that are `queued`, `validating` or `running`. |
| `coga_family_import_job_staleness_seconds` | gauge | `status` | Seconds since the least recent sign of life (heartbeat, start or request) among those jobs; 0 when there are none. |
| `coga_metrics_collection_failures_total` | counter | `source` | Scrapes that could not read a source (`postgres` for the import jobs); that part is then left out of the scrape. |
| `coga_build_info` | gauge | `version`, `git_sha` | The running build, always 1. |
| `python_*`, `process_*` | | | Python and process metrics from the client library; `process_*` on Linux only. |

**No identifiers in labels.** `route` is the template of the route a request matched, as the
request audit log records it (relative to the `/api` router: `/families/{family_id}`;
`/metrics` itself at the root), never the path the request carried. A request that matched no
route is labelled `unmatched`, and a method outside the standard set `OTHER`, which also bounds
the number of series.

## Suggested alert rules

In Prometheus rule syntax; Cloud Monitoring reads the same expressions as PromQL alert
policies. The thresholds follow the defaults: the integrity check runs every 6 hours
(`CLICKHOUSE_INTEGRITY_INTERVAL_SECONDS=21600`), the audit queue holds 10,000 events
(`AUDIT_LOG_QUEUE_SIZE`), and an import job without a heartbeat for 10 minutes is taken over by
the next worker.

```yaml
groups:
  - name: coga
    rules:
      - alert: CoGAClickHouseIntegrity
        expr: coga_clickhouse_integrity_status{status=~"corrupt|missing|check_failed"} == 1
        labels: {severity: page}
        annotations:
          summary: "ClickHouse integrity check: {{ $labels.status }} for {{ $labels.assembly }}"
      - alert: CoGAIntegrityCheckNotRunning
        expr: time() - coga_clickhouse_integrity_sweep_timestamp_seconds > 2 * 21600 or coga_clickhouse_integrity_sweep_failed == 1
        labels: {severity: ticket}
      - alert: CoGAAuditEventsNotPersisted
        expr: increase(coga_audit_events_not_persisted_total[15m]) > 0
        labels: {severity: page}
        annotations:
          summary: "{{ $labels.pipeline }}: accountability events could not be persisted (their payloads are in the ERROR log)"
      - alert: CoGAAuditBacklog
        expr: coga_audit_events_queued > 8000
        for: 10m
        labels: {severity: ticket}
      - alert: CoGAServerErrors
        expr: sum(rate(coga_http_requests_total{status=~"5.."}[5m])) / sum(rate(coga_http_requests_total[5m])) > 0.05
        for: 10m
        labels: {severity: ticket}
      - alert: CoGAImportStuck
        expr: coga_family_import_job_staleness_seconds{status=~"validating|running"} > 900
        labels: {severity: ticket}
        annotations:
          summary: "An import job shows no sign of life; sign-out of its family stays refused until it ends"
      - alert: CoGAMetricsSourceFailing
        expr: increase(coga_metrics_collection_failures_total[15m]) > 0
        labels: {severity: ticket}
```
