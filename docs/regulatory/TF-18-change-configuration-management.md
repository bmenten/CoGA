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

## 6. Branch protection
The CI gates are enforced: `main` carries **ten required status checks** with **strict**
(up-to-date-before-merge) enforcement, so no change merges without passing them
([TF-09 §1](TF-09-verification-validation.md), [security-posture.md §5](../security-posture.md)
control S-6).

Two gaps in that enforcement remain open, and bound what the gates evidence:

- **🔲 `enforce_admins` is disabled** — a repository administrator can bypass the required
  checks. Any bypass is visible in the merge record, but it is not prevented.
- **🔲 No approving review is mechanically required** (branch protection carries no
  `required_pull_request_reviews`, and there is no `CODEOWNERS`). The **4-eye approval of §4 is
  therefore a process commitment, not an enforced control**; enforcement is scheduled with the
  first beta release, as stated in [`CONTRIBUTING.md`](../../CONTRIBUTING.md). Until then, this
  document must not be read as evidence that every merge was independently approved.

## 6a. Release procedure
The abstract flow above is executed by the concrete, step-by-step procedure in
[`RELEASING.md`](../../RELEASING.md), which produces a filled
[release record](../release-record-template.md) per release — version, tag, commit SHA, both
image digests, SBOM hashes, CI run URLs, deviations and approval. That record is the artefact
that ties a signed clinical report back to a specific build.

## 7. Records
Change requests, impact/significance assessments, review and CI evidence, re-validation
results, release records, and configuration baselines are retained per the CMGG QMS and
available to FAMHP on request (Art. 5(5)(e),(h)).

## 8. Change record log
Pre-release configuration changes to the device schema definition are logged here; each row
maps to the §4 significance assessment.

| # | Date | Change | Significance (§4) | Rationale | Evidence |
| --- | --- | --- | --- | --- | --- |
| CR-003 | 2026-07-30 | **Admin-configurable sequencing-QC acceptance limits.** New `qc_threshold_profiles` / `qc_thresholds` tables, an admin page (**Admin → Sequencing QC Thresholds**) setting a warning and an error limit per metric within named per-assay profiles, and server-side evaluation attaching a per-metric and worst-case state to each family member. The family members table shows that state as a compact chip (verdict word + mean depth, amber/red tone). The set of gateable metrics and the failing side of each are fixed in code (`qc_threshold_service.QC_METRICS`), not configurable. | **Minor `x.Y.z`** for *this release* — new backward-compatible capability that changes no existing output: **no cut-off values are shipped**, so on deployment nothing is gated and every metric reports *not assessed*. See the caveat opposite. | Per-sample sequencing QC was captured at import (CR-002) but only displayed as raw numbers, with no acceptance criterion — nothing in the device distinguished an interpretable run from an inadequate one, and the analyst-at-sign-out was the sole barrier (the control TF-06 §6 explicitly calls insufficient on its own). | Unit tests: `test_qc_threshold_service.py` (15) covering bound semantics per direction, unmeasured *and* unconfigured metrics reporting `skip` rather than `pass`, inverted bounds still failing, and the worst-metric rollup; `AdminQcThresholdsPage.test.tsx` (7); `SampleQcCell.test.tsx` (8). Full suites green (backend 1001 passed / 68 skipped; frontend 441). End-to-end against the reference pacbio family: limits 20/10 on mean depth → `warn` on 18.57x; 30/25 → `fail`; inverted bounds and unknown metric keys rejected with 400. **Caveat — entering a limit is a cut-off change.** §4 names a "cut-off change" as **major**, and §8 below covers only *pre-release* configuration of the device schema. This release ships the mechanism, not any cut-off; the first limit a laboratory enters is a change to the device's decision rules with no route in this document. **A §4c (runtime clinical-configuration change) and a configuration-change log are required before the feature is used clinically** — recommended, not yet agreed. New hazard **H14** added to TF-06; new requirements REQ-QC-003…005 in TF-09a with TF-09b rows. |
| CR-002 | 2026-07-29 | **Long-read (nf-core/lrsvar) family-package ingestion.** Five new package dataset types — `cnv` (depth-based CNV calls → structural variants, source `hificnv`, with copy number and VEP-derived genes), `mito` (chrM calls → small variants, source `mito`, annotated from the mutserve TSV), `qc` (read/depth metrics + QC-report location on the sample), `alignments` (aligned-read location for the browser), `pipeline_info` (tool versions → `family_annotation_manifest`, run parameters → `families.metadata`). Discovery gained wildcard-tolerant path matching (the SV annotator writes its release into the filename) and a VCF sample-column resolver (callers name the column after their own input file: `Sample0`, `<sample>_sort`). One new ClickHouse column (`SV/entries.calls.cn`) added by in-place `ALTER … ADD COLUMN IF NOT EXISTS`. No Postgres schema change (new keys inside existing JSONB `metadata` columns). | **Minor `x.Y.z`** — new backward-compatible ingestion capability. No existing dataset's parsing, storage or clinical meaning changes; each new callset carries its own `source` tag so it cannot displace an existing one. Two behaviour corrections are in scope and assessed below. | The device could not ingest a long-read package at all: the pipeline's per-sample layout matched no discovery pattern, and its VCF sample columns were rejected or silently dropped. CNV calls and mitochondrial variants had no ingestion path despite the reader-side support existing. Tool provenance shipped with the package was discarded, leaving reports untraceable to the pipeline that produced their evidence. | Unit tests: `test_family_package_long_read.py` (33) covering glob containment, sample-column resolution, CNV/mito/QC/pipeline parsing and a validator for every supported dataset type; `SampleQcCell.test.tsx` (5). Full suites green (backend 986 passed / 68 skipped; frontend 422). End-to-end: the reference PacBio HG002 package validates with 0 errors and 0 warnings across all 9 declared datasets. **Behaviour corrections included:** (a) TRGT family import returned `imported` after inserting zero rows when the VCF sample column was not a PED id — it now resolves the column or fails; (b) a failing non-`glimpse2` SNV import ran its compensating delete against the hard-coded `clair3` source, which could remove a different callset — the delete is now scoped to the source actually written. Both are corrections of silent-data-loss defects, not changes to validated behaviour. **Requires technical opvolgvalidatie (H11.1-F13) before clinical use**, plus the [TF-06](TF-06-risk-management-plan.md) risk review for sample-misassignment on the new per-sample datasets. |
| CR-001 | 2026-07-08 | **Consolidated the 43 incremental Postgres schema files** (`postgres/001_*.sql`–`042_*.sql`, incl. the duplicate `026_*` prefix) into **5 idempotent, domain-grouped baselines** (`01_access.sql`, `02_reference.sql`, `03_assay.sql`, `04_traceability.sql`, `05_grants.sql`). Each table is now created once in its final form (later `ALTER … ADD COLUMN` steps folded into the `CREATE TABLE`). The startup loader (`init_postgres_schema`) is unchanged — it still applies every file in sorted name order on each boot (no migration ledger). | **Patch `x.y.Z`** — refactor with **no functional/clinical impact**; the device schema is content-identical. | Readability, maintainability and traceability of the device schema definition before release: one final-form definition per table instead of a 43-file incremental history. | **Schema-identical proven** via a pg_dump/catalogue fingerprint before vs. after: 589 columns, 185 constraints, 167 indexes, 4 triggers, 4 functions, 220 grants — all identical, plus identical seed data. System-test/CI smoke (real Postgres startup + admin seed, [TF-09 §](TF-09-verification-validation.md)) green. No follow-up validation report required (patch level). |
