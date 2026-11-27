# 4. Initial deployment & seeding

This chapter describes how CoGA goes from an empty system to a running platform, in the Docker setup and on Google Cloud with Terraform: how the database structures come into being, how the first administrator and the reference data are *seeded* (filled for the first time), and in which order the backend does this at startup. The step-by-step guides are `docs/development.md` (local) and `docs/deployment-gcp.md` (Google Cloud).

A few terms. A **container** is an isolated, running software package; **Docker Compose** starts several containers together from one file. **DDL** is SQL that creates or changes tables.

## From zero to a running stack

CoGA consists of five containers: Postgres (metadata and review state), ClickHouse (the variant storage), the FastAPI backend, the import worker (the same backend image, which runs the package imports) and the React frontend.

### The Docker setup

`docker-compose.yml` describes the five services. Some choices matter for traceability and robustness:

- **Pinned images.** The database images are pinned not only by a tag but by a **digest** (`@sha256:…`). That way every machine uses exactly the same image.
- **Health and order.** The backend starts only when Postgres and ClickHouse are healthy; the frontend and the import worker only when the backend is healthy. The backend gets plenty of time, because at startup it applies the schema and loads reference data. The import worker migrates nothing itself and only checks the schema revision.
- **Graceful shutdown.** ClickHouse gets a generous shutdown period: a write that is interrupted in the middle of its work can damage data parts.
- **Restarting.** Every service has a restart policy, so that the setup runs again by itself after a restart of the server. Anyone who first stopped the setup with `docker compose stop` starts it again themselves with `docker compose up -d`.
- **Build identity.** The backend image receives `APP_VERSION` and `GIT_SHA` at build time. A local build is not a release: without `APP_VERSION` the image reports the version from `VERSION` with `dev` added (`0.1.0-dev`). `.env.example` warns: do not set it in `.env`, otherwise the runtime overrides the baked-in value and falsifies the version that is frozen in every signed report. Outside development the backend does not start without `GIT_SHA` (7 to 40 hexadecimal characters), because every signed report states that commit. Of `scripts/`, the image contains only what the backend itself runs: `clinical_cnv_knowledgebase.py` (the build of the clinical CNV knowledge base: from Admin, after an assembly import and at startup, below) and `import_dgv.py` (an import by the administrator). The test seeders stay out; one of them creates a login with a default password.

**Where in the code:** `docker-compose.yml`; the warning in `.env.example`.

### The development setup

For development a second file goes on top of the base:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

`docker-compose.dev.yml` sets `APP_ENV=development` and mounts the source code into the containers, so that changes take effect immediately. It starts the backend with automatic reloading and the frontend with the Vite development server. `APP_ENV=development` also switches off the check for weak secrets, so that you can work locally with the placeholders of `.env.example`. Outside development the backend would then refuse to start; the full rule is in [chapter 2](02-security-roles-permissions.md#refusal-to-start-with-weak-secrets).

**Where in the code:** `docker-compose.dev.yml`; the check in `validate_security_defaults` in `backend/app/core/config.py`.

## Creating the database structures

### Postgres

The Postgres schema is in five SQL files (`01_access.sql` to `05_grants.sql`, chapter 3). The backend reads them in name order, splits each file into separate statements and runs everything in one transaction. The splitter takes *dollar quotes* (`$$ … $$`) into account, so that a semicolon in a trigger function does not cut a statement off halfway. This happens only on an empty database, by revision `0001`, which also records the digest of the baselines in `schema_baselines`. The migration step refuses a database that this version did not make (tables without a ledger, an unknown revision, other baselines). The log reports the revision the database is at (chapter 3).

**Where in the code:** `init_postgres_schema` in `backend/app/core/postgres.py`; the migration step `apply_postgres_schema` in `backend/app/core/postgres_migrations.py`; the revisions in `backend/db/migrations/versions/`.

### ClickHouse

The ClickHouse file only creates the database; the database name comes from the setting `CLICKHOUSE_DATABASE`. The backend creates the variant and track tables per assembly only when they are first needed (chapter 3).

**Where in the code:** `init_clickhouse_schema` in `backend/app/core/clickhouse.py`.

### Two ways to apply the schema

Who may create the schema is deliberately kept separate from who runs the app (the database rights from chapter 2):

| Setting | Who runs the DDL | When |
| --- | --- | --- |
| `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=true` (default) | The app itself, at startup, as owner of the tables | The current setup with one database login |
| `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP=false` | A separate step (`backend/app/db_migrate.py`) as owner; the app then runs as the restricted role `coga_app` | When the runtime may not run DDL |

Both paths use the same functions: there is one source of truth for the schema. The migration step refuses to run as `coga_app`, before it changes anything. If `POSTGRES_APP_PASSWORD` is set, the migration step also enables the login of `coga_app`. The password is not sent to the database in plain text: the step sends a SCRAM-SHA-256 derivative, as `\password` in `psql` does, and the password appears in no log line. On Google Cloud this step runs as the Cloud Run job `coga-db-migrate` (`terraform/migrate.tf`), as soon as `db_runtime_role = "coga_app"`.

An app that does not migrate itself reads only the migration ledger and the baseline digest at startup. It starts only if the database is at exactly its own schema revision and was made from its own baselines. If the database has no revision in the ledger, the app reports that the migration step must run first. If the database is at a revision the app does not know, or comes from other baselines, then another version made or migrated it (chapter 3). ClickHouse always follows the app's startup path.

**Where in the code:** `backend/app/db_migrate.py`; the settings in `backend/app/core/config.py`; the procedure in `docs/db-runtime-role-runbook.md`.

## Seeding: administrator and reference data

### The first administrator

`init_postgres_admin_user` creates the first administrator from `ADMIN_USERNAME`, `ADMIN_PASSWORD` and `ADMIN_EMAIL`, with the role `admin`. If that user already exists, the function replaces only the stored hash, and only if it does not match `ADMIN_PASSWORD`: whoever rotates the secret thus rotates the password (chapter 5). It leaves the user's role as it is; only with the role `admin` is that user the emergency administrator (chapter 5). An account with that name that is linked to a Microsoft account is left untouched. The password is never stored in plain text: the backend hashes it with bcrypt, which it calls directly (chapter 5). The function also works under the restricted role `coga_app`, which deliberately keeps `INSERT` and `UPDATE` rights on `users`.

**Where in the code:** `init_postgres_admin_user` in `backend/app/db_migrate.py`.

### The reference genome GRCh38

Without an organism and an assembly there is no coordinate system for variants. At startup the backend makes sure that *Homo sapiens* with assembly **GRCh38** exists, with cytobands and genes. If either of the two is missing, it fetches them: cytobands from UCSC, genes from the GENCODE annotation (a GTF file, via `REFERENCE_GENCODE_GTF_URL`). It reads the cytobands as UCSC publishes them that day, and records the file with its name and SHA-256. If UCSC cannot be reached, it loads the backup copy that CoGA ships (`data/ref-data/cytobands/`, for hg38 and hg19), only with the pinned SHA-256 (`CYTOBAND_BACKUP_FILES`); that way the genes still load too, because they come in the same import. If GENCODE fails, it falls back on a UCSC gene table and records that. If the whole download fails, it creates only the organism and the assembly (with version `hg38`): the platform stays usable, and the genes can be imported later. A following start completes that assembly, and an import from UCSC on the *Organisms and assemblies* page updates it. Both keep the same row and take over the version (the GRC patch, e.g. `p14`) and the release date that UCSC gives at that moment. There is never a second GRCh38, not even when UCSC moves to a later patch: an assembly is its name (chapter 3). A strict check on the genome name prevents it from being misused in a download URL (*server-side request forgery*). `REFERENCE_BOOTSTRAP_ENABLED=false` switches all this off.

**T2T-CHM13v2.0** can optionally be added as a second assembly (`REFERENCE_BOOTSTRAP_T2T=true`; off by default). Its annotation is poorer: UCSC's RefSeq-derived GTF provides coordinates, but no biotypes, Ensembl ids or MANE labels, and there is no cytoband table. If that import fails, it is never fatal.

The import names the assembly as UCSC's description does: `T2T CHM13v2.0`, with a space (`hs1` if that description gives no name). Its ClickHouse tables are called `T2T_CHM13v2.0`; NCBI writes `T2T-CHM13v2.0`. CoGA recognises T2T-CHM13v2.0 under each of those names, with one rule in the backend and one in the frontend, in upper or lower case: `T2T-CHM13`, `T2T CHM13` or `T2T_CHM13`, with or without `v2.0`, `CHM13v2.0` and `hs1` (`is_t2t_chm13` in `backend/app/services/sex_chromosomes.py`, next to `grc_build`; `isT2tChm13` in `frontend/src/lib/reference.ts`). Another CHM13 release (v1.0, v1.1) or another telomere-to-telomere genome does not count: hs1 is v2.0. That rule chooses the UCSC link (`hs1`) and the IGV genome, puts T2T after GRCh38 among the loci of a gene and the regions of a panel, and gives the STRchive coordinates at which a repeat call is placed. T2T is outside the validated scope (GRCh38 only).

CoGA also recognises GRCh38 and GRCh37 with one rule, `grc_build` (`grcBuild` in the frontend): GRCh38 or hg38, GRCh37 or hg19, in upper or lower case, with or without a patch suffix such as `.p14`. The UCSC import names an assembly `hg38` if UCSC's description gives no name. That rule chooses the gene page's links to UCSC and gnomAD (`hg38` and gnomAD v4 for GRCh38, `hg19` and gnomAD v2.1 for GRCh37). The UCSC and gnomAD links of the variant list and the hg38 locus of the gene page read the assembly by its name, and otherwise by its version, in each case as a whole. A name the rules do not know, such as `GRCh38_no_alt`, gets no UCSC link and no gnomAD release, just as IGV opens no genome for it.

Where a project, a family, a gene or the explorers name an assembly, they show its build (`GRCh38`, `GRCh37`) or, for another assembly, its name, never its version (`assemblyDisplayName` in `lib/reference.ts`). The version is the release of the reference data that CoGA loaded (UCSC's hg38 is `GRCh38.p14`), not the reference to which a family's data were aligned. A patch adds scaffolds and leaves the coordinates of the chromosomes untouched; the FASTA the pipeline aligned to (an analysis set, with or without ALT contigs, decoys or masked regions) is the family's, and is in its annotation manifest as its reference assembly (chapter 6). A list for choosing an assembly adds the version only where two assemblies would have the same name. The Reference data page shows the version, as the release of the reference data. The `assembly` of a package file is compared by build with that of the project (`same_assembly` in `services/sex_chromosomes.py`).

The loci of a gene, the regions of a panel and the sizes in the panel list are in one order: GRCh38, T2T-CHM13, GRCh37, and then the other assemblies by name (`assembly_display_rank`; `compareAssemblyNames` in the frontend). The gene page opens on the first one that has the gene.

**Where in the code:** `backend/app/services/reference_source_service.py` (`ensure_human_grch38_reference_on_startup`, `ensure_human_t2t_reference_on_startup`); the name rules and the order in `backend/app/services/sex_chromosomes.py` (`grc_build`, `is_t2t_chm13`, `assembly_display_rank`) and `frontend/src/lib/reference.ts`.

### Built-in tracks and the repeat catalogue

- **Segmental duplications** are loaded from a file (`REFERENCE_SEGMENTAL_DUPLICATIONS_PATH`), only if that dataset is still empty for the assembly. The file must be byte for byte the shipped ClinGen file (release V2.1 for GRCh38; `SEGMENTAL_DUPLICATIONS_PINNED_SHA256`). The startup does not load any other file: it logs one error with the file, both SHA-256 values and the fix, and continues without that track. The file holds GRCh38 coordinates, so the startup loads it only into the assembly that `REFERENCE_BOOTSTRAP_ASSEMBLY_NAME` names (GRCh38 by default), if `grc_build` reads it as GRCh38 (above; the same rule chooses the pseudo-autosomal regions of GRCh38 and GRCh37). If the setting names another assembly, such as T2T-CHM13 or GRCh37, the startup loads nothing and logs one warning with that assembly and the fix: an administrator uploads that assembly's own file through the Reference data page (chapter 15).
- **Clinical CNVs, DGV and ClinVar.** If the assembly `GRCh37` or `GRCh38` does not yet have clinical CNVs, DGV variants or a ClinVar release, the startup queues the build of the knowledge base, the load of the pinned DGV release (2025-12-01) and the load of the newest weekly ClinVar release, without an account (`queue_startup_reference_loads`; `REFERENCE_BOOTSTRAP_CLINICAL_CNVS`, `REFERENCE_BOOTSTRAP_DGV`, `REFERENCE_BOOTSTRAP_CLINVAR`). That happens only once ClickHouse is ready, as the ClinVar release is stored in ClickHouse. An assembly import from UCSC does the same, as the administrator who imports it. Those jobs run in the background and never hold up the startup. Without the build script (`CLINICAL_CNV_KB_SCRIPT_PATH`) the build of the knowledge base does not start. An assembly that already has them gets nothing new: an administrator starts a rebuild or another release on the Reference data page (chapter 15). With `CLINVAR_WEEKLY_LOAD` (on by default) CoGA also looks every day itself whether there is a newer ClinVar release (chapter 15).
- **The repeat catalogue** combines a built-in list of loci with the STRchive loci (`data/ref-data/STRchive-loci.json`). The built-in loci carry the thresholds of their STRchive row, so that the status of a call does not depend on which row finds its TRID. Every start writes both again (updated per `locus_id`), so a changed threshold applies from the next start. Calls already imported keep their stored status on the genome tracks; the repeat page re-assesses them immediately (chapter 8). The seed never writes QA's count correction per locus (`repeat_locus_count_units`, chapter 8): a start keeps it.
- **The STRchive release is pinned** (decision of the owner, 2026-10-14). The source is the shipped release: the file in the repository, with SHA-256 `16301ecb0023…` (82 loci). It is STRchive release v2.28.0 (8 October 2026), byte for byte identical to the `STRchive-loci-v2.28.0.json` that STRchive published with that release: the SHA-256 is the digest the release states for that file (`STRCHIVE_PINNED_RELEASE`). STRchive's thresholds are CoGA's cut-offs, as the release gives them, without CoGA's own validation per locus (decision of the owner, 2026-11-16). The file itself does not name a release, so the SHA-256 is its identity. `STRCHIVE_PINNED_LOCI_SHA256` fixes it completely; no setting can replace it. The startup reads the file from `TRGT_STRCHIVE_LOCI_PATH` (on Cloud Run the reference data bucket), or the copy in the repository if that file is not there. If the file read is not byte for byte the shipped release, or if there is no file, the backend does not start. It then writes nothing and logs one error with the file, both SHA-256 values and the fix (copy `data/ref-data/STRchive-loci.json` unchanged). An identical copy is accepted, wherever it is. Every start also removes the STRchive rows of `repeat_loci` that the release does not know, such as the loci of another release that an earlier start wrote. Moving to another release is a reviewed change: the file, the pin and the thresholds of the built-in loci change together (`docs/data-import.md`, section *The STRchive release*).

**Where in the code:** `seed_builtin_reference_tracks` in `backend/app/services/reference_metadata_service.py`, with `grc_build` in `backend/app/services/sex_chromosomes.py` (which assembly is GRCh38); `seed_builtin_repeat_catalog` and `read_shipped_strchive_release` in `backend/app/services/repeat_expansion_pg.py`; the pin (`STRCHIVE_PINNED_LOCI_SHA256`) in `backend/app/core/config.py`.

### The HPO ontology

If there are no HPO terms in the database yet, the backend imports the **newest HPO release** (decision of the owner, 2026-11-05). It reads from HPO which release appeared last (HPO's latest GitHub release) and downloads it from its own PURL, a URL that does not move along with new releases (`https://purl.obolibrary.org/obo/hp/releases/<date>/hp.obo`). Every request, every redirect included, goes over HTTPS. The file must be an OBO file that states that release, otherwise the backend refuses it. It stores the release and the SHA-256 of the file with every term. On a database that already has terms, a start changes nothing: an administrator applies a newer release (chapter 12).

The file in the repository (`data/ref-data/hpo/hp.obo`, release 2026-02-16) is the **fallback**. The backend uses it if HPO cannot be reached (also if GitHub's limit of 60 requests per hour is reached), if it refuses the downloaded file, or if the download is switched off (`HPO_DOWNLOAD_LATEST_ON_STARTUP=false`, for an installation without internet). It then logs one warning with what went wrong and the fallback release. It imports the fallback only if its SHA-256 is that of the pinned file:

- **A file that is already in place** (`HPO_ONTOLOGY_PATH`, on Cloud Run the bucket) with a different SHA-256 is refused with one error in the log (the file, both SHA-256 values and the fix). The backend then imports nothing and does not download anything in its place either. The startup continues; the HPO terminology stays empty until a release has been loaded.
- **Only if there is no file** and the download is switched on does it download the fallback release from its own PURL. Any other file, e.g. a later release, it refuses before anything is written: it logs an error and imports nothing.

Two installations that start at different times can therefore begin with a different HPO release; the administration page shows which one is loaded. The release and SHA-256 of the fallback are fixed in the code; no setting changes them. A different fallback comes only through a reviewed code change. The release and the date of the ontology are stored along with it, so that it can be seen later which version an analysis used (chapter 12).

**Where in the code:** `ensure_hpo_ontology_on_startup` in `backend/app/services/hpo_service.py`, with `fetch_latest_hpo_release` and `download_hpo_release` in `backend/app/services/hpo_release_download.py`; the release and SHA-256 of the fallback (`HPO_PINNED_RELEASE`, `HPO_PINNED_ONTOLOGY_SHA256`) in `backend/app/core/config.py`.

### The gene reference (dbNSFP)

The enriched gene information (aliases, ids, constraint, disease associations) is loaded by a background job, not by the startup. At startup the backend queues a first job if that is switched on (`GENE_REFERENCE_BOOTSTRAP_ON_STARTUP`), there are GRCh38 genes and the cache `gene_info` is still empty, **also without the local dbNSFP gene file** (decision of the owner, 2026-11-05). That file is not in the repository. The other sources (HGNC, ClinGen, GenCC, ClinVar) are always the current export. Without dbNSFP each gene costs one request to NCBI, so the job takes hours, and the dbNSFP fields (constraint, orthologues) stay empty. If the file is there later, while no gene in the cache was synchronised with it, the next start queues another job that adds it. After that, a new synchronisation is an administrator's task. A separate worker runs the job; chapter 13 describes how.

**Where in the code:** `backend/app/services/gene_info_jobs_pg.py`.

## The startup order

FastAPI has a *lifespan*: a function that runs once at startup and once at shutdown. In `backend/app/main.py` it does the following, in this order:

1. Wait until Postgres can be reached.
2. **Only if** `POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP` is switched on: migrate the Postgres schema (an empty database from the baselines, a database this version did not make refused, then the revisions the ledger lacks) and create the first administrator. If it is switched off (the restricted role `coga_app`), the backend only checks that the ledger names exactly its own schema revision and the digest its own baselines. Otherwise it does not start; the message is also in the log.
3. Start the background writers for the audit log and the UI events, so that everything after this is already logged.
4. In one Postgres session: seed the repeat catalogue, ensure GRCh38, optionally import T2T-CHM13, load the HPO ontology, load the newest Monarch release if there is no Monarch data yet (chapter 12), seed the built-in tracks and, if needed, queue the first gene reference job.
5. Wait until ClickHouse can be reached and apply the ClickHouse schema: create the database if it is missing. The tables per assembly follow when they are first needed (chapter 3).
6. Remove the backup tables of `overwrite` imports that no running import owns, left behind by an import whose process stopped (chapter 6). An error here does not hold up the startup. Then queue the rebuild of the clinical CNV knowledge base, the DGV load and the ClinVar load for a GRCh37 or GRCh38 assembly that lacks them (above).
7. Start the ClickHouse integrity monitoring. It checks the variant tables shortly after startup and then at a fixed interval, and logs an error if it finds damage (chapter 11).
8. Start the gene reference worker and the package import workers (their number via `FAMILY_IMPORT_WORKER_COUNT`). With `0` the backend starts no import workers and leaves the jobs to import worker processes, as under Docker Compose (below). Only with import workers does it also start the worker of the exploratory ancestry PCA, the variant carrier worker (which keeps the carrier summary of the Variant Explorer up to date, chapter 3) and, if `FAMILY_IMPORT_WATCH_ROOTS` names a folder, the watcher of the import folders (chapter 6).

At shutdown the backend stops all these workers cleanly and closes the database connections.

**Where in the code:** the function `lifespan` in `backend/app/main.py`; described in `docs/application-scheme.md`, section *Startup and background work*.

### The import worker in its own process

A Python process uses one processor core for all its work, however many workers it runs. If all import workers ran in the backend, all imports of a large batch of families would share that one core, with the event loop that answers the users' requests. That is why `python -m app.import_worker` can run the import workers in a process of their own, without an API: it answers no requests and listens on no port. Docker Compose runs it as the service `import-worker`, from the same image and with the same settings and the same `/data` folder as the backend. There, the backend itself gets no import worker (`FAMILY_IMPORT_WORKER_COUNT=0`). Each replica is one process, which runs `FAMILY_IMPORT_WORKER_COUNT` jobs at a time (1 by default). `IMPORT_WORKER_REPLICAS` (1 by default) determines how many replicas run, and so how many families are imported at the same time, each on its own core. On Google Cloud the imports run in the backend: `terraform/` has no separate worker service.

At startup the process checks what the backend checks before starting its workers, and nothing else:

- it waits for Postgres and does not start if the database has a different schema revision (it never migrates);
- it waits for ClickHouse and creates the database if it is missing;
- it removes the backup tables of `overwrite` imports that no running import owns;
- then it starts its workers: the import workers, the worker of the exploratory ancestry PCA, the variant carrier worker and, if `FAMILY_IMPORT_WATCH_ROOTS` names a folder, the watcher of the import folders (only one watches at a time, in whichever process).

On SIGTERM (`docker compose stop`) or SIGINT it stops its workers as the backend does at shutdown, closes the database connections and exits with code 0. An import that was running stops where it is; the next worker takes over the job as soon as its heartbeat is ten minutes old (chapter 6). A start that fails, or a worker that stops by itself, ends with code 1, and Compose starts the process again.

What the workers of all processes share goes through Postgres and therefore holds across processes and hosts: claiming a job (`FOR UPDATE SKIP LOCKED`), its heartbeat (which the database's clock sets and judges), ending a job with a stale heartbeat, and the write locks on a family's variants (advisory locks on the import's own connection). A worker logs every job it claims, with its worker id, which begins with the process id: a job's record names its worker only while the job is running. In the development setup the import worker does not reload on a code change, so that an edit does not stop a running import. `docker compose restart import-worker` picks up a change.

**Where in the code:** `backend/app/import_worker.py`; the service `import-worker` in `docker-compose.yml` and `docker-compose.dev.yml`; described in `docs/application-scheme.md`, section *Import-worker processes*.

## Google Cloud with Terraform

The folder `terraform/` builds one CoGA environment in a Google Cloud project. In outline:

- **Network and storage.** A private network without public IP addresses for the databases. Cloud SQL (Postgres) and a ClickHouse VM with encrypted disks and daily snapshots. Storage buckets for the import packages and the reference data, both read-only for the app and mounted as a folder in the backend: the `imports/` folder of the PHI bucket at `/data/imports` (`FAMILY_IMPORT_ROOTS` and `FAMILY_IMPORT_WATCH_ROOTS`, one folder per project), the reference data at `/data/ref-data`. No browser reads a bucket: the backend serves the reads, the signal tracks and the QC report itself. Everything is encrypted with a customer-managed key (CMEK). An optional block on outgoing traffic for the ClickHouse VM (`clickhouse_restrict_egress`, off by default) is in `terraform/egress.tf`. The ClickHouse VM gets the same per-query memory limits as the Docker setup, from the same file (`clickhouse/users.d/memory-guards.xml`): at most 4 GB per query, and GROUP BY and ORDER BY write to disk from 2 GB.
- **Application.** Backend and frontend run on Cloud Run and can be reached only through the external load balancer. The load balancer handles TLS and sends `/api` to the backend. Cloud Armor (on by default) adds DDoS protection, rate limiting per IP, an optional list of allowed IP ranges and a web application firewall (OWASP CRS). By default that web application firewall only logs, and it blocks only with `cloud_armor_waf_enforce = true`. The job `coga-db-migrate` (`terraform/migrate.tf`) applies the schema when the app runs as `coga_app`. Before every schema migration that a new backend image brings (that job, or in owner mode the first start of the new revision), `terraform apply` takes an on-demand backup of the Cloud SQL database with gcloud and waits until it is complete. That is the restore point if the migration has to be reversed. If the backup fails, the rollout stops before the migration and the running version keeps working. For this the deploy account needs a role of its own from the central infra repo (`cogaPreMigrationBackup`).
- **What Terraform does not do.** This configuration does not itself create the CMEK key or the service accounts, nor does it enable the Google APIs; it only refers to them. At CMGG the central infra repo provides them (template: `terraform/main-repo-reference/coga-prerequisites.tf.example`). That way the CoGA rollout cannot give itself extra rights. For a standalone project, `docs/deployment-gcp.md` describes those steps (§5.2 and §5.4).
- **Secrets.** Terraform creates only the *containers* in Secret Manager (`coga-secret-key`, `coga-integrity-anchor-key`, `coga-admin-password`, `coga-postgres-password`, `coga-clickhouse-password`, `coga-postgres-app-password`, and for the monitoring `coga-metrics-token`). You add the values separately (`docs/deployment-gcp.md` §5.5), so that they are never in Terraform variables. None of them ends up in the Terraform state or the plan. Terraform does read `coga-postgres-password` on every plan and apply, to set the owner's password in Cloud SQL. It passes it on through a write-only argument: the state stores only the secret's version number, so that a new version changes the password at the next apply. Still keep the state bucket private: it describes the whole environment. The values must meet the rule from chapter 2: `SECRET_KEY` has at least 32 characters, and the anchor key is the base64 of exactly 32 random bytes (`openssl rand -base64 32 | tr -d '\n'`) and differs from `SECRET_KEY`.
- **TLS for ClickHouse.** Terraform creates this itself: its own certificate authority and the server certificate, in `coga-clickhouse-tls-key` and `coga-clickhouse-tls-cert`. Every run creates the private keys anew, and neither enters the Terraform state or the plan. The server key goes to `coga-clickhouse-tls-key`; the certificate authority's key is kept nowhere. Because the certificate authority's key is gone after that run, Terraform always issues the certificate authority and the server certificate anew, together; the ClickHouse VM must then restart (`docs/deployment-gcp.md` §12.4). Terraform creates a new version of those two secrets before it destroys the old one, so that a VM that starts during the apply always finds a key and a certificate. That requires Terraform 1.11 or later and the google provider 8.x.
- **Monitoring and alerts.** `terraform/monitoring.tf` places a collector next to the backend that reads `/metrics` every minute, with a token from `coga-metrics-token`, and sends the figures to Google Cloud Managed Service for Prometheus. Alert rules report by email to `alert_notification_emails`, among other things, a damaged variant database, lost audit rows, a stuck import and an unreachable app. An uptime check fetches `/api/health` every minute through the load balancer. For this the deploy account needs the Monitoring roles from `terraform/main-repo-reference/rollout-checklist.md`. What each metric and each alert means is described in `docs/monitoring.md`.
- **The path of a request.** A user opens `https://coga.cmgg.be` → the load balancer (TLS and Cloud Armor) → Cloud Run → the backend. The backend reaches Cloud SQL through the Cloud SQL connector (encrypted, over the private network) and ClickHouse over HTTPS with its own certificate authority. The first login uses the email address of the first administrator (`ADMIN_EMAIL`, set by Terraform to `admin@<app_domain>`; signing in always uses an email address) and the password from `coga-admin-password`. With Microsoft sign-in, that is possible only on `/login/emergency`, as long as `AZURE_ADMIN_OVERRIDE` is switched on (chapter 5).

Some IVDR obligations remain a matter of process and fall outside the code, such as change control and an updated DPIA, given that Google hosts the data. Whether Google is a processor or a sub-processor is for the owner to decide (`docs/deployment-gcp.md` §13).

**Where in the code:** the `.tf` files in `terraform/`; the concise reference in `terraform/README.md` and the full guide in `docs/deployment-gcp.md`.

## Security and traceability during rollout

- **Secrets.** Outside development the backend does not start with placeholder or weak secrets (chapter 2). On Google Cloud they come from Secret Manager and are put into the container only at startup, not into images. The Terraform state holds only a reference to them (above).
- **Encryption in transit.** Postgres through the Cloud SQL connector; ClickHouse over HTTPS, with a certificate that the backend verifies (`CLICKHOUSE_SECURE`, `CLICKHOUSE_VERIFY`, `CLICKHOUSE_CA_CERT`). At rest, CMEK encrypts Cloud SQL, the disks and the buckets.
- **Restricted database role.** `coga_app` has no DDL rights and no `UPDATE`/`DELETE` on the append-only tables. By default the app still runs as owner, until the switch-over from chapter 2.
- **Reproducibility.** The base, database and collector images are pinned by digest. CoGA's own images get a tag with the release or the commit, and are rolled out at the digest their build pushed: the running revision is exactly the built image. A release is built only from a commit on `main`, and its SBOMs are attached to the release with their SHA-256. `APP_VERSION` and `GIT_SHA` are baked in at build time and frozen in every signed report. The dbNSFP file, the ACMG SF list and the GENCODE edition are pinned reference versions. Of HPO only the fallback release is pinned: a new installation loads the newest release and stores which one it is (see *The HPO ontology*). Every cached gene record notes for each source which edition supplied it (chapter 13).
- **Audit from the start.** The audit writer starts before the seeding, and the ClickHouse monitoring runs from startup onwards.

## Key files

| File | Role |
| --- | --- |
| `backend/app/main.py` | The startup order (`lifespan`) |
| `backend/app/import_worker.py` | The import workers and their background workers in a process of their own |
| `backend/app/db_migrate.py` | Schema migration as owner, first administrator, login of `coga_app` |
| `backend/app/core/config.py` | All settings and the check for weak secrets |
| `backend/app/core/postgres.py` · `backend/app/core/clickhouse.py` | Applying the schema; connections (including the Cloud SQL connector and TLS to ClickHouse) |
| `backend/app/core/postgres_migrations.py` · `backend/db/migrations/` | The Postgres migration step and the Alembic revisions |
| `backend/app/services/reference_source_service.py` | GRCh38 (and optionally T2T): organism, assembly, cytobands, genes |
| `backend/app/services/reference_metadata_service.py` | Built-in tracks at startup (segmental duplications) |
| `backend/app/services/reference_auto_load.py` | The build of the clinical CNVs, the DGV load and the ClinVar load that the startup and an assembly import queue |
| `backend/app/services/hpo_service.py` · `gene_info_jobs_pg.py` | HPO ontology; the gene reference job |
| `docker-compose.yml` · `docker-compose.dev.yml` · `.env.example` | The Docker setup and all settings with their default value |
| `terraform/` · `docs/deployment-gcp.md` | Google Cloud infrastructure and the rollout guide |
