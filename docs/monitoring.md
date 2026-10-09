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
  reaches `/metrics` from inside the deployment (below). Locally the backend listens on
  `127.0.0.1:8000`:

  ```bash
  curl -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
  ```

- **One process per instance.** Each backend instance runs one uvicorn process and answers for
  itself, so every instance is scraped (a sidecar does that by construction).
- **A scrape is a request like any other.** The request audit log records it.

## In the deployment

[`terraform/monitoring.tf`](../terraform/monitoring.tf) wires it up on Google Cloud:

1. **The token** is the secret `coga-metrics-token`; the operator adds its value with the other
   secrets ([deployment-gcp.md](deployment-gcp.md) 5.5). Only the backend's account may read it.
2. **A collector runs beside the backend.** The Google-Built OpenTelemetry Collector, pinned by
   digest, is a second container (`metrics-collector`) of the backend's Cloud Run service. Every
   minute it scrapes `localhost:8000/metrics` with the token and writes the series to Google Cloud
   Managed Service for Prometheus. Managed Service for Prometheus' own Cloud Run sidecar cannot
   send a bearer token, so it is not used. The collector's configuration is
   [`terraform/scripts/metrics-collector.yaml.tftpl`](../terraform/scripts/metrics-collector.yaml.tftpl);
   it holds no secret.
3. **Alert policies** in Cloud Monitoring read the series with PromQL and email the addresses in
   `alert_notification_emails` (below). An incident closes by itself 30 minutes after its
   condition clears.
4. **An uptime check** fetches `https://<app_domain>/api/health` every minute from several of
   Google's regions, through DNS, TLS, the load balancer and Cloud Armor, as a user would.

`metrics_collection_enabled = false` leaves the collector, the token and the metric alerts out;
the uptime check stays (`uptime_check_enabled`). Terraform warns when no address is set, and when
the uptime check is on while `allowed_ingress_cidrs` would deny Google's checkers.

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

## The alert policies

| Alert | Severity | Fires when (PromQL) | What to do |
| --- | --- | --- | --- |
| ClickHouse integrity check found damage | critical | `max by (assembly, status) (coga_clickhouse_integrity_status{status=~"corrupt\|missing\|check_failed"}) == 1` | An assembly's variant tables are damaged, or the check could not run: searches on it may fail or miss variants. Bio-IT reads the backend log ("ClickHouse variant integrity") and *Administration → ClickHouse*, and repairs; no sign-out on that assembly until it is clean. |
| ClickHouse integrity check is not running | warning | no sweep for 12 hours (it runs every 6), or the last sweep could not list the assemblies | Damage would go unnoticed: find out in the log why the monitor stopped. |
| Accountability events could not be persisted | critical | `increase(coga_audit_events_not_persisted_total[15m]) > 0` | The audit trail (IVDR) lost events. Each one is in the backend's ERROR log with its payload: restore it from there, and check Cloud SQL. |
| Audit queue is backing up | warning | over 8,000 of the 10,000 queued events, for 10 minutes | The database is not keeping up; requests slow down once the queue is full. Check Cloud SQL. |
| More than 5% of requests fail with a server error | warning | the 5xx share of `coga_http_requests_total` above 5%, for 10 minutes | Something is broken: `coga_http_requests_total` by `route` shows where; read the backend log. |
| A package import shows no sign of life | warning | a validating or running job's staleness above 15 minutes, for 5 minutes | Sign-out of its family stays refused while the job is active. Look at *Administration → Package Import*. Once the heartbeat is 10 minutes old, the next worker that looks for work runs a validating job again, and ends a running one as interrupted without running it again: its family stays marked import-incomplete until what it had not finished is imported again with overwrite ([data-import.md](data-import.md#the-import-job)). A process that is alive but could not write the heartbeat (check its database connections) stops the import once the heartbeat reaches the database again; the backend log names such a job ("was stopped where it was"), and an update or end its job could not record ("matched no row", "could not record it"). |
| A metrics source cannot be read | warning | `increase(coga_metrics_collection_failures_total[15m]) > 0` | The import-job series are missing, so the import alert is blind. Check the backend's connection to Postgres. |
| No metrics from the backend | critical | `absent(coga_build_info)`, for 10 minutes | The backend is down, or the collector cannot scrape it (a 401 means the two containers hold different tokens: roll the backend) or cannot write. Every other metric alert is blind meanwhile. |
| `/api/health` is failing | critical | the uptime check fails from more than one region, for 5 minutes | Users cannot reach CoGA: check the load balancer, its certificate, Cloud Armor and the backend service. |

The thresholds follow the backend's defaults: the integrity check runs every 6 hours
(`CLICKHOUSE_INTEGRITY_INTERVAL_SECONDS=21600`), the audit queue holds 10,000 events
(`AUDIT_LOG_QUEUE_SIZE`), and a worker takes an import job without a heartbeat for 10 minutes
for stopped. The database's clock stamps the heartbeat and judges its age, for the workers as
for the staleness metric, so instances whose clocks differ agree on it. Each backend instance
runs its own integrity monitor and reads the same import jobs, so the queries take the maximum
over instances. The same PromQL works in a self-run
Prometheus.
