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
the baseline they are measured against would be inventing evidence, not recording it. Before the
first release candidate a change carries no TF-18 record
([TF-18 §3a](docs/regulatory/TF-18-change-configuration-management.md)); one that alters a
clinical output is listed, with a **proposed** level that QA has yet to confirm, in
[TF-10 §8](docs/regulatory/TF-10-performance-evaluation-plan.md), the scope of the first
validation.

The work from before this file was started (2026-07-28, #396), when CoGA had 353 merged pull
requests, is summarised here rather than transcribed: the authoritative record is the git
history and the
[pull-request list](https://github.com/bmenten/CoGA/pulls?q=is%3Apr+is%3Amerged).

**From the first release candidate onward, each entry carries its TF-18 level**, like so:

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
- **ClinVar support in the Clinical CNV Explorer** — the knowledgebase's per-region count of
  pathogenic ClinVar CNVs, per side, is stored in `clinical_cnvs` and shown in the explorer and on
  the CNV page, with a link to each supporting ClinVar record. A knowledgebase built without
  ClinVar reads "not recorded", not 0 (#625).
- **Scheduled ClickHouse integrity result for admins** — an admin endpoint and the ClickHouse page show each
  assembly's last scheduled integrity result and when it ran, or that it could not run (#660).
- **NIPT artifact list in the clinical audit trail (#683)** — each add, update, removal and
  auto-seed through the admin API is a hash-chained clinical audit event with the actor, the variant
  and its state before and after, on a chain of its own (`system:nipt-artifacts`) that
  `/admin/integrity/verify` checks. The list decides which variants every NIPT analysis of its assay
  filters out (#700).
- **Computed-style diff for stylesheet changes (#712)** — `frontend/scripts/stylediff` renders two
  production builds side by side against the same seeded stack and compares every element's computed
  style, pseudo-elements and box over ~110 routes at three widths, with forced `:hover`/`:focus`,
  expanded `<details>` and print media; `scripts/seed_style_diff_demo.py` seeds the NIPT demo and the
  demo quartet for it. It verified that the CSS clean-up changed nothing on screen. A manual tool, not
  a CI job (#720).

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
  - Backend: FastAPI 0.141.1, Starlette 1.7.0, uvicorn 0.54.0, SQLAlchemy 2.1.1, pydantic
    2.13.5, pydantic-settings 2.15.0, clickhouse-connect 1.9.0, PyJWT 2.15.0, pysam 0.24.1,
    pyBigWig 0.3.26, lxml 6.1.3, anyio 4.14.2, soupsieve 2.9, plus cloud and support libraries
    (#409, #438, #503, #504, #506, #565). pandas 3.0.6 (#691), which no longer needs pytz and
    tzdata, so both leave the lock (76 packages); its one user, the clinical CNV knowledgebase
    build, gives byte-identical output for GRCh38 and GRCh37 under pandas 2.3.3 and 3.0.6.
  - Frontend: React 19.3.0, react-router 8.4.0, TanStack Query 5.104.0, axios 1.20.0, igv.js
    3.8.9, Vite 8.3.1, qs 6.16.0 (#406, #441, #496, #505, #692).
  - Test and lint tooling: jsdom 24 → 30 (#435), @testing-library/jest-dom 7, vitest 5.0.2 with
    @vitest/coverage-v8 5.0.2, Playwright 1.63.0, typescript-eslint 8.70.1, @eslint/js 9.39.5
    and @types/node 26.6.3 (#408, #441, #502, #505, #629, #692). The jest-dom matchers are now
    typed through its vitest entry, which vitest 5 needs.
  - GitHub Actions: setup-python 7 and setup-node 7 (#404, #405), CodeQL action 4.38.2 (#433,
    #480, #561).
  - Container images: the rebuilt `postgres:16` digest in compose and CI (#559) and the
    rebuilt `node:22-alpine` digest of the frontend image (#564).
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
- **Small-variant page split (#528)** — the 441-line function behind every small-variant page
  resolves the request's scope once and serves each path from a function of its own; a test
  recorded before the split shows every path makes the same calls and returns the same page (#595).
- **User guide in Markdown (#528)** — the in-app guide's 20 sections are Markdown files under
  `content/docs/user-guide/` instead of 2,000 lines of JSX; a test holds each section to the text
  it had before the move (#596).
- **Generated API types (#528)** — the frontend's API types are generated from the backend's
  OpenAPI schema, CI fails when they are stale, and `tsc` checks the hand-written types against
  them; a family member's `sequencing_qc` may be null, and two stale NIPT types are gone (#597).
- **Dataset importer registry (#528)** — the package import's 15 dataset importers register
  themselves by type and take one import job, replacing a 160-line dispatcher; a test holds the
  registry to the supported dataset types (#598).
- **Family member dialog (#528)** — the member dialog (metadata draft, HPO phenotypes, removal)
  moved out of the 2,350-line family page into a component of its own; tests of its flows,
  recorded before the move, pass after it (#599).
- **Filter form sections (#528)** — the small-variant filter form, one ~1,800-line component, is
  split into a shared section shell and 11 section components; a markup snapshot recorded before the
  split shows the form renders exactly as it did (#600).
- **Package-import and review imports (#528)** — modules import the package-import and
  small-variant review helpers from the modules that define them, not through
  `family_package_import.py` and `small_variant_review_pg.py`, which passed 213 and 19 names on
  (#619).
- **Python tool configuration (#673)** — ruff, mypy, pytest and coverage read one `pyproject.toml`
  instead of `ruff.toml`, `mypy.ini`, `pytest.ini` and `.coveragerc`. The lint gate also refuses a
  broad `except` that neither re-raises nor logs its traceback (BLE001) and an import below module
  code (E402) unless a `noqa` says why, and a `noqa` that no longer suppresses anything (RUF100): 63
  stale markers are gone, and 32 broad handlers now carry their reason. `.gitignore` and
  `.dockerignore` cover `.env.*`, `.claude/` and the coverage files (#687).
- **Held-back dependency majors (#674)** — Dependabot no longer proposes ESLint 10, @eslint/js 10 or
  TypeScript 6.1 and later, which the lint plugins don't support yet (eslint-plugin-react and
  jsx-a11y accept ESLint 9 at most; typescript-eslint 8 needs TypeScript below 6.1). ESLint and
  @eslint/js now update in one PR. Dependabot #376, #377 and #556, which failed CI for that
  reason, are closed (#688).
- **CI workflows (#677)** — the three integration jobs share one datastore `services` block (a YAML
  anchor), so an image digest is bumped in one place; the `sbom` job runs `scripts/generate-sbom.sh
  --native` instead of a copy of it, so the generator pins live in the script only; CodeQL also
  scans the production frontend server, proxy and CSP (`frontend/*.mjs`) and `scripts/`, one of
  which the knowledgebase rebuild runs; the `master` branch triggers are gone; and the Playwright
  step says it serves the production bundle, not the Vite dev server (#694).
- **Test gates (#678)** — the 13 e2e modules that skipped when their committed fixture was missing
  now fail, since a missing fixture is a broken checkout. Every module the type-check gate lists has
  a coverage floor, and the floor script fails when one is added without; the floors (unit and
  combined, global and per module) are raised to 3 points under what CI measured. The frontend's 67
  explicit `any`s are typed, so ESLint now allows no warnings at all. The package-validation command
  has a test (#695).
- **Shared helpers (#684)** — the PED parser and its row type (four and six copies), the count-with-noun
  formatter of four charts, and the ClickHouse table-name and UUID-check helpers (three copies each)
  each live once now (`lib/pedigree.ts`, `lib/countOf.ts`, `clickhouse_variant_ids`, `core/sql.require_uuid`).
  The APCAD and coverage-segment charts use the shared chromosome normaliser, so a `chrx` or `chr01` in
  the data now reaches the X or 1 panel, as on the other tracks. The `formatBp` variants stay: each
  formats for its own scale (#701).
- **CSS custom properties (#708)** — `theme.css` read 13 custom properties that it never defined. Where a
  use had a fallback, the fallback rendered; without one, the property was unset. `--radius-sm`
  turned out to come from Tailwind's theme layer (4px), so its 6px fallback never applied. The danger
  colour is now the signature red it always was, and the warning and danger surfaces, borders and text
  are real tokens with the values they fell back to. One-offs are written as the value they render, and
  the three uses without a fallback say what they render (`inherit`, `unset`). Nothing renders
  differently. The stylesheet test now also fails on a custom property that neither the stylesheet nor
  a component defines (#715).
- **CSS duplicates (#709)** — five selector lists that `theme.css` spelled out in two rules each are
  one rule now (`.surface-card`, `.gene-profile-stat`, the assemblies-table cells, the embedded catalog
  toolbar), a `max-width` that a later rule always overrode is gone, and three pairs of neighbouring
  rules with the same declarations share one. Each merge was checked against the cascade: no rule in
  between sets a related property at the same specificity on an element both could match. Nothing
  renders differently. The rest of the apparent duplication is the shared-base-then-override pattern
  (`button, .form-button {…}` then `.button-secondary {…}`) or coincidences between unrelated components,
  and stays (#716).
- **CSS colour tokens (#710)** — 39 colour literals that were the value of an existing token, used in
  that token's role, are the token now: hairline borders `--color-border`/`--color-border-strong`, white
  surfaces and text `--color-surface`/`--color-white`, and the brand and accent colours. A literal that
  only happens to share a token's value in another role (a border colour used as a background) keeps
  its value. Nothing renders differently. The file still holds 355 distinct colour literals, many of
  them near-identical shades of the same few colours (three warning-text browns, four muted greys, six
  near-white surfaces); unifying those would change pixels and is left for a design decision (#717).
- **Stylesheet modules (#711)** — the 11,000-line `theme.css` is now the ordered list of 28 modules
  under `styles/theme/` (tokens, base, layout, docs, the family workspace, variant filters, tables and
  cards, genes, repeat expansions, paraphase, mitochondrial DNA, the genome view, admin, projects,
  modals, the variant explorer, the report and more; the largest is 22 KB). The modules are
  consecutive slices of the old file in its own order, so the cascade is unchanged: the built CSS is
  byte-identical. The stylesheet test reads the modules in import order and fails when `theme.css`
  holds a rule of its own or a module under `styles/theme/` is not imported (#718).

### Removed

- **Mouse-wheel zoom in the chromosome view** (added in #366) — the wheel scrolls the page
  again. Drag-to-select and the zoom controls are unchanged (#489).
- **Per-gene HGNC, Ensembl, Ensembl-homology and ClinGen-page lookups** in the gene-reference
  sync. The bulk HGNC set and GENCODE now supply what they did, and NCBI is the only per-gene
  call left (#447).
- **`REFERENCE_ALIAS_PATH` and `REFERENCE_CYTOBAND_PATH`** — settings nothing read (#553).
- **Unused dependencies (#675)** — `lxml` (the knowledgebase script parses HTML with the standard
  library parser), `autoprefixer` (Tailwind 4 already prefixes what the supported browsers need; it
  only added prefixes for Firefox and Opera releases from before 2019) and the direct
  `@typescript-eslint/eslint-plugin` and `/parser` entries, which `typescript-eslint` brings in.
  `python-dotenv` stays: it reads the `.env` file (#689).
- **ClickHouse schema upgrades (#679)** — the code that brought older ClickHouse tables up to date:
  the migration that dropped the per-sample summary without `project_guid`, the drop of the retired
  `gt_stats` aggregates, the read path's tolerance for that old summary, and four `ALTER`s that
  added or dropped columns the `CREATE TABLE` statements already define. The data is synthetic, so
  the tables are created from their final definition only; a local database with an older schema
  is reset (`docker compose down -v`). A table with an older row identity is still refused (#696).
- **Postgres schema upgrades (#680)** — the statements in the baselines that upgraded an older
  database: six `ALTER TABLE … ADD COLUMN IF NOT EXISTS` for 11 columns the `CREATE TABLE`s already define, the
  `DO` block that rebuilt `gene_panel_regions` per assembly, and the one that closed the rebuild
  jobs an old index let through, with that index's `DROP`. A fresh database gets the same schema
  (compared column by column, with every constraint, index, trigger, function and grant), and a
  database from an older schema is reset, not migrated (#697).
- **Vestigial API fields, aliases and routes (#681)** — the explorer page's `page` field (unused
  since keyset paging, #274); five deprecated S3-named aliases in `object_storage`; family-scoped
  small-variant presets, which no screen created since presets became reusable (a preset is now
  its owner's, one per name, and the preset tables lose their *Scope* column; structural-variant
  presets keep their family or reusable choice); and the frontend redirects `/family-intake`,
  `/families` and `/admin/operations/variants`. The NIPT page's back link goes to the dashboard
  (#698).
- **API endpoints without a caller (#682)** — `GET /reference/sequence` and `/reference/reads/{sample_id}`
  (with `pyfaidx` and the `REFERENCE_FASTA_PATH` and `READS_PATH` settings only they read), the per-sample
  `GET /structural-variants/{sample_id}` (the family SV list takes a `sample` parameter), `GET
  /admin/projects` (the data inventory shows a family's projects), `POST /hpo/import` (`POST
  /admin/hpo/sync`, behind *Admin → HPO Terminology*, does the same with a preview) and `GET
  /families/{id}/members/{sample_id}/impact` (a member delete without `confirm` answers with the impact).
  `GET /panels/{id}/versions/{version}` stays, to look up the panel version a report names (#699).
- **External fetal fraction (#683)** — the NIPT summary and variant list no longer accept the
  `external_ff` parameter no screen sent. It could replace the computed fetal fraction when too few
  informative sites gave one; the fetal fraction is now always CoGA's own estimate. Its disagreement
  flag and REQ-NIPT-005 go with it (#700).
- **Pre-release signed-record formats (#681)** — the sign-out check, the signed view and the docs no
  longer carry special readings for records signed by earlier development builds: the assumed module
  list of a record without one, the "signed before CoGA froze it" gap and the uncompared evidence of
  a record without SV/CNV drift, and the empty SV list assumed for a record without one. The release
  candidate's snapshot format is the first CoGA reads. The mechanism for later formats stays: a module
  or section a record does not hold is not compared and reads as not in the record. Old records still
  verify (#705).
- **Dead CSS (#707)** — `theme.css` loses the 331 rules whose selectors named classes no component
  renders any more (remnants of the gene explorer, the gene profile, the compact checklist, the old
  dashboard, variant-card banners and more), 33 dead selectors in lists that stay, 4 declarations a
  later rule with the same selector overrode, and 7 custom properties nothing read: 40 KB (15 %) of
  the file, 31 KB of the built CSS. Every page renders exactly as before, checked by a computed-style
  diff of the old and new builds. A test now fails when the stylesheet names a class the source
  cannot produce, or defines a custom property nothing reads (#714).

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
- **Escape in a dialog just opened** — a dialog answered Escape only after a later task, so an
  Escape pressed as it appeared closed the dialog underneath (the reference upload instead of its
  overwrite confirmation); a dialog now answers Escape from the moment it is drawn (#601).
- **Track states that asked for nothing** — over a view with no width (start at or past the
  end), the chromosome view's tracks named themselves "loading" for good, the small-variant track
  said there were too many variants, and the SV and repeat tracks said there were none. They now
  say there is no region in view, and ask the API for nothing. The genome overview's SV and
  repeat tracks no longer say "none" before the genome layout is known, and the coverage chart's
  name says when it failed to load, is loading or holds no data. The mitochondrion is chrM in
  every track's name and chromosome list, as in the viewer header; it was chrMT in some. A
  histogram of equal-width bins narrower than 1 no longer draws its bars on top of each other
  (#603).
- **A failed project catalogue hid the validation banner** — every family page reads its
  reference assembly, and whether that assembly is inside the validated scope, from the project
  catalogue. When that request failed, the scope stayed unknown, so the *Not validated for
  clinical use* banner never appeared. The pages said "Reference not linked", the small-variant
  page waited for good, and the report rendered without variants. The pages now say that the
  reference could not be loaded and that the validated scope is unconfirmed, with a retry, and
  the report is not prepared until it loads (#611).
- **Report pages that printed a failure as complete** — when a request failed, the family report
  could render as "0 small variants and 0 structural variants", drop its reported SVs, call a
  signed case a draft, and print missing gene descriptions, HPO terms, drift check, audit trail
  or provenance as "none". The NIPT report printed "No fetal-fraction estimate is available"
  when the estimate could not be loaded. Both pages now show no report without the family or
  its reported variants. They mark each part that could not be loaded where it belongs, and a
  printout then starts with "Incomplete — … could not be loaded". While the sign-out record is
  unknown, sign-out is not offered (#612).
- **SV and NIPT searches that read a failure as no variants** — a failed SV search showed an
  empty table and "Showing 0", and a failed NIPT search showed "No variants match the current
  search". A failed NIPT summary hid the fetal fraction and its low-confidence and disagreement
  warnings, and counted every category and filter step as 0. A failed coverage QC stayed
  "Loading coverage…". A failed panel list left the select at "Any gene panel" while a panel
  from the URL was still applied, and dropped the default Mendeliome scope without a word.
  Each is now said where the result would be, with the server's reason and a retry. The counts
  read "—", and an applied panel stays visible in the select (#613).
- **Viewers, ROI markers and family page that read a failure as missing data** — failed requests
  were shown as data that is not there:
  - a failed track-availability request as "No BED data for selected samples";
  - failed ROI markers as "0 markers in view · 0 informative for embryos", an uninformative
    region;
  - a failed family as "No region of interest";
  - failed haplotypes at the ROI as an embryo with no segregation badge and no recombination or
    uninformative warning;
  - a failed presence check as a missing workspace link, with "Checking…" left up for good;
  - failed curation counts and HPO terms as 0 and "-";
  - a failed status list as "No status" on a reviewed case.

  Each is now said as a failure, with a retry. An embryo reads *⚠ segregation not derived*. A
  workspace whose check failed keeps its link. The current status stays shown, and cannot be
  changed until its list loads (#614).
- **Location filters dropped without notice** — an interval-list entry that did not parse was
  skipped: a BED line, a single position, an en-dash range or an end before its start. The search
  then covered less than the list asked, and a list with no readable entry came back as a family
  without variants. A malformed region in the SV search's "Gene or region" box, such as
  `chr1:100-`, was searched as a gene name and read as no SVs. The monogenic NIPT search sent,
  and chipped, the interval list, excluded intervals and excluded genes, but never applied them.
  An unreadable entry is now named under its field before the search, and refused by the server
  (422) if it arrives in a URL. An en-dash range and a single position are read. The NIPT search
  applies all three filters (#615).
- **ACMG suggestions that changed without notice** — when the gene profile could not be loaded,
  the ACMG dialog said nothing. PVS1's evidence read "LOF disease mechanism is unconfirmed", as
  if the gene had been checked, and PP4 was not offered when the HPO terms failed. The dialog
  now names the failed lookup, with a retry. PVS1, BS2 and PP4 say they are not assessed and are
  offered for review. No point total changes (#616).
- **Secondary pages that showed a failed request as none** — a failed request on a variant card,
  the member dialog, a gene panel, the chromosome and genome viewers, the pipeline settings and
  annotation provenance, the gene page, the panel, project and reference catalogues, the
  package-import folder scan and IGV's depth and MAF tracks read as empty, or loaded for good. The
  family-scoped pages titled any failure "Family not found". Each now says it could not load, with
  the server's reason and a retry; "Family not found" is kept for a family the server does not know
  (#617).
- **De novo on a son's X and Y** — outside the pseudo-autosomal regions a male carries one X and one
  Y, so a de novo variant there is hemizygous (`1` or `1/1`). The de novo patterns required a
  heterozygous child, so it was never a de novo candidate. It now is, when the mother (for the X) or
  the father (for the Y) is confidently reference and the other parent does not carry it; a
  mother–son duo suffices for the X. Autosomes, the pseudo-autosomal regions and daughters keep the
  diploid rule (#620).
- **ACMG de novo (PM6) for a son's X and Y** — the ACMG dialog judged PM6 from both parents' calls
  without regard to sex. A son's chrY de novo, where the mother has no call, read "de novo cannot be
  assessed", and an X variant carried only by the father read "Inherited from a parent". Where a son
  is hemizygous, the parent who passes him that chromosome now decides, as in the de novo filter;
  the backend says where that is (`hemizygous_in_males`) (#622).
- **ClinVar support counts in the CNV knowledgebase build** — the build read a ClinVar CNV's
  loss/gain side from words in its name only. An array record, named in ISCN notation
  (`…(chr7:73330452-74799773)x1`), therefore counted toward neither side of a region's support. The
  side now comes from ClinVar's `Type`, then from the copy number in the name (on X and Y only `x0`
  and `x3` or more), and only then from the name's words. CoGA does not load these counts into
  `clinical_cnvs`, so nothing the app shows changes (#623).
- **CNV page: failed or missing** — the clinical CNV page said "CNV not found." for any failure; a
  server error now reads "CNV could not be loaded", with the reason and a retry (#625).
- **Deploy: a merge to main ships its code** — every main build was tagged `:main`, so after the
  first deploy Terraform saw no new image: Cloud Run kept the previous code and the database
  migration job never ran again. Main builds are now tagged with their commit,
  `main-<12-character commit>` (#632).
- **IGV reads in Google Cloud** — with Google Cloud Storage as the store, IGV reads aligned reads
  from the PHI bucket in the browser, which needs a CORS policy the bucket lacked. The bucket now
  lets the app's own origin read it, and nothing else (#633).
- **Review edits keep a CNV classification** — toggling a tag or saving the review dialog on an
  SV/CNV no longer erases its ClinGen classification, nor the classification label the ClinGen
  dialog set; sending `cnv_acmg` as null clears the scoring on purpose (#639).
- **Per-sample SV upload scoped to its own source** — a Sniffles, Spectre or manual upload now checks,
  merges and replaces only that caller's calls. It refused a sample's first upload over its NeedlR
  calls and, on overwrite, duplicated other sources' SVs so that a later merge dropped the sample's
  calls. An unknown `source_format` is refused (#643).
- **ACMG suggestions: conflicting ClinVar and the right parents** — the ACMG dialog and the mtDNA
  table no longer read a ClinVar "Conflicting classifications of pathogenicity" record as
  pathogenic. PM6/PS2 and the mtDNA maternal transmission use the parents the pedigree links, not a
  grandparent who shares the role. PM6 is offered for review instead of applied when a reference
  parent has under 8 reads or the proband is homozygous (#644).
- **Sign-out and incomplete imports** — sign-out refuses a family whose data import only partly
  succeeded unless the signer gives a reason, which is frozen into the signed record and the audit
  trail; every family page warns while the import is incomplete, naming the failed datasets and the
  import job that holds their errors (#650).
- **Compound-het phase and the SV second-hit index** — the second-hit badge no longer calls an
  SNV + SV pair trans, or "effectively biallelic", on the strength of relatives who carry neither
  hit. Phase comes from the reads or the parents, by the same rule for SNV + SNV pairs, which now
  also show trans by segregation. The SV→gene index is rebuilt after any SV change, not only after
  a package import (#645).
- **Monogenic NIPT consistency** — the variant list reports and classifies against the Summary's fetal
  fraction; categories respect the father's genotype, and a father call under 10× counts as no call;
  paternity rests on confident father calls only; haploid chrX calls inform fetal sex and the
  father's sex check; artifact auto-seeding counts only the assay's cfDNA samples and never lists a
  common or ClinVar pathogenic/conflicting variant; the Sample-QC NIPT summary describes the checks
  that run (#646).
- **Clinical CNV knowledgebase rebuild no longer gets stuck** — a rebuild requested during another,
  or cut off by a restart or redeploy, stayed active for good and refused every later rebuild. A
  rebuild is now refused with a 409 only while another is really active; one whose server has
  gone quiet for ten minutes is closed as failed; a build stops after
  `CLINICAL_CNV_KB_BUILD_TIMEOUT_SECONDS` (default two hours); a database holding a stuck job is
  repaired on upgrade (#642).
- **HPO release in the signed record** — a signed report now records the HPO release it was produced
  with, and a report signed before this is not shown as changed for lacking it; the HPO admin
  summary and the prioritised-ranking cache follow the release that is actually loaded; the HPO
  admin page starts from the file the backend loads (`hp.obo`, not `hpo.obo`) (#647).
- **Device label and problem route** — the app footer and the family and NIPT report footers name the
  running build (version and short commit), the in-house-IVD status and the manufacturer; "Report a
  problem" goes to the CMGG route set by `VITE_PROBLEM_REPORT_URL`, and a production build without it
  no longer links the public GitHub issue form (#648).
- **SV/CNV review changes are audited** — SV/CNV classification (the ClinGen scoring included), tag
  and note changes, and a cleared small-variant review, now write hash-chained clinical audit events
  (#649).
- **PGT embryo calls** — an embryo without its own haplotype across the ROI reads *uninformative*,
  not *unaffected*, and a recessive embryo whose other homolog is unseen is not called a carrier;
  an unconfirmed phasing side no longer defines the disease haplotype; an X-linked recessive embryo
  of unrecorded sex is not called a carrier when a son would be affected (a "sex unknown" warning
  gives both calls); a male embryo at a pseudo-autosomal locus is read on both copies, so a paternal
  risk haplotype there is called (#652).
- **Bucket package imports in cloud mode** — importing a family package from a gs:// or s3:// folder
  no longer downloads its CRAM/BAM files, and the genome browser shows its reads and its depth, MAF
  and copy-number tracks from the bucket. Raw-file provenance records each file's SHA-256 and size,
  or the bucket's own record for files left there; the family record, import log and validation
  report name the source folder, and a package without `family_id` is named after it; S3 discovery
  lists every package; the admin raw-files view no longer calls a bucket file missing (#653).
- **Per-sample SV rewrites keep every other call** — an SV upload or admin delete for one sample writes
  every other stored call back unchanged (phase set, breakend end, project, inactive members' calls);
  a per-sample SV upload also records its caller in the family's annotation versions (#654).
- **Classified variants reopen with their saved criteria** — every list of small variants (the tables and
  cards, the report, the NIPT candidates, the mtDNA analysis) now serves each review with its ACMG record.
  The lists left it out, so the ACMG dialog reopened a classified variant with the pre-evaluation alone, a
  save replaced the stored criteria with it, and the report showed no classification motivation. The
  family's review count also counts a review that holds only an ACMG classification (#662).
- **Deleting a sample keeps the other members' small variants** — the whole-sample delete writes every
  other stored call back unchanged, in every callset (the imputed one included), project and field; it
  used to rebuild them from the family view and wipe the imputed callset, an inactive member's calls
  and rows in projects the family had left (#655).
- **Signed report drawn from its record** — a signed case opens on its signed version, rendered and
  printed from the frozen snapshot alone (version, date, signer, content hash, each reported variant's
  classification, criteria, frozen evidence and note, the checks at sign-out). It says what the record does
  not hold (the variant description, gene and phenotype context) rather than filling it in from live data.
  The live report of a signed case is labelled as not the signed version, on screen and in print, even
  when it matches; after sign-out the page shows the version just signed (#659).
- **SV/CNV evidence drift** — saving a CNV (ClinGen) classification freezes the evidence it rests on (type,
  genes and gene count, pLI, annotated inheritance, caller and locus, an annotation hash, the SV callset's
  versions). The drift banner and the sign-out drift gate cover SV/CNV classifications, a reported one
  without frozen evidence included, with the same acknowledgement, and a signed version lists them under its
  evidence drift at sign-out; a scoring for an SV not in the data is refused. Earlier signed reports still
  verify, are not compared on what they predate, and say they hold no SV/CNV drift (#661).
- **A variant's calls from two callsets are both kept** — a clair3 and a GLIMPSE2 row of one small variant,
  or a Sniffles and a Spectre call of one SV at the same breakpoints, shared their ClickHouse sort key, so a
  part merge kept only one: the direct call could be lost and the variant leave the diagnostic lists. Each
  stored row is now identified by its callset, and the SV list shows such an SV once per caller; variant
  ids are unchanged. The backend refuses to start on variant tables with the older sort key: recreate the
  assembly's small-variant and SV tables and re-import its families (docs/database.md, "Row identity")
  (#658).
- **NIPT report no longer cut short without a trace** — the report listed the first 500 candidates of its
  scope as if they were all, and the variant list behind it stopped at 5,000 classified variants with a
  total that looked exact. The report now says how many it lists of how many, where the list stops and
  why, on screen and at the top of the printout, and each group counts what it lists; the NIPT page says
  when its list stops at the limit. A *Scope* section names the gene panel (with its version) and the
  genes, and says no other filter of the NIPT page applies (#656).
- **A family small-variant upload that works says so** — the upload answered every successful upload
  with a 500 after storing its rows, so the Upload page reported a failure and a retry met a 409; it
  now answers 200 with its result. An unknown `source_format` is refused (422) before anything is
  stored (#666).
- **Importing one sample's mitochondrial calls keeps every other sample's** — each file replaced the
  family's whole mito callset, so only the last sample's chrM calls remained (a file without variants
  removed them all); the mother's and siblings' calls, which the maternal transmission and the mito
  ACMG PP1/BS4 read, were lost. Each file now replaces only its sample's calls (#666).
- **The audit trail records every change to a classification** — a save that changes only a
  criterion's strength or points, its evidence or a suggestion now writes a clinical audit event
  with the whole criteria record before and after, for small variants and CNVs; an unchanged
  re-save still writes none (#665).
- **ClickHouse integrity check on real data** — the check (the admin action and the scheduled monitor)
  crashed on any variant table without rows, so the endpoint answered 500 and each sweep logged a failure
  and recorded no status for that assembly; and it judged a table with several parts on its first part only,
  so a corrupt later part passed. It now takes ClickHouse's own verdict on every part of each table, names
  the failed parts, passes a table without rows, and never passes a result it cannot read. The gene-index
  rebuild's check before its swap had the same flaw and is fixed with it (#663).
- **Reference imports record their release** — a GENCODE import records its version and date; every other
  source records `not stated`; an upload is recorded as `upload` (it used to take its last row's source) and a
  built-in file under its file name (#660).
- **Structure and HPO stale markers are stored** — a structure update records whether it kept the imported
  data (`raw_datasets_preserved`), and a clear is recorded and warned about; before, neither marker was ever
  written (#660).
- **A structure save refreshes the genome-overview lineage and warms the ranking**, like a member edit (#660).
- **Re-run match runs again** — the phenotype-match panel's *Re-run match* did nothing and kept the first
  ranking. Each press now runs the match on the phenotypes recorded then; a run in progress and a failure
  (with its reason) are shown as such, and an earlier ranking never stays on screen as the current one
  (#657).
- **Variant summary button** — the button showed for families with small variants only, where the
  structural-variant summary had nothing to show; it now appears when the family has structural variants
  (#657).
- **Circos plot per assembly** — the Circos page drew every family on GRCh38's chromosomes; it now draws the
  family's own assembly, and draws nothing, saying why, when it has no linked project, no chromosome sizes
  or chromosomes it cannot place (#657).
- **A source-scoped SV delete leaves none of that source's rows behind** — since each caller's call of an
  SV has its own key (#658), it also has its own `SV/variants/details` and `SV/key_lookup` row. Deleting
  one source, the delete half of a per-sample SV upload's overwrite and of a package dataset's re-import,
  removed only its `SV/entries` rows, so the details and lookup rows of every SV it removed stayed behind,
  with no entry to reach them. The delete now also removes the source's details and lookup rows whose key
  no remaining entry of the family has. Both tables are family-scoped, so other families' rows are neither
  read nor touched; other sources' rows stay as stored, and a row that another source's entry still reaches
  is kept. No page, count or report changes: nothing read those rows (#668).
- **A family's variant writes run one at a time** — every write of a family's variants reads its stored rows,
  deletes them and inserts them again changed, in ClickHouse, which has no transactions: the per-sample SV
  and small-variant uploads, the admin sample and family deletes, a package import's datasets and its
  restore of a failed overwrite, a PED overwrite and a structure change that clears the data. Nothing kept
  two of them apart. Two writes of one family started together (PROBAND's and MOTHER's Sniffles uploads, an
  admin delete and an upload) each wrote from what it had read before the other wrote, and both answered
  200: one writer's calls were lost, a deleted sample's call came back, a variant was stored twice with
  different calls (a part merge then keeps one), or an SV lost its details row, with its span, length and
  annotation, to the other write's delete. Each writer now holds a Postgres advisory lock on the family's
  small variants, its SVs or both, from before its read until its transaction ends, so the second write of a
  family waits for the first, in any worker or process; a package import holds both from before its
  snapshot until it has finished or restored the family. A write that waited for the deletion of its
  sample or family writes nothing and answers 404 (#670).
- **A package import records each sample's mtDNA haplogroup, and the mito import leaves nothing in
  `/tmp`** — the import read a sample's haplogroup from its mutserve annotation after the upload had
  already closed that annotation, so no package import ever recorded one. The mtDNA workspace showed
  *n/a* for every sample, and the haplogroup comparison that the Sample QC review asks for in
  mitochondrial cases (TF-01 §4 condition 7) had nothing to compare. The upload now closes only an
  annotation it parsed itself. The import reads the haplogroup and then removes the annotation's
  temporary database whatever happens; a file naming a sample the family lacks used to leave it in
  `/tmp` (memory, on Cloud Run) until the instance restarted. A VEP or mutserve table whose parse fails
  part-way no longer leaves its database behind either (#671).
- **Sample-filter values that can't be read (#686)** — a per-sample minimum (GQ, DP, AF or AD alt, or
  an SV's QUAL) that is not a number fails the search with a 422 that names it. An unreadable AF or
  AD-alt minimum used to be dropped, so the search returned more than asked without saying so, and an
  unreadable GQ or DP minimum surfaced as a 500. A raw-file provenance record that can't be written
  still doesn't fail the import, but the loss is now logged with the family, dataset and file (#690).
- **Clinical CNV knowledgebase sources** — the GRCh37 build fetched ClinGen's recurrent-CNV regions
  from `…-hg19.bed`, a 404 (ClinGen publishes `…-hg37.bed`), logged it and went on: every GRCh37
  knowledgebase lacked them. A rebuild now adds 57 regions (1q21.1 TAR, 7q11.23 Williams-Beuren,
  15q11.2q13 PWS/AS, 16p11.2, 22q11.2 and more) and gives 29 curated regions that source too. In
  GRCh38, ClinGen writes X as `chrx`, which the build kept as `x`, so the clinical-CNV queries for X
  never returned four X-linked recurrent regions (Xp22.31, Xp11.22p11.23, two in Xq28), and they had no
  cytoband; chromosome names are now read case-insensitively. When ClinGen's dosage curation or
  recurrent regions cannot be loaded the build stops instead of writing a knowledgebase without them: the
  rebuild fails, keeps the knowledgebase it would have replaced, and its error, now shown on the
  Reference catalogue, says why. Rebuild the knowledgebase of both assemblies after upgrading (#721).
- **A first `docker compose up` starts the frontend** — on its first boot the backend downloads
  GENCODE, RefSeq and HPO before it answers its health check, which took about 150 s, and the
  healthcheck allowed about 165 s (a 90 s start period plus five 15 s retries), with a probe failing
  now and then while the reference sync ran. Compose then marked the backend unhealthy and left the
  frontend `Created`, reporting *dependency backend failed*. The start period is now 10 minutes, and
  probes run every 5 s during it, so the backend turns healthy as soon as it answers (#713).

### Security

- **Log forging** — the warning logged when a failed small-variant rewrite cannot restore
  the rows it read now scrubs the source label and family id of control characters, and
  `scrub_log` strips line feeds in the form CodeQL recognizes as a sanitizer, so its call
  sites stop being reported (CodeQL py/log-injection).
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
- **Google Cloud go-live hardening, ready and off by default** — `db_runtime_role = "coga_app"`
  runs the API as the restricted database role, with a migration job under its own account that
  applies the schema and enables the role's login, handing Postgres a SCRAM verifier rather than
  the password. `clickhouse_restrict_egress` limits the ClickHouse VM to Google APIs. The deploy
  job refuses to run until `gcp-deploy` has required reviewers, and ingress ranges are validated
  as CIDRs (#627).
- **QC-limit history closed to the runtime role** — the restricted database role `coga_app`
  could still update and delete `qc_threshold_changes`, the append-only history of QC
  acceptance limits; only its trigger refused. It now holds INSERT and SELECT only, like the
  other append-only tables. The integration test reads every trigger-guarded table from the
  catalogue and fails if one keeps UPDATE or DELETE (#630).
- **QC-limit history tested on the server** — a cut-off edit is now shown, in a unit test and
  against the real database, to record the limit it replaced, the new limit, who made the
  change and why; a change without a reason is refused and records nothing (#631).
- **Reference-data bucket read-only to the app** — the backend's service account held a role
  that could write the reference-data bucket, which is mounted read-only and never written by the
  app. It now holds the read-only `roles/storage.objectViewer`, as on the PHI bucket (#634).
- **Audit log and manifest controls enforced** — outside development the backend refuses
  `AUDIT_LOG_MODE=off` and an `INTEGRITY_ANCHOR_SIGNING_KEY` equal to `SECRET_KEY`; only an admin can
  replace a family's annotation manifest, which is always recorded as manual, with the manifest it
  replaced, on the family's audit trail, and an import can no longer overwrite a replacement
  (#651).
- **Frontend server (#702)** — the `/api` proxy's error log put the request URL into the console
  format string, so a `%s` in it was read as a directive and a line break could forge a log line; it
  now passes the method and URL as `%s` arguments with line breaks deleted (#704). The app shell is read
  once at start-up and served from memory, so no page request reaches the file system (CodeQL
  found both once #694 scanned the server) (#703).
- **urllib3 2.8.0** — the HTTP library under the ClickHouse client, requests and botocore moves from
  2.7.0, which GHSA-8988-9cw3-xx77 and GHSA-vxq7-64xx-v4gw affect, to 2.8.0, which fixes both. The
  blocking dependency audit failed every open PR on them. The unit suite and the integration and e2e
  suites against Postgres and ClickHouse pass on 2.8.0 (#719).

### Documentation

- **Retired workplan IDs and roadmap (#676)** — the ~70 references to the retired improvement
  workplan's IDs (P0-1 … P3-5) in code comments, tests, the schema, Terraform and the docs now say
  what they meant or cite the pull request that did it. The roadmap drops its two finished items (the
  HPO release in the signed record, #647; sign-out reading the import-incomplete flag, #650) and says
  what is still open: a queued or running import is not flagged (#693).
- **Deployment notes and the traceability matrix corrected; an unused ACMG flag removed** —
  `terraform/secrets.tf` said the Cloud SQL user and the Cloud Run revisions resolve `latest` at
  apply time. Terraform reads the Postgres password through a data source when it plans or
  applies; Cloud Run resolves each `latest` env-var secret when an instance starts, and the
  ClickHouse VM fetches its password when it boots. The Google Cloud guide's sizing note says what
  a package import from a bucket stages in `/tmp` (every object except the aligned reads and their
  indexes) and that the copy is deleted when the import ends; **Validate package** is a dry-run
  import job, not an extra copy held for one request. TF-09b cites the tests from #666 for
  REQ-DATA-009. The ACMG criteria no longer carry `autoEvaluable`, a flag nothing read whose values
  no longer matched what the pre-evaluation assesses (#667).
- **Handleiding: the notes on secrets and the Terraform state corrected** — chapter 4 said the
  Secret Manager values are added separately so that they never end up in the Terraform state.
  `coga-postgres-password` does: Terraform reads it to set the owner's password in Cloud SQL, as
  `terraform/secrets.tf` and `terraform/README.md` say. The chapter now says the values are kept
  out of Terraform variables, names that exception and asks for a private state bucket, and its
  security section no longer says the secrets never reach the state. The same bullet no longer
  says the code does not check that `SECRET_KEY` and the anchor key differ: since #651 the
  backend refuses to start when they are equal (#669).
- **Pre-release change log folded into the validation plan** — before the first release candidate
  TF-18 keeps no per-change records. TF-18 §3a sets out the lifecycle phases (development, release
  candidate, beta clinical validation, v1.0.0) and when change control starts; TF-10 §8 lists, one
  line each, the changes that altered a clinical output, the scope of the first validation; the
  release checklist (TF-09 §6) asks for the recorded code review. References to the retired CR
  numbers now name the pull request (#664).
- **In-app docs and clinical notes trimmed and corrected** — the reference docs lab users follow
  were re-checked against the code and state the known limits plainly; the user guide has fifteen
  sections instead of twenty; the clinical notes in `docs/` keep developer detail only, and the NIPT
  classification note is merged into `docs/monogenic-nipt.md` (#641).
- **Technical and deployment docs trimmed and corrected** — the Google Cloud guide no longer
  generates an integrity-anchor key the backend refuses (it must be 32 bytes); the data-import
  guide is rewritten at half the length; the database reference lists every table; the
  traceability, security and Terraform documents describe what the code does now (#640).
- **Handleiding rewritten against current main** — the fifteen chapters of the Dutch codebase
  manual were re-checked against the code and shortened by almost half, with one home per topic
  and the limits a reviewer should know stated plainly (#638).
- **Root and developer guides trimmed and corrected** — the README is a short front door; setup,
  architecture and open work each have one home (`docs/development.md`,
  `docs/application-scheme.md`, `docs/ROADMAP.md`); the retired workplan and the separate storage
  document are gone; AGENTS.md, CONTRIBUTING.md and SECURITY.md match how the project works now
  (#637).
- **Technical file checked against the code** — every document in `docs/regulatory/` was re-read
  against the code. The Sample QC checks, the version display, the in-app label, the problem-report
  route and several requirement statuses now say what CoGA does, with open gaps marked for the
  owner (#636).
- **Overview deck removed** — the seven-slide overview deck in `docs/overview-deck/`, with its
  PDF and slide images, is removed at the owner's request; nothing linked to it (#635).
- **Schema references repointed** — the handleiding, the runtime-role runbook and three other
  docs named Postgres schema files that #373 replaced. Each reference now names the baseline
  (`01_access.sql` to `05_grants.sql`) that holds the object, and the runbook and handleiding
  list all four tables the append-only `REVOKE` covers (#628).
- **Google Cloud is the production target** — the owner's decision (Terraform on Google Cloud;
  DPIA signed, data-processing agreement being signed; no production deployment yet) is
  recorded in TF-02 §10 and TF-14, and the deployment guide, runbook and Terraform README
  describe the go-live switches (#627).
- **Password policy confirmed** — the device owner confirmed the 15-character minimum for a new
  local account (#582), recorded in TF-18 and `docs/security-posture.md` (#626).
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

[Unreleased]: https://github.com/bmenten/CoGA/commits/main
