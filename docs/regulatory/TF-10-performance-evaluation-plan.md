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

> Performance of a software-only interpretive device is established along the three IVDR
> pillars — **scientific validity, analytical performance, clinical performance** — adapted
> to the fact that CoGA produces *interpretation/decision support*, not a measured analyte.
> The core method is **concordance of CoGA's outputs against CMGG's already-validated,
> accredited reference assays** (the comparator/predicate methods), on defined validation
> sample sets, per clinical application. The resulting evidence is reported in
> [TF-11 Performance Evaluation Report](TF-11-performance-evaluation-report.md).
>
> **In CMGG QMS terms (H11.1-OP5 §5.2 / H11.1-OP1 §8):** this is the **klinische validatie**
> performed **per analysis/method** that uses CoGA, distinct from the software's bio-IT
> ingangsvalidatie ([TF-09](TF-09-verification-validation.md)), and coordinated by the
> **business contactpersoon** with the bio-IT team. The SOP's performance characteristics
> (accuracy, precision, reportable range, reference interval where applicable, positive/negative
> controls) become the concordance metrics of §2–§3; how they map onto the CMGG form
> (H11.1-F11, report `VAL-Pxx`) is in §7.

---

## 1. Performance evaluation strategy

| Pillar | For CoGA this means | Evidence |
| --- | --- | --- |
| **Scientific validity** | The association of the variants/results CoGA surfaces with the clinical conditions is established by the scientific community (ACMG/AMP 2015, ClinGen, gnomAD, ClinVar, GenCC, established inheritance genetics) — CoGA applies, not invents, these associations. | Literature & database references; this is **not** re-established empirically. |
| **Analytical performance** | Does CoGA correctly *process and present* its inputs and correctly *derive* its outputs? Concordance of CoGA's variant lists, classifications, and derived calls against the validated comparator; reproducibility/repeatability. | This plan §3–§4; TF-09 software V&V. |
| **Clinical performance** | Does CoGA's *final, signed-out clinical conclusion* agree with the validated assay's clinical conclusion on real cases? | This plan §3 concordance studies (the validation cases). |

Because the upstream wet-lab and bioinformatics workflows are separately validated and
accredited, this evaluation isolates **CoGA's contribution**: given the same validated
inputs, does CoGA lead the analyst to the same clinical conclusion as the established assay?

## 2. Validation design (overview)

**Method:** retrospective/prospective **concordance study** per application. For each
validation case, the established (already-validated, accredited) assay/result is the
**comparator ("truth")**; the case is independently analysed in CoGA by a qualified
analyst following the intended workflow, and CoGA's outcome is compared to the comparator.

**Validation sample matrix (as specified by CMGG):**

| Application | Validation set | N | Comparator ("truth") |
| --- | --- | --- | --- |
| Expanded carrier screening (BeGECS, long-read) | **BeGECS couples** | **50 couples** | **Current gold standard: WES analysis + FraX + SMA + DMD deletion (MLPA) + CYP21A2 analysis** |
| PGT (shallow WGS) | **PGT embryos** | **100 embryos** | **Current GENType / HOPLA method**, and/or confirmatory testing |
| Rare-disorder diagnostics (long-read) | **WGS trios** | **30 trios** | Validated diagnostic NGS result / established clinical conclusion |
| Monogenic NIPT | **Monogenic NIPT samples** | **30 samples** | Validated comparator (‹invasive confirmatory genotype / established NIPT result / known fetal genotype›) |
| Mitochondrial disease (ONT adaptive sampling) | **mtDNA + nuclear mito-gene cases** | **🔲 N (define)** | Validated comparator (‹established mtDNA assay + nuclear mito-gene NGS result; orthogonal heteroplasmy quantitation›) |

**Acceptance — carrier screening (C1).** CoGA's **sensitivity and specificity must be at least
equal to the current gold-standard method**. 🔲 To make that criterion testable *before* the study
runs, the current method's numeric sensitivity/specificity — documented in the CMGG
**kwaliteitshandboek (KHB)** — must be cited here with its **document ID and version**. A purely
comparative criterion with no stated baseline cannot be failed, which H11.1-F11 does not permit.

**Analytical baseline (C6).** A run against **GIAB** reference material is included, to establish
analytical performance independently of the clinical comparators above. Record the GIAB sample(s)
and truth-set version used; that baseline is captured on the commit the validation blesses, and is
not reconstructable afterwards.

