# Sample-integrity QC — reference

The **Sample QC** page (the **Sample QC** button on the family page) checks automatically that a
family's samples are who the pedigree says they are. Run it **before** you trust any interpretation. It
catches sample swaps, wrong relationships, wrong-sex labels, contamination and consanguinity — problems
that quietly invalidate a variant call or a segregation analysis.

How to read the page is in the [user guide](/docs), section *Sample-integrity QC*. This page holds the
checks and their thresholds.

---

## Why it is application-aware

CoGA serves several applications, and each has its own notion of integrity. The page first works out
the application, then runs only the checks that make sense for it:

| Application | How CoGA recognises it | Checks |
| --- | --- | --- |
| **Long-read WGS family** | a pedigree with parent–child links | sex · relatedness · Mendelian errors |
| **Shallow-WGS PGT** | a member with the embryo role (imputed genotypes) | sex · **parentage** (embryos ↔ parents) · Mendelian errors |
| **Monogenic NIPT (cfDNA)** | the family was created as a Monogenic NIPT family | **paternity** · **fetal sex** · **parent sex** · **cfDNA category check** |
| **Carrier couple** | two members, no parent–child link | sex · confirmation that the two are unrelated |
| **Single targeted sample** | one sample | sex only |

Any other family shape gets sex, relatedness and Mendelian checks where the data allows.

The genotypes come from the family's own calls when available, and from the imputed GLIMPSE2 calls
otherwise; if neither is present, from the first call set the family has.

---

## The checks

Each check returns **pass**, **warn**, **fail** or **not run**, and the page rolls the worst of them up
into the overall verdict.

### Sex

Genetic sex comes from the **heterozygosity rate on chromosome X**: a man (one X) has almost no X
heterozygotes, a woman many.

- 5% or less → **male**; 15% or more → **female**; in between → indeterminate (**warn**).
- At least **200** X sites are needed; with fewer the result is indeterminate (**warn**).
- A haploid call (`0` or `1`, how some callers write a man's X) counts as a homozygous site. A
  no-call or a half call (`./1`) is left out.
- A recorded sex that differs from the genetic sex is a **fail** — most often a sample swap or a
  mislabelled tube. No recorded sex is a **warn**.

### Relatedness against the pedigree

Relatedness uses the **KING-robust kinship** coefficient (φ) together with the **IBS0 rate** (the share
of sites where the two samples are opposite homozygotes). The kinship bands:

| Kinship φ | Inferred relationship |
| --- | --- |
| above 0.354 | duplicate or identical twin |
| 0.177 – 0.354 | first degree: **parent–child** when IBS0 is below 0.8%, otherwise **sibling** |
| 0.0884 – 0.177 | second degree |
| 0.0442 – 0.0884 | third degree |
| below 0.0442 | unrelated |

Each pair's inferred relationship is compared with the pedigree:

- A recorded **parent–child** pair that is not inferred as parent–child → **fail** (swap or wrong
  parent).
- Recorded **siblings** that are not first degree → **fail**.
- A pair expected to be **unrelated** that looks second or third degree → **warn**; first degree or
  duplicate → **fail** (duplicate, swap or consanguinity).

A pair needs at least **1,000** shared sites; with fewer the result is a **warn**, not a false alarm.

**Parents and consanguinity.** The two parents of a child are expected to be unrelated. The matrix
always shows that pair, so it confirms *parents unrelated — no consanguinity*, or flags it when they look
related. Other expected-unrelated pairs that pass are hidden to keep the matrix readable.

### Mendelian errors

For each child with genotyped parents, the **Mendelian-error rate** is the share of sites where the
child's genotype cannot be made from one allele of each parent. With one genotyped parent, an error is a
child that shares no allele with that parent.

- 5% or more → **fail**; 2% or more → **warn**; otherwise **pass**.
- At least **200** informative sites are needed; with fewer the result is a **warn**.

A clear rate points to a swap, a wrong parent or genotyping noise. The same logic highlights impossible
genotypes on the **Review ROI markers** page.

---

## Monogenic NIPT checks

In NIPT there is no clean fetal genome, and the mother's sample is a cfDNA mixture (maternal DNA with a
small fetal fraction). CoGA models the case as a trio backed by the father's germline sample and the
cfDNA, so these checks read the cfDNA categories rather than genotype relatedness. The categories are
explained in the [Monogenic NIPT reference](/docs/reference/monogenic-nipt).

