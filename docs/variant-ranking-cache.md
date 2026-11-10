# Prioritised ranking cache

The default small-variant view is the **Phenotype priority** preset on the **Mendeliome**
panel. Computing that ranking takes several seconds, mostly to score each gene against the
patient's phenotype with the Monarch knowledge graph. The result depends only on its inputs, so
CoGA caches the ranked order per family and reuses it until an input changes.

## What is cached

`family_variant_ranking_cache` (in `03_assay.sql`) keeps, per family and query:

- the ranked order, a list of `{variant_id, priority}` (the score breakdown);
- the total, and whether the ranking was truncated;
- `provenance`: the query's filters and a digest of the genes and regions its panel filter
  applied (`panel_members`).

Variant annotations and review state are not cached. Every read fetches them fresh, so a cached
ranking never shows a stale annotation or review status.

The ranking holds the same variants as the unranked search. A search that pairs (compound het,
recessive, expanded carrier screening) ranks only its pairs' variants, its single variants and the
couple's findings (see [A search that pairs](#a-search-that-pairs)). The pairs themselves
are not cached.

Each family keeps its six most recent queries; storing a new one removes older rows.

## The cache key

Each row is keyed by `inputs_hash`, a SHA-256 over everything that changes the ranking:

| Input | Where it comes from |
| --- | --- |
| The query filters (not the page), including the inheritance mode and expanded carrier screening | the request |
| The gene panel and its version | `gene_panels.version` and `external_version` |
| The genes and regions the panel filter applied | as the query read them for the family's assembly: `gene_panel_genes`, `gene_panel_regions` and each gene's loci in `genes` (a digest, `panel_members_digest`) |
| The regions the query kept to or left out, and the genes it left out | as the query resolved them: the intervals, the excluded genes' loci (from `genes`) and names |
| The present HPO terms of the affected members | `individual_hpo` |
| The pedigree, who is affected and each member's sex (they name a carrier screen's couple and decide its X-linked findings) | the loaded family the ranking is computed on: members with sex, role and status, and the relationships |
| The family's small-variant data | the ClickHouse `SNV_INDEL/family_data_version` table: every insert, delete or re-import of the family's variants adds a token |
| The Monarch release | `monarch_gene_disease.release_version` |
| The loaded HPO ontology: its release and import time | the most recently written `hpo_term` row (`release_version`, `updated_at`), read as the signed record reads it ([annotation-provenance.md](annotation-provenance.md#the-reference-modules)) |
| Gene constraint (pLI, missense-Z) | the latest `gene_info.updated_at` for the assembly |
| The review-filter state | the query's review-tag and excluded-variant sets |
| The assembly and the scoring version | the family, and `_ALGORITHM_VERSION` in `variant_ranking_cache.py` |

Gene loci come from the gene reference at query time, not from a version. A reload that moves a
gene therefore changes the regions, and so the key, of every panel and excluded gene that holds
it, while the panel's version and the filters stay the same.

A gene filter reads no loci: ranked or not, it keeps the variants annotated with the gene. A reload
changes neither what it keeps nor its key.

The panel digest is taken from what the query applied, so a panel edit committed while the query
runs cannot pair the ranking with the panel's next version.

A request whose inputs differ from every cached row misses: an exact hit is never a ranking
computed on other inputs. The page number is not in the key; the whole order is cached and each
page is a slice of it.

The scoring reads pLI and missense Z from the gene profiles of the family's assembly (the profiles
the key's `gene_info.updated_at` covers), matching the gene's symbol in any case
([gene_constraint_metrics.py](../backend/app/services/gene_constraint_metrics.py)).

## Resolving a panel

A panel filter applies the panel's genes, the regions it stores for the family's assembly, and each
gene's loci in that assembly's gene reference (`_fetch_panel_constraints`). The key digests them.
The check that lets a broader ranking serve a narrower panel (below) reads the broader ranking's
panel the same way. For the Mendeliome that is 5,300 genes and, since the gene reference holds one
row per transcript, some 40,000 loci.

Read again on every request, this would cost more than computing a small panel's ranking: 0.3 to 0.6 s
per request with a gene reference of one row per gene, 0.4 to 1.7 s with one of GENCODE's size.
Every request with the Mendeliome would pay it, the default view's exact hit included, and so
would the check for a narrower panel opened from the Mendeliome's ranking.

Each process therefore keeps a panel's resolution and its digest, per panel and assembly, and uses
it only while the panel's token is unchanged. The token is read on every use, in one statement
(`_PANEL_RESOLUTION_TOKEN_SQL`):

| Part of the token | What moves it |
| --- | --- |
| The panel's version | every change of its genes or regions through the panel service (an edit, a Mendeliome rebuild, a PanelApp import) |
| A SHA-256 of the panel's gene rows, and one of its region rows for the assembly | any change of those rows, by any path, version raised or not |
| The gene-reference imports recorded for the assembly (their number and the latest) | every load of the gene reference: each import records itself in `reference_dataset_imports`, in the transaction that writes its rows |

- A resolution is kept only when the token read after its rows equals the one read before them, so
  it holds no change committed while it was read.
- At most eight resolutions are kept, the most recently used.
- Every process reads the token from the database, so all processes see a change at once.
- A kept resolution and each of its regions are frozen: one object serves every request that
  applies the panel.

Only the reference import (`apply_reference_gene_rows`, `apply_reference_dataset_text`) writes the
gene reference, and it records itself with its rows. A `genes` row written outside it, by hand in
the database, is not seen by a kept resolution until the next import of the assembly's gene
reference or a restart.

Reading a panel is cheaper as well:

- Each gene is kept once with a dict; a list scan would take about 0.2 s for the
  Mendeliome.
- The gene-reference lookup binds its gene list as one array parameter
  (`= ANY(CAST(:terms AS text[]))`) instead of one parameter per gene. asyncpg refuses a statement
  of more than 32,767 parameters, and Postgres plans an expanding IN anew on every lookup: for
  the Mendeliome in a GENCODE-size reference, a sequential scan of the assembly's transcripts,
  0.2 s and more. As one array it keeps a plan that reads the `upper(hgnc_symbol)` and
  `upper(gene_id)` indexes: 0.05 s.

Measured with the sub-panel benchmark of 2026-10-13 on the throwaway stack: a synthetic trio of
2,900 variants, a 5,300-gene Mendeliome and sub-panels of it; without and with the measures above, back to
back on a shared machine; best / median of 7, in seconds; a gene reference of one row per gene and
one of GENCODE's size (eight transcripts per gene, 247,200 rows):

| Request | One row per gene: without | with | GENCODE size: without | with |
| --- | --- | --- | --- | --- |
| A 25-gene sub-panel, the Mendeliome's ranking cached | 0.33 / 0.33 | 0.06 / 0.06 | 0.48 / 0.51 | 0.06 / 0.06 |
| The same computed afresh | 0.06 / 0.06 | | 0.06 / 0.06 | |
| A 100-gene sub-panel, the Mendeliome's ranking cached | 0.34 / 0.35 | 0.06 / 0.07 | 0.50 / 0.52 | 0.08 / 0.09 |
| The same computed afresh | 0.09 / 0.10 | | 0.10 / 0.11 | |
| A 500-gene sub-panel, the Mendeliome's ranking cached | 0.38 / 0.40 | 0.10 / 0.11 | 0.57 / 0.65 | 0.10 / 0.11 |
| The same computed afresh | 0.29 / 0.37 | | 0.27 / 0.37 | |
| The Mendeliome's exact hit, a page of 100 | 0.33 / 0.33 | 0.07 / 0.07 | 0.57 / 0.58 | 0.06 / 0.06 |
| The default view's exact hit (the preset's filters), a page of 100 | 0.31 / 0.34 | 0.06 / 0.06 | 0.49 / 0.50 | 0.06 / 0.07 |
| Resolving the Mendeliome alone | 0.26 / 0.27 | 0.003 / 0.003 | 0.37 / 0.39 | 0.006 / 0.008 |

The fresh computations were measured only with the measures in place. Each sub-panel gave the
same result served and fresh (variants, order, scores, ranks, total). Served from the Mendeliome's ranking, a
sub-panel also reads that ranking (about 0.015 s for its 2,299 variants) and, for the check, the
Mendeliome panel's token (about 0.003 s).

## Applying a panel

A panel keeps a variant in one of its regions or annotated with one of its genes. The family
search applies it in SQL, where it reads the family's rows.

Testing each of the family's rows against every region (`arrayExists` over the regions) costs the
number of rows times the panel's size: 1.3 s at 500 regions and 2.1 s at 1,000, for a family of
about 190,000 rows. The region arrays, repeated for every row ClickHouse reads, can also run it out
of memory, which the page answers 422 ("matches too many variants"). The regions are therefore
joined to the family's own rows on 64-kb windows, as the Variant Explorer joins its annotation index
(`_small_family_region_condition`). A row is kept when its chromosome, position and REF length
(all the overlap rule reads of a row) match a row of the family that overlaps a region. The join
reads the family's rows in its projects and within the search's locus, every row the search
itself reads, so it keeps exactly the rows the per-row test keeps. The intervals and the excluded
genes and intervals, short lists, keep the per-row test.

Measured on the throwaway stack, the per-row test and the join back to back on a shared machine (best /
median, in seconds). The family page, with synthetic trios written straight into the entries table
and panels of regions only (the page: its rows and its count; the track: 10 Mb):

| Panel and request | 190,000 rows: per-row test | join | 1,000,000 rows: per-row test | join |
| --- | --- | --- | --- | --- |
| 5 regions, the page | 0.14 / 0.17 | 0.36 / 0.40 | 0.27 / 0.27 | 0.63 / 0.65 |
| 25 regions, the page | 0.22 / 0.22 | 0.31 / 0.34 | 0.36 / 0.36 | 0.31 / 0.35 |
| 100 regions, the page | 0.42 / 0.43 | 0.40 / 0.44 | 0.76 / 0.85 | 0.33 / 0.33 |
| 500 regions, the page | 1.43 / 1.69 | 0.45 / 0.49 | 422 after 3.8 | 0.36 / 0.37 |
| 1,000 regions, the page | 3.70 / 3.74 | 0.39 / 0.43 | 422 after 4.9 | 0.38 / 0.41 |
| 1,000 regions, compound het | 1.46 / 1.49 | 0.48 / 0.52 | 3.04 / 3.16 | 0.40 / 0.46 |
| 5 regions, the track | 0.01 / 0.01 | 0.03 / 0.05 | 0.01 / 0.02 | 0.06 / 0.06 |
| 1,000 regions, the track | 0.06 / 0.06 | 0.11 / 0.12 | 0.13 / 0.14 | 0.06 / 0.06 |

With the per-row test, 500 and 1,000 regions ran ClickHouse out of memory on the larger family,
answered 422. Every request that answered gave the same answer with both. The join costs about one more read of the family's
rows: a five-region panel, which the per-row test handles in hundredths of a second, takes 0.2 to
0.4 s longer, and the track a few hundredths.

### A panel of any size

Every gene panel, of any size, such as the Mendeliome, matches as above, by its genes and by
position: on the family page and in the Variant Explorer. Matched on gene symbols alone, a large
panel would miss a variant that a smaller panel reaches by position only: one whose annotation names
its gene differently from the gene reference (an earlier symbol, or only the symbol of an
overlapping gene). The owner decided that large panels match by position as well. The
structural-variant search follows the same rule (see
[Structural variants](#structural-variants) below).

In a gene reference of one row per transcript the Mendeliome resolves to some 42,000 loci. Bound
one by one, they would make each query that applies the panel 1.2 MB (0.13 MB on gene symbols alone) and
slow its join. They are bound merged instead (`_small_region_bounds`): the regions of a
chromosome that share a base become one, about one per gene. A variant overlaps a merged region
exactly when it overlaps one of the regions merged into it; regions that only touch stay apart.
The queries take 0.26 MB.

Measured with `clickhouse local` 26.3 (4 threads, shared machine; best of 7, in seconds), each
query built and bound by the app's own code, on a synthetic cohort:

- 20,000 genes of eight nested transcripts each, 5,300 of them the Mendeliome (42,400 loci, 4,874
  once merged);
- 1,190,000 variants spread over the genome and annotated with the genes that hold them, 2 % of
  those in a Mendeliome gene under another symbol;
- two families of 190,000 and 1,000,000 rows.

| Request with the Mendeliome | Gene symbols alone | Each locus bound | Merged |
| --- | --- | --- | --- |
| Family page, 190,000 rows: a page of 100 | 0.14 | 0.31 | 0.19 |
| the ranked window (5,001 rows) | 0.14 | 0.30 | 0.19 |
| the bounded count | 0.06 | 0.22 | 0.10 |
| variants kept | 15,311 | 15,616 | 15,616 |
| Family page, 1,000,000 rows: a page of 100 | 0.23 | 0.43 | 0.32 |
| the ranked window (5,001 rows) | 0.22 | 0.41 | 0.31 |
| the bounded count | 0.07 | 0.31 | 0.14 |
| variants kept | 80,822 | 82,476 | 82,476 |
| Variant Explorer, 1,190,000 variants: the bounded count | 0.10 | 0.28 | 0.16 |
| variants kept | 96,133 | 98,092 | 98,092 |
| Size of each query | 0.13 MB | 1.23 MB | 0.26 MB |

Each locus bound and the merged loci keep the same variants (the same keys).

The chromosome view's track availability (`GET /families/{family_id}/track-availability`) decides
whose small-variant track the view draws. It applies the panel with the family page's own clause
(`_small_variant_present_sample_names` builds its query with `_small_query_filter_parts`): a member
is offered a track exactly when the list shows them a variant under the panel.

Measured as above, on the same cohort and Mendeliome (best / median of 7 runs, the panel's regions
tested per row and the list's clause alternating, on a shared machine under load): the
availability's one aggregate over the rows the view reads, and for comparison a member's track (its
bounded count).

| Track availability with the Mendeliome | Regions tested per row | The list's clause | A member's track |
| --- | --- | --- | --- |
| 190,000 rows, the whole of chromosome 1 | 0.49 / 0.52 | 0.13 / 0.14 | 0.07 / 0.08 |
| 190,000 rows, 10 Mb of chromosome 1 | 0.04 / 0.05 | 0.12 / 0.12 | 0.06 / 0.07 |
| 1,000,000 rows, the whole of chromosome 1 | 2.52 / 3.06 | 0.14 / 0.15 | 0.08 / 0.09 |
| 1,000,000 rows, 10 Mb of chromosome 1 | 0.11 / 0.15 | 0.12 / 0.13 | 0.06 / 0.07 |
| Size of the query | 0.13 MB | 0.26 MB | 0.26 MB |

Both clauses keep the same rows here (1,175 and 72, 6,302 and 335), as this cohort annotates a
variant only with the genes that hold it; `e2e/test_e2e_track_availability_panel.py` shows a
variant reached by a gene alone. With the list's clause the availability cost about the same over any
window. ClickHouse's analyzer planned the aggregate worse than the track's count: with
`enable_analyzer=0` the 10 Mb window of the smaller family took 0.045 s.

The cause is the aggregate's shape. A subquery over the family's matching rows, with their calls
ARRAY JOINed and the samples grouped outside it, took the analyzer some 36 ms under the Mendeliome
whatever the window, three times a member's track count with the same panel clause (11 ms): in a
small window, most of the availability's time. The aggregate is therefore one
SELECT over the family's rows: each matching row's visible samples with an ALT allele, picked from
its call arrays (`arrayFilter`), and the distinct samples. Its base-match probe, which runs when the
search sends sample filters, also reads the rows in one SELECT.

Measured on the throwaway stack (clickhouse-server 26.8, the image CI runs, shared machine): the
two shapes alternating, best / median of 9 runs, in seconds, on a cohort built like the one above
(19,157 genes, 5,287 of them the Mendeliome's, with 42,296 loci; families of 190,000 and 1,000,000
rows).

| The availability's aggregate with the Mendeliome | Subquery, ARRAY JOIN | One SELECT |
| --- | --- | --- |
| 190,000 rows, the whole of chromosome 1 | 0.067 / 0.072 | 0.042 / 0.043 |
| 190,000 rows, 10 Mb of chromosome 1 | 0.062 / 0.063 | 0.036 / 0.036 |
| 190,000 rows, 100 kb of chromosome 1 | 0.062 / 0.068 | 0.038 / 0.039 |
| 1,000,000 rows, the whole of chromosome 1 | 0.085 / 0.087 | 0.059 / 0.061 |
| 1,000,000 rows, 10 Mb of chromosome 1 | 0.064 / 0.065 | 0.037 / 0.038 |
| 1,000,000 rows, 100 kb of chromosome 1 | 0.066 / 0.067 | 0.038 / 0.040 |
| 1,000,000 rows, 10 Mb, with a de novo search's sample filters | 0.085 / 0.089 | 0.055 / 0.061 |
| The analyzer's time, median run (ms) | 35 to 43 | 11 to 17 |

Both shapes offered the same members in every case measured: the Mendeliome, a two-gene panel and
no panel; the whole chromosome and windows of 10 Mb, 1 Mb and 100 kb; with and without sample
filters. With two genes or no panel either shape takes 0.003 to 0.03 s. The old analyzer took 0.046
to 0.070 s over the subquery shape, and as long as the default analyzer over one SELECT; the query
keeps the default analyzer. `integration/test_track_availability_presence_integration.py` holds
the analyzer's time over the aggregate within twice its time over a member's track count.

### Structural variants

Matched only on the genes an SV's annotation names, a panel misses an SV in one of its genes
whose annotation names the gene differently. Matched on its regions alone, it misses an SV whose
annotation names one of its genes without overlapping it (a gene listed beside the SV, as VEP lists
the genes up to 5 kb away).

The owner decided that the SV page follows the small-variant rule. A gene panel of any size keeps
an SV that:

- is annotated with one of its genes (the SV's gene symbols, in any capitals); or
- overlaps one of its regions: on the same chromosome, the SV's start at or before the region's end
  and its end at or after the region's start. Its breakend mate is not read.

The SV list (ranked or not), its CSV export, the genome track and the track availability apply it,
in SQL (`_structural_panel_filter_condition`) and in the record filter (`_StructuralPanelMatch`,
its regions read by bisection over a lookup built on first use, `_RegionLookup`) alike. SV rankings
are not cached.

In SQL the panel's regions are joined to the family's own SV rows on 64-kb windows, as the family
page joins its small variants (`_structural_family_region_condition`). Each SV is listed under every
window its span touches, from the lower of its start and end to the higher. A search of one
chromosome, such as a genome track's, binds only that chromosome's regions.

The regions are bound with any region that lies inside another left out
(`_structural_region_bounds`), not merged where they share a base as for small variants. An SV
stored with its END before its POS (a caller's END is stored as written) overlaps, by the rule, a
region that holds both; a chain of regions merged into one can hold both where none of them does.
Leaving out a region that lies inside another keeps every answer, and the record filter reads the
same regions.

The panel narrows the candidate read in SQL, so the 50,000-SV cap counts the panel's SVs.

Measured as above (`clickhouse local` 26.3, 4 threads, shared machine; best of 7, in seconds), each
query built and bound by the app's own code, on a synthetic cohort:

- the same 20,000 genes, 5,300 of them the Mendeliome (42,400 loci; 13,746 once those inside
  another are left out, 4,874 merged);
- SVs spread over the genome: 85 % of 50 bp to 10 kb, 10 % breakends and 5 % CNVs of 10 kb to
  10 Mb, each annotated with the genes it overlaps and those within 5 kb, 2 % of the Mendeliome
  genes it overlaps under another symbol;
- two families of 60,000 and 200,000 SVs.

The candidate read is the list's (at most 50,001 SVs, with their annotations); the track reads one
member's SVs over 10 Mb of chromosome 1.

| Request with the Mendeliome | Gene symbols alone | Each locus bound | Merged | Inside another left out |
| --- | --- | --- | --- | --- |
| 60,000 SVs: the candidate read | 0.24 | 0.22 | 0.10 | 0.13 |
| SVs read / kept by the panel | 50,001 / 5,792 | 7,040 / 7,040 | 7,040 / 7,040 | 7,040 / 7,040 |
| a member's track over 10 Mb | 0.011 | 0.045 | 0.036 | 0.038 |
| 200,000 SVs: the candidate read | 0.44 | 0.31 | 0.18 | 0.17 |
| SVs read / kept by the panel | 50,001 / 5,837 | 23,685 / 23,685 | 23,685 / 23,685 | 23,685 / 23,685 |
| a member's track over 10 Mb | 0.017 | 0.056 | 0.039 | 0.038 |
| Size of the candidate read / the track's read | 1.2 / 1.3 KB | 1.17 / 0.15 MB | 0.19 / 0.08 MB | 0.42 / 0.09 MB |

On gene symbols alone, the candidate read stopped at the first 50,001 SVs of either family, so the
panel kept 5,792 and 5,837 SVs where the gene names alone reach 6,934 and 23,372. Bound by locus, it
reads the panel's SVs only, 106 and 313 of them reached by position alone. Every way of binding the loci keeps the same
SVs (the cohort holds no SV with its END before its POS). Further:

- The record filter over the SVs read takes 0.026 s and 0.036 s (0.008 s over 50,001 SVs on gene
  names alone).
- The app decodes the annotations of 7,040 and 23,685 SVs instead of 50,001.
- A track's read takes 0.02 to 0.03 s longer than on gene symbols alone.
- Bound merged, the candidate read would take up to 0.03 s less and the track's about the same;
  with each locus bound, 0.09 to 0.14 s and up to 0.02 s more.

## Serving a narrower panel from a broader one

The panel decides which variants are in scope. A variant's score depends on the panel in one way
only: a compound-het pair counts when both its variants are in scope. So, as long as a narrower
panel splits no pair, its ranking is the broader panel's ranking restricted to its variants, in the
same order. Each row therefore also stores `base_hash`: the same digest without the panel, its
version and its genes and regions.

On a miss, CoGA first fetches the variants the requested panel reaches, with the filter a direct
computation uses (by gene symbol and by position), and works out their segregation modes among
themselves. These first steps of a direct computation are quick; the phenotype scoring after them
takes the seconds. CoGA then looks for a cached row with the same `base_hash` that meets the
conditions below. It reads the rows without their rankings, and a row's ranking only once its
panel passes. A row on another panel thus costs no read of a ranking that can hold thousands of
variants. The row:

1. is complete, not truncated: the ranking considers at most 5,000 candidates, and a truncated one
   may miss a low-ranked variant of the smaller panel. A request whose own variants overflow that
   window is never served from another row;
2. has no panel, or a panel that still holds the genes and regions the row was computed on. Each
   row records them in its `provenance` (`panel_members`, the same digest as in the key). CoGA
   compares that record with the panel as it resolves now: its kept resolution while the token is
   unchanged (see [Resolving a panel](#resolving-a-panel)), else the panel read again. A row whose
   panel has been deleted is not used;
3. covers the requested panel's genes (a row without a panel covers every gene);
4. holds every variant the requested panel reaches; and
5. recorded for each of them the segregation modes they have among themselves.

CoGA serves that row's ranked order restricted to those variants, numbered from 1 within the
requested panel. This is what a direct computation of the panel gives: the same variants, order,
scores and ranks. Narrowing from the Mendeliome to a diagnostic sub-panel stays a cache hit
whenever conditions 4 and 5 hold. When no row qualifies, the ranking is computed directly from the
variants already fetched.

Why the second condition: restricting a ranking can only drop variants, never add any. A ranking
computed before its panel gained a gene holds none of that gene's variants. A new version that leaves the genes and regions unchanged, such as a description edit,
still serves the panel from its earlier ranking.

Why the fourth: narrowing cannot bring back a variant the row lacks. Every panel is matched by its
genes and its regions (see [A panel of any size](#a-panel-of-any-size)), and the row's panel holds the requested genes (condition 3) with their loci
as the gene reference gives them now (condition 2). So the row holds every variant the requested
panel reaches by a gene or a gene's locus. It can still lack one the requested panel reaches only
by a region of its own that the row's panel does not hold: coordinates PanelApp gave for a gene, or
a gene's locus stored when the panel was made. Such a panel is computed directly; the others are
served from the row.

Why the fifth: a compound-het pair whose partner the requested panel leaves out is no pair within
that panel. That variant loses the compound-het mode the row recorded, and with it segregation
weight and score, unless another mode is as strong. Served from the row, the panel would keep the pair,
its weight and score, and the ranks the row numbered among all its own variants. A row
records rounded scores, so CoGA does not derive a new score from them: a panel that splits a pair
is computed directly. The ranks are renumbered whenever a panel is served from a broader row.

## A search that pairs

A compound-het or recessive search keeps a variant for its partner; expanded carrier screening, for
what the other partner carries in its gene. No SQL decides this: the search applies these rules in
Python to a window of candidates (`_small_variants_candidate_page`). The ranked search applies the
same rules to the same window (`_prioritized_small_variants_page`), so it keeps the same variants
and pairs and ranks only those, each with the segregation modes it has among them.

The cache holds the order of the variants, not the pairs. On an exact hit such a search first
fetches its candidates and works out its pairs. It then numbers its variants in the cached order,
provided the row holds every one of them with the segregation modes they have now; otherwise it is
computed again.

The single-variant modes a variant fits (de novo / dominant, homozygous recessive, X-linked), which
its segregation weight reads and the recessive search keeps alongside its pairs, come from the SQL
the inheritance filter itself applies: the candidate read returns them as flags
(`_fetch_small_variant_rows(segregation_flags=True)`, built by `_small_segregation_flag_columns` from
`_small_inheritance_mode_clauses`), so the ranking and the filter cannot read a mode apart. Only the
trio de novo, which no search filters by, and the compound-het pairing are worked out in Python.

The candidates a ranking reads are the rows the SQL keeps: no filter is applied a second time in
Python.

A narrower panel is served from a broader row under the conditions above, applied
to the variants the narrower search keeps; its pairs are worked out within its own reach. The page
lists each pair where its better-ranked variant ranks.

The window is `_PRIORITIZE_CANDIDATE_LIMIT` (5,000), the largest the unranked search reads. When it
overflows, the page flags the ranking truncated and the candidates capped, and its total counts the
items found within the window: a lower bound, as no SQL count counts pairs.

## Warming after an edit

After an edit that changes the ranking, a background task (`precompute_family_ranking_safe`)
replays the family's most recent prioritised query with the new inputs, so the next open is fast
too. It only replays a query the family has already run. Errors are logged and ignored. It runs
after:

- an HPO term is added, changed or removed;
- a structure save or a member edit (single, batch or removal);
- a PED upload or manual family creation.

The replay prepares the query as a live request does (`_resolve_small_variant_scope`): its panel,
gene filter and other filters; its intervals, excluded intervals and excluded genes; and, for a
search that asks for a structural-variant second hit, the genes the family's SVs hit as its SV→gene
index holds them now. Its ranking thus goes under the key the query's next open reads.

## When the cache misses

| Event | Effect |
| --- | --- |
| HPO, member or structure edit | the key changes; the background task computes the new ranking |
| Variants added, deleted or re-imported, by any route | the data version changes, so the key changes |
| A gene panel's genes or regions changed by an edit, a Mendeliome rebuild or a PanelApp import | the panel's version and its genes and regions change, so the key changes, and the panel's earlier rankings serve no panel any more |
| The gene reference reloaded, moving a gene | the regions of the panels and excluded genes that hold the gene change, so their keys change |
| Monarch, HPO or gene constraint refreshed | every ranking that reads it misses |
| Review tags or exclusions changed | the review signature changes |
| Scoring code changed, or which variants a filter keeps | bump `_ALGORITHM_VERSION`; every ranking misses |

A change to an input the key covers needs no bump: the hashed input changes, so no earlier row can
match.

A package import also deletes the family's cached rows when it finishes. This only frees rows the
new data version could never match.

## What the user sees

The response (`VariantPage`) carries `ranking_cached` and `ranking_computed_at`. The results then
show "⚡ Prioritised ranking served from cache · computed N min ago". The note is information only.
An exact hit is a ranking computed on the same inputs. A narrowed one is the ranking a direct
computation of the narrower panel gives, from a ranking whose panel has not changed since (see
above). "Computed N min ago" is when that broader ranking was computed.

## Where the code is

- Cache rows and keys: `backend/app/services/variant_ranking_cache.py`
- Serving, sub-panel reuse and warming: `backend/app/services/clickhouse_family_variants.py`
  (`_prioritized_small_variants_page`, `_serve_ranking_from_cache`,
  `_serve_subpanel_from_superset`, `_priorities_from_ranking`, `_superset_panel_if_unchanged`,
  `precompute_family_ranking_safe`); what a search keeps, ranked or not:
  `_kept_small_candidates`, `_small_inheritance_items`, and the ranked page
  `_prioritized_items_page`
- Resolving a panel and keeping its resolution: the same module (`_fetch_panel_constraints`,
  `_read_panel_constraints`, `_panel_resolution_token`, `_PANEL_RESOLUTION_TOKEN_SQL`,
  `_panel_members`, `_fetch_gene_regions_for_terms`)
- The panel filter: `backend/app/services/clickhouse_variant_queries.py`
  (`_small_panel_filter_condition`); its regions, merged where they share a base
  (`_small_region_bounds`), joined to the family's rows (`_small_family_region_condition`), see
  [Applying a panel](#applying-a-panel); for structural variants
  `_structural_panel_filter_condition`, its regions with those inside another left out
  (`_structural_region_bounds`), joined to the family's SV rows
  (`_structural_family_region_condition`), and `_StructuralPanelMatch`, see
  [Structural variants](#structural-variants)
- Warming hooks: `backend/app/routers/families.py`, `backend/app/routers/ped.py`
