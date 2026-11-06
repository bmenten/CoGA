# Database Schema

CoGA keeps its metadata in Postgres and the high-volume variant and track rows in
ClickHouse. The API joins the two at request time. This page is the table-by-table
reference.

## Postgres Tables

The schema is five idempotent baseline files in
[backend/db/schema/postgres/](../backend/db/schema/postgres/), which create each table in its
final form, and the Alembic revisions in
[backend/db/migrations/versions/](../backend/db/migrations/versions/), which bring a table that
already exists to that form. One migration step applies both, as the table owner, on every
start in the default mode (see "Startup Behavior"); the ledger table `alembic_version` records
the revision a database is at ([Schema Migrations](#schema-migrations)).

Some baselines also add columns to an older database. A revision runs the same statements, for
when the baselines are no longer re-applied:

- `01_access.sql` adds `users.azure_object_id` and `project_users.access_level` with
  `ALTER TABLE … ADD COLUMN IF NOT EXISTS`. A database made before Microsoft sign-in keeps its
  accounts (SEC-4); one made before the reader level keeps its memberships, each as an
  analyst's (SEC-1). Revisions `0006` and `0005`.
- `03_assay.sql` gives a database made before SEC-32 `family_import_jobs.requested_by_id` and
  the presets' `owner_id`, each in a block that runs only while the column is missing. Revision
  `0008`.
- `02_reference.sql` does the same for `gene_info_refresh_jobs.requested_by_id` and
  `clinical_cnv_kb_jobs.requested_by_id` (SEC-35). Revision `0010`.
- `02_reference.sql` adds `gene_panels.external_selection` with `ALTER TABLE … ADD COLUMN IF
  NOT EXISTS`, before the unique index that reads it. Revision `0020`.

### 01_access.sql: genome foundation and access

| Table | Holds |
| --- | --- |
| `species` | organisms: name, common name, NCBI taxonomy id |
| `assemblies` | genome assemblies per species |
| `chromosomes` | chromosome sizes and cytobands per assembly |
| `users` | accounts: username, password hash, role and active flag. The role is one of five, lowest first, each with every right of the ones before it (`services/access_control.py`): `viewer` (reads the records of their projects), `analyst` (also analyses them), `supervisor` (also signs an analysis out), `superuser` (also every project, and the gene panels, variant tags, filter presets, sequencing QC thresholds and package import) and `admin` (the whole administration). An account made at its first Microsoft sign-in, or at its first sign-in through the organisation's CAS server (which releases that ID; the account starts inactive, waiting for approval), holds the user's Microsoft Entra ID object ID (`azure_object_id`, unique), by which every later sign-in finds it, and no password (`hashed_password` holds a marker no password matches). A local account, such as the emergency administrator, has no object ID (SEC-4). `activated_at` is when the account was first active: its creation's time for an account made active, NULL for a sign-up or a first CAS sign-in until an administrator activates it; only an account with no `activated_at` can be deleted (`DELETE /api/auth/users/{id}`), any other is archived. `archived_at` is when an administrator archived it; `users_archived_inactive_check` holds an archived account inactive. Archiving keeps the row, so every record that names the account still does. `sessions_ended_at` is when the account last signed out (`POST /api/auth/logout`): every token issued to it before then, CoGA's own and Microsoft's, is refused, on every device; NULL until its first logout |
| `projects` | projects, each bound to one species and assembly |
| `project_users` | which users belong to which project, and each member's `access_level`: `analyst` (reads and changes the project's records, as far as their role lets them; the default) or `reader` (only reads them; SEC-1). The level only takes rights away: a member whose role is `viewer` changes nothing. Data access is scoped by project; a `superuser` or an `admin` has every right in every project |
| `auth_login_attempts` | failed-login counts and back-off per email and per client address, and sign-up attempts per address |

### 02_reference.sql: reference and annotation data

| Table | Holds |
| --- | --- |
| `genes` | gene loci per assembly, one row per transcript (GENCODE for GRCh38) |
| `gene_info` | the cached gene reference per human gene: identifiers, disease links, dosage, constraint, the gene's entry on the ACMG secondary-findings list (`extra.acmg_secondary_finding`), and a status and release per source |
| `gene_info_refresh_jobs` | queued and running gene-reference syncs. `requested_by_id` is the account that asked for the job (SEC-35): a worker runs the job only while that account is an active administrator; otherwise it ends the job `failed`, with the reason, before writing any gene record. `requested_by` is the e-mail address it was asked under, shown with the job, or `startup-bootstrap` for the sync a start queues (no account's; it runs). Deleting the account keeps the job, linked to no account; it can then no longer run. `worker_id` and `heartbeat_at` mark the worker running a job: it writes the heartbeat while it loads the sources and with its progress. Another worker takes up a running job whose heartbeat is five minutes old; the first then stops, and ends a job failed only while it is still its own (SAFE-36) |
| `blacklist` | problem regions per assembly |
| `clinical_cnvs` | known CNV syndromes per assembly, with their ClinVar support (below) |
| `clinical_cnv_kb_jobs` | admin-triggered rebuilds of the clinical CNV knowledgebase, per assembly: status (`queued`, `running`, `completed` or `failed`), rows inserted, the error (for a failed build, the last reason it gave) and the tail of the build log. At most one job is queued or running (`idx_clinical_cnv_kb_jobs_one_active`, a unique index on a constant). `worker_id` and `heartbeat_at` mark the worker running a job; a job whose heartbeat is ten minutes old is closed as failed. `requested_by_id` is the account that asked for the rebuild (SEC-35). The rebuild runs, and loads what it built, only while that account is an active administrator, checked at the start and before the load; otherwise it ends `failed`, with the reason, and loads nothing. `requested_by` is the e-mail address it was asked under, shown with the job. Deleting the account keeps the job, linked to no account; it can then no longer run. |
| `dgv_variants` | Database of Genomic Variants entries per assembly, with a gain/loss/mixed class |
| `dgv_load_jobs` | loads of the pinned DGV release for an assembly (`DGV_PINNED_RELEASES`), asked for on the Reference page or queued by a start or an assembly import: release, source URL, status (`queued`, `running`, `completed` or `failed`), the download's progress (`bytes_read` of `bytes_total`), rows inserted and the error. As for `clinical_cnv_kb_jobs`: at most one job queued or running (`idx_dgv_load_jobs_one_active`), `worker_id` and `heartbeat_at` mark its worker and a job silent for ten minutes is closed as failed, and an administrator's load (`requested_by_id`, SEC-35) runs and commits only while that account is an active administrator. A load a start queues is `startup-bootstrap`'s, no account's. A load commits nothing unless the whole file is the pinned one ([data-import.md](data-import.md#dgv)) |
| `segmental_duplications` | segmental duplications and low-copy repeats per assembly |
| `gene_panels`, `gene_panel_genes` | panels and their gene lists. An external panel's `source` and `external_id` (the PanelApp panel's id; `mendeliome` for the Mendeliome) say where it comes from, and `external_selection` which part of it the panel holds: for a PanelApp panel, the confidence levels it was imported with, lowest first (`3` green only, `2,3` green and amber, `1,2,3` green, amber and red). Each choice of levels of one PanelApp panel is a panel of its own, with its own name, versions and genes: `idx_gene_panels_source_external_selection` holds one panel per source, external id and selection (NULL equal to NULL, so there is one Mendeliome) |
| `gene_panel_regions` | a panel's coordinates, one row per gene (or PanelApp region) per assembly (below) |
| `gene_panel_versions` | a snapshot of every version of a panel: genes, regions, source, external version and author. Version 1 is archived when a panel is created by hand, built (the Mendeliome) or first imported from PanelApp. The next version is archived at each hand edit of a local panel (its genes or description), each Mendeliome rebuild that changes its genes (or is forced) and each PanelApp import that changes the panel's genes or regions. The app never rewrites a snapshot, and `UNIQUE (panel_id, version)` refuses a second one under the same number. But no trigger makes the table append-only, and deleting a panel deletes its versions (`ON DELETE CASCADE`). The panel page lists the versions; no screen shows one version's genes. `GET /api/panels/{id}/versions/{version}` returns them, to look up the panel version a report names |
| `hpo_term`, `hpo_synonym`, `hpo_edge`, `hpo_closure` | one HPO release: terms, synonyms, `is_a` links, and every ancestor of a term with its distance |
| `monarch_gene_disease`, `monarch_disease_phenotype` | Monarch gene-to-disease links (predicate, sources, causal or not) and disease-to-HPO annotations, including negated ones |
| `repeat_loci` | the TRGT repeat catalogue (STRchive thresholds, genes, diseases, motifs): the built-in loci and the shipped STRchive release's, rewritten at every start, which removes STRchive loci the release does not list ([data-import.md](data-import.md#the-strchive-release)) |
| `repeat_locus_count_units` | QA's record, per `repeat_loci` row: the count correction added to TRGT's count before it is classified (`count_offset`, repeat units, 0 for none), whether TRGT's count, so corrected, is the clinical repeat number its ranges count (`validated`), with the `reason`, who (`updated_by`, `updated_by_name`) and when (below) |
| `reference_dataset_imports` | every reference import and upload: dataset, rows inserted, whether it replaced data, source, the release the source states (below), who and when |

`clinical_cnvs` carries the knowledgebase's ClinVar support per region:
`clinvar_pathogenic_loss_count` and `clinvar_pathogenic_gain_count` (pathogenic ClinVar CNVs
that overlap the region by at least 30 % reciprocally, per side) and
`clinvar_pathogenic_accessions` (their VariationIDs). NULL means the knowledgebase recorded
no ClinVar support (built without ClinVar, or loaded from a BED-style file), not zero.

`gene_panel_regions` is keyed on `(panel_id, assembly_id, gene, chr, start, end)`. A family's
panel filter reads only the rows of its own assembly, plus the loci of the panel's genes
resolved in that assembly at query time. With no resolved assembly it narrows by gene symbol
alone. PanelApp's own coordinates are stored for the assembly they were requested for. Each
backend process caches a panel's resolution per panel and assembly while the panel's version,
its gene and region rows and the assembly's gene-reference imports are unchanged
([variant-ranking-cache.md](variant-ranking-cache.md#resolving-a-panel)).

`repeat_locus_count_units` holds the laboratory's own check, not catalogue data. TRGT counts
the motif across the region its own catalogue gives a locus, which need not be the stretch the
clinical repeat number counts (at FMR1 it leaves out the AGG interruptions; see
[data-import.md](data-import.md), section 1). A locus without a row has no correction and is
not validated. No row is written in advance: a correction exists only once QA enters it.

- `count_offset` is added to TRGT's count before every classification of a call at the locus:
  at import, on the repeat page, in the genome tracks and in the signed record
  (`services/repeat_count_units.py`, `corrected_repeat_count`). A count it would take below 0
  reads 0. It applies whether or not the locus is validated. TRGT's own count is shown beside
  the corrected one. A `CHECK` holds it from -20 to 20 (`repeat_locus_count_units_count_offset_check`).
- Until a locus is validated, the repeat page and the genome tracks mark a corrected count
  within 3 repeats of a boundary its classification uses. The mark changes no status.
- The catalogue seed upserts `repeat_loci` at every start but never writes this table, so a
  start keeps QA's record. `locus_id` references `repeat_loci` (a row for a locus the catalogue
  lacks is refused); a `CHECK` refuses a blank `reason`.
- An admin sets it on the *Repeat catalogue* page (`PUT /api/admin/repeat-loci/{locus_id}/count-units`,
  with the correction, whether validated, and a required reason). Each change is a
  hash-chained `clinical_audit_events` row on a chain of its own,
  `family_identifier = system:repeat-loci`, with the record before and after.
- The family table and the genome track read the row in the statement that reads the calls. A
  sign-out freezes it into the signed record beside the counts, statuses and warnings it led to
  (`repeat_expansions`, [clinical-traceability.md](clinical-traceability.md)).
- FMR1 has no constant correction: TRGT leaves out 1 to 3 AGG interruptions per allele. Its
  correction stays 0 and the locus not validated until CoGA reads the interruptions TRGT
  reports beside the CGG copies.

`reference_dataset_imports.source_version` and `source_release_date` hold the release the
source states about itself:

- A GENCODE gene import takes both from its GTF header.
- A clinical-CNV knowledgebase rebuild, built from the sources as they are on the day, records
  in `source_version` the files it read: first the one pinned file, ClinGen's recurrent CNV
  file, by release, file name and SHA-256 (`ClinGen recurrent CNV V2.1: ClinGen_recurrent_CNV_V2.1-hg38.bed,
  SHA-256 …`), then each file read as published that day by file name and SHA-256 (`read as
  published: UCSC cytobands: cytoBand.txt.gz, SHA-256 …`; TRACE-18;
  [data-import.md](data-import.md)).
- A DGV load records the pinned release, the file and its SHA-256 (`2025-12-01
  (GRCh38_hg38_variants_2025-12-01.txt, SHA-256 …)`), and the release's date.
- An assembly's cytobands from UCSC record the table read as published that day by file name
  and SHA-256 (`read as published: UCSC cytobands: cytoBandIdeo.txt.gz, SHA-256 …`), or, when
  UCSC could not serve it, the shipped backup by name, the day it was taken and SHA-256
  (`source` `ucsc (coga backup)`; [data/ref-data/cytobands/README.md](../data/ref-data/cytobands/README.md)).
- No other source states one: UCSC gene tables, one band per chromosome built from UCSC's
  chromosome sizes (T2T), a GTF without that header, uploads, the built-in
  reference files and the DGV script. Their `source_version` is `not stated` (as is a
  knowledgebase rebuild's from before the pin), and `source_release_date` is NULL.

`source` is the label the readers show, such as `gencode v50 (Ensembl 116)`; a built-in
reference file is recorded under its file name.

### 03_assay.sql: families, samples, assay data and review

| Table | Holds |
| --- | --- |
| `family_statuses` | the admin-managed workflow statuses; `families.status_id` points here. Seeded: `solved`, `strong_candidate`, `reviewed_no_candidate`, `closed`, `analysis_in_progress`, `data_ready`, `waiting_for_data`, `loading_failed`, `no_data_expected` |
| `families` | a family: its `family_id`, PED text, region of interest, status, assignee and `metadata` (below). The PED text (`pedigree`) is the one the family's last import or Family Builder save wrote. The family response instead serves a PED text written from the stored members and parent-child relationships (`pedigree_text.py`, as the PED download does), in the stored text's member order; the pages draw that one. The stored text could disagree with the structure (SAFE-8: a package import of an existing family replaces the text but not the relationships or sexes, and a Family Builder request with a parent ID of only spaces wrote a row of five fields) |
| `samples` | a sample: its `sample_id`, family, sex and `metadata` (below) |
| `family_members` | a sample's place in its family: role, clinical and carrier status, active flag |
| `family_projects`, `sample_projects` | which projects can see a family or a sample |
| `family_relationships` | explicit links between two samples: `parent_child` (with the role at each end), `couple`, or `relative` (`sample_id_b` is related through `sample_id_a` by an unknown degree, such as a PGT index known only to be on the mother's side); `source` says what made it (`pedigree`: the parents a pedigree gives; `manifest`: a package manifest's `family.relationships`; `manual`: a couple typed in, or an edit), `active = false` retires it |
| `family_structure_versions` | **append-only**, hash-chained: one row per change of a family's pedigree (its creation, a structure or member edit, a package import that changes a member's affected or carrier status, a sample deleted with its data): the version, a `structure_hash` over the members (sex, role, affected and carrier status, active) and the relationships, the full snapshot, and the family's own ID (`family_identifier`), which stays when the family is deleted (below) |
| `family_import_jobs` | package-import jobs: status, logs, validation issues and a summary per dataset, with progress (start and end; while running, the share of its files read and the time left). `family_id` is the family a job imports, committed with status `running` before the import writes anything of it (an import that cannot record it writes nothing); the report sign-out refuses a family while a job of it is queued, validating or running. A running job writes `heartbeat_at` every minute, stamped and judged by the database's clock (`now()`). A worker claims a job whose heartbeat is ten minutes old: it runs the job again if it was still `validating`, and ends it `failed` (interrupted) if it was `running`, adding a line to its log either way. An update that names a worker applies only while the job is that worker's, so the heartbeat of an import whose job was ended this way stops the import. `requested_by_id` is the account that asked for the job (SEC-32): the import runs as that account. Every import route needs an admin, so a user who is no admin sees no job. `requested_by` is the e-mail address it was asked under, shown with the job (a Microsoft sign-in may later change an account's address); it decides nothing. Deleting the account keeps the job, linked to no account; it can then no longer run. A job whose account is deactivated, or is no longer an administrator (SEC-35), before a worker takes it up does not run either: the worker ends it `failed`, with the reason, before writing anything of the family. `manifest_text` is the manifest CoGA keeps for the job, which its validation and import read instead of the package folder's own (a folder CoGA cannot write, such as a share mounted read-only), with its SHA-256 in `manifest_sha256`; both are NULL for a job that reads the folder's manifest, and `family_import_jobs_manifest_check` holds both or neither. `package_fingerprint` is the fingerprint of the local package folder when the job was queued (`family_package_fingerprint.py`: a SHA-256 over each file's path, size and modification time, each link's target, and the kept manifest's SHA-256; `family_import_jobs_package_fingerprint_check`: NULL or 64 lowercase hexadecimal digits); the job imports nothing when the folder's differs as it starts. It is NULL for a package in a bucket, a folder the queue could not read, and a job queued before revision `0019` |
| `import_deliveries` | packages pipelines delivered into a watched import folder (`FAMILY_IMPORT_WATCH_ROOTS`; `import_watcher.py`): one row per package folder and package fingerprint (`import_deliveries_folder_fingerprint_key`), so a folder that changes is a new delivery; an open one is checked again in place when the project its marker names, or its family or samples, change in CoGA. It keeps the watched folder, the folder's path and name, the ready marker as read (`marker`, `coga-ready.json`) with its text's SHA-256 and the run it names, the family, the project the marker names, the `state` (`incomplete`, `ready`, `needs_attention`, `superseded`, `gone`; once a person decided, `queued`, `imported`, `failed` or `refused`; `approved` and `changed_after_import` for a later step), the `issues` that stand in the way and the `warnings` an approver sees (code, message, path), a summary of the validation, the manifest Discover drafted for a folder without one (`manifest_text`, with `manifest_sha256`; both or neither), the last import job it was approved for (a dry run, or the import: `import_delivery_review.py`), how many rounds an open delivery's folder was missing, and when it was first and last seen and last changed state. Mutable state: each change of state is an event on the clinical audit chain `system:import-inbox`, which is the record. A new table: its baseline only |
| `individual_hpo` | per-person HPO terms, each `present`, `absent` or `unknown`. An analyst of the family's project or an admin adds, changes and removes them; a package import brings them in. Each change, an import's too, is a `phenotype` event on the clinical audit trail ([clinical-traceability.md](clinical-traceability.md)) |
| `repeat_expansions` | TRGT repeat calls per sample |
| `sample_paraphase_results` | Paraphase copy-number and haplotype results per sample |
| `nipt_artifact_variants` | the recurrent-artifact list for monogenic NIPT, per assembly and assay: entries added one by one, imported from the NIPT-M pipeline's recurrent table (as `curated`) or seeded from the cohort (`auto`) (see [monogenic-nipt.md](monogenic-nipt.md) and [data-import.md](data-import.md#the-nipt-artifact-list)) |
| `sample_interval_track_sources` | one row per sample, track type (`coverage`, `segments`, `apcad`, `apcad_pcf`, `apcad_segregation`, `haplotype`, `target_coverage`), source and file, with its row count; the rows themselves are in ClickHouse. A `haplotype` row's `metadata.pedigree_basis` holds what its blocks were built for besides the genotypes (the index couple, the couple's children in the file and the samples' sexes), which the PGT analysis compares with the pedigree (CLIN-66) |
| `small_variant_reviews` | the classification, ACMG criteria, tags, notes and evidence snapshot of a small variant in a family. The classification is free text: the dialogs write the class label ("Likely pathogenic - class 4"), in sentence case since revision `0013`, and a save stores a class label sent in another case as the label is now; the filters, the Variant Explorer and the check against the latest sign-out read a class label whatever its case |
| `structural_variant_reviews` | the same for a structural variant or CNV, with the CNV ACMG points and the evidence snapshot of the CNV classification |
| `repeat_expansion_reviews`, `paraphase_reviews` | the analyst's review of a repeat locus (`locus_id`, the catalogue's TRGT TRID) and of a Paraphase region (`gene_symbol`, Paraphase's region key), one per family and assembly (`UNIQUE (family_id, assembly_id, key)`). It holds tags from the review-tag catalogue, a note, and a classification on the type's scale (`classification`, checked: for a repeat `pathogenic_expansion`, `reduced_penetrance`, `premutation`, `intermediate`, `normal`, `uncertain`; for a Paraphase region `affected`, `carrier`, `risk_allele`, `normal`, `uncertain`), beside CoGA's automatic status, which it never changes. `evidence_snapshot` holds the calls a classification or the Report tag rests on (`captured_at`, `evidence`: the locus's or region's entry as the signed record freezes it), frozen when a save sets or changes the classification or adds the Report tag; the sign-out compares it with the calls then. A save that says nothing deletes the review. Each change is a clinical audit event (`variant_id` `repeat:<locus_id>` or `paraphase:<gene_symbol>`); see [docs/repeat-paraphase-review-design.md](repeat-paraphase-review-design.md) |
| `small_variant_filter_presets`, `structural_variant_filter_presets` | saved filter sets, per user. A small-variant one is reusable in every family; a structural-variant one is for one family or all. The owner is the account (`owner_id`, SEC-32), so a preset stays its owner's when a Microsoft sign-in changes the account's user name, and is deleted with the account. One name per owner (for a structural-variant preset, per owner, family and scope). `owner` is the user name the preset was last saved under, shown with it |
| `small_variant_tag_definitions`, `small_variant_tag_definition_project_links` | the review-tag catalogue, global or per project. A tag's `key` is what reviews, saved filter presets and audit events hold. It is set once, from the label at creation (numbered, as `<slug>_2`, when another tag, renamed or deleted, already holds that slug); an edit changes the label, never the key. A delete sets `is_active = false` and keeps the links, so the reviews that hold the tag still show it; no review save adds an inactive tag |
| `family_sv_gene_index`, `family_sv_gene_index_status` | per family, which genes a structural variant hits (for the "also hit by an SV" flag), and when and from which SV data version (`sv_data_version`, below) that index was built. It is rebuilt at next use once the family's SVs have changed |
| `family_variant_ranking_cache` | cached prioritised rankings (see [variant-ranking-cache.md](variant-ranking-cache.md)) |
| `qc_threshold_profiles` | named sets of sequencing-QC cut-offs, one per assay type (below) |
| `qc_thresholds` | per profile and metric, a warning and an error bound (either may be null) |
| `qc_threshold_changes` | **append-only** history of every cut-off edit (below) |

A `CHECK` (`sample_interval_track_sources_track_type_check`) holds
`sample_interval_track_sources.track_type` to the track types above. The baseline creates the
table only when it is missing, so a database created before `target_coverage` was in the list
(2026-10-04, for the NIPT-M pair) kept the old check, and importing a per-target coverage table
failed. Revision `0003` gives it the current check at its next start
([Schema Migrations](#schema-migrations)); revision `0012` gives it the check with
`apcad_segregation` (an embryo's segregation table, for the parental-origin view).

`repeat_expansions.status` is a call's status as its import classified it: `normal`, `review`,
`not_assessed`, `intermediate`, `premutation`, `pathogenic` or `unknown` (no call).
`not_assessed` is a call at a locus without thresholds: no catalogue row names its TRID, or the
row states no intermediate, premutation or pathogenic threshold (a count inside the benign range
such a row states is `normal`). The repeat page and the genome tracks both classify the call
again against the current catalogue, so a POLG call of 10 stored as `not_assessed` before that
rule reads normal on both at once (CLIN-14; before, the tracks showed the stored status until
the call was imported again).

A `CHECK` (`repeat_expansions_status_check`) holds the status to these values. A database
created before `not_assessed` was in the list (2026-10-13) kept the old check, and a TRGT import with
a call at a locus without thresholds failed, with the whole repeat dataset. Revision `0002`
gives it the current check at its next start ([Schema Migrations](#schema-migrations)).

A call whose TRID names several STRchive loci (the gene of ARX or HOXA13) and whose region
picks none of them is stored without thresholds. `repeat_expansions.metadata` holds the reason
under `locus_not_resolved` (CLIN-31), and the repeat page shows it with the call.

`sample_paraphase_results.total_cn`, `gene_cn` and `highest_total_cn` are `integer` columns
filled from each sample's Paraphase JSON. A copy number they cannot hold (below −2,147,483,648
or above 2,147,483,647) fails the `paraphase` dataset before any sample's results are replaced,
as a TRGT position beyond any chromosome fails the TRGT file. Before DATA-7 it failed the insert
with a database error, after the sample's results were deleted. A copy number that is not a
number is stored as null.

JSON keys that package import writes into the `metadata` columns:

| Column | Key | Contents |
| --- | --- | --- |
| `samples.metadata` | `sequencing_qc` | read metrics (NanoPlot), depth (mosdepth, or the PGT pipeline's mean coverage), alignment metrics (`alignment`, from Qualimap), the sex ngs-bits read (`sex_check`, which the Sample-QC sex check compares for a sample without chrX genotypes), an embryo's PGT QC (`pgt`: allele drop-out and drop-in, Mendelian concordance before and after imputation, as percentages) and the path of the QC report |
| `samples.metadata` | `alignment` | the CRAM/BAM path and index, package-relative (`path`, `index_path`), and for a package in a bucket the objects' URIs (`uri`, `index_uri`), which IGV reads; a local package's paths are read in the family's `package_import` folder |
| `samples.metadata` | `signal_tracks` | the package-relative paths of the HiFiCNV depth, MAF and copy-number files, served to IGV, and for a package in a bucket the objects' URIs under `uris` |
| `samples.metadata` | `mtdna` | the mtDNA haplogroup from the mutserve annotation |
| `samples.metadata` | `sv_files` | the file name per structural-variant source |
| `samples.metadata` | `assay`, `assay_panel` | copied from the manifest's `samples` entry (kept whole under `package_sample_metadata`): `assay: nipt_cfdna` marks the maternal-plasma cfDNA sample of a monogenic NIPT family, and `assay_panel` names its capture panel, which scopes the NIPT artifact list |
| `sample_interval_track_sources.metadata` | `normalization` | the transform the import applied to a track's values before storing them: `log2_ratio_to_diploid` on a HiFiCNV copy-number track (`segments`: log2 of the copy number over 2); `log2_ratio_to_autosomal_median` on a HiFiCNV depth track (`coverage`: log2 of the depth over the sample's autosomal median, held in `autosomal_median_depth`); or `none` on a depth track whose file gave no usable autosomal median (`autosomal_median_depth` null, or at or below zero), whose depths are stored as read. A track without the key holds its file's values. The track availability serves each coverage track's normalization (`coverage_normalizations`) |
| `sample_interval_track_sources.metadata` | `content` | what a coverage BED (source `web`) declared it holds when it was loaded (TRACE-9): `read_depth` or `log2_ratio`, from the BED upload's `content` or the package manifest's coverage entry, or `unknown` for a manifest entry without it. A coverage BED stored before BEDs declared this has no key and reads as `unknown`; the data is synthetic, so nothing was migrated. Other tracks' records have none. With `normalization` it decides what each coverage track holds (`coverage_read_depth.py::coverage_track_content`): for a coverage BED, what it declared; for another track, what its transform made of its file (read depth after `none` alone, a log2 ratio after a log2-ratio transform, else unknown; without the key, a WisecondorX or QDNAseq file's log2 ratios, any other caller's unknown). The track availability serves it (`coverage_contents`). The viewers draw read depth on a depth axis, a log2 ratio on their log2-ratio axis, and an unknown track on its own range without an axis. The readers that take coverage values as read depth (the mtDNA coverage and its `mtdna.mean_depth` verdict; the NIPT coverage check without a per-target table) read one `coverage` track per sample that holds read depth: a coverage BED declared as read depth, or a track whose records all name `none`; never a log2 ratio, nor a track of unknown kind. Each track they leave out is named with the reason (the mtDNA QC notes, the NIPT check's `coverage_notes`). A sample with two read-depth tracks is read from its BED |
| `sample_interval_track_sources.metadata` | `uploaded_from` | where the track's file came from: `web` for a single-file upload (the BED upload, the imputed-genotype upload's haplotype blocks), `family_package` for a package import's dataset (its WisecondorX, QDNAseq and HiFiCNV tracks, the APCAD tracks of a VCF or of a BED, the PCF tracks, its coverage BEDs and its per-target coverage tables), `demo-script` for the demo quartet loader. The BED loaders record it since 2026-10-20. The single-file BED upload and a package's coverage BED share the source `web`, so without this key no record says which of the two loaded a sample's coverage BED. A track stored before then has no key; nor has an APCAD BED or a per-target coverage table loaded before 2026-10-21 (TRACE-17). The signed record names it for each coverage track (`coverage_tracks` in `report_signouts.snapshot`: `upload`, `package`, or null when the record says nothing) |
| `families.metadata` | `pipeline` | the Nextflow run parameters (reference build, callers, annotation caches; for the PGT pipeline the affected parent, ROI and QDNAseq bin size, and the callset and phasing panel it started from) |
| `families.metadata` | `pipeline_qc` | the QC the PGT pipeline reported for the family as a whole: the KING kinship, IBS0 and SNP count of every pair (`kinship`), and the files they came from |
| `families.metadata` | `haplotype_phase_corrections` | the parents' phase switches the haplotype blocks undid, one per switch: the parent and its side, the chromosome, the position from which its two haplotypes are swapped, the end of the children's switches, and how many informative children switched, out of how many. Replaced at every haplotype import; the lineage colouring and the phased markers read the parents' phase through them |
| `families.metadata` | `pipeline_haplotype_origin` | the PGT pipeline's reading of which haplotype of the affected parent is the affected one: the parent, the sites each haplotype shares with the index's affected and normal haplotypes, and the pipeline's conclusion. Evidence only; CoGA's risk-haplotype inference does not read it |
| `families.metadata` | `package_import` | the import provenance: folder (`folder_path`: a local folder's real path, or a bucket folder's URI), manifest, datasets and the manifest's own `metadata`. The manifest is named by where it was read (`manifest_source`: `folder`, its file's path in `manifest_path`, or `coga`, the text the import's job kept, with `manifest_path` null and a managed copy among the family's `raw_import_files` as dataset `manifest`) and its SHA-256 (`manifest_sha256`: of the file's bytes, or of the kept text). `package_fingerprint` is the folder's fingerprint the import read it with, taken before it read the folder (NULL for a package in a bucket). The package list reads `folder_path` to name, per folder, the families imported from it (`imported_package_folders.py`). `delivery` names the delivery of a watched import folder it was imported from (its id, the pipeline's run, the ready marker's SHA-256 and the package fingerprint), absent for a package a user chose. `previous_import` keeps the record the family had before this import registered it again (without a previous record of its own; absent for a family's first import): an import undone because its package changed while it was read puts that record back (`put_back_family_package_record`). The paths recorded on the samples (`alignment`, `signal_tracks`, the QC report) are served from that folder, when it lies below `FAMILY_IMPORT_ROOTS` |
| `families.metadata` | `pgt`, `analysis_type` | the manifest's PGT context (the inheritance model, the obligate and proven carriers, the affected parents and the indexes) and analysis type |
| `families.metadata` | `import_incomplete` | set by an import that failed for some datasets and kept the others. It holds the datasets that failed and those that imported, when, and the import job (after a failed overwrite whose restore failed too, other than by refusing without its whole backup: every dataset of that import as failed, none as imported). For each failed dataset it holds every scope it failed for, oldest first, each with the job that holds its error (`failed_scopes`), and the job of its latest failure (`failed_jobs`, which the family pages and a sign-out name; null for a failure outside an import job, a single-file upload's, which they name with no job). In `failed_scopes` a later failure keeps an earlier one's; a failure of the same dataset under another callset or other samples never replaces it; the same scope failing again is one failure, with its latest job. A completed import removes each failure it brought again for that scope (imported, imported with warnings, or registered when its package carries nothing CoGA imports for the dataset), as does each single-file upload; the flag goes with the last failure. Once what is left is one job's, that job becomes the flag's `job_id`. An admin's deletion of a sample removes the sample from each failure's scope as it commits: a failure that named only that sample goes, one that named others too keeps them, and one of a family-level file (no samples) stays; the flag goes with its last failure (SAFE-35: no import or upload can bring a deleted sample's data again, so a failure naming it could never be cleared). A flag written with one scope per dataset (`scopes`, its job in `failed_jobs`) is read as one failure each, and stays as it was until a failure leaves it. A flag not in this shape stays as it is, a later failure included |
| `families.metadata` | `import_unfinished` | the imports that have begun writing the family and not finished, by job: when each began, its datasets and those it finished. An import writes its entry before its first write (and again once it holds the family's variant-write locks) and removes it when it ends, so an entry that stays names an import whose process stopped part-way. The entry records each dataset's scope (`scopes`: the small variants' callset, as `imported_scopes` records it; a per-sample dataset's samples; `empty` for a dataset that carries nothing CoGA imports, which any import of the dataset completes). Only an `overwrite` that imports again, for that scope, what the import had not finished removes it. **A failed overwrite:** once its restore begins, its entry names every dataset of the family that ClickHouse holds (`imported_scopes`: the restore puts Postgres back in one transaction, so a dataset Postgres alone holds cannot be left partly written) and every dataset of the import, none finished, each listed with its scope, one sample at a time, under `whole_family`. A restore that puts the family back or refuses to start removes the entry; one that fails part-way (or whose process stops in it) leaves it. Each `overwrite` that completes, and each single-file upload with `overwrite`, removes from it what it brought again; the entry goes with the last item. **A single-file upload** that writes ClickHouse (small variants, structural variants, interval tracks) writes an entry of its own before its first write there, keyed `upload-` and an id, with no job, committed apart from the upload. In the same `whole_family` shape it lists what the upload may leave partly written or missing (its callset; its caller's calls of every sample its rewrite deletes; the sample's track). The upload's commit removes it. A failure removes it once nothing is missing; otherwise it stays and gates sign-out until each item has been brought again with `overwrite`. **A deletion** of the family's data that deletes or rewrites ClickHouse rows (an admin's deletion of a sample, of its SVs or of the family; a PED upload or manual family that replaces it; a structure change that clears its data) writes an entry keyed `deletion-` and an id the same way, before its first write there. It lists each dataset the family holds that the deletion may leave partly written or missing; an item that stands for one sample's part of a family-level file names the sample (`sample`). Its commit removes it (with the family, for a deletion of the family). The deletion of a sample removes that sample's items from every `whole_family` entry, and the sample from each dataset's scope in an import's own entry: a dataset whose scope named only that sample goes, and the entry goes with its last dataset (SAFE-35). A failure leaves exactly what its writes may have left missing, or removes the entry (SAFE-31; [data-import.md](data-import.md#the-mark-of-an-upload-that-did-not-finish)) |
| `families.metadata` | `imported_scopes` | what the family's imports imported and its single-file uploads brought, by dataset, each with its scope: `source`, the callset the small variants were stored as (the `source_format` an SNV dataset declares, or with `auto` the one its file holds, read before the import's first write; `auto` itself names no callset and nothing completes it, [data-import.md](data-import.md#the-small-variant-callset-an-import-records)); `samples`, one sample at a time for a per-sample dataset, null for a family-level file; for a coverage BED, `content`, what it declared it holds (TRACE-9), which a later record of the same sample's BED replaces and no overwrite compares. A dataset that carried nothing CoGA imports is not recorded: the family holds nothing of it. An import adds what it imported when it ends, with its flag or its clear, and what a stopped import had finished once that import's entry goes. An upload adds what it brought in the transaction that commits it, under the package dataset that brings the same data or under a name of its own (`sv_<caller>`, `bed_<type>`; [data-import.md](data-import.md#what-an-upload-records)). Whatever a failed overwrite's restore may remove besides its own datasets, its entry in `import_unfinished` names all of it |

A family's `metadata` also holds `derived_data_status` (which analyses an edit made stale, and
whether a structure update kept the imported data; see
[family-member-management.md](family-member-management.md)) and `qc_profile` (below).

**Sequencing-QC limits.**

- A family uses the profile named in `families.metadata->>'qc_profile'`, or the `is_default`
  one; a partial unique index allows only one default.
- The seeded profiles (`default`, `long_read_wgs`, `short_read_wgs`, `short_read_panel`,
  `nipt_monogenic`, `pgt`) are empty, because the laboratory agrees the cut-offs. A new profile
  (`POST /admin/qc-thresholds/profiles`) inherits none. Its key comes from the label and never
  changes, since families refer to it.
- Code defines which metrics exist and whether a low or a high value fails
  (`qc_threshold_service.QC_METRICS`); only the bounds are configuration. `direction` is copied
  onto each row so it stays readable.
- Each edit to a bound requires a reason. `qc_threshold_changes` records it with the replaced
  value, the new value, the reason and the user; the request log alone could not say what the
  old value was.

### 04_traceability.sql: provenance, audit and integrity

| Table | Holds |
| --- | --- |
| `alembic_version` | the schema-migration ledger: one row, the revision the database is at. Only the migration step writes it, as the owner; `coga_app` may read it ([Schema Migrations](#schema-migrations)) |
| `audit_log_events` | **append-only** log of every HTTP request: user, route, status, duration, client address, and the request body with sensitive fields masked; a value Postgres cannot store is escaped, and each event is stored once, under its `event_id` (below) |
| `ui_events` | **append-only** log of UI interactions that never reach the backend (clicks, in-app navigation), masked before storage, escaped and stored once like `audit_log_events`; the browser's time of each, within bounds (below). Not hash-chained (below) |
| `raw_import_files` | every source file an import used, with its size and SHA-256, for download and integrity checks (below); an uploaded coverage BED's `metadata.content` says what it was declared to hold (TRACE-9) |
| `family_annotation_manifest` | per family, the upstream annotation versions (VEP, ClinVar, gnomAD, …) and where they came from (`manifest`, `vcf_header` or `manual`) |
| `clinical_audit_events` | **append-only**, hash-chained log of clinical actions: classification, tags, notes, phenotype changes, compound-het pair reviews, pedigree changes, a family's deletion (its last event), the first search of a family that applies each gene-panel version (`panel_applied`) and sign-out, with before and after; one chain per family, and chains of their own for the NIPT artifact list (`system:nipt-artifacts`), the repeat loci's counting-unit record (`system:repeat-loci`) and the deliveries of the watched import folders (`system:import-inbox`, by `system:import-watcher`) |
| `report_signouts` | **append-only**, hash-chained signed reports: each sign-out is a new version with a content hash and the frozen snapshot; its report code is made from the family identifier, the version and the content hash, so no column holds it ([clinical-traceability.md](clinical-traceability.md#the-report-code)). Versions are numbered per family identifier, on across every family made under it, and unique per identifier (`uq_report_signouts_family_identifier_version`, TRACE-13) |
| `integrity_anchors` | **append-only**, signed snapshots of every hash chain's head |

**Files from a bucket.** For a package imported from a bucket, `storage_path` is the
object's `gs://` or `s3://` URI. `metadata.store_object` holds the store's own record of the
object: its size, GCS `generation` or S3 `version_id`, `etag`, and the store's checksums, each
labelled with its `algorithm` and `encoding` (and for S3 whether it covers the whole object). A
file staged for the import also has its SHA-256, taken from the staged copy. An alignment that
stayed in the bucket has none: the store's checksums are not SHA-256, so they never go in
`sha256`. **Verify** on the admin page checks such a file against `store_object` instead of
re-hashing it: the object must still exist, with the recorded size and generation or version
id (an unversioned S3 object is compared by ETag). It reports a vanished object as `missing`
and a replaced one as `mismatch`. The file is not downloaded through CoGA.

**Where a recorded file is read.** `storage_path` is read back as data, so an edited row could
name any file. The admin page lists, downloads and verifies a file only where an import puts
one. Its real path (symlinks resolved) must lie inside a local `FAMILY_IMPORT_ROOTS` folder,
the temporary folder a bucket package is staged in, or the upload folder `data/raw_imports`
(`services/raw_import_files_pg.py`); the file is read by that real path.

- A row naming any other path is listed as unavailable without being looked up; its download
  and **Verify** answer 403. Verify asks the store only about an object below a remote
  `FAMILY_IMPORT_ROOTS` entry. The list marks a row of either kind `outside_read_folders`, and
  the admin page says it is not read and why a Verify was refused (SEC-27).
- With `FAMILY_IMPORT_ROOTS` empty (the import then takes a package folder anywhere), only the
  upload and staging folders are read.
- Deleting a family or a sample unlinks its `managed` copies only inside the upload folder. A
  managed row naming any other path is skipped with a log line, and nothing is deleted for it.
  A package folder need not be named after its family, nor an upload folder after the family's
  present ID (SEC-29).
- The deletion reads its managed rows in its transaction and unlinks their copies only after it
  commits, so a failed deletion keeps every file its records name (before, a failed deletion
  could leave records whose files were gone). A copy that cannot be unlinked after the commit
  stays on disk and is logged with its record, its path and the error's kind: a file without a
  record, never a record without its file (SAFE-35).

**Values Postgres cannot store.** A request can carry what Postgres refuses: a NUL character
(a `%00` in the path or query string reaches the app decoded; `\u0000` in a JSON body), half
of a UTF-16 surrogate pair (`\ud800` in a JSON body), and the JSON numbers `NaN` and
`Infinity`. Such a value used to fail the INSERT, so the request's row (in the async mode,
every row of its batch) never reached the table. The writers of `audit_log_events` and
`ui_events` now store it as an escape (`core/pg_storable.py`): a NUL as the four characters
`\x00`, half a pair as `\udXXX`, and a number as the string `"NaN"`, `"Infinity"` or
`"-Infinity"`; a JSON key is treated like a value. `_escaped` lists, sorted, the columns where
this happened: in `request_meta` for `audit_log_events`, in `detail` for `ui_events` (where a
client's own `_escaped` is dropped). A row without it is stored as the request sent it. The
escape is not unique, since a request can send the characters `\x00` itself: `_escaped` says
that a column holds an escape, not which one.

**Each event stored once (SEC-17).** The worker that writes `audit_log_events` and `ui_events`
retries a batch after a failed attempt, and after its last attempt writes the batch's events
one at a time (`services/event_pipeline.py`). A commit whose answer never reached the worker
(the connection dropped as Postgres committed) looked like a failure, so each row of the batch
was stored twice. Now each event carries an id of its own, `event_id` (a unique UUID), which the
backend gives it when it makes the event, before queuing it. A UI event's id is the one the
browser gave it (`lib/telemetry.ts`); `routers/ui_events.py` keeps it when it spells a UUID (an
event without one gets the backend's). So a batch the browser sends again (one in flight as its
page closed, or a page-close keepalive whose answer it never read; SEC-36) also stores each
event once. Every write of the event carries the id, and the writers' INSERT does nothing for
an id the table holds (`ON CONFLICT (event_id) DO NOTHING`; the INSERT and SELECT that
`coga_app` holds on the table are all it needs, and the triggers that refuse UPDATE never
fire). A row written before the column has no id and, the tables being append-only, keeps
none. An event the pipeline could not store is logged with its payload, `event_id` included: a
row restored from that line with its `event_id` is stored once, also when the event did reach
the table.

**A row naming a deleted user (SEC-17).** A row whose user's account was deleted while the row
waited in the queue (only operator SQL deletes accounts) fails the foreign key on `user_id`,
and was lost. Written alone, as the worker writes a failed batch's events, it is now stored
without the link: `user_id` empty, the user's id kept in `request_meta._deleted_user_id` or
`detail._deleted_user_id`, and `user_email` and `user_role` as they were. Only the writers set
that key; a client's own is dropped from `detail`. The log says so in one line that names the
error by its kind and carries no value, and the event is not counted as lost.

**A UI event's time.** `ui_events.occurred_at` is the browser's clock; `created_at` is the
database's. The endpoint (`routers/ui_events.py`) keeps `occurred_at` when it lies between a day
before and five minutes after the moment the server received the event; a value without an
offset is read as UTC. Any other value is stored as that receive time, and
`detail._client_occurred_at` keeps the client's value in ISO 8601 (only the endpoint sets that
key; a client's own is dropped). A row without it holds the browser's time, or none. Before, a
value the database driver cannot convert to UTC (`0001-01-01T00:00:00+05:00` lies before the
year 1 in UTC) failed the INSERT, in the async mode with every other row of its batch, and a
naive `9999-12-31T23:59:59.999999` was stored as `infinity`.

**Records outside the user's projects.** A request for a family, sample or project that exists
outside the user's projects is answered exactly like a request for an unknown ID (404). Its
`audit_log_events` row keeps the difference: `request_meta.record_hidden` names the kind
(`family`, `sample` or `project`), as it does for another user's filter preset or import job
(`filter_preset`, `import_job`). The middleware sets it; a request cannot.

What these records hold and how they are checked is in
[clinical-traceability.md](clinical-traceability.md).

Triggers in this file refuse UPDATE and DELETE on the seven append-only tables
(`audit_log_events`, `ui_events`, `clinical_audit_events`, `report_signouts`,
`integrity_anchors`, `qc_threshold_changes` and `family_structure_versions`; the last two are
made in `03_assay.sql`). On the tables that name a user or a family, the one change they allow
is the `ON DELETE SET NULL` unlink when that user or family is deleted; the rows keep a copy of
the actor and the family identifier. The file's three ancestry PCA tables are append-only too
([Exploratory ancestry PCA](#exploratory-ancestry-pca)).

**The structure versions' chain (SEC-7).** Each `family_structure_versions` row carries a
`row_hash` over its content (its id, time, family identifier, version, structure hash, snapshot
and metadata) and the previous row's hash, per family, like `clinical_audit_events`. The chain
is partitioned and ordered on `family_identifier`, then `created_at` and `id`. So a family's
deletion, which nulls `family_id` (it used to delete the versions), leaves the chain whole and
verifiable.

- `GET /admin/integrity/verify?table=family_structure_versions&family_id=…` walks it, and the
  signed anchors record its head.
- Who made each change is in the matching `pedigree` event of the clinical audit trail.
  `created_by` is a user link that the account's deletion nulls, so the chain does not bind it.
- Versions written before the chain have no `row_hash` and no `family_identifier`, and are not
  part of it.
- A database made before the chain gains its columns, the nullable `family_id` and the
  `ON DELETE SET NULL` link from upgrade statements in `03_assay.sql` (each a no-op on a new
  database), and from revision `0004` once the baselines are frozen
  ([Schema Migrations](#schema-migrations)).
- A family's deletion writes its last clinical audit event (`family_deleted`) before the family
  row goes, so the chains it leaves end with it.

**A family made again under its ID (TRACE-13).** The three chains belong to a family identifier,
not a family row: a family deleted and made again under its ID writes on them after the
deletion.

- The structure versions and the clinical audit events are ordered by time, which runs on
  across the two families. The structure versions are numbered per family row, so the new
  family's start again at 1. Its `pedigree` events, and its `family_deleted` event, name how
  many versions the deleted families stored on the chain before it (`deleted_family_versions`),
  so the clinical audit trail names each version once (ENG-19).
- The sign-outs are ordered by version, so their versions are numbered per family identifier:
  the new family's first sign-out is the version after the deleted family's last.
  `uq_report_signouts_family_identifier_version` makes a version unique per identifier, where
  `UNIQUE (family_id, version)` cannot, as the deletion nulls `family_id`. A database where an
  identifier already holds a version twice (a family made again and signed out in both, before
  this rule) cannot take that index: the check before it in `04_traceability.sql` stops the
  migration with a message that counts the identifiers and names none.

**Why `ui_events` is not chained.** A UI event is what the browser reports about itself (any
signed-in user can send any event), not the device's record of a clinical action. A chain would
prove only that nobody changed what a client claimed. The HTTP audit log beside it is
append-only without a chain for the same reason; the clinical actions are on the chained
clinical audit trail.

### 05_grants.sql: the runtime role

Creates the restricted role `coga_app` and grants it normal access to every table, except:
UPDATE, DELETE and TRUNCATE on the seven append-only tables and the three append-only ancestry
PCA tables, and anything but SELECT on the ledger `alembic_version`. How and when the
application switches to it is in [db-runtime-role-runbook.md](db-runtime-role-runbook.md).

## Schema Migrations

The Postgres schema reaches a database through one migration step,
[postgres_migrations.py](../backend/app/core/postgres_migrations.py), which `init_postgres_schema`
runs. The API runs it at every start when it connects as the table owner
(`POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP`, the default); when the API runs as the restricted
role, `python -m app.db_migrate` runs it as the owner before each rollout
([db-runtime-role-runbook.md](db-runtime-role-runbook.md)), and the API checks the revision at
its start instead (below). The step is one transaction, so it applies whole or not at all:

1. It takes an advisory lock, so two instances starting together migrate one after the
   other.
2. It refuses to run as `coga_app`. It also refuses a ledger that names a revision this version
   does not have: a newer version migrated that database, and older code does not run on a newer
   schema.
3. Until the release candidate, it re-applies the five baseline files. They are idempotent, so
   a pull request that edits a baseline reaches every existing database, the dev stack's
   included. But `CREATE TABLE IF NOT EXISTS` leaves an existing table as it is.
4. It runs the Alembic revisions in
   [backend/db/migrations/versions/](../backend/db/migrations/versions/) that the ledger does not
   list yet, and records the last one in `alembic_version`.

| Revision | What it does |
| --- | --- |
| `0001` | applies the five baseline files |
| `0002` | gives `repeat_expansions` the baseline's `repeat_expansions_status_check`, with `review` and `premutation` (2026-10-07) and `not_assessed` (2026-10-13) |
| `0003` | gives `family_relationships` and `sample_interval_track_sources` the baseline's checks, with `relative` (2026-10-02) and `target_coverage` (2026-10-04) |
| `0004` | gives `family_structure_versions` its hash chain (`family_identifier`, `row_hash`, `prev_hash` and the chain's index), and a nullable `family_id` that the family's deletion nulls; gives it and `ui_events` their append-only trigger and REVOKE (SEC-7); by running those statements of the baselines |
| `0005` | gives `project_users` its `access_level` (`analyst` or `reader`, with its check), each stored membership an analyst's (SEC-1), by running the statement `01_access.sql` adds it with |
| `0006` | gives `users` its `azure_object_id` (a unique UUID, empty for a password account) for Microsoft sign-in (SEC-4), each stored account kept without one, by running the statement `01_access.sql` adds it with |
| `0007` | makes a family identifier's sign-out versions unique (`uq_report_signouts_family_identifier_version`, replacing the plain `idx_report_signouts_chain`; TRACE-13), after a check that stops it where an identifier holds a version twice, by running those statements of `04_traceability.sql`; every stored sign-out stays |
| `0008` | keeps an import job's requester and a filter preset's owner by the account (SEC-32): gives `family_import_jobs` its `requested_by_id`, each job linked to the account its `requested_by` address finds or to none, and both preset tables their `owner_id`, each preset given to the account its `owner` user name finds (a preset no account's user name finds is removed), with the foreign keys and the presets' unique indexes on the account, by running those statements of `03_assay.sql`; every job stays |
| `0009` | gives `audit_log_events` and `ui_events` their `event_id` (a unique UUID, SEC-17), each stored row kept without one, by running the two statements `04_traceability.sql` adds it with |
| `0010` | keeps a gene-reference sync's and a clinical CNV knowledgebase rebuild's requester by the account (SEC-35): gives `gene_info_refresh_jobs` and `clinical_cnv_kb_jobs` their `requested_by_id`, each job linked to the account its `requested_by` address finds or to none (as `startup-bootstrap`), with the foreign keys, by running those statements of `02_reference.sql`; every job stays |
| `0011` | drops three columns no code read (ENG-6): `auth_login_attempts.last_success_at` and `small_variant_reviews.compound_het_partner_variant_keys`, never written or read, and `structural_variant_reviews.variant_key`, never written, with its unique index; every row kept with its other values. It also removes the key `unresolved_roi` from the families' `metadata`, which an import wrote when it could not resolve a manifest's region of interest, and nothing read or cleared |
| `0012` | gives `sample_interval_track_sources` the baseline's check, with `apcad_segregation` (an embryo's segregation table from the PGT pipeline, read by the `apcad` dataset for the parental-origin view) |
| `0013` | writes the class labels in sentence case (ENG-6): where a mutable row holds a class label in another case ("Likely Pathogenic - class 4"), it writes the label as the code writes it now ("Likely pathogenic - class 4"). That is the classification, the compound-het pair's classification and the ACMG scoring's (`acmg`) of `small_variant_reviews`; the classification and the CNV scoring's (`cnv_acmg`) of `structural_variant_reviews`; and the classifications each filter preset filters on (`filters.classification`, a list or comma-separated text). Only the case changes; text that is no class label, every other value and `updated_at` stay. The append-only tables keep their text |
| `0014` | gives `repeat_locus_count_units` its `count_offset` (QA's count correction per locus, CLIN-57), with the baseline's default 0 and check; every stored record kept with no correction |
| `0015` | gives `users` the baseline's `users_role_check` with the five roles (`viewer`, `analyst`, `supervisor`, `superuser`, `admin`; the owner's decision of 2026-11-04) and, on a database whose check is from before them, makes each `viewer` an `analyst`, as a viewer could then change the records of each project where it was an analyst; a `superuser` and an `admin` keep their role. The request audit has no row for these changes: the revision is their record |
| `0016` | gives `ancestry_runs` and `ancestry_results` what the PCA reading the stored callsets needs ([Exploratory ancestry PCA](#exploratory-ancestry-pca)) |
| `0017` | gives `users` its `activated_at` and `archived_at` with `users_archived_inactive_check`. An active account, and an inactive one with a request of its own in `audit_log_events` (it signed in once), get their creation's time as `activated_at`; an inactive one without such a row never signed in and waits for approval (NULL). No account is archived |
| `0018` | gives `family_import_jobs` its `manifest_text` and `manifest_sha256` with `family_import_jobs_manifest_check`: an import job can keep the manifest it validates and imports with. Every job stored before read its folder's manifest and keeps neither |
| `0019` | gives `family_import_jobs` its `package_fingerprint` with `family_import_jobs_package_fingerprint_check`: an import job keeps the fingerprint of the package folder it was queued for. Every job stored before was queued without one and keeps none |
| `0020` | gives `gene_panels` its `external_selection` and replaces the unique index on source and external id (`idx_gene_panels_source_external`) with one on source, external id and selection (`idx_gene_panels_source_external_selection`), so one PanelApp panel can be imported once per choice of confidence levels, each a panel of its own. Each PanelApp panel stored before gets the levels its last import took, as its `source_metadata` records them (`import_options.confidence_levels`, lowest first); one whose metadata records none, and every other panel, gets none |
| `0021` | gives `users` its `sessions_ended_at`, when the account last signed out, every token issued to it before then refused. Every account stored before gets none: none has signed out this way |

A database the baselines created before the ledger existed has an empty ledger, so its first
start with the ledger runs every revision. Each revision ends at the baseline's definition,
whatever it starts from. Every row stays, with two exceptions: `0008` removes a filter preset
whose user name no account has (it is nobody's), and `0011` drops three columns with the values
they held (none of which anything read) and the families' `unresolved_roi`. `0013` changes only
the case of class labels in mutable rows. The integration
test [test_postgres_migrations.py](../backend/tests/integration/test_postgres_migrations.py)
shows both:

- a new database ends with exactly the schema the baseline files create (the same catalogue
  fingerprint of its columns, constraints, indexes, triggers, functions, sequences, views,
  privileges, extensions and owners);
- a database from before the ledger, with the old checks and rows in its tables, reaches that
  same fingerprint with its rows unchanged, and then stores a TRGT call at a locus without
  thresholds.

**Writing a revision.** Until the release candidate, a change to a table that already exists (a
value added to a `CHECK`, a column, a type, an index) goes in its baseline as the table's final
form, and in a new revision that brings an existing table to that form. From the repository
root, `alembic revision --rev-id <next number> -m "<what it changes>"` writes the revision from
the template ([script.py.mako](../backend/db/migrations/script.py.mako)). A revision:

- may meet a table that already has the change (a new database gets the baseline first);
- keeps every stored row valid;
- runs forward only: its `downgrade` refuses.

A new table needs only its baseline. `backend/tests/test_postgres_migrations.py` checks that the
revisions form one numbered line (so two pull requests that take the same number fail it), and
that a revision's check is its baseline's. Alembic's commands that need a database
(`alembic upgrade`, `alembic current`) stop in `env.py`: the revisions run only through the
migration step.

**A start that does not migrate.** With `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` (the
restricted role) the API only reads the ledger at its start, which `coga_app` may
(`check_schema_revision` in the same module). It starts only when the ledger names exactly this
version's newest revision:

- An older revision, an empty ledger or none at all means the migration job has not run for
  this version. The start is refused with "The database is at schema revision …, and this
  version of CoGA needs revision …", which says to run the job first.
- A revision this version does not have means a newer version migrated the database: "…, which
  this version of CoGA does not have …: this version is older than the database".

Either way the message is logged and nothing else starts. Until the release candidate a pull
request can still change a baseline without a new revision (a new table, say), which this check
cannot see. On Google Cloud the migration job runs before every rollout of a new image, so it
applies such a change too.

**Upgrading a database that holds data.** Take a backup first. On Google Cloud the deploy does
so itself. Before each schema migration it starts (the migration job, or the first start of a
new revision in the default mode), it takes an on-demand Cloud SQL backup and waits until it is
done; it stops before the migration when the backup fails
([deployment-gcp.md](deployment-gcp.md), 12.1). That backup is the restore point of the upgrade
itself, beside the daily backups and point-in-time recovery (12.3). Elsewhere, take one by hand
before you migrate; locally the data is synthetic. Then roll out the new version. In the default
mode its first start migrates; with the restricted role the migration job runs first, and the
API rolls out only when it succeeded ([deployment-gcp.md](deployment-gcp.md), 12.8). The log
names the revision the database came from and is now at
(`Postgres schema migrated from revision … to …`). A failed migration keeps nothing, and the
running version keeps serving.

**Going back.** Revisions do not run backwards. A version older than the database's revision
refuses to start on it, whether it migrates at start (step 2) or not (above); versions from
before the ledger do not check. Restore the backup taken before the upgrade
([deployment-gcp.md](deployment-gcp.md), 12.3), or go forward with a fix
([RELEASING.md](../RELEASING.md#rollback)).

### Freezing the baselines at the release candidate

This is a proposal for QA to confirm ([TF-18](regulatory/TF-18-change-configuration-management.md),
§3a). Until the release candidate, pull requests still edit the baseline files, and step 3
brings those edits to every database. The release candidate's pull request freezes them:

1. It sets `REAPPLY_BASELINES_ON_EVERY_RUN` to `False` in `postgres_migrations.py`. A start then
   runs only the revisions the ledger lacks; revision `0001` applies the baselines to a new
   database, and to one from before the ledger.
2. It pins the SHA-256 of the five baseline files in a test, so they cannot change after the
   release candidate: a database created later gets the same baseline as one created at the
   release candidate.
3. From then on every schema change is a new revision only. Its change record (TF-18 §8) names
   the revision, its verification runs it on a database at the previous revision (as the
   integration test does), and the release record states the release's last revision.

## ClickHouse Tables

[001_coga_variant_storage.sql](../backend/db/schema/clickhouse/001_coga_variant_storage.sql)
only creates the database. Each assembly gets its own tables, created the first time the
backend uses that assembly, by
[clickhouse_variant_storage.py](../backend/app/services/clickhouse_variant_storage.py) and
[clickhouse_interval_tracks.py](../backend/app/services/clickhouse_interval_tracks.py). Their
names start with the assembly, for example `GRCh38/SNV_INDEL/entries`.

| Table | Holds |
| --- | --- |
| `SNV_INDEL/variants/details` | one row per small variant and annotation set: position, alleles, the full annotation |
| `SNV_INDEL/variants/annotations` | per variant and transcript: gene, consequence, HGVS, population frequencies, scores. The family search and the Variant Explorer test their annotation filters here, all on one row (one transcript). The family search reads only the rows of variants the family carries, here and in the two indexes below; the Variant Explorer reads every family's |
| `SNV_INDEL/variants/annotation_index` | per variant: its genes and the fields the filters use, pooled over its transcripts, for what holds for the variant as a whole (its rsID, genes and ClinVar terms). The Variant Explorer also reads each variant's position here to apply a gene panel's regions to every family at once |
| `SNV_INDEL/variants/gene_index` | per gene term (symbol or gene id) and variant, for gene searches and the gene panel filter of the family page and the Variant Explorer |
| `SNV_INDEL/entries` | the calls: per family and variant, each sample's genotype and call fields (`calls.*`). The family page also reads a family's own rows here: by position, to apply a gene panel's regions (a join on 64-kb windows; ENG-15); and by key, so that each annotation filter gathers the family's own variants into its set, not every family's (`_small_family_key_condition`) |
| `SNV_INDEL/family_variant_summary`, `SNV_INDEL/family_sample_variant_summary` | variant counts per family and per sample |
| `SNV_INDEL/family_data_version` | one token per change to a family's small variants (below) |
| `SV/variants/details` | one row per SV key, that is per family, structural variant and source: type, span, the SVLEN its caller wrote (`svLen`; the SV length filters measure it), the full annotation. A call written as a sequence allele without SVLEN (VCF 4.4 writes it `.`) is stored with the length its alleles state, len(ALT) − len(REF), negative for a deletion (CLIN-41); any other call without SVLEN is stored with none and measured by END − POS. A call stored before that rule keeps its stored value until its file is imported again |
| `SV/key_lookup` | maps a family's variant ids, per source, to their internal keys |
| `SV/entries` | the structural-variant calls per family and sample |
| `SV/family_data_version` | one token per change to a family's structural variants (below) |
| `INTERVAL/entries` | the interval-track rows: coverage, segments, APCAD, PCF segments, haplotype blocks, a capture panel's per-target coverage (`target_coverage`, below), and the genome-wide haplotype lineage the genome overview reads (`haplotype_lineage`, precomputed from the haplotype blocks; see [haplotype-segregation-analysis.md](haplotype-segregation-analysis.md)) |

An `overwrite` package import of an existing family also makes **backup tables**: a copy of
the family's rows in `SNV_INDEL/entries`, `SV/entries`, `SV/variants/details`, `SV/key_lookup`
and `INTERVAL/entries`, named after the import: `<assembly>/SNAPSHOT/<import job id>/<table>`
(`run-<hex>` for an import run outside a job). They are never served, and the startup storage
check ignores them. The import drops them when it ends. For an import whose process stopped,
the worker that ends its job drops them, and every start drops a backup whose job has ended (or
one made outside a job, a day after it was made). See
[clickhouse_family_snapshot.py](../backend/app/services/clickhouse_family_snapshot.py).

Column-level detail lives with the DDL in `clickhouse_variant_storage.py`
(`ensure_clickhouse_variant_tables`) and `clickhouse_interval_tracks.py`. Notable columns:

| Table | Column | Why it exists |
| --- | --- | --- |
| `…/SNV_INDEL/entries` | `calls.dp`, `calls.ad` | The call's read depth and allele depths, `UInt32` (up to 4,294,967,295 reads). A mitochondrial or amplicon call reads far more than the 65,535 of the former `UInt16`, and one such depth failed the insert of a whole upload or import (SAFE-30). A table created with the narrower type is altered to `UInt32` the first time the backend ensures the assembly's tables (`_widen_small_variant_call_columns`). It reads the two columns' types from `system.columns` first, so it alters a table once; every stored value is kept. `calls.gq`, a phred score, stays `UInt16`. |
| `…/SNV_INDEL/entries` | `calls.ps` | Phase set, for read-based cis/trans against a phased SV |
| `…/SNV_INDEL/entries` | `calls.af` | Per-allele fraction; on chrM this *is* the heteroplasmy level the mtDNA workspace reads |
| `…/SNV_INDEL/entries` | `calls.filters` | The call's own FILTER values (`PASS` kept as is). Filled only when the VCF holds one sample, as each file of a monogenic NIPT pair, or of a primary callset read one file per sample, does: the record's FILTER then describes that one call. A DeepVariant `RefCall` kept as a partner's call says the caller read that partner as reference there. A call from a multi-sample VCF has none, since its record's FILTER describes the site. |
| `…/SNV_INDEL/entries` | `calls.metrics` | The caller's metrics of the call, a map from the INFO key to a number, filled the same way ([vcf_call_metrics.py](../backend/app/services/vcf_call_metrics.py)): Mutect2's `TLOD`, `FS`, `SOR`, `MQ`, `ECNT` and others, and VarDict's `SBF`, `NM`, `MSI` and others; for a key with one value per allele (`MMQ`, `MBQ`, `MFRL`, `RPA`) the first ALT's value under the key and the reference's under `<key>_REF`; `QUAL` when the record has one; `STR` = 1 for Mutect2's short-tandem-repeat flag, and `RU_LEN`, the length of the repeat unit. A Mutect2 tumour-only call has no QUAL: its quality is `TLOD`. The NIPT quality filter, the father's genotype class and the de novo triage read them. |
| `…/SV/entries` | `calls.ps` | Phase set for a structural call |
| `…/SV/entries` | `calls.cn` | Copy number from a call's `FORMAT/CN`: a depth-based CNV caller's (HiFiCNV), and an uploaded Sniffles or Spectre call's (since CLIN-39; before, it was dropped, so an uploaded CNV named no direction). `GT=1/1` on a duplication cannot tell CN=3 from CN=6; the ClinGen CNV dosage scoring needs the actual number, and a CNV's gain or loss is read from it (`sv_copy_change`). Null for callers that report none. |
| `…/INTERVAL/entries` | `value`, `record_id`, `metadata_json` | On a `target_coverage` row (source `coverage_table`), one capture target of a per-target coverage table, at the table's BED coordinates: its mean depth, its gene (the first field of the table's `attribute`), and the table's `attribute`, `median`, `min`, `max`, `proportion_covered` (the percentage of its bases with any coverage) and `zero_coverage_bases`. The NIPT coverage check, the plasma's sex profile and the plasma's depth where it has no call read them ([nipt_target_coverage.py](../backend/app/services/nipt_target_coverage.py)); the track viewers do not draw this track. |

An earlier version also created columns no code read, which this version drops the first time
it ensures an assembly's tables (`_drop_unused_columns`, ENG-6): `liftedOverChrom` and
`liftedOverPos` of `SNV_INDEL/variants/details` (always null), `sample_type` of both `entries`
tables (always `WGS`), `transcript_ids`, `sift_terms` and `polyphen_terms` of
`SNV_INDEL/variants/annotation_index` (written, never read) and `calls.gq` of `SV/entries`
(never written). It reads the assembly's columns from `system.columns` first: a table that
still has some gets one `ALTER TABLE … DROP COLUMN IF EXISTS`, once; a table created without
them gets none. Every other value of every row is kept. A process of the previous version still
names the columns, so an import it runs across the rollout fails at its next write to an
altered table, as an import fails on any failed write. Let a running import end first.

Every value a loader writes is bound to its column first
([clickhouse_column_bounds.py](../backend/app/services/clickhouse_column_bounds.py), applied by
`clickhouse_variant_rows.py` and `clickhouse_interval_tracks.py`). The ClickHouse client refuses
a whole insert when one value falls outside its column. So a value its column cannot hold, or
one that is not a finite number, is stored as missing: a GQ above 65,535 or written `inf`, a
depth above 4,294,967,295, a negative phase set, a QUAL, allele fraction, caller metric or
annotation score beyond the largest `Float32` (about 3.4 × 10³⁸), an SVLEN outside `Int32`, a
copy number above 65,535, a gnomAD count outside `UInt32`.

- A call's allele fractions, one per alternate allele, are stored all or none. One so missing,
  or missing or unreadable in the file, leaves the call without fractions; its allele balance
  is then read from its depths. Leaving out only that one would move each later fraction onto
  the allele before it (DATA-7).
- An allele depth so missing is stored as 0, as a missing or unreadable one already is, so the
  other alleles keep their places.
- The common-variant flag (`is_gnomad_gt_5_percent`) reads each population frequency as its
  column stores it, so one so missing is no frequency over 5 %.
- A record whose position (a POS or END, a track row's start or end) no column holds cannot be
  placed. Its loader skips it as malformed, as it skips a POS that is not a number, and counts
  it with those (the small-variant upload's `skipped_malformed`, a track's `skipped`).
- A contig named by a negative or an enormous number is ranked with the other contigs in
  `xpos`.

The `source` column on both `entries` tables scopes deletes and re-imports, so one family can
hold several callsets side by side:

- Small variants: `clair3` (the primary callset, whatever caller made it: one joint VCF, or the
  long-read pipeline's one VCF per sample read side by side, a site being one row holding the
  call of each sample whose file has a record there; see
  [data-import.md](data-import.md#per-sample-callsets-of-a-long-read-couple)), `glimpse2`
  (imputed; hidden from the diagnostic lists by default), `mito` (chrM) and `nipt` (a monogenic
  NIPT pair: the plasma's and the father's single-sample VCFs, each merged into the callset for
  its own sample, so a variant is one row holding each sample's call where its file has one).
- Structural variants: `needlr`, `hificnv` and `mito_sv` (chrM deletions and duplications, each
  sample's heteroplasmy in the record's annotations) from packages, and `manual_upload`,
  `sniffles` or `spectre` from a direct upload.

The `source` of an interval track (on its `INTERVAL/entries` rows and its
`sample_interval_track_sources` record) scopes its deletes the same way. A sample can hold
several callers' tracks of one type, and each loader deletes and replaces only its own source's
track, rows and record. Sources per track type:

- `coverage`: `web` (a BED upload, and a package's coverage BED), `wisecondorx`, `qdnaseq` and
  `hificnv`;
- `segments`: the same;
- `apcad`: `apcad` (the package's APCAD dataset), `hificnv` (the minor allele fraction) and
  `web`;
- `apcad_pcf`: `pcf` and `web`;
- `apcad_segregation`: `apcad` (the APCAD dataset, from an embryo's segregation table);
- `haplotype`: `glimpse2`;
- `target_coverage`: `coverage_table`.

Only these delete every source's tracks: the admin deletes (a sample's track type, a sample, a
family), a structure edit that clears the family's genomic data, the removal of a family a
failed import created, and a failed overwrite's restore (which writes its backup back).

Deleting a sample (`DELETE /admin/samples/{sample_id}`) rewrites the family's
`SNV_INDEL/entries` from the stored rows: every callset, project and column comes back as
stored, minus that sample's calls. The shared `SNV_INDEL/variants/*` tables are left alone, so
each row keeps its annotation version and annotation-set hash.

An SV delete (`delete_family_structural_variants`) covers a family, or one source of it: the
delete half of a per-sample upload's rewrite and of a package dataset's re-import. It deletes the
scope's `SV/entries` rows first, then the scope's `SV/variants/details` and `SV/key_lookup` rows
whose key no remaining `SV/entries` row of the family has. Unlike the small-variant annotation
tables, these two are not shared between families: an SV's key hashes the family
([Row identity](#row-identity)), every row carries `family_guid`, and every write that keeps a
stored key (a per-sample upload, the admin sample delete, a snapshot restore) writes it back to
the family it read it from. No other family's entry can reach them, so the delete reads only the
family's own entries. After a family delete no entry is left, and all of the family's rows go.

A rewrite of the family's SV rows (`rewrite_family_structural_variants`: a per-sample upload,
the admin deletes of a sample's SVs or of a sample, a package's chrM SVs) deletes its scope and
writes the rows again. It is handed the rows it deletes, as read under the family's lock.

- A write that fails after the delete deletes the scope again, with what it had stored, and
  writes those rows back. One that cannot put them back raises `StructuralVariantsNotRestored`,
  and the caller marks the family for what may be missing
  ([data-import.md](data-import.md#the-mark-of-an-upload-that-did-not-finish)).
- The rewrites of the small-variant rows put back what they deleted the same way: the admin
  delete of a sample (`SmallVariantsNotRestored` when it cannot) and a per-sample upload of a
  package's chrM or NIPT files (`DatasetLeftMissing`, naming each sample whose calls the rows
  held, when it cannot).
- After a source-scoped delete the source's rows go, but a row whose key another source's entry
  still has is kept. Two callers' calls at one position shared a key before the key named the
  source, and `variants/details` keeps one row per key, so deleting it would strip the other
  caller's SV of its span, length and annotation.
- Other sources' and other families' rows are never touched.

`SNV_INDEL/family_data_version` (plain `MergeTree`) is not variant data. Every change to a
family's small variants (an insert, a full or source-scoped delete, and the summary refresh
that also follows a snapshot restore) appends one row with a random token once the write
completes. The count and sum of the family's tokens are its small-variant data version, which
the ranking cache puts in its key. Rows are never collapsed, so the version never returns to an
earlier value. The family page's search reads it before and after it runs, and runs once more
when it moved. Reason: its subqueries (a panel's window join, the annotation and sample
subqueries) read the family's rows apart from its main read, so a write landing between two of
them could make the answer miss a row (ENG-17).

`SV/family_data_version` does the same for structural variants: every SV insert and delete, and
a snapshot restore, appends a token. The SV→gene index stores the version it was built from in
`family_sv_gene_index_status.sv_data_version` and is rebuilt when the family's version has moved.

### One write at a time per family

ClickHouse has no transactions, and a write of a family's variants replaces rows it has read:

- a per-sample upload merges its file's calls into its source's stored rows and writes them
  back;
- the admin sample delete writes the family's rows back without the sample;
- a package dataset replaces its source;
- a failed package import restores the rows it snapshotted.

So each writer holds a transaction-scoped Postgres advisory lock
(`pg_advisory_xact_lock(hashtext('family-variant-writes:<type>:<family uuid>'))`, `<type>`
being `small_variants` or `structural_variants`) from before its first read of the rows until
its transaction ends
([family_variant_write_lock.py](../backend/app/services/family_variant_write_lock.py)). A second
write of the same family and type waits for the first to commit or roll back, in any worker or
process. Readers take no lock. A write that needs both locks takes the small-variant lock first.

| Writer | Holds |
| --- | --- |
| per-sample SV upload (`POST /structural-variants/upload/{sample_id}`) | SVs |
| family small-variant upload (`POST /families/{family_id}/small-variants/upload`) | small variants |
| admin SV delete of a sample (`DELETE /admin/data/samples/{sample_id}/structural_variants`) | SVs |
| admin small-variant delete (`DELETE /admin/data/families/{family_id}/small_variants`) | small variants |
| admin sample and family deletes (`DELETE /admin/samples/{sample_id}`, `DELETE /admin/families/{family_id}`) | both |
| PED overwrite of an existing family; a structure change that clears the family's data | both |
| package import, from before its snapshot until it has finished or restored the family | both |
| per-sample repeat-expansion and interval-track uploads (`POST /repeat-expansions/upload/{sample_id}`, `POST /bed/upload/{sample_id}/{bed_type}`): the import's restore rewrites those rows too | both |
| admin delete of a sample's interval track, every caller's or (`?source=…`) one caller's, or its repeat calls (`DELETE /admin/data/samples/{sample_id}/{track type}`, `…/repeat_expansions`): the import's restore rewrites those rows too, and would undo the delete | both |
| the demo loader (`scripts/load_demo_quartet.py`), for each upload it makes | as the upload's route |

The package import commits after its datasets, so it holds its locks on a Postgres connection
of its own; the dataset loaders it runs take none of their own for the family. That connection
stays idle in its transaction for the whole import, so the server must not end idle
transactions (`idle_in_transaction_session_timeout`, off by default): ending it would release
the locks while the import runs.

An upload or a package import checks, once it holds the lock, that its family and samples are
still stored: when the write before it deleted them, it writes nothing and answers 404.

A single-file upload writes its mark in `families.metadata.import_unfinished` before its first
write to ClickHouse. It writes the mark on a connection of its own, committed while the upload's
transaction holds its locks: an UPDATE of the family row, which waits for no advisory lock. It
writes the mark before its own transaction locks that row (its record of what it brings), since
the mark's write would wait for that lock. A deletion of the family's data writes its mark the
same way, under its locks and before its transaction updates or deletes the family row
(SAFE-31).

### Deletes are synchronous

The `entries` tables are `CollapsingMergeTree(sign)`, but CoGA never uses the collapse: no
writer stores a `sign = -1` row. Every delete or rewrite of a family's rows runs `ALTER TABLE …
DELETE … SETTINGS mutations_sync = 1`, which returns once the old rows are gone, so the readers
that filter `sign = 1` without `FINAL` (the family views, the variant explorer,
`count_cfdna_carriers`) never see a stale row. A new writer deletes synchronously before it
inserts. One that stored cancel rows, or a replacement that relies on a merge to drop the old
row, would need `FINAL` on all of those reads.

### Inserts are synchronous

ClickHouse buffers an insert by default (`async_insert = 1`, waiting for it) and answers only
once a timer of 50 to 200 ms has flushed the buffer. CoGA sends every insert with
`async_insert = 0`, so it is stored as one part when it returns. The setting is the default of
`core/clickhouse.insert_clickhouse`, the one function that hands rows to the driver
(`execute_clickhouse` passes it an `INSERT … VALUES` with its rows, and no other module takes
the driver's client). Every writer sends its rows in batches, one batch at a time, and each
batch had waited for that timer:

| Writer | What it inserts |
| --- | --- |
| `clickhouse_variant_storage` | the small-variant and SV tables, a batch of a thousand records to each of five tables; the family's data version, one row per write; the family summaries; a per-sample upload's rewrite of `entries` |
| `clickhouse_interval_tracks.insert_interval_track_rows` | the interval tracks: a package's coverage, segments, APCAD, PCF and segregation tracks 50,000 rows at a time (`family_package_tracks.INTERVAL_INSERT_BATCH_ROWS`); a BED upload, a capture panel's target coverage, the haplotype blocks and the lineage, each in one insert |

The rows stored are the same either way, and so are the parts: a single client's insert that
waited for the buffer was flushed in a part of its own. Two kinds of insert never used the
buffer: one whose rows exceed `async_insert_max_data_size` (10 MiB), and an `INSERT … SELECT`
(an import's snapshot and its restore, the gene-index rebuild).

A writer's own `settings` override the default. `{"async_insert": 1}` would suit tiny inserts
sent by many requests at once, which ClickHouse can gather into one part. No writer passes it:
ClickHouse is written only by imports, uploads, the lineage precompute (one insert per
pedigree edit, run after the edit), the admin's gene-index rebuild, and the deletes of a
family's data: an admin's (deleting a sample, or a sample's SVs, writes the family's other rows
back), a PED upload or Family Builder save that replaces a family, and a structure change that
clears its data. A writer that needs the buffer is listed here with the reason.

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
  per-sample upload names an SV `chrom-start-end-type---`, a breakend's with its mate
  (`…-BND-5-3000000-3000000`), without its caller, and a package's NeedlR import names its calls
  the same way, never by the record's ID, which its annotation keeps. So a Sniffles, a Spectre and
  a NeedlR call at the same breakpoints have one id but three keys. Each has its own `entries`,
  `variants/details` and `key_lookup` row.
- The variant id does not depend on the callset, with two exceptions whose id names their
  source: every HiFiCNV call, named by its coordinates like the others and never by its record
  ID since CLIN-39, and every mtDNA SV (`build_structural_variant_id(..., source=...)` in
  `family_package_variants.py`). Reviews, classification evidence snapshots, the ranking cache
  and the SV→gene index attach by the id, so they cover every callset's row of a variant.
- Two calls of one position, type and mate with other alleles or another length (insertions in
  particular) are two SVs: one keeps the id, each other one gets a suffix, its length
  (`…-INS----7`), then `.2`, `.3` (`clickhouse_variant_ids.event_discriminators`). Which one keeps
  the id follows the events, not the lines: the one this source already stores under it, else
  the shortest (then by REF and ALT), so a file read in another order gives each event the id it
  had. A NeedlR import reads its files together; an upload, which stores no alleles, matches a
  call to the SV of its length (CLIN-39).

When several callsets hold one small variant, the diagnostic lists show the direct call; an
imputed callset shows only when it is asked for. Reviews and the drift check read the direct
call, and among several direct callsets the one first in name order. The SV list shows an SV
once per caller, each row with that caller's calls.

#### Tables with a different row identity

A table's sort key is fixed when ClickHouse creates it. At startup, and before it creates an
assembly's tables, the backend compares each table's sort key with the one in the list above.
If a table has another key, the backend refuses to start; the message names the table, its sort
key and the one it needs. The rows are not copied into a new table: under the other key, merges
may already have dropped calls, and only a re-import brings them back.

An `SNV_INDEL/entries` table without the per-call columns `calls.filters` and `calls.metrics`
(created before they existed) is refused the same way, at the same two points. The message
("Refusing to use ClickHouse variant tables created by an earlier version") names the table and
the columns it lacks. The backend adds no column to a stored table, and a re-import is what
fills the columns, so the recovery is the same. The one change it makes to a stored table is to
widen the depth columns `calls.dp` and `calls.ad` to `UInt32` ([above](#clickhouse-tables)).

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
- `repeat_expansion_reviews` and `paraphase_reviews` attach it to a family's repeat loci and
  Paraphase regions in its assembly (`families`, `assemblies`; both deleted with them)

## Startup Behavior

When `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP` is true (the default), every start migrates
the Postgres schema (until the release candidate it re-applies the five baseline files, then
runs the revisions the ledger lacks: [Schema Migrations](#schema-migrations)) and makes sure the
admin user exists. With the restricted runtime role a separate migration step
(`backend/app/db_migrate.py`) does this instead, as the table owner, and the start refuses to go
on unless the database is at this version's own schema revision
([A start that does not migrate](#schema-migrations)).

In ClickHouse, startup creates the database and refuses to go on when an assembly's tables have
a different row identity, or its `SNV_INDEL/entries` table lacks the per-call columns
([Row identity](#row-identity)). Each assembly's tables are created at first use; an
`SNV_INDEL/entries` table created with the narrower depth columns is widened then
([ClickHouse Tables](#clickhouse-tables)). The full startup order is in
[application-scheme.md](application-scheme.md), "Startup and background work"; the reference
data it loads is listed in [data-import.md](data-import.md), section 1.

### Exploratory ancestry PCA

Seven tables in `04_traceability.sql` hold the exploratory ancestry PCA, which is outside the
intended purpose and never enters a sign-out or the signed record ([ancestry-pca.md](ancestry-pca.md)).
A projection reads the family's stored small variants in ClickHouse at the reference markers;
revision 0016 (2026-11-04) gave the runs and results what that needs.

| Table | Holds |
| --- | --- |
| `ancestry_reference_panels` | Append-only public reference snapshots keyed by the model NPZ's SHA-256: metadata, PC variance, population labels, the exome mask's checksum and definition, and reference coordinates |
| `ancestry_runs` | Project-scoped jobs pinning the reference checksum: a user's (`trigger = 'user'`, with `requested_by`) or the worker's own once the family's genotypes changed (`trigger = 'automatic'`, no requester; a check makes a user's run name its requester). `storage_version` is the family's small-variant data version the run was queued for, then the one it read: the worker's sweep queues no second run for a version. Queued, running, completed, failed or interrupted |
| `ancestry_results` | One append-only snapshot per completed run: each individual's four PCs or why there are none, the callset it was read from and how (genome, exome, a call at every marker), every callset's scan, the `storage_version` read, the calculation version and the rules, with a digest of the rows read |
| `ancestry_sample_choices` | Append-only choices of how one individual is read, the latest per individual holding: CoGA's own reading (`auto`), one callset (`callset`, which also includes an embryo) with the regions it covers, or left out (`exclude`). `regions` holds the regions' source (a gene panel's id and version, or a BED file's name and SHA-256), their interval count and the reference markers inside them (`loci`); never the BED file. A new table: its baseline only |
| `ancestry_files` | Append-only VCF files a family package declared for ancestry without importing them (a gVCF): the family, project, import job, package and relative path, and label. Of the declarations of one path the latest counts. A new table: its baseline only |
| `ancestry_file_scans` | Append-only scans of a declared file: `scanned` (its SHA-256 and size as read, its sample columns and its calls at the reference markers, `rows`) or `failed` (a fixed reason), and the reference whose markers were kept. A calculation reads the latest scan, never the file. A new table: its baseline only |
| `wgs_sources` | Append-only WGS file sources that projections read before revision 0016 (a retained upload by `raw_file_id`, a family package by its import job), with their checksum, the stored genotypes' version and fingerprint and the absence and unfiltered-call policies. A run or result from before then names one in `source_id`; nothing writes a source any more |

`wgs_sources`, `ancestry_reference_panels`, `ancestry_results`, `ancestry_sample_choices`, `ancestry_files` and `ancestry_file_scans` have mutation-blocking triggers,
and `coga_app` may not UPDATE, DELETE or TRUNCATE them (`05_grants.sql`). A running job holds its
own row lock until it ends; a job whose process stopped loses it, and the next worker ends it as
interrupted. Publication is checked again under the family's variant-write locks, so a family,
genotype, member, choice or permission change in between publishes nothing. Earlier projections keep
their own reference snapshot.
