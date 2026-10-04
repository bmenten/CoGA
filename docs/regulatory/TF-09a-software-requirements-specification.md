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

- **ID scheme:** `REQ-<AREA>-NNN`. Areas: NIPT, CARR, PGT, DIAG, CLASS, TRACE, SEC, DATA, QC, MITO, PERF, UI, RPT.
- **Criticality** (per requirement): **C** = a failure could contribute to a wrong clinical result; **B** = could mislead but is normally caught; **A** = no injury possible. This is not an IEC 62304 software safety class: the software as a whole is Class C, with no lower-class decomposition ([TF-07 §1](TF-07-software-lifecycle-plan.md)).
- **Risk** column references the hazard IDs in the [Risk Management Plan (TF-06 §6)](TF-06-risk-management-plan.md); "—" means no hazard is linked yet ([TF-09 §3](TF-09-verification-validation.md)).
- Every requirement is **verifiable**; the verifying evidence is in the RTM. Requirements
  with weak/absent verification are listed in [TF-09b §3](TF-09b-requirements-traceability-matrix.md).

## 2. Scope & assumptions

- Scope is the CoGA device per [TF-02](TF-02-device-description.md): from validated annotated VCF/tracks to the signed report.
- **Assumption:** inputs are produced by separately-validated upstream workflows; CoGA does not validate primary calling/annotation (TF-01 §4).
- Functional requirements are grouped by clinical application and cross-cutting module.

---

## 3. Functional requirements

