# Haplotype segregation analysis — reference

The **Haplotype track** (in the chromosome view) is CoGA's preimplantation-genetic-testing (PGT)
surface. It traces the four grandparental haplotypes through the family so you can read off **which
embryos inherited the disease haplotype(s)**, and it surfaces the artifacts — recombinations near the
locus, uninformative markers — that would make that call unsafe. The track opens on the region of
interest (ROI) with flanking context.

This is the in-depth reference. For the workflow-level overview see the [in-app user guide](/docs) section *Haplotype segregation analysis*.

---

## Everything coloured is derived

The single most important thing to understand is that **nothing on the track is typed in**. The
founder colours, the relatives' colouring, the disease haplotype, and each embryo's
affected / carrier / unaffected call are all **computed** from the phased genotypes and the pedigree.
Your only inputs are:

- the **pedigree** — roles, parentage, and recorded sex;
- the recorded **affected / carrier status** of family members;
- the **inheritance model** (dominant, recessive, X-linked dominant/recessive);
- the **region of interest** (the locus you are analysing).

From those, CoGA derives the rest. You do not paint haplotypes or assert which one is the disease
haplotype — the analysis does, and tells you how confident it is.

---

## The clinical question

A couple — or a single parent plus a donor — produces a set of embryos, and a known disorder
segregates in one or both families. Per embryo, you are asking:

- **Dominant** — did it inherit the single affected haplotype shared by the affected parent and
  affected relatives at the locus?
- **Recessive** — did it inherit a carrier haplotype from *each* side (→ at risk / affected), from
  *one* side (→ carrier), or from *neither*?
- **X-linked** — sex-aware: a male inheriting the affected maternal haplotype is at risk; a female
  inheriting it on one side is a carrier.

The analysis answers this by colouring every individual's two haplotypes by which grandparental
founder they descend from (identity-by-descent), then identifying which founder haplotype carries the
disease allele.

---

## The two layers

The track draws two complementary layers for every member, and the relationship between them is the
whole point.

1. **Cleaned haplotype blocks** — the colour-coded inheritance blocks. Each member has two lanes (their
   two homologs); a block is a stretch of chromosome descending from one founder haplotype, recolouring
   at each recombination breakpoint. This is the **interpretation** layer — the smoothed, easy-to-read
   answer to "which haplotype is this".
2. **Raw phased-marker overlay** — one dot per informative imputed marker, drawn on top of the blocks,
   **with no binning, smoothing, or voting**. This is the **diagnostic** layer. Because it is raw, it
   exposes exactly what the blocks hide: isolated phasing switches, jitter at recombination boundaries,
   and the precise marker where a crossover occurs.

The blocks are the cleaned version of the markers; the markers are there to let you **audit** the
blocks. Use the overlay to confirm a breakpoint is real and to spot artifacts before trusting an
embryo call.

---

## The colour code

Colour encodes **which founder haplotype** a block descends from. There are four founders — the index
couple's four homologs, one per grandparent line:

| Lane / colour | Meaning |
| --- | --- |
| **Dark blue** | Paternal founder homolog 0 |
| **Light blue** | Paternal founder homolog 1 |
| **Dark green** | Maternal founder homolog 0 |
| **Light green** | Maternal founder homolog 1 |
| **Grey** | *Untransmitted* or *unknown* — a homolog not inherited from a placed founder, or one CoGA could not place. Carries no founder identity. |

The dark/light split within a side is the two grandparental haplotypes on that side. The **absolute**
dark-vs-light assignment is arbitrary — it comes from the raw phasing orientation. What matters is
**consistency**: the same physical grandparental haplotype keeps the same shade across everyone in the
family, so you can trace one haplotype from an affected grandparent down to an embryo.

### Risk overlay

On top of the founder colour, the locus-carrying haplotype(s) are highlighted so the disease haplotype
stands out:

| Overlay | Meaning |
| --- | --- |
| **Red** | The **dominant** affected haplotype — the single haplotype shared by the affected / obligate members at the ROI. |
| **Orange** | A **recessive carrier** haplotype. An affected individual has two orange haplotypes; a carrier has one. |

