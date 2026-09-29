Filtering narrows the genome to a candidate list; prioritisation puts that list in order. The
**Phenotype priority (Exomiser-style)** preset combines the Monarch phenotype signal with the usual
variant evidence to rank candidates the way tools such as Exomiser do — so the most likely causal
variant tends to rise to the top instead of being buried in a long, position-sorted list.

### Turning it on

Apply the **Phenotype priority (Exomiser-style)** preset. It sets a sensible starting point — rare
(gnomAD popmax \< 0.1%) and HIGH/MODERATE impact — and switches the result list into ranked mode. A
**Score** column appears (sortable), the rows come back ordered best-first, and a small ✦ marks
variants whose gene matches the patient’s phenotypes. You can still adjust any filter;
prioritisation re-ranks whatever passes.

### What the score blends

Each variant gets a single priority score in `[0, 1]` built from four parts:

- **Pathogenicity** — variant impact and loss-of-function, ClinVar assertions (a pathogenic ClinVar
  record dominates), and the in-silico predictors (CADD, REVEL, SpliceAI, and AlphaMissense). Gene
  constraint (gnomAD pLI for loss-of-function, missense-Z for missense) raises confidence for the
  relevant variant class.
- **Rarity** — rarer variants score higher, using the gnomAD population-max frequency.
- **Segregation** — whether the variant fits an inheritance pattern in the pedigree (see below).
- **Phenotype fit** — how well the gene’s Monarch phenotype profile matches the affected
  individuals’ HPO, computed locally with a Phenomizer-style information-content similarity (rarer,
  more specific shared phenotypes count more).

Pathogenicity, rarity, and segregation form a *variant score*; phenotype fit then **reorders**
candidates so a gene that matches the patient can rise above an equally damaging variant in an
unrelated gene. Genes Monarch knows nothing about are not penalised on the variant axis — their raw
variant score is shown separately, so a strong candidate in a novel gene stays findable (sort by the
variant score to surface them).

### Segregation modes

Using the pedigree (affected status, sex, and parent–child links), each variant is tagged with the
inheritance patterns it is compatible with:

- **De novo** — heterozygous in an affected child and confidently absent in *both* genotyped parents
  (a true trio is required; a parent with a missing or low-coverage genotype does not qualify).
  On chrX and chrY outside the pseudo-autosomal regions a son is hemizygous, so his call reads `1`
  or `1/1`. It is de novo when the parent he got that chromosome from, the mother for the X and the
  father for the Y, is confidently absent, and the other parent does not carry it.
- **Homozygous recessive** — affected individuals homozygous-alt, no unaffected homozygous-alt.
- **Compound heterozygous** — two heterozygous hits in the same gene that segregate as a pair.
- **X-linked recessive** — sex-aware X-chromosome pattern.
- **Dominant** — affected carry the variant, unaffected do not (covers inherited-dominant and
  no-trio cases).

### The score breakdown

Open a variant’s review dialog to see exactly *why* it ranked where it did: the combined score and
rank, the variant / pathogenicity / rarity / phenotype sub-scores, the compatible inheritance modes,
the gene constraint values, and — most usefully — the specific patient phenotypes that drove the
gene’s phenotype match. This explainability is the point: the ranking is a transparent aid, not a
black box.

### Export

With the preset active, *Download CSV* exports the variants in ranked order with **Priority** and
**Rank** columns, so the file matches what you see on screen.

> **Read the scores as a ranking, not a verdict.** Priority scores order candidates *within a
> family*; they are not calibrated probabilities of pathogenicity. Phenotype matching needs HPO
> recorded on the affected individuals — without it, ranking falls back to impact/rarity/segregation
> only. If the result list shows a *“ranking is incomplete”* warning, more candidates matched than
> the ranker handles at once: tighten the filters (frequency, impact, a gene panel, or an
> inheritance mode) so the full set is ranked.

> **It’s cached, and stays fresh.** The ranking is relatively expensive to compute, so CoGA caches
> it: the first open computes it, every open after is near-instant (a *“served from cache”* note
> shows when), and it refreshes automatically whenever phenotypes, the pedigree, the panel, the
> filters or the annotations change. Narrowing from the Mendeliome to a smaller gene panel is
> instant — it’s served from the broader ranking, since the scores don’t depend on the panel.

[Prioritised ranking \& caching reference (invalidation, warming, sub-panel serving)](/docs/reference/variant-ranking-cache "further-reading")
