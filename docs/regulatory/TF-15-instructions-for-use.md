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
> this IFU is the reference manual, complemented by the in-app user guide (`/docs`) and the
> in-app reference docs (`/docs/reference/<slug>`). Content is drawn from TF-01, TF-02, TF-06,
> TF-10/11, TF-13.

---

## 1. Device identification (label)
- **Name:** CoGA — Comprehensive Genomic Analysis.
- **Version / build:** ‹X.Y.Z (git ‹hash›)›, the version and short commit of the running build as `/api/version` reports it. The footer of every page shows it, and so does the footer of every report, family and NIPT alike: *Software: CoGA X.Y.Z (‹hash›)*. A report asks for it each time it opens, so its footer names the build that produced the page. If the version cannot be loaded, the report footer says so and a printout starts with *Incomplete*. The build that signed a version is frozen into that version and shown in its sign-out block.
- **Manufacturer:** Center for Medical Genetics Ghent (CMGG), Ghent University Hospital, C. Heymanslaan 10, 9000 Ghent — **in-house IVD per IVDR Article 5(5); not CE-marked; for internal CMGG use only.**
- **In-app label:** the footer of every page, the sign-in page included, reads:

  > CoGA, Comprehensive Genomic Analysis · Version X.Y.Z (‹hash›)
  >
  > In-house IVD per IVDR Article 5(5) · Not CE-marked · For internal CMGG use only
  >
  > Manufacturer: Center for Medical Genetics, Ghent University Hospital, C. Heymanslaan 10, 9000 Ghent

  Every report footer repeats the last two lines after its software line. The words *In-house IVD* stand in for the IVD symbol. The wording is set in `frontend/src/lib/deviceLabel.ts`; a change to it is a change to the label ([TF-18](TF-18-change-configuration-management.md)).

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
6. **Sample identity & data integrity** — **review the Sample QC** before sign-out to confirm sample identity and rule out sample swaps or contamination (the checks are listed in TF-01 §4 condition 7); for mitochondrial cases, also compare the samples' mtDNA haplogroups. This is mandatory for family/trio and combined mtDNA/nuclear (mitochondrial) cases. CoGA blocks sign-out when a Sample QC check fails or cannot be verified, unless you record a reason.
7. **Authorised signatories only** — only personnel the laboratory has authorised as signatories may sign out a report. CoGA lets any member of the project sign out and does not check signing authority itself; it records who signed each version in the signed record and the audit trail (TF-06 H15).
8. **Not for** primary variant calling, somatic/oncology use, patient/home use, or non-accredited settings.

## 4. Instructions for safe use (per application)
‹Step-by-step operating instructions per application, referencing the in-app user guide.
For each: required inputs, how to set up the family/pedigree, how to run and read the
analysis, and **how to interpret each QC/warning signal and what to do when it fires.**›
- Monogenic NIPT — see the in-app reference *Monogenic NIPT (cfDNA)* (`/docs/reference/monogenic-nipt`); read FF, CI and category-8 dropout.
- PGT — see *Haplotype segregation analysis* (`/docs/reference/haplotype-segregation`); read informative markers, Mendel errors, recombination near ROI, "uninformative" results, donor-family limits.
- Carrier screening — couple-wise at-risk interpretation; reportable-variant confirmation.
- Rare-disorder — multi-data-type review (SNV/SV/repeat/Paraphase/mtDNA); ACMG classification is overridable (*Semi-automatic ACMG classification*, `/docs/reference/acmg-classification`).
- Mitochondrial (ONT adaptive sampling) — review the complete mtDNA (heteroplasmy %, maternal transmission, haplogroup) **and** the nuclear mito-gene panel together; **review the Sample QC and compare the samples' haplogroups** before sign-out.
- All applications — Sample QC: *Sample-integrity QC* (`/docs/reference/sample-qc`); sign-out: *Report traceability & sign-out* (`/docs/reference/clinical-traceability`).

## 5. Interpretation of results & residual risks
Outputs are candidates/pre-evaluations with QC. Residual risks the user must be aware of are
listed per TF-06 (e.g. possibility of a missed variant if a filter is too aggressive,
uninformative/ambiguous calls, drift if reference data changed). The provenance footer and
drift indicators support correct interpretation. The report page shows live data: it says when
it no longer matches the signed version, and the signed version itself can be downloaded.

## 6. Minimum IT & security requirements (IVDR Annex I §16.4)
Operate CoGA only in the UZ Gent/CMGG Google Cloud project built from `terraform/`
([TF-02 §10](TF-02-device-description.md)), with the go-live switches of
[TF-13 §3](TF-13-cybersecurity.md) on. That gives: TLS at the load balancer and to the
datastores; encrypted datastores; secrets in a secrets manager; datastores on a private
network with no public address; access only from institutional networks, with institutional
identity; and operational logging. **Supported browser:** 🔲 to confirm (INPUTS E6) — proposed:
a current Chrome or Edge, the Chromium browsers the browser tests use
([TF-09d](TF-09d-browser-e2e-verification.md)).

## 7. Manufacturer & support
- Report a problem or incident as a **CMGGMC probleemmelding** ([TF-17 §2](TF-17-vigilance-capa.md)). **Report a problem**, in the footer of every page and on the *New features* page, opens that route. The route is set when the frontend is built: `VITE_PROBLEM_REPORT_URL`, which the Google Cloud build takes from the repository variable `COGA_PROBLEM_REPORT_URL` ([deployment-gcp.md §10](../deployment-gcp.md#10-cicd-the-normal-path)). A clinical build never links the public GitHub issue form: without the route, it shows no problem-report link. Do not report a clinical problem on GitHub ([SECURITY.md](../../SECURITY.md)). **🔲 OWNER:** give the CMGGMC route for `COGA_PROBLEM_REPORT_URL`, and name the CMGG support contact: ‹contact›.
- Reference: the in-app user guide (`/docs`), the in-app reference docs and this technical file.

## 8. Revision
The IFU is updated on any change affecting intended purpose, limitations, validated scope,
or operating requirements (TF-18), and its version tracks the device version.
