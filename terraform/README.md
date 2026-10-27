# CoGA — Terraform (Google Cloud)

Infrastructure as code for one CoGA environment on Google Cloud. This page is the reference
for reviewers: the design, the files, the boundary with the central infra repo, the go-live
switches and the known residuals. **To deploy or operate CoGA, follow the step-by-step
guide, [docs/deployment-gcp.md](../docs/deployment-gcp.md).** The security items this
configuration addresses are tracked in [TF-13 §3](../docs/regulatory/TF-13-cybersecurity.md).

## Design notes

- **Same origin.** The external HTTPS load balancer routes `/api/*` to the backend and
  everything else to the frontend, so there is no CORS hop. Both Cloud Run services accept
  traffic only from the load balancer (`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`), never on
  their `run.app` URLs.
- **Backend workers.** The backend keeps at least one instance with always-allocated CPU,
  so its in-process workers (package imports, gene reference sync, audit writers) keep
  running. Job claims use `FOR UPDATE SKIP LOCKED`, so more than one instance is safe.
- **ClickHouse on a VM.** Google Cloud has no managed ClickHouse, so it runs in a container
  on a Container-Optimized OS VM, with a dedicated CMEK data disk that is snapshotted daily.
  The VM reads its password and TLS material from Secret Manager at boot, never from
  instance metadata, and serves HTTPS on 8443 with a private CA (`tls.tf`) that the backend
  verifies. Terraform keeps the CA's key nowhere, so it issues the CA and the server
  certificate together; the backend gets the new CA in that apply, and the VM must then be
  restarted (a daily timer re-fetches the certificate otherwise). Its startup script also
  writes the query memory guards Docker Compose mounts, from the same file
  (`clickhouse/users.d/memory-guards.xml`): each query capped at 4 GB, GROUP BY and ORDER BY
  state spilled to disk past 2 GB.
- **Secrets.** This configuration creates the Secret Manager containers; the values are
  added by hand ([deployment-gcp.md §5.5](../docs/deployment-gcp.md)), so application
  secrets never pass through Terraform variables. Cloud Run reads them by reference.
- **No secret in the state.** Terraform reads the owner's password from Secret Manager
  through an ephemeral resource and sets it on the Cloud SQL user through a write-only
  argument (`secrets.tf`, `database.tf`); the ClickHouse CA and server keys are ephemeral,
  passed only to write-only arguments, and the server key reaches Secret Manager the same
  way (`tls.tf`). The state and the plan hold no password or private key: only the version
  numbers that tell Terraform when to send a new one. That needs Terraform 1.11 or later
  and the google provider 8.x (`versions.tf`).
- **Storage.** A PHI bucket and a reference-data bucket, both CMEK-encrypted, with uniform
  bucket-level access and public access prevention. The backend's account reads both
  (`roles/storage.objectViewer`) and writes neither; the reference-data bucket is mounted
  read-only at `/data/ref-data`. The PHI bucket's CORS policy admits the app's origin for
  reads only, so IGV can fetch reads through signed URLs.
- **Encryption.** One customer-managed key (`cmek_key_self_link`, required, no fallback)
  covers Cloud SQL, both disks and both buckets.

## Files

| File | Holds |
| --- | --- |
| `main.tf`, `versions.tf`, `variables.tf`, `outputs.tf` | provider, remote state (GCS), inputs with their defaults, outputs |
| `network.tf` | the VPC and subnet (with Private Google Access), private service access for Cloud SQL, the serverless VPC connector, Cloud NAT and the ClickHouse firewall rule |
| `database.tf` | Cloud SQL (private IP, encrypted connections only, backups and PITR) and the ClickHouse VM with its disk and snapshot policy |
| `cloudrun.tf` | the backend and frontend services and their settings |
| `migrate.tf` | the `coga-db-migrate` job for the restricted database role (below), and the on-demand Cloud SQL backup the apply takes before each schema migration it starts (`scripts/backup-postgres-before-migration.sh`) |
| `loadbalancer.tf`, `armor.tf` | the HTTPS load balancer (managed certificate, TLS 1.2+ `MODERN` policy) and Cloud Armor |
| `secrets.tf`, `tls.tf` | the Secret Manager containers and grants; the private CA and ClickHouse certificate |
| `storage.tf` | the two buckets and their grants |
| `egress.tf` | the ClickHouse egress lockdown (below) |
| `monitoring.tf` | the metrics token, the collector's configuration (`scripts/metrics-collector.yaml.tftpl`; the collector itself is a second container of the backend in `cloudrun.tf`), the alert policies and their email channels, and the uptime check ([docs/monitoring.md](../docs/monitoring.md)) |
| `scripts/` | the ClickHouse VM's startup, certificate-refresh and shutdown scripts, and the backup before a schema migration, which the apply runs with gcloud |

