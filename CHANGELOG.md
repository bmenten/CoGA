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
- **Long-read (nf-core/lrsvar) family packages** — the per-sample package layout now imports
  depth-based CNV calls (HiFiCNV), mitochondrial calls with their heteroplasmy (mutserve),
  per-sample sequencing QC, aligned-read locations, and the pipeline's tool versions and run
  parameters. Discovery tolerates wildcards and resolves VCF sample columns (#402). The
  pipeline's QC report opens from the family page through a short-lived, sandboxed link
  (#402, #403).
- **CNV caller signal tracks** — HiFiCNV read depth, copy number and minor-allele fraction
  (#416); WisecondorX and QDNAseq from the long-read layout (#417); one caller-labelled
  coverage track per CNV caller on a shared log2 axis (#418); and each caller's signal files
  streamed to IGV (#419). pyBigWig is a new SOUP dependency (#416).
- **Sequencing-QC acceptance limits** — admins set a warning and an error limit per metric, in
  named per-assay profiles. Limits are evaluated server-side and shown as a compact chip in the
  family members table. No cut-offs ship, so nothing is gated until a laboratory enters one
  (#410, #411, #412). The mitochondrial QC verdict uses the same limits (#413). NIPT and PGT
  profiles, plus profiles that admins create themselves (#422). Every limit change needs a
  reason, and the limits in force are frozen into each report sign-out (#423).
- **Analysis-pipeline record** — the run's parameters and tool and database versions, on the
  family workspace and in the clinical report (#410, #411, #413). They are grouped by job and
  can be collapsed on the workspace (#422, #424, #425).
- **Gene reference built on HGNC, with release traceability** — the HGNC complete set decides
  which genes exist (45,031; previously 30,002) and resolves renamed symbols. ClinGen, GenCC and
  ClinVar gene-condition are consulted for every gene. Each cached record states the release
  and checksum of the source file that produced it (#444, #446).
- **Gene loci from GENCODE** — GRCh38 gene and transcript coordinates now come from GENCODE v50
  instead of UCSC refGene, with real biotypes, Ensembl, HGNC and RefSeq identifiers, and MANE
  tags. UCSC remains the fallback (#445).
- **T2T-CHM13v2.0 as an optional second human assembly** — off by default
  (`REFERENCE_BOOTSTRAP_T2T`). It is outside the validated scope, which is GRCh38 only (TF-01)
  (#452).
- **Gene Explorer** — MANE Select, MANE Plus Clinical, Ensembl canonical and CCDS badges from
  GENCODE's transcript tags (#447, #448, #456); RefSeq accessions per transcript (#453);
  external links that resolve by accession for UniProt, CCDS, UCSC, Monarch and MGI (#447,
  #451); and the release of each source behind the page (#454).
- **Small-variant card** — an HGVS.g headline with the cytoband (#460, #461); CCDS and RefSeq in
  the transcript modal (#457); Monarch and OMIM gene–disease associations in the expanded
  detail (#458); VCF IDs split and linked to dbSNP, COSMIC or HGMD (#461, #464). On small-variant
  and structural-variant cards the Frequencies heading links to gnomAD, except on assemblies
  gnomAD does not publish (T2T) (#471).
- **Follow-up in a new tab** — genome workspaces open in a new tab from the variant lists
  (#439). The repeat-expansion and Paraphase tables gain IGV links and new-tab chromosome views
  (#473, #475). The SV second-hit badge links to the SVs behind it (#468, #470).
- **Release tooling** — `scripts/check-release-version.sh` fails a release whose tag does not
  match `VERSION` (#398); `RELEASING.md` and a release-record template (#400).

### Changed

- **Postgres schema consolidated** from 43 incremental migrations into 5 idempotent,
  domain-grouped baselines, proven schema-identical by a `pg_dump` fingerprint (#373).
- **ClickHouse query engine split** out of a single 5.6K-line module into a 2.9K-line core
  (#370), after a wider footprint reduction and god-module refactor (#369).
- **Node 20 → 22** across CI, Docker and SBOM tooling (#388), with the floor declared in
  `.nvmrc` and `engines` (#391).
- **react-router v7 → v8** (#389), and **ESLint 9 flat config** (#360).
- Dropped the never-read `project_gt_stats` / `gt_stats` aggregate cascade (#367).
- **Compound heterozygosity uses read-backed phasing** — a pair the caller phased onto one
  haplotype (cis) is no longer a candidate, and pairs in trans within one phase set are
  labelled read-backed (#465).
- **Frequency presets also bound popmax** — at the same ceiling as the global allele
  frequency, so an annotation that carries only VEP `MAX_AF` is filtered too. This is stricter
  for variants that are common in one population (#466).
- **dbNSFP gene reference pinned to 5.4** — after placing the file, run a gene-reference sync
  by hand (#435).
- **Structural-variant viewer windows** — IGV opens on the whole call ±1 kb and the chromosome
  view on ±50 kb, instead of the start breakpoint (#440, #472).
- **Mitochondrial QC uses the app-wide status vocabulary** (`pass` / `warn` / `fail` / `skip`)
  (#415).
- **One header for every family page** (#476, #477, #482). Family-workspace buttons are
  grouped and share one style (#483, #484, #486, #487). The region of interest sits on its own
  title line, and the ROI-markers link appears only for families with an embryo (#488).
- Small-variant card layout and typography (#459, #462, #463), filter-control sizing (#467),
  the QC-threshold admin page (#426, #427, #428), and grouping in the pipeline-settings panel
  (#424, #425).
- **CI** — a release is built once, from the published release, and deploys are serialised
  (#398). Unused imports removed (#450).
- **Dependencies** (Dependabot):
  - Backend: FastAPI 0.141.1, Starlette 1.6.0, uvicorn 0.53.0, SQLAlchemy 2.0.54, pydantic
    2.13.5, pydantic-settings 2.15.0, clickhouse-connect 1.8.0, PyJWT 2.14.0, pysam 0.24.1,
    pyBigWig 0.3.26, lxml 6.1.3, anyio 4.14.2, soupsieve 2.9, plus cloud and support libraries
    (#409, #438, #503, #504, #506).
  - Frontend: React 19.3.0, react-router 8.4.0, TanStack Query 5.103.2, axios 1.20.0, igv.js
    3.8.9, Vite 8.3.1, qs 6.16.0 (#406, #441, #496, #505).
  - Test and lint tooling: jsdom 24 → 30 (#435), @testing-library/jest-dom 7, vitest 4.1.11,
    Playwright 1.63.0 and typescript-eslint 8.70.1 (#408, #441, #502, #505).
  - GitHub Actions: setup-python 7 and setup-node 7 (#404, #405), CodeQL action 4.37.7 (#433,
    #480).

### Removed

- **Mouse-wheel zoom in the chromosome view** (added in #366) — the wheel scrolls the page
  again. Drag-to-select and the zoom controls are unchanged (#489).
- **Per-gene HGNC, Ensembl, Ensembl-homology and ClinGen-page lookups** in the gene-reference
  sync. The bulk HGNC set and GENCODE now supply what they did, and NCBI is the only per-gene
  call left (#447).

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
- **Analysis provenance** — the family annotation manifest had not been written since
  2026-06-29: an asyncpg parameter-typing error was logged and swallowed, so no tool version was
  recorded for any family (#413). Tool names in the provenance record are no longer
  title-cased (#414).
- **ClinGen curations** — the ClinGen dosage and gene-validity downloads start with a banner,
  and every row had been silently skipped. They are now ingested (#443).
- **Prioritised ranking after uploads and deletes** — the ranking cache is now keyed on the
  family's variant data and on the HPO and gene-constraint releases. A direct upload or an
  admin delete no longer leaves an outdated prioritised ranking and total in place (#531).
- **Structural-variant filtering** — gene and panel filters now run before the candidate cap,
  so a gene-filtered, prioritised search no longer misses SVs that fall outside the first
  5,000 rows (#469).
- **Repeat-expansion status at contraction loci** — normal calls at VWA1 and MIR7-2 had been
  flagged pathogenic on every sample. Status now follows the catalogue's benign and pathogenic
  ranges. At such a locus, a count outside every stated range is reported as unknown (#474).
- **Structural-variant card position** — from #440 until this fix, the card headline showed
  the padded viewer window (±50 kb) instead of the call's own coordinates (#472).
- **HiFiCNV tracks** — the minor-allele-fraction track now renders, and copy number is drawn on
  the segments axis as log2(CN/2) (#420). The MAF downsample spreads across the whole
  chromosome, on a 0–0.5 axis (#421).
- **Small-variant card** — AlphaMissense is shown (the card had read keys that the annotation
  never contains) (#460). An ID column holding several accessions no longer links to a dbSNP
  record that does not exist (#461, #464).
- **Gene Explorer** — the overview now names the MANE, canonical and Vega identifiers (#448,
  #449). A gene with a `not_consulted` source no longer fails the gene profile with a 500
  (#454). The sync only enriches genes that HGNC recognises (#446).
- **Viewers** — genome and chromosome tracks are sized to their real container, including when
  the page opens in a background tab (#485).
- **UI** — the QC report link (#403), the QC chip tooltip and placement (#411), QC-threshold
  inputs that lost to a more specific shared rule, now guarded by a stylesheet test (#428), the
  family stat row after the header refactor (#482), and the workspace-button cascade (#487).
- **Signed report view** — the report page presents its sign-out record as signed only while
  the live content still matches the latest signed version, compared section by section; when it
  does not, the record says what changed, and every page that is not the verified signed record
  prints with a notice at the top. The frozen record can be downloaded. Reported structural
  variants and CNVs are now frozen into the signed snapshot. Overriding the evidence-drift gate
  needs a reason, like the Sample-QC override, and a failed sign-out is shown instead of being
  dropped (#532).
- **Viewer failures** — a failed track request in the genome and chromosome viewers shows as a
  failure with a retry instead of "no data". The genome haplotype track no longer builds the
  PGT risk state from the sources that happened to load. The pan fallback no longer paints one
  chromosome's data under another, pedigree edits drop the cached haplotypes, and the error
  screen clears on navigation (#533).
- **Repeat status for unclassifiable alleles** — an allele outside every catalogued range at a
  contraction locus (VWA1, MIR7-2) is now shown as *review* and kept in the aberrant-only view,
  instead of hiding behind a normal allele. Counts on a benign/grey-zone boundary at expansion
  loci (RFC1 11, ATXN8OS 50) read as grey zone again, and a gene catalogued twice resolves to
  one entry deterministically (#540).
- **CSV exports** — a small-variant or SV export above 10,000 rows is no longer cut there
  silently: it holds every row up to the 50,000-row cap, and one above the cap is saved as
  `…-TRUNCATED-first-50000.csv` and announced in the UI (#538).
- **ClinVar P/LP frequency override** — the ClickHouse query now applies the rescue the option
  promises, so a ClinVar pathogenic / likely-pathogenic variant above the frequency ceiling is
  kept in every view that has the option on. ClinVar's aggregate `Pathogenic/Likely_pathogenic`
  counts as both terms (#537).
- **Sign-out and review fallbacks** — a lookup that fails while the sign-out snapshot is frozen
  (the QC cut-offs, the reference-assembly or Monarch version) is recorded as unavailable with its
  reason instead of as an empty block, and the signed record and its audit entry say what was not
  captured. A stored ACMG or CNV classification that no longer validates is logged and flagged, and
  the classification editor warns before it is overwritten. A Mendeliome that failed to regenerate
  after a Monarch refresh is reported to the admin instead of only logged (#541).
- **Validated assembly scope** — a report on a reference assembly outside the validated scope
  (`VALIDATED_ASSEMBLIES`, default GRCh38; T2T-CHM13 is out of scope) can no longer be signed out,
  with no override, and every family page and the report label such a family *Not validated for
  clinical use*. Comma-separated `FAMILY_IMPORT_ROOTS` and `CORS_ORIGINS` values set as real
  environment variables no longer fail at startup (#542).
- **Gene-panel coordinates per assembly** — a panel's regions are stored per reference assembly,
  and a family's panel filter uses only its own assembly's coordinates. Before, a GRCh38 family
  could be filtered on T2T coordinates (and PanelApp GRCh37 coordinates on any family), so
  variants outside the panel could be shown as panel hits. The panel pages show each region's
  assembly and the panel size per assembly (#543).
- **Haploid and multi-allelic genotypes** — genotype filters, the inheritance modes, the
  per-sample counts and track presence now share one genotype classification. So a hemizygous
  `1` (chrM, or chrX/chrY in a male) counts as Hom, a `1/2` as Het, and a haploid `0` as
  reference. Before, these calls matched no genotype group: the X-linked filter dropped a
  hemizygous son, and an all-haploid sample looked empty (#544).
- **Concurrent review saves** — saving a variant review that another reviewer changed after you
  opened it is refused, and the page shows their review instead of overwriting it. Before, the
  last save won silently (#546).

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
- **aiohttp 3.14.3 and cryptography 50.0.0** — closes PYSEC-2026-3545, -3546 and -3547
  (aiohttp) and PYSEC-2026-3552 (cryptography), replacing Dependabot #429 and #430, which could
  not pass the `deps` gate separately (#436).
- **Development tree** — js-yaml 4.3.1 (GHSA-5p4m-2wfm-xmqj) and brace-expansion 1.1.18
  (GHSA-mh99-v99m-4gvg, GHSA-rgw5-rvv9-x895), as lockfile-only changes (#442); js-yaml 4.3.2,
  which limits merge-key CPU use (#499).
- Dependabot bumps whose release notes include security fixes: axios 1.19.0, which raises the
  form-data floor (#406), and PyJWT 2.14.0 (#506).

### Documentation

- Licensed under **Apache-2.0** with a regulatory `NOTICE` recording that CoGA is an in-house
  IVD under IVDR Article 5(5), is not CE-marked, and that its validation does not transfer
  with the source (#394).
- **`SECURITY.md`** with a private vulnerability-reporting route and explicit separation from
  the clinical vigilance path (#393); **`CONTRIBUTING.md`** and **`CODE_OF_CONDUCT.md`** (#395).
- CI now fails when the generated handleiding page drifts from its Markdown (#390).

---
- **Technical file reconciled with the code** — required CI checks, SBOM automation, the
  Terraform status, SOUP pins, requirement and test counts. The claim that every PR is
  independently reviewed is withdrawn (#399).
- **Answered questionnaire items folded in** — per-application scopes; GRCh38 as the only
  validated assembly, with T2T out of scope; named role holders; comparators. This also records
  that BELAC certificate 351-MED is held by Universiteit Gent, not by the declaring institution
  (#401).
- **TF-18 change records CR-002 to CR-012** were added alongside the changes they describe,
  together with TF-06 hazard H14, the TF-09a/TF-09b QC and traceability requirements, and the
  TF-08 SOUP entries for the long-read callers and pyBigWig (#402, #412, #413, #415–#423).
- The SBOM README names the `node:22` generator container (#397). The handleiding and README
  are updated for the gene-reference rebuild, GENCODE and the optional T2T assembly (#455).

### A note on the record before this file existed

CoGA has **353 merged pull requests** since 2026-05-08, most of them in the initial build-out
during June 2026. The entries above are a curated summary of that work, not a transcription:
the authoritative record is the git history and the
[pull-request list](https://github.com/bmenten/CoGA/pulls?q=is%3Apr+is%3Amerged). From the
first release onward, entries are written per change as it lands, as TF-18 requires.

[Unreleased]: https://github.com/bmenten/CoGA/commits/main
