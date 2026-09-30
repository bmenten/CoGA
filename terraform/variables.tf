variable "project_id" {
  description = "GCP project ID that hosts the CoGA deployment."
  type        = string
}

variable "region" {
  description = "Primary region for Cloud Run, Cloud SQL, ClickHouse, and buckets. Keep in the EU for IVDR data residency."
  type        = string
  default     = "europe-west1"
}

variable "zone" {
  description = "Zone for the ClickHouse Compute Engine instance and its data disk."
  type        = string
  default     = "europe-west1-b"
}

variable "backend_image" {
  description = "Full backend container image URL, e.g. europe-west1-docker.pkg.dev/<project>/<repo>/coga-backend:<tag>."
  type        = string
}

variable "frontend_image" {
  description = "Full frontend container image URL, e.g. europe-west1-docker.pkg.dev/<project>/<repo>/coga-frontend:<tag>."
  type        = string
}

variable "app_domain" {
  description = "Public domain served by the external HTTPS load balancer. A managed TLS cert is provisioned for it; point an A record at the LB IP output."
  type        = string
  default     = "coga.cmgg.be"
}

# ---------------------------------------------------------------------------
# Encryption (CMEK) — mandatory (organization policy), always on
# ---------------------------------------------------------------------------

variable "cmek_key_self_link" {
  description = "Resource id of the KMS crypto key used for CMEK on Cloud SQL, Compute disks, and GCS buckets, in the form projects/<p>/locations/<region>/keyRings/<ring>/cryptoKeys/<key>. Must live in the same region as the resources it protects. Encryption is mandatory, so this is required. The key is created/managed and granted to the Google service agents in the central infra repo (terraform/main-repo-reference/)."
  type        = string

  validation {
    condition     = can(regex("^projects/[^/]+/locations/[^/]+/keyRings/[^/]+/cryptoKeys/[^/]+$", var.cmek_key_self_link))
    error_message = "cmek_key_self_link must be a fully-qualified crypto key id: projects/<p>/locations/<loc>/keyRings/<ring>/cryptoKeys/<key>."
  }
}

# ---------------------------------------------------------------------------
# Runtime service accounts (created + IAM-granted in the central infra repo)
# ---------------------------------------------------------------------------

variable "backend_service_account_email" {
  description = "Email of the backend Cloud Run runtime service account. Created and IAM-granted in the central infra repo. Empty = derive the default name coga-backend-run@<project>.iam.gserviceaccount.com."
  type        = string
  default     = ""
}

variable "frontend_service_account_email" {
  description = "Email of the frontend Cloud Run runtime service account. Created and IAM-granted in the central infra repo. Empty = derive the default name coga-frontend-run@<project>.iam.gserviceaccount.com."
  type        = string
  default     = ""
}

variable "clickhouse_vm_service_account_email" {
  description = "Email of the ClickHouse VM service account. Created and IAM-granted in the central infra repo. Empty = derive the default name coga-clickhouse-vm@<project>.iam.gserviceaccount.com."
  type        = string
  default     = ""
}

variable "db_migrate_service_account_email" {
  description = "Email of the service account the db-migrate Cloud Run job runs as (used only with db_runtime_role = \"coga_app\"). Its own account, so the API's account never holds the Postgres owner's password. Created and IAM-granted (roles/cloudsql.client) in the central infra repo. Empty = derive the default name coga-db-migrate@<project>.iam.gserviceaccount.com."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Database sizing
# ---------------------------------------------------------------------------

variable "db_tier" {
  description = "Cloud SQL machine tier. db-custom-1-3840 (1 vCPU / 3.75GB) is a small-but-real default; scale up for production load."
  type        = string
  default     = "db-custom-1-3840"
}

variable "db_availability_type" {
  description = "Cloud SQL availability: ZONAL (single zone, cheaper) or REGIONAL (HA failover). Use REGIONAL for production."
  type        = string
  default     = "ZONAL"

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.db_availability_type)
    error_message = "db_availability_type must be ZONAL or REGIONAL."
  }
}

