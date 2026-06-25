# Monogenic NIPT (cell-free DNA) — reference

Monogenic NIPT screens a pregnancy for single-gene disorders from **cell-free DNA
(cfDNA) in maternal plasma**, cross-referenced with a paternal sample. This reference
covers both halves of the feature: the **analysis workflow** (the two-sample trio
model, ingestion, and the summary / variants / coverage surfaces) and the
**fetal-fraction estimation and per-variant classification algorithm** (the eight
maternal/fetal categories, how FF is computed, and what each category lets you read off
the data).

This is the in-depth reference. For the workflow-level overview see the
[in-app user guide](/docs) section *Monogenic NIPT (cell-free DNA)*.

---

## Part 1 — The analysis

### The clinical question

The plasma cfDNA is a **mixture**: mostly maternal DNA with a minor **fetal fraction
(FF)**. The fetus is **never sequenced directly** — its genotype is *inferred* from how
far the cfDNA allele fraction (VAF) deviates from the clean maternal expectations (0%,
50%, 100%), in proportion to the fetal fraction. CoGA's job is to estimate FF, classify
every cfDNA variant by the maternal/fetal zygosity that explains its VAF, and let you
hunt for de novo, dominant, and recessive risks while reading the fetal genotype off the
VAF rather than observing it. The questions you must be able to answer:

- **What is the fetal fraction?** Everything downstream depends on it.
- **How many variants fall in each category**, and how many were dropped by filters?
- **Is on-target coverage adequate** across the investigated regions?
- **De novo dominant** — a plausible causal de novo variant in the fetus, distinguished
  from maternal mosaicism and noise?
- **Paternal / maternal dominant** — did the fetus inherit a causal parental variant?
- **Recessive** — do father and mother each carry a causal variant in the *same* gene,
  and did the fetus inherit **both**?

### A two-sample trio

CoGA models the case as a trio — father, mother, fetus — backed by only **two physical
samples**:

| Pedigree node | Physical sample | Variant data |
| --- | --- | --- |
| Father | Father germline VCF | observed genotypes |
| Mother | **cfDNA plasma VCF** (the mixture) | observed allele fractions |
| Fetus | placeholder, no VCF | **inferred**, never observed |

The cfDNA VCF is uploaded **as the mother's sample** because it is literally
maternal-plus-fetal signal; the fetus is a placeholder with no sequence of its own. A
family is in NIPT mode when its analysis type is `monogenic_nipt` and the cfDNA sample is
tagged `nipt_cfdna` — that is what surfaces the Monogenic NIPT tab.

### Ingestion

The input is a **single combined two-sample VCF** with a father column and a cfDNA
column, joint-genotyped so that every variant row carries both a father call and a cfDNA
call (a hom-ref sample is `0/0`, not missing). Ingestion captures, per call, the allele
depth (DP), the alt-supporting reads, and the VAF, plus the **site-level QUAL** the NIPT
quality filter relies on. Both samples are sequenced over the same target (today ≈5,000
genes; potentially a smaller panel or the whole exome).

> **Derived, not entered.** The fetal fraction and every per-variant category are
> **computed** from the two samples — their allele fractions, depths, qualities, and the
> father genotype. The only inputs are the combined VCF, the coverage and target regions,
> the pedigree, and your filter choices.

### What the surfaces show

The Monogenic NIPT workspace mirrors the small-variant page, with three views.

**Summary.** A fetal-fraction badge (estimate, 95% confidence interval, and the
category-7 site count), the per-category counts (categories 1–8), and a **filter funnel**
— *total → quality-filtered → artifact-filtered → analysed* — so you can see exactly how
many sites each step removed. A *Low-confidence FF* chip appears when there are too few
sites or the interval is wide; an *external FF* (from an upstream caller) is shown
alongside the computed value, with an *FF disagreement* chip when the two differ.

**Variants.** The full small-variant display (cards under ~100 matches, otherwise the
table) with the complete annotation — father and cfDNA genotypes, ClinVar, gnomAD,
in-silico scores, HGVS — plus tagging, reporting, and classification. Each variant also
carries a **NIPT classification block**: the category, the maternal and fetal state, the
observed and expected VAF, the confidence, and any flags. On top of the usual
small-variant filters, two NIPT-specific controls are added: **maternal/fetal categories**
(pick any of the eight; each shows its count) and **inheritance presets** (*De novo*,
*Paternal dominant*, *Maternal dominant*, *Recessive at-risk* — see Part 2).