> **🔲 INPUT NEEDED (per application):** name the exact comparator method, define
> the unit of analysis and the "truth" source, and set the **acceptance criteria** (the
> §3 tables propose defaults to confirm). Also confirm prospective vs retrospective,
> case selection (consecutive vs enriched for positives), and that positive/at-risk cases
> are sufficiently represented to power agreement on the clinically critical calls.

**Statistics:** report agreement as **PPA** (positive percent agreement), **NPA**,
**OPA** (overall percent agreement), and **Cohen's κ** where applicable, each with **95%
confidence intervals** (Clopper–Pearson / Wilson). All **discordances are adjudicated** and
root-caused (CoGA error · comparator error · upstream-input difference · genuine
interpretive disagreement). Enrichment for positive/at-risk cases is documented and its
effect on agreement estimates noted.

## 3. Per-application performance protocols

### 3.1 Expanded carrier screening — BeGECS (50 couples)
- **Unit of analysis:** per-variant carrier calls and the **couple-level at-risk** conclusion (both partners carrying a variant in the same recessive gene / X-linked finding).
- **Inputs:** validated long-read annotated VCFs over the BeGECS gene set; CoGA carrier-screening workflow.
- **Metrics:** variant-level PPA/NPA/OPA of carrier calls vs comparator; couple-level at-risk concordance (the clinically actionable output); concordance of reported ACMG class for reportable variants.
- **Proposed acceptance (confirm):** ≥99% OPA on carrier variant detection; **100% agreement on couple-level at-risk conclusions** (any discordance investigated and resolved); all clinically reportable (P/LP) carrier variants concordant.
- **Edge cases:** technically difficult genes/regions on long-read; pseudogene/Paraphase-relevant loci; CNV/SV-mediated carrier findings.

### 3.2 PGT (100 embryos)
- **Units of analysis:** per-embryo **ROI segregation call** (affected/at-risk · carrier · unaffected · uninformative); **direct mutation** genotype; **aneuploidy** status; **large (>10 Mb) SV** status.
- **Inputs:** shallow-WGS per-embryo and family VCFs, phased/imputed markers, segment/CN/APCAD tracks, pedigree (incl. single-parent/donor).
- **Metrics:**
  - Haplotype segregation: concordance of per-embryo call vs comparator (and vs born-child/confirmatory outcome where available); rate and handling of **uninformative** calls.
  - Direct mutation detection: PPA/NPA vs comparator genotype.
  - Aneuploidy: per-chromosome PPA/NPA/OPA vs comparator, at the claimed limit of **> 35 % mosaicism** (TF-01 §3.3).
  - Large SV: detection concordance at the claimed **> 10 Mb** threshold; characterize the size/type detection limit.
- **Proposed acceptance (confirm):** 100% concordance on **informative** embryo segregation calls; no **false "unaffected"** on truly at-risk embryos (the safety-critical error); aneuploidy and >10 Mb SV concordance ≥‹threshold›.
- **Edge cases:** recombination near the ROI, sparse informative markers, donor/single-parent families (expected "uninformative" for recessive — verify safe behavior), mosaic embryos, sex chromosomes; embryo data missing at the ROI (expected "uninformative"); X-linked embryos of unrecorded sex (never "carrier" or "unaffected" where a son and a daughter would differ); male embryos at a pseudo-autosomal locus (read on both copies).

### 3.3 Rare-disorder diagnostics — WGS trios (30 trios)
- **Unit of analysis:** the **diagnostic conclusion** (causal/candidate variant(s) identified and their ACMG class) and the set of reported variants.
- **Inputs:** comprehensive long-read trio data: SNV/indel, SV, repeat expansions (TRGT), Paraphase, mtDNA; HPO; pedigree.
- **Metrics:** concordance of the reported causal/candidate variant(s) with the established diagnosis; PPA for known causal variants across **each data type** (SNV/indel, SV, repeat, Paraphase, mtDNA); ACMG-class concordance; de novo / inheritance-mode concordance via the trio logic.
- **Proposed acceptance (confirm):** 100% detection of the known causal variant(s) among CoGA candidates for previously-solved trios; ACMG class within one tier and same actionability; correct inheritance/de-novo assignment.
- **Edge cases:** each non-SNV data type represented; compound-het (SNV+SV second hit); repeat-expansion and Paraphase-resolved loci; mtDNA heteroplasmy.
- **🔲 OWNER (clinical):** TF-01 §3.4 also claims proband-only analysis, but this protocol validates trios only. Add a proband-only arm, or narrow the claim.

