# TF-07 — Software Development & Lifecycle Plan

| Field | Value |
| --- | --- |
| Document ID | TF-07 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Standards | IEC 62304:2006+A1:2015 (software lifecycle); IEC 82304-1 (health software product); supports IVDR Annex I §16.2 |

> Defines the lifecycle processes by which CoGA is developed, verified, released and
> maintained "in accordance with the state of the art" (IVDR Annex I §16.2). It documents
> the **existing engineering practice** of the project and the gaps to formalize.
>
> **Governing procedure.** This plan implements, for CoGA, CMGG's controlled software
> procedure **`H11.1-OP5` "Methodologie voor softwareontwikkeling"** (v1, 16-04-2026).
> Per that SOP, **IEC 62304 is applied "as inspiration"** (a guideline), alongside
> ISO/IEC 27001 (information security), GDPR, and ISO 15189:2022 — IVDR is the regulation.
> Where this document cites 62304 clauses, they structure the work; they are not a claim of
> certified 62304 compliance.

---

## 1. Software safety classification (IEC 62304 §4.3)

| | |
| --- | --- |
| **Proposed class** | **Class C** — a software failure (e.g. a missed pathogenic variant, a wrong embryo segregation call, a wrong fetal-risk category) could, if not caught, contribute to a serious clinical decision (embryo transfer/discard, reproductive decision, missed diagnosis). |
| Justification | Per IEC 62304, classification reflects the *worst-case* harm if the software fails and external risk controls are insufficient. Although a qualified professional signs out every result (a strong control), professional review alone is not treated as sufficient to downgrade below C for the safety-critical calls (TF-06). |
| Effect | Class C requires the full set of 62304 development, architecture, detailed-design, integration, system-test, and maintenance activities, with documented traceability. **🔲 INPUT NEEDED:** confirm classification with CMGG quality; a documented decomposition could assign lower classes to non-safety items (e.g. cosmetic UI) if justified. |

## 2. Lifecycle model

CoGA uses an **iterative/incremental** model (feature branches → PR → CI gates → review →
merge → release), mapped onto the IEC 62304 processes below. The existing per-feature design
docs (e.g. [monogenic-nipt.md](../monogenic-nipt.md), [clinical-traceability.md](../clinical-traceability.md))
are the project's de-facto design records; this plan formalizes the structure around them.

These processes are the engineering realization of the **H11.1-OP5 phase flow**:
**projectaanvraag** (a CMGGMC "Projectaanvraag nieuwe diagnostische Bio-IT toepassing"
questionnaire — project description, analysis info, validation plan, data/IT infrastructure,
planning — including a market-research / make-or-buy assessment, cf. [TF-05](TF-05-equivalence-justification.md))
→ **functionele analyse** (DPO advice obtained when personal data is processed, [TF-14](TF-14-dpia.md))
→ **ontwerp** → **implementatie** → **validatie** (bio-IT ingangsvalidatie
[TF-09](TF-09-verification-validation.md) / [TF-06](TF-06-risk-management-plan.md) + clinical
validation per method [TF-10](TF-10-performance-evaluation-plan.md)) → **operationele fase**
([TF-16](TF-16-post-market-surveillance-plan.md) / [TF-17](TF-17-vigilance-capa.md)).

| IEC 62304 process | How CoGA realizes it | Evidence / location |
| --- | --- | --- |
| 5.1 Development planning | This document; per-feature design docs | `docs/`, this file |
| 5.2 Requirements analysis | Software Requirements Spec (to formalize) + per-feature "clinical question" sections | TF-09 §SRS; `docs/*.md` |
| 5.3 Architectural design | [application-scheme.md](../application-scheme.md), [storage-architecture.md](../storage-architecture.md), [TF-02](TF-02-device-description.md) | `docs/` |
| 5.4 Detailed design | Per-feature design docs; code-level docstrings | `docs/`, source |
| 5.5 Implementation & unit verification | Python/TypeScript implementation + pytest/vitest unit tests | `backend/tests`, `frontend/src/**/*.test.tsx` |
| 5.6 Integration & integration testing | Smoke suite booting real Postgres+ClickHouse | `backend/tests/integration` |
| 5.7 System testing | Performance/concordance evaluation; end-to-end fixtures | [TF-10](TF-10-performance-evaluation-plan.md), TF-09 |
| 5.8 Release | Tagged version + build identifier; release checklist | TF-18 |
| 6 Maintenance | Bugfix/enhancement under same gates; change control | TF-18 |
| 7 Risk management | ISO 14971 process | [TF-06](TF-06-risk-management-plan.md) |
| 8 Configuration management | Git, pinned deps, schema baselines | TF-18, [TF-08](TF-08-soup-register.md) |
| 9 Problem resolution | Issue tracking + CAPA | [TF-17](TF-17-vigilance-capa.md) |

## 3. Roles & responsibilities

Per H11.1-OP5 (CMGG roles):

