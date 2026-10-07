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
- **AuthN:** JWT bearer (HS256 local, RS256 Azure) via **PyJWT**, optional Azure AD; local fallback restricted to admins; algorithm allow-list enforced (no `alg=none`/confusion).
- **AuthZ:** project-scoped RBAC enforced at one checkpoint on every PHI endpoint; SQL-level filtering, not post-filtering; admin-gated mutations. A 2026-06 review found no exploitable cross-project PHI access (IDOR); all reference and genomic-data endpoints require authentication.
- **Accountability:** append-only (immutability-trigger-protected) HTTP audit log of who-accessed-what-when, which cannot be switched off outside development, nor kept from recording a request by what the request carries (a value Postgres cannot store, such as a NUL from `%00` in the URL, is written as a flagged escape); durable async pipeline (S-5), whose backlog and unpersisted events `GET /metrics` exposes (token-guarded, never routed by the load balancer, no identifiers in its labels) and Cloud Monitoring alerts on, together with a damaged variant store, server errors, stuck imports and an uptime check (`terraform/monitoring.tf`; pending the first apply); failed-login throttling **and per-IP signup throttling**; PII minimization (query-key-only, secret masking; no request body or error text in the application log, an error named by its kind).
- **Input hardening (DoS/traversal):** bounded upload read + decompression (anti gzip-bomb, HTTP 413 past a configurable cap); capped variant `page_size`; family-package manifest paths contained to the authorised package root (no arbitrary host-file read); a control character in a path parameter or a family or sample ID query value, and a NUL from `%00` in any query value (which failed the lookup with a 500), refused with 400 at sign-in, before any lookup, the same answer for every caller.
- **Secrets/integrity:** refuse-to-start on default or weak secrets in prod, on an integrity-anchor signing key equal to `SECRET_KEY`, on CORS settings that admit a loopback origin or any site, and on a build without its commit (`GIT_SHA`, recorded in every signed report); the image carries only the two scripts the backend runs, no test seeder; bcrypt password hashing; content-hashed immutable clinical sign-out and audit (result integrity).
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
| S-3 | Secrets management | 🟡 **In IaC, pending first apply.** Six application secrets and the ClickHouse TLS key and certificate live in Secret Manager with region-pinned replication. Cloud Run reads them by reference, never as literals; the ClickHouse VM fetches its password and TLS material at boot; the application secrets' values are added outside Terraform (`terraform/secrets.tf`, `tls.tf`, `cloudrun.tf`). The backend refuses to start outside development when `INTEGRITY_ANCHOR_SIGNING_KEY` is the same value as `SECRET_KEY`, so the anchor key and the session-token secret are always two secrets. **Residual actions:** (a) **no rotation automation**, so rotating `SECRET_KEY` remains a manual, unscheduled act; (b) the Cloud SQL user password, and the ClickHouse CA and server private keys that Terraform generates, sit in Terraform **state**, protected only by keeping the state bucket private and CMEK-encrypted ([terraform/README.md](../../terraform/README.md)). |
| S-4 | Byte-level PHI download audit | 🔲 **Open.** GCS Data Access audit logs (`storage.googleapis.com` DATA_READ/DATA_WRITE) are specified only in the **central-infra template** (`terraform/main-repo-reference/coga-prerequisites.tf.example`), which belongs to another repository; CoGA's own configuration does not set them. The buckets have no access logging of their own, and the load-balancer logs are sampled and are **not** the clinical audit trail. `storage_backend` still defaults to `local`, so there are no GCS object reads to audit yet. **Action:** apply the central-infra template **and** set `storage_backend` to `gcs`. |
| S-5 | Audit-queue durability | ✅ Done — a full async queue applies backpressure then writes the event synchronously; the worker retries batch writes and records (never silently drops) any unpersistable event at ERROR with its payload; the queue bound is `AUDIT_LOG_QUEUE_SIZE` (default 10000); silent drops are refused in production (`AUDIT_LOG_DROP_ALLOWED=false`), and so is switching the request and UI-event logs off (`AUDIT_LOG_MODE=off`). What an event holds cannot make it unpersistable or fail its batch: a value Postgres cannot store (a NUL, half a surrogate pair, `NaN`) is written as an escape and named in `_escaped`; a failed write is logged by its error type and SQLSTATE, not by the error's text, which quotes the row. See [security-posture.md](../security-posture.md) §2. |
| S-6 | Branch-protection required checks | ✅ Done — see [TF-18 §6](TF-18-change-configuration-management.md). |
| S-7 | Dependency pinning | ✅ Done — backend deps are hash-locked (`pip-compile --generate-hashes`, reproduced in Docker for the backend's Python on amd64) and audited by a **blocking** `pip-audit --require-hashes` gate; the frontend is `package-lock.json` + a blocking `npm audit` (production tree). Dependabot is on; the JWT stack was migrated `python-jose` → PyJWT to drop the no-fix `ecdsa` advisory (see TF-08 / SECURITY-AUDIT-ALLOWLIST.md). |
| S-8 | Network posture | 🟡 **In IaC, pending first apply.** Custom VPC; Cloud SQL on a private IP only, accepting encrypted connections only; the ClickHouse VM has no external IP and **no SSH ingress**, and its firewall admits only the TLS port (8443) from the Cloud Run connector range and its own subnet; egress through Cloud NAT with Private Google Access; both Cloud Run services are reachable only through the load balancer; buckets with uniform access and public access prevention; Cloud Armor with adaptive DDoS protection, per-IP rate limiting and the OWASP CRS WAF (`terraform/network.tf`, `database.tf`, `cloudrun.tf`, `storage.tf`, `armor.tf`). **Residual actions:** go-live switches 1, 3 and 4 below; the PHI bucket read grant is bucket-wide (`objectViewer`), not per prefix. See [security-posture.md](../security-posture.md) §4. |

**Go-live switches.** Four settings ship off by default. Each is switched on in its own
change-controlled deployment before clinical go-live
([deployment-gcp.md §12.7–§12.10](../deployment-gcp.md)):

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

## 4. Secure development lifecycle (IEC 81001-5-1)
- Security requirements captured in the SRS ([TF-09a §3.7](TF-09a-software-requirements-specification.md), REQ-SEC) and traced (RTM).
- Secure coding & review: changes land through pull requests gated by the required status checks, and security-relevant dependencies are flagged (TF-08 §A). Independent review is not yet mechanically enforced ([TF-18 §6](TF-18-change-configuration-management.md)); this item must not be read as evidence that every PR was independently reviewed.
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
- The `sbom` job is **not** a required status check, and the release build (`build.yml`, which
  runs on `release: published`) makes no SBOM: the release's SBOM comes from the CI run of the
  tagged commit. Capturing it into the technical file is therefore a **manual archiving step**
  ([`RELEASING.md`](../../RELEASING.md), [TF-18](TF-18-change-configuration-management.md)), and
  the artifact expires after 90 days if not archived.

## 6. Vulnerability management
- **Monitoring:** Dependabot (in use) + CVE feeds for layer-A SOUP, especially security-critical items (`PyJWT`/`cryptography`, `bcrypt`, `axios`, FastAPI/Starlette, drivers).
- **Triage:** assess each advisory for exploitability in CoGA's deployment and impact on safety/PHI; severity-rank.
- **Remediation:** patch under change control (TF-18) with CI + review; emergency path for actively-exploited criticals.
- **Receiving reports:** a public [`SECURITY.md`](../../SECURITY.md) states the intake route — GitHub **private vulnerability reporting** (enabled on the repository), which keeps a report confidential to the maintainers until an advisory is published. It also sets the scope of a report, forbids attaching real patient data to a report, and directs suspected patient-safety incidents to the vigilance route (TF-17) rather than a GitHub advisory.
- **Disclosure/coordination:** the contact is the **project lead and developer (bjorn.menten@ugent.be)**, stated in [`SECURITY.md`](../../SECURITY.md) alongside the GitHub private-reporting route, with a five-working-day acknowledgement aim. **🔲 INPUT NEEDED** — formal response/remediation targets and the UZ Gent IT security escalation path still to be agreed and aligned with vigilance (TF-17); due with the first beta release.

## 7. Minimum IT/security requirements for operation (IVDR Annex I §16.4)
Stated in the IFU: [TF-15 §6](TF-15-instructions-for-use.md).

## 8. Records
Threat model, SBOMs, vulnerability triage log, security test results, and security-relevant
change records are retained per the CMGG QMS.
