# TF-10 — Performance Evaluation Plan

| Field | Value |
| --- | --- |
| Document ID | TF-10 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹Clinical lead per application + software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Basis | IVDR (EU) 2017/746 **Annex XIII Part A**; MDCG 2020-1 (clinical evaluation of MDSW) |

> CoGA's performance is established along the three IVDR pillars: **scientific validity,
> analytical performance, clinical performance**. They are adapted to a software-only
> interpretive device: CoGA produces *interpretation/decision support*, not a measured analyte.
> The core method is **concordance of CoGA's outputs with CMGG's already-validated, accredited
> reference assays** (the comparator/predicate methods), on defined validation sample sets, per
> clinical application. The evidence is reported in
> [TF-11 Performance Evaluation Report](TF-11-performance-evaluation-report.md).
>
> **In CMGG QMS terms (H11.1-OP5 §5.2 / H11.1-OP1 §8):** this is the **klinische validatie**,
> done **per analysis/method** that uses CoGA. It is separate from the software's bio-IT
> ingangsvalidatie ([TF-09](TF-09-verification-validation.md)). The **business
> contactpersoon** coordinates it with the bio-IT team. The SOP's performance characteristics
> (accuracy, precision, reportable range, reference interval where applicable, positive/negative
> controls) become the concordance metrics of §2–§3; §7 maps them onto the CMGG form
> (H11.1-F11, report `VAL-Pxx`).

---

## 1. Performance evaluation strategy

| Pillar | For CoGA this means | Evidence |
| --- | --- | --- |
| **Scientific validity** | The scientific community has established the association of the variants/results CoGA surfaces with the clinical conditions (ACMG/AMP 2015, ClinGen, gnomAD, ClinVar, GenCC, established inheritance genetics). CoGA applies these associations; it does not invent them. | Literature and database references; **not** re-established empirically. |
| **Analytical performance** | Does CoGA correctly *process and present* its inputs and correctly *derive* its outputs? Concordance of CoGA's variant lists, classifications and derived calls with the validated comparator; reproducibility/repeatability. | This plan §3–§4; TF-09 software V&V. |
| **Clinical performance** | Does the conclusion of the analysis a supervisor signs out in CoGA (the closed, frozen analysis the clinical report in the EPD refers to, [TF-02 §3](TF-02-device-description.md)) agree with the validated assay's clinical conclusion on real cases? | This plan §3 concordance studies (the validation cases). |

The upstream wet-lab and bioinformatics workflows are separately validated and accredited, so
this evaluation isolates **CoGA's contribution**: given the same validated inputs, does CoGA
lead the analyst to the same clinical conclusion as the established assay?

## 2. Validation design (overview)

**Method:** retrospective/prospective **concordance study** per application. For each
validation case the established (already-validated, accredited) assay/result is the
**comparator ("truth")**. A qualified analyst analyses the case independently in CoGA, following
the intended workflow, and CoGA's outcome is compared with the comparator.

**Validation sample matrix (as specified by CMGG):**

| Application | Validation set | N | Comparator ("truth") |
| --- | --- | --- | --- |
| Expanded carrier screening (BeGECS, long-read) | **BeGECS couples** | **50 couples** | **Current gold standard: WES analysis + FraX + SMA + DMD deletion (MLPA) + CYP21A2 analysis** |
| PGT (shallow WGS) | **PGT embryos** | **100 embryos** | **Current GENType / HOPLA method**, and/or confirmatory testing |
| Rare-disorder diagnostics (long-read) | **WGS trios** | **30 trios** | Validated diagnostic NGS result / established clinical conclusion |
| Rare-disorder diagnostics (short-read WGS) | **🔲 (define)** | **🔲 N (define)** | 🔲 (define) |
| Monogenic NIPT | **Monogenic NIPT samples** | **30 samples** | Validated comparator (‹invasive confirmatory genotype / established NIPT result / known fetal genotype›) |
| Mitochondrial disease (ONT adaptive sampling) | **mtDNA + nuclear mito-gene cases** | **🔲 N (define)** | Validated comparator (‹established mtDNA assay + nuclear mito-gene NGS result; orthogonal heteroplasmy quantitation›) |
| Conventional NIPT | **Maternal plasmas** (singleton and twin pregnancies, > 12 weeks) | **🔲 N (define)** | **The laboratory's current routine NIPT method on the same plasmas**, with the **confirmed diagnosis** (karyotype, array, birth outcome) of the screen-positives |

