# Haplotype segregation analysis — reference

The **Haplotypes** track (chromosome view and genome overview) is CoGA's tool for preimplantation
genetic testing (PGT). It traces the four parental haplotypes through the family, finds the haplotype
that carries the disease allele, and derives for each embryo whether it inherited it. It also shows the
raw markers, so you can catch the recombinations and artifacts that would make a call unsafe.

How to open and read the track is in the [user guide](/docs), section *Haplotype segregation (PGT)*.
This page holds the rules.

---

## Everything coloured is derived

Nothing on the track is typed in. The founder colours, the relatives' colours, the disease haplotype
and each embryo's call are **computed** from the phased genotypes and the pedigree. Your inputs are:

- the **pedigree**: roles, parentage and sex;
- the **affected and carrier status** of the members;
- the **inheritance model**. If none is set, CoGA assumes recessive when any member is marked as a
  carrier, and dominant otherwise;
- the **region of interest (ROI)**: the disease locus. The embryo calls on the family page and the
  risk line on the genome overview need an ROI; without one they read "not assessed". In the chromosome
  view, when the ROI is not on the chromosome shown, the risk line is worked out over the region in
  view instead: set the ROI to read it at the locus.

---

## The two layers

- **Haplotype blocks** — the cleaned, colour-coded inheritance blocks. Each member shows two lanes, one
  per homolog; a block changes colour at each recombination. This is the layer you read.
- **Raw marker dots** — one dot per informative imputed marker, with no smoothing. This is the layer
  you check: it shows isolated phasing switches, noise at block edges, and the exact marker where a
  crossover happens.

Use the dots to confirm that a breakpoint is real before you trust an embryo call.

---

## The colours

| Legend | Colour | Meaning |
| --- | --- | --- |
| **P1**, **P2** | dark and light blue | the father's two homologs |
| **M1**, **M2** | dark and light green | the mother's two homologs |
| **Untransmitted** | grey | a relative's homolog that was not passed down to the family, or one CoGA could not place |

Which homolog is P1 and which is P2 is arbitrary (it comes from the phasing). What matters is that the
same physical haplotype keeps its colour across the whole family, so you can follow it from a
grandparent to an embryo.

**The risk line.** The disease haplotype is marked by a line under the band:

| Line | Legend | Meaning |
| --- | --- | --- |
| Solid red | Affected | the affected haplotype for a dominant or an X-linked model |
| Dashed orange | Carrier | a recessive carrier haplotype: an affected member carries two, a carrier one |

On the genome overview the risk line appears only on the ROI's chromosome: which homolog carries the
disease allele is known only at the ROI.

---

## How relatives are coloured

Trio phasing only anchors the **nuclear family**: the father, the mother and their children or
embryos. Relatives (grandparents, aunts, uncles, cousins) are not part of that phasing, and their roles
do not say which side of the family they belong to (a paternal grandmother is stored as a mother). So
CoGA recolours every relative from the raw phased genotypes:

1. It starts from the nuclear family, already coloured.
2. It walks the pedigree from parent to child and matches each relative's two homologs against the
   member they connect to (identity by descent). The shared homolog takes that member's colour; the
   other homolog is greyed as untransmitted.

A paternal grandmother therefore gets one coloured homolog (the one she shares with the father) and one
grey. That is how you see which paternal haplotype carries a dominant allele.

The matching follows recombinations: a haplotype keeps its colour but can move to the other lane at a
crossover. A member CoGA cannot place with confidence is shown entirely grey, never in the wrong colour.

### Single-parent (donor) families

CoGA supports embryos with one known parent (a single woman, or a couple using a donor gamete) when the
disorder runs in the known parent's family. The known parent's two homologs are the founders, phased by
the grandparents; the embryos' known-parent lane is coloured and the donor lane is grey.

---

## Finding the disease haplotype

| Model | How CoGA finds it | Needs |
| --- | --- | --- |
| Dominant, X-linked dominant | The one haplotype that every affected member and obligate carrier shares at the ROI. | At least 2 affected members or obligate carriers. |
| Recessive | On each parent's side, the haplotype shared by the affected members at the ROI. | At least 1 affected member. Carriers are not used. |
| X-linked recessive | The haplotype the affected males share; without affected males, the affected females' haplotype on each side. | Affected members. |

Only members recorded as **not a carrier** remove a false candidate in the dominant models. An
unaffected member whose carrier status is unknown is not used.

