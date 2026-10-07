# ==========================================
# MONITORING AND ALERTING
# ==========================================
#
# The backend serves its operational metrics at GET /metrics (docs/monitoring.md). The endpoint
# is off unless METRICS_TOKEN is set, and it sits outside /api, so the load balancer never
# routes it. A collector runs as a sidecar of the backend's Cloud Run service (cloudrun.tf): it
# scrapes the endpoint over the instance's loopback with the token, and writes the series to
# Google Cloud Managed Service for Prometheus. The alert policies below read them with PromQL,
# and an uptime check watches the public path through the load balancer.
#
# Managed Service for Prometheus' own Cloud Run sidecar cannot send a bearer token (its scrape
# configuration has no authorization field), so this runs the Google-Built OpenTelemetry
# Collector, whose Prometheus receiver can.
#
# Project-level roles are granted in the central infra repo (main-repo-reference/): the
# backend's account, which the collector runs as, already holds roles/monitoring.metricWriter;
# the deploy account needs the rights to manage alert policies, notification channels and
# uptime checks (rollout-checklist.md, Part C).

# The bearer token the collector sends and the backend checks. Like the app secrets, Terraform
# creates the container only; the value is added out of band (docs/deployment-gcp.md 5.5).
resource "google_secret_manager_secret" "metrics_token" {
  secret_id = "${local.name_prefix}-metrics-token"
  labels    = var.labels

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

# Both containers of a backend revision run as the backend's account.
resource "google_secret_manager_secret_iam_member" "backend_metrics_token" {
  secret_id = google_secret_manager_secret.metrics_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.backend_sa_email}"
}

locals {
  backend_service_name = "${local.name_prefix}-backend"
  # The port the backend container listens on (cloudrun.tf), which the collector scrapes, and
  # the collector's health-check port, which its probes read.
  backend_port                  = 8000
  metrics_collector_health_port = 13133

  # Passed to the collector as an environment variable (--config=env:OTELCOL_CONFIG). It holds
  # no secret: the token reaches the collector as METRICS_TOKEN, from Secret Manager.
  metrics_collector_config = templatefile("${path.module}/scripts/metrics-collector.yaml.tftpl", {
    job             = local.backend_service_name
    namespace       = local.backend_service_name
    project         = var.project_id
    backend_port    = local.backend_port
    health_port     = local.metrics_collector_health_port
    scrape_interval = "60s"
  })

  alert_channels = [for channel in google_monitoring_notification_channel.email : channel.id]

  # One alert policy per entry, on the series the backend exposes. Each backend instance runs
  # its own integrity monitor and reads the same import jobs, so the queries take the maximum
  # over instances. The thresholds follow the backend's defaults: the integrity check runs
  # every 6 hours, the audit queue holds 10,000 events, and an import job without a heartbeat
  # for 10 minutes is taken over by the next worker.
  metric_alerts = {
    clickhouse_integrity = {
      name     = "CoGA: ClickHouse integrity check found damage"
      severity = "CRITICAL"
      duration = "0s"
      query    = "max by (assembly, status) (coga_clickhouse_integrity_status{status=~\"corrupt|missing|check_failed\"}) == 1"
      doc      = "The scheduled integrity check of an assembly's variant tables reported corrupt or missing data, or could not run. Gene and panel searches on that assembly may fail or miss variants. See the backend log (\"ClickHouse variant integrity\") and Administration, ClickHouse, then docs/monitoring.md."
    }
    integrity_check_not_running = {
      name     = "CoGA: ClickHouse integrity check is not running"
      severity = "WARNING"
      duration = "0s"
      query    = "(time() - max(coga_clickhouse_integrity_sweep_timestamp_seconds)) > 43200 or max(coga_clickhouse_integrity_sweep_failed) == 1"
      doc      = "No scheduled integrity sweep for 12 hours (it runs every 6), or the last sweep could not list the variant assemblies. Damage would go unnoticed. See the backend log and docs/monitoring.md."
    }
    audit_events_not_persisted = {
      name     = "CoGA: accountability events could not be persisted"
      severity = "CRITICAL"
      duration = "0s"
      query    = "sum by (pipeline) (increase(coga_audit_events_not_persisted_total[15m])) > 0"
      doc      = "The request audit log or the UI-event log lost events: their database write failed, after the worker's retries or as a synchronous write. Each lost event is in the backend's ERROR log with its payload; recover them from there. Check Cloud SQL. See docs/monitoring.md."
    }
    audit_backlog = {
      name     = "CoGA: audit queue is backing up"
      severity = "WARNING"
      duration = "600s"
      query    = "max by (pipeline) (coga_audit_events_queued) > 8000"
      doc      = "An audit pipeline's queue has held over 8,000 of its 10,000 events for 10 minutes: the database is not keeping up. When it fills, requests write their audit rows synchronously and slow down. Check Cloud SQL. See docs/monitoring.md."
    }
    server_errors = {
      name     = "CoGA: more than 5% of requests fail with a server error"
      severity = "WARNING"
      duration = "600s"
      query    = "sum(rate(coga_http_requests_total{status=~\"5..\"}[5m])) / sum(rate(coga_http_requests_total[5m])) > 0.05"
      doc      = "Over 5% of the backend's answers were 5xx for 10 minutes. coga_http_requests_total by route shows which. See the backend log and docs/monitoring.md."
    }
    import_stuck = {
      name     = "CoGA: a package import shows no sign of life"
      severity = "WARNING"
      duration = "300s"
      query    = "max by (status) (coga_family_import_job_staleness_seconds{status=~\"validating|running\"}) > 900"
      doc      = "An import job has not reported for 15 minutes; after 10 the next worker takes it over. While it stays active, sign-out of its family is refused. See Administration, Package Import, and docs/monitoring.md."
    }
    metrics_source_failing = {
      name     = "CoGA: a metrics source cannot be read"
      severity = "WARNING"
      duration = "0s"
      query    = "sum by (source) (increase(coga_metrics_collection_failures_total[15m])) > 0"
      doc      = "A scrape could not read one of its sources (postgres: the import jobs), so those series are missing and their alerts cannot fire. See the backend log and docs/monitoring.md."
    }
    metrics_absent = {
      name     = "CoGA: no metrics from the backend"
      severity = "CRITICAL"
      duration = "600s"
      query    = "absent(coga_build_info)"
      doc      = "No backend metrics for 10 minutes: the backend is down, or the collector beside it cannot scrape it (wrong or missing coga-metrics-token) or write to Managed Service for Prometheus. Every other metric alert is blind meanwhile. See the collector's log in the backend service, and docs/monitoring.md."
    }
  }
}

