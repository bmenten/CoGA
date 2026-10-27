Tables are for finding candidates; viewers are for checking them in their genomic context. From the
family page:

- **Genome view** — the whole genome with its variants and tracks.
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
