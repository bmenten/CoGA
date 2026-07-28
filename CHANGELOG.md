# Changelog

All notable changes to CoGA are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## How this file relates to change control

[TF-18](docs/regulatory/TF-18-change-configuration-management.md) §4 classifies every change
as **patch**, **minor** or **major** by its impact on clinical output, and that level decides
what validation evidence is required. A changelog note is mandatory at every level.

That model classifies a change **against the previously validated version**. CoGA has not yet
had one: there is no release, no tag, and `VERSION` is still `0.1.0`. Everything below is
therefore pre-first-release development toward that baseline, and is deliberately **not**
labelled patch/minor/major — retrofitting regulatory classifications onto work that predates
the baseline they are measured against would be inventing evidence, not recording it.

**From the first release onward, each entry carries its TF-18 level**, like so:

```markdown
### Fixed
- **[patch]** Short description of the change. (#NNN)
```

---

## [Unreleased] — towards 0.1.0

First release candidate. The device boundary is _annotated VCF → signed clinical report_.

### Added

- **Family workspace** — pedigree-aware dashboard with review/curation summaries and an
  editable region of interest; per-data-type views surfaced only when the data exists: small
  variants (SNV/indel), structural variants, a combined variant summary, repeat expansions
  (TRGT), Paraphase, and mitochondrial analysis.
- **Monogenic NIPT** — cfDNA-from-maternal-plasma analysis with fetal-fraction estimation,
  the maternal/fetal VAF category model, and a dedicated report view.
- **PGT haplotype segregation** — pedigree-aware IBD founder colouring with a raw
  phased-marker overlay and ROI marker overview, deriving an embryo classification
  (affected / carrier / unaffected / uninformative), including single-parent (donor) families.
- **Mitochondrial-disease testing** — ONT long-read adaptive sampling covering the complete
  mtDNA and the nuclear mito-gene panel in one run, interpreted together, with Sample QC
  (relatedness, sex, Mendelian, maternal lineage) used to flag sample swaps.
- **Genome visualization** — whole-genome overview, per-chromosome view, Circos plot, and an
  embedded IGV browser. Unified zoom, mouse-wheel zoom and panning in the chromosome view (#366).
- **Semi-automatic ACMG classifier** — pre-evaluates ACMG/AMP criteria from variant, trio and
  gene data onto a points scale, every criterion overridable.
- **Case sign-out and clinical traceability** — a frozen, versioned report snapshot bound to
  the software version and the annotation/reference versions, gated on classification drift
  and Sample-QC acknowledgement, recorded in an append-only, hash-chained audit trail.
- **Discovery tools** — Gene Explorer with MANE/RefSeq/Ensembl transcript badging and
  constraint metrics; global Small Variant Explorer with cross-project carrier counts and
  drill-down; Clinical CNV Explorer over the curated CNV knowledge base; HPO term browser and
  reusable gene-panel catalog.
- **Intake and administration** — Family Builder for manual pedigrees; folder-based Package
  Import with manifest discovery and dry-run validation; admin tooling for users, projects,
  data management, gene-reference sync, ClickHouse maintenance and audit logs.
- **Import atomicity** — snapshot/restore so a failed overwrite cannot leave a family
  partially replaced, for ClickHouse (#383) and Postgres (#384).
- **End-to-end validation harness** — API-contract suite for visualization endpoints, review
  round-trip with audit hash-chain and report sign-out, failure/degradation and job-lifecycle
  coverage, and a realistic demo-bundle smoke run.
- **IVDR technical file** under [`docs/regulatory/`](docs/regulatory/README.md), and a
  15-chapter Dutch codebase manual for the review board with a reproducible web build (#385).

### Changed

- **Postgres schema consolidated** from 43 incremental migrations into 5 idempotent,
  domain-grouped baselines, proven schema-identical by a `pg_dump` fingerprint (#373).
- **ClickHouse query engine split** out of a single 5.6K-line module into a 2.9K-line core
  (#370), after a wider footprint reduction and god-module refactor (#369).
- **Node 20 → 22** across CI, Docker and SBOM tooling (#388), with the floor declared in
  `.nvmrc` and `engines` (#391).
- **react-router v7 → v8** (#389), and **ESLint 9 flat config** (#360).
- Dropped the never-read `project_gt_stats` / `gt_stats` aggregate cascade (#367).

### Fixed

- **Import pipeline robustness** — atomicity, orphan cleanup, bounded reads and numeric
  guards, preserving partial-success semantics (#362).
- **Sign-out gating** — refuse to sign out on unverifiable sample-integrity checks, not only
  on detected mismatches (#342), and gate reported classifications with no evidence snapshot
  (#349).
- **Fail-closed corrections** — family-import path guard (#359), unverifiable family rename
  with ambiguous multi-assembly surfaced (#345), unverifiable ClinVar classification drift
  with whole-token matching (#344), and refusal to load Monarch data with an unknown release
  version (#347).
- **ClickHouse** — memory guards to stop `MEMORY_LIMIT` on the chromosome view (#371),
  small-variant list dedup with page clamping (#350), and small-variant deletes scoped by
  source so one caller cannot clobber another's data (#341).
- **Bounded gunzip** truncating BGZF/multi-member gzip to the first block (#368).
- **Audit integrity** — verify the whole anchor chain and warn on unsigned anchors in
  production (#351).
- ACMG classification modal no longer wipes in-progress analyst edits (#346).

### Security

- **Hardening sweep (2026-07)** — authentication (proxy-aware client IP, signup enumeration,
  login timing, override audit, CORS regex) (#361); infrastructure and CI (workflow
  permissions, digest pins, non-root backend container, checksum verification, injection)
  (#363); GitHub Actions pinned to full commit SHAs (#343).
- **Path and input containment** — staged object keys confined to the staging directory
  (#340), HPO ontology import/sync constrained to authorized ref-data directories (#356),
  URL path segments escaped with bounded BED windows (#353), and outbound download/gunzip
  bounded against decompression bombs (#352).
- **CodeQL findings resolved** — prototype-injection and telemetry RNG (#354), control-char
  scrubbing of user values in logs (#355), and a ReDoS in the ClickHouse INSERT-query parser
  (#357).
- **Dependency-audit gate narrowed** so a single unfixable advisory cannot force the whole
  production gate off; exemptions must be named, justified and review-dated, and expire
  (#386). The one exemption it carried was retired by the react-router upgrade (#389).
- Telemetry no longer stores nested UI-event detail structures, and `user_agent` is capped
  (#348).

### Documentation

- Licensed under **Apache-2.0** with a regulatory `NOTICE` recording that CoGA is an in-house
  IVD under IVDR Article 5(5), is not CE-marked, and that its validation does not transfer
  with the source (#394).
- **`SECURITY.md`** with a private vulnerability-reporting route and explicit separation from
  the clinical vigilance path (#393); **`CONTRIBUTING.md`** and **`CODE_OF_CONDUCT.md`** (#395).
- CI now fails when the generated handleiding page drifts from its Markdown (#390).

---

### A note on the record before this file existed

CoGA has **353 merged pull requests** since 2026-05-08, most of them in the initial build-out
during June 2026. The entries above are a curated summary of that work, not a transcription:
the authoritative record is the git history and the
[pull-request list](https://github.com/bmenten/CoGA/pulls?q=is%3Apr+is%3Amerged). From the
first release onward, entries are written per change as it lands, as TF-18 requires.

[Unreleased]: https://github.com/bmenten/CoGA/commits/main
