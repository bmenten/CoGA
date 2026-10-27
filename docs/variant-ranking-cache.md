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
| The pedigree, who is affected and each member's sex, which name a carrier screen's couple and decide its X-linked findings | the loaded family the ranking is computed on: its members with their sex, role and status, and the relationships. Until ENG-13 the key read the family's latest `family_structure_versions.structure_hash` after the family was loaded, so an edit committed in between stored a ranking computed on the old pedigree under the new pedigree's key |
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

The scoring reads each gene's pLI and missense Z from the gene profiles of the family's
assembly, the profiles the key's `gene_info.updated_at` covers, with the gene's symbol matched
in any case ([gene_constraint_metrics.py](../backend/app/services/gene_constraint_metrics.py)).
Until TRACE-15 it read the newest profile of any assembly, which a refresh of another
assembly's profiles could change without changing the key, and a symbol written otherwise
than its profile found no constraint. `_ALGORITHM_VERSION` 7 retires the rankings computed
then.

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

## Applying a panel

A panel keeps a variant in one of its regions or annotated with one of its genes. The family
search applies it twice: in SQL, where it reads the family's rows, and in the record filter
(`_kept_small_candidates` → `_small_record_matches`) over the candidates it fetched, which a
ranking computed afresh runs, and a compound-het, recessive or carrier-screening search, ranked
or not, on an exact hit too. Until ENG-15 both cost the number of rows times the panel's size:

- **In SQL** each of the family's rows was tested against every region (`arrayExists` over the
  regions): 1.3 s at 500 regions and 2.1 s at 1,000 for a family of about 190,000 rows. The
  region arrays, repeated for every row ClickHouse reads, could also run it out of memory, which
  the page answers 422 ("matches too many variants"). The regions are now joined to the family's
  own rows on 64-kb windows, as the Variant Explorer joins its annotation index
  (`_small_family_region_condition`). A row is kept when its chromosome, position and REF length,
  all that the overlap rule reads of a row, are those of a row of the family that overlaps a
  region. The join reads the family's rows in its projects and within the search's locus, every
  row the search itself reads, so it keeps exactly the rows the per-row test kept. The intervals
  and the excluded genes and intervals, short lists, keep the per-row test.
- **In the record filter** the panel's gene names were casefolded again for every candidate
  (5,300 for the Mendeliome) and every region was tested even after a gene had matched. The panel
  is now prepared once per search (`_SmallPanelMatch`): its genes casefolded once, and its regions
  read only for a record none of its genes names, by bisection over a lookup built on first use.

Neither changes what a search keeps or how it ranks, so the scoring version stays. Measured on the
throwaway stack, main and the change back to back on a shared machine (best and median, in
seconds). The family page, with synthetic trios written straight into the entries table and
panels of regions only (the page: its rows and its count; the track: 10 Mb):

| Panel and request | 190,000 rows: before | after | 1,000,000 rows: before | after |
| --- | --- | --- | --- | --- |
| 5 regions, the page | 0.14 / 0.17 | 0.36 / 0.40 | 0.27 / 0.27 | 0.63 / 0.65 |
| 25 regions, the page | 0.22 / 0.22 | 0.31 / 0.34 | 0.36 / 0.36 | 0.31 / 0.35 |
| 100 regions, the page | 0.42 / 0.43 | 0.40 / 0.44 | 0.76 / 0.85 | 0.33 / 0.33 |
| 500 regions, the page | 1.43 / 1.69 | 0.45 / 0.49 | 422 after 3.8 | 0.36 / 0.37 |
| 1,000 regions, the page | 3.70 / 3.74 | 0.39 / 0.43 | 422 after 4.9 | 0.38 / 0.41 |
| 1,000 regions, compound het | 1.46 / 1.49 | 0.48 / 0.52 | 3.04 / 3.16 | 0.40 / 0.46 |
| 5 regions, the track | 0.01 / 0.01 | 0.03 / 0.05 | 0.01 / 0.02 | 0.06 / 0.06 |
| 1,000 regions, the track | 0.06 / 0.06 | 0.11 / 0.12 | 0.13 / 0.14 | 0.06 / 0.06 |

Before, the page ran ClickHouse out of memory with 500 and 1,000 regions on the larger family,
which it answers 422 (in a first round, on a more loaded machine, with 500 regions on the
smaller family too). Every request that answered did so alike before and after. The join costs
about one more read of the family's rows: a panel of five regions, which the per-row test
handled in hundredths of a second, takes 0.2 to 0.4 s longer, and the track a few hundredths.

