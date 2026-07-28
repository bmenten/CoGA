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
Tracked in [security-posture.md](../security-posture.md) "Remaining"; restated as controlled actions:

| # | Item | Action |
| --- | --- | --- |
| S-1 | Encryption at rest for Postgres/ClickHouse | Managed encrypted storage or LUKS/full-disk; document. |
| S-2 | TLS between services & to datastores | 🟡 App support landed — `POSTGRES_SSLMODE` (asyncpg) and `CLICKHOUSE_SECURE`/`CLICKHOUSE_VERIFY` (HTTPS) configure encrypted links; default plain for dev. **Operational action:** provision TLS-terminated datastores and set these in production. See [security-posture.md](../security-posture.md) §3. |
| S-3 | Secrets management | Move DB/ClickHouse creds out of compose into a secret store; rotate `SECRET_KEY`. |
| S-4 | Byte-level PHI download audit | S3 server-access logging / CloudTrail data events. |
| S-5 | Audit-queue durability | ✅ Done — a full async queue applies backpressure then writes the event synchronously; the worker retries batch writes and records (never silently drops) any unpersistable event at ERROR with its payload; default bound raised to `AUDIT_LOG_QUEUE_SIZE=10000`; silent drops refused in production (`AUDIT_LOG_DROP_ALLOWED=false`). See [security-posture.md](../security-posture.md) §2. |
| S-6 | Branch-protection required checks | ✅ Done — `main` requires all ten CI gates (`backend`, `frontend`, `smoke`, `e2e`, `e2e-playwright`, `catalogue`, `deps`, `secret-scan`, `codeql` ×2) with **strict** (up-to-date-before-merge) enforcement. |
| S-7 | Dependency pinning | ✅ Done — backend deps are hash-locked (`pip-compile --generate-hashes`, reproduced in Docker py3.10/amd64) and audited by a **blocking** `pip-audit --require-hashes` gate; the frontend is `package-lock.json` + a blocking `npm audit` (production tree). Dependabot is on; the JWT stack was migrated `python-jose` → PyJWT to drop the no-fix `ecdsa` advisory (see TF-08 / SECURITY-AUDIT-ALLOWLIST.md). |
| S-8 | Network posture | Private subnets for datastores; VPC endpoint for S3; minimal IAM (`s3:GetObject` on the PHI prefix only). See [security-posture.md](../security-posture.md) §4. |

## 4. Secure development lifecycle (IEC 81001-5-1)
- Security requirements captured in the SRS (TF-09 §3.5) and traced (RTM).
- Secure coding & review: every PR independently reviewed; security-relevant deps flagged (TF-08 §A).
- Verification: access-control tests (`test_access_control.py`), audit immutability tests, refuse-to-start tests; CI gates.
- Threat modeling: **🔲 ACTION** — produce a lightweight threat model (data-flow + trust boundaries: browser ↔ API ↔ Postgres/ClickHouse ↔ S3/filesystem; auth boundary; admin vs viewer) and review per significant change.

## 5. SBOM (Software Bill of Materials)
The SBOM is the reconciled SOUP register ([TF-08](TF-08-soup-register.md)) plus container
base images, exported in a standard format (CycloneDX/SPDX) per release. **🔲 ACTION:**
generate the SBOM automatically from the lockfiles (`backend/requirements*.txt`,
`frontend/package-lock.json`) and image digests at build time.

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
