# Deploying CoGA to Google Cloud (Terraform) — Full Guide

This is the **step-by-step, plain-language** guide to standing up CoGA on Google
Cloud with Terraform. It assumes you can use a terminal but does **not** assume you
are a GCP or Terraform expert — every concept is explained the first time it
appears.

> For the design notes, the map of the `.tf` files and the known residuals, see
> [terraform/README.md](../terraform/README.md). This document is the end-to-end
> walkthrough and operations manual.

CoGA is built to handle patient data (PHI) and is regulated as an in-house IVD under
IVDR. **Treat every step here as production-grade**: secrets, encryption, network
isolation, audit logging, and backups are not optional. There is no production
deployment yet.

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
10. [Deploying through CI/CD (the normal path)](#10-cicd-the-normal-path)
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
| **Object storage** | Raw family data (CRAM/BAM for IGV, import packages) + reference data. | Google Cloud Storage (GCS) buckets |

**The supporting infrastructure** (created for you): a private network (VPC), a
load balancer with a managed TLS certificate, a Web Application Firewall (Cloud
Armor), secret storage (Secret Manager), least-privilege access to the buckets and
secrets, and automated backups. The encryption key, the service accounts and the
project APIs come from outside this configuration (4.3).

Everything lives in **`europe-west1` (Belgium)** by default, for EU data residency.

---

## 2. Glossary

You'll meet these terms throughout. Skim now, refer back later.

- **Terraform** — a tool that creates cloud resources from text files (`.tf`). You
  describe the *desired* state; Terraform makes reality match it. (OpenTofu is a
  drop-in open-source equivalent — `tofu` instead of `terraform`.)
- **Terraform state** — Terraform's record of what it created. Stored remotely in a
  **GCS bucket** so the whole team shares one source of truth. It holds no password or
  private key (Terraform passes those through **write-only** arguments, which it never
  stores), but it maps the whole deployment, so the bucket must be private + encrypted.
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
  at runtime (never baked into images).
- **Cloud KMS / CMEK** — Customer-Managed Encryption Keys. "Encryption at rest" with
  *your* key (rather than Google's default key), for stronger control.
- **Artifact Registry** — where the built container images are stored.
- **Workload Identity Federation (WIF)** — lets GitHub Actions authenticate to GCP
  **without** a long-lived key file (keyless CI).
- **Service account (SA)** — a non-human identity. The backend runs *as* a service
  account with only the permissions it needs (least privilege).

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
   browser a short-lived **signed URL** so it streams CRAM/BAM bytes and the CNV
   caller's signal files directly from the bucket.

---

## 4. Prerequisites

### 4.1 Tools on your laptop

| Tool | Why | Install |
|------|-----|---------|
| `gcloud` | Talk to GCP from the CLI; `terraform apply` runs it too, for the backup before each schema migration (12.1) and the migration job (12.8) | <https://cloud.google.com/sdk/docs/install> |
| `terraform` (or `tofu`), at the version `terraform/versions.tf` requires: 1.11 or later | Run the deployment | <https://developer.hashicorp.com/terraform/install> |
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
sensitive. At CMGG the landing zone and the central infra repo provide them; the
bootstrap section (next) shows how to create them in a standalone project:

- A **GCS bucket for Terraform state** (private, versioned, CMEK).
- A **Cloud KMS key** for CMEK (same region as everything else).
- An **Artifact Registry** repository for images.
- **Workload Identity Federation** + the CI service accounts (only needed for the
  GitHub Actions path).
- Control over **DNS** for your domain (e.g. `coga.cmgg.be`).
- The **project APIs, the runtime service accounts and their roles**, the key grants to
  Google's service agents and the bucket data-access audit log, from the central infra
  repo ([terraform/main-repo-reference/](../terraform/main-repo-reference/coga-prerequisites.tf.example)),
  with the role that lets the deploy account take the backup before each schema migration
  (12.1). The go-live switches in 12.8 and 12.10 need two more things from it: the
  `coga-db-migrate` account, and the Cloud DNS API with Artifact Registry read access for
  the ClickHouse VM.

---

## 5. One-time bootstrap

Do this **once per environment** (e.g. once for `dev`, once for `prod`). Replace the
`<...>` placeholders. At CMGG, 5.1–5.4 and 5.6 are provided by the landing zone and the
central infra repo; run them yourself only in a standalone project. 5.5 is always yours.

```bash
# Pick your project and region.
export PROJECT=<your-coga-project-id>
export REGION=europe-west1
gcloud config set project "$PROJECT"
```

### 5.1 Terraform state bucket

Terraform needs somewhere to store its state. Make a private, versioned bucket (5.2 then
sets its encryption key, before any state is written):

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
export CMEK_KEY="projects/${PROJECT}/locations/${REGION}/keyRings/coga/cryptoKeys/coga"

# Let Cloud Storage use the key, and encrypt the state bucket with it.
gcloud storage service-agent --project="$PROJECT" --authorize-cmek="$CMEK_KEY"
gcloud storage buckets update "gs://${STATE_BUCKET}" --default-encryption-key="$CMEK_KEY"
```

> CMEK is mandatory (organization policy) and always on — `cmek_key_self_link` is a
> required variable. The central infra template grants the key to the Cloud SQL,
> Compute and Storage service agents (5.4).

### 5.3 Artifact Registry (for images)

```bash
gcloud artifacts repositories create gen-ghreg-shared-gbl \
  --repository-format=docker --location="$REGION" || true
```

> `gen-ghreg-shared-gbl` is the repo name the CI workflow expects. If your org uses a
> different name, change `AR_REPO` in `.github/workflows/build.yml`.

### 5.4 Enable the APIs

CoGA's Terraform enables no APIs and creates no service accounts: that needs rights the
CoGA pipeline deliberately lacks. The central infra template,
[coga-prerequisites.tf.example](../terraform/main-repo-reference/coga-prerequisites.tf.example),
enables the APIs, creates the runtime service accounts, grants their roles and the key
grants above, and turns on the bucket data-access audit log. In a standalone project,
copy it into a Terraform directory of its own, drop the `.example` suffix and apply it as
a project owner, with `project_id` and `cmek_key_self_link`, before CoGA's first apply.

### 5.5 Create the secret values

Terraform creates the secret **containers**, but you provide the **values** so real
secrets never live in Terraform variables. First create just the containers, then
add values:

```bash
cd terraform
terraform init \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="prefix=coga/dev"

# Create ONLY the secret containers first.
terraform apply -target='google_secret_manager_secret.app' -target='google_secret_manager_secret.metrics_token' \
  -var="project_id=${PROJECT}" \
  -var="cmek_key_self_link=${CMEK_KEY}" \
  -var="backend_image=placeholder" -var="frontend_image=placeholder"

# Now add a value to each. Use STRONG, DISTINCT values.
openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add coga-secret-key            --data-file=-
openssl rand -base64 32 | tr -d '\n' | gcloud secrets versions add coga-integrity-anchor-key  --data-file=-
printf '%s' 'CHOOSE-A-STRONG-ADMIN-PASSWORD'   | gcloud secrets versions add coga-admin-password   --data-file=-
openssl rand -base64 36 | tr -d '\n' | gcloud secrets versions add coga-postgres-password      --data-file=-
openssl rand -base64 36 | tr -d '\n' | gcloud secrets versions add coga-clickhouse-password    --data-file=-
# Only before switching to the restricted database role (12.8); printable ASCII.
openssl rand -base64 36 | tr -d '\n' | gcloud secrets versions add coga-postgres-app-password  --data-file=-
# The token the metrics collector sends to the backend's /metrics (12.6); needed unless
# metrics_collection_enabled = false.
openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add coga-metrics-token          --data-file=-
```

Notes:

- `coga-secret-key` and `coga-integrity-anchor-key` **must be different** values; the backend
  refuses to start when they are the same. So must `coga-metrics-token`, which also needs at
  least 32 characters.
- `coga-integrity-anchor-key` must be the base64 of exactly 32 random bytes (an Ed25519
  seed), which is what `openssl rand -base64 32` prints. Any other length and the backend
  refuses to start.
- You do **not** create `coga-clickhouse-tls-*` — Terraform generates the ClickHouse
  TLS cert/key itself, from keys it keeps in neither its state nor its plan (12.4).
- Terraform reads `coga-postgres-password` during each plan and apply to set the database
  owner's password, and keeps only the number of the version it read, not the value.
- The `coga-admin-password` is the first login password of the admin user **`coga-admin`**,
  who signs in with the email `admin@<app_domain>` (Section 9). Once Microsoft sign-in is on,
  `coga-admin` is the emergency administrator, and this secret stays its password (12.12).

### 5.6 (CI only) Workload Identity Federation

Only needed for the GitHub Actions path (Section 10). WIF lets GitHub authenticate to
GCP without storing a key. This is org-specific; the workflow expects these service
accounts, where `<short>` is the `GCP_REGION_SHORT` repository variable:

- `reg-dev-<short>-gh-actions@<registry-project>` (pushes images),
- `reg-dev-<short>-cb-runner@<registry-project>` (runs Cloud Build),
- `coga-dev-<short>-gh-actions@<coga-project>` (runs `terraform apply`).

Set it up following <https://github.com/google-github-actions/auth#setup> and grant
those accounts the roles they need (Cloud Build, Artifact Registry writer, and — for the
deploy account — the roles to run `terraform apply`, act as the runtime service accounts
and run Cloud Run jobs; see
[main-repo-reference/rollout-checklist.md](../terraform/main-repo-reference/rollout-checklist.md)).
If you only ever deploy manually, skip this.

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
| `backend_image` / `frontend_image` | Container images to run, by digest (`…@sha256:…`) | *(required)* — CI sets these |
| `allowed_ingress_cidrs` | Source ranges allowed at the edge (UGent/UZ + VPN); empty = open | `[]` |
| `*_service_account_email` | Override runtime SA emails (created in the central repo) | *(derived by default)* |
| `db_tier` | Cloud SQL machine size | `db-custom-1-3840` (1 vCPU / 3.75 GB) |
| `db_availability_type` | `ZONAL` (cheaper) or `REGIONAL` (HA) | `ZONAL` |
| `clickhouse_machine_type` | ClickHouse VM size | `e2-standard-4` (4 vCPU / 16 GB) |
| `clickhouse_data_disk_gb` | ClickHouse data disk | `200` |
| `storage_backend` | `local` or `gcs` | `local` (flip to `gcs` later — Section 11) |
| `enable_cloud_armor` | Edge WAF/DDoS | `true` |
| `cloud_armor_waf_enforce` | Block (vs log-only) WAF matches | `false` (log-only first) |
| `azure_ad_tenant_id` / `azure_ad_client_id` | Microsoft sign-in (12.12): the directory (tenant) ID and the application (client) ID IT gives you; both or neither | empty (password sign-in) |
| `azure_ad_api_scope` / `azure_admin_override` | CoGA's API scope, and the emergency administrator's password sign-in beside Microsoft sign-in (12.12) | empty (`api://<client id>/access_as_user`) / `false` |
| `alert_notification_emails` | Who the alert policies email (12.6) | `[]` — set it, or nobody is told (Terraform warns) |
| `metrics_collection_enabled` | Run the metrics collector beside the backend, and the metric alerts | `true` |
| `uptime_check_enabled` | Check `https://<app_domain>/api/health` every minute and alert when it fails | `true` (see 12.6 with `allowed_ingress_cidrs`) |

The go-live switches (`cloud_armor_waf_enforce`, `db_runtime_role`,
`allowed_ingress_cidrs`, `clickhouse_restrict_egress`) are described in 12.7–12.10, and
Microsoft sign-in (`azure_ad_*`) in 12.12.
Every variable, with its default, is in `terraform/variables.tf`.

For **production**, consider `db_availability_type = "REGIONAL"` and a larger
`db_tier` / `clickhouse_machine_type`.

---

## 7. First deployment (manual)

You need container images first. Either let CI build them (Section 10), or build them
manually from the repo root:

```bash
# From the repo root. Stamp the version and commit (frozen into signed reports). A manual
# build is not a release, so it reports VERSION as a development build (e.g. 0.1.0-dev).
export TAG="manual-$(git rev-parse --short=12 HEAD)"
export IMG_BASE="europe-west1-docker.pkg.dev/${PROJECT}/gen-ghreg-shared-gbl/coga"
export PROBLEM_REPORT_URL="<the CMGGMC probleemmelding route>"

gcloud builds submit --config=ci/cloudbuild.backend.yaml \
  --substitutions=_IMAGE=${IMG_BASE}-backend:${TAG},_APP_VERSION=$(./scripts/build-version.sh),_GIT_SHA=$(git rev-parse --short=12 HEAD) .

gcloud builds submit --config=ci/cloudbuild.frontend.yaml \
  --substitutions=_IMAGE=${IMG_BASE}-frontend:${TAG},_PROBLEM_REPORT_URL=${PROBLEM_REPORT_URL} .

# Each image as Terraform gets it: by the digest of what you just pushed.
export BACKEND_IMAGE="${IMG_BASE}-backend@$(gcloud artifacts docker images describe \
  "${IMG_BASE}-backend:${TAG}" --format='value(image_summary.digest)')"
export FRONTEND_IMAGE="${IMG_BASE}-frontend@$(gcloud artifacts docker images describe \
  "${IMG_BASE}-frontend:${TAG}" --format='value(image_summary.digest)')"
echo "$BACKEND_IMAGE" "$FRONTEND_IMAGE"   # each ends in @sha256: and 64 hex digits
```

Give every build a new tag, as above, and deploy it by its digest (`…@sha256:…`), as CI
does: the digest names exactly the image you built, whatever the tag is later pushed over,
and a new build always changes the image string, which is what Terraform deploys on.

`./scripts/build-version.sh` prints `VERSION` with the pre-release identifier `dev` added
(`0.1.0` → `0.1.0-dev`, `0.1.0-rc.1` → `0.1.0-rc.1.dev`): only a release build, which
`build.yml` makes from a published release, reports a release's version
([RELEASING.md](../RELEASING.md)).

`_PROBLEM_REPORT_URL` is where **Report a problem** in the app goes: the CMGGMC
probleemmelding route, as an `https://` or `mailto:` address without commas. It is built
into the frontend image; without it the app shows no problem-report link
([TF-15 §7](regulatory/TF-15-instructions-for-use.md)).

Then apply Terraform:

```bash
cd terraform
terraform init -backend-config="bucket=${STATE_BUCKET}" -backend-config="prefix=coga/dev"

terraform plan -out=tfplan \
  -var="project_id=${PROJECT}" \
  -var="cmek_key_self_link=${CMEK_KEY}" \
  -var="backend_image=${BACKEND_IMAGE}" \
  -var="frontend_image=${FRONTEND_IMAGE}"

terraform apply tfplan
```

Terraform creates the network, databases, secrets wiring, Cloud Run, the load
balancer and Cloud Armor. The first apply takes a while; Cloud SQL takes the longest.
Before the backend starts, the apply takes an on-demand backup of the database with your
`gcloud` login (12.1), as it does before every schema migration.

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
curl -i https://coga.cmgg.be/api/health         # 200 {"status":"ok"} once the app runs
curl -i https://coga.cmgg.be/api/health/ready   # 200 only when Postgres and ClickHouse answer
curl -s https://coga.cmgg.be/api/version        # the version and git commit that are running
curl -s -o /dev/null -D - https://coga.cmgg.be/ | grep -i strict-transport-security
curl -s -o /dev/null -D - https://coga.cmgg.be/api/health | grep -i strict-transport-security
```

The last two print `strict-transport-security: max-age=63072000; includeSubDomains`: the
app page and the API both tell the browser to use only HTTPS for this host, for two years
(`ENABLE_HSTS=true` on both Cloud Run services).

Then open `https://coga.cmgg.be` in a browser and log in with:

- **Email:** `admin@<app_domain>` (`admin@coga.cmgg.be` by default; Terraform sets it as
  `ADMIN_EMAIL`, and CoGA signs in by email, not by the username `coga-admin`)
- **Password:** the `coga-admin-password` value you set in 5.5

If the page loads and you can log in, the deployment is live. **Rotate the admin
password** (12.2) and **set up Microsoft sign-in** (12.12): users then sign in with their
hospital or university Microsoft account, and `coga-admin` becomes the emergency
administrator.

The footer of every page names the same version and commit as `/api/version`, and its
**Report a problem** link opens the CMGGMC route.

**Client addresses.** The load balancer appends `<client-ip>,<lb-ip>` to
`X-Forwarded-For`, and the backend takes the client `trusted_proxy_hops` (default 2)
entries from the right, never the client-settable left-most one. After the first
deploy, check that the audit log's `remoteIp` for your own request is your public
address.

---

## 10. CI/CD (the normal path)

Day-to-day you don't run Terraform by hand — GitHub Actions does it. The workflow is
[.github/workflows/build.yml](../.github/workflows/build.yml):

- **On a pull request:** it only runs `terraform fmt -check` + `terraform validate`
  (no credentials, no changes). Safe to review. This check and the deploy both run the
  Terraform version the workflow pins (`terraform_version`), at least the 1.11 that
  `terraform/versions.tf` requires.
- **On push to `main` (or a published release):** it builds both images (stamping
  `APP_VERSION`/`GIT_SHA`), pushes them to Artifact Registry, then runs `terraform
  init/plan/apply` to deploy. A release build stamps its release's version; a `main` build
  stamps `VERSION` marked as a development build (`0.1.0-dev`, or `0.1.0-rc.1.dev` after the
  release candidate), so `/api/version` and the reports never give it a release's version. A
  `main` build is tagged `main-<12-character commit>` and a release build with its release
  tag, and Terraform gets each image by the digest its build pushed (`…@sha256:…`), so the
  revision Cloud Run runs is exactly the image that run built. Every build is a new digest,
  so every deploy carries a new image: Cloud Run rolls out new revisions, and with the
  restricted database role (12.8) the migration job runs again. Before the schema migration
  that a new image brings, the apply takes an on-demand Cloud SQL backup, and a backup that
  fails stops the deploy (12.1). Only **one** trigger
  applies: pushes to `main` by default, or published releases when the repository variable
  `COGA_DEPLOY_TRIGGER` is `release`. There is one environment and one state, so a later
  `main` push must not overwrite a release deployment.
- **On a published release** it also refuses a tag that is not on `main` before it builds
  anything, and attaches the release's SBOMs to the release ([RELEASING.md](../RELEASING.md)).

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
| `COGA_DEPLOY_TRIGGER` | repository | `main` (default) or `release`: which event deploys |
| `COGA_PROBLEM_REPORT_URL` | repository | Where **Report a problem** in the app goes: the CMGGMC probleemmelding route, as an `https://` or `mailto:` address without commas. CI builds it into the frontend image; without it the app shows no problem-report link. |
| `COGA_TFVARS` | `gcp-deploy` environment | Every other Terraform variable, as HCL (for example `storage_backend = "gcs"`, `app_domain = "..."`). CI writes it to `ci.auto.tfvars` before planning, so a value set once is not reverted by the next deploy; the five values CI passes with `-var` still win. No secrets here: they live in Secret Manager. |

Once configured, merging to `main` deploys automatically, after approval: the deploy job
runs in the `gcp-deploy` environment and **refuses to run until that environment has at
least one required reviewer** (Settings → Environments → `gcp-deploy` → Required
reviewers). An infrastructure apply therefore never runs without a person approving it.

---

## 11. GCS storage backend

By default `storage_backend = "local"`, so the app does **not** yet read family data
from a bucket. The code, bucket, and permissions are all in place — turning it on is a
two-step flip:

1. **Upload the family packages** to the PHI bucket, one folder per family with its
   manifest, CRAM/BAM files included ([data-import.md](data-import.md), section 3):

   ```bash
   BUCKET=$(terraform output -raw phi_bucket)
   gcloud storage cp -r FAM001 "gs://${BUCKET}/imports/"   # -> imports/FAM001/...
   ```

2. **Flip the switch** and apply:

   ```bash
   terraform apply -var="storage_backend=gcs"   # plus your other -vars
   ```

Package Import then reads from `gs://<phi bucket>/imports` unless the
`family_import_roots` variable names other locations (`FAMILY_IMPORT_ROOTS`). It stages a
package to the backend's temporary folder, except the aligned reads, which stay in the
bucket, and records where each alignment and CNV signal file lies. IGV reads those files
through short-lived **signed URLs** (keyless, via IAM `SignBlob`). The backend signs a
recorded location only when it is in the PHI bucket under `FAMILY_IMPORT_ROOTS`, so a
package imported from a root in another bucket is imported, but IGV cannot show its reads.
Alignments that no import recorded are looked for at `<family_id>/<sample_id>.cram` (with
`.crai`), or the same under `<family_id>/bams/` or `<family_id>/alignments/`, at the top of
the bucket (under `GCS_PREFIX` when that is set).

**Sizing.** The temporary folder is `/tmp`, which on Cloud Run is memory: a staged package
counts against `backend_memory` (2 GiB by default). The aligned reads and their indexes stay
in the bucket; every other object under the package folder is staged, whether the manifest
names it or not. For a whole-genome trio, the SNV VCF and its VEP table alone can take
several GB. The import also parses the VEP table into a temporary database in `/tmp`, and
reads each SV and CNV VCF whole into memory (up to `MAX_UPLOAD_BYTES`, 1 GiB, and
`MAX_DECOMPRESSED_UPLOAD_BYTES`, 2 GiB once decompressed). Each import job stages its own
copy and deletes it when the import ends, whether it succeeded or failed, and each backend
instance runs up to `FAMILY_IMPORT_WORKER_COUNT` jobs at once (1 by default). A
**Validate package** run is a dry-run import job and stages the package the same way; only
the API's `POST /api/family-imports/validate` stages a copy outside the jobs, and deletes it
before it answers. Set `backend_memory` to fit the largest package without its alignments,
plus that parsing, for every import that may run at once.

The **reference-data** bucket (`refdata`) is always mounted into the backend at
`/data/ref-data`, regardless of this setting, and **read-only**. The backend image holds
no reference files, so upload all of `data/ref-data/` from the repository, plus the
dbNSFP gene file ([data-import.md](data-import.md), section 2):

```bash
REFDATA=$(terraform output -raw refdata_bucket)
gcloud storage cp -r data/ref-data/* "gs://${REFDATA}/"
gcloud storage cp dbNSFP5.4_gene.gz "gs://${REFDATA}/"
```

If the HPO ontology is missing there, the startup bootstrap downloads the same pinned
release to a temporary directory, and imports it only if its SHA-256 is the vendored
file's ([data/ref-data/hpo/README.md](../data/ref-data/hpo/README.md#the-download-fallback)).

Two more files in the bucket must be the repository's files of the version being deployed,
byte for byte: `hpo/hp.obo` and
`clinical_cnv_syndromes_hg38_bundle/ClinGen_recurrent_CNV_V2.1-hg38.bed`, GRCh38's segmental
duplications. A start on a database without HPO terms, or without GRCh38 segmental
duplications, loads the file only if its SHA-256 is the pinned one (the segmental
duplications into a GRCh38 assembly only:
[data-import.md](data-import.md#what-startup-loads)). Any other file is not loaded: the log
names it, both SHA-256 digests and the fix, and the start goes on without it (the HPO
terminology, or the segmental duplications track, stays empty until the file is right).
Copying them again is always safe:

```bash
gcloud storage cp data/ref-data/hpo/hp.obo "gs://${REFDATA}/hpo/"
gcloud storage cp data/ref-data/clinical_cnv_syndromes_hg38_bundle/ClinGen_recurrent_CNV_V2.1-hg38.bed \
  "gs://${REFDATA}/clinical_cnv_syndromes_hg38_bundle/"
```

The repeat catalogue's `STRchive-loci.json` must be the repository's file of the version
being deployed, byte for byte. If the bucket holds another file, or none, the backend does
not start, and its log names the file, both SHA-256 digests and the fix
([data-import.md](data-import.md#the-strchive-release)); a new revision that cannot start never
receives traffic. Copying the file again is always safe:

```bash
gcloud storage cp data/ref-data/STRchive-loci.json "gs://${REFDATA}/"
```

A version that moves to another STRchive release ships its file and says so in its change
record. Copy that file to the bucket just before you deploy the version: from then on an
instance of the running version that starts (a scale-out or a restart) is refused, until the
new revision serves.

---

## 12. Day-2 operations

### 12.1 Deploy a new version of the app

- **Normal:** merge to `main` → CI builds new images, and after approval applies them.
- **Manual:** build new images under a new tag (Section 7), then apply them by digest:
  `terraform apply -var="backend_image=...@sha256:<digest>" -var="frontend_image=...@sha256:<digest>"`.

Cloud Run rolls out a new revision with zero-downtime; if it fails its health check,
traffic stays on the old revision.

**A Postgres schema change** reaches the database with the new version: in owner mode its
first start migrates the schema, and with the restricted role (12.8) the migration job does so
before the rollout ([database.md](database.md#schema-migrations)). Before either, the apply
takes an on-demand backup of `coga-postgres` and waits until it is done: the restore point of
the upgrade, described as `Before the schema migration of coga-backend@sha256:<digest>`, the
image the deploy runs ([terraform/migrate.tf](../terraform/migrate.tf)). It takes one for every
new backend image, because the machine that applies cannot reach the database to read its
revision, and until the release candidate a migration can change the schema without a new
revision; an apply that changes no image takes none. When the backup fails, the apply stops
before the migration, and the running revision keeps serving (15). The migration is one
transaction: when it fails, nothing of it is kept, the new revision does not start, and traffic
stays on the old one. With the restricted role the API, which does not migrate, also checks at
its start that the database is at exactly its own revision, and refuses to start otherwise.
Revisions do not run backwards, and a version older than the database's revision refuses to
start, so going back across a revision means restoring the backup taken before it (12.3).

### 12.2 Rotate a secret

Add a new version, then roll the backend so it picks it up:

```bash
printf '%s' 'NEW-VALUE' | gcloud secrets versions add coga-secret-key --data-file=-
# Re-deploy the backend revision (re-reads "latest"):
gcloud run services update coga-backend --region "$REGION" --update-labels rotated=$(date +%s)
```

The backend reads its secrets when an instance starts, so running instances keep the old
value until the roll.

For the **Postgres password**, Terraform sets the database user's password from the
secret. Add the new version, run `terraform apply` (it changes the user's password, not
the backend: it sees the new version by its number, the only part of the secret its state
keeps), then roll the backend as above. Between the two, running instances cannot
open new database connections, so do it at a quiet time. For `coga_app`'s password, see
[db-runtime-role-runbook.md](db-runtime-role-runbook.md), "Google Cloud".

For **`coga-admin-password`**, the password of `coga-admin` (the emergency administrator,
12.12): add the new version, then let the admin seed run. In owner mode it runs when the
backend starts, so roll the backend as above; with the restricted role (12.8) re-run the
migration job (`gcloud run jobs execute coga-db-migrate --region "$REGION" --wait`). The seed
replaces the stored password hash when it no longer matches the secret, so the secret is the
password; nothing else changes a local password.

### 12.3 Backups & restore

**PostgreSQL** has automated daily backups + point-in-time recovery (PITR), retained
per `db_backup_retained_count` (default 30).

```bash
# List backups:
gcloud sql backups list --instance coga-postgres
# Restore into a NEW instance (safest — never overwrite the live one blindly):
gcloud sql backups restore <BACKUP_ID> --restore-instance=coga-postgres-restored --backup-instance=coga-postgres
```

**The backup before each schema migration** (12.1) is an on-demand backup. Unlike the
automated ones it stays until someone deletes it, so delete those you no longer need as a
restore point, keeping at least the one taken before the running version's migration:

```bash
gcloud sql backups list --instance coga-postgres --filter='type=ON_DEMAND' \
  --format='table(id,windowStartTime,status,description)'
gcloud sql backups delete <BACKUP_ID> --instance coga-postgres
```

**Going back across a schema migration** (a rollback to a version older than the database's
revision, [RELEASING.md](../RELEASING.md#rollback)) restores that backup onto the live
instance: everything written after it is lost, so do it announced, at a quiet time, and record
it as a change (TF-18).

1. Find the backup taken before the migration of the version you leave: its description names
   that version's image, `Before the schema migration of coga-backend@sha256:<digest>` (the
   digest is in its deploy's run summary).
2. Keep what that version wrote: `gcloud sql backups create --instance coga-postgres
   --description="Before the rollback restore"`.
3. Restore: `gcloud sql backups restore <BACKUP_ID> --restore-instance=coga-postgres`. The
   instance is unavailable while it restores.
4. Right away, deploy the previous release by its digests (RELEASING.md, Rollback). Its apply
   takes a new backup, and its migration finds the database at its own revision.

Until step 4 finishes, the newer version still serves, on the older schema, and its requests
may fail. In owner mode an instance of it that starts in that time (a scale-out or a restart)
migrates the database forward again, and the previous release then refuses to start on it:
restore once more and deploy. With the restricted role such an instance refuses to start.

**ClickHouse** data disk is snapshotted daily (retained `clickhouse_snapshot_retention_days`,
default 14).

```bash
# List snapshots:
gcloud compute snapshots list --filter="name~coga-clickhouse"
# Recover: create a new disk from a snapshot, then attach it to a recovery VM.
gcloud compute disks create coga-clickhouse-data-restored \
  --source-snapshot=<SNAPSHOT_NAME> --zone="$REGION-b" --type=pd-ssd
```

**Upgrading ClickHouse** (a new `clickhouse_image`). The server upgrades its data directory
in place the first time the new version starts, and the previous version is not guaranteed
to read it afterwards, so switching the image back is not a rollback. Take a snapshot of the
data disk first (above); to roll back, restore that snapshot.

> **Run a restore drill** before go-live: actually restore into a
> throwaway instance/VM and confirm the data is intact. A backup you've never
> restored is not a backup.

### 12.4 ClickHouse TLS cert rotation

Terraform generates the private keys of the CA and of the ClickHouse server during the run
that issues their certificates, and keeps them in neither its state nor its plan
([terraform/tls.tf](../terraform/tls.tf)). The CA's key is therefore gone once that run
ends, so the CA and the server certificate are always issued together, in one apply. That
happens:

- when `clickhouse_tls_key_version` in `terraform/tls.tf` is raised, as a reviewed change
  (to rotate the keys, for example after a suspected compromise);
- when the VM's internal address changes (a new server key as well);
- 30 days before the 10-year certificates expire: the CA renews and re-issues the server
  certificate, for the same server key.

The same apply rolls out a backend revision that trusts only the new CA, while the VM
keeps serving the old certificate until it restarts or its daily `systemd` timer re-fetches
the key and certificate from Secret Manager. Until then the app cannot reach ClickHouse.
So plan such an apply for a quiet time, and restart the VM right after it (its shutdown
script stops ClickHouse cleanly, and its startup script fetches the new key and
certificate):

```bash
gcloud compute instances stop coga-clickhouse-vm --zone "$REGION-b"
gcloud compute instances start coga-clickhouse-vm --zone "$REGION-b"
```

Then check that the app's health check is green and that a variant search answers. The VM
has **no SSH ingress** (TF-13 S-8), which is why the restart, not a shell, is the way to
make it reload.

If an apply fails with `x509: provided PrivateKey doesn't match parent's PublicKey`, a change
would have re-issued the server certificate without the CA (for example a new validity
period on the server certificate alone). Raise `clickhouse_tls_key_version` in the same
change, apply, and restart the VM.

### 12.5 Scaling

- **Backend/frontend traffic:** raise `backend_max_instances` / `frontend_max_instances`.
- **Database:** raise `db_tier` (and `REGIONAL` for HA).
- **ClickHouse:** raise `clickhouse_machine_type` / `clickhouse_data_disk_gb`. Terraform
  grows the disk, but not the file system on it: afterwards run
  `sudo resize2fs /dev/disk/by-id/google-clickhouse-data` on the VM (through the
  break-glass SSH rule of 12.4). Plan capacity ahead.

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

**Metrics and alerts** (`terraform/monitoring.tf`, [monitoring.md](monitoring.md)). The
backend serves Prometheus metrics at `/metrics`: request errors and latency per route, the
ClickHouse integrity check per assembly, the audit pipelines' backlog and lost events, stuck
imports. A collector, the Google-Built OpenTelemetry Collector, runs as a second container of
`coga-backend` (`metrics-collector`). It scrapes `localhost:8000/metrics` every minute with the
token from `coga-metrics-token` and writes to Managed Service for Prometheus. The load balancer
never routes `/metrics`, so the endpoint stays inside the service. Alert policies read the series
with PromQL and email `alert_notification_emails`; an uptime check fetches
`https://<app_domain>/api/health` every minute from several regions.

After the first apply, check that metrics arrive: in Cloud Monitoring, *Metrics Explorer*, PromQL,
query `coga_build_info`; it should show the running version. *Alerting* lists the policies, each
with what to do when it fires.

- **The deploy account** needs the rights to create alert policies, notification channels and
  uptime checks (`terraform/main-repo-reference/rollout-checklist.md`, Part C).
- **With `allowed_ingress_cidrs` set,** Cloud Armor denies Google's uptime checkers unless their
  ranges are allowed (`gcloud monitoring uptime list-ips`); allow them, or set
  `uptime_check_enabled = false`. Terraform warns about this combination.
- **Without the collector** (`metrics_collection_enabled = false`), the backend gets no
  `METRICS_TOKEN`, its `/metrics` is off, and only the uptime check alerts.

### 12.7 Cloud Armor: from log-only to enforce

The WAF ships in **preview (log-only)** so it can't false-positive-block the
genomics API on day one. After reviewing the WAF logs (12.6) and confirming no
legitimate requests are flagged:

```bash
terraform apply -var="cloud_armor_waf_enforce=true"   # plus your other -vars
```

Tune `cloud_armor_rate_limit_per_minute` to your real peak interactive load.

### 12.8 Switch the API to the restricted database role

Every deployment starts in owner mode: the API connects as the table owner and applies
the schema on startup. At go-live, switch it to the restricted role `coga_app`, which
cannot run DDL or change the append-only audit tables (TF-09b REQ-TRACE-008). This is a
change-controlled deployment; the full procedure, verification and rollback are in
[db-runtime-role-runbook.md](db-runtime-role-runbook.md), "Google Cloud". In short:

1. Have the central infra repo create the `coga-db-migrate` service account, with
   `roles/cloudsql.client`, and let the deploy account act as it.
2. Add a version to `coga-postgres-app-password` (5.5).
3. Set `db_runtime_role = "coga_app"` (in CI: the `COGA_TFVARS` environment variable) and
   deploy.

Terraform then takes an on-demand backup of the database (12.1) and runs the
`coga-db-migrate` Cloud Run job as the owner. It migrates the schema (12.1), seeds the admin
user and enables `coga_app`'s login, and only then does the backend roll out, connecting as
`coga_app`. The backup and the job run again with every new backend image. The API, which no
longer migrates, refuses to start unless the database is at its own schema revision. The
API's service account loses access to the owner's password.

### 12.9 Restrict the app to institutional networks

Set `allowed_ingress_cidrs` to the UGent / UZ Gent public ranges and the VPN egress
ranges IT confirms. Cloud Armor then denies every other source at the edge, before rate
limiting, the WAF or application authentication. Test it first from inside and outside
those networks; an empty list (the default) leaves the app reachable from anywhere.

### 12.10 ClickHouse egress lockdown

By default the ClickHouse VM reaches the internet through Cloud NAT, to pull its Docker
Hub image. With `clickhouse_restrict_egress = true` it may only reach Google APIs
(Secret Manager, Logging, Artifact Registry) over `private.googleapis.com`, and every
other outbound connection is denied. Before switching it on:

1. Copy the ClickHouse image into Artifact Registry with its digest unchanged (for
   example `gcrane cp`, which copies the manifest as it is), and set `clickhouse_image` to
   it. The plan refuses a Docker Hub image while the lockdown is on.
2. Grant the VM's service account `roles/artifactregistry.reader` on that repository, and
   enable the Cloud DNS API (central infra repo).
3. Deploy, then reset the VM so its startup script pulls the image the new way:
   `gcloud compute instances reset coga-clickhouse-vm --zone "$REGION-b"`. Watch the serial
   console (15) until ClickHouse is up and the app's health check is green.

With the lockdown on, Container-Optimized OS can no longer update itself in place. Patch
it by recreating the VM on a current image; the data disk is separate and is kept.

### 12.11 Once: clear the secrets out of a state written before OPS-10

Up to the change that closed OPS-10, Terraform kept the database owner's password and the
ClickHouse TLS private keys (the CA's and the server's) in its state. It no longer does:
the configuration now needs Terraform 1.11 or later and version 8 of the google provider,
and passes those values only through write-only arguments. An environment that was applied
before that change still has them, in the current state file and in every older version the
versioned state bucket keeps. **Skip this section if no environment was applied before the
change** (none had been when it was made). Otherwise, once per environment:

1. **Plan a quiet hour.** Step 3 leaves ClickHouse unreachable from the app for a few
   minutes, and step 4 briefly stops the backend from opening new database connections.
2. **Deploy the change as usual** (merge, then approve the `gcp-deploy` job; or by hand
   with Terraform 1.11 or later, Section 7). No `terraform state rm` or import is needed:
   the apply itself drops the old values from the current state. Read the plan before you
   approve it. Expect this:
   - `google_sql_user.coga` updated in place: the password moves to the write-only
     argument. Terraform sets the same password again, so the backend notices nothing.
   - `tls_private_key.ca` and `tls_private_key.clickhouse` destroyed. They existed only in
     the state.
   - `tls_self_signed_cert.ca`, `tls_cert_request.clickhouse`,
     `tls_locally_signed_cert.clickhouse` and the versions of `coga-clickhouse-tls-key` and
     `coga-clickhouse-tls-cert` replaced: new keys, a new CA and a new server certificate.
     The old secret versions, with the old server key, are destroyed.
   - `google_cloud_run_v2_service.backend` updated (the new CA in `CLICKHOUSE_CA_CERT`),
     and the ClickHouse VM's metadata updated in place (a comment in its
     certificate-refresh script). With the restricted database role (12.8),
     `google_cloud_run_v2_job.db_migrate` updated as well (`deletion_protection = false`).

   An in-place update that only records a default of the newer provider (for example
   `send_age_if_zero` in a bucket's lifecycle rule) is harmless. Any other replacement or
   destruction is not part of this change: stop and find out why before approving.
3. **Restart the ClickHouse VM right after the apply** (12.4), so it loads the new key and
   certificate; the backend's new revision trusts only the new CA. Then check that the
   health check is green and that a variant search answers.
4. **Rotate the owner's password**, because the older state versions still hold the old
   one. Add a new version to `coga-postgres-password` (5.5), apply again so that Terraform
   sets it on the database user, then roll the backend (both in 12.2). Once the app works
   on the new password, disable the old version:
   `gcloud secrets versions disable <old version> --secret=coga-postgres-password`.
   The old ClickHouse keys need no such step: step 2 replaced them, and nothing trusts the
   old CA any more.
5. **Delete the older versions of the state file**, which still hold the old values, if
   your retention rules allow deleting state history. After steps 3 and 4 those values open
   nothing, so this only tidies up. Keep the live version:

   ```bash
   gcloud storage ls --all-versions "gs://${STATE_BUCKET}/coga/dev/default.tfstate"
   # Delete each generation older than the live one (the live one has the highest number):
   gcloud storage rm "gs://${STATE_BUCKET}/coga/dev/default.tfstate#<generation>"
   ```

6. **Delete any local copies** of the state or of plan files (`tfplan`) made before the
   change, for example from a manual run (Section 7); they hold the same values. CI keeps
   none: its plan file lives only on the runner.

### 12.12 Microsoft sign-in (Azure AD / Microsoft Entra ID)

At go-live users sign in with their hospital or university Microsoft account (SEC-4):
Microsoft Entra ID (formerly Azure AD) checks the password and MFA, and IT handles password
resets and leavers. The browser signs the user in with the authorization code flow with PKCE
(MSAL) and sends the access token Microsoft issues for CoGA's API scope with every request;
the backend checks it (signature, issuer, tenant, audience, scope, lifetime). A user's first
sign-in makes their CoGA account, a viewer in no project, so they see no patient data until an
administrator adds them to a project; administrators set the role on the *Users* page. Without
these settings every account signs in with its password, as in development.

**What IT creates** (in the organisation's Entra ID, once per environment):

1. An **app registration**, for example `CoGA (production)`:
   - *Supported account types*: accounts in this organisational directory only (single
     tenant).
   - *Authentication*: platform **Single-page application**, redirect URI
     `https://<app_domain>/login` (for example `https://coga.cmgg.be/login`). CoGA also
     returns there after a Microsoft sign-out. No web platform, no implicit grant (leave
     *Access tokens* and *ID tokens* unticked), and **no client secret or certificate**: a
     single-page application has none.
   - *Expose an API*: application ID URI `api://<application (client) ID>` (the default), and
     one scope **`access_as_user`** (*Access CoGA as the signed-in user*), enabled. The app is
     its own client: the browser asks for this scope.
   - *Manifest*: **`"requestedAccessTokenVersion": 2`** (`accessTokenAcceptedVersion: 2` in
     the older manifest). CoGA accepts only version 2.0 tokens, issued by
     `https://login.microsoftonline.com/<tenant ID>/v2.0` for the application ID; a version 1
     token is refused, and the backend log then says so.
   - *API permissions*: Microsoft Graph `openid`, `profile` and `offline_access` (delegated)
     and the app's own `access_as_user`, with **admin consent** granted for the organisation,
     so no user is asked to consent.
   - *Token configuration*: the optional claims **`email`**, **`given_name`** and
     **`family_name`** in the **access** token. Without `email`, CoGA takes
     `preferred_username` (the user principal name) as the e-mail address; without a name it
     shows the e-mail address.
2. On its **enterprise application**: *Assignment required* = **Yes**, with the group of lab
   members who use CoGA assigned. Nobody else can get a token, so nobody else gets an account
   or sees the list of CoGA users.
3. The organisation's **Conditional Access** policy (MFA) applied to the app.

**What IT gives you** (none of it is secret): the **directory (tenant) ID** and the
**application (client) ID**, both GUIDs, and the scope if it is not
`api://<client ID>/access_as_user`.

**What you set**: `azure_ad_tenant_id`, `azure_ad_client_id` and, for another scope,
`azure_ad_api_scope` (6). A plan with one of the two IDs and not the other is refused, and so
is the backend's start; so are a tenant that is no GUID (`common`, `organizations`, a domain
name) and an override without Microsoft sign-in. The browser fetches its tokens from
`https://login.microsoftonline.com`: the default CSP allows it, and a narrowed
`CSP_CONNECT_SRC` (Docker Compose) must keep it.

**Go-live** (each step a change-controlled deployment, TF-18):

1. Deploy with the two IDs and `azure_admin_override = true`.
2. The lab's administrators each sign in with Microsoft once; CoGA makes their accounts.
3. Sign in at `https://<app_domain>/login/emergency` as `coga-admin` (`admin@<app_domain>`,
   the `coga-admin-password` secret), give those accounts the admin role on the *Users* page,
   and add the users to their projects.
4. Deploy with `azure_admin_override = false`. From then on everyone signs in with Microsoft;
   the password sign-in and the sign-up are refused.

**The emergency (break-glass) administrator** is `coga-admin`, the one local account. While
`azure_admin_override` is `false` (the default) it cannot sign in. When Microsoft sign-in
cannot be used and an administrator must act: deploy with `azure_admin_override = true`, sign
in at `/login/emergency`, do what is needed, and deploy with `false` again. Every emergency
sign-in and every request made with it writes an `azure_admin_override` warning to the
backend log and is flagged *Emergency sign-in* on the *Audit logs* page
(`request_meta.emergency_sign_in`); review them after each use. CoGA does not change its
role or deactivate it, and its password is the `coga-admin-password` secret (12.2).

**Leavers and changes**: disabling the account in Entra ID stops new sign-ins; an access token
already issued stays valid until it expires (about an hour). For an immediate stop, deactivate
the account on the *Users* page. A changed name or e-mail address follows at the next sign-in:
the account is bound to the user's object ID in Entra ID, never to an e-mail address. A first
sign-in whose e-mail address belongs to another CoGA account is refused rather than linked;
an administrator resolves it in the database.

---

## 13. Security & compliance

This deployment closes the deployment-level security items tracked in
[TF-13 §3](regulatory/TF-13-cybersecurity.md) and
[security-posture.md](security-posture.md):

| Item | How it's handled here |
|------|----------------------|
| **S-1** encryption at rest | CMEK on Cloud SQL, both disks, and both GCS buckets |
| **S-2** TLS to datastores | Postgres via the Cloud SQL Connector (mTLS, verify-full grade); ClickHouse over HTTPS:8443 with a private CA the backend verifies |
| **S-3** secrets management | Secret Manager; values injected at runtime, not baked into images; no password or private key in the Terraform state or plan (write-only arguments, 12.4, 12.11) |
| **S-4** byte-level PHI audit | GCS Data Access audit logs (set in the central infra repo) |
| **S-8** network posture | Private IPs, no public DB ingress, no SSH to the ClickHouse VM, least-privilege service accounts, NAT/PGA, optional edge IP allowlist (12.9) and ClickHouse egress lockdown (12.10) |
| DB privilege separation (#262) | `db_runtime_role = "coga_app"` (12.8): the API runs as a role that cannot change the audit trail and cannot read the owner's password |
| Sign-in (SEC-4) | Microsoft Entra ID (12.12): MFA, password policy and leavers by IT, the app assigned to the lab's group; one local emergency administrator, whose password sign-in is off unless `azure_admin_override` is switched on |
| Backups | Cloud SQL PITR + retained backups; daily ClickHouse disk snapshots (**do a restore drill**) |
| edge protection | Cloud Armor: adaptive DDoS, per-IP rate limiting, OWASP CRS 4.22 WAF, optional UGent/UZ IP allowlist; the HTTPS load balancer accepts TLS 1.2+ with the `MODERN` profile; the app page and the API send HSTS (`max-age=63072000; includeSubDomains`) |

**Still your responsibility (process, not code):** IVDR **change control** (TF-18) for
each switch in 12.7–12.10 and 12.12 and for the first production deployment, and the IFU (TF-15)
minimum IT requirements. Google Cloud is the chosen host. The DPIA and the data
processing agreement with Google are tracked in TF-14 and TF-02 §10.

---

## 14. Cost overview

Rough monthly drivers (EU pricing, order-of-magnitude — confirm with the GCP pricing
calculator):

- **Cloud SQL** — the biggest fixed cost; scales with `db_tier` and `REGIONAL` HA.
- **ClickHouse VM + SSD** — a constantly-running `e2-standard-4` + 200 GB SSD.
- **Cloud Run** — cheap; backend keeps `min_instance_count = 1` (background workers), the frontend scales to zero when idle + traffic.
- **Load balancer + Cloud Armor** — small fixed fee + per-request.
- **Storage + egress** — GCS for CRAM/BAM (can be large) + signed-URL download egress.
- **Metrics collector** — a second container in each backend instance, 1 vCPU and 512 MiB always
  allocated (`metrics_collector_cpu`, `metrics_collector_memory`), plus Managed Service for
  Prometheus ingestion: a few thousand series scraped every minute, a few euros a month.
- **KMS / Secret Manager / logging** — negligible.

To reduce a **dev** environment's cost: `db_availability_type = "ZONAL"`, a smaller
`db_tier`, a smaller `clickhouse_machine_type`, and lower disk sizes.

---

## 15. Troubleshooting

| Symptom | Likely cause & fix |
|---------|--------------------|
| Managed cert stuck `PROVISIONING` | DNS A record not pointing at `load_balancer_ip` yet, or domain not resolving. Fix DNS; wait up to ~60 min. |
| Backend revision won't go healthy | A required secret has no `latest` version (Section 5.5), or the DB/ClickHouse isn't reachable. Check `gcloud run services logs read coga-backend`. |
| "Refusing to start outside development/test with missing or weak secrets: …" | The named secrets are placeholders or malformed: `SECRET_KEY` needs 32+ characters, `INTEGRITY_ANCHOR_SIGNING_KEY` the base64 of 32 bytes, `METRICS_TOKEN` (when set) 32+ characters, and the Postgres, ClickHouse and admin passwords real values. Add correct secret versions (5.5) and roll the backend (12.2). |
| "Refusing to start outside development/test: INTEGRITY_ANCHOR_SIGNING_KEY is the same value as SECRET_KEY…" | Both secrets hold one value. Add a new anchor key as a version of `coga-integrity-anchor-key` (5.5) and roll the backend (12.2). |
| "Refusing to start outside development/test with AUDIT_LOG_MODE=off…" | The backend's environment switches the request and UI-event audit logs off. Remove the setting (the default is `async`) and roll the backend (12.2). |
| "Refusing to start outside development/test with cross-origin access open…" | `CORS_ORIGINS` or `CORS_ORIGIN_REGEX` admits localhost or any site; the development defaults do. The UI is same-origin behind the load balancer: Terraform sets `CORS_ORIGINS` to the app's domain and `CORS_ORIGIN_REGEX` empty, for the backend and the `coga-db-migrate` job alike (`backend_production_env` in `cloudrun.tf`). Remove any override and roll the backend (12.2). |
| A backend revision won't start, and the `metrics-collector` container's log shows its configuration or startup failing | Usually `coga-metrics-token` has no version (5.5): the collector cannot start without its `METRICS_TOKEN`. Add one and roll the backend (12.2), or set `metrics_collection_enabled = false`. |
| The alert *CoGA: no metrics from the backend* fires, while the app works | The collector cannot scrape (its log shows `Failed to scrape Prometheus endpoint`: a 401 means the two containers hold different tokens, so roll the backend after a token change) or cannot write (the backend account lacks `roles/monitoring.metricWriter`). |
| The uptime check fails although users can reach the app | With `allowed_ingress_cidrs` set, Cloud Armor denies the uptime checkers. Allow `gcloud monitoring uptime list-ips` ranges, or set `uptime_check_enabled = false`. |
| "Refusing to start outside development/test without the commit this build was made from…" | The image was built without `GIT_SHA`, so the reports it signs could not name their commit. Rebuild through `build.yml` or `ci/cloudbuild.backend.yaml`, which stamp it, and deploy that image. |
| `terraform apply` fails reading the Postgres password | The `coga-postgres-password` secret version doesn't exist yet — complete the secret bootstrap (5.5) before the full apply. |
| ClickHouse VM has no data / won't start the container | No Cloud NAT egress to pull the image, or the data disk didn't mount. With the egress lockdown (12.10): the image is not in Artifact Registry, or the VM's account cannot read it. Check the VM serial console: `gcloud compute instances get-serial-port-output coga-clickhouse-vm --zone "$REGION-b"`. |
| Deploy job fails at "Refuse to deploy without required reviewers" | The `gcp-deploy` environment has no required reviewer. Add one (Section 10) and re-run the job. |
| `terraform apply` fails in `terraform_data.pre_migration_backup` or `terraform_data.db_migrate` with "The backup of coga-postgres before the schema migration failed: …" | The backup before the schema migration did not succeed (12.1), so the migration did not run and the running revision keeps serving. "gcloud could not start it": the account that applies lacks `cloudsql.backupRuns.create` (the deploy account's `cogaPreMigrationBackup` role, [rollout-checklist.md](../terraform/main-repo-reference/rollout-checklist.md) Part C), or another operation on the instance is running. "Not done within 3600s": the backup may still finish (`gcloud sql operations list --instance coga-postgres`). Fix the cause and run the apply again: it takes a new backup. |
| `terraform apply` fails in `terraform_data.db_migrate` | The schema migration job failed (12.8); the running revision keeps serving. Read its logs: `gcloud run jobs executions list --job coga-db-migrate --region "$REGION"`, then `gcloud logging read 'resource.type="cloud_run_job" resource.labels.job_name="coga-db-migrate"' --limit 50`. The job loads the backend's settings first, so a "Refusing to start outside development/test…" line in its log has the cause given for that message above; once that is fixed, run the apply again. |
| "The database is at schema revision …, which this version of CoGA does not have" | A newer version migrated the database, and an older one was rolled out on it. Roll the newer version out again, or restore the backup taken before that upgrade (12.1, 12.3). |
| "The database is at schema revision …, and this version of CoGA needs revision …" | The API does not migrate at start (the restricted role, 12.8), and the migration job has not brought the database to this version. Run it (`gcloud run jobs execute coga-db-migrate --region "$REGION" --wait`, or deploy again) and roll the backend (12.2). |
| "The schema migration runs as the owner of the tables, not as coga_app…" | The API connects as `coga_app` (`POSTGRES_USER`) with `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP` on, and only the owner migrates. `db_runtime_role = "coga_app"` sets both together and runs the migration job as the owner (12.8): remove any hand-set override of either and roll the backend. |
| Postgres connector errors | The backend's service account lacks `roles/cloudsql.client`, or the Cloud SQL Admin API is off. Both come from the central infra repo (5.4), not from this configuration. |
| Legitimate requests blocked | If you enabled `cloud_armor_waf_enforce`, review the WAF logs (12.6) and tune; revert to `false` to log-only. |
| Frontend loads but API calls 404 | The LB path rule must route `/api/*` to the backend — re-`apply`; confirm the URL map exists. |

---

## 16. Teardown

**Destroying this environment deletes patient data.** Be certain, and export anything
you must keep first.

Three safety guards must be lifted manually:

1. **Cloud SQL** has `deletion_protection = true`. Set it to `false` in
   `terraform/database.tf` and `apply` before you can destroy it.
2. **GCS buckets** have `force_destroy = false`. Empty them (or set `force_destroy =
   true`) before destroy, or Terraform refuses to delete non-empty buckets.
3. **The two Cloud Run services** are protected by the google provider's own
   `deletion_protection`, on unless set. Add `deletion_protection = false` to
   `coga-backend` and `coga-frontend` in `terraform/cloudrun.tf` and `apply` first. (The
   `coga-db-migrate` job has it off already, because rolling back the switch of 12.8
   removes the job.)

Then:

```bash
cd terraform
terraform destroy -var="project_id=${PROJECT}" -var="cmek_key_self_link=${CMEK_KEY}" \
  -var="backend_image=x" -var="frontend_image=x"
```

The KMS key, state bucket, and Artifact Registry (the landing zone) are **not**
managed by this Terraform and survive — delete them separately if you truly want
nothing left.

---

## 17. FAQ

**Can I run this in a different region?**
Yes — set `region`/`zone` (and put the KMS key in that region). Keep it in the EU for
data residency.

**Why is ClickHouse on a VM, and what is not automated yet?**
See the design notes and the known residuals in
[terraform/README.md](../terraform/README.md) (for example the VM restart a ClickHouse
certificate re-issue needs, 12.4). The IVDR change-control and DPIA paperwork is a manual,
required step.

---

*Design notes and residuals:* [terraform/README.md](../terraform/README.md).
*Security posture:* [security-posture.md](security-posture.md) ·
[regulatory/TF-13-cybersecurity.md](regulatory/TF-13-cybersecurity.md).