The record filter, on #839's synthetic trio and 5,300-gene Mendeliome (the benchmark above), with
a gene reference of one row per gene and one of GENCODE's size:

| Request on the Mendeliome | One row per gene: before | after | GENCODE size: before | after |
| --- | --- | --- | --- | --- |
| Ranked, computed afresh | 3.04 / 3.11 | 1.38 / 1.41 | 2.84 / 3.10 | 1.40 / 1.42 |
| Compound het, ranked, exact hit | 1.57 / 1.80 | 0.20 / 0.25 | 1.61 / 1.67 | 0.20 / 0.25 |
| Recessive, ranked, exact hit | 1.89 / 5.29 | 0.20 / 0.21 | 1.65 / 1.93 | 0.21 / 0.22 |
| Carrier screening, ranked, exact hit | 1.64 / 1.68 | 0.19 / 0.25 | 1.66 / 1.69 | 0.21 / 0.22 |
| Compound het, unranked | 4.84 / 4.86 | 0.15 / 0.16 | 1.60 / 1.64 | 0.15 / 0.17 |
| Carrier screening, unranked | 1.58 / 1.69 | 0.15 / 0.15 | 1.61 / 1.64 | 0.16 / 0.16 |
| The default view, computed afresh | 2.89 / 2.92 | 1.37 / 1.40 | 3.80 / 8.24 | 1.43 / 1.46 |
| The record filter alone, in each of these (best) | 1.36 to 4.41 | 0.011 to 0.042 | 1.38 to 1.57 | 0.011 to 0.043 |

Every request answered the same before and after: the variants, their order, the pairs, the
scores, the ranks and the total. An exact hit of a search that does not pair reads no
candidates, so it runs no record filter.

### A panel of any size

