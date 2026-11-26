Most candidates are found on the **Small variants** page. The filters take a genome down to a short
list; the *Phenotype priority* preset puts it in order.

### The default view

The page opens with the **Phenotype priority (Exomiser-style)** preset on the **Mendeliome** panel:

- HIGH or MODERATE impact;
- gnomAD exome, genome and popmax frequency 1% or below, and at most 10 homozygotes and 10 hemizygotes;
- a ClinVar pathogenic or likely pathogenic record overrides the impact and frequency cut-offs, so a
  pathogenic synonymous or splice-region variant and a common founder variant stay in;
- ClinVar benign and likely benign left out;
- carried (het or hom) by each affected member, with genotype quality (GQ) 20, depth 10, allele
  balance 0.2 and 4 alt reads or more;
- inheritance *All, compound het grouped*: the compound-het pairs among the results are listed by
  gene, each gene where its best-ranked variant ranks, and every other variant on its own.

> **The default view hides variants.** A variant outside the Mendeliome, a common variant, a
> low-quality call, or a variant an affected member does not carry is not shown. Press **Clear all
> filters** before you conclude that a variant is not in the data.

### A family with several analyses

A family can hold several analyses, on the same DNA or on different samples of one person: short-read
and then long-read WGS, for instance. Each stays apart. When two or more of them have small variants
of their own, the page reads one analysis at a time: pick it in the list beside the family's name (the
analysis imported last is shown first). The list, its counts, the filters, the ranking and the CSV
export are that analysis's alone (a callset filter reads only the analysis's own callsets), and a
review you save is that analysis's review: a variant both callsets hold has a review in each
analysis, and each sign-out freezes its own. An inheritance filter reads the members the analysis's
package lists. The **Variant summary** offers the same list for its small-variant counts, and on the
workspace each curation line names the analysis whose reviews it counts. The **Structural variants**
page reads one at a time the analyses with structural variants of their own (the conventional NIPT of
each pregnancy), from a list of its own. The links between the family's pages keep the analysis you
read, and the genome view reads the analysis the page you came from named.

A member the analysis's package lists can still have no calls in it: long-read WGS of the proband
alone whose package lists the parents. A note above the results names such members. An inheritance
filter reads a call it does not find as reference, so while one is applied the note warns, for
instance: *The parents (MOTHER and FATHER1) have no calls in the long-read WGS analysis: the
inheritance filter reads their missing calls as reference, so it cannot leave out variants inherited
from them.* An affected member without calls leaves the filter no variant at all. The
**Inheritance** row says the same, and the CSV export names those members in its last column,
*Members without calls*.

### Filters and presets

The filters sit under **Advanced filters**, closed when the page opens, one category to a row across
the page: **Phenotype**, **Structural second hit**, **Inheritance**, **Pathogenicity**, **Annotations**,
**In silico**, **Frequency**, **Call quality**, **Locations**, **Exclude** and **Review and curation**.
Closed, **Advanced filters** says how many categories have a filter set. A row's bar says how many
filters its category holds and carries its quick choice at the right; click the bar for the
category's fields. A category with a filter set is marked red at the left of its bar. **Expand all**
and **Collapse all** open or close every row.

- **Inheritance** has the inheritance model as its quick choice. *All, compound het grouped* keeps
  every variant the other filters keep, as *All* does, and lists the compound-het pairs among them by
  gene, as *Compound heterozygous* does. Every other variant stays a single result. The *Phenotype
  priority* and *Any affected* presets set it.
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
  - A maximum count keeps a variant without that count, as absent from gnomAD. When such a variant
    may be in gnomAD after all, a note above the list says how many passed unchecked (*Passed the
    gnomAD count filter unchecked*); hover it for what it counts.
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
  class 3* beside the *Pathogenic - class 5* tag: that variant shows class 5. Capitals do not
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
- *ClinVar pathogenic / likely pathogenic overrules the frequency, consequence and in-silico
  filters*, under **Frequency** and on by default: a transcript with a ClinVar pathogenic or likely
  pathogenic record passes the impact, consequence and SpliceAI filter, the in-silico filter and
  every frequency and gnomAD count, whatever its values. The other filters still apply to it: the
  transcript, HGVS, *Canonical only*, *MANE only*, *LoF only*, the ClinVar status, the genotypes.

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

