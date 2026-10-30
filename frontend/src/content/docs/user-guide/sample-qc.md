Open a family and press **Sample QC** to check that the samples are who the pedigree says. A swapped
tube, a wrong parent, a mislabelled sex or unexpected relatedness quietly invalidates everything
downstream, so run it before you interpret. It does not measure contamination. Some results can be a
sign of it, among other causes: a high Mendelian-error rate, or heterozygous calls on a man's X that
leave his genetic sex indeterminate or female. A pass does not rule contamination out.

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
  confirmed and grey ? when it was not checked. Hover a cell for the reason. A sample without X
  genotypes, such as in a PGT family, is sexed from its reads by the pipeline's ngs-bits, and the
  reason says so. An embryo's recorded sex comes from that same call, so its ✓ confirms nothing
  independent; nor does that of an index added with the sex ngs-bits read.
- **Relatedness matrix** — each pair's inferred relationship, kinship and IBS0. A pair that contradicts
  the pedigree, including parents who look related, is outlined: red for a fail, amber for a warning.
- **NIPT cards** — paternity, fetal sex and the category check, for a cfDNA family.
- **Sequencing & embryo QC** — in the same per-sample table, under its own heading: each sample's
  sequencing QC as its pipeline reported it, one column per measure: mean depth and alignment, and for a PGT embryo its allele drop-out (ADO) and drop-in (ADI)
  and its Mendelian concordance with its parents before and after imputation, the numbers of the
  pipeline's dashboard. A value outside the lab's cut-off shows its verdict; one without a cut-off is
  shown, not judged; **Seq. QC** is the sample's worst. These do not change the sample's status or the
  overall verdict, or stop sign-out; the signed report records every value.

> **What to do with a warning or a fail.** A fail points to a real problem: re-check the sample sheet,
> the pedigree and the genotypes before you interpret. A warning usually means too little data to decide
> (few informative sites, a low fetal fraction, a check that could not run); it weakens the analysis but
> does not invalidate it. A fail, or a check of the pedigree or of a sample's identity that could not run
> (a NIPT parent's sex, for example), also stops sign-out until someone records a reason.

[Sample-integrity QC reference (checks, thresholds)](/docs/reference/sample-qc "further-reading")
