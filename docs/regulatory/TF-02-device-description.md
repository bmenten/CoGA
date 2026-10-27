# TF-02 — Device Description & Specification

| Field | Value |
| --- | --- |
| Document ID | TF-02 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Device | CoGA, software version ‹X.Y.Z› |

---

## 1. Device identification

| Attribute | Value |
| --- | --- |
| Name | CoGA — Comprehensive Genomic Analysis |
| Type | Software as a Medical Device (in-house IVD), standalone (MDSW per MDCG 2019-11) |
| Manufacturer | Center for Medical Genetics Ghent (CMGG), Ghent University Hospital |
| Device identifier | CMGGMC **software number `Sxxxx`** (assigned in the CMGGMC ICT module per H11.1-OP5) — the UDI-DI-equivalent for this in-house device. **🔲 INPUT NEEDED:** the assigned `Sxxxx`. |
| Version identifier | Semantic version `x.y.z` + git commit hash ([TF-18 §2](TF-18-change-configuration-management.md)). Served at `/api/version` and shown in the footer of every page and every report ([TF-15 §1](TF-15-instructions-for-use.md)); frozen into every signed report and shown in its sign-out block. |
| Form of delivery | Server-deployed web application, used internally at CMGG; no physical media, no transfer to third parties. |

## 2. Intended purpose

See [TF-01 Intended Purpose Statement](TF-01-intended-purpose.md). CoGA is decision-support
software for genomic variant interpretation across five clinical applications (monogenic
NIPT, expanded carrier screening, PGT, rare-disorder diagnostics, and combined mtDNA +
nuclear mitochondrial-disease testing from ONT long-read adaptive sampling).

## 3. Device boundary

CoGA's regulated boundary **begins at validated digital inputs and ends at the closed, frozen
analysis in CoGA**: *annotated VCF → closed, frozen analysis in CoGA; the clinical report is
drafted and medically signed in the EPD and refers 1:1 to that analysis* (owner decision,
2026-10-14). The analyst's sign-out is the analytical sign-off: it closes the analysis and
freezes it into a signed version (§6, §8). The medical sign-off is not given in CoGA: the
clinical report is drafted and medically signed in the hospital's electronic patient record
(EPD), and each report refers to exactly one signed version (1:1). Everything upstream is out
of scope for this device and is covered by separate validation/accreditation; the EPD,
downstream, is outside the device too.

