The **Haplotypes** track (chromosome view and genome overview) is CoGA's PGT tool. It colours each
person's two haplotypes by the parental homolog they descend from, finds the haplotype that carries the
disease allele, and calls each embryo from it.

### What the family needs

- Phased imputed genotypes (GLIMPSE2) for the couple, the embryos and the relatives you want coloured.
- A correct pedigree, including the grandparents, who anchor the disease haplotype.
- Affected and carrier status on the members that define the disease haplotype.
- An **inheritance model** (if none is set, CoGA assumes recessive when a member is marked as a carrier,
  otherwise dominant) and a **region of interest** at the disease locus. Without an ROI there are no
  embryo calls.

### Reading the track

- **P1** and **P2** (blues) are the father's homologs, **M1** and **M2** (greens) the mother's.
  **Untransmitted** (grey) is a relative's homolog that was not passed down, or one CoGA could not
  place.
- The disease haplotype has a line under the band: **solid red** (*Affected*) for a dominant or X-linked
  model, **dashed orange** (*Carrier*) for a recessive one. On the genome overview the line appears only
  on the ROI's chromosome.
- The **marker dots** are the raw phased markers. Use them to check that a colour change is a real
  recombination and not phasing noise. On a very large region they are hidden: zoom in.

### The embryo calls

Each embryo's call is a badge in the **Family members** table on the family page: *Affected / at risk*,
*Carrier*, *Unaffected* or *Uninformative*. A call that the embryo does not carry a disease haplotype
needs the embryo's own haplotype, seen across the ROI; without it the embryo reads *Uninformative*.
Next to the badge may be:

- **⚠ recombination** — a haplotype change inside the ROI or within 250 kb of it; the call may not hold
  across the locus.
- **⚠ phase corrected** — CoGA undid a phase switch in a parent's haplotypes inside or close to the ROI.
  Where the phase switched is known only roughly, so the call across the locus is less certain: review
  the ROI markers.
- **⚠ uninformative** — no call is made: no disease haplotype could be resolved, or the embryo's
  haplotype does not cover the ROI.
- **⚠ sex unknown** — an X-linked recessive call depends on the embryo's sex, which is not recorded. The
  embryo then reads *Affected / at risk* or *Uninformative*, and the warning gives the call for a son and
  for a daughter. Record the sex to resolve it.

**Review ROI markers →** shows the phased genotypes of every member across the ROI, with each child's
Mendelian-error rate. Use it to re-check a surprising call: a single marker that disagrees with its
neighbours is noise, not a crossover, and a clear Mendelian-error rate points to a swap or a wrong
pedigree.

In a single-parent (donor) family the donor lane stays grey, and a recessive call is uninformative
because the donor side is unknown.

[Haplotype segregation reference (colours, inference rules, embryo calls)](/docs/reference/haplotype-segregation "further-reading")
