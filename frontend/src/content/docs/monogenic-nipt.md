# Monogenic NIPT (cell-free DNA) — reference

Monogenic NIPT screens a pregnancy for single-gene disorders from **cell-free DNA (cfDNA) in maternal
plasma**, together with a sample from the father. The plasma cfDNA is mostly maternal DNA with a small
**fetal fraction (FF)**. The fetus is never sequenced: CoGA infers its genotype from how far each cfDNA
allele fraction (VAF) moves away from the maternal values of 0%, 50% and 100%.

How to set up a NIPT family and use the page is in the [user guide](/docs), section *Monogenic NIPT*.
This page holds the model and the rules. The model's constants are those of the lab's R validation of
NIPT-M (version 0.5.1, six families with trio exomes); the page's quality panel names it.

> **Screening, not diagnosis.** A NIPT call is decision support derived from cfDNA. Confirm it with an
> invasive diagnostic test before clinical use.

---

## A two-sample trio

CoGA models the case as a trio backed by two physical samples.

| Family member | Sample | What CoGA sees |
| --- | --- | --- |
| Father | germline DNA | observed genotypes |
| Mother | **cfDNA** from plasma (maternal plus fetal DNA) | observed allele fractions |
| Fetus | none | **inferred**, never observed |

A family is a NIPT family when it was created with the Monogenic NIPT option in Family Builder, or
imported with a package whose manifest says `analysis_type: monogenic_nipt`. CoGA takes the sample
marked as maternal-plasma cfDNA as the mother's sample (the mother's own sample if none is marked).

### What the input must contain

CoGA takes either input. The analysis is the same for both.

- **Two single-sample VCFs**, as the NIPT-M pipeline writes them: the plasma's calls (Mutect2 in
  tumour-only mode) and the father's. Package Import finds a pair by the sample names of the PED and
  imports the plasma first. Each call keeps its own FILTER values and the caller's metrics (TLOD, FS,
  mapping quality, mismatches, repeat units and so on), which the quality filter and the de novo
  triage read.
- **One combined VCF** with a father column and a cfDNA column, genotyped jointly. The quality is the
  site's QUAL.

Either way the calls need **allele depths (FORMAT/AD)**. The VAF is the alt reads divided by all the
reads at the site, never the caller's modelled AF (used only when there is no AD). A record that a
multi-allelic site was split into keeps only its own allele's reads, so its depth is restored from
all the alleles of the site.

The father's single-sample VCF holds over a million low-level calls, almost all noise. On import CoGA
keeps a paternal record when one of its calls reaches 15% alt reads (below the 20% of a heterozygous
call, so no genotype is lost) or when it lies on a position where the plasma has a call. It drops the
rest and reports how many.

**The per-target coverage tables** (`coverage_<sample>.txt`, one row per capture target with its mean,
median and covered share) belong with the VCFs. The plasma's table drives the coverage check and the
sex profile, and it gives the plasma's depth where its VCF has no call, which tells whether a missing
paternal allele would have shown (see *Paternal alleles*). The father's table gives his depth where his
VCF has none. Without the tables CoGA falls back on a coverage track over the family's region of
interest, and an allele the plasma does not show cannot be read.

### The father's genotype

CoGA reads the father's genotype from his allele depths, as the R pipeline does. Mutect2 writes `0/1`
for every call, so the GT is used only when a call has no AD.

| Class | Rule |
| --- | --- |
| het | 20% or more alt reads, below 80% |
| hom-alt | 80% or more alt reads |
| low-level signal | below 20% alt reads (noise, mosaicism, or an artefact both samples share) |
| too thin to class | fewer than 20 reads, fewer than 5 alt reads, or a quality (TLOD) below 20 |
| reference | no alt read, at 20 reads or more |
| no call | his single-sample VCF has no call at the site: read as reference, unless his coverage there is below 20× |
| no data | a combined VCF's missing genotype |

A low-level signal, a call too thin to class and no data all count as **no usable father call**: at
such a site a de novo allele and a paternal one cannot be told apart.

### One allele, two representations

