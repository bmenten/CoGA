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

### Phase switches in a parent

A switch error in a parent's statistical phasing swaps the parent's two haplotypes from one site on.
Every embryo then seems to cross over at the same place on that parent's side. Where all of three, or
all but one of four or more, of the couple's children switch together within 2 Mb, and that is not
likely by chance from their other switches, CoGA reads a phase switch in the parent and swaps that
parent's haplotypes back from there: the spurious crossovers disappear, and a child that did not switch
there turns out to have crossed over there. The same swap applies to every child, so no embryo's call
changes; the raw dots follow the corrected phase too, while the genotypes in their tooltip stay as
called.

The parent's track marks each correction with a solid line and a small triangle in the warning colour;
hovering it says how many children switched and where. When a correction lies inside or close to the
ROI, or up to 2 Mb after it (the phase may have switched that much before the children's switches
show it), every embryo shows **⚠ phase corrected**: where the phase switched is known only roughly,
so the call across the locus is less certain, and an embryo's own crossover there may not show.
Review the ROI markers.

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

### Relatives of unknown degree

A PGT index is often known only to be on the mother's or the father's side. Linked as **Related to**
that parent (the dotted arc in the pedigree), it is coloured along the genome when it turns out to be
that parent's own parent or child: when it shares one of the parent's haplotypes along nearly every
chromosome, which CoGA tests one chromosome at a time over the whole genome. It is then coloured as a
parent or child is.

A more distant relative (a sibling, an aunt, a cousin) shares a haplotype with the parent only in
stretches, and on imputed low-pass genotypes a stretch cannot be found marker by marker: it looks too
much like what unrelated people share by chance. CoGA reads such a relative at the ROI only, as PGT-M
reads a distant reference: from the informative markers on both sides of the ROI. Where the 3 Mb on
each side both show the relative carrying the same one of the parent's haplotypes, that haplotype is
coloured on the relative across the ROI and those 3 Mb; the relative's other haplotype and the rest of
the chromosome stay grey. Where either side does not show it clearly (no haplotype shared, both shared,
a crossover near the ROI, or too few informative markers), the relative stays grey. A changed ROI is
read again. A grey index does not help find the disease haplotype; record how it is related through
members that have data, if they are in the family, to have it coloured.

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

Next to the call, **⚠ recombination** says a crossover falls inside or close to the ROI, and **⚠ phase
corrected** that a parent's phase switch was undone there (see [Phase switches in a
parent](#phase-switches-in-a-parent)).

### What a call rests on

A call that the embryo carries a disease haplotype needs only that haplotype, seen anywhere in the ROI.
A call that it does not needs the embryo's own haplotype: *Unaffected*, and the clear side of a
*Carrier* call, need a homolog from that parent seen at every position of the ROI. The parent sides
needed are those a disease haplotype was found on. So the donor side of a single-parent family is not
needed, and outside the pseudo-autosomal regions a male on chrX is called on his one X, from his
mother.

**Males in the pseudo-autosomal regions.** In PAR1 and PAR2 a male has two copies: his mother's, and
his father's from the father's X or Y. There he is called like any other embryo, on both lanes, so a
paternal risk haplotype at a PAR locus (for example SHOX in PAR1) is seen. CoGA takes the PAR bounds
from the family's assembly (GRCh38 or GRCh37). At an ROI that crosses a PAR boundary, and on an
assembly whose PAR bounds CoGA does not have, a male is called as having two copies: a call that then
needs his father's side reads *Uninformative* where that side has no data.

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
