Monogenic NIPT screens a pregnancy for single-gene disorders from **cell-free DNA (cfDNA) in maternal
plasma**, with a sample from the father. The fetus is never sequenced: its genotype is inferred from the
cfDNA allele fractions and the **fetal fraction (FF)**, the share of the cfDNA that comes from the
fetus.

### Setting up a NIPT family

In **Family Builder**, tick **Monogenic NIPT (cfDNA from maternal plasma)**. Add the father, the cfDNA
sample as the mother (mark it **cfDNA (maternal plasma)**) and the fetus as the proband. A package import
does the same when its manifest says `analysis_type: monogenic_nipt`. The data is one combined VCF with a
father column and a cfDNA column, and it must carry FORMAT/AD. The family page then shows a **Monogenic
NIPT** button.

### The NIPT page

- **The header** shows the fetal fraction, its 95% confidence interval and the number of sites it rests
  on, a *Low-confidence FF* chip when the estimate is weak, and the filter funnel (*Total*,
  *Quality-filtered*, *Artifact-filtered*, *Analysed*).
- **The filters** are the small-variant filters plus **Maternal/fetal categories** (tick any of the
  eight; each shows its count) and an **Inheritance** preset: *De novo candidates*, *Paternal dominant*,
  *Maternal dominant* or *Recessive at-risk*.
- **Each variant** carries its category, the maternal and fetal state, the observed and expected VAF, a
  confidence and any flags, next to the usual annotation, tags and ACMG classification.

Categories 1 (de novo) and 7 (paternal allele transmitted) are the robust signals. Whether the fetus
inherited a maternal allele (categories 2–6) depends on the fetal fraction and the depth. Category 8 is
a quality signal.

> **Two presets need care.** *Maternal dominant* ticks only category 3: tick category 4 as well.
> *Recessive at-risk* lists every carrier variant in genes where both parents carry one; it does not say
> that the fetus is affected. Read the fetal status off each variant's category.

### The NIPT report

**Report** on the NIPT page opens a printable summary: the fetal fraction, the coverage check of the
panel genes, and the classified variants grouped as *De novo in fetus*, *Dominant, transmitted*,
*Recessive / biallelic risk* and *Other categories*. It uses the panel or genes you selected, not your
other filters, and lists up to 500 variants. **Print report** prints it. A NIPT report has no sign-out.

> **Screening, not diagnosis.** A NIPT call must be confirmed by an invasive diagnostic test. Check the
> fetal fraction and its interval before you read any category, and a de novo call's VAF against
> `FF / 2` before you act on it: CoGA does not test it.

[Monogenic NIPT reference (the model, the categories, the flags, the presets)](/docs/reference/monogenic-nipt "further-reading")
