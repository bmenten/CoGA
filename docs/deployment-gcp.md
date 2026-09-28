# Deploying CoGA to Google Cloud (Terraform) — Full Guide

This is the **step-by-step, plain-language** guide to standing up CoGA on Google
Cloud with Terraform. It assumes you can use a terminal but does **not** assume you
are a GCP or Terraform expert — every concept is explained the first time it
appears.

> If you just want the terse reference (variables, resource list), see
> [terraform/README.md](../terraform/README.md). This document is the friendly,
> end-to-end walkthrough and operations manual.

CoGA is a clinical genomics platform handling patient data (PHI), regulated as an
in-house IVD under IVDR. **Treat every step here as production-grade**: secrets,
encryption, network isolation, audit logging, and backups are not optional.

---

## Table of contents

1. [What gets deployed (the big picture)](#1-what-gets-deployed-the-big-picture)
2. [Glossary — the GCP & Terraform words](#2-glossary)
3. [How a request flows through the system](#3-how-a-request-flows)
4. [What you need before you start](#4-prerequisites)
5. [One-time bootstrap (do this once per environment)](#5-one-time-bootstrap)
6. [Configure your variables](#6-configure-your-variables)
7. [First deployment (manual)](#7-first-deployment-manual)
8. [Point your domain at it + TLS](#8-dns--tls)
9. [Verify it works](#9-verify-it-works)
10. [Deploying through CI/CD (the normal path)](#10-cicd)
11. [Turning on the GCS storage backend](#11-gcs-storage-backend)
12. [Day-2 operations (runbooks)](#12-day-2-operations)
13. [Security & compliance mapping](#13-security--compliance)
14. [Rough cost overview](#14-cost-overview)
15. [Troubleshooting](#15-troubleshooting)
16. [Tearing it down](#16-teardown)
17. [FAQ](#17-faq)

---

## 1. What gets deployed (the big picture)

Terraform creates one self-contained CoGA environment in a GCP project:

```text
                              Internet (clinicians)
                                     │  HTTPS — coga.cmgg.be
                          ┌──────────▼───────────┐
                          │  External HTTPS LB    │   Google-managed TLS cert
                          │  + Cloud Armor (WAF)  │   /api/*  → backend
                          │  path routing         │   /*      → frontend
                          └────┬───────────┬──────┘
              serverless NEG   │           │  serverless NEG
                      ┌────────▼───┐   ┌───▼─────────┐
                      │  backend   │   │  frontend   │  Cloud Run (no public URL;
                      │ (FastAPI)  │   │ (React/SPA) │   only the LB can reach them)
                      └─────┬──────┘   └─────────────┘
            VPC connector   │ (private ranges only)
              ┌─────────────┼──────────────────┐
              │             │                   │
       ┌──────▼──────┐ ┌────▼──────────┐   Secret Manager (passwords, keys)
       │  Cloud SQL  │ │  ClickHouse   │   GCS buckets: phi + refdata (CMEK)
       │ PostgreSQL  │ │  on a VM      │   Cloud NAT (VM egress) + Private
       │ (private IP)│ │ (private IP,  │     Google Access
       │ via Connector│ │  HTTPS 8443) │   Cloud KMS (CMEK), Cloud Logging
       └─────────────┘ └───────────────┘
```

**The application** is two stateless containers:

| Piece | What it is | Runs on |
|-------|-----------|---------|
| **backend** | FastAPI API (variant queries, review, auth, imports). Serves everything under `/api`. | Cloud Run |
| **frontend** | React single-page app (the UI you see in the browser). | Cloud Run |

**The data** lives in three places:

| Store | What it holds | Service |
|-------|--------------|---------|
| **PostgreSQL** | Users, projects, families, samples, review state, audit logs. | Cloud SQL (managed) |
| **ClickHouse** | High-volume variant rows (SNV/SV, interval tracks). | Self-hosted on a Compute Engine VM (GCP has no managed ClickHouse) |
| **Object storage** | Raw family data (CRAM/BAM for IGV) + reference data. | Google Cloud Storage (GCS) buckets |

**The supporting infrastructure** (created for you): a private network (VPC), a
load balancer with a managed TLS certificate, a Web Application Firewall (Cloud
Armor), secret storage (Secret Manager), encryption keys (Cloud KMS / CMEK),
least-privilege identities (service accounts), and automated backups.

Everything lives in **`europe-west1` (Belgium)** by default, for EU data residency.

---

## 2. Glossary

You'll meet these terms throughout. Skim now, refer back later.

- **Terraform** — a tool that creates cloud resources from text files (`.tf`). You
  describe the *desired* state; Terraform makes reality match it. (OpenTofu is a
  drop-in open-source equivalent — `tofu` instead of `terraform`.)
- **Terraform state** — Terraform's record of what it created. Stored remotely in a
  **GCS bucket** so the whole team shares one source of truth. **It contains
  secrets**, so the bucket must be private + encrypted.
- **Provider** — the plugin that lets Terraform talk to a specific cloud (here,
  `hashicorp/google`).
- **`apply` / `plan`** — `plan` shows what *would* change; `apply` makes it happen.
- **Project** — a GCP container for resources and billing. CoGA may use one project,
  or several (a runtime project, a shared image-registry project, a KMS project).
- **Cloud Run** — runs a container without you managing servers; scales up/down
  automatically. We use it for the stateless backend & frontend.
- **Cloud SQL** — managed PostgreSQL (Google runs/patches/backs-up the database).
- **ClickHouse** — a columnar analytics database (for the huge variant tables). No
  managed GCP version exists, so we run it in a container on a small VM.
- **VPC** — a private network. Our databases have **no public IP**; they're only
  reachable inside this network.
- **Serverless VPC connector** — the bridge that lets Cloud Run reach into the
  private VPC (to talk to the databases).
- **Cloud NAT** — gives the (public-IP-less) ClickHouse VM a way to make *outbound*
  internet calls (e.g. to pull its container image).
- **Private Google Access** — lets the VM reach Google APIs (Secret Manager, etc.)
  without a public IP.
- **Load balancer (LB)** — the public front door. Terminates HTTPS, applies the WAF,
  and routes `/api/*` to the backend and everything else to the frontend.
- **Cloud Armor** — a Web Application Firewall + DDoS protection in front of the LB.
- **Secret Manager** — secure storage for passwords and keys, injected into the app
  at runtime (never baked into images or Terraform state where avoidable).
- **Cloud KMS / CMEK** — Customer-Managed Encryption Keys. "Encryption at rest" with
  *your* key (rather than Google's default key), for stronger control.
- **Artifact Registry** — where the built container images are stored.
- **Workload Identity Federation (WIF)** — lets GitHub Actions authenticate to GCP
  **without** a long-lived key file (keyless CI).
- **Service account (SA)** — a non-human identity. The backend runs *as* a service
  account with only the permissions it needs (least privilege).
- **PHI** — Protected Health Information (patient data). The reason for all the
  encryption/audit/isolation.

---

## 3. How a request flows

Understanding this makes everything else click:

1. A clinician opens `https://coga.cmgg.be`. DNS points the domain at the **load
   balancer's IP**.
2. The LB terminates TLS (using the Google-managed certificate) and runs the request
   through **Cloud Armor** (rate limiting, WAF).
3. The LB looks at the path:
   - **`/api/...`** → sent to the **backend** Cloud Run service.
   - **anything else** → sent to the **frontend** Cloud Run service (the SPA).
   - Because both are served from the *same domain*, the browser makes same-origin
     calls — **no CORS complexity**, and the backend never needs a public URL.
4. The backend talks to **PostgreSQL** (via the Cloud SQL Connector, encrypted) and
   **ClickHouse** (HTTPS on port 8443) over the **private VPC** — none of that
   traffic touches the public internet.
5. For genome viewing (IGV), once the GCS backend is on, the backend hands the
   browser a short-lived **signed URL** so it streams CRAM/BAM bytes directly from
   the bucket.

---

## 4. Prerequisites

### 4.1 Tools on your laptop

| Tool | Why | Install |
|------|-----|---------|
| `gcloud` | Talk to GCP from the CLI | <https://cloud.google.com/sdk/docs/install> |
| `terraform` ≥ 1.6 (or `tofu`) | Run the deployment | <https://developer.hashicorp.com/terraform/install> |
| `git` | Get the code | your package manager |
| `openssl` | Generate strong secrets | usually preinstalled |

Authenticate once: `gcloud auth login` and `gcloud auth application-default login`.

### 4.2 GCP access

You (or an admin) need:

- A **GCP project** for CoGA, with **billing enabled**.
- Enough IAM permissions to create the resources (Owner on the project for the
  initial bootstrap is simplest; tighten later).
- The ability to create the **landing-zone** pieces below (or have an admin provide
  them).

### 4.3 Landing-zone pieces (created once, possibly by an admin)

These exist *outside* the per-environment Terraform because they're shared or
sensitive. The bootstrap section (next) shows how to create them if they don't
exist:

- A **GCS bucket for Terraform state** (private, versioned).
- A **Cloud KMS key** for CMEK (same region as everything else).
- An **Artifact Registry** repository for images.
- **Workload Identity Federation** + the CI service accounts (only needed for the
  GitHub Actions path).
- Control over **DNS** for your domain (e.g. `coga.cmgg.be`).

---

## 5. One-time bootstrap

Do this **once per environment** (e.g. once for `dev`, once for `prod`). Replace the
`<...>` placeholders.

```bash
# Pick your project and region.
export PROJECT=<your-coga-project-id>
export REGION=europe-west1
gcloud config set project "$PROJECT"
```

### 5.1 Terraform state bucket

Terraform needs somewhere to store its state. Make a private, versioned bucket:

```bash
export STATE_BUCKET="${PROJECT}-tfstate"
gcloud storage buckets create "gs://${STATE_BUCKET}" \
  --location="$REGION" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning
```

### 5.2 KMS key (CMEK)

One key encrypts the database, disks, and buckets. It **must** be in the same region
as everything else.

```bash
gcloud kms keyrings create coga --location "$REGION" || true
gcloud kms keys create coga --location "$REGION" --keyring coga \
  --purpose encryption --rotation-period 90d \
  --next-rotation-time "$(date -u -d '+90 days' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v+90d +%Y-%m-%dT%H:%M:%SZ)"

# This string is your cmek_key_self_link variable:
echo "projects/${PROJECT}/locations/${REGION}/keyRings/coga/cryptoKeys/coga"
```

> CMEK is mandatory (organization policy) and always on — `cmek_key_self_link` is a
> required variable. The key is granted to the Cloud SQL / Compute / Storage service
> agents in the central infra repo before this config applies.

### 5.3 Artifact Registry (for images)

```bash
gcloud artifacts repositories create gen-ghreg-shared-gbl \
  --repository-format=docker --location="$REGION" || true
```

> `gen-ghreg-shared-gbl` is the repo name the CI workflow expects. If your org uses a
> different name, change `AR_REPO` in `.github/workflows/build.yml`.

### 5.4 Enable the APIs

Terraform enables most APIs itself, but enabling them up front avoids first-run
races:

```bash
gcloud services enable \
  compute.googleapis.com run.googleapis.com sqladmin.googleapis.com \
  servicenetworking.googleapis.com vpcaccess.googleapis.com \
  secretmanager.googleapis.com cloudkms.googleapis.com storage.googleapis.com \
  artifactregistry.googleapis.com iam.googleapis.com iamcredentials.googleapis.com \
  certificatemanager.googleapis.com logging.googleapis.com monitoring.googleapis.com
```

### 5.5 Create the secret values

Terraform creates the secret **containers**, but you provide the **values** so real
secrets never live in Terraform variables. First create just the containers, then
add values:

```bash
cd terraform
terraform init \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="prefix=coga/dev"

# Create ONLY the app secret containers first.
terraform apply -target='google_secret_manager_secret.app' \
  -var="project_id=${PROJECT}" \
  -var="cmek_key_self_link=projects/${PROJECT}/locations/${REGION}/keyRings/coga/cryptoKeys/coga" \
  -var="backend_image=placeholder" -var="frontend_image=placeholder"

# Now add a value to each. Use STRONG, DISTINCT values.
openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add coga-secret-key            --data-file=-
openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add coga-integrity-anchor-key  --data-file=-
printf '%s' 'CHOOSE-A-STRONG-ADMIN-PASSWORD'   | gcloud secrets versions add coga-admin-password   --data-file=-
openssl rand -base64 36 | tr -d '\n' | gcloud secrets versions add coga-postgres-password      --data-file=-
openssl rand -base64 36 | tr -d '\n' | gcloud secrets versions add coga-clickhouse-password    --data-file=-
```

Notes:

- `coga-secret-key` and `coga-integrity-anchor-key` **must be different** values.
- You do **not** create `coga-clickhouse-tls-*` — Terraform generates the ClickHouse
  TLS cert/key itself.
- The `coga-admin-password` is the first login password for user **`coga-admin`**.

### 5.6 (CI only) Workload Identity Federation

Only needed for the GitHub Actions path (Section 10). WIF lets GitHub authenticate to
GCP without storing a key. This is org-specific; the workflow expects service
accounts named like `coga-dev-<region>-gh-actions@<project>` and
`reg-dev-<region>-gh-actions@<registry-project>`. Set it up following
<https://github.com/google-github-actions/auth#setup> and grant those SAs the roles
they need (Cloud Build, Artifact Registry writer, and — for the deploy SA — the
roles to run `terraform apply`). If you only ever deploy manually, skip this.

---

## 6. Configure your variables

Copy the example and edit it:

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # terraform.tfvars is gitignored
```

The variables you'll most likely set:

| Variable | Meaning | Default |
|----------|---------|---------|
| `project_id` | Your GCP project | *(required)* |
| `region` / `zone` | Where to deploy | `europe-west1` / `europe-west1-b` |
| `app_domain` | Public domain for the app | `coga.cmgg.be` |
| `cmek_key_self_link` | Your KMS key (from 5.2) | *(required — CMEK is always on)* |
| `backend_image` / `frontend_image` | Container images to run | *(required)* — CI sets these |
| `allowed_ingress_cidrs` | Source ranges allowed at the edge (UGent/UZ + VPN); empty = open | `[]` |
| `*_service_account_email` | Override runtime SA emails (created in the central repo) | *(derived by default)* |
| `db_tier` | Cloud SQL machine size | `db-custom-1-3840` (1 vCPU / 3.75 GB) |
| `db_availability_type` | `ZONAL` (cheaper) or `REGIONAL` (HA) | `ZONAL` |
| `clickhouse_machine_type` | ClickHouse VM size | `e2-standard-4` (4 vCPU / 16 GB) |
| `clickhouse_data_disk_gb` | ClickHouse data disk | `200` |
| `storage_backend` | `local`, `s3`, or `gcs` | `local` (flip to `gcs` later — Section 11) |
| `enable_cloud_armor` | Edge WAF/DDoS | `true` |
| `cloud_armor_waf_enforce` | Block (vs log-only) WAF matches | `false` (log-only first) |
| `azure_ad_tenant_id` / `azure_ad_client_id` | Institutional login | empty |

For **production**, consider `db_availability_type = "REGIONAL"` and a larger
`db_tier` / `clickhouse_machine_type`.

---

## 7. First deployment (manual)

You need container images first. Either let CI build them (Section 10), or build them
manually from the repo root:

```bash
# From the repo root. Stamp the real version/SHA (frozen into signed reports).
export TAG="manual-$(git rev-parse --short=12 HEAD)"
export IMG_BASE="europe-west1-docker.pkg.dev/${PROJECT}/gen-ghreg-shared-gbl/coga"

gcloud builds submit --config=ci/cloudbuild.backend.yaml \
  --substitutions=_IMAGE=${IMG_BASE}-backend:${TAG},_APP_VERSION=$(cat VERSION),_GIT_SHA=$(git rev-parse --short=12 HEAD) .

gcloud builds submit --config=ci/cloudbuild.frontend.yaml \
  --substitutions=_IMAGE=${IMG_BASE}-frontend:${TAG} .
```

Then apply Terraform:

```bash
cd terraform
terraform init -backend-config="bucket=${STATE_BUCKET}" -backend-config="prefix=coga/dev"

terraform plan -out=tfplan \
  -var="project_id=${PROJECT}" \
  -var="cmek_key_self_link=projects/${PROJECT}/locations/${REGION}/keyRings/coga/cryptoKeys/coga" \
  -var="backend_image=${IMG_BASE}-backend:${TAG}" \
  -var="frontend_image=${IMG_BASE}-frontend:${TAG}"

terraform apply tfplan
```

Terraform will create ~40 resources (network, databases, secrets wiring, Cloud Run,
load balancer, Cloud Armor, …). The first apply takes several minutes (Cloud SQL
alone is ~10 min).

> **Tip:** if a step needs the secret values (it reads the Postgres password to set
> the DB user), make sure you completed Section 5.5 first.

---

## 8. DNS & TLS

The Google-managed certificate only finishes provisioning **after** your domain
resolves to the load balancer. So:

```bash
cd terraform
terraform output -raw load_balancer_ip      # e.g. 34.120.x.x
```

Create a DNS **A record**: `coga.cmgg.be → <that IP>`.

Then wait (typically 15–60 minutes) for the certificate. Check status:

```bash
gcloud compute ssl-certificates describe coga-cert --global \
  --format='value(managed.status, managed.domainStatus)'
```

It moves `PROVISIONING → ACTIVE`. Until it's `ACTIVE`, browsers will show a TLS
warning — that's expected during this window.

---

## 9. Verify it works

```bash
# Health endpoint (should print {"status":"ok"} or similar with HTTP 200):
curl -i https://coga.cmgg.be/api/health
```

Then open `https://coga.cmgg.be` in a browser and log in with:

- **Username:** `coga-admin`
- **Password:** the `coga-admin-password` value you set in 5.5

If the page loads and you can log in, the deployment is live. **Change/rotate the
admin password** and create real user accounts.

---

## 10. CI/CD (the normal path)

Day-to-day you don't run Terraform by hand — GitHub Actions does it. The workflow is
[.github/workflows/build.yml](../.github/workflows/build.yml):

- **On a pull request:** it only runs `terraform fmt -check` + `terraform validate`
  (no credentials, no changes). Safe to review.
- **On push to `main` (or a release):** it builds both images (stamping
  `APP_VERSION`/`GIT_SHA`), pushes them to Artifact Registry, then runs `terraform
  init/plan/apply` to deploy. Only **one** trigger applies (#520): pushes to `main` by
  default, or published releases when the repository variable `COGA_DEPLOY_TRIGGER` is
  `release`. There is one environment and one state, so a later `main` push must not
  overwrite a release deployment.

Set these **GitHub repository secrets** (Settings → Secrets and variables → Actions):

| Secret | What |
|--------|------|
| `GCP_COGA_PROJECT_ID` | The CoGA runtime project |
| `GCP_REGISTRY_PROJECT_ID` | Project hosting Artifact Registry |
| `GCP_CLOUDBUILD_STAGING_BUCKET` | Bucket for Cloud Build source/logs |
| `GCP_WIF_PROVIDER` | The Workload Identity provider resource name |
| `GCP_COGA_TF_STATE_BUCKET` | The state bucket from 5.1 |
| `GCP_COGA_CMEK_KEY_SELF_LINK` | The KMS key from 5.2 |

And these **variables**:

| Variable | Where | What |
|----------|-------|------|
| `GCP_REGION_SHORT` | repository | e.g. `euw1`, used in the SA names |
| `COGA_DEPLOY_TRIGGER` | repository | `main` (default) or `release`: which event deploys (#520) |
| `COGA_TFVARS` | `gcp-deploy` environment | Every other Terraform variable, as HCL (for example `storage_backend = "gcs"`, `app_domain = "..."`). CI writes it to `ci.auto.tfvars` before planning, so a value set once is not reverted by the next deploy; the five values CI passes with `-var` still win. No secrets here: they live in Secret Manager. |

Once configured, merging to `main` deploys automatically.

---

## 11. GCS storage backend

By default `storage_backend = "local"`, so the app does **not** yet read family
CRAM/BAM from a bucket. The code, bucket, and permissions are all in place — turning
it on is a two-step flip:

1. **Upload family data** to the PHI bucket, mirroring the layout
   `<family_id>/<file>`:

   ```bash
   BUCKET=$(terraform output -raw phi_bucket)
   gcloud storage cp FAM001.cram     "gs://${BUCKET}/FAM001/FAM001.cram"
   gcloud storage cp FAM001.cram.crai "gs://${BUCKET}/FAM001/FAM001.cram.crai"
   ```

2. **Flip the switch** and apply:

   ```bash
   terraform apply -var="storage_backend=gcs"   # plus your other -vars
   ```

The backend then serves IGV alignments as short-lived **signed URLs** (keyless, via
IAM `SignBlob`) and can stage family-package imports from `gs://` paths. Package Import
reads from `gs://<phi bucket>/imports` unless the `family_import_roots` variable names
other locations (`FAMILY_IMPORT_ROOTS`, #520); upload package folders under that prefix.

The **reference-data** bucket (`refdata`) is always mounted into the backend at
`/data/ref-data`, regardless of this setting, and **read-only** (#520): load the
reference files into the bucket out of band, e.g.
`gcloud storage cp dbNSFP5.4_gene.gz STRchive-loci.json "gs://$(terraform output -raw refdata_bucket)/"`.
The HPO bootstrap downloads its ontology to a temporary directory when that path is not
writable, so an empty `hpo/` folder is fine.

**Client addresses.** The load balancer appends `<client-ip>,<lb-ip>` to
`X-Forwarded-For`, and the backend takes the client `trusted_proxy_hops` (default 2)
entries from the right, never the client-settable left-most one (#520). After the first
deploy, check that the audit log's `remoteIp` for your own request is your public
address. The HTTPS load balancer enforces TLS 1.2+ with the `MODERN` profile.

---

## 12. Day-2 operations

### 12.1 Deploy a new version of the app

- **Normal:** merge to `main` → CI builds + applies.
- **Manual:** build a new image (Section 7), then
  `terraform apply -var="backend_image=...:newtag" -var="frontend_image=...:newtag"`.

Cloud Run rolls out a new revision with zero-downtime; if it fails its health check,
traffic stays on the old revision.

### 12.2 Rotate a secret

Add a new version, then roll the backend so it picks it up:

```bash
printf '%s' 'NEW-VALUE' | gcloud secrets versions add coga-secret-key --data-file=-
# Re-deploy the backend revision (re-reads "latest"):
gcloud run services update coga-backend --region "$REGION" --update-labels rotated=$(date +%s)
```

For the **Postgres password**, the database user's password is set from the secret by
Terraform, so: add the new version, then `terraform apply` (it updates the DB user and
the backend together).

### 12.3 Backups & restore

**PostgreSQL** has automated daily backups + point-in-time recovery (PITR), retained
per `db_backup_retained_count` (default 30).

```bash
# List backups:
gcloud sql backups list --instance coga-postgres
# Restore into a NEW instance (safest — never overwrite the live one blindly):
gcloud sql backups restore <BACKUP_ID> --restore-instance=coga-postgres-restored --backup-instance=coga-postgres
```

**ClickHouse** data disk is snapshotted daily (retained `clickhouse_snapshot_retention_days`,
default 14).

```bash
# List snapshots:
gcloud compute snapshots list --filter="name~coga-clickhouse"
# Recover: create a new disk from a snapshot, then attach it to a recovery VM.
gcloud compute disks create coga-clickhouse-data-restored \
  --source-snapshot=<SNAPSHOT_NAME> --zone="$REGION-b" --type=pd-ssd
```

**Upgrading ClickHouse** (a new `clickhouse_image`, e.g. 25.3 → 26.8 LTS in #524). The server
upgrades its data directory in place the first time the new version starts, and the previous
version is not guaranteed to read it afterwards, so switching the image back is not a rollback.
Take a snapshot of the data disk first (above); to roll back, restore that snapshot. The
25.3 → 26.8 step was verified on a data volume written by 25.3: every table attached with the
same row counts and content hashes, `CHECK TABLE` and the integrity sweep passed, and the e2e
suite ran against it.

> **Run a restore drill** before go-live (IVDR item P1-13): actually restore into a
> throwaway instance/VM and confirm the data is intact. A backup you've never
> restored is not a backup.

### 12.4 ClickHouse TLS cert rotation

Automatic: a daily `systemd` timer on the VM re-fetches the cert from Secret Manager
and restarts ClickHouse only if it changed — no action needed for a server-cert
re-issue. The VM has **no SSH ingress** (see §S-8), so there is normally nothing to do
by hand. If you must force a refresh, either wait for the daily timer, or add a
temporary break-glass IAP SSH rule (source `35.235.240.0/20`, tcp/22, target tag
`clickhouse`), run `sudo /etc/coga-clickhouse/refresh-certs.sh`, then remove the rule.
Rotating the **CA** (10-year) is rare and needs a backend redeploy to pick up the new
`CLICKHOUSE_CA_CERT`.

### 12.5 Scaling

- **Backend/frontend traffic:** raise `backend_max_instances` / `frontend_max_instances`.
- **Database:** raise `db_tier` (and `REGIONAL` for HA).
- **ClickHouse:** raise `clickhouse_machine_type` / `clickhouse_data_disk_gb`
  (the disk auto-grows on the FS only if you resize + grow; plan capacity).

### 12.6 Logs & monitoring

Everything logs to **Cloud Logging**. Useful filters:

```bash
# Backend app logs:
gcloud logging read 'resource.type="cloud_run_revision" resource.labels.service_name="coga-backend"' --limit 50
# Cloud Armor / WAF decisions (to review before enforcing):
gcloud logging read 'resource.type="http_load_balancer" jsonPayload.enforcedSecurityPolicy.name="coga-armor"' --limit 50
# Byte-level PHI object reads (GCS data-access audit, security item S-4):
gcloud logging read 'protoPayload.serviceName="storage.googleapis.com" protoPayload.methodName="storage.objects.get"' --limit 50
```

### 12.7 Cloud Armor: from log-only to enforce

The WAF ships in **preview (log-only)** so it can't false-positive-block the
genomics API on day one. After reviewing the WAF logs (12.6) and confirming no
legitimate requests are flagged:

```bash
terraform apply -var="cloud_armor_waf_enforce=true"   # plus your other -vars
```

Tune `cloud_armor_rate_limit_per_minute` to your real peak interactive load.

---

## 13. Security & compliance

This deployment closes the deployment-level security items tracked in
[TF-13 §3](regulatory/TF-13-cybersecurity.md) and
[security-posture.md](security-posture.md):

| Item | How it's handled here |
|------|----------------------|
| **S-1** encryption at rest | CMEK on Cloud SQL, both disks, and both GCS buckets |
| **S-2** TLS to datastores | Postgres via the Cloud SQL Connector (mTLS, verify-full grade); ClickHouse over HTTPS:8443 with a private CA the backend verifies |
| **S-3** secrets management | Secret Manager; values injected at runtime, not baked into images |
| **S-4** byte-level PHI audit | GCS Data Access audit logs (set in the central infra repo) |
| **S-8** network posture | Private IPs, no public DB ingress, no SSH to the ClickHouse VM, least-privilege service accounts, NAT/PGA, optional edge IP allowlist |
| **P1-13** backups | Cloud SQL PITR + retained backups; daily ClickHouse disk snapshots (**do a restore drill**) |
| edge protection | Cloud Armor: adaptive DDoS, per-IP rate limiting, OWASP CRS 4.22 WAF, optional UGent/UZ IP allowlist |

**Still your responsibility (process, not code):** IVDR **change control** (TF-18)
and an updated **DPIA** (TF-14) — deploying to Google Cloud adds Google as a
data sub-processor, which must be assessed and documented, and the IFU
(TF-15) minimum-IT-requirements updated.

---

## 14. Cost overview

Rough monthly drivers (EU pricing, order-of-magnitude — confirm with the GCP pricing
calculator):

- **Cloud SQL** — the biggest fixed cost; scales with `db_tier` and `REGIONAL` HA.
- **ClickHouse VM + SSD** — a constantly-running `e2-standard-4` + 200 GB SSD.
- **Cloud Run** — cheap; backend keeps `min_instance_count = 1` (background workers), the frontend scales to zero when idle + traffic.
- **Load balancer + Cloud Armor** — small fixed fee + per-request.
- **Storage + egress** — GCS for CRAM/BAM (can be large) + signed-URL download egress.
- **KMS / Secret Manager / logging** — negligible.

To reduce a **dev** environment's cost: `db_availability_type = "ZONAL"`, a smaller
`db_tier`, a smaller `clickhouse_machine_type`, and lower disk sizes.

---

## 15. Troubleshooting

| Symptom | Likely cause & fix |
|---------|--------------------|
| Managed cert stuck `PROVISIONING` | DNS A record not pointing at `load_balancer_ip` yet, or domain not resolving. Fix DNS; wait up to ~60 min. |
| Backend revision won't go healthy | A required secret has no `latest` version (Section 5.5), or the DB/ClickHouse isn't reachable. Check `gcloud run services logs read coga-backend`. |
| "Refusing to start … insecure default credentials" | `SECRET_KEY`/`ADMIN_PASSWORD` still placeholders. Add real secret versions and redeploy. |
| `terraform apply` fails reading the Postgres password | The `coga-postgres-password` secret version doesn't exist yet — complete the secret bootstrap (5.5) before the full apply. |
| ClickHouse VM has no data / won't start the container | No Cloud NAT egress to pull the image, or the data disk didn't mount. Check the VM serial console: `gcloud compute instances get-serial-port-output coga-clickhouse-vm --zone "$REGION-b"`. |
| Postgres connector errors | Backend SA missing `roles/cloudsql.client`, or the Cloud SQL Admin API disabled. Both are wired by Terraform — re-`apply`. |
| Legitimate requests blocked | If you enabled `cloud_armor_waf_enforce`, review the WAF logs (12.6) and tune; revert to `false` to log-only. |
| Frontend loads but API calls 404 | The LB path rule must route `/api/*` to the backend — re-`apply`; confirm the URL map exists. |

---

## 16. Teardown

**Destroying this environment deletes patient data.** Be certain, and export anything
you must keep first.

Two safety guards must be lifted manually:

1. **Cloud SQL** has `deletion_protection = true`. Set it to `false` in
   `terraform/database.tf` and `apply` before you can destroy it.
2. **GCS buckets** have `force_destroy = false`. Empty them (or set `force_destroy =
   true`) before destroy, or Terraform refuses to delete non-empty buckets.

Then:

```bash
cd terraform
terraform destroy -var="project_id=${PROJECT}" -var="cmek_key_self_link=..." \
  -var="backend_image=x" -var="frontend_image=x"
```

The KMS key, state bucket, and Artifact Registry (the landing zone) are **not**
managed by this Terraform and survive — delete them separately if you truly want
nothing left.

---

## 17. FAQ

**Why is ClickHouse on a plain VM instead of managed?**
GCP has no managed ClickHouse. The VM uses a dedicated, encrypted, snapshot-backed
disk, fetches its password/cert from Secret Manager at boot, serves HTTPS, and
gracefully shuts down to avoid corruption. For a guaranteed multi-minute flush
window under all conditions, a future option is running it on GKE as a StatefulSet.

**Can I run this in a different region?**
Yes — set `region`/`zone` (and put the KMS key in that region). Keep it in the EU for
data residency.

**Do I have to use CMEK / Cloud Armor / Azure login?**
CMEK is **mandatory** (org policy) and always on. Cloud Armor (`enable_cloud_armor`)
can be turned off but is recommended for PHI. Azure AD login is optional (leave the
`azure_ad_*` vars empty to disable).

**Where do the build version numbers come from?**
`APP_VERSION` (from the `VERSION` file) and `GIT_SHA` are baked into the image at
build time and frozen into every signed clinical report — which is why CI builds from
the repo root and passes them as build args. Don't bypass that.

**Is anything not yet automated?**
A couple of operational residuals are listed in
[terraform/README.md](../terraform/README.md) (e.g. the SQL-user password living in
Terraform state — keep the state bucket locked down). The IVDR change-control/DPIA
paperwork is a manual, required step.

---

*Quick reference (variables, resource list, residuals):*
[terraform/README.md](../terraform/README.md).
*Security posture:* [security-posture.md](security-posture.md) ·
[regulatory/TF-13-cybersecurity.md](regulatory/TF-13-cybersecurity.md).
