# CoGA Technical File — Consolidated Inputs Questionnaire

| Field | Value |
| --- | --- |
| Purpose | Collect every open input (the **🔲 INPUT NEEDED** items) across the technical file in one place, for the CMGG clinical and RA/quality teams to complete. |
| Version | v0.1 DRAFT |
| Owner | ‹CMGG software lead› |
| Date | 2026-06-25 |

> Group A–F below feed specific documents (cited). Answers will be folded into the controlled
> documents. "—" = not yet answered.
>
> **Several items are now answered by the governing SOP `H11.1-OP5`** (and folded into the TF
> docs): the device-identifier scheme (CMGGMC software number `Sxxxx` + semantic versioning),
> the role structure (bio-IT SPOC, IT coördinator, projectverantwoordelijke, business
> contactpersoon, kwaliteitscel, DPO), the change-control model (patch/minor/major), and the
> validation split (bio-IT vs clinical, templates H11.1-F12.2 / F11 / F2 / F13). Remaining
> items are mostly **names/dates** and **per-application clinical comparators/thresholds**.

---

## A. Regulatory & institutional identity → TF-04, TF-05, README
| # | Question | Answer |
| --- | --- | --- |
| A1 | Exact legal manufacturer name & address (CMGG / UZ Gent) for the declaration | — |
| A2 | BELAC ISO 15189 accreditation number and the relevant scope reference | — |
| A3 | Named responsible persons: device owner, quality/RA lead, lab director, (Art. 15 PRRC-equivalent if used) | — |
| A4 | Confirm device scope: **one device, five applications** (recommended) vs separate files | — |
| A5 | Confirm IVDR Annex VIII risk class to state for transparency (expected Class C) | — |
| A6 | Confirm Belgian in-house transitional dates & any national provisions with FAMHP | — |
| A7 | Where will the public Art. 5(5)(f) declaration be published? | — |

## B. Intended purpose specifics → TF-01
| # | Question | Answer |
| --- | --- | --- |
| B1 | Monogenic NIPT: gestational-age window & indication criteria | — |
| B2 | Carrier screening (BeGECS): reproductive vs general indication; gene-list/panel scope & its version source | — |
| B3 | PGT: which sub-scopes are claimed — PGT-M / PGT-A / PGT-SR? | — |
| B4 | PGT: the exact claims for aneuploidy resolution and structural-variant size (≥10 Mb) | — |
| B5 | Rare-disorder: proband-only and/or trio/family modes in scope? | — |
| B6 | Supported reference assemblies (e.g. GRCh38 only?) | — |
| B7 | Mitochondrial (ONT adaptive sampling): nuclear mito-gene panel & version source; proband-only vs trio/maternal modes; single- vs multi-tissue heteroplasmy scope | — |

## C. Performance evaluation → TF-10, TF-11
| # | Question | Answer |
| --- | --- | --- |
| C1 | Carrier (50 couples): exact validated comparator method + "truth" source; acceptance thresholds | — |
| C2 | PGT (100 embryos): comparator (SNP-array / current PGT method) + confirmatory source; acceptance thresholds; aneuploidy & SV detection-limit claims | — |
| C3 | WGS trios (30): comparator/established-diagnosis source; per-data-type acceptance | — |
| C4 | Monogenic NIPT (30): comparator/known-fetal-genotype source; FF agreement tolerance; concordance threshold | — |
| C4b | Mitochondrial (ONT adaptive sampling): **validation N**; comparator method(s) for mtDNA + nuclear; heteroplasmy-agreement tolerance; tissue scope | — |
| C5 | Per application: prospective vs retrospective; consecutive vs enriched-for-positives case selection | — |
| C6 | Will an analytical baseline run against a reference material (GIAB/GeT-RM) be included? | — |
| C7 | PMPF: ongoing concordance sampling frequency & sample size per application | — |

## D. Software lifecycle, V&V, configuration → TF-07, TF-08, TF-09, TF-18
| # | Question | Answer |
| --- | --- | --- |
| D1 | Confirm IEC 62304 safety class (C) and any justified lower-class decomposition | — |
| D2 | Named role holders incl. **independent reviewer** for Class C | — |
| D3 | Device version / UDI-equivalent scheme & where reference-data versions attach | ✅ Answered by H11.1-OP5: CMGGMC software number **`Sxxxx`** + semantic `x.y.z`; reference-data versions attach via the per-case manifest. Still needed: the **assigned `Sxxxx`**. |
| D4 | Approval to **pin all backend runtime dependencies** to exact versions | — |
| D5 | Production Postgres & ClickHouse versions; pin container base-image digests | — |
| D6 | Confirm SRS will be produced as a controlled document / requirements register | — |
| D7 | Enforce CI gates as **required status checks** on `main`? (owner + date) | ✅ **Done** — ten required checks, strict enforcement. Still open: enable `enforce_admins`, and require an approving review (4-eye). |

## E. Security, usability & data protection → TF-12, TF-13, TF-14
| # | Question | Answer |
| --- | --- | --- |
| E1 | Production deployment topology (managed PG/CH, TLS, secrets manager, network isolation, S3/CloudTrail) — owner & target date for S-1…S-8 | — |
| E2 | Vulnerability-disclosure handling with UZ Gent IT security | — |
| E3 | Summative usability evaluation: participant count per user group, schedule, facilitator | — |
| E4 | DPO consultation date; lawful basis & Art. 9 condition confirmation | — |
| E5 | Pseudonymization extent within CoGA; retention period; erasure-vs-record-keeping policy | — |
| E6 | Supported browser(s) for the IFU minimum-requirements section | — |

## F. Post-market & vigilance → TF-16, TF-17
| # | Question | Answer |
| --- | --- | --- |
| F1 | PMS review cadence (e.g. annual) and PMS-report owner | — |
| F2 | PMS quantitative indicators & action thresholds | — |
| F3 | FAMHP in-house-device serious-incident reportability criteria, timelines & channel | — |
| F4 | User intake/support & incident-reporting contact for the IFU | — |
