# Family Report Template

CoGA drafts a family report for the variants an analyst selects in a family's workspace.
The report turns the curated variant data, the ACMG/AMP criteria, the gene context and the
family's phenotype into readable prose that follows common guidelines for reporting
(candidate causal) variants.

> **Decision support.** The report is assembled from data already in CoGA. A qualified
> clinical scientist must review and confirm it before any clinical use. The analyst signs it
> out into a frozen, content-hashed record, the closed analysis (the analytical sign-off; see
> [clinical-traceability.md](clinical-traceability.md)). The clinical report itself is drafted
> and medically signed in the hospital's electronic patient record (EPD), outside CoGA, and
> refers 1:1 to that record ([TF-02 §3](regulatory/TF-02-device-description.md)).

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
classifications whose evidence changed, the clinical audit trail, and the **Sign out
report** action with its gates. What these record and how they work is in
[clinical-traceability.md](clinical-traceability.md); how a
lab user works with them is in the user guide's clinical report section
([clinical-report.md](../frontend/src/content/docs/user-guide/clinical-report.md)).

The live report always draws live data and is never the signed report. It says what a sign-out
is: the analytical sign-off in CoGA, whose record the EPD report of the medical sign-off refers
to by its report code. After a sign-out it checks itself against the latest signed version and
says whether it still matches.

A signed version is rendered from its frozen record alone: its report code, each reported
variant's description as reviewed (gene, transcript, HGVS c. and p., consequence, each sample's
genotype), classification, criteria, frozen evidence, tags and note, in a PGT family the embryo
calls at the region of interest (*PGT embryo results*, which the live report does not show), each
with its phasing QC and that QC's verdict against the thresholds the record states, the
gene panels applied, the repeat-expansion and Paraphase results (in a record that holds them, each
repeat count near a classification boundary marked beside its call, and whether QA had validated
TRGT's counting units at each locus), the reference-data releases, and the checks at sign-out. The record holds none of the other prose inputs above (the population frequency and in
silico predictions, segregation, gene and phenotype context), so a signed version says what its
record does not hold; a version signed before CoGA froze the variant description names each
variant by its ID alone. Both views download the signed version as JSON.

## Printing

**Print report** on the live report, and **Print signed version N** on a signed version, open
the browser's print dialog. The print styles hide the report's buttons and keep each card on one
page, so it exports cleanly to PDF. Every printout of the live report starts with a notice
that it is not the signed report; the notice also says when the clinical audit trail shows
only its most recent events (**Show older events** on the page reads the rest, a page at a time).
A signed version prints with a notice only when its record
fails its content hash, a later version supersedes it, or the versions could not be listed.
