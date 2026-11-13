# Sample-integrity QC — reference

The **Sample QC** page (the **Sample QC** button on the family page) checks that a family's samples are
who the pedigree says they are. Run it **before** you trust any interpretation. It catches sample swaps,
wrong relationships, wrong-sex labels and consanguinity — problems that quietly invalidate a variant
call or a segregation analysis. It does not measure contamination, but some of its results can point
to it (see *Sign-out and limits*).

How to read the page: [user guide](/docs), section *Sample-integrity QC*. This page holds the checks and
their thresholds.

---

## Why it is application-aware

CoGA serves several applications, each with its own notion of integrity. The page first works out the
application, then runs only the checks that fit it:

| Application | How CoGA recognises it | Checks |
| --- | --- | --- |
| **Long-read WGS family** | a pedigree with parent–child links | sex · relatedness · Mendelian errors |
| **Shallow-WGS PGT** | a member with the embryo role (imputed genotypes) | sex · **parentage** (embryos ↔ parents) · Mendelian errors |
| **Monogenic NIPT (cfDNA)** | the family was created as a Monogenic NIPT family | **paternity** · **fetal sex** · **parent sex** · **cfDNA category check** |
| **Carrier couple** | two members, no parent–child link | sex · confirmation that the two are unrelated |
| **Single targeted sample** | one sample | sex only |

Any other family shape gets sex, relatedness and Mendelian checks where the data allows.

The genotypes come from the family's own calls; failing those, from the imputed GLIMPSE2 calls; failing
both, from the first call set the family has.

### Which sites are read

The checks read a fixed sample of that call set's single-base (SNV) sites; an indel's genotype is
called with more error. A genotype with a quality (GQ) below **20** counts as a no-call: the checks read
only the calls the caller is at least 99% sure of. Unfiltered, a raw joint call set's low-depth
homozygous calls can make a parent and child look like opposite homozygotes at close to 1% of the sites. A
call set without a GQ (imputed GLIMPSE2 calls) is read as called.

- **Relatedness and Mendelian errors:** up to **90,000** sites spread over all 22 autosomes.
- **Sex:** up to **20,000** sites on chromosome X outside the pseudo-autosomal regions (the regions
  where a man has two copies). On an assembly other than GRCh37 or GRCh38 the whole X is read.

The sample must cover the whole genome. Siblings share both parental copies over about a quarter of the
genome, one over half and none over a quarter, in stretches tens of megabases long. Sites from only a
few such stretches measure only what the siblings share there, so two siblings could look like
duplicates or like second-degree relatives. The sample is pseudo-random but fixed: the same data always
gives the same sites, so a signed report holds the result the page showed.

---

## The checks

Each check returns **pass**, **warn**, **fail** or **not run**. The overall verdict is the worst of
them. A check that did not run confirmed nothing, so there it counts as a **warn**: the page never reads
*All sample-integrity checks passed* beside it, and a note names each sample whose sex could not be
checked. The overall verdict is **not run** only when no check could run at all.

### Sex

Genetic sex comes from the **heterozygosity rate on chromosome X**: a man (one X) has almost no X
heterozygotes, a woman many.

