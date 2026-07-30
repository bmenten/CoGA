# Database Schema

The live schema is split across `Postgres` and `ClickHouse`.

## Postgres Tables

The Postgres schema lives in five domain-grouped, idempotent baseline files under `backend/db/schema/postgres/`. Each table is created once in its final form (later `ALTER ... ADD COLUMN` steps are folded into the `CREATE TABLE`). The domains are: `01_access` (pgcrypto + genome foundation + identity/authorization), `02_reference` (reference/annotation data), `03_assay` (families/samples + per-sample assay data + review/curation), `04_traceability` (import provenance + append-only hash-chained clinical audit + integrity), and `05_grants` (the restricted `coga_app` runtime role + grants/revokes).

Metadata and access:

- `users`
- `species`
- `assemblies`
- `projects`
- `project_users`
- `families`
- `family_projects`
- `samples`
- `sample_projects`
- `family_members`

Reference data:

- `chromosomes`
- `genes`
- `blacklist`
- `clinical_cnvs`
- `segmental_duplications`

Review and annotation state:

- `small_variant_reviews`
- `structural_variant_reviews`
- `small_variant_filter_presets`
- `small_variant_tag_definitions`
- `small_variant_tag_definition_project_links`
- `gene_panels`
- `gene_panel_genes`
- `gene_panel_regions`
- `gene_info`
- `gene_info_refresh_jobs`
- `audit_log_events`

Repeat expansions and tracks:

- `repeat_loci`
- `repeat_expansions`
- `sample_interval_track_sources`
- `sample_paraphase_results`

Per-sample and per-family JSONB metadata written by package import (no schema change —
these are keys inside the existing `metadata` columns):

| Column | Key | Contents |
| --- | --- | --- |
| `samples.metadata` | `sequencing_qc` | Read-level metrics (NanoPlot/NanoStats), per-chromosome and genome-wide depth (mosdepth), and the package-relative path of the rendered QC report |
| `samples.metadata` | `alignment` | Package-relative CRAM/BAM path + index, so the genome browser resolves reads that live in the pipeline's own layout |
| `samples.metadata` | `mtdna` | mtDNA haplogroup assigned from the mutserve annotation |
| `samples.metadata` | `sv_files` | Filename per structural-variant source |
| `families.metadata` | `pipeline` | Nextflow run parameters (reference build, callers, annotation caches) |

Sequencing-QC acceptance limits (admin-managed):

- `qc_threshold_profiles` — named cut-off sets, one per assay type; a family resolves to
  the profile named in `families.metadata->>'qc_profile'` and to the `is_default` profile
  otherwise. A partial unique index enforces at most one default.
- `qc_threshold_changes` — **append-only** history of every cut-off edit, holding the
  value that was *replaced* as well as the new one plus the acting user. The HTTP
  request-audit pipeline records the request but has no prior value, so "who lowered the
  depth limit, and from what" is only answerable here. UPDATE and DELETE are rejected by
  trigger (`04_traceability.sql`), like the clinical audit and sign-out chains.
- `qc_thresholds` — per profile and metric, a warning and an error bound (either may be
  null). The *metric catalogue* — which metrics exist and whether a low or a high value is
  the failing side — lives in code (`qc_threshold_service.QC_METRICS`), because it follows
  what the QC parsers emit; only the bounds are configuration. `direction` is written
  through from that catalogue so a stored row stays interpretable without the code version
  that wrote it.

Import jobs:

- `family_import_jobs`

Canonical schema files:

- [01_access.sql](../backend/db/schema/postgres/01_access.sql) — pgcrypto extension + genome foundation (`species`, `assemblies`, `chromosomes`) + identity/authorization (`users`, `projects`, `project_users`, `auth_login_attempts`)
- [02_reference.sql](../backend/db/schema/postgres/02_reference.sql) — reference/annotation data (`genes`, `gene_info`, `blacklist`, `clinical_cnvs`, `dgv_variants`, `segmental_duplications`, `gene_panels` and children, `hpo_*`, `monarch_*`, `repeat_loci`, `reference_dataset_imports`)
- [03_assay.sql](../backend/db/schema/postgres/03_assay.sql) — families/samples + per-sample assay data + review/curation (`families`, `samples`, `family_members`, `family_projects`, `sample_projects`, `individual_hpo`, `repeat_expansions`, `sample_paraphase_results`, `sample_interval_track_sources`, `small_variant_reviews`, `structural_variant_reviews`, tag/preset tables, `family_sv_gene_index`, `family_variant_ranking_cache`, `family_import_jobs`, `qc_threshold_profiles`, `qc_thresholds`, `qc_threshold_changes`)
- [04_traceability.sql](../backend/db/schema/postgres/04_traceability.sql) — import provenance + append-only hash-chained clinical audit + integrity (`audit_log_events`, `ui_events`, `raw_import_files`, `family_annotation_manifest`, `clinical_audit_events`, `report_signouts`, `integrity_anchors`) plus the immutability trigger functions/triggers, including the append-only guard on `qc_threshold_changes`
- [05_grants.sql](../backend/db/schema/postgres/05_grants.sql) — the restricted `coga_app` runtime role + grants/revokes

## ClickHouse Tables

The ClickHouse SQL bootstrap creates the database:

- [001_coga_variant_storage.sql](../backend/db/schema/clickhouse/001_coga_variant_storage.sql)

Per-assembly variant and interval tables are created at runtime by:

- [clickhouse_variant_storage.py](../backend/app/services/clickhouse_variant_storage.py)
- [clickhouse_interval_tracks.py](../backend/app/services/clickhouse_interval_tracks.py)

The important logical entities are:

- small variant records
- small variant sample calls
- structural variant records
- structural variant sample calls
- interval track records for coverage, WisecondorX segments, APCAD, PCF APCAD segment overlays, and haplotypes

Column-level detail lives with the DDL in `clickhouse_variant_storage.py`
(`ensure_clickhouse_variant_tables`), which also carries the in-place `ALTER … ADD COLUMN
IF NOT EXISTS` migrations for existing databases. Columns worth calling out:

| Table | Column | Why it exists |
| --- | --- | --- |
| `…/SNV_INDEL/entries` | `calls.ps` | Phase set, for read-based cis/trans against a phased SV |
| `…/SNV_INDEL/entries` | `calls.af` | Per-allele fraction; on chrM this *is* the heteroplasmy level the mtDNA workspace reads |
| `…/SV/entries` | `calls.ps` | Phase set for a structural call |
| `…/SV/entries` | `calls.cn` | Copy number from a depth-based CNV caller (HiFiCNV `FORMAT/CN`). `GT=1/1` on a duplication cannot distinguish CN=3 from CN=6, and the ClinGen CNV dosage scoring needs the actual number. Null for callers that report none. |

The `source` column on both `entries` tables scopes deletes and re-imports, so one family
can hold several independent callsets side by side: `clair3` (primary nuclear SNVs),
`glimpse2` (imputed), `mito` (chrM), `needlr` and `hificnv` (SVs/CNVs).

## Identifier Rules

- Metadata rows use UUID primary keys.
- API-facing variant IDs are stable string identifiers.
- Human-facing family/sample identifiers remain `family_id` and `sample_id`.

## Relationships

- `species -> assemblies`
- `assemblies -> chromosomes / genes / blacklist / clinical_cnvs / segmental_duplications`
- `projects -> species + assemblies`
- `families <-> projects`
- `samples -> families`
- `samples <-> projects`
- `family_members` maps pedigree roles and affected state
- `small_variant_reviews` attach Postgres annotations to ClickHouse variant IDs/keys

## Startup Behavior

Application startup:

1. waits for Postgres
2. applies the Postgres schema — the loader re-applies all five baseline files in sorted name order on every boot (idempotent; there is no migration ledger)
3. ensures the admin user exists
4. seeds the built-in repeat catalog
5. ensures Homo sapiens GRCh38 exists and imports missing GRCh38 cytobands/genes from UCSC when available
6. seeds built-in hg38 reference tracks such as clinical CNVs and segmental duplications
7. queues the first dbNSFP-backed human gene reference sync when the local dbNSFP gene file is present and `gene_info` is empty
8. waits for ClickHouse
9. applies the ClickHouse schema
10. starts the gene refresh worker
