# Application Scheme

```mermaid
flowchart LR
    UI["React frontend<br/>login, dashboards, family browser, tracks"] --> API["FastAPI routers"]

    API --> AUTH["Auth + access control"]
    API --> META["Metadata services"]
    API --> VAR["Variant query services<br/>family-scoped + cross-project explorer"]
    API --> GENE["Gene / HPO / panel services"]
    API --> REF["Reference services"]
    API --> REVIEW["Review + repeat services"]

    AUTH --> PG["Postgres"]
    META --> PG
    GENE --> PG
    REF --> PG
    REVIEW --> PG

    VAR --> CH["ClickHouse"]
    VAR --> PG
    REVIEW --> CH

    REF --> FS["Filesystem assets<br/>FASTA, BAM/CRAM"]

    JOBS["Startup + workers<br/>schema bootstrap, repeat catalog seed,<br/>gene refresh worker"] --> PG
    JOBS --> CH
```

## Flow Summary

- Frontend requests hit FastAPI routers.
- User identity and project/family/sample scope are resolved from Postgres.
- Metadata, reference, gene/HPO/panel, and the Gene Explorer endpoints read from Postgres.
- Family-scoped small/structural variant endpoints query ClickHouse; the Global Small Variant Explorer aggregates genotypes from ClickHouse across the user's accessible projects and joins tags/classifications from Postgres.
- Review annotations, repeat expansions, Paraphase, mitochondrial analysis, panels, and track availability are assembled from Postgres and joined onto variant responses.
- Reference sequence and read endpoints also use local filesystem assets.

## Main Code Areas

- `backend/app/core/`: settings, Postgres, ClickHouse, Azure auth helpers
- `backend/app/routers/`: API surface
- `backend/app/services/metadata_service.py`: metadata and access control
- `backend/app/services/clickhouse_family_variants.py`: family-scoped variant queries
- `backend/app/services/variant_explorer_service.py`: cross-project variant-centric aggregation (Global Small Variant Explorer)
- `backend/app/services/gene_metadata_service.py`: Gene Explorer profiles and transcript metadata
- `backend/app/services/variant_upload_service.py`: variant ingestion, with `haplotype_block_builder.py` (haplotype blocks from phased genotypes) and `annotation_table_parser.py` (VEP TSV and mutserve annotation tables)
- `backend/app/services/bed_service.py`: interval-track ingestion and retrieval
- `backend/app/services/repeat_expansion_pg.py`: repeat catalog and sample calls
- `backend/app/services/paraphase_pg.py`, `mitochondrial_analysis.py`: Paraphase and mtDNA analysis

## Storage Boundary

- `Postgres` is authoritative for metadata and state.
- `ClickHouse` is authoritative for variant payloads.