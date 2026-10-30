Tables are for finding candidates; viewers are for checking them in their genomic context. From the
family page:

- **Genome view** — chromosomes 1–22, X and Y with their variants and tracks. The mitochondrion,
  too small to see there, is in the chromosome view (chrM) and on the **mtDNA analysis** page.
- **Chromosome view** — one chromosome with coverage, segments, APCAD, haplotypes and variant tracks.
  Clicking the ROI on the family page opens it here with 1 Mb on each side.
- **Circos plot** — genome-wide structural relationships at a glance, drawn on chromosomes 1–22, X and
  Y of the family's assembly. For an assembly it cannot draw, the page says why and draws nothing.
- **IGV viewer** — the reads at a locus, on the genome of the family's assembly. For an assembly it
  has no genome for, the page says so and opens none, rather than another assembly's genome.

The **IGV** and **View** links on a variant row or card, and the **Genome** and **Circos** buttons above
the structural-variant table, open in a new browser tab, so your filtered list stays as it was.

### The small-variant track

In the chromosome view each small variant is drawn by its class, with a legend on the track:

| Mark | Meaning |
| --- | --- |
| Red diamond | ClinVar pathogenic or likely pathogenic |
| Orange triangle | HIGH impact |
| Green dot | MODERATE impact |
| Grey dot | LOW impact or other |
| Hollow blue square | ClinVar benign or likely benign |

A variant with a conflicting or uncertain ClinVar record is drawn by its impact. A review tag draws a
ring in the tag's colour around the mark; the mark itself does not change. Hover a mark for its impact,
ClinVar status and parental origin.

When the sample shown has a parent in the family, the track has three rows: variants inherited from the
father (top), from the mother (bottom), and of unknown origin (middle: homozygous, de novo or
ambiguous). The origin comes from the parents' genotypes when both are there, otherwise from the phased
genotype. Without a parent in the family there is one row.

> **Viewers show only what was imported.** A coverage, segment, APCAD or haplotype track appears when
> that data was loaded for the sample, so an empty track usually means the layer is missing, not that
> the viewer failed. When a view holds too many variants to draw, the track says so: zoom in or filter.

Coverage is drawn one track per CNV caller, on the axis of what the track holds. The callers' log2
ratios share the log2-ratio axis, with the gain and loss colours and the caller's segments. Read depth is
drawn on a depth axis from 0, without those colours or segments: a coverage BED declared as read depth,
and a HiFiCNV depth file without autosomal depth, which gives no baseline to take a ratio against, so its
depths are kept as read (the track's header says so). A coverage BED says what it holds when it is
loaded; one that does not is drawn on its own range without an axis, and its header warns that it is
read as neither depth nor a log2 ratio.

In a PGT family, judge each embryo's aneuploidy and large (>10 Mb) structural variants yourself, on its
segment, copy-number (coverage) and APCAD tracks. CoGA shows the tracks but makes no aneuploidy call.

For an aneuploidy, tick **Parental origin (aneuploidy)** under *Tracks*, in the genome view and in the
chromosome view. It is offered for an embryo whose PGT pipeline run wrote its segregation table, and is
off until you ask for it. It draws the embryo's sites as the pipeline's own plots do, in two panels:
**Mother heterozygous** (the pipeline's group 2: the mother heterozygous, the father homozygous) and
**Father heterozygous** (group 3). Each site is at the embryo's allele fraction, from 0 to 1, coloured by
the other parent's homozygous allele: amber for the reference (2B, 3B), the heterozygous parent's
colour for the alternate (2A, 3A). The genome view draws every chromosome side by side, so an aneuploid
one stands out against the others; the chromosome view draws the region in view. Read each panel against
its guide lines:

| On a parent's panel | Reading |
| --- | --- |
| bands at 0, ½ and 1 | one of that parent's homologs (disomy) |
| only ⅓ and ⅔, nothing at 0 or 1 | both of that parent's homologs (heterodisomy) |
| 0, ⅓, ⅔ and 1 | two copies of one of its homologs (isodisomy), or, in a trisomy, the panel of the parent who gave one copy of three |

Read the result against the centromere on the chromosome view's ideogram:

- heterodisomy at the centromere points to an error in meiosis I;
- isodisomy at the centromere, turning to heterodisomy past a crossover, points to meiosis II;
- isodisomy along the whole chromosome points to a mitotic error, usually as a mosaic, where the bands
  sit between those of a disomy and a trisomy.

Which parent gave the extra copy of an isodisomic trisomy, the APCAD track shows. The pipeline keeps only
sites the embryo was read deeply at, so a trisomic chromosome has many more sites than the others, and a
monosomy almost none. CoGA draws the sites and makes no call from them.

The PGT haplotype track is explained under [Haplotype segregation](#haplotype-segregation).

### The structural-variant tracks

The genome and chromosome views draw every SV that the structural-variant list shows, in one of six
rows:

| Row | SVs drawn there |
| --- | --- |
| DEL | Deletions of any subtype (`DEL:ME`), and CNVs that remove a copy |
| DUP | Duplications of any subtype (`DUP:TANDEM`), and CNVs that add a copy |
| INV | Inversions |
| INS | Insertions of any subtype (`INS:ME`) |
| BND | Breakends and translocations (`TRA`) |
| Other | Any other type, and CNVs that change no copy number, in grey |

A CNV is placed by the copy number stored for the sample's call, as the SV ranking scores it: a male
has one copy of chrX and chrY outside the pseudo-autosomal regions, and a CNV without a stored copy
number counts as a loss. Hover an SV for its type as the caller wrote it, a CNV's copy-number change,
and the call's copy number (`CN`). The Circos plot draws each SV the same way, from the calls of the
whole family.
