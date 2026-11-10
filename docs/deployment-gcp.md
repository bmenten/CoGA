# Deploying CoGA to Google Cloud (Terraform) — Full Guide

A **step-by-step, plain-language** guide to setting up CoGA on Google Cloud with Terraform.
You need to know a terminal, not GCP or Terraform: every term is explained at first use.

> Design notes, the map of the `.tf` files and the known residuals are in
> [terraform/README.md](../terraform/README.md). This document is the end-to-end
> walkthrough and operations manual.

CoGA handles patient data (PHI) and is regulated as an in-house IVD under IVDR. **Treat every
step as production-grade**: secrets, encryption, network isolation, audit logging and backups
are not optional. There is no production deployment yet.

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
| **frontend** | React single-page app (the UI in the browser). | Cloud Run |

**The data** lives in three places:

| Store | What it holds | Service |
|-------|--------------|---------|
| **PostgreSQL** | Users, projects, families, samples, review state, audit logs. | Cloud SQL (managed) |
| **ClickHouse** | High-volume variant rows (SNV/SV, interval tracks). | Self-hosted on a Compute Engine VM (GCP has no managed ClickHouse) |
| **Object storage** | Raw family data (CRAM/BAM for IGV, import packages) + reference data. | Google Cloud Storage (GCS) buckets |

**The supporting infrastructure** (created for you): a private network (VPC), a load balancer
with a managed TLS certificate, a Web Application Firewall (Cloud Armor), secret storage
(Secret Manager), least-privilege access to the buckets and secrets, and automated backups.
The encryption key, the service accounts and the project APIs come from outside this
configuration (4.3).

Everything lives in **`europe-west1` (Belgium)** by default, for EU data residency.

---

## 2. Glossary

- **Terraform** — a tool that creates cloud resources from text files (`.tf`). You describe
  the *desired* state; Terraform makes reality match it. OpenTofu is a drop-in open-source
  equivalent (`tofu` instead of `terraform`).
- **Terraform state** — Terraform's record of what it created, kept in a **GCS bucket** so the
  whole team shares one source of truth. It holds no password or private key (Terraform passes
  those through **write-only** arguments, which it never stores), but it maps the whole
  deployment, so the bucket must be private and encrypted.
- **`apply` / `plan`** — `plan` shows what *would* change; `apply` makes it happen.
- **Project** — a GCP container for resources and billing. CoGA may use one project, or
  several (a runtime project, a shared image-registry project, a KMS project).
- **Cloud Run** — runs a container without servers for you to manage, and scales up and down
  automatically. It runs the stateless backend and frontend.
- **Cloud SQL** — managed PostgreSQL: Google runs, patches and backs up the database.
- **ClickHouse** — a columnar analytics database, for the huge variant tables. GCP has no
  managed version, so it runs in a container on a small VM.
- **VPC** — a private network. The databases have **no public IP**; they are reachable only
  inside this network.
- **Serverless VPC connector** — the bridge that lets Cloud Run reach into the private VPC,
  to the databases.
- **Cloud NAT** — lets the ClickHouse VM, which has no public IP, make *outbound* internet
  calls (e.g. to pull its container image).
- **Private Google Access** — lets the VM reach Google APIs (Secret Manager, etc.) without a
  public IP.
- **Load balancer (LB)** — the public front door. It terminates HTTPS, applies the WAF, and
  routes `/api/*` to the backend and everything else to the frontend.
- **Cloud Armor** — a Web Application Firewall + DDoS protection in front of the LB.
- **Secret Manager** — secure storage for passwords and keys, injected into the app at
  runtime (never baked into images).
- **Cloud KMS / CMEK** — Customer-Managed Encryption Keys: encryption at rest with *your* key
  instead of Google's default key, for stronger control.
- **Artifact Registry** — stores the built container images.
- **Workload Identity Federation (WIF)** — lets GitHub Actions authenticate to GCP
  **without** a long-lived key file (keyless CI).
- **Service account (SA)** — a non-human identity. The backend runs *as* a service account
  with only the permissions it needs (least privilege).

---

## 3. How a request flows

1. A clinician opens `https://coga.cmgg.be`. DNS points the domain at the **load balancer's
   IP**.
2. The LB terminates TLS with the Google-managed certificate and passes the request through
   **Cloud Armor** (rate limiting, WAF).
3. The LB routes by path:
   - **`/api/...`** → the **backend** Cloud Run service.
   - **anything else** → the **frontend** Cloud Run service (the SPA).
   - Both are served from the *same domain*, so the browser makes same-origin calls: **no
     CORS complexity**, and the backend never needs a public URL.
4. The backend talks to **PostgreSQL** (via the Cloud SQL Connector, encrypted) and
   **ClickHouse** (HTTPS on port 8443) over the **private VPC**; none of that traffic
   touches the public internet.
5. For genome viewing (IGV), once the GCS backend is on, the backend gives the browser a
   short-lived **signed URL**, so the browser streams CRAM/BAM bytes and the CNV caller's
   signal files directly from the bucket.

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
- Enough IAM permissions to create the resources (Owner on the project is simplest for the
  initial bootstrap; tighten later).
- The ability to create the **landing-zone** pieces below, or an admin who provides them.

### 4.3 Landing-zone pieces (created once, possibly by an admin)

These live *outside* the per-environment Terraform because they are shared or sensitive. At
CMGG the landing zone and the central infra repo provide them; Section 5 shows how to create
them in a standalone project:

- A **GCS bucket for Terraform state** (private, versioned, CMEK).
- A **Cloud KMS key** for CMEK (same region as everything else).
- An **Artifact Registry** repository for images.
- **Workload Identity Federation** + the CI service accounts (only for the GitHub Actions
  path).
