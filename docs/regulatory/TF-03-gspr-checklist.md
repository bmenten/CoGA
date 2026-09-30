# TF-03 — General Safety & Performance Requirements (GSPR) Conformity Checklist

| Field | Value |
| --- | --- |
| Document ID | TF-03 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG RA / quality› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Basis | IVDR (EU) 2017/746 **Annex I** |

> Clause-by-clause mapping of the IVDR Annex I GSPRs to applicability, the method of
> conformity, and the evidence in this technical file. This is the core of the Article
> 5(5)(f) declaration: the institution declares the device meets these requirements, and
> states with justification any that are not fully met. **Clause numbering must be
> verified against the current regulation text by CMGG RA.** "Met by" cites the
> controlling document(s); ◐ = partially evidenced today, action tracked.

Legend — **Status:** ✅ met (evidence exists) · ◐ in progress · ⬜ not started · N/A.

## Chapter I — General requirements

| § | Requirement (paraphrased) | Applic. | Status | Method of conformity / evidence |
| --- | --- | --- | --- | --- |
| 1 | Achieve intended performance; safe & effective; risks acceptable vs benefit | Yes | ◐ | [TF-01](TF-01-intended-purpose.md), [TF-06](TF-06-risk-management-plan.md), TF-10/TF-11 (performance) |
| 2 | Reduce risks as far as possible without adverse benefit-risk | Yes | ◐ | [TF-06](TF-06-risk-management-plan.md) risk controls; design controls in TF-07 |
| 3 | Establish & maintain a **risk management system** | Yes | ◐ | [TF-06](TF-06-risk-management-plan.md) (ISO 14971) |
| 4 | Risk-control measures in priority order (inherent safety → protective → information) | Yes | ◐ | TF-06 risk-control table; IFU warnings TF-15 |
| 5 | Reduce risks related to **use error** (ergonomics, user knowledge) | Yes | ◐ | [TF-12](TF-12-usability.md) Usability (IEC 62366-1): drafted; summative evaluation not yet run |
| 6 | Performance & safety maintained over the device **lifetime** | Yes | ◐ | TF-18 change control; TF-16 PMS; evidence-drift surfacing ([clinical-traceability](../clinical-traceability.md)) |
| 7 | Transport/storage conditions | N/A | N/A | Software, no physical media; delivered/operated within CMGG |
| 8 | Benefit-risk acceptable under normal use | Yes | ◐ | TF-06 §benefit-risk; TF-11 performance report |

## Chapter II — Performance, design & manufacture

