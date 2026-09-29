The variant explorer answers cohort-level questions that a single family cannot: *in how many
samples does this pathogenic variant occur? which families carry a variant in this gene? which
reported variants exist across the database?* Each row is a unique variant aggregated across every
project you can access.

### What each row shows

- Gene, variant, consequence, most-severe classification, and aggregated tags.
- Total carrier samples with a heterozygous / homozygous split, and the number of distinct families.

### How you use it

- Filter with the same dimensions as the family search — gene, consequence, ClinVar, frequency,
  in-silico — plus tags and ACMG classification to surface, for example, every variant tagged
  *Reported*, or every individual with a pathogenic variant in a certain gene.
- Click a heterozygous, homozygous, or family count to drill into the carriers, grouped by family,
  and link straight to the family workspace.
- Optionally add a per-sample genotype filter to find variants a specific sample carries.
- Imputed calls are left out by default but can be included with a toggle.

> **Counts are relative to your access.** Aggregations only span the projects you can see, so
> internal-frequency context reflects your accessible cohort.
