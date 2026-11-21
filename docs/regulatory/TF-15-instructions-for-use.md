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
> "label" is the in-app identification (version, manufacturer, in-house-IVD statement). This
> IFU is the reference manual; the in-app user guide (`/docs`) and the in-app reference docs
> (`/docs/reference/<slug>`) complement it. Content comes from TF-01, TF-02, TF-06,
> TF-10/11, TF-13.

**In short.** CoGA supports your interpretation; it never replaces it. Its sign-out is the
analytical sign-off; the clinical report is signed in the EPD (§3 item 2).

- **Always:** check the QC signals before you trust a derived call (§3 item 5); review the
  Sample QC before you sign out (§3 item 7); deal with every sign-out warning (§4).
- **Never:** use CoGA outside its validated scope (§3 item 6), use the ancestry PCA clinically
  (§3 item 10), or report a clinical problem on GitHub (§7).

---

## 1. Device identification (label)
- **Name:** CoGA — Comprehensive Genomic Analysis.
- **Version / build:** ‹X.Y.Z (git ‹hash›)›: the version and short commit of the running build, as `/api/version` reports it. The footer of every page and of every report (family and NIPT) shows it: *Software: CoGA X.Y.Z (‹hash›)*. A report fetches it each time it opens, so its footer names the build that produced the page. If the version cannot be loaded, the report footer says so and a printout starts with *Incomplete*. The build that signed a version is frozen into that version and shown in its sign-out block.
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
interpretation across seven clinical applications: monogenic NIPT screening, expanded carrier
screening (BeGECS, long-read), PGT (shallow WGS), rare-disorder diagnostics (long-read),
rare-disorder diagnostics (short-read whole-genome sequencing), combined mtDNA + nuclear
mitochondrial-disease testing (ONT long-read adaptive sampling), and conventional NIPT screening
(trisomies 21, 18 and 13 and the sex-chromosome aneuploidies, from the copy number of maternal
plasma): filtering, visualisation and interpretation of the results, and their traceability up
to the closed, frozen analysis. **For use by trained clinical laboratory professionals only**, in
an ISO 15189-accredited laboratory.