**Filters met on** names a transcript whose own impact, consequence and scores meet the filters: a
variant that only the ClinVar override keeps names none. The ranking and **ACMG classify** read the
transcript the row describes.

> **MANE only needs GRCh38.** *MANE only* keeps a variant with a MANE Select or MANE Plus Clinical
> transcript. VEP sets these flags on GRCh38 only, so on a GRCh37 or T2T family *MANE only* keeps
> nothing.

The built-in presets are *Phenotype priority*, *Dominant strict*, *Dominant relaxed*, *Expanded carrier
screening*, *Compound het*, *Recessive hom*, *Recessive broad*, *Any affected* and *ClinVar review*. A
preset saves the recipe, not the result; save your own to standardise a search. Compound-heterozygous
candidates, also under *All, compound het grouped*, and the findings of *Expanded carrier screening*
are listed by gene. Up to 100 results show
as cards, more as a table; *Table* and *Cards* above the list switch between the two. A gene counts as
one result.

At the top of the page, *Showing* counts the results, and *Variants* and *Genes* count what the
filters keep: the variants, and the genes they are in. Each variant counts toward the gene its card
names; a variant without a gene counts toward none. A count with a + is a lower bound. Above the
list, *Results* gives the number of results, followed by the number of variants when that differs (for
example *12 · 30 variants*).

A gene is a frame headed by the gene, as a single card is, with its diseases and inheritance once, and
holds each of its variants as the list of single variants shows it: as its full card, headed by its
place (*Variant 1 of 3*) and its change, or as a row of the table. Fold the frame to its heading and the
changes of its variants, or fold one card inside it.

A compound-het gene holds every variant of its pairs once. Its lines say which parent each variant came
from (*Paternal*, *Maternal*; *Parent not traced* where the parents' genotypes cannot tell), and each
card carries the same label. Then each pair that can be the recessive cause has a line of its own: its
phase, as the reads or the parents place it, and the pair review it holds, with the phase recorded
there. Two variants from the father and three from the mother are one gene with five variants and six
pairs: each paternal variant with each maternal one. Two variants from one parent are in cis and form
no pair.

A card takes its colour from ClinVar, here and on the monogenic NIPT page: light red when ClinVar calls
the variant pathogenic or likely pathogenic, light green when it calls it benign or likely benign.
Classifications that disagree, such as *uncertain significance & likely pathogenic*, are a conflict:
light orange when one of them is pathogenic or likely pathogenic. Any other conflicting, uncertain or
other ClinVar status, or none, leaves the card white. In the table a row is tinted the same way, more
faintly, and the ClinVar chip takes the same colour.

Under its heading, a card names up to three diseases its gene is linked to: Monarch's causal links,
those more sources back first; else the gene's OMIM diseases; else Monarch's associations, marked *not
causal*. Each name opens the disease in Monarch, or in OMIM for an OMIM disease. **+N more** opens
**More annotations & detail**, which lists every Monarch and OMIM link. *No gene–disease link cached*
means CoGA's copy of Monarch and OMIM holds none for the gene.

The line below names the gene's **Inheritance**: each mode a GenCC or ClinGen curation classified
Definitive, Strong, Moderate or Supportive gives it, dominant and recessive each in a colour of its
own. Hover a mode for the curations behind it. *None curated in GenCC or ClinGen* means the gene has a
disease but no such curation. Where ClinGen has curated the gene's dosage, **ClinGen dosage** shows its
haploinsufficiency (HI) and triplosensitivity (TS) scores and opens the curation. A gene on the ACMG
secondary-findings list has a chip such as *ACMG SF v3.3*; hover it for the list's phenotype, category,
inheritance and variants to report. *The ACMG SF list could not be read* means no chip does not tell
you the gene is off the list; the gene page says which.

The card's columns:

- **Variant** opens with the gene's ID and the transcript the card describes, then gives HGVS.g, HGVS.c
  and HGVS.p. HGVS.c and HGVS.p are on that transcript, without its accession. HGVS.g is VEP's (its
  HGVSg) where the annotation has it; otherwise it is written from the VCF record and is not
  HGVS-normalised. Hover the label to see which.