### 3.4 Monogenic NIPT (30 samples)
- **Units of analysis:** **fetal-fraction estimate** (quantitative QC, vs comparator/known FF); **per-variant zygosity category**; and the **inheritance-preset conclusions** (de novo, paternal/maternal dominant, recessive at-risk).
- **Inputs:** combined two-sample (paternal + cfDNA) annotated VCFs, coverage, pedigree; external FF where available.
- **Metrics:**
  - FF: bias and correlation (Pearson/Deming, Bland–Altman) of CoGA's fetal-fraction estimate (from paternally inherited variants, category 7) vs comparator FF; agreement with external FF (disagreement-flag behavior).
  - Category assignment & inheritance calls: PPA/NPA/OPA vs the confirmed fetal genotype/comparator; **false-negative rate** (paternal homozygous variants missing from the cfDNA, category 8).
- **Proposed acceptance (confirm):** FF within ‹±X absolute / ±Y%› of comparator; **no missed at-risk fetal calls** (false-negative is the safety-critical error for a screening test); category concordance ≥‹threshold› at adequate FF/coverage; correct low-confidence behavior at low FF/depth (no forced calls).
- **Edge cases:** low fetal fraction, low depth, category-8 dropout, FF disagreement with external estimate.

### 3.5 Mitochondrial disease — ONT adaptive sampling (N to define)
- **Units of analysis:** the **mtDNA variant + heteroplasmy** call set (variant detection and heteroplasmy fraction), the **nuclear mito-gene** variant/diagnostic conclusion, and the combined **diagnostic conclusion**.
- **Inputs:** annotated VCF(s)/tracks from the validated ONT adaptive-sampling run (complete mtDNA + nuclear mito-gene panel); HPO; pedigree (incl. mother where available).
- **Metrics:** mtDNA variant PPA/NPA vs comparator; **heteroplasmy quantitation** agreement (bias/correlation, Bland–Altman) vs an orthogonal method; nuclear mito-gene causal-variant detection + ACMG-class concordance; maternal-inheritance concordance; **sample-swap detection** exercised — the Sample QC and the analyst's haplogroup comparison (deliberate-mismatch controls where feasible).
- **Proposed acceptance (confirm):** 100% detection of known causal mtDNA/nuclear variant(s); heteroplasmy within ‹±X%› of comparator; swaps caught on the mismatch controls; no missed causal variant.
- **Edge cases:** low heteroplasmy near the limit of detection, homoplasmy, mtDNA coverage gaps, nuclear–mtDNA dual findings, single- vs multi-tissue heteroplasmy.
- **🔲 INPUT NEEDED:** validation N, comparator method(s), heteroplasmy-agreement tolerance, and which tissues are validated.

## 4. Reproducibility, robustness & cross-cutting checks
- **Reproducibility/repeatability:** same validated input → identical signed report (content-hash equality); re-analysis by a second analyst (inter-operator) on a subset.
- **Robustness:** behavior on degraded inputs (low coverage, missing tracks, malformed VCF) — should fail safe / warn, not silently mis-call.
- **Analytical baseline:** the GIAB run of §2 (C6).
- **Software V&V cross-reference:** unit/integration/system tests and requirements traceability are in [TF-09](TF-09-verification-validation.md); this plan covers the *clinical/analytical concordance* layer above them.

## 5. Acceptance, discordance handling & reporting
- A study **passes** when every application meets its confirmed acceptance criteria and all discordances are adjudicated with documented root cause and (where a CoGA defect) a fix + re-test.
- Safety-critical errors (false-negative at-risk/affected calls in NIPT/PGT/carrier; missed causal variant in diagnostics) trigger **failure** and a CAPA, regardless of aggregate concordance.
- Results, stratified analyses, CIs, discordance adjudication, limitations, and the
  resulting **claimed performance** and **validated scope** (panels, assays, assemblies,
  populations) are documented in [TF-11](TF-11-performance-evaluation-report.md) and feed
  the IFU (TF-15), the GSPR §9.1 line (TF-03), and the residual-risk review (TF-06).

## 6. Post-market performance follow-up (PMPF)
Ongoing concordance monitoring, drift events, and any new edge cases observed in clinical
use feed back per [TF-16 PMS](TF-16-post-market-surveillance-plan.md); the validated scope
is re-confirmed on material change (new panel, assay, assembly, or algorithm version) via
TF-18 change control, which defines when re-validation is required.

