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
| Version identifier | Semantic version `x.y.z` + git commit hash, shown in-app and in every report footer (see §8); change control in [TF-18](TF-18-change-configuration-management.md). |
| Form of delivery | Server-deployed web application, used internally at CMGG; no physical media, no transfer to third parties. |

## 2. Intended purpose

See [TF-01 Intended Purpose Statement](TF-01-intended-purpose.md). CoGA is decision-support
software for genomic variant interpretation across five clinical applications (monogenic
NIPT, expanded carrier screening, PGT, rare-disorder diagnostics, and combined mtDNA +
nuclear mitochondrial-disease testing from ONT long-read adaptive sampling).

## 3. Device boundary

CoGA's regulated boundary **begins at validated digital inputs and ends at the signed-out
clinical report**. Everything upstream is out of scope for this device and is covered by
separate validation/accreditation.

```
  ┌────────────────────── OUT OF SCOPE (separately validated & accredited) ──────────────────────┐
  │  Specimen → wet-lab assay → sequencing → alignment/variant-calling → annotation (Nextflow)    │
  └───────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                   │  annotated VCF, coverage/segment/CNV/APCD tracks,
                                                   │  repeat/Paraphase/mtDNA results, pedigree metadata
  ┌────────────────────────────────────────────────▼──────────────────────────── CoGA (THIS DEVICE) ┐
  │  Ingestion → storage (Postgres metadata + ClickHouse variants) → filtering/aggregation →         │
  │  pedigree-aware analysis (trio, NIPT, PGT haplotyping, SV/CNV, repeats, mtDNA, Paraphase) →       │
  │  visualization → semi-automatic ACMG/AMP classification (overridable) → review/curation →         │
  │  version-pinned, content-hashed, signed-out clinical report                                       │
  └───────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Explicitly upstream / not part of CoGA:** base-calling, demultiplexing, alignment,
variant calling, annotation, fetal-fraction *primary* estimation by an external caller
(CoGA recomputes and cross-checks but the wet-lab assay produces the cfDNA), imputation/
phasing computation (CoGA consumes phased/imputed markers). The provenance and versions of
these upstream modules are captured per family and frozen into the report
([clinical-traceability.md](../clinical-traceability.md)).

## 4. Inputs

| Input | Description | Used by |
| --- | --- | --- |
| Annotated VCF (SNV/indel) | Single- or multi-sample, with VEP/ClinVar/gnomAD/dbNSFP/SpliceAI annotations; site QUAL and per-call GT/DP/AF/AD. For the mitochondrial app (3.5) this includes the nuclear mito-gene panel from the ONT adaptive-sampling run. | All applications |
| Structural-variant VCF | SV calls | PGT (large SV), rare-disorder |
| Interval tracks | Coverage, segments, copy-number, APCD, haplotype-lineage, in BED-like form | PGT (aneuploidy/SV/coverage), NIPT (coverage), rare-disorder |
| Phased/imputed markers | Per-site phased genotypes for haplotype segregation | PGT |
| Repeat expansions (TRGT), Paraphase, mtDNA results | Per-sample specialized caller outputs; the **complete-mtDNA** call set from ONT adaptive sampling drives the mitochondrial app | Rare-disorder, mitochondrial (3.5) |
| Copy-number VCF (depth-based caller) | Per-sample CNV calls with copy number, confidence intervals and overlapping genes; ingested as reviewable structural variants alongside the alignment-based SV calls | Rare-disorder, PGT |
| mtDNA annotation table | Per-variant heteroplasmy level, coverage, haplogroup assignment and mtDNA population frequency accompanying the chrM call set | Mitochondrial (3.5) |
| Sequencing QC | Per-sample read metrics (length, quality, N50, yield) and per-chromosome depth, plus the location of the pipeline's rendered QC report | All |
| Aligned reads (CRAM/BAM) | Location only — read data is streamed to the browser for visual confirmation and never re-analysed by the device | All |
| Pipeline run record | Tool versions and run parameters of the upstream analysis pipeline, captured per family for report traceability | All |
| Pedigree / family metadata | Roles, parentage, sex, affected/carrier status, ROI, inheritance model, assay tags | All |
| Reference data | Assembly, gene/transcript reference, ClinVar/gnomAD/GenCC/PanelApp/HPO/Monarch, clinical-CNV catalogue | All |

## 5. Functional modules

| Module | Function | Reference |
| --- | --- | --- |
| Ingestion | Parse multi-sample VCF and tracks into Postgres/ClickHouse | [data-import.md](../data-import.md) |
| Family-scoped variant query | Filter SNV/SV by gene/panel/frequency/consequence/ROI; trio inheritance via genotype matching | [application-scheme.md](../application-scheme.md) |
| Global Small Variant Explorer | Cross-project variant-centric aggregation with carrier counts | README |
| Semi-automatic ACMG/AMP classifier | Pre-position ACMG criteria, server-recompute points/class on save; overridable | [acmg-classification.md](../acmg-classification.md) |
| Monogenic-NIPT analysis | Fetal-fraction estimation, 8-category VAF zygosity classification, inheritance presets, coverage/QC funnels | [monogenic-nipt.md](../monogenic-nipt.md) |
| PGT haplotype segregation | Pedigree-IBD founder colouring, disease-haplotype inference, derived embryo classification + QC | [haplotype-segregation-analysis.md](../haplotype-segregation-analysis.md) |
| Structural / CNV / aneuploidy review | SV second-hit, large-SV and aneuploidy interval tracks | [snv-sv-compound-het.md](../snv-sv-compound-het.md) |
| Repeat / Paraphase / mtDNA | Specialized per-data-type views | README |
| Visualization | Whole-genome, per-chromosome, Circos, embedded IGV | README |
| Review & reporting | Per-variant ACMG/tags/notes, phenotype & carrier axes, version-pinned signed-out report | [report-template.md](../report-template.md), [clinical-traceability.md](../clinical-traceability.md) |

## 6. Outputs

- Filtered/prioritized candidate-variant lists with annotations and internal/external frequencies.
- Semi-automatic ACMG/AMP classification (5-class + VUS sub-tier), fully overridable, server-recomputed.
- Application-specific derived calls: NIPT fetal-fraction + per-variant category; PGT per-embryo ROI classification with QC; aneuploidy/large-SV review.
- A **reproducible, content-hashed, version-pinned, signed-out clinical report** with a provenance footer and an immutable clinical audit trail.

> No output is an autonomous diagnosis. All outputs are reviewed and signed out by a
> qualified professional (TF-01 §4).

## 7. Architecture & technology

- **Frontend:** React, TypeScript, Vite, Tailwind.
- **Backend:** FastAPI (Python), SQLAlchemy async, ClickHouse client.
- **Postgres:** authoritative for metadata, users/projects, review state, ACMG blobs, audit logs, repeat/Paraphase results, gene/HPO/panel cache, version manifest, sign-out snapshots.
- **ClickHouse:** authoritative for variant payloads and interval tracks per assembly, plus cross-project genotype aggregates.
- **Filesystem / object store:** reference FASTA and BAM/CRAM for visualization (presigned, access-checked).

A complete component list with versions and risk classification of third-party
components is maintained in **TF-08 SOUP Register** (planned).

## 8. Integrity, reproducibility & traceability features (design elements)

These are both product features and risk controls / GSPR evidence:

- **Per-family annotation/version manifest** and report **provenance footer**.
- **Per-classification evidence snapshot** frozen at classification time; **evidence-drift detection** flags when underlying data changed.
- **Immutable, append-only clinical audit trail** (who classified/tagged/edited/signed, with field-level deltas).
- **Frozen, versioned, SHA-256 content-hashed case sign-out**; amendments create new versions, signed snapshots are never mutated; sign-out is **gated on unacknowledged evidence drift**.
- **Server-side recomputation** of ACMG scores so stored classifications never depend on the browser.

Reference: [clinical-traceability.md](../clinical-traceability.md).

## 9. Configurations / variants

CoGA is one device with per-application configuration (assay tags, gene panels, inheritance
presets) rather than separate software variants. Configuration items under control:
gene-panel definitions and their source version, ACMG criterion pre-positioning rules,
NIPT artifact lists (per assay/panel), reference-data releases. Configuration management is
governed by **TF-18**.

## 10. Operating environment & deployment

- Server deployment within the CMGG/UZ Gent managed environment; containerized (Docker Compose today; production deployment topology **🔲 INPUT NEEDED** — managed Postgres/ClickHouse, TLS, secrets manager, encryption at rest per [security-posture.md](../security-posture.md) open items).
- Authentication via JWT (HS256), optional Azure AD; project-scoped RBAC; admin-gated mutations.
- No internet exposure of PHI beyond the institution; not transferred to any other legal entity (Art. 5(5)(a)).

## 11. Standards & common specifications applied

IVDR Annex I; ISO 14971 (risk); IEC 62304 & IEC 82304-1 (software lifecycle); IEC 62366-1
(usability); IEC 81001-5-1 & MDCG 2019-16 (cybersecurity); EN ISO 15189 (lab QMS, via CMGG
accreditation); GDPR. The full list and the GSPR mapping are in [TF-03](TF-03-gspr-checklist.md).
