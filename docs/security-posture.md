# Security & PHI posture

CoGA stores and serves real patient genomes (families under `data/`), so it is a
PHI system. This is a point-in-time posture review of access control, audit
logging, encryption, and the deployment path, plus the hardening landed
alongside it and the items that remain (mostly deployment/infrastructure, which
the application code cannot enforce on its own).

Legend: ✅ enforced in code · 🟡 partial / config-dependent, **or codified in Terraform
but not yet applied to a live project** · ⛔ not yet done (deployment responsibility).

> Most §3–§4 items sit in that middle state. The GCP target under [`terraform/`](../terraform/)
> is written and reviewable but has **never been applied**, so it carries no deployed
> evidence — do not read 🟡 as "in force".

## 1. Authentication & RBAC

- ✅ **AuthN.** JWT bearer (HS256) with optional Azure AD; local JWT fallback is
  restricted to admins. See `backend/app/dependencies.py`
  (`get_current_user`, `get_current_admin_user`). Roles: `admin`/`superuser` vs
  `viewer` (`ADMIN_ROLES` in `metadata_service.py`).
- ✅ **AuthZ is project-scoped.** Every family/sample/variant endpoint resolves
  access through one checkpoint:
  `build_family_metadata_context` → `get_accessible_family_mapping` →
  `_ensure_user_can_access_metadata_projects` (and the sample equivalent). A
  non-admin may only reach families/samples whose project they belong to; admins
  bypass scoping. List endpoints filter at the SQL level
  (`list_family_records(metadata_project_ids=…)`), not by post-filtering.
- ✅ **Admin-gated mutations.** All destructive / structure-changing operations
  (member edits, ROI, project assignment, deletions, reference-data management)
  require `get_current_admin_user`.
- ✅ **PHI download scoping.** CRAM/BAM endpoints check family + sample access
  before issuing presigned URLs (`routers/cram.py`).
- ✅ **No default secrets in prod.** `Settings.validate_security_defaults`
  refuses to start outside dev/test if `SECRET_KEY` / `POSTGRES_PASSWORD` /
  `ADMIN_PASSWORD` are still placeholders. Passwords are bcrypt-hashed.

**IDOR review:** every endpoint taking a `family_id` / `sample_id` / `project_id`
routes through the scoping checkpoint; reference data (genes, assemblies, CNV
catalogue) is intentionally unscoped (public, non-PHI). No unscoped PHI endpoint
was found.

**Landed in this change:** RBAC depth tests in
`backend/tests/test_access_control.py` covering the previously-untested
cross-user / multi-project scenarios — a viewer is denied a family in a project
they are not in, granted one that shares a project, denied with no projects, and
an admin bypasses scoping. These lock in the access boundary against regression.

**Session-token handling (XSS blast-radius).** Tokens are bearer JWTs held in
`localStorage` and sent as `Authorization: Bearer`. Rather than migrate to
HttpOnly cookies (a large change that adds CSRF surface and reworks the Azure +
analytics-beacon paths), the blast radius of a hypothetical XSS is bounded by
(a) the maximally strict CSP (`default-src 'none'`, §3/headers), and (b) a short
**2-hour token lifetime** (`ACCESS_TOKEN_EXPIRE_MINUTES`, reduced from 6h).
Immediate lockout already works — every request re-checks `is_active`, so
deactivating a user revokes access at once. The only residual is a
leaked-but-still-active token within the 2-hour window; for a single-lab internal
deployment this is an **accepted residual** (no full token-revocation denylist).

**Azure local-admin override (break-glass).** When Azure AD is configured,
`AZURE_ADMIN_OVERRIDE` (default **off**) lets an admin fall back to a
locally-signed token if Azure is unreachable. Enabling it widens the admin trust
boundary to anyone holding `SECRET_KEY`, so **keep it off in production** unless a
break-glass path is explicitly required (the prod refuse-to-start guard against a
weak `SECRET_KEY` still applies).

**Accepted residual — staff roster.** `GET /api/users` returns the user roster
(names/emails) to any authenticated user; it powers the reviewer-assignment
picker. This is **intentional and accepted**: CoGA is a **local installation in a
single clinic/lab** where all users are colleagues (an internal directory), with
no cross-tenant boundary to protect.

## 2. Access / audit logging

