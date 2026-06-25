# Prioritised ranking & caching — reference

When you open a family's small-variant page, CoGA ranks the candidate variants by how well
each gene matches the patient's phenotype (the **Phenotype priority** preset, scoped to the
**Mendeliome** panel). That ranking is relatively expensive to compute, so CoGA **caches**
it and reuses it until something that affects the ranking changes — and warms it in the
background so it's ready when you need it.

This is the in-depth reference. For the workflow-level overview see the
[in-app user guide](/docs) section *Phenotype-driven variant prioritisation*.

---

## What you'll see

- The **first open** of a family's prioritised view (or the first after a relevant change)
  computes the ranking once — a few seconds.
- **Every open after that is near-instant**, served from the cache.
- A subtle note appears under the results when a ranking comes from the cache:
  **⚡ Prioritised ranking served from cache · computed N min ago.** It's purely
  informational — see *Staying correct* below.

## It refreshes itself — you never see a stale ranking

The cached ranking is tied to the exact inputs that produced it. If any of these change,
the next open automatically recomputes (and a fresh cache entry is stored):

- the affected individuals' **HPO phenotype terms** (the main driver of the ranking),
- the **pedigree** / affected status,
- the **gene panel** (including a regenerated Mendeliome),
- the **filters** (impact, frequency, ClinVar exclusions, per-sample genotype/quality),
- the **Monarch release** the platform is running,
- re-**importing** the family's variant data.

So you never have to manually refresh, and you can trust that a cached ranking reflects the
current data. The "computed N min ago" note tells you *when* it was last produced, not that
it might be out of date.

## Background warming — the first open after editing phenotypes is fast too

Phenotypes are usually entered *after* a case is set up. Normally that would make the first
open after entering them slow (a recompute). To avoid this, when you **add, change or
remove an HPO term** (or edit the pedigree), CoGA quietly **re-computes the family's
prioritised ranking in the background**, replaying your most recent prioritised search with
the new phenotypes. By the time you open the variant page, the ranking is already waiting in
the cache.

## Narrowing to a smaller panel is instant

A common workflow is to start broad on the **Mendeliome**, then narrow to a focused
diagnostic gene panel. Because a variant's priority **score doesn't depend on the panel**
(the panel only decides which variants are *in scope*), CoGA serves the smaller panel
**directly from the broader Mendeliome ranking** — no re-analysis. So switching from the
Mendeliome to a sub-panel (with the same other filters) is immediate.

A few details that keep this correct:

- It only happens when the broader cached ranking is **complete** (not truncated by the
  ranking-window cap) and actually **covers** the smaller panel's genes — which a
  Mendeliome covers for essentially any disease-gene sub-panel.
- Membership is **re-checked exactly** against the data, so the narrowed list is identical
  to computing that panel from scratch — no variant is silently dropped.
- A panel containing genes **outside** the cached ranking (e.g. a non-disease gene not in
  the Mendeliome) simply computes fresh and is then cached itself.

> **Going the other way** (broadening to a *larger* set than what's cached) recomputes, as
> it should — the broader set may contain variants the narrower ranking never saw. If you
> run a full **no-panel** prioritised view, that becomes a universal cached ranking that any
> panel can then be served from.

## Staying correct — what the cache never does

- It **never serves stale annotations or review state**: the variant records and your
  classifications/tags are always fetched fresh on read; only the *ranked order + scores*
  come from the cache.
- It **never serves a ranking whose inputs have changed**: the cache key includes all of
  them, so any change is a miss.
- A sub-panel served from a superset is **re-validated** against the live data, so it equals
  a direct computation.

The net effect: the prioritised view is fast and the order is stable between opens, while
staying faithful to the current phenotypes, pedigree, panel, filters, and annotations.
