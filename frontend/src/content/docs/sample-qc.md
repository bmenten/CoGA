# Sample-integrity QC — reference

The **Sample QC** page (per family, *Sample QC* button) is an automated check that a
family's samples are who the pedigree says they are, run **before** you trust any
downstream interpretation. It catches sample swaps, mislabelled relationships,
wrong-sex labels, contamination and consanguinity — the failure modes that quietly
invalidate a variant call or a segregation analysis.

This is the in-depth reference. For the workflow-level overview see the
[in-app user guide](/docs) section *Sample-integrity QC*.

---

## Why it is application-aware

CoGA serves several applications, each with a different input modality and a
different notion of "integrity". A single generic QC would be wrong for most of
them, so the page **resolves the application first** and then runs only the checks
that make sense:

| Application | How it is recognised | Checks that run |
| --- | --- | --- |
| **Long-read WGS family** | a pedigree with parent–child edges, full SNV call set | sex · relatedness · Mendelian |
| **Shallow-WGS PGT** | an `embryo` role is present (GLIMPSE2-imputed genotypes) | sex · **parentage** (embryos ↔ parents) · Mendelian |
| **Monogenic NIPT (cfDNA)** | `analysis_type = monogenic_nipt` | **paternity** (cat 7/8) · **fetal sex** · **parent sex** · **cfDNA category QC** |
| **Carrier couple (BEGECS)** | two members, no parent–child edge | sex · expected-**unrelated** confirmation |
| **Single targeted** | one sample | sex only |

The application is inferred from the `analysis_type` metadata (only NIPT is tagged
explicitly) plus the family structure (embryo roles, couple shape) — mirroring the
fact that the applications are distinguished by the input files they were built
from. The resolution lives in `resolve_application()` and the per-application
gating in the `QcProfile` table, both in `backend/app/services/sample_integrity_qc.py`.

The genotype callset is chosen automatically, preferring real calls over imputed:
**clair3 > glimpse2** (and the cfDNA VarDict calls for NIPT).

---

## The checks

Every check returns a status — **pass / warn / fail / not-run** — and the page rolls
the worst of them up into the overall verdict.

### Sex concordance

Genetic sex is inferred from the **heterozygosity rate on chromosome X**: a
hemizygous male calls almost no X heterozygotes, a female calls many.

- `het ≤ 0.05` → **male**, `het ≥ 0.15` → **female**, in between → indeterminate.
- Needs at least **200** X sites; below that the call is *indeterminate* (warn).
- A mismatch between the recorded sex and the genotype sex is a **fail** — the most
  common cause is a sample swap or a mislabelled tube.

### Relatedness vs the pedigree

Pairwise relatedness uses **KING-robust kinship** (φ) together with the **IBS0 rate**
(the fraction of sites where the two samples are opposite homozygotes). Kinship bands
map to a relationship:

| Kinship φ | Inferred relationship |
| --- | --- |
| > 0.354 | duplicate / monozygotic twin |
| 0.177 – 0.354 | first degree → **parent–child** (IBS0 ≈ 0) or **sibling** (IBS0 > 0) |
| 0.0884 – 0.177 | second degree |
| 0.0442 – 0.0884 | third degree |
| < 0.0442 | unrelated |

Each pair's *observed* relationship is compared against what the pedigree *asserts*:

- An asserted **parent–child** that looks unrelated → **fail** (swap or wrong parent).
- An asserted **sibling** that is not first-degree → **fail**.
- An expected-**unrelated** pair that looks related → **fail/warn** (duplicate, swap,
  or consanguinity).

A pair needs at least **1,000** shared sites; below that the result is *indeterminate*
(warn) rather than a false alarm.

#### Co-parents and consanguinity

The two parents of a child are an *expected-unrelated* pair, and CoGA keeps that
check visible so the matrix confirms **"parents unrelated — no consanguinity"**, or
flags it red when they look related (consanguinity or a sample duplication). For
other applications, expected-unrelated pairs that pass are suppressed to keep the
matrix focused; co-parent pairs and carrier couples are always shown.

### Mendelian-error rate

For each child with genotyped parents, the **Mendelian-error rate** is the fraction
of informative sites where the child's genotype cannot be formed from one allele of
each parent (an impossible transmission). Two parents give the full consistency
check; a single genotyped parent falls back to allele-sharing.

- `≥ 5%` → **fail**, `≥ 2%` → **warn**, otherwise **pass** (needs ≥ 200 informative
  sites).
- A non-trivial rate points to a swap, a wrong parent, or genotyping noise.

The same Mendelian logic also drives the per-marker highlight on the *Review ROI
markers* page, where offending positions are tinted orange.

---

## Monogenic NIPT checks

NIPT is special: there is no clean fetal genome and the "mother" is a maternal-plasma
cfDNA mixture (mostly maternal DNA with a minor fetal fraction). CoGA models the case
as a trio backed by two physical samples — a **paternal germline** VCF and the
**cfDNA**. The QC therefore reads the cfDNA *category distribution* rather than
genotype relatedness. (For the category model itself, see the *Monogenic NIPT* user-
guide section.)