- ✅ **Who-accessed-what-when trail.** Request/response middleware
  (`middleware/request_logging.py`) records every authenticated request to
  `audit_log_events` (actor id/email/role, method, path, status, timestamp,
  client IP) via an async queue worker. Failed logins are tracked separately.
- ✅ **PII minimisation.** Query strings are reduced to keys by default
  (`AUDIT_LOG_QUERY_STRING_MODE=keys`); secret-like body fields are masked.
- ✅ **Append-only (new).** `04_traceability.sql` adds a trigger that
  blocks `DELETE` and `UPDATE` on `audit_log_events`, with a single carve-out for
  the `ON DELETE SET NULL` user-unlink cascade (column-agnostic jsonb diff), so
  account removal still works while the denormalised `user_email`/`user_role`
  preserve the actor. Verified against live Postgres: insert ok, update/delete
  blocked, user-deletion cascade still nulls `user_id`.
- ✅ **Durable pipeline (new — TF-13 S-5).** A full async queue no longer silently
  drops (`services/event_pipeline.py`): it applies backpressure for up to
  `AUDIT_LOG_BACKPRESSURE_TIMEOUT_SECONDS` and then writes the event synchronously,
  the worker retries failed batch writes (`AUDIT_LOG_MAX_WRITE_ATTEMPTS`), and any
  event that still cannot be persisted is logged at ERROR with its full (already
  sanitised) payload and counted (`dropped_event_count`) for alerting — never lost
  without a trace. The default bound is raised to `AUDIT_LOG_QUEUE_SIZE=10000`;
  `AUDIT_LOG_DROP_ALLOWED=true` restores the old drop-on-full behaviour for low
  overhead and is **refused outside development**.

**Remaining (deployment):**
- ⛔ **Byte-level object downloads are not backend-audited.** The backend logs
  *issuance* of a signed URL but the browser fetches bytes from the object store
  directly. The remedy is codified as a project-wide **GCS Data Access audit config**
  (`storage.googleapis.com`, DATA_READ + DATA_WRITE) — but only in the central-infra
  template (§4), so this gap is genuinely open. It also cannot bite yet:
  `storage_backend` defaults to `local`, so no GCS object reads exist to audit.
- 🟡 Request bodies log clinical payloads (only secret-like keys are masked).
  Consider PHI-field masking if bodies are retained long-term.

## 3. Encryption

- ✅ **In transit (app edge).** Presigned S3 URLs are HTTPS; production is
  expected to terminate TLS at the proxy/ingress.
- ✅ **Secrets at rest in DB.** Passwords bcrypt-hashed.
- 🟡 **In transit to datastores (TLS — S-2).** The app now supports TLS to both
  stores: set `POSTGRES_SSLMODE` (e.g. `require`/`verify-full`, passed to asyncpg)
  and `CLICKHOUSE_SECURE=true` (HTTPS; use `CLICKHOUSE_HTTP_PORT=8443`,
  `CLICKHOUSE_VERIFY=true`). Defaults stay plain for local/dev. **Codified, not yet
  applied:** the Terraform target terminates TLS on both datastores and sets these
  values — Cloud SQL runs `ssl_mode = ENCRYPTED_ONLY`, and the ClickHouse VM serves
  HTTPS on 8443 with material fetched from Secret Manager at boot.
- 🟡 **At rest (databases).** `docker-compose.yml` (dev/local) still runs
  Postgres/ClickHouse on plain Docker volumes with no encryption. The production
  path is codified but **not yet applied**: Cloud SQL and both the ClickHouse data
  and boot disks are encrypted with a **customer-managed key (CMEK)**, supplied by a
  required variable with no Google-managed fallback (`terraform/database.tf`).
- 🟡 **Object store at rest.** The app only *reads* PHI objects (there is no
  application-side upload path), so encryption is a bucket-level responsibility. The
  Terraform buckets already set **CMEK default encryption**, uniform bucket-level
  access, and `public_access_prevention = enforced` (`terraform/storage.tf`) —
  codified, not yet applied.

## 4. Object storage / deployment path & PHI scoping

The deployment **is** codified as infrastructure-as-code: [`terraform/`](../terraform/)
holds the GCP target (Cloud Run, Cloud SQL, a ClickHouse VM, GCS, Cloud Armor). It has
**never been applied** — no GCP project is configured and the `deploy` job skips on every
run — so everything below is *written and reviewable, without deployed evidence*.