### 3.1 Monogenic NIPT (REQ-NIPT)

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-NIPT-001 | Estimate fetal fraction (FF) from the category-7 sites that pass the quality and artifact filters — autosomal, with a cfDNA call of their own, a usable het or hom-alt father call, a cfDNA depth of 20 or more and a VAF of 0.5% to 25% — as a depth-weighted pooled estimate with a 95% confidence interval and supporting-site count, with the per-site median and the 5th and 95th percentiles of the sites' VAFs; mark it low-confidence below 30 sites or with an interval wider than ±3 percentage points, and estimate none from fewer than 5 sites. The Summary, the variant list and the report use this one estimate. | C | H6 |
| REQ-NIPT-002 | Classify each cfDNA variant into one of the 8 maternal/fetal zygosity categories by beta-binomial likelihood against the FF-derived expected VAF, with the overdispersion and maternal-heterozygous reference bias of the R NIPT-M v0.5.1 validation and Mendelian prior weights (de novo 0.02), with a per-call confidence, among only the categories the father's genotype allows. Read the father's genotype from his allele depths (reference without an alt read, het from 20% alt reads, hom-alt from 80%; the GT only without AD); a call below 20 reads, or with alt reads but fewer than 5 of them, a quality below 20 or under 20% of the reads, counts as no usable call, and no call in a per-sample paternal VCF reads as reference unless his coverage there is below 20×. | C | H6 |
| REQ-NIPT-003 | At low FF or low depth, attach low confidence and **must not force** a fetal-inheritance call. | C | H6 |
| REQ-NIPT-004 | Exclude recurrent-artifact variants (per assay/panel) and report the filter funnel counts of the cfDNA calls (total → quality → artifact → analysed), counting apart the paternal alleles without a cfDNA call of their own. Artifact seeding counts recurrence in the assay's own cfDNA samples only, and never lists a common variant or one with a ClinVar pathogenic, likely pathogenic or conflicting record. Each family's analysis checks its listed alleles again with the family's own annotation and keeps, flagged, one that is common (a population frequency above 5%) or that a ClinVar record may assert pathogenic. Every change to the artifact list is recorded in the clinical audit trail. | C | H1 |
| REQ-NIPT-006 | With the plasma's per-target coverage table, report the coverage of its capture targets (those of the selected gene panel or genes, otherwise all): a target is **weak** when its mean depth is below 300× or a base of it has no coverage; count the targets below 1000×; name each gene with a weak target and each selected gene that is no target. Without the table, compute per-region and overall **median on-target coverage** over the selected genes or the family ROI and flag a region with no coverage, a median below 20× or less than 90% of its length covered. | C | H1 |
| REQ-NIPT-007 | Provide four inheritance views over the variants that match the other filters: **de novo in the fetus** (REQ-NIPT-011); **paternal, inherited by the fetus**, the father's het or hom-alt alleles the mother does not carry (no cfDNA call, or one at 25% or less) whose transmission probability is 50% or more; **maternal, inherited by the fetus**, the mother's alleles (categories 2–6) whose inheritance probability is 50% or more; and **recessive: both parents carriers** (REQ-NIPT-013). On request the paternal and maternal views also list the alleles the fetus did not inherit, each with its probability. | C | H1, H6 |
| REQ-NIPT-008 | Surface the category-8 (paternal hom-alt absent from cfDNA) **false-negative QC signal** where the cfDNA depth (from its call, or from the plasma's per-target coverage where it has none) is 20 or more and leaves at least 3 expected alt reads; otherwise flag the absence as undetectable at this FF, a low-depth dropout or without plasma depth, with no category. | C | H6 |
| REQ-NIPT-009 | Take a monogenic NIPT's calls as one combined VCF with a paternal and a cfDNA column, or as the NIPT-M pipeline's two single-sample VCFs (the cfDNA and the father's) with each sample's per-target coverage table, stored as one callset. Read a per-sample SNV callset only for a monogenic NIPT family; refuse a per-sample VCF that does not hold exactly one sample, and a coverage table without the columns it needs. Import the cfDNA file first, and keep a paternal record only where a call of it reaches 15% alt reads or it lies on a cfDNA call position (an MNV over any of its bases), counting the records left out. Keep each single-sample call's FILTER values and caller metrics. Read a call's VAF from its allele depths (alt reads over every read at the site, a split multi-allelic record's depth restored from all its alleles; the caller's AF only without AD), and a sample's depth where its VCF has no call from its per-target coverage. | C | H1, H4 |
| REQ-NIPT-010 | Apply the cfDNA quality filter of the R NIPT-M v0.5.1 validation: a quality of 20 or more (TLOD, else the call's QUAL, else the site's QUAL), at least 5 alt reads, a VAF of 1% or more, a strand-bias score (FS) of 20 or less and, once the FF is estimated, a VAF of at least a quarter of FF/2; a value the call lacks passes. Leave a failing call out of the FF, the category counts and the de novo candidates; count the failures by reason; list a failing call in the variant list and the report, flagged with its reason. | C | H1, H6 |
| REQ-NIPT-011 | Triage the de novo candidates as the R NIPT-M v0.5.1 pipeline does. A candidate is an autosomal cfDNA call of the site's own that passes the quality filter, where the father has no supported call (no call, reference, or a low-level, thin or missing call), with a VAF in the fetal window: strict from the 5th to the 95th percentile of the FF sites' VAFs (at most 35%), loose a quarter wider on each side (at least 0.5%). Score it from its window, its annotation (impact, SpliceAI, novelty, population frequency), the caller's technical metrics, recurrence in other cfDNA samples, a paternal signal (a low-level, thin or missing father call, or one over part of the allele) and its variant class; give it high priority (strict window, a score of 10 or more, an SNV or an indel outside a repeat, no paternal signal), medium (a score of 7 or more) or low. Leave out, as recurrent, a candidate in the window that a cfDNA sample of the assay outside the family carries with a call of at least 5 alt reads and 1% of the reads (a call without allele depths on its genotype), unless a ClinVar record may assert it pathogenic (a pathogenic, likely pathogenic or conflicting record). List the candidates from the chosen lowest priority, ranked by score. | C | H1, H2 |
| REQ-NIPT-012 | Give each variant the probability of the fetal inheritance its site bears on: where the father carries the allele and the mother does not, that the fetus inherited his allele (FF/2 against a 0.2% background; prior ½ for a het and 0.99 for a hom-alt father), from the plasma's depth in the per-target coverage where it has no call; where the mother carries it, that the fetus inherited her allele, from the posterior over her band of categories as the father's genotype allows (a hom-alt mother always passes hers), and that the fetus is homozygous. Flag a maternal inference on an indel or MNV, and on a VAF more than 4 standard deviations from its best fetal state. | C | H6 |
| REQ-NIPT-013 | In the recessive view, list the genes in which the mother (categories 2–4) and the father (genotype het) are each heterozygous for an allele among the matching variants (a homozygous parent is no carrier), with each allele's probability that the fetus inherited it and the gene's fetal risk: the highest, over its maternal and paternal alleles, of the product of their inheritance probabilities for alleles at two sites, and of the probability that the fetus is homozygous for a site both parents carry. Read an inheritance the cfDNA did not tell at its Mendelian prior and say so; note an allele without a population frequency (an MNV as such) and a maternal allele REQ-NIPT-012 flags; order the genes by risk, and list every carrier variant of the listed genes. | C | H1, H6 |
| REQ-NIPT-014 | Where one sample's per-sample VCF has no call at a record, look the allele up base by base among that sample's calls in the family (an MNV for SNVs, or another MNV; indels are not matched): read the call off them when they carry every base, and mark the site partly called when they carry some. A plasma call read off another record, or carrying only part of the allele, counts toward neither the FF, nor paternity, nor the fetal sex, nor the de novo candidates; a father's call over part of the allele is a paternal signal in the de novo triage; the paternal view always lists a paternal allele the plasma calls only in part. | C | H1, H2 |
| REQ-NIPT-015 | Open the NIPT page with the quality checks: the FF with its interval, per-site median and de novo window; paternity (REQ-QC-001); the fetal sex from the father's X alleles (REQ-QC-001) and from the plasma's chrY coverage (a chrY depth between 2% and half of the autosomal depth reads male; one at 2% or less reads female only at an FF of 4% or more), given as female or male when the signals agree or only one tells, **discordant** (a failed check) when they disagree, and indeterminate otherwise; the maternal-plasma sample from its chrX and chrY depth, failing male DNA (chrY above half the autosomal depth with chrX below 0.9 of it) and warning on chrY above half the autosomal depth otherwise; the target coverage (REQ-NIPT-006); the quality-filter failures by reason; and the model reference. | C | H4, H6 |
| REQ-NIPT-016 | Let an administrator add the NIPT-M pipeline's recurrent-artefact table (the rows it marks as recurrent artefacts, or a plain list of alleles) to an assay's artifact list: refuse a table without allele columns; never add an allele whose annotation on the assembly is common (a gnomAD or TopMed frequency above 5%) or may assert pathogenic in ClinVar (a pathogenic, likely pathogenic or conflicting record), and report how many each protection left out; record an import that adds alleles as one clinical audit event naming the file, every allele added and the alleles a protection left out. An allele no family on the assembly carries has no annotation to check when the table is imported; each family's analysis checks it again (REQ-NIPT-004). | C | H1 |

### 3.2 Expanded carrier screening — BeGECS (REQ-CARR)

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-CARR-001 | Filter and present carrier variants scoped to a defined gene panel (BeGECS gene set). | C | H1, H12 |
| REQ-CARR-002 | Support couple-level at-risk determination (both partners carrying a variant in the same recessive gene / relevant X-linked finding). An X-linked finding is a variant a female partner carries on chrX outside the pseudo-autosomal regions, on its own; a partner without a recorded sex counts as female, and X-linked dominant genes are included (both confirmed by QA, the kwaliteitsbeheerder, on 2026-10-01; recorded by the owner). | C | H2 |
| REQ-CARR-003 | Capture and track the gene-panel version used for a screen. | B | H8, H12 |
| REQ-CARR-004 | Apply a gene panel to a family with coordinates of the family's own reference assembly only: store panel coordinates per assembly, never apply another assembly's, and narrow by gene alone when no assembly is resolved. | C | H12 |

### 3.3 Preimplantation genetic testing (REQ-PGT)

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-PGT-001 | Compute pedigree-IBD founder-haplotype lineage colouring; render unplaceable members/relatives as grey (never mis-coloured). | C | H5 |
| REQ-PGT-002 | Segment haplotype blocks recombination-aware, committing a lane switch only past length/width thresholds (suppress isolated phasing noise). | C | H5 |
| REQ-PGT-003 | Provide a raw phased-marker overlay (no binning/smoothing) for the index couple's children. | C | H5 |
| REQ-PGT-004 | Report per-child **Mendel-error rate** and **informative-site count** as QC. | C | H4 |
| REQ-PGT-005 | Derive each embryo's ROI classification (affected/at-risk · carrier · unaffected · uninformative) per inheritance model, defaulting to **uninformative** when the model is unresolved or when the embryo's own haplotype does not cover the ROI on a parental side the call needs. Where an X-linked recessive call depends on an embryo sex that is not recorded, assume neither sex (**affected/at-risk** if either call is, else **uninformative**) and show both calls. Read a male as having one X only outside the pseudo-autosomal regions of the family's assembly, and as two copies where these are not known. | C | H5 |
| REQ-PGT-006 | Support single-parent/donor pedigrees: colour the known-parent lane, grey the donor lane; return **uninformative** for recessive donor cases. | C | H5 |
| REQ-PGT-007 | Detect and present structural variants, filterable by length/type, supporting the large (>10 Mb) SV claim. | C | H7 |
| REQ-PGT-008 | Detect embryo aneuploidy from segment/copy-number data. | C | H7 |
| REQ-PGT-009 | Provide direct mutation detection (genotype at the ROI/locus). | C | H1 |
| REQ-PGT-010 | Serve the genome-overview lineage from a staleness-guarded precompute; never serve stale colours after a pedigree/affected-set change. | B | H5, H8 |
| REQ-PGT-011 | Undo a switch in a parent's phasing: where all of three, or all but one of four or more, of the couple's informative children switch the homolog inherited from that parent within 2 Mb, and fewer than 0.05 such clusters are expected by chance from their other switches, swap that parent's haplotypes from there in the blocks and wherever the parents' phase is read (lineage colouring, phased markers); keep the corrections on the family and mark them on the parent's track; flag every embryo when one lies within the ROI flank. Leave the genotypes as called. | C | H5 |
| REQ-PGT-012 | Colour a relative linked to the family by an unknown degree along the genome when it is the linked member's parent or child: when it shares one of the member's haplotypes along at least 90% of the autosomes the parent-child test can read (at least 15); colour it then as a parent or child. Read any other such relative at the ROI only: colour the member's haplotype it carries across the ROI and 3 Mb on each side when, on each 3 Mb flank, the sites where the member is heterozygous and the relative homozygous number at least 100 and 15% of the member's heterozygous sites and name one of the member's haplotypes at least 96% of the time, both flanks the same one, and the member's colour of it does not change there; keep the relative grey elsewhere and wherever the flanks do not say so. Read it again when the ROI changes. | C | H5 |
| REQ-PGT-013 | Record the affected parent and the index of a PGT with the statuses its inheritance model asks: the affected parent affected under AD and XLD, and for a father under XLR, and a proven carrier under AR, and for a mother under XLR; the index affected under AD, XLD and AR, and under XLR affected if male and a proven carrier if female (none while its sex is not recorded); neither under mitochondrial inheritance. A status recorded for the member wins; a contradiction is warned at validation, as is each status derived. | C | H5 |

### 3.4 Rare-disorder diagnostics (REQ-DIAG)

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-DIAG-001 | Apply pedigree/trio inheritance filtering (de-novo, dominant, recessive, compound-het) over observed genotypes. | C | H2 |
| REQ-DIAG-002 | Flag de-novo only when no parent who could have passed the allele on carries it: on autosomes, in the pseudo-autosomal regions and in a daughter, a full trio with both parents confidently hom-ref at the site; in a son on chrX/chrY outside the PARs (hemizygous), the parent who transmits that chromosome (the mother for X, the father for Y) confidently hom-ref and the other parent not carrying the ALT (#545). | C | H1, H2 |
| REQ-DIAG-003 | Detect compound-heterozygous and SV second-hit (SNV + SV in the same gene). | C | H2 |
| REQ-DIAG-004 | Ingest repeat-expansion (TRGT) calls and classify normal/intermediate/pathogenic against a locus catalog. | C | — |
| REQ-DIAG-005 | Analyse Paraphase medical regions (e.g. SMN) for copy-number/haplotype. | C | — |
| REQ-DIAG-006 | Analyse mtDNA with maternal-transmission logic and heteroplasmy/homoplasmy inference. | C | H13 |
| REQ-DIAG-007 | Prioritize candidate variants by combined pathogenicity, rarity, segregation and phenotype evidence. | B | H1 |
| REQ-DIAG-008 | Provide HPO-based and Monarch semantic-similarity phenotype matching. | B | — |

### 3.5 Variant classification (REQ-CLASS)

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-CLASS-001 | Compute ACMG/AMP point totals and the 5-class band per the ClinGen Bayesian thresholds. | C | H3 |
| REQ-CLASS-002 | Apply BA1 (AF ≥ 5%) as a stand-alone benign override. | C | H3 |
| REQ-CLASS-003 | Count only **accepted** criteria toward the score; every criterion is analyst-overridable. | C | H3 |
| REQ-CLASS-004 | Assign VUS sub-tiers (cold/warm/hot) within the VUS band. | B | — |
| REQ-CLASS-005 | Recompute class and points **server-side** on save; a stored classification never depends on the browser. | C | H3 |
| REQ-CLASS-006 | Compute CNV ACMG classification (ClinGen 2019) with distinct gain/loss rules. | C | H3 |

### 3.6 Clinical traceability & integrity (REQ-TRACE)

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-TRACE-001 | Maintain a per-family annotation/version manifest, merging the pipeline layer over the platform layer in a canonical order. | C | H8 |
| REQ-TRACE-002 | Freeze a per-classification evidence snapshot (annotation hash + key evidence) at classification time. | C | H8 |
| REQ-TRACE-003 | Detect evidence drift (stored vs current annotation hash) and surface from→to changes. | C | H8 |
| REQ-TRACE-004 | Record an append-only clinical audit trail with field-level before/after, one entry per change, in the same transaction as the review save, for small-variant, structural-variant and CNV reviews alike. The classification entry holds the whole stored record: every criterion with its strength (for a CNV, its points), acceptance, evidence and suggestion flag, and the class and point total. A change to any of it is recorded even when the class stays the same; an unchanged re-save records nothing; a cleared or deleted review is included. | C | H9, H8 |
| REQ-TRACE-005 | Produce a frozen, versioned, SHA-256 content-hashed sign-out snapshot; the hash is stable and order-independent; signed snapshots are never mutated (amend = new version). | C | H9 |
| REQ-TRACE-006 | Gate sign-out on unacknowledged evidence drift: reject unless the analyst acknowledges it with a reason, which is frozen into the signed record and the audit trail. | C | H8 |
| REQ-TRACE-007 | Render a signed-out report **from the frozen snapshot**, not by re-querying live stores. What the snapshot does not hold is said, never filled in from live data (open item: [TF-09b §3](TF-09b-requirements-traceability-matrix.md)). | C | H9 |
| REQ-TRACE-008 | Enforce audit/sign-out immutability at the database (append-only trigger; no UPDATE/DELETE). | C | H9 |
| REQ-TRACE-009 | Refuse sign-out, without an override, for a family whose reference assembly is outside the configured validated set (default GRCh38) or unresolved; label such a family "not validated for clinical use" on the family and report pages. | C | H12 |
| REQ-TRACE-010 | Refuse a variant-review save made against a review that has changed since the client loaded it (409 with the current review), rather than overwrite another reviewer's classification, criteria, tags or note; serialize concurrent saves of one variant. | C | H9, H3 |
| REQ-TRACE-011 | Record, per family, the analysis pipeline and engine version and the version of every tool and reference database behind its callset, and present them with the run configuration. | C | H8 |
| REQ-TRACE-012 | Refuse sign-out while a Sample QC check has failed, or while the Sample QC cannot confirm a declared family relationship or a sample's identity, unless the analyst acknowledges it with a reason; freeze the QC result and the reason into the signed record and the audit trail. | C | H4 |
| REQ-TRACE-013 | Refuse sign-out while a package import has left the family partly loaded — the import-incomplete flag is set, or an import that began writing the family did not finish (its process stopped part-way, so the entry it recorded before its first write of the family remains) — unless the analyst acknowledges it with a reason; freeze what the import left out (the failed datasets, the datasets an unfinished import had not finished, and the import job) and the reason into the signed record and the audit trail; warn on every family page while either is set. Clear the import-incomplete flag only as an import imports each failed dataset again, for the same samples and small-variant source, keeping an earlier import's failed datasets (each with its import job) when a later import fails too. Clear an unfinished import's entry only by an import that completes and imports its unfinished datasets again with overwrite, for the same samples and small-variant source. End, keeping its record, an import job whose process stopped after its import began writing the family, rather than run it again from the start. | C | H16 |
| REQ-TRACE-014 | Freeze, when a structural-variant or CNV classification (a CNV scoring) is saved, the evidence the CNV classifier reads — the SV's type, the genes it overlaps and their count, the pLI and the annotated inheritance — with its caller and locus, a hash of its annotation, and the versions of the SV callset and the reference gene loci behind it; refuse a scoring for an SV not in the family's data. Report each SV/CNV classification whose evidence changed (naming what moved), whose SV is no longer in the data, or whose frozen evidence cannot be read. Count these, and a reported SV/CNV with no frozen evidence, in the sign-out drift gate (REQ-TRACE-006) and freeze them into the signed record. | C | H8, H3 |

### 3.7 Access control & security (REQ-SEC)

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-SEC-001 | Enforce project-scoped access on every PHI endpoint at the SQL level; a viewer cannot reach a family/sample in a project they are not in. | C | H11 |
| REQ-SEC-002 | Gate all destructive/structure-changing mutations behind admin role. | C | H11 |
| REQ-SEC-003 | Authenticate via JWT (HS256), with local fallback restricted to admins. | C | H11 |
| REQ-SEC-004 | Record an append-only HTTP audit log of who-accessed-what-when, with PII minimization. | C | H11 |
| REQ-SEC-005 | Throttle/track failed logins. | B | H11 |
| REQ-SEC-006 | Refuse to start in production with default secrets; mask secret-like fields in logs. | C | H11 |
| REQ-SEC-007 | Issue PHI file (CRAM/BAM) access only after family+sample access checks (presigned URL). | C | H11 |

### 3.8 Ingestion & storage (REQ-DATA)

| ID | Requirement | Criticality | Risk |
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
| REQ-DATA-012 | Classify every stored genotype — haploid, multi-allelic and half calls included — into exactly one of hom-alt, het, hom-ref or no-call, and apply that one classification in every genotype filter, inheritance check, count and presence check (backend SQL and Python, and the UI). | C | H1, H2 |

### 3.9 Sample QC (REQ-QC)

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-QC-001 | Provide a sample-integrity QC suite matched to the application: sex check, relatedness against the pedigree and Mendelian consistency; for NIPT, paternity, fetal and parent sex and the cfDNA category check. NIPT paternity counts the father's usable het and hom-alt calls at autosomal sites where the mother does not carry the allele and the cfDNA depth leaves at least 10 expected alt reads: of his hom-alt alleles, 90% or more seen passes and below 80% fails (from 20 sites); of his het alleles, 35–65% seen passes and outside 25–75% fails (from 50 sites); the worse verdict holds, and too few sites of both kinds is unverifiable. The NIPT fetal sex reads his hom-alt calls on chrX outside the pseudo-autosomal regions: from 8 informative sites, 80% or more of his alleles seen is female, 10% or less male, otherwise indeterminate. A per-sample NIPT callset's genotypes are read from the allele depths, a sample without a call as reference. | C | H4 |
| REQ-QC-002 | Surface sample-integrity QC at the family level for review before sign-out. | C | H4 |
| REQ-QC-003 | Maintain admin-managed sequencing-QC acceptance limits: per metric a warning limit and an error limit, grouped into named per-assay profiles, with the last-changing user recorded. The set of gateable metrics and the failing side of each are fixed by the software, not configurable. | C | H14 |
| REQ-QC-004 | Evaluate each sample's recorded sequencing QC against the limits of its resolved profile **server-side**, returning a per-metric state (pass / warn / fail / not assessed) and the sample's worst state. A metric that was not measured, or for which no limit is configured, yields *not assessed* and never *pass*. | C | H14 |
| REQ-QC-005 | Show each sample's worst sequencing-QC state in the family members table, distinguishable without relying on colour alone, and name the breaching metrics with their values and the limits they crossed on inspection. | C | H14, H10 |
| REQ-QC-006 | Apply the same admin-managed limits to the mitochondrial QC verdict (chrM mean depth, contamination); no QC acceptance limit is fixed in code. | C | H14, H13 |
| REQ-QC-007 | Record every acceptance-limit change in an append-only history holding the replaced value, the new value and the acting user, and require the change to be confirmed against a restatement of both sides before it is committed. | C | H14, H9 |

### 3.10 Mitochondrial disease — combined mtDNA + nuclear (REQ-MITO)

Application 3.5: ONT long-read adaptive sampling produces the complete mtDNA and the nuclear
mito-gene panel in one run; CoGA interprets both together. Reuses mtDNA (REQ-DIAG-006), nuclear
small-variant/SV (REQ-DIAG-001/003), classification (REQ-CLASS-*) and Sample QC (REQ-QC-*).

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-MITO-001 | Interpret the complete mtDNA and the nuclear mito-gene panel from a single adaptive-sampling run together in one family workspace. | C | H1 |
| REQ-MITO-002 | Quantify mtDNA heteroplasmy and apply maternal-transmission logic, and show each sample's haplogroup. | C | H13 |
| REQ-MITO-003 | Require **Sample QC** review (relatedness, sex, Mendelian consistency) for data integrity and sample-swap detection before sign-out, and show each sample's mtDNA haplogroup so the analyst can check maternal-lineage consistency. | C | H4 |

## 4. Non-functional requirements

### 4.1 Performance (REQ-PERF) — defined in TF-10, evidenced in TF-11

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-PERF-001 | Meet the per-application analytical/clinical concordance acceptance criteria vs the validated comparator assays, on the validation sets of [TF-10 §2](TF-10-performance-evaluation-plan.md). | C | H1–H7 |
| REQ-PERF-002 | Reproducibility: identical validated input yields an identical content-hashed signed report. | C | H9 |
| REQ-PERF-003 | Robustness: degraded/incomplete inputs fail safe (warn/abstain), never silently mis-call. | C | H1, H6 |

### 4.2 Safety-critical user interface (REQ-UI) — usability detail in TF-12

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-UI-001 | The NIPT dashboard surfaces the quality checks (REQ-NIPT-015), FF + CI, category counts, the filter funnel with drop counts, and coverage QC, without extra navigation; each variant shows its plasma reads, the father's genotype class, its fetal-inheritance probabilities, a de novo candidate's priority and its flags. | C | H6, H1, H4, H10 |
| REQ-UI-002 | The haplotype track shows lineage colours, the raw-marker overlay, informative-marker count, Mendel-error, and recombination/uninformative warnings. | C | H5, H10 |
| REQ-UI-003 | The ACMG modal presents overridable criteria and the points scale, distinguishes suggested vs accepted, and recomputes server-side on save. | C | H3, H10 |
| REQ-UI-004 | The report page shows the provenance footer, the evidence-drift badge, the sign-out/amend action with its acknowledge-with-reason dialogs, and the audit timeline. | C | H8, H9, H10 |
| REQ-UI-005 | Route guards enforce authentication (`RequireAuth`) and admin-only areas (`RequireAdmin`). | C | H11 |
| REQ-UI-006 | Login redirect (`next`) is validated against unsafe targets. | B | H11 |
| REQ-UI-007 | The pedigree renders affected/carrier status and a per-sample QC ring. | B | H4 |
| REQ-UI-008 | The app and every report (family and NIPT) identify the running build (version and commit) and carry the in-house-IVD label and the manufacturer ([TF-15 §1](TF-15-instructions-for-use.md)); a build that cannot be loaded is said as such, and marks a report printout incomplete. Problem reports go to the configured CMGGMC route, never a public tracker; without the route, a production build shows no problem-report link ([TF-15 §7](TF-15-instructions-for-use.md)). | B | H9, H10 |

### 4.3 Reporting (REQ-RPT)

| ID | Requirement | Criticality | Risk |
| --- | --- | --- | --- |
| REQ-RPT-001 | Assemble the family clinical report from report-tagged variants with gene/HPO context and the reference label/version. | C | H9 |
| REQ-RPT-002 | The NIPT report presents FF, the quality checks (fetal sex, paternity, the maternal-plasma sample), the coverage of the targets in scope, candidates grouped by inheritance, and a confirmatory-testing disclaimer. | C | H6, H4 |

## 5. Maintenance

This SRS is revised whenever a change adds/alters a requirement (TF-18). New requirements take
the next free ID in their area; retired requirements are marked deprecated, not deleted, to
preserve traceability history.
