Three explorers answer questions beyond a single case. Open them from the menu under the arrow at the
right of the top bar, or from the dashboard.

### Gene Explorer

A profile per gene: the transcripts, badged **MANE Select**, **MANE Plus Clinical**, **RefSeq Select**
and **Ensembl Canonical**; constraint metrics (pLI, LOEUF, missense constraint); disease associations
with ClinGen and GenCC evidence and OMIM links; the *Monarch gene–disease associations* (see
[Phenotypes, panels and phenotype matching](#phenotype-matching)); and links to the main external
resources.

### Variant Explorer

Every small variant across the projects you can access, one row per variant: gene, variant,
classification, consequence and tags, with the number of carriers (het and hom) and of families.

- Filter as on the family page (gene, consequence, ClinVar, frequency, in-silico scores), plus tags and
  classification — for example every variant tagged *Report*.
- Click a het, hom or family count to see the carriers per family, with links to their family pages.
- Imputed calls are left out unless you tick **Include imputed variants (GLIMPSE2 / SHAPEIT)**.

### Clinical CNV explorer

The catalogue of known clinical CNVs (syndromes and recurrent regions) for an assembly. Search by name,
chromosome or type; each row shows the CNV, cytoband, location, size and **ClinVar P/LP**.

- **ClinVar P/LP**, for example *12 loss · 3 gain*, counts the pathogenic or likely pathogenic ClinVar
  copy-number variants that overlap the region by at least 30% both ways, as counted when the
  knowledgebase was built.
- **"—"** means that the knowledgebase was built without ClinVar: *not recorded*, not zero.
- Click a CNV for its detail page, with its description, the supporting ClinVar records and links to
  OMIM and DECIPHER. The same page opens when you click a clinical CNV in the viewers.

> **Counts follow your access.** The Variant Explorer counts only the projects you can see, so its
> cohort counts reflect your accessible cohort.
