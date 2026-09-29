# Monogenic NIPT (cell-free DNA) — reference

Monogenic NIPT screens a pregnancy for single-gene disorders from **cell-free DNA (cfDNA) in maternal
plasma**, together with a sample from the father. The plasma cfDNA is mostly maternal DNA with a small
**fetal fraction (FF)**. The fetus is never sequenced: CoGA infers its genotype from how far each cfDNA
allele fraction (VAF) moves away from the maternal values of 0%, 50% and 100%.

How to set up a NIPT family and use the page is in the [user guide](/docs), section *Monogenic NIPT*.
This page holds the model and the rules.

> **Screening, not diagnosis.** A NIPT call is decision support derived from cfDNA. Confirm it with an
> invasive diagnostic test before clinical use.

---

## A two-sample trio

CoGA models the case as a trio backed by two physical samples.

| Family member | Sample | What CoGA sees |
| --- | --- | --- |
| Father | germline VCF column | observed genotypes |
| Mother | **cfDNA** column (maternal plus fetal DNA) | observed allele fractions |
| Fetus | none | **inferred**, never observed |

A family is a NIPT family when it was created with the Monogenic NIPT option in Family Builder, or
imported with a package whose manifest says `analysis_type: monogenic_nipt`. CoGA takes the sample
marked as maternal-plasma cfDNA as the mother's sample (the mother's own sample if none is marked).

### What the input must contain

- **One combined VCF** with a father column and a cfDNA column, genotyped jointly, so every row carries
  both calls. A reference call must read `0/0`, not missing.
- Per call: the genotype (GT), the **read depth (DP)** and the **allele depths (AD)**; an allele
  fraction (AF) is used when present. Per site: QUAL.
- **FORMAT/AD is required.** CoGA takes the cfDNA alt-read count from the second AD value. Without AD no
  site counts as present in the cfDNA, and no fetal fraction can be estimated.
- The VAF is the AF value when given, otherwise alt reads divided by DP.
- A coverage track for the cfDNA sample, for the on-target coverage check.

---

## The fetal fraction

For a biallelic site, with `m` and `f` the maternal and fetal alt-allele fractions (0, ½ or 1), the
expected cfDNA allele fraction is `VAF = m · (1 − FF) + f · FF`.

**Which sites.** FF is estimated from category-7 sites: the mother is reference, the father carries the
alt allele, and the fetus inherited it, so the alt signal comes from the fetus alone and sits at
`FF / 2`. CoGA selects these sites as follows:

- autosomal;
- the father's genotype is heterozygous or homozygous alt, with a depth of 10 or more;
- cfDNA depth 20 or more, at least 3 alt reads, site QUAL 20 or more;
- cfDNA VAF above 0 and at most 25%, which separates the `FF / 2` sites from the maternal band near 50%.

**The estimate.** `FF = 2 × (sum of alt reads ÷ sum of depth)` over those sites, with a 95% confidence
interval (Wilson). The page shows the estimate, the interval and the number of sites.

- **Low-confidence FF** appears when there are fewer than 30 sites, or when the interval is wider than
  ±3 percentage points.
- With fewer than 5 sites no FF is estimated: the badge reads 0% with *Low-confidence FF*, and no
  fetal call can be trusted.
- If the estimate cannot be loaded, the badge says *Fetal fraction could not be loaded* and the counts
  read "—". Do not read the category calls until it loads (**Retry**).

---

## The eight categories

Once FF is known, each category has a fixed expected VAF, and each cfDNA variant is assigned to the
category that best explains its reads.

| Cat | Maternal / fetal state | Expected cfDNA VAF | Clinical meaning |
| --- | --- | --- | --- |
| 1 | De novo in the fetus (absent in both parents) | FF / 2 | Candidate de novo dominant variant |
| 2 | Mother het, not inherited | 50% − FF/2 | A maternal allele the fetus did not receive |
| 3 | Mother het, fetus het | 50% | Maternal allele transmitted, when the father is reference (see below) |
| 4 | Mother het, fetus hom-alt | 50% + FF/2 | Fetus homozygous: recessive risk |
| 5 | Mother hom-alt, fetus het | 100% − FF/2 | Fetus received a reference allele from the father |
| 6 | Mother and fetus hom-alt | 100% | Both homozygous (often a common variant) |
| 7 | Paternal allele transmitted (mother reference) | FF / 2 | Used for the FF estimate; the paternal hit of a possible compound pair |
| 8 | Father hom-alt, absent in cfDNA | ≈ 0 (expected FF/2) | Quality signal: an allele the fetus must carry, not seen |