| Role (H11.1-OP5) | Holder | Responsibility |
| --- | --- | --- |
| **bio-IT SPOC** | ‹CMGG bio-IT group — see KHB organigram› | Single point of contact; triages project requests with the IT coördinator (and Sequencing Core); intake governance. |
| **CMGG IT coördinator** | **Tom Sante** | Project prioritization/planning; co-approves minor/major releases into production. |
| **Projectverantwoordelijke** (project lead) | **Björn Menten** | Owns design quality, implementation, the bio-IT ingangsvalidatie (incl. risk analysis), and release. |
| **Developer(s)** | **Björn Menten** | Implementation, unit/integration tests, SOUP monitoring; works via the project lead for cross-team input. |
| **Independent reviewer** | **Tom Sante** (CMGG bio-IT) | Code review / 4-eye approval before merge — a second (bio-)IT team member, and necessarily **not** the author of the change. |
| **Business contactpersoon** ("Klinisch coördinator") | ‹per application — see KHB› | Clinical requirements and coordinates the **clinical validation per method** ([TF-10](TF-10-performance-evaluation-plan.md)); co-approves major releases. |
| **Kwaliteitscel / labokwaliteitsverantwoordelijke** | **Greta Vandercruyssen** | QMS compliance, document control (KHB), and sign-off on in-production-taking. |
| **UZ Gent DPO** | ‹to be named at DPO consultation› | Consulted when personal data is processed ([TF-14](TF-14-dpia.md)). |
| **Head of department** | **Fransiska Malfait** | Departmental accountability for the in-house device. |
| **Lab director / Head of CMGG** | **Björn Menten** | Release authorization, residual-risk acceptance. |

The role holders are drawn from the CMGG bio-IT group; the authoritative register is the CMGG
**kwaliteitshandboek (KHB)** and organigram, of which this table is a project-specific extract.

> **Segregation of duties — recorded, not resolved.** The **developer, project lead and lab
> director are the same person** (Björn Menten), so implementation and release authorization are
> not independent. Two controls compensate and must therefore hold:
> 1. **Independent review is performed by the IT coördinator** (Tom Sante), who is not the author.
>    This is the 4-eye control of [TF-18 §4](TF-18-change-configuration-management.md); its
>    enforcement status is tracked in [TF-18 §6](TF-18-change-configuration-management.md).
> 2. **The H11.1-F12.2 validation report carries three distinct signatures** —
>    eindverantwoordelijke (Björn Menten), IT-team coördinator (Tom Sante) and
>    kwaliteitsbeheerder (Greta Vandercruyssen) — so no single person both produces and approves
>    the validation.
>
> This concentration should be reviewed by the kwaliteitscel and either accepted as a documented
> residual or resolved by appointing a separate release authorizer.

## 4. Development environment & tooling

- **Environment separation (H11.1-OP5):** development never happens directly on a production system — it uses a dedicated dev environment, or the test environment where none exists.
- **Registration & identifier:** CoGA is registered in the CMGGMC **ICT module** with a software number **`Sxxxx`**; semantic versioning `x.y.z` (see [TF-18](TF-18-change-configuration-management.md)); only **major** versions are recorded in the CMGGMC ICT "Software" section.
- Languages/runtimes: Python 3.10 (backend), Node 22 / TypeScript 6 (frontend).
- Source control: Git/GitHub; feature branches; pull/merge requests with code review via a project-specific checklist (DevOps); **branch protection with ten required status checks**, strict (up-to-date-before-merge), enforced on `main` — see [TF-09 §1](TF-09-verification-validation.md). An approving review is **not** mechanically required (no `required_pull_request_reviews`), so the 4-eye rule is a process commitment pending enforcement at the first beta release ([TF-18 §6](TF-18-change-configuration-management.md)).
- Build/packaging: Docker / Docker Compose; pinned `backend/requirements.txt`, `frontend/package-lock.json`.
- CI: GitHub Actions (`.github/workflows/ci.yml`) — backend pytest, real-startup smoke, frontend tsc+eslint+vitest.
- Tool validation: development tools (linters, test runners, CI) are not part of the device; their adequacy is evidenced by the gates they enforce. Compilers/build tools are configuration-controlled via pinned versions.

## 5. Deliverables per increment

For each change reaching a clinical release: updated requirements/design where affected,
implementation, unit + integration tests (green in CI), updated traceability (TF-09),
risk-file review (TF-06) when a new hazard or control is touched, and a release record (TF-18).

## 6. Maintenance & legacy

CoGA is an actively maintained product. Maintenance changes follow the same process and gates
as development; the change-control procedure (TF-18) determines, per change, whether
re-verification and/or re-validation (TF-09/TF-10) is required and whether the change is
"significant" enough to warrant a new declaration/notification. Reference-data and SOUP
updates are handled per [TF-08](TF-08-soup-register.md).

## 7. Records

Lifecycle records (design docs, PRs/reviews, CI results, test reports, release records,
risk-file revisions) are retained per the CMGG QMS retention policy for in-house IVDs.
