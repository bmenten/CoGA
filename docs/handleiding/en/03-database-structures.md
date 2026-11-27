# 3. Database structures (Postgres & ClickHouse)

CoGA splits its data between two databases: **Postgres** for metadata, access rights, review state and the clinical paper trails, and **ClickHouse** for the huge numbers of variant and track rows. The table-by-table reference is `docs/database.md`; it is the source of truth.

A few terms: **DDL** (*Data Definition Language*) are the SQL commands that create or change tables; a **foreign key** is a reference from a row to a row in another table; an **ERD** (*entity-relationship diagram*) draws the tables with their keys and the references between them.

## Two databases, one agreement

Postgres is the source of truth for everything that is relational, transactional and safety-critical. It offers foreign keys, `CHECK` rules and triggers: exactly what access control and an unalterable audit trail need. ClickHouse filters and counts fast over hundreds of millions of rows; that is where the variants and the large interval tracks are kept (chapter 1 explains the choice).

Metadata rows use UUIDs as their key. The variant IDs the API shows are stable strings (e.g. `1-1000-A-T`), independent of the storage. For people, `family_id` and `sample_id` remain the recognisable names.

## Postgres: five baseline files

The Postgres schema is in five SQL files in `backend/db/schema/postgres/`, grouped by domain. Each table is created there once, in its final form. Revision `0001` applies them to an empty database (see below, *How the schemas are applied*).

| File | Domain | Main tables |
| --- | --- | --- |
| `01_access.sql` | Genome basis and access | `species`, `assemblies`, `chromosomes` (with the cytobands), `users` (with the role rule), `projects`, `project_users`, `auth_login_attempts` |
| `02_reference.sql` | Reference and knowledge data | `genes`, `gene_info`, `blacklist`, `clinical_cnvs`, `dgv_variants`, `dgv_load_jobs`, `clinvar_load_jobs`, `segmental_duplications`, the panels (`gene_panels`, `gene_panel_versions`, …), the HPO tables, the Monarch tables, `repeat_loci` and QA's per-locus count correction `repeat_locus_count_units`, `reference_dataset_imports`, and the job tables `gene_info_refresh_jobs` and `clinical_cnv_kb_jobs` |
| `03_assay.sql` | Families, samples and review state | `family_statuses`, `families`, `samples`, `family_members`, `family_projects`, `sample_projects`, `family_relationships`, the append-only history of the pedigree `family_structure_versions`, `individual_hpo`, the assay data (`repeat_expansions`, `sample_paraphase_results`, `nipt_artifact_variants`, `sample_interval_track_sources`), the reviews (`small_variant_reviews`, `structural_variant_reviews`, `repeat_expansion_reviews`, `paraphase_reviews`), tags and filter presets, the import (`family_import_jobs`, the deliveries `import_deliveries` and their append-only corrections `import_delivery_revisions`), the caches (`family_variant_ranking_cache`, `family_sv_gene_index`) and the sequencing QC limits (`qc_threshold_profiles`, `qc_thresholds` and their append-only history `qc_threshold_changes`) |
| `04_traceability.sql` | Provenance and clinical audit trail | `raw_import_files`, `family_annotation_manifest`, `audit_log_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors`, `ui_events`, the tables of the exploratory ancestry PCA (`ancestry_reference_panels`, `ancestry_runs`, `ancestry_results`, `ancestry_sample_choices`; chapter 15), plus the append-only triggers (including those on `qc_threshold_changes`, `family_structure_versions` and `import_delivery_revisions`), the migration ledger `alembic_version` and the baseline digest `schema_baselines` |
| `05_grants.sql` | Separation of privileges | The restricted runtime role `coga_app`, the `REVOKE` on the append-only tables, the migration ledger and the baseline digest (chapter 2) |

Two features stand out. Almost all per-assembly reference data refers to `assemblies` with `ON DELETE CASCADE`; the Monarch tables are deliberately assembly-independent (linked on HGNC and MONDO IDs). And the frozen ACMG evidence per classification is in the column `small_variant_reviews.acmg_evidence_snapshot`, that of a CNV classification in `structural_variant_reviews.cnv_evidence_snapshot` (chapter 10).

**An assembly is its name.** `assemblies.assembly_name` is unique. The variant tables in ClickHouse (below) and every lookup by name read only the name. Two assemblies with one name would therefore share one set of variant tables, while genes, cytobands, panels, DGV and clinical CNVs could each hang on a different row. `version` and `release_date` are what the last import from UCSC reported: the GRC patch, e.g. `p14`. A later import of that assembly keeps the same row and the same ID and updates those two, even if UCSC meanwhile describes a later patch (chapter 4). *Add assembly manually* refuses a name that already exists (`409`).

**Where in the code:** the files in `backend/db/schema/postgres/`; `_store_assembly` in `backend/app/services/reference_source_service.py` and `create_assembly_record` in `backend/app/services/metadata_service.py` (the assembly by name); the full column reference in `docs/database.md`, with an ERD of the core and one per domain of each baseline file (*Postgres Tables*).

## ClickHouse: tables per assembly

The ClickHouse schema file `backend/db/schema/clickhouse/001_coga_variant_storage.sql` only creates the database. The backend creates the actual tables at runtime, **per assembly**: their names start with the assembly (e.g. `` `GRCh38/SNV_INDEL/entries` ``). First the assembly name is converted to a safe key: characters other than letters, digits, full stop, underscore and hyphen (e.g. a space) become `_`, so `T2T CHM13v2.0` becomes `T2T_CHM13v2.0`. Import and reading use the same function and so always find the same table.

| Tables (per assembly) | What they contain |
| --- | --- |
| `SNV_INDEL/variants/details`, `…/annotations`, `…/annotation_index`, `…/gene_index` | The variant record, the full annotation per annotation version, and the indexes the filter pages search on (on annotation fields and on gene) |
| `SNV_INDEL/entries` | The calls per family: per variant the genotypes, depth and quality of each sample. From a single-sample VCF (such as the two files of a NIPT pair) also the FILTER values and the caller metrics of each call (`calls.filters`, `calls.metrics`). Per row also `transcripts`: the values of the row's own annotation that the per-transcript filters test, as `…/annotations` stores them per transcript, without the transcript and HGVS text. The most-read table |
| `SNV_INDEL/family_variant_summary`, `…/family_sample_variant_summary`, `…/family_data_version` | Summaries per family and per sample, and a version that changes with every change to a family's variant data (the ranking cache uses it, chapter 12) |
| `SNV_INDEL/carriers`, `…/carrier_generations` | The carrier summary of the Variant Explorer: per variant the carriers each family contributes, and what each family's contribution was computed from (below) |
| `SNV_INDEL/clinvar` | The ClinVar releases CoGA loaded itself (chapter 15): one partition per release, one row per allele with the key of the allele in minimal form and ClinVar's values as published. Only the current and the previous release are kept; Postgres says which is current |
| `SV/variants/details`, `SV/key_lookup`, `SV/entries`, `…/family_data_version` | The same for structural variants, with a version of their own that changes with every change to a family's SVs (the SV index for the second hit uses it, chapter 8) |
| `INTERVAL/entries` | Interval tracks: coverage, CNV segments, APCAD, PCF segments, haplotype blocks, the genome-wide haplotype lineage (`haplotype_lineage`) and the coverage per capture target of a NIPT panel (`target_coverage`), with the source and the file name on each row |

An `overwrite` import of an existing family also creates temporary backup tables `<assembly>/SNAPSHOT/<import>/<table>`. They hold a copy of that family's rows in `SNV_INDEL/entries`, `SV/entries`, `SV/variants/details`, `SV/key_lookup` and `INTERVAL/entries`, to restore the family on failure. The import removes them when it ends. For a stopped import, the worker that ends its job does so, and otherwise the next start (chapter 6).

For a reviewer, five things matter:

- The `entries` tables of small variants and SVs have a `sign` column (`CollapsingMergeTree`), but CoGA writes every row with `sign = 1`. Old rows disappear on a new import or a deletion with a synchronous `ALTER TABLE … DELETE` (`mutations_sync = 1`), never through a cancelling row (−1). The readers filter on `sign = 1`, without `FINAL`. That is only correct as long as no writer writes cancelling rows.
- The `entries` tables of small variants and SVs are partitioned per project (`PARTITION BY project_guid`). That makes the project filter cheap and keeps queries across projects bounded. `INTERVAL/entries` has no project column and is partitioned per track type.
- A family has its `entries` rows once per project it is linked to (without a project, under `unassigned`), and whoever reads the family reads only the rows of its projects. If an administrator changes its projects, the rows move with them, in two steps under the family's write locks: before the new links are fixed, each new project receives, per callset, a copy from a linked project (rows it already had are replaced); then the rows under the projects the family left disappear. A package import into an existing family also links it to the import's project, and before its snapshot gives each project of the family the callsets that project does not yet have (chapter 6). If a copy fails halfway, it erases what the project received of that callset. A project to which families are still linked is not deleted (`409`, with the count): the links would disappear and the rows would remain under the deleted project (chapter 15).
- A row in `entries` is the call of one callset (`source`) for one variant, in one family and one project. When ClickHouse merges the parts of a table, it keeps one row per sort key; that is why the sort key of `SNV_INDEL/entries` ends in `key, source`, and those of `SV/entries` and `SV/key_lookup` also in the callset (`docs/database.md`, *Row identity*). So the direct call (named after its caller, such as `haplotypecaller`) and the imputed call (GLIMPSE2) of one variant remain two rows; the diagnostic lists show the direct one. The calls of two callers (short-read and long-read WGS of the same DNA) also remain two rows. The callset `nipt` of a monogenic NIPT comes from two VCFs with one sample each, the plasma and the father. Each file fills in, in the same row per variant, only the call of its own sample. A family can hold several pregnancies of one woman, and each pregnancy keeps its data under sources of its own: the callset, the coverage tables and the ccnv output of a pregnancy carry after their name `_p` and eight hexadecimal characters of the SHA-256 of its `pregnancy_id` (`nipt_p1a2b3c4d`, `ccnv_p1a2b3c4d`). So the import of one pregnancy replaces nothing of another, not even the father's calls; the readers read such a source as the source it is kept for (`source_base`). For an SV the key itself also holds the source, because the ID of an SV from a per-sample upload does not name the caller: two callers on the same breakpoints remain two rows. The variant ID of a small variant does not depend on the callset. That of an SV usually does not either: only a HiFiCNV call without its own VCF ID and an mtDNA SV carry their source in the ID, and so get a review of their own. Reviews, classification snapshots, the ranking cache and the SV index for the second hit are tied to that ID.
- The Variant Explorer counts a variant's carriers from the carrier summary `SNV_INDEL/carriers`, with the project filter (chapter 14). Counting from each family's `entries` rows does not fit within a page's 110 s at three or four whole genomes. The summary is derived data: every row can be recomputed from `entries` and the members and projects of the families in Postgres. Every writer of a family's `entries` rows first takes the family's contribution out, under the family's write lock. The variant carrier worker, which runs where the package imports run, adds every 5 s the contribution of each family whose rows, members or projects differ from what the summary holds. Until that has happened, the explorer calls such a family *pending*. `carrier_generations` records what each contribution was computed from. The full rule is in `docs/database.md` (*The variant carrier summary*).

**Where in the code:** the ERDs of the ClickHouse tables and of their link with Postgres in `docs/database.md` (*ClickHouse Tables*); `ensure_clickhouse_variant_tables(assembly_name)` in `backend/app/services/clickhouse_variant_storage.py`, `ensure_clickhouse_interval_table(assembly_name)` in `backend/app/services/clickhouse_interval_tracks.py`, and `clickhouse_dataset_key` in `backend/app/core/clickhouse.py`. The keys are built in `backend/app/services/clickhouse_variant_ids.py`. The carrier summary is in `backend/app/services/variant_carrier_summary.py` (`detach_family_variant_carriers`, `reconcile_variant_carriers`).

## How a ClickHouse row is linked to Postgres

The two databases share no physical join; the application links them through shared keys. The ClickHouse column `project_guid` holds the Postgres project UUID (`projects.id`) as text, and `family_guid` the family UUID (`families.id`).

1. The backend determines from **Postgres** which family, samples and projects the user may see.
2. Those UUIDs go as parameters into the **ClickHouse** query (`family_guid`, `project_guid IN …`), so that only permitted rows come back.
3. The backend completes the variants with the review state from **Postgres**. The review tables are unique per family and variant ID: exactly one review belongs to one variant in one family.

So the access decision is always made first in Postgres; ClickHouse only sees the keys that have already been filtered. Anyone who wants to verify that data does not leak between projects checks two things: that the scope is determined correctly in Postgres (chapter 2), and that every ClickHouse query carries the project filter (chapters 8 and 14).

**Where in the code:** `backend/app/services/clickhouse_family_variants.py` (family variants) and `backend/app/services/variant_explorer_service.py` (variants across projects).

## How the schemas are applied

The Postgres schema reaches a database through one migration step (`backend/app/core/postgres_migrations.py`), by default at every start of the backend. If the backend runs as the restricted role `coga_app`, a separate job runs that step before the rollout. The backend then only checks at start-up that the database is at exactly its own revision and was made from its own baselines (chapter 4). The step runs in one transaction, as owner of the tables: it happens entirely or not at all.

1. It takes an *advisory lock* (a lock in the database), so that two instances that start together migrate one after the other.
2. It refuses to run as `coga_app`.
3. It refuses any database this version did not make, before anything happens. It migrates only an empty database (an empty migration ledger and no table in the schema `public`) or a database whose ledger `alembic_version` names one of its revisions and whose digest in `schema_baselines` equals the SHA-256 of its own five baselines. Tables without a ledger, a revision this version does not know (another version made or migrated the database) and a database from other baselines are each refused, with a message that says which case it is.
4. It runs the Alembic revisions in `backend/db/migrations/versions/` that the ledger does not yet list, and records the last one there. Revision `0001` applies the five baselines to the empty database and records their digest in `schema_baselines`.

**Before the release candidate.** CoGA holds only test data, and every database is created empty. A schema change is an edit of its baseline, never a migration of an existing database: revision `0001` is the only revision, and a database from the previous baselines is refused at its next start (step 3). Then create it again empty (`docker compose down -v`, `docs/development.md`) and import the test data again. The integration test `backend/tests/integration/test_postgres_migrations.py` shows that an empty database gets exactly the schema of the five files (the same catalogue fingerprint), that a later start changes nothing, and that every refused database stays as it was.

The proposal for QA is that the release candidate freezes the baselines (`docs/database.md`, *Freezing the baselines at the release candidate*). A test then records their SHA-256, and from then on every schema change is a revision of its own after `0001`, which only runs forward. Anyone who wants to go back to an older version restores the backup from before the upgrade; on Google Cloud the rollout takes that backup itself, before every migration it starts (`docs/deployment-gcp.md`, 12.1 and 12.3).

The backend creates the ClickHouse tables with `CREATE TABLE IF NOT EXISTS`, directly in their final form. Every database is created from scratch; a local database with tables from another version is created again (`docker compose down -v`).

The depth columns of `SNV_INDEL/entries`, the read depth (`calls.dp`) and the per-allele depths (`calls.ad`), are `UInt32`, up to 4,294,967,295 reads: a mitochondrial or amplicon call reads far more than the 65,535 of a `UInt16`. The genotype quality (`calls.gq`), a phred score, is `UInt16`.

Every value a loader writes is first bound to its column (`clickhouse_column_bounds.py`, applied in `clickhouse_variant_rows.py` and `clickhouse_interval_tracks.py`). This is because the ClickHouse client refuses a whole insert as soon as one value does not fit its column. A value its column cannot hold, or that is not a finite number, is stored as missing: a GQ above 65,535 or `inf`, a negative phase set, a QUAL, allele fraction, caller metric or annotation score beyond the largest `Float32`, an SVLEN outside `Int32`. An allele fraction that is missing in this way drops out of the call's list. An allele depth that is missing in this way becomes 0, like a missing or unreadable one, so that the other alleles keep their place. A record whose position no column can hold (a negative POS, an END beyond 4,294,967,295, a negative start of a track row) cannot be placed. Its loader skips it as malformed, just like a POS that is not a number, and counts it where it counts those. The full rule per column is in `docs/database.md`.

Who applies the schema (the app itself as owner, or a separate migration step) and in what order the backend starts up is described in [chapter 4](04-deployment-and-seeding.md).

**Where in the code:** `init_postgres_schema` in `backend/app/core/postgres.py`, `init_clickhouse_schema` in `backend/app/core/clickhouse.py` and `ensure_clickhouse_variant_tables` in `backend/app/services/clickhouse_variant_storage.py`.

## Guarantees in the database layer

Many IVDR guarantees sit in the database itself, so that not even a fault in the application or a misused API request can bypass them.

- **Append-only through triggers.** `audit_log_events`, `clinical_audit_events`, `report_signouts`, `integrity_anchors`, `qc_threshold_changes`, `family_structure_versions`, `import_delivery_revisions` and `ui_events` each have a trigger that blocks `DELETE` and refuses `UPDATE`. The tables of the exploratory ancestry PCA (`ancestry_reference_panels`, `ancestry_results`, `ancestry_sample_choices`) have one too. The only exception is setting a reference to a user or family to `NULL` when that user or family is deleted; denormalised columns (e.g. `user_email`, `family_identifier`) then keep who it was. The triggers are in `04_traceability.sql`.
- **Hash chain.** `clinical_audit_events`, `report_signouts` and `family_structure_versions` carry `row_hash` and `prev_hash`: each row binds its content to the previous row of the same family. Changing, erasing or reordering rows thus becomes visible. `report_signouts` also carries a hash of the frozen report. The HTTP access log `audit_log_events` and the log of UI events `ui_events` are append-only through their trigger, but not hash-chained.
- **Integrity anchors.** Each time an administrator or an external scheduler calls `POST /api/admin/integrity/anchor`, `integrity_anchors` records the heads of all chains, signed with an Ed25519 key kept outside the database. Outside development an unsigned anchor is refused. CoGA does not schedule this itself: how often it happens is a procedural agreement (chapter 11).
- **Separation of privileges.** `05_grants.sql` creates the role `coga_app` without login and revokes `UPDATE`, `DELETE` and `TRUNCATE` on the append-only tables. The migration step enables that login with `POSTGRES_APP_PASSWORD` (on Google Cloud with `db_runtime_role = "coga_app"`). By default the app still runs as owner (chapter 2).
- **Access rules in the schema.** The `CHECK` on `users.role` allows only the five roles, from low to high `viewer`, `analyst`, `supervisor`, `superuser` and `admin` (chapter 2). Foreign keys with `ON DELETE CASCADE` clean up the associated rows when a family or sample is deleted.
- **Provenance.** Three tables record which files and versions produced a result: `raw_import_files` (the SHA-256 of each source file), `family_annotation_manifest` (the tool and database versions per analysis of a family) and `reference_dataset_imports` (each import of reference data). That is the basis for the traceability in [chapter 11](11-report-and-traceability.md).

## Key files

| File | Role |
| --- | --- |
| `docs/database.md` | Reference of all tables and columns (source of truth), with the ERDs of both databases |
| `backend/db/schema/postgres/01_access.sql` … `05_grants.sql` | The five Postgres baselines |
| `backend/db/schema/clickhouse/001_coga_variant_storage.sql` | Creates the ClickHouse database |
| `backend/app/core/postgres.py` | Postgres connection and applying the schema |
| `backend/app/core/postgres_migrations.py` | The migration step: lock, refusals, the baseline digest and the Alembic revisions; the check of revision and digest at a start without migration |
| `backend/db/migrations/versions/` | The Alembic revisions of the Postgres schema |
| `backend/app/core/clickhouse.py` | ClickHouse client and the safe assembly key |
| `backend/app/services/clickhouse_variant_storage.py` | Creates the variant tables per assembly |
| `backend/app/services/clickhouse_interval_tracks.py` | Creates the interval table per assembly |
| `backend/app/services/variant_carrier_summary.py` | The carrier summary of the Variant Explorer and the worker that keeps it up to date |
| `backend/app/services/variant_explorer_service.py` | The link between Postgres UUIDs and ClickHouse keys, across projects |
