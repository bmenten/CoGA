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

> The intended purpose is the legal anchor of the technical file: every requirement, risk
> control and performance claim traces back to the statement in §2. It follows the elements
> **IVDR Annex I §20.4.1** requires.

---

## 1. Summary statement

**CoGA is a software-only in-vitro diagnostic medical device intended to assist
qualified clinical laboratory professionals in the filtering, visualization, and
interpretation of genomic variant data and associated quality/coverage metrics derived
from validated next-generation-sequencing (NGS) workflows, in order to support the
identification and clinical classification of genetic variants relevant to inherited
disorders, carrier status, fetal single-gene risk, fetal aneuploidy risk, and embryo genetic
status.**

CoGA is a **decision-support tool** for filtering, visualising and interpreting results, and
for their traceability. It does not generate primary sequence, call variants, or issue an
autonomous diagnostic result. For the conventional NIPT (§3.7) it applies a fixed rule, with
bounds QA sets, to the upstream pipeline's copy-number output and proposes a screening result per
target; that result, like every output, is reviewed by the analyst and signed out by a
supervisor. Every interpretive output is **reviewed and confirmed by a qualified clinical
laboratory professional**, and two sign-offs follow, in two places:

- **The analytical sign-off, in CoGA.** A supervisor signs out the analysis (*Sign out*; a user
  with the supervisor role or higher, TF-06 H15): this closes the analysis and freezes it into a
  signed version, the device's output.
- **The medical sign-off, in the EPD.** The clinical report is drafted and medically signed
  in the hospital's electronic patient record (EPD) by a signatory the laboratory has
  authorised, who remains responsible for the final clinical conclusion. The report refers
  1:1 to the frozen CoGA analysis it rests on. The EPD is outside the device
  ([TF-02 §3](TF-02-device-description.md)).

