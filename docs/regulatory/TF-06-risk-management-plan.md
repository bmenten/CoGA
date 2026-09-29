# TF-06 — Risk Management Plan

| Field | Value |
| --- | --- |
| Document ID | TF-06 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead + quality› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Standard | EN ISO 14971:2019 (+ ISO/TR 24971 guidance) |

> This plan defines how risk is managed across CoGA's lifecycle. The living **Risk
> Management File (RMF)** — the full hazard/FMEA analysis with risk estimates and control
> verification — is maintained as a controlled spreadsheet/register referenced here; §6
> seeds it with the principal hazards.
>
> **In CMGG QMS terms (H11.1-OP5 §5.1.3):** the risk analysis is part of the **bio-IT
> ingangsvalidatie dossier** (template **H11.1-F12.2**). CMGG applies a **risk-based**
> approach that treats everything around development, implementation and integration as a
> potential risk to the **confidentiality, integrity and availability (CIA)** of the
> information (ISO/IEC 27001), to be mitigated with appropriate measures — this complements
> the ISO 14971 patient-safety hazards below; the RMF covers both. The risk analysis is
> **reviewed at each minor/major change** ([TF-18](TF-18-change-configuration-management.md)).

---

## 1. Scope

All five clinical applications of CoGA (TF-01) and the full software lifecycle (development,
release, clinical use, change, decommissioning). Upstream wet-lab/bioinformatics risks are
**out of scope** here (covered by their own validation) **except** at the interface: CoGA's
assumptions about, and handling of, its inputs.

## 2. Risk management process & responsibilities

| Activity | Responsible | Output |
| --- | --- | --- |
| Risk planning | Software lead + quality | This plan |
| Hazard identification & risk analysis | Multidisciplinary: developer(s), clinical lab geneticist(s), quality | RMF |
| Risk evaluation & control | Software lead + clinical lead | Risk-control measures, design changes |
| Verification of controls | Developer + reviewer | TF-09 V&V evidence |
| Residual-risk & benefit-risk review | Lab director | Sign-off |
| Production & post-production monitoring | Quality + PMS owner | [TF-16 PMS](TF-16-post-market-surveillance-plan.md) feedback into RMF |

## 3. Risk acceptability policy

Risk is estimated as **Severity × Probability**; for software the probability of a
*systematic* fault is treated per ISO 14971 / IEC 62304 guidance (probability of the fault
occurring is hard to estimate, so emphasis is on **severity** and on rigour of controls,
verification, and detectability).

**Severity scale (clinical-genetics-oriented):**

| Level | Definition (example) |
| --- | --- |
| S1 Negligible | Inconvenience; no impact on result (e.g. cosmetic UI issue) |
| S2 Minor | Recoverable delay/rework; caught at routine review |
| S3 Serious | Could contribute to a wrong interpretation if not caught at review |
| S4 Critical | Could lead to a wrong clinical result reaching the report (e.g. missed pathogenic variant, wrong embryo selected, wrong fetal-risk call) |
| S5 Catastrophic | Wrong result drives an irreversible clinical decision (e.g. embryo transfer/discard, termination, missed treatable condition) |

**Acceptability:** Any hazardous situation with potential severity ≥ S4 requires risk
controls reducing risk **as far as possible (AFAP)** and explicit residual-risk acceptance
by the lab director; reliance on "the professional will catch it at sign-out" is a
**legitimate but not sufficient sole control** for S4/S5 — at least one device-level control
(design, alert, or QC gate) is required in addition. **🔲 INPUT NEEDED:** confirm the
severity/probability matrix and acceptability thresholds with CMGG quality.

## 4. Risk-control hierarchy (ISO 14971 §7, IVDR Annex I §4)

1. **Inherent safety by design** — e.g. server-side recomputation of ACMG scores; derived calls gated by QC; immutable/append-only audit and sign-out; "uninformative" safe-default for unresolved haplotypes.
2. **Protective measures / alerts** — e.g. evidence-drift badges; sign-out gates: evidence drift, a failed or unverifiable Sample QC and an incomplete import block sign-out until a reason is recorded, and a family on an unvalidated assembly cannot be signed out at all; Mendel-error and informative-marker QC; fetal-fraction confidence interval and disagreement flag; coverage funnels.
3. **Information for safety** — IFU/labelling warnings (TF-15), in-app guidance, limitations (TF-01 §4), provenance footer.

