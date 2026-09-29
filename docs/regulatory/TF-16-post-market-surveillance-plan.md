# TF-16 — Post-Market Surveillance Plan (incl. PMPF)

| Field | Value |
| --- | --- |
| Document ID | TF-16 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG quality + software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Basis | IVDR Art. 78–81 (PMS), Annex III; **Article 5(5)(i)** (review of clinical-use experience); MDCG guidance |

> Article 5(5)(i) explicitly requires the institution to **review experience gained from
> clinical use and take corrective action**. This plan defines that surveillance loop, scaled
> to an in-house device (no PSUR filing to a notified body, but an equivalent **internal
> periodic review**), and a **post-market performance follow-up (PMPF)** for ongoing
> concordance with the validated assays.

---

## 1. PMS objectives
1. Confirm CoGA's performance and safety hold in routine clinical use.
2. Detect new hazards, use errors, or edge cases not seen in validation.
3. Confirm the validated scope (panels/assays/assemblies) remains adequate.
4. Feed findings into the risk file (TF-06), performance evaluation (TF-10/11), usability (TF-12), and design.

This is the **operationele fase** of H11.1-OP5 §6: incidents and feature requests are managed
via **CMGGMC** (probleemmeldingen / suggestions), and monitoring and logging track execution and
errors (on Google Cloud through Cloud Logging, [deployment-gcp.md §12.6](../deployment-gcp.md)).
**🔲 Alerting is not yet configured** and must be set up before go-live. A hard requirement:
**every analysed sample is linked to the software version (`Sxxxx` `x.y.z`) used for it**, so
any signal can be scoped to the exact version. CoGA meets it through the signed report
([TF-18 §2](TF-18-change-configuration-management.md)).

## 2. Data sources (proactive & reactive)
| Source | What it tells us | Mechanism |
| --- | --- | --- |
| Incidents / near-misses | Safety signals | TF-17 incident log; **CMGGMC probleemmeldingen** |
| User feedback / complaints | Usability, defects, gaps | **CMGGMC** suggestions/intake |
| **Concordance monitoring (PMPF)** | Ongoing agreement with validated comparator on routine cases | Periodic sampling/audit; the validation design (TF-10) continued in-life |
| **Evidence-drift events** | Reference-data changes affecting prior interpretations | Built-in per-family drift check on the report page ([clinical-traceability.md](../clinical-traceability.md)); there is no cross-case drift list yet, so a drift-rate indicator (§4) needs a database query |
| Audit logs | Misuse, access anomalies, usage patterns | Append-only audit |
| Defect/anomaly tracker | Software-quality trend | TF-09 / issue tracker |
| Literature / database updates | New variant–disease knowledge, guideline changes | Reference-data monitoring (TF-08) |
| SOUP/security advisories | Vulnerabilities | TF-13 |

## 3. PMPF (post-market performance follow-up)
- Continue periodic **concordance checks against the validated assays** beyond the initial validation cohorts, sampling routine cases per application (frequency/sample size **🔲 INPUT NEEDED**, e.g. quarterly N per application).
- Specifically monitor the **safety-critical error modes** (missed at-risk/affected calls, missed causal variant) and the **uninformative/edge-case rates**.
- Reconfirm performance after any reference-data, panel, assay, or algorithm change (TF-18).

## 4. Indicators & thresholds (define)
‹Define quantitative PMS indicators and action thresholds, e.g.: concordance drop below the
TF-10 acceptance level; any safety-critical discordance; drift-event rate; recurring use
error; defect-recurrence. Crossing a threshold triggers investigation + CAPA (TF-17).›

## 5. Review cadence & outputs
- **Periodic PMS review** (e.g. annually, **🔲 confirm**) consolidating §2 sources into a **PMS report** (the in-house equivalent of the IVDR PMS report/PSUR): findings, trends, actions, updates to risk/performance/usability files, and a benefit-risk re-confirmation.
- Ad-hoc review on any serious signal.
- Outputs feed TF-06 (risk), TF-10/11 (performance), TF-12 (usability), TF-05 (equivalence re-check), and the design backlog.

## 6. Responsibilities
‹PMS owner (quality), software lead, clinical leads per application, lab director sign-off.›

## 7. Records
PMS reports, PMPF results, the feedback log, and resulting actions are retained per the CMGG
QMS and are available to the competent authority (FAMHP) on request (Art. 5(5)(e)).
