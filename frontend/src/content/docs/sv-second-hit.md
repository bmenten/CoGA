# SNV + SV compound heterozygosity (cross-type second hit) — reference

Recessive disease is often caused by **two different kinds of variant in the same gene**: a small
variant (SNV or indel) on one allele and a **structural variant** (SV) on the other. The most important
case is a **heterozygous SNV plus an overlapping deletion**: the deletion removes the other copy, so a
variant that looks heterozygous is in fact the only copy left.

Small variants and structural variants are filtered on separate pages, so these pairs are easy to miss.
CoGA flags them on the small-variant page. How to use the filter is in the [user guide](/docs), section
*Small-variant prioritisation*.

---

## What you see

A small variant whose gene is **also hit by an SV** carries an **SV badge** next to the gene name:

- the SV type and count, for example `SV: DEL` or `SV: DEL, INS ×2`;
- a **phase chip**, `trans` or `cis`, when a phase was decided; a `✓` on the chip means the phase comes
  from read phasing;
- red styling when it is a **deletion in trans with a heterozygous SNV**, which CoGA calls *effectively
  biallelic*.

Hover or focus the badge for an explanation. It gives the SV zygosity in the affected members (`het`,
`hom` or `mixed`) and how the phase was decided. The badge links to the family's structural variants for
that gene, in a new tab.

## The filter

In the small-variant filters, the **Structural second hit** section has an **Also hit by an SV**
switch. It keeps only variants in genes that also carry an SV, and combines with every other filter
(for example the Mendeliome panel, a frequency cut-off and a consequence).

---

## trans or cis

A second hit completes a recessive genotype only if the two variants are on opposite alleles (in
trans). CoGA decides the phase in one of two ways.

**By read phasing**, when the SVs were phased upstream (for example long-read calling with Sniffles2
and HiPhase or LongPhase). If the SNV and the SV share a phase set in an affected member, CoGA reads
trans or cis directly. A cis in any affected member wins.

**By segregation**, otherwise. CoGA calls the pair **trans** when all of this holds:

- every affected member is heterozygous for the SNV and carries the SV;
- the family holds at least one unaffected member;
- no unaffected member carries both variants.

If an unaffected member carries both, the pair is **cis**. Without an unaffected member, the phase is
**unknown**.

> **A segregation "trans" can rest on nothing.** The rule does not require an unaffected member to
> carry either variant. Relatives who carry neither the SNV nor the SV still give "trans", although
> they say nothing about the phase. Before you accept a segregation-based trans, check that each parent
> carries one of the two variants.

For two small variants in the same gene (SNV + SNV), CoGA is more careful: it reports trans only from
read phasing, and a pair that passes the genotype rule stays *unknown*.

---

## How matching works

- A small variant is matched against **every gene it overlaps**, not only its main annotation, so the
  badge and the filter agree.
- Matching is at the **gene** level (any overlap), so a whole-gene deletion is included.
- CoGA builds the map of *which genes the family's SVs hit* the first time you open the family's small
  variants, and keeps it.

> **After a per-sample SV upload the map is not rebuilt.** A package import rebuilds the map. An SV file
> uploaded for a single sample (on the **Upload family and sample data** page) does not: the badges and
> the filter can then miss the new SVs. Check the structural-variant page directly for the genes that
> matter.

---

## Caveats

- The badge only says that an SV overlaps the gene. Review the SV itself (type, size, quality,
  breakpoints) on the structural-variant page before acting.
- A `cis` or `unknown` verdict does not rule a pair out: it reflects the evidence available, which is
  limited in small families and singletons.
