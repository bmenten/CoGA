The **Haplotype track** (in the chromosome view) is CoGA's preimplantation-genetic-testing (PGT)
surface. It colours every family member's two haplotypes by which grandparental founder they descend
from, identifies the haplotype that carries the disease allele, and from that derives — *per embryo*
— whether it inherited it.

> **Derived, not entered.** The founder colours, the relatives' colouring, the disease haplotype,
> and each embryo's affected / carrier / unaffected call are all *computed* from the phased
> genotypes and the pedigree. The only inputs are the pedigree (roles, parentage, sex), the recorded
> affected / carrier status, the inheritance model, and the region of interest.

### The two layers

- **Cleaned haplotype blocks** — the colour-coded inheritance blocks. Each member shows their two
  homologs; a block recolours at every recombination breakpoint. This is the smoothed, easy-to-read
  interpretation layer.
- **Raw phased-marker overlay** — one dot per informative imputed marker, **with no binning or
  smoothing**, drawn over the blocks. This is the diagnostic layer: it exposes isolated phasing
  switches, jitter at boundaries, and the exact marker where a crossover occurs — so you can confirm
  a breakpoint is real and spot artifacts before trusting a call.

### The colour code

Colour encodes which of the four founder haplotypes (one per grandparent line) a block descends
from:

| Colour | Meaning |
| --- | --- |
| **Dark blue** | Paternal founder homolog 0 |
| **Light blue** | Paternal founder homolog 1 |
| **Dark green** | Maternal founder homolog 0 |
| **Light green** | Maternal founder homolog 1 |
| **Grey** | *Untransmitted* or *unknown* — a homolog not inherited from a placed founder, or one that could not be placed. No founder identity. |
| **Red (overlay)** | The **dominant** affected haplotype shared by the affected members at the locus. |
| **Orange (overlay)** | A **recessive carrier** haplotype — an affected individual has two, a carrier has one. |

The dark/light split is the two grandparental haplotypes on a side. The absolute dark-vs-light label
is arbitrary (it comes from the raw phasing); what matters is **consistency** — the same physical
grandparental haplotype keeps its shade across the whole family, so you can trace one haplotype from
an affected grandparent down to an embryo.

### How relatives are coloured (pedigree IBD)

The stored blocks are only meaningful for the index nuclear family (the parents and their
children/embryos), where trio phasing grounds the four founders. A relative's stored blocks are
*not* trustworthy, and CoGA's flat role model would even paint a paternal grandmother (stored as `role =
mother`) entirely green. So CoGA **recomputes every relative from the raw phased genotypes**:
starting from the coloured nuclear core, it walks the pedigree and identity-by-descent (IBD) matches
each relative's homologs against the connected member. The shared homolog inherits that founder
colour; the untransmitted homolog is greyed. A paternal grandmother thus gets one homolog coloured
(whichever the affected father shares) and one grey — which is exactly what identifies *which*
paternal haplotype carries the dominant allele. Matching is recombination-aware (a haplotype keeps
its colour but jumps lanes at a crossover), and any member that cannot be confidently placed is left
fully grey rather than mis-coloured.

### Single-parent (donor) families

For embryos with only one known parent (e.g. a single woman or a couple using a donor gamete) while
the disorder segregates in the known parent's family, the core is anchored on the embryos. The known
parent's two homologs are the founders (traced up to the affected grandparents, who phase them), and
the embryos are coloured by IBD against the known parent — the **known-parent lane is coloured and
the donor lane is greyed**.

### The derived embryo call (at the ROI)

CoGA infers the disease haplotype(s) from the affected/carrier members and the inheritance model —
for a dominant disorder, the single haplotype the affected members share at the locus; for a
recessive disorder, a carrier haplotype on each parental side; X-linked is sex-aware. It then
classifies each embryo at the ROI:

- **Affected / at-risk** — carries the disease haplotype as the model requires (the dominant
  haplotype, both recessive carrier haplotypes, or the sex-appropriate X-linked combination).
- **Carrier** — recessive: carries one of the two carrier haplotypes (X-linked female: on one side).
- **Unaffected (non-carrier)** — carries none of the disease haplotype(s).
- **Uninformative** — the disease model could not be resolved to a unique haplotype, so no call is
  made.

> **Two warnings make a call unsafe — and the raw markers are how you catch them.** A *recombination
> close to the ROI* means the haplotype at the variant may differ from the flanks; use the overlay
> to see exactly where the breakpoint falls. And *uninformative markers at the ROI* mean the
> haplotype there is interpolated, not observed — check the marker overview before trusting the
> call.

### The ROI marker overview and QC

A members × markers grid shows the raw phased genotypes across the ROI, colour-coded by haplotype,
with each member's informative-marker count — the table to **re-check a surprising embryo call**
against the underlying genotypes and to spot per-site artifacts (a lone marker disagreeing with its
neighbours = phasing noise, not a real crossover). Per child, CoGA also reports two QC numbers from
the jointly-informative sites: the **informative-site count** (more is stronger evidence) and the
**Mendel-error rate** — the fraction of sites where the child's genotype is impossible given the
parents'. A non-trivial Mendel rate flags a likely sample swap or wrong pedigree and should be
resolved before any haplotype call is trusted. (The raw overlay and overview are computed only for
the index parents' own children; relatives appear on the track but carry no marker dots, as the
parent-of-origin logic is meaningless for them.)

### Known limitations

- **Recessive single-parent families are uninformative at the ROI** — a recessive call needs both
  parental risk haplotypes, but the donor side is unknown, so the embryo call is deliberately
  *uninformative* (the known-parent risk haplotype is still coloured).
- **Relatives are greyed on the sex chromosomes and mtDNA** — the IBD logic assumes two homologs per
  site, which hemizygous X, the non-recombining Y, and the mitochondrion break; the nuclear core
  keeps its role-based colouring there.
- **Very large regions truncate the overlay** — when the whole-chromosome marker fetch is capped,
  the raw overlay is suppressed (with a “zoom in” state) rather than drawn part-way; the cleaned
  blocks still render. Zoom into the ROI to restore it.

[Haplotype segregation reference (founder colouring, derived embryo calls, QC)](/docs/reference/haplotype-segregation "further-reading")
