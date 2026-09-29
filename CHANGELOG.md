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
- **One backend test root** — the top-level `tests/` folder is merged into `backend/tests/`,
  so `pytest` runs the whole suite from the repository root or from `backend/`, and the
  catalogue check fails on a test file outside it (#553).
- **Frontend production image** — the build toolchain (vite, typescript, `@types/*`) moved to
  devDependencies, so the production image no longer installs it; its `node_modules` drops
  from 119 to 61 MB. No dependency version changed (#553).
- **Backend runtime: Python 3.12** — the backend image, CI and the lock scripts move from Python
  3.10, which reaches end of life on 2026-10-31, to Python 3.12 (3.12.14; security fixes until
  October 2028). No dependency version changed; the lock drops four backports only 3.10 needed
  (#555).
- **ClickHouse 26.8 LTS** — compose, CI and the Terraform VM move from ClickHouse 25.3 LTS, out of
  support since 2026-03-20, to 26.8 LTS (26.8.14.3, supported until 2027-08-27). An existing data
  volume is upgraded in place on first start and cannot be moved back; snapshot it first. A test
  keeps compose, CI and Terraform on the same datastore images (#563).
- **Verification gates (#526)** — CI lints the backend (ruff), type-checks the clinical-critical
  modules (mypy), records coverage in the smoke and e2e jobs and combines it with the unit run
  under floors of its own, and runs the browser journeys against the production bundle and server,
  failing on any CSP violation. The unit coverage floors are raised and the Playwright specs are
  catalogued (#567).
- **API models split by domain** — the 3,000-line `backend/app/schemas.py` is now a `schemas/`
  package with one module per domain, all re-exported, so imports are unchanged. The OpenAPI
  document is byte-identical (#571).
- **Access rules in their own module** — `CurrentUser`, `ADMIN_ROLES` (defined twice until now)
  and the project-visibility rules move from `metadata_service` to `services/access_control.py`,
  so the 64 modules that needed only them no longer import the metadata layer (#572).
- **Tests for the thinly tested review and job modules (#526)** — structural-variant reviews and
  their CNV classification, variant tag definitions and the clinical CNV knowledgebase rebuild job
  now have unit tests and coverage floors (#574).
- **Frontend lint rules (#526)** — ESLint enforces the React hooks rules and the jsx-a11y
  accessibility rules. `any` is now a warning, and `npm run lint` fails when the warning count
  rises above its budget of 67 (#575).
- **Small-variant loader split (#528)** — the VEP and mutserve annotation-table parsers and the
  haplotype-block builder leave the 1,900-line upload module for modules of their own. The
  builder is now a class fed one record at a time; a test built from its recorded output shows
  the blocks are unchanged (#578).
- **Frontend tests (#526)** — 19 new test files cover what had none: the gene track, the
  ideograms, the interval and repeat tracks, the variant explorer, the SV summary, the clinical CNV
  explorer, and the admin and sign-up pages. The coverage floors rise to just below the new figures
  (lines 78 %), with a floor of their own for the visualisations (#579).
- **ClickHouse variant imports (#528)** — modules import the ClickHouse query and record helpers
  from the modules that define them, not through `clickhouse_family_variants.py`, which passed
  120 names on. That ends a dependency cycle between the family-variant layer and variant storage
  that two function-level imports had hidden (#584).
- **Frontend coverage (#526)** — the SV results table, its column picker and the locus parser
  have tests; no frontend file is below 30 % of lines, and the coverage floors rise to lines 80 %
  (visualisations 88 %) (#594).

### Removed

- **Mouse-wheel zoom in the chromosome view** (added in #366) — the wheel scrolls the page
  again. Drag-to-select and the zoom controls are unchanged (#489).
- **Per-gene HGNC, Ensembl, Ensembl-homology and ClinGen-page lookups** in the gene-reference
  sync. The bulk HGNC set and GENCODE now supply what they did, and NCBI is the only per-gene
  call left (#447).
- **`REFERENCE_ALIAS_PATH` and `REFERENCE_CYTOBAND_PATH`** — settings nothing read (#553).

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
- **Clinical dialogs** — the review and ACMG dialogs no longer drop half-finished input on a
  stray click: they ask first, close on Escape, and keep keyboard focus inside. The coverage
  chart shows the gain/loss colour thresholds in use and marks thresholds changed in this
  browser (#547).
- **Quick Start ClickHouse login** — `.env.example` gave ClickHouse's built-in `default` user
  no password, which the ClickHouse image refuses for network clients, so the documented
  stack could not connect. It now names a `coga` user with a placeholder password (#553).
- **Frontend server path** — `server.mjs` answered every page with a 404 when installed under a
  path that runs through a dot-directory; it now serves `index.html` from its build directory (#567).
- **Structural-variant filters** — the structural-variant search now shares the small-variant
  search's filter-value helpers. A value typed with a stray space no longer appears twice, and a
  sample filter without a genotype selection gets the defaults instead of throwing (#569).
- **Package imports from a bucket** — a Package Import queued from a `gs://` or `s3://` folder
  was stored as `gs:/bucket/…`, no longer a bucket URI, so the worker looked for a local folder
  and the import failed. The URI is now stored unchanged (#570).
- **Requests stalled behind slow work (#527)** — password hashing, the haplotype lineage step,
  the NIPT fetal-fraction and classification work, the phenotype scoring behind the prioritised
  view and the HPO ontology parse now run in worker threads, so other users' requests are served
  meanwhile on the single worker. `SMTP_HOST`, which was read outside the settings, is now a
  documented setting (#568).
- **Gene-reference provenance and the variant card (#536)** — when GENCODE cannot be fetched, the
  fall-back to the UCSC gene table is reported rather than logged only, and each report's manifest
  records where CoGA's gene loci came from. The variant card's `g.` string is labelled "Genomic
  change", since it is not HGVS-normalised, and a transcript's CCDS and RefSeq are withheld when the
  variant names a different transcript version than the gene annotation holds (#573).
- **Dialogs and keyboard access (#529)** — the family-member, QC cut-off, family-deletion,
  transcript, carrier and reference-data dialogs close on Escape and keep keyboard focus inside.
  The family-member dialog asks before dropping edits not yet applied, and the QC cut-off
  confirmation before dropping a typed reason. Closing a dialog gives focus back to where it
  was, also when a control inside took focus as it opened. The SV second-hit badge and the
  genome overview's chromosomes work from the keyboard, and the badge shows a focus ring (#575).
- **Superuser accounts** — twelve checks tested for the literal role `admin`, so a superuser,
  an admin everywhere else, was refused when editing variant tags and gene panels, creating
  families, reading import jobs, reading a gene profile outside its projects and listing
  accounts. After a superuser's Monarch refresh the Mendeliome was not regenerated. Every check
  now counts both admin roles (#576).
- **Colour-only displays (#529)** — the pedigree's sample-QC verdict is a ring with a ✓, ! or
  ✕ badge, and no longer recolours the symbol, which had replaced the black affected fill and
  the carrier-type colour. Small variants are marked by shape as well as colour (a diamond for
  ClinVar P/LP, a triangle for HIGH impact, a hollow square for ClinVar B/LB), a review tag rings
  the mark instead of recolouring it, and the track has a legend. The risk-haplotype line is
  solid for an affected and dashed for a carrier haplotype, set off from the band (#577).
- **Track displays (#526)** — a view inside a gene's intron shows the gene. While a pan loads, a
  segmental duplication, blacklisted region or clinical CNV from the previous window is no longer
  drawn at the left edge under its own name. The zoomed ideogram gives each tick its own label at
  gene-level zoom, and on the genome overview a pathogenic repeat locus is drawn over its normal
  neighbours (#579).
- **Variant explorer filters** — "ClinVar P/LP overrules the frequency filter", on by default,
  was shown as applied but ignored, so a pathogenic variant above a gnomAD ceiling was left out; the
  explorer now applies it. Six filters it cannot apply (interval list, transcript, excluded genes,
  intervals and tags, saved notes) are no longer offered. The gene link opens the gene, a capped
  total reads 10,000+, a loading or failed assembly list is no longer shown as "no variants", and a
  sample id may contain ":" (#580).
- **Failures shown as empty** — the family SV summary and the clinical CNV explorer showed a
  failed load as "not enough data" and "no clinical CNVs match", so a failed lookup looked like a
  syndrome missing from the catalogue. They now say the load failed and offer a retry. The SV
  sharing matrix drops its totals, which counted a variant once per pair of carriers (#581).
- **Sign-up and user list messages** — after signing up, the page says the account awaits an
  administrator instead of opening a login that then fails; Sign Up cannot be sent twice. The user
  list says when a (de)activation failed and why (#582).
- **HPO sync and HPO term list** — "Apply sync" imports only the file and overrides that were
  just previewed; it could apply without a preview, or another file than the one on screen. The
  release date no longer shows a day early west of UTC, and "Back to SVs" keeps the project and
  filters (#583).
- **Charts for screen readers (#529)** — every track, chart, ideogram and the pedigree has a
  text name that says what it shows: how many genes, variants, loci or segments are in view and
  the salient ones, the haplotype risk state, or that it is loading or failed to load, never a
  failure as none (#587).
- **SV tracks cut without a trace** — in a view denser than one track page, the chromosome view's
  SV track drew only the left-most SVs, and for a large family the genome overview stopped at
  50,000 SVs, so the rest looked free of SVs. Both tracks now say that there are too many SVs to
  display; the genome track's limit counts the sample's own SVs (#590).
- **Stale marks under a track failure** — after a failed pan, the tracks drew the previous
  window's marks, misplaced, under the error overlay; they now draw nothing there. While a pan
  loads, the gene and DGV tracks no longer draw a held feature left of the new window at its
  edge (#591).
- **Circos page states** — a failed chromosome request no longer leaves the page loading for good,
  a failed SV request no longer reads as a family without SVs, and past 50,000 SVs the plot says
  there are too many to draw instead of leaving the last chromosomes without links (#593).
- **Risk haplotype on the genome overview** — the risk haplotype found at the ROI was also
  drawn on every other chromosome carrying the same homolog label, where it means nothing, and
  without an ROI the overview showed chr1's risk state. It is now drawn on the ROI's chromosome
  only, and without an ROI the risk state reads "not assessed" (#592).
- **SV lengths and quick-tag state** — the SV table and cards wrote 101 bp–1 kb in kb with one
  decimal (150 bp read "0.1 kb"); they now write bp up to 1 kb, as the report does. The Review,
  Exclude and Report toggles show their pressed state with a check mark and `aria-pressed`, not by
  a tint alone (#594).

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
- **Session and web tier (#521)** — logging out now clears the data the app had cached, so the next
  person at the same browser tab cannot see the previous user's families. API paths encode the
  identifiers they carry. The frontend server's `/api` proxy survives a backend reset
  mid-response, times out a backend that never answers, and no longer reveals the backend
  address (#548).
- **Backend configuration (#522)** — outside development the backend refuses to start with a
  short `SECRET_KEY`, without a ClickHouse password or without an integrity-anchor signing
  key, and never writes an unsigned anchor. The species list needs a signed-in user. The HPO
  ontology download is HTTPS-only, bounded and checked, and the knowledgebase build script no
  longer inherits the backend's secrets (#549).
- **Deployment configuration (#520)** — behind the load balancer the backend records the real
  client address, not one a client can set. The HTTPS load balancer requires TLS 1.2+.
  Reference data is mounted read-only, gs:// package imports are configured, only one event
  deploys to the single environment, and Terraform variables persist across deploys. CI
  pins its service images, and docker compose keeps the databases on loopback and the
  backend secrets out of the frontend container (#550).
- **ClickHouse placeholder password** — outside development the backend also refuses
  `change-me` as the ClickHouse password, as it already did for Postgres and admin (#553).
- **SOUP maintenance (#525)** — password hashing calls bcrypt 5.0.0 directly instead of the
  unmaintained passlib 1.7.4, which had held bcrypt at 3.2.0. Stored hashes verify unchanged,
  including passwords longer than bcrypt's 72-byte limit. Dependabot now also refreshes the
  digests of the pinned container images (#554).
- **Sign-up password** — a new account's password must be at least 15 characters; any string, the
  empty one included, was accepted. A development build no longer logs a failed sign-up or login
  request, which carries the password (#582).

### Documentation

- **Docs and repo hygiene (#530)** — AGENTS.md, the README, `.env.example` (now every backend
  setting with its default), `docs/database.md` (eight undocumented tables, plus the HPO and
  Monarch tables), TF-08 §A.2 (locked frontend versions and where each runs), RELEASING.md
  step 4 (the SBOM comes from the CI run of the tagged commit) and the `docs/testing.md` counts
  now match the code (#553).
- **Technical-file consistency** — requirement counts, a duplicate requirement ID
  (REQ-TRACE-008 → REQ-TRACE-011 for the provenance requirement), the SOUP register reconciled
  with the lockfile and runtime end-of-life dates, and the hosting and processor statements for
  the Google Cloud deployment (#552).
- **Sign-out authority** — the instructions for use and the user documentation state that only
  authorised signatories may sign out a report. CoGA does not check signing authority itself;
  this is recorded as an accepted residual risk (TF-06 H15) (#551).
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
