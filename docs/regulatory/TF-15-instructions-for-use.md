# TF-15 — Instructions for Use & Labelling

| Field | Value |
| --- | --- |
| Document ID | TF-15 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead + clinical lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Basis | IVDR Annex I §20 (information supplied with the device) |

> The IFU is the controlled "information for safety." For an internal web application the
> "label" is the in-app identification (version, manufacturer, in-house-IVD statement) and
> this IFU is the reference manual, complemented by the in-app user guide (`/docs`). Content
> is drawn from TF-01, TF-02, TF-06, TF-10/11, TF-13.

---

## 1. Device identification (label)
- **Name:** CoGA — Comprehensive Genomic Analysis.
- **Version / build:** displayed in-app and in every report footer ‹X.Y.Z (git ‹hash›)›.
- **Manufacturer:** Center for Medical Genetics Ghent (CMGG), UZ Gent — **in-house IVD per IVDR Article 5(5); not CE-marked; for internal CMGG use only.**
- **Symbol/equivalent:** "IVD", "in-house device", manufacturer identity. **🔲 confirm labelling presentation in-app.**

## 2. Intended purpose & users
Full statement in [TF-01](TF-01-intended-purpose.md). Decision-support software for genomic
interpretation across monogenic NIPT screening, expanded carrier screening (BeGECS,
long-read), PGT (shallow WGS), rare-disorder diagnostics (long-read), and combined mtDNA +
nuclear mitochondrial-disease testing (ONT long-read adaptive sampling). **For use by
trained clinical laboratory professionals only**, in an ISO 15189-accredited laboratory.

## 3. Warnings, limitations & contraindications (from TF-01 §4)
1. **Decision support only** — every result must be reviewed and signed out by a qualified professional; CoGA does not issue an autonomous diagnosis.
2. **Validated upstream pipeline required** — CoGA processes outputs of separately validated/accredited wet-lab and bioinformatics workflows; it does not detect all errors in its inputs.
3. **Screening vs diagnosis** — NIPT and carrier-screening results are screening; at-risk findings require confirmatory diagnostic testing.
4. **Inferred genotypes** — NIPT fetal genotype and PGT embryo haplotype are inferred, not observed; **check the QC signals** (fetal fraction & CI, informative-marker count, Mendel-error rate, recombination proximity) before trusting a call.
5. **Validated scope only** — use only within the validated panels/assays/assemblies/populations (TF-11); use outside is off-label. The validated reference assembly is **GRCh38**: a family on any other assembly is labelled *Not validated for clinical use* and its report cannot be signed out.
6. **Sample identity & data integrity** — **review the Sample QC** (relatedness, sex, Mendelian consistency, maternal-lineage) to confirm sample identity and rule out sample swaps/contamination before sign-out — mandatory for family/trio and combined mtDNA/nuclear (mitochondrial) cases.
7. **Not for** primary variant calling, somatic/oncology use, patient/home use, or non-accredited settings.

## 4. Instructions for safe use (per application)
‹Step-by-step operating instructions per application, referencing the in-app user guide.
For each: required inputs, how to set up the family/pedigree, how to run and read the
analysis, and **how to interpret each QC/warning signal and what to do when it fires.**›
- Monogenic NIPT — see [monogenic-nipt.md](../monogenic-nipt.md); read FF, CI, category-8 dropout, external-FF disagreement.
- PGT — see [haplotype-segregation-analysis.md](../haplotype-segregation-analysis.md); read informative markers, Mendel errors, recombination near ROI, "uninformative" results, donor-family limits.
- Carrier screening — couple-wise at-risk interpretation; reportable-variant confirmation.
- Rare-disorder — multi-data-type review (SNV/SV/repeat/Paraphase/mtDNA); ACMG classification is overridable.
- Mitochondrial (ONT adaptive sampling) — review the complete mtDNA (heteroplasmy %, maternal transmission, haplogroup) **and** the nuclear mito-gene panel together; **review the Sample QC for maternal-lineage/sample-swap integrity** before sign-out.

## 5. Interpretation of results & residual risks
Outputs are candidates/pre-evaluations with QC. Residual risks the user must be aware of are
listed per TF-06 (e.g. possibility of a missed variant if a filter is too aggressive,
uninformative/ambiguous calls, drift if reference data changed). The provenance footer and
drift indicators support correct interpretation.

## 6. Minimum IT & security requirements (IVDR §16.4)
From [TF-13 §7](TF-13-cybersecurity.md): operate only within the UZ Gent/CMGG managed
environment with TLS, encrypted datastores, managed secrets, network isolation, institutional
identity, and operational logging. Supported browser(s): ‹specify›.

## 7. Manufacturer & support
- CMGG contact for support and **to report a problem/incident**: ‹contact›.
- Reference to the in-app user guide (`/docs`) and to this technical file.

## 8. Revision
The IFU is updated on any change affecting intended purpose, limitations, validated scope,
or operating requirements (TF-18), and its version tracks the device version.