**Coverage.** On-target coverage summarised as a **median depth** over the panel or
family ROI, both overall and per region — the target is the gene panel / family ROI, not a
separate upload.

### Artifacts

Recurrent artifacts are capture- and chemistry-specific, so each assay/panel keeps its
own **artifact list** (a panel-of-normals). Sites on the list are excluded at analysis
time and counted in the funnel ("N filtered as artifacts"). The list is seeded
automatically from cohort recurrence within the same assay and can be curated manually.
Removing recurrent artifacts upstream is what keeps the low-VAF category-1/7 cluster
clean.

NIPT has **dedicated sample-integrity QC** (paternity from categories 7/8, fetal sex,
parent sex, and a category-distribution sanity check) — see the
[Sample-integrity QC reference](/docs/reference/sample-qc).

---

## Part 2 — Fetal fraction & classification

### The model

For a biallelic site, write the maternal alt-copy fraction as `m ∈ {0, ½, 1}` and the
fetal alt-copy fraction as `f ∈ {0, ½, 1}`. The expected cfDNA allele fraction is

```text
VAF = m · (1 − FF) + f · FF
```

Enumerating the maternal/fetal states — and using the father's genotype to resolve the
two cases that need it — gives eight categories. Because the expected VAFs are
deterministic functions of FF, the classifier's cluster centres are fixed once FF is
known.

| Cat | Maternal / fetal state | Expected cfDNA VAF | Clinical meaning |
| --- | --- | --- | --- |
| 1 | **De novo in fetus** (absent in both parents) | **FF / 2** (low) | Candidate de novo dominant — father hom-ref separates it from paternal transmission |
| 2 | Maternal het, **not** inherited | **50% − FF/2** | A maternal carrier allele the fetus did not receive |
| 3 | Maternal het, fetus het | **50%** | Maternal allele transmitted; the maternal hit of a possible compound pair |
| 4 | Maternal het, fetus hom-alt | **50% + FF/2** | Fetus inherited both alleles — homozygous recessive risk |
| 5 | Maternal hom-alt, fetus het | **100% − FF/2** | Fetus inherited one reference allele from the father |
| 6 | Maternal & fetal hom-alt | **100%** | Both homozygous (commonly a common variant) |
| 7 | **Paternal allele transmitted** (mother hom-ref) | **FF / 2** | Drives the fetal-fraction estimate; the paternal hit of a possible compound pair |
| 8 | Paternal hom-alt, **absent** in cfDNA | ≈ 0 (expected FF/2) | False-negative QC signal — a variant the fetus should carry but the assay missed |

Categories 1 and 7 share the same expected VAF (`FF / 2`) and are told apart only by the
father genotype: hom-ref means de novo, a carried allele means paternal transmission.

### Estimating the fetal fraction

FF is read off **category-7 sites** — positions where the mother is hom-ref, the father
carries the allele, and the alt is present in the cfDNA, so the only alt signal is the
fetus's single obligately-inherited paternal allele, sitting at a clean `FF/2`. CoGA
cannot observe "mother hom-ref" directly, so these sites are selected operationally:
**father carries** *and* **cfDNA VAF is low**. Requiring the father to carry the allele
excludes de novo and maternal-mosaicism sites (father hom-ref) that would contaminate the
estimate at the same VAF. A VAF ceiling of **0.25** isolates the `FF/2` cluster from the
nearest maternal band (category 2 sits at ≥ 0.375 even at an implausible FF = 25%). Only
well-covered, high-quality autosomal sites are used.

Two estimators are reported together:

- **Pooled (headline)** — `FF = 2 · (Σ alt reads / Σ depth)` across category-7 sites, the
  depth-weighted estimate. Its 95% confidence interval is a **Wilson score interval**,
  scaled ×2.
- **Per-site median (cross-check)** — `FF = 2 × median(VAF)` over the same sites, robust
  to a few residual-artifact outliers. If the two estimators disagree by more than the CI
  half-width, the run is flagged (it suggests artifact contamination or a sub-population).

The estimate is reported with **N(sites)** and a confidence interval so it can be trusted
or distrusted at a glance. It is marked **low-confidence** when there are too few sites
(default < 30) or the CI is too wide (half-width > ~0.03); below a hard floor of a handful
of sites no FF is computed at all.

**External FF.** When the run supplies an external FF from an upstream caller, it is
recorded beside the computed value and a disagreement is **flagged** (default tolerance
~3 absolute FF points) rather than silently overriding. The **computed estimate stays the
default**.

The complementary **category-8** signal is the false-negative QC: paternal hom-alt sites
that *should* appear at `FF/2` but are absent from the cfDNA. A high category-8 rate flags
allele dropout, insufficient FF, or coverage gaps.

### Assigning categories

As FF → 0 the band centres collapse together (category 2 at `50 − FF/2` and category 3 at
`50` nearly coincide), so classification is a **likelihood problem with known centres**,
not a set of hard VAF cut-offs. Given FF, each cfDNA variant is scored against every
candidate category with a **beta-binomial** likelihood on the integer read counts
`(alt reads, depth)`. The beta-binomial's overdispersion reflects real sequencing noise,
so at high depth it refuses razor-thin, unsupportable distinctions between neighbouring
bands (it recovers a plain binomial as overdispersion → 0).

The father genotype and presence **prune the candidate set**, which is what makes the
otherwise-degenerate `category 1 = category 7 = FF/2` separable:

| Site | Candidate categories |
| --- | --- |
| present & father hom-ref | 1, 2, 3, 4, 5, 6 (de novo or maternal) |
| present & father carries | 2, 3, 4, 5, 6, 7 (maternal or paternal) |
| present & father missing | 1–7, flagged `father_no_coverage` (de novo vs paternal ambiguous) |
| absent & father hom-alt | category 8, or undetectable (see below) |
| absent & father het | paternal allele simply not transmitted (no category) |

Each variant is assigned to the **highest-posterior** category; the posterior of the
chosen category is its **confidence**, and the runner-up category and its posterior are
reported too. De novo (category 1) carries a down-weighted prior — it is rare, so a de
novo call must clear a real evidence bar: the father must be confidently hom-ref with
coverage, and the VAF must sit within the FF confidence interval of `FF/2`.

**Absence and category 8.** Absence is only meaningful when detection was *expected*. For
a paternal hom-alt site missing from the cfDNA, the expected fetal alt-read count is
`E = depth · FF/2`. If depth is adequate and `E ≥ ~3`, the site is a genuine **category-8
false negative**; if depth is too low or `E < ~3`, it is honestly "couldn't have seen it"
(flagged `low_depth_dropout` or `undetectable_at_ff`), not a QC failure.

### Reading inheritance off the categories

The inheritance presets are direct reads of the category assignment:

- **De novo dominant** → category 1.
- **Paternal dominant transmission** → category 7 present (vs. absent = not transmitted).
- **Maternal dominant transmission** → a maternal-het variant in category 3/4 (inherited)
  vs. category 2 (not inherited).
- **Recessive at-risk** → father and mother each carry a causal variant in the same gene,
  and the fetus inherited **both** — the paternal allele via category 7 and the maternal
  allele via category 3/4 (or a single category-4/6 homozygous hit). Both members of each
  compound pair are kept.

### What is and is not resolvable

The categories are **not equally reliable**, and the page reports them in two tiers:

- **Robust regardless of FF or depth** — de novo (category 1), paternal transmission
  (category 7 present vs. absent), and the coarse maternal carrier state (low VAF / ~0.5
  band / ~1.0 band → hom-ref / het / hom). These are the high-value, dependable signals.
- **FF- and depth-limited** — the fetal inheritance of a *maternal* allele (category 2 vs
  3 vs 4, and 5 vs 6). Distinguishing these requires resolving a `±FF/2` shift around 0.5,
  which needs depth on the order of `1/FF²` (hundreds of reads at FF ≈ 4%). Below adequate
  depth or when FF is too low, the classifier returns the maternal state but sets the
  fetal inheritance to **unknown** and raises a flag rather than guessing.

Use each variant's **confidence** and its **flags** (for example `ff_too_low`,
`low_depth`, `father_no_coverage`, `multiallelic`, `approx_counts`) to decide how much
weight a single call deserves. This split is what keeps the recessive workflow
trustworthy: the paternal allele's inheritance (category 7) is robust, and the maternal
allele's inheritance is reported with explicit confidence so an at-risk call is never
asserted on noise.

> **Out of scope (documented limitations).** Local CNVs or aneuploidy at a locus break
> the `m, f ∈ {0, ½, 1}` dosage assumption; sex chromosomes are excluded from FF and
> classification (fetal sex is unknown to the classifier and X/Y dosage differs);
> multi-allelic sites take the max-AD alt and are flagged.