## 3. Warnings, limitations & contraindications (from TF-01 §4)
1. **Decision support only** — every result must be reviewed by a qualified professional; CoGA does not issue an autonomous diagnosis.
2. **Sign-out in CoGA is the analytical sign-off, not the medical one** — **Sign out report** is a supervisor's sign-off: it closes the analysis and freezes it into a signed version. The clinical report is drafted and medically signed in the hospital's electronic patient record (EPD), outside CoGA. It refers 1:1 to the signed version it rests on: one report, one signed version. An amended analysis is a new signed version; a report that rests on it refers to that version. CoGA does not see the EPD report and does not check it against the signed version (TF-01 §4 condition 8, [TF-02 §3](TF-02-device-description.md)).
3. **Validated upstream pipeline required** — CoGA processes outputs of separately validated or accredited wet-lab and bioinformatics workflows; it does not detect all errors in its inputs.
4. **Screening vs diagnosis** — NIPT (monogenic and conventional) and carrier-screening results are screening; at-risk findings require confirmatory diagnostic testing.
5. **Inferred genotypes** — the NIPT fetal genotype and the PGT embryo haplotype are inferred, not observed; the conventional NIPT reads the fetal copy number from the plasma's, at its fetal fraction. **Check the QC signals** (fetal fraction & CI, informative-marker count, Mendel-error rate, recombination proximity; for the conventional NIPT, the fetal-fraction estimates, the limits of detection and ccnv's warnings) before trusting a call.
6. **Validated scope only** — use CoGA only within the validated panels, assays, assemblies and populations (TF-11); use outside them is off-label. The validated reference assembly is **GRCh38**: a family on any other assembly is labelled *Not validated for clinical use* and its report cannot be signed out.
7. **Sample identity & data integrity** — **review the Sample QC** before sign-out to confirm sample identity and rule out sample swaps (checks listed in TF-01 §4 condition 7). For mitochondrial cases, also compare the samples' mtDNA haplogroups. This review is mandatory for family/trio and combined mtDNA/nuclear (mitochondrial) cases. CoGA blocks sign-out when a Sample QC check fails or cannot be verified, unless you record a reason. The Sample QC does not measure contamination: an excess of Mendelian errors, or heterozygous calls on a male's X outside the pseudo-autosomal regions, can be a sign of it, and a pass does not rule it out (TF-01 §4 condition 7).
8. **Authorised personnel only** — sign out an analysis in CoGA only if the laboratory has authorised you to. CoGA lets only a supervisor of the project, a superuser or an administrator sign out (an analyst, a viewer and a reader cannot). An administrator gives the supervisor role to the personnel the laboratory has authorised; CoGA does not check that authorisation itself. It records who signed each version in the signed record and the audit trail (TF-06 H15). The medical sign-off of the clinical report is given in the EPD, under its own signing rights.
9. **Not for** primary variant calling, somatic/oncology use, patient/home use, or non-accredited settings.
10. **Ancestry PCA is exploratory** — the *Ancestry (exploratory)* section of Sample QC shows genetic similarity to public reference populations, not ancestry or ethnicity. It is outside the intended purpose and not validated: do not use it for any clinical interpretation or report. It is never part of the sign-out or the signed record (TF-01 §4).
11. **Conventional NIPT: the claim and the population** (TF-01 §3.7):
    - The validated claim covers **trisomies 21, 18 and 13 and the sex-chromosome aneuploidies**. The other autosomes and the segmental CNVs are shown marked *outside the validated claim*, on the cNIPT page, the report page and the signed record: they are not validated results.
    - The validated population is pregnancies of more than 12 weeks, singleton or twin. Outside it: triplets or more, a vanishing twin, a maternal organ or bone-marrow transplant or a blood transfusion in the months before sampling, and a known maternal malignancy.
    - A twin pregnancy gives one fetal fraction and one result, which does not say which fetus a finding concerns.
    - The cfDNA is mostly placental: a result may reflect the placenta rather than the fetus (confined placental mosaicism), and a maternal copy-number change or mosaicism can underlie a finding.
    - A high risk requires confirmatory diagnostic testing; a low risk does not exclude an aneuploidy.
    - The rule's bounds are QA's. A result that rests on a provisional bound says so: provisional bounds are not validated.
12. **Monogenic NIPT: multiple pregnancies** (TF-01 §3.1) — the validated population is **singleton pregnancies of more than 12 weeks**; multiple pregnancies are **outside the validated scope**. A twin or higher multiple pregnancy can still be analysed: every result of a pregnancy whose package names two or more fetuses carries the note that a fetal result may be present in one fetus or in more than one, and that the analysis cannot tell which. The note says it does not apply to a monozygotic twin, whose fetuses share one genotype; CoGA is not told the zygosity. The parents' carrier status is read as in a singleton pregnancy (REQ-NIPT-018).

## 4. Instructions for safe use (per application)

**Every application, before you sign out** (*Sample-integrity QC*, `/docs/reference/sample-qc`;
*Report traceability & sign-out*, `/docs/reference/clinical-traceability`):

- Review the Sample QC (§3 item 7).
- Deal with each warning the sign-out shows. It holds the sign-out until you acknowledge it with
  a reason, which the signed record keeps: evidence drift; a failed or unverifiable Sample QC
  (for monogenic NIPT, also a failed quality check; for the conventional NIPT, a maternal plasma
  without a result or a result that rests on a provisional bound); an incomplete import; a
  package folder changed after its import; an abnormal repeat or Paraphase call without a review
  (or review it).
- A family on an assembly outside the validated set, or with an import queued or running,
  cannot be signed out at all.

**Per application:**