- 5% or less → **male**; 15% or more → **female**; in between → indeterminate (**warn**).
- At least **200** X sites are needed; with fewer the result is indeterminate (**warn**).
- A haploid call (`0` or `1`, as some callers write a man's X) counts as a homozygous site. A no-call
  or a half call (`./1`) is left out.
- A recorded sex that differs from the genetic sex is a **fail** — most often a sample swap or a
  mislabelled tube. No recorded sex is a **warn**.
- A sample with no called X site is sexed from its reads instead, if the pipeline gave a sex call (see
  *Sex from the reads*). Without one, its sex check is **not run**: its sex is indeterminate, and the
  overall verdict is at least **warn**.

#### Sex from the reads

A PGT family's genotypes are imputed for the autosomes only, so its samples have no X genotypes. The PGT
pipeline also runs ngs-bits *SampleGender*, which reads each sample's sex from the ratio of its reads on
chromosome Y to those on chromosome X. CoGA stores that call with the sample's sequencing QC when the
family is imported. For a sample without X genotypes, the sex check compares that call with the
recorded sex, graded as above: a match is a **pass**, a different sex a **fail**, and an indeterminate
call or no recorded sex a **warn**. The message says the sex was read from the reads, not the genotypes,
and quotes the ratio and the read counts. A sample with X genotypes is sexed from them, not from the
call.

An embryo's recorded sex comes from that same call: the pipeline's pedigree file gives each embryo the
sex ngs-bits read. So for an embryo the match confirms nothing independent. It is a **pass**, and its
message says so. The parents' recorded sex comes from the pedigree, so theirs is a real comparison. An
index missing from the pipeline's pedigree file is added at discovery with the sex ngs-bits read, unless
the manifest gives another sex before the import. Its match then rests on the same call too, although
its message does not say so.

### Relatedness against the pedigree

Relatedness uses the **KING-robust kinship** coefficient (φ) with the **IBS0 rate** (the share of sites
where the two samples are opposite homozygotes). The kinship bands:

| Kinship φ | Inferred relationship |
| --- | --- |
| above 0.354 | duplicate or identical twin |
| 0.177 – 0.354 | first degree: **parent–child** when IBS0 is below the family's cut (below), otherwise **sibling** |
| 0.0884 – 0.177 | second degree |
| 0.0442 – 0.0884 | third degree |
| below 0.0442 | unrelated |

Over the whole genome, siblings' kinship stays close to 0.25, like a parent and child's. IBS0 tells
them apart. A parent and child share a copy everywhere, so their IBS0 comes from genotyping errors
alone. Siblings share no parental copy over about a quarter of the genome, and there they are opposite
homozygotes as often as two unrelated people. Their IBS0 is about a quarter of an unrelated pair's on
the same sites, and that depends on the call set: on a short-read genome about 8% for unrelated people,
so about 2% for siblings.

The family's parents are that unrelated pair. A first-degree pair reads as **parent–child** when its
IBS0 is below **an eighth of the parents' IBS0**, half what siblings are expected to show. Each
first-degree pair's message gives the cut, for example *IBS0 0.30% (parent-child below 0.99%, an eighth
of the parents' 7.90%)*. With more than one pair of parents in the pedigree, the median of their IBS0
is used. When there is no such pair, the cut is a fixed **0.8%**: only one parent was sequenced, or the
parents look related (consanguinity), or they share too few sites. A sibling pair below the cut is
inferred as parent–child, and a recorded sibling pair accepts that as first degree.

Each pair's inferred relationship is compared with the pedigree:

- A recorded **parent–child** pair that is not inferred as parent–child → **fail** (swap or wrong
  parent).
- Recorded **siblings** that are not first degree → **fail**.
- A pair expected to be **unrelated** that looks second or third degree → **warn**; first degree or
  duplicate → **fail** (duplicate, swap or consanguinity).

A pair needs at least **1,000** shared sites; with fewer the result is a **warn**, not a false alarm.

**Parents and consanguinity.** A child's two parents are expected to be unrelated. The matrix always
shows that pair: it confirms *parents unrelated — no consanguinity*, or flags them when they look
related. Other expected-unrelated pairs that pass are hidden to keep the matrix readable.

**A call set made one sample at a time.** The long-read pipeline calls a couple screened for
carriership one partner at a time, with no joint VCF. Where one partner's file has a record at a site
and the other partner's has none, the checks read the other partner as reference there, as a joint VCF
calls a covered site. A site without reads is read the same way, which lowers the kinship: on
low-coverage data an *unrelated* result does not exclude a relationship. The page then says so in a
note, with the share of the sites read at which each partner had no record, and the overall verdict is
at least **warn**.

### Mendelian errors

For each child with genotyped parents, the **Mendelian-error rate** is the share of sites where the
child's genotype cannot be made from one allele of each parent. With one genotyped parent, an error is a
child that shares no allele with that parent.

- 5% or more → **fail**; 2% or more → **warn**; otherwise **pass**.
- At least **200** informative sites are needed; with fewer the result is a **warn**.

A clear rate points to a swap, a wrong parent, contamination or genotyping noise. The **Review ROI
markers** page highlights impossible genotypes by the same logic.

---

## Monogenic NIPT checks

In NIPT there is no clean fetal genome: the mother's sample is a cfDNA mixture (maternal DNA with a
small fetal fraction). CoGA models the case as a trio backed by the father's germline sample and the
cfDNA. So these checks read the cfDNA analysis (the father's alleles in the cfDNA, and the categories),
not genotype relatedness. The analysis is explained in the
[Monogenic NIPT reference](/docs/reference/monogenic-nipt).

### Paternity (the father's alleles in the cfDNA)

A fetus carries every allele its father is homozygous for, and half of those he is heterozygous for.
CoGA counts the father's alleles at sites where the mother does not carry them (no cfDNA call, or one
at 25% or less) and the cfDNA depth leaves at least 10 expected alt reads, so a transmitted allele is
almost never missed. Only usable father calls count: a genotype read off his allele depths at a depth
of 20 or more. An allele counts as seen when the probability that the fetus inherited it is 50% or
more.

