# Monarch Initiative Integration — Design

Status: Phases 1–4 implemented
Owner: TBD
Related: [storage-architecture.md](storage-architecture.md), [data-import.md](data-import.md), [database.md](database.md), [ROADMAP.md](ROADMAP.md)

> **Implementation note (Phase 1).** Built against the Monarch KG `2026-06-08`
> release. A few details below were refined once the real data was inspected; the
> [Phase 1 — implemented](#phase-1--gene--disease-implemented) section is the source
> of truth for what shipped. Key deviations from the original proposal:
>
> - **Per-association TSVs, not the full KG tarball.** Monarch publishes pre-split,
>   denormalized files under `tsv/gene_associations/` (labels included inline), so
>   Phase 1 downloads ~360 KB instead of the 300 MB graph and needs no nodes file.
> - **Standalone table, read-time join — not `gene_info.extra`.** Gene→disease data
>   is per-HGNC and assembly-independent, so it lives in its own table joined onto
>   the profile by symbol at read time, rather than denormalized into every
>   per-assembly `gene_info` row.
> - **One row per (gene, disease).** Sources and predicates are aggregated; `causal`
>   tracks only `biolink:causes` (Monarch's causal file), matching Monarch's own
>   causal/noncausal split.
> - **Inline admin endpoint, not a job queue.** The refresh runs in ~2.5 s, so the
>   gene-reference job/worker machinery would be disproportionate.
> - **OMIM/Orphanet xref columns deferred.** MONDO is the stored disease id; mapping
>   to OMIM (needs the nodes file or `/v3/api/mappings`) is left to a follow-up.

## Goal

Integrate [Monarch Initiative](https://monarchinitiative.org/) gene–disease–phenotype
associations into CoGA so that:

1. Gene profiles show curated disease associations (OMIM/Orphanet/MONDO) with provenance.
2. Disease–phenotype (HPO) expectations can be compared against a family member's
   observed phenotypes.
3. A patient's HPO profile can drive a ranked list of candidate genes/diseases
   (phenotype-driven prioritization) in the variant workflow.

Monarch normalizes ~30 upstream sources (HPOA, OMIM, Orphanet, ClinGen, GenCC,
Alliance of Genome Resources, GO, Reactome, …) into a single
[Biolink Model](https://biolink.github.io/biolink-model/) knowledge graph keyed by
stable CURIEs. Those CURIEs (`HGNC:`, `OMIM:`, `MONDO:`, `HP:`, `Orphanet:`) map
directly onto identifiers CoGA already stores.

## Why this fits CoGA today

| Monarch entity/edge | Maps onto existing CoGA structure |
| --- | --- |
| Gene node (`HGNC:`) | `gene_info.hgnc_id`, `gene_info.ensembl_gene_id` ([02_reference.sql](../backend/db/schema/postgres/02_reference.sql)) |
| Phenotype node (`HP:`) | `hpo_term.hpo_id` ([02_reference.sql](../backend/db/schema/postgres/02_reference.sql)) |
| Disease node (`MONDO:`/`OMIM:`) | `gene_info.omim_gene_id`, `gene_info.extra` JSONB |
| Gene→Disease edge | New table + `gene_info.extra.monarch_associations` |
| Disease→Phenotype edge | New table |
| Per-patient observed HPO | `individual_hpo` ([03_assay.sql](../backend/db/schema/postgres/03_assay.sql)) |

The existing gene-reference sync (job queue + admin trigger + JSONB enrichment) and
the existing HPO ontology tables give us most of the plumbing already.

## Edge types of interest

| Subject → Object | Biolink predicate | Upstream source |
| --- | --- | --- |
| Gene → Disease | `causes` / `gene_associated_with_condition` | OMIM, Orphanet, ClinGen, GenCC |
| Disease → Phenotype | `has_phenotype` | HPOA |
| Gene → Phenotype | `has_phenotype` (derived) | HPOA |
| Variant → Disease | `is_sequence_variant_of` | ClinVar |
| Gene → Gene | `orthologous_to` | Panther / Alliance |

## Data acquisition

Two complementary channels.

### Bulk download (static associations)

Monthly releases at
[data.monarchinitiative.org/monarch-kg/latest](https://data.monarchinitiative.org/monarch-kg/latest/index.html).

| File | Size | Notes |
| --- | --- | --- |
| `monarch-kg.tar.gz` | ~300 MB | KGX TSV: `monarch-kg_nodes.tsv` + `monarch-kg_edges.tsv` — **recommended** |
| `monarch-kg.db.gz` | ~5.4 GB | Full SQLite |
| `monarch-kg.duckdb` | ~6.3 GB | DuckDB |
| `monarch-kg.jsonl.tar.gz` | ~465 MB | JSONL |
| `monarch-kg.neo4j.dump` / `.nt.gz` | — | Neo4j / RDF |

The full graph is one option, but Monarch also publishes **pre-split, denormalized
per-association TSVs** under `tsv/gene_associations/` and `tsv/disease_associations/`
— far smaller and with subject/object **labels inline**, so no nodes file is needed.
Phase 1 uses these:

| File | Rows (2026-06-08) | Predicates / sources |
| --- | --- | --- |
| `gene_associations/gene_disease.9606.tsv.gz` | ~7.2k | `causes`, `associated_with_increased_likelihood_of` · OMIM, ClinGen |
| `gene_associations/gene_disease.noncausal.tsv.gz` | ~8.9k | `gene_associated_with_condition`, `contributes_to` · OMIM, Orphanet |
| `gene_associations/gene_phenotype.9606.tsv.gz` | — | gene→HPO (Phase 3 input) |
| `disease_associations/disease_phenotype.all.tsv.gz` | — | disease→HPO (Phase 2 input) |

These denormalized files carry `subject`, `subject_label`, `predicate`, `object`,
`object_label`, `negated`, `primary_knowledge_source`, `aggregator_knowledge_source`
(plus mostly-empty `qualifiers`/`publications`/`has_evidence`). Phase 1 filters to
`HGNC:`→`MONDO:` rows where `negated` is not true. The noncausal file is cross-species
and mixes in a few `MONDO:`-subject rows, so the `HGNC:` subject filter is required.
The release version comes from `metadata.yaml` (`version: '2026-06-08'`). No graph
database is required for CoGA's read patterns.

### Live REST API (interactive matching)

[api-v3.monarchinitiative.org](https://api-v3.monarchinitiative.org/v3/docs), no auth.
Endpoints confirmed from its OpenAPI spec:

- `GET /v3/api/entity/{id}/{category}` — association table for an entity
- `GET /v3/api/entity/{context_id}/disease-phenotype-grid` — disease×phenotype grid for a gene
- `GET /v3/api/search`, `GET /v3/api/autocomplete` — entity search
- `POST /v3/api/semsim/compare`, `POST /v3/api/semsim/search` — phenotype-profile
  semantic similarity (ranks diseases/genes by phenotypic match)
- `GET /v3/api/mappings` — MONDO ↔ OMIM/Orphanet cross-references
- `GET /v3/api/annotate` — free-text → HPO/disease grounding
- `GET /v3/api/sources/versions` — upstream source versions (for provenance display)

## Identifier strategy

Monarch's canonical disease identifier is **MONDO**; CoGA currently keys diseases on
OMIM. Decision: **store MONDO as canonical, carry OMIM/Orphanet as cross-references.**
Monarch ships the mappings (`/v3/api/mappings` and the nodes TSV `xref` column), so we
do not need to force everything to OMIM. Genes key on `HGNC:` to align with
`gene_info.hgnc_id`.

## Storage

Reference associations are low-cardinality, join-heavy data — Postgres, alongside the
existing `hpo_*` tables, not ClickHouse.

**As shipped** — Phase 1's `monarch_gene_disease` (see
[02_reference.sql](../backend/db/schema/postgres/02_reference.sql))
collapses to one row per `(gene, disease)`, aggregating the sources and predicates
that a pair is asserted under. The original per-predicate, per-source proposal was
dropped after finding 232 duplicate `(gene, predicate, disease)` rows in the causal
file alone (same pair from OMIM and ClinGen):

```sql
CREATE TABLE IF NOT EXISTS monarch_gene_disease (
    hgnc_id         TEXT NOT NULL,
    gene_symbol     TEXT NOT NULL,
    mondo_id        TEXT NOT NULL,
    disease_label   TEXT,
    predicate       TEXT NOT NULL,         -- strongest relationship (biolink local name)
    predicates      JSONB NOT NULL DEFAULT '[]'::jsonb,
    sources         JSONB NOT NULL DEFAULT '[]'::jsonb,
    causal          BOOLEAN NOT NULL DEFAULT FALSE,   -- true only for biolink:causes
    release_version TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    PRIMARY KEY (hgnc_id, mondo_id)
);
CREATE INDEX idx_monarch_gene_disease_symbol ON monarch_gene_disease (upper(gene_symbol));
CREATE INDEX idx_monarch_gene_disease_mondo  ON monarch_gene_disease (mondo_id);
```

The earlier idea of denormalizing into `gene_info.extra.monarch_associations` was
**not** used: the data is per-HGNC and assembly-independent, so a standalone table
read by symbol at profile-build time is a single source of truth with no cross-assembly
staleness. `omim_id`/`orphanet_id` xref columns were also dropped from Phase 1 — the
disease key is MONDO and OMIM mapping is a follow-up.

Phase 2's `monarch_disease_phenotype` (**as shipped** — see
[02_reference.sql](../backend/db/schema/postgres/02_reference.sql)).
The proposed `frequency`/`onset` columns were dropped: the denormalized
`disease_phenotype.all.tsv.gz` does not carry them (its `qualifiers` column is empty).
A `negated` flag was added for explicitly excluded phenotypes:

```sql
CREATE TABLE IF NOT EXISTS monarch_disease_phenotype (
    mondo_id        TEXT NOT NULL,
    disease_label   TEXT,
    hpo_id          TEXT NOT NULL,
    phenotype_label TEXT,
    negated         BOOLEAN NOT NULL DEFAULT FALSE,   -- disease explicitly does NOT present this
    sources         JSONB NOT NULL DEFAULT '[]'::jsonb,
    release_version TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    PRIMARY KEY (mondo_id, hpo_id)
);
CREATE INDEX idx_monarch_disease_phenotype_hpo ON monarch_disease_phenotype (hpo_id);
```

## Ingestion

New service `backend/app/services/monarch_ingest.py`, mirroring
[gene_info_bulk_sources.py](../backend/app/services/gene_info_bulk_sources.py) and the
job orchestration in
[gene_info_jobs_pg.py](../backend/app/services/gene_info_jobs_pg.py):

1. Admin triggers `POST /admin/monarch/refresh` (queued job, status tracked like
   `gene_info_refresh_jobs`).
2. Worker downloads `monarch-kg.tar.gz`, records the release version from the directory
   name / `sources/versions`.
3. Stream `monarch-kg_edges.tsv`; filter to target predicates and human prefixes.
4. Resolve CURIEs; map MONDO↔OMIM via the nodes TSV `xref` column.
5. Upsert `monarch_gene_disease` and `monarch_disease_phenotype` (truncate-and-load per
   release, or upsert keyed on release_version).
6. Refresh `gene_info.extra.monarch_associations` for affected genes.

Refresh cadence: monthly, manual/admin-triggered (matches gene-reference sync). The job
should be idempotent and record `release_version` so the UI can show data provenance.

## Surfacing in the product

### Phase 1 — Gene → disease (implemented)

Shipped in this change. The pieces:

- **Migration** [02_reference.sql](../backend/db/schema/postgres/02_reference.sql)
  — `monarch_gene_disease`, one row per `(hgnc_id, mondo_id)` with aggregated
  `predicates`/`sources`, a representative `predicate`, a `causal` flag, and
  `release_version`. Indexed on `upper(gene_symbol)` and `mondo_id`.
- **Ingest** [monarch_ingest.py](../backend/app/services/monarch_ingest.py)
  — downloads the two human gene→disease TSVs
  (`gene_disease.9606.tsv.gz`, `gene_disease.noncausal.tsv.gz`), filters to
  `HGNC:`→`MONDO:` non-negated edges, aggregates, and replaces the table in one
  transaction. `refresh_monarch_gene_disease()` returns a summary;
  `list_monarch_gene_disease(symbol=…)` is the read-time lookup.
- **API** — `POST /api/admin/monarch/refresh` (admin-only,
  [admin.py](../backend/app/routers/admin.py)) runs the refresh inline and returns
  `MonarchRefreshSummaryOut`. The gene profile
  ([gene_metadata_service.py](../backend/app/services/gene_metadata_service.py))
  now returns a typed `monarch_associations` list on `GeneProfileOut`.
- **Frontend** — a "Monarch gene–disease associations" subsection on the gene
  profile disease panel ([GeneInfoPage.tsx](../frontend/src/pages/genes/GeneInfoPage.tsx)),
  each disease linking to `monarchinitiative.org/{MONDO}` with a
  predicate + sources caption; causal associations sort first.
- **Tests** — [test_monarch_ingest.py](../backend/tests/test_monarch_ingest.py)
  (parsing/aggregation/filtering) and an extended
  [GeneInfoPage test](../frontend/src/pages/genes/__tests__/GeneInfoPage.test.tsx).

Verified end-to-end against the live dev stack: the `2026-06-08` release ingests in
~2.5 s to **13,200 pairs across 5,463 genes** (6,877 causal).

**Operational note:** the table is populated by running the refresh, not at startup.
After deploy, an admin must `POST /api/admin/monarch/refresh` once (and monthly
thereafter) — until then the profile shows the empty state.

### Phase 2 — Disease → phenotype (implemented)

Shipped in this change. Builds directly on Phase 1's gene→disease MONDO key.

- **Migration** [02_reference.sql](../backend/db/schema/postgres/02_reference.sql)
  — `monarch_disease_phenotype`, one row per `(mondo_id, hpo_id)` with aggregated
  `sources` and a `negated` flag (an explicitly *excluded* phenotype; a present
  assertion from any source wins over an exclusion). Indexed on `hpo_id`.
- **Ingest** — `refresh_monarch_disease_phenotype()` loads
  `disease_associations/disease_phenotype.all.tsv.gz` (~4 MB), filters `MONDO:`→`HP:`
  rows, aggregates, and replaces the table in chunked inserts. A new
  `refresh_monarch()` orchestrator fetches the release once and refreshes both tables;
  `POST /api/admin/monarch/refresh` now calls it and `MonarchRefreshSummaryOut` gained
  the disease/phenotype counts.
- **Overlap** — `summarize_disease_phenotypes()` returns each disease's expected
  phenotype count and, when a family is in scope, the expected phenotypes the patient
  exhibits. Matching is **ancestor-aware** via `hpo_closure`: an expected phenotype
  counts as observed when the family has that HPO term *or a more specific descendant*
  (`family_observed_phenotype_closure()` expands the family's `present`
  `individual_hpo` terms to their ancestors). The gene profile already accepts
  `family_id`, so this rides the existing variant-review → gene-profile link.
- **Frontend** — each Monarch disease on the gene profile now shows its expected
  phenotype count and, in a family context, the matched phenotypes as chips
  ("N phenotypes · M observed in family").
- **Tests** — added disease-phenotype parsing tests (source aggregation, present-wins,
  exclusion-kept, non-HP filtering) and extended the GeneInfoPage test for the
  phenotype caption + matched chip.

Verified end-to-end against the live dev stack: full refresh ~8 s →
**245,814 disease→phenotype pairs across 11,230 diseases** (702 exclusions); **90 %**
of gene→disease MONDO ids have phenotypes; a real family's present terms matched
3 of Fanconi anemia's 106 expected phenotypes via HPO ancestry.

**Match semantics:** Phase 2 matches in one direction (disease expects X; patient has
X or a subtype). Symmetric information-content similarity is Phase 3 (semsim).

### Phase 3 — Phenotype-driven prioritization (implemented)

Shipped in this change. Pure live API — no bulk ingest, no new tables.

- **Service** [monarch_semsim.py](../backend/app/services/monarch_semsim.py)
  — `semsim_search(termset, group, limit)` POSTs to Monarch
  `POST /v3/api/semsim/search` (`group` ∈ `Human Genes` / `Human Diseases`,
  `ancestor_information_content` metric) and returns ranked `{rank, score, id, name}`.
  Wrapped with a 25 s timeout, a 1 h in-memory TTL cache keyed by the sorted termset,
  and a `MonarchSemsimError` so callers can surface a clean "unavailable".
- **Endpoint** `GET /api/families/{family_id}/phenotype-match` (optional `sample_id`,
  `group`, `limit`) in [families.py](../backend/app/routers/families.py) — collects the
  family's (or one member's) `present` `individual_hpo` terms, ranks genes, and flags
  which results exist in this platform (`gene_in_platform`) so the UI links straight to
  the gene profile. `FamilyPhenotypeMatchOut` carries the query terms + ranked results.
- **Frontend** — [MonarchPhenotypeMatchPanel.tsx](../frontend/src/pages/families/MonarchPhenotypeMatchPanel.tsx)
  on the family detail page: an on-demand "Find candidate genes" button (so Monarch is
  not hit on every page load) that renders the ranked list; in-platform genes link to
  `/genes?gene=…&family_id=…&project_id=…`.
- **Tests** — [test_monarch_semsim.py](../backend/tests/test_monarch_semsim.py)
  (normalize/cache/short-circuit, no network) and a
  [panel test](../frontend/src/pages/families/__tests__/MonarchPhenotypeMatchPanel.test.tsx).

**This closes the loop.** Patient phenotypes → ranked candidate genes (Phase 3) →
click a gene → its Monarch diseases (Phase 1) and which of the patient's phenotypes
match each disease (Phase 2). Verified end-to-end over HTTP against a real family with
congenital-hypothyroidism phenotypes: top hits were **TG, IYD, DUOXA2** — the thyroid
dyshormonogenesis genes — all linkable to the gene profile.

This is the symmetric, information-content phenotype similarity that Phase 2's
one-directional overlap could not provide.

**Ranking semantics (why a broad term ranks poorly).** The result is a *similarity
score*, not a membership test: each gene is scored by how closely its expected
phenotype profile resembles the patient's full HPO set, weighted by information
content (IC = −ln(p), p = fraction of diseases annotated to the term or a descendant).
Broad terms have high p and therefore near-zero IC. A single very general term such as
*Intellectual disability* (HP:0001249) is annotated to a huge share of diseases and is
linked to 1,000+ genes, so on its own it barely discriminates — every linked gene gets
an almost identical low score, the order is close to arbitrary, and Monarch only returns
the top ~50 anyway (`MAX_LIMIT`). This is inherent to IC-weighted similarity, not a bug.
The ranking only becomes meaningful as more *specific* co-occurring terms (high IC) are
added: the best-match-average then lifts the genes whose diseases explain those specific
features above the ones sharing only the umbrella term. The same property governs the
Phase 4 local Phenomizer score (which additionally bounds the patient set to the 60
highest-IC terms, so a broad term is dropped first when truncating).

### Phase 4 — Exomiser-style variant prioritization (implemented)

Uses the phenotype signal to rank a family's small variants, combining gene–phenotype
match with variant impact, rarity, segregation, and quality — the Exomiser model,
built on CoGA's existing annotation and pedigree infrastructure.

- **Local phenotype score** [monarch_phenotype_score.py](../backend/app/services/monarch_phenotype_score.py)
  — a Phenomizer/Resnik best-match-average between the affected individuals' HPO terms
  and each gene's Monarch phenotype profile, using information content derived from the
  Phase 2 disease→phenotype table propagated through `hpo_closure`. Unlike the Phase 3
  semsim API (top ~50 genes), this scores **every** gene with a candidate variant, with
  no per-request network call (IC map cached process-wide).
- **Scoring math** [variant_prioritization.py](../backend/app/services/variant_prioritization.py)
  — `pathogenicity` (impact/LoF/ClinVar/CADD/REVEL/SpliceAI, predictor-only capped below
  the ClinVar-reserved 1.0), `frequency` (gnomAD popmax decay), a `segregation` weight
  (trio-confirmed de novo, inherited dominant, homozygous recessive, compound het,
  X-linked via the existing pedigree helpers), and a weighted `combined` score where
  phenotype relevance reorders
  candidates without burying novel-gene candidates (their raw variant score is shown).
- **Query** — `GET /api/families/{family_id}/small-variants?prioritize=true` adds an
  isolated branch in [clickhouse_family_variants.py](../backend/app/services/clickhouse_family_variants.py)
  that fetches the filtered candidate set, computes segregation modes and scores, ranks,
  and returns a `priority` block per variant (`VariantPriorityOut`).
- **Frontend** — a built-in **"Phenotype priority (Exomiser-style)"** preset (rare +
  HIGH/MODERATE + `prioritize`), a sortable **Score** column with a phenotype-match
  marker and a hover breakdown, and a **priority breakdown panel** in the variant review
  dialog (variant / pathogenicity / rarity / phenotype sub-scores, compatible inheritance,
  matched phenotypes).
- **Tests** — [test_variant_prioritization.py](../backend/tests/test_variant_prioritization.py)
  (scoring math + Phenomizer) and the extended
  [SmallVariantTable test](../frontend/src/pages/families/__tests__/SmallVariantTable.test.tsx).

Verified end-to-end over HTTP on the congenital-hypothyroidism family: with the preset
applied, **ANTXR2** (HIGH-impact, homozygous-recessive) rose to **#1** on its phenotype
match (the patient's own "Congenital hypothyroidism, Goiter" shown as the reason), while
rare deleterious variants in repetitive false-positive loci (MUC4, NBPF10, USP17L) — with
no phenotype link — were pushed below it.

**De novo (trio-aware).** A variant is called de novo only when it is heterozygous in
an affected child and confidently homozygous-reference in **both** parents (full trio,
parent genotypes present and covered at ≥ `_DE_NOVO_MIN_PARENT_DP`), using the pedigree
`parent_child` relationships. This is distinguished from inherited dominant (which still
covers no-trio cases), and de novo carries the stronger segregation weight. A
homozygous-alt child with reference parents is left to the recessive pattern rather than
mislabeled de novo.

**Missense sharpening (AlphaMissense + gene constraint).** `pathogenicity` also folds in
**AlphaMissense** (the categorical `alpha_missense_class` from the annotation payload, plus
the numeric `am_pathogenicity` when a VCF carries it) and **gene-level constraint** from
`gene_info` (gnomAD pLI for LoF, missense-Z for missense, fetched for all candidates).
An AlphaMissense `likely_pathogenic` call lifts a missense toward the predictor ceiling;
`likely_benign` caps it (de-prioritizing benign missense) — but ClinVar assertions still
take precedence over both. pLI is validated to its [0, 1] range, so malformed source
constraint data is ignored rather than trusted. These signals surface in the review
dialog's priority breakdown.

**Caveats.** Scores rank *within a family*; they are not calibrated probabilities like
Exomiser's trained model. The candidate set is capped (5,000); when the filtered set
overflows the window the response reports the true filtered count and sets
`ranking_truncated`, and the UI shows a "ranking is incomplete — narrow your filters"
banner (the ranking only covers the window, so the true top variant could lie outside
it). Numeric AlphaMissense and gene constraint persist in the annotation payload /
`gene_info` respectively; a columnar filter index for AlphaMissense remains a future
enhancement.

### Hardening (code review)

A high-effort, multi-angle review ran over the whole branch; all findings were
addressed:

- **Ranking window** raised to 5,000; overflow now reports the true count and flags
  `ranking_truncated` (was a silent, position-ordered truncation).
- **Atomic Monarch refresh** — the two table swaps share one transaction, so a
  mid-refresh failure can't leave the tables on mismatched releases. The
  information-content cache is invalidated on refresh (and never caches an empty map),
  and its first-call aggregate is single-flight (an `asyncio.Lock`) to avoid a
  thundering herd on a cold cache.
- **CSV export** honours `prioritize` and prepends `Priority`/`Rank` columns so the
  file matches the ranked on-screen order.
- **Segregation is neutral when it can't be evaluated** (no affected individuals
  flagged) rather than applying a uniform penalty while phenotype is still scored.
- **Patient HPO** is bounded by information content (most informative terms), not
  lexicographic HPO-id order; the SpliceAI range guard is shared between the parse and
  read paths.

## Open questions / follow-ups

1. **Backfill for already-stored corrupt data.** Two annotation-import bugs were fixed
   in this branch — dbNSFP scientific-notation parsing (corrupted gnomAD pLI/LOEUF in
   `gene_info`) and a SpliceAI position-field leak (`spliceai_max > 1` in ~800k stored
   variants). The code fixes are forward-correct and the SpliceAI read path corrects
   stored data on the fly, but Postgres `gene_info` constraint values stay wrong until a
   gene-reference re-ingest. The dev DB was repaired directly; **other environments need
   a re-ingest** (or a committed backfill job). Tracked as the main follow-up.
2. **Bulk semsim vs live API for Phase 3** — shipped against the live API with a 25 s
   timeout + 1 h cache. Revisit if latency/availability becomes a problem (Monarch ships
   a `semsimian` SQLite for self-hosting). A variant-explorer ranking column and a
   columnar AlphaMissense filter index remain future enhancements.
3. **Refresh scheduling** — currently a manual admin action (release version is recorded
   on every row); could be moved to a scheduled job.
4. **Cleanup** — `_download_text`/`_download_gzip_tsv` and the two TTL caches are small
   boilerplate duplications left in place; a shared "fetch + filter + paginate" helper
   could serve both the inheritance and prioritization query paths.

## Documentation

User-facing documentation lives in the in-app **CoGA User Guide**
(route `/docs`; the section text is Markdown under
[content/docs/user-guide/](../frontend/src/content/docs/user-guide/)):

- **Phenotype matching (Monarch Initiative)** — the gene-profile gene–disease blocks,
  expected-phenotype overlap, and the family candidate-gene panel.
- **Phenotype-driven variant prioritisation (Exomiser-style)** — the preset, the Score
  column and breakdown, how the score is built, segregation modes, export, and caveats.
- Plus woven references in the Gene Explorer, Administration (the Monarch refresh), and
  Glossary (Monarch, HPO, phenotype similarity, pLI/LOEUF, AlphaMissense, de novo,
  priority score) sections.

## Sources

- [Monarch Initiative in 2024 (NAR)](https://academic.oup.com/nar/article/52/D1/D938/7449493)
- [Monarch ingest documentation](https://monarch-initiative.github.io/monarch-ingest/)
- [KG downloads](https://data.monarchinitiative.org/monarch-kg/latest/index.html)
- [Monarch v3 API](https://api-v3.monarchinitiative.org/v3/docs)
- [Biolink Model](https://biolink.github.io/biolink-model/)
