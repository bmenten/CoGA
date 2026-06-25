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
- [Security & PHI Posture](security-posture.md)
  - Access control / RBAC, audit logging (append-only), encryption, and the S3/deployment PHI-scoping checklist, plus the CI gates.
- [Roadmap](ROADMAP.md)
  - Current backlog and direction notes.
- [Clinical Traceability, Sign-out & Audit (plan)](clinical-traceability.md)
  - Proposed design for clinical-grade end-to-end traceability: the annotation/reference version manifest + report footer, per-classification evidence snapshots, annotation-version drift surfacing, an immutable clinical audit trail, and case sign-out with a frozen, versioned report snapshot. Grounded in the current code, with a phased delivery plan.

## Also Useful

- [ACMG Classification](acmg-classification.md)
  - The semi-automatic ACMG/AMP classifier: the points/scoring model, VUS hot/warm/cold sub-tiers, the mtDNA-specific (McCormick 2020) rule set, and the full pre-check and exclusion rules used to auto-position each criterion.
- [Family Report Template](report-template.md)
  - Drafting a clinical report from variants tagged `report`: variant description, ACMG motivation, gene context and HPO phenotype coupling.
- [Family Member Management](family-member-management.md)
  - How phenotype/carrier/structure edits propagate, what is preserved versus marked stale, and the batch-update flow.
- [Sample-integrity QC](../frontend/src/content/docs/sample-qc.md)
  - Application-aware sample QC: sex concordance, KING relatedness + consanguinity, Mendelian-error rate, and the monogenic-NIPT cfDNA checks (paternity, fetal sex, parent sex, category QC), with thresholds and data sources. It is authored in the frontend so it renders in-app at `/docs/reference/sample-qc` (linked from the user guide) — viewable in the browser while the repository is private.
- [Haplotype Segregation Analysis](haplotype-segregation-analysis.md)
  - The PGT haplotype track: pedigree-aware IBD founder colouring, the raw phased-marker overlay, the four-founder + grey + risk colour code, single-parent (donor) families, the derived embryo classification (affected/carrier/unaffected/uninformative) with recombination/uninformative-marker warnings, and the ROI marker overview + QC signals.
- [Monogenic NIPT Analysis](monogenic-nipt.md)
  - The cfDNA-from-plasma feature (implemented): the 2-sample trio model, fetal-fraction estimation, the eight maternal/fetal VAF categories, quality/artifact filtering, on-target coverage reporting, and the dashboard — built on the small-variant pipeline. Doubles as the design reference.
- [Monogenic NIPT — Fetal Fraction & Classification Algorithm](monogenic-nipt-classification.md)
  - The Phase 3 algorithm reference (implemented): data structures, the category-7 fetal-fraction estimator with external-FF reconciliation, the beta-binomial per-variant classifier and confidence model, the resolvable-vs-not reliability split, edge cases, and the test plan.
- [Demo Quartet Walkthrough](../demo/quartet_family/README.md)
  - File inventory and usage notes for the bundled synthetic family dataset.

## Recommended Reading Paths

- New developer:
  - Read [Application Scheme](application-scheme.md), [Storage Architecture](storage-architecture.md), then [Development and Reset Guide](development.md).
- Loading or replacing data:
  - Read [Data Import Guide](data-import.md), then [Database Schema](database.md) if you need collection-level details.
- Analyst or reviewer:
  - Start with the in-app user guide at `/docs`, then use [Application Scheme](application-scheme.md) for architecture context.
