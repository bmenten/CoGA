# Database Schema

CoGA keeps its metadata in Postgres and the high-volume variant and track rows in
ClickHouse. The API joins the two at request time. This page is the table-by-table
reference.

## Postgres Tables

The schema is five idempotent baseline files in
[backend/db/schema/postgres/](../backend/db/schema/postgres/), applied in name order on every
start (see "Startup Behavior"). There is no migration ledger and no upgrade statement: each
table is created in its final form, and a database from an older schema is reset, not
migrated ([development.md](development.md#stop-and-reset)).

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
| `clinical_cnv_kb_jobs` | Progress of admin-triggered rebuilds of the clinical CNV knowledgebase, per assembly (`queued`, `running`, `completed` or `failed`), with the number of rows inserted, the error (for a failed build, the reason it gave last) and the tail of the build log. At most one job is queued or running at a time (`idx_clinical_cnv_kb_jobs_one_active`, a unique index on a constant). `worker_id` and `heartbeat_at` mark the worker running a job; a job whose heartbeat is ten minutes old is closed as failed. |
| `dgv_variants` | Database of Genomic Variants entries per assembly, with a gain/loss/mixed class |
| `segmental_duplications` | segmental duplications and low-copy repeats per assembly |
| `gene_panels`, `gene_panel_genes` | panels and their gene lists |
| `gene_panel_regions` | a panel's coordinates, one row per gene (or PanelApp region) per assembly (below) |
| `gene_panel_versions` | an immutable snapshot of every version of a panel: genes, regions, source, external version and author. `GET /api/panels/{id}/versions/{version}` returns one; no screen shows it, it is there to look up the panel version a report names |
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
| `family_relationships` | explicit links between two samples: `parent_child` (with the role at each end), `couple`, or `relative` (`sample_id_b` is related through `sample_id_a` by an unknown degree, such as a PGT index known only to be on the mother's side); `source` says what made it (`pedigree`: the parents a pedigree gives; `manifest`: a package manifest's `family.relationships`; `manual`: a couple typed in, or an edit), `active = false` retires it |
| `family_structure_versions` | one row per pedigree or phenotype change: the version, a `structure_hash` over roles, parentage and affected status, and the full snapshot |
| `family_import_jobs` | package-import jobs: status, logs, validation issues and a summary per dataset, with its progress (when it started and ended; while it runs, the share of its files read and the time left). `family_id` is the family a job imports, committed with status `running` before the import writes anything of it (an import that cannot record it writes nothing); the report sign-out refuses a family while a job of it is queued, validating or running. A running job writes `heartbeat_at` every minute; a worker claims a job whose heartbeat is ten minutes old, runs it again if it was still `validating`, and ends it `failed` (interrupted) if it was `running`, adding a line to its log either way |
| `individual_hpo` | per-person HPO terms, each `present`, `absent` or `unknown` |
| `repeat_expansions` | TRGT repeat calls per sample |
| `sample_paraphase_results` | Paraphase copy-number and haplotype results per sample |
| `nipt_artifact_variants` | the recurrent-artifact list for monogenic NIPT, per assembly and assay: entries added one by one, imported from the NIPT-M pipeline's recurrent table (as `curated`) or seeded from the cohort (`auto`) (see [monogenic-nipt.md](monogenic-nipt.md) and [data-import.md](data-import.md#the-nipt-artifact-list)) |
| `sample_interval_track_sources` | one row per sample, track type (`coverage`, `segments`, `apcad`, `apcad_pcf`, `haplotype`, `target_coverage`), source and file, with its row count; the rows themselves are in ClickHouse |
| `small_variant_reviews` | the classification, ACMG criteria, tags, notes and evidence snapshot of a small variant in a family |
| `structural_variant_reviews` | the same for a structural variant or CNV, with the CNV ACMG points and the evidence snapshot of the CNV classification |
| `small_variant_filter_presets`, `structural_variant_filter_presets` | saved filter sets, per user: a small-variant one is reusable in every family, a structural-variant one is for one family or all |
| `small_variant_tag_definitions`, `small_variant_tag_definition_project_links` | the review-tag catalogue, global or per project. A tag's `key` is what reviews, saved filter presets and audit events hold: it is set once, from the label at creation (numbered, as `<slug>_2`, when another tag, renamed or deleted, already holds that slug), and an edit changes the label, never the key. A delete sets `is_active = false` and keeps the links, so the reviews that hold the tag still show it; no review save adds an inactive tag |
| `family_sv_gene_index`, `family_sv_gene_index_status` | per family, which genes a structural variant hits (for the "also hit by an SV" flag), and when and from which SV data version (`sv_data_version`, below) that index was built. It is rebuilt on next use once the family's SVs have changed |
| `family_variant_ranking_cache` | cached prioritised rankings (see [variant-ranking-cache.md](variant-ranking-cache.md)) |
| `qc_threshold_profiles` | named sets of sequencing-QC cut-offs, one per assay type (below) |
| `qc_thresholds` | per profile and metric, a warning and an error bound (either may be null) |
| `qc_threshold_changes` | **append-only** history of every cut-off edit (below) |

`sample_interval_track_sources.track_type` is held to the track types above by a `CHECK`
(`sample_interval_track_sources_track_type_check`). The baseline creates the table only when it
is missing, so a database created before `target_coverage` was in the list keeps the old check,
and the import of a per-target coverage table fails on it. Reset such a database
([development.md](development.md#stop-and-reset)).

JSON keys that package import writes into the `metadata` columns:

| Column | Key | Contents |
| --- | --- | --- |
| `samples.metadata` | `sequencing_qc` | read metrics (NanoPlot), depth (mosdepth, or the PGT pipeline's mean coverage), alignment metrics (`alignment`, from Qualimap), the sex ngs-bits read (`sex_check`), an embryo's PGT QC (`pgt`: allele drop-out and drop-in, Mendelian concordance before and after imputation, as percentages) and the path of the QC report |
| `samples.metadata` | `alignment` | the CRAM/BAM path and index, package-relative (`path`, `index_path`), and for a package in a bucket the objects' URIs (`uri`, `index_uri`), which IGV reads |
| `samples.metadata` | `signal_tracks` | the package-relative paths of the HiFiCNV depth, MAF and copy-number files, served to IGV, and for a package in a bucket the objects' URIs under `uris` |
| `samples.metadata` | `mtdna` | the mtDNA haplogroup from the mutserve annotation |
| `samples.metadata` | `sv_files` | the file name per structural-variant source |
| `samples.metadata` | `assay`, `assay_panel` | copied from the manifest's `samples` entry (kept whole under `package_sample_metadata`): `assay: nipt_cfdna` marks the maternal-plasma cfDNA sample of a monogenic NIPT family, and `assay_panel` names its capture panel, which scopes the NIPT artifact list |
| `families.metadata` | `pipeline` | the Nextflow run parameters (reference build, callers, annotation caches; for the PGT pipeline the affected parent, ROI and QDNAseq bin size, and the callset and phasing panel it started from) |
| `families.metadata` | `pipeline_qc` | the QC the PGT pipeline reported for the family as a whole: the KING kinship, IBS0 and SNP count of every pair (`kinship`), and the files they came from |
| `families.metadata` | `haplotype_phase_corrections` | the parents' phase switches the haplotype blocks undid, one per switch: the parent and its side, the chromosome, the position from which its two haplotypes are swapped and the end of the children's switches, and how many of how many informative children switched. Replaced at every haplotype import; the lineage colouring and the phased markers read the parents' phase through them |
| `families.metadata` | `pipeline_haplotype_origin` | the PGT pipeline's reading of which haplotype of the affected parent is the affected one: the parent, the sites each haplotype shares with the index's affected and normal haplotypes, and the pipeline's conclusion. Evidence only; CoGA's risk-haplotype inference does not read it |
| `families.metadata` | `package_import` | the import provenance: folder (a bucket folder's URI), manifest, datasets and the manifest's own `metadata` |
| `families.metadata` | `pgt`, `analysis_type` | the manifest's PGT context (the inheritance model, the obligate and proven carriers, the affected parents and the indexes) and analysis type |
| `families.metadata` | `unresolved_roi` | the manifest's `roi` when the import could not resolve it in the family's assembly (`query`, and `source: manifest`); the family's region of interest is then left as it was |
| `families.metadata` | `import_incomplete` | set by an import that failed for some datasets and kept the others: the datasets that failed and those that imported, when, and the import job; for each failed dataset the job that holds its error (`failed_jobs`, as a later failure keeps an earlier one's) and its scope (`scopes`). A completed import removes each failed dataset it imported again for that scope, and the flag with the last |
| `families.metadata` | `import_unfinished` | the imports that have begun writing the family and not finished, by job: when each began, its datasets and those it finished. An import writes its entry before its first write (and again once it holds the family's variant-write locks) and removes it when it ends, so an entry that stays names one whose process stopped part-way. It records each dataset's scope (`scopes`: the small variants' `source_format`, a per-sample dataset's samples), and only an `overwrite` that imports again what it had not finished, for that scope, removes it |

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
| `audit_log_events` | **append-only** log of every HTTP request: user, route, status, duration, client address, and the request body with sensitive fields masked; a value Postgres cannot store is escaped (below) |
| `ui_events` | UI interactions that never reach the backend (clicks, in-app navigation), masked before storage, escaped like `audit_log_events` |
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

**Values Postgres cannot store.** A request can carry what Postgres refuses: a NUL character
(a `%00` in the path or query string reaches the app decoded; `\u0000` in a JSON body), half
of a UTF-16 surrogate pair (`\ud800` in a JSON body), and the JSON numbers `NaN` and
`Infinity`. Such a value used to fail the INSERT, so the request's row, and in the async mode
every row of its batch, never reached the table. The writers of `audit_log_events` and
`ui_events` now store it as an escape (`core/pg_storable.py`): a NUL as the four characters
`\x00`, half a pair as `\udXXX`, and a number as the string `"NaN"`, `"Infinity"` or
`"-Infinity"`; a JSON key is treated like a value. `_escaped` lists the columns this happened
in, sorted: in `request_meta` for `audit_log_events`, in `detail` for `ui_events`, where a
client's own `_escaped` is dropped. A row without it is stored as the request sent it. The
escape is not unique, since a request can send the characters `\x00` itself: `_escaped` says
that a column holds an escape, not which one.

**Records outside the user's projects.** A request for a family, sample or project that
exists outside the user's projects is answered exactly like a request for an unknown ID (404).
Its `audit_log_events` row keeps the difference: `request_meta.record_hidden` names the kind
(`family`, `sample` or `project`). The middleware sets it; a request cannot.

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
| `INTERVAL/entries` | the interval-track rows: coverage, segments, APCAD, PCF segments, haplotype blocks, a capture panel's per-target coverage (`target_coverage`, below), and the genome-wide haplotype lineage the genome overview reads (`haplotype_lineage`, precomputed from the haplotype blocks; see [haplotype-segregation-analysis.md](haplotype-segregation-analysis.md)) |

An `overwrite` package import of an existing family also makes **backup tables**, a copy of
the family's rows in `SNV_INDEL/entries`, `SV/entries`, `SV/variants/details`, `SV/key_lookup`
and `INTERVAL/entries`, named after the import: `<assembly>/SNAPSHOT/<import job id>/<table>`
(`run-<hex>` for an import run outside a job). They are never served, and the startup storage
check ignores them. The import drops them when it ends; for one whose process stopped, the
worker that ends its job drops them, and every start drops a backup whose job has ended (or
one made outside a job, a day after it was made). See
[clickhouse_family_snapshot.py](../backend/app/services/clickhouse_family_snapshot.py).

Column-level detail lives with the DDL in `clickhouse_variant_storage.py`
(`ensure_clickhouse_variant_tables`) and `clickhouse_interval_tracks.py`. Columns worth calling
out:

| Table | Column | Why it exists |
| --- | --- | --- |
| `…/SNV_INDEL/entries` | `calls.ps` | Phase set, for read-based cis/trans against a phased SV |
| `…/SNV_INDEL/entries` | `calls.af` | Per-allele fraction; on chrM this *is* the heteroplasmy level the mtDNA workspace reads |
| `…/SNV_INDEL/entries` | `calls.filters` | The call's own FILTER values (`PASS` kept as is). Filled only when the VCF holds one sample, as each file of a monogenic NIPT pair, and of a primary callset read one file per sample, does: its record's FILTER then describes that one call (a DeepVariant `RefCall` kept as a partner's call says the caller read that partner as reference there). A call from a multi-sample VCF has none, since its record's FILTER describes the site. |
| `…/SNV_INDEL/entries` | `calls.metrics` | The caller's metrics of the call, a map from the INFO key to a number, filled the same way ([vcf_call_metrics.py](../backend/app/services/vcf_call_metrics.py)): Mutect2's `TLOD`, `FS`, `SOR`, `MQ`, `ECNT` and others, and VarDict's `SBF`, `NM`, `MSI` and others; for a key with one value per allele (`MMQ`, `MBQ`, `MFRL`, `RPA`) the first ALT's value under the key and the reference's under `<key>_REF`; `QUAL` when the record has one; `STR` = 1 for Mutect2's short-tandem-repeat flag, and `RU_LEN`, the length of the repeat unit. A Mutect2 tumour-only call has no QUAL: its quality is `TLOD`. The NIPT quality filter, the father's genotype class and the de novo triage read them. |
| `…/SV/entries` | `calls.ps` | Phase set for a structural call |
| `…/SV/entries` | `calls.cn` | Copy number from a depth-based CNV caller (HiFiCNV `FORMAT/CN`). `GT=1/1` on a duplication cannot distinguish CN=3 from CN=6, and the ClinGen CNV dosage scoring needs the actual number. Null for callers that report none. |
| `…/INTERVAL/entries` | `value`, `record_id`, `metadata_json` | On a `target_coverage` row (source `coverage_table`), one capture target of a per-target coverage table, at the table's BED coordinates: its mean depth, its gene (the first field of the table's `attribute`), and the table's `attribute`, `median`, `min`, `max`, `proportion_covered` (the percentage of its bases with any coverage) and `zero_coverage_bases`. The NIPT coverage check, the plasma's sex profile and the plasma's depth where it has no call read them ([nipt_target_coverage.py](../backend/app/services/nipt_target_coverage.py)); the track viewers do not draw this track. |

The `source` column on both `entries` tables scopes deletes and re-imports, so one family can
hold several callsets side by side. Small variants: `clair3` (the primary callset, whatever
caller made it: one joint VCF, or the long-read pipeline's one VCF per sample read side by side,
a site being one row holding the call of each sample whose file has a record there; see
[data-import.md](data-import.md#per-sample-callsets-of-a-long-read-couple)), `glimpse2` (imputed;
hidden from the diagnostic lists by default), `mito`
(chrM) and `nipt` (a monogenic NIPT pair: the plasma's and the father's single-sample VCFs, each
merged into the callset for its own sample, so a variant is one row holding each sample's call
where its file has one). Structural variants: `needlr`, `hificnv` and `mito_sv` (chrM deletions and duplications, each
sample's heteroplasmy in the record's annotations) from packages, and
`manual_upload`, `sniffles` or `spectre` from a direct upload.

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

### One write at a time per family

ClickHouse has no transactions, and a write of a family's variants replaces rows it has read: a
per-sample upload merges its file's calls into its source's stored rows and writes them back,
the admin sample delete writes the family's rows back without the sample, a package dataset
replaces its source, and a failed package import restores the rows it snapshotted. Each writer
therefore holds a transaction-scoped Postgres advisory lock
(`pg_advisory_xact_lock(hashtext('family-variant-writes:<type>:<family uuid>'))`, `<type>`
being `small_variants` or `structural_variants`) from before its first read of the rows until
its transaction ends
([family_variant_write_lock.py](../backend/app/services/family_variant_write_lock.py)). A second
write of the same family and type waits for the first to commit or roll back, in any worker or
process. Readers take no lock. A write that needs both takes the small-variant lock first.

| Writer | Holds |
| --- | --- |
| per-sample SV upload (`POST /structural-variants/upload/{sample_id}`) | SVs |
| family small-variant upload (`POST /families/{family_id}/small-variants/upload`) | small variants |
| admin SV delete of a sample (`DELETE /admin/data/samples/{sample_id}/structural_variants`) | SVs |
| admin small-variant delete (`DELETE /admin/data/families/{family_id}/small_variants`) | small variants |
| admin sample and family deletes (`DELETE /admin/samples/{sample_id}`, `DELETE /admin/families/{family_id}`) | both |
| PED overwrite of an existing family; a structure change that clears the family's data | both |
| package import, from before its snapshot until it has finished or restored the family | both |

The package import commits after its datasets, so it holds its locks on a Postgres connection
of its own; the dataset loaders it runs take none of their own for the family. That connection
stays idle in its transaction for the whole import, so the server must not end idle
transactions (`idle_in_transaction_session_timeout`, off by default): ending it would release
the locks while the import runs.

An upload or a package import checks, once it holds the lock, that its family and samples are
still stored: when the write before it deleted them, it writes nothing and answers 404.

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
- The variant id does not depend on the callset, with two exceptions whose id names their
  source: a HiFiCNV call without an id of its own, and every mtDNA SV
  (`build_structural_variant_id(..., source=...)` in `family_package_variants.py`). Reviews,
  classification evidence snapshots, the ranking cache and the SV→gene index attach by the id,
  so they cover every callset's row of a variant.

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

An `SNV_INDEL/entries` table without the per-call columns `calls.filters` and `calls.metrics`
(created before they existed) is refused the same way, at the same two points: the message
("Refusing to use ClickHouse variant tables created by an earlier version") names the table and
the columns it lacks. The backend adds no column to a stored table (there is no `ALTER`), and a
re-import is what fills the columns, so the recovery is the same.

To recover, for each assembly the message names:

1. Stop the backend.
2. Drop the assembly's small-variant and SV tables. List them with
   `SELECT name FROM system.tables WHERE database = 'coga' AND match(name, '^GRCh38/(SNV_INDEL|SV)/')`
   (with your database and assembly), then drop each one with ``DROP TABLE coga.`<name>` SYNC``.
   The interval tracks (`INTERVAL/entries`) stay.
3. Start the backend. It creates the tables with the sort keys above and every column.
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
to go on when an assembly's tables have a different row identity, or its `SNV_INDEL/entries`
table lacks the per-call columns ([Row identity](#row-identity)); each assembly's tables are
created on first use. The full startup order is in
[application-scheme.md](application-scheme.md), "Startup and background work"; the reference
data it loads is listed in [data-import.md](data-import.md), section 1.
