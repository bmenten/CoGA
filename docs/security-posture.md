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
  (`services/metadata_service.py`), which asks `user_can_access_metadata_projects`
  (`services/access_control.py`), and the sample equivalent `get_accessible_sample_mapping`. A
  non-admin reaches only the families and samples of their own projects; admins see all. List
  endpoints filter in SQL, not after the fact. `backend/tests/test_access_control.py` covers
  the cross-user and multi-project cases.
- ✅ **A record outside your projects looks unknown.** A family or sample of another project
  answers exactly like an ID nobody uses: `404 {"detail":"Family not found"}` or
  `404 {"detail":"Sample not found"}`, raised at the checkpoint for both cases. A viewer
  therefore cannot find out which family and sample IDs exist in other projects. A project ID
  given to the Family Builder or the gene profile is treated the same way
  (`404 Project not found`). The request audit still tells the two apart: the
  `audit_log_events` row of such a request names the kind in `request_meta.record_hidden`
  (`family`, `sample` or `project`), which the response never carries
  (`main._answer_record_not_visible`).
  `backend/tests/e2e/test_e2e_inaccessible_records_look_unknown.py` asks every route with a
  family or sample ID in its path, on real Postgres, for a family of another project and for
  an unknown one, and requires the same status and body. Refusals because of the caller's
  role (`403 Admin access required`) are unchanged: they say nothing about the record.
- ✅ **No control characters in what a request looks up.** A `%00` in a URL arrives decoded,
  and Postgres cannot compare a NUL, so a request that looked up a family's path
  (`/api/families/FAM%00X`) or any other path or query value with one failed with a 500.
  `get_current_user` answers 400, naming the parameter, when a path parameter, a query
  parameter's name or a `family_id` or `sample_id` query value holds a C0 control character
  or DEL, which no family or sample ID may hold (`services/family_identifiers.py`), and when
  any other query value holds a NUL. A gene or interval list typed one per line keeps its line
  breaks. The check runs once the caller is signed in, so the 401 comes first and the
  refusal's audit row names the caller, and before any route looks a value up, with the same
  answer for every caller: it reveals nothing about which families exist or who may see them.
  Every route that takes a path or query value signs its caller in, except the QC-report
  download, which checks its own signed link first (`test_request_control_characters.py`).
- ✅ **Admin-only changes.** Structure changes and deletions of families and data (member and
  structure edits, region of interest, project assignment, family and sample deletions),
  replacing a family's annotation manifest, reference data, imports and uploads require
  `get_current_admin_user`. A user with access to a family can edit its phenotypes, reviews
  and saved filters.
- ✅ **A UUID a request names a record by is read once.** asyncpg's uuid codec takes nothing
  but hex digits and hyphens, so a path or query value that was no UUID
  (`/api/admin/data/files/PROBEX/download`), or a UUID in braces or after `urn:uuid:` that a
  route had checked with `uuid.UUID` and then bound as received, failed the request with a
  500. Every such value (a raw file, an import job, an HPO annotation, a NIPT artifact, a
  panel, a filter preset, a user, a project, a species, an assembly, a clinical CNV, the
  `panel_id` filter) is now read by `core/sql.py` (`canonical_uuid`, `require_uuid`): one that
  spells no UUID is answered as an unknown record is, with the route's own 404 or the 400 it
  answers an invalid id with, before any query; one that does is bound as its canonical text,
  so each spelling of a record's id names that record. What `uuid.UUID` merely tolerates (an
  underscore, a space or a sign among the digits, `0x`, digits of another script) spells no
  UUID: read as a number, it would name a different-looking record. The value is read where
  the record is looked up, after the sign-in, the role and the family's project checks, so the
  answer is the one an unknown record gets, whether or not a record of that id exists in a
  project the caller cannot see (`test_request_uuid.py`,
  `e2e/test_e2e_request_malformed_uuid.py`). The ids a project, assembly, family-project or
  family-status body carries are read the same way, and so are the `assembly_id` of a NIPT
  artifact (its add, auto-seed and table import) and the `project_id` of an import request:
  one that spells no UUID or names no record is refused as an unknown assembly or project
  (404) before anything is written, so the artifact list's audit chain holds no event for it,
  and each audit event records the canonical id (`test_request_body_uuid.py`,
  `e2e/test_e2e_request_body_uuid.py`, which also sweeps every UUID a request body carries).
- ✅ **Scoped downloads.** The CRAM/BAM and signal-track endpoints check family and sample
  access before they hand out a signed URL (`routers/cram.py`, `routers/signal_tracks.py`).
  They sign a location the import recorded only when it names an object in the configured
  bucket below `FAMILY_IMPORT_ROOTS`, so a changed database row cannot point them at any
  other object.
