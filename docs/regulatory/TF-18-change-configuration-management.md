# TF-18 — Change & Configuration Management

| Field | Value |
| --- | --- |
| Document ID | TF-18 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead + quality› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Basis | IEC 62304 §6 (maintenance) & §8 (configuration management); **IVDR Article 5(5)(h)** (devices produced per the documentation) |

> Article 5(5)(h) requires CMGG to ensure CoGA is **manufactured in accordance with its
> documentation**. For software that means controlled configuration, controlled change, and a
> defined rule for **when a change requires re-verification, re-validation, or an updated
> declaration**.

---

## 1. Configuration items (what is under control)
- **Source code** — Git repository; releases are tagged; every clinical build maps to a commit hash.
- **Dependencies** — `backend/requirements.txt` (pin all — see TF-08 open item), `frontend/package-lock.json`, container base-image digests.
- **Database schema** — idempotent, domain-grouped SQL baselines (`backend/db/schema/postgres/01_access.sql`–`05_grants.sql`), applied in name order on every startup (no migration ledger — the loader re-runs all files each boot).
- **Reference data & content** — assembly, ClinVar/gnomAD/dbNSFP/VEP/etc. releases (versioned via the manifest), gene panels and their source version, NIPT artifact lists, ACMG criterion-positioning rules.
- **Configuration/secrets** — environment settings (secrets in a manager, not in VCS).
- **Documentation** — this technical file and the per-feature design docs.

## 2. Version identification
- **Software number `Sxxxx`** — CoGA is registered in the CMGGMC ICT module and assigned a software number (`Sxxxx`) per H11.1-OP5; this is the device identifier (UDI-DI-equivalent) for this in-house device.
- **Semantic version `x.y.z`** (+ git commit hash) identifies each released build; shown in-app and in every report footer. **Only major (`X.y.z`) versions are recorded in the CMGGMC ICT "Software" section** per H11.1-OP5 §4.4.5.
- **The `VERSION` file at the repository root is the single source of truth.** The release commit bumps it; the release is then tagged **`v<VERSION>`** (e.g. `VERSION` = `0.1.0-beta.1` → tag `v0.1.0-beta.1`). Pre-release builds use a SemVer pre-release suffix (`-beta.N`, `-rc.N`) so a beta is never mistaken for the release it precedes. The build stamps `APP_VERSION` from that file into the image, and it surfaces at `/api/version` and in the report footer — so a tag that disagreed with `VERSION` would file a result against a version that does not identify the software that produced it. [`scripts/check-release-version.sh`](../../scripts/check-release-version.sh) enforces the match in the `prepare` job of `build.yml` and fails the release before any image is stamped; it can also be run locally before tagging.
- Each signed report already embeds the device + reference-data versions (content-hashed) — the per-case configuration record. Per the operational-phase rule, **every analysed sample is unambiguously linked to the software version used** ([TF-16](TF-16-post-market-surveillance-plan.md)).

## 3. Change-control workflow
```
Change request → impact analysis → significance assessment → implement (branch)
   → CI gates + review (TF-09) → risk review (TF-06) → re-verify / re-validate (if triggered)
   → release approval (lab director) → release record → deploy → docs/RTM/SBOM updated
```

## 4. Significance assessment — semantic patch / minor / major (H11.1-OP5 §4.4.5, §5.4)

Every change is classified on the semantic version it produces; the version level drives the
required follow-up validation ("opvolgvalidatie") and the approval authority. This is the
CMGG impact-based model, applied to CoGA.

| Level | Impact | Example triggers | Required actions | Approval |
| --- | --- | --- | --- | --- |
| **Patch `x.y.Z`** | Backward-compatible technical; **no functional/clinical impact** on output. | Dependency patch bump; bugfix with no output change; refactor/logging; security update; Nextflow/nf-core bump without behaviour change. | System test (+ unit test for the fix); note in **CHANGELOG.md**. **No follow-up validation report.** | 4-eye: a 2nd (bio-)IT team member. |
| **Minor `x.Y.z`** | New backward-compatible functionality; **no change to clinical meaning / intended use**. | New/extended feature; bugfix with limited output change; performance change; parameter change within validated bounds. | Patch steps **+ technical opvolgvalidatie** (template **H11.1-F13**, `VAL-Sxxxx-OPVx`): compare to the previous validated version on a small fixed dataset (e.g. **GIAB**), document output diffs; **review the risk analysis** ([TF-06](TF-06-risk-management-plan.md)). No clinical follow-up, but **explicit confirmation that clinical interpretation is unchanged**. | Projectverantwoordelijke + IT coördinator. |
| **Major `X.y.z`** | Backward-incompatible / **potential impact on clinical output, interpretation or intended use**. | Mapper/variant-caller/cut-off change; **other annotation or reference versions (e.g. VEP, genome build)**; pipeline-logic (filters/decision-rules/parameters) or **intended-use** change; new application/panel/assay/assembly. | Minor steps **+ clinical opvolgvalidatie per affected method** (template **H11.1-F2**) with the business contactpersoon ([TF-10](TF-10-performance-evaluation-plan.md)); update CMGGMC ICT; for intended-purpose/scope changes also update [TF-01](TF-01-intended-purpose.md)/[TF-02](TF-02-device-description.md), re-assess [TF-03 GSPR](TF-03-gspr-checklist.md), and the **Declaration ([TF-04](TF-04-declaration-of-conformity.md))** + **equivalence ([TF-05](TF-05-equivalence-justification.md))**. | Projectverantwoordelijke + IT coördinator + business contactpersoon. |

