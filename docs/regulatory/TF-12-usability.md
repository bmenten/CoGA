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
| U1 | Analyst overlooks a low fetal-fraction / wide-CI warning and trusts a NIPT category call | H6 | FF gauge, CI and disagreement flag shown prominently; a fetal fraction that could not be loaded is said so on the NIPT page and report, never shown as "no estimate" or a count of 0 (#612, #613) |
| U2 | Analyst trusts a PGT embryo call despite recombination near the ROI, sparse informative markers, or missing embryo data at the ROI | H5 | Raw-marker overlay, informative-marker count, recombination warning, explicit "uninformative"; an embryo without its own haplotype across the ROI reads "uninformative", with a missing-data warning; the risk haplotype is told apart by line style, not colour alone, and drawn only on the ROI's chromosome; haplotypes that could not be loaded read "segregation not derived" (#577, #592, #614) |
| U3 | Analyst signs out while evidence has drifted, without realizing it | H8 | Drift badge; sign-out is blocked until the drift is acknowledged with a reason; a drift check that could not be made is said so, not shown as "no drift" (#612) |
| U4 | Analyst misreads the filter funnel and believes nothing was dropped when variants were filtered out | H1 | Explicit drop counts at each funnel stage; a track holding more than it can draw says "too many to display" instead of drawing part of the view (#590), and an empty view says "no region in view" (#603); a location filter that cannot be read is refused, never applied in part, and the NIPT search applies the filters it shows (#615) |
| U5 | Analyst signs out the wrong variant / wrong candidate set | H10 | Clear "report"-tagged set; a signed version is shown and printed from its record, and the live report is labelled as not the signed version and says whether it matches the latest; audit trail; a report whose family or reported variants could not be loaded is not shown, and a part that could not be loaded marks the printout incomplete (#612) |
| U6 | Analyst misreads a Mendel-error/QC flag indicating sample swap | H4 | Mendel-error rate per child with guidance; on the pedigree each sample's QC verdict is a ring whose line and badge (✓ ! ✕) tell pass, warn and fail apart, keeping the affected and carrier fills (#577); sign-out blocked on a failed or unverifiable Sample QC until a reason is recorded (REQ-TRACE-012) |
| U7 | Analyst acts on data from the wrong assembly/panel/assay scope | H12 | Assembly/assay context displayed; sign-out refused off the validated assembly (GRCh38), with a "Not validated for clinical use" label on the family and report pages, and panel coordinates scoped per assembly; if the project catalogue cannot be loaded, the pages say the validated scope is unconfirmed and the report is not prepared (#611) |
| U8 | Analyst loses half-finished review or ACMG input through a stray backdrop click or Escape, or two reviewers overwrite each other's classification | H3, H9 | Clinical dialogs — review, ACMG, family-member and QC cut-off among them — ask before discarding unsaved input, answer Escape from the moment they are drawn, trap focus and ignore a text-selection drag onto the backdrop (#547, #575, #601); a save against a review changed since it was loaded is refused and the current review shown (REQ-TRACE-010) |
| U9 | Two workstations colour the same coverage differently (per-browser gain/loss thresholds) and the analyst reads a CNV call from the colour | H1, H7 | The chart always shows the thresholds in use and marks custom (this-browser) values; Settings explains the scope and resets them (#547) |

## 4. User interface specification & risk controls
The UI-level risk controls above are requirements (traced in TF-09 RTM). Design principles:
safety-critical signals are **visible without extra navigation**, destructive/irreversible
actions (sign-out) require confirmation and are gated, derived calls always display their
QC basis, and **a clinical state is not conveyed by colour alone** (WCAG 1.4.1): a shape,
line style, glyph or text carries it too. #577 applies this to the pedigree QC ring, the
risk-haplotype line and the small-variant marks; #594 to the Review, Exclude and Report
toggles, whose pressed state carries a check mark and is announced as pressed to screen readers.
Two colour codes remain, each named in a tooltip: the pedigree's carrier-type half-fill, and the MODERATE versus LOW small-variant
dots. Every chart, track, ideogram and the pedigree is an image with a **text name that
states what it shows now** (WCAG 1.1.1): the counts and the salient items, or that it is
loading or failed to load, never a failure as "none" (#587). The charts' pointer-only
interactions have **keyboard or text equivalents** (WCAG 2.1.1):

| Pointer interaction | Equivalent |
| --- | --- |
| Dragging to zoom, or selecting on the ideogram | The chromosome view's start/end fields, gene or locus jump, and pan/zoom buttons |
| Clicking a chromosome on the genome overview, the APCAD chart or the Circos plot | The overview's chromosome buttons (#575) |
| Track tooltips, and clicks through to a variant or CNV | The small-variant and SV tables, with their IGV and View links, and the clinical CNV explorer |

The point values of the coverage and APCAD charts have no text equivalent beyond the charts'
names, and are read from the chart. In-app guidance — the user guide at `/docs` and the
reference docs at `/docs/reference/<slug>` — is part of the information for safety (TF-15).

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
