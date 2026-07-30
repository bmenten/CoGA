# TF-09a — Software Requirements Specification (SRS)

| Field | Value |
| --- | --- |
| Document ID | TF-09a |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Parent | [TF-09 V&V Plan](TF-09-verification-validation.md); traced in [TF-09b RTM](TF-09b-requirements-traceability-matrix.md) |
| Standards | IEC 62304 §5.2; IVDR Annex I §16 |

> The authoritative list of CoGA's software requirements, each with a stable ID. The
> forward links (requirement → design → code → test → risk) live in the **RTM
> ([TF-09b](TF-09b-requirements-traceability-matrix.md))**. This SRS was derived from the
> per-feature design docs and a point-in-time inventory of the implementation and test
> suites; it is maintained under change control ([TF-18](TF-18-change-configuration-management.md)).

---

## 1. Conventions

- **ID scheme:** `REQ-<AREA>-NNN`. Areas: NIPT, CARR, PGT, DIAG, CLASS, TRACE, SEC, DATA, QC, PRIOR, UI, RPT, PERF.
- **Safety class** (per requirement, IEC 62304): **C** = a failure could contribute to a wrong clinical result; **B** = could mislead but is normally caught; **A** = no injury possible.
- **Risk** column references the hazard IDs in the [Risk Management Plan (TF-06 §6)](TF-06-risk-management-plan.md).
- Every requirement is **verifiable**; the verifying evidence is in the RTM. Requirements
  with weak/absent verification are listed in [TF-09b §Gaps](TF-09b-requirements-traceability-matrix.md).

## 2. Scope & assumptions

- Scope is the CoGA device per [TF-02](TF-02-device-description.md): from validated annotated VCF/tracks to the signed report.
- **Assumption:** inputs are produced by separately-validated upstream workflows; CoGA does not validate primary calling/annotation (TF-01 §4).
- Functional requirements are grouped by clinical application and cross-cutting module.

---

## 3. Functional requirements

### 3.1 Monogenic NIPT (REQ-NIPT)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-NIPT-001 | Estimate fetal fraction (FF) from category-7 sites as a robust weighted median, reporting a confidence interval and supporting-site count. | C | H6 |
| REQ-NIPT-002 | Classify each cfDNA variant into one of the 8 maternal/fetal zygosity categories by binomial likelihood against the FF-derived expected VAF, with a per-call confidence. | C | H6 |
| REQ-NIPT-003 | At low FF or low depth, attach low confidence and **must not force** a fetal-inheritance call. | C | H6 |
| REQ-NIPT-004 | Exclude recurrent-artifact variants (per assay/panel) and report the filter funnel counts (total → quality → artifact → analysed). | C | H1 |
| REQ-NIPT-005 | When an external FF is supplied, record it alongside the computed estimate and **flag disagreement** (computed estimate remains default). | B | H6 |
| REQ-NIPT-006 | Compute per-region and overall **median on-target coverage** and flag low-coverage regions against a threshold. | C | H1 |
| REQ-NIPT-007 | Provide inheritance presets: de-novo, paternal-dominant, maternal-dominant, recessive-at-risk (cross-variant gene pairing). | B | — |
| REQ-NIPT-008 | Surface the category-8 (paternal hom-alt absent from cfDNA) **false-negative QC signal**. | C | H6 |

### 3.2 Expanded carrier screening — BeGECS (REQ-CARR)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-CARR-001 | Filter and present carrier variants scoped to a defined gene panel (BeGECS gene set). | C | H1, H12 |
| REQ-CARR-002 | Support couple-level at-risk determination (both partners carrying a variant in the same recessive gene / relevant X-linked finding). | C | H2 |
| REQ-CARR-003 | Capture and track the gene-panel version used for a screen. | B | H8, H12 |

