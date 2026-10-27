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
- **Database schema** — idempotent, domain-grouped SQL baselines (`backend/db/schema/postgres/01_access.sql`–`05_grants.sql`) and the Alembic revisions that bring an existing database to them (`backend/db/migrations/versions/`), applied as one migration step, as the table owner: by the API at startup in the default mode, or, once the API runs as the restricted database role, by the migration job before each rollout. The ledger table `alembic_version` records the revision each database is at, and a version older than that revision refuses to start on it; an API that does not migrate at startup (the restricted role) starts only on a database at exactly its own revision. On Google Cloud the deploy takes an on-demand Cloud SQL backup before each migration it starts, the restore point of that upgrade, and stops before the migration when the backup fails ([deployment-gcp.md](../deployment-gcp.md), 12.1). Until the release candidate every run re-applies the baselines first ([database.md](../database.md#schema-migrations)).
- **Reference data & content** — assembly, ClinVar/gnomAD/dbNSFP/VEP/etc. releases (versioned via the manifest), gene panels and their source version, NIPT artifact lists, ACMG criterion-positioning rules.
- **Configuration/secrets** — environment settings (secrets in a manager, not in VCS).
- **Documentation** — this technical file and the per-feature design docs.

## 2. Version identification
- **Software number `Sxxxx`** — CoGA is to be registered in the CMGGMC ICT module and assigned a software number (`Sxxxx`) per H11.1-OP5 (**🔲** not yet assigned); this is the device identifier (UDI-DI-equivalent) for this in-house device.
- **Semantic version `x.y.z`** (+ git commit hash) identifies each released build. The running version is served at `/api/version` and shown in the footer of every page and every report ([TF-15 §1](TF-15-instructions-for-use.md)); each signed report freezes the version that signed it and shows it in its sign-out block. **Only major (`X.y.z`) versions are recorded in the CMGGMC ICT "Software" section** per H11.1-OP5 §4.4.5.
- **The `VERSION` file at the repository root is the single source of truth.** The release commit bumps it; the release is then tagged **`v<VERSION>`** (e.g. `VERSION` = `0.1.0-beta.1` → tag `v0.1.0-beta.1`). Pre-release builds use a SemVer pre-release suffix (`-beta.N`, `-rc.N`) so a beta is never mistaken for the release it precedes. The build stamps `APP_VERSION` from that file into the image, from where it reaches `/api/version`, the app and report footers, and every signed report — so a tag that disagreed with `VERSION` would file a result against a version that does not identify the software that produced it. Only the release build (`build.yml` building a published release, tagged `v<VERSION>`) stamps `VERSION` as it is; every other build (a push to `main`, a manual or local build) stamps `VERSION` with the SemVer pre-release identifier `dev` added (`0.1.0-dev`, `0.1.0-rc.1.dev`), so a build of `main` after a release's tag is never mistaken for that release ([`scripts/build-version.sh`](../../scripts/build-version.sh)); `VERSION` therefore carries neither `dev` nor build metadata. [`scripts/check-release-version.sh`](../../scripts/check-release-version.sh) enforces the match and that format in the `prepare` job of `build.yml` and fails the release before any image is stamped; it can also be run locally before tagging.
- Each signed report freezes the CoGA version and commit together with the reference-data versions, inside its content hash — the per-case configuration record. It links every signed-out case to the software version that produced it, as the operational-phase rule of H11.1-OP5 §6 requires ([TF-16 §1](TF-16-post-market-surveillance-plan.md)).

## 3. Change-control workflow
Change request → impact analysis → significance assessment → implementation on a branch → CI
gates (TF-09) → merge, with no approval on the pull request (§6) → risk review (TF-06) →
re-verification / re-validation if triggered → QA's four-eyes review of the release's changes,
signed before the tag (§6) → release approval (lab director) → release record → deployment →
docs, RTM and SBOM updated.

## 3a. Lifecycle phases — when change control applies

CoGA is still in development: there is no validated baseline. The change control of §4–§8 applies
from the first release candidate.

| Phase | What happens | How changes are handled |
| --- | --- | --- |
| **Development** (now) | Synthetic data only; no clinical use. Every change is a pull request on the protected `main` branch, verified by the CI gates ([TF-09 §1](TF-09-verification-validation.md)); the pull request and the git history are its record, and `CHANGELOG.md` holds one summary of the pre-release work; the risk file ([TF-06](TF-06-risk-management-plan.md)) is kept current. | No per-change record in §8. [TF-10 §8](TF-10-performance-evaluation-plan.md) lists by area the clinical behaviour the first validation must cover; a change updates it only when it adds clinical behaviour no area covers. |
| **Release candidate** | Feature freeze; `VERSION` takes an `-rc.N` suffix and the tag follows it (§2). A documented, risk-based design and code review: every clinical-critical module (the ones the type-check and coverage gates single out: scoring, prioritisation and filters, variant search, storage and review, data and knowledgebase import, NIPT, PGT, sample QC, traceability and sign-out) in full, the rest by sample and automated analysis. Bio-IT ingangsvalidatie on H11.1-F12.2 ([TF-09](TF-09-verification-validation.md)); security go-live ([TF-13 §3](TF-13-cybersecurity.md)); QA confirms the TF-10 §8 list as validation scope. | Full change control from here: every change is assessed (§4) and recorded (§8). |
| **Beta — clinical validation** | Clinical validation per method on H11.1-F11 ([TF-10](TF-10-performance-evaluation-plan.md)), on the frozen release candidate, in parallel with the current validated workflow. CoGA results are not used for patient care until the validation concludes *voldoet*. | Bug fixes only, where possible. Each fix is a new `-rc.N` with its §4 level, and states which validation it leaves standing and which it re-opens. |
| **Release (v1.0.0)** | Release approval by the lab director and the release record (§7); registration in CMGGMC (`Sxxxx`); the Art. 5(5)(e) declaration made public before first use; post-market surveillance ([TF-16](TF-16-post-market-surveillance-plan.md)). | §4–§8 as written: patch, minor or major per H11.1-OP5. |

**Proposal for QA: the database schema at the release candidate.** Not adopted; QA confirms
or changes it. Until the release candidate, pull requests edit the five Postgres baseline files,
and every migration run re-applies them, so each edit reaches every database. The release
candidate's pull request would freeze them: the migration step stops re-applying them
(`REAPPLY_BASELINES_ON_EVERY_RUN = False` in `backend/app/core/postgres_migrations.py`), revision
`0001` applies them to a new database only, and a test pins their SHA-256. From then on each
schema change is a new Alembic revision only: its change record (§8) names the revision, its
verification runs it on a database at the previous revision, the release record states the
release's last revision, and it runs forward only, so going back means restoring the backup
taken before the upgrade
([database.md](../database.md#freezing-the-baselines-at-the-release-candidate)).

## 4. Significance assessment — semantic patch / minor / major (H11.1-OP5 §4.4.5, §5.4)

Every change is classified on the semantic version it produces; the version level drives the
required follow-up validation ("opvolgvalidatie") and the approval authority. This is the
CMGG impact-based model, applied to CoGA.

| Level | Impact | Example triggers | Required actions | Approval |
| --- | --- | --- | --- | --- |
| **Patch `x.y.Z`** | Backward-compatible technical; **no functional/clinical impact** on output. | Dependency patch bump; bugfix with no output change; refactor/logging; security update; Nextflow/nf-core bump without behaviour change. | System test (+ unit test for the fix); note in **CHANGELOG.md**. **No follow-up validation report.** | 4-eye: QA's review of the release's changes (§6), in place of a second (bio-)IT team member's approval per change, which CoGA's one-developer team cannot give. |
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
- `build.yml` refuses a release tag whose commit is not on `main` ([`scripts/check-release-on-main.sh`](../../scripts/check-release-on-main.sh), with `main`'s whole history), deploys each image by the digest its build pushed (`…@sha256:…`), not by a tag, and attaches the release's CycloneDX SBOMs, with their SHA-256 sums, to the GitHub release ([`RELEASING.md`](../../RELEASING.md)).
- The **release verification checklist** (TF-09 §6) must be complete and signed.
- Deployment to the clinical environment is controlled; the running version is served at `/api/version` and shown in the app footer (§2). A clinical build sets `COGA_PROBLEM_REPORT_URL`, so that **Report a problem** opens the CMGGMC route ([TF-15 §7](TF-15-instructions-for-use.md)).
- Rollback: the prior tagged release is retained. Each signed version's frozen record stays in the database and can be downloaded.

## 6. Branch protection
The CI gates are enforced through a GitHub ruleset on `main`, `CoGA_branch_protect` (a
repository setting, not a file in the repository). **Twelve status checks are required**, so no
change merges without passing them: `backend (pytest)`, `frontend (tsc + eslint + vitest)`,
`coverage (unit + smoke + e2e combined)`, `smoke (real startup against Postgres + ClickHouse)`,
`e2e (golden-trio pipeline against Postgres + ClickHouse)`, `e2e-playwright (browser journeys)`,
`catalogue (test overview in sync)`, `deps (pip-audit + npm audit)`, `secret-scan (gitleaks)`,
`codeql (python)`, `codeql (javascript-typescript)` and `CodeQL`, the code-scanning result,
which fails on an alert the pull request introduces (control S-6,
[security-posture.md §5](../security-posture.md)). A branch need not be up to date with `main`
before it merges: since 2026-10-13 a pull request merges on its own last run, and `main`'s run
after each merge checks the combined result (strict enforcement had serialised every merge
behind a fresh run). What each gate verifies: [TF-09 §1](TF-09-verification-validation.md) and
[docs/testing.md](../testing.md).

Two limits bound what the gates evidence: one open gap, and one arrangement the owner chose.

- **🔲 An administrator can change the ruleset** — it lists no bypass actors, so its checks
  apply to administrators too, but an administrator can edit or disable it, and that is not
  prevented. On 2026-10-13 the required checks were removed for some minutes, and #820 merged
  with its `smoke` check failing (#823 corrected the test).
- **🔲 No approving review is required on a pull request, by decision.** The ruleset carries no
  pull-request review rule and there is no `CODEOWNERS`, and that is now the arrangement, not
  a gap: the owner decided on 2026-10-14 (REG-8) that pull requests need no approval, because
  CoGA has one developer and no second (bio-)IT team member can approve each change. The
  four-eyes principle is met per release instead, by QA's review below. That review has not
  run yet, and it covers a sample: this document is evidence of an independent review only for
  the pull requests included in a review QA has signed.

**Four-eyes review per release (QA).** Before a release is tagged (§2: a beta, a release
candidate or a release), QA, the quality role of [TF-07 §3](TF-07-software-lifecycle-plan.md),
reviews the pull requests merged since the last tag it reviewed, and signs off. It is a
compensating measure for the missing per-change approval: it comes after the merge, so a
defect it finds is fixed by a new change before the tag, not kept from merging.

- **What QA reviews:** at least every pull request that changed a clinical-critical module,
  one listed under `[tool.mypy]` in `pyproject.toml`, and a sample of the other pull requests,
  which QA chooses and records how it chose.
- **What QA looks at, per pull request:** its description (what changed and why, the change
  level and what was verified, and how); the summary of its diff (the files it changed and how
  much); its tests (those it added or changed, and the required checks it passed); and its
  verification records (the TF-09b rows, the TF-09c and TF-09d evidence and the documents it
  updated).
- **How it is recorded:** one review record per release, filed per the CMGG QMS (§7). It lists
  the pull requests reviewed (number, title, merge commit) and how the sample was chosen, QA's
  findings and how each was resolved, or accepted with a reason, and QA's conclusion. QA signs
  and dates it before the tag is made; the release record cites it
  ([release record](../release-record-template.md), [`RELEASING.md`](../../RELEASING.md)).
  No tag is made while a finding that blocks the release is open.

## 6a. Release procedure
The abstract flow above is executed by the concrete, step-by-step procedure in
[`RELEASING.md`](../../RELEASING.md), which produces a filled
[release record](../release-record-template.md) per release — version, tag, commit SHA, both
image digests, SBOM hashes, CI run URLs, QA's four-eyes review (§6), deviations and approval.
That record is the artefact that ties a signed-out analysis back to a specific build, and,
through the clinical report's 1:1 reference to that analysis, the report in the EPD too.

## 7. Records
Change requests, impact/significance assessments, QA's four-eyes review records (§6), CI
evidence, re-validation results, release records, and configuration baselines are retained
per the CMGG QMS and
available to FAMHP on request (Art. 5(5)(e),(h)).

## 8. Change record log

Before the first release candidate this log holds no per-change records (§3a). Each change is a
pull request in git (`CHANGELOG.md` summarises the pre-release work once, with no per-change
entries), and [TF-10 §8](TF-10-performance-evaluation-plan.md) lists by area the clinical
behaviour the first validation must cover. The rows kept here until 2026-09-30 (CR-001 to
CR-117), and the per-change list TF-10 §8 kept until the same date, remain in the files' git
history; the behaviour they describe falls under the TF-10 §8 areas.

From the first release candidate, each change adds a row, newest first, numbered from CR-118:

| CR | Date | Change | Significance (§4 level) | Rationale | Evidence |
| --- | --- | --- | --- | --- | --- |