The risk overlay is derived (see *Disease-haplotype inference* below), not entered.

---

## How each member is coloured (pedigree IBD)

Trio phasing only grounds the index **nuclear family** — the father, the mother, and their direct
children / embryos. There the four founder homologs get their stable colour, and each child's paternal
(hap1) / maternal (hap2) homolog is coloured to match the founder it came from.

**Relatives** (a grandparent, an aunt/uncle, a cousin) are not part of that trio phasing, so their
stored blocks are biologically meaningless and are never trusted as-is. Worse, CoGA's role model is
flat — a paternal grandmother is stored with `role = mother`, which a naïve colourer would paint
entirely green. So CoGA **recomputes every relative's colour from the raw phased genotypes**:

1. Start from the nuclear core (the founders and their children), already coloured.
2. Walk the pedigree along parent–child edges. For each relative reached from an already-coloured
   member, **identity-by-descent (IBD) match** the relative's two homologs against that member's two
   homologs. The shared homolog inherits the member's colour; the relative's other homolog is
   *untransmitted* and is **greyed out**.

So a paternal grandmother gets **exactly one homolog coloured** (whichever blue the affected father
shares with her) and the other grey. That is what lets you read off *which* paternal haplotype carries
the dominant disease allele: the affected grandparent's coloured homolog is the disease haplotype, and
you follow that colour down to the embryos.

The matching is **recombination-aware**. A relative's shared haplotype switches lanes at each meiotic
crossover; CoGA recovers those switch points and only commits a switch once a run of contradicting
markers is both long enough and wide enough — so real crossovers split the track but isolated phasing
noise does not. At a crossover the disease-carrying colour keeps its identity but jumps lanes (grey
follows it). Any member CoGA cannot confidently place is rendered **entirely grey** — never
mis-coloured.

### Single-parent (donor) families

CoGA supports embryos with only **one known parent** (a single woman, or a couple using a donor
gamete) while the disorder segregates in the known parent's family. The core is **anchored on the
embryos**: the index parents are the embryos' parents, and the donor side is simply absent.

- The **known parent's two homologs are the founders** (one per grandparent), coloured by tracing them
  up to the affected grandparent.
- The embryos are the known parent's children, so the same relative-IBD machinery colours the
  **known-parent-derived lane** and **greys the donor lane**.

The grandparents are essential here — they are what phase the known parent so the disease haplotype can
be identified.

---

## Disease-haplotype inference

The disease haplotype(s) are inferred from the recorded affected / carrier status and the inheritance
model:

- **Dominant** — the single haplotype **shared at the ROI by the informative members** (affecteds plus
  obligate carriers). Taking the intersection of the haplotypes those members carry leaves the one
  signature they all share; known unaffected non-carriers subtract false candidates. Highlighted
  **red**.
- **Recessive** — a carrier haplotype on **each side**: the paternal-side haplotype shared by the
  affecteds and the maternal-side one. Both highlighted **orange**. An affected embryo carries both; a
  carrier carries one.
- **X-linked recessive** — from affected males (hemizygous) where available, else from affected females
  per side. Sex-aware.
- **X-linked dominant** — the single shared affected haplotype, as for dominant.

If the members do not resolve to a unique haplotype, the model is **uninformative**: no risk overlay is
drawn and embryo calls fall back to *uninformative*.

---

## The derived embryo classification (at the ROI)

For each embryo, CoGA compares the haplotypes it carries at the ROI against the inferred disease
haplotype(s) and assigns one of four states. **This call is derived; it is not an entered status.**

| State | Meaning |
| --- | --- |
| **Affected / at-risk** | Carries the disease haplotype as the model requires — the dominant affected haplotype, **both** recessive carrier haplotypes, or the sex-appropriate X-linked at-risk combination. |
| **Carrier** | Recessive: carries **one** of the two carrier haplotypes. X-linked female: carries it on one side. |
| **Unaffected (non-carrier)** | Carries none of the disease haplotype(s). |
| **Uninformative** | The disease model could not be resolved (no unique haplotype; or, recessive, only one side resolved), so no call can be made. |

### Two warnings to read before trusting a call