### 3.3 Preimplantation genetic testing (REQ-PGT)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-PGT-001 | Compute pedigree-IBD founder-haplotype lineage colouring; render unplaceable members/relatives as grey (never mis-coloured). | C | H5 |
| REQ-PGT-002 | Segment haplotype blocks recombination-aware, committing a lane switch only past length/width thresholds (suppress isolated phasing noise). | C | H5 |
| REQ-PGT-003 | Provide a raw phased-marker overlay (no binning/smoothing) for the index couple's children. | C | H5 |
| REQ-PGT-004 | Report per-child **Mendel-error rate** and **informative-site count** as QC. | C | H4 |
| REQ-PGT-005 | Derive each embryo's ROI classification (affected/at-risk · carrier · unaffected · uninformative) per inheritance model, defaulting to **uninformative** when the model is unresolved. | C | H5 |
| REQ-PGT-006 | Support single-parent/donor pedigrees: colour the known-parent lane, grey the donor lane; return **uninformative** for recessive donor cases. | C | H5 |
| REQ-PGT-007 | Detect and present structural variants, filterable by length/type, supporting the large (>10 Mb) SV claim. | C | H7 |
| REQ-PGT-008 | Detect embryo aneuploidy from segment/copy-number data. | C | H7 |
| REQ-PGT-009 | Provide direct mutation detection (genotype at the ROI/locus). | C | H1 |
| REQ-PGT-010 | Serve the genome-overview lineage from a staleness-guarded precompute; never serve stale colours after a pedigree/affected-set change. | B | H5, H8 |

### 3.4 Rare-disorder diagnostics (REQ-DIAG)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-DIAG-001 | Apply pedigree/trio inheritance filtering (de-novo, dominant, recessive, compound-het) over observed genotypes. | C | H2 |
| REQ-DIAG-002 | Flag de-novo only with a full trio and both parents hom-ref at the site. | C | H2 |
| REQ-DIAG-003 | Detect compound-heterozygous and SV second-hit (SNV + SV in the same gene). | C | H2 |
| REQ-DIAG-004 | Ingest repeat-expansion (TRGT) calls and classify normal/intermediate/pathogenic against a locus catalog. | C | — |
| REQ-DIAG-005 | Analyse Paraphase medical regions (e.g. SMN) for copy-number/haplotype. | C | — |
| REQ-DIAG-006 | Analyse mtDNA with maternal-transmission logic and heteroplasmy/homoplasmy inference. | C | — |
| REQ-DIAG-007 | Prioritize candidate variants by combined pathogenicity, rarity, segregation and phenotype evidence. | B | H1 |
| REQ-DIAG-008 | Provide HPO-based and Monarch semantic-similarity phenotype matching. | B | — |

### 3.5 Variant classification (REQ-CLASS)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-CLASS-001 | Compute ACMG/AMP point totals and the 5-class band per the ClinGen Bayesian thresholds. | C | H3 |
| REQ-CLASS-002 | Apply BA1 (AF ≥ 5%) as a stand-alone benign override. | C | H3 |
| REQ-CLASS-003 | Count only **accepted** criteria toward the score; every criterion is analyst-overridable. | C | H3 |
| REQ-CLASS-004 | Assign VUS sub-tiers (cold/warm/hot) within the VUS band. | B | — |
| REQ-CLASS-005 | Recompute class and points **server-side** on save; a stored classification never depends on the browser. | C | H3 |
| REQ-CLASS-006 | Compute CNV ACMG classification (ClinGen 2019) with distinct gain/loss rules. | C | H3 |

### 3.6 Clinical traceability & integrity (REQ-TRACE)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-TRACE-001 | Maintain a per-family annotation/version manifest, merging the pipeline layer over the platform layer in a canonical order. | C | H8 |
| REQ-TRACE-002 | Freeze a per-classification evidence snapshot (annotation hash + key evidence) at classification time. | C | H8 |
| REQ-TRACE-003 | Detect evidence drift (stored vs current annotation hash) and surface from→to changes. | C | H8 |
| REQ-TRACE-004 | Record an append-only clinical audit trail with field-level before/after, one entry per change, in the same transaction as the review save. | C | H9, H8 |
| REQ-TRACE-005 | Produce a frozen, versioned, SHA-256 content-hashed sign-out snapshot; the hash is stable and order-independent; signed snapshots are never mutated (amend = new version). | C | H9 |
| REQ-TRACE-006 | Gate sign-out on unacknowledged evidence drift (reject unless explicitly acknowledged). | C | H8 |
| REQ-TRACE-007 | Render a signed-out report **from the frozen snapshot**, not by re-querying live stores. | C | H9 |
| REQ-TRACE-008 | Enforce audit/sign-out immutability at the database (append-only trigger; no UPDATE/DELETE). | C | H9 |