Until 2026-10-14 a gene panel of more than 1,000 regions (`_PANEL_REGION_INLINE_LIMIT`), the
Mendeliome among them, was matched on gene symbols alone, in SQL and in the record filter:
inlining its regions risked the query-size limit, and testing each row against each region was
slow. Such a panel missed a variant a smaller panel reaches by position only, one whose
annotation names its gene otherwise than the gene reference does (an earlier symbol, or only the
symbol of an overlapping gene). The owner decided that large panels match by position as well:
every panel now keeps a variant in one of its regions or annotated with one of its genes,
whatever its size, on the family page, in its record filter and in the Variant Explorer.
`_ALGORITHM_VERSION` 6 retires the rankings computed before, which lack those variants. The
structural-variant search follows the same rule since 2026-10-20 (see
[Structural variants](#structural-variants) below).

In a gene reference of one row per transcript the Mendeliome resolves to some 42,000 loci. Bound
one by one, they made each query that applies the panel 1.2 MB (0.13 MB on gene symbols alone)
and its join slower. They are bound merged instead (`_small_region_bounds`): the regions of a
chromosome that share a base become one, about one per gene, and a variant overlaps a merged
region exactly when it overlaps one of the regions merged into it (regions that only touch stay
apart). The queries take 0.26 MB.

Measured with `clickhouse local` 26.3 (4 threads, on a shared machine; best of 7, in seconds),
each query built and bound by the app's own code, on a synthetic cohort: 20,000 genes of eight
nested transcripts each, 5,300 of them the Mendeliome (42,400 loci, 4,874 once merged); 1,190,000
variants spread over the genome and annotated with the genes that hold them, 2 % of those in a
Mendeliome gene under another symbol; two families of 190,000 and 1,000,000 rows.

| Request with the Mendeliome | Gene symbols alone (before) | Each locus bound | Merged (now) |
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

Each locus bound and the merged loci keep the same variants (the same keys). The record filter
over a ranked window of 5,001 candidates takes 0.037 s instead of 0.023 s: it builds the lookup of
the panel's loci once, for the candidates none of its genes names.

The chromosome view's track availability (`GET /families/{family_id}/track-availability`), which
decides whose small-variant track the view draws, applied a panel's regions alone until CLIN-62,
each row tested against every region, and a panel without genes or regions as no panel: a member
whose only variants under the panel are annotated with one of its genes outside its regions
(VEP's upstream and downstream genes) was offered no track although the list showed them, and
under a panel that keeps nothing every member with a variant was offered one. It now applies the
panel with the family page's own clause (`_small_variant_present_sample_names` builds its query
with `_small_query_filter_parts`), so a member is offered a track exactly when the list shows them
a variant under the panel. Measured as above, on the same cohort and Mendeliome (best and median
of 7 runs, before and after alternating, on a shared machine under load): the availability's one
aggregate over the rows the view reads, and for comparison a member's track (its bounded count).

| Track availability with the Mendeliome | Regions tested per row (before) | The list's clause (now) | A member's track |
| --- | --- | --- | --- |
| 190,000 rows, the whole of chromosome 1 | 0.49 / 0.52 | 0.13 / 0.14 | 0.07 / 0.08 |
| 190,000 rows, 10 Mb of chromosome 1 | 0.04 / 0.05 | 0.12 / 0.12 | 0.06 / 0.07 |
| 1,000,000 rows, the whole of chromosome 1 | 2.52 / 3.06 | 0.14 / 0.15 | 0.08 / 0.09 |
| 1,000,000 rows, 10 Mb of chromosome 1 | 0.11 / 0.15 | 0.12 / 0.13 | 0.06 / 0.07 |
| Size of the query | 0.13 MB | 0.26 MB | 0.26 MB |

Before and after keep the same rows here (1,175 and 72, 6,302 and 335), as this cohort annotates
a variant only with the genes that hold it; a variant reached by a gene alone is shown by
`e2e/test_e2e_track_availability_panel.py`. The availability now costs about the same over any
window. ClickHouse's analyzer plans the aggregate around the join less well than the track's
count: with `enable_analyzer=0` the 10 Mb window of the smaller family takes 0.045 s.

### Structural variants

Until 2026-10-20 the structural-variant search kept a rule of its own: a gene panel of more than
1,000 regions (`_PANEL_REGION_INLINE_LIMIT`), the Mendeliome among them, was matched on the genes
an SV's annotation names alone, and only in Python, after the candidate read had stopped at
50,000 SVs; a smaller panel was matched on its regions alone. The Mendeliome, the SV page's
default, missed an SV in one of its genes whose annotation names the gene otherwise, and a
smaller panel an SV whose annotation names one of its genes without overlapping it (a gene the
annotation lists beside the SV, as VEP lists the genes up to 5 kb away). The owner decided that
the SV page follows the small-variant rule: a gene panel of any size keeps an SV whose annotation
names one of its genes (the SV's gene symbols, in any capitals) or that overlaps one of its
regions (on the same chromosome, the SV's start at or before the region's end and its end at or
after the region's start; its breakend mate is not read). The SV list, ranked or not, its CSV
export, the genome track and the track availability apply it, in SQL
(`_structural_panel_filter_condition`) and in the record filter (`_StructuralPanelMatch`) alike,
and `_PANEL_REGION_INLINE_LIMIT` is gone. SV rankings are not cached, so none has to be retired.

In SQL the panel's regions are joined to the family's own SV rows on 64-kb windows, as the
family page joins its small variants (`_structural_family_region_condition`): each SV is listed
under every window its span touches, from the lower of its start and end to the higher. A
search of one chromosome, as a genome track's is, binds only that chromosome's regions. The
regions are bound with those that lie inside another left out (`_structural_region_bounds`),
not merged where they share a base as the small-variant searches merge them: an SV stored with
its END before its POS (an END a caller wrote so is stored as written) overlaps by the rule a
region that holds both, and a chain of regions merged into one can hold both where none of them
does. Leaving out a region that lies inside another keeps every answer, and the record filter
reads the same regions. The panel now narrows the candidate read in SQL, so the 50,000-SV cap
counts the panel's SVs: before, a family of more than 50,000 SV rows had its Mendeliome view
read from the first 50,000 SVs of its callset in genome order, and flagged as incomplete.

Measured as above (`clickhouse local` 26.3, 4 threads, a shared machine; best of 7, in seconds),
each query built and bound by the app's own code, on a synthetic cohort: the same 20,000 genes,
5,300 of them the Mendeliome (42,400 loci; 13,746 once those inside another are left out, 4,874
merged); SVs spread over the genome, 85 % of 50 bp to 10 kb, 10 % breakends and 5 % CNVs of
10 kb to 10 Mb, each annotated with the genes it overlaps and those within 5 kb, 2 % of the
Mendeliome genes it overlaps under another symbol; two families of 60,000 and 200,000 SVs. The
candidate read is the list's (at most 50,001 SVs, with their annotations); the track reads one
member's SVs over 10 Mb of chromosome 1.

| Request with the Mendeliome | Gene symbols alone (before) | Each locus bound | Merged | Inside another left out (now) |
| --- | --- | --- | --- | --- |
| 60,000 SVs: the candidate read | 0.24 | 0.22 | 0.10 | 0.13 |
| SVs read / kept by the panel | 50,001 / 5,792 | 7,040 / 7,040 | 7,040 / 7,040 | 7,040 / 7,040 |
| a member's track over 10 Mb | 0.011 | 0.045 | 0.036 | 0.038 |
| 200,000 SVs: the candidate read | 0.44 | 0.31 | 0.18 | 0.17 |
| SVs read / kept by the panel | 50,001 / 5,837 | 23,685 / 23,685 | 23,685 / 23,685 | 23,685 / 23,685 |
| a member's track over 10 Mb | 0.017 | 0.056 | 0.039 | 0.038 |
| Size of the candidate read / the track's read | 1.2 / 1.3 KB | 1.17 / 0.15 MB | 0.19 / 0.08 MB | 0.42 / 0.09 MB |

Before, the candidate read stopped at the first 50,001 SVs of either family, so the panel kept
5,792 and 5,837 SVs where the gene names alone reach 6,934 and 23,372; it now reads the panel's
SVs only, 106 and 313 of them reached by position alone. Every way of binding the loci keeps the
same SVs (the cohort holds no SV with its END before its POS). The record filter over the SVs
read takes 0.026 s and 0.036 s (0.008 s over 50,001 SVs on gene names alone), and the app now
decodes the annotations of 7,040 and 23,685 SVs instead of 50,001. A track's read takes 0.02 to
0.03 s longer than on gene symbols alone. Bound merged, the candidate read would take up to 0.03 s
less and the track's about the same; each locus bound, 0.09 to 0.14 s and up to 0.02 s more.

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
with the same `base_hash` that meets the conditions below, reading the rows without their
rankings and a row's ranking only once its panel passes: a row on another panel costs no
read of a ranking that can hold thousands of variants (until ENG-13 every row's was read
first). The row:

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

Why the fourth: narrowing a row's ranking cannot bring back a variant the row lacks. Every
panel is matched by its genes and its regions, and the row's panel holds the requested genes
(the third condition) with their loci as the gene reference gives them now (the second), so
the row holds every variant the requested panel reaches by a gene or a gene's locus. It can
still lack one the requested panel reaches by a region of its own only: coordinates PanelApp
gave for a gene, or a gene's locus stored when the panel was made, that the row's panel does
not hold. Such a panel is computed directly; the others are served from the row. Until
2026-10-14 a panel of more than 1,000 regions, such as the Mendeliome, was matched on gene
symbols alone, so its ranking lacked every variant a smaller panel reaches by position only,
for instance because the annotation names its gene by another symbol than the gene reference
does, and before this condition the smaller panel served from that ranking lacked it too
(CLIN-34): CoGA checked that the row's panel held the requested panel's gene symbols, then
narrowed the row's variants to the requested panel.

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

The replay prepares the query as a live request does (`_resolve_small_variant_scope`): its
panel, gene filter and other filters, its intervals, excluded intervals and excluded genes,
and, for a search that asks for a structural-variant second hit, the genes the family's SVs
hit as its SV→gene index holds them now. So its ranking goes under the key the query's next
open reads. Until ENG-13 it left out the intervals and exclusions and replayed the SV hit
genes the stored ranking had resolved (#845): its ranking went under a key no request read,
and took one of the family's six rows.

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
- The panel filter: `backend/app/services/clickhouse_variant_queries.py`
  (`_small_panel_filter_condition`); its regions, merged where they share a base
  (`_small_region_bounds`), joined to the family's rows (`_small_family_region_condition`), and
  the panel as the record filter applies it (`_SmallPanelMatch`), see
  [Applying a panel](#applying-a-panel); for structural variants
  `_structural_panel_filter_condition`, its regions with those inside another left out
  (`_structural_region_bounds`), joined to the family's SV rows
  (`_structural_family_region_condition`), and `_StructuralPanelMatch`, see
  [Structural variants](#structural-variants)
- Warming hooks: `backend/app/routers/families.py`, `backend/app/routers/ped.py`
