Monogenic NIPT screens a pregnancy for single-gene disorders from **cell-free DNA (cfDNA) in maternal
plasma**, with a sample from the father. The fetus is never sequenced: its genotype is inferred from the
cfDNA allele fractions and the **fetal fraction (FF)**, the share of the cfDNA that comes from the
fetus.

### Setting up a NIPT family

In **Family builder**, tick **Monogenic NIPT (cfDNA from maternal plasma)** and give the **Pregnancy
ID**, the laboratory's ID of the pregnancy. Add the father, the cfDNA sample as the mother (mark it
**cfDNA (maternal plasma)**) and the fetus as the proband: CoGA records it as the pregnancy's fetus
(role `fetus`), which has no data of its own and is neither a proband nor an embryo. A package import
does the same when its package says `analysis_type: monogenic_nipt` and names the pregnancy
(`pregnancy_id`). The family page then shows a **Monogenic NIPT** button.

The data is the NIPT-M pipeline's output: a single-sample VCF of the plasma and one of the father, with
a per-target coverage table for each (`coverage_<sample>.txt`). The package import finds them by the sample
names of the PED. One combined VCF with a father column and a cfDNA column works as well. The calls
must carry FORMAT/AD.

### Several pregnancies

A family may hold several pregnancies of one woman. Each pregnancy's NIPT is an analysis of its own:
its data, reviews and sign-out are its own.

- A monogenic and a conventional NIPT are one pregnancy only when their packages name the same
  pregnancy ID. A package of another pregnancy adds an analysis beside the others and changes nothing
  of theirs.
- Each pregnancy has its own plasma and fetus. The father may be the same sample in both: each
  pregnancy keeps its own copy of his calls.
- The NIPT page, its report and the Sample QC show one pregnancy at a time. Choose it in the analysis
  list beside the family name (*monogenic NIPT of pregnancy ZW1*). The page opens on
  the pregnancy imported last.
- Two plasmas of one woman, linked as the same individual, are drawn as one person but are never one
  pregnancy: their results never merge.
- When a pregnancy's package lists two or more fetuses, the NIPT page, the NIPT report and the signed
  version show the **multiple-pregnancy note**: a fetal result may be present in one fetus or in more
  than one, and the analysis cannot tell which. The note does not apply to a monozygotic twin, whose
  fetuses share one genotype; CoGA is not told the zygosity, so it shows the note for every multiple
  pregnancy.

### The NIPT page

- **The header** shows the fetal fraction, its 95% confidence interval and the number of sites it rests
  on, a *Low-confidence FF* chip when the estimate is weak, and the filter funnel (*Total*,
  *Quality-filtered*, *Artifact-filtered*, *Analysed*). As on every family page, it warns
  *Import incomplete* while a data import has left the family partly loaded or has not finished,
  and *Not validated for clinical use* for a family outside the validated scope.
- **The diagnostic gene panel** says which gene panel the pregnancy's analysis is read and reported
  within: the indication's panel, or the clinical exome's. Choose it in the card under the header
  and change it when the indication changes; each change is on the family's audit trail. The page
  and its report open on it (without one, on the Mendeliome), and the sign-out freezes its version,
  its gene list and the coverage of its capture targets. A gene of the panel without a capture target
  is not tested. Without a diagnostic panel the sign-out asks for a reason.
- **The NIPT report** lists the **diagnostic candidates** of its scope by the locked criteria of the
  R validation (rare, protein-altering, ClinVar read strictly; see the reference): the de novo,
  paternal and maternal alleles and the recessive genes left for further investigation. The sign-out
  freezes them with the criteria's version. The page's filters stay adjustable, for exploring.
- **Quality checks** come first: the fetal fraction (with the per-site median and the de novo window),
  the fetal sex from the father's X alleles and from the chrY coverage, **paternity**, the maternal
  plasma sample, the coverage of the capture targets and why cfDNA calls failed the quality filter. The
  section opens folded, with one verdict: **Pass** when every check passed, **Fail** as soon as one
  failed, **Warning** otherwise (a check that could not run counts as a warning). Click it for the
  details. A failed paternity check means another father, a sample mix-up, the father's own DNA in the
  plasma or more than one fetus (its message says which): do not read the variants.
- **The filters** sit under **Advanced filters**, closed when the page opens, as on the small-variant
  page. They are the small-variant page's *Pathogenicity*, *Annotations*, *In silico*, *Frequency*,
  *Locations* and *Exclude* filters and its review tags (*Review and curation*: the tags, not the
  classification or saved notes), plus **Maternal/fetal categories** (tick any of the eight; each
  shows its count), a minimum classification confidence, **Hide recurrent low-VAF calls** (below) and
  an **Inheritance** view:
  - *De novo in the fetus*: calls in the fetal window without a supported call in the father, triaged
    and ranked by score. **List de novo candidates of priority** picks high, high and medium, or every
    candidate.
  - *Paternal, inherited by the fetus* and *Maternal, inherited by the fetus*: each parent's alleles
    with the probability that the fetus inherited them. **Also list the alleles the fetus did not
    inherit** adds the rest.
  - *Recessive: both parents carriers*: the genes where both parents carry an allele (and, for a son,
    the X-linked genes in which his mother carries one), in a table with per gene the fetal status
    (affected, carrier, unaffected or undetermined) and its probabilities.
