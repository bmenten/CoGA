Monogenic NIPT (non-invasive prenatal testing) screens a pregnancy for single-gene disorders using
**cell-free DNA (cfDNA) from maternal plasma**, cross-referenced with a **paternal** sample. The
plasma cfDNA is a mixture: mostly maternal DNA with a minor **fetal fraction**. The fetus is never
sequenced directly — its genotype is *inferred* from how far the cfDNA allele fraction (VAF)
deviates from the clean maternal expectations (0%, 50%, 100%), in proportion to the fetal fraction.

> **Two samples, three people.** CoGA models the case as a trio — father, mother, fetus — backed by
> only two physical samples. The *father* column is an ordinary germline VCF; the *mother* column is
> the plasma cfDNA (maternal plus fetal signal); the *fetus* is a placeholder with no sequence of
> its own. A family is in NIPT mode when its analysis type is `monogenic_nipt` and the cfDNA sample
> is tagged `nipt_cfdna` — that is what surfaces the Monogenic NIPT tab.

### The fetal fraction, and how it is calculated

Everything downstream depends on the **fetal fraction (FF)** — the proportion of the plasma cfDNA
that is fetal. For a biallelic site the expected cfDNA allele fraction is `VAF = m · (1 − FF) + f ·
FF`, where `m` and `f` are the maternal and fetal alt-allele fractions (0, ½, or 1).

FF is estimated from **category-7 sites**: positions where the mother is homozygous reference, the
father carries the alt allele, and the alt is present in the cfDNA. The fetus must have inherited a
paternal alt allele, so it sits as a clean heterozygote whose only contribution to the plasma is
fetal — the site sits at exactly `FF / 2`. Taking the robust median over many such sites and
doubling it gives **FF = 2 × median(VAF) over category-7 sites**. CoGA restricts this to
well-covered, high-quality autosomal sites and reports the number of sites and a 95% confidence
interval, so the estimate can be trusted or distrusted at a glance.

> **Reading the FF badges.** The header shows the FF estimate, its confidence interval, and the
> category-7 site count. A *Low-confidence FF* chip appears when there are too few sites or the
> interval is wide. If the run supplied an *external FF* (from an upstream caller) it is shown
> alongside, and a *FF disagreement* chip flags when the two differ — the computed estimate stays
> the default and is never silently overridden.

### The eight maternal/fetal categories

Once FF is known, the expected VAF of every category is a fixed number, so each cfDNA variant is
assigned to the category whose expected VAF best explains its observed reads (a binomial likelihood,
not a hard cut-off). The father genotype resolves the two cases that would otherwise be ambiguous.

| Cat | Maternal / fetal state | Expected cfDNA VAF | Clinical meaning |
| --- | --- | --- | --- |
| 1 | De novo in fetus (absent in both parents) | FF / 2 (low) | Candidate de novo dominant — father hom-ref separates it from paternal transmission |
| 2 | Maternal het, not inherited | 50% − FF/2 | A maternal carrier allele the fetus did not receive |
| 3 | Maternal het, fetus het | 50% | Maternal allele transmitted; the maternal hit of a possible compound pair |
| 4 | Maternal het, fetus hom-alt | 50% + FF/2 | Fetus inherited both alleles — homozygous recessive risk |
| 5 | Maternal hom-alt, fetus het | 100% − FF/2 | Fetus inherited one reference allele from the father |
| 6 | Maternal \& fetal hom-alt | 100% | Both homozygous (commonly a common variant) |
| 7 | Paternal allele transmitted (mother hom-ref) | FF / 2 | Drives the fetal-fraction estimate; the paternal hit of a possible compound pair |
| 8 | Paternal hom-alt, absent in cfDNA | ≈ 0 (expected FF/2) | False-negative QC signal — a variant the fetus should carry but the assay missed |

Categories 1 and 7 share the same expected VAF (`FF / 2`) and are told apart only by the father
genotype: hom-ref means de novo, a carried allele means paternal transmission. A high category-8
rate is a warning — those sites should have appeared at `FF / 2`, so their absence points to
dropout, insufficient FF, or coverage gaps.

### What is and is not resolvable

As FF approaches zero the category centres collapse together — category 2 (`50% − FF/2`) and
category 3 (`50%`) nearly coincide — so a low-depth or low-FF site gets a low confidence rather than
a forced call. Each variant carries a **confidence**; the maternal state is usually solid even when
the fetal call is uncertain. Use the confidence and the flags (for example *ff\_too\_low*,
*low\_depth*, *ambiguous*) to decide how much weight a single call deserves.

### Using the page

The Monogenic NIPT workspace mirrors the small-variant page. The header folds in the fetal fraction
and the filter funnel (total → quality-filtered → artifact-filtered → analysed). The collapsible
filter panel adds, on top of the usual small-variant filters (gene panel, genes and intervals,
annotation, frequencies, in-silico, flags), two NIPT-specific controls:

- **Maternal/fetal categories** — pick any of the eight categories; each option shows how many
  variants fall in it. This replaces the genotype subsection of the small-variant filter.
- **Inheritance presets** — one-click clinical reads of the category assignment: *De novo* (category
  1), *Paternal dominant* and *Maternal dominant* (transmitted parental alleles), and *Recessive
  at-risk* (the fetus inherited a maternal and a paternal hit in the same gene, or is homozygous).

Results use the full small-variant display: when fewer than 100 variants match it switches to cards,
and every variant carries the complete annotation (father and cfDNA genotypes, ClinVar, gnomAD,
in-silico scores, HGVS) plus tagging, reporting, and ACMG classification — the same review state as
the small-variant page. The **NIPT classification block** on each card (and the NIPT column in the
table) shows the category, the maternal and fetal state, the observed and expected VAF, and the
confidence. On-target coverage is summarised as a single median depth over the panel or family ROI.

> **Derived, not entered.** The fetal fraction and every per-variant category are computed from the
> two samples — their allele fractions, depths, qualities, and the father genotype. The only inputs
> are the combined VCF, the coverage and target regions, the pedigree, and your filter choices.

[Monogenic NIPT reference (cfDNA workflow, fetal fraction, the 8 categories)](/docs/reference/monogenic-nipt "further-reading")
