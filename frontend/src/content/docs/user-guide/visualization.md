Tables are for finding candidates; viewers are for checking them in their genomic context. From the
family page:

- **Genome view** — the whole genome with its variants and tracks.
- **Chromosome view** — one chromosome with coverage, segments, APCAD, haplotypes and variant tracks.
  Clicking the ROI on the family page opens it here with 1 Mb on each side.
- **Circos plot** — genome-wide structural relationships at a glance, drawn on chromosomes 1–22, X and
  Y of the family's assembly. For an assembly it cannot draw, the page says why and draws nothing.
- **IGV viewer** — the reads at a locus.

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

The PGT haplotype track is explained under [Haplotype segregation](#haplotype-segregation).
