# Monarch Initiative integration — developer notes

Related: [application-scheme.md](application-scheme.md) (storage boundary), [data-import.md](data-import.md),
[database.md](database.md).

CoGA uses the [Monarch Initiative](https://monarchinitiative.org/) knowledge graph for gene → disease →
HPO phenotype links, a live candidate-gene ranking, and the phenotype part of the variant ranking. What
lab users need (what the graph links, the gene-profile associations, the candidate panel, how the
ranking works and feeds PP4) is in the canonical in-app reference
[`frontend/src/content/docs/monarch-integration.md`](../frontend/src/content/docs/monarch-integration.md)
(shown at `/docs/reference/monarch-integration`). This note covers the implementation.

## Data sources

CoGA loads Monarch's pre-split, denormalised association TSVs (under
`https://data.monarchinitiative.org/monarch-kg/latest/tsv/`), not the full graph:

| File | Predicates | Sources seen |
| --- | --- | --- |
| `gene_associations/gene_disease.9606.tsv.gz` | `causes`, `associated_with_increased_likelihood_of` | OMIM, ClinGen |
| `gene_associations/gene_disease.noncausal.tsv.gz` | `gene_associated_with_condition`, `contributes_to` | OMIM, Orphanet |
| `disease_associations/disease_phenotype.all.tsv.gz` | disease → HPO | HPO annotations |

The files carry `subject`, `subject_label`, `predicate`, `object`, `object_label`, `negated` and
`primary_knowledge_source`. The gene files are filtered to non-negated `HGNC:` → `MONDO:` rows (the
noncausal file is cross-species). The release version comes from `metadata.yaml`. MONDO is the stored
disease identifier; there are no OMIM or Orphanet cross-reference columns.

## Tables

Both are in Postgres (baseline `backend/db/schema/postgres/02_reference.sql`); columns in
[database.md](database.md).

- `monarch_gene_disease` — one row per `(hgnc_id, mondo_id)`. A pair asserted by several sources or
  predicates collapses to one row with aggregated `predicates` and `sources`, a representative
  `predicate` (the strongest), a `causal` flag (true only for `causes`) and `release_version`.
- `monarch_disease_phenotype` — one row per `(mondo_id, hpo_id)` with aggregated `sources` and a
  `negated` flag, kept only when no source asserts the phenotype as present.

## What uses them

- **Ingest** — `backend/app/services/monarch_ingest.py`. `refresh_monarch()` downloads the release once
  and replaces both tables in one transaction, so a failed refresh never leaves them on different
  releases. A start on a database without Monarch data runs it (`ensure_monarch_on_startup`,
  `REFERENCE_BOOTSTRAP_MONARCH`), so a new instance begins on the newest release; a failure is logged
  and the start goes on. After that an administrator refreshes it (**Admin → Monarch data**,
  `POST /api/admin/monarch/refresh`). The administrator's refresh also re-versions the Mendeliome panel
  (`panel_metadata_service.regenerate_mendeliome`: genes with a `causes` or
  `gene_associated_with_condition` link) and invalidates the information-content cache. The start
  builds no panel, as a panel is created by an account: build the Mendeliome on the Gene panels page.
- **Gene profile** — `gene_metadata_service.py` returns `monarch_associations` on the gene profile,
  causal pairs first. With a `family_id`, each disease's expected phenotypes are matched against the
  family's present HPO terms, expanded to their ancestors through `hpo_closure`.
- **Candidate-gene panel** — `monarch_semsim.py` calls Monarch's `POST /v3/api/semsim/search` (group
  `Human Genes`, ancestor information content) with a 25 s timeout and a 1 h in-memory cache keyed by the
  sorted term set. A failure raises `MonarchSemsimError`; the endpoint turns it into a 502, which the
  panel shows as "unavailable". `GET /api/families/{family_id}/phenotype-match` sends the family's present
  HPO terms (all members unless `sample_id` is given) and flags genes that exist in CoGA. Monarch returns
  at most 50 results.
- **Local phenotype score** — `monarch_phenotype_score.py`: a Resnik best-match average between the
  affected members' present HPO terms and each gene's phenotype profile, normalised to 0–1, with
  information content derived from `monarch_disease_phenotype` through `hpo_closure` (cached
  process-wide for an hour). The patient set is capped at the 60 most informative terms and a gene's
  profile at 200 terms.
- **Variant ranking** — `variant_prioritization.py` combines the variant score (pathogenicity × rarity
  × segregation weight) with the phenotype score, half and half; a gene without Monarch data scores 0 on
  the phenotype side. `GET /api/families/{family_id}/small-variants?prioritize=true` ranks at most 5,000
  candidates (`_PRIORITIZE_CANDIDATE_LIMIT`) and sets `ranking_truncated` when more matched. It ranks
  only what the same search keeps unranked: with compound-het or recessive pairing, or
  expanded carrier screening, the pairs' variants, the single variants and the couple's findings. Each
  pair is listed at its better-ranked variant; past the window it also sets `candidates_capped`.
  Rankings are cached ([variant-ranking-cache.md](variant-ranking-cache.md)), in the combined order.
  `rank_by=variant_score` orders the whole ranking by the variant score instead (the combined score,
  then the variant id, break ties), so a variant in a gene without Monarch data ranks by its own
  evidence; the scores are the same, the ranks are numbered in that order, and the page names its
  order in `ranked_by`. The order is put on the cached ranking as the page is served, so it needs no
  cache row of its own. The CSV export of a ranked search writes the combined score (*Priority*), the
  *Variant score* and the *Rank*.
  `GET /api/families/{family_id}/structural-variants?prioritize=true` (not cached, `rank_by` as for
  small variants) scores an SV's
  pathogenicity from its event class, the genes it overlaps and their pLI
  (`structural_pathogenicity_score`). A copy-number change is scored by its direction, with the rule of
  the SNV + SV second hit (`sv_copy_change.copy_change`):
  - loss (0.7, at least 0.9 in a gene with pLI ≥ 0.9): a `DEL` of any subtype, or a `CNV` whose
    carriers' copy number shows a loss (or that stores none);
  - gain (0.5): a `DUP` of any subtype, or a CNV gain.
- **ACMG PP4** — the ACMG dialog reads the variant's phenotype score (see
  [acmg-classification.md](acmg-classification.md)).

## Tests

The ingest, semsim and scoring paths have backend tests; the gene profile and the candidate panel have
frontend tests. All are listed in [testing.md](testing.md).
