Most candidates are found on the **Small variants** page. The filters take a genome down to a short
list; the *Phenotype priority* preset puts it in order.

### The default view

The page opens with the **Phenotype priority (Exomiser-style)** preset on the **Mendeliome** panel:

- HIGH or MODERATE impact;
- gnomAD exome, genome and popmax frequency 1% or below, and at most 10 homozygotes and 10 hemizygotes —
  a ClinVar pathogenic or likely pathogenic record overrides these cut-offs;
- ClinVar benign and likely benign left out;
- carried (het or hom) by each affected member, with genotype quality (GQ) 20, depth 10, allele
  balance 0.2 and 4 alt reads or more.

> **The default view hides variants.** A variant outside the Mendeliome, a common variant, a
> low-quality call, or a variant an affected member does not carry is not shown. Press **Clear all
> filters** before you conclude that a variant is not in the data.

### Filters and presets

The filters sit under **Advanced filters**, grouped as on the screen: **Phenotype**, **Structural
second hit**, **Inheritance**, **Pathogenicity**, **Annotations**, **In silico**, **Frequency**, **Call
quality**, **Locations**, **Exclude** and **Review and curation**.

- **Annotations** groups the VEP consequences as seqr does: nonsense, essential splice site,
  frameshift, missense, in frame, synonymous, extended splice site, other, motif feature and regulatory
  feature. A group's own box ticks or clears all its terms. Beside them are the VEP impacts (HIGH,
  MODERATE, LOW, MODIFIER), the SpliceAI minimum, and *Canonical only*, *MANE only* and *LoF only*. A
  saved search's term that no box offers is listed under *Other selected terms*, ticked.
  - Its *Quick* choice sets the consequences and the impacts together, and leaves the SpliceAI
    minimum and the three switches as they are. *All* clears both. *High impact* ticks HIGH, and
    *Moderate to high impact* HIGH and MODERATE, with no consequence. *All coding* ticks the coding
    consequences (missense, frameshift, stop gained, stop lost, start lost, splice acceptor, donor
    and region, in-frame insertion and deletion, protein altering, synonymous and coding sequence
    variant), with no impact. Any other selection shows as *Custom*.
- **Frequency** has a slider per population. In its header, AF sets every population at once; AC and
  H/H set gnomAD's allele count and its homozygote and hemizygote counts. AF shows the maximum shared
  by the populations that have one, or *mixed* when they differ.
- **Call quality** sets the minimum genotype quality, allele balance, depth and alt reads of the
  members' calls, for all members at once or per member.
  - With *Affected only* on, only the affected members' calls are held to them, so a parent's
    reference call is not held to an allele balance it cannot have. Switched off, every member gets
    the minimums the affected members share.
  - *Pass variants only*, under *Filter value*, keeps a variant when every member's call that carries
    it passed its caller's FILTER (PASS, or no filter set), whatever *Affected only* says. Only the
    family's members count: a sample taken out of the family, or not yet added, holds nothing back. A callset
    of one file per sample gives each call its own FILTER, so a parent's reference call (DeepVariant's
    *RefCall*) holds nothing back. A multi-sample VCF gives the FILTER of the site.
  - The quick choices *High quality* and *Passing* turn it on, as in seqr; *All variants* turns it
    off.
- **Review and curation** keeps a variant by the class its review, or its compound-het pair's review,
  shows. A class word typed as the classification (*VUS*) gives its class; otherwise the review's class
  tag gives it, the most severe of two; otherwise the classification as typed. So *Likely pathogenic -
  class 4* also finds a variant classified by its class tag alone, and not one classified as *VUS -
  class 3* beside the *Likely pathogenic - class 4* tag: that variant shows class 4. Capitals do not
  matter.

For de novo and dominant, a male's call on chrX or chrY outside the pseudo-autosomal regions (`1` or
`1/1`) counts as one copy. A location entry that cannot be read (for example an end before its start)
is named under its field, and nothing is searched until you fix it. A per-sample minimum (GQ, DP, AF or
AD alt) that is not a number fails the search, and the message names it.

The annotation filters must all hold on one transcript of the variant: impact and consequence, the
transcript, HGVS, *Canonical only*, *MANE only* and *LoF only*, the ClinVar status, the in-silico scores
and the frequencies. So with *MANE only* and HIGH impact, a variant whose HIGH-impact transcript is not
its MANE transcript is left out.

- Under **In silico**, one of the predictors you set is enough. The SpliceAI minimum, set under
  **Annotations**, counts as one of them. A SpliceAI score at that minimum also passes the impact and
  consequence filter.
- A SIFT or PolyPhen choice keeps a transcript whose prediction is exactly that one, whatever score VEP
  wrote after it. *deleterious* keeps `deleterious(0.01)` but not `deleterious_low_confidence(0.03)`,
  which is a choice of its own; *tolerated* leaves out `tolerated_low_confidence`.
- The gene, the rsID and the excluded ClinVar statuses apply to the variant as a whole. A gene keeps
  every variant annotated with it, including one VEP places just outside the gene (upstream or
  downstream), ranked or not.

A card, a table row and a CSV row describe one transcript of the variant: MANE Select, else the
canonical one, else the most severe. The filters may have held on another. For example, *deleterious*
keeps a variant whose MANE transcript SIFT calls tolerated when a second transcript is deleterious;
HIGH or MODERATE impact keeps one that is synonymous on its MANE transcript and missense on another.
In that case:

- the card (under its transcript) and the table row (under its effect) say **Filters met on**, with
  the transcript the filters held on and its consequence, LoF and in-silico predictions that differ
  from the row's. The row's own values stay as they are;
- the card's transcript list marks that transcript *Filters met*;
- the CSV gives it in its last columns, *Filter-matched transcript* to *Filter-matched PolyPhen*.

The ranking and **ACMG classify** read the transcript the row describes.

> **MANE only needs GRCh38.** *MANE only* keeps a variant with a MANE Select or MANE Plus Clinical
> transcript. VEP sets these flags on GRCh38 only, so on a GRCh37 or T2T family *MANE only* keeps
> nothing.

The built-in presets are *Phenotype priority*, *Dominant strict*, *Dominant relaxed*, *Expanded carrier
screening*, *Compound het*, *Recessive hom*, *Recessive broad*, *Any affected* and *ClinVar review*. A
preset saves the recipe, not the result; save your own to standardise a search. Compound-heterozygous
candidates are shown as pairs. Up to 100 matches show as cards, more as a table.

*Expanded carrier screening* is for couples only. It keeps:

- the genes where both partners carry a rare (below 1%) high- or moderate-impact variant;
- the variants a female partner carries on chrX outside the pseudo-autosomal regions, which put a son
  at risk on their own.

A ClinVar pathogenic or likely pathogenic call overrides the frequency cut-off, so a common founder
variant stays in. A partner whose sex is not recorded counts as female. Mitochondrial variants are not
part of it, as mtDNA passes from the mother alone.

Your saved searches are shared with the monogenic NIPT page. A search saved there can carry filters
that only the NIPT list applies: an inheritance view such as *De novo in the fetus*, the maternal/fetal
categories, the minimum confidence. This page does not send them and shows no chip for them; it names
them once under the active filters. *De novo in the fetus* is not
*De novo / dominant*, which reads the family members' own genotypes.

> **A search can stop part-way.** Compound-het and recessive pairing and *Expanded carrier screening*
> read at most 5,000 candidates before they filter, ranked or not. When a search reaches that limit, a
> warning above the results says *"Results may be incomplete"* and after how many candidates the search
> stopped. The count is then a lower bound (for example *12+*), and a match beyond the limit is not
> shown. Narrow the filters with a region, a gene panel or a gene until the warning is gone.

### The phenotype ranking

With *Phenotype priority* on, a **Score** column appears and the rows are ranked, best first. The score
combines the variant's own evidence (impact and predictors, ClinVar, rarity, fit with the pedigree) with
how well its gene matches the affected members' HPO terms. A ✦ marks a gene that matches. Hover the
score, or read the card, for the breakdown: *Variant*, *Pathogenicity*, *Rarity* and *Phenotype*, the
compatible inheritance modes and the matched phenotypes.

- A gene without Monarch data gets no phenotype credit and ranks lower; its *Variant* score is not
  affected. Look further down for strong variants, or untick **Phenotype prioritization**.
- Without HPO terms on the affected members, the ranking uses the variant evidence only.
- The ranking changes the order of a search, not what it finds. With an inheritance mode or
  *Expanded carrier screening* it ranks the same variants and compound-het pairs the search finds
  without it. A pair stays a pair, listed where its better-ranked variant ranks; each variant shows
  its own rank.
- The score orders candidates within the family; it is not a probability of pathogenicity.
- A warning that *"Ranking may be incomplete"* means more candidates matched than CoGA ranks at once
  (it says how many were ranked), so the best one may be missing. Narrow the filters: a region, a
  panel, a gene, or a tighter frequency or impact.

**Why the ranking can be instant.** CoGA stores a ranking once computed; a later open shows
*⚡ Prioritised ranking served from cache* and when it was computed. It is recomputed automatically
when anything it depends on changes: the phenotypes, the pedigree, the panel's genes and regions, the
filters (including review-tag filters), the family's variant data, or the reference data (the Monarch
release, each import of the HPO ontology, gene constraint, and the gene positions a panel or an
excluded gene uses).

Narrowing from the Mendeliome to a smaller panel reuses the broader ranking, ranked from 1 within the
smaller panel, as long as the Mendeliome's genes and regions have not changed since that ranking was
computed. The smaller panel gets its own ranking instead when it reaches a variant the broader ranking
lacks (through a region of its own, such as the position PanelApp gives for a gene), or keeps one
variant of a compound-het pair without the other.

**Download CSV** exports the list, with *Priority* and *Rank* columns when the ranking is on.

### Also hit by a structural variant

A small variant whose gene is also hit by an SV carries an **SV badge** (for example `SV: DEL`), with a
`trans` or `cis` chip when a phase was decided. **Also hit by an SV**, under *Structural second hit*,
keeps only these genes. The phase comes from read phasing or from the parents' genotypes, by the same
rule as for compound-het pairs; the reference gives the rule. Each SV of the gene gets its own phase,
listed in the badge's explanation. A deletion in trans with the SNV marks the gene *effectively
biallelic*, whatever the other SVs are.

[SNV + SV compound heterozygosity reference (the badge, trans or cis)](/docs/reference/sv-second-hit "further-reading")