If this does not lead to exactly one haplotype (for recessive, one on each side), the model is
**uninformative**: no risk line is drawn and every embryo reads *Uninformative*.

---

## The embryo call (at the ROI)

For each embryo, CoGA compares the haplotypes it carries at the ROI with the disease haplotype(s). The
call appears as a badge in the **Family members** table on the family page.

| Badge | Meaning |
| --- | --- |
| **Affected / at risk** | Carries the disease haplotype as the model requires: the dominant haplotype, both recessive haplotypes, or the X-linked combination for its sex. |
| **Carrier** | Recessive: carries one of the two carrier haplotypes, and its other homolog, seen across the ROI, is not the other one. X-linked, female: carries it on one side. |
| **Unaffected** | Carries none of the disease haplotypes, and its own haplotype is seen across the ROI. |
| **Uninformative** | No call is made: the disease haplotype could not be resolved, or the embryo's own haplotype does not cover the ROI. |

### What a call rests on

A call that the embryo carries a disease haplotype needs only that haplotype, seen anywhere in the ROI.
A call that it does not needs the embryo's own haplotype: *Unaffected*, and the clear side of a
*Carrier* call, need a homolog from that parent seen at every position of the ROI. The parent sides
needed are those a disease haplotype was found on. So the donor side of a single-parent family is not
needed, and a male on chrX is called on his one X, from his mother.

If, on a side the call needs, the embryo has no block at the ROI, a block over only part of it, a grey
lane, or a homolog the phasing has not confirmed, it reads *Uninformative*.

**X-linked recessive, sex not recorded.** With the mother's risk haplotype a son is affected and a
daughter a carrier. So an embryo whose sex is not recorded is called both ways. If the two calls agree,
that is the call. If they differ, CoGA assumes neither sex: the embryo reads *Affected / at risk* when
either call is, and *Uninformative* otherwise. Record the embryo's sex to resolve it.

Three warnings can sit next to the badge:

- **⚠ recombination** — a haplotype block boundary lies inside the ROI or within 250 kb of it. The
  embryo's haplotype may change across the locus; use the markers to see where the breakpoint falls.
- **⚠ uninformative** — no call is made. Its tooltip says why: no disease haplotype could be resolved
  at the ROI, or the embryo's own haplotype does not cover it.
- **⚠ sex unknown** — an X-linked recessive call depends on the embryo's sex, which is not recorded. Its
  tooltip gives the call for a son and for a daughter.

If the haplotypes at the ROI cannot be loaded, the embryo shows *⚠ segregation not derived*: its call
and its warnings are then unknown, not absent.

---

## Review ROI markers

**Review ROI markers →** on the family page opens a members × markers grid of the raw phased genotypes
across the ROI. Use it to re-check a surprising call against the genotypes:

- see which markers drive the haplotype assignment, and how many are informative for the embryos;
- spot a single marker that disagrees with its neighbours: phasing or imputation noise, not a real
  recombination;
- see impossible transmissions (Mendelian errors), shaded darker orange.

The markers are computed only for the parents' own children (in a single-parent family, the known
parent's children). Relatives appear on the track but carry no marker dots.

### Per-child checks

Per child, CoGA reports two numbers:

- **Informative sites** — the number of sites where the child and the parents all have a phased
  genotype. This counts every such site, not only the sites that tell the haplotypes apart.
- **Mendelian error rate** — the share of those sites where the child's genotype cannot come from the
  parents. A clear rate points to a sample swap or a wrong pedigree: resolve it before trusting any
  call. (With one known parent, an error is a child that shares no allele with that parent.)

Both parents and the child heterozygous is consistent, just uninformative, and is not an error.

---

## What the family needs

- **Phased imputed genotypes** (GLIMPSE2) for the couple and the embryos, and for any relatives you
  want coloured.
- A **pedigree** with correct parentage, roles and sex, including the grandparents, who phase the
  parents and so anchor the disease haplotype.
- The **affected and carrier status** of the members that define the disease haplotype.
- The **inheritance model** and the **ROI**.

---

## Known limitations

- **Recessive single-parent families are uninformative at the ROI.** A recessive call needs both
  parental risk haplotypes, and the donor side is unknown. The known parent's risk haplotype is still
  coloured.
- **Relatives stay grey on chrX, chrY and the mitochondrion.** The relative matching assumes two
  homologs at every site. The nuclear family keeps its colours there.
- **Very large regions.** On a very large region the marker dots are hidden and the track asks you to
  zoom in; the blocks still show. Coloured relative blocks may then end at the last site with evidence.
