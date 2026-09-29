# Prioritised ranking cache

The default small-variant view is the **Phenotype priority** preset on the **Mendeliome**
panel. Computing that ranking takes several seconds, mostly for scoring each gene against
the patient's phenotype with the Monarch knowledge graph. The result depends only on its
inputs, so CoGA caches the ranked order per family and reuses it until an input changes.
This page describes how that cache works and when it misses.

## What is cached

`family_variant_ranking_cache` (in `03_assay.sql`) keeps, per family and per query, the
ranked order as a list of `{variant_id, priority}` (the score breakdown), with the total,
whether the ranking was truncated, and the query's filters. It does not keep the variant
annotations or the review state: those are fetched fresh on every read, so a cached ranking
never shows stale annotations or a stale review status.

Only the six most recent queries per family are kept; older rows are removed when a new one
is stored.

## The cache key

Each row is keyed by `inputs_hash`, a SHA-256 over everything that changes the ranking:

| Input | Where it comes from |
| --- | --- |
| The query filters, without the page | the request |
| The gene panel and its version | `gene_panels.version` and `external_version` |
| The present HPO terms of the affected members | `individual_hpo` |
| The pedigree and who is affected | the family's latest `family_structure_versions.structure_hash` |
| The family's small-variant data | the ClickHouse `SNV_INDEL/family_data_version` table: every insert, delete or re-import of the family's variants adds a token |
| The Monarch release | `monarch_gene_disease.release_version` |
| The loaded HPO ontology: its release and import time | the most recently written `hpo_term` row (`release_version`, `updated_at`), read as the signed record reads it ([annotation-provenance.md](annotation-provenance.md#the-reference-modules)); the import time tells apart two imports that recorded no release |
| Gene constraint (pLI, missense-Z) | the latest `gene_info.updated_at` for the assembly |
| The review-filter state | the review-tag and excluded-variant sets of the query |
| The assembly and the scoring version | the family, and `_ALGORITHM_VERSION` in `variant_ranking_cache.py` |

A request whose inputs differ from every cached row misses and is computed again, so a stale
ranking is never served. The page number is not part of the key: the whole order is cached
and each page is a slice of it.

## Serving a narrower panel from a broader one

The panel only decides which variants are in scope; it never changes a variant's score. So
the ranking for a narrower panel is the broader panel's ranking, restricted to the narrower
panel's variants, in the same order. Each row therefore also stores `base_hash`: the same
digest without the panel.

On a miss, CoGA looks for a cached row with the same `base_hash` that:

1. is complete, not truncated (the ranking considers at most 5,000 candidates, and a
   truncated one may miss a low-ranked variant of the smaller panel); and
2. covers the requested panel's genes (a row without a panel covers every gene).

It then asks ClickHouse which of that row's variants match the requested panel, with the
same filter a direct computation uses, and serves the ranked order restricted to them.
Narrowing from the Mendeliome to a diagnostic sub-panel is therefore instant. When no
covering row exists, the ranking is computed directly.

## Warming after an edit

After an edit that changes the ranking, a background task
(`precompute_family_ranking_safe`) replays the family's most recent prioritised query with
the new inputs, so the next open is fast too. It only replays a query the family has already
run; before that there is nothing to warm. Errors are logged and ignored.

It runs after an HPO term is added, changed or removed, after a member edit (single, batch
or removal), and after a PED upload or manual family creation.

## When the cache misses

| Event | Effect |
| --- | --- |
| HPO or member edit | the key changes; the background task computes the new ranking |
| Structure change through `PUT /families/{family_id}/structure` | the key changes; computed on the next open |
| Variants added, deleted or re-imported, by any route | the data version changes, so the key changes |
| Gene panel regenerated | the panel version changes |
| Monarch, HPO or gene reference refreshed | every ranking that reads it misses |
| Review tags or exclusions changed | the review signature changes |
| Scoring code changed | bump `_ALGORITHM_VERSION`; every ranking misses |

A change to what the key covers needs no bump: the hashed input itself changes, so no row
cached before can match.

A package import also deletes the family's cached rows when it finishes. That only frees
rows the new data version could never match again.

## What the user sees

The response (`VariantPage`) carries `ranking_cached` and `ranking_computed_at`. The results
then show "⚡ Prioritised ranking served from cache · computed N min ago". The note is for
information only; the cache never serves a ranking whose inputs changed.

## Where the code is

- Cache rows and keys: `backend/app/services/variant_ranking_cache.py`
- Serving, sub-panel reuse and warming: `backend/app/services/clickhouse_family_variants.py`
  (`_prioritized_small_variants_page`, `_serve_ranking_from_cache`,
  `_serve_subpanel_from_superset`, `precompute_family_ranking_safe`)
- Warming hooks: `backend/app/routers/families.py`, `backend/app/routers/ped.py`
