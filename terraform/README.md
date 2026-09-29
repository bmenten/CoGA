# CoGA — Terraform (Google Cloud)

Infrastructure-as-code for deploying CoGA to GCP. Derived from the first-draft
`add/tf-deploy-scripts` branch and reworked to boot cleanly and meet the
deployment-level security items tracked in
[TF-13 §3](../docs/regulatory/TF-13-cybersecurity.md) and
[security-posture.md §3–4](../docs/security-posture.md).

> **New here?** Start with the full step-by-step walkthrough and operations manual:
> **[docs/deployment-gcp.md](../docs/deployment-gcp.md)**. This file is the terse
> reference (architecture, variables, resource list, residuals).

## Architecture

```text
                         Internet
                            │  HTTPS (managed cert, app_domain)
                  ┌─────────▼──────────┐
                  │  External HTTPS LB │   /api/*  → backend
                  │  (path routing)    │   /*      → frontend
                  └────┬──────────┬────┘
        serverless NEG │          │ serverless NEG
              ┌────────▼───┐  ┌───▼─────────┐
              │ backend    │  │ frontend    │   Cloud Run (ingress: internal-LB)
              │ Cloud Run  │  │ Cloud Run   │
              └─────┬──────┘  └─────────────┘
        VPC connector│ (PRIVATE_RANGES_ONLY)
          ┌──────────┼───────────────┐
          │          │               │
   ┌──────▼─────┐ ┌──▼───────────┐  Secret Manager (secret_key_ref)
   │ Cloud SQL  │ │ ClickHouse   │  GCS: phi + refdata buckets (CMEK)
   │ Postgres16 │ │ COS VM + disk│  Cloud NAT (VM egress), PGA (Google APIs)
   │ private IP │ │ private IP   │
   └────────────┘ └──────────────┘
```

- **Same-origin by design**: the LB routes `/api/*` to the backend and everything
  else to the frontend, so there is no CORS hop and the backend is never reachable
  on its public `run.app` URL (`ingress = INTERNAL_LOAD_BALANCER`).
- **Backend workers**: `min_instance_count = 1` + always-allocated CPU keeps the
  in-process workers alive; job claims use `FOR UPDATE SKIP LOCKED`, so scaling past
  one instance is safe.
- **ClickHouse** has no managed GCP equivalent → a Container-Optimized OS VM with a
  dedicated, snapshot-backed CMEK data disk. Its password is read from Secret
  Manager at boot, never placed in instance metadata or state.

## Prerequisites (provisioned outside this config)

1. **Two/three projects** (or one): the CoGA runtime project, the shared Artifact
   Registry project, and a KMS project. Adjust to your landing zone.
2. **A KMS crypto key** (`cmek_key_self_link`) in the same region as the resources.
   Encryption is mandatory (org policy) — the key is always used; there is no toggle.
3. **A GCS state bucket** — private, versioned, CMEK, tight IAM. It holds the Cloud
   SQL user password in plaintext (see "Secrets" below).
4. **Workload Identity Federation** for GitHub Actions + the service accounts named
   in [`.github/workflows/build.yml`](../.github/workflows/build.yml)
   (`reg-*-gh-actions`, `reg-*-cb-runner`, `coga-*-gh-actions`).
5. **Artifact Registry repo** (the workflow uses `gen-ghreg-shared-gbl`).
6. **DNS** control for `app_domain`.
7. **Central infra repo prerequisites** — this config no longer enables project APIs,
   creates service accounts, or grants project-level / KMS IAM (so the CoGA pipeline
   holds no project-IAM-admin or SA-admin rights and cannot self-escalate). The central
   repo must provision the APIs, the three runtime SAs (`coga-backend-run`,
   `coga-frontend-run`, `coga-clickhouse-vm`), their project + KMS role grants, and the
   project-wide GCS Data Access audit config **before** this config applies. A
   ready-to-lift template lives in
   [`main-repo-reference/`](main-repo-reference/coga-prerequisites.tf.example). If the
   SA names differ, pass their emails via the `*_service_account_email` variables.
   Two go-live switches need more from it: the restricted database role needs a fourth
   account, `coga-db-migrate` (below), and the ClickHouse egress lockdown needs the Cloud
   DNS API plus Artifact Registry read access for the VM's account.

GitHub repo secrets used by CI: `GCP_REGISTRY_PROJECT_ID`,
`GCP_CLOUDBUILD_STAGING_BUCKET`, `GCP_WIF_PROVIDER`, `GCP_COGA_PROJECT_ID`,
`GCP_COGA_TF_STATE_BUCKET`, `GCP_COGA_CMEK_KEY_SELF_LINK`; repo var
`GCP_REGION_SHORT`.

## Secrets (bootstrap before first apply)

Terraform creates the secret **containers**; the **values** are added out-of-band so
app secrets never pass through Terraform variables. Because the Cloud SQL user and
the Cloud Run revisions resolve `latest` at apply time, do a one-time bootstrap:

```bash
PROJECT=my-coga-project
cd terraform

# 1. Create just the secret containers first.
tofu apply -target='google_secret_manager_secret.app' \
  -var="project_id=$PROJECT" -var="cmek_key_self_link=..." \
  -var="backend_image=placeholder" -var="frontend_image=placeholder"

# 2. Add a version to each (use strong, generated values).
printf '%s' "$SECRET_KEY"  | gcloud secrets versions add coga-secret-key           --data-file=- --project "$PROJECT"
printf '%s' "$ANCHOR_KEY"  | gcloud secrets versions add coga-integrity-anchor-key --data-file=- --project "$PROJECT"
printf '%s' "$ADMIN_PW"    | gcloud secrets versions add coga-admin-password       --data-file=- --project "$PROJECT"
printf '%s' "$PG_PW"       | gcloud secrets versions add coga-postgres-password    --data-file=- --project "$PROJECT"
printf '%s' "$CH_PW"       | gcloud secrets versions add coga-clickhouse-password  --data-file=- --project "$PROJECT"
```

- `SECRET_KEY` and `INTEGRITY_ANCHOR_SIGNING_KEY` must be **distinct**.
- `coga-postgres-app-password` is created too but needs a version only before switching to
  the restricted database role (below).
- The backend refuses to start in production with placeholder secrets, so these must
  be real before the first full apply ([config.py](../backend/app/core/config.py)).

## Deploy

CI ([`.github/workflows/build.yml`](../.github/workflows/build.yml)) does it end to
end on push to `main`: builds both images from the **repo-root context** (so
`APP_VERSION`/`GIT_SHA` are baked in and `scripts/` is included), then
`terraform init/plan/apply`. PRs run `terraform fmt -check` + `validate` only — no
apply. The deploy job refuses to run until the `gcp-deploy` environment has at least one
required reviewer (Settings → Environments), so an apply never runs unapproved.

Manual:

```bash
cd terraform
tofu init -backend-config="bucket=<state-bucket>" -backend-config="prefix=coga/dev"
tofu apply -var="project_id=..." -var="cmek_key_self_link=..." \
           -var="backend_image=..." -var="frontend_image=..."
```

After apply, point an A record for `app_domain` at the `load_balancer_ip` output;
the Google-managed cert provisions once DNS resolves (can take ~15–60 min).

## How this maps to the security items

| Item | Status in this config |
|---|---|
| S-1 encryption at rest | CMEK on Cloud SQL, both disks, and both buckets (mandatory, always on; key granted to the service agents in the central infra repo) |
| S-2 TLS to datastores | Postgres: Cloud SQL Python Connector (mTLS + verify-full-grade) over private IP, `ssl_mode=ENCRYPTED_ONLY`. ClickHouse: HTTPS on 8443 with a private CA; backend verifies (`CLICKHOUSE_CA_CERT` + `SERVER_HOST_NAME`) |
| S-3 secrets management | Secret Manager + `secret_key_ref`; VM reads its password at boot |
| S-4 byte-level PHI audit | GCS Data Access audit logs (project-wide; set in the central infra repo) |
| S-8 network posture | Private IPs, no public DB ingress, no SSH to the ClickHouse VM, least-privilege SAs, NAT/PGA; optional edge IP allowlist (`allowed_ingress_cidrs`) and ClickHouse egress lockdown (`clickhouse_restrict_egress`) |
| P1-3/P1-4 DB privilege separation | `db_runtime_role = "coga_app"`: the API runs as the restricted role and cannot read the owner's password; the `db-migrate` job applies the schema as the owner |
| P1-13 backups | Cloud SQL PITR + retained backups; daily ClickHouse disk snapshots |

## Object storage backend

The app supports a native **GCS** backend (`backend/app/core/object_storage.py`):
IGV alignments are served as IAM-signed (keyless `SignBlob`) URLs and family-package
imports stage from `gs://`. It is wired but **off by default** (`var.storage_backend
= "local"`); `GCS_BUCKET` is always pointed at the `phi` bucket, so activation is a
single change:

1. Upload family data to the `phi` bucket (`<family_id>/<file>` layout).
2. Set `storage_backend = "gcs"` and apply.

The backend SA has `objectViewer` on the `phi` bucket + `serviceAccountTokenCreator`
on itself, and the IAM Credentials API is enabled — all required for signed URLs.

## Edge protection (Cloud Armor)

A Cloud Armor policy is attached to both LB backend services (`enable_cloud_armor`):
adaptive L7 DDoS defense, per-IP rate limiting (`cloud_armor_rate_limit_per_minute`,
enforced), and the OWASP **CRS 4.22** WAF (SQLi/XSS/RCE/LFI). The WAF ships in
**preview (log-only)** by default — review the Cloud Armor logs against real traffic,
then set `cloud_armor_waf_enforce = true` to block. This avoids false-positive blocks
on the genomics API's unusual query payloads.

Set **`allowed_ingress_cidrs`** to the UGent / UZ Gent public ranges (plus VPN egress)
to restrict access at the edge: any source outside the list is denied before rate
limiting / WAF / app auth. Left empty (default) the app is reachable from anywhere and
relies on application authentication only.

## Restricted database role (`db_runtime_role`)

Every deployment starts with `db_runtime_role = "owner"`: the API connects as the table
owner, `coga_admin`, and applies the schema on startup. Set `"coga_app"` to close the
owner-bypass gap of TF-09b REQ-TRACE-008. The API then connects as the restricted role,
which cannot run DDL or change the append-only audit tables, and its service account loses
access to the owner's password. The `coga-db-migrate` Cloud Run job ([`migrate.tf`](migrate.tf))
takes over the owner's work: Terraform runs it with every new backend image, and the backend
waits for it. It applies the schema, seeds the admin user and enables `coga_app`'s login from
the `coga-postgres-app-password` secret, so no SQL is run by hand. The procedure, verification
and rollback are in [docs/db-runtime-role-runbook.md](../docs/db-runtime-role-runbook.md),
"Google Cloud".

## ClickHouse egress lockdown (`clickhouse_restrict_egress`)

Off by default: the ClickHouse VM reaches the internet through Cloud NAT, which it needs to
pull the Docker Hub image. With the lockdown on ([`egress.tf`](egress.tf)) the VM may only
open connections to Google APIs, over `private.googleapis.com` with private DNS zones for
`googleapis.com` and `pkg.dev`. Everything else is denied, so a compromised database VM
cannot send genotypes elsewhere. Before switching it on, mirror the ClickHouse image into
Artifact Registry and point `clickhouse_image` at it (the plan refuses a Docker Hub image),
grant the VM's account `roles/artifactregistry.reader` on that repository, and enable the
Cloud DNS API. Container-Optimized OS can then no longer update itself in place: patch it by
recreating the VM on a current image (the data disk is kept).

## ClickHouse cert rotation

The server cert/key are re-fetched from Secret Manager by a daily systemd timer on
the VM, which restarts ClickHouse only when they change — so a server-cert re-issue
needs no manual VM rebuild. The CA is the verification anchor wired into the backend
env, so it is long-lived (10y); rotating the **CA** still requires a backend redeploy
to pick up the new `CLICKHOUSE_CA_CERT`.

## Known residuals / deferred (follow-ups)

- **ClickHouse graceful shutdown.** Best-effort `docker stop -t 90` on VM shutdown +
  `MIGRATE` on maintenance. For a guaranteed 5-min flush window, move ClickHouse to a
  GKE StatefulSet with `terminationGracePeriodSeconds = 300`.
- **State holds the SQL user password and the ClickHouse TLS keys.** The Cloud SQL owner
  password is read from Secret Manager into state, and the private CA and server keys are
  generated in it (`tls.tf`). App secrets and `coga_app`'s password are *not* in state
  (referenced by `secret_key_ref`). Keep the state bucket private, versioned and CMEK, with
  tight IAM. Keeping these values out of state needs Terraform 1.11+ write-only arguments,
  which means moving off the pinned 5.x google provider: a separate, change-controlled
  upgrade. Until then this is an accepted residual risk for the owner to confirm (#364).
- **IVDR change control.** Google Cloud is the chosen host; the DPIA is signed and the data
  processing agreement with Google is being signed (owner, 2026-09-29). The decision is
  recorded in TF-02 §10 and TF-14 (CR-089). The TF-13/TF-15 IFU updates follow at go-live.