- Control over **DNS** for your domain (e.g. `coga.cmgg.be`).
- From the central infra repo
  ([terraform/main-repo-reference/](../terraform/main-repo-reference/coga-prerequisites.tf.example)):
  the **project APIs, the runtime service accounts and their roles**, the key grants to
  Google's service agents, the bucket data-access audit log, and the role that lets the
  deploy account take the backup before each schema migration (12.1). The go-live switches
  in 12.8 and 12.10 need two more things from it: the `coga-db-migrate` account, and the
  Cloud DNS API with Artifact Registry read access for the ClickHouse VM.

---

## 5. One-time bootstrap

Do this **once per environment** (e.g. once for `dev`, once for `prod`). Replace the
`<...>` placeholders. At CMGG, the landing zone and the central infra repo provide 5.1–5.4
and 5.6; run them yourself only in a standalone project. 5.5 is always yours.

```bash
# Pick your project and region.
export PROJECT=<your-coga-project-id>
export REGION=europe-west1
gcloud config set project "$PROJECT"
```

### 5.1 Terraform state bucket

Make a private, versioned bucket for Terraform's state (5.2 then sets its encryption key,
before any state is written):

```bash
export STATE_BUCKET="${PROJECT}-tfstate"
gcloud storage buckets create "gs://${STATE_BUCKET}" \
  --location="$REGION" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning
```

### 5.2 KMS key (CMEK)

One key encrypts the database, disks and buckets. It **must** be in the same region as
everything else.

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

> CMEK is mandatory (organization policy) and always on: `cmek_key_self_link` is a required
> variable. The central infra template grants the key to the Cloud SQL, Compute and Storage
> service agents (5.4).

### 5.3 Artifact Registry (for images)

```bash
gcloud artifacts repositories create gen-ghreg-shared-gbl \
  --repository-format=docker --location="$REGION" || true
```

> `gen-ghreg-shared-gbl` is the repo name the CI workflow expects. If your org uses another
> name, change `AR_REPO` in `.github/workflows/build.yml`.

### 5.4 Enable the APIs

CoGA's Terraform enables no APIs and creates no service accounts: that needs rights the
CoGA pipeline deliberately lacks. The central infra template,
[coga-prerequisites.tf.example](../terraform/main-repo-reference/coga-prerequisites.tf.example),
enables the APIs, creates the runtime service accounts, grants their roles and the key
grants above, and turns on the bucket data-access audit log. In a standalone project, copy
it into a Terraform directory of its own, drop the `.example` suffix and apply it as a
project owner, with `project_id` and `cmek_key_self_link`, before CoGA's first apply.

### 5.5 Create the secret values

Terraform creates the secret **containers**; you add the **values**, so real secrets never
live in Terraform variables. Create only the containers first, then add the values:

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
  seed), which is what `openssl rand -base64 32` prints. With any other length the backend
  refuses to start.
- You do **not** create `coga-clickhouse-tls-*`: Terraform generates the ClickHouse TLS
  cert/key itself, from keys it keeps in neither its state nor its plan (12.4).
- Terraform reads `coga-postgres-password` during each plan and apply to set the database
  owner's password, and keeps only the number of the version it read, not the value.
- `coga-admin-password` is the first login password of the admin user **`coga-admin`**, who
  signs in with the email `admin@<app_domain>` (Section 9). Once Microsoft sign-in is on,
  `coga-admin` is the emergency administrator, and this secret stays its password (12.11).

### 5.6 (CI only) Workload Identity Federation

Only for the GitHub Actions path (Section 10); if you only ever deploy manually, skip this.
The set-up is org-specific. The workflow expects these service accounts, where `<short>` is
the `GCP_REGION_SHORT` repository variable:

- `reg-dev-<short>-gh-actions@<registry-project>` (pushes images),
- `reg-dev-<short>-cb-runner@<registry-project>` (runs Cloud Build),
- `coga-dev-<short>-gh-actions@<coga-project>` (runs `terraform apply`).

Set it up following <https://github.com/google-github-actions/auth#setup>, and grant those
accounts the roles they need: Cloud Build, Artifact Registry writer, and, for the deploy
account, the roles to run `terraform apply`, act as the runtime service accounts and run
Cloud Run jobs (see
[main-repo-reference/rollout-checklist.md](../terraform/main-repo-reference/rollout-checklist.md)).

---

## 6. Configure your variables

Copy the example and edit it:

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # terraform.tfvars is gitignored
```

The variables you will most likely set:

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
| `azure_ad_tenant_id` / `azure_ad_client_id` | Microsoft sign-in (12.11): the directory (tenant) ID and the application (client) ID IT gives you; both or neither | empty (password sign-in) |
| `azure_ad_api_scope` / `azure_admin_override` | CoGA's API scope, and the emergency administrator's password sign-in beside Microsoft sign-in (12.11) | empty (`api://<client id>/access_as_user`) / `false` |
| `alert_notification_emails` | Who the alert policies email (12.6) | `[]` — set it, or nobody is told (Terraform warns) |
| `metrics_collection_enabled` | Run the metrics collector beside the backend, and the metric alerts | `true` |
| `uptime_check_enabled` | Check `https://<app_domain>/api/health` every minute and alert when it fails | `true` (see 12.6 with `allowed_ingress_cidrs`) |

The go-live switches (`cloud_armor_waf_enforce`, `db_runtime_role`,
`allowed_ingress_cidrs`, `clickhouse_restrict_egress`) are in 12.7–12.10, and Microsoft
sign-in (`azure_ad_*`) in 12.11. Every variable, with its default, is in
`terraform/variables.tf`.

For **production**, consider `db_availability_type = "REGIONAL"` and a larger
`db_tier` / `clickhouse_machine_type`.

---

## 7. First deployment (manual)

You need container images first: let CI build them (Section 10), or build them by hand from
the repo root:

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
does. The digest names exactly the image you built, even if the tag is later pushed over,
and a new build always changes the image string, which is what Terraform deploys on.

