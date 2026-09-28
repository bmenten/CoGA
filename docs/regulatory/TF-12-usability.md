# TF-12 — Usability Engineering File

| Field | Value |
| --- | --- |
| Document ID | TF-12 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead + a clinical lab geneticist (user rep)› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Standard | IEC 62366-1:2015+A1:2020; supports IVDR Annex I §5 & §16 |

> Usability engineering for a clinical interpretation tool focuses on **use errors that
> could lead to a wrong clinical conclusion**. The objective is to show that intended users
> can operate CoGA safely, and that the safety-critical interactions (acting on QC warnings,
> sign-out, drift acknowledgment) are not error-prone.

---

## 1. Use specification (62366-1 §5.1)

| Element | Specification |
| --- | --- |
| Intended user profile | Trained clinical laboratory geneticists / molecular biologists / clinical scientists; domain experts in variant interpretation; not patients. |
| Use environment | ISO 15189-accredited clinical genetics laboratory; desktop workstation; professional, non-time-critical (no emergency-use) context. |
| Operating principle | Filter/visualize/interpret validated genomic data; pre-evaluations are reviewed and confirmed; results are signed out. |
| Frequency/training | Routine professional use; users trained and competency-assessed under the ISO 15189 QMS. |

## 2. User interface characteristics & primary operating functions

Primary functions (the ones users perform to reach a clinical conclusion): apply/adjust
filters; review candidate variants and annotations; run/read the application-specific
analysis (NIPT categories, PGT embryo calls, SV/CNV); read QC signals; classify (ACMG,
overridable); tag/note; **sign out / amend**; acknowledge evidence drift.

## 3. Hazard-related use scenarios (62366-1 §5.3–5.5)

Derived from the risk file (TF-06); these are the scenarios where a **use error** could
cause harm and that the summative evaluation must cover:

| ID | Hazard-related use scenario | Linked hazard | UI risk control |
| --- | --- | --- | --- |
| U1 | Analyst overlooks a low fetal-fraction / wide-CI warning and trusts a NIPT category call | H6 | FF gauge + CI + disagreement flag prominently surfaced |
| U2 | Analyst trusts a PGT embryo call despite recombination near the ROI or sparse informative markers | H5 | Raw-marker overlay, informative-marker count, recombination warning, "uninformative" state |
| U3 | Analyst signs out while evidence has drifted, without realizing it | H8 | Drift badge + sign-out **409 gate** requiring explicit acknowledgment |
| U4 | Analyst misreads the filter funnel and believes nothing was dropped when variants were filtered out | H1 | Explicit drop counts at each funnel stage |
| U5 | Analyst signs out the wrong variant / wrong candidate set | H10 | Clear "report"-tagged set, frozen snapshot preview, audit trail |
| U6 | Analyst misreads a Mendel-error/QC flag indicating sample swap | H4 | Mendel-error rate surfaced per child with guidance |
| U7 | Analyst acts on data from the wrong assembly/panel/assay scope | H12 | Assembly/assay context displayed; off-scope guard (✅ #515: sign-out refused off `VALIDATED_ASSEMBLIES`, "Not validated for clinical use" label on family and report pages; panel coordinates scoped per assembly) |
| U8 | Analyst loses half-finished review or ACMG input through a stray backdrop click or Escape, or two reviewers overwrite each other's classification | H3, H9 | Clinical dialogs ask before discarding unsaved input, close on Escape, trap focus and ignore a text-selection drag onto the backdrop (#529); a save against a review changed since it was loaded is refused and the current review shown (#513) |
| U9 | Two workstations colour the same coverage differently (per-browser gain/loss thresholds) and the analyst reads a CNV call from the colour | H1, H7 | The chart always shows the thresholds in use and marks custom (this-browser) values; Settings explains the scope and resets them (#529) |

## 4. User interface specification & risk controls
The UI-level risk controls above are requirements (traced in TF-09 RTM). Design principles:
safety-critical signals are **visible without extra navigation**, destructive/irreversible
actions (sign-out) require confirmation and are gated, and derived calls always display their
QC basis. In-app guidance lives at `/docs` (the user guide) and is part of "information for
safety" (TF-15).

## 5. Evaluation plan

- **Formative evaluation** (iterative, during development): heuristic review and walkthroughs of the hazard-related scenarios with one or more user reps; findings feed UI changes. ‹Record sessions/findings.›
- **Summative evaluation** (validation): representative intended users (target **n ≥ 15** per distinct user group, **🔲 confirm**) perform the hazard-related use scenarios (U1–U9) on realistic cases without coaching; **use errors and difficulties are recorded and risk-assessed**. Acceptance: no uncontrolled use error that could lead to a wrong clinical conclusion; residual use-related risk acceptable (TF-06).
- Conducted on the release candidate; re-evaluated when a safety-critical UI element changes (TF-18).

## 6. Known use problems & field feedback
Use-related complaints/near-misses from clinical use feed back via PMS (TF-16) and are
re-assessed here. Any new use error → risk file + (if needed) UI change + CAPA (TF-17).

## 7. Records
Use specification, scenario list, formative findings, summative protocol + results, and the
use-error analysis are retained per the CMGG QMS. **🔲 ACTION:** schedule and run the
summative evaluation before clinical go-live.
