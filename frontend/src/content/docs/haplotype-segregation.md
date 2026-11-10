# Haplotype segregation analysis — reference

The **Haplotypes** track (chromosome view and genome overview) is CoGA's tool for preimplantation
genetic testing (PGT). It traces the four parental haplotypes through the family, finds the haplotype
that carries the disease allele, and derives for each embryo whether it inherited it. It also shows the
raw markers, so you can catch the recombinations and artifacts that would make a call unsafe.

How to open and read the track: [user guide](/docs), section *Haplotype segregation (PGT)*. This page
holds the rules.

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

A switch error in a parent's statistical phasing swaps that parent's two haplotypes from one site on.
Every embryo then seems to cross over at the same place on that parent's side. CoGA reads a phase
switch in the parent when the couple's children switch together within 2 Mb — all of three children,
or all but one of four or more — and their other switches make that unlikely by chance. It then swaps
that parent's haplotypes back from there. The spurious crossovers disappear, and a child that did not
switch there turns out to have crossed over there. The swap applies to every child, so no embryo's call
changes. The raw dots follow the corrected phase too; the genotypes in their tooltip stay as called.
CoGA looks for phase switches on the autosomes only: on chrX a parent's phase switch is not undone, and
every embryo still seems to cross over there.

The parent's track marks each correction with a solid line and a small triangle in the warning colour;
hovering it says how many children switched and where. When a correction lies inside or close to the
ROI, or up to 2 Mb after it (the phase may have switched that much before the children's switches show
it), every embryo shows **⚠ phase corrected**. The switch point is known only roughly, so the call
across the locus is less certain, and an embryo's own crossover there may not show. Review the ROI
markers.

### The blocks on chrX

