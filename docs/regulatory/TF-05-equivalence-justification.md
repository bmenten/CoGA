# TF-05 — Equivalence & Clinical-Need Justification (IVDR Article 5(5)(d))

| Field | Value |
| --- | --- |
| Document ID | TF-05 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹Clinical lead + CMGG RA› |
| Approver | ‹Head of Center for Medical Genetics› |
| Date | 2026-06-25 |

> Article 5(5)(d) requires the institution to justify in its documentation that the
> specific needs of the target patient group **cannot be met, or cannot be met at the
> appropriate level of performance, by an equivalent CE-marked device** available on the
> market. For when this condition applies, see [README §1](README.md); CMGG prepares it now,
> to stay close to CE-IVDR readiness.
>
> **🔲 INPUT NEEDED:** the market survey below gives the *structure* and CMGG's position;
> the clinical and RA team must supply or confirm the named-product evidence and dates
> before approval.

---

## 1. Target patient groups and their specific needs

| Application | Target group | Specific need not served off-the-shelf |
| --- | --- | --- |
| Monogenic NIPT | Pregnancies screened for single-gene disorders from maternal cfDNA + paternal sample | Fetal-fraction-aware 8-category zygosity inference from a *two-sample* cfDNA/paternal VCF with the inheritance presets CMGG's protocol requires; tightly coupled to CMGG's validated cfDNA assay output. |
| Expanded carrier screening (BeGECS) | Couples/individuals in the **Belgian Expanded Genetic Carrier Screening** programme, on **long-read** sequencing | Carrier interpretation over the BeGECS gene set on long-read data, with couple-wise at-risk pairing, integrated with CMGG's validated long-read workflow. |
| PGT (shallow WGS) | Couples / single-parent-plus-donor families in PGT | Imputation-based **haplotype segregation** + **direct mutation detection** + **simultaneous aneuploidy** + **large (>10 Mb) SV** read from shallow WGS, with embryo-anchored and donor pedigrees — an integrated combination no single CE-IVD product provides. |
| Rare-disorder diagnostics | Patients with suspected rare Mendelian disease, **comprehensive long-read** sequencing | Unified review of SNV/indel, SV, repeat expansions (TRGT), Paraphase, and mtDNA from one long-read genome, with HPO/pedigree-aware prioritization and semi-automatic ACMG/AMP classification. |
| Mitochondrial-disease testing (ONT adaptive sampling) | Patients with suspected mitochondrial disease (± mother/family) | **Joint interpretation of the complete mtDNA *and* the nuclear mito-gene panel** produced in one ONT long-read **adaptive-sampling** run — mtDNA heteroplasmy + maternal-transmission analysis alongside nuclear small-variant/SV review, in a single workspace with integrated Sample-QC sample-swap checks. No CE-IVD product interprets both genomes from this combined adaptive-sampling assay in CMGG's configuration. |
| Conventional NIPT | Pregnancies (> 12 weeks, singleton or twin) screened for trisomies 21, 18 and 13 and sex-chromosome aneuploidies from maternal plasma | Decision support over the output of CMGG's own shallow-WGS NIPT pipeline (ccnv): a screening result per target by a fixed rule of z-score and fetal-fraction concordance, with QA's bounds, in the same family workspace and sign-out as the monogenic NIPT of the same pregnancy. The rule is not a novel analyte method: the copy number, z-scores and fetal fraction are ccnv's. The need is the coupling to that pipeline's output, not the screening itself (§2). |

## 2. Market survey of candidate CE-marked devices

> Structure of the assessment. Fill the named-product cells from the CMGG market survey.

| Candidate category | Example CE-marked products | Covers which application(s)? | Why not equivalent / sufficient |
| --- | --- | --- | --- |
| Variant-interpretation / tertiary-analysis platforms | ‹e.g. product A, B› | Partial: rare-disorder SNV interpretation | Do not integrate CMGG's PGT haplotyping, monogenic-NIPT zygosity model, long-read repeat/Paraphase/mtDNA, or the BeGECS workflow; not validated on CMGG's specific upstream pipelines and assemblies. |
| PGT-dedicated software | ‹…› | Partial: PGT-A or PGT-M | Typically array/SNP- or targeted-based; do not combine shallow-WGS imputation haplotyping **with** simultaneous aneuploidy **and** >10 Mb SV in CMGG's configuration. |
| NIPT software | ‹…› | Aneuploidy NIPT: the conventional NIPT's claims; not the monogenic NIPT | Aneuploidy-focused; do not perform single-gene fetal-fraction-aware zygosity inference from a two-sample cfDNA/paternal VCF. For aneuploidy screening, CE-marked NIPT products exist with the conventional NIPT's claims (trisomies 21, 18 and 13; sex-chromosome aneuploidies), each with the analysis software of its own assay. That software reads its own assay's data, not the output of CMGG's ccnv pipeline, so it cannot serve as the decision support over that pipeline. 🔲 RA: whether the Article 5(5)(d) justification for the conventional NIPT rests on the upstream assay (the in-house wet-lab and ccnv workflow, against the CE-marked NIPT products) rather than on CoGA's rule over its output. |
| Carrier-screening pipelines | ‹…› | Partial | Not aligned to the BeGECS panel on long-read, nor to CMGG's couple-wise reporting. |
| Mitochondrial-disease panels / mtDNA tools | ‹…› | Partial: mtDNA *or* nuclear, separately | Typically analyse mtDNA and the nuclear mito-genes as separate assays/tools; do not interpret both from one ONT adaptive-sampling run with integrated heteroplasmy analysis and Sample QC in CMGG's configuration. |

## 3. Equivalence conclusion

No single CE-marked IVD device, nor a practical combination of them, meets the integrated,
locally-validated needs of the CMGG applications of §1 **at the appropriate level of
performance**, because:

- (a) each application is tightly coupled to CMGG's separately validated and accredited
  upstream workflows and their specific outputs (APCAD/segment tracks, imputed/phased markers,
  two-sample cfDNA VCFs, the conventional NIPT's copy-number output);
- (b) several analyses (monogenic-NIPT zygosity inference, shallow-WGS PGT haplotyping with
  simultaneous aneuploidy + large SV) are methodologically bespoke and not offered as
  validated CE-IVD products. The conventional NIPT's rule is not among them: it applies a fixed
  rule to the values the upstream pipeline computes, and its need rests on (a); and
- (c) the device's clinical performance will be established **against CMGG's own validated
  reference assays** ([TF-10 Performance Evaluation Plan](TF-10-performance-evaluation-plan.md)),
  a baseline a generic third-party product cannot reproduce.

CMGG therefore manufactures and uses CoGA under the Article 5(5) in-house exemption, and
voluntarily aligns to CE-IVDR documentation and performance standards.

## 4. Review

This justification is reviewed whenever the market materially changes, at least at each
PMS review cycle ([TF-16](TF-16-post-market-surveillance-plan.md)) and before the date this
condition applies (README §1).