## 5. Existing controls already implemented (control inventory)

These are live and serve as risk controls; they are cited from the RMF:

- Reproducibility & integrity: version manifest, per-classification evidence snapshot, evidence-drift detection, immutable clinical audit of every review change (small variants, structural variants and CNVs, a cleared review included) and of each sign-out (REQ-TRACE-004), content-hashed frozen sign-out, server-side score recompute; sign-out gates for evidence drift, Sample QC, an incomplete import and assembly scope (REQ-TRACE-006, REQ-TRACE-012, REQ-TRACE-013, REQ-TRACE-009); the report page checked against the latest signed version. ([clinical-traceability.md](../clinical-traceability.md))
- Access & accountability: project-scoped RBAC, admin-gated mutations, append-only access audit, refuse-to-start on default secrets. ([security-posture.md](../security-posture.md))
- Analytic QC surfaced to the user: NIPT fetal-fraction CI + category-8 false-negative signal; PGT Mendel-error rate, informative-marker counts, recombination-proximity warnings, safe "uninformative" fallback, staleness-guarded haplotype precompute. ([monogenic-nipt.md](../monogenic-nipt.md), [haplotype-segregation-analysis.md](../haplotype-segregation-analysis.md))
- **Sample identity & integrity:** the **Sample QC** (checks listed in TF-01 §4 condition 7), reviewed before sign-out, with the sign-out gate of REQ-TRACE-012 — the explicit control for H4 (REQ-QC-001/002; `test_sample_integrity_qc.py`, `test_report_signout.py`).

## 6. Principal hazards (RMF seed)

Starter set; the RMF expands each with hazardous situation, sequence of events, severity,
control, residual risk, and V&V reference.

