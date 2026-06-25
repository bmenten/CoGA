# Family Report Template

CoGA can draft a clinical report for the variants an analyst has selected from a
family's small-variant workspace. The report turns curated variant data, ACMG/AMP
criteria, gene context and family phenotype into readable prose that follows
common guidelines for reporting (candidate causal) variants.

> **Decision support.** The report is assembled from data already in CoGA. A
> qualified clinical scientist must review and confirm it before any clinical use.
> The report carries its full provenance (annotation/reference versions, evidence
> drift, an immutable audit trail) and can be **signed out** into a frozen,
> content-hashed snapshot — see [Traceability & sign-out](#traceability--sign-out)
> and `docs/clinical-traceability.md`.

---

## Selecting variants for the report

Variants are included by tagging them with the **`report`** review tag:

- On the small-variant **table** and **cards**, a **Report** quick-toggle sits
  next to *Review* and *Exclude*.
- From the **Tags & notes** dialog (formerly *Edit review* / *More tags*) or the
  **ACMG classify** modal, toggle the *Report* tag alongside the other review
  tags. Existing tags are shown in the ACMG modal so the full review state is
  visible while classifying.

`report` is a default *collaboration* tag (`backend/app/services/small_variant_review_pg.py`),
so it needs no migration and appears automatically in the tag pickers and filters.

## Opening the report

The **Report** link in the family workspace *Variants* section opens
`/families/:familyId/report`. The page fetches every small variant tagged
`report` (`GET /families/{id}/small-variants?review_tag=report`), the gene profile
for each reported gene (`GET /genes/profile`), and the family's HPO annotations
(`GET /families/{id}/hpo`).

## What each variant section contains

For every reported variant the template drafts:

1. **Variant description** — a full sentence with zygosity (from the proband
   genotype), consequence, gene, HGVS (`c.`/`p.`) and locus, followed by gnomAD
   frequency, ClinVar assertion and in-silico predictions (CADD, REVEL, SIFT,
   PolyPhen, SpliceAI), and a segregation sentence across family members.
2. **Classification motivation** — the accepted ACMG/AMP criteria written out
   (code, applied strength, description and any analyst evidence), with an
   evidence summary tying together frequency, ClinVar, in-silico and segregation.
   The classification and point total come from the saved ACMG review.
3. **Gene** — the curated gene summary plus associated conditions (OMIM / GenCC)
   with mode of inheritance, and gene-panel memberships.
4. **Phenotype (HPO)** — the family's recorded HPO terms, highlighting any that
   overlap the gene's annotated HPO associations to support a phenotypic match.
5. **Analyst note** — the review note, when present.

The prose helpers live in `frontend/src/pages/families/reportNarrative.ts` and are
unit-tested in isolation so the wording can evolve safely.

## Traceability & sign-out

The report is also the provenance and sign-out surface for the case (see
`docs/clinical-traceability.md` for the full design). Four things travel with it:

1. **Provenance footer** — a generation timestamp and the annotation/reference
   **module versions** that produced the data (assembly, VEP, ClinVar, gnomAD,
   dbNSFP, SpliceAI, GenCC, PanelApp, Monarch, HPO).
   `GET /families/{id}/annotation-manifest`.
2. **Evidence-drift banner** — an amber warning listing any classification whose
   backing annotation changed since it was made (`ClinVar X → Y`), so stale
   interpretations don't silently persist. `GET /families/{id}/classification-drift`.
3. **Classification audit trail** — an immutable, append-only record of who
   classified, tagged or annotated each variant, when, and what changed
   (before → after). `GET /families/{id}/clinical-audit`.
4. **Sign-out** — **Sign out report** freezes the manifest, the reported variant
   list, each classification and its evidence snapshot, and the drift state into a
   **versioned, SHA-256 content-hashed** snapshot (`report_signouts`, append-only).
   Sign-out is **gated on drift** (a `409` unless the drift is acknowledged) and is
   recorded in the audit trail; amendments create a new version. The report shows a
   green frozen sign-out record (version · who · when · content hash).
   `POST /families/{id}/report/sign-out`.

## Printing / export

**Print report** triggers the browser print dialog; print styles hide the page
chrome and keep each variant card from breaking across pages, so it exports
cleanly to PDF.