- **His homozygous alleles** (all expected): 90% or more seen → **pass**; below 80% → **fail**; between
  → **warn**. Needs at least **20** such sites.
- **His heterozygous alleles** (half expected): 35–65% seen → **pass**; outside 25–75% → **fail**;
  between → **warn**. Needs at least **50** such sites.
- The verdict is the worse of the two. Too few sites of both kinds → **warn** (unverifiable).

A fail means another father, or a sample or file mix-up (for example the wrong paternal VCF).

### Fetal sex (paternal X transmission)

The father passes his X to a daughter and his Y to a son. So an allele on the father's X (outside the
pseudo-autosomal regions) shows in the cfDNA at about `FF / 2` for a girl and is absent for a boy. An
allele at a maternal level (VAF 30% or more) is the mother's and is ignored.

- At least **8** informative sites are needed; otherwise indeterminate (**warn**).
- 80% or more of the father's X alleles seen → **female**; 10% or less → **male**. In between is
  indeterminate (**warn**): the alleles are not this fetus's father's, or the calls are noisy.
- Only sites where the father is hom-alt count (a man's X), at a depth of 20 or more.

The NIPT page adds a second, independent signal, the plasma's chrY coverage, and shows the call of the
two together.

### Parent sex

The father and the cfDNA sample are sexed from X heterozygosity, as above. The cfDNA is mostly maternal
DNA, so it should read female. Because it is a mixture, this catches a gross problem (plasma not from a
woman) but can be indeterminate on sparse data.

No relatedness check covers either sample, so a parent with an indeterminate sex stops sign-out until
someone records a reason. If their genotypes cannot be loaded, both parents read indeterminate (**not
run**), and the page notes that the parents' sex could not be checked and names them.

### cfDNA category check

A sanity check on the shape of the category counts:

- **Excess de novo (category 1):** de novo variants are rare. 15% or more of the classified sites →
  **fail**; 5% or more → **warn** (artifacts or contamination).
- **Maternal transmission:** about half of the mother's heterozygous alleles reach the fetus, so
  `(cat 3 + cat 4) ÷ (cat 2 + cat 3 + cat 4)` should be near 50%. 30 or more percentage points off →
  **fail** (wrong mother or a sample problem); 15 or more → **warn**. Needs at least **20**
  maternal-het sites. The margins are wide because detecting inherited alleles depends on the fetal
  fraction.
- The category-8 count is shown alongside.

---

## Reading the page

**Pedigree with QC overlay.** Each assessed person gets a ring and a badge:

| Verdict | Ring | Badge |
| --- | --- | --- |
| Pass | thin, solid, green | ✓ |
| Warn | dashed, amber | ! |
| Fail | thick, solid, red | ✕ |

The ring shows the worst of the person's checks, and a check that did not run counts as a warning. So a
parent whose relatedness passed but whose sex could not be checked gets the amber ring, not the green
one. A person without a ring was not assessed. Line and badge tell the verdicts apart, not only colour.
The symbol keeps its clinical meaning (black for affected, the carrier half-fill in its carrier
colour). Hover a symbol for the reason.

**Per-sample table.** Recorded sex against genetic sex, and the Mendelian-error rate, coloured by
status. The last column holds the person's verdict, rolled up as the ring is. Hover a cell for the
explanation. The genetic sex carries a mark for its verdict:

| Verdict | Mark | Meaning |
| --- | --- | --- |
| Pass | ✓, green | matches the recorded sex |
| Warn | !, amber | not confirmed: indeterminate, or no recorded sex to compare with |
| Fail | ✗, red | contradicts the recorded sex — a possible swap or mislabelled tube |
| Not run | ?, grey | not checked: no X genotypes, and no sex call from the reads |

**Relatedness matrix.** A sample × sample grid (lower half only; it is symmetric). Each cell shows the
inferred relationship, the kinship φ and the IBS0. A pair that contradicts the pedigree — including
parents who look related — is outlined: red for a fail, amber for a warning (for example parents who
look second or third degree, or a pair with too few shared sites).

**NIPT cards.** For a cfDNA family the page adds *Paternity*, *Fetal sex* and *cfDNA category QC*
cards, and the parent-sex rows appear in the per-sample table.

**Sequencing & embryo QC.** The same per-sample table holds, under its own heading, the sequencing QC
each sample's pipeline reported, one column per measure:

- the mean depth and Qualimap's alignment metrics;
- for a PGT embryo, the numbers the PGT pipeline's dashboard shows: its allele drop-out (**ADO**) and
  drop-in (**ADI**), from Picard (`picard/<family>_ADO_ADI.csv`), and its Mendelian concordance with
  its parents before and after imputation, from RTG Tools (`rtgtools/<family>_cohort_concordance.csv`
  and `_imputed_concordance.csv`). They are read when the package's `qc` dataset is imported.

Each value is judged against the family's cut-offs (**Admin → Sequencing QC thresholds**). A value
outside them shows its verdict beside it (*32.1% · Warning*). The last column (**Seq. QC**) holds the
sample's worst verdict, as the **Seq. QC** chip of the family page does. A value without a cut-off is
shown, not judged (*Not run*); the text above the table names those measures. These are not
sample-integrity checks: they do not change the sample's ring, its status or the page's overall
verdict, and do not stop sign-out. The signed report records every value with its verdict.

---

## Sign-out and limits

- **Sample QC gates sign-out.** Sign-out stops until someone acknowledges with a reason (see the
  [traceability reference](/docs/reference/clinical-traceability)) when Sample QC failed, or when a
  check of the pedigree or of a sample's identity could not run for lack of data (such as a NIPT
  parent's sex, see *Parent sex*).
- **Missing data gives a warning, not an error.** If the genotypes or the cfDNA analysis cannot be
  loaded, the page says so and shows what it could compute. A check that did not run makes the overall
  verdict a warning, which the signed report records. It stops sign-out only in the cases of the point
  above.
- **A screening check, not an identity test.** Relatedness and Mendelian errors read up to 90,000 sites
  from all autosomes, sex up to 20,000 X sites (see *Which sites are read*). A call set that covers
  only part of the genome (a few chromosomes or a region) cannot give a genome-wide sample; there,
  siblings can read as more or less related than first degree. When the sampled sites lie on fewer
  than 20 of the 22 autosomes, the checks still run and the report warns: *The callset covers part of
  the genome: its sites lie on N of the 22 autosomes, so the checks read only that part.* The warning
  makes the verdict a warning, and the signed report records it.
- **Contamination is not measured.** No check estimates how much of a sample's DNA comes from someone
  else, so a pass does not rule contamination out. Some results fit it, among other causes:
  - a child's Mendelian-error rate at warn or fail (*Mendelian errors*);
  - heterozygous calls on a man's X outside the pseudo-autosomal regions, which raise his X
    heterozygosity so that his sex can read indeterminate, or female against the record (*Sex*);
  - in a NIPT family, an excess of de novo sites (*cfDNA category check*).

  The **mtDNA analysis** page shows a mitochondrial contamination estimate where one was imported with
  the sample, judged against the lab's limits (**Sequencing QC thresholds**).
