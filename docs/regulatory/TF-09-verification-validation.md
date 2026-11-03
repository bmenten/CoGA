# TF-09 — Software Verification & Validation Plan & Requirements Traceability

| Field | Value |
| --- | --- |
| Document ID | TF-09 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Standards | IEC 62304 §5.5–5.7, §5.1.6 (traceability); IVDR Annex I §16.1 |

> Defines how CoGA is **verified** (built right: requirements → design → code → tests) and
> **validated** (right product: meets the clinical need). The test suites and CI carry most of
> the verification. Validation is the performance evaluation
> ([TF-10](TF-10-performance-evaluation-plan.md)) and the usability validation ([TF-12](TF-12-usability.md)).
>
> **In CMGG QMS terms (H11.1-OP5):** this document is the software's **bio-IT
> ingangsvalidatie**: the entry validation at first use, with acceptance criteria, test results
> and conclusion. It is reported on template **H11.1-F12.2** as report **`VAL-Sxxxx`** and
> embeds the risk analysis ([TF-06](TF-06-risk-management-plan.md)). The **clinical validation
> per analysis/method** (H11.1-OP1 §8) is separate:
> [TF-10](TF-10-performance-evaluation-plan.md)/[TF-11](TF-11-performance-evaluation-report.md).
>
> H11.1-OP5 expects acceptance testing on real data in an environment close to production.
> **🔲 Not yet done:** verification so far runs on synthetic data
> ([TF-09c §7](TF-09c-e2e-pipeline-verification.md)), and there is no production environment
> yet ([TF-02 §10](TF-02-device-description.md)).

---

## 1. Verification strategy & levels

