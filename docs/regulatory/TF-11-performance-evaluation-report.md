# TF-11 — Performance Evaluation Report

| Field | Value |
| --- | --- |
| Document ID | TF-11 |
| Version | v0.1 DRAFT (template — pending study execution) |
| Status | **Awaiting data.** Structure defined; results to be populated when the TF-10 studies run. |
| Owner | ‹Clinical lead per application + software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Basis | IVDR Annex XIII Part A; executes [TF-10 Performance Evaluation Plan](TF-10-performance-evaluation-plan.md) |

> This is the report that records the **results** of the validation studies defined in
> TF-10 and states the **claimed performance** and **validated scope**. It is the document
> a competent authority reads to judge GSPR §9.1. Sections are stubbed; fill on execution.
>
> **CMGG forms.** This report is the consolidated evidence the signed CMGG validation forms
> draw from; which form is used when is set out in
> [TF-18 §4b](TF-18-change-configuration-management.md).

---

## 1. Scope & references
Executes TF-10. Device version under test: ‹X.Y.Z (git ‹hash›)›. Reference-data versions at
test: ‹from the version manifest›. Comparator assays: ‹per application — TF-10 §2›.

## 2. Scientific validity
Summary of the established variant–condition associations relied upon (ACMG/AMP, ClinGen,
gnomAD, ClinVar, GenCC, inheritance genetics). ‹Cite the basis; not re-established empirically.›

## 3. Analytical & clinical performance — results

### 3.1 Expanded carrier screening (BeGECS, 50 couples)
| Metric | Result | 95% CI | Acceptance | Pass? |
| --- | --- | --- | --- | --- |
| Variant-level carrier OPA | ‹…› | ‹…› | ≥99% ‹confirm› | ‹…› |
| Couple-level at-risk concordance | ‹…› | ‹…› | 100% ‹confirm› | ‹…› |
| Reportable (P/LP) concordance | ‹…› | | all | ‹…› |
Discordance adjudication: ‹table — case, CoGA call, comparator, root cause, action›.

### 3.2 PGT (100 embryos)
| Metric | Result | 95% CI | Acceptance | Pass? |
| --- | --- | --- | --- | --- |
| Informative embryo segregation concordance | ‹…› | | 100% ‹confirm› | |
| False "unaffected" on at-risk embryo | ‹…› | | 0 | |
| Direct-mutation PPA/NPA | ‹…› | | ‹…› | |
| Aneuploidy per-chromosome OPA | ‹…› | | ‹…› | |
| Large SV (> 10 Mb) concordance | ‹…› | | ‹…› | |
| Uninformative rate (incl. donor families) | ‹…› | | characterized | |

### 3.3 Rare-disorder WGS trios (30)
| Metric | Result | Acceptance | Pass? |
| --- | --- | --- | --- |
| Known causal variant among CoGA candidates | ‹…› | 100% | |
| Per-data-type PPA (SNV/indel, SV, repeat, Paraphase, mtDNA) | ‹…› | ‹…› | |
| ACMG-class concordance | ‹…› | within 1 tier / same actionability | |
| Inheritance / de-novo concordance | ‹…› | ‹…› | |

### 3.4 Monogenic NIPT (30)
| Metric | Result | Acceptance | Pass? |
| --- | --- | --- | --- |
| FF bias vs comparator (Bland–Altman) | ‹…› | within ‹±X› | |
| Category/inheritance concordance | ‹…› | ≥‹threshold› | |
| Missed at-risk fetal calls | ‹…› | 0 | |
| Low-FF/low-depth behavior | ‹…› | low-confidence, no forced call | |

### 3.5 Mitochondrial disease — ONT adaptive sampling (N ‹…›)
| Metric | Result | Acceptance | Pass? |
| --- | --- | --- | --- |
| mtDNA variant PPA/NPA vs comparator | ‹…› | ‹…› | |
| Heteroplasmy quantitation agreement (Bland–Altman) | ‹…› | within ‹±X%› | |
| Nuclear mito-gene causal-variant detection + ACMG concordance | ‹…› | 100% / within 1 tier | |
| Maternal-inheritance concordance | ‹…› | ‹…› | |
| Sample-swap detection (Sample QC + haplogroup comparison) | ‹…› | correct on controls | |

## 4. Reproducibility & robustness
‹Content-hash reproducibility result; inter-operator subset; degraded-input behavior; GIAB analytical baseline (TF-10 §2).›

## 5. Discordance analysis & defects
‹All discordances, root causes, any CoGA defects found, fixes + re-test, residual.›

## 6. Conclusion — claimed performance & validated scope
- **Claimed performance:** ‹per application›.
- **Validated scope:** assemblies ‹…›, panels/gene sets ‹…›, assays ‹…›, populations ‹…›, device version ‹…›, reference-data versions ‹…›. **Use outside this scope is off-label** and feeds the IFU (TF-15) and limitations (TF-01 §4).
- **Benefit-risk:** ‹statement feeding TF-06 §7›.
- **Outcome:** ‹pass / conditional / fail + CAPA›.

## 7. Post-market performance follow-up
Ongoing monitoring plan → [TF-16 PMS/PMPF](TF-16-post-market-surveillance-plan.md); re-validation triggers → [TF-18](TF-18-change-configuration-management.md).