- **Each variant** carries its category, the plasma reads, the father's genotype, the probabilities
  that the fetus inherited the paternal or maternal allele or is homozygous, a de novo candidate's
  priority, any flags and **Other cfDNA samples**: in how many other cfDNA samples of the assay, in
  every project, its allele is called, and how (maternal, the father's allele, or an *unexplained
  low-VAF* call that no parent explains, the mark of a recurrent artefact). This sits next to the usual
  annotation, tags and ACMG classification. As on the small-variant page, the diseases its gene is
  linked to are named under the card's heading, and **Collapse** folds a card you are done with to
  its heading.
- **Hide recurrent low-VAF calls** leaves out the variants that are an unexplained low-VAF call in more
  than the number of other cfDNA samples you give. A variant that ClinVar may call pathogenic, or that
  is common in the population, stays listed, marked *kept by the recurrence filter*. The filter narrows
  the list only; the fetal fraction and the classifications do not change. The count grows with every
  pregnancy imported, so it is also how you find candidates for the assay's artifact list.
- **Download CSV**, above the variants, saves the list as a CSV file: every variant the list
  classifies, in its order, with its category, reads, probabilities, de novo triage and recurrence,
  and on every row the fetal fraction and, for a multiple pregnancy, the multiple-pregnancy note. A
  list cut at its limit says so in the file name (*TRUNCATED*).
- **On-target coverage**, under the variants, gives the median depth over the capture targets of the
  panel and genes you selected (every target without a selection) and how many genes have a weak
  target: a mean below 300× or a base without coverage, where a fetal variant can be missed.
  **Coverage details** opens the coverage page: the genes below target and above it, each opening to
  its targets and their depth. **Back to NIPT** returns to the NIPT page with your filters.

The built-in presets **De novo**, **Paternal, inherited** and **Recessive (both parents carrier)** set a
view with high or moderate impact and a gnomAD frequency of 1% or less (a ClinVar pathogenic or likely
pathogenic record overrides both); **De novo** also leaves out ClinVar benign and likely benign. The page opens
on **De novo** on the **Mendeliome** panel, named under the presets as *Active filter: De novo,
Mendeliome panel* (*default*), as on the small-variant page. Pick a narrower panel if you need one. A
link with its own filters, such as **Back to NIPT**, opens on those.

> **Read the probabilities, not only the category.** A de novo or paternal allele shows clearly at any
> fetal fraction. Whether the fetus inherited a *maternal* allele needs depth and a fair fetal fraction:
> check its probability. An inheritance between 20% and 80% leaves a recessive gene's status
> undetermined. The recessive status assumes both alleles are pathogenic and, at two sites, in trans;
> an allele without a population frequency (every MNV) is noted.

### The NIPT report

**NIPT report**, at the right of the header beside **Report**, opens a printable summary: the fetal
fraction, the quality checks, the coverage of the targets in scope, and the classified variants grouped
as *De novo in fetus*, *Dominant, transmitted*, *Recessive / biallelic risk* and *Other categories*.

- Its footer names when it was made and the CoGA version that made it.
- Its *Scope* names the panel and genes you selected; your other filters do not apply.
- It lists up to 500 variants. When there are more, it says how many it lists of how many and where the
  list stops, on screen and in print. Narrow the scope with a panel or a gene.
- An *Import incomplete* or *Not validated for clinical use* warning heads it, on screen and in print,
  whenever the family page would show one.
- The report is live, never the signed record. It says so beside the latest signed version, and every
  printout starts with *Draft* or *Not the signed report*.

**Print report** prints it.

### Signing the NIPT analysis out

**Sign out NIPT analysis** on the NIPT report is the analytical sign-off. It freezes the analysis into
a signed record, which the EPD report refers to by its report code. It is the family's own sign-out,
with the same checks and report code, whether you sign out here or from the family report. The record
holds the NIPT results as CoGA computed them: the pregnancy and its fetuses, the fetal fraction and the
quality checks, the coverage, and each variant tagged *report* with its category, reads, probabilities
and recurrence in the other cfDNA samples.

When the family also holds another analysis (the conventional NIPT of the pregnancy, or another
pregnancy's NIPT), each is signed out on its own: the NIPT report signs out the monogenic NIPT of the
pregnancy it shows, and its record freezes and checks that analysis's data only. The family's
**Report** page signs out the conventional NIPT of each pregnancy.

- Tag the variants you report first. **Show reported (N)**, in the NIPT page's header, lists them
  before you sign out: the variants tagged *report*, with every other filter cleared.
- A tagged variant the analysis does not classify (not in the family's NIPT data, on the assay's
  artifact list, or without a call in the plasma) is in no list. The page names it above the list with
  the reason, as the signed record does.
- Besides the Sample QC, a NIPT quality check that failed (no fetal-fraction estimate, fetal-sex
  signals that disagree, or male DNA as the maternal plasma) stops the sign-out until you record a
  reason.
- Afterwards the signed version opens, with its report code. The NIPT report then shows **Amend
  sign-out** and says whether the data still matches the signed version.
- Only a supervisor of the family's project, a superuser or an administrator signs out. For an
  analyst, a viewer or a reader the button is disabled, here and on the report, and says why.

> **Screening, not diagnosis.** A NIPT call must be confirmed by an invasive diagnostic test. Check the
> fetal fraction and the quality checks before you read any variant, and a de novo candidate's absence
> in the father and its reads before you act on it.

[Monogenic NIPT reference (the model, the categories, the views, the quality checks)](/docs/reference/monogenic-nipt "further-reading")