resource "google_monitoring_notification_channel" "email" {
  for_each     = toset(var.alert_notification_emails)
  display_name = "CoGA alerts: ${each.value}"
  type         = "email"
  labels = {
    email_address = each.value
  }
  user_labels = var.labels
}

resource "google_monitoring_alert_policy" "metrics" {
  for_each     = var.metrics_collection_enabled ? local.metric_alerts : {}
  display_name = each.value.name
  combiner     = "OR"
  severity     = each.value.severity
  user_labels  = merge(var.labels, { coga_alert = each.key })

  conditions {
    display_name = each.value.name
    condition_prometheus_query_language {
      query               = each.value.query
      duration            = each.value.duration
      evaluation_interval = "60s"
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  notification_channels = local.alert_channels

  documentation {
    content   = each.value.doc
    mime_type = "text/markdown"
  }
}

# The public path: DNS, TLS, the load balancer, Cloud Armor and the backend, as a user meets it.
resource "google_monitoring_uptime_check_config" "health" {
  count        = var.uptime_check_enabled ? 1 : 0
  display_name = "CoGA: /api/health"
  timeout      = "10s"
  period       = "60s"
  checker_type = "STATIC_IP_CHECKERS"

  http_check {
    path           = "/api/health"
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"
    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.app_domain
    }
  }

  content_matchers {
    content = "\"status\":\"ok\""
    matcher = "CONTAINS_STRING"
  }

  user_labels = var.labels
}

resource "google_monitoring_alert_policy" "uptime" {
  count        = var.uptime_check_enabled ? 1 : 0
  display_name = "CoGA: /api/health is failing"
  combiner     = "OR"
  severity     = "CRITICAL"
  user_labels  = merge(var.labels, { coga_alert = "uptime" })

  conditions {
    display_name = "The uptime check fails from more than one region"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.health[0].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
      trigger {
        count = 1
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  notification_channels = local.alert_channels

  documentation {
    content   = "https://${var.app_domain}/api/health has failed from more than one of Google's checker regions for 5 minutes: users cannot reach CoGA. Check the load balancer, its certificate, Cloud Armor and the backend service. See docs/deployment-gcp.md (Troubleshooting)."
    mime_type = "text/markdown"
  }
}

# Warnings, not errors: the apply goes ahead, and the plan says what would leave the alerting
# toothless.
check "alerts_reach_someone" {
  assert {
    condition     = length(var.alert_notification_emails) > 0
    error_message = "alert_notification_emails is empty: the alert policies open incidents in Cloud Monitoring, but nobody is notified."
  }
}

check "uptime_checkers_pass_the_allowlist" {
  assert {
    condition     = !(var.uptime_check_enabled && length(var.allowed_ingress_cidrs) > 0)
    error_message = "uptime_check_enabled with allowed_ingress_cidrs set: Cloud Armor denies Google's uptime checkers unless their ranges (gcloud monitoring uptime list-ips) are in the allowlist, and the check then fails. Allow them, or set uptime_check_enabled = false."
  }
}