Categories 1 and 7 have the same expected VAF. Only the father's genotype tells them apart: reference
means de novo, a carried allele means paternal transmission.

Category 3 means that the mother's allele was transmitted only when the father is reference. When the
father is hom-alt, the fetus has his alt allele and the mother's reference allele, so her alt allele
was **not** transmitted. When he is het, category 3 cannot say whose alt allele the fetus has. The
*Maternal dominant* preset and the Sample QC maternal-transmission check count every category-3 site
as inherited.

On the variant card, **Maternal** shows `hom_ref`, `het`, `hom` or `unknown`, and **Fetal** shows the
inheritance of the chosen category (for example `paternal_transmitted` or `maternal_inherited_het`).
`unknown` means CoGA withheld the fetal call.

### How a category is chosen

Each variant is scored against the candidate categories with a beta-binomial model of its alt reads
and depth, which allows for real sequencing noise. The most probable category wins; its probability is
the **confidence** (0 to 1), and the runner-up is kept.

The fetus has one allele from each parent, so the father's genotype limits the candidates to the fetal
states it allows. A reference father passes no alt allele; a hom-alt father always passes one.

| Site | Candidate categories |
| --- | --- |
| In the cfDNA, father reference | 1, 2, 3, 5 (the fetus has his reference allele) |
| In the cfDNA, father het | 2–7 (maternal or paternal) |
| In the cfDNA, father hom-alt | 3, 4, 6, 7 (the fetus has his alt allele) |
| In the cfDNA, no usable father call | 1–7, flagged `father_no_coverage` |
| Not in the cfDNA, father hom-alt | category 8, or no category (see the flags) |
| Not in the cfDNA, father het | no category: the paternal allele was not transmitted |