## Boundary with the central infra repo

This configuration enables no project APIs, creates no service accounts and grants no
project-level or KMS roles, so the CoGA deploy pipeline cannot grant itself privileges. The
central infra repo provides those, and the CoGA configuration refers to the accounts by
email (override them with the `*_service_account_email` variables). The template to lift,
and the checklist for a first rollout, are in [main-repo-reference/](main-repo-reference/).
Grants on the resources this configuration creates itself (its secrets and buckets) stay
here.

## Restricted database role (`db_runtime_role`)

Every deployment starts with `db_runtime_role = "owner"`: the API connects as the table
owner, `coga_admin`, and applies the schema on startup. Set `"coga_app"` to close the
owner-bypass gap of TF-09b REQ-TRACE-008. The API then connects as the restricted role,
which cannot run DDL or change the append-only audit tables, and its service account loses
access to the owner's password. The `coga-db-migrate` Cloud Run job ([`migrate.tf`](migrate.tf))
takes over the owner's work: Terraform runs it with every new backend image, after an
on-demand backup of the database, and the backend waits for it. It applies the schema, seeds
the admin user and enables `coga_app`'s login from the `coga-postgres-app-password` secret, so
no SQL is run by hand. The API, which then does not migrate, refuses to start unless the
database is at its own schema revision. The procedure, verification and rollback are in
[docs/db-runtime-role-runbook.md](../docs/db-runtime-role-runbook.md), "Google Cloud".

In either mode, every new backend image migrates the schema (the job, or in owner mode the
API's first start), and the apply first takes an on-demand Cloud SQL backup with gcloud, the
restore point of that migration; a backup that fails stops the apply before the migration
runs. The deploy pipeline's account needs the backup permissions
([main-repo-reference/](main-repo-reference/)).

## ClickHouse egress lockdown (`clickhouse_restrict_egress`)

Off by default: the ClickHouse VM reaches the internet through Cloud NAT, which it needs to
pull the Docker Hub image. With the lockdown on ([`egress.tf`](egress.tf)) the VM may only
open connections to Google APIs, over `private.googleapis.com` with private DNS zones for
`googleapis.com` and `pkg.dev`. Everything else is denied, so a compromised database VM
cannot send genotypes elsewhere. Before switching it on, mirror the ClickHouse image into
Artifact Registry and point `clickhouse_image` at it (the plan refuses a Docker Hub image),
grant the VM's account `roles/artifactregistry.reader` on that repository, and enable the
Cloud DNS API. Container-Optimized OS can then no longer update itself in place: patch it by
recreating the VM on a current image (the data disk is kept). The steps are in
[deployment-gcp.md §12.10](../docs/deployment-gcp.md).

The other go-live switches, `cloud_armor_waf_enforce` and `allowed_ingress_cidrs`, are in
deployment-gcp.md §12.7 and §12.9.

## Known residuals / deferred (follow-ups)

- **ClickHouse graceful shutdown.** Best-effort `docker stop -t 90` on VM shutdown +
  `MIGRATE` on maintenance. For a guaranteed 5-min flush window, move ClickHouse to a
  GKE StatefulSet with `terminationGracePeriodSeconds = 300`.
- **A ClickHouse certificate re-issue needs a VM restart.** The CA's key exists only during
  the apply that creates the CA, so every re-issue (key rotation, a new VM address,
  renewal) brings a new CA, which the backend trusts from that apply on. The app cannot
  reach ClickHouse until the VM restarts or its daily timer runs
  ([deployment-gcp.md §12.4](../docs/deployment-gcp.md)). Terraform stores the new key and
  certificate before it destroys the old ones, so a VM that boots during that apply still
  finds both, though not always a matching pair until that restart.
- **Older state versions.** An environment applied before OPS-10 still has the owner's
  password and the old TLS keys in its older state versions: rotate and tidy up once
  ([deployment-gcp.md §12.11](../docs/deployment-gcp.md)).
- **Bucket grants are bucket-wide.** The backend's read access covers each whole bucket, not
  a prefix.
- **No secret rotation automation.** Rotating a secret is a manual step
  (deployment-gcp.md §12.2).