`./scripts/build-version.sh` prints `VERSION` with the pre-release identifier `dev` added
(`0.1.0` → `0.1.0-dev`, `0.1.0-rc.1` → `0.1.0-rc.1.dev`). Only a release build, which
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

Terraform creates the network, databases, secrets wiring, Cloud Run, the load balancer and
Cloud Armor. The first apply takes a while; Cloud SQL takes the longest. Before the backend
starts, the apply takes an on-demand backup of the database with your `gcloud` login (12.1),
as it does before every schema migration.

> **Tip:** the apply reads the Postgres password to set the DB user, so complete Section 5.5
> first.

---

## 8. DNS & TLS

The Google-managed certificate finishes provisioning only **after** your domain resolves to
the load balancer. Get its IP:

```bash
cd terraform
terraform output -raw load_balancer_ip      # e.g. 34.120.x.x
```

Create a DNS **A record**: `coga.cmgg.be → <that IP>`.

Then wait for the certificate, typically 15–60 minutes. Check its status:

```bash
gcloud compute ssl-certificates describe coga-cert --global \
  --format='value(managed.status, managed.domainStatus)'
```

It moves `PROVISIONING → ACTIVE`. Until it is `ACTIVE`, browsers show a TLS warning; that is
expected.

---

## 9. Verify it works

```bash
curl -i https://coga.cmgg.be/api/health         # 200 {"status":"ok"} once the app runs
curl -i https://coga.cmgg.be/api/health/ready   # 200 only when Postgres and ClickHouse answer
curl -s https://coga.cmgg.be/api/version        # the version and git commit that are running
curl -s -o /dev/null -D - https://coga.cmgg.be/ | grep -i strict-transport-security
curl -s -o /dev/null -D - https://coga.cmgg.be/api/health | grep -i strict-transport-security
```

The last two each print `strict-transport-security: max-age=63072000; includeSubDomains`
once. The app page and the API both tell the browser to use only HTTPS for this host, for
two years (`ENABLE_HSTS=true` on both Cloud Run services). The load balancer's HTTPS URL map
sets the same header on every answer it relays, replacing the services' own, so Cloud Run's
own error pages carry it too (`terraform/loadbalancer.tf`).

Then open `https://coga.cmgg.be` in a browser and log in with:

- **Email:** `admin@<app_domain>` (`admin@coga.cmgg.be` by default; Terraform sets it as
  `ADMIN_EMAIL`, and CoGA signs in by email, not by the username `coga-admin`)
- **Password:** the `coga-admin-password` value you set in 5.5

If the page loads and you can log in, the deployment is live. **Rotate the admin password**
(12.2) and **set up Microsoft sign-in** (12.11): users then sign in with their hospital or
university Microsoft account, and `coga-admin` becomes the emergency administrator.

The footer of every page names the same version and commit as `/api/version`, and its
**Report a problem** link opens the CMGGMC route.

**Client addresses.** The load balancer appends `<client-ip>,<lb-ip>` to `X-Forwarded-For`.
The backend takes the client `trusted_proxy_hops` (default 2) entries from the right, never
the client-settable left-most one. After the first deploy, check that the audit log's
`remoteIp` for your own request is your public address.

---

## 10. CI/CD (the normal path)

Day to day, GitHub Actions runs Terraform, not you. The workflow is
[.github/workflows/build.yml](../.github/workflows/build.yml):

- **On a pull request:** only `terraform fmt -check` + `terraform validate` (no credentials,
  no changes), so it is safe to review. This check and the deploy both run the Terraform
  version the workflow pins (`terraform_version`), at least the 1.11 that
  `terraform/versions.tf` requires.
- **On push to `main` (or a published release):** it builds both images (stamping
  `APP_VERSION`/`GIT_SHA`), pushes them to Artifact Registry, then runs `terraform init/plan/apply` to deploy.
  - A release build stamps its release's version. A `main` build stamps `VERSION` marked as a
    development build (`0.1.0-dev`, or `0.1.0-rc.1.dev` after the release candidate), so
    `/api/version` and the reports never give it a release's version.
  - A `main` build is tagged `main-<12-character commit>`, a release build with its release
    tag. Terraform gets each image by the digest its build pushed (`…@sha256:…`), so the
    revision Cloud Run runs is exactly the image that run built.
  - Every build is a new digest, so every deploy carries a new image: Cloud Run rolls out new
    revisions, and with the restricted database role (12.8) the migration job runs again.
    Before the schema migration a new image brings, the apply takes an on-demand Cloud SQL
    backup; a failed backup stops the deploy (12.1).
  - Only **one** trigger applies: pushes to `main` by default, or published releases when the
    repository variable `COGA_DEPLOY_TRIGGER` is `release`. There is one environment and one
    state, so a later `main` push must not overwrite a release deployment.
- **On a published release** it also refuses a tag that is not on `main`, before it builds
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
| `COGA_TFVARS` | `gcp-deploy` environment | Every other Terraform variable, as HCL (for example `storage_backend = "gcs"`, `app_domain = "..."`). CI writes it to `ci.auto.tfvars` before planning, so the next deploy does not revert a value set once; the five values CI passes with `-var` still win. No secrets here: they live in Secret Manager. |

Once configured, merging to `main` deploys automatically, after approval. The deploy job runs
in the `gcp-deploy` environment and **refuses to run until that environment has at least one
required reviewer** (Settings → Environments → `gcp-deploy` → Required reviewers), so an
infrastructure apply never runs without a person approving it.

---

## 11. GCS storage backend

By default `storage_backend = "local"`: the app does **not** read family data from a bucket
yet. The code, bucket and permissions are in place; turning it on takes two steps:

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