(See the in-app reference *Semi-automatic ACMG classification*
([source](../../frontend/src/content/docs/acmg-classification.md)): *"decision support, not an
autoclassifier"* — every criterion is overridable.)

---

## 2. Intended-purpose elements (IVDR Annex I §20.4.1)

| Element | Specification |
| --- | --- |
| **What is detected/measured** | CoGA does not measure an analyte. It **processes, filters, aggregates, visualizes and supports interpretation of** variant calls (SNV/indel, structural variants, repeat expansions, mtDNA, Paraphase), genotype allele fractions, segment/copy-number/coverage tracks, the copy-number ratios, z-scores and fetal-fraction estimates of maternal plasma, and imputation-based haplotype/segregation data **already produced by validated upstream pipelines**. |
| **Function** | Qualitative decision support: candidate-variant filtering and prioritization; pedigree/trio-aware inheritance analysis; semi-automatic ACMG/AMP variant classification (overridable); genome/locus visualization; PGT haplotype-segregation reading; monogenic-NIPT fetal-fraction and zygosity classification; a conventional-NIPT screening result per target (high risk, inconclusive, low risk, no result) by a rule of z-score and fetal-fraction concordance; the embryo segment, copy-number and APCAD tracks on which the analyst reviews aneuploidy and large structural variants; a reproducible, version-pinned analysis that a supervisor signs out (closes and freezes), to which the clinical report in the EPD refers. |
| **Specimen / input type** | **Not a primary specimen.** Inputs are digital files from validated upstream workflows: annotated VCF(s), coverage/segment/CNV/APCAD interval tracks, per-target coverage tables, the copy-number bins, segments, calls and QC of the conventional NIPT pipeline, repeat-expansion and Paraphase results, and pedigree metadata. The biological specimens (e.g. genomic DNA, maternal plasma cfDNA, embryo biopsy WGS) are processed by separately-validated wet-lab and bioinformatics workflows **upstream of CoGA**. |
| **Test type** | Qualitative, interpretive (not quantitative measurement). The conventional NIPT's result is qualitative, read from quantitative values the upstream pipeline computes. |
| **Intended user** | Trained clinical laboratory professionals — clinical laboratory geneticists, molecular biologists, and clinical/medical scientists — operating within CMGG. **Not for use by patients or untrained users.** |
| **Intended use environment** | A professional, ISO 15189-accredited clinical genetics laboratory (CMGG). Not for home/near-patient use. |
| **Intended patient population** | Defined per clinical application in §3. |
| **Single/multiple use** | Software; reusable. |

---

## 3. Clinical applications (intended uses)

CoGA is one software device with seven declared clinical applications. Each is a distinct
intended use, with its own population, input, output and limitations, evaluated separately
in the Performance Evaluation (TF-10/TF-11).

### 3.1 Monogenic NIPT screening
- **Purpose:** Support **screening** of an ongoing pregnancy for single-gene disorders from cell-free DNA in maternal plasma, cross-referenced with a paternal sample.
- **Input:** The NIPT-M pipeline's two single-sample annotated VCFs (maternal-plasma cfDNA and paternal germline) with each sample's per-target coverage table, or one combined two-sample annotated VCF (paternal + cfDNA) with target coverage; the calls carry allele depths (FORMAT/AD); pedigree; the analysis's diagnostic gene panel, its scope. CoGA estimates the fetal fraction itself and takes no externally supplied value.
- **Output (decision support):** Estimated fetal fraction (with confidence interval and supporting-site count), per-variant maternal/fetal zygosity category (8-category model) with the probability that the fetus inherited the paternal or maternal allele, triaged de novo candidates, the locked diagnostic candidates of the diagnostic gene panel (de novo, paternal, maternal and recessive, by the fixed criteria of the R NIPT-M v0.7.3 validation), the fetal status per gene where both parents carry an allele (and per X-linked gene read by the fetal sex: a son's mother's allele, one parent's of a daughter, either parent's when the sex is not known), with which of the alleles the fetus inherited and the probabilities that it is affected, a carrier or neither, quality checks (the expected fetal reads, paternity, fetal sex, the maternal-plasma sample, target coverage), filter/QC funnels, and inheritance views (de novo, paternal/maternal inherited, recessive). Reference: [monogenic-nipt.md](../monogenic-nipt.md).
- **Population:** **Singleton pregnancies at a gestational age of more than 12 weeks**, undergoing monogenic NIPT per CMGG clinical criteria. Multiple pregnancies and gestational age ≤ 12 weeks are **outside the validated scope**. A twin pregnancy can be analysed (the owner's decision of 2026-11-21): every result of a pregnancy whose package names two or more fetuses carries a note that a fetal result may be present in one fetus or in more than one (not for a monozygotic twin, whose zygosity CoGA is not told); the parents' carrier status is read as in a singleton pregnancy.
- **Nature of result:** **Screening**, not diagnosis; fetal genotype is *inferred* from allele fraction, never directly observed. Positive/at-risk findings require confirmatory diagnostic testing.
- **Limits:**
  - A result without a fetal-specific allele is reliable only when the QC gate on the expected fetal reads passes (median target depth × FF/2 of at least QA's bound, provisionally 40; REQ-NIPT-021).
  - A gene of the diagnostic panel without a capture target is not tested.
  - Not detected: large indels and VNTRs, repeat expansions, HLA, paralogous genes and poorly captured targets.
  - The maternal allele's inheritance is the least certain inference, above all at a low fetal fraction.
  - On chrX outside the pseudo-autosomal regions, CoGA classifies only when the fetal sex is known.

### 3.2 Expanded carrier screening (long-read sequencing)
- **Purpose:** Support interpretation of **expanded carrier screening** from long-read NGS, identifying carrier status across a defined set of genes/conditions.
- **Input:** Annotated VCF and associated tracks from the validated long-read workflow; gene-panel definition.
- **Output:** Filtered/classified carrier variants with ACMG support, gene-panel-scoped views, carrier-status tracking (independent of phenotype axis).
- **Population:** Couples undergoing carrier screening on a **reproductive indication** (BeGECS). General-population screening without a reproductive indication is **outside the validated scope**.
- **Panel scope:** the gene list is a **controlled document in the CMGG kwaliteitshandboek (KHB)**. The applicable **document ID and version must be recorded per study and per release** (TF-10, TF-18) — the validated scope is the panel *version*, not the panel by name.
- **Nature of result:** Carrier-status decision support; reportable carrier findings are confirmed and signed out by the laboratory.

### 3.3 Preimplantation genetic testing (PGT)
- **Purpose:** Support PGT analysis on embryos from **shallow whole-genome sequencing**, combining (a) imputation-based **haplotype segregation** to read which embryos inherited a disease haplotype, (b) **direct mutation detection**, (c) a simultaneous **aneuploidy review**, and (d) a review for **large (>10 Mb) structural variants**. The analyst performs (c) and (d) on the segment, copy-number and APCAD tracks CoGA shows for each embryo (and, for (d), on the structural-variant calls it lists); CoGA has no automatic aneuploidy detector (REQ-PGT-008).
- **Input:** Per-embryo and family annotated VCFs, phased/imputed marker data, segment/copy-number/APCAD tracks, pedigree (including single-parent/donor configurations), ROI, inheritance model. References: [haplotype-segregation-analysis.md](../haplotype-segregation-analysis.md).
- **Output:** Per-embryo derived classification at the ROI (affected/at-risk · carrier · unaffected · uninformative) for dominant/recessive/X-linked models, with recombination- and informativeness-aware QC warnings; each embryo's segment, copy-number and APCAD tracks for the analyst's aneuploidy and large-SV review; direct-mutation genotype.
- **Population:** Couples / single-parent-plus-donor families undergoing PGT for a known segregating disorder and/or aneuploidy/structural risk.
- **Sub-scopes claimed:** comprehensive PGT — **PGT-M** (monogenic), **PGT-SR** (structural rearrangement), **PGT-AS** (aneuploidy screening) and **PGT-mitoDNA**. PGT-mitoDNA is a **sub-scope of this application**, not a separate application.
- **Claims of the analyst's review:** aneuploidy down to **> 35% mosaicism**; structural variants **> 10 Mb**. They are limits of the review the analyst performs on CoGA's tracks, not of an automatic detector. Both will be established by the [TF-10](TF-10-performance-evaluation-plan.md) study against the current GENType / HOPLA method; below these limits no claim is made.
- **Nature of result:** Embryo-selection decision support; the embryo call is *derived* from phased genotypes and pedigree, and is *uninformative* when no disease haplotype resolves or the embryo's own haplotype does not cover the ROI on a side the call needs (REQ-PGT-005). A recombination near the ROI is flagged. The call's QC at the ROI (Mendel-error rate, informative-marker count, markers on each parental side the call reads) is checked against provisional thresholds. A call whose QC falls short carries a warning, which neither withholds nor changes it and does not refuse the sign-out (owner decision of 2026-10-20). The laboratory director and QA are to confirm the thresholds.

### 3.4 Comprehensive rare-disorder diagnostics (long-read sequencing)
- **Purpose:** Support **diagnostic** interpretation of rare inherited disorders from comprehensive long-read genome sequencing, across SNV/indel, structural variants, repeat expansions (TRGT), Paraphase, and mtDNA.
- **Input:** Annotated VCFs and all associated tracks from the validated long-read genome workflow; HPO terms; pedigree.
- **Output:** Pedigree-aware candidate filtering and prioritization, semi-automatic ACMG/AMP classification, multi-data-type variant review, gene/HPO/panel context, a reproducible analysis a supervisor signs out (closed and frozen), to which the clinical report in the EPD refers.
- **Population:** Patients (and families) with suspected rare Mendelian disease referred to CMGG. **Both proband-only and family (trio) analysis are in scope.**
- **Nature of result:** Diagnostic decision support; final classification and reporting by the clinical laboratory.

### 3.5 Combined mitochondrial-disease testing (ONT long-read adaptive sampling)
- **Purpose:** Support **diagnostic** interpretation of mitochondrial disease by examining, in a single assay, the **complete mitochondrial genome (mtDNA)** together with the **nuclear genes implicated in mitochondrial pathologies**: one Oxford Nanopore (ONT) long-read **adaptive-sampling** run targets the entire mtDNA *and* the nuclear mito-gene panel.
- **Input:** Annotated VCF(s) and associated tracks from the validated ONT adaptive-sampling workflow, covering the full mtDNA and the nuclear mitochondrial-disease gene panel; HPO terms; pedigree. (The adaptive sampling itself is an upstream, separately-validated wet-lab/bioinformatics process; CoGA consumes its output, see §4 condition 1.)
- **Output:** Combined review in one family workspace — mtDNA analysis with **heteroplasmy** quantification and maternal-transmission logic (and each sample's haplogroup), plus nuclear small-variant/SV interpretation over the mito-gene panel, with semi-automatic ACMG/AMP classification and a reproducible analysis a supervisor signs out (closed and frozen), to which the clinical report in the EPD refers.
- **Data integrity / sample swaps (explicit):** mtDNA follows the maternal lineage and the nuclear and mtDNA results are read together, so the **Sample QC must be reviewed before sign-out**, and the samples' mtDNA haplogroups compared for maternal-lineage consistency. See §4 condition 7 and [TF-06](TF-06-risk-management-plan.md) hazard H4.
- **Population:** Patients with suspected mitochondrial disease. **Proband-only** analysis is the primary mode; **maternal / trio** analysis is also supported where the mother or family is available.
- **Panel scope:** the nuclear mito-gene panel is a **controlled document in the CMGG kwaliteitshandboek (KHB)**; the applicable document ID and version must be recorded per study and per release. The CMGG-specific mitochondrial validation dossier also governs this application.
- **Tissue scope:** **multiple tissues may be analysed** for heteroplasmy; the tissues covered by the validated claim will be those established by the [TF-10](TF-10-performance-evaluation-plan.md) study.
- **Nature of result:** Diagnostic decision support; mtDNA heteroplasmy and maternal inheritance are interpreted with their QC, and final classification/reporting is by the laboratory.

### 3.6 Rare-disorder diagnostics (short-read whole-genome sequencing)
- **Purpose:** Support **diagnostic** interpretation of rare inherited disorders from conventional short-read (Illumina) whole-genome sequencing, through its small-variant callset.
- **Input:** The family's joint small-variant callset from the validated short-read workflow (GATK HaplotypeCaller per sample, joint-genotyped with GenotypeGVCFs), VEP-annotated; each sample's alignment (CRAM/BAM) for review in the genome browser; HPO terms; pedigree. Reference: [data-import.md](../data-import.md).
- **Output:** As §3.4, for SNV/indel: pedigree-aware candidate filtering and prioritization, semi-automatic ACMG/AMP classification, gene/HPO/panel context, and a reproducible analysis a supervisor signs out (closed and frozen), to which the clinical report in the EPD refers.
- **Scope of the data:** SNV/indel only. Structural variants, repeat expansions, Paraphase and mtDNA heteroplasmy are not part of this application's input.
- **Population:** Patients (and families) with suspected rare Mendelian disease referred to CMGG, proband-only and family analysis (🔲 to confirm, §5).
- **Nature of result:** Diagnostic decision support; final classification and reporting by the clinical laboratory.

### 3.7 Conventional NIPT screening
- **Purpose:** Support **screening** of an ongoing pregnancy for **fetal trisomies 21, 18 and 13 and sex-chromosome aneuploidies** from the genome-wide copy number of cell-free DNA in maternal plasma (shallow whole-genome sequencing).
- **Input:** The laboratory's ccnv pipeline output in its prenatal mode, per maternal plasma: the copy number in 100-kb bins, the segments (log2 ratio, z-score, expected ratio, implied fetal fraction, discordance, origin), the gain and loss calls, and the QC file (the fetal-fraction estimates, the fetal sex, the sex-chromosome call, the limits of detection, ccnv's warnings); pedigree. CoGA takes ccnv's effective (Fisher-weighted) fetal fraction and shows the other estimates beside it. Reference: [conventional-nipt.md](../conventional-nipt.md).
- **Output (decision support):** A screening result per target, each autosome (an aneuploidy: whole, or of at least half the chromosome), the sex chromosomes and the segmental CNVs: **high risk**, **inconclusive**, **low risk** or **no result** (segmental CNVs below the limits of detection: *not assessed*), with its reasons, the findings it rests on and the bounds it applied. A segment is high risk where its z-score reaches the call bound and it is concordant with the fetal fraction (its implied fetal fraction within a band around it, not flagged discordant, of an origin that is not maternal or mosaic); a segment that is not concordant, or in the grey zone below the call bound, is inconclusive. A claimed target is never low risk while the fetal fraction is below ccnv's limit of detection of arm-level events: it is inconclusive. No target has a result below the fetal-fraction bound. Also: the genome and chromosome views of the copy number with the lines where a fetal trisomy and monosomy lie at the plasma's fetal fraction; the fetal sex and sex-chromosome call as ccnv reads them; a signed record that freezes the result, its bounds and a digest of its inputs.
- **Claims:** **trisomies 21, 18 and 13**, and **sex-chromosome aneuploidies** (45,X; 47,XXX; 47,XXY; 47,XYY). The other autosomal aneuploidies and the segmental CNVs are shown, each marked *outside the validated claim* on the result, the report and the signed record; no claim is made for them. The performance of the claims is established by the [TF-10](TF-10-performance-evaluation-plan.md) §3.7 study.
- **The rule's bounds** (the fetal-fraction floor, the z-score call and grey-zone bounds, the implied-fetal-fraction band, the sex-chromosome call confidence, the smallest segmental CNV assessed) are QA's, in the QC-threshold profile `nipt_conventional`, each change with its reason in the append-only history. A bound QA has not set takes a provisional value, the result says so, and signing it out asks for an acknowledgement (§4 condition 7). QA and the laboratory director confirm the bounds before the release candidate (§5).
- **Population:** Pregnancies at a gestational age of **more than 12 weeks**, **singleton or twin**, undergoing NIPT per CMGG clinical criteria. In a twin pregnancy the plasma gives one fetal fraction and one result, which does not say which fetus a finding concerns. **Outside the validated scope:** triplets or more; a vanishing twin; a maternal organ or bone-marrow transplant, or a blood transfusion in the months before sampling; a known maternal malignancy.
- **Pregnancies and NIPTs:** a family may hold several pregnancies of one woman, and of each the monogenic NIPT (§3.1) and the conventional NIPT, each an analysis of its own, named by the pregnancy its package gives (the owner's decision of 2026-11-21). A monogenic and a conventional NIPT are one pregnancy only when their packages name the same pregnancy. Each is read, reviewed and signed out on its own; the results of two pregnancies never merge, and a plasma or a fetus is one pregnancy's. The conventional NIPT's plasma may carry another sample ID than the monogenic NIPT's, and a later pregnancy's plasma does; the samples are then linked as one person (the same individual), never one pregnancy.
- **Nature of result:** **Screening**, not diagnosis. The cfDNA is mostly placental, so a result may reflect the placenta rather than the fetus (confined placental mosaicism), and a maternal copy-number change or mosaicism can underlie a finding (it is then inconclusive). A high risk requires confirmatory diagnostic testing; a low risk does not exclude an aneuploidy.

---

## 4. Conditions of use, limitations & contraindications

CoGA must **only** be used under the following conditions (they become labelling/IFU
statements and risk controls):

1. **Validated upstream pipeline required.** CoGA works on the output of upstream wet-lab and bioinformatics workflows that are **separately validated and ISO 15189-accredited**. It does not validate its input and cannot detect all errors in it. The provenance and versions of the upstream modules are captured per family and frozen into the signed-out analysis ([clinical-traceability.md](../clinical-traceability.md)).
2. **Decision support only.** Outputs are candidates and pre-evaluations. A qualified clinical laboratory professional must review and confirm every interpretation; CoGA does not issue autonomous results. The sign-out in CoGA, a supervisor's, closes the analysis; it is not the medical sign-off, which is given on the clinical report in the EPD (§1, condition 8).
3. **Screening vs diagnosis.** The NIPT (3.1, 3.7) and carrier (3.2) applications are **screening**; at-risk findings require confirmatory diagnostic testing.
4. **Inferred genotypes and copy number.** In NIPT (3.1) the fetal genotype, and in PGT haplotyping (3.3) the embryo haplotype, are **inferred**, not directly observed. In the conventional NIPT (3.7) the fetal copy number is read from the plasma's, at its fetal fraction. CoGA shows the QC signals that bound their reliability (fetal fraction, informative-marker counts, Mendel-error rate, recombination proximity; for 3.7, the fetal-fraction estimates, the limits of detection and ccnv's warnings); these **must** be checked before trusting a call.
5. **Defined assays/panels only.** Each application is valid only for the gene panels, assays, and assemblies for which it has been verified (TF-09/TF-11). Use outside the verified scope is off-label.
6. **Reference assembly.** The validated scope is **GRCh38 only**. T2T-CHM13 is a planned future extension and is **out of scope until separately validated**. The software enforces this: the list of validated assemblies is configuration (default GRCh38, changed only under change control per TF-18), a report on any other assembly cannot be signed out, and the family and report pages label such a family *Not validated for clinical use* (REQ-TRACE-009; TF-06 H12).
7. **Sample identity & data integrity (Sample QC).** Before the analysis is signed out, and so before any clinical report refers to it, the family's **Sample QC** page **must be reviewed to confirm sample identity and to detect sample swaps or data-integrity problems** (see [TF-06](TF-06-risk-management-plan.md) hazard H4).
   - **Checks**, depending on the application: each sample's sex against the recorded sex, the relatedness of each pair against the pedigree, and Mendelian consistency; for NIPT, paternity, fetal and parent sex and the cfDNA category pattern.
   - **Contamination is not measured.** An excess of Mendelian errors, heterozygous calls on a male's X outside the pseudo-autosomal regions and, for NIPT, an excess of de novo sites are signs consistent with it, among other causes; a pass does not rule it out. Where a mitochondrial contamination estimate is imported with a sample, the mtDNA page judges it against the laboratory's limits (REQ-QC-006).
   - **The review is mandatory** for the family/trio- and lineage-based applications (3.1, 3.3, 3.4), and especially for combined mtDNA/nuclear testing (3.5). There the analyst also compares the samples' mtDNA haplogroups, since a maternal-lineage mismatch is a further swap signal; CoGA shows each sample's haplogroup but does not compare them.
   - **Sign-out is blocked** when a Sample QC check fails, or cannot confirm a declared relationship or a sample's identity, unless the analyst records a reason (REQ-TRACE-012). For a monogenic NIPT family the gate acts on the sign-out of its analysis, from the NIPT page or the report page. A NIPT quality check that failed blocks it too: no fetal-fraction estimate, too few expected fetal reads or a QC gate on the provisional bound, fetal-sex signals that disagree, male DNA given as the maternal plasma, no diagnostic gene panel, or NIPT results the record could not read, under the same acknowledgement ([TF-02 §6](TF-02-device-description.md)).
   - **Conventional NIPT (3.7).** The Sample QC runs no check: the copy-number output holds no genotypes, and the fetal sex is known only from the analysis itself. The sign-out of a conventional NIPT is blocked, under the same acknowledgement, by a plasma without a result and by a result that rests on provisional bounds.
8. **The clinical report is signed in the EPD, outside the device.** CoGA's sign-out is the analytical sign-off, given by a supervisor: it closes the analysis and freezes it into a signed version. The clinical report is drafted and medically signed in the hospital's electronic patient record (EPD) and refers 1:1 to the signed version it rests on. CoGA does not see the EPD report and does not check it against that version ([TF-02 §3](TF-02-device-description.md)).

**Not intended for:**
- Primary base-calling, alignment, or variant calling (these are upstream).
- Somatic/tumor variant interpretation or oncology indications.
- Standalone diagnosis without professional review and sign-out.
- Use by patients, or in a non-accredited setting.
- Populations, panels, sample types, or assemblies outside the verified scope.
- A conventional-NIPT result for a target outside the claim (§3.7: the other autosomes, the
  segmental CNVs) as a validated result. It is shown, marked *outside the validated claim*.
- The **ancestry PCA** (a family's genetic similarity to public reference populations, in the
  *Ancestry (exploratory)* section of the family's Sample QC, calculated automatically from its
  stored callsets): an exploratory feature outside the intended purpose. It is not validated,
  not for clinical interpretation, and never part of the sign-out or of the signed record. The
  section and its plot are labelled *Exploratory — not for clinical use*.

---

## 5. Open items requiring confirmation

The per-application population, sub-scope and threshold questions are **resolved** (§3, recorded
in [INPUTS-QUESTIONNAIRE](INPUTS-QUESTIONNAIRE.md) A4–A5, B1–B7).

What remains before this statement can be approved:

- **🔲 Panel versions.** The BeGECS carrier gene list and the nuclear mito-gene panel are
  controlled KHB documents; their **document IDs and versions** must be cited here and in
  [TF-10](TF-10-performance-evaluation-plan.md). The validated scope is a panel *version*
  (§3.2): a panel named without its version does not fix the boundary of the performance
  evaluation.
- **🔲 Belgian transitional dates / FAMHP national provisions** (questionnaire A6) and the
  **publication location of the Art. 5(5)(f) declaration** (A7).
- **🔲 The assigned CMGGMC software number `Sxxxx`** ([TF-02](TF-02-device-description.md)).
- **🔲 Conventional NIPT (§3.7).** The owner added it as a clinical application on 2026-11-19 and
  set its population and claims on 2026-11-20 ([INPUTS-QUESTIONNAIRE](INPUTS-QUESTIONNAIRE.md) B8).
  QA and the laboratory director confirm the rule's bounds, provisional until then;
  [TF-10](TF-10-performance-evaluation-plan.md) §3.7 needs its study size and acceptance criteria;
  the ccnv pipeline's version, which its output does not yet carry, is to be recorded with each
  delivery (condition 1).
- **🔲 Short-read WGS (§3.6).** The owner added it as a clinical application on 2026-11-18. The
  owner confirms its population and its limitation to SNV/indel; [TF-10](TF-10-performance-evaluation-plan.md)
  §3.6 needs its validation set, comparator and acceptance.