variable "db_backup_retained_count" {
  description = "Number of automated Cloud SQL backups to retain."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# ClickHouse VM
# ---------------------------------------------------------------------------

variable "clickhouse_machine_type" {
  description = "Compute Engine machine type for the self-hosted ClickHouse VM. e2-standard-4 (4 vCPU / 16GB) is a sane analytical starting point."
  type        = string
  default     = "e2-standard-4"
}

variable "clickhouse_data_disk_gb" {
  description = "Size (GB) of the dedicated, snapshot-backed ClickHouse data disk."
  type        = number
  default     = 200
}

variable "clickhouse_image" {
  description = "ClickHouse server container image, digest-pinned to match the local stack (tag kept for readability). Bump the digest alongside the tag."
  type        = string
  default     = "clickhouse/clickhouse-server:26.8@sha256:4769eec6a9b9842a7d102c8bdfca0400128bc6edf65dec4e19a7d3a2b25f32db"
}

variable "clickhouse_restrict_egress" {
  description = "Limit the ClickHouse VM's outbound traffic to Google APIs (private.googleapis.com) and block everything else (egress.tf). Needs the ClickHouse image mirrored into Artifact Registry (clickhouse_image on a *-docker.pkg.dev host), the Cloud DNS API enabled, and the VM's service account allowed to read the registry. false = outbound through Cloud NAT, as today."
  type        = bool
  default     = false
}

variable "clickhouse_snapshot_retention_days" {
  description = "Days to retain daily ClickHouse data-disk snapshots."
  type        = number
  default     = 14
}

# ---------------------------------------------------------------------------
# Cloud Run sizing
# ---------------------------------------------------------------------------

variable "backend_cpu" {
  description = "CPU limit for the backend Cloud Run service."
  type        = string
  default     = "2"
}

variable "backend_memory" {
  description = "Memory limit for the backend Cloud Run service. Reference bootstrap (dbNSFP/HPO) and ClickHouse client buffers want headroom."
  type        = string
  default     = "2Gi"
}

variable "backend_max_instances" {
  description = "Max backend instances. Job workers use FOR UPDATE SKIP LOCKED, so multiple instances are safe."
  type        = number
  default     = 4
}

variable "frontend_max_instances" {
  description = "Max frontend instances."
  type        = number
  default     = 5
}

# ---------------------------------------------------------------------------
# Identity provider (Azure AD / Entra) — optional, wired into the backend env
# ---------------------------------------------------------------------------

variable "azure_ad_tenant_id" {
  description = "Azure AD / Entra tenant id for institutional login. Empty disables Azure auth env."
  type        = string
  default     = ""
}

variable "azure_ad_client_id" {
  description = "Azure AD / Entra application (client) id."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Cloud Armor (WAF / edge protection)
# ---------------------------------------------------------------------------

variable "enable_cloud_armor" {
  description = "Attach a Cloud Armor security policy (adaptive L7 DDoS, per-IP rate limiting, OWASP CRS WAF) to the load balancer's backend services."
  type        = bool
  default     = true
}

variable "cloud_armor_waf_enforce" {
  description = "Enforce the OWASP CRS WAF rules. false = preview/log-only (recommended until tuned against the genomics API's traffic, to avoid false-positive blocks); true = block matches with 403."
  type        = bool
  default     = false
}

variable "cloud_armor_rate_limit_per_minute" {
  description = "Per-client-IP request budget per minute before throttling (429). Tune to expected peak interactive use."
  type        = number
  default     = 600
}

variable "allowed_ingress_cidrs" {
  description = "Source IP ranges (CIDR) allowed to reach the app through the load balancer — e.g. UGent / UZ Gent public ranges and institutional VPN egress. When non-empty, Cloud Armor denies every other source at the edge (highest-priority rule), so the app is only reachable on-network. Empty = no IP restriction (open to the internet; app authentication still applies). Requires enable_cloud_armor = true."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.allowed_ingress_cidrs : can(cidrhost(cidr, 0))])
    error_message = "Every allowed_ingress_cidrs entry must be a CIDR range, such as 192.0.2.0/24."
  }
}

# ---------------------------------------------------------------------------
# Database privilege separation (#262)
# ---------------------------------------------------------------------------

variable "db_runtime_role" {
  description = "Postgres login the API runs as. \"owner\" = the table owner (coga_admin), which also applies the schema on startup: the single-login mode every deployment starts in. \"coga_app\" = the restricted runtime role, which cannot run DDL or change the append-only audit tables (TF-09b REQ-TRACE-008). The db-migrate Cloud Run job (migrate.tf) then applies the schema as the owner before each backend rollout and enables coga_app's login from the coga-postgres-app-password secret, and the API no longer receives the owner's password. Needs a version in that secret and the db-migrate service account: docs/db-runtime-role-runbook.md, \"Google Cloud\"."
  type        = string
  default     = "owner"

  validation {
    condition     = contains(["owner", "coga_app"], var.db_runtime_role)
    error_message = "db_runtime_role must be \"owner\" or \"coga_app\"."
  }
}

variable "run_db_schema_migrations_on_startup" {
  description = "Whether the API applies Postgres schema DDL + admin seed on startup, when it runs as the owner (db_runtime_role = \"owner\"). false = something else runs 'python -m app.db_migrate' as the owner before each rollout. Ignored with db_runtime_role = \"coga_app\": that role cannot run DDL, so the db-migrate job always does it (migrate.tf)."
  type        = bool
  default     = true
}

variable "forwarded_allow_ips" {
  description = "Peers uvicorn trusts X-Forwarded-For from (FORWARDED_ALLOW_IPS). Cloud Run terminates the connection at Google's managed front end, so the immediate peer is a Google-internal IP that varies and the container gets no direct external ingress, so '*' is the standard value there. With '*' uvicorn would take the left-most X-Forwarded-For entry, which a client sets itself; the backend therefore re-derives the client from the right using trusted_proxy_hops (#520), and uvicorn is only relied on for the scheme."
  type        = string
  default     = "*"
}

variable "trusted_proxy_hops" {
  description = "Proxies in front of the backend that each append to X-Forwarded-For (TRUSTED_PROXY_HOPS). The external Application Load Balancer appends '<client-ip>,<lb-ip>', so the client is 2 entries from the right. The audit log remoteIp and the signup/login throttles use it. After the first deploy, check that your own request's remoteIp is your public address; adjust if another proxy is added in front."
  type        = number
  default     = 2

  validation {
    condition     = var.trusted_proxy_hops >= 0 && var.trusted_proxy_hops <= 5
    error_message = "trusted_proxy_hops must be between 0 and 5."
  }
}

variable "family_import_roots" {
  description = "Locations Package Import may read family folders from (FAMILY_IMPORT_ROOTS). Empty = 'gs://<phi bucket>/imports' when storage_backend is gcs, otherwise the application default. Upload packages under that prefix (#520)."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Object storage backend
# ---------------------------------------------------------------------------

variable "storage_backend" {
  description = "Backend for raw family data: local, s3, or gcs. Flip to gcs once the PHI bucket is populated; the backend then serves IGV via IAM-signed URLs and stages imports from gs://."
  type        = string
  default     = "local"

  validation {
    condition     = contains(["local", "s3", "gcs"], var.storage_backend)
    error_message = "storage_backend must be local, s3, or gcs."
  }
}

# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

variable "labels" {
  description = "Labels applied to created resources."
  type        = map(string)
  default = {
    app        = "coga"
    managed-by = "terraform"
  }
}
