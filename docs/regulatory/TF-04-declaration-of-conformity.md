# TF-04 — In-House Declaration of Conformity (IVDR Article 5(5)(f))

| Field | Value |
| --- | --- |
| Document ID | TF-04 |
| Version | v0.1 DRAFT |
| Status | Draft template — **not to be signed until GSPR open items (TF-03) are closed** |
| Owner | ‹CMGG RA / quality› |
| Approver | ‹Head of Center for Medical Genetics / legal representative UZ Gent› |
| Date | 2026-06-25 |

> This is the **public declaration** required by IVDR Article 5(5)(f). When approved it
> must be made publicly available (e.g. on the CMGG website alongside the
> [accreditation page](https://www.cmgg.be/nl/over-ons/accreditatie)) and provided to the
> competent authority (Belgium: **FAMHP / FAGG-AFMPS**) on request. Confirm the exact
> required content and publication channel with CMGG RA; Member States may impose
> additional national requirements.

---

## Declaration

The health institution identified below declares, under IVDR (EU) 2017/746 Article 5(5),
that it manufactures and uses the in-house in-vitro diagnostic device described herein,
that the device is **not transferred to another legal entity**, and that the device meets
the relevant general safety and performance requirements of Annex I.

### 1. Health institution (manufacturer)

| Field | Value |
| --- | --- |
| Name | Center for Medical Genetics Ghent (CMGG) |
| Legal entity | Ghent University Hospital (UZ Gent) |
| Address | Corneel Heymanslaan 10, 9000 Gent, Belgium *(as written in the BELAC scope annex)* |
| Accreditation | EN ISO 15189:2022 — BELAC accreditation **351-MED**, **certificate version 9**, validity **2026-09-11 → 2031-09-10**; scope annex **BELAC 351-MED V. 21**, with the same validity. Source: [351-MED certificate + scope annex (BELAC)](https://ng3.economie.fgov.be/NI/belac/medilabs/scope_pdf/351-MED.pdf), retrieved 2026-09-30. BELAC replaces the file at that address with each new version, so a dated copy of the cited version belongs in the CMGG QMS. **🔲 QA:** file a dated copy of certificate version 9 and scope annex V. 21 (document control, [TF-07 §3](TF-07-software-lifecycle-plan.md)). |
| Responsible contact | Björn Menten — Lab director / project lead, <bjorn.menten@ugent.be> |

> **🔲 RA DECISION REQUIRED — the declaring institution and the accreditation holder are not
> the same legal person.** Verified against the BELAC source on 2026-09-30: certificate 351-MED
> version 9, like version 8 before it, is issued to **Universiteit Gent**,
> Sint-Pietersnieuwstraat 25, 9000 Gent, enterprise number **0248.015.142**. *Universitair
> ziekenhuis Gent — Centrum Medische Genetica Gent (CMGG)*, Corneel Heymanslaan 10, appears in
> scope annex V. 21 as an **activity site**, not as the certificate holder.
>
> IVDR Article 5(5)(c) requires the **health institution** making this declaration to comply
> with EN ISO 15189 (or applicable national provisions). CMGG RA must therefore confirm **which
> legal person declares** — and that the accreditation relied on covers it. This is not a
> wording preference; it determines whether the declaration is valid.

### 2. Device identification

| Field | Value |
| --- | --- |
| Device name | CoGA — Comprehensive Genomic Analysis |
| Device type | Software (standalone in-house IVD / MDSW) |
| Version covered by this declaration | ‹X.Y.Z (git ‹hash›)› |
| Risk class (informative) | **Class C** per IVDR Annex VIII. Declaration validity is not contingent on a class for in-house devices; stated for transparency. Separately, the software safety class under IEC 62304 is also C ([TF-07 §1](TF-07-software-lifecycle-plan.md)). |
| Intended purpose | Decision-support software for filtering, visualization and interpretation of genomic data from validated NGS workflows, across five clinical applications: monogenic NIPT screening, expanded carrier screening (long-read), preimplantation genetic testing (PGT), rare-disorder diagnostics (long-read), and combined mtDNA + nuclear mitochondrial-disease testing (ONT long-read adaptive sampling). Full statement: [TF-01](TF-01-intended-purpose.md). |
| Intended users / setting | Trained clinical laboratory professionals within CMGG, ISO 15189-accredited laboratory. |

### 3. Conformity statement

CMGG declares that:

1. The device is manufactured and used **exclusively within the health institution** and is not transferred to another legal entity (Art. 5(5)(a)).
2. Manufacture and use occur under an **appropriate quality management system** integrated with the CMGG ISO 15189 QMS (Art. 5(5)(b)).
3. The laboratory is **compliant with EN ISO 15189** and accredited accordingly (Art. 5(5)(c)).
4. The device **meets the general safety and performance requirements of Annex I** as documented in [TF-03 GSPR Checklist](TF-03-gspr-checklist.md), **except** for the requirements listed in §4 below, for which a reasoned justification is provided (Art. 5(5)(f)(iii)).
5. Documentation of the design, manufacturing process and performance data sufficient to allow the competent authority to assess Annex I conformity is held and available on request (Art. 5(5)(e), (g)).
6. Experience from clinical use is reviewed and corrective actions taken (Art. 5(5)(i)) per [TF-16 PMS Plan](TF-16-post-market-surveillance-plan.md).

### 4. Requirements not fully met (with justification)

‹List per [TF-03 §"Requirements not yet fully met"](TF-03-gspr-checklist.md). To be **empty
or fully justified** at signature. Do not sign while substantive performance or
information-security GSPRs are open.›

### 5. Competent authority

Information on the manufacturing, modification and use of this device, including the
justification thereof, will be provided to the Belgian competent authority (**FAMHP /
FAGG-AFMPS**) upon request (Art. 5(5)(e)).

### 6. Signatures

**🔲 OWNER:** who signs as Head of Center for Medical Genetics ([INPUTS A3](INPUTS-QUESTIONNAIRE.md)).

| Role | Name | Signature | Date |
| --- | --- | --- | --- |
| Head of Center for Medical Genetics | ‹…› | | |
| Quality / RA responsible | ‹…› | | |
| ‹Legal representative UZ Gent, if required› | ‹…› | | |

---

> **Equivalence justification (Art. 5(5)(d)).** A separate justification that no equivalent
> CE-marked device meets the target patient group's needs is maintained in
> [TF-05](TF-05-equivalence-justification.md). For when this condition applies, see
> [README §1](README.md).
