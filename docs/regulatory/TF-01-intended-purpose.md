# TF-01 — Intended Purpose Statement

| Field | Value |
| --- | --- |
| Document ID | TF-01 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead / device owner› |
| Approver | ‹Lab director / Head of Center for Medical Genetics› |
| Date | 2026-06-25 |
| Device | CoGA (Comprehensive Genomic Analysis), software version ‹X.Y.Z› |

> The intended purpose is the legal anchor of the technical file. Every requirement,
> risk control, and performance claim downstream traces back to the statement in §2.
> It is written to the elements required by **IVDR Annex I §20.4.1**.

---

## 1. Summary statement

**CoGA is a software-only in-vitro diagnostic medical device intended to assist
qualified clinical laboratory professionals in the filtering, visualization, and
interpretation of genomic variant data and associated quality/coverage metrics derived
from validated next-generation-sequencing (NGS) workflows, in order to support the
identification and clinical classification of genetic variants relevant to inherited
disorders, carrier status, fetal single-gene risk, and embryo genetic status.**

CoGA is a **decision-support tool**. It does not generate primary sequence, call
variants, or issue an autonomous diagnostic result. Every interpretive output is
**reviewed, confirmed, and signed out by a qualified clinical laboratory professional**,
who remains responsible for the final clinical conclusion. (See the design stance in
[acmg-classification.md](../acmg-classification.md): *"decision support, not an
autoclassifier — every criterion is overridable."*)

---

## 2. Intended-purpose elements (IVDR Annex I §20.4.1)

| Element | Specification |
| --- | --- |
| **What is detected/measured** | CoGA does not measure an analyte. It **processes, filters, aggregates, visualizes and supports interpretation of** variant calls (SNV/indel, structural variants, repeat expansions, mtDNA, Paraphase), genotype allele fractions, segment/copy-number/coverage tracks, and imputation-based haplotype/segregation data **already produced by validated upstream pipelines**. |
| **Function** | Qualitative decision support: candidate-variant filtering and prioritization; pedigree/trio-aware inheritance analysis; semi-automatic ACMG/AMP variant classification (overridable); genome/locus visualization; PGT haplotype-segregation reading; monogenic-NIPT fetal-fraction and zygosity classification; embryo aneuploidy and large structural-variant review; reproducible, version-pinned, signed-out clinical reporting. |
| **Specimen / input type** | **Not a primary specimen.** Inputs are digital files from validated upstream workflows: annotated VCF(s), coverage/segment/CNV/APCD interval tracks, repeat-expansion and Paraphase results, and pedigree metadata. The biological specimens (e.g. genomic DNA, maternal plasma cfDNA, embryo biopsy WGS) are processed by separately-validated wet-lab and bioinformatics workflows **upstream of CoGA**. |
| **Test type** | Qualitative, interpretive (not quantitative measurement). |
| **Intended user** | Trained clinical laboratory professionals — clinical laboratory geneticists, molecular biologists, and clinical/medical scientists — operating within CMGG. **Not for use by patients or untrained users.** |
| **Intended use environment** | A professional, ISO 15189-accredited clinical genetics laboratory (CMGG). Not for home/near-patient use. |
| **Intended patient population** | Defined per clinical application in §3. |
| **Single/multiple use** | Software; reusable. |

---

## 3. Clinical applications (intended uses)

CoGA is a single software device with five declared clinical applications. Each is a
distinct intended use with its own population, input, output, and limitations, and is
evaluated separately in the Performance Evaluation (TF-10/TF-11).

### 3.1 Monogenic NIPT screening
- **Purpose:** Support **screening** of an ongoing pregnancy for single-gene disorders from cell-free DNA in maternal plasma, cross-referenced with a paternal sample.
- **Input:** A combined two-sample annotated VCF (paternal germline + maternal-plasma cfDNA), target coverage, pedigree. Optional externally-supplied fetal fraction.
- **Output (decision support):** Estimated fetal fraction (with confidence interval and supporting-site count), per-variant maternal/fetal zygosity category (8-category model), filter/QC funnels, on-target coverage summary, and inheritance presets (de novo, paternal/maternal dominant, recessive at-risk). Reference: [monogenic-nipt.md](../monogenic-nipt.md).
- **Population:** **Singleton pregnancies at a gestational age of more than 12 weeks**, undergoing monogenic NIPT per CMGG clinical criteria. Multiple pregnancies and gestational age ≤ 12 weeks are **outside the validated scope**.
- **Nature of result:** **Screening**, not diagnosis; fetal genotype is *inferred* from allele fraction, never directly observed. Positive/at-risk findings require confirmatory diagnostic testing.

### 3.2 Expanded carrier screening (long-read sequencing)
- **Purpose:** Support interpretation of **expanded carrier screening** from long-read NGS, identifying carrier status across a defined set of genes/conditions.
- **Input:** Annotated VCF and associated tracks from the validated long-read workflow; gene-panel definition.
- **Output:** Filtered/classified carrier variants with ACMG support, gene-panel-scoped views, carrier-status tracking (independent of phenotype axis).
- **Population:** Couples undergoing carrier screening on a **reproductive indication** (BeGECS). General-population screening without a reproductive indication is **outside the validated scope**.
- **Panel scope:** the gene list is a **controlled document in the CMGG kwaliteitshandboek (KHB)**. The applicable **document ID and version must be recorded per study and per release** (TF-10, TF-18) — the validated scope is the panel *version*, not the panel by name.
- **Nature of result:** Carrier-status decision support; reportable carrier findings are confirmed and signed out by the laboratory.

### 3.3 Preimplantation genetic testing (PGT)
- **Purpose:** Support PGT analysis on embryos from **shallow whole-genome sequencing**, combining (a) imputation-based **haplotype segregation** to read which embryos inherited a disease haplotype, (b) **direct mutation detection**, (c) simultaneous **aneuploidy detection**, and (d) detection of **large (>10 Mb) structural variants**.
- **Input:** Per-embryo and family annotated VCFs, phased/imputed marker data, segment/copy-number/APCD tracks, pedigree (including single-parent/donor configurations), ROI, inheritance model. References: [haplotype-segregation-analysis.md](../haplotype-segregation-analysis.md).
- **Output:** Per-embryo derived classification at the ROI (affected/at-risk · carrier · unaffected · uninformative) for dominant/recessive/X-linked models, with recombination- and informativeness-aware QC warnings; aneuploidy and large-SV review tracks; direct-mutation genotype.
- **Population:** Couples / single-parent-plus-donor families undergoing PGT for a known segregating disorder and/or aneuploidy/structural risk.
- **Sub-scopes claimed:** comprehensive PGT — **PGT-M** (monogenic), **PGT-SR** (structural rearrangement), **PGT-AS** (aneuploidy screening) and **PGT-mitoDNA**. PGT-mitoDNA is a **sub-scope of this application**, not a separate application.
- **Detection claims:** aneuploidy down to **> 35% mosaicism**; structural variants **> 10 Mb**. Both are established in [TF-10](TF-10-performance-evaluation-plan.md) against the current GENType / HOPLA method; below these limits no claim is made.
- **Nature of result:** Embryo-selection decision support; the embryo call is *derived* from phased genotypes and pedigree and is explicitly gated by QC (Mendel-error rate, informative-marker count, recombination proximity).

### 3.4 Comprehensive rare-disorder diagnostics (long-read sequencing)
- **Purpose:** Support **diagnostic** interpretation of rare inherited disorders from comprehensive long-read genome sequencing, across SNV/indel, structural variants, repeat expansions (TRGT), Paraphase, and mtDNA.
- **Input:** Annotated VCFs and all associated tracks from the validated long-read genome workflow; HPO terms; pedigree.
- **Output:** Pedigree-aware candidate filtering and prioritization, semi-automatic ACMG/AMP classification, multi-data-type variant review, gene/HPO/panel context, reproducible signed-out clinical report.
- **Population:** Patients (and families) with suspected rare Mendelian disease referred to CMGG. **Both proband-only and family (trio) analysis are in scope.**
- **Nature of result:** Diagnostic decision support; final classification and reporting by the clinical laboratory.

### 3.5 Combined mitochondrial-disease testing (ONT long-read adaptive sampling)
- **Purpose:** Support **diagnostic** interpretation of mitochondrial disease by examining, in a single assay, the **complete mitochondrial genome (mtDNA)** together with the **nuclear genes implicated in mitochondrial pathologies**. An Oxford Nanopore (ONT) long-read **adaptive-sampling** run targets the entire mtDNA *and* the nuclear mito-gene panel simultaneously, so both genomes are examined in one go.
- **Input:** Annotated VCF(s) and associated tracks from the validated ONT adaptive-sampling workflow, covering the full mtDNA and the nuclear mitochondrial-disease gene panel; HPO terms; pedigree. (The adaptive sampling itself is an upstream, separately-validated wet-lab/bioinformatics process; CoGA consumes its output — see §4.1.)
- **Output:** Combined review in one family workspace — mtDNA analysis with **heteroplasmy** quantification and maternal-transmission logic (and a maternal haplogroup summary), plus nuclear small-variant/SV interpretation over the mito-gene panel, with semi-automatic ACMG/AMP classification and a reproducible signed-out report.
- **Data integrity / sample swaps (explicit):** mtDNA follows the maternal lineage and the nuclear+mtDNA assay is interpreted jointly, so the **Sample QC module** — sex check, relatedness, Mendelian consistency, heterozygosity ratio and per-mode variant counts (`sample_integrity_service`; the family Sample-QC view) — **must be reviewed for data integrity and to detect sample swaps/contamination before sign-out**. The mtDNA maternal-haplogroup summary additionally supports maternal-lineage verification. See §4 condition 6 and [TF-06](TF-06-risk-management-plan.md) hazard H4.
- **Population:** Patients with suspected mitochondrial disease. **Proband-only** analysis is the primary mode; **maternal / trio** analysis is also supported where the mother or family is available.
- **Panel scope:** the nuclear mito-gene panel is a **controlled document in the CMGG kwaliteitshandboek (KHB)**; the applicable document ID and version must be recorded per study and per release. This application is additionally governed by the CMGG-specific mitochondrial validation dossier.
- **Tissue scope:** **multiple tissues may be analysed** for heteroplasmy; the tissues covered by the validated claim are those established in [TF-10](TF-10-performance-evaluation-plan.md).
- **Nature of result:** Diagnostic decision support; mtDNA heteroplasmy and maternal inheritance are interpreted with their QC, and final classification/reporting is by the laboratory.

---

## 4. Conditions of use, limitations & contraindications

CoGA must **only** be used under the following conditions (these become labelling/IFU
statements and risk controls):

1. **Validated upstream pipeline required.** CoGA operates on the output of upstream wet-lab and bioinformatics workflows that are **separately validated and ISO 15189-accredited**. CoGA does not validate, and cannot detect all errors in, its input. The provenance/version of the upstream modules is captured per family and frozen into the signed report (see [clinical-traceability.md](../clinical-traceability.md)).
2. **Decision support only.** Outputs are candidates and pre-evaluations. A qualified clinical laboratory professional must review and confirm every interpretation; CoGA does not issue autonomous results.
3. **Screening vs diagnosis.** The NIPT (3.1) and carrier (3.2) applications are **screening**; at-risk findings require confirmatory diagnostic testing.
4. **Inferred genotypes.** In NIPT (3.1) the fetal genotype and in PGT haplotyping (3.3) the embryo haplotype are **inferred**, not directly observed; CoGA exposes the QC signals (fetal fraction, informative-marker counts, Mendel-error rate, recombination proximity) that bound the reliability of those inferences, and these **must** be checked before trusting a call.
5. **Defined assays/panels only.** Each application is valid only for the gene panels, assays, and assemblies for which it has been verified (TF-09/TF-11). Use outside the verified scope is off-label.
6. **Reference assembly.** The validated scope is **GRCh38 only**. T2T-CHM13 is a planned future extension and is **out of scope until separately validated**.
6. **Sample identity & data integrity (Sample QC).** Before any result is signed out, the **Sample QC module** (relatedness, sex check, Mendelian consistency, heterozygosity ratio, per-mode variant counts — `sample_integrity_service`; the family Sample-QC view) **must be reviewed to confirm sample identity and detect sample swaps/contamination or data-integrity problems**. This is mandatory for the family/trio- and lineage-based applications (3.1, 3.3, 3.4) and especially for combined mtDNA/nuclear testing (3.5), where maternal-lineage discordance is an additional swap signal. See [TF-06](TF-06-risk-management-plan.md) hazard H4.

**Not intended for:**
- Primary base-calling, alignment, or variant calling (these are upstream).
- Somatic/tumor variant interpretation or oncology indications.
- Standalone diagnosis without professional review and sign-out.
- Use by patients, or in a non-accredited setting.
- Populations, panels, sample types, or assemblies outside the verified scope.

---

## 5. Open items requiring confirmation

The per-application population, sub-scope and threshold questions are **resolved** (§3, recorded
in [INPUTS-QUESTIONNAIRE](INPUTS-QUESTIONNAIRE.md) A4–A5, B1–B7).

What remains before this statement can be approved:

- **🔲 Panel versions.** The BeGECS carrier gene list and the nuclear mito-gene panel are
  controlled KHB documents; their **document IDs and versions** must be cited here and in
  [TF-10](TF-10-performance-evaluation-plan.md). The validated scope is a panel *version* — a
  panel referred to only by name does not fix the boundary of the performance evaluation.
- **🔲 Belgian transitional dates / FAMHP national provisions** (questionnaire A6) and the
  **publication location of the Art. 5(5)(f) declaration** (A7).
- **🔲 The assigned CMGGMC software number `Sxxxx`** ([TF-02](TF-02-device-description.md)).
