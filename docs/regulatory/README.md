# CoGA — Technical File (In-House IVD, IVDR Article 5(5))

**Device:** CoGA (Comprehensive Genomic Analysis) — clinical genomic interpretation software
**Manufacturer (health institution):** Center for Medical Genetics Ghent (CMGG), Ghent University Hospital
**Regulatory basis:** In-house device under **Regulation (EU) 2017/746 (IVDR) Article 5(5)**, manufactured and used within CMGG, under **EN ISO 15189** accreditation (BELAC 351-MED; [accreditation scope](https://www.cmgg.be/nl/over-ons/accreditatie)). Which legal person declares, and whether the certificate relied on covers it, is an open RA decision ([TF-04 §1](TF-04-declaration-of-conformity.md)).
**Objective:** Meet the Article 5(5) conditions in full while voluntarily aligning the documentation with the CE‑IVDR technical-documentation structure (Annex II/III) so that a future move toward CE marking, or a competent-authority review, is low-friction.

> **Status: DRAFT for internal review.** None of the documents in this folder are
> approved or effective. They are working drafts prepared to seed the CMGG quality
> system. Document-control metadata (owners, approvers, effective dates, version
> numbers) must be reconciled with the CMGG QMS before release. Placeholders are
> marked `‹…›`; open decisions are marked **🔲 INPUT NEEDED**.

---

## 1. Why these documents exist (Article 5(5) checklist)

IVDR Article 5(5) lets a health institution manufacture and use a device on itself
without CE marking, provided **all** of the following are met. This folder maps each
condition to the document that satisfies it.

| Art. 5(5) condition | Requirement (paraphrased) | Satisfied by |
| --- | --- | --- |
| (a) | Device not transferred to another legal entity | [TF-04 Declaration](TF-04-declaration-of-conformity.md) §scope; CMGG deployment policy |
| (b) | Manufacture & use under an appropriate **QMS** | CMGG ISO 15189 QMS — governing SOP **H11.1-OP5** (see §1a) + [TF-07 Software Lifecycle Plan](TF-07-software-lifecycle-plan.md) |
| (c) | Laboratory compliant with **EN ISO 15189** (incl. accreditation) | Accreditation certificate (referenced, not reproduced here; see [TF-04 §1](TF-04-declaration-of-conformity.md)) |
| (d) | Justification that **no equivalent CE device** meets the need | [TF-05 Equivalence & Clinical-Need Justification](TF-05-equivalence-justification.md) |
| (e) | Provide information on use to **competent authority** on request | [TF-04 Declaration](TF-04-declaration-of-conformity.md) §competent-authority; this whole file |
| (f) | Public **declaration** (institution identity, device ID, GSPR conformity statement) | [TF-04 Declaration of Conformity](TF-04-declaration-of-conformity.md) |
| (g) | Documentation of design, manufacture & performance sufficient for the CA to verify GSPR | This **entire technical file** |
| (h) | Ensure devices are produced per that documentation | [TF-18 Change & Configuration Management](TF-18-change-configuration-management.md) + release procedure |
| (i) | Review **clinical-use experience** and take corrective action | [TF-16 Post-Market Surveillance Plan](TF-16-post-market-surveillance-plan.md) |

> **Transitional timing (verify with CMGG RA / FAMHP).** Under the amended IVDR
> transitional provisions (Reg. (EU) 2022/112), the Article 5(5) conditions in
> points (b), (c) and (e)–(i) apply from **26 May 2024**; the point (d) equivalence
> justification applies from **26 May 2028**. TF‑05 is therefore prepared ahead of
> its legal deadline as part of the "as close to CE‑IVDR as possible" goal.

### 1a. Governing CMGG QMS procedure (H11.1-OP5)

This technical file is **not free-standing** — it operates under CMGG's controlled software
procedure **`H11.1-OP5` "Methodologie voor softwareontwikkeling"** (CMG-H11.1-OP5, v1, in
voege 16-04-2026), itself under the ISO 15189:2022 QMS (BELAC-accredited). Per that SOP,
validation under IVDR uses **IEC 62304 "as inspiration"** (not strict compliance) plus
**ISO/IEC 27001** (information security, CIA triad), **GDPR**, and **ISO 15189:2022**. The
TF documents map onto the SOP as follows:

| CMGG QMS artefact (H11.1-OP5) | This technical file |
| --- | --- |
| Methodology / lifecycle (projectaanvraag → analyse → ontwerp → implementatie → validatie → operationele fase) | [TF-07 Software Lifecycle Plan](TF-07-software-lifecycle-plan.md) |
| **bio-IT ingangsvalidatie** of the software (template **H11.1-F12.2**, report `VAL-Sxxxx`), incl. the risk analysis | [TF-09 V&V](TF-09-verification-validation.md) + [TF-06 Risk Management](TF-06-risk-management-plan.md) |
| **Klinische validatie** per analysis/method (**H11.1-OP1 §8**, templates H11.1-F11 / F2) | [TF-10 Performance Evaluation](TF-10-performance-evaluation-plan.md) / [TF-11](TF-11-performance-evaluation-report.md) |
| Semantic versioning + patch/minor/major opvolgvalidatie (H11.1-F13 / F2); `Sxxxx` in CMGGMC ICT | [TF-18 Change & Configuration Management](TF-18-change-configuration-management.md) |
| Information security (ISO 27001) / GDPR / DPO | [TF-13 Cybersecurity](TF-13-cybersecurity.md) / [TF-14 DPIA](TF-14-dpia.md) |
| Operationele fase: incidents via CMGGMC, monitoring, sample↔version linkage | [TF-16 PMS](TF-16-post-market-surveillance-plan.md) / [TF-17 Vigilance/CAPA](TF-17-vigilance-capa.md) |

> Terminology bridge: CMGG's **"bio-IT validation"** ≈ software V&V (TF-09); CMGG's
> **"klinische validatie"** per method ≈ the per-application performance evaluation
> (TF-10/TF-11). The device identifier is the CMGGMC **software number `Sxxxx`**.

#### CMGG validation-report templates (the forms the TF docs feed)

The TF documents are structured so the actual CMGG report forms can be filled directly:

| CMGG form (version) | Purpose | Filled from |
| --- | --- | --- |
| **H11.1-F12.2** v5 (21-04-2026) | **In-house software** validation (bio-IT ingangsvalidatie), `VAL-Sxxxx` | [TF-09](TF-09-verification-validation.md) §7 + [TF-06](TF-06-risk-management-plan.md) §6a |
| **H11.1-F11** v6 (05-01-2023) | **Clinical** validation per method/analysis, `VAL-Pxx` | [TF-10](TF-10-performance-evaluation-plan.md) §7 / [TF-11](TF-11-performance-evaluation-report.md) |
| **H11.1-F13** v5 (16-08-2023) | **Limited change** / technical opvolgvalidatie, `VAL-Sxxxx-OPVx` | [TF-18](TF-18-change-configuration-management.md) §4 (minor) |
| **H11.1-F2** / **F14** (verify) | **Clinical** opvolgvalidatie (major change) | [TF-18](TF-18-change-configuration-management.md) §4 (major) |
| H11.1-F12.1 v5 (commercial software) | SOUP / third-party reference (CoGA is in-house) | [TF-08](TF-08-soup-register.md) |
| H11.1-F10 (devices) | Not applicable (software-only device) | — |

Report IDs: `VAL-Sxxxx` is the software validation report (`Sxxxx` is CoGA's software number),
`VAL-Sxxxx-OPVx` a follow-up validation of it (number x), and `VAL-Pxx` the clinical validation
of analysis protocol H10.1-Pxx. The forms add the year to the file name.

> **🔲 verify with CMGG quality:** the clinical-follow-up template is cited as **H11.1-F2** in
> H11.1-OP5 but as **H11.1-F14** on the H11.1-F11 form — confirm the current code.

---

## 2. Document register (technical file contents)

Numbered to mirror an IVDR Annex II/III dossier. Status legend: ✅ drafted ·
◐ skeleton/partial · ⬜ planned.

| ID | Document | Maps to | Standard | Status |
| --- | --- | --- | --- | --- |
| TF-01 | [Intended Purpose Statement](TF-01-intended-purpose.md) | Annex II §1.1 | IVDR Annex I §20.4.1 | ✅ |
| TF-02 | [Device Description & Specification](TF-02-device-description.md) | Annex II §1.1–1.2 | — | ✅ |
| TF-03 | [GSPR Conformity Checklist](TF-03-gspr-checklist.md) | Annex II §4 | IVDR Annex I | ✅ |
| TF-04 | [In-House Declaration of Conformity](TF-04-declaration-of-conformity.md) | Art. 5(5)(f) | IVDR Annex I | ✅ |
| TF-05 | [Equivalence & Clinical-Need Justification](TF-05-equivalence-justification.md) | Art. 5(5)(d) | — | ✅ |
| TF-06 | [Risk Management Plan](TF-06-risk-management-plan.md) | Annex II §5 | ISO 14971 | ✅ |
| TF-07 | [Software Development & Lifecycle Plan](TF-07-software-lifecycle-plan.md) | Annex II §3 | IEC 62304 / 82304-1 | ✅ |
| TF-08 | [SOUP & Reference-Database Register](TF-08-soup-register.md) | Annex II §3 | IEC 62304 §8 | ✅ |
| TF-09 | [Software V&V Plan & Requirements Traceability](TF-09-verification-validation.md) | Annex II §3 | IEC 62304 §5.5–5.7 | ✅ |
| TF-09a | [Software Requirements Specification (SRS)](TF-09a-software-requirements-specification.md) | Annex II §3 | IEC 62304 §5.2 | ✅ |
| TF-09b | [Requirements Traceability Matrix (RTM)](TF-09b-requirements-traceability-matrix.md) | Annex II §3 | IEC 62304 §5.1.6 | ✅ |
| TF-09c | [End-to-End Pipeline Verification (golden dataset)](TF-09c-e2e-pipeline-verification.md) | Annex II §3 | IEC 62304 §5.6–5.7 | ✅ |
| TF-09d | [Browser (GUI) End-to-End Verification & Manual Reproduction](TF-09d-browser-e2e-verification.md) | Annex II §3 | IEC 62304 §5.7 | ✅ |
| TF-10 | [Performance Evaluation Plan](TF-10-performance-evaluation-plan.md) | Annex II §1.2; Annex XIII | IVDR Annex XIII | ✅ |
| TF-11 | [Performance Evaluation Report](TF-11-performance-evaluation-report.md) | Annex XIII Part A | IVDR Annex XIII | ◐ template (awaiting study data) |
| TF-12 | [Usability Engineering File](TF-12-usability.md) | Annex I §16 | IEC 62366-1 | ✅ |
| TF-13 | [Cybersecurity Management & SBOM](TF-13-cybersecurity.md) | Annex I §16.4 | IEC 81001-5-1; MDCG 2019-16 | ✅ |
| TF-14 | [Data Protection Impact Assessment](TF-14-dpia.md) | — | GDPR Art. 9 & 35 | ✅ (to reconcile with the signed DPIA, #518) |
| TF-15 | [Instructions for Use & Labelling](TF-15-instructions-for-use.md) | Annex I §20 | IVDR Annex I §20 | ✅ |
| TF-16 | [Post-Market Surveillance Plan (+ PMPF)](TF-16-post-market-surveillance-plan.md) | Annex II §9; Art. 5(5)(i) | IVDR Art. 78–81 | ✅ |
| TF-17 | [Vigilance, Incident & CAPA Procedure](TF-17-vigilance-capa.md) | Art. 5(5)(i) | IVDR Art. 82–83 (model) | ✅ |
| TF-18 | [Change & Configuration Management](TF-18-change-configuration-management.md) | Art. 5(5)(h) | IEC 62304 §6, §8 | ✅ |
| — | [Consolidated Inputs Questionnaire](INPUTS-QUESTIONNAIRE.md) | (working aid) | — | ✅ |

## 3. Existing engineering evidence feeding this file

CoGA already implements several controls that serve as direct GSPR/lifecycle evidence;
the technical file references rather than duplicates them:

- **Clinical traceability, sign-out & audit** — [docs/clinical-traceability.md](../clinical-traceability.md): version manifest, per-classification evidence snapshots, evidence-drift detection, immutable clinical audit trail, content-hashed frozen sign-out. (Feeds GSPR §16 repeatability/traceability, risk controls, PMS.)
- **Annotation & tool provenance (version control)** — [docs/annotation-provenance.md](../annotation-provenance.md): per-family capture of the caller, annotation-engine and reference-database versions — from the pipeline's run manifest (`software_versions.yaml`) when the package has one, otherwise from the VCF headers — refreshed on re-import and frozen into the sign-out snapshot. (Feeds GSPR §16 traceability and the EFFECTIVE UITVOERING / version-control axis of the bio-IT validation; supports [TF-08 SOUP register](TF-08-soup-register.md).)
- **Security & PHI posture** — [docs/security-posture.md](../security-posture.md): project-scoped RBAC, append-only audit log, secrets handling. (Feeds TF-13, TF-14.)
- **ACMG classifier** — the in-app reference *Semi-automatic ACMG classification* ([source](../../frontend/src/content/docs/acmg-classification.md)) holds the rules and the "overridable, server-recomputed, not an autoclassifier" stance; [docs/acmg-classification.md](../acmg-classification.md) holds the engine notes. (Feeds intended purpose, risk, performance evaluation.)
- **Assay design references** — [monogenic-nipt.md](../monogenic-nipt.md), [haplotype-segregation-analysis.md](../haplotype-segregation-analysis.md), and the per-feature docs. (Feed device description and per-application performance evaluation.)
- **Clinical rules for lab users** — the in-app reference docs (`frontend/src/content/docs/`, shown at `/docs/reference/<slug>`) and the in-app user guide (`/docs`). They are part of the information for safety ([TF-15](TF-15-instructions-for-use.md)).

## 4. Document-control convention

Each document carries a control header: ID, version, status, owner, approver, date. Until the
CMGG QMS assigns formal IDs and revision control, these drafts carry `v0.1 DRAFT`. **The date in
a header is that of the first draft; later revisions are tracked in git, and
from the first release candidate also in `CHANGELOG.md` and the change log
([TF-18 §8](TF-18-change-configuration-management.md)).** Approval, periodic review cadence, and
storage of the controlled master copy are governed by the CMGG QMS (ISO 15189 clause 8.3
document control).

> **🔲 OWNER:** fill the Owner and Approver fields with names (from
> [INPUTS A3](INPUTS-QUESTIONNAIRE.md) and [TF-07 §3](TF-07-software-lifecycle-plan.md)), or keep
> them as roles.
