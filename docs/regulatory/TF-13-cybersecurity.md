# TF-13 — Cybersecurity Management & SBOM

| Field | Value |
| --- | --- |
| Document ID | TF-13 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead + UZ Gent IT security› |
| Approver | ‹Lab director / CISO delegate› |
| Date | 2026-06-25 |
| Standards | IEC 81001-5-1 (health software security lifecycle); MDCG 2019-16 (medical device cybersecurity); IVDR Annex I §16.2 & §16.4 |

> Cybersecurity for CoGA protects (a) **patient-data confidentiality/integrity** (PHI:
> genomes) and (b) the **integrity of the clinical result**. This file builds directly on
> the existing [security-posture.md](../security-posture.md) review; it adds the lifecycle,
> SBOM, and vulnerability-management framing the standards expect. Data-protection (GDPR)
> aspects are in [TF-14 DPIA](TF-14-dpia.md).

---

## 1. Security risk management
Security risks are managed within the ISO 14971 process (TF-06): hazard **H11
(unauthorized access / integrity / confidentiality breach)** and the integrity of clinical
outputs. Threats are assessed for impact on safety (wrong/leaked result) as well as on
confidentiality and availability.

The **ISO/IEC 27001** confidentiality-integrity-availability (CIA) analysis that the governing
SOP H11.1-OP5 requires is part of the risk analysis ([TF-06](TF-06-risk-management-plan.md)).
IEC 81001-5-1 / MDCG 2019-16 below give the device-specific security detail.