- **Buckets (implemented, `storage.tf`).** Two buckets — PHI (family CRAM/BAM +
  packages) and refdata — both with uniform bucket-level access,
  `public_access_prevention = enforced`, `force_destroy = false`, and **CMEK** default
  encryption.
- **IAM least privilege (implemented, `storage.tf`).** The backend runtime service
  account holds `roles/storage.objectViewer` on the PHI bucket only — read-only, and a
  resource-level grant rather than a project-wide one. The app has no upload path.
  **Residual:** the grant is bucket-wide, not prefix-scoped.
- **Network (implemented, `network.tf`/`database.tf`/`cloudrun.tf`).** Custom VPC with
  **Private Google Access** (the GCP analogue of a VPC endpoint); Cloud SQL has
  `ipv4_enabled = false` over private-service-access peering; the ClickHouse VM has no
  external IP and no SSH ingress; both Cloud Run services are ingress-restricted to the
  internal load balancer, so neither is reachable on its `run.app` URL.
- **Presigned URLs** (`S3_PRESIGN_EXPIRY_SECONDS`, default 1h) are bearer tokens —
  anyone with the link can fetch within the TTL. Keep the TTL short; rely on the
  per-object scope and the access checks that precede issuance.
- **Secrets (implemented, `secrets.tf`/`cloudrun.tf`).** Five region-pinned Secret
  Manager containers; values are added out-of-band so plaintext never passes through
  Terraform variables, and Cloud Run injects them **by reference**, never as literals.
  **Residual:** no rotation automation — rotating `SECRET_KEY` is still a manual act.
- **Observability (defined elsewhere, ⛔ not in force).** The GCP equivalent of
  CloudTrail data events is a project-wide **GCS Data Access audit config**
  (`storage.googleapis.com`, DATA_READ + DATA_WRITE). It needs project-IAM-admin
  rights, so it lives in the central-infra template
  `terraform/main-repo-reference/coga-prerequisites.tf.example` — another repository,
  still an `.example`. This is what would close the byte-level audit gap in §2, and it
  remains open.

## 5. CI enforcement of the gates

Two workflows enforce the gates on every PR and push to `main`:

- **`ci.yml`** — `backend` (`pytest`), `frontend` (`tsc` + `eslint` + `vitest`),
  `smoke` + `e2e` + `e2e-playwright` (real Postgres + ClickHouse), `sbom`
  (CycloneDX), and `catalogue` (test-overview in sync).
- **`security.yml`** — `deps` (blocking `pip-audit --require-hashes` + production
  `npm audit`), `secret-scan` (the gitleaks **binary**, full-history — the licensed
  `gitleaks-action` is not usable under the org), and `codeql` (Python + JS/TS SAST,
  per-PR diff baseline).

All ten checks are **required status checks** on `main` with **strict**
(up-to-date-before-merge) enforcement, so a failing test, a known-vulnerable or
unpinned dependency, a committed secret, or a newly-introduced code-scanning alert
blocks merge (**closes S-6**). Every suppression is recorded in
[SECURITY-AUDIT-ALLOWLIST.md](../SECURITY-AUDIT-ALLOWLIST.md).

## Summary

The application-layer posture is solid and was independently re-audited in 2026-06
(multi-agent review of authz/IDOR, auth, injection, SSRF/upload): consistent
project-scoped RBAC with **no exploitable cross-project IDOR**, a durable
append-only audit trail, authenticated reference endpoints, per-IP signup
throttling, input-size / decompression / path-traversal hardening, PyJWT-based
tokens, and a refuse-to-start guard against default secrets. The supply-chain and
code-scanning gates are required in CI — **S-5 (audit durability), S-6 (required
checks) and S-7 (dependency pinning) are closed**.

The remaining open items are deployment-level and live in the infrastructure layer, not
the application. Four of the five are now **codified in [`terraform/`](../terraform/) but
never applied to a live project**, so what remains for them is the first apply plus
captured evidence: encryption at rest and TLS for the datastores (**S-1/S-2**), secrets
management (**S-3**), and bucket policy / least-privilege IAM / network posture
(**S-8**). **S-4** (byte-level download audit) is the exception and is genuinely open —
its audit config lives only in the central-infra template, and PHI is not served from
GCS yet. See §3–§4 here and [TF-13 §3](regulatory/TF-13-cybersecurity.md).
