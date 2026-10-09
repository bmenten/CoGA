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

- **Overall verdict** — the worst of the checks. A check that could not run counts as a warning, never as
  a pass, and a note names each sample whose sex could not be checked.
- **Pedigree** — each person's symbol carries a ring and a badge: green ✓ pass, amber ! warning, red ✕
  fail. A check that could not run counts as a warning here too. Hover a symbol for the reason.
- **Per-sample table** — recorded sex against genetic sex, and the Mendelian-error rate. The genetic sex
  carries green ✓ when it matches the record, red ✗ when it does not, amber ! when it could not be
  confirmed and grey ? when it was not checked. Hover a cell for the reason.
- **Relatedness matrix** — each pair's inferred relationship, kinship and IBS0. A pair that contradicts
  the pedigree, including parents who look related, is outlined: red for a fail, amber for a warning.
- **NIPT cards** — paternity, fetal sex and the category check, for a cfDNA family.

> **What to do with a warning or a fail.** A fail points to a real problem: re-check the sample sheet,
> the pedigree and the genotypes before you interpret. A warning usually means too little data to decide
> (few informative sites, a low fetal fraction, a check that could not run); it weakens the analysis but
> does not invalidate it. A fail, or a check of the pedigree or of a sample's identity that could not run
> (a NIPT parent's sex, for example), also stops sign-out until someone records a reason.

[Sample-integrity QC reference (checks, thresholds)](/docs/reference/sample-qc "further-reading")