### 3.7 Access control & security (REQ-SEC)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-SEC-001 | Enforce project-scoped access on every PHI endpoint at the SQL level; a viewer cannot reach a family/sample in a project they are not in. | C | H11 |
| REQ-SEC-002 | Gate all destructive/structure-changing mutations behind admin role. | C | H11 |
| REQ-SEC-003 | Authenticate via JWT (HS256), with local fallback restricted to admins. | C | H11 |
| REQ-SEC-004 | Record an append-only HTTP audit log of who-accessed-what-when, with PII minimization. | C | H11 |
| REQ-SEC-005 | Throttle/track failed logins. | B | H11 |
| REQ-SEC-006 | Refuse to start in production with default secrets; mask secret-like fields in logs. | C | H11 |
| REQ-SEC-007 | Issue PHI file (CRAM/BAM) access only after family+sample access checks (presigned URL). | C | H11 |

### 3.8 Ingestion & storage (REQ-DATA)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-DATA-001 | Parse multi-sample VCF into per-call GT/DP/AF/AD and site-level QUAL. | C | H1 |
| REQ-DATA-002 | Preserve genotype phasing (PS phase blocks) through ingestion. | C | H5 |
| REQ-DATA-003 | Ingest structural-variant VCFs (BND/DEL/DUP/INV). | C | — |
| REQ-DATA-004 | Record uploaded-file metadata and verify file integrity (checksum). | B | H4 |
| REQ-DATA-005 | Discover and validate family packages (manifest/PED), normalize samples, and preserve analysis-type tags. | C | H4, H12 |
| REQ-DATA-006 | Provision/maintain ClickHouse variant tables and surface mutation/health status. | B | H9 |
| REQ-DATA-007 | Resolve a VCF sample column to the family sample it belongs to (declared override, tool suffix, or a per-sample dataset binding), and fail the import when it resolves to none. | C | H4 |
| REQ-DATA-008 | Ingest depth-based CNV calls (copy number and overlapping genes) as reviewable structural variants, kept separate from alignment-based SV calls. | C | H1 |
| REQ-DATA-009 | Ingest mitochondrial (chrM) calls with their heteroplasmy level and mtDNA-specific annotation, stored separately from the nuclear callset. | C | H1 |
| REQ-DATA-010 | Capture the analysis pipeline's tool versions and run parameters per family for report traceability. | B | H4 |
| REQ-DATA-011 | Record per-sample sequencing QC (read metrics, depth) and the location of the pipeline's QC report and aligned reads. | B | H4 |

### 3.9 Sample QC (REQ-QC)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-QC-001 | Provide a sample-integrity QC suite (sex check, relatedness, Mendelian consistency, heterozygosity ratio, per-mode variant counts). | C | H4 |
| REQ-QC-002 | Surface sample-integrity QC at the family level for review before sign-out. | C | H4 |
| REQ-QC-003 | Maintain admin-managed sequencing-QC acceptance limits: per metric a warning limit and an error limit, grouped into named per-assay profiles, with the last-changing user recorded. The set of gateable metrics and the failing side of each are fixed by the software, not configurable. | C | H14 |
| REQ-QC-004 | Evaluate each sample's recorded sequencing QC against the limits of its resolved profile **server-side**, returning a per-metric state (pass / warn / fail / not assessed) and the sample's worst state. A metric that was not measured, or for which no limit is configured, yields *not assessed* and never *pass*. | C | H14 |
| REQ-QC-005 | Show each sample's worst sequencing-QC state in the family members table, distinguishable without relying on colour alone, and name the breaching metrics with their values and the limits they crossed on inspection. | C | H14, H10 |
| REQ-QC-006 | Apply the same admin-managed limits to the mitochondrial QC verdict (chrM mean depth, contamination); no QC acceptance limit is fixed in code. | C | H14, H13 |
| REQ-QC-007 | Record every acceptance-limit change in an append-only history holding the replaced value, the new value and the acting user, and require the change to be confirmed against a restatement of both sides before it is committed. | C | H14, H9 |
| REQ-TRACE-008 | Record, per family, the analysis pipeline and engine version and the version of every tool and reference database behind its callset, and present them with the run configuration. | C | H8 |