## 2. Security capabilities already implemented (from security-posture.md)
- **AuthN (SEC-4):** at go-live users sign in with their hospital or university Microsoft account (Microsoft Entra ID / Azure AD), which carries the password policy, MFA (Conditional Access), password reset and leavers; the browser uses MSAL (`@azure/msal-browser`, authorization code flow with PKCE) and sends Entra ID's RS256 access token for CoGA's API scope, which the backend checks via **PyJWT** against the directory's published keys: version 2.0, CoGA's own directory (`iss`, `tid`), CoGA's API asked for by CoGA (`aud`, `azp`), CoGA's scope (`scp`, so no application token), lifetime. Accounts are bound to the Entra object ID (`oid`), made at the first sign-in as a viewer in no project, never linked to an existing account by e-mail address. One local account, the emergency (break-glass) administrator, signs in with its password only while `AZURE_ADMIN_OVERRIDE` is on (default off); each use is logged and flagged in the request audit. A half Azure configuration is refused at startup. Without Azure AD (development) the password sign-in issues HS256 tokens. Algorithm allow-list per path (no `alg=none` or key confusion). Global roles are set in the UI by an administrator (not their own, not the emergency administrator's), each change recorded with its before and after in the request audit.
- **AuthZ:** project-scoped RBAC enforced at one checkpoint on every PHI endpoint; SQL-level filtering, not post-filtering; admin-gated mutations; a per-project access level (analyst: read and change; reader: read only), a reader's change and a reader's request for the clinical audit trail refused at the same checkpoint (SEC-1); a case assigned to, and recorded as reviewed by, an active analyst of one of the family's projects or an active admin only, a save naming anyone else refused (422). A 2026-06 review found no exploitable cross-project PHI access (IDOR); all reference and genomic-data endpoints require authentication.
- **Accountability:** append-only (immutability-trigger-protected) HTTP audit log of who-accessed-what-when, and an append-only UI-event log beside it (SEC-7), which cannot be switched off outside development, nor kept from recording a request by what the request carries (a value Postgres cannot store, such as a NUL from `%00` in the URL, is written as a flagged escape); durable async pipeline (S-5), whose backlog and unpersisted events `GET /metrics` exposes (token-guarded, never routed by the load balancer, no identifiers in its labels) and Cloud Monitoring alerts on, together with a damaged variant store, server errors, stuck imports and an uptime check (`terraform/monitoring.tf`; pending the first apply); failed-login throttling **and per-IP signup throttling**; PII minimization (query-key-only, secret masking; no request body or error text in the application log, an error named by its kind).
- **Input hardening (DoS/traversal):** bounded upload read + decompression (anti gzip-bomb, HTTP 413 past a configurable cap); capped variant `page_size`; family-package manifest paths, and the file names Discover builds from a family or sample ID, contained to the authorised package root (no arbitrary host-file read: an ID that is an absolute path or climbs out with `..` names no file Discover looks up), and so is the manifest itself (one that is a symlink out of the package is neither read nor written through); the package list shows only the package folders the import's folder guard takes (one that is a symlink leading out of the import roots, or one that loops, is neither listed nor looked into); a manifest path starting with `~` refused rather than expanded to a home folder, and a symlink loop in a package path refused rather than failing with a 500; the files the CRAM/BAM, signal-track and QC-report endpoints look up with local storage (the alignments by their layouts, the signal files and the QC report at the path recorded on the sample), contained to the family's own folders (an ID or a recorded path that climbs out or names an absolute path elsewhere, or a symlink leading into another family's folder or out of the data directory, finds no file); the source files the admin page downloads and verifies at the path recorded in `raw_import_files`, contained to the local import roots, the staging folder of a bucket package and the upload folder, symlinks resolved (any other recorded path is refused with 403, and Verify asks the object store only about an object below the remote import roots), and the managed copies a family or sample delete unlinks, contained to the upload folder (no arbitrary host-file read or delete through an edited row); a control character in a path parameter or a family or sample ID query value, and a NUL from `%00` in any query value (which failed the lookup with a 500), refused with 400 at sign-in, before any lookup, the same answer for every caller.
- **Secrets/integrity:** refuse-to-start on default or weak secrets in prod, on an integrity-anchor signing key equal to `SECRET_KEY`, on CORS settings that admit a loopback origin or any site, and on a build without its commit (`GIT_SHA`, recorded in every signed report); the image carries only the two scripts the backend runs, no test seeder; bcrypt password hashing; content-hashed immutable clinical sign-out and audit (result integrity): the hash-chained clinical audit trail records every review save (a compound-het pair review included), every change of a family's pedigree and members with its before and after, and every sign-out, and the pedigree's structure versions are append-only and hash-chained per family (SEC-7).
- **PHI download scoping:** CRAM/BAM presigned URLs issued only after family+sample access checks.
- **Supply chain:** hash-locked, audited dependencies (S-7); CodeQL SAST + gitleaks secret-scan + dependency-audit as **required** CI gates (S-6).

## 3. Open security items (deployment) — must close before clinical go-live
Also summarised in [security-posture.md](../security-posture.md).

> **Status vocabulary.** Several controls below are **implemented in Terraform under
> `terraform/` but have never been applied** — no GCP project is configured, the `deploy` job
> skips on every run, and no state exists. Those read **🟡 In IaC, pending first apply**: the
> configuration is written and reviewable, but there is no deployed evidence, so none may be
> claimed as closed. Closing them requires the first apply plus captured evidence.

| # | Item | Action |
| --- | --- | --- |
| S-1 | Encryption at rest for Postgres/ClickHouse | 🟡 **In IaC, pending first apply.** CMEK is applied uniformly and is mandatory — a required, regex-validated variable with no default and no Google-managed-key fallback (`terraform/main.tf`, `variables.tf`). Covers Cloud SQL Postgres, the ClickHouse data disk and boot disk, and both GCS buckets (`database.tf`, `storage.tf`). **Action:** apply, then capture the key resource names and per-resource evidence. |
| S-2 | TLS between services & to datastores | 🟡 **In IaC, pending first apply.** Postgres is reached through the Cloud SQL connector (mTLS) with `sslmode=require`; ClickHouse over HTTPS (port 8443) with a private CA that the backend verifies (`terraform/cloudrun.tf`, `tls.tf`). Local development defaults to plain connections. **Action:** apply, then capture evidence. |
| S-3 | Secrets management | 🟡 **In IaC, pending first apply.** Six application secrets, the metrics-scrape token and the ClickHouse TLS key and certificate live in Secret Manager with region-pinned replication. Cloud Run reads them by reference, never as literals; the ClickHouse VM fetches its password and TLS material at boot; the values of the application secrets and the metrics token are added outside Terraform (`terraform/secrets.tf`, `monitoring.tf`, `tls.tf`, `cloudrun.tf`). The backend refuses to start outside development when `INTEGRITY_ANCHOR_SIGNING_KEY` is the same value as `SECRET_KEY`, so the anchor key and the session-token secret are always two secrets. The Terraform state and plan hold no password or private key: Terraform reads the Cloud SQL owner's password through an ephemeral resource and sets it through a write-only argument, and generates the ClickHouse CA and server keys as ephemeral values that reach the certificates and Secret Manager only through write-only arguments (`terraform/secrets.tf`, `database.tf`, `tls.tf`; Terraform 1.11+, google provider 8.x). **Residual actions:** (a) **no rotation automation**, so rotating `SECRET_KEY` remains a manual, unscheduled act; (b) an environment applied before that provider upgrade (OPS-10) keeps the old owner password and TLS keys in its older state versions until the one-time clean-up ([deployment-gcp.md §12.11](../deployment-gcp.md)), though none had been applied when it was made; (c) the CA's key is kept nowhere, so every ClickHouse certificate re-issue brings a new CA and needs a VM restart ([deployment-gcp.md §12.4](../deployment-gcp.md)). |
| S-4 | Byte-level PHI download audit | 🔲 **Open.** GCS Data Access audit logs (`storage.googleapis.com` DATA_READ/DATA_WRITE) are specified only in the **central-infra template** (`terraform/main-repo-reference/coga-prerequisites.tf.example`), which belongs to another repository; CoGA's own configuration does not set them. The buckets have no access logging of their own, and the load-balancer logs are sampled and are **not** the clinical audit trail. `storage_backend` still defaults to `local`, so there are no GCS object reads to audit yet. **Action:** apply the central-infra template **and** set `storage_backend` to `gcs`. |
| S-5 | Audit-queue durability | ✅ Done — a full async queue applies backpressure then writes the event synchronously; the worker retries batch writes, then writes a failed batch's events one at a time so that one event the database refuses costs only itself, and records (never silently drops) any unpersistable event at ERROR with its payload; so does an event written without the queue (`AUDIT_LOG_MODE=sync`, or no worker running), whose failed write counts toward the same alert and fails neither its request nor a UI-event batch, which is still answered 202; the queue bound is `AUDIT_LOG_QUEUE_SIZE` (default 10000); silent drops are refused in production (`AUDIT_LOG_DROP_ALLOWED=false`), and so is switching the request and UI-event logs off (`AUDIT_LOG_MODE=off`). What an event holds cannot make it unpersistable or fail its batch: a value Postgres cannot store (a NUL, half a surrogate pair, `NaN`) is written as an escape and named in `_escaped`, and a UI event's `occurred_at` more than a day before or five minutes after the server received it is stored as the receive time, the client's value kept in `detail._client_occurred_at`; a failed write is logged by its error type and SQLSTATE, not by the error's text, which quotes the row. See [security-posture.md](../security-posture.md) §2. |
| S-6 | Branch-protection required checks | ✅ Done — see [TF-18 §6](TF-18-change-configuration-management.md). |
| S-7 | Dependency pinning | ✅ Done — backend deps are hash-locked (`pip-compile --generate-hashes`) and audited by a **blocking** `pip-audit --require-hashes` gate; every CI job and the image build install them on Python 3.12, where pip refuses a file whose hash differs from the lock's. That the lock is what `scripts/compile-requirements.sh` (pip-compile in Docker, Python 3.12 on amd64) would produce is **not verified**: no workflow runs the script, Dependabot updates the lock with its own pip-compile, and #894 compiled its change with Python 3.13 outside Docker, keeping the Python 3.12 header by hand (OPS-25). The frontend is `package-lock.json` + a blocking `npm audit` (production tree). Dependabot is on; the JWT stack was migrated `python-jose` → PyJWT to drop the no-fix `ecdsa` advisory (see TF-08 / SECURITY-AUDIT-ALLOWLIST.md). |
| S-8 | Network posture | 🟡 **In IaC, pending first apply.** Custom VPC; Cloud SQL on a private IP only, accepting encrypted connections only; the ClickHouse VM has no external IP and **no SSH ingress**, and its firewall admits only the TLS port (8443) from the Cloud Run connector range and its own subnet; egress through Cloud NAT with Private Google Access; both Cloud Run services are reachable only through the load balancer; buckets with uniform access and public access prevention; Cloud Armor with adaptive DDoS protection, per-IP rate limiting and the OWASP CRS WAF (`terraform/network.tf`, `database.tf`, `cloudrun.tf`, `storage.tf`, `armor.tf`). **Residual actions:** go-live switches 1, 3 and 4 below; the PHI bucket read grant is bucket-wide (`objectViewer`), not per prefix. See [security-posture.md](../security-posture.md) §4. |

**Go-live switches.** Five settings ship off by default. Each is switched on in its own
change-controlled deployment before clinical go-live
([deployment-gcp.md §12.7–§12.10 and §12.12](../deployment-gcp.md)):

1. **Enforce the web-application firewall** (`cloud_armor_waf_enforce`). Until then the OWASP
   rules only log.
2. **Run the API as the restricted database role** `coga_app` (`db_runtime_role`). Until then
   the API connects as the table owner, who can disable the append-only triggers, so the
   append-only guarantee rests on the triggers and the hash chains alone (TF-09b
   REQ-TRACE-008).
3. **Restrict the load balancer to UZ Gent/UGent networks** (`allowed_ingress_cidrs`). Until
   then the app is reachable from anywhere, with application login only.
4. **Lock down the ClickHouse VM's outbound traffic** to Google APIs
   (`clickhouse_restrict_egress`). Until then it reaches the internet through Cloud NAT.
5. **Sign in with Microsoft** (`azure_ad_tenant_id`, `azure_ad_client_id`), once IT has
   registered the app (assignment to the lab's group required, MFA by Conditional Access).
   Until then every account signs in with a local password, without MFA. The emergency
   administrator's password sign-in (`azure_admin_override`) stays off after the go-live
   deployment that bootstraps the first administrators.

**Accepted residual — integrity anchors (OPS-7, owner's decision of 2026-10-14).** A signed
integrity anchor records the head of every hash chain, so that an owner who rebuilds or
shortens a chain afterwards is detected ([clinical-traceability.md](../clinical-traceability.md),
"Tamper evidence and its limits"; TF-09b REQ-TRACE-008). Anchors are made only when an admin
asks for one: nothing schedules them, no copy of an anchor leaves the database (the
`export_anchor()` hook does nothing), verification trusts the one configured signing key, and
that key sits in Secret Manager, not in an HSM. So while the API connects as the database
owner, deleting the newest anchors goes unnoticed, and a change made after the last anchor is
covered by none. **What is accepted:** these four gaps, until after the release candidate;
the anchors stay manual. **Why:** the anchors are the outermost layer of the tamper
evidence, and the layers beneath them stay in force: the append-only triggers refuse every
update and deletion of the audit and sign-out records (only the database owner can switch
them off), the per-family hash chains expose a changed, removed or reordered record whenever
an admin verifies them, and once go-live switch 2 is on, the API's database role can neither
switch the triggers off nor delete an anchor. Choosing the schedule, the store outside the
database and the custody of the key is left until after the release candidate.
**Compensating measure:** an admin creates anchors by hand (`POST /admin/integrity/anchor`;
there is no page for it) and verifies the chains against the latest one
(`GET /admin/integrity/anchor/verify` and `/verify-chain`). **Re-evaluate** after the release
candidate: decide the schedule, the export target and the key custody, or renew this
acceptance. Pending QA approval.

## 4. Secure development lifecycle (IEC 81001-5-1)
- Security requirements captured in the SRS ([TF-09a §3.7](TF-09a-software-requirements-specification.md), REQ-SEC) and traced (RTM).
- Secure coding & review: changes land through pull requests gated by the required status checks, and security-relevant dependencies are flagged (TF-08 §A). Pull requests need no approval: the four-eyes review is QA's, per release, signed before the tag ([TF-18 §6](TF-18-change-configuration-management.md)). Only the pull requests that QA's signed review of a release includes (every change to a clinical-critical module and a sample of the rest) count as independently reviewed, so this item must not be read as evidence that every PR was.
- Verification: access-control tests (`test_access_control.py`), audit immutability tests, refuse-to-start tests; CI gates.
- Threat modeling: **🔲 ACTION** — produce a lightweight threat model (data flow and trust boundaries: browser ↔ load balancer and Cloud Armor ↔ API ↔ Cloud SQL / ClickHouse VM ↔ GCS; auth boundary; admin vs viewer) and review it per significant change.

## 5. SBOM (Software Bill of Materials)
✅ **Done for dependencies.** A **CycloneDX 1.6 JSON** inventory is generated on every build by
the `sbom` job in `.github/workflows/ci.yml`, from the hash-locked `backend/requirements.txt`
(via `cyclonedx-bom`) and `frontend/package-lock.json` (via `@cyclonedx/cyclonedx-npm`), and
uploaded as the `sbom-cyclonedx` artifact with 90-day retention. It is reproducible on demand
via [`scripts/generate-sbom.sh`](../../scripts/generate-sbom.sh) in pinned containers, and is
reconciled against the SOUP register ([TF-08](TF-08-soup-register.md)).

Three limits are stated so the artifact is not overread:

- **Container base images are not inventoried** — only the two dependency lockfiles are covered.
  **🔲 ACTION:** add a container/base-image SBOM (e.g. syft against the pushed image digests).
- Only **CycloneDX** is produced; there is no SPDX output.
- The `sbom` job is **not** a required status check. The release build (`build.yml`, which
  runs on `release: published`) makes the release's SBOMs itself, from the release commit with
  the same script, generator pins and runtimes, and attaches them with their SHA-256 sums to
  the GitHub release (job `release-sbom`), where they stay as long as the release does, not
  the 90 days of the CI artifact; the release record cites their sums
  ([`RELEASING.md`](../../RELEASING.md), [TF-18](TF-18-change-configuration-management.md)).
  Deleting the release deletes them.

## 6. Vulnerability management
- **Monitoring:** Dependabot (in use) + CVE feeds for layer-A SOUP, especially security-critical items (`PyJWT`/`cryptography`, `bcrypt`, `axios`, `@azure/msal-browser`, FastAPI/Starlette, drivers).
- **Triage:** assess each advisory for exploitability in CoGA's deployment and impact on safety/PHI; severity-rank.
- **Remediation:** patch under change control (TF-18) with CI + review; emergency path for actively-exploited criticals.
- **Receiving reports:** a public [`SECURITY.md`](../../SECURITY.md) states the intake route — GitHub **private vulnerability reporting** (enabled on the repository), which keeps a report confidential to the maintainers until an advisory is published. It also sets the scope of a report, forbids attaching real patient data to a report, and directs suspected patient-safety incidents to the vigilance route (TF-17) rather than a GitHub advisory.
- **Disclosure/coordination:** the contact is the **project lead and developer (bjorn.menten@ugent.be)**, stated in [`SECURITY.md`](../../SECURITY.md) alongside the GitHub private-reporting route, with a five-working-day acknowledgement aim. **🔲 INPUT NEEDED** — formal response/remediation targets and the UZ Gent IT security escalation path still to be agreed and aligned with vigilance (TF-17); due with the first beta release.

## 7. Minimum IT/security requirements for operation (IVDR Annex I §16.4)
Stated in the IFU: [TF-15 §6](TF-15-instructions-for-use.md).

## 8. Records
Threat model, SBOMs, vulnerability triage log, security test results, and security-relevant
change records are retained per the CMGG QMS.
