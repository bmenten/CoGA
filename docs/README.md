# Documentation Index

This directory contains the repository-facing documentation for CoGA.
Use it alongside the in-app user guide at `/docs` when the application is running.

## Start Here

- [Application Scheme](application-scheme.md)
  - Repo-accurate architecture diagrams for the frontend, backend API, storage layers, runtime assets, and import tooling.
- [Storage Architecture](storage-architecture.md)
  - CoGA Postgres plus ClickHouse split, schema entrypoints, and migration-status notes.
- [Development and Reset Guide](development.md)
  - Local setup, Docker versus non-Docker behavior, environment variables, reset steps, and startup troubleshooting.
- [Data Import Guide](data-import.md)
  - Canonical import order, CLI versus web upload responsibilities, demo-dataset loading, and supported assay/reference file flows.
- [Database Schema](database.md)
  - Collection-by-collection schema reference for access control, reference layers, assay data, review state, gene caches, and repeat-expansion data.
- [Roadmap](ROADMAP.md)
  - Current backlog and direction notes.

## Also Useful

- [Demo Quartet Walkthrough](../demo/quartet_family/README.md)
  - File inventory and usage notes for the bundled synthetic family dataset.

## Recommended Reading Paths

- New developer:
  - Read [Application Scheme](application-scheme.md), [Storage Architecture](storage-architecture.md), then [Development and Reset Guide](development.md).
- Loading or replacing data:
  - Read [Data Import Guide](data-import.md), then [Database Schema](database.md) if you need collection-level details.
- Analyst or reviewer:
  - Start with the in-app user guide at `/docs`, then use [Application Scheme](application-scheme.md) for architecture context.
