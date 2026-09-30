# Database Schema

CoGA keeps its metadata in Postgres and the high-volume variant and track rows in
ClickHouse. The API joins the two at request time. This page is the table-by-table
reference.

## Postgres Tables

The schema is five idempotent baseline files in
[backend/db/schema/postgres/](../backend/db/schema/postgres/), applied in name order on every
start (see "Startup Behavior"). There is no migration ledger. Each table is created in its
final form; the few columns added later use `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in the
same file.

### 01_access.sql: genome foundation and access

| Table | Holds |
| --- | --- |
| `species` | organisms: name, common name, NCBI taxonomy id |
| `assemblies` | genome assemblies per species |
| `chromosomes` | chromosome sizes and cytobands per assembly |
| `users` | accounts: username, password hash, role (`admin`, `superuser` or `viewer`), active flag |
| `projects` | projects, each bound to one species and assembly |
| `project_users` | which users belong to which project; data access is scoped by project |
| `auth_login_attempts` | failed-login counts and back-off per email and per client address, and sign-up attempts per address |

### 02_reference.sql: reference and annotation data

| Table | Holds |
| --- | --- |
| `genes` | gene loci per assembly, one row per transcript (GENCODE for GRCh38) |
| `gene_info` | the cached gene reference per human gene: identifiers, disease links, dosage, constraint, and a status and release per source |
| `gene_info_refresh_jobs` | queued and running gene-reference syncs |
| `blacklist` | problem regions per assembly |
| `clinical_cnvs` | known CNV syndromes per assembly, with their ClinVar support (below) |
| `clinical_cnv_kb_jobs` | Progress of admin-triggered rebuilds of the clinical CNV knowledgebase, per assembly (`queued`, `running`, `completed` or `failed`), with the number of rows inserted, the error and the tail of the build log. At most one job is queued or running at a time (`idx_clinical_cnv_kb_jobs_one_active`, a unique index on a constant). `worker_id` and `heartbeat_at` mark the worker running a job; a job whose heartbeat is ten minutes old is closed as failed. |
| `dgv_variants` | Database of Genomic Variants entries per assembly, with a gain/loss/mixed class |
| `segmental_duplications` | segmental duplications and low-copy repeats per assembly |
| `gene_panels`, `gene_panel_genes` | panels and their gene lists |
| `gene_panel_regions` | a panel's coordinates, one row per gene (or PanelApp region) per assembly (below) |
| `gene_panel_versions` | an immutable snapshot of every version of a panel: genes, regions, source, external version and author |
| `hpo_term`, `hpo_synonym`, `hpo_edge`, `hpo_closure` | one HPO release: terms, synonyms, `is_a` links, and every ancestor of a term with its distance |
| `monarch_gene_disease`, `monarch_disease_phenotype` | Monarch gene-to-disease links (predicate, sources, causal or not) and disease-to-HPO annotations, including negated ones |
| `repeat_loci` | the TRGT repeat catalogue (STRchive thresholds, genes, diseases, motifs) |
| `reference_dataset_imports` | every reference import and upload: dataset, rows inserted, whether it replaced data, source, the release the source states (below), who and when |

`clinical_cnvs` carries the knowledgebase's ClinVar support per region:
`clinvar_pathogenic_loss_count` and `clinvar_pathogenic_gain_count` (pathogenic ClinVar CNVs
that overlap the region by at least 30 % reciprocally, per side) and
`clinvar_pathogenic_accessions` (their VariationIDs). NULL means the knowledgebase recorded
no ClinVar support (built without ClinVar, or loaded from a BED-style file), not zero.

`gene_panel_regions` is keyed on `(panel_id, assembly_id, gene, chr, start, end)`. A family's
panel filter reads only the rows of its own assembly, plus the loci of the panel's genes
resolved in that assembly at query time. With no resolved assembly it narrows by gene symbol
alone. PanelApp's own coordinates are stored for the assembly they were requested for.

`reference_dataset_imports.source_version` and `source_release_date` hold the release the
source states about itself. A GENCODE gene import takes both from its GTF header. No other
source states one: UCSC tables, a GTF without that header, the clinical-CNV knowledgebase
(built from the sources as they are on the day), uploads, the built-in reference files and
the DGV script. Their `source_version` is `not stated` and their `source_release_date` is
NULL. `source` is the label the readers show, such as `gencode v50 (Ensembl 116)`; a
built-in reference file is recorded under its file name.

### 03_assay.sql: families, samples, assay data and review

| Table | Holds |
| --- | --- |
| `family_statuses` | the admin-managed workflow statuses; `families.status_id` points here. Seeded: `solved`, `strong_candidate`, `reviewed_no_candidate`, `closed`, `analysis_in_progress`, `data_ready`, `waiting_for_data`, `loading_failed`, `no_data_expected` |
| `families` | a family: its `family_id`, PED text, region of interest, status, assignee and `metadata` (below) |
| `samples` | a sample: its `sample_id`, family, sex and `metadata` (below) |
| `family_members` | a sample's place in its family: role, clinical and carrier status, active flag |
| `family_projects`, `sample_projects` | which projects can see a family or a sample |
| `family_relationships` | explicit links between two samples: `parent_child` (with the role at each end) or `couple`; `source` says whether a PED import or an edit made it, `active = false` retires it |
| `family_structure_versions` | one row per pedigree or phenotype change: the version, a `structure_hash` over roles, parentage and affected status, and the full snapshot |
| `family_import_jobs` | package-import jobs: status, logs, validation issues and a summary per dataset |
| `individual_hpo` | per-person HPO terms, each `present`, `absent` or `unknown` |
| `repeat_expansions` | TRGT repeat calls per sample |
| `sample_paraphase_results` | Paraphase copy-number and haplotype results per sample |
| `nipt_artifact_variants` | the recurrent-artifact list for monogenic NIPT, per assembly and assay, curated or seeded from the cohort (see [monogenic-nipt.md](monogenic-nipt.md)) |
| `sample_interval_track_sources` | one row per sample, track type (`coverage`, `segments`, `apcad`, `apcad_pcf`, `haplotype`), source and file, with its row count; the rows themselves are in ClickHouse |
| `small_variant_reviews` | the classification, ACMG criteria, tags, notes and evidence snapshot of a small variant in a family |
| `structural_variant_reviews` | the same for a structural variant or CNV, with the CNV ACMG points and the evidence snapshot of the CNV classification |
| `small_variant_filter_presets`, `structural_variant_filter_presets` | saved filter sets, per user and for one family or all |
| `small_variant_tag_definitions`, `small_variant_tag_definition_project_links` | the review-tag catalogue, global or per project |
| `family_sv_gene_index`, `family_sv_gene_index_status` | per family, which genes a structural variant hits (for the "also hit by an SV" flag), and when and from which SV data version (`sv_data_version`, below) that index was built. It is rebuilt on next use once the family's SVs have changed |
| `family_variant_ranking_cache` | cached prioritised rankings (see [variant-ranking-cache.md](variant-ranking-cache.md)) |
| `qc_threshold_profiles` | named sets of sequencing-QC cut-offs, one per assay type (below) |
| `qc_thresholds` | per profile and metric, a warning and an error bound (either may be null) |
| `qc_threshold_changes` | **append-only** history of every cut-off edit (below) |

JSON keys that package import writes into the `metadata` columns:

| Column | Key | Contents |
| --- | --- | --- |
| `samples.metadata` | `sequencing_qc` | read metrics (NanoPlot), depth (mosdepth) and the path of the QC report |
| `samples.metadata` | `alignment` | the CRAM/BAM path and index, package-relative (`path`, `index_path`), and for a package in a bucket the objects' URIs (`uri`, `index_uri`), which IGV reads |
| `samples.metadata` | `signal_tracks` | the package-relative paths of the HiFiCNV depth, MAF and copy-number files, served to IGV, and for a package in a bucket the objects' URIs under `uris` |
| `samples.metadata` | `mtdna` | the mtDNA haplogroup from the mutserve annotation |
| `samples.metadata` | `sv_files` | the file name per structural-variant source |
| `families.metadata` | `pipeline` | the Nextflow run parameters (reference build, callers, annotation caches) |
| `families.metadata` | `package_import` | the import provenance: folder (a bucket folder's URI), manifest, datasets and the manifest's own `metadata` |
| `families.metadata` | `pgt`, `analysis_type` | the manifest's PGT context and analysis type |

A family's `metadata` also holds `derived_data_status` (which analyses an edit made stale, and
whether a structure update kept the imported data; see
[family-member-management.md](family-member-management.md)) and `qc_profile` (below).

**Sequencing-QC limits.** A family uses the profile named in `families.metadata->>'qc_profile'`,
or the `is_default` one; a partial unique index allows only one default. The seeded profiles
(`default`, `long_read_wgs`, `short_read_wgs`, `short_read_panel`, `nipt_monogenic`, `pgt`)
are empty, because the laboratory agrees the cut-offs. A new profile
(`POST /admin/qc-thresholds/profiles`) inherits none. Its key comes from the label and never
changes, since families refer to it. Which metrics exist, and whether a low or a high value
fails, is defined in code (`qc_threshold_service.QC_METRICS`); only the bounds are
configuration, and `direction` is copied onto each row so it stays readable. Each edit to a
bound requires a reason and is recorded in `qc_threshold_changes` with the replaced value, the
new value, the reason and the user; the request log alone could not say what the old value
was.

### 04_traceability.sql: provenance, audit and integrity

| Table | Holds |
| --- | --- |
| `audit_log_events` | **append-only** log of every HTTP request: user, route, status, duration, client address, and the request body with sensitive fields masked |
| `ui_events` | UI interactions that never reach the backend (clicks, in-app navigation), masked before storage |
| `raw_import_files` | every source file an import used, with its size and SHA-256, for download and integrity checks (below) |
| `family_annotation_manifest` | per family, the upstream annotation versions (VEP, ClinVar, gnomAD, …) and where they came from (`manifest`, `vcf_header` or `manual`) |
| `clinical_audit_events` | **append-only**, hash-chained log of clinical actions: classification, tags, notes and sign-out, with before and after |
| `report_signouts` | **append-only**, hash-chained signed reports: each sign-out is a new version with a content hash and the frozen snapshot |
| `integrity_anchors` | **append-only**, signed snapshots of every hash chain's head |

**Files from a bucket.** For a package imported from a bucket, `storage_path` is the
object's `gs://` or `s3://` URI, and `metadata.store_object` holds the store's own record of
the object: its size, GCS `generation` or S3 `version_id`, `etag`, and the store's checksums,
each labelled with its `algorithm` and `encoding` (and for S3 whether it covers the whole
object). A file staged for the import also has its SHA-256, taken from the staged copy. An
alignment that stayed in the bucket has none: the store's checksums are not SHA-256, so they
are never put in `sha256`. **Verify** on the admin page checks such a file against
`store_object` rather than re-hashing it: the object must still exist, with the recorded
size and generation or version id (an unversioned S3 object is compared by ETag). It reports
a vanished object as `missing` and a replaced one as `mismatch`. The file is not downloaded
through CoGA.

What these records hold and how they are checked is in
[clinical-traceability.md](clinical-traceability.md).

Triggers in this file refuse UPDATE and DELETE on the five append-only tables
(`audit_log_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors` and
`qc_threshold_changes`). On the tables that name a user or a family, the one change they
allow is the `ON DELETE SET NULL` unlink when that user or family is deleted; the rows keep a
copy of the actor and the family identifier.

### 05_grants.sql: the runtime role

Creates the restricted role `coga_app` and grants it normal access to every table, except
UPDATE, DELETE and TRUNCATE on the five append-only tables. How and when the application
switches to it is in [db-runtime-role-runbook.md](db-runtime-role-runbook.md).

## ClickHouse Tables

[001_coga_variant_storage.sql](../backend/db/schema/clickhouse/001_coga_variant_storage.sql)
only creates the database. Each assembly gets its own tables, created the first time the
backend uses that assembly, by
[clickhouse_variant_storage.py](../backend/app/services/clickhouse_variant_storage.py) and
[clickhouse_interval_tracks.py](../backend/app/services/clickhouse_interval_tracks.py).
Their names start with the assembly, for example `GRCh38/SNV_INDEL/entries`.

| Table | Holds |
| --- | --- |
| `SNV_INDEL/variants/details` | one row per small variant and annotation set: position, alleles, the full annotation |
| `SNV_INDEL/variants/annotations` | per variant and transcript: gene, consequence, HGVS, population frequencies, scores |
| `SNV_INDEL/variants/annotation_index` | per variant: its genes and the fields the filters use |
| `SNV_INDEL/variants/gene_index` | per gene term and variant, for gene searches |
| `SNV_INDEL/entries` | the calls: per family and variant, each sample's genotype and call fields (`calls.*`) |
| `SNV_INDEL/family_variant_summary`, `SNV_INDEL/family_sample_variant_summary` | variant counts per family and per sample |
| `SNV_INDEL/family_data_version` | one token per change to a family's small variants (below) |
| `SV/variants/details` | one row per SV key, that is per family, structural variant and source: type, span, the full annotation |
| `SV/key_lookup` | maps a family's variant ids, per source, to their internal keys |
| `SV/entries` | the structural-variant calls per family and sample |
| `SV/family_data_version` | one token per change to a family's structural variants (below) |
| `INTERVAL/entries` | the interval-track rows: coverage, segments, APCAD, PCF segments and haplotype blocks |

Column-level detail lives with the DDL in `clickhouse_variant_storage.py`
(`ensure_clickhouse_variant_tables`). Columns worth calling out:

| Table | Column | Why it exists |
| --- | --- | --- |
| `…/SNV_INDEL/entries` | `calls.ps` | Phase set, for read-based cis/trans against a phased SV |
| `…/SNV_INDEL/entries` | `calls.af` | Per-allele fraction; on chrM this *is* the heteroplasmy level the mtDNA workspace reads |
| `…/SV/entries` | `calls.ps` | Phase set for a structural call |
| `…/SV/entries` | `calls.cn` | Copy number from a depth-based CNV caller (HiFiCNV `FORMAT/CN`). `GT=1/1` on a duplication cannot distinguish CN=3 from CN=6, and the ClinGen CNV dosage scoring needs the actual number. Null for callers that report none. |

The `source` column on both `entries` tables scopes deletes and re-imports, so one family can
hold several callsets side by side. Small variants: `clair3` (the primary callset, whatever
caller made it), `glimpse2` (imputed; hidden from the diagnostic lists by default) and `mito`
(chrM). Structural variants: `needlr` and `hificnv` from packages, and `manual_upload`,
`sniffles` or `spectre` from a direct upload.

Deleting a sample (`DELETE /admin/samples/{sample_id}`) rewrites the family's
`SNV_INDEL/entries` from the stored rows: every callset, project and column comes back as
stored, minus that sample's calls. The shared `SNV_INDEL/variants/*` tables are left alone, so
each row keeps its annotation version and annotation-set hash.

An SV delete (`delete_family_structural_variants`) covers a family, or one source of it: the
delete half of a per-sample upload's rewrite and of a package dataset's re-import. It deletes the
scope's `SV/entries` rows first, then the scope's `SV/variants/details` and `SV/key_lookup` rows
whose key no remaining `SV/entries` row of the family has. Unlike the small-variant annotation
tables, these two are not shared between families. An SV's key hashes the family
([Row identity](#row-identity)), every row carries `family_guid`, and every write that keeps a
stored key (a per-sample upload, the admin sample delete, a snapshot restore) writes it back to
the family it read it from. No other family's entry can reach them, so the delete reads only the
family's own entries. After a family delete no entry is left and all of the family's rows go.
After a source-scoped delete the source's rows go, but a row whose key another source's entry
still has is kept: two callers' calls at one position shared a key before the key named the
source, and `variants/details` keeps one row per key, so deleting it would strip the other
caller's SV of its span, length and annotation. Other sources' and other families' rows are
never touched.

`SNV_INDEL/family_data_version` (plain `MergeTree`) is not variant data. Every change to a
family's small variants (an insert, a full or source-scoped delete, and the summary refresh
that also follows a snapshot restore) appends one row with a random token once the write
completes. The count and sum of the family's tokens are its small-variant data version, which
the ranking cache puts in its key. Rows are never collapsed, so the version never returns to
an earlier value.

`SV/family_data_version` does the same for structural variants: every SV insert and delete, and
a snapshot restore, appends a token. The SV→gene index stores the version it was built from in
`family_sv_gene_index_status.sv_data_version` and is rebuilt when the family's version has moved.

### Row identity

A stored row is one callset's call of one variant, in one family and one project. When
ClickHouse merges a table's parts it keeps one row per sort key, so the sort key of each of
these tables ends with the callset (`source`):

| Table | Sort key |
| --- | --- |
| `SNV_INDEL/entries` | `project_guid, family_guid, xpos, key, source` |
| `SV/entries` | `project_guid, family_guid, svType, chrom, start, key, source` |
| `SV/key_lookup` | `family_guid, variantId, source` |

- A small variant's `key` is a hash of the assembly and the variant id. It is the same for
  every family and callset, so the shared annotation tables (`variants/details`,
  `annotations`, `annotation_index`, `gene_index`) serve them all. A clair3 call and a GLIMPSE2
  imputation of one variant are two `entries` rows with one key.
- An SV's `key` is a hash of the assembly, the family, the variant id and the source. A
  per-sample upload names an SV `chrom-start-end-type---`, without its caller, so a Sniffles and
  a Spectre call at the same breakpoints have one id but two keys. Each has its own `entries`,
  `variants/details` and `key_lookup` row.
- The variant id does not depend on the callset. Reviews, classification evidence snapshots,
  the ranking cache and the SV→gene index attach by it, so they cover every callset's row of a
  variant.

When several callsets hold one small variant, the diagnostic lists show the direct call; an
imputed callset shows only when it is asked for. Reviews and the drift check read the direct
call, and among several direct callsets the one first in name order. The SV list shows an SV
once per caller, each row with that caller's calls.

#### Tables with a different row identity

A table's sort key is fixed when ClickHouse creates it. At startup, and before it creates an
assembly's tables, the backend compares the sort key of each table above with the one in the
list. If a table has another key, the backend refuses to start. The message names the table,
its sort key and the one it needs. The rows are not copied into a new table: under the other
key, merges may already have dropped calls, and only a re-import brings them back.

To recover, for each assembly the message names:

1. Stop the backend.
2. Drop the assembly's small-variant and SV tables. List them with
   `SELECT name FROM system.tables WHERE database = 'coga' AND match(name, '^GRCh38/(SNV_INDEL|SV)/')`
   (with your database and assembly), then drop each one with ``DROP TABLE coga.`<name>` SYNC``.
   The interval tracks (`INTERVAL/entries`) stay.
3. Start the backend. It creates the tables with the sort keys above.
4. Re-import every family of the assembly. Reviews, classifications and signed reports are in
   Postgres, and attach again by variant id.

Locally, `docker compose down -v` and loading the demo data again does the same
([development.md](development.md#stop-and-reset)).

## Identifier Rules

- Metadata rows use UUID primary keys.
- API-facing variant IDs are stable string identifiers.
- People see `family_id` and `sample_id`.

## Relationships

- `species -> assemblies`
- `assemblies -> chromosomes / genes / blacklist / clinical_cnvs / dgv_variants / segmental_duplications / gene_panel_regions`
- `projects -> species + assemblies`
- `families <-> projects`, `samples <-> projects`
- `samples -> families`
- `family_members` maps pedigree roles and affected state
- `small_variant_reviews` and `structural_variant_reviews` attach Postgres review state to
  ClickHouse variant IDs and keys

## Startup Behavior

On every start the backend re-applies the five Postgres baseline files and makes sure the
admin user exists, when `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP` is true (the default).
With the restricted runtime role a separate migration step (`backend/app/db_migrate.py`)
does this instead, as the table owner. In ClickHouse, startup creates the database and refuses
to go on when an assembly's tables have a different row identity
([Row identity](#row-identity)); each assembly's tables are created on first use. The full startup order is in
[application-scheme.md](application-scheme.md), "Startup and background work"; the reference
data it loads is listed in [data-import.md](data-import.md), section 1.