| Level | Method | Where | Gate |
| --- | --- | --- | --- |
| Unit | pytest (backend), vitest (frontend) | `backend/tests`, `frontend/src/**/*.test.ts(x)` | CI `backend`, `frontend` jobs |
| Static analysis | Frontend: TypeScript `tsc`, ESLint with the React hooks and accessibility rules. Backend: ruff; mypy on the clinical-critical modules listed under `[tool.mypy]` in `pyproject.toml` | frontend, backend | CI `frontend` and `backend` jobs |
| Integration | Tests against real Postgres and ClickHouse: app startup (schema, admin seed, health), database immutability and role privileges, hash chains and anchors, ClickHouse queries | `backend/tests/integration` | CI `smoke` job |
| End-to-end (system) | Golden-dataset pipeline run (ingest → query/API → review/audit/sign-out) and realistic demo bundles, checked against documented expected results ([TF-09c](TF-09c-e2e-pipeline-verification.md)) | `backend/tests/e2e` | CI `e2e` job |
| Browser / GUI end-to-end | Chromium drives the UI's production build against a live backend and datastores (login → family workspace → genome view → sign-out); reviewers can reproduce it by hand ([TF-09d](TF-09d-browser-e2e-verification.md)) | `frontend/e2e` | CI `e2e-playwright` job |
| Build | Both production images built as the release build builds them (`ci/cloudbuild.*.yaml`: Dockerfile, target, build arguments, the backend stamped with its version and commit), not pushed; each image then loads its app without serving it | `backend/Dockerfile`, `frontend/Dockerfile` | CI `images` job (not a required check: making it one is the owner's branch-protection setting, [TF-18 §6](TF-18-change-configuration-management.md)) |
| System / clinical | Concordance with validated assays | [TF-10](TF-10-performance-evaluation-plan.md) | Performance report TF-11 |
| Coverage | Unit coverage, with per-module floors on the clinical-critical modules; unit, smoke and e2e coverage combined, with floors for the modules real datastores exercise | `scripts/check-coverage-floor.py` | CI `backend` and `coverage` jobs (both required) |
| Regression | Full suite re-run on every PR and push to main | CI | Required checks |

**CI enforcement:** the gates run on every PR and on push to `main`.
[TF-18 §6](TF-18-change-configuration-management.md) sets out which are required status checks
and the limits of that enforcement: an administrator can bypass them, and no approving review is
required (pull requests need no approval; QA does the four-eyes review per release). The gates
support the claim *"CI gates are blocking"*, **not** *"every merge was independently
reviewed"*. A change counts as independently reviewed only when QA's signed review of its
release includes it. What each job runs: [docs/testing.md](../testing.md).

**Test level ↔ version level (H11.1-OP5 §4.4.6).** A change's semantic-version level
([TF-18](TF-18-change-configuration-management.md)) sets the depth of testing it needs:

- **patch:** focuses on system tests that confirm existing functionality is unaffected;
- **minor:** verifies new *and* existing functionality, integration tests included;
- **major:** requires thorough testing at all levels, plus clinical opvolgvalidatie.

## 2. Validation strategy

- **Clinical/analytical validation:** concordance with validated comparator assays per application, on the validation sets of [TF-10 §2](TF-10-performance-evaluation-plan.md); results in [TF-11](TF-11-performance-evaluation-report.md).
- **Usability validation:** summative evaluation that intended users can use CoGA without unacceptable use error ([TF-12](TF-12-usability.md)).
- **Reproducibility validation:** the same validated input gives an identical content-hashed signed record (IVDR Annex I §16.1 repeatability); the frozen sign-out's content hash is the mechanism. The record is the device's output, the closed, frozen analysis. The clinical report drafted from it in the EPD is outside the device ([TF-02 §3](TF-02-device-description.md)).

## 3. Software Requirements Specification (SRS)

The SRS is a controlled companion document,
**[TF-09a — Software Requirements Specification](TF-09a-software-requirements-specification.md)**.
It gives every requirement a stable ID, grouped by application and cross-cutting area
(functional per application, performance, interface/input, risk-control, security, usability,
reporting), with a criticality rating and, where one applies, a link to its TF-06 hazard.
Requirements without a hazard link are marked "—". **🔲** The RMF review is to assign a hazard
to the criticality-C ones among them (REQ-DIAG-004, REQ-DIAG-005, REQ-DATA-003). The SRS derives
from the per-feature design docs and the implementation/test inventory, and is revised under
change control (TF-18).

## 4. Requirements traceability matrix (RTM)

The RTM, the spine 62304/IVDR expect, is a controlled companion document,
**[TF-09b — Requirements Traceability Matrix](TF-09b-requirements-traceability-matrix.md)**.
It traces each SRS requirement forward to its implementation (`file::function`) and verifying
test(s), and back to its hazard, with a status (✅ directly verified · ◐ partial / clinically
validated in TF-10 · ⚠ verification gap). TF-09b §3 lists the open items as a CAPA backlog that
must be closed before the first clinical release (§6 below).

## 5. Anomaly handling

Defects found in verification or in the field are recorded and risk-assessed (could it affect a
clinical result? → severity per TF-06). They are fixed under change control (TF-18) and
regression-tested; where clinically relevant, they go to vigilance/CAPA (TF-17). The append-only
clinical audit trail helps reconstruct any affected case.

## 6. Release verification checklist (per clinical release)

[`RELEASING.md`](../../RELEASING.md) gives the mechanics of a release: tagging, building, capturing evidence and filing the record. This checklist is the clinical gate that must pass before those mechanics run for a clinical release.

- [ ] All required CI gates green on the release commit ([TF-18 §6](TF-18-change-configuration-management.md)).
- [ ] Design and code review recorded for the release candidate: reviewers, scope, findings and their resolution ([TF-18 §3a](TF-18-change-configuration-management.md)).
- [ ] QA's four-eyes review of the release's changes signed before the tag: every change to the clinical-critical modules and a sample of the rest ([TF-18 §6](TF-18-change-configuration-management.md)).
- [ ] RTM updated; no requirement without a passing verifying test.
- [ ] Risk file (TF-06) reviewed for new/affected hazards; controls verified.
- [ ] SOUP register / SBOM (TF-08/TF-13) reconciled; no unaddressed high-severity vuln.
- [ ] Change significance assessed (TF-18); re-validation run if triggered (TF-10).
- [ ] `VERSION` bumped and the tag matches it ([TF-18 §2](TF-18-change-configuration-management.md)); the version is shown where [TF-15 §1](TF-15-instructions-for-use.md) requires it: the app footer and every report footer.
- [ ] The build sets `COGA_PROBLEM_REPORT_URL`, so **Report a problem** opens the CMGGMC route ([TF-15 §7](TF-15-instructions-for-use.md)).
- [ ] Release record signed (TF-18); lab director authorization.

## 7. Mapping to the CMGG report form (H11.1-F12.2)

The bio-IT ingangsvalidatie is reported on **template H11.1-F12.2** (in-house software, v5
21-04-2026) as report `VAL-Sxxxx` (the form adds the year to the file name). The
eindverantwoordelijke, the IT-team coördinator and the kwaliteitsbeheerder sign it. Where each
form section is filled from:

| H11.1-F12.2 section | Filled from |
| --- | --- |
| SITUERING — type software / soort acceptatietesten / gevolgen koppelingen | [TF-02](TF-02-device-description.md) (in-house, standalone MDSW); ingangstest vs upgrade per [TF-18](TF-18-change-configuration-management.md) |
| GEGEVENS voor de IVDR (IH-IVD; functie diagnose/monitoring/…; kwalitatief) | [TF-01 Intended Purpose](TF-01-intended-purpose.md) |
| BEOOGD GEBRUIK, DOEL | TF-01 + the SRS ([TF-09a](TF-09a-software-requirements-specification.md)) |
| ACCEPTATIECRITERIA (juistheid, traceerbaarheid, gebruiksvriendelijkheid; **patiëntveiligheid, continuïteit, data-integriteit**) | TF-09a requirements + TF-10 acceptance criteria |
| EFFECTIEVE UITVOERING — the five validation axes (below) | §1 verification levels and the test suites; [docs/testing.md](../testing.md) |
| RISICOANALYSE — VEILIGHEIDSEISEN IVDR (Annex I §13.2/§14/§16.1–16.4 table) | [TF-06 §6a](TF-06-risk-management-plan.md) |
| IMPLEMENTATIE (goedkeuring / bekendmaking / officiële ingebruikname) | [TF-18 §5](TF-18-change-configuration-management.md) |

**The form's five validation axes**, with CoGA's evidence:

| H11.1-F12.2 axis | CoGA evidence |
| --- | --- |
| **Gebruiksvriendelijkheid** (usability) | Usability engineering and summative evaluation ([TF-12](TF-12-usability.md); not yet run); frontend component tests. The browser journeys exercise the intended-use workflows as rendered: behavioural verification, not the summative evaluation ([TF-09d](TF-09d-browser-e2e-verification.md)). |
| **Accuraatheid / patiëntveiligheid** — (on)juistheid (e.g. measuring function, patient-material identification) | The clinical-output logic (NIPT/PGT/ACMG/CNV/mtDNA) and its tests (TF-09b RTM); per-stage expected-versus-actual results on the golden dataset ([TF-09c](TF-09c-e2e-pipeline-verification.md) §1–§3), and the same results on screen (TF-09d); patient-material identity through the Sample QC and its sign-out gate (TF-01 §4 condition 7, TF-06 H4). |
| **Traceerbaarheid** | Version manifest, evidence snapshots, immutable clinical audit, content-hashed sign-out ([clinical-traceability.md](../clinical-traceability.md)), asserted end to end over a review round-trip (TF-09c) and a sign-out in the browser (TF-09d); each signed-out case linked to the software version ([TF-18 §2](TF-18-change-configuration-management.md)). |
| **Continuïteit** (back-up of data) | Postgres and ClickHouse backups are codified in `terraform/` but not yet applied; a restore drill is due before go-live ([deployment-gcp.md §12.3](../deployment-gcp.md)) — **🔲 deployment item**. Deterministic re-import and a reproducible golden run (TF-09c). |
| **Data-integriteit** | Append-only audit/sign-out (DB immutability triggers) and tamper-evidence, asserted against the live database (TF-09c); file checksum verification (REQ-DATA-004); fail-clean import; RBAC; encryption at rest is codified but not yet applied ([TF-13](TF-13-cybersecurity.md) S-1) — **🔲 deployment item**. |
