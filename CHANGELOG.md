# Changelog

All notable changes to CoGA are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## How this file relates to change control

[TF-18](docs/regulatory/TF-18-change-configuration-management.md) §4 classifies every change
as **patch**, **minor** or **major** by its impact on clinical output, and that level decides
what validation evidence is required. It classifies a change **against the previously
validated version**, and CoGA has not had one yet: there is no release, no tag, and `VERSION`
is still `0.1.0`.

**Before the first release candidate** this file keeps no per-change entries. The development
toward that baseline is summarised once, below. It is deliberately not labelled
patch/minor/major: retrofitting levels onto work that predates the baseline they are measured
against would be inventing evidence, not recording it. The authoritative record of each change
is its pull request and the git history
([merged pull requests](https://github.com/bmenten/CoGA/pulls?q=is%3Apr+is%3Amerged)). A
pre-release change that alters a clinical output is listed, with a **proposed** level that QA
has yet to confirm, in [TF-10 §8](docs/regulatory/TF-10-performance-evaluation-plan.md), the
scope of the first validation ([TF-18 §3a](docs/regulatory/TF-18-change-configuration-management.md)).

**From the first release candidate onward** every change gets an entry here carrying its TF-18
level, and a row in the change-record log of
[TF-18 §8](docs/regulatory/TF-18-change-configuration-management.md):

```markdown
### Fixed
- **[patch]** Short description of the change. (#NNN)
```

---

## [Unreleased] — towards 0.1.0

First release candidate. The device boundary is _annotated VCF → signed clinical report_.
This section summarises the pre-release development up to 2026-09-30 (about 590 merged pull
requests); it names only a representative pull request where one helps.

### Added

- **Family workspace** — a pedigree-aware dashboard with review summaries and an editable
  region of interest, and a view per data type that appears only when the data exists: small
  variants, structural variants and CNVs, a combined variant summary, repeat expansions
  (TRGT), Paraphase and mitochondrial DNA.
- **Clinical applications** — monogenic NIPT (fetal-fraction estimation and the maternal/fetal
  VAF category model, with its own report); PGT haplotype segregation (IBD founder colouring,
  embryo classification, single-parent and donor families); mitochondrial-disease testing
  (whole mtDNA plus the nuclear mito-gene panel from ONT adaptive sampling).
- **Genome visualisation** — whole-genome overview, chromosome view, Circos plot and embedded
  IGV, with caller-labelled CNV signal tracks (HiFiCNV, WisecondorX, QDNAseq).
- **Interpretation** — a semi-automatic ACMG/AMP classifier with every criterion overridable,
  ClinGen CNV scoring, compound-heterozygote detection with read-backed phasing and an SV
  second-hit index, and a prioritised variant ranking.
- **Case sign-out and traceability** — a frozen, versioned report snapshot bound to the
  software, annotation, reference and HPO releases and the QC limits in force; gated on
  classification drift, sample integrity, sample QC, incomplete imports and the validated
  assembly scope; recorded in an append-only, hash-chained clinical audit trail with a signed
  external chain-head anchor and an integrity verifier.
- **Discovery tools** — Gene Explorer (HGNC gene set, GENCODE loci, MANE/RefSeq/CCDS badging,
  source releases), a global Small Variant Explorer with keyset pagination, a Clinical CNV
  Explorer over the curated knowledge base (with ClinVar support counts), an HPO browser and a
  gene-panel catalogue.
- **Intake and administration** — Family Builder; folder- and bucket-based Package Import with
  manifest discovery, dry-run validation and snapshot/restore atomicity; nf-core/lrsvar
  long-read packages; per-assay sequencing-QC acceptance limits; admin tooling for users,
  projects, data, reference sync, ClickHouse maintenance and integrity, and the audit logs.
- **Assemblies** — GRCh38 is the validated scope; T2T-CHM13v2.0 is an optional, unvalidated
  second assembly; GRCh37 coordinates for genes and the clinical CNV knowledge base.
- **Verification** — an end-to-end harness (golden-trio import, API contract, review and
  sign-out, failure and job lifecycle, demo smoke, haplotypes) and Playwright journeys against
  the production bundle, backing TF-09c/TF-09d; a computed-style diff tool for stylesheet
  changes.
- **Regulatory and release** — the IVDR technical file under
  [`docs/regulatory/`](docs/regulatory/README.md), a Dutch codebase manual (handleiding) for
  the review board, `RELEASING.md` and the release-version check.

### Changed

- **Platform** — Python 3.12, Node 22, ClickHouse 26.8 LTS, react-router 8, ESLint 9 flat
  config; dependencies kept current through Dependabot.
- **Schema** — the Postgres schema consolidated into five idempotent baselines (#373);
  ClickHouse per-assembly tables created at runtime.
- **Code structure** — the ClickHouse query engine, small-variant loader and page, API models,
  access rules, dataset importers and large frontend components split into focused modules;
  frontend API types generated from the OpenAPI schema; the stylesheet split into 28 ordered
  modules with a byte-identical build.
- **Verification gates** — ruff, mypy on the clinical-critical modules, per-module coverage
  floors (unit and combined), zero-warning ESLint with the hooks and accessibility rules, the
  test catalogue, and CSP-checked browser journeys.
- **Every family variant write is serialised** per family by an advisory lock (#670).

### Removed

- Code that upgraded older ClickHouse and Postgres schemas, vestigial API fields, alias routes
  and endpoints without a caller, pre-release signed-record formats, the external fetal
  fraction input, unused settings and dependencies, and ~15 % of the CSS that nothing used.

### Fixed

- **Fail-closed behaviour** — failed or partial requests, imports and knowledge-base sources
  now show as failures, never as empty or complete data, across the viewers, reports, exports,
  searches and sign-out.
- **Clinical correctness** — hemizygous X/Y handling in de novo and PM6, haploid and
  multi-allelic genotypes, repeat-expansion status, PGT embryo calls, NIPT consistency,
  compound-het phase, gene-panel coordinates per assembly and ClinVar frequency rescue.
- **Data integrity** — per-sample and per-source variant rewrites keep every other call,
  concurrent review saves are detected, every classification change is audited, and reference
  imports record their release.
- **Operations** — ClickHouse memory guards and integrity checks, bounded gzip reads, first-boot
  compose timing, bucket imports in cloud mode, and deploys that ship the merged code.

### Security

- Authentication and session hardening (proxy-aware client IP, sign-up enumeration, 15-character
  passwords, cache cleared on logout); the backend refuses placeholder or weak secrets outside
  development; path and input containment; CodeQL findings resolved; a restricted database role
  and Google Cloud go-live hardening, ready and off by default; dependency advisories patched as
  they appeared; `SECURITY.md` with a private reporting route.

### Documentation

- The technical file, handleiding, in-app user guide and developer guides checked against the
  code and kept current; Google Cloud confirmed as the production target.
