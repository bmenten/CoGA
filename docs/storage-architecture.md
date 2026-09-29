# Storage Architecture

CoGA uses a split storage model:

- `Postgres` stores metadata, authorization scope, user state, panels, repeat expansions, gene cache, and interval-track source metadata.
- `ClickHouse` stores variant records for small variants, structural variants, and high-volume interval tracks.

## Postgres

Primary responsibilities:

- Users and project access
- Species, assemblies, chromosomes, genes, blacklist, clinical CNVs, and segmental duplications/LCRs
- Families, samples, pedigree structure, project assignments
- Review state, filter presets, tag definitions
- Repeat expansion catalog and sample calls
- Gene panels and gene reference refresh jobs
- Interval-track source metadata for coverage, APCAD, segments, and haplotypes

Schema source:

The schema is defined by five domain-grouped, idempotent baseline files. Each table is created once in its final shape (later `ALTER ... ADD COLUMN` steps folded into the `CREATE TABLE`). The loader (`init_postgres_schema`) applies every file in sorted name order on each boot; there is no migration ledger.

- [01_access.sql](../backend/db/schema/postgres/01_access.sql) — pgcrypto extension, genome foundation (species, assemblies, chromosomes), and identity/authorization (users, projects, project_users, auth_login_attempts).
- [02_reference.sql](../backend/db/schema/postgres/02_reference.sql) — reference/annotation data: genes and gene info (+refresh jobs), blacklist, clinical CNVs (+KB jobs), DGV variants, segmental duplications, gene panels (+genes/regions/versions), HPO (term/synonym/edge/closure), Monarch gene-disease and disease-phenotype, repeat loci, reference dataset imports.
- [03_assay.sql](../backend/db/schema/postgres/03_assay.sql) — families/samples plus per-sample assay data and review/curation: family statuses (+seed), families, samples, family members/projects/relationships, structure versions, import jobs, individual HPO, repeat expansions, Paraphase results, NIPT artifact variants, interval-track sources, small-variant reviews/presets/tag definitions (+project links), structural-variant reviews (+presets), family SV gene index (+status), ranking cache, sequencing-QC threshold profiles and thresholds (+ their append-only change history).
- [04_traceability.sql](../backend/db/schema/postgres/04_traceability.sql) — import provenance plus the append-only, hash-chained clinical audit and integrity surface: audit_log_events, ui_events, raw_import_files, family_annotation_manifest, clinical_audit_events, report_signouts, integrity_anchors, and the immutability trigger functions/triggers (including the one on qc_threshold_changes).
- [05_grants.sql](../backend/db/schema/postgres/05_grants.sql) — the restricted runtime role `coga_app` and its grants/revokes.

## ClickHouse

Primary responsibilities:

- Assembly-scoped small variant storage
- Assembly-scoped structural variant storage
- Family and sample genotypes/calls over flattened CoGA rows
- Cross-project genotype aggregates (per-project and global allele/carrier counts) used by the Global Small Variant Explorer
- Assembly-scoped interval-track rows for coverage, WisecondorX segments, APCAD, PCF APCAD segment overlays, and haplotypes

Database bootstrap:

- [001_coga_variant_storage.sql](../backend/db/schema/clickhouse/001_coga_variant_storage.sql)

Runtime table creation:

- [clickhouse_variant_storage.py](../backend/app/services/clickhouse_variant_storage.py)
- [clickhouse_interval_tracks.py](../backend/app/services/clickhouse_interval_tracks.py)

## Runtime Flow

1. FastAPI resolves user and family/sample scope from Postgres.
2. Metadata-backed endpoints read entirely from Postgres.
3. Variant listing and query endpoints read family-scoped records from ClickHouse.
4. Review annotations are joined back from Postgres onto ClickHouse results.
5. Upload endpoints write metadata to Postgres and high-volume variant/interval payloads to ClickHouse.

## Operational Notes

- The backend boot process waits for Postgres and ClickHouse, applies schema bootstrap, seeds the repeat catalog, and starts the gene refresh worker.
- Variant IDs exposed by the API are storage-agnostic strings; metadata IDs are UUIDs.