- ✅ **No weak secrets outside development.** `Settings.validate_security_defaults` refuses to
  start when `SECRET_KEY` is a placeholder or shorter than 32 characters, when
  `CLICKHOUSE_PASSWORD` is empty or a placeholder, when `POSTGRES_PASSWORD` or
  `ADMIN_PASSWORD` is a placeholder, or when `INTEGRITY_ANCHOR_SIGNING_KEY` is not the base64
  of a 32-byte Ed25519 seed or is the same value as `SECRET_KEY` (then whoever can mint a
  session token could also sign an integrity anchor). An unsigned integrity anchor is refused
  as well. Passwords are stored as bcrypt hashes.
- ✅ **No cross-origin access outside development.** The deployed UI is served from the API's
  own origin, so the backend refuses to start while `CORS_ORIGINS` or `CORS_ORIGIN_REGEX`
  admits a loopback origin (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0`) or any site (`*`, or a
  pattern matching an unrelated origin). The development defaults admit localhost with
  credentials, which would let a page served on the user's own machine call the API as the
  signed-in user. Terraform sets the app's origin and an empty pattern.
- ✅ **Every running build names its commit.** Outside development the backend refuses to
  start without a `GIT_SHA` (7–40 hex characters), which every signed report records
  (TF-18 §2). `build.yml` and `ci/cloudbuild.backend.yaml` stamp it.
- ✅ **Metrics without identifiers, behind a token.** `GET /metrics` is off unless
  `METRICS_TOKEN` is set, then answers only that bearer token (32+ characters outside
  development, distinct from the other secrets). It sits outside `/api`, which is all the load
  balancer and the frontend server pass to the backend, so it is never served to the internet.
  Requests are labelled by route template, never by path, so no family, sample or variant
  identifier reaches a metric ([monitoring.md](monitoring.md)). In the deployment a collector
  in the backend's own Cloud Run service scrapes it over the instance's loopback, with the token
  from Secret Manager, which only the backend's account may read (`terraform/monitoring.tf`).
- 🟡 **Alerts on what matters, pending the first apply.** Cloud Monitoring policies page on a
  damaged variant store, lost accountability events and an unreachable app, and warn on a stalled
  integrity check, a backed-up audit queue, server errors, a stuck import and missing metrics; an
  uptime check fetches `/api/health` through the load balancer ([monitoring.md](monitoring.md)).
- ✅ **The image ships only the scripts it runs.** `clinical_cnv_knowledgebase.py` (the admin
  rebuild) and `import_dgv.py` (an operator import); the test seeders, one of which creates a
  sign-in with a default password, the demo loaders and the CI checks stay out of it.
- ✅ **Passwords at sign-up** need at least 15 characters (`SIGNUP_PASSWORD_MIN_LENGTH`,
  following NIST SP 800-63B-4 for a single-factor password); a shorter one gets a 422 before
  any throttling or hashing. The device owner confirmed this policy (#626).
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

**Residual: IDs that are taken.** Family and sample IDs are unique across all projects, so
creating a family refuses an ID that another project already uses (`409 Family id already
exists`, `409 Sample id already exists`). A user who may create families (the Family Builder
is open to a viewer with a project) can learn from that that the ID exists, but nothing else
about it. Closing that would take IDs scoped per project.

**Session tokens.** Tokens are bearer JWTs kept in `localStorage`. Moving them to HttpOnly
cookies would add CSRF surface and rework the Azure and telemetry paths, so the damage an XSS
could do is bounded instead by the app's CSP, which allows no inline or outside scripts
(§4a), and by a 2-hour token lifetime (`ACCESS_TOKEN_EXPIRE_MINUTES`). Every request
re-checks `is_active`, so deactivating a user ends their access at once. A leaked token of a still-active user stays
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
- ✅ **Requests for other projects' records stay visible in the audit.** Such a request is
  answered as for an unknown ID (§1), but its row carries `request_meta.record_hidden`, so the
  audit shows who asked for families, samples or projects outside their own.
- ✅ **Minimal personal data.** Query strings are reduced to their keys by default
  (`AUDIT_LOG_QUERY_STRING_MODE=keys`), and secret-like body fields are masked.
- ✅ **Append-only.** A trigger in `04_traceability.sql` blocks DELETE and UPDATE on
  `audit_log_events`. The one exception is the `ON DELETE SET NULL` unlink when a user
  account is deleted; the copied `user_email` and `user_role` keep the actor.
- ✅ **No silent loss (S-5).** A full queue applies backpressure for up to
  `AUDIT_LOG_BACKPRESSURE_TIMEOUT_SECONDS` and then writes the event directly; the worker
  retries failed writes (`AUDIT_LOG_MAX_WRITE_ATTEMPTS`); an event that still cannot be stored
  is logged at ERROR with its (already masked) payload and counted for alerting. The same holds
  without the queue, with `AUDIT_LOG_MODE=sync` or while no worker runs (before startup
  completes, after shutdown): each event is written as its request runs, and a write that fails
  is logged and counted the same way, where it used to leave a warning the alert did not count.
  Neither the request nor a batch of UI events fails because of it: the UI-event endpoint still
  answers 202, so the browser does not resend the events already stored. Outside
  development the backend refuses to start with `AUDIT_LOG_DROP_ALLOWED=true`, which drops
  events (`services/event_pipeline.py`), or with `AUDIT_LOG_MODE=off`, which writes no
  request or UI-event log at all (`core/config.py`).
- ✅ **No request escapes the audit table.** Postgres refuses a NUL, half a surrogate pair,
  `NaN` and `Infinity` in TEXT or JSONB. A signed-in user could put one in a JSON field the
  endpoint ignores, or `%00` in the URL: the action was carried out, and its row, with every
  other row of its async batch, reached only the log. Such a value is now stored as a visible
  escape, with the columns named in `request_meta._escaped` ([database.md](database.md)); the
  UI-event log does the same. A NUL in the URL is now refused at sign-in (§1), and the
  refusal's row is kept the same way, under the caller's name.
- ✅ **A failed audit write is logged by its kind.** The log line names the exception by its
  type and SQLSTATE (`describe_error` in `core/coga_logging.py`), never by the error's text,
  which quotes the statement and the row it could not insert. The row itself is logged once,
  as its masked payload (S-5).
- ✅ **No request body and no error text in the application log.** The request body is written
  only to `audit_log_events`, and so is the text of an error, which for a failed statement
  quotes its SQL and parameters: values from the request, or read for it. An unhandled error's
  500 line gives the error's kind (`describe_error`: the exception type with the driver's error
  and SQLSTATE, or ClickHouse's error code and name), the route template and the frames of
  every exception in its chain, without their messages (`describe_traceback`). Starlette raises
  the error again to uvicorn once it has answered the 500, and uvicorn logs it with its
  traceback; a filter writes that traceback the same way (`RedactServerErrorFilter`, installed
  by `main.py`). The full text stays in the request's audit row (`audit_log_events.error`),
  which only an admin reads. The other lines that log a failed query on a request path (Sample
  QC, the ClickHouse variant query and its retry) name it the same way. Every other line that
  carries an exception (`logger.exception()`, `exc_info=True`), from any logger, CoGA's or a
  library's, and at any level, is written the same way by the JSON formatter
  (`JsonLogFormatter`): the exception's kind in `error`, the frames of its chain in
  `traceback`, never its message. If the traceback cannot be written, the line still goes out
  with the kind, because a formatter that fails makes `logging` print the logged exception's
  full text to stderr. The one exception is a row the audit table cannot store: it is logged at
  ERROR with its masked payload, body and error text included, so it can be restored (S-5).
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
| S-3 secrets | 🟡 seven application secrets in Secret Manager (the metrics token among them), their values added by hand, plus the ClickHouse TLS key and certificate that Terraform generates; Cloud Run reads them by reference; no rotation automation |
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
- ✅ **Headers** (`frontend/securityHeaders.mjs`): a CSP that allows scripts only from the app
  itself (`script-src 'self'`), no framing and no plugins, and a `Permissions-Policy` that
  denies the camera, microphone, geolocation, payment, USB and the other device APIs. API
  responses carry `default-src 'none'` (`backend/app/middleware/security_headers.py`).
- 🟡 **CSP `connect-src`** is `'self' https:` by default, because IGV loads genomes from a
  changing set of hosts and a signed CRAM URL points at the object store. A deployment that
  knows its hosts narrows it with `CSP_CONNECT_SRC`. `style-src` keeps `'unsafe-inline'`
  because IGV injects inline styles.
- ✅ **Docker Compose** publishes Postgres, ClickHouse and the backend on loopback only, and
  the frontend container gets only the settings its server reads.

## 5. CI enforcement of the gates

Every pull request and push to `main` runs the gates in `ci.yml` (backend, frontend, smoke,
e2e, e2e-playwright, catalogue, plus coverage, the SBOM and a build of both production images
that pushes nothing) and `security.yml` (`deps`: blocking `pip-audit --require-hashes` and
production `npm audit`; `secret-scan`: gitleaks over the full history; `codeql` for Python and
JavaScript/TypeScript). Ten of these are required status checks on `main` with strict,
up-to-date-before-merge enforcement (S-6); coverage, the SBOM and the image build are not.
The list is in [docs/testing.md](testing.md); the policy and its open gaps are in
[TF-18 §6](regulatory/TF-18-change-configuration-management.md).
`build.yml` checks the Terraform (`fmt`, `validate`) on pull requests and deploys from `main`.
Every suppressed advisory is recorded in
[SECURITY-AUDIT-ALLOWLIST.md](../SECURITY-AUDIT-ALLOWLIST.md).

## Summary

The application layer applies project-scoped access consistently, with no cross-project IDOR
found, keeps a durable append-only audit trail, needs a signed-in user for reference data,
throttles sign-ups and logins, bounds input sizes, decompression and paths, refuses a control
character in what a request looks up, and refuses to
start on weak or shared secrets or with the request audit log switched off. S-5 (audit
durability), S-6 (required checks) and S-7 (dependency pinning) are closed. The open items are deployment-level: S-1, S-2, S-3 and S-8 are written
in Terraform and wait for the first deployment and its evidence; S-4 is open. See
[TF-13 §3](regulatory/TF-13-cybersecurity.md).