| # | Hazard / failure mode | Application(s) | Sev | Existing/needed control |
| --- | --- | --- | --- | --- |
| H1 | **Missed pathogenic variant** (over-aggressive filter; variant dropped by QUAL/artifact/frequency filter) | All | S4–S5 | Filter funnels with drop counts; configurable, reviewable filters; verification of filter logic (TF-09); one genotype classification for every filter, inheritance check, count and presence check, so haploid (chrM, male chrX/chrY) and multi-allelic calls are not invisible (REQ-DATA-012); the de novo patterns read a son's hemizygous chrX/chrY call (REQ-DIAG-002); NIPT artifact seeding never lists a common variant or one with a ClinVar pathogenic, likely pathogenic or conflicting record (REQ-NIPT-004); IFU on filter limitations |
| H2 | **False-positive / mis-prioritized variant** drives wrong conclusion | All | S3–S4 | ACMG decision support overridable; review/sign-out; internal/external frequency context; male chrX/chrY de novo calls use the sex-aware rule (REQ-DIAG-002) |
| H3 | **Wrong ACMG class** from incorrect criterion auto-positioning | All | S3–S4 | Overridable criteria, server recompute, evidence snapshot, drift surfacing; classifier verification vs reference set (TF-10/11) |
| H4 | **Sample/pedigree swap, contamination or wrong inheritance model** → wrong segregation/interpretation | PGT, WGS, NIPT, mito (3.5) | S5 | **Sample QC** (TF-01 §4 condition 7; REQ-QC-001/002) — **mandatory review before sign-out**; sign-out is blocked when a check fails or cannot confirm a declared relationship or a sample's identity, unless a reason is recorded (REQ-TRACE-012); per-child Mendel-error rate; NIPT paternity counts only sites with a usable father call, so a father without calls cannot look like a match; for mito (3.5) the analyst compares the samples' mtDNA haplogroups (a manual check: CoGA lists them but does not compare them); verified by `test_sample_integrity_qc.py` and `test_report_signout.py` |
| H5 | **Wrong embryo classification** (recombination near ROI, sparse informative markers, mis-coloured relative, embryo data missing at the ROI, embryo sex not recorded, a male embryo at a pseudo-autosomal locus) | PGT | S5 | Recombination-aware blocks, raw-marker overlay, informative-marker count, "uninformative" safe default, donor-side greying; an "unaffected" or "carrier" call only on the embryo's own haplotype seen across the ROI, else "uninformative"; an X-linked recessive call that depends on an unrecorded sex assumes neither sex and shows both calls; a male read as one X only outside the PARs of the family's assembly (two copies where they are not known); verification on the PGT embryo concordance (TF-10) |
| H6 | **Incorrect fetal-fraction estimate** → mis-categorized fetal genotype | NIPT | S4–S5 | Fetal fraction from paternally inherited sites (category 7) with CI and site count, one estimate for every NIPT view (REQ-NIPT-001); categories limited to the fetal states the father's genotype allows, a father call below the minimum depth counting as no call (REQ-NIPT-002); the category-8 signal (paternal homozygous variants missing from the cfDNA); external-FF disagreement flag; verification on the NIPT concordance (TF-10) |
| H7 | **Missed/incorrect aneuploidy or large SV** | PGT | S4–S5 | SV/segment tracks; **define detection thresholds & verify** (> 10 Mb claim) in TF-10 |
| H8 | **Stale reference/annotation data** silently changes interpretation | All | S3–S4 | Version manifest, evidence-drift detection, sign-out blocked on drift until it is acknowledged with a reason (REQ-TRACE-006) |
| H9 | **Reproducibility failure** — report cannot be regenerated as signed | All | S4 | Each signed version is frozen and content-hashed, re-checked on read and downloadable; the report page is checked against the latest signed version and marked when it differs ("Not the signed report", "Not verified"); every report names the build that produced it (REQ-UI-008). **🔲 Rendering a signed report from its frozen record is not yet implemented** (REQ-TRACE-007, TF-09b §3). |
| H10 | **Use error** — analyst misreads a QC warning / signs out wrong candidate | All | S4–S5 | Usability engineering (TF-12); IFU and the in-app label, with the CMGGMC problem route (REQ-UI-008); UI alerts; training under ISO 15189 competency |
| H11 | **Unauthorized access / data integrity / confidentiality breach** (PHI) | All | S3–S4 | RBAC, audit; encryption, TLS and network restriction codified in `terraform/` but not yet applied ([TF-13 §3](TF-13-cybersecurity.md)); TF-14 |
| H12 | **Wrong assembly / off-scope panel/assay used** | All | S4 | Assembly-scoped storage; **off-scope assembly guard**: sign-out refused, without override, for an assembly outside the validated set (default GRCh38) or an unresolved one, and a "not validated for clinical use" label on the family and report pages (REQ-TRACE-009); **gene-panel coordinates scoped per assembly**: a family's panel filter reads only its own assembly's coordinates (REQ-CARR-004); IFU |
| H13 | **Incorrect mtDNA heteroplasmy / maternal-inheritance interpretation** | mito (3.5) | S4 | Heteroplasmy quantification + maternal-transmission logic with QC; Sample QC and the analyst's haplogroup comparison (H4); ACMG review/sign-out; unit tests (`test_mitochondrial_analysis.py`); clinical verification in TF-10 §3.5 |
| H14 | **Interpretation of a sequencing run of inadequate quality** — a false negative is reported from a run with too little depth, too short reads or too little aligned yield to support it | All | S4 | Per-sample sequencing QC captured at import and evaluated against admin-set acceptance limits per assay profile; the worst state is shown in the family members table, distinguishable **without relying on colour alone**, with the breaching metric and the limit it crossed named on inspection. A metric with **no configured limit is reported as not assessed, never as a pass**, so an unconfigured gate cannot read as a green one. Residual risk: the limits are lab configuration — with none entered nothing is gated, and the analyst-at-sign-out control is the only barrier (cf. the §3 acceptability rule). |
| H15 | **Report signed out by a person without signing authority** — any authenticated project member, the `viewer` role included, can sign out a report; RBAC has global roles only and no signer role | All | S4 | **Procedural control** (accepted residual, #519): only personnel the laboratory has authorised as signatories sign out reports (CMGG SOP; TF-15 §3 item 7). **Detective controls:** every sign-out records the signer in the append-only, hash-chained sign-out record and the clinical audit trail, is versioned (an amendment is a new version) and shows the signer on the report. See §7. |
| H16 | **Report signed out on partly imported data** — a package import failed for some datasets and left the family partly loaded, so the analysis and the report can lack whole datasets (for example every structural variant) | All | S4 | Fail-clean import: a new family with nothing imported is removed, and a failed overwrite is put back to its state before the import; otherwise the family is flagged import-incomplete with the failed datasets and the import job that holds their errors, and the job ends `failed`. An *Import incomplete* warning on every family page, printed with the report; **sign-out is refused unless the signer acknowledges it with a reason**, frozen with the flag into the signed record and the audit trail (REQ-TRACE-013) |

## 6a. IVDR Annex I software risk table (H11.1-F12.2 / H14.4-OP1)

The CMGG in-house software validation form **H11.1-F12.2** carries a risk table keyed to the
**IVDR Annex I** software requirements, scored **hoog / middelhoog / zeer laag / NVT** per the
risk procedure **H14.4-OP1** (FMEA / What-if; templates H14.4-OP1-B2/B3). The ISO 14971
hazards in §6 map onto those IVDR categories — and onto the BELAC ISO 15189 triad
**patiëntveiligheid · continuïteit · data-integriteit** — as follows:

| IVDR Annex I category (F12.2) | CoGA hazards / controls |
| --- | --- |
| **§13.2** interaction with environment / IT-environment, interference, koppelingen (wrong result, wrong patient-material identification) | H4 (sample/pedigree swap → Sample QC), H8 (stale reference data → drift), H12 (off-scope assembly/panel); upstream-pipeline boundary (TF-01 §4). |
| **§14** measuring function (analytical error, correct units) | H6 (NIPT fetal-fraction estimate — a derived QC measure with CI); otherwise qualitative/interpretive. |
| **§16.1** repeatability / reliability / performance; **single-fault-safe** | H9 (frozen, content-hashed signed record; the report page checked against it), H5/H6 safe "uninformative"/low-confidence fallbacks (REQ-PERF-003), server-side recompute, H16 (a dataset that fails to import is flagged, shown and gates sign-out). |
| **§16.2** state-of-the-art development; information security; V&V | TF-07/TF-09, TF-13 (infosec), H11. |
| **§16.3** mobile computing platforms | N/A — desktop browser in the accredited lab (TF-01). |
| **§16.4** minimum hardware / IT-network / IT-security (unauthorized access) | H11 (RBAC, audit); deployment items S-1…S-8 ([TF-13 §3](TF-13-cybersecurity.md)). |

Triad rollup: **patiëntveiligheid** = H1–H7, H10, H12, H13, H14, H16 (wrong/missed clinical call);
**data-integriteit** = H8, H9, H11, H15 (drift, reproducibility, integrity/access, who signed);
**continuïteit** = availability and back-up (backups are codified in `terraform/` but not yet
applied, and a restore drill is due before go-live — [deployment-gcp.md §12.3](../deployment-gcp.md)).
Each row's residual score and action is recorded in the RMF and copied into the F12.2 table.

## 7. Residual risk & benefit-risk

After controls, each residual risk is re-estimated in the RMF; overall residual risk and the
benefit-risk balance (the clinical value of faster, reproducible, traceable interpretation
vs. residual interpretive risk under professional oversight) are evaluated and signed off by
the lab director. Inputs: TF-11 performance results, PMS data.

**Accepted residual — sign-out authority (H15, decision of 2026-09-28, #519).** The software
does not separate the authority to sign out a report from project membership. The laboratory
keeps that authority procedural: only personnel it has authorised as signatories sign out,
which is how signing authority is already governed for the laboratory's other reports under
ISO 15189. The residual risk is accepted because (a) a sign-out cannot be anonymous or
silent: the signer's identity is frozen into the append-only, hash-chained sign-out record
and the audit trail, and shown on the report; (b) a wrongly issued sign-out is detectable in
that record and correctable by an amendment, which is a new version and never overwrites the
old; and (c) sign-out is a deliberate, gated action (drift, Sample-QC, incomplete-import and assembly-scope gates),
not a side effect. This is an **exception to the §3 rule** that an S4 hazard needs a
device-level control besides the professional at sign-out: the device's controls for H15 are
detective, not preventive. **Re-evaluate** if an unauthorised sign-out is ever found through
PMS (TF-16), or when a signer role is added to RBAC. Pending lab-director confirmation.

## 8. Production & post-production information

PMS ([TF-16](TF-16-post-market-surveillance-plan.md)) and the vigilance/CAPA process
([TF-17](TF-17-vigilance-capa.md)) feed real-world experience (incidents, near-misses,
user feedback, drift events) back into the RMF; the RMF is reviewed at each release and at
defined intervals.