| § | Requirement (paraphrased) | Applic. | Status | Method of conformity / evidence |
| --- | --- | --- | --- | --- |
| 9.1 | Performance characteristics — **analytical & clinical performance** appropriate to intended purpose | Yes | ◐ | [TF-10 Performance Evaluation Plan](TF-10-performance-evaluation-plan.md); TF-11 report; Annex XIII |
| 9.3 | Metrological traceability of assigned values | N/A* | N/A | CoGA assigns no measured analyte value; *traceability of interpretation* handled via version manifest ([clinical-traceability](../clinical-traceability.md)) |
| 9.4 | Analytical performance maintained; revalidate on change | Yes | ◐ | [TF-18 §4](TF-18-change-configuration-management.md) sets the re-verification and re-validation per change level; TF-09 |
| 10 | Chemical/physical/biological properties | N/A | N/A | Software only |
| 11 | Infection & microbial contamination | N/A | N/A | Software only |
| 12 | Materials of biological origin | N/A | N/A | Software only |
| 13 | Construction & interaction with environment | Partial | ◐ | IT environment & integration: [security-posture](../security-posture.md); TF-13 |
| 14 | Devices with a **measuring function** | N/A* | N/A | Qualitative/interpretive; *NIPT fetal fraction is a derived QC estimate with CI, not a diagnostic measurement* — verify classification with RA |
| 15 | Protection against radiation | N/A | N/A | — |
| **16.1** | **Software/programmable systems shall ensure repeatability, reliability & performance** in line with intended use; single-fault safety | Yes | ◐ | Frozen, content-hashed sign-out record, from which a signed report is rendered and printed, + server-side recompute ([clinical-traceability](../clinical-traceability.md)); the record does not yet hold the variant description ([TF-09b §3](TF-09b-requirements-traceability-matrix.md)); TF-09 V&V; TF-06 |
| **16.2** | Software developed per **state of the art**: development lifecycle, risk management incl. **information security**, verification & validation | Yes | ◐ | Governing CMGG SOP **H11.1-OP5** ([README §1a](README.md)) → [TF-07 Software Lifecycle Plan](TF-07-software-lifecycle-plan.md); TF-06; TF-09; TF-13 |
| 16.3 | Mobile-platform-specific design considerations | N/A | N/A | Desktop browser in a controlled lab environment; no mobile intended use |
| **16.4** | Set out **minimum hardware / IT-network / IT-security requirements** incl. protection against unauthorised access | Yes | ◐ | [security-posture](../security-posture.md) (RBAC, audit, secrets); TF-13; [TF-15 §6](TF-15-instructions-for-use.md) IFU minimum requirements |
| 17 | Devices connected to / equipped with energy sources | N/A | N/A | — |
| 18 | Protection against mechanical/thermal risks | N/A | N/A | — |
| 19 | Devices for self-testing / near-patient testing | N/A | N/A | Professional use only, accredited lab (TF-01) |
| 20.1–20.2 | **Information supplied with the device** — label & IFU, comprehensible to intended user | Yes | ◐ | [TF-15](TF-15-instructions-for-use.md) IFU & labelling (draft); the label in the footer of every page and report; in-app user guide (`/docs`) and reference docs |
| 20.4.1 | Intended-purpose elements stated | Yes | ✅ | [TF-01](TF-01-intended-purpose.md) §2 |
| 20.4.1 | Limitations, warnings, residual-risk information, required upstream conditions | Yes | ◐ | TF-01 §4; TF-15; provenance footer |
| 20.4.1 | Version / build identification accessible to user | Yes | ◐ | Shown in the footer of every page and every report, and frozen into each signed report ([TF-15 §1](TF-15-instructions-for-use.md)). Scheme: [TF-18 §2](TF-18-change-configuration-management.md); **🔲** `Sxxxx` not yet assigned |

\* Items marked N/A* are formally non-applicable but have an analogous control noted, because
the device's interpretive nature changes how the classical IVD wording maps.

## Requirements not (yet) fully met — Article 5(5)(f)(iii) reasoned justifications

The Article 5(5) declaration must list any GSPR **not fully met**, with justification.
Current open items (to be closed before declaration, or carried with justification):

| § | Gap | Plan / justification |
| --- | --- | --- |
| 5 | Summative usability evaluation not yet run | TF-12 is drafted (use specification, hazard-related scenarios, UI risk controls); run the summative evaluation before clinical go-live. Interim: professional-only users, training under ISO 15189 competency. |
| 9.1 | Performance evaluation not yet executed | TF-10 plan defined (concordance against the validated assays, validation sets in TF-10 §2); TF-11 report pending execution. |
| 13/16.4 | Deployment-level controls: encryption at rest, TLS to the datastores, secrets management, network restriction, PHI download audit | Codified for Google Cloud in `terraform/` but never applied; the download audit (S-4) is still open ([TF-13 §3](TF-13-cybersecurity.md)). Close before clinical go-live. |
| 16.2 | Lifecycle documentation per the governing SOP H11.1-OP5 to be completed | Codebase practices exist (CI gates, tests, audit) and the SOP defines the process; the bio-IT ingangsvalidatie (H11.1-F12.2) + TF-07/TF-09 formalize it. |
| 20.1 | IFU not yet issued as a controlled document | [TF-15](TF-15-instructions-for-use.md) and the in-app docs exist as its basis. |

> No GSPR is proposed to be *permanently* unmet; all open items have a remediation path.
> The declaration should be signed only once these are closed or carry an accepted,
> documented justification.
