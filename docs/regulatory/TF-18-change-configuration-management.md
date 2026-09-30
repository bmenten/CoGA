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
- **Dependencies** — `backend/requirements.txt` (every package pinned and hash-verified, [TF-08 §A.1](TF-08-soup-register.md)), `frontend/package-lock.json`, container base-image digests.
- **Database schema** — idempotent, domain-grouped SQL baselines (`backend/db/schema/postgres/01_access.sql`–`05_grants.sql`), applied in name order: by the API at startup in the default mode, or, once the API runs as the restricted database role, by the migration job before each rollout. There is no migration history: every file is re-applied each time.
- **Reference data & content** — assembly, ClinVar/gnomAD/dbNSFP/VEP/etc. releases (versioned via the manifest), gene panels and their source version, NIPT artifact lists, ACMG criterion-positioning rules.
- **Configuration/secrets** — environment settings (secrets in a manager, not in VCS).
- **Documentation** — this technical file and the per-feature design docs.

## 2. Version identification
- **Software number `Sxxxx`** — CoGA is to be registered in the CMGGMC ICT module and assigned a software number (`Sxxxx`) per H11.1-OP5 (**🔲** not yet assigned); this is the device identifier (UDI-DI-equivalent) for this in-house device.
- **Semantic version `x.y.z`** (+ git commit hash) identifies each released build. The running version is served at `/api/version` and shown in the footer of every page and every report ([TF-15 §1](TF-15-instructions-for-use.md)); each signed report freezes the version that signed it and shows it in its sign-out block. **Only major (`X.y.z`) versions are recorded in the CMGGMC ICT "Software" section** per H11.1-OP5 §4.4.5.
- **The `VERSION` file at the repository root is the single source of truth.** The release commit bumps it; the release is then tagged **`v<VERSION>`** (e.g. `VERSION` = `0.1.0-beta.1` → tag `v0.1.0-beta.1`). Pre-release builds use a SemVer pre-release suffix (`-beta.N`, `-rc.N`) so a beta is never mistaken for the release it precedes. The build stamps `APP_VERSION` from that file into the image, from where it reaches `/api/version`, the app and report footers, and every signed report — so a tag that disagreed with `VERSION` would file a result against a version that does not identify the software that produced it. [`scripts/check-release-version.sh`](../../scripts/check-release-version.sh) enforces the match in the `prepare` job of `build.yml` and fails the release before any image is stamped; it can also be run locally before tagging.
- Each signed report freezes the CoGA version and commit together with the reference-data versions, inside its content hash — the per-case configuration record. It links every signed-out case to the software version that produced it, as the operational-phase rule of H11.1-OP5 §6 requires ([TF-16 §1](TF-16-post-market-surveillance-plan.md)).

## 3. Change-control workflow
Change request → impact analysis → significance assessment → implementation on a branch → CI
gates and review (TF-09) → risk review (TF-06) → re-verification / re-validation if triggered →
release approval (lab director) → release record → deployment → docs, RTM and SBOM updated.

## 3a. Lifecycle phases — when change control applies

CoGA is still in development: there is no validated baseline. The change control of §4–§8 applies
from the first release candidate.

| Phase | What happens | How changes are handled |
| --- | --- | --- |
| **Development** (now) | Synthetic data only; no clinical use. Every change is a pull request on the protected `main` branch, verified by the CI gates ([TF-09 §1](TF-09-verification-validation.md)) and entered in `CHANGELOG.md`; the risk file ([TF-06](TF-06-risk-management-plan.md)) is kept current. | No per-change record in §8. A change that alters a clinical output adds one line to [TF-10 §8](TF-10-performance-evaluation-plan.md) with the §4 level it would carry: the behaviour the first validation must cover. |
| **Release candidate** | Feature freeze; `VERSION` takes an `-rc.N` suffix and the tag follows it (§2). A documented, risk-based design and code review: every clinical-critical module (the ones the type-check and coverage gates single out: scoring, prioritisation and filters, NIPT, PGT, sample QC, traceability and sign-out) in full, the rest by sample and automated analysis. Bio-IT ingangsvalidatie on H11.1-F12.2 ([TF-09](TF-09-verification-validation.md)); security go-live ([TF-13 §3](TF-13-cybersecurity.md)); QA confirms the TF-10 §8 list as validation scope. | Full change control from here: every change is assessed (§4) and recorded (§8). |
| **Beta — clinical validation** | Clinical validation per method on H11.1-F11 ([TF-10](TF-10-performance-evaluation-plan.md)), on the frozen release candidate, in parallel with the current validated workflow. CoGA results are not used for patient care until the validation concludes *voldoet*. | Bug fixes only, where possible. Each fix is a new `-rc.N` with its §4 level, and states which validation it leaves standing and which it re-opens. |
| **Release (v1.0.0)** | Release approval by the lab director and the release record (§7); registration in CMGGMC (`Sxxxx`); the Art. 5(5)(e) declaration made public before first use; post-market surveillance ([TF-16](TF-16-post-market-surveillance-plan.md)). | §4–§8 as written: patch, minor or major per H11.1-OP5. |

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

