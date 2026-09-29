Open a family and press **Sample QC** to run an automated sample-integrity check. It verifies that
the samples are who the pedigree says they are *before* any variant call, segregation analysis, or
report is trusted — the failure modes it catches (a swapped tube, a wrong parent, a mislabelled sex,
contamination, unexpected relatedness) quietly invalidate everything downstream.

> **It adapts to the application.** CoGA runs different assays with different notions of integrity,
> so the page resolves the application first and runs only the meaningful checks:

- **Long-read WGS family** — sex concordance, relatedness vs the pedigree, and the Mendelian-error
  rate.
- **Shallow-WGS PGT** — embryo sex and *parentage* (each embryo a true child of both parents — no
  switch), plus Mendelian.
- **Monogenic NIPT (cfDNA)** — paternity (categories 7/8), fetal sex (paternal X transmission),
  germline parent sex, and a cfDNA category-distribution QC, instead of genotype relatedness.
- **Carrier couple** — sex per partner and a confirmation that the two are unrelated.
- **Single targeted sample** — sex only.

### Reading the page

- **Pedigree with QC overlay.** Each individual’s symbol carries its roll-up verdict — the outline
  and any filled region turn **green** (pass), **amber** (warning) or **red** (fail). Hover a symbol
  for why.
- **Per-sample table.** Recorded sex vs genotype sex (green when concordant, red on mismatch) and
  the Mendelian-error rate, colour-coded by status.
- **Relatedness matrix.** A sample × sample grid (lower triangle — it is symmetric) coloured by the
  inferred relationship, with kinship (φ) and IBS0 per cell. A pair that contradicts the pedigree —
  including co-parents who look related (consanguinity) — is outlined in red.
- **NIPT cards.** For a cfDNA family, dedicated paternity, fetal-sex and cfDNA-category-QC cards.

> **What to do with a warning or fail.** A fail points to a real integrity problem — resolve it
> (re-check the sample sheet, the pedigree, or the genotypes) before interpreting. A warning usually
> means too little data to call confidently (few informative sites, low fetal fraction); it weakens,
> but does not invalidate, the downstream analysis. On partial or mock data the page degrades to
> warnings rather than failing.

[Sample-integrity QC reference (checks, thresholds, data sources)](/docs/reference/sample-qc "further-reading")