**Acceptance — carrier screening (C1).** CoGA's **sensitivity and specificity must be at least
equal to the current gold-standard method**. 🔲 To make that criterion testable *before* the
study runs, the current method's numeric sensitivity/specificity, documented in the CMGG
**kwaliteitshandboek (KHB)**, must be cited here with its **document ID and version**. A purely
comparative criterion with no stated baseline cannot be failed, which H11.1-F11 does not permit.

**Analytical baseline (C6).** A run on **GIAB** reference material is included to establish
analytical performance independently of the clinical comparators above. Record the GIAB
sample(s) and truth-set version used. The baseline is captured on the commit the validation
blesses and cannot be reconstructed afterwards.

> **🔲 INPUT NEEDED (per application):** name the exact comparator method, define the unit of
> analysis and the "truth" source, and set the **acceptance criteria** (the §3 tables propose
> defaults to confirm). Also confirm prospective vs retrospective, case selection (consecutive
> vs enriched for positives), and that positive/at-risk cases are represented well enough to
> power agreement on the clinically critical calls.

**Statistics:** report agreement as **PPA** (positive percent agreement), **NPA**, **OPA**
(overall percent agreement) and **Cohen's κ** where applicable, each with **95% confidence
intervals** (Clopper–Pearson / Wilson). All **discordances are adjudicated** and root-caused
(CoGA error · comparator error · upstream-input difference · genuine interpretive
disagreement). Document any enrichment for positive/at-risk cases and note its effect on the
agreement estimates.

## 3. Per-application performance protocols

### 3.1 Expanded carrier screening — BeGECS (50 couples)
- **Unit of analysis:** per-variant carrier calls and the **couple-level at-risk** conclusion (both partners carrying a variant in the same recessive gene / X-linked finding).
- **Inputs:** validated long-read annotated VCFs over the BeGECS gene set; CoGA carrier-screening workflow.
- **Metrics:** variant-level PPA/NPA/OPA of carrier calls vs comparator; couple-level at-risk concordance (the clinically actionable output); concordance of the reported ACMG class for reportable variants.
- **Proposed acceptance (confirm):** ≥99% OPA on carrier variant detection; **100% agreement on couple-level at-risk conclusions** (any discordance investigated and resolved); all clinically reportable (P/LP) carrier variants concordant.
- **Edge cases:** technically difficult genes/regions on long-read; pseudogene/Paraphase-relevant loci; CNV/SV-mediated carrier findings.

### 3.2 PGT (100 embryos)
- **Units of analysis:** per-embryo **ROI segregation call** (affected/at-risk · carrier · unaffected · uninformative); **direct mutation** genotype; the **aneuploidy** status and the **large (>10 Mb) SV** status as the analyst reads them on CoGA's segment, copy-number and APCAD tracks (CoGA makes no automatic aneuploidy call, REQ-PGT-008).
- **Inputs:** shallow-WGS per-embryo and family VCFs, phased/imputed markers, segment/CN/APCAD tracks, pedigree (incl. single-parent/donor).
- **Metrics:**
  - Haplotype segregation: concordance of the per-embryo call with the comparator (and with the born-child/confirmatory outcome where available); rate and handling of **uninformative** calls.
  - Direct mutation detection: PPA/NPA vs comparator genotype.
  - Aneuploidy, as the analyst reads it on CoGA's tracks: per-chromosome PPA/NPA/OPA vs comparator, at the claimed limit of that review, **> 35 % mosaicism** (TF-01 §3.3).
  - Large SV, as the analyst reads it on CoGA's tracks and SV calls: concordance at the claimed **> 10 Mb** threshold; characterize the size/type limit of that review.
