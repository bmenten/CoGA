# Storage Architecture

CoGA uses a split storage model:

- `Postgres` stores metadata, authorization scope, user state, panels, repeat expansions, gene cache, and interval-track source metadata.
- `ClickHouse` stores variant records for small variants, structural variants, and high-volume interval tracks.

## Postgres

Primary responsibilities:

- Users and project access
- Species, assemblies, chromosomes, genes, blacklist, clinical CNVs
- Families, samples, pedigree structure, project assignments
- Review state, filter presets, tag definitions
- Repeat expansion catalog and sample calls
- Gene panels and gene reference refresh jobs
- Interval-track source metadata for coverage, APCAD, segments, and haplotypes

Schema source:

- [001_metadata.sql](../backend/db/schema/postgres/001_metadata.sql)
- [002_repeat_expansions.sql](../backend/db/schema/postgres/002_repeat_expansions.sql)
- [003_interval_tracks.sql](../backend/db/schema/postgres/003_interval_tracks.sql)
- [004_audit_logs.sql](../backend/db/schema/postgres/004_audit_logs.sql)
- [005_gene_panel_description.sql](../backend/db/schema/postgres/005_gene_panel_description.sql)
- [006_project_scoped_variant_tags.sql](../backend/db/schema/postgres/006_project_scoped_variant_tags.sql)

## ClickHouse

Primary responsibilities:

- Assembly-scoped small variant storage
- Assembly-scoped structural variant storage
- Family and sample genotypes/calls over flattened CoGA rows
- Assembly-scoped interval-track rows for coverage, WisecondorX segments, APCAD, and haplotypes

Schema source:

- [001_coga_variant_storage.sql](../backend/db/schema/clickhouse/001_coga_variant_storage.sql)

## Runtime Flow

1. FastAPI resolves user and family/sample scope from Postgres.
2. Metadata-backed endpoints read entirely from Postgres.
3. Variant listing and query endpoints read family-scoped records from ClickHouse.
4. Review annotations are joined back from Postgres onto ClickHouse results.
5. Upload endpoints write metadata to Postgres and high-volume variant/interval payloads to ClickHouse.

## Operational Notes

- The backend boot process waits for Postgres and ClickHouse, applies schema bootstrap, seeds the repeat catalog, and starts the gene refresh worker.
- There is no MongoDB compatibility layer in the current application.
