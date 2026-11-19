Open a family and press **Sample QC** to check that the samples are who the pedigree says. A swapped
tube, a wrong parent, a mislabelled sex or unexpected relatedness quietly invalidates everything
downstream, so run it before you interpret.

It does not measure contamination. Some results can be a sign of it, among other causes: a high
Mendelian-error rate, or heterozygous calls on a man's X that leave his genetic sex indeterminate or
female. A pass does not rule contamination out.

The page first works out what kind of case it is and runs only the checks that fit:

- **Family with parents** — sex, relatedness against the pedigree, Mendelian errors.
- **Shallow-WGS PGT** — embryo sex and parentage (each embryo a child of both parents), Mendelian
  errors.
- **Monogenic NIPT** — paternity, fetal sex, parent sex and a check of the cfDNA categories.
- **Conventional NIPT** — no check: ccnv's output holds no genotypes, and the fetal sex is known only
  from the analysis, as an embryo's is in PGT.
- **Carrier couple** — sex per partner, and that the two are unrelated.
- **Single sample** — sex only.

The checks read the family's genotypes (*Read from* in the header names the call set and the number
of sites). A genotype with a quality (GQ) below 20 counts as a no-call.

### Reading the page

- **The verdict** at the top — the worst of the checks. A check that could not run counts as a
  warning, never as a pass. A note names each sample whose sex could not be checked.
- **Pedigree & sample integrity** — each person's symbol carries a ring and a badge: green ✓ pass,
  amber ! warning, red ✕ fail. A check that could not run counts as a warning here too. Hover a symbol
  for the reason.
- **Family members & per-sample checks** — recorded sex against genetic sex, and the Mendelian-error
  rate. The genetic sex carries green ✓ when it matches the record, red ✗ when it does not, amber !
  when it could not be confirmed and grey ? when it was not checked. Hover a cell for the reason. A
  sample without X genotypes (for example in a PGT family) is sexed from its reads by the pipeline's
  ngs-bits; the reason says so. An embryo has sex `0` (unknown) in the family's PED: its genetic sex
  shows the sex its data indicate, with ✓ (*Embryo's sex not recorded*), and amber ! only when the
  data tell no sex.
- **Relatedness vs pedigree** (**Parentage (embryos ↔ parents)** in a PGT family) — each pair's
  inferred relationship, kinship and IBS0. A pair that contradicts the pedigree, including parents who
  look related, is outlined: red for a fail, amber for a warning. A first-degree pair reads as
  parent–child when its IBS0 is below an eighth of the IBS0 of the family's unrelated parents, or
  below a fixed 0.8% without them; otherwise as siblings. The pair's message gives the cut.
- **Paternity**, **Fetal sex** and **cfDNA category QC** — for a cfDNA family.
- **Sequencing & embryo QC** — under its own heading in the per-sample table: each sample's sequencing
  QC as its pipeline reported it, one column per measure. These are mean depth and alignment and, for a
  PGT embryo, its allele drop-out (ADO) and drop-in (ADI) and its Mendelian concordance with its parents
  before and after imputation: the numbers of the pipeline's dashboard. A value outside the lab's
  cut-off shows its verdict; a value without a cut-off is shown, not judged. **Seq. QC** is the
  sample's worst. These values do not change the sample's status or the overall verdict, and do not
  stop sign-out. The signed report records every value.

> **What to do with a warning or a fail.** A fail points to a real problem: re-check the sample sheet,
> the pedigree and the genotypes before you interpret. A warning usually means too little data to decide
> (few informative sites, a low fetal fraction, a check that could not run); it weakens the analysis but
> does not invalidate it. A fail, or a check of the pedigree or of a sample's identity that could not run
> (a NIPT parent's sex, for example), also stops sign-out until someone records a reason.

[Sample-integrity QC reference (checks, thresholds)](/docs/reference/sample-qc "further-reading")