- **Proposed acceptance (confirm):** 100% concordance on **informative** embryo segregation calls; no **false "unaffected"** on truly at-risk embryos (the safety-critical error); aneuploidy and >10 Mb SV concordance ≥‹threshold›.
- **Edge cases:** recombination near the ROI; sparse informative markers; donor/single-parent families (expected "uninformative" for recessive — verify safe behavior); mosaic embryos; sex chromosomes; embryo data missing at the ROI (expected "uninformative"); X-linked embryos of unrecorded sex (never "carrier" or "unaffected" where a son and a daughter would differ); male embryos at a pseudo-autosomal locus (read on both copies).

### 3.3 Rare-disorder diagnostics — WGS trios (30 trios)
- **Unit of analysis:** the **diagnostic conclusion** (causal/candidate variant(s) identified and their ACMG class) and the set of reported variants.
- **Inputs:** comprehensive long-read trio data: SNV/indel, SV, repeat expansions (TRGT), Paraphase, mtDNA; HPO; pedigree.
- **Metrics:** concordance of the reported causal/candidate variant(s) with the established diagnosis; PPA for known causal variants across **each data type** (SNV/indel, SV, repeat, Paraphase, mtDNA); ACMG-class concordance; de novo / inheritance-mode concordance through the trio logic.
- **Proposed acceptance (confirm):** 100% detection of the known causal variant(s) among CoGA candidates for previously-solved trios; ACMG class within one tier and same actionability; correct inheritance/de-novo assignment.
- **Edge cases:** each non-SNV data type represented; compound-het (SNV+SV second hit); repeat-expansion and Paraphase-resolved loci; mtDNA heteroplasmy.
- **🔲 OWNER (clinical):** TF-01 §3.4 also claims proband-only analysis, but this protocol validates trios only. Add a proband-only arm, or narrow the claim.

### 3.4 Monogenic NIPT (30 samples)
- **Units of analysis:** **fetal-fraction estimate** (quantitative QC, vs comparator/known FF); **per-variant zygosity category**; and the **inheritance-preset conclusions** (de novo, paternal/maternal dominant, recessive at-risk).
- **Inputs:** the NIPT-M pipeline's single-sample cfDNA and paternal annotated VCFs with their per-target coverage tables (or combined two-sample VCFs with coverage), pedigree; external FF where available.
- **Metrics:**
  - FF: bias and correlation (Pearson/Deming, Bland–Altman) of CoGA's fetal-fraction estimate (from paternally inherited variants, category 7) vs comparator FF (the external FF, where available).
  - Category assignment & inheritance calls: PPA/NPA/OPA vs the confirmed fetal genotype/comparator; **false-negative rate** (paternal homozygous variants missing from the cfDNA, category 8).
- **Proposed acceptance (confirm):** FF within ‹±X absolute / ±Y%› of comparator; **no missed at-risk fetal calls** (false-negative is the safety-critical error for a screening test); category concordance ≥‹threshold› at adequate FF/coverage; correct low-confidence behavior at low FF/depth (no forced calls).
- **Edge cases:** low fetal fraction, low depth, category-8 dropout, FF far from the comparator.

### 3.5 Mitochondrial disease — ONT adaptive sampling (N to define)
- **Units of analysis:** the **mtDNA variant + heteroplasmy** call set (variant detection and heteroplasmy fraction), the **nuclear mito-gene** variant/diagnostic conclusion, and the combined **diagnostic conclusion**.
- **Inputs:** annotated VCF(s)/tracks from the validated ONT adaptive-sampling run (complete mtDNA + nuclear mito-gene panel); HPO; pedigree (incl. mother where available).
- **Metrics:** mtDNA variant PPA/NPA vs comparator; **heteroplasmy quantitation** agreement (bias/correlation, Bland–Altman) vs an orthogonal method; nuclear mito-gene causal-variant detection + ACMG-class concordance; maternal-inheritance concordance; **sample-swap detection** exercised: the Sample QC and the analyst's haplogroup comparison (deliberate-mismatch controls where feasible).
- **Proposed acceptance (confirm):** 100% detection of known causal mtDNA/nuclear variant(s); heteroplasmy within ‹±X%› of comparator; swaps caught on the mismatch controls; no missed causal variant.
- **Edge cases:** low heteroplasmy near the limit of detection, homoplasmy, mtDNA coverage gaps, nuclear–mtDNA dual findings, single- vs multi-tissue heteroplasmy.
- **🔲 INPUT NEEDED:** validation N, comparator method(s), heteroplasmy-agreement tolerance, and which tissues are validated.