The classification is read at a single point — the ROI — so two situations make it unsafe, and the
raw-marker overlay is how you catch both:

- **Recombination close to the ROI.** A crossover near the locus means the haplotype the embryo carries
  *at the variant* may differ from what it carries a short distance away. Use the overlay to see exactly
  where the breakpoint falls relative to the ROI; a breakpoint inside or adjacent to the locus warrants
  caution and confirmation.
- **Uninformative markers at the ROI.** If the locus sits in a stretch with few or no informative
  markers, the haplotype identity there is interpolated from the flanks rather than directly observed.
  Sparse informative markers at the ROI weaken the call — check the marker overview below.

---

## The ROI marker overview

Alongside the track, CoGA shows a **per-site marker overview**: a members × markers grid of the raw
phased genotypes across the ROI, colour-coded by haplotype, with the **informative-marker count** for
each member. This is the table you use to *re-check* a surprising embryo call against the underlying
data:

- Confirm the genotypes that drive the haplotype assignment at and around the ROI.
- See **how many informative markers** actually distinguish the haplotypes near the locus — a handful
  of markers over a wide span is weak evidence.
- Spot per-site inconsistencies — a single marker disagreeing with its neighbours is a phasing /
  imputation artifact, not a real recombination.

The overlay and overview are deliberately **raw** — one call per site, no binning — because hiding the
noise would hide exactly the signal you need to validate the clean blocks. They are computed only for
the **index parents' own children** (single-parent families: the one known parent's children). Running
the parent-of-origin transmission logic on a relative would be biologically backwards and produce
coincidental, wildly-switching noise, so relatives appear on the track — their lineage block is the
meaningful view — but carry no marker dots.

---

## Per-child quality-control signals

Per child, CoGA reports two QC numbers from the sites where both parents and the child have a valid
phased genotype (jointly informative sites):

- **Informative-site count** — how many sites actually distinguish the haplotypes. More is better; a low
  count over the region means weak phasing evidence.
- **Mendel-error rate** — the fraction of jointly-informative sites where the child's genotype is
  **impossible** given the parents' alleles. A non-trivial rate is a red flag for a **sample swap or
  wrong pedigree** and should be resolved before any haplotype call is trusted. (For a single-parent
  family, a Mendel error is the child sharing *no* allele with the known parent.)

A genuine Mendelian inconsistency is distinct from benign parent-of-origin ambiguity — e.g. both
parents and the child heterozygous. That is perfectly consistent, just uninformative, and is **not**
counted as an error.

---

## Inputs required

For the track to be meaningful, the family needs:

- **Phased imputed genotypes** for the index couple and their embryos (the GLIMPSE2-imputed callset),
  and for any relatives you want coloured.
- A **pedigree** with correct parentage, roles, and sex — including the grandparents, who phase the
  parents and so anchor the disease haplotype.
- The recorded **affected / carrier status** of the informative members.
- The **inheritance model** and the **region of interest**.

If the informative members do not resolve a unique disease haplotype, or if the parent / embryo
genotypes are missing, the track still renders the founder colouring but the embryo calls return
*uninformative* — a deliberately safe result rather than a guess.

---

## Known limitations

- **Recessive single-parent (donor) families are uninformative at the ROI.** Classifying a recessive
  embryo needs *both* parental risk haplotypes, but the donor side is unknown, so the embryo call
  returns **uninformative**. The known-parent risk haplotype is still coloured.
- **Relatives are greyed on sex chromosomes and mtDNA.** The IBD logic assumes two homologs at every
  site. Hemizygous X (in males), the non-recombining Y, and the mitochondrion break that assumption, so
  on non-autosomes CoGA leaves relatives grey rather than risk mis-colouring them. The nuclear core
  still keeps its role-based colouring there.
- **Truncation on very large regions.** Whole-chromosome marker fetches are capped. When the cap is
  hit, the raw-marker overlay is suppressed (with a "too many sites — zoom in" state) rather than drawn
  partway across a block, and coloured relative blocks are clamped to the last site with evidence (the
  rest greyed). The cleaned blocks still render — zoom into the ROI to restore the full marker overlay.