Outside the pseudo-autosomal regions (PARs) a man has one X. The blocks read him by the rule the phasing
QC uses (see [At an X-linked locus](#phasing-qc-at-the-roi)): his call of one allele, or of the same
allele twice, is that one allele; a call of two different alleles is not read.

- A son's X is his mother's. His blocks show the homolog he inherited from her, read where she is
  heterozygous, and none of his father's, who gave him a Y.
- A daughter receives her father's one X and one of her mother's two. Her paternal lane is her father's
  X, the same in every daughter, so it never changes. Her maternal homolog is read where her father's
  allele is known.
- A member whose sex is not recorded is read as having two copies, as on the autosomes.

Each block lies in PAR1, in the X between the PARs, or in PAR2, so a haplotype read in a PAR is not
carried over the X. CoGA takes the PAR bounds from the family's assembly (GRCh38, GRCh37 or
T2T-CHM13v2.0); on an assembly whose PAR bounds it does not have, every member is read as having two
copies.

The blocks are made when the phased genotypes are imported, with the sexes recorded then. Record every
member's sex before the import. After recording or correcting a sex, import the phased genotypes again,
or the blocks on chrX keep the sexes they were made with.

---

## The colours

| Legend | Colour | Meaning |
| --- | --- | --- |
| **P1**, **P2** | dark and light blue | the father's two homologs |
| **M1**, **M2** | dark and light green | the mother's two homologs |
| **Untransmitted** | grey | a relative's homolog that was not passed down to the family, or one CoGA could not place |

Which homolog is P1 and which P2 is arbitrary (it comes from the phasing). What matters: the same
physical haplotype keeps its colour across the whole family, so you can follow it from a grandparent to
an embryo.

**The risk line.** A line under the band marks the disease haplotype:

| Line | Legend | Meaning |
| --- | --- | --- |
| Solid red | Affected | the affected haplotype for a dominant or an X-linked model |
| Dashed orange | Carrier | a recessive carrier haplotype: an affected member carries two, a carrier one |

On the genome overview the risk line appears only on the ROI's chromosome: which homolog carries the
disease allele is known only at the ROI.

---

## How relatives are coloured

Trio phasing anchors only the **nuclear family**: the father, the mother and their children or
embryos. Relatives (grandparents, aunts, uncles, cousins) are not part of that phasing, and their roles
do not say which side of the family they are on (a paternal grandmother is stored as a mother). So CoGA
recolours every relative from the raw phased genotypes:

1. It starts from the nuclear family, already coloured.
2. It walks the pedigree from parent to child and matches each relative's two homologs against the
   member they connect to (identity by descent). The shared homolog takes that member's colour; the
   other homolog is greyed as untransmitted.

A paternal grandmother therefore gets one coloured homolog (the one she shares with the father) and one
grey. That shows which paternal haplotype carries a dominant allele.

The matching follows recombinations: a haplotype keeps its colour but can move to the other lane at a
crossover. A member CoGA cannot place with confidence is shown entirely grey, never in the wrong colour.

### Relatives of unknown degree

A PGT index is often known only to be on the mother's or the father's side. Link it as **Related to**
that parent (the dotted arc in the pedigree). CoGA then tests it one chromosome at a time over the
whole genome. If it shares one of the parent's haplotypes along nearly every chromosome, it is that
parent's own parent or child, and it is coloured along the genome as a parent or child is.

A more distant relative (a sibling, an aunt, a cousin) shares a haplotype with the parent only in
stretches. On imputed low-pass genotypes such a stretch cannot be found marker by marker: it looks too
much like what unrelated people share by chance. So CoGA reads such a relative at the ROI only, as
PGT-M reads a distant reference: from the informative markers on both sides of the ROI.

- When the 3 Mb on each side both show the relative carrying the same one of the parent's haplotypes,
  that haplotype is coloured on the relative across the ROI and those 3 Mb. The relative's other
  haplotype and the rest of the chromosome stay grey.
- When either side does not show it clearly (no haplotype shared, both shared, a crossover near the
  ROI, or too few informative markers), the relative stays grey.
- A changed ROI is read again.

A grey index does not help find the disease haplotype. To have it coloured, record how it is related
through members that have data, if they are in the family.

### Single-parent (donor) families

CoGA supports embryos with one known parent (a single woman, or a couple using a donor gamete) when the
disorder runs in the known parent's family. The known parent's two homologs are the founders, phased by
the grandparents; the embryos' known-parent lane is coloured and the donor lane is grey.

### Relatives on chrX

Outside the PARs a man has one X, which he passes to his daughters and never to his sons. CoGA colours
the relatives on chrX by the sexes recorded in the pedigree, one stretch at a time: in PAR1 and PAR2 as
on an autosome, and between the PARs as follows.

- A man's one X takes the colour of the homolog he carries of the woman he connects to, on his one
  band. The mother's father carries her paternal X whole; a woman's son carries her two X's in turn,
  switching at her crossovers.
- A woman connected to a man (his mother, or his daughter) carries his X on one lane, which takes his
  colour; her other lane is grey.
- Two women are matched as on the autosomes.
- Nothing passes from a man to his son: the father's father, and a son reached only through his
  father, stay grey between the PARs.

CoGA reads a man's share from the markers where the woman is heterozygous: his allele names the X it
lies on. The X's he is read to carry must agree with nearly all of them, as only a real parent and
child do.

In a donor family the embryos are coloured the same way: a known mother's son carries her X's in turn;
a known father's daughter carries his X; his son, whose X is the donor's, stays grey.

A member whose sex is not recorded is matched as having two copies. The colours follow the sexes
recorded now (on the genome overview, once its background refresh has run). chrY and the mitochondrion
are not coloured, nor is chrX of an assembly whose PAR bounds CoGA does not have; it has those of
GRCh38, GRCh37 and T2T-CHM13v2.0.

---

## Finding the disease haplotype

| Model | How CoGA finds it | Needs |
| --- | --- | --- |
| Dominant, X-linked dominant | The one haplotype that every affected member and obligate carrier shares at the ROI. | At least 2 affected members or obligate carriers. |
| Recessive | On each parent's side, the haplotype shared by the affected members at the ROI. | At least 1 affected member. Carriers are not used. |
| X-linked recessive | The haplotype the affected males share; without affected males, the affected females' haplotype on each side. | Affected members. |

In the dominant models, only members recorded as **not a carrier** remove a false candidate. An
unaffected member whose carrier status is unknown is not used.

If this does not lead to exactly one haplotype (for recessive, one on each side), the model is
**uninformative**: no risk line is drawn and every embryo reads *Uninformative*.

---

## The embryo call (at the ROI)

For each embryo, CoGA compares the haplotypes it carries at the ROI with the disease haplotype(s). The
call appears as a badge in the **Family members** table on the family page. The server works out the
calls from the stored haplotype blocks over the ROI and 1 Mb on each side. The tracks' risk line and
risk state follow the same rules. **Sign out report** freezes the same calls into the signed version,
each with:

- the haplotypes it carries at the ROI;
- its warnings;
- its QC at the ROI over the same window (the informative sites, the Mendelian error rate and the
  markers on each flank that the ROI marker review shows), and that QC's verdict against the thresholds
  (see [Phasing QC at the ROI](#phasing-qc-at-the-roi));
- the haplotype file it was read from.

| Badge | Meaning |
| --- | --- |
| **Affected / at risk** | Carries the disease haplotype as the model requires: the dominant haplotype, both recessive haplotypes, or the X-linked combination for its sex. |
| **Carrier** | Recessive: carries one of the two carrier haplotypes, and its other homolog, seen across the ROI, is not the other one. X-linked, female: carries it on one side. |
| **Unaffected** | Carries none of the disease haplotypes, and its own haplotype is seen across the ROI. |
| **Uninformative** | No call is made: the disease haplotype could not be resolved, or the embryo's own haplotype does not cover the ROI. |

### What a call rests on

A call that the embryo carries a disease haplotype needs only that haplotype, seen anywhere in the ROI.
A call that it does not needs the embryo's own haplotype: *Unaffected*, and the clear side of a
*Carrier* call, need a homolog from that parent seen at every position of the ROI. Only the parent
sides a disease haplotype was found on are needed. So the donor side of a single-parent family is not
needed, and on chrX outside the PARs a male is called on his one X, from his mother.

**X-linked recessive, a daughter.** An unaffected father's X is taken as not at risk: an unaffected man
does not carry a fully penetrant X-linked recessive disorder, so a daughter inherits a healthy X from
him. Her call rests on her maternal X: *Carrier* with the mother's risk haplotype, *Unaffected* with the
mother's other X seen across the ROI. This is the owner's confirmed rule for a fully penetrant X-linked
recessive disorder (2026-10-14).

**Males in the pseudo-autosomal regions.** In PAR1 and PAR2 a male has two copies: his mother's, and
his father's from the father's X or Y. There he is called like any other embryo, on both lanes, so a
paternal risk haplotype at a PAR locus (for example SHOX in PAR1) is seen. At an ROI that crosses a PAR
boundary, and on an assembly whose PAR bounds CoGA does not have, a male is called as having two
copies: a call that then needs his father's side reads *Uninformative* where that side has no data.

If, on a side the call needs, the embryo has no block at the ROI, a block over only part of it, a grey
lane, or a homolog the phasing has not confirmed, it reads *Uninformative*.

**X-linked recessive, sex not recorded.** With the mother's risk haplotype a son is affected and a
daughter a carrier. So an embryo whose sex is not recorded is called both ways. If the two calls agree,
that is the call. If they differ, CoGA assumes neither sex: the embryo reads *Affected / at risk* when
either call is, and *Uninformative* otherwise. Record the embryo's sex to resolve it.

These warnings can sit next to the badge:

- **⚠ phasing QC** — the embryo's phasing QC at the ROI does not meet its thresholds (see [Phasing QC
  at the ROI](#phasing-qc-at-the-roi)). Its tooltip names each measure, its value and its threshold.
- **⚠ both homologs** — the embryo's markers at the ROI mix both of a parent's homologs, so it is not
  called (see [Markers that mix both of a parent's homologs](#markers-that-mix-both-of-a-parents-homologs)).
  Its tooltip gives the share of markers off one homolog and the threshold.
- **⚠ QC not counted** — no phasing QC was counted at the ROI (the embryo is not recorded as a child of
  the couple, or the ROI and its flanks hold more sites than the marker review reads), so the call was
  not checked against the thresholds. That is not a pass.
- **⚠ recombination** — a haplotype block boundary lies inside the ROI or within 250 kb of it. The
  embryo's haplotype may change across the locus; use the markers to see where the breakpoint falls.
- **⚠ phase corrected** — a parent's phase switch was undone inside or close to the ROI (see [Phase
  switches in a parent](#phase-switches-in-a-parent)). Its tooltip says how many children switched and
  where.
- **⚠ uninformative** — no call is made. Its tooltip says why: no disease haplotype could be resolved
  at the ROI, the embryo's own haplotype does not cover it, or the haplotype blocks are outdated (see
  [What the family needs](#what-the-family-needs)).
- **⚠ sex unknown** — an X-linked recessive call depends on the embryo's sex, which is not recorded. Its
  tooltip gives the call for a son and for a daughter.

If the embryo calls at the ROI cannot be loaded, the embryo shows *⚠ segregation not derived*: its call
and its warnings are then unknown, not absent.

### Phasing QC at the ROI

Each called embryo's phasing QC is checked against three thresholds over the window the call reads: the
ROI and 1 Mb on each side. Counting there matters most when the ROI is the variant itself, as the PGT
pipeline gives it: no marker lies in an ROI that is a single variant, while a Mendelian error or a
missing allele nearby weakens the call. A call that does not meet a threshold gets **⚠ phasing QC**
beside its badge. The call itself is not changed or hidden, and sign-out is not held up: the warning
tells you to check the call in the ROI markers, and you decide. **Sign out report** freezes into the
signed version each embryo's verdict (met; did not meet, with each measure; or not counted) and the
thresholds in force.

| Measure, over the ROI and 1 Mb on each side | Threshold | Why this value |
| --- | --- | --- |
| **Informative sites**: sites where both parents and the embryo have a phased genotype (for a son on chrX outside the PARs: his mother and he) | at least 50 | With fewer, a single Mendelian error already puts the rate above 2%, so the rate cannot tell one genotyping error from a swap. With no error among 50 sites, a rate of 6% or more is ruled out with 95% confidence. |
| **Mendelian error rate**: the share of those sites whose genotype the embryo cannot have from these parents | at most 2% | Where CoGA's Sample QC starts to warn, and what the haplotype track marks. A higher rate at the locus points to a sample swap, a wrong pedigree, contamination, or a deletion or a uniparental segment there. |
| **Markers on each flank of the ROI, on each parent's side the call reads**: sites left of the ROI (below its start) and right of it (above its end) where the homolog inherited from that parent is read; the ROI's own sites count on neither flank | at least 6 on each flank | The ESHRE PGT Consortium's good practice recommendations for monogenic disorders (2020) ask for at least six informative SNPs on each side of the locus when the embryo is read from linked markers alone. Many markers on one flank do not make up for too few on the other. |

**These values are provisional.** The developers proposed them. The laboratory director and QA are to
confirm or change them before clinical use, and the validation is to test them. The laboratory is to
weigh one difference from the ESHRE recommendation: CoGA reads imputed low-pass genotypes, not
amplified loci. The window (1 Mb on each side) is the one the call reads; the owner chose it on
2026-10-30.

Only the parental sides the call reads are checked for markers: the side or sides a disease haplotype
was resolved on (both for a recessive disorder), and for a son on chrX outside the PARs his mother's. A
side the call does not read (the other parent's in a dominant family, a donor's) is not checked, so it
cannot raise a warning about evidence the call does not use. Without a resolved disease haplotype no
side is read, and only the informative sites and the Mendelian error rate are checked. The thresholds
are set in `backend/app/services/pgt_embryo_qc.py`; a change there changes what the family page warns
about and what a signed version freezes.

**At an X-linked locus.** On chrX outside the PARs a man has one X, and the QC counts read him so: his
call, written as one allele (`1`) or as the same allele twice (`1|1`), is that one allele.

- A son's X is his mother's, so he is counted against her alone. His informative sites are those where
  she and he have a genotype. A Mendelian error is an allele she does not carry, or a call of two
  different alleles, which a son cannot have on his one X (a sex mix-up or contamination). His father
  gives him a Y, so a father and son are neither informative nor an error.
- A daughter's paternal X is her father's one X, which tells none of his homologs apart, so it gives
  no marker on his side.
- A man's call of two different alleles is not read: at that site his daughters are not counted.
- A member whose sex is not recorded is read as having two copies, as on the autosomes: record the sex.
- On an assembly whose PAR bounds CoGA does not have, every member is read as having two copies.

The ROI markers shade an impossible transmission by the same rule.

### Markers that mix both of a parent's homologs

An embryo's haplotype blocks show one homolog of each parent wherever it is. Take an embryo that
carries both of one parent's homologs at the ROI, as in a trisomy of that chromosome from an error in
the first meiotic division. Its blocks are just as clean, showing one of the two, and it would be called
as not carrying the other, which it does carry. Its own markers show it. With one homolog of a parent
they agree with it at nearly every site, or with two either side of a crossover. With both, the
imputation, which reads every embryo as two copies, assigns the sites to either homolog, and they mix.

So each side's markers over the ROI and 1 Mb on each side get a **discordance**: the share of them off
the best reading of the window as one homolog, or two with one crossover between (so a crossover alone
does not raise it). Where it is **above 10%, over at least 20 markers, on a parent's side the call
reads**, the embryo is not called: it reads *Uninformative* with **⚠ both homologs**, whatever its
blocks say, and its haplotype tracks show no risk state. This is the one QC measure that changes a call
rather than warning beside it (the owner's decision of 2026-10-30): a call made from one homolog when
the embryo carries both could be a false *Unaffected*. Read the embryo's copy-number and APCAD tracks,
as for any aneuploidy. **Sign out report** freezes each side's discordance and the threshold.

It is a safety net under your aneuploidy review, not an aneuploidy detector:

- a trisomy with two copies of the same homolog (from an error in the second meiotic division near the
  centromere, or after fertilisation) leaves the markers in agreement;
- a trisomy of another chromosome is not looked at;
- a low-level mosaic may stay under the threshold.

The threshold is provisional, like the others. On a real PGT family's embryos, a chromosome with one
homolog of a parent stayed well under 10% wherever it had markers, while a chromosome carrying both of
the mother's homologs read above it nearly everywhere. The laboratory director and QA are to confirm
it.

---

## Review ROI markers

**Review ROI markers →** on the family page opens a members × markers grid of the raw phased genotypes
across the ROI and 1 Mb on each side (the window the calls and their phasing QC read). The markers
outside the ROI are dimmed. Zoom and pan to narrow or move the window. Where it holds more than 800
informative markers, the 800 nearest the ROI are drawn. Use it to re-check a surprising call against
the genotypes:

- see which markers drive the haplotype assignment, and how many are informative for the embryos;
- spot a single marker that disagrees with its neighbours: phasing or imputation noise, not a real
  recombination;
- see impossible transmissions (Mendelian errors), shaded darker orange.

The markers are computed only for the parents' own children (in a single-parent family, the known
parent's children). Relatives appear on the track but carry no marker dots.

Each member has a row per homolog. A call of one allele, such as a man's one X outside the PARs, shows
its allele once (on the father's first row; on anyone else's second, such as a son's maternal row) and
– on the other row. Where a man has one X, his call of the same allele twice is shown the same way, as
the QC reads it, and a son's Mendelian error follows [At an X-linked locus](#phasing-qc-at-the-roi).
Hovering the marker dots in the chromosome view shows each member's call by the same rule, its two
homologs side by side: a father's one X reads, for example, `A | –` and a son's `– | G`.

### Per-child checks

Per child, CoGA reports two numbers:

- **Informative sites** — the number of sites where the child and the parents all have a phased
  genotype. This counts every such site, not only the sites that tell the haplotypes apart. On chrX
  outside the PARs a son is counted against his mother alone (see [At an X-linked
  locus](#phasing-qc-at-the-roi)).
- **Mendelian error rate** — the share of those sites where the child's genotype cannot come from the
  parents. A clear rate points to a sample swap or a wrong pedigree: resolve it before trusting any
  call. (With one known parent, an error is a child that shares no allele with that parent.)

Both parents and the child heterozygous is consistent, only uninformative, and not an error. Each
embryo call's QC verdict reads these numbers and each parent side's flank markers over the window the
review opens on, against the thresholds of [Phasing QC at the ROI](#phasing-qc-at-the-roi).

---

## What the family needs

- **Phased imputed genotypes** (GLIMPSE2) for the couple and the embryos, and for any relatives you
  want coloured.
- A **pedigree** with correct parentage, roles and sex, including the grandparents, who phase the
  parents and so anchor the disease haplotype. On chrX the blocks are made with the sexes recorded
  when the phased genotypes are imported (see [The blocks on chrX](#the-blocks-on-chrx)).
- **Blocks built for the pedigree as it is.** The haplotype blocks are built when the phased genotypes
  are imported, from the couple, the couple's children and each member's sex as the pedigree then
  records them. If any of these changes afterwards, the blocks are outdated until the phased genotypes
  are imported again: the family page warns, every embryo reads *Uninformative*, the haplotype tracks
  of the genome overview and the chromosome view show *Outdated blocks: no risk state* and draw no risk
  haplotype, and a sign-out records why. A change of affected status does not outdate them, as the
  calls read it when they are made; nor does a member added who is not in the phased file. Blocks
  without a record of what they were built for count as outdated too.
- The **affected and carrier status** of the members that define the disease haplotype.
- The **inheritance model** and the **ROI**.

---

## Known limitations

- **Recessive single-parent families are uninformative at the ROI.** A recessive call needs both
  parental risk haplotypes, and the donor side is unknown. The known parent's risk haplotype is still
  coloured.
- **Relatives stay grey on chrY and the mitochondrion**, and on chrX of an assembly whose PAR bounds
  CoGA does not have. The nuclear family keeps its colours there.
- **Very large regions.** On a very large region the marker dots are hidden and the track asks you to
  zoom in; the blocks still show. Coloured relative blocks may then end at the last site with evidence.
