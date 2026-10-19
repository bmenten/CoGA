# Prioritised ranking cache

The default small-variant view is the **Phenotype priority** preset on the **Mendeliome**
panel. Computing that ranking takes several seconds, mostly for scoring each gene against
the patient's phenotype with the Monarch knowledge graph. The result depends only on its
inputs, so CoGA caches the ranked order per family and reuses it until an input changes.
This page describes how that cache works and when it misses.

## What is cached

`family_variant_ranking_cache` (in `03_assay.sql`) keeps, per family and per query, the
ranked order as a list of `{variant_id, priority}` (the score breakdown), with the total,
whether the ranking was truncated, and in `provenance` the query's filters and a digest of
the genes and regions its panel filter applied (`panel_members`). It does not keep the variant
annotations or the review state: those are fetched fresh on every read, so a cached ranking
never shows stale annotations or a stale review status.

The ranked variants are the ones the search keeps, as it keeps them unranked. A search that
pairs (compound het, recessive, expanded carrier screening) ranks only its pairs' variants,
its single variants and the couple's findings, not every candidate its SQL prefilter lets
through (CLIN-44, see [A search that pairs](#a-search-that-pairs)). The order is of
variants; the pairs are not cached.

Only the six most recent queries per family are kept; older rows are removed when a new one
is stored.

## The cache key

Each row is keyed by `inputs_hash`, a SHA-256 over everything that changes the ranking:

| Input | Where it comes from |
| --- | --- |
| The query filters, without the page, the inheritance mode and expanded carrier screening among them | the request |
| The gene panel and its version | `gene_panels.version` and `external_version` |
| The genes and regions the panel filter applied | the panel's genes and regions as the query read them for the family's assembly: `gene_panel_genes`, `gene_panel_regions` and each gene's loci in `genes` (a digest, `panel_members_digest`) |
| The regions the query kept to or left out, and the genes it left out | the intervals, the loci (from `genes`) of the excluded genes, and the excluded genes' names, as the query resolved them |
| The present HPO terms of the affected members | `individual_hpo` |
| The pedigree, who is affected and each member's sex, which name a carrier screen's couple and decide its X-linked findings | the family's latest `family_structure_versions.structure_hash` (members with their sex, role and status, and the relationships); for a family without one, the same read from the loaded family |
| The family's small-variant data | the ClickHouse `SNV_INDEL/family_data_version` table: every insert, delete or re-import of the family's variants adds a token |
| The Monarch release | `monarch_gene_disease.release_version` |
| The loaded HPO ontology: its release and import time | the most recently written `hpo_term` row (`release_version`, `updated_at`), read as the signed record reads it ([annotation-provenance.md](annotation-provenance.md#the-reference-modules)); the import time tells apart two imports that recorded no release |
| Gene constraint (pLI, missense-Z) | the latest `gene_info.updated_at` for the assembly |
| The review-filter state | the review-tag and excluded-variant sets of the query |
| The assembly and the scoring version | the family, and `_ALGORITHM_VERSION` in `variant_ranking_cache.py` |

The gene loci come from the gene reference when the query runs, not from a version: a
reload of the gene reference that moves a gene changes the regions of every panel and every
excluded gene that holds it, and so the key, while the panel's version and the filters stay
as they were. A gene filter reads no loci: it keeps the variants annotated with the gene,
ranked or not, so a reload changes neither what it keeps nor its key (ENG-1; the ranked and
paired searches used to keep only the variants inside the gene's loci). The digest of the panel's genes and regions is also taken from what the query applied,
so an edit of the panel committed while the query runs cannot pair the ranking with the
panel's next version.

A request whose inputs differ from every cached row misses, so an exact hit is never a
ranking computed on other inputs. The page number is not part of the key: the whole order is
cached and each page is a slice of it.

## Resolving a panel

A panel filter applies the panel's genes, the regions the panel stores for the family's
assembly, and each of its genes' loci in that assembly's gene reference
(`_fetch_panel_constraints`). The key digests them, and the check below that lets a broader
ranking serve a narrower panel reads the broader ranking's panel the same way. For the
Mendeliome that is 5,300 genes and, since the gene reference holds one row per transcript,
some 40,000 loci. Read again on every request, it cost more than computing a small panel's
ranking: 0.3 to 0.6 s per request with a gene reference of one row per gene, and 0.4 to 1.7 s
with one of GENCODE's size (ENG-14). Every request with the Mendeliome paid it, the default
view's exact hit included, and a narrower panel opened from the Mendeliome's ranking paid it
for the check.

Each process therefore keeps a panel's resolution, with its digest, per panel and assembly,
and uses it only while its token is unchanged. The token is read on every use, in one
statement (`_PANEL_RESOLUTION_TOKEN_SQL`):

| Part of the token | What moves it |
| --- | --- |
| The panel's version | every change of its genes or regions through the panel service (an edit, a Mendeliome rebuild, a PanelApp import) |
| A SHA-256 of the panel's gene rows, and one of its region rows for the assembly | any change of those rows, by any path, a version raised or not |
| The gene-reference imports recorded for the assembly (their number and the latest) | every load of the gene reference: each import records itself in `reference_dataset_imports` in the transaction that writes its rows |

A resolution is kept only when the token read after its rows is the one read before them, so
it holds no change committed while it was read. At most eight resolutions are kept, the most
recently used, and every process reads the token from the database, so all of them see a
change at once. The kept resolution, and each of its regions, is frozen: the same object
serves every request that applies the panel.

The gene reference is written only by the reference import (`apply_reference_gene_rows`,
`apply_reference_dataset_text`), which records itself with its rows. A row of `genes` written
past it, by hand in the database, is not seen by a kept resolution until the next import of
the assembly's gene reference or a restart.

Reading a panel became cheaper as well. Its genes are kept once each with a dict, where a list
scan took about 0.2 s for the Mendeliome, and the gene-reference lookup binds its gene list as
one array parameter (`= ANY(CAST(:terms AS text[]))`) instead of one parameter per gene.
asyncpg refuses a statement of more than 32,767 parameters, and Postgres planned the
expanding IN anew on every lookup: for the Mendeliome in a GENCODE-size reference, a
sequential scan of the assembly's transcripts, 0.2 s and more. As one array it keeps a plan
that reads the `upper(hgnc_symbol)` and `upper(gene_id)` indexes, 0.05 s.

Measured with #839's benchmark on the throwaway stack (a synthetic trio of 2,900 variants, a
5,300-gene Mendeliome and sub-panels of it, before and after ENG-14 back to back on a shared
machine; best and median of 7, in seconds), with a gene reference of one row per gene and one
of GENCODE's size (eight transcripts per gene, 247,200 rows):

| Request | One row per gene: before | after | GENCODE size: before | after |
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

The fresh computations are those measured after the change. Each sub-panel came out the same
served and fresh (variants, order, scores, ranks, total). What a sub-panel served from the
Mendeliome's ranking reads beyond a fresh computation is that ranking itself, about 0.015 s for
its 2,299 variants; the check of the Mendeliome's panel reads its token, about 0.003 s.

## Serving a narrower panel from a broader one

The panel decides which variants are in scope. A variant's score depends on it in one way
only: a compound-het pair counts when both of its variants are in scope. So the ranking for
a narrower panel is the broader panel's ranking, restricted to the narrower panel's
variants, in the same order, as long as the narrower panel splits no pair. Each row
therefore also stores `base_hash`: the same digest without the panel, its version and its
genes and regions.

On a miss, CoGA first fetches the variants the requested panel reaches, with the filter a
direct computation uses (by gene symbol and by position), and works out their segregation
modes among themselves: the first steps of a direct computation, and quick ones. The
phenotype scoring that follows them is what takes seconds. CoGA then looks for a cached row
with the same `base_hash` that:

1. is complete, not truncated (the ranking considers at most 5,000 candidates, and a
   truncated one may miss a low-ranked variant of the smaller panel). A request whose own
   variants overflow that window is never served from another row;
2. has no panel, or a panel that still holds the genes and regions the row was computed on.
   Each row records them in its `provenance` (`panel_members`, the same digest as in the
   key), and CoGA compares that record with the panel as it resolves now: its kept
   resolution while the token is unchanged (see [Resolving a panel](#resolving-a-panel)),
   else the panel read again. A row that records nothing (cached before rankings recorded
   their panel), or whose panel has been deleted, is not used;
3. covers the requested panel's genes (a row without a panel covers every gene);
4. holds every variant the requested panel reaches; and
5. recorded for each of them the segregation modes they have among themselves.

It serves that row's ranked order restricted to those variants, numbered from 1 within the
requested panel. That is the ranking a direct computation of the panel gives: the same
variants, in the same order, with the same scores and ranks. Narrowing from the Mendeliome
to a diagnostic sub-panel stays a cache hit whenever conditions 4 and 5 hold. When no row
qualifies, the ranking is computed directly from the variants already fetched.

Why the second condition: restricting the row's ranking can only drop variants from it,
never add any. A ranking computed before its panel gained a gene holds none of that gene's
variants, whatever the panel holds now. Before CoGA compared the record, such a ranking
served the panel itself once its version had moved (the exact key misses), and any other
panel the grown panel now covered, the Mendeliome default view included (CLIN-27). A new
version that leaves the genes and regions as they were, such as an edit of the description,
still serves the panel from its earlier ranking.

Why the fourth: a panel of more than 1,000 regions (`_PANEL_REGION_INLINE_LIMIT`), such as
the Mendeliome, is matched on gene symbols alone, while a smaller panel is also matched by
position: its genes' loci in the gene reference and the coordinates it stores. A variant a
smaller panel reaches by position only, for instance because the annotation names its gene
by another symbol than the gene reference does, is not in the Mendeliome's ranking, and the
smaller panel served from that ranking lacked it (CLIN-34). Before, CoGA checked that the
row's panel held the requested panel's gene symbols, then narrowed the row's variants to the
requested panel, which cannot bring back a variant the row lacks. Only a panel that reaches
such a variant is computed directly; the others are still served from the Mendeliome.

Why the fifth: a compound-het pair whose partner the requested panel leaves out is no pair
within that panel, so that variant loses the compound-het mode the row recorded, and with it,
unless another mode is as strong, segregation weight and score. Served from the row, the
panel kept the pair, its weight and score, and the ranks the row numbered among all its own
variants (CLIN-35). A row records rounded scores, so CoGA
does not work a new score out of them: a panel that splits a pair is computed directly. The
ranks are numbered again whenever a panel is served from a broader row.

## A search that pairs

A compound-het or recessive search keeps a variant for its partner, and expanded carrier
screening for what the other partner carries in its gene: no SQL decides these, and the
search applies them in Python to a window of candidates (`_small_variants_candidate_page`).
The ranked search applies the same rules to the same window
(`_prioritized_small_variants_page`), so it keeps the same variants and pairs, and ranks
only those, each with the segregation modes it has among them. Before CLIN-44 it ranked
every candidate the SQL prefilter let through: for a compound-het search, every variant
heterozygous in each affected member, paired or not. `_ALGORITHM_VERSION` 4 retires the
rankings computed then.

The cache holds the order of the variants, not the pairs. So on an exact hit such a search
first fetches its candidates and works out its pairs, then numbers its variants in the
cached order, provided the row holds every one of them with the segregation modes they have
now; otherwise it is computed again. A narrower panel is served from a broader row under
the conditions above, applied to the variants the narrower search keeps: its pairs are
worked out within its own reach. The page lists each pair where its better-ranked variant
ranks.

Its window is `_PRIORITIZE_CANDIDATE_LIMIT` (5,000), the largest the unranked search reads.
When the window overflows, the page flags both the ranking truncated and the candidates
capped, and its total counts the items found within the window, a lower bound: no SQL count
counts pairs.

## Warming after an edit

After an edit that changes the ranking, a background task
(`precompute_family_ranking_safe`) replays the family's most recent prioritised query with
the new inputs, so the next open is fast too. It only replays a query the family has already
run; before that there is nothing to warm. Errors are logged and ignored.

It runs after an HPO term is added, changed or removed, after a structure save or a member
edit (single, batch or removal), and after a PED upload or manual family creation.

The replay applies the query's panel, gene filter and other filters, but not its intervals,
excluded intervals or excluded genes. Because the key covers the regions and genes a query
applied, its ranking of such a query is stored under another key than the query's own, and
the query's next open computes it again. Before the key covered them, that ranking was served
to the query as if it had kept to its intervals and exclusions.

## When the cache misses

| Event | Effect |
| --- | --- |
| HPO, member or structure edit | the key changes; the background task computes the new ranking |
| Variants added, deleted or re-imported, by any route | the data version changes, so the key changes |
| A gene panel's genes or regions changed: an edit, a Mendeliome rebuild or a PanelApp import that changes them | the panel's version and its genes and regions change, so the key changes, and the panel's earlier rankings serve no panel any more |
| The gene reference reloaded, moving a gene | the regions of the panels and excluded genes that hold the gene change, so their keys change |
| Monarch, HPO or gene constraint refreshed | every ranking that reads it misses |
| Review tags or exclusions changed | the review signature changes |
| Scoring code changed, or which variants a filter keeps | bump `_ALGORITHM_VERSION`; every ranking misses |

A change to what the key covers needs no bump: the hashed input itself changes, so no row
cached before can match.

A package import also deletes the family's cached rows when it finishes. That only frees
rows the new data version could never match again.

## What the user sees

The response (`VariantPage`) carries `ranking_cached` and `ranking_computed_at`. The results
then show "⚡ Prioritised ranking served from cache · computed N min ago". The note is for
information only: an exact hit is a ranking computed on the same inputs, and a narrowed one
is the ranking a direct computation of the narrower panel gives, from a ranking whose panel
has not changed since (see above). "Computed N min ago" is when that broader ranking was
computed.

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
- The panel filter, and the region limit above which it matches gene symbols alone:
  `backend/app/services/clickhouse_variant_queries.py` (`_small_panel_filter_condition`,
  `_PANEL_REGION_INLINE_LIMIT`)
- Warming hooks: `backend/app/routers/families.py`, `backend/app/routers/ped.py`