### 3.6 Rare-disorder diagnostics — short-read WGS (N to define)
- **Unit of analysis:** as §3.3, for SNV/indel: the **diagnostic conclusion** and the set of reported variants.
- **Inputs:** the family's VEP-annotated joint GATK callset (HaplotypeCaller, GenotypeGVCFs), each sample's alignment, HPO, pedigree (TF-01 §3.6).
- **Metrics:** concordance of the reported causal/candidate SNV/indel(s) with the established diagnosis; ACMG-class concordance; de novo / inheritance-mode concordance; the callset read as GATK writes it (no FILTER of its own, physical phasing not taken for imputation).
- **🔲 INPUT NEEDED:** validation set and N, comparator, acceptance criteria, and whether proband-only cases are included.

### 3.7 Conventional NIPT (N to define)
- **Units of analysis:** the screening result of each **claimed target** per maternal plasma: trisomy 21, trisomy 18, trisomy 13 and the sex-chromosome aneuploidies (45,X; 47,XXX; 47,XXY; 47,XYY) (TF-01 §3.7); and whether the plasma has a result at all.
- **Inputs:** per maternal plasma, the ccnv pipeline's prenatal output (bins, segments, calls VCF, QC file); pedigree. The ccnv version of each plasma is recorded with the study (🔲 its output carries none yet, TF-01 §5).
- **Design:** 🔲 retrospective, prospective or both, and the case selection (consecutive, or enriched for screen-positives) (INPUTS C5). Each plasma is analysed by the comparator and in CoGA; the analyst reads CoGA's result per target without the comparator's.
- **Comparator ("truth"):** the laboratory's current routine NIPT method on the same plasmas; for the screen-positives, the confirmed diagnosis (karyotype, array, birth outcome) (owner, 2026-11-20; INPUTS C4c). 🔲 How the truth of a screen-negative is set (the comparator's result alone, or a birth outcome where it is known): the sensitivity and the NPV depend on it.
- **Metrics, per claimed target:**
  - sensitivity, specificity, PPV and NPV against the confirmed diagnosis, and agreement (PPA, NPA, OPA) with the comparator's result, each with its 95% CI;
  - the **no-result rate** (a fetal fraction below its bound) and the **inconclusive rate**, beside the comparator's own rate of failed or uncalled samples;
  - agreement of the fetal fraction (ccnv's effective estimate) with the comparator's, where the comparator gives one (bias and correlation, Bland–Altman).
- **The rule's bounds:** the study counts only on bounds QA has set, which QA and the laboratory director confirm before it starts (TF-01 §3.7, §5). A result that rests on a provisional bound is not a study result. The bounds in force are recorded with the study; a later change of a bound is a change under [TF-18](TF-18-change-configuration-management.md).
- **Twins:** in scope, and reported as a stratum of their own. The plasma gives one fetal fraction and one result, which does not say which fetus a finding concerns; an aneuploidy of one fetus lies at about half the fetal fraction.
- **Excluded:** pregnancies outside the validated scope (gestational age ≤ 12 weeks; triplets or more; a vanishing twin; a maternal organ or bone-marrow transplant, or a blood transfusion in the months before sampling; a known maternal malignancy).
- **Targets outside the claim:** the other autosomes and the segmental CNVs are recorded with each case, but not evaluated as claims (TF-01 §3.7).
- **Safety-critical error:** a **low risk** on a claimed target where the confirmed diagnosis shows the aneuploidy. Each is adjudicated (CoGA's rule or bounds, the comparator, the upstream input, or the biology: confined placental mosaicism, a maternal copy-number change). 🔲 Whether a false low risk the comparator shares counts as a failure under §5 is part of the acceptance criteria.
- **Edge cases:** a fetal fraction near its bound; the grey zone; a finding not concordant with the fetal fraction (sub- or super-fetal, of maternal or mosaic origin); a maternal copy-number change; placental or fetal mosaicism; twins; ccnv's warnings; a sex-chromosome call below its confidence bound or of mixed signals.
- **🔲 INPUT NEEDED:** design and case selection, validation N (enough screen-positives per claimed target to estimate its sensitivity), and the acceptance criteria per claimed target (sensitivity, specificity, the no-result and inconclusive rates).

## 4. Reproducibility, robustness & cross-cutting checks
- **Reproducibility/repeatability:** the same validated input gives an identical signed report (content-hash equality); a second analyst re-analyses a subset (inter-operator).
- **Robustness:** behavior on degraded inputs (low coverage, missing tracks, malformed VCF) should fail safe / warn, not silently mis-call.
- **Analytical baseline:** the GIAB run of §2 (C6).
- **Software V&V cross-reference:** unit/integration/system tests and requirements traceability are in [TF-09](TF-09-verification-validation.md); this plan covers the *clinical/analytical concordance* layer above them.

## 5. Acceptance, discordance handling & reporting
- A study **passes** when every application meets its confirmed acceptance criteria and all discordances are adjudicated with a documented root cause and, for a CoGA defect, a fix and re-test.
- Safety-critical errors (false-negative at-risk/affected calls in NIPT/PGT/carrier; missed causal variant in diagnostics) trigger **failure** and a CAPA, whatever the aggregate concordance.
- [TF-11](TF-11-performance-evaluation-report.md) documents the results, stratified analyses,
  CIs, discordance adjudication, limitations, and the resulting **claimed performance** and
  **validated scope** (panels, assays, assemblies, populations). These feed the IFU (TF-15),
  the GSPR §9.1 line (TF-03) and the residual-risk review (TF-06).

## 6. Post-market performance follow-up (PMPF)
Ongoing concordance monitoring, drift events and new edge cases seen in clinical use feed back
per [TF-16 PMS](TF-16-post-market-surveillance-plan.md). The validated scope is re-confirmed on
material change (new panel, assay, assembly or algorithm version) through TF-18 change control,
which defines when re-validation is required.

## 7. Mapping to the CMGG report form (H11.1-F11)

Each clinical application's evaluation is reported **per method/analysis** on **template
H11.1-F11** (v6, 05-01-2023) as report `VAL-Pxx` (xx follows the analysis-protocol code
H10.1-Pxx; the form adds the year to the file name). The laboratoriumverantwoordelijke(n) and
the kwaliteitsbeheerder sign it. CoGA's qualitative/interpretive outputs map onto the form's
performance vocabulary:

| H11.1-F11 parameter | For CoGA |
| --- | --- |
| Type test | **Qualitative** (variant/embryo/fetal-risk present or not); NIPT fetal fraction is a derived quantitative QC value. The conventional NIPT's result per target is qualitative, read from ccnv's quantitative values (§3.7). |
| **Analytical performance** — qualitative: **analytische sensitiviteit / specificiteit** | = **PPA / NPA** vs the validated comparator (§2–§3). Genetic-test target: **~100% sensitivity** (no missed at-risk/causal call, the safety-critical error in each §3 protocol). |
| Trueness / precision (quantitative) | NIPT FF bias (Bland–Altman) + repeatability; mtDNA heteroplasmy agreement (§3.5); the conventional NIPT's fetal-fraction agreement with the comparator, where it gives one (§3.7). |
| Herhaalbaarheid / reproduceerbaarheid | Content-hash reproducibility + inter-operator subset (§4). |
| Vergelijking met een 2e onafhankelijke methode; controlemateriaal; **3/n regel**; EKE/interlab | The comparator assay is the independent method; reference material (the GIAB baseline, §2); estimate sens/spec with the **3/n rule** when control counts are small; EKE/interlab where available. |
| Robuustheid, detectielimiet | Degraded-input fail-safe (REQ-PERF-003); the limits of the analyst's >10 Mb SV and aneuploidy review on CoGA's tracks (§3.2); heteroplasmy detection limit (§3.5); the conventional NIPT's fetal-fraction bound, below which there is no result, and its no-result and inconclusive rates (§3.7). |
| Risico op staalverwisseling; subjectiviteit | Sample QC (sample-swap, TF-06 H4); inter-operator/blind re-read on a subset (§4). |
| **Clinical performance** — diagnostische sens/spec, PPV/NPV, likelihood ratio, verwachte waarden | The conclusion of the signed-out analysis vs the established diagnosis (§3); for the conventional NIPT, sensitivity, specificity, PPV and NPV per claimed target against the confirmed diagnosis (§3.7); for new applications, accumulate through PMPF (§6). |
| Risicoanalyse — veiligheidseisen IVDR (§19) | [TF-06](TF-06-risk-management-plan.md) (shared with the bio-IT validation). |
| Gevolgen voor kwaliteitsdocumentatie (§21) | Update the analysis protocol (H10.1-Pxx), IFU ([TF-15](TF-15-instructions-for-use.md)), and CMGGMC. |

The pre-defined **aanvaardingscriteria** (the §3 "proposed acceptance" rows, confirmed with the
clinical leads) go in the form's acceptance column. The **🔲 INPUT NEEDED** items in §2–§3 are
the fields still to fix before the form is signed.

## 8. Clinical behaviour developed before the release candidate

Before the first release candidate no change is recorded one by one
([TF-18 §3a](TF-18-change-configuration-management.md)): the pull request and the git history
are the record. This section lists, by area, the clinical behaviour built during development
that the first validation must exercise beyond the per-application protocols of §3. At the
release candidate QA confirms the list as the validation scope; from then on each change is
assessed and recorded in [TF-18 §8](TF-18-change-configuration-management.md). A pre-release
pull request updates this list only when it adds clinical behaviour that no area below covers.

- **Variant filtering and genotypes**
  - One genotype classification for every filter, inheritance check and count, haploid and
    multi-allelic calls included.
  - Frequency presets that also bound popmax; the ClinVar P/LP frequency override.
  - Unreadable filter values and location filters refused, not dropped.
  - Gene-panel regions per assembly.
  - A gene panel of any size, the Mendeliome included, keeps the variants in its regions as
    well as those annotated with its genes. So the default view also lists a variant inside a
    Mendeliome gene whose annotation names another gene.
  - The review-classification filters, on the family page and in the Variant Explorer, keep a
    variant by the class its review shows: a bare class word in the text, else its class tag,
    else the text; the compound-het pair's review alike.
- **Inheritance**
  - De novo and hemizygous calls on a son's X and Y, and the sex-aware PM6/PS2 rule.
  - Compound-heterozygote pairing with read-backed phasing and informative relatives; the SV
    second-hit index.
  - Expanded carrier screening keeps the genes in which both partners carry a variant and, on
    chrX outside the pseudo-autosomal regions, a female partner's variant on its own (never a
    mitochondrial variant). Its preset reads rare high- or moderate-impact variants with the
    ClinVar P/LP frequency override, so a long-read genome's screen reaches chrX within the
    candidate window, and leaves out ClinVar benign and likely benign variants.
- **Structural variants, CNVs and repeats**
  - SV gene and panel filters before the candidate cap. A gene panel of any size, the
    Mendeliome included, keeps the SVs whose annotation names one of its genes as well as
    those overlapping one of its regions.
  - The SV review-classification filter keeps an SV by the class its review shows on the SV
    page: a bare class word in the text, else its class tag, else the text; a CNV
    classification through the class label it saves as the text.
  - SV length filters measure a call's SVLEN (without one, the length its alleles state for a
    call written as its sequence, END − POS otherwise), include an insertion written with END
    at POS, and let a translocation pass any minimum but no exact length.
  - Every reader reads SV records written without END or SVTYPE as VCF 4.4 defines them (a
    deletion over its SVLEN, a breakend as a BND), and a breakend's mate alike (a TRA's from
    INFO/CHR2 and END). A deletion, duplication, inversion or CNV that states neither END nor
    SVLEN is left out and counted.
  - An uploaded CNV's copy number, which gives it its gain or loss.
  - Two events on one position are two SVs, by an order of the events, in an upload as in a
    NeedlR import. NeedlR calls are stored one SV per event, named by its coordinates as an
    uploaded call is; two calls under one record ID are never one SV; each sample keeps its own
    call where one event's lines merge.
  - The clinical CNV knowledge base (ClinGen curation and recurrent regions, ClinVar support,
    GRCh37 and GRCh38 builds).
  - Repeat-expansion status, including contraction loci, unclassifiable alleles and an FMR1
    premutation told from the grey zone.
  - CNV caller ingestion (HiFiCNV, WisecondorX, QDNAseq) and their signal tracks.
