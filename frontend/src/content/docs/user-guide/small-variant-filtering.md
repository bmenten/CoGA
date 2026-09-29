The **Small variants** page is where most candidates are found. The filters take a genome down to a
short list; the *Phenotype priority* preset puts that list in order.

### The default view

The page opens with the **Phenotype priority (Exomiser-style)** preset on the **Mendeliome** panel:

- HIGH or MODERATE impact;
- gnomAD exome, genome and popmax frequency 1% or below, and at most 10 homozygotes and 10 hemizygotes —
  a ClinVar pathogenic or likely pathogenic record overrides these cut-offs;
- ClinVar benign and likely benign left out;
- carried (het or hom) by each affected member, with QUAL 20, depth 10, allele fraction 0.2 and 4 alt
  reads or more.

> **The default view hides variants.** A variant outside the Mendeliome, a common variant, a
> low-quality call, or a variant an affected member does not carry is not shown. Press **Clear all
> filters** before you conclude that a variant is not in the data.

### Filters and presets

The filters are grouped as on the screen: **Phenotype**, **Structural second hit**, **Inheritance**,
**Pathogenicity**, **Annotations** (with *Canonical only*, *MANE only* and *LoF only*), **In Silico**,
**Frequency**, **Locations**, **Exclude** and **Review and curation**. For de novo and dominant, a male's
call on chrX or chrY outside the pseudo-autosomal regions (`1` or `1/1`) counts as one copy. A location
entry that cannot be read (for example an end before its start) is named under its field, and nothing is
searched until you fix it.

The built-in presets are *Phenotype priority*, *Dominant strict*, *Dominant relaxed*, *Expanded carrier
screening* (couples only: genes where both partners carry a rare variant), *Compound het*, *Recessive
hom*, *Recessive broad*, *Any affected* and *ClinVar review*. A preset saves the recipe, not the result;
save your own to standardise a search. Compound-heterozygous candidates are shown as pairs. Up to 100
matches show as cards, more as a table.

### The phenotype ranking

With *Phenotype priority* on, a **Score** column appears and the rows come ranked, best first. The score
combines the variant's own evidence (impact and predictors, ClinVar, rarity, fit with the pedigree) with
how well its gene matches the affected members' HPO terms. A ✦ marks a gene that matches. Hover the
score, or read the card, for the breakdown: *Variant*, *Pathogenicity*, *Rarity* and *Phenotype*, the
compatible inheritance modes and the matched phenotypes.

- A gene without Monarch data gets no phenotype credit and ranks lower; its *Variant* score is not
  affected. Look further down for strong variants, or untick **Phenotype prioritization**.
- Without HPO terms on the affected members, the ranking uses the variant evidence only.
- The score orders candidates within the family; it is not a probability of pathogenicity.
- A warning that *"this ranking is incomplete"* means more candidates matched than CoGA ranks at once:
  narrow the filters (frequency, impact, a panel or an inheritance mode).

**Why the ranking can be instant.** CoGA keeps the ranking once it is computed; a later open shows
*⚡ Prioritised ranking served from cache* and when it was computed. It is recomputed automatically when
anything it depends on changes: the phenotypes, the pedigree, the panel, the filters (including
review-tag filters), the family's variant data, or the reference data (Monarch, HPO, gene constraint).
Narrowing from the Mendeliome to a smaller panel is served from the broader ranking.

**Download CSV** exports the list, with *Priority* and *Rank* columns when the ranking is on.

### Also hit by a structural variant

A small variant whose gene is also hit by an SV carries an **SV badge** (for example `SV: DEL`), with a
`trans` or `cis` chip when a phase was decided. **Also hit by an SV** under *Structural second hit* keeps
only these genes. Check a segregation-based `trans` before you rely on it: the reference explains why.

[SNV + SV compound heterozygosity reference (the badge, trans or cis)](/docs/reference/sv-second-hit "further-reading")