### Paternity (categories 7 and 8)

Category 7 is a paternal allele that reached the fetus; category 8 is an allele the father is
homozygous for but that is absent from the cfDNA. Only sites with a usable father call count: a
genotype at a depth of 10 or more. Where the father has no usable call, a site at `FF / 2` is still
placed in category 7, but it says nothing about paternity and is left out. The share of absent paternal
alleles, `cat 8 ÷ (cat 7 + cat 8)`:

- no category-7 site, or 70% or more absent → **fail** (non-paternity or a sample mix-up);
- 40% or more absent → **warn** (can also reflect a low fetal fraction);
- at least **10** paternal-informative sites are needed; with fewer, **warn**.

### Fetal sex (paternal X transmission)

The father passes his X to a daughter and his Y to a son. So an allele on the father's X (outside the
pseudo-autosomal regions) shows in the cfDNA at about `FF / 2` for a girl and is absent for a boy. An
allele at a maternal level (VAF 30% or more) belongs to the mother and is ignored.

- At least **8** informative sites are needed; otherwise indeterminate (**warn**).
- 3 or more transmitted paternal-X alleles → **female**; none → **male**; otherwise indeterminate.
- Only sites where the father carries the allele count: `1/1`, or a haploid `1` as some callers write
  a man's X, at a depth of 10 or more.

### Parent sex

The father and the cfDNA sample are sexed from X heterozygosity, as above. The cfDNA is mostly maternal
DNA, so it should read female. Because it is a mixture, this catches a gross problem (plasma not from a
woman) but can end up indeterminate on sparse data.

### cfDNA category check

A sanity check on the shape of the category counts:

- **Excess de novo (category 1):** de novo variants are rare. 15% or more of the classified sites →
  **fail**; 5% or more → **warn** (artifacts or contamination).
- **Maternal transmission:** about half of the mother's heterozygous alleles reach the fetus, so
  `(cat 3 + cat 4) ÷ (cat 2 + cat 3 + cat 4)` should be near 50%. 30 or more percentage points away →
  **fail** (wrong mother or a sample problem); 15 or more → **warn**. At least **20** maternal-het sites
  are needed. The margins are wide because detecting inherited alleles depends on the fetal fraction.
- The category-8 count is shown alongside.

---

## Reading the page

**Pedigree with QC overlay.** Each assessed person gets a ring and a badge:

| Verdict | Ring | Badge |
| --- | --- | --- |
| Pass | thin, solid, green | ✓ |
| Warn | dashed, amber | ! |
| Fail | thick, solid, red | ✕ |

A person without a ring was not assessed. Line and badge tell the verdicts apart as well as colour. The
symbol keeps its clinical meaning (black for affected, the carrier half-fill in its carrier colour).
Hover a symbol for the reason.

**Per-sample table.** Recorded sex against genetic sex, and the Mendelian-error rate, coloured by
status. Hover a cell for the explanation.

**Relatedness matrix.** A sample × sample grid (lower half only; it is symmetric). Each cell shows the
inferred relationship, the kinship φ and the IBS0. A pair that contradicts the pedigree — including
parents who look related — is outlined in red.

**NIPT cards.** For a cfDNA family the page adds *Paternity*, *Fetal sex* and *cfDNA category QC*
cards, and the parent-sex rows appear in the per-sample table.

---

## Sign-out and limits

- **Sample QC gates sign-out.** A failed Sample QC, or a pedigree check that could not run for lack of
  data, stops sign-out until someone acknowledges it with a reason (see the
  [traceability reference](/docs/reference/clinical-traceability)).
- **Missing data gives a warning, not an error.** If the genotypes or the cfDNA analysis cannot be
  loaded, the page says so and shows what it could compute.
- **A screening check, not an identity test.** Relatedness and Mendelian errors use a sample of sites
  from a few autosomes, and sex a capped number of X sites.