## 7. Mapping to the CMGG report form (H11.1-F11)

Each clinical application's evaluation is reported **per method/analysis** on **template
H11.1-F11** (v6, 05-01-2023) as report `VAL-Pxx` (xx follows the analysis-protocol code
H10.1-Pxx; the form adds the year to the file name), signed by the
laboratoriumverantwoordelijke(n) and kwaliteitsbeheerder.
CoGA's qualitative/interpretive outputs map onto the form's performance vocabulary as follows:

| H11.1-F11 parameter | For CoGA |
| --- | --- |
| Type test | **Qualitative** (variant/embryo/fetal-risk present or not); NIPT fetal fraction is a derived quantitative QC value. |
| **Analytical performance** — qualitative: **analytische sensitiviteit / specificiteit** | = **PPA / NPA** vs the validated comparator (§2–§3). Genetic-test target: **~100% sensitivity** (no missed at-risk/causal call) — CoGA's safety-critical error in each §3 protocol. |
| Trueness / precision (quantitative) | NIPT FF bias (Bland–Altman) + repeatability; mtDNA heteroplasmy agreement (§3.5). |
| Herhaalbaarheid / reproduceerbaarheid | Content-hash reproducibility + inter-operator subset (§4). |
| Vergelijking met een 2e onafhankelijke methode; controlemateriaal; **3/n regel**; EKE/interlab | The comparator assay is the independent method; reference material (the GIAB baseline, §2); estimate sens/spec with the **3/n rule** when control counts are small; EKE/interlab where available. |
| Robuustheid, detectielimiet | Degraded-input fail-safe (REQ-PERF-003); >10 Mb SV / aneuploidy / heteroplasmy detection limits (§3.2, §3.5). |
| Risico op staalverwisseling; subjectiviteit | Sample QC (sample-swap, TF-06 H4); inter-operator/blind re-read on a subset (§4). |
| **Clinical performance** — diagnostische sens/spec, PPV/NPV, likelihood ratio, verwachte waarden | The signed-out clinical conclusion vs the established diagnosis (§3); for new applications, accumulate via PMPF (§6). |
| Risicoanalyse — veiligheidseisen IVDR (§19) | [TF-06](TF-06-risk-management-plan.md) (shared with the bio-IT validation). |
| Gevolgen voor kwaliteitsdocumentatie (§21) | Update the analysis protocol (H10.1-Pxx), IFU ([TF-15](TF-15-instructions-for-use.md)), and CMGGMC. |

Pre-defined **aanvaardingscriteria** (the §3 "proposed acceptance" rows, confirmed with the
clinical leads) go in the form's acceptance column; **🔲 INPUT NEEDED** items in §2–§3 are the
fields still to fix before the form is signed.

## 8. Behaviour changed during development that the first validation must cover

These changes were made before the first release candidate ([TF-18 §3a](TF-18-change-configuration-management.md)).
Each was proposed as a minor or major change when it was made: it alters a clinical output, what a
user sees or what the signed record holds, or it replaces a component those rest on. The first
validation must exercise each one, and QA confirms the list and the levels at the release
candidate. One line per change: what changed, its pull request, and the proposed level.

### Proposed major

