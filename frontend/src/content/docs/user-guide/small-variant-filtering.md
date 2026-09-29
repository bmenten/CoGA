The small-variant page is where most candidate-finding happens. Filters are grouped so you can move
from a broad genome to a short candidate list quickly, then save the recipe as a preset.

### Filter dimensions

- **Location** — gene symbols, genomic regions/intervals, or a gene panel. Write an interval as
  `chr13:32315086-32400266` (an en dash, or a single position, also works), one per line. An entry
  that cannot be read, such as a BED line (BED is 0-based) or an end before its start, is named under
  its field and nothing is searched: an interval is never dropped from the search unnoticed.
- **Inheritance** — de novo / dominant, recessive (homozygous), compound heterozygous, and X-linked
  models, plus expanded carrier screening for couples.
- **Variant type** — SNV, indel, or MNV.
- **Consequence and impact** — HIGH / MODERATE / LOW / MODIFIER and specific effects (missense,
  frameshift, stop gained, splice, and so on).
- **ClinVar and classification** — pathogenic through benign, conflicting interpretations, and your
  own ACMG classifications.
- **Population frequency** — gnomAD exomes/genomes/popmax and TOPMed allele frequencies, allele
  counts, and homozygote/hemizygote caps.
- **In-silico evidence** — CADD, REVEL, SpliceAI, SIFT, and PolyPhen thresholds, amongst others.
- **Transcript scope** — restrict to canonical, MANE, or loss-of-function transcripts.
- **Tags and notes** — include or exclude review tags, or require notes.

### Compound heterozygotes and per-sample genotypes

Compound-heterozygous candidates are grouped as pairs so you can assess both hits in a gene
together. Per-sample genotype and quality thresholds (genotype, QUAL, DP, AF, AD) let you encode
segregation expectations across the family directly in the search.

### Cross-type “second hit”: a gene also hit by a structural variant

Recessive disease is often caused by a small variant on one allele and a *structural* variant on the
other — most importantly an SNV plus an overlapping deletion, which removes the second copy and
makes a “heterozygous” SNV effectively biallelic. Because small variants and structural variants are
filtered in separate workspaces, these pairs are easy to miss, so CoGA flags them for you.

- Any small variant whose gene is **also hit by a structural variant** carries an **SV badge**
  showing the SV type and the zygosity in affected individuals.
- A **trans / cis** verdict says whether the two hits sit on opposite alleles (a
  compound-heterozygous candidate) or the same one — inferred from family segregation, and read
  directly from the haplotypes when the SVs are phased (shown as a distinct badge).
- A deletion in trans with a heterozygous SNV is highlighted as **effectively biallelic** — the
  highest-yield case.
- The **Structural second hit** filter (*“Also hit by an SV”*) restricts the results to just these
  genes, so you can screen for the pattern in one pass.

[SNV + SV compound heterozygosity (cross-type second hit, trans/cis phasing)](/docs/reference/sv-second-hit "further-reading")

### Bi-sample partner analysis

A special case is the coupled partner analysis — a dedicated filter for (expanded) preconception
carrier screening. Only genes where both partners carry a variant meeting the filter criteria are
returned.

> **Presets save the recipe, not the result.** Built-in presets cover common patterns (dominant,
> recessive, compound het, carrier screening, ClinVar review, and *phenotype priority* — see the
> next section); save your own to standardise variant filtration.
