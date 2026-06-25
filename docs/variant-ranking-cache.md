# Phenotype-prioritised ranking cache

How CoGA makes the default small-variant view fast: it caches the phenotype-prioritised
ranking per family, serves narrower panels from broader cached rankings, warms the cache
in the background, and invalidates automatically when any input changes. This is the
design reference; the analyst-facing description is the in-app
[Prioritised ranking & caching](../frontend/src/content/docs/variant-ranking-cache.md)
reference and the User Guide.

---

## Why

The default small-variant view is the **Phenotype priority (Exomiser-style)** preset scoped
to the **Mendeliome** panel. Computing it costs ~10 s, dominated by per-gene phenotype
scoring against the Monarch knowledge graph (the ClickHouse candidate fetch is ~1–2 s; the
phenomizer-style scoring of the candidate genes is ~2–4 s; the rest is segregation, gene
constraint, and per-variant scoring). Recomputing that on every open is wasteful because
the result is **deterministic** given its inputs and those inputs rarely change between
opens.

So the ranked order is cached and reused until an input actually changes.

## What is cached

`family_variant_ranking_cache` (migration `035`, extended by `036`) stores, per family and
per query signature, the **compact ranked order** — an ordered list of
`{variant_id, priority}` (the score breakdown), plus `total`, the truncation flag, and
provenance. It does **not** store the variant annotations or review state; those are
re-fetched fresh on every read, so a cached ranking can never serve stale annotations or a
stale review status.

The cache is bounded to the few most-recent query signatures per family (older ones are
pruned on write).

## Freshness: the `inputs_hash`

A cache row is keyed by `inputs_hash` = SHA-256 of a canonical digest of **everything that
changes the ranking**:

| Input | Source | Why it matters |
| --- | --- | --- |
| Query filters (impact, frequency caps, exclude-ClinVar, genotype/QC sample filters, …) | the request | Different filters → different candidate set / order |
| Gene panel id **+ panel version** | the request + `gene_panels.version` | Restricts scope; a regenerated panel changes membership |
| Affected individuals' **HPO terms** | `individual_hpo` (present, affected samples) | The dominant driver of the phenotype score |
| **Pedigree / affected status** | the family's latest `family_structure_versions.structure_hash` | Drives segregation and which samples are "affected" |
| **Monarch release** | `max(monarch_gene_disease.release_version)` | The gene↔phenotype graph the scoring reads |
| **Review-filter state** | resolved review-tag / excluded-variant id sets | Review-tag filters are resolved outside the filter object, so they are folded in explicitly to avoid collisions |
| Scoring **algorithm version** | a constant in `variant_ranking_cache.py` | Bump to invalidate all rankings when scoring logic changes |

Any change flips the hash, so a request whose inputs differ from a cached row simply misses
and recomputes — **a stale ranking is never served.** Pagination (`page`, `page_size`) is
*not* part of the hash: the whole ranked order is cached, and any page is sliced from it.

> **Note — variant data.** Re-importing a family's variants changes the ranking but is not
> covered by the hash (the variant content is in ClickHouse, not cheaply hashable). The
> import flow therefore **clears** the family's cache on completion (see Invalidation).

## Serving a hit

On an exact `inputs_hash` hit (`_serve_ranking_from_cache` in
`clickhouse_family_variants.py`): take the requested page's slice of the cached order,
fetch those variant records from ClickHouse by id, attach the cached score breakdown, and
hydrate review state + internal cohort frequency. Fast (~1 s: a by-id fetch of ~100
records, no scoring) and always current (records + review state are fresh).

## Panel-agnostic: serving a sub-panel from a superset

The per-variant scores are **panel-independent** — the panel only restricts *which*
variants are in scope, it never changes a variant's pathogenicity / rarity / segregation /
phenotype score. So a narrower panel's ranking is exactly the broader (superset) ranking
restricted to the narrower panel's variants, **in the same order**.

To exploit this, each cache row also carries a `base_hash` (migration `036`) = the same
digest as `inputs_hash` but **with the panel removed**. All panels over the same
family / phenotype / filters share a `base_hash`.

On an exact-hash miss, `_serve_subpanel_from_superset`:

1. finds complete (non-truncated) cached rows with the same `base_hash`;
2. keeps only those whose panel's **genes cover** the requested panel's genes
   (`requested ⊆ cached`; a row with no panel covers everything);
3. **re-validates membership against ClickHouse** — fetches which of the superset's
   variant ids match the requested panel using the *exact* panel filter (the same SQL +
   Python check the live path uses), so the served set equals a direct compute with **no
   missed variants**;
4. filters the superset's ranked order to those ids and serves the page.

This generalises the "cache all genes" idea safely:

- The **Mendeliome** (the default) is the superset for its subsets — so narrowing from the
  Mendeliome to a diagnostic sub-panel is instant.
- If a **no-panel** (all-genes) prioritised view is ever run, it is cached as a *universal*
  superset that can serve any panel.
- The default is **never forced** through a slow, truncation-prone whole-genome scan: a
  superset is used only when it is complete and covering, else the request computes live.

**Guards.** A truncated superset (more than the ~5,000-candidate ranking window) is never
used for subsets — it might be missing a low-ranked in-panel variant. A panel with genes
outside the cached superset is not covered, so it computes live.

## Background warming

After an edit that changes the ranking, the next open should also be fast — not just
repeat opens. `precompute_family_ranking_safe` (best-effort, its own session, errors
swallowed) **replays the family's most recent prioritised query** with the now-current
inputs, storing the result under the new hash. It only ever replays an existing query
(read from the cache row's provenance) — it never *guesses* the default filters, which
sidesteps both frontend/backend coupling and the "no HPO entered yet at import" problem.

Scheduled (via `BackgroundTasks`) from the same edit endpoints that already refresh the
genome-overview haplotype lineage:

- HPO create / update / delete (`routers/families.py`)
- pedigree / member edits (`routers/families.py`, `routers/ped.py`)

## Invalidation — summary

| Event | Effect |
| --- | --- |
| HPO or pedigree/affected edit | `inputs_hash` changes (miss); a background warm pre-computes the new entry |
| Gene-panel update (e.g. Mendeliome regenerated) | `panel_version` changes → `inputs_hash` changes (miss) |
| Monarch release refreshed | `monarch_release` changes → all rankings miss |
| Review-tag / exclude change | review signature changes → miss |
| Variant **re-import** | the family's cache is **cleared** (`clear_family_ranking_cache`) |
| Scoring algorithm change (code) | bump `_ALGORITHM_VERSION` → all rankings miss |

## Provenance surfaced to the UI

`VariantPage` carries `ranking_cached` (served from cache?) and `ranking_computed_at` (when
the ranking was computed). The small-variant results show a subtle
*"⚡ Prioritised ranking served from cache · computed N min ago"* note. The note is purely
informational — invalidation is automatic, so a cached ranking is always consistent with
its inputs.

## Files

- Schema: `db/schema/postgres/035_family_variant_ranking_cache.sql`,
  `036_ranking_cache_superset.sql`
- Cache service: `app/services/variant_ranking_cache.py` (`compute_ranking_hashes`,
  `get_cached_ranking`, `store_ranking`, `find_superset_candidates`,
  `clear_family_ranking_cache`)
- Integration: `app/services/clickhouse_family_variants.py`
  (`_prioritized_small_variants_page`, `_serve_ranking_from_cache`,
  `_serve_subpanel_from_superset`, `precompute_family_ranking_safe`)
- Warming hooks: `app/routers/families.py`, `app/routers/ped.py`
- Re-import clear: `app/services/family_package_import.py`
- Response fields: `VariantPage.ranking_cached` / `ranking_computed_at` in `app/schemas.py`