- Every small-variant list serves the review's ACMG record, so reopening a classified variant shows its saved criteria and a re-save keeps them (#662; proposed Major)
- SV/CNV classifications freeze their evidence, and sign-out gates on its drift (#661; proposed Major)
- A signed case shows and prints its frozen record, and the live report says it is not the signed version (#659; proposed Major)
- PGT embryo calls no longer reassure without the data to support them (#652; proposed Major)
- Sign-out refuses a partly imported family unless the signer acknowledges it (#650; proposed Major)
- Monogenic NIPT: one fetal fraction, fetal states the father allows, confident paternity and a protected artifact list (#646; proposed Major)
- Compound-het phase comes from informative relatives only, and the SV second-hit index follows every SV change (#645; proposed Major)
- ACMG suggestions read a conflicting ClinVar record as neither pathogenic nor benign, and take the parents from the pedigree links (#644; proposed Major)
- The ACMG dialog applies the sex-aware de novo rule for PM6/PS2 (#622; proposed Major)
- A son's hemizygous de novo call on chrX or chrY is a de novo candidate (#620; proposed Major)
- Repeat status: an unclassifiable allele surfaces for review; monotonic boundaries read conservatively; deterministic catalogue match (#540; proposed Major)
- ClinVar P/LP frequency override applied in the ClickHouse query (#537; proposed Major)
- Repeat-expansion status at contraction loci (#474; proposed Major)
- SV gene/panel filters applied before the candidate cap; SV second-hit badge leads to its SVs (#468; proposed Major)
- Frequency presets bound popmax at the same ceiling as the global AF (#466; proposed Major)
- Compound-het pairing uses read-backed phasing (#465; proposed Major)
- T2T-CHM13v2.0 importable as a second human assembly (off by default) (#452; proposed Major)
- Gene reference rebuilt: HGNC as the gene register, every source for every gene, per-source releases, and gene loci from GENCODE (#443; proposed Major)
- dbNSFP gene table pinned to release 5.4 (#435; proposed Major)

### Proposed minor

- Deleting a sample keeps every other small-variant call as stored (#655; proposed Minor)
- A per-sample SV upload or delete writes every other call back as stored; SV uploads record their caller (#654; proposed Minor)
- A package imported from a bucket works, and is traceable, in cloud mode (#653; proposed Minor)
- The audit log cannot be switched off in production, the two signing keys must differ, and only an admin can replace a family's annotation manifest, on the audit trail (#651; proposed Minor)
- SV/CNV review changes, and a cleared review, are recorded in the clinical audit trail (#649; proposed Minor)
- The app and every report name the running build and carry the device label; problems go to the CMGG route (#648; proposed Minor)
- A signed report names the HPO release; the admin summary and the ranking cache name the one that is loaded (#647; proposed Minor)
- A per-sample SV upload checks, merges and replaces only its own source (#643; proposed Minor)
- A review edit no longer erases a stored CNV classification or its label (#639; proposed Minor)
- The Clinical CNV Explorer shows the knowledgebase's ClinVar loss/gain support (#625; proposed Minor)
- A location filter that cannot be read is refused, not dropped, and the NIPT search applies its location filters (#615; proposed Minor)
- The Circos page shows a failure as a failure, and a capped SV list as capped (#593; proposed Minor)
- The genome overview draws the risk haplotype only where it means something (#592; proposed Minor)
- The SV tracks say when a view holds more SVs than they can draw (#590; proposed Minor)
- The variant explorer applies what it offers, and offers only what it applies (#580; proposed Minor)
- ClickHouse moved to the 26.8 LTS line (#563; proposed Minor)
- Backend runtime moved to Python 3.12 (#555; proposed Minor)
- Concurrent review saves: a stale save is refused instead of silently overwriting (#546; proposed Minor)
- One genotype classification for every filter, inheritance check, count and presence check (#544; proposed Minor)
- Gene-panel coordinates scoped per assembly (#543; proposed Minor)
- Off-scope assembly guard: sign-out refused and the family labelled outside the validated assembly scope (#542; proposed Minor)
- Sign-out and review fallbacks made explicit instead of silently empty (#541; proposed Minor)
- CSV exports no longer cut silently at 10,000 rows (#538; proposed Minor)
- Viewer tracks show a failed request as a failure, never as an empty region (#533; proposed Minor)
- The report is presented as signed only while it matches the signed record; reported SVs frozen; drift override needs a reason (#532; proposed Minor)
- Prioritised-ranking cache keyed on the family's variant data and on the reference data the scores read (#531; proposed Minor)
- Small-variant card: HGVS.g headline, cytoband, gene–disease context, CCDS/RefSeq per transcript, AlphaMissense shown, parsed variant IDs (#457; proposed Minor)
- Gene Explorer: transcript designations from GENCODE, identifiers and links by accession, and the release behind each source (#447; proposed Minor)
- Traceability for QC cut-off changes (recorded 2026-07-30; proposed Minor)
- QC threshold profiles for NIPT and PGT; admin-managed profiles; two settings surfaces condensed (recorded 2026-07-30; proposed Minor)
- CNV caller signal files in the genome browser (recorded 2026-07-30; proposed Minor)
- Per-caller coverage tracks on a shared axis (recorded 2026-07-30; proposed Minor)
- WisecondorX and QDNAseq ingestion for the long-read package layout (recorded 2026-07-30; proposed Minor)
- HiFiCNV signal tracks (read depth, copy number, minor allele fraction) (recorded 2026-07-30; proposed Minor)
- Admin-configurable sequencing-QC acceptance limits (recorded 2026-07-30; proposed Minor)
- Long-read (nf-core/lrsvar) family-package ingestion (recorded 2026-07-29; proposed Minor)