Package Import then reads from `gs://<phi bucket>/imports`, unless the `family_import_roots`
variable names other locations (`FAMILY_IMPORT_ROOTS`). It stages a package to the backend's
temporary folder, except the aligned reads, which stay in the bucket. It records where each
alignment and CNV signal file lies, and IGV reads those files through short-lived **signed
URLs** (keyless, via IAM `SignBlob`). The backend signs a recorded location only when it is
in the PHI bucket under `FAMILY_IMPORT_ROOTS`: a package imported from a root in another
bucket is imported, but IGV cannot show its reads. Alignments that no import recorded are
looked for at `<family_id>/<sample_id>.cram` (with `.crai`), or the same under
`<family_id>/bams/` or `<family_id>/alignments/`, at the top of the bucket (under
`GCS_PREFIX` when that is set).

**Sizing.** The temporary folder is `/tmp`, which on Cloud Run is memory: a staged package
counts against `backend_memory` (2 GiB by default).

- The aligned reads and their indexes stay in the bucket. Every other object under the
  package folder is staged, whether the manifest names it or not. For a whole-genome trio,
  the SNV VCF and its VEP table alone can take several GB.
- The import also parses the VEP table into a temporary database in `/tmp`, and reads each
  SV and CNV VCF whole into memory (up to `MAX_UPLOAD_BYTES`, 1 GiB, and
  `MAX_DECOMPRESSED_UPLOAD_BYTES`, 2 GiB once decompressed).
