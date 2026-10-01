# CoGA Technical File — Consolidated Inputs Questionnaire

| Field | Value |
| --- | --- |
| Purpose | Collect every open input (the **🔲 INPUT NEEDED** items) across the technical file in one place, for the CMGG clinical and RA/quality teams to complete. |
| Version | v0.1 DRAFT |
| Owner | ‹CMGG software lead› |
| Date | 2026-06-25 |

> Groups A–F below feed specific documents (cited). Answers are folded into the controlled
> documents. ✅ = answered · ◐ = partly answered · "—" = not yet answered.
>
> The governing SOP `H11.1-OP5` already answers the identifier scheme, the roles, the change
> control and the validation split; those answers are in the TF documents
> ([README §1a](README.md)). What remains is mostly **names and dates** and the
> **per-application clinical comparators and thresholds**.

---

## A. Regulatory & institutional identity → TF-04, TF-05, README
| # | Question | Answer |
| --- | --- | --- |
| A1 | Exact legal manufacturer name & address (CMGG / UZ Gent) for the declaration | ✅ **Center for Medical Genetics, Ghent University Hospital, C. Heymanslaan 10, 9000 Ghent**. 🔲 RA: the accreditation certificate is issued to Universiteit Gent, so confirm which legal person declares ([TF-04 §1](TF-04-declaration-of-conformity.md)). |
| A2 | BELAC ISO 15189 accreditation number and the relevant scope reference | ✅ **351-MED** (ISO 15189 — Medical laboratories). Scope: [351-MED scope PDF](https://ng3.economie.fgov.be/NI/belac/medilabs/scope_pdf/351-MED.pdf). ✅ TF-04 §1 cites the renewed certificate, version 9 (2026-09-11 → 2031-09-10), and scope annex V. 21 (owner, 2026-09-30). 🔲 QA: file a dated copy of both in the CMGG QMS. |
| A3 | Named responsible persons: device owner, quality/RA lead, lab director, (Art. 15 PRRC-equivalent if used) | ✅ Named in the CMGG QMS (KHB, organigram), not in this public repository: the developer and project lead, who is also the lab director; the IT-coördinator, who is the independent reviewer; the head of department; the QC-verantwoordelijke (kwaliteitsbeheerder) (confirmed by the owner, 2026-09-30). See [TF-07 §3](TF-07-software-lifecycle-plan.md) — note the recorded developer/lab-director concentration. 🔲 OWNER: TF-07 §3 calls the lab director "Lab director / Head of CMGG"; confirm the titles and who signs [TF-04 §6](TF-04-declaration-of-conformity.md) as "Head of Center for Medical Genetics". |
| A4 | Confirm device scope: **one device, five applications** (recommended) vs separate files | ✅ **One device, five applications** (may be extended in future). PGT-mitoDNA is a **sub-scope of the PGT application**, not a sixth application. |
| A5 | Confirm IVDR Annex VIII risk class to state for transparency (expected Class C) | ✅ **Class C** |
| A6 | Confirm Belgian in-house transitional dates & any national provisions with FAMHP | 🔲 **Open** — to confirm with FAMHP / RA. |
| A7 | Where will the public Art. 5(5)(f) declaration be published? | 🔲 **Open** — publication location not yet decided. |

## B. Intended purpose specifics → TF-01
| # | Question | Answer |
| --- | --- | --- |
| B1 | Monogenic NIPT: gestational-age window & indication criteria | ✅ Gestational age **> 12 weeks**; **singleton pregnancies** only. |
| B2 | Carrier screening (BeGECS): reproductive vs general indication; gene-list/panel scope & its version source | ✅ **Reproductive** indication. Gene list is a controlled document in the **CMGG kwaliteitshandboek (KHB)**; cite its document ID + version in TF-01/TF-10 when the study is run. |
| B3 | PGT: which sub-scopes are claimed — PGT-M / PGT-A / PGT-SR? | ✅ **Comprehensive PGT: PGT-M + PGT-SR + PGT-AS (aneuploidy screening) + PGT-mitoDNA**, the last as a sub-scope of PGT. |
| B4 | PGT: the exact claims for aneuploidy resolution and structural-variant size (≥10 Mb) | ✅ Aneuploidy: **mosaicism > 35%**. SV detection: **> 10 Mb** (retained). |
| B5 | Rare-disorder: proband-only and/or trio/family modes in scope? | ✅ **Both** — proband-only and family (trio) analysis. |
| B6 | Supported reference assemblies (e.g. GRCh38 only?) | ✅ **GRCh38 only** for the validated scope. T2T is a future extension and is **out of scope** until separately validated. |
| B7 | Mitochondrial (ONT adaptive sampling): nuclear mito-gene panel & version source; proband-only vs trio/maternal modes; single- vs multi-tissue heteroplasmy scope | ✅ Panel is a controlled document in the **CMGG KHB** (cite ID + version). **Proband-only**, with **maternal/trio** analysis also possible; **multiple tissues** may be analysed. Governed by the CMGG-specific validation dossier. |

## C. Performance evaluation → TF-10, TF-11
| # | Question | Answer |
| --- | --- | --- |
| C1 | Carrier (50 couples): exact validated comparator method + "truth" source; acceptance thresholds | ✅ Comparator = **current gold standard: WES + FraX + SMA + DMD deletions (MLPA) + CYP21A2**. Acceptance: **sensitivity and specificity at least equal to the current method**. 🔲 The current method's numeric figures are documented in the **CMGG KHB** — cite the document ID, version and the figures in TF-10 so the criterion is falsifiable before the study starts. |
| C2 | PGT (100 embryos): comparator (SNP-array / current PGT method) + confirmatory source; acceptance thresholds; aneuploidy & SV detection-limit claims | ✅ Comparator = **current GENType / HOPLA method**. Detection claims per B4 (mosaicism > 35%; SV > 10 Mb). 🔲 Acceptance thresholds still to be stated numerically. |
| C3 | WGS trios (30): comparator/established-diagnosis source; per-data-type acceptance | — |
| C4 | Monogenic NIPT (30): comparator/known-fetal-genotype source; FF agreement tolerance; concordance threshold | — |
| C4b | Mitochondrial (ONT adaptive sampling): **validation N**; comparator method(s) for mtDNA + nuclear; heteroplasmy-agreement tolerance; tissue scope | — |
| C5 | Per application: prospective vs retrospective; consecutive vs enriched-for-positives case selection | — |
| C6 | Will an analytical baseline run against a reference material (GIAB/GeT-RM) be included? | ✅ **Yes** — GIAB will be included for analysis and comparison. |
| C7 | PMPF: ongoing concordance sampling frequency & sample size per application | — |

## D. Software lifecycle, V&V, configuration → TF-07, TF-08, TF-09, TF-18
| # | Question | Answer |
| --- | --- | --- |
| D1 | Confirm IEC 62304 safety class (C) and any justified lower-class decomposition | ✅ **Class C**; no lower-class decomposition claimed. |
| D2 | Named role holders incl. **independent reviewer** for Class C | ✅ Holders are from the **CMGG bio-IT group** (authoritative register: KHB + organigram); the roles for CoGA in [TF-07 §3](TF-07-software-lifecycle-plan.md). **Independent reviewer: the IT-coördinator.** |
| D3 | Device version / UDI-equivalent scheme & where reference-data versions attach | ✅ Answered by H11.1-OP5: CMGGMC software number **`Sxxxx`** + semantic `x.y.z`; reference-data versions attach via the per-case manifest. Still needed: the **assigned `Sxxxx`**. |
| D4 | Approval to **pin all backend runtime dependencies** to exact versions | ✅ **Approved and implemented.** `backend/requirements.txt` pins every package to an exact, hash-verified version and is installed with `--require-hashes` ([TF-08 §A.1](TF-08-soup-register.md)). |
| D5 | Production Postgres & ClickHouse versions; pin container base-image digests | ✅ PostgreSQL 16 (Cloud SQL `POSTGRES_16` on Google Cloud) and ClickHouse 26.8 LTS; every container image is pinned by digest ([TF-08 §A.3](TF-08-soup-register.md)). |
| D6 | Confirm SRS will be produced as a controlled document / requirements register | ✅ Yes — [TF-09a](TF-09a-software-requirements-specification.md) is the SRS, revised under change control (TF-18). |
| D7 | Enforce CI gates as **required status checks** on `main`? (owner + date) | ✅ **Done**, with strict enforcement. Still open: `enforce_admins` and a required approving review (4-eye). See [TF-18 §6](TF-18-change-configuration-management.md). |

## E. Security, usability & data protection → TF-12, TF-13, TF-14
| # | Question | Answer |
| --- | --- | --- |
| E1 | Production deployment topology (managed PG/CH, TLS, secrets manager, network isolation, download audit) — owner & target date for S-1…S-8 | ◐ Decided: Google Cloud, codified in `terraform/` and not yet applied ([TF-02 §10](TF-02-device-description.md)); open items and go-live switches in [TF-13 §3](TF-13-cybersecurity.md). Open: owner and target date for the first apply and for each go-live switch. |
| E2 | Vulnerability-disclosure handling with UZ Gent IT security | — |
| E3 | Summative usability evaluation: participant count per user group, schedule, facilitator | — |
| E4 | DPO consultation date; lawful basis & Art. 9 condition confirmation | ◐ A DPIA signed with the UZ Gent DPO exists outside this repository (owner, 2026-09-29). [TF-14](TF-14-dpia.md) is to be reconciled with it and filed, including the lawful basis and the Art. 9 condition (#518). |
| E5 | Pseudonymization extent within CoGA; retention period; erasure-vs-record-keeping policy | — |
| E6 | Supported browser(s) for the IFU minimum-requirements section | — (proposal to confirm: a current Chrome or Edge — the browser tests run in Chromium only, [TF-09d](TF-09d-browser-e2e-verification.md)) |

## F. Post-market & vigilance → TF-16, TF-17
| # | Question | Answer |
| --- | --- | --- |
| F1 | PMS review cadence (e.g. annual) and PMS-report owner | — |
| F2 | PMS quantitative indicators & action thresholds | — |
| F3 | FAMHP in-house-device serious-incident reportability criteria, timelines & channel | — |
| F4 | User intake/support & incident-reporting contact for the IFU | ◐ Problems and incidents are reported as a CMGGMC probleemmelding ([TF-17 §2](TF-17-vigilance-capa.md)); the support contact is still to be named ([TF-15 §7](TF-15-instructions-for-use.md)). |