- Monogenic NIPT — see the in-app reference *Monogenic NIPT (cfDNA)* (`/docs/reference/monogenic-nipt`). Read the quality checks first (FF and CI, paternity, fetal sex, the maternal-plasma sample, target coverage). After a failed paternity check, do not read the variants (another father, or a sample or file mix-up). Then read each variant's inheritance probability, not only its category, and the category-8 dropout. Before acting on a de novo candidate, confirm that it is absent in the father and check its reads.
- Conventional NIPT — see the in-app reference *Conventional NIPT (cell-free DNA)* (`/docs/reference/conventional-nipt`). Read the fetal fraction and the warnings first (a low fetal fraction, ccnv's warnings, a twin pregnancy), and any bound marked provisional. Then read chromosomes 21, 18 and 13 and the sex chromosomes, the claimed targets, each with its reasons and findings; read the other autosomes and the segmental CNVs as findings outside the claim. Before acting on a finding, check it in the chromosome view (its implied fetal fraction and origin against a maternal copy-number change or mosaicism). A high risk calls for confirmatory diagnostic testing (§3 item 11).
- PGT — see *Haplotype segregation analysis* (`/docs/reference/haplotype-segregation`). Read informative markers, Mendel errors, recombination near the ROI, "uninformative" results and donor-family limits. Judge each embryo's aneuploidy and large (>10 Mb) structural variants yourself, on its segment, copy-number and APCAD tracks in the genome overview and the chromosome view: CoGA makes no automatic aneuploidy call (TF-01 §3.3).
- Carrier screening — couple-wise at-risk interpretation; reportable-variant confirmation. The *Expanded carrier screening* preset lists the genes in which both partners carry a rare (below 1%) high- or moderate-impact variant (ClinVar P/LP whatever its frequency), and a female partner's X-linked variant on its own; it leaves out ClinVar benign and likely benign variants.
- Rare-disorder — multi-data-type review (SNV/SV/repeat/Paraphase/mtDNA); ACMG classification is overridable (*Semi-automatic ACMG classification*, `/docs/reference/acmg-classification`). An inheritance filter reads a member without calls in the analysis as reference: when the small-variant list names members without calls (a proband-only long-read analysis beside its parents), a variant the de novo / dominant filter keeps may be inherited from them.
- Mitochondrial (ONT adaptive sampling) — review the complete mtDNA (heteroplasmy %, maternal transmission, haplogroup) **and** the nuclear mito-gene panel together; **review the Sample QC and compare the samples' haplogroups** before sign-out.

‹To add: step-by-step operating instructions per application, referencing the in-app user
guide. For each: required inputs, how to set up the family/pedigree, how to run and read the
analysis, and **how to interpret each QC/warning signal and what to do when it fires.**›

## 5. Interpretation of results & residual risks
Outputs are candidates and pre-evaluations with QC. TF-06 lists the residual risks the user
must know (e.g. a missed variant if a filter is too aggressive, uninformative or ambiguous
calls, drift if reference data changed). The provenance footer and drift indicators support
correct interpretation.

A signed case opens on its signed version, rendered and printed from the frozen record. That
version is the signed-out analysis, which the clinical report in the EPD refers to
(§3 item 2). It holds:

- each reported variant's description as the analyst reviewed it (gene, HGVS, consequence,
  genotypes);
- in a PGT family, the embryo calls;
- in a monogenic NIPT family, the NIPT results (of its candidates, those tagged for the report);
- in a conventional NIPT family, each maternal plasma's result per target as the cNIPT page
  showed it, each target outside the claim marked so, with the bounds it applied and its inputs.

It does not hold the population frequencies and in silico predictions the live report shows,
the segregation, or the gene and phenotype context; the page says so. The live report shows
those from current data. It is labelled as not the signed version, on screen and in print,
and says when it no longer matches the signed version.

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
- Report a problem or incident as a **CMGGMC probleemmelding** ([TF-17 §2](TF-17-vigilance-capa.md)): **Report a problem**, in the footer of every page and on the *New features* page, opens that route. Do not report a clinical problem on GitHub ([SECURITY.md](../../SECURITY.md)).
- The route is set at frontend build time: `VITE_PROBLEM_REPORT_URL`, which the Google Cloud build takes from the repository variable `COGA_PROBLEM_REPORT_URL` ([deployment-gcp.md §10](../deployment-gcp.md#10-cicd-the-normal-path)). A clinical build never links the public GitHub issue form: without the route, it shows no problem-report link. **🔲 OWNER:** give the CMGGMC route for `COGA_PROBLEM_REPORT_URL`, and name the CMGG support contact: ‹contact›.
- Reference: the in-app user guide (`/docs`), the in-app reference docs and this technical file.

## 8. Revision
The IFU is updated on any change to intended purpose, limitations, validated scope or
operating requirements (TF-18). Its version tracks the device version.