### 3.10 Mitochondrial disease — combined mtDNA + nuclear (REQ-MITO)

Application 3.5: ONT long-read adaptive sampling produces the complete mtDNA and the nuclear
mito-gene panel in one run; CoGA interprets both together. Reuses mtDNA (REQ-DIAG-006), nuclear
small-variant/SV (REQ-DIAG-001/003), classification (REQ-CLASS-*) and Sample QC (REQ-QC-*).

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-MITO-001 | Interpret the complete mtDNA and the nuclear mito-gene panel from a single adaptive-sampling run together in one family workspace. | C | H1 |
| REQ-MITO-002 | Quantify mtDNA heteroplasmy and apply maternal-transmission logic (incl. a maternal haplogroup summary). | C | H13 |
| REQ-MITO-003 | Require **Sample QC** review (relatedness/sex/Mendelian + maternal-lineage consistency) for data integrity and sample-swap detection before sign-out. | C | H4 |

## 4. Non-functional requirements

### 4.1 Performance (REQ-PERF) — defined in TF-10, evidenced in TF-11

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-PERF-001 | Meet the per-application analytical/clinical concordance acceptance criteria vs the validated comparator assays (50 BeGECS couples, 100 PGT embryos, 30 WGS trios, 30 NIPT). | C | H1–H7 |
| REQ-PERF-002 | Reproducibility: identical validated input yields an identical content-hashed signed report. | C | H9 |
| REQ-PERF-003 | Robustness: degraded/incomplete inputs fail safe (warn/abstain), never silently mis-call. | C | H1, H6 |

### 4.2 Safety-critical user interface (REQ-UI) — usability detail in TF-12

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-UI-001 | The NIPT dashboard surfaces FF + CI, category counts, the filter funnel with drop counts, and coverage QC, without extra navigation. | C | H6, H1, H10 |
| REQ-UI-002 | The haplotype track shows lineage colours, the raw-marker overlay, informative-marker count, Mendel-error, and recombination/uninformative warnings. | C | H5, H10 |
| REQ-UI-003 | The ACMG modal presents overridable criteria and the points scale, distinguishes suggested vs accepted, and recomputes server-side on save. | C | H3, H10 |
| REQ-UI-004 | The report page shows the provenance footer, the evidence-drift badge, the sign-out/amend action with the 409 acknowledge flow, and the audit timeline. | C | H8, H9, H10 |
| REQ-UI-005 | Route guards enforce authentication (`RequireAuth`) and admin-only areas (`RequireAdmin`). | C | H11 |
| REQ-UI-006 | Login redirect (`next`) is validated against unsafe targets. | B | H11 |
| REQ-UI-007 | The pedigree renders affected/carrier status and a per-sample QC ring. | B | H4 |

### 4.3 Reporting (REQ-RPT)

| ID | Requirement | Class | Risk |
| --- | --- | --- | --- |
| REQ-RPT-001 | Assemble the family clinical report from report-tagged variants with gene/HPO context and the reference label/version. | C | H9 |
| REQ-RPT-002 | The NIPT report presents FF, coverage QC, candidates grouped by inheritance, and a confirmatory-testing disclaimer. | C | H6 |

## 5. Maintenance

This SRS is revised whenever a change adds/alters a requirement (TF-18). New requirements take
the next free ID in their area; retired requirements are marked deprecated, not deleted, to
preserve traceability history.
