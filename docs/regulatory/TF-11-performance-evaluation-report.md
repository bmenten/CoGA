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

> This report records the **results** of the validation studies defined in TF-10 and states
> the **claimed performance** and **validated scope**. A competent authority reads it to judge
> GSPR §9.1. Sections are stubbed; fill them on execution.
>
> **CMGG forms.** The signed CMGG validation forms draw on this report as their consolidated
> evidence; [TF-18 §4b](TF-18-change-configuration-management.md) sets out which form is used
> when.

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
The 30 samples are not among the 22 families of the R NIPT-M v0.7.3 development: ‹confirm›.

| Metric | Result | Acceptance | Pass? |
| --- | --- | --- | --- |
| FF bias vs comparator (Bland–Altman) | ‹…› | within ‹±X› | |
| QC gate: fetal-specific recall per bin of expected fetal reads; samples failing the gate | ‹…› | ‹…› | |
| Category/inheritance concordance (autosomes; a daughter's and a son's chrX) | ‹…› | ≥‹threshold› | |
| Maternal inheritance accuracy per FF bin | ‹…› | ‹…› | |
| Locked diagnostic candidates: the comparator's diagnostic variants listed, per view; candidates per sample | ‹…› | ‹…› | |
| Recessive fetal status vs the fetal genotype; genes left undetermined | ‹…› | no affected fetus called carrier or unaffected | |
| Missed at-risk fetal calls in samples that pass the QC gate | ‹…› | 0 | |
| Low-FF/low-depth behavior | ‹…› | low-confidence, no forced call; the gate fails below its bound | |

### 3.5 Mitochondrial disease — ONT adaptive sampling (N ‹…›)
| Metric | Result | Acceptance | Pass? |
| --- | --- | --- | --- |
| mtDNA variant PPA/NPA vs comparator | ‹…› | ‹…› | |
| Heteroplasmy quantitation agreement (Bland–Altman) | ‹…› | within ‹±X%› | |
| Nuclear mito-gene causal-variant detection + ACMG concordance | ‹…› | 100% / within 1 tier | |
| Maternal-inheritance concordance | ‹…› | ‹…› | |
| Sample-swap detection (Sample QC + haplogroup comparison) | ‹…› | correct on controls | |

### 3.6 Rare-disorder diagnostics — short-read WGS (N ‹…›)
| Metric | Result | Acceptance | Pass? |
| --- | --- | --- | --- |
| Known causal SNV/indel among CoGA candidates | ‹…› | ‹…› | |
| ACMG-class concordance | ‹…› | ‹…› | |
| Inheritance / de-novo concordance | ‹…› | ‹…› | |

### 3.7 Conventional NIPT (N ‹…›)
Per claimed target (trisomy 21, trisomy 18, trisomy 13, sex-chromosome aneuploidies), on bounds
QA has set; twins reported as a stratum of their own. Bounds in force: ‹from the threshold
history›; ccnv version: ‹…›.

| Metric | Result | 95% CI | Acceptance | Pass? |
| --- | --- | --- | --- | --- |
| Sensitivity vs confirmed diagnosis | ‹…› | ‹…› | ‹…› | |
| Specificity | ‹…› | ‹…› | ‹…› | |
| PPV / NPV | ‹…› | ‹…› | ‹…› | |
| PPA / NPA / OPA vs the routine NIPT method | ‹…› | ‹…› | ‹…› | |
| No-result rate (fetal fraction below its bound) | ‹…› | | ‹…› | |
| Inconclusive rate | ‹…› | | ‹…› | |
| Fetal-fraction agreement (Bland–Altman), where the comparator gives one | ‹…› | | ‹…› | |
| False low risk on a claimed target | ‹…› | | ‹…› | |

Targets outside the claim (the other autosomes, the segmental CNVs): recorded, not evaluated as
claims. Discordance adjudication: ‹table — case, CoGA result, comparator, confirmed diagnosis,
root cause, action›.

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
