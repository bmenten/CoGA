Monogenic NIPT screens a pregnancy for single-gene disorders from **cell-free DNA (cfDNA) in maternal
plasma**, with a sample from the father. The fetus is never sequenced: its genotype is inferred from the
cfDNA allele fractions and the **fetal fraction (FF)**, the share of the cfDNA that comes from the
fetus.

### Setting up a NIPT family

In **Family builder**, tick **Monogenic NIPT (cfDNA from maternal plasma)**. Add the father, the cfDNA
sample as the mother (mark it **cfDNA (maternal plasma)**) and the fetus as the proband. A package import
does the same when its manifest says `analysis_type: monogenic_nipt`. The family page then shows a
**Monogenic NIPT** button.

The data is the NIPT-M pipeline's output: a single-sample VCF of the plasma and one of the father, with
a per-target coverage table for each (`coverage_<sample>.txt`). The package import finds them by the sample
names of the PED. One combined VCF with a father column and a cfDNA column works as well. The calls
must carry FORMAT/AD.

### The NIPT page

- **The header** shows the fetal fraction, its 95% confidence interval and the number of sites it rests
  on, a *Low-confidence FF* chip when the estimate is weak, and the filter funnel (*Total*,
  *Quality-filtered*, *Artifact-filtered*, *Analysed*). As on every family page, it warns
  *Import incomplete* while a data import has left the family partly loaded or has not finished,
  and *Not validated for clinical use* for a family outside the validated scope.
- **Quality checks** come first: the fetal fraction (with the per-site median and the de novo window),
  the fetal sex from the father's X alleles and from the chrY coverage, **paternity**, the maternal
  plasma sample, the coverage of the capture targets and why cfDNA calls failed the quality filter. The
  section opens folded, with one verdict: **Pass** when every check passed, **Fail** as soon as one
  failed, **Warning** otherwise (a check that could not run counts as a warning). Click it for the
  details. A failed paternity check means another father or a sample mix-up: do not read the variants.
- **The filters** are the small-variant page's *Pathogenicity*, *Annotations*, *In Silico*, *Frequency*,
  *Locations* and *Exclude* filters and its review tags (*Review and curation*: the tags, not the
  classification or saved notes), plus **Maternal/fetal categories** (tick any of the eight; each
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
overrides the frequency); **De novo** also leaves out ClinVar benign and likely benign. The page opens
on **De novo** on the **Mendeliome** panel, named under the presets as *Active filter: De novo,
Mendeliome panel* (*default*), as on the small-variant page; pick a narrower panel if you need one. A link with its own
filters, such as **Back to NIPT**, opens on those.

> **Read the probabilities, not only the category.** A de novo or paternal allele shows clearly at any
> fetal fraction. Whether the fetus inherited a *maternal* allele needs depth and a fair fetal fraction:
> check its probability. The recessive risk assumes both alleles are pathogenic and, at two sites, in
> trans; an allele without a population frequency (every MNV) is noted.

### The NIPT report

**NIPT report**, at the right of the header beside **Report**, opens a printable summary: the fetal fraction, the quality checks, the
coverage of the targets in scope, and the classified variants grouped as *De novo in fetus*,
*Dominant, transmitted*, *Recessive / biallelic risk* and *Other categories*. Its footer names when it
was made and the CoGA version that made it. Its *Scope* names the panel and genes you selected; your
other filters do not apply. It lists up to 500 variants. When there are more, it says how many it lists
of how many and where the list stops, on screen and in print: narrow the scope with a panel or a gene.
**Print report** prints it. An *Import incomplete* or *Not validated for clinical use* warning heads
it, on screen and in print, whenever the family page would show one. The report is live, never the
signed record: it says so beside the latest signed version, and every printout starts with *Draft* or
*Not the signed report*.

### Signing the NIPT analysis out

**Sign out NIPT analysis** on the NIPT report is the analytical sign-off: it freezes the analysis into
the family's signed record, which the EPD report refers to by its report code. It is the family's own
sign-out, with the same checks and report code, so the family has one signed record whether you sign
out here or from the family report. The record holds the NIPT results as CoGA computed them: the fetal
fraction and the quality checks, the coverage, and each variant tagged *report* with its category,
reads and probabilities. Tag the variants you report first. **Show reported (N)**, in the NIPT page's
header, lists them before you sign out: the variants tagged *report*, with every other filter
cleared. A tagged variant the analysis does not classify (not in the family's NIPT data, on the
assay's artifact list, or without a call in the plasma) is in no list: the page names it above the
list with the reason, as the signed record does. Besides the Sample QC, a NIPT quality
check that failed (no fetal-fraction estimate, fetal-sex signals that disagree, or male DNA as the
maternal plasma) stops the sign-out until you record a reason. Afterwards the signed version opens,
with its report code; the NIPT report then shows **Amend sign-out** and says whether the data still
matches the signed version. A reader of the family's project cannot sign out, here or on the report:
the button says why.

> **Screening, not diagnosis.** A NIPT call must be confirmed by an invasive diagnostic test. Check the
> fetal fraction and the quality checks before you read any variant, and a de novo candidate's absence
> in the father and its reads before you act on it.

[Monogenic NIPT reference (the model, the categories, the views, the quality checks)](/docs/reference/monogenic-nipt "further-reading")