- **Frequencies**: a *filter* row under a gnomAD frequency means the site fails gnomAD's own QC (AC0,
  RF …): gnomAD itself does not trust that frequency.
- **In silico**: a dot before each prediction shows how it reads, in seqr's colours: green and olive
  benign, grey indeterminate, yellow, red and dark red pathogenic (supporting, moderate, strong). The
  thresholds are seqr's, from ClinGen's calibration of the predictors; SpliceAI uses the cut-offs of
  CoGA's ACMG evaluation. pLI and Missense Z describe the gene and have no dot.

*Expanded carrier screening* is for couples only. The couple is the two partners the family links as a
couple, else its mother and father, else its two people. Two samples linked as the same individual are
one person, never a couple: a family of one person sampled twice has no couple. A partner sampled twice
carries what either of their samples in the analysis you read carries. It keeps:

- the genes where both partners carry a rare (below 1%) high- or moderate-impact variant;
- the variants either partner carries on chrX outside the pseudo-autosomal regions, each on its own:
  a woman's carrier status alone puts a son at risk of an X-linked condition, and a man passes his X
  to every daughter.

Each gene is one result, with every variant either partner carries there: two, three or more. Its
frame says why the gene is a finding (*Both partners*, *X-linked*) and which variants each partner
carries; each variant's card names the partner who carries it, in that partner's colour.

A ClinVar pathogenic or likely pathogenic call overrides the frequency and impact cut-offs, so a common
founder variant and a pathogenic synonymous or splice-region variant stay in. A ClinVar benign or
likely benign variant is left out, so it never makes a gene one both partners carry a variant in. The
X-linked rule does not read the partners' recorded sex. Mitochondrial variants are not part of it, as
mtDNA passes from the mother alone.

Your saved searches are shared with the monogenic NIPT page. A search saved there can carry filters
that only the NIPT list applies: an inheritance view such as *De novo in the fetus*, the maternal/fetal
categories, the minimum confidence. This page does not send them and shows no chip for them; it names
them once under the active filters. *De novo in the fetus* is not
*De novo / dominant*, which reads the family members' own genotypes.

> **A search can stop part-way.** Compound-het and recessive pairing, *All, compound het grouped* and
> *Expanded carrier screening* read at most 5,000 candidates before they filter, ranked or not. When a search reaches that limit, a
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
  affected. To find such novel-gene candidates, set **Rank by** in the **Phenotype** filters to
  *Variant score*: the whole ranking, not only the page shown, is then ordered by the variant score,
  the phenotype match left out of the order. The scores stay the same; the **Score** column and each
  card show the variant score and the rank in that order, and a note above the list says it is ranked
  so. The CSV export of a ranked list writes both scores and the rank.
- Without HPO terms on the affected members, the ranking uses the variant evidence only.
- The ranking changes the order of a search, not what it finds. With an inheritance mode or
  *Expanded carrier screening* it ranks the same variants and compound-het pairs the search finds
  without it. A gene is listed where its best-ranked variant ranks; each variant shows its own rank.
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

**Download CSV** exports the list, with *Priority*, *Variant score* and *Rank* columns when the
ranking is on, in the order it was ranked by. Its
*Classification* column holds the class each review shows on the page, *Classification text* the
classification as typed, and *Tags* the review's tags. *HGVSg* is VEP's HGVS.g; it is empty where
the annotation has none, since the CSV gives the position and alleles in their own columns.

### Also hit by a structural variant

A small variant whose gene is also hit by an SV carries an **SV badge** (for example `SV: DEL`), with a
`trans` or `cis` chip when a phase was decided. **Also hit by an SV**, under *Structural second hit*,
keeps only these genes. The phase comes from read phasing or from the parents' genotypes, by the same
rule as for compound-het pairs; the reference gives the rule. Each SV of the gene gets its own phase,
listed in the badge's explanation. A deletion in trans with the SNV marks the gene *effectively
biallelic*, whatever the other SVs are.

[SNV + SV compound heterozygosity reference (the badge, trans or cis)](/docs/reference/sv-second-hit "further-reading")