- Each import job stages its own copy and deletes it when the import ends, succeeded or
  failed. Each backend instance runs up to `FAMILY_IMPORT_WORKER_COUNT` jobs at once (1 by
  default), all on its one core: `terraform/` runs the imports in the API's instances, not
  in import-worker processes of their own as Docker Compose does
  ([application-scheme.md](application-scheme.md#import-worker-processes)).
- A dry run is an import job and stages the package the same way.

Set `backend_memory` to fit the largest package without its alignments, plus that parsing,
for every import that may run at once.

The **reference-data** bucket (`refdata`) is always mounted into the backend at
`/data/ref-data`, **read-only**, whatever this setting is. The backend image holds no
reference files, so upload all of `data/ref-data/` from the repository, plus the dbNSFP gene
file ([data-import.md](data-import.md), section 2):

```bash
REFDATA=$(terraform output -raw refdata_bucket)
gcloud storage cp -r data/ref-data/* "gs://${REFDATA}/"
gcloud storage cp dbNSFP5.4_gene.gz "gs://${REFDATA}/"
```

The first start imports HPO's newest release, downloaded from HPO; the bucket's `hpo/hp.obo`
is its backup when HPO cannot be reached. If the backup is missing there too, the startup
downloads the vendored release to a temporary directory, and imports it only if its SHA-256 is
the vendored file's ([data/ref-data/hpo/README.md](../data/ref-data/hpo/README.md#how-a-start-loads-hpo)).

Two of these files must be the repository's files of the version being deployed, byte for
byte: `hpo/hp.obo` and
`clinical_cnv_syndromes_hg38_bundle/ClinGen_recurrent_CNV_V2.1-hg38.bed`, GRCh38's segmental
duplications. A start on a database without HPO terms, or without GRCh38 segmental
duplications, loads the file only if its SHA-256 is the pinned one (the segmental
duplications into a GRCh38 assembly only:
[data-import.md](data-import.md#what-startup-loads)). Any other file is not loaded: the log
names it, both SHA-256 digests and the fix, and the start goes on without it. The HPO
terminology, or the segmental duplications track, then stays empty until the file is right.
Copying them again is always safe:

```bash
gcloud storage cp data/ref-data/hpo/hp.obo "gs://${REFDATA}/hpo/"
gcloud storage cp data/ref-data/clinical_cnv_syndromes_hg38_bundle/ClinGen_recurrent_CNV_V2.1-hg38.bed \
  "gs://${REFDATA}/clinical_cnv_syndromes_hg38_bundle/"
```

The repeat catalogue's `STRchive-loci.json` must also be the repository's file of the version
being deployed, byte for byte. If the bucket holds another file, or none, the backend does
not start, and its log names the file, both SHA-256 digests and the fix
([data-import.md](data-import.md#the-strchive-release)); a new revision that cannot start never
receives traffic. Copying the file again is always safe:

```bash
gcloud storage cp data/ref-data/STRchive-loci.json "gs://${REFDATA}/"
```

A version that moves to another STRchive release ships its file and says so in its change
record. Copy that file to the bucket just before you deploy the version. From then on, an
instance of the running version that starts (a scale-out or a restart) is refused, until the
new revision serves.

---

## 12. Day-2 operations

### 12.1 Deploy a new version of the app

- **Normal:** merge to `main` → CI builds new images, and after approval applies them.
- **Manual:** build new images under a new tag (Section 7), then apply them by digest:
  `terraform apply -var="backend_image=...@sha256:<digest>" -var="frontend_image=...@sha256:<digest>"`.

Cloud Run rolls out a new revision with zero downtime; if it fails its health check, traffic
stays on the old revision.

**A Postgres schema change** reaches the database with the new version. In owner mode its
first start migrates the schema; with the restricted role (12.8) the migration job does so
before the rollout ([database.md](database.md#schema-migrations)).

- Before either, the apply takes an on-demand backup of `coga-postgres` and waits until it
  is done. It is the restore point of the upgrade, described as
  `Before the schema migration of coga-backend@sha256:<digest>`, the image the deploy runs
  ([terraform/migrate.tf](../terraform/migrate.tf)).
- It takes one for every new backend image, because the machine that applies cannot reach
  the database to read its revision, and until the release candidate a migration can change
  the schema without a new revision. An apply that changes no image takes none.
- When the backup fails, the apply stops before the migration, and the running revision
  keeps serving (15).
- The migration is one transaction: when it fails, nothing of it is kept, the new revision
  does not start, and traffic stays on the old one.
- With the restricted role the API, which does not migrate, also checks at its start that
  the database is at exactly its own revision, and refuses to start otherwise.
- Revisions do not run backwards, and a version older than the database's revision refuses
  to start. Going back across a revision therefore means restoring the backup taken before
  it (12.3).

### 12.2 Rotate a secret

Add a new version, then roll the backend so it picks it up:

```bash
printf '%s' 'NEW-VALUE' | gcloud secrets versions add coga-secret-key --data-file=-
# Re-deploy the backend revision (re-reads "latest"):
gcloud run services update coga-backend --region "$REGION" --update-labels rotated=$(date +%s)
```

The backend reads its secrets when an instance starts, so running instances keep the old
value until the roll.

For the **Postgres password**, Terraform sets the database user's password from the secret.
Add the new version and run `terraform apply`: it changes the user's password, not the
backend (it sees the new version by its number, the only part of the secret its state
keeps). Then roll the backend as above. Between the two, running instances cannot open new
database connections, so do it at a quiet time. For `coga_app`'s password, see
[db-runtime-role-runbook.md](db-runtime-role-runbook.md), "Google Cloud".

For **`coga-admin-password`**, the password of `coga-admin` (the emergency administrator,
12.11): add the new version, then let the admin seed run. In owner mode it runs when the
backend starts, so roll the backend as above; with the restricted role (12.8), re-run the
migration job (`gcloud run jobs execute coga-db-migrate --region "$REGION" --wait`). The seed
replaces the stored password hash when it no longer matches the secret, so the secret is the
password; nothing else changes a local password.

### 12.3 Backups & restore

**PostgreSQL** has automated daily backups + point-in-time recovery (PITR), retained per
`db_backup_retained_count` (default 30).

```bash
# List backups:
gcloud sql backups list --instance coga-postgres
# Restore into a NEW instance (safest — never overwrite the live one blindly):
gcloud sql backups restore <BACKUP_ID> --restore-instance=coga-postgres-restored --backup-instance=coga-postgres
```

**The backup before each schema migration** (12.1) is an on-demand backup. Unlike the
automated ones it stays until someone deletes it. Delete those you no longer need as a
restore point, but keep at least the one taken before the running version's migration:

```bash
gcloud sql backups list --instance coga-postgres --filter='type=ON_DEMAND' \
  --format='table(id,windowStartTime,status,description)'
gcloud sql backups delete <BACKUP_ID> --instance coga-postgres
```

**Going back across a schema migration** (a rollback to a version older than the database's
revision, [RELEASING.md](../RELEASING.md#rollback)) restores that backup onto the live
instance. Everything written after it is lost, so announce it, do it at a quiet time, and
record it as a change (TF-18).

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
may fail. In owner mode, an instance of it that starts in that time (a scale-out or a
restart) migrates the database forward again, and the previous release then refuses to start
on it: restore once more and deploy. With the restricted role such an instance refuses to
start.

The **ClickHouse** data disk is snapshotted daily, retained for
`clickhouse_snapshot_retention_days` (default 14).

```bash
# List snapshots:
gcloud compute snapshots list --filter="name~coga-clickhouse"
# Recover: create a new disk from a snapshot, then attach it to a recovery VM.
gcloud compute disks create coga-clickhouse-data-restored \
  --source-snapshot=<SNAPSHOT_NAME> --zone="$REGION-b" --type=pd-ssd
```

**Upgrading ClickHouse** (a new `clickhouse_image`). The server upgrades its data directory
in place the first time the new version starts, and the previous version is not guaranteed
to read it afterwards, so switching the image back is not a rollback. Snapshot the data disk
first (above); to roll back, restore that snapshot.

> **Run a restore drill** before go-live: restore into a throwaway instance/VM and confirm
> the data is intact. A backup you have never restored is not a backup.

### 12.4 ClickHouse TLS cert rotation

Terraform generates the private keys of the CA and of the ClickHouse server during the run
that issues their certificates, and keeps them in neither its state nor its plan
([terraform/tls.tf](../terraform/tls.tf)). The CA's key is gone once that run ends, so the CA
and the server certificate are always issued together, in one apply. That happens:

- when `clickhouse_tls_key_version` in `terraform/tls.tf` is raised, as a reviewed change
  (to rotate the keys, for example after a suspected compromise);
- when the VM's internal address changes (a new server key as well);
- 30 days before the 10-year certificates expire: the CA renews and re-issues the server
  certificate, for the same server key.

The same apply rolls out a backend revision that trusts only the new CA. The VM keeps serving
the old certificate until it restarts or its daily `systemd` timer re-fetches the key and
certificate from Secret Manager; until then the app cannot reach ClickHouse. So plan such an
apply for a quiet time, and restart the VM right after it (its shutdown script stops
ClickHouse cleanly; its startup script fetches the new key and certificate):

```bash
gcloud compute instances stop coga-clickhouse-vm --zone "$REGION-b"
gcloud compute instances start coga-clickhouse-vm --zone "$REGION-b"
```

Then check that the app's health check is green and that a variant search answers. The VM
has **no SSH ingress** (TF-13 S-8), so a restart, not a shell, makes it reload.

Terraform stores the new key and certificate in Secret Manager before it destroys their old
versions, so a VM that boots during the apply still finds a key and a certificate to load.
A VM that boots between the new key and the new certificate may
load one new and one old; the restart after the apply loads the new pair.

If an apply fails with `x509: provided PrivateKey doesn't match parent's PublicKey`, a change
would have re-issued the server certificate without the CA (for example a new validity
period on the server certificate alone). Raise `clickhouse_tls_key_version` in the same
change, apply, and restart the VM.

### 12.5 Scaling

- **Backend/frontend traffic:** raise `backend_max_instances` / `frontend_max_instances`.
- **Database:** raise `db_tier` (and `REGIONAL` for HA).
- **ClickHouse:** raise `clickhouse_machine_type` / `clickhouse_data_disk_gb`. Terraform
  grows the disk, but not the file system on it: afterwards run
  `sudo resize2fs /dev/disk/by-id/google-clickhouse-data` on the VM. The VM has no SSH
  ingress (12.4), so add a temporary break-glass IAP SSH rule (source `35.235.240.0/20`,
  tcp/22, target tag `clickhouse`) and remove it afterwards (`terraform/network.tf`). Plan
  capacity ahead.
- **ClickHouse memory per query:** the VM runs ClickHouse with the memory guards Docker
  Compose uses, from the same file, `clickhouse/users.d/memory-guards.xml`. Each
  query is capped at 4 GB, and spills GROUP BY and ORDER BY state to disk past 2 GB. A
  larger machine therefore runs more such queries at once, not larger ones. The backend's
  `CLICKHOUSE_MAX_MEMORY_USAGE`, when set, overrides the cap for the app's queries. The
  startup script writes the file at each boot, so a change to it reaches the VM at its next
  restart (12.4), after the apply.

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

**Metrics and alerts** (`terraform/monitoring.tf`, [monitoring.md](monitoring.md)).

- The backend serves Prometheus metrics at `/metrics`: request errors and latency per route,
  the ClickHouse integrity check per assembly, the audit pipelines' backlog and lost events,
  stuck imports.
- A collector, the Google-Built OpenTelemetry Collector, runs as a second container of
  `coga-backend` (`metrics-collector`). It scrapes `localhost:8000/metrics` every minute with
  the token from `coga-metrics-token` and writes to Managed Service for Prometheus. The load
  balancer never routes `/metrics`, so the endpoint stays inside the service.
- Alert policies read the series with PromQL and email `alert_notification_emails`; an
  uptime check fetches `https://<app_domain>/api/health` every minute from several regions.

After the first apply, check that metrics arrive: in Cloud Monitoring, *Metrics Explorer*,
PromQL, query `coga_build_info`; it should show the running version. *Alerting* lists the
policies, each with what to do when it fires.

- **The deploy account** needs the rights to create alert policies, notification channels and
  uptime checks (`terraform/main-repo-reference/rollout-checklist.md`, Part C).
- **With `allowed_ingress_cidrs` set,** Cloud Armor denies Google's uptime checkers unless their
  ranges are allowed (`gcloud monitoring uptime list-ips`); allow them, or set
  `uptime_check_enabled = false`. Terraform warns about this combination.
- **Without the collector** (`metrics_collection_enabled = false`), the backend gets no
  `METRICS_TOKEN`, its `/metrics` is off, and only the uptime check alerts.

### 12.7 Cloud Armor: from log-only to enforce

The WAF ships in **preview (log-only)**, so false positives cannot block the genomics API on
day one. Once the WAF logs (12.6) show no legitimate requests flagged:

```bash
terraform apply -var="cloud_armor_waf_enforce=true"   # plus your other -vars
```

Tune `cloud_armor_rate_limit_per_minute` to your real peak interactive load.

### 12.8 Switch the API to the restricted database role

Every deployment starts in owner mode: the API connects as the table owner and applies the
schema on startup. At go-live, switch it to the restricted role `coga_app`, which cannot run
DDL or change the append-only audit tables (TF-09b REQ-TRACE-008). This is a
change-controlled deployment; the full procedure, verification and rollback are in
[db-runtime-role-runbook.md](db-runtime-role-runbook.md), "Google Cloud". In short:

1. Have the central infra repo create the `coga-db-migrate` service account, with
   `roles/cloudsql.client`, and let the deploy account act as it.
2. Add a version to `coga-postgres-app-password` (5.5).
3. Set `db_runtime_role = "coga_app"` (in CI: the `COGA_TFVARS` environment variable) and
   deploy.

Terraform then takes an on-demand backup of the database (12.1) and runs the
`coga-db-migrate` Cloud Run job as the owner. The job migrates the schema (12.1), seeds the
admin user and enables `coga_app`'s login. Only then does the backend roll out, connecting as
`coga_app`. The backup and the job run again with every new backend image. The API, which no
longer migrates, refuses to start unless the database is at its own schema revision. The
API's service account loses access to the owner's password.

### 12.9 Restrict the app to institutional networks

Set `allowed_ingress_cidrs` to the UGent / UZ Gent public ranges and the VPN egress ranges IT
confirms. Cloud Armor then denies every other source at the edge, before rate limiting, the
WAF or application authentication. Test it first from inside and outside those networks; an
empty list (the default) leaves the app reachable from anywhere.

### 12.10 ClickHouse egress lockdown

By default the ClickHouse VM reaches the internet through Cloud NAT, to pull its Docker Hub
image. With `clickhouse_restrict_egress = true` it may only reach Google APIs (Secret
Manager, Logging, Artifact Registry) over `private.googleapis.com`; every other outbound
connection is denied. Before switching it on:

1. Copy the ClickHouse image into Artifact Registry with its digest unchanged (for example
   `gcrane cp`, which copies the manifest as it is), and set `clickhouse_image` to it. The
   plan refuses a Docker Hub image while the lockdown is on.
2. Grant the VM's service account `roles/artifactregistry.reader` on that repository, and
   enable the Cloud DNS API (central infra repo).
3. Deploy, then reset the VM so its startup script pulls the image the new way:
   `gcloud compute instances reset coga-clickhouse-vm --zone "$REGION-b"`. Watch the serial
   console (15) until ClickHouse is up and the app's health check is green.

With the lockdown on, Container-Optimized OS can no longer update itself in place. Patch it
by recreating the VM on a current image; the data disk is separate and is kept.

### 12.11 Microsoft sign-in (Azure AD / Microsoft Entra ID)

At go-live users sign in with their hospital or university Microsoft account.
Microsoft Entra ID (formerly Azure AD) checks the password and MFA; IT handles password
resets and leavers. The browser signs the user in with the authorization code flow with PKCE
(MSAL), and sends the access token Microsoft issues for CoGA's API scope with every request.
The backend checks it (signature, issuer, tenant, audience, scope, lifetime). A user's first
sign-in makes their CoGA account: a viewer in no project, who sees no patient data until an
administrator adds them to a project. Administrators set the role on the *Users* page.
Without these settings every account signs in with its password, as in development.

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
     `https://login.microsoftonline.com/<tenant ID>/v2.0` for the application ID. A version 1
     token is refused, and the backend log says so.
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
`azure_ad_api_scope` (6). The plan refuses, and so does the backend's start: one of the two
IDs without the other, a tenant that is no GUID (`common`, `organizations`, a domain name),
and an override without Microsoft sign-in. The browser fetches its tokens from
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
sign-in, and every request made with it, writes an `azure_admin_override` warning to the
backend log and is flagged *Emergency sign-in* on the *Audit logs* page
(`request_meta.emergency_sign_in`); review them after each use. CoGA does not change its
role or deactivate it, and its password is the `coga-admin-password` secret (12.2).

**Leavers and changes**:

- Disabling the account in Entra ID stops new sign-ins; an access token already issued stays
  valid until it expires (about an hour).
- For an immediate stop, deactivate the account on the *Users* page. The jobs it queued as
  an administrator are then refused too (an import or a gene-reference refresh not yet
  started, a knowledgebase rebuild not yet loaded), as after a change of its role away from
  administrator.
- A changed name or e-mail address follows at the next sign-in. The account is bound to the
  user's object ID in Entra ID, never to an e-mail address, and the user's import jobs and
  filter presets are kept by the account, so they stay theirs.
- A first sign-in whose e-mail address belongs to another CoGA account is refused rather than
  linked; an administrator resolves it in the database.

---

## 13. Security & compliance

This deployment closes the deployment-level security items tracked in
[TF-13 §3](regulatory/TF-13-cybersecurity.md) and
[security-posture.md](security-posture.md):

| Item | How it's handled here |
|------|----------------------|
| **S-1** encryption at rest | CMEK on Cloud SQL, both disks, and both GCS buckets |
| **S-2** TLS to datastores | Postgres via the Cloud SQL Connector (mTLS, verify-full grade); ClickHouse over HTTPS:8443 with a private CA the backend verifies |
| **S-3** secrets management | Secret Manager; values injected at runtime, not baked into images; no password or private key in the Terraform state or plan (write-only arguments, 12.4) |
| **S-4** byte-level PHI audit | GCS Data Access audit logs (set in the central infra repo) |
| **S-8** network posture | Private IPs, no public DB ingress, no SSH to the ClickHouse VM, least-privilege service accounts, NAT/PGA, optional edge IP allowlist (12.9) and ClickHouse egress lockdown (12.10) |
| DB privilege separation | `db_runtime_role = "coga_app"` (12.8): the API runs as a role that cannot change the audit trail and cannot read the owner's password |
| Sign-in | Microsoft Entra ID (12.11): MFA, password policy and leavers by IT, the app assigned to the lab's group; one local emergency administrator, whose password sign-in is off unless `azure_admin_override` is switched on |
| Backups | Cloud SQL PITR + retained backups; daily ClickHouse disk snapshots (**do a restore drill**) |
| edge protection | Cloud Armor: adaptive DDoS, per-IP rate limiting, OWASP CRS 4.22 WAF, optional UGent/UZ IP allowlist; the HTTPS load balancer accepts TLS 1.2+ with the `MODERN` profile; the app page and the API send HSTS (`max-age=63072000; includeSubDomains`), and the load balancer sets it on every answer it relays |

**Still your responsibility (process, not code):** IVDR **change control** (TF-18) for each
switch in 12.7–12.11 and for the first production deployment, and the IFU (TF-15)
minimum IT requirements. Google Cloud is the chosen host. The DPIA and the data processing
agreement with Google are tracked in TF-14 and TF-02 §10.

---

## 14. Cost overview

Rough monthly drivers (EU pricing, order of magnitude; confirm with the GCP pricing
calculator):

- **Cloud SQL** — the biggest fixed cost; scales with `db_tier` and `REGIONAL` HA.
- **ClickHouse VM + SSD** — an always-running `e2-standard-4` + 200 GB SSD.
- **Cloud Run** — cheap: the backend keeps `min_instance_count = 1` (background workers), the
  frontend scales to zero when idle; plus traffic.
- **Load balancer + Cloud Armor** — small fixed fee + per-request.
- **Storage + egress** — GCS for CRAM/BAM (can be large) + signed-URL download egress.
- **Metrics collector** — a second container in each backend instance, 1 vCPU and 512 MiB always
  allocated (`metrics_collector_cpu`, `metrics_collector_memory`), plus Managed Service for
  Prometheus ingestion: a few thousand series scraped every minute, a few euros a month.
- **KMS / Secret Manager / logging** — negligible.

To reduce a **dev** environment's cost: `db_availability_type = "ZONAL"`, a smaller
`db_tier`, a smaller `clickhouse_machine_type`, and smaller disks.

---

## 15. Troubleshooting

| Symptom | Likely cause & fix |
|---------|--------------------|
| Managed cert stuck `PROVISIONING` | The DNS A record does not point at `load_balancer_ip` yet, or the domain does not resolve. Fix DNS; wait up to ~60 min. |
| Backend revision won't go healthy | A required secret has no `latest` version (Section 5.5), or the DB/ClickHouse is unreachable. Check `gcloud run services logs read coga-backend`. |
| "Refusing to start outside development/test with missing or weak secrets: …" | The named secrets are placeholders or malformed: `SECRET_KEY` needs 32+ characters, `INTEGRITY_ANCHOR_SIGNING_KEY` the base64 of 32 bytes, `METRICS_TOKEN` (when set) 32+ characters, and the Postgres, ClickHouse and admin passwords real values. Add correct secret versions (5.5) and roll the backend (12.2). |
| "Refusing to start outside development/test: INTEGRITY_ANCHOR_SIGNING_KEY is the same value as SECRET_KEY…" | Both secrets hold one value. Add a new anchor key as a version of `coga-integrity-anchor-key` (5.5) and roll the backend (12.2). |
| "Refusing to start outside development/test with AUDIT_LOG_MODE=off…" | The backend's environment switches the request and UI-event audit logs off. Remove the setting (the default is `async`) and roll the backend (12.2). |
| "Refusing to start outside development/test with cross-origin access open…" | `CORS_ORIGINS` or `CORS_ORIGIN_REGEX` admits localhost or any site, as the development defaults do. The UI is same-origin behind the load balancer: Terraform sets `CORS_ORIGINS` to the app's domain and `CORS_ORIGIN_REGEX` empty, for the backend and the `coga-db-migrate` job alike (`backend_production_env` in `cloudrun.tf`). Remove any override and roll the backend (12.2). |
| A backend revision won't start, and the `metrics-collector` container's log shows its configuration or startup failing | Usually `coga-metrics-token` has no version (5.5): the collector cannot start without its `METRICS_TOKEN`. Add one and roll the backend (12.2), or set `metrics_collection_enabled = false`. |
| The alert *CoGA: no metrics from the backend* fires, while the app works | The collector cannot scrape (its log shows `Failed to scrape Prometheus endpoint`; a 401 means the two containers hold different tokens, so roll the backend after a token change) or cannot write (the backend account lacks `roles/monitoring.metricWriter`). |
| The uptime check fails although users can reach the app | With `allowed_ingress_cidrs` set, Cloud Armor denies the uptime checkers. Allow the `gcloud monitoring uptime list-ips` ranges, or set `uptime_check_enabled = false`. |
| "Refusing to start outside development/test without the commit this build was made from…" | The image was built without `GIT_SHA`, so the reports it signs could not name their commit. Rebuild through `build.yml` or `ci/cloudbuild.backend.yaml`, which stamp it, and deploy that image. |
| `terraform apply` fails reading the Postgres password | The `coga-postgres-password` secret version does not exist yet: complete the secret bootstrap (5.5) before the full apply. |
| ClickHouse VM has no data / won't start the container | No Cloud NAT egress to pull the image, or the data disk did not mount. With the egress lockdown (12.10): the image is not in Artifact Registry, or the VM's account cannot read it. Check the VM serial console: `gcloud compute instances get-serial-port-output coga-clickhouse-vm --zone "$REGION-b"`. |
| Deploy job fails at "Refuse to deploy without required reviewers" | The `gcp-deploy` environment has no required reviewer. Add one (Section 10) and re-run the job. |
| `terraform apply` fails in `terraform_data.pre_migration_backup` or `terraform_data.db_migrate` with "The backup of coga-postgres before the schema migration failed: …" | The backup before the schema migration did not succeed (12.1), so the migration did not run and the running revision keeps serving. "gcloud could not start it": the account that applies lacks `cloudsql.backupRuns.create` (the deploy account's `cogaPreMigrationBackup` role, [rollout-checklist.md](../terraform/main-repo-reference/rollout-checklist.md) Part C), or another operation on the instance is running. "Not done within 3600s": the backup may still finish (`gcloud sql operations list --instance coga-postgres`). Fix the cause and run the apply again: it takes a new backup. |
| `terraform apply` fails in `terraform_data.db_migrate` | The schema migration job failed (12.8); the running revision keeps serving. Read its logs: `gcloud run jobs executions list --job coga-db-migrate --region "$REGION"`, then `gcloud logging read 'resource.type="cloud_run_job" resource.labels.job_name="coga-db-migrate"' --limit 50`. The job loads the backend's settings first, so a "Refusing to start outside development/test…" line in its log has the cause given for that message above; fix it and run the apply again. |
| "The database is at schema revision …, which this version of CoGA does not have" | A newer version migrated the database, and an older one was rolled out on it. Roll the newer version out again, or restore the backup taken before that upgrade (12.1, 12.3). |
| "The database is at schema revision …, and this version of CoGA needs revision …" | The API does not migrate at start (the restricted role, 12.8), and the migration job has not brought the database to this version. Run it (`gcloud run jobs execute coga-db-migrate --region "$REGION" --wait`, or deploy again) and roll the backend (12.2). |
| "The schema migration runs as the owner of the tables, not as coga_app…" | The API connects as `coga_app` (`POSTGRES_USER`) with `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP` on, and only the owner migrates. `db_runtime_role = "coga_app"` sets both together and runs the migration job as the owner (12.8): remove any hand-set override of either and roll the backend. |
| Postgres connector errors | The backend's service account lacks `roles/cloudsql.client`, or the Cloud SQL Admin API is off. Both come from the central infra repo (5.4), not from this configuration. |
| Legitimate requests blocked | If you enabled `cloud_armor_waf_enforce`, review the WAF logs (12.6) and tune; revert to `false` for log-only. |
| Frontend loads but API calls 404 | The LB path rule must route `/api/*` to the backend: re-`apply`, and confirm the URL map exists. |

---

## 16. Teardown

**Destroying this environment deletes patient data.** Be certain, and first export anything
you must keep.

Lift three safety guards by hand first:

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

The KMS key, state bucket and Artifact Registry (the landing zone) are **not** managed by
this Terraform and survive; delete them separately if you want nothing left.

---

## 17. FAQ

**Can I run this in a different region?**
Yes: set `region`/`zone` (and put the KMS key in that region). Keep it in the EU for data
residency.

**Why is ClickHouse on a VM, and what is not automated yet?**
See the design notes and the known residuals in
[terraform/README.md](../terraform/README.md) (for example the VM restart a ClickHouse
certificate re-issue needs, 12.4). The IVDR change-control and DPIA paperwork is a manual,
required step.

---

*Design notes and residuals:* [terraform/README.md](../terraform/README.md).
*Security posture:* [security-posture.md](security-posture.md) ·
[regulatory/TF-13-cybersecurity.md](regulatory/TF-13-cybersecurity.md).