> Rule of thumb (and the dividing line between minor and major): a change that could alter a
> **clinical output**, **interpretation**, or the **validated scope/intended use** is a
> **major** and cannot reach clinical use without clinical opvolgvalidatie and the
> corresponding document updates.

### 4a. Hotfix exception (H11.1-OP5 §7)
For an acute operational problem with serious impact, a **hotfix** may skip steps of the
methodology, **provided** the debug steps and changes are recorded in the project repository
(so the rationale is reconstructable). After the hotfix, the change **must still pass through
the normal §4 (patch/minor/major) flow**.

### 4b. Validation-report forms per change level
- **First clinical release** — bio-IT ingangsvalidatie on **H11.1-F12.2** (`VAL-Sxx`, software) → [TF-09 §7](TF-09-verification-validation.md); clinical validation per method on **H11.1-F11** (`VAL-Pxx`) → [TF-10 §7](TF-10-performance-evaluation-plan.md).
- **Minor** — technical opvolgvalidatie on **H11.1-F13** (`VAL-Sxx-OPVx`): compare to the previous validated version on a fixed dataset (GIAB), document output diffs, review the risk analysis.
- **Major** — clinical opvolgvalidatie per affected method on **H11.1-F2** *(or H11.1-F14 — the SOP and the F11 form cite different codes; **🔲 confirm with CMGG quality**)*.
- Approvals/signatures and the `voldoet / voldoet voorlopig / voldoet niet` + dated `vrijgave` are part of each form.

## 5. Release & deployment
- Releases are built from a tagged commit with pinned dependencies (reproducible build).
- The **release verification checklist** (TF-09 §6) must be complete and signed.
- Deployment to the clinical environment is controlled; the running version is verifiable in-app.
- Rollback: the prior tagged release is retained; signed reports are reproducible from their frozen snapshots regardless of the deployed version.

## 6. Branch protection (enforcement gap)
**🔲 ACTION:** enforce the CI gates (`backend`, `smoke`, `frontend`) as **required status
checks** on `main` so no change merges without passing them (also flagged in TF-09 and
[security-posture.md §5](../security-posture.md)). Until enforced, the gates are advisory.

## 7. Records
Change requests, impact/significance assessments, review and CI evidence, re-validation
results, release records, and configuration baselines are retained per the CMGG QMS and
available to FAMHP on request (Art. 5(5)(e),(h)).

## 8. Change record log
Pre-release configuration changes to the device schema definition are logged here; each row
maps to the §4 significance assessment.

| # | Date | Change | Significance (§4) | Rationale | Evidence |
| --- | --- | --- | --- | --- | --- |
| CR-001 | 2026-07-08 | **Consolidated the 43 incremental Postgres schema files** (`postgres/001_*.sql`–`042_*.sql`, incl. the duplicate `026_*` prefix) into **5 idempotent, domain-grouped baselines** (`01_access.sql`, `02_reference.sql`, `03_assay.sql`, `04_traceability.sql`, `05_grants.sql`). Each table is now created once in its final form (later `ALTER … ADD COLUMN` steps folded into the `CREATE TABLE`). The startup loader (`init_postgres_schema`) is unchanged — it still applies every file in sorted name order on each boot (no migration ledger). | **Patch `x.y.Z`** — refactor with **no functional/clinical impact**; the device schema is content-identical. | Readability, maintainability and traceability of the device schema definition before release: one final-form definition per table instead of a 43-file incremental history. | **Schema-identical proven** via a pg_dump/catalogue fingerprint before vs. after: 589 columns, 185 constraints, 167 indexes, 4 triggers, 4 functions, 220 grants — all identical, plus identical seed data. System-test/CI smoke (real Postgres startup + admin seed, [TF-09 §](TF-09-verification-validation.md)) green. No follow-up validation report required (patch level). |
