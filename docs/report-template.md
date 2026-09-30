# Family Report Template

CoGA drafts a clinical report for the variants an analyst selects in a family's workspace.
The report turns the curated variant data, the ACMG/AMP criteria, the gene context and the
family's phenotype into readable prose that follows common guidelines for reporting
(candidate causal) variants.

> **Decision support.** The report is assembled from data already in CoGA. A qualified
> clinical scientist must review and confirm it before any clinical use. The report can be
> signed out into a frozen, content-hashed record; see
> [clinical-traceability.md](clinical-traceability.md).

## Selecting variants for the report

A variant is included by giving it the **`report`** review tag:

- in the small-variant and structural-variant **tables** and **cards**, with the **Report**
  toggle next to *Review* and *Exclude*;
- in the **Tags & notes** dialog or the **ACMG classify** dialog, next to the other review
  tags.

`report` is one of the built-in collaboration tags
(`backend/app/services/small_variant_review_tags.py`), so it appears in every tag picker and
filter.

## Opening the report

The **Report** button in the family workspace opens `/families/{family_id}/report`. A family
that has been signed out opens on its latest signed version, drawn from the frozen record
(below); this template is the live report (`?view=live`), which is where the case is signed
out. The live report loads the small variants and the structural variants
tagged `report`
(`GET /families/{family_id}/small-variants?review_tag=report` and the
`structural-variants` equivalent), the gene profile of each reported gene
(`GET /genes/profile`), the family's HPO terms (`GET /families/{family_id}/hpo`) and its
annotation manifest.

## What each variant section contains

For every reported small variant the template drafts:

1. **Variant description**: a full sentence with the zygosity (from the proband's
   genotype), consequence, gene, HGVS (`c.`/`p.`) and locus, followed by the gnomAD
   frequency, the ClinVar assertion and the in-silico predictions (CADD, REVEL, SIFT,
   PolyPhen, SpliceAI), and a segregation sentence across the family members.
2. **Classification motivation**: the accepted ACMG/AMP criteria written out (code, applied
   strength, description and any analyst evidence), with an evidence summary tying together
   frequency, ClinVar, in-silico predictions and segregation. The classification and point
   total come from the saved ACMG review.
3. **Gene**: the curated gene summary, its associated conditions (OMIM, GenCC) with their
   mode of inheritance, and the gene panels it belongs to.
4. **Phenotype (HPO)**: the family's recorded HPO terms, highlighting those that overlap the
   gene's HPO associations.
5. **Analyst note**: the review note, when there is one.

The prose helpers live in `frontend/src/pages/families/reportNarrative.ts` and are
unit-tested on their own, so the wording can change safely.

## Provenance, drift and sign-out

The report page also shows the case's provenance (the CoGA build and the annotation
manifest's module versions, in the footer, with the device label), an amber banner for
classifications whose evidence changed, the classification audit trail, and the **Sign out
report** action with its gates. What these record and how they work is in
[clinical-traceability.md](clinical-traceability.md); how a
lab user works with them is in the user guide's clinical report section
([clinical-report.md](../frontend/src/content/docs/user-guide/clinical-report.md)).

The live report always draws live data and is never the signed report. After a sign-out it
checks itself against the latest signed version and says whether it still matches.

A signed version is rendered from its frozen record alone: each reported variant's
classification, criteria, frozen evidence, tags and note, and the checks at sign-out. The record
holds none of the prose inputs above (the variant description, segregation, gene and phenotype
context), so a signed version names each variant by its ID and says what its record does not
hold. Both views download the signed version as JSON.

## Printing

**Print report** on the live report, and **Print signed version N** on a signed version, open
the browser's print dialog. The print styles hide the report's buttons and keep each card on one
page, so it exports cleanly to PDF. Every printout of the live report starts with a notice
that it is not the signed report. A signed version prints with a notice only when its record
fails its content hash, a later version supersedes it, or the versions could not be listed.