A site counts as present in the cfDNA from 3 alt reads. A father call is usable from a depth of 10:
a thinner call counts as no call, like a missing genotype. CoGA reads the father's genotype as it does
everywhere: a haploid `1` (how some callers write a man's X) is hom-alt, and a half call such as `./0`
is no call.

**What a de novo call rests on.** Category 1 needs at least 3 alt reads in the cfDNA and a father
called reference at a depth of 10 or more. It also has a low prior weight of 0.02 (1 in 50), so it
wins only on clearly stronger evidence than the alternatives. CoGA does **not** check that the VAF lies
within the FF interval: a site well below `FF / 2` can still be called de novo. Check the VAF against
`FF / 2` before you act on a de novo call.

### Flags

Flags appear as chips on the variant card.

| Flag | Meaning |
| --- | --- |
| `ambiguous` | The confidence is below 0.90: a neighbouring category is close. |
| `ff_low_confidence` | The fetal-fraction estimate is low-confidence. Set on every autosomal site. |
| `ff_too_low` | FF is below 1%. The maternal state is given, the fetal call is withheld (categories 2–6). |
| `father_no_coverage` | The father has no usable call at the site (no genotype, or a depth below 10), so de novo and paternal cannot be told apart. |
| `low_depth` | cfDNA depth below 20 at a site present in the cfDNA (variant list only; see below). |
| `false_negative` | Category 8: the father is hom-alt, the cfDNA depth was enough to expect at least 3 alt reads, and fewer than 3 were seen. |
| `undetectable_at_ff` | The father is hom-alt and the allele is absent, but too few alt reads were expected at this FF to see it. Not a quality failure. |
| `low_depth_dropout` | As above, but the cfDNA depth is below 20. |
| `no_alt_signal` | The allele is not present in the cfDNA (fewer than 3 alt reads), and the father does not carry it or has no usable call. |
| `sex_chromosome_unsupported` | chrX, chrY or the mitochondrion: not classified. |

---

## What can and cannot be resolved

The categories are not equally reliable.

- **Robust at any FF or depth:** de novo (category 1), paternal transmission (category 7 present or
  absent), and the coarse maternal state (reference, het, hom).
- **Limited by FF and depth:** whether the fetus inherited a *maternal* allele (category 2 versus 3
  versus 4, and 5 versus 6). This needs a shift of `FF / 2` around 50% to be resolved, which takes depth
  in the order of `1 / FF²` (hundreds of reads at FF ≈ 4%). Low depth lowers the confidence (watch for
  `ambiguous`); only an FF below 1% withholds the fetal call (`ff_too_low`).

Use the confidence and the flags to decide how much weight a single call deserves.

> **Out of scope.** A local CNV or aneuploidy breaks the `0, ½, 1` dosage assumption. The sex
> chromosomes are not classified. Fetal sex is estimated separately, by the Sample QC page.

---

## Inheritance presets

The **Inheritance** control in the NIPT filters reads the category assignment directly:

| Preset | Shows |
| --- | --- |
| De novo candidates | category 1 |
| Paternal dominant (transmitted) | category 7 |
| Maternal dominant (transmitted) | categories 3 and 4 — but picking the preset ticks only category 3. Tick category 4 as well, or category-4 variants are left out. |
| Recessive at-risk | every carrier variant in genes where the mother carries a variant (categories 2–6) and the father carries one (het or hom-alt genotype) |

**Recessive at-risk does not say that the fetus is affected.** It lists the carrier variants in genes
where both parents carry one. Read the fetal status off each variant's category: 4 or 6 means the fetus
is homozygous; a maternal 3 plus a paternal 7 in the same gene is a compound-heterozygous candidate;
2 is a maternal allele the fetus did not inherit; 8 is a paternal allele the fetus must carry but the
assay did not see.

The built-in filter presets combine these with the usual filters: **De novo** (category 1, HIGH or
MODERATE impact, gnomAD 1% or below, a ClinVar pathogenic record overriding the frequency) and
**Recessive (both parents carrier)** (Recessive at-risk with the same impact and frequency filters).

---

## Summary and variant list

The **Summary** (the FF badge, the filter funnel and the category counts) removes sites that fail the
quality filter (cfDNA depth below 20, or QUAL below 20) and sites on the artifact list, then estimates
FF and classifies what is left.

The **variant list** and the report classify against that same FF, and they leave artifact-list sites
out. The variant list also shows sites that fail the quality filter: a low-depth site is flagged
`low_depth`, and a site that fails on QUAL alone carries no flag. Its category counts can therefore be
higher than the Summary's.

---

## Artifact list

Recurrent artifacts are capture-specific, so each cfDNA assay has its own artifact list (the panel
recorded on the cfDNA sample, otherwise one shared list). Listed sites are removed from the Summary and
the variant list, and counted as *Artifact-filtered* in the funnel.

An administrator maintains the list through the API; there is no screen for it, and nothing is added
automatically. On request, the API seeds the list with candidates: variants carried by 5 or more cfDNA
samples of the same assay (samples marked as maternal-plasma cfDNA). It never lists:

- a common variant (a gnomAD or TopMed frequency above 5% recorded at import): the FF estimate relies
  on these sites;
- a variant with a ClinVar pathogenic or likely pathogenic record, or with a conflicting record, which
  may hold a pathogenic submission. A familial founder variant recurs in a disease-focused panel, and a
  listed variant is left out of every analysis of the assay.

A site with no population or ClinVar annotation is not protected, and a real recurrent variant that
ClinVar does not call pathogenic can still be seeded. Review a seeded list before use.

---

## Coverage

On-target coverage is the median depth of the cfDNA sample's coverage track over the target: the
selected gene panel or genes, otherwise the family's region of interest. A target region is flagged
when it has no coverage, a median below 20×, or less than 90% of its length covered.

NIPT has its own sample-integrity checks (paternity, fetal sex, parent sex and the category
distribution): see the [Sample-integrity QC reference](/docs/reference/sample-qc).
