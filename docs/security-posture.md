# Security & PHI posture

CoGA is built to hold patient genomes, so it is treated as a system that handles protected
health information (PHI). The data it holds today is synthetic. This page describes the
security controls in the application and the state of the deployment items. The deployment
items S-1 to S-8 are tracked in [TF-13 §3](regulatory/TF-13-cybersecurity.md); the steps
that put them in place are in [deployment-gcp.md](deployment-gcp.md).

Legend: ✅ enforced in code · 🟡 partial, or depends on configuration or a first deployment
· ⛔ not done.

## 1. Authentication & RBAC

- ✅ **Sign-in.** JWT bearer tokens (HS256), with optional Azure AD. Without Azure, local
  tokens work for everyone; with Azure, a local token is accepted only from an admin and only
  with `AZURE_ADMIN_OVERRIDE` on (below).
  See `backend/app/dependencies.py` (`get_current_user`, `get_current_admin_user`). Roles are
  `admin`, `superuser` and `viewer`; every admin check goes through `ADMIN_ROLES`
  (`services/access_control.py`), so a superuser is an admin everywhere, and
  `test_admin_role_checks.py` fails on any comparison with the literal `"admin"`.
- ✅ **Access is scoped by project.** Every family, sample and variant endpoint resolves
  access through one checkpoint: `build_family_metadata_context`
  (`services/family_metadata_context.py`) → `get_accessible_family_mapping`
  (`services/metadata_service.py`) → `ensure_user_can_access_metadata_projects`
  (`services/access_control.py`), and the sample equivalent. A non-admin reaches only the
  families and samples of their own projects; admins see all. List endpoints filter in SQL,
  not after the fact. `backend/tests/test_access_control.py` covers the cross-user and
  multi-project cases.
- ✅ **Admin-only changes.** Structure changes and deletions of families and data (member and
  structure edits, region of interest, project assignment, family and sample deletions),
  reference data, imports and uploads require `get_current_admin_user`. A user with access
  to a family can edit its phenotypes, reviews and saved filters.
- ✅ **Scoped downloads.** The CRAM/BAM endpoints check family and sample access before they
  hand out a signed URL (`routers/cram.py`).
- ✅ **No weak secrets outside development.** `Settings.validate_security_defaults` refuses to
  start when `SECRET_KEY` is a placeholder or shorter than 32 characters, when
  `CLICKHOUSE_PASSWORD` is empty or a placeholder, when `POSTGRES_PASSWORD` or
  `ADMIN_PASSWORD` is a placeholder, or when `INTEGRITY_ANCHOR_SIGNING_KEY` is not the base64
  of a 32-byte Ed25519 seed. An unsigned integrity anchor is refused as well. Passwords are
  stored as bcrypt hashes.
- ✅ **Passwords at sign-up** need at least 15 characters (`SIGNUP_PASSWORD_MIN_LENGTH`,
  following NIST SP 800-63B-4 for a single-factor password); a shorter one gets a 422 before
  any throttling or hashing. The device owner confirmed this policy (TF-18 CR-088).
- ✅ **Reference routers need a signed-in user**, the species list included. Reference data
  (genes, assemblies, the CNV catalogue) is not project-scoped: it is public and not PHI.
- ✅ **Outbound downloads.** The HPO bootstrap downloads over HTTPS only, capped in size,
  checked to be an OBO file, optionally pinned by `HPO_ONTOLOGY_SHA256` (the digest is logged
  either way) and written atomically. The clinical-CNV knowledgebase script runs with an
  allowlisted environment: no database passwords, signing keys or cloud credentials;
  `OMIM_API_KEY` is its one credential.