For CoGA, typical majors are a change to a filter preset, an ACMG criterion rule or an
inheritance rule, a new annotation or reference-data release, or a change to the validated
assemblies. The mapper and variant caller run upstream of the device
([TF-02 §3](TF-02-device-description.md)).

### 4a. Hotfix exception (H11.1-OP5 §7)
For an acute operational problem with serious impact, a **hotfix** may skip steps of the
methodology, **provided** the debug steps and changes are recorded in the project repository
(so the rationale is reconstructable). After the hotfix, the change **must still pass through
the normal §4 (patch/minor/major) flow**.

### 4b. Validation-report forms per change level
- **First clinical release** — bio-IT ingangsvalidatie on **H11.1-F12.2** (`VAL-Sxxxx`, software) → [TF-09 §7](TF-09-verification-validation.md); clinical validation per method on **H11.1-F11** (`VAL-Pxx`) → [TF-10 §7](TF-10-performance-evaluation-plan.md).
- **Minor** — technical opvolgvalidatie on **H11.1-F13** (`VAL-Sxxxx-OPVx`), as in the §4 table.
- **Major** — clinical opvolgvalidatie per affected method on **H11.1-F2** *(or H11.1-F14 — the SOP and the F11 form cite different codes; **🔲 confirm with CMGG quality**)*.
- Each form carries its approvals and signatures, and the conclusion *voldoet / voldoet voorlopig / voldoet niet* with a dated *vrijgave*.

## 5. Release & deployment
- Releases are built from a tagged commit with pinned dependencies (reproducible build).
- The **release verification checklist** (TF-09 §6) must be complete and signed.
- Deployment to the clinical environment is controlled; the running version is served at `/api/version` and shown in the app footer (§2). A clinical build sets `COGA_PROBLEM_REPORT_URL`, so that **Report a problem** opens the CMGGMC route ([TF-15 §7](TF-15-instructions-for-use.md)).
- Rollback: the prior tagged release is retained. Each signed version's frozen record stays in the database and can be downloaded.

## 6. Branch protection
The CI gates are enforced through GitHub branch protection on `main` (a repository setting, not
a file in the repository). **Ten status checks are required**, with **strict**
(up-to-date-before-merge) enforcement, so no change merges without passing them:
`backend (pytest)`, `frontend (tsc + eslint + vitest)`,
`smoke (real startup against Postgres + ClickHouse)`,
`e2e (golden-trio pipeline against Postgres + ClickHouse)`, `e2e-playwright (browser journeys)`,
`catalogue (test overview in sync)`, `deps (pip-audit + npm audit)`, `secret-scan (gitleaks)`,
`codeql (python)` and `codeql (javascript-typescript)` (control S-6,
[security-posture.md §5](../security-posture.md)). What each gate verifies:
[TF-09 §1](TF-09-verification-validation.md) and [docs/testing.md](../testing.md).

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

Before the first release candidate this log holds no per-change records (§3a). Each change is a
pull request in git with its `CHANGELOG.md` entry, and a change that alters a clinical output adds
a line to [TF-10 §8](TF-10-performance-evaluation-plan.md). The rows kept here until 2026-09-30
(CR-001 to CR-117) remain in this file's git history; their major and minor items are on the
TF-10 §8 list.

From the first release candidate, each change adds a row, newest first, numbered from CR-118:

| CR | Date | Change | Significance (§4 level) | Rationale | Evidence |
| --- | --- | --- | --- | --- | --- |
