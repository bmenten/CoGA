Tables are for finding candidates; viewers are for confirming and inspecting them and diving deeper
into their genomic context. Each viewer reads the tracks that were imported for the family.

- **Genome overview** — whole-genome context for variants and tracks.
- **Chromosome view** — a single chromosome with coverage, segments, and variant tracks; the ROI
  opens here with ±1 Mb of flanking context.
- **Circos plot** — genome-wide structural relationships at a glance.
- **IGV** — read-level confirmation against the reference for a specific locus.

Opening any of these from a variant list — the IGV and View links on a row or card, or the Genome
and Circos buttons above the structural-variant table — opens a new browser tab. The list you were
working through keeps its filters, sort order, and scroll position instead of re-running the query
each time you come back from a locus.

### Small-variant track: colours and rows

In the Chromosome view, each small variant is drawn as a dot. Its colour encodes the predicted
consequence, with ClinVar taking precedence:

- **Functional impact** — high impact is **light orange**, medium (moderate) is **light green**, and
  low / modifier is **light gray**.
- **ClinVar overrides the impact colour** — benign / likely benign is **light blue**, and pathogenic
  / likely pathogenic is **red**. Other ClinVar states (uncertain, conflicting) keep the impact
  colour.
- **A review tag colour**, when you have tagged the variant, takes priority over both of the above.

When the displayed sample is a child with a parent in the family (or phasing is available), the
track splits into three rows by parental origin:

- **Top row** — variants on the paternal haplotype (hap1).
- **Middle row** — undetermined / unknown parental origin.
- **Bottom row** — variants on the maternal haplotype (hap2).

Origin is read from the parent genotypes (Mendelian inheritance) when both parents are present,
falling back to the phased haplotype order of the variant otherwise. Homozygous, de-novo, and
ambiguous variants stay in the middle row. These are the *raw* calls — hover any dot to see its
impact, ClinVar significance, and parental origin.

### Haplotype track

The haplotype track enables visual inspection of recombinations. Raw (imputed) informative markers
are shown, and the (imputed) haplotype blocks are colour-coded according to the pedigree
information. For individuals affected by a dominant disorder, the shared haplotype blocks are
colour-coded in red. Individuals affected by a recessive disorder have two affected haplotypes
(coloured orange), while carrier parents — or other carriers in the pedigree — carry a single
affected (orange) haplotype.

> **Viewers only show what was imported.** Coverage, segment, APCAD, and haplotype tracks appear
> when the corresponding sample data exists; an empty track usually means that layer was not loaded,
> not that the viewer failed.