- **Interpretation**
  - The ACMG suggestions: conflicting ClinVar; parents from the pedigree; PP2 offered for
    review, never applied, at a gnomAD missense Z of 3.09 or more (the gene profile's or, when
    it holds none, the VCF's own); a de novo variant as PS2 where the Sample QC confirms the
    parents and PM6 where it does not, at the strength of ClinGen's de novo points, the phenotype
    tier read from the gene–phenotype match score (its cut-offs to calibrate on solved trios).
  - Criteria never applied together (PS2 and PM6, PVS1 and PP3, …): the dialog keeps one, the
    server refuses both; PM1 and PP3 together at most Strong.
  - Classifications that reopen with their saved criteria; CNV classifications preserved by
    review edits.
  - The prioritised ranking and its cache keys.
- **Application logic**
  - Monogenic NIPT: one fetal fraction; paternally allowed fetal states; the R NIPT-M
    validation's quality filter and model constants; paternity from how many of the father's
    homozygous and of his heterozygous alleles the plasma shows; the fetal sex from the
    father's X alleles and the plasma's chrY coverage; the maternal-plasma sex profile and the
    per-target coverage check; the fetal-inheritance probabilities; the de novo triage; the
    per-gene recessive risk; an allele written as an MNV in one sample and as SNVs in the
    other; the audited artifact list and its import from the pipeline's recurrent table; each
    allele's recurrence in the assay's other cfDNA samples and the filter on it; a report that
    says when it is incomplete.
  - PGT embryo calls that are uninformative without supporting data; that carry a warning when
    their phasing QC at the ROI falls below its provisional thresholds over the ROI and 1 Mb on
    each side (informative sites, Mendelian error rate, markers on each flank of the ROI on
    each side the call reads); and that are not called where their markers at the ROI mix both
    of a parent's homologs (a trisomy the blocks cannot show).
  - The risk-haplotype display: a parent's phase switches undone where the embryos switch
    together; a relative of unknown degree coloured along the genome when it is the linked
    member's parent or child, and otherwise at the ROI where both flanks name the haplotype it
    shares; relatives and a donor family's embryos coloured on chrX by their recorded sex (a
    man's one X between the PARs, none passed from a man to his son); the affected parent's
    and the index's statuses from the inheritance model.
  - The mtDNA haplogroup and per-sample mitochondrial imports.
  - Conventional NIPT: a result per target by z-score and fetal-fraction concordance over ccnv's
    segments and QC, with ccnv's effective fetal fraction; no result below the fetal-fraction
    bound; a finding not concordant with the fetal fraction, or of maternal or mosaic origin,
    inconclusive, never high risk; segmental CNVs assessed within ccnv's limits of detection; the
    sex chromosomes from ccnv's sex-chromosome call; QA's bounds in the profile
    `nipt_conventional`, a provisional bound named on the result; the warnings of a low fetal
    fraction, ccnv's own and a twin pregnancy; each target marked whether the intended purpose
    claims it, the others shown *outside the validated claim*; the ccnv tracks drawn with the
    lines where a fetal trisomy and monosomy lie at the fetal fraction.
  - The Sample QC read from sites across every autosome, so sibling embryos read as siblings,
    and its sex check from chrX outside the pseudo-autosomal regions.
- **Data integrity**
  - Package imports:
    - the long-read layout;
    - the long-read couple without a PED: its members and their sex from the pipeline's
      folders and TRGT's karyotype, and its one SNV VCF per partner read as one callset, a
      partner without a record at the other's variant read as reference;
    - the PGT pipeline layout, its pedigree the PED at the top of the folder (embryos, the
      index, a relative linked by `related_to=`), and each embryo's segregation table, drawn as
      the parental-origin view of an aneuploid chromosome;
    - the monogenic NIPT pair: one VCF per sample with the father's noise left out, and the
      per-target coverage tables;
    - the conventional NIPT's ccnv output (bins, segments, calls VCF, QC file), refused unless
      the files are one run's result; its plasma, under another sample ID than the monogenic
      NIPT's plasma of the same pregnancy, linked to it as the same individual;
    - a package read where it lies, on a share or a bucket mounted as a folder; snapshot/restore.
  - Coverage BEDs that declare whether they hold read depth or a log2 ratio: read as depth
    only when declared so; an undeclared one drawn without an axis.
  - Serialised per-family variant writes.
  - Per-sample and per-source rewrites and deletes that keep every other call; calls from two
    callsets or sources both kept.
  - Concurrent review saves refused when stale.
- **Review of repeat expansions and Paraphase findings**
  - One review per family and assembly per repeat locus and Paraphase region, with tags, a
    note and a classification on the type's own scale, beside the unchanged automatic status.
  - The calls a classification or the Report tag rests on, frozen and compared at sign-out (a
    re-import, a catalogue threshold or a count correction changes them), and
    counted in the evidence-drift gate.
  - An abnormal call without a review acknowledged with a reason (a count near a boundary
    included, a grey zone alone not).
  - The reviews frozen into the record beside every call
    (docs/repeat-paraphase-review-design.md).
- **Sign-out and the signed record**
  - Who may sign out: a supervisor of the family's project, a superuser or an admin; an
    analyst, a viewer and a reader are refused before any gate, and nothing is written (TF-06
    H15).
  - The frozen snapshot and its drift gates (small variants, SVs and CNVs); the
    sample-integrity, sample-QC, incomplete-import and assembly-scope gates.
  - The signed view rendered and printed from its frozen record alone.
  - Each reported variant's class, frozen at sign-out as its review shows it on the family page
    (a bare class word in the text, else its class tag, else the text) and shown so on the live
    report and the signed version, with the ACMG or CNV score beside it when that is another
    class; an SV's CNV scoring never shown as its class; a class changed since sign-out, by its
    text alone too, named by the check.
  - The software, annotation, reference, HPO and QC-limit versions it binds.
  - What the analysis looked at, frozen with it: each reported variant's description and
    genotypes; the gene-panel versions the family's searches applied; the reference-data
    releases; the repeat-expansion and Paraphase calls with their status; in a PGT family, each
    embryo's call at the ROI as the family page shows it; in a monogenic NIPT family, its NIPT
    results as the NIPT page computes them, signed out from the NIPT report too, with a failed
    NIPT quality check gating the sign-out with the Sample QC; in a conventional NIPT family,
    each plasma's result as the cNIPT page shows it, with its bounds and inputs, a plasma
    without a result or a result on a provisional bound gating the sign-out with the Sample QC;
    and what each coverage track held (read depth, a log2 ratio or unknown) and where it was
    loaded from.
  - A family that holds several analyses (the monogenic and the conventional NIPT of one
    pregnancy; the NIPTs of several pregnancies of one woman, one NIPT analysis per pregnancy,
    keyed by the pregnancy its package names; short-read and then long-read WGS of the same DNA)
    signs each out on its own, its record freezing and checking that analysis's data. Each
    pregnancy keeps its own data, the father's calls among them, so an import of one pregnancy
    replaces nothing of another's, and a package of one pregnancy that lists another's maternal
    plasma or fetus is refused.
  - The report code the EPD report quotes, its check symbol and its lookup.
  - The device label and the build identity.
  - Every review, classification and phenotype change on the clinical audit trail, a
    compound-het pair review, every change of a family's pedigree and members and a family's
    deletion included, and the pedigree's structure versions, append-only and hash-chained per
    family.
- **Reference data** — the gene reference (HGNC register, GENCODE loci, per-source releases),
  dbNSFP 5.4, HPO releases, and T2T-CHM13v2.0 as an optional assembly outside the validated
  scope.
- **Failures shown as failures** — viewers, tracks, reports, searches and exports show a
  failed or capped request as such, never as empty or complete data. A sample whose sequencing
  QC was not assessed (no limit configured, or no verdict served) reads *Not run* in the
  family members table, never as a pass.
- **Platform** — Python 3.12 and ClickHouse 26.8 LTS under the validated build.
