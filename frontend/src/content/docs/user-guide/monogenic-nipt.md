Monogenic NIPT screens a pregnancy for single-gene disorders from **cell-free DNA (cfDNA) in maternal
plasma**, with a sample from the father. The fetus is never sequenced: its genotype is inferred from the
cfDNA allele fractions and the **fetal fraction (FF)**, the share of the cfDNA that comes from the
fetus.

### Setting up a NIPT family

In **Family Builder**, tick **Monogenic NIPT (cfDNA from maternal plasma)**. Add the father, the cfDNA
sample as the mother (mark it **cfDNA (maternal plasma)**) and the fetus as the proband. A package import
does the same when its manifest says `analysis_type: monogenic_nipt`. The family page then shows a
**Monogenic NIPT** button.

The data is the NIPT-M pipeline's output: a single-sample VCF of the plasma and one of the father, with
a per-target coverage table for each (`coverage_<sample>.txt`). Package Import finds them by the sample
names of the PED. One combined VCF with a father column and a cfDNA column works as well. The calls
must carry FORMAT/AD.

### The NIPT page

- **The header** shows the fetal fraction, its 95% confidence interval and the number of sites it rests
  on, a *Low-confidence FF* chip when the estimate is weak, and the filter funnel (*Total*,
  *Quality-filtered*, *Artifact-filtered*, *Analysed*).
- **Quality checks** come first: the fetal fraction (with the per-site median and the de novo window),
  the fetal sex from the father's X alleles and from the chrY coverage, **paternity**, the maternal
  plasma sample, the coverage of the capture targets and why cfDNA calls failed the quality filter. A
  failed paternity check means another father or a sample mix-up: do not read the variants.
- **The filters** are the small-variant page's *Pathogenicity*, *Annotations*, *In Silico*, *Frequency*,
  *Locations* and *Exclude* filters, plus **Maternal/fetal categories** (tick any of the eight; each
  shows its count), a minimum classification confidence and an **Inheritance** view:
  - *De novo in the fetus*: calls in the fetal window without a supported call in the father, triaged
    and ranked by score. **List de novo candidates of priority** picks high, high and medium, or every
    candidate.
  - *Paternal, inherited by the fetus* and *Maternal, inherited by the fetus*: each parent's alleles
    with the probability that the fetus inherited them. **Also list the alleles the fetus did not
    inherit** adds the rest.
  - *Recessive: both parents carriers*: the genes where both parents are heterozygous for an allele,
    in a table with the fetal risk per gene.
- **Each variant** carries its category, the plasma reads, the father's genotype, the probabilities
  that the fetus inherited the paternal or maternal allele or is homozygous, a de novo candidate's
  priority and any flags, next to the usual annotation, tags and ACMG classification.
- **On-target coverage**, under the variants, gives the median depth over the capture targets of the
  panel and genes you selected (every target without a selection) and how many genes have a weak
  target: a mean below 300× or a base without coverage, where a fetal variant can be missed.
  **Coverage details** opens the coverage page: the genes below target and above it, each opening to
  its targets and their depth. **Back to NIPT** returns to the NIPT page with your filters.

The built-in presets **De novo**, **Paternal, inherited** and **Recessive (both parents carrier)** set a
view with high or moderate impact and a gnomAD frequency of 1% or less (a ClinVar pathogenic record
overrides the frequency). Pick a gene panel as well. The page opens on **De novo**; a link with its own
filters, such as **Back to NIPT**, opens on those.

> **Read the probabilities, not only the category.** A de novo or paternal allele shows clearly at any
> fetal fraction. Whether the fetus inherited a *maternal* allele needs depth and a fair fetal fraction:
> check its probability. The recessive risk assumes both alleles are pathogenic and, at two sites, in
> trans; an allele without a population frequency (every MNV) is noted.

### The NIPT report

**Report** on the NIPT page opens a printable summary: the fetal fraction, the quality checks, the
coverage of the targets in scope, and the classified variants grouped as *De novo in fetus*,
*Dominant, transmitted*, *Recessive / biallelic risk* and *Other categories*. Its footer names when it
was made and the CoGA version that made it. Its *Scope* names the panel and genes you selected; your
other filters do not apply. It lists up to 500 variants. When there are more, it says how many it lists
of how many and where the list stops, on screen and in print: narrow the scope with a panel or a gene.
**Print report** prints it. A NIPT report has no sign-out.

> **Screening, not diagnosis.** A NIPT call must be confirmed by an invasive diagnostic test. Check the
> fetal fraction and the quality checks before you read any variant, and a de novo candidate's absence
> in the father and its reads before you act on it.

[Monogenic NIPT reference (the model, the categories, the views, the quality checks)](/docs/reference/monogenic-nipt "further-reading")
