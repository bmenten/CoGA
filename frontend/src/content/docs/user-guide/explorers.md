Three explorers answer questions beyond a single case. Open them from the menu under the arrow at the
right of the top bar, or from the dashboard.

### Gene explorer

A profile per gene: the transcripts, badged **MANE Select**, **MANE Plus Clinical**, **RefSeq Select**
and **Ensembl Canonical**; constraint metrics (pLI, LOEUF, missense constraint); disease associations
with ClinGen and GenCC evidence and OMIM links; the *Monarch gene–disease associations* (see
[Phenotypes, panels and phenotype matching](#phenotype-matching)); and links to the main external
resources.

- **ACMG secondary finding** says whether the gene is on the ACMG secondary-findings list that CoGA
  ships: *On ACMG SF v3.3*, with the phenotype, category, inheritance, variants to report and
  footnotes the list gives for the gene, or *Not on ACMG SF v3.3*. *Not checked* means the list could
  not be read for this gene.
- **ClinGen dosage curation** gives ClinGen's haploinsufficiency and triplosensitivity curations of the
  gene, the date of the curation and a link to ClinGen's report. The item with an *HI score* is
  ClinGen's haploinsufficiency score as dbNSFP carries it.

### Variant explorer

Every small variant across the projects you can access, one row per variant: gene, variant,
classification, consequence and tags, with the number of carriers (het and hom) and of families.
A variant reviewed in more than one family shows the most severe classification of those reviews, and
the tags of all of them. Each review counts with the class the family page shows for it, also when it
holds only a class tag such as *Likely pathogenic - class 4*. Of two reviews of the same class, the row
shows the one saved last. The classification filter keeps a variant one of whose reviews shows that
class, as on the family page, so it also finds a review classified by its class tag alone. The row may
show a more severe class, from another family's review.

- Filter as on the family page (gene, consequence, ClinVar, frequency, in-silico scores), plus tags and
  classification — for example every variant tagged *Report*.
- The annotation filters work as on the family page: they must all hold on one transcript of the
  variant, one of the in-silico predictors you set is enough, and *MANE only* keeps nothing on GRCh37 or
  T2T (see [Small-variant prioritisation](#small-variant-filtering)).
- Every filter is read for each family as its own family page reads it, so a row counts the carriers
  and families of the family pages that show the variant. When two families' files were annotated
  differently (two VEP runs), a filter on the gene, consequence, ClinVar or frequency counts only the
  family whose own annotation meets it.
- A row counts only the samples their family shows. A member taken out of a family keeps its calls in
  that family's imported data, but is not counted there or listed among its carriers; the genotype
  filter neither offers nor reads a sample that no family shows.
- A location reads the chromosome with or without *chr*, and the mitochondrion as chrM, M or MT.
- **Genotype per sample** keeps the variants that sample carries as het, hom or either (a haploid call,
  on chrM or a male's X, counts as hom). The row still counts every carrier in your projects, not only
  that sample's family.
- A gene panel works as on the family page too: it keeps the variants of its genes and the variants in
  its regions (PanelApp's regions for the assembly, and each gene's position in the gene reference), so a
  variant whose annotation names its gene differently is kept. This holds for a panel of any size, the
  Mendeliome included.
- Click a het, hom or family count to see the carriers per family, with links to their family pages.
- Imputed calls are left out unless you tick **Include imputed variants (GLIMPSE2 / SHAPEIT)**.
- **Download CSV** exports the filtered variants with their annotation, up to 50,000 rows. A file that
  misses matches has *TRUNCATED* in its name, and the page says why: more variants matched than the
  50,000 rows (narrow the filters), or the tag or classification filter matched more than the 200,000
  variants one search takes (filter on fewer tags or classifications).
- The counts come from a summary CoGA keeps as families' variants are written. While a family is being
  imported, or for a few seconds after its members or projects change, the page says how many families
  are being added or brought up to date: their variants may be missing, or counted as before the change.
  Refresh in a moment.

The same filtered variants can also be viewed three other ways, from the tabs above the results:

- **Genes**: one row per gene a variant's annotation names, with the number of variants (also by their
  most severe impact on the gene) and the carriers *summed over those variants*: a sample with two
  variants in the gene counts twice. Click a gene to open its variants, where each variant's carriers
  are counted once.
- **Transcripts**: one row per transcript a variant's annotation names, every transcript or only the
  MANE or canonical ones, with its gene and the variants by their most severe consequence on it (loss of
  function, missense or in-frame, splice region, synonymous, other) and the carriers summed over them.
  Click the gene to open its variants.
- **Cohort frequency**: the variants by their number of carriers in your projects (1, 2–5, 6–20,
  21–100, more than 100) against their gnomAD allele frequency (not in gnomAD, below 0.1 %, 0.1–1 %,
  1–5 %, 5 % and more). Variants frequent here and rare in gnomAD can be artefacts or founder variants.
  Click a cell to open its variants; the filter shows above the results until you remove it.

### Clinical CNV explorer

The catalogue of known clinical CNVs (syndromes and recurrent regions) for an assembly. Search by name,
chromosome or type; each row shows the CNV, cytoband, location, size and **ClinVar P/LP**.

- **Cytoband** is the band the knowledgebase states. Where it states none, CoGA reads the bands the
  region spans from the assembly's cytobands, the ones the ideogram draws. An assembly without
  cytobands, such as T2T, shows none.

- **ClinVar P/LP**, for example *12 loss · 3 gain*, counts the pathogenic or likely pathogenic ClinVar
  copy-number variants that overlap the region by at least 30% both ways, as counted when the
  knowledgebase was built.
- **"—"** means that the knowledgebase was built without ClinVar: *not recorded*, not zero.
- Click a CNV for its detail page, with its description, the supporting ClinVar records and links to
  OMIM and DECIPHER. The same page opens when you click a clinical CNV in the viewers.

> **Counts follow your access.** The variant explorer's cohort counts include only the projects you can
> see: your own, or every project for a superuser or an administrator.
