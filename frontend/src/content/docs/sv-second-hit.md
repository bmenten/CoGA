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
trans). CoGA decides the phase in one of two ways, and the badge says which.

**By read phasing**, when the SVs were phased upstream (for example long-read calling with Sniffles2
and HiPhase or LongPhase). If the SNV and the SV share a phase set in an affected member, CoGA reads
trans or cis directly. A cis in any affected member wins. A call with one allele missing (a half call
such as `.|1`) does not show which copy carries the variant, so the phase then comes from segregation.

**By segregation**, otherwise, from the family's genotypes. Every affected member must be
heterozygous for the SNV and carry the SV. Then:

- **cis** when an unaffected member carries both variants: either both sit on one copy in this family,
  or that member has the same genotype without the disease.
- Otherwise CoGA traces each affected member's two variants through their parents in the pedigree. A
  parent with a reference call for a variant did not pass it on, so the child's copy came from the
  other parent. Failing that, a variant goes to the only parent known to carry it.
  - One variant from each parent gives **trans**. One sequenced parent who carries one of the two and
    has a reference call for the other is enough: the other variant then came from the other parent.
  - Both from the same parent gives **cis**, but only when both parents are genotyped for both
    variants.
- **unknown** in every other case. A sibling or other relative who carries neither variant says
  nothing about the phase. Neither does a variant that both parents lack (de novo): it may sit on
  either copy.

A reference call counts only with at least 8 reads behind it, where the call reports its depth; a
missing call or a no-call is not a reference call. An SV file uploaded for one sample holds no calls for
the other members, so it never shows that a parent lacks the SV; a parent counts as carrying it when any
SV of theirs hits the same gene. A male is not traced on chrX or chrY outside the pseudo-autosomal
regions, where he has a single copy.

Two small variants in the same gene (SNV + SNV) are phased the same way, half calls included: by read
phasing first, and otherwise by this rule. Their pair card says *In trans · read-backed* or
*In trans · by segregation*. A pair in cis is not shown: it cannot be the recessive cause.

---

## How matching works

- A small variant is matched against **every gene it overlaps**, not only its main annotation, so the
  badge and the filter agree.
- Matching is at the **gene** level (any overlap), so a whole-gene deletion is included.
- CoGA builds the map of *which genes the family's SVs hit* the first time you open the family's small
  variants. Any change to the family's SVs (a package import, an SV file uploaded for one sample, a
  deletion) makes the next open rebuild it, so the badges and the filter follow the current SVs.

---

## Caveats

- The badge only says that an SV overlaps the gene. Review the SV itself (type, size, quality,
  breakpoints) on the structural-variant page before acting.
- An `unknown` verdict does not rule a pair out: it reflects the evidence available, which is limited
  in small families, duos and singletons.
- A `cis` from an unaffected member who carries both variants assumes that member is truly unaffected.
  For a late-onset or incompletely penetrant disorder, check the variants yourself: such an SNV + SNV
  pair is not listed as a pair.
