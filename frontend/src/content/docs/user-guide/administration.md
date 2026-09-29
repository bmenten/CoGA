Administrative tooling lives behind admin access and keeps the platform governed, current and
healthy. Everything is reachable from the [admin dashboard](/admin), which groups the workspaces
into six operational domains. The sections below describe what each one does.

### 1 · Reference data

The shared, system-wide datasets every project reads. These rarely change day to day, but keeping
them current is what makes annotations, gene context and phenotype matching accurate.

- **Species \& assemblies** ([/admin/reference/assemblies](/admin/reference/assemblies)) — the
  reference genome builds (e.g. GRCh38) and their per-assembly layers: cytobands, gene and
  transcript models, clinical CNVs, segmental duplications and DGV. Shows the status of each dataset
  and lets you trigger a **gene-metadata sync** — refresh a single gene by symbol or queue an
  all-human refresh — and watch the resulting jobs (progress, errors). The same area rebuilds the
  **clinical-CNV knowledge base** from ClinVar and DGV.
- **Gene panels** ([/admin/reference/gene-panels](/admin/reference/gene-panels)) — the panel
  catalogue, each panel’s source metadata and its gene membership. Panels chosen here are what
  analysts pick from in [case setup](#phenotypes-and-panels) and the small-variant location filter.
- **HPO terminology** ([/admin/reference/hpo](/admin/reference/hpo)) — the Human Phenotype Ontology
  release: term count, synonyms, relationships and sync status. Preview and apply a new ontology
  release so phenotype entry and matching use current terms.
- **Monarch knowledge graph** — load the monthly Monarch release (gene–disease and disease–phenotype
  associations) that powers [phenotype matching](#phenotype-matching) and [variant
  prioritisation](#variant-prioritisation). Run it once after deploy and roughly monthly to stay
  current; until it is run, the phenotype blocks show an empty state.

### 2 · Users \& access

This is what scopes every query, cohort count and family list in the platform.

- **Users** ([/admin/access/users](/admin/access/users)) — user accounts, their role, and their
  activation status. Deactivate an account to revoke access without deleting its history.
- **Projects \& access** ([/admin/access/projects](/admin/access/projects)) — the project catalogue
  (each project pins an assembly) and the family/sample-to-project assignments. A user only ever
  sees data in the projects they are granted, and the [Global Small Variant
  Explorer](#variant-explorer) counts only across those projects, so access here directly defines
  each analyst’s and each cohort query’s scope.

### 3 · Data management

The imported family, sample and assay data, plus the workflow labels around it.

- **Family \& sample data** ([/admin/data/families](/admin/data/families)) — search and page the
  full inventory of families and samples, drill into a family’s members and per-assay layers, and
  reassign a family or sample to different projects. Per-family **raw import files** can be
  downloaded and integrity-verified. Deletion workflows are here too, scoped precisely: remove a
  single assay layer for one sample, or an entire sample or family. (Deletions are irreversible and
  audit-logged.)
- **Family statuses** ([/admin/data/family-statuses](/admin/data/family-statuses)) — curate the
  workflow statuses analysts assign to families (e.g. *new*, *in progress*, *completed*): add,
  rename, recolour or remove them.
- **Package import** ([/admin/data/upload](/admin/data/upload)) — validate an import manifest and
  run a package-based **initial or incremental import** from the browser (the same flow available on
  the CLI). See [case setup](#case-setup) for what an import brings in.

### 4 · Variant configuration

The interpretation vocabulary and reusable filters shared across the team.

- **Variant tags** ([/admin/variants/tags](/admin/variants/tags)) — define the review tags analysts
  apply during [interpretation](#interpretation-and-review): create, rename, recolour and remove
  them, and scope custom tags per project. The built-in ACMG class tags and the VUS hot/warm/cold
  tier tags are managed automatically and appear here for reference.
- **Preset filters** ([/admin/variants/presets](/admin/variants/presets)) — review the built-in and
  saved [small-variant filter presets](#small-variant-filtering) the team relies on for consistent
  review. Presets are authored from the family workspace; this view is the catalogue.

### 5 · Database \& operations

- **ClickHouse tables \& operations** ([/admin/operations/clickhouse](/admin/operations/clickhouse))
  — the per-assembly variant tables that back small-variant search, with their status and size, plus
  manual maintenance: **ensure** (create or repair a missing/corrupt table), **optimise** (compact
  storage, with an optional *final* pass) and **rebuild the small-variant gene index** (the
  materialised view behind gene-based lookups). Reach for these after a large import or if a
  gene-scoped query looks incomplete.

### 6 · Monitoring \& audit

- **Audit logs** ([/admin/monitoring/audit-logs](/admin/monitoring/audit-logs)) — a full activity
  trail for inspection and platform optimisation, split into two views, each filterable by user,
  path, method and status. **API requests** records every backend call: who, what, when, the
  response and duration, inferred data updates, and which search filters a query used. **UI
  interactions** records client-side activity that never reaches the backend — every button and link
  click and each in-app navigation. Identifiers are masked (paths reduced to *:id*, query strings to
  their filter names), so the trail shows *how* the platform is used without exposing patient data.

Personal display preferences live on the [Settings](/settings) page (not an admin tool); release
notes are on the [New features](/new-features) page.