### Paternity (categories 7 / 8)

A category-7 site is a paternal allele transmitted to the fetus; a category-8 site is
a paternal hom-alt allele that is *absent* from the cfDNA (it should have been
transmitted). The false-negative rate `cat8 / (cat7 + cat8)`:

- `cat7 = 0` or FN rate `≥ 0.70` → **fail** (non-paternity / sample mixup).
- FN rate `≥ 0.40` → **warn** (may reflect low fetal fraction rather than non-paternity).
- Needs at least **10** paternal-informative sites.

### Fetal sex (paternal X transmission, no chrY needed)

The father transmits his X to a daughter and his Y to a son, so a paternal-only allele
on the **non-PAR X** appears in the cfDNA for a female fetus (transmitted, ~FF/2) and
is absent for a male fetus (the category-8 "not transmitted" signal). A present allele
at a maternal level (high VAF) is the mother's and is excluded. PAR1/PAR2 are excluded.

- enough transmitted paternal-X alleles → **female**; none transmitted but the
  informative sites are present → **male**; too few informative sites → *indeterminate*.

### Parent sex (X-SNV zygosity)

The germline parents are sexed from X heterozygosity, exactly like the genotype sex
check above: the **father** germline reads hemizygous (male), and the **cfDNA**
maternal-plasma sample is ~all maternal so it reads female-het. Because the cfDNA is a
mixture (not clean germline), the maternal call is approximate — it reliably catches a
gross problem (plasma not from a female) but lands on *indeterminate* on sparse data.

### cfDNA category-distribution QC

A sanity check on the shape of the category tally:

- **De-novo (category 1) excess** — de novos are rare; `≥ 15%` of classified sites →
  **fail**, `≥ 5%` → **warn** (artifacts / contamination).
- **Maternal transmission rate** — about half of the mother's heterozygous alleles
  reach the fetus, i.e. `(cat3 + cat4) / (cat2 + cat3 + cat4) ≈ 0.5`. A rate **> 0.30
  away** from 50% → **fail** (wrong mother / sample issue), **> 0.15 away** → **warn**.
  Needs ≥ 20 maternal-het sites; the tolerance is wide because cfDNA detection of
  inherited alleles is fetal-fraction-dependent.
- The category-8 (paternal-absent) count is reported alongside.

---

## Reading the page

**Pedigree with QC overlay.** Each assessed individual gets a ring around their
symbol, with a badge at its top left:

| Verdict | Ring | Badge |
| --- | --- | --- |
| Pass | thin, solid, green | ✓ |
| Warn | dashed, amber | ! |
| Fail | thick, solid, red | ✕ |

An individual without a ring was not assessed. Line and badge tell the verdicts apart as
well as colour. The symbol itself keeps its clinical meaning: black for affected, the
carrier half-fill in its carrier-type colour. Hover a symbol for the reason.

**Per-sample QC table.** One row per family member: recorded sex vs genotype sex
(green when concordant, red on mismatch) and the Mendelian-error rate, colour-coded by
status. Hover any cell for the explanation.

**Relatedness association matrix.** A sample × sample grid, lower triangle only (it is
symmetric). Each cell shows the inferred relationship (coloured by type), the kinship
φ and the IBS0; a pair that contradicts the pedigree — including co-parents who look
related — is outlined in red. Hover a cell for the full pair detail.

**NIPT cards.** For a cfDNA family the page adds *Paternity*, *Fetal sex* and *cfDNA
category QC* cards, and the parent sex rows appear in the per-sample table.

---

## Robustness, data sources and limitations

- **Degrades to a warning, never an error.** If the cfDNA analysis or the genotype
  load fails (missing or mock data, an unavailable callset), the page adds a warning
  note and shows what it could compute instead of erroring out.
- **Thresholds** live as named constants in `sample_integrity_qc.py`
  (`KINSHIP_*`, `MENDEL_*`, `PATERNITY_*`, `NIPT_*`, the sex `*_X_HET` bounds) and the
  fetal-sex bounds in `nipt_analysis.py` — change them there, not inline.
- **Sampling.** Relatedness/Mendelian use a few autosomes' worth of sites and a
  capped X-site count for sex; this is a screening QC, not a forensic identity test.
- **NIPT mother sample.** There is no separate maternal germline sample — the cfDNA
  stands in for the mother. If a family only has cfDNA + father, the maternal checks
  will not populate.

## Where the code lives

| Concern | File |
| --- | --- |
| Pure QC maths + thresholds + application profiles | `backend/app/services/sample_integrity_qc.py` |
| Loader, application/pedigree resolution, NIPT wiring | `backend/app/services/sample_integrity_service.py` |
| NIPT category model + fetal-sex inference | `backend/app/services/nipt_analysis.py` |
| API schema + endpoint | `backend/app/schemas.py`, `backend/app/routers/families.py` |
| The page | `frontend/src/pages/families/FamilySampleQcPage.tsx` |
| Pedigree QC overlay | `frontend/src/components/visualizations/Pedigree.tsx` |
