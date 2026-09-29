Open a family and press **Sample QC** to check that the samples are who the pedigree says. A swapped
tube, a wrong parent, a mislabelled sex, contamination or unexpected relatedness quietly invalidates
everything downstream, so run it before you interpret.

The page first works out what kind of case it is and runs only the checks that fit:

- **Long-read WGS family** — sex, relatedness against the pedigree, Mendelian errors.
- **Shallow-WGS PGT** — embryo sex and parentage (each embryo a child of both parents), Mendelian
  errors.
- **Monogenic NIPT** — paternity, fetal sex, parent sex and a check of the cfDNA categories.
- **Carrier couple** — sex per partner, and that the two are unrelated.
- **Single sample** — sex only.

### Reading the page

- **Pedigree** — each person's symbol carries a ring and a badge: green ✓ pass, amber ! warning, red ✕
  fail. Hover a symbol for the reason.
- **Per-sample table** — recorded sex against genetic sex, and the Mendelian-error rate.
- **Relatedness matrix** — each pair's inferred relationship, kinship and IBS0. A pair that contradicts
  the pedigree, including parents who look related, is outlined in red.
- **NIPT cards** — paternity, fetal sex and the category check, for a cfDNA family.

> **What to do with a warning or a fail.** A fail points to a real problem: re-check the sample sheet,
> the pedigree and the genotypes before you interpret. A warning usually means too little data to decide
> (few informative sites, a low fetal fraction); it weakens the analysis but does not invalidate it. A
> fail, or a pedigree check that could not run, also stops sign-out until someone records a reason.

[Sample-integrity QC reference (checks, thresholds)](/docs/reference/sample-qc "further-reading")