```
  ┌─────────────────────── OUT OF SCOPE (separately validated & accredited) ────────────────────────┐
  │  Specimen → wet-lab assay → sequencing → alignment/variant-calling → annotation (Nextflow)      │
  └────────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                   │  annotated VCF, coverage/segment/CNV/APCAD tracks,
                                                   │  repeat/Paraphase/mtDNA results, pedigree metadata
  ┌────────────────────────────────────────────────▼─────────────────────────── CoGA (THIS DEVICE) ─┐
  │  Ingestion → storage (Postgres metadata + ClickHouse variants) → filtering/aggregation →        │
  │  pedigree-aware analysis (trio, NIPT, PGT haplotyping, SV/CNV, repeats, mtDNA, Paraphase) →     │
  │  visualization → semi-automatic ACMG/AMP classification (overridable) → review/curation →       │
  │  the analyst's sign-out: a closed, version-pinned, content-hashed, frozen analysis              │
  └────────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                   │  the clinical report refers 1:1 to one signed version
                                                   │  of the frozen analysis
  ┌────────────────────────────────── OUT OF SCOPE (the hospital's electronic patient record, EPD) ─┐
  │  Clinical report drafted and medically signed by an authorised signatory                        │
  └─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Explicitly upstream / not part of CoGA:** base-calling, demultiplexing, alignment,
variant calling, annotation, per-target coverage computation (for monogenic NIPT, CoGA
estimates the fetal fraction itself from the calls and takes no externally supplied value),
imputation/phasing computation (CoGA consumes phased/imputed markers). The provenance and
versions of these upstream modules are captured per family and frozen into the signed-out
analysis ([clinical-traceability.md](../clinical-traceability.md)).

**Explicitly downstream / not part of CoGA:** drafting the clinical report, its medical
sign-off and its storage, all in the EPD. CoGA does not see the EPD report: it does not check
that the report refers to a signed version, nor that the report says what that version holds.
The reference is written into the report by the person who drafts it.

## 4. Inputs

| Input | Description | Used by |
| --- | --- | --- |
| Annotated VCF (SNV/indel) | Single- or multi-sample, with VEP/ClinVar/gnomAD/dbNSFP/SpliceAI annotations; site QUAL and per-call GT/DP/AF/AD; a single-sample VCF's FILTER and caller metrics (e.g. Mutect2 TLOD, FS) kept with its call. For the mitochondrial app (3.5) this includes the nuclear mito-gene panel from the ONT adaptive-sampling run. For monogenic NIPT, one VCF per sample (cfDNA and paternal) or a joint VCF. | All applications |
| Structural-variant VCF | SV calls | PGT (large SV), rare-disorder |
| Interval tracks | Coverage, segments, copy-number, APCAD (allele fraction), haplotype-lineage, in BED-like form | PGT (aneuploidy/SV/coverage), NIPT (coverage), rare-disorder |
| Per-target coverage table | Per capture target: mean and median depth and the share of its bases covered (the NIPT-M pipeline's `coverage_<sample>.txt`), stored as the sample's target-coverage track | NIPT (coverage QC, the plasma's sex profile, a sample's depth where its VCF has no call) |
| Phased/imputed markers | Per-site phased genotypes for haplotype segregation | PGT |
| Repeat expansions (TRGT), Paraphase, mtDNA results | Per-sample specialized caller outputs; the **complete-mtDNA** call set from ONT adaptive sampling drives the mitochondrial app | Rare-disorder, mitochondrial (3.5) |
| Copy-number VCF (depth-based caller) | Per-sample CNV calls with copy number, confidence intervals and overlapping genes; ingested as reviewable structural variants alongside the alignment-based SV calls | Rare-disorder, PGT |
| mtDNA annotation table | Per-variant heteroplasmy level, coverage, haplogroup assignment and mtDNA population frequency accompanying the chrM call set | Mitochondrial (3.5) |
| Sequencing QC | Per-sample read metrics (length, quality, N50, yield) and per-chromosome depth, plus the location of the pipeline's rendered QC report | All |
| Aligned reads (CRAM/BAM) | Location only — read data is streamed to the browser for visual confirmation and never re-analysed by the device | All |
| Pipeline run record | Tool versions and run parameters of the upstream analysis pipeline, captured per family for report traceability | All |
| Pedigree / family metadata | Roles, parentage, sex, affected/carrier status, ROI, inheritance model, assay tags | All |
| Reference data | Assembly, gene/transcript reference, ClinVar/gnomAD/GenCC/PanelApp/HPO/Monarch, clinical-CNV catalogue, ACMG SF list | All |

## 5. Functional modules

| Module | Function | Reference |
| --- | --- | --- |
| Ingestion | Parse multi-sample VCF and tracks into Postgres/ClickHouse | [data-import.md](../data-import.md) |
| Family-scoped variant query | Filter SNV/SV by gene/panel/frequency/consequence/ROI; trio inheritance via genotype matching | [application-scheme.md](../application-scheme.md) |
| Global Small Variant Explorer | Cross-project variant-centric aggregation with carrier counts | README |
| Semi-automatic ACMG/AMP classifier | Pre-position ACMG criteria, server-recompute points/class on save; overridable | In-app reference ([source](../../frontend/src/content/docs/acmg-classification.md)); [acmg-classification.md](../acmg-classification.md) |
| Monogenic-NIPT analysis | Fetal-fraction estimation, cfDNA quality filter, 8-category VAF zygosity classification with fetal-inheritance probabilities, de novo triage, inheritance views with the per-gene recessive fetal risk, quality checks (paternity, fetal sex, maternal-plasma sample, target coverage), coverage/QC funnels | [monogenic-nipt.md](../monogenic-nipt.md) |
| PGT haplotype segregation | Pedigree-IBD founder colouring, disease-haplotype inference, derived embryo classification + QC | [haplotype-segregation-analysis.md](../haplotype-segregation-analysis.md) |
| Structural / CNV / aneuploidy review | SV second-hit; each sample's segment, copy-number and APCAD tracks, on which the analyst judges an embryo's aneuploidy and large SVs (CoGA makes no automatic aneuploidy call, REQ-PGT-008) | [snv-sv-compound-het.md](../snv-sv-compound-het.md) |
| Repeat / Paraphase / mtDNA | Specialized per-data-type views | README |
| Visualization | Whole-genome, per-chromosome, Circos, embedded IGV | README |
| Review & reporting | Per-variant ACMG/tags/notes, phenotype & carrier axes; the family report, which the analyst signs out as a version-pinned, frozen analysis (§3) | [report-template.md](../report-template.md), [clinical-traceability.md](../clinical-traceability.md) |

## 6. Outputs

- Filtered/prioritized candidate-variant lists with annotations and internal/external frequencies.
- Semi-automatic ACMG/AMP classification (5-class + VUS sub-tier), fully overridable, server-recomputed.
- Application-specific derived calls: NIPT fetal-fraction + per-variant category and fetal-inheritance probabilities, triaged de novo candidates, per-gene recessive fetal risk and quality checks; PGT per-embryo ROI classification with QC. For the analyst's aneuploidy and large-SV review, each embryo's segment, copy-number and APCAD tracks (no automatic aneuploidy call).
- A **signed-out analysis** (the family report, signed out by the analyst: the analytical sign-off) with a provenance footer, and an immutable clinical audit trail. Each signed version is frozen and content-hashed, rendered and printed from its frozen record, and downloadable; the live report says when it no longer matches the latest signed version (TF-06 H9). The clinical report is not a CoGA output: it is drafted and medically signed in the EPD and refers 1:1 to a signed version (§3).

**Which outputs are signed.** The sign-out freezes the family's analysis into one signed record,
which the clinical report in the EPD quotes by its report code (§3; the owner's decision of
2026-10-14). Every family's record holds the reported small variants, structural variants and
CNVs (each with its classification, criteria, frozen evidence, tags and note, and its
description as the analyst reviewed it, #879), the annotation, reference and software versions,
the reference-data releases, the gene-panel versions the family's searches applied, the
repeat-expansion and Paraphase calls with their status, what each coverage track held (#911),
the evidence drift, the Sample QC, the sequencing QC with its cut-offs and the import state
([clinical-traceability.md](../clinical-traceability.md)). A PGT family's record also holds the
embryo calls (REQ-PGT-005): the backend makes them from the stored haplotype blocks, the family
page shows the calls it serves, and the record freezes them with each embryo's phasing QC at
the ROI and that QC's verdict against provisional thresholds (#899, #908). A monogenic NIPT
family's analysis is signed out from the NIPT page or the report page, and its record also
holds the NIPT results as the backend computes them: the fetal-fraction estimate, the quality
checks with their verdicts, the target coverage, each variant tagged for the report with its
category, fetal-inheritance probabilities and, for a de novo candidate, its triage, and the
recessive fetal risk of those variants' genes (#900). The record holds no gene or phenotype
context, no population frequencies or in silico predictions beyond those a small variant's
classification froze in its evidence snapshot, and no NIPT candidate that is not tagged for the
report (TF-09b §3). **🔲 OWNER:** decide per application what the signed record is to hold
(decided on 2026-10-14, as above; TF-06 H5, H6, H9).

> No output is an autonomous diagnosis. All outputs are reviewed by a qualified professional
> (TF-01 §4). The analyst signs out the analysis in CoGA (the analytical sign-off), and the
> clinical report is medically signed in the EPD (TF-01 §1); CoGA records the sign-out of the
> family's analysis, made from the report page or, for a monogenic NIPT family, the NIPT page.

## 7. Architecture & technology

- **Frontend:** React, TypeScript, Vite, Tailwind.
- **Backend:** FastAPI (Python), SQLAlchemy async, ClickHouse client.
- **Postgres:** authoritative for metadata, users/projects, review state, ACMG blobs, audit logs, repeat/Paraphase results, gene/HPO/panel cache, version manifest, sign-out snapshots.
- **ClickHouse:** authoritative for variant payloads and interval tracks per assembly, plus cross-project genotype aggregates.
- **Filesystem / object store:** reference FASTA and BAM/CRAM for visualization (presigned, access-checked).

A complete component list with versions and risk classification of third-party
components is maintained in the [TF-08 SOUP Register](TF-08-soup-register.md).

## 8. Integrity, reproducibility & traceability features (design elements)

These are both product features and risk controls / GSPR evidence:

- **Per-family annotation/version manifest** and report **provenance footer**.
- **Per-classification evidence snapshot** frozen at classification time; **evidence-drift detection** flags when underlying data changed.
- **Immutable, append-only clinical audit trail** (who classified/tagged/edited/signed, with field-level deltas).
- **Frozen, versioned, SHA-256 content-hashed case sign-out**, the analyst's analytical sign-off that closes the analysis; the clinical report in the EPD refers 1:1 to a signed version (§3). Amendments create new versions and signed snapshots are never changed. Sign-out is **refused** for a family on an assembly outside the validated set (no override), and while the family's data is being written: an import of it queued or running, or another write holding its variant-write locks (no override; a sign-out shares those locks while it reads its snapshot, TF-06 H16). It is **blocked** by unacknowledged evidence drift, by a failed or unverifiable Sample QC, and by an incomplete import (an import that failed for some datasets, or one, an upload or a deletion that did not finish, REQ-TRACE-013) until the analyst records a reason.
- A signed version is **rendered and printed from its frozen record** alone, and says what that record does not hold (REQ-TRACE-007). The live report is labelled as not the signed version and is **checked against the latest signed version**: it says when the two differ. The signed version can be downloaded.
- **Server-side recomputation** of ACMG scores so stored classifications never depend on the browser.

Reference: [clinical-traceability.md](../clinical-traceability.md).

## 9. Configurations / variants

CoGA is one device with per-application configuration (assay tags, gene panels, inheritance
presets) rather than separate software variants. Configuration items under control:
gene-panel definitions and their source version, ACMG criterion pre-positioning rules,
NIPT artifact lists (per assay/panel; seeded from the assay's cfDNA samples or imported from
the NIPT-M pipeline's recurrent-artefact table), reference-data releases. Configuration
management is governed by **TF-18**.

## 10. Operating environment & deployment

- Containerized. **Production target: Google Cloud**, deployed with Terraform (owner decision,
  #627). There is **no production deployment yet**: the configuration in `terraform/` is
  written but has never been applied. It describes Cloud Run services behind an external
  HTTPS load balancer (TLS 1.2+, Cloud Armor), Cloud SQL, a ClickHouse VM and CMEK-encrypted
  buckets in the configured region (default `europe-west1`). The load balancer is
  **internet-facing** until the go-live switches are on; those switches and the other open
  security items are listed in [TF-13 §3](TF-13-cybersecurity.md).
- **Docker Compose** is for local development and verification, on synthetic data only. The
  databases and the API are bound to the host's loopback; only the web UI is published.
- Authentication: users sign in with their institutional Microsoft account (Microsoft Entra ID /
  Azure AD, through MSAL with the authorization code flow and PKCE); the backend checks the RS256
  access token Entra ID issues for CoGA's API. One local emergency administrator signs in with a
  password only while `AZURE_ADMIN_OVERRIDE` is on. Without Azure AD (development) accounts sign in
  with a password and an HS256 token (SEC-4, [TF-13 §2](TF-13-cybersecurity.md)). Project-scoped
  RBAC; admin-gated mutations.
- The **device** is not transferred to any other legal entity (Art. 5(5)(a)). Hosting of
  patient data by Google Cloud, the DPIA and the data-processing agreement:
  [TF-14 §1](TF-14-dpia.md).

## 11. Standards & common specifications applied

IVDR Annex I; ISO 14971 (risk); IEC 62304 & IEC 82304-1 (software lifecycle); IEC 62366-1
(usability); IEC 81001-5-1 & MDCG 2019-16 (cybersecurity); EN ISO 15189 (lab QMS, via CMGG
accreditation); GDPR. The full list and the GSPR mapping are in [TF-03](TF-03-gspr-checklist.md).
