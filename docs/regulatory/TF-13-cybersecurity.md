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

Per the governing SOP **H11.1-OP5**, CMGG applies an **ISO/IEC 27001** information-security
lens across the whole lifecycle: everything around development, implementation and integration
is treated as a potential risk to the **confidentiality, integrity and availability (CIA)** of
the information, to be mitigated with appropriate measures, and this CIA risk analysis is part
of the bio-IT ingangsvalidatie dossier (H11.1-F12.2). IEC 81001-5-1 / MDCG 2019-16 below give
the device-specific security detail.

## 2. Security capabilities already implemented (from security-posture.md)
- **AuthN:** JWT bearer (HS256 local, RS256 Azure) via **PyJWT**, optional Azure AD; local fallback restricted to admins; algorithm allow-list enforced (no `alg=none`/confusion).
- **AuthZ:** project-scoped RBAC enforced at one checkpoint on every PHI endpoint; SQL-level filtering, not post-filtering; admin-gated mutations. IDOR re-verified by a 2026-06 multi-agent audit — no exploitable cross-project PHI access; all reference/genomic-data endpoints now require authentication.
- **Accountability:** append-only (immutability-trigger-protected) HTTP audit log of who-accessed-what-when; durable async pipeline (S-5); failed-login throttling **and per-IP signup throttling**; PII minimization (query-key-only, secret masking).
- **Input hardening (DoS/traversal):** bounded upload read + decompression (anti gzip-bomb, HTTP 413 past a configurable cap); capped variant `page_size`; family-package manifest paths contained to the authorised package root (no arbitrary host-file read).
- **Secrets/integrity:** refuse-to-start on default secrets in prod; bcrypt password hashing; content-hashed immutable clinical sign-out and audit (result integrity).
- **PHI download scoping:** CRAM/BAM presigned URLs issued only after family+sample access checks.
- **Supply chain:** hash-locked, audited dependencies (S-7); CodeQL SAST + gitleaks secret-scan + dependency-audit as **required** CI gates (S-6).

## 3. Open security items (deployment) — must close before clinical go-live
Tracked in [security-posture.md](../security-posture.md) "Remaining"; restated as controlled actions.

> **Status vocabulary.** Several controls below are **implemented in Terraform under
> `terraform/` but have never been applied** — no GCP project is configured, the `deploy` job
> skips on every run, and no state exists. Those read **🟡 In IaC, pending first apply**: the
> configuration is written and reviewable, but there is no deployed evidence, so none may be
> claimed as closed. Closing them requires the first apply plus captured evidence.

| # | Item | Action |
| --- | --- | --- |
| S-1 | Encryption at rest for Postgres/ClickHouse | 🟡 **In IaC, pending first apply.** CMEK is applied uniformly and is mandatory — a required, regex-validated variable with no default and no Google-managed-key fallback (`terraform/main.tf`, `variables.tf`). Covers Cloud SQL Postgres 16, the ClickHouse data disk and boot disk, and both GCS buckets (`database.tf`, `storage.tf`). **Action:** apply, then capture the key resource names and per-resource evidence. |
| S-2 | TLS between services & to datastores | 🟡 App support landed — `POSTGRES_SSLMODE` (asyncpg) and `CLICKHOUSE_SECURE`/`CLICKHOUSE_VERIFY` (HTTPS) configure encrypted links; default plain for dev. **Operational action:** provision TLS-terminated datastores and set these in production. See [security-posture.md](../security-posture.md) §3. |
| S-3 | Secrets management | 🟡 **In IaC, pending first apply.** Five Secret Manager containers with region-pinned replication; Cloud Run injects every secret **by reference** (`secret_key_ref`), never as a literal; the ClickHouse VM fetches its password and TLS material at boot rather than via instance metadata; resource-level `secretAccessor` grants only; values are added out-of-band so plaintext never passes through Terraform variables (`terraform/secrets.tf`, `cloudrun.tf`, `database.tf`). **Residual actions:** (a) **no rotation automation** — no `rotation` block on any secret, so rotating `SECRET_KEY` remains a manual, uncalendared act; (b) the Cloud SQL user password is read into Terraform **state**, mitigated only by a private CMEK-encrypted state bucket. *(The original 'move creds out of compose' framing is obsolete: compose is the dev path only and already interpolates from `.env`; the GCP path does not use compose.)* |
| S-4 | Byte-level PHI download audit | 🔲 **Open.** GCS Data Access audit logs (`storage.googleapis.com` DATA_READ/DATA_WRITE) are specified only in the **central-infra template** (`terraform/main-repo-reference/coga-prerequisites.tf.example`), which by design lives in another repository and is still an `.example`; CoGA's own config explicitly disclaims the project-level IAM audit config. Neither bucket has a `logging {}` block, and the load-balancer logs are 10%-sampled and are **not** the clinical audit trail. Second reason it cannot close: `storage_backend` defaults to `local`, so there are no GCS object reads to audit yet. **Action:** apply the central-infra template **and** flip `storage_backend` to `gcs`. |
| S-5 | Audit-queue durability | ✅ Done — a full async queue applies backpressure then writes the event synchronously; the worker retries batch writes and records (never silently drops) any unpersistable event at ERROR with its payload; default bound raised to `AUDIT_LOG_QUEUE_SIZE=10000`; silent drops refused in production (`AUDIT_LOG_DROP_ALLOWED=false`). See [security-posture.md](../security-posture.md) §2. |
| S-6 | Branch-protection required checks | ✅ Done — `main` requires all ten CI gates (`backend`, `frontend`, `smoke`, `e2e`, `e2e-playwright`, `catalogue`, `deps`, `secret-scan`, `codeql` ×2) with **strict** (up-to-date-before-merge) enforcement. |
| S-7 | Dependency pinning | ✅ Done — backend deps are hash-locked (`pip-compile --generate-hashes`, reproduced in Docker py3.10/amd64) and audited by a **blocking** `pip-audit --require-hashes` gate; the frontend is `package-lock.json` + a blocking `npm audit` (production tree). Dependabot is on; the JWT stack was migrated `python-jose` → PyJWT to drop the no-fix `ecdsa` advisory (see TF-08 / SECURITY-AUDIT-ALLOWLIST.md). |
| S-8 | Network posture | 🟡 **In IaC, pending first apply.** Custom VPC, no auto-subnets; Cloud SQL **private IP only** with `ssl_mode = ENCRYPTED_ONLY` over Private Service Access; ClickHouse VM has no external IP and **no SSH ingress**; firewall admits only tcp/8443 from the connector range; egress via Cloud NAT + Private Google Access (the GCP analogue of a VPC endpoint); both Cloud Run services are `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`, so neither is reachable on its `run.app` URL; buckets are uniform-BLA with public access prevention enforced; Cloud Armor with adaptive L7 DDoS, per-IP rate limiting and OWASP CRS 4.22 (`terraform/network.tf`, `database.tf`, `cloudrun.tf`, `storage.tf`, `armor.tf`). **Residual actions:** (a) the WAF ships in **preview/log-only** (`cloud_armor_waf_enforce` defaults false); (b) **no institutional IP allowlist** — `allowed_ingress_cidrs` defaults to `[]`, i.e. reachable from anywhere with application auth only; (c) the PHI grant is bucket-wide `objectViewer`, not prefix-scoped. See [security-posture.md](../security-posture.md) §4. |