Mutect2 writes adjacent phased SNVs as one multi-nucleotide variant (MNV). The plasma and the father
are called apart, so one VCF can hold an MNV where the other holds its SNVs, or another MNV. When a
record lacks the other sample's call, CoGA looks for it base by base:

| Flag | Meaning |
| --- | --- |
| `father_call_other_representation` | The father's calls carry every base of the plasma's allele (an MNV holding this SNV, say). His genotype is read off them. |
| `father_call_overlaps` | They carry some of its bases. He stays without a call; the de novo triage counts it as a paternal signal. |
| `plasma_call_other_representation` | The plasma's calls carry every base of the father's allele. Its reads are theirs. Those calls are sites of their own, so this one does not count again toward the fetal fraction, paternity or the fetal sex. |
| `plasma_call_overlaps` | They carry some of its bases. Whether the fetus inherited the allele is uncertain: it is not paternity evidence, and the paternal view always lists it. |

An indel written differently in the two files (trimmed or aligned another way) is not matched.

---

## The quality filter

A cfDNA call passes the quality filter of the R validation's selected setting when it has:

- a quality of 20 or more (Mutect2's TLOD; otherwise the call's QUAL; for a combined VCF the site's QUAL);
- at least 5 alt reads and an allele fraction of 1% or more;
- a strand-bias score (FS) of 20 or less;
- once the fetal fraction is known, an allele fraction of at least a quarter of `FF / 2`.

A value the call does not have passes. The funnel on the NIPT page counts the cfDNA calls that fail,
and the quality panel lists why. A failed call is left out of the fetal fraction, the category counts
and the de novo candidates. The variant list still shows it, flagged `quality:` with the reason
(`low_quality`, `few_alt_reads`, `low_vaf`, `strand_bias`, `below_fetal_vaf_floor`).

---

## The fetal fraction

For a biallelic site, with `m` and `f` the maternal and fetal alt-allele fractions (0, ½ or 1), the
expected cfDNA allele fraction is `VAF = m · (1 − FF) + f · FF`.

**Which sites.** FF is estimated from category-7 sites: the mother is reference, the father carries the
alt allele, and the fetus inherited it, so the alt signal comes from the fetus alone and sits at
`FF / 2`. CoGA selects these sites as follows:

- autosomal, with a cfDNA call of the site's own;
- the father's genotype is het or hom-alt, at a depth of 20 or more;
- cfDNA depth 20 or more, and the call passes the quality filter;
- cfDNA VAF from 0.5% to 25%. 25% is midway between `FF / 2` and the `50% − FF / 2` of a maternal
  allele the fetus did not inherit, so a site both parents carry stays out even at a high FF.

**The estimate.** `FF = 2 × (sum of alt reads ÷ sum of depth)` over those sites, with a 95% confidence
interval (Wilson). The quality panel also gives the per-site median (the R pipeline's estimate), and
the 5th and 95th percentiles of the sites' allele fractions, which bound the de novo window.

- **Low-confidence FF** appears when there are fewer than 30 sites, or when the interval is wider than
  ±3 percentage points.
- With fewer than 5 sites no FF is estimated: the badge reads 0% with *Low-confidence FF*, and no
  fetal call can be trusted.
- If the estimate cannot be loaded, the badge says *Fetal fraction could not be loaded* and the counts
  read "—". Do not read the category calls until it loads (**Retry**).

With the per-target coverage, the plasma's chrY depth gives an indicative second estimate for a male
fetus: `2 × (chrY ÷ autosomal depth) ÷ 1.2`. It rests on the panel's few chrY targets.

---

## The eight categories

Once FF is known, each category has a fixed expected VAF, and each cfDNA variant is assigned to the
category that best explains its reads.

| Cat | Maternal / fetal state | Expected cfDNA VAF | Clinical meaning |
| --- | --- | --- | --- |
| 1 | De novo in the fetus (absent in both parents) | FF / 2 | Candidate de novo dominant variant |
| 2 | Mother het, not inherited | 50% − FF/2 | A maternal allele the fetus did not receive |
| 3 | Mother het, fetus het | 50% | Whose allele the fetus has depends on the father (see below) |
| 4 | Mother het, fetus hom-alt | 50% + FF/2 | Fetus homozygous: recessive risk |
| 5 | Mother hom-alt, fetus het | 100% − FF/2 | Fetus received a reference allele from the father |
| 6 | Mother and fetus hom-alt | 100% | Both homozygous (often a common variant) |
| 7 | Paternal allele transmitted (mother reference) | FF / 2 | Used for the FF estimate |
| 8 | Father hom-alt, absent in cfDNA | ≈ 0 (expected FF/2) | Quality signal: an allele the fetus must carry, not seen |

Categories 1 and 7 have the same expected VAF. Only the father's genotype tells them apart: reference
means de novo, a carried allele means paternal transmission.

Category 3 means the mother's allele was transmitted only when the father is reference. When he is
hom-alt, the fetus has his alt allele and the mother's reference allele. When he is het, either
parent's alt allele can make the fetus het. The probabilities below take this into account; the
category alone does not.

On the variant card, **Maternal** shows `hom_ref`, `het`, `hom` or `unknown`, and **Fetal** shows the
inheritance of the chosen category (for example `paternal_transmitted` or `maternal_inherited_het`).
`unknown` means CoGA withheld the fetal call.

### How a category is chosen

Each variant is scored against the candidate categories with a beta-binomial model of its alt reads
and depth. The model's overdispersion (0.0037) and the reference bias of a maternal heterozygous site
(its allele fraction sits 1.1 percentage points below the expected value) were fitted on the R
validation. The most probable category wins; its probability is the **confidence** (0 to 1), and the
runner-up is kept.

The fetus has one allele from each parent, so the father's genotype limits the candidates to the fetal
states it allows, with Mendelian prior weights. A reference father passes no alt allele; a hom-alt
father always passes one.

| Site | Candidate categories |
| --- | --- |
| In the cfDNA, father reference (or no call) | 1, 2, 3, 5 (the fetus has his reference allele) |
| In the cfDNA, father het | 2–7 (maternal or paternal) |
| In the cfDNA, father hom-alt | 3, 4, 6, 7 (the fetus has his alt allele) |
| In the cfDNA, no usable father call | 1–7, flagged `father_no_coverage` |
| Not in the cfDNA, father hom-alt | category 8, or no category (see the flags) |
| Not in the cfDNA, father het | no category: the paternal allele was not transmitted, or not seen |

A site counts as present in the cfDNA from 5 alt reads. Category 1 has a low prior weight of 0.02
(1 in 50), so it wins only on clearly stronger evidence than the alternatives.

### The fetal inheritance probabilities

Next to the category, each variant gives the probability of what matters clinically:

- **Paternal allele inherited.** At a site the mother does not carry, a transmitted allele sits at
  `FF / 2` and an untransmitted one at the noise level (0.2%). The prior is ½ for a het father and 0.99
  for a hom-alt one. Where the plasma has no call, its depth comes from the target coverage: no alt
  read at a depth where `FF / 2` would give many is strong evidence the allele was not inherited.
- **Maternal allele inherited.** At a site the mother carries, from her band of categories alone
  (2, 3 and 4): category 3 and 4 beside a reference father; category 4 and half of category 3 beside a
  het father; category 4 out of 3 and 4 beside a hom-alt father. A hom-alt mother always passes her
  allele.
- **Fetus homozygous.** The probability of category 4 (or 6).

The maternal probability is given at every site the mother carries, as in the R validation. It needs
depth: the shift of `FF / 2` around 50% takes depth in the order of `1 / FF²` to resolve. The R
validation's maternal model was 95% accurate on SNVs and 74% on indels, so an indel carries the flag
`maternal_inference_indel`. A site whose allele fraction fits no fetal genotype (more than 4 standard
deviations off; site-specific capture bias or a copy-number change) carries `allele_balance_outlier`.

### Flags

Flags appear as chips on the variant card.

| Flag | Meaning |
| --- | --- |
| `ambiguous` | The confidence is below 0.90: a neighbouring category is close. |
| `ff_low_confidence` | The fetal-fraction estimate is low-confidence. Set on every autosomal site. |
| `ff_too_low` | FF is below 1%. The maternal state is given, the fetal call is withheld (categories 2–6). |
| `father_no_call` | The father's VCF has no call at the site (read as reference). |
| `father_low_level_signal` | The father has a call below 20% alt reads (not a genotype). |
| `father_no_coverage` | The father has no usable call at the site, so de novo and paternal cannot be told apart. |
| `low_depth` | cfDNA depth below 20 at a site present in the cfDNA. |
| `quality:…` | The cfDNA call fails the quality filter, with the reason. |
| `few_alt_reads` | Fewer than 5 alt reads at a paternal allele where few were expected: read as transmitted. |
| `false_negative` | Category 8: the father is hom-alt, the cfDNA depth was enough to expect at least 3 alt reads, and the allele was not seen. |
| `undetectable_at_ff` | The father carries the allele and it is absent, but too few alt reads were expected at this FF to see it. Not a quality failure. |
| `low_depth_dropout` | As above, but the cfDNA depth is below 20. |
| `no_plasma_depth` | The father carries the allele, the plasma has no call, and its depth there is unknown (no target covers the site). |
| `no_alt_signal` | The allele is not present in the cfDNA, and the father does not carry it or has no usable call. |
| `maternal_inference_indel`, `allele_balance_outlier` | See the maternal probability above. |
| `father_call_…`, `plasma_call_…` | The other sample's call was looked up in another representation (see above). |
| `artifact_list_protected` | The site is on the assay's artifact list, but the family's own annotation marks it common or ClinVar-pathogenic, so it stays in (see *Artifact list*). |
| `sex_chromosome_unsupported` | chrX, chrY or the mitochondrion: not classified. |

---

## The views

The **Inheritance** control in the NIPT filters picks one of four views. Each works on the variants that
match the other filters, so set the gene panel, consequence and frequency filters first.

### De novo in the fetus

A candidate is a cfDNA call of the site's own that passes the quality filter, on an autosome, where the
father has no supported call (no call, reference, a low-level signal, too thin, or no data). CoGA then
triages it as the R pipeline does.

**The fetal window.** A fetal allele the mother lacks sits where the paternal alleles the fetus
inherited sit. The strict window runs from the 5th to the 95th percentile of the FF sites' allele
fractions (at most 35%); the loose window is a quarter wider on each side (at least 0.5%). A call above
the window is a maternal allele, one below it noise: both are left out (*outside the fetal window*).

**The score** adds up:

| Part | Points |
| --- | --- |
| Window | strict 8, loose 6 |
| Annotation | HIGH impact +4, MODERATE +2; SpliceAI 0.2 or more +2; novel (no rsID) +1; population frequency 0.1% or less, or none, +1; above 1% −3 |
| Technical, +1 each when met and −1 when not | caller FILTER PASS; mapping quality of the alt reads 55 or more; strand bias FS 10 or less; 2 mismatches or fewer per read; 2 repeat units or fewer; no other cfDNA sample of the assay carries it |
| Paternal signal | −2 when the father has a low-level, thin or missing call, or calls part of the allele |
| Variant class | SNV +1, indel −1, MNV −2; an indel in a repeat of 3 units or more a further −2 |

**The priority.** *High*: in the strict window, a score of 10 or more, an SNV or an indel not in a
repeat, and no paternal signal. *Medium*: a score of 7 or more. *Low*: the rest in the window. A
candidate in the window that a cfDNA sample of another family of the assay carries is *recurrent in
other samples* and left out, as the R pipeline does. That sample's call must have at least 5 alt
reads and 1% of the reads, so a few reads crossed over between the samples of a run do not count.
A candidate that a ClinVar record may call pathogenic is never left out this way: a de novo hotspot
recurs between pregnancies tested on one panel. **List de novo candidates of priority** sets the
lowest priority listed. Candidates are ranked by score.

Confirm a candidate's absence in the father and its allele fraction in the reads before acting on it.

### Paternal, inherited by the fetus

The father's alleles (het or hom-alt) that the mother does not carry: no plasma call, or a call at 25%
or less. Each shows the probability that the fetus inherited it. By default only the alleles inherited
with a probability of 50% or more are listed; **Also list the alleles the fetus did not inherit** adds
the rest, for example to see that a paternal pathogenic allele was *not* inherited. An allele the plasma
calls in part in another representation is always listed.

### Maternal, inherited by the fetus

The mother's alleles (categories 2 to 6), with the probability that the fetus inherited each. By default
those inherited with a probability of 50% or more; the same check box adds the rest.

### Recessive: both parents carriers

The genes where the mother and the father are each **heterozygous** for an allele among the variants
that match. A parent homozygous for an allele is not a carrier: for a recessive disease that parent
would be affected, and mostly the variant is common. The fetus then always inherits that parent's
allele, so read the other parent's allele in the paternal or maternal view.

The table **Recessive fetal risk by gene** gives, per gene, every maternal and paternal allele with the
probability that the fetus inherited it, and the **fetal risk**: the probability that it inherited a
maternal and a paternal allele.

- For a maternal allele and a paternal allele at different sites: the product of the two probabilities.
- For a site both parents carry: the probability that the fetus is homozygous for it.
- The gene's risk is the highest pair, which the table highlights. A carrier couple's prior is 25%.
- An inheritance the cfDNA could not tell is read at its 50% prior, and the table says so.

The risk assumes both alleles are pathogenic and, for two sites, that the maternal and paternal alleles
are in trans. An allele without any population frequency is noted: the frequency filters let it
through. An MNV never has one (gnomAD lists its SNVs) and is often a common haplotype. The variant list
below the table holds every carrier variant of the listed genes.

### Built-in presets

The presets combine the views with the usual filters: **De novo** (the de novo view, HIGH or MODERATE
impact, gnomAD 1% or below, a ClinVar pathogenic record overriding the frequency), **Paternal, inherited**
(the paternal view with the same filters) and **Recessive (both parents carrier)** (the recessive view
with the same filters).

---

## What can and cannot be resolved

The calls are not equally reliable.

- **Robust at any FF or depth:** a de novo allele and a paternal allele (present at `FF / 2` or absent),
  and the coarse maternal state (reference, het, hom).
- **Limited by FF and depth:** whether the fetus inherited a *maternal* allele (category 2 versus 3
  versus 4, and 5 versus 6). Watch the probability and the confidence; only an FF below 1% withholds
  the fetal call (`ff_too_low`).

> **Out of scope.** A local CNV or aneuploidy breaks the `0, ½, 1` dosage assumption. The sex
> chromosomes are not classified; the fetal sex is estimated apart (see *Quality checks*).

---

## Quality checks

The NIPT page opens with the checks a reader confirms before reading any variant.

- **Fetal fraction**, with its interval, the per-site median, the chrY estimate and the de novo window.
- **Fetal sex**, from two independent signals. *Paternal X*: at the father's X alleles (outside the
  pseudo-autosomal regions, where he is hom-alt and the plasma's allele fraction is below 30%), a
  daughter shows the allele and a son does not. From at least 8 informative sites, 80% or more seen is
  female and 10% or less male; a share in between is indeterminate: the alleles are not this fetus's
  father's, or the calls are noisy. *chrY coverage*: a chrY signal above the noise (2% of the autosomal
  depth) means a male fetus; none, at an FF of 4% or more, a female one. The call is the signal(s)
  that tell, and *discordant* when they disagree.
- **Paternity.** The father's alleles that the fetus must or may carry, at sites where the mother does
  not carry them and the plasma depth leaves at least 10 expected alt reads. Every allele of a hom-alt
  father must show: 90% or more seen passes, below 80% fails. Half of a het father's alleles show:
  35–65% seen passes, outside 25–75% fails. A check needs 20 hom-alt or 50 het sites. A fail means
  another father, or a sample or file mix-up: do not read the variants.
- **Maternal plasma sample**, from the chrX and chrY depth: female plasma passes; a chrY near the
  autosomal level with a low chrX is male DNA, which cannot be maternal plasma (fail).
- **Target coverage** and the **cfDNA quality filter** (see *Coverage*, and above).

---

## Summary and variant list

The **Summary** (the FF badge, the filter funnel and the category counts) removes the cfDNA calls that
fail the quality filter and the sites on the artifact list, then estimates FF and classifies what is
left. A paternal allele without a cfDNA call is counted apart (*paternal only*).

The **variant list** and the report classify against that same FF, and they leave artifact-list sites
out. The variant list also shows sites that fail the quality filter, flagged with the reason, so its
category counts can be higher than the Summary's.

The variant list classifies the first 5,000 variants of its search, in genomic order. When more
match, the page says so: the list stops part-way through the genome, and its count is a lower bound.
Narrow the search with a gene panel, a gene or a region.

---

## The report

The report uses only the gene panel and the gene of the NIPT page's search. Its **Scope** section names
them, with the panel's version, and says that no other filter of the page applies: not the categories,
the inheritance view, the confidence, the regions, or the frequency and consequence filters. With both
a panel and a gene, a variant is listed when it matches both. Artifact-list sites are left out, and sites
that fail the quality filter are listed with the reason, as in the variant list. It states the fetal
fraction, the quality checks (fetal sex, paternity, the plasma sample) and the coverage of the targets
in scope.

A report lists at most 500 candidates, the first ones in genomic order. When its scope holds more, or
more variants match than the variant list classifies, the report says so on screen, above the list and
at the top of a printout: how many it lists of how many, where the list stops, and why. Each group then
counts only the candidates it lists. Narrow the scope with a gene panel or a gene, then open the report
again.

---

## Artifact list

Recurrent artifacts are capture-specific, so each cfDNA assay has its own artifact list (the panel
recorded on the cfDNA sample, otherwise one shared list). Listed sites are removed from the Summary and
the variant list, and counted as *Artifact-filtered* in the funnel.

An administrator maintains the list through the API; there is no screen for it. Two ways fill it:

- **Import the R pipeline's recurrent list** (`recurrent_cfdna_artifact_filter_*.tsv`): the rows it marks
  `filter_as_recurrent_artifact`, each with the number of cfDNA families that carry it.
- **Seed it from CoGA's own cfDNA samples:** variants carried by 5 or more cfDNA samples of the same
  assay (samples marked as maternal-plasma cfDNA).

Neither ever lists:

- a common variant (a population frequency above 5% recorded at import): the FF estimate relies on
  these sites;
- a variant with a ClinVar pathogenic or likely pathogenic record, or with a conflicting record, which
  may hold a pathogenic submission. A familial founder variant recurs in a disease-focused panel, and a
  listed variant is left out of every analysis of the assay.

The protections are checked on the annotation of the families on the assembly when an allele is
listed, so an allele no family carried then is listed unchecked. Each family's analysis therefore
checks its listed alleles again with its own annotation: one it marks common or ClinVar-pathogenic
stays in, flagged `artifact_list_protected`. A site with no population or ClinVar annotation is not
protected, and a real recurrent variant that ClinVar does not call pathogenic can still be listed.
Review the list before use. Every change to the list is a clinical audit event.

---

## Coverage

With the per-target coverage table, the coverage check reads the plasma's capture targets: in the
selected gene panel or genes, otherwise every target. A target is **weak** when its mean depth is below
300× or a base of it has no coverage; a fetal variant there can be missed. The panel reports how many
targets are below 1000× as well, without flagging them. The coverage card lists the genes with a weak
target, and a selected gene that is not a target at all.

Without the table, on-target coverage is the median depth of the cfDNA sample's coverage track over the
selected genes, otherwise the family's region of interest. A region is flagged when it has no coverage,
a median below 20×, or less than 90% of its length covered.

NIPT has its own sample-integrity checks on the Sample QC page as well (paternity, fetal sex, parent
sex and the category distribution): see the [Sample-integrity QC reference](/docs/reference/sample-qc).