- 🟡 **QC-report links** carry a 5-minute token, scoped to the family and sample, in the query
  string, because a browser navigation cannot send a bearer token. The report is served with
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store` and a sandbox CSP; the token is
  masked in the access log, and the request audit keeps query keys only. It can still sit in
  the browser history until it expires.

**IDOR review.** Every endpoint that takes a `family_id`, `sample_id` or `project_id` goes
through the checkpoint above. No unscoped PHI endpoint was found.

**Session tokens.** Tokens are bearer JWTs kept in `localStorage`. Moving them to HttpOnly
cookies would add CSRF surface and rework the Azure and telemetry paths, so the damage an XSS
could do is bounded instead by a strict CSP (`default-src 'none'`, §4) and a 2-hour token
lifetime (`ACCESS_TOKEN_EXPIRE_MINUTES`). Every request re-checks `is_active`, so
deactivating a user ends their access at once. A leaked token of a still-active user stays
valid until it expires; there is no revocation list. For a single-lab internal deployment
this is an **accepted residual**.

**Azure local-admin fallback (break-glass).** With Azure AD configured,
`AZURE_ADMIN_OVERRIDE` (default off) lets an admin sign in with a local token when Azure is
unreachable. That widens the admin trust boundary to anyone holding `SECRET_KEY`, so keep it
off in production unless a break-glass path is required.

**Accepted residual: staff roster.** `GET /api/users` returns names and e-mail addresses to
any signed-in user, for the reviewer picker. CoGA is installed for one lab, where all users
are colleagues, so there is no tenant boundary to protect.

## 2. Access / audit logging

- ✅ **Who accessed what, and when.** The request middleware
  (`middleware/request_logging.py`) records every API request in `audit_log_events`: the user
  (when signed in), method, path, status, time and client address. Failed logins are
  counted separately (`auth_login_attempts`).
- ✅ **Minimal personal data.** Query strings are reduced to their keys by default
  (`AUDIT_LOG_QUERY_STRING_MODE=keys`), and secret-like body fields are masked.
- ✅ **Append-only.** A trigger in `04_traceability.sql` blocks DELETE and UPDATE on
  `audit_log_events`. The one exception is the `ON DELETE SET NULL` unlink when a user
  account is deleted; the copied `user_email` and `user_role` keep the actor.
- ✅ **No silent loss (S-5).** A full queue applies backpressure for up to
  `AUDIT_LOG_BACKPRESSURE_TIMEOUT_SECONDS` and then writes the event directly; the worker
  retries failed writes (`AUDIT_LOG_MAX_WRITE_ATTEMPTS`); an event that still cannot be stored
  is logged at ERROR with its (already masked) payload and counted for alerting. Dropping
  events (`AUDIT_LOG_DROP_ALLOWED=true`) is refused outside development
  (`services/event_pipeline.py`).
- 🟡 **Request bodies are logged with their clinical content**; only secret-like keys are
  masked. Consider masking PHI fields if bodies are kept long-term.
- ⛔ **Byte-level downloads (S-4).** The backend logs that it issued a signed URL, but the
  browser fetches the bytes from the bucket directly. The bucket's data-access audit log is
  configured only in the central infra template, and no PHI is served from a bucket yet
  (§4).

The clinical audit trail of classifications and sign-outs is separate; see
[clinical-traceability.md](clinical-traceability.md).

## 3. Encryption

- ✅ **Passwords** are bcrypt-hashed. Signed URLs are HTTPS.
- 🟡 **To the datastores (S-2).** The backend supports TLS to both: `POSTGRES_SSLMODE`
  (for example `require` or `verify-full`) and `CLICKHOUSE_SECURE=true` with
  `CLICKHOUSE_HTTP_PORT=8443` and `CLICKHOUSE_VERIFY=true`. The defaults stay plain for local
  work. The Terraform target reaches Cloud SQL through the Cloud SQL connector, on an
  instance that accepts encrypted connections only, and serves ClickHouse over HTTPS on 8443
  with a private CA the backend verifies.
- 🟡 **At rest (S-1).** Docker Compose, for local work, keeps Postgres and ClickHouse on plain
  Docker volumes. The Terraform target encrypts Cloud SQL, both ClickHouse disks and both
  buckets with a customer-managed key (CMEK), which is a required variable with no fallback.
- ✅ **In transit at the edge.** The HTTPS load balancer accepts TLS 1.2 and later, with the
  `MODERN` profile.

## 4. Object storage / deployment path & PHI scoping

The deployment is written as Terraform in [`terraform/`](../terraform/) (Cloud Run, Cloud SQL,
a ClickHouse VM, buckets, Cloud Armor), but it has never been applied: there is no GCP
project yet. Each item below is therefore written and reviewable, without deployed evidence.

| Item | State |
| --- | --- |
| S-1 encryption at rest | 🟡 CMEK on the database, the disks and both buckets, in Terraform |
| S-2 TLS to the datastores | 🟡 supported by the app; set in Terraform |
| S-3 secrets | 🟡 six Secret Manager secrets, injected into Cloud Run by reference; values added by hand; no rotation automation |
| S-4 byte-level download audit | ⛔ open: configured only in the central infra template, and PHI is not yet served from a bucket |
| S-8 network | 🟡 private database and ClickHouse, no SSH, Cloud Run reachable only through the load balancer, Cloud Armor; the WAF enforcement, the institutional IP allowlist and the ClickHouse egress lockdown are go-live switches |

What the application relies on there:

- **Buckets.** A PHI bucket (family data and packages) and a reference-data bucket, both with
  uniform bucket-level access, public access prevention and CMEK.
- **Least privilege.** The backend's service account holds `roles/storage.objectViewer`
  (read-only) on both buckets. The reference-data bucket is mounted read-only at
  `/data/ref-data`, and nothing in the app writes to either bucket; reference files are loaded
  by the operator. The grant covers the whole bucket, not a prefix.
- **Signed URLs** (`S3_PRESIGN_EXPIRY_SECONDS`, default 1 hour; GCS uses the same setting)
  are bearer links: anyone who has one can fetch the object until it expires. Keep the time
  short; access is checked before a URL is issued.
- **Client address.** Behind the load balancer the backend takes the client address
  `TRUSTED_PROXY_HOPS` entries from the right of `X-Forwarded-For` (Terraform sets 2), never
  the left-most entry, which a client can set. The audit log and the sign-up and login
  throttles use it.

### 4a. Web tier: the single-page app and its `/api` proxy

- ✅ **Logout leaves nothing behind.** It flushes the pending UI telemetry, clears the query
  cache (which holds family data) and only then the session. Login starts with an empty cache.
- ✅ **Encoded path segments.** API paths are built with `apiPath` (`lib/apiPath.ts`), which
  percent-encodes every identifier, so an imported id such as `../families/F1` cannot
  redirect a call. Query strings go through `raw()`.
- ✅ **Escaped tooltips.** The d3 tooltips built as HTML escape their values
  (`lib/escapeHtml.ts`).
- ✅ **Proxy robustness** (`frontend/server.mjs`, `proxyRequest.mjs`). A reset mid-stream ends
  that response instead of crashing; the backend must start answering within
  `API_PROXY_TIMEOUT_MS` (else 504); a client that goes away aborts the upstream request; the
  502/504 body does not name the internal backend.
- ✅ **Forwarded headers.** The proxy sends the backend one clean `X-Forwarded-For` (its peer,
  or what the trusted proxy in front of it added) plus `X-Forwarded-Proto` and `-Host`, so a
  browser cannot choose the address the backend throttles and audits. On Cloud Run the load
  balancer sends `/api` straight to the backend; this proxy is the path for Docker Compose.
- ✅ **Headers** (`frontend/securityHeaders.mjs`): a strict CSP, and a `Permissions-Policy`
  that denies the camera, microphone, geolocation, payment, USB and the other device APIs.
- 🟡 **CSP `connect-src`** is `'self' https:` by default, because IGV loads genomes from a
  changing set of hosts and a signed CRAM URL points at the object store. A deployment that
  knows its hosts narrows it with `CSP_CONNECT_SRC`. `style-src` keeps `'unsafe-inline'`
  because IGV injects inline styles.
- ✅ **Docker Compose** publishes Postgres, ClickHouse and the backend on loopback only, and
  the frontend container gets only the settings its server reads.

## 5. CI enforcement of the gates

Every pull request and push to `main` runs the gates in `ci.yml` (backend, frontend, smoke,
e2e, e2e-playwright, catalogue, plus coverage and the SBOM) and `security.yml` (`deps`:
blocking `pip-audit --require-hashes` and production `npm audit`; `secret-scan`: gitleaks over
the full history; `codeql` for Python and JavaScript/TypeScript). Ten of these are required
status checks on `main` with strict, up-to-date-before-merge enforcement (S-6); coverage and
the SBOM are not. The list is in [CONTRIBUTING.md](../CONTRIBUTING.md); the policy and its
open gaps are in [TF-18 §6](regulatory/TF-18-change-configuration-management.md).
`build.yml` checks the Terraform (`fmt`, `validate`) on pull requests and deploys from `main`.
Every suppressed advisory is recorded in
[SECURITY-AUDIT-ALLOWLIST.md](../SECURITY-AUDIT-ALLOWLIST.md).

## Summary

The application layer applies project-scoped access consistently, with no cross-project IDOR
found, keeps a durable append-only audit trail, needs a signed-in user for reference data,
throttles sign-ups and logins, bounds input sizes, decompression and paths, and refuses to
start on weak secrets. S-5 (audit durability), S-6 (required checks) and S-7 (dependency
pinning) are closed. The open items are deployment-level: S-1, S-2, S-3 and S-8 are written
in Terraform and wait for the first deployment and its evidence; S-4 is open. See
[TF-13 §3](regulatory/TF-13-cybersecurity.md).