## 4. Secure development lifecycle (IEC 81001-5-1)
- Security requirements captured in the SRS (TF-09 §3.5) and traced (RTM).
- Secure coding & review: changes land through pull requests gated by ten required status checks, and security-relevant dependencies are flagged (TF-08 §A). **An approving review is not yet mechanically required** (no `required_pull_request_reviews`), so independent review is a process commitment under [TF-18 §6](TF-18-change-configuration-management.md), not an enforced control — this item must not be read as evidence that every PR was independently reviewed.
- Verification: access-control tests (`test_access_control.py`), audit immutability tests, refuse-to-start tests; CI gates.
- Threat modeling: **🔲 ACTION** — produce a lightweight threat model (data-flow + trust boundaries: browser ↔ API ↔ Postgres/ClickHouse ↔ S3/filesystem; auth boundary; admin vs viewer) and review per significant change.

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
- The `sbom` job is **not** one of the ten required status checks, and CI does not trigger on
  `release: published` — so capturing the release-time SBOM into the technical file is a
  **manual archiving step** under [TF-18](TF-18-change-configuration-management.md), and the
  artifact expires after 90 days if not archived.

## 6. Vulnerability management
- **Monitoring:** Dependabot (in use) + CVE feeds for layer-A SOUP, especially security-critical items (`python-jose`, `passlib`/`bcrypt`, `axios`, FastAPI/Starlette, drivers).
- **Triage:** assess each advisory for exploitability in CoGA's deployment and impact on safety/PHI; severity-rank.
- **Remediation:** patch under change control (TF-18) with CI + review; emergency path for actively-exploited criticals.
- **Receiving reports:** a public [`SECURITY.md`](../../SECURITY.md) states the intake route — GitHub **private vulnerability reporting** (enabled on the repository), which keeps a report confidential to the maintainers until an advisory is published. It also fixes scope (the device boundary vs. the UZ Gent/CMGG-operated environment), forbids attaching real patient data to a report, and directs suspected patient-safety incidents to the vigilance route (TF-17) rather than a GitHub advisory.
- **Disclosure/coordination:** the named contact is **Björn Menten (bjorn.menten@ugent.be)**, stated in [`SECURITY.md`](../../SECURITY.md) alongside the GitHub private-reporting route, with a five-working-day acknowledgement aim. **🔲 INPUT NEEDED** — formal response/remediation targets and the UZ Gent IT security escalation path still to be agreed and aligned with vigilance (TF-17); due with the first beta release.

## 7. Minimum IT/security requirements for operation (IVDR §16.4 → IFU)
CoGA is operated only within the UZ Gent/CMGG managed environment with: TLS termination at
the proxy/ingress; encrypted datastores; managed secrets; network isolation of datastores;
access via institutional identity; and the operational logging in §2/§3. These become the
**minimum-requirements section of the IFU** (TF-15).

## 8. Records
Threat model, SBOMs, vulnerability triage log, security test results, and security-relevant
change records are retained per the CMGG QMS.
