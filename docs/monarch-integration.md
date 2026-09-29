# Monarch Initiative integration — developer notes

Related: [application-scheme.md](application-scheme.md) (storage boundary), [data-import.md](data-import.md),
[database.md](database.md).

CoGA uses the [Monarch Initiative](https://monarchinitiative.org/) knowledge graph for gene → disease →
HPO phenotype links, a live candidate-gene ranking, and the phenotype part of the variant ranking. What
lab users need — what the graph links, the gene-profile associations, the candidate panel, how the
ranking works and feeds PP4 — is in the in-app reference
[`frontend/src/content/docs/monarch-integration.md`](../frontend/src/content/docs/monarch-integration.md)
(shown at `/docs/reference/monarch-integration`). That page is the canonical home; this note covers the
implementation.

## Data sources

CoGA loads Monarch's pre-split, denormalised association TSVs (under
`https://data.monarchinitiative.org/monarch-kg/latest/tsv/`), not the full graph:

| File | Predicates | Sources seen |
| --- | --- | --- |
| `gene_associations/gene_disease.9606.tsv.gz` | `causes`, `associated_with_increased_likelihood_of` | OMIM, ClinGen |
| `gene_associations/gene_disease.noncausal.tsv.gz` | `gene_associated_with_condition`, `contributes_to` | OMIM, Orphanet |
| `disease_associations/disease_phenotype.all.tsv.gz` | disease → HPO | HPO annotations |

The files carry `subject`, `subject_label`, `predicate`, `object`, `object_label`, `negated` and
`primary_knowledge_source`. The gene files are filtered to `HGNC:` → `MONDO:` rows that are not negated
(the noncausal file is cross-species). The release version comes from `metadata.yaml`. MONDO is the
stored disease identifier; there are no OMIM or Orphanet cross-reference columns.

## Tables

Both live in Postgres (baseline `backend/db/schema/postgres/02_reference.sql`); see
[database.md](database.md) for the columns.

- `monarch_gene_disease` — one row per `(hgnc_id, mondo_id)`. A pair asserted by several sources or
  predicates collapses to one row with aggregated `predicates` and `sources`, a representative
  `predicate` (the strongest), a `causal` flag (true only for `causes`) and `release_version`.
- `monarch_disease_phenotype` — one row per `(mondo_id, hpo_id)` with aggregated `sources` and a
  `negated` flag, kept only when no source asserts the phenotype as present.

## What uses them

- **Ingest** — `backend/app/services/monarch_ingest.py`. `refresh_monarch()` downloads the release once
  and replaces both tables in one transaction, so a failed refresh never leaves them on different
  releases. Triggered by an administrator (**Admin → Monarch Data**, `POST /api/admin/monarch/refresh`);
  nothing runs at start-up. A refresh also re-versions the Mendeliome panel
  (`panel_metadata_service.regenerate_mendeliome`: genes with a `causes` or
  `gene_associated_with_condition` link) and invalidates the information-content cache.
- **Gene profile** — `gene_metadata_service.py` returns `monarch_associations` on the gene profile,
  causal pairs first. With a `family_id`, each disease's expected phenotypes are matched against the
  family's present HPO terms, expanded to their ancestors through `hpo_closure`.
- **Candidate-gene panel** — `monarch_semsim.py` calls Monarch's `POST /v3/api/semsim/search` (group
  `Human Genes`, ancestor information content) with a 25 s timeout and a 1 h in-memory cache keyed by the
  sorted term set; failures raise `MonarchSemsimError`, which the endpoint turns into a 502 the panel shows
  as "unavailable". `GET /api/families/{family_id}/phenotype-match` sends the family's present HPO terms
  (all members unless `sample_id` is given) and flags genes that exist in CoGA. Monarch returns at most
  50 results.
- **Local phenotype score** — `monarch_phenotype_score.py`: a Resnik best-match average between the
  affected members' present HPO terms and each gene's phenotype profile, normalised to 0–1, with
  information content derived from `monarch_disease_phenotype` through `hpo_closure` (cached
  process-wide for an hour). The patient set is capped at the 60 most informative terms and a gene's
  profile at 200 terms.
- **Variant ranking** — `variant_prioritization.py` combines the variant score (pathogenicity × rarity
  × segregation weight) with the phenotype score, half and half; a gene without Monarch data scores 0 on
  the phenotype side. `GET /api/families/{family_id}/small-variants?prioritize=true` ranks at most 5,000
  candidates (`_PRIORITIZE_CANDIDATE_LIMIT`) and sets `ranking_truncated` when more matched. Rankings are
  cached; see [variant-ranking-cache.md](variant-ranking-cache.md).
- **ACMG PP4** — the ACMG dialog reads the variant's phenotype score (see
  [acmg-classification.md](acmg-classification.md)).

## Tests

The ingest, semsim and scoring paths have backend tests, and the gene profile and the candidate panel
have frontend tests; all are listed in [testing.md](testing.md).
