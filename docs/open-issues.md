# Open issues

Every known open issue, ranked by priority. Status of **2026-10-08**, `main` at `6b990c07`
(after #787) plus the cleanup branch that adds this page. Sources: the open GitHub issues and
pull requests, the follow-ups recorded in the pull requests of the last weeks, the residuals
and open items in `docs/` and the technical file, and the codebase audit of 2026-10-08, whose
agents checked each item in the code. Line numbers drift: search for the function or file
named, and re-check an item before acting on it.

The unranked roadmap is [ROADMAP.md](ROADMAP.md). Decisions only the owner or QA can make
are also tracked in issue [#518](https://github.com/bmenten/CoGA/issues/518).

**Priorities**

- **P0** — a defect that silently gives a wrong clinical result, loses data or opens a
  security hole in production: fix before anything else. **None:** nothing is in clinical
  use and the data are synthetic. The first six P1 items would be P0 in production.
- **P1** — blocks the first release candidate (RC): wrong behaviour or records with
  clinical or audit impact, or a step the RC needs.
- **P2** — to be done before the RC.
- **P3** — after the RC, or nice to have.

**Owners:** *Eng* engineering · *Owner* a decision of the device owner · *QA* the quality
manager · *LD* the laboratory director · *Ops* the operator or organisation admin · *DPO*
the data protection officer · *RA* regulatory affairs.

**The repository on that date:** 4 open issues (#1, #364, #518, #746) and 1 open pull
request (#788, green); no open code-scanning or Dependabot alerts; CI green on `main`; no
image built, nothing deployed, no release tagged.

## Ranked list: P1 and P2

| Rank | ID | Pri | Issue | Owner |
|---:|---|---|---|---|
| 1 | SAFE-1 | P1 | The admin SV upload stores a file's first sample column as the chosen sample | Eng |
| 2 | CLIN-1 | P1 | Sample QC reads "all checks passed" while a sex check did not run | Eng, Owner |
| 3 | CLIN-2 | P1 | FMR1: a 200-repeat allele is called a full mutation | QA, Eng |
| 4 | CLIN-3 | P1 | The review API takes any ACMG strength for any criterion, and counts a repeated criterion twice | Eng |
| 5 | SAFE-11 | P1 | The NIPT pages hide the import-incomplete banner | Eng |
| 6 | SAFE-2 | P1 | A live import whose heartbeat goes stale is ended as interrupted (#746) | Eng, Owner |
| 7 | TRACE-1 | P1 | NIPT per-target coverage tables get no raw-file provenance row | Eng |
| 8 | TRACE-2 | P1 | A `##<tool>Command` header line is recorded with its first word as the tool's version | Eng |
| 9 | REG-1 | P1 | Traceability-matrix rows that no test or implementation backs | QA, Owner, Eng |
| 10 | REG-2 | P1 | Decide what the signed record holds per application | Owner |
| 11 | OPS-1 | P1 | The database-migration job cannot start in production | Eng, Ops |
| 12 | OPS-2 | P1 | No container image has ever been built | Eng, Ops |
| 13 | OPS-3 | P1 | Terraform never applied; one environment; every push would deploy | Ops, Owner |
| 14 | OPS-4 | P1 | The go-live switches are all off (#364) | Ops |
| 15 | OPS-5 | P1 | No restore has been tested; no consistent Postgres + ClickHouse restore | Ops, Eng |
| 16 | REG-3 | P1 | The RC steps of TF-18 §3a have not started | Owner, QA |
| 17 | REG-4 | P1 | Technical-file sign-off (#518) | Owner, QA, RA |
| 18 | REG-5 | P1 | Risk-file confirmations | LD, QA |
| 19 | REG-6 | P1 | Performance-evaluation inputs (TF-10) | Owner, QA |
| 20 | REG-7 | P1 | Usability evaluation, DPIA filing, national provisions | Owner, DPO, RA, QA |
| 21 | REG-8 | P1 | Independent (4-eye) review is not enforced on `main` | Owner, Ops |
| 22 | CLIN-4 | P2 | The SNV + SV second-hit badge can read a phase from a half call | Eng |
| 23 | CLIN-5 | P2 | The Variant Explorer's *MANE only* drops MANE Plus Clinical transcripts | Eng |
| 24 | CLIN-6 | P2 | An X-linked recessive embryo call rests on an unconfirmed assumption | Owner, LD |
| 25 | CLIN-7 | P2 | The evidence snapshot lacks frequencies and in-silico scores | Owner, Eng |
| 26 | AUDIT-1 | P2 | The report page shows the 200 newest clinical audit events as the whole trail | Eng |
| 27 | CLIN-19 | P2 | Gene explorer rows that no source fills read "—" (ACMG secondary finding for BRCA1) | Owner, Eng |
| 28 | SEC-1 | P2 | Any project member can change a family's phenotypes through the API | Owner, Eng |
| 29 | SEC-2 | P2 | Family and sample IDs reach URLs unencoded | Eng |
| 30 | SEC-3 | P2 | Discover reads outside the package folder through `..` in an ID | Eng |
| 31 | SEC-4 | P2 | Users and sessions; Azure AD locks users out | Owner, Eng |
| 32 | SEC-5 | P2 | The frontend sends no HSTS header on Google Cloud | Ops, Eng |
| 33 | SEC-6 | P2 | The request audit holds clinical content with no retention period | Owner, DPO |
| 34 | SEC-7 | P2 | Gaps in what the clinical audit trail and the append-only rule cover | Owner, Eng |
| 35 | UI-1 | P2 | Opening *Users* soon after *Audit logs* crashes the page | Eng |
| 36 | AUDIT-2 | P2 | One malformed UI-event timestamp loses a batch of other users' events | Eng |
| 37 | AUDIT-3 | P2 | The async audit batch write is all-or-nothing | Eng |
| 38 | AUDIT-4 | P2 | The admin audit page does not show `request_meta` | Eng |
| 39 | DATA-1 | P2 | Reference downloads keep only the first member of a gzip file | Eng |
| 40 | DATA-2 | P2 | The Variant Explorer CSV export stops at 50,000 rows without saying so | Eng |
| 41 | TRACE-3 | P2 | The ClinGen recurrent-CNV file names another release than its file name | Eng, QA |
| 42 | TRACE-4 | P2 | The HiFiCNV copy-number track does not record its transform | Eng |
| 43 | TRACE-5 | P2 | Gene reference, PanelApp and CNV knowledgebase releases are not in the manifest | Eng |
| 44 | CLIN-8 | P2 | A sign-out refusal reason does not name its sample | Owner |
| 45 | CLIN-9 | P2 | QA confirmation of the carrier-screening preset and the chrM exclusion | QA |
| 46 | CLIN-10 | P2 | Parent–embryo IBS0 never checked on real imputed data | Owner |
| 47 | OPS-6 | P2 | Deployment settings: problem-report link, SMTP, the HPO fallback download | Ops, Eng |
| 48 | OPS-7 | P2 | Integrity anchors: no schedule, no export, one key | Owner, Eng |
| 49 | SEC-8 | P2 | Merge #788 (event-loop log lines quote values) | Owner |
| 50 | REG-9 | P2 | Traceability-matrix rows whose cited tests cover less than they claim | QA, Eng |
| 51 | REG-10 | P2 | No regression truth set (GIAB / GeT-RM) | Eng, QA |
| 52 | REG-11 | P2 | Post-market, vigilance and security-process inputs | QA, RA, Owner |
| 53 | ENG-1 | P2 | The small-variant filters exist twice (SQL and Python) | Eng |
| 54 | ENG-2 | P2 | The SV list and its CSV export declare the same filters twice | Eng, Owner |
| 55 | ENG-3 | P2 | No migration ledger | Owner |
| 56 | OPS-8 | P2 | Version identity and release mechanics | Eng |
| 57 | OPS-9 | P2 | Scale is unproven | Eng |
| 58 | OPS-10 | P2 | Secrets in the Terraform state | Owner |

## P1 — before the release candidate

### SAFE-1 · The admin SV upload stores a file's first sample column as the chosen sample

`upload_structural_variant_file` (`variant_upload_service.py`) never reads the `#CHROM`
line: the Sniffles and Spectre readers in `structural_variant_ingest.py` take the first
sample column, and its calls are stored under the sample named in the URL. A joint Sniffles2
VCF, or another member's file, silently becomes this sample's calls. #762 closed the same
hole for TRGT, mito and HiFiCNV files; this upload was left out. **Next:** resolve the column
with `per_sample_vcf_column` before any write (400 otherwise), with tests.

### CLIN-1 · Sample QC reads "all checks passed" while a sex check did not run

A sample without chrX genotypes gets a sex check with status `skip`, and `skip` ranks below
`pass` when the overall status is rolled up (`_worst` in `sample_integrity_qc.py`,
`worstQcStatus` in `frontend/src/lib/qcStatus.ts`). The page then reads "All
sample-integrity checks passed", and that `pass` is frozen into the signed `sample_qc`. The
sign-out gate catches only samples that no relatedness check anchors. **Next:** the owner
chooses the roll-up (a skipped check counts as `warn`, or the summary names the checks that
did not run); fix both ranks; test.

### CLIN-2 · FMR1: a 200-repeat allele is called a full mutation

The built-in fallback row for FMR1 (`repeat_expansion_catalog.py`) has `pathogenic_min: 200`,
and the classifier compares with `>=` (`repeat_expansion_pg.py`), so exactly 200 CGG repeats
reads *pathogenic*. The premutation range is 55–200 and a full mutation is more than 200; the
STRchive row (`FXS_FMR1`) uses 201. Which row applies depends on the call's TRID. **Next:** QA
confirms; set 201 and add a boundary test.

### CLIN-3 · The review API takes any ACMG strength for any criterion, and counts a repeated criterion twice

The server checks a criterion's code and its strength against two global lists
(`small_variant_review_acmg.py`, `acmg_points.py`); the per-criterion limits live only in the
frontend (`frontend/src/lib/acmg/criteria.ts`). It also sums every entry, so PVS1 sent twice
scores +16, class 5. An API client with access to the family can store BS1 at *very strong*
(−8) or PM2 at *very strong* (+8). The CNV scorer clamps per criterion but also sums repeated
codes. The UI cannot produce these, but the server's recompute is what protects the stored
and signed classification. **Next:** refuse a repeated code and a strength the criterion does
not allow (400), with a parity test against `criteria.ts`; the same for CNV criteria.

### SAFE-11 · The NIPT pages hide the import-incomplete banner

The NIPT page, the NIPT report and the NIPT coverage page do not use `FamilyPageHeader`, so
they never render `ImportIncompleteBanner` (the header's comment says "on every family
page"), and the NIPT page also lacks `AssemblyScopeBanner`. A NIPT family can be flagged
`import_incomplete` like any other, and the NIPT report has no sign-out gate (REG-2), so a
partly imported family reads as complete on screen and on the printed report. The IGV and
ROI-marker viewers lack the banner too. **Next:** render both banners on every family page;
test that each family route shows them.

### SAFE-2 · A live import whose heartbeat goes stale is ended as interrupted (#746)

When a running import's heartbeat is ten minutes old, the next worker ends the job as
interrupted and drops its overwrite backups, but the import goes on. Its later progress and
final updates match no row (`_update_job_progress` filters on `worker_id` and does not check
the row count). A restore deletes a table's rows before it checks that the backup exists, and
a failed restore still names the imported datasets in the `import_incomplete` flag. The
variant-write locks live on a connection nothing checks. **Next:** the owner picks among the
fixes in the issue; add the issue's two tests.

### TRACE-1 · NIPT per-target coverage tables get no raw-file provenance row

`_PROVENANCE_PATH_KEYS` (`family_package_registration.py`) lacks `target_table`, so the per-target
coverage table that drives the NIPT depth reading gets no `raw_import_files` row, no checksum
and nothing for *Verify*. The PCF aliases `_pcf_role_path` accepts (`maternal_file`,
`mat_file`, `paternal_file`, `pat_file`) are missing too. **Next:** add the keys; test.

### TRACE-2 · A `##<tool>Command` header line is recorded with its first word as the tool's version

`_parse_generic` (`vcf_header_provenance.py`) takes the first word of a command line as the
version: `##bcftools_viewCommand=view …` records bcftools "view", `##SnpSiftCmd` records
"SnpSift", and a `##trgtCommand` before `##trgtVersion` records "trgt" and blocks the real
version. The manifest is frozen into each sign-out. **Next:** read a version only from a
version line or a version-shaped token; test the three cases.

### REG-1 · Traceability-matrix rows that no test or implementation backs

- REQ-QC-005 is ✅ in TF-09b, but its cited `SampleQcCell.test.tsx` never renders a verdict:
  the verdict word, tone and breach sentence have no test.
- REQ-CARR-003 (*track the gene-panel version a screen used*) is ✅, but no test touches the
  panel-version functions (`panel_metadata_service.py`), and nothing records which panel
  version a screen or report used.
- REQ-PGT-008 (*detect embryo aneuploidy*, criticality C) has no detector: CoGA displays the
  segment and CNV tracks, and TF-09b's action "add a detection unit test" assumes code that
  does not exist.

**Next:** QA sets the true status; add the tests; the owner rewords REQ-PGT-008 to what the
software does, or a detector is built.

### REG-2 · Decide what the signed record holds per application

The signed snapshot has no variant description (REQ-TRACE-007 ◐); the NIPT report has no
sign-out; the PGT embryo calls (REQ-PGT-005) are not among the signed report sections
(`REPORT_CONTENT_SECTIONS` in `report_signout_service.py`), and no document says so, while
TF-02 lists these calls as outputs. The device boundary is *annotated VCF → signed report*,
and the RC freezes the snapshot format. **Next:** the owner decides per application; until
then, document the PGT and NIPT gap (hazards H5, H6, H9).

### OPS-1 · The database-migration job cannot start in production

The Terraform `coga-db-migrate` Cloud Run job (`terraform/migrate.tf`) runs with
`APP_ENV=production` but without `CORS_ORIGINS`/`CORS_ORIGIN_REGEX`. Since #733 the settings
refuse the default local origins outside development, so `python -m app.db_migrate` stops at
import, and the switch to the restricted database role (`db_runtime_role = "coga_app"`,
deployment-gcp.md §12.8) fails. `test_deployment_config.py` checks only `cloudrun.tf`.
**Next:** give the job the API's CORS settings and extend the test to every job.

### OPS-2 · No container image has ever been built

`build.yml` skips build, push and deploy while `GCP_WIF_PROVIDER` is unset (the repository
has no secrets), and no CI job builds the Dockerfiles, so a broken Dockerfile would surface
only at the RC. **Next:** a pull-request job that builds both production images without
pushing (a workflow edit for the owner); configure Google Cloud.

### OPS-3 · Terraform never applied; one environment; every push would deploy

No environments, secrets or variables exist; `GCP_ENV` is `dev`; `COGA_DEPLOY_TRIGGER` is
unset, so once Google Cloud is configured every push to `main` deploys (any value other than
`main` or `release` silently disables deploys). **Next:** bootstrap per
`docs/deployment-gcp.md` §5–7, set the trigger to `release` before clinical use, and decide on
a validation environment separate from production (TF-09).

### OPS-4 · The go-live switches are all off (#364)

Terraform defaults: `db_runtime_role = "owner"`, the WAF not enforced,
`allowed_ingress_cidrs = []`, `clickhouse_restrict_egress = false`,
`storage_backend = "local"`, `alert_notification_emails = []`; no required reviewers on the
`gcp-deploy` environment; TF-13 S-4 (download audit) open. **Next:** switch each on as its own
change-controlled deployment (deployment-gcp.md §12.7–12.10), after OPS-1.

### OPS-5 · No restore has been tested

Cloud SQL point-in-time backups and daily, crash-consistent ClickHouse disk snapshots are
taken independently; nothing restores both to one consistent point, and no restore drill has
been run. **Next:** run the drill and write a joint restore procedure before go-live.

### REG-3 · The RC steps of TF-18 §3a have not started

Feature freeze, the `-rc.N` tag, a documented risk-based review of the clinical-critical
modules, the bio-IT ingangsvalidatie (H11.1-F12.2), the security go-live and QA's
confirmation of the TF-10 §8 list. **Next:** plan them once the P1 defects above are fixed.

### REG-4 · Technical-file sign-off (#518)

All TF documents are v0.1 drafts with placeholder owner and approver fields; the `Sxxxx`
identifier is unassigned (TF-02, TF-07, TF-18); TF-04 needs the declaring legal person and
its signatory; TF-07's role titles are open; QA is to file dated copies of the BELAC
certificate (the citation was updated in #724). **Next:** work through #518 and update its
checklist (the BELAC citation and the per-change level confirmations are done or
superseded).

### REG-5 · Risk-file confirmations

The accepted residual H15 (CoGA does not check signing authority) awaits the laboratory
director's confirmation (TF-06); the severity matrix awaits QA; the risk review must assign
hazards to REQ-DIAG-004/005 and REQ-DATA-003 (TF-09). **Next:** confirm and sign.

### REG-6 · Performance-evaluation inputs

TF-10's comparators, numeric acceptance criteria and sample sizes per application
(INPUTS-QUESTIONNAIRE C1–C7); TF-11 awaits data; the partial (◐) and unverified (⚠) rows of
TF-09b §3. **Next:** fill TF-10 before the beta validation.

### REG-7 · Usability evaluation, DPIA filing, national provisions

The TF-12 summative usability evaluation has not run; TF-14 still needs the signed DPIA, the
DPO's opinion, the data-processing agreement, the processor or sub-processor question and the
pseudonymisation decision; the FAMHP provisions and where the public declaration is published
are open (INPUTS A6/A7, TF-01). **Next:** close before clinical use.

### REG-8 · Independent (4-eye) review is not enforced

`main` requires no approving review, has no CODEOWNERS, and administrators can bypass the
required checks; there is one human committer (TF-18 §6, INPUTS D7). **Next:** enable review
and admin enforcement, and decide how independence is met, by the first beta at the latest.

## P2 — before the release candidate

- **CLIN-4 · The SNV + SV second-hit badge can read a phase from a half call.**
  `sv_gene_index_service` reads `.|1` as the alternate allele on haplotype 1, where the SNV
  path (`clickhouse_variant_queries`) declines a half call, so the badge can show a
  read-backed cis/trans verdict the SNV path would not. Apply the same rule to both.
- **CLIN-5 · The Variant Explorer's *MANE only* drops MANE Plus Clinical transcripts.** The
  explorer reads `has_mane_select` only; the family search reads MANE Select or MANE Plus
  Clinical (`has_mane_plus_clinical` is written and never read). Align the explorer.
- **CLIN-6 · An X-linked recessive embryo call rests on an unconfirmed assumption.**
  `haplotypeRisk.ts` assumes, "owner to confirm", that an unaffected father's X is not at
  risk when it classifies a female embryo. The owner or laboratory director confirms it, and
  TF-09a (REQ-PGT-005) and the haplotype docs record it.
- **CLIN-7 · The evidence snapshot lacks frequencies and in-silico scores.** It keeps the
  annotation version, the annotation-set hash, the ClinVar significance and the time. It is a
  stored format: decide the fields before the RC.
- **AUDIT-1 · The report page shows the 200 newest clinical audit events as the whole
  trail.** `list_clinical_audit` returns 200 events without paging or a truncation flag, and
  the report page calls the list "an immutable record of who classified, tagged or annotated
  each variant". A review save can write three events, so about 70 saves fill it. Page it, or
  say when it is cut.
- **CLIN-19 · Gene explorer rows that no source fills read "—".** *Clinical actionability*,
  *Variant pathogenicity* and *ACMG secondary finding* (`GeneInfoPage.tsx`) read keys no
  backend code writes, so they always show "—", BRCA1's secondary-finding status included;
  the ClinGen bulk dosage entries show as an extra haploinsufficiency item without their
  triplosensitivity, report link and date. Fill the rows from a source or remove them
  (owner).
- **SEC-1 · Any project member can change a family's phenotypes through the API.** The HPO
  create, update and delete routes (`routers/families.py`) use `get_current_user`; the UI and
  the user guide say administrators only. Phenotypes feed the ranking and PP4. The owner
  decides: gate the API to admins, or change the documentation.
- **SEC-2 · Family and sample IDs reach URLs unencoded.** `cram.py`, `signal_tracks.py` and
  `family_qc_reports.py` build URLs from IDs, and `IgvViewer.tsx` builds the manifest URLs
  without `apiPath`. The ID rule allows `/ ? # % ..`, so such an ID breaks IGV or the QC link,
  or redirects the request (the class of #521). Encode every segment.
- **SEC-3 · Discover reads outside the package folder through `..` in an ID.**
  `_detect_ped_path` opens `<root>/<family id>.ped` without the containment guard, and the NIPT
  discovery globs with PED sample IDs and reads the matching VCF header. Admin only; it can
  leak a PED's sample IDs, a VCF's column count and an absolute path (validation refuses the
  path later). Guard both reads; consider refusing `/` and `..` in IDs (SEC-15).
- **SEC-4 · Users and sessions; Azure AD locks users out.** No server-side logout or token
  revocation (an accepted residual), no password change or reset, no MFA;
  `PATCH /api/auth/users/{id}` changes only `is_active`, so a role changes only by SQL. With
  Azure AD configured, every local token but an admin's with `AZURE_ADMIN_OVERRIDE` is
  refused, and the frontend has no Azure sign-in, so setting Terraform's `azure_ad_*`
  variables locks everyone else out. The owner decides the go-live sign-in model.
- **SEC-5 · The frontend sends no HSTS header on Google Cloud.** Its Cloud Run service sets no
  `ENABLE_HSTS` (`terraform/cloudrun.tf`), so the app page is served without
  Strict-Transport-Security; only `/api` answers carry it, while `loadbalancer.tf` says the app
  sends it. Set it.
- **SEC-6 · The request audit holds clinical content with no retention period.**
  `audit_log_events` stores request bodies in an append-only table; the retention period is
  still a placeholder in TF-14 (INPUTS E5). The owner and the DPO decide retention and masking.
- **SEC-7 · Gaps in what the clinical audit trail and the append-only rule cover.** A
  compound-het pair review writes no clinical audit event; pedigree, member and HPO edits are
  only in the HTTP audit; `family_structure_versions` is neither append-only nor hash-chained;
  and AGENTS.md lists `ui_events` among the traceability tables, though it has no
  `*_block_mutation` trigger and no `REVOKE`. Decide which of these are clinical-audit events,
  and correct AGENTS.md or protect `ui_events`.
- **UI-1 · Opening *Users* soon after *Audit logs* crashes the page.** Three queries share
  the cache key `['admin','users']` with different data shapes: the audit page caches only
  `{id, email}`, which the users page then reads as full records (`u.projects.length` throws;
  until then every user shows inactive with no role); the projects page uses the key too.
  Give each its own key, or use `select`.
- **AUDIT-2 · One malformed UI-event timestamp loses a batch of other users' events.**
  `UiEventIn.occurred_at` comes from the client unchecked; a value such as
  `0001-01-01T00:00:00+05:00` makes the Postgres driver's timestamp encoder overflow, and in
  async mode the whole batch (up to 50 events, any user's) fails every retry and reaches only
  the error log and the counter. Bound the value at the API.
- **AUDIT-3 · The async audit batch write is all-or-nothing.** For a failure other than an
  event's content (#768 handles unstorable content) every event of the batch is only logged
  and counted. Retry the rows one by one after the last batch attempt.
- **AUDIT-4 · The admin audit page does not show `request_meta`.** The `record_hidden` and
  `_escaped` flags that `docs/security-posture.md` relies on are visible only through the API.
- **DATA-1 · Reference downloads keep only the first member of a gzip file.**
  `bounded_download.gunzip_bounded` stops after the first member, unlike the
  `gzip.decompress` it replaced and the upload path's reader. Its callers (reference sources,
  Monarch, the gene-info bulk files) would silently truncate a bgzipped upstream file. Read
  every member, under the same size cap.
- **DATA-2 · The Variant Explorer CSV export stops at 50,000 rows without saying so.** It also
  cuts the review-tag set at 200,000 ids, and sends neither the `TRUNCATED` file name nor the
  `X-CoGA-Export-*` headers of the family exports (#725); the route's docstring promises every
  filtered variant.
- **TRACE-3 · The ClinGen recurrent-CNV file names another release than its file name.** The
  bundled `ClinGen_recurrent_CNV_V2.1-hg38.bed` has the track name
  `ClinGen_recurrent_CNV_V2.0-hg38`, and the import records the release from the file name.
  Check the file against ClinGen's V2.1 download by checksum.
- **TRACE-4 · The HiFiCNV copy-number track does not record its transform.**
  `_import_copy_number_track` takes `extra_metadata` and never writes it, so the
  `log2_ratio_to_diploid` normalisation is lost (the bigWig importer records its own).
- **TRACE-5 · Gene reference, PanelApp and CNV knowledgebase releases are not in the
  manifest** a report used (clinical-traceability.md, known limitations).
- **CLIN-8 · A sign-out refusal reason does not name its sample.** The 409 repeats each
  check's message ("No chrX genotypes available…"; the relatedness and Mendelian messages omit
  the pair or child), and the list is frozen into the signed record. Prefixing the IDs
  changes audit text: owner decision.
- **CLIN-9 · QA confirmation of the carrier-screening preset and the chrM exclusion** (#763).
  TF-09a records the chrM exclusion as the owner's decision.
- **CLIN-10 · Parent–embryo IBS0 never checked on real imputed data** since the genome-wide
  site sample (#742). The owner checks one real PGT family on the Sample QC page.
- **OPS-6 · Deployment settings.** `COGA_PROBLEM_REPORT_URL` is unset, so a clinical build has
  no problem-report link (TF-15); `SMTP_HOST` is not set in Terraform although `ADMIN_EMAIL`
  is, so every sign-up notification fails against localhost; the HPO fallback download is the
  floating `hp.obo` with no pinned checksum (pin the release URL and the vendored file's
  SHA-256).
- **OPS-7 · Integrity anchors.** Created only by an admin request, nothing schedules them,
  `export_anchor()` is an empty hook (so deleting the newest anchors goes unnoticed while the
  API connects as the owner), verification trusts one key, no HSM (REQ-TRACE-008 🔲). Decide
  the schedule and the export target, or accept the risk for the RC.
- **SEC-8 · Merge #788.** asyncio's default handler writes a failed task's repr, its
  exception message included, into the log line; #788 makes it value-free. Green, waiting.
- **REG-9 · Traceability-matrix rows whose cited tests cover less than they claim.**
  REQ-UI-006 (the unsafe `next` paths are untested), REQ-SEC-003 (the admin-override fallback
  is untested), REQ-QC-003 ("unknown metric rejected" was never tested), REQ-DATA-001 (the
  per-call GT/DP/AF/AD parsing), REQ-PGT-007 (SV filtering by length and type). Add the tests
  or correct the rows.
- **REG-10 · No regression truth set.** No GIAB or GeT-RM set with a concordance harness,
  which TF-18 §4 needs for every minor change after the RC (INPUTS C6).
- **REG-11 · Post-market, vigilance and security-process inputs.** PMS cadence and
  indicators, FAMHP reportability and the support contact (TF-16, TF-17);
  vulnerability-response targets, a threat model and a base-image SBOM (TF-13); the supported
  browser (TF-15); form H11.1-F2 vs F14 (TF-18); the TF-05 market survey; panel versions
  (TF-01).
- **ENG-1 · The small-variant filters exist twice** (ClickHouse SQL and Python); parity tests
  cover only genotype classes and hemizygous positions. A parity test over every filter field.
- **ENG-2 · The SV list and its CSV export declare the same 27 filter parameters twice**
  (`families_structural_variants.py`), so a filter added to one makes the CSV differ from the
  screen. Folding them into one dependency reorders the list route's OpenAPI parameters:
  owner decision.
- **ENG-3 · No migration ledger.** The Postgres baselines are re-applied at every start. The
  owner decides how a schema change reaches a populated production database, before the RC
  freezes the schema.
- **OPS-8 · Version identity and release mechanics.** A build of `main` stamps the plain
  `VERSION`, so after the RC tag every main build reports the RC's version (in `/api/version`
  and the signed report's footer); deploy pins a tag, not a digest; nothing checks that a
  release tag is on `main`; the SBOM is archived by hand (RELEASING.md).
- **OPS-9 · Scale is unproven.** No genome-scale import or query benchmark; one uvicorn
  process; the import and refresh workers run in the API process; VCF lines are parsed on the
  event loop between inserts; Sample QC blocks the event loop for about 2.3 s for ten samples
  (genotype parsing and evaluation, no `asyncio.to_thread`).
- **OPS-10 · Secrets in the Terraform state.** The owner password and the ClickHouse TLS keys
  enter the state (private, CMEK bucket); the provider pin blocks write-only arguments. Accept
  the residual or schedule the provider upgrade.

## P3 — after the release candidate

### Data integrity and import

- **SAFE-3 ·** Per-sample TRGT and HiFiCNV files are checked one at a time, and a failed `cnv`
  or `repeats_trgt` dataset can leave part of its rows (only `snv` and `haplotypes` clean up
  after themselves); the `import_incomplete` gate covers sign-out. Check every column up
  front, as the mito dataset does.
- **SAFE-4 ·** A long-read SNV file out of position order is found only while streaming,
  after an overwrite has deleted the stored callset; the overwrite snapshot restores it, so it
  is lost only if that restore fails too (SAFE-2).
- **SAFE-5 ·** A NUL in a manifest path raises an uncaught `ValueError` in path resolution:
  500 on validate, Discover and *Write manifest.yaml*, and a job failure with a raw message.
- **SAFE-6 ·** The cfDNA sample lookup at import ignores manifest `samples` entries keyed `id`,
  which registration accepts.
- **SAFE-7 ·** `families.metadata.unresolved_roi` is written and never read, and never cleared
  when a later import resolves the ROI.
- **SAFE-8 ·** An API client can store a malformed pedigree row through the Family Builder: a
  whitespace-only parent ID gives a five-field PED row, and the drawing then shifts its columns
  (the UI trims, so only direct API calls).
- **SAFE-9 ·** `FamilyMemberUpdate.clear_existing_genomic_data` is accepted and ignored.
  Removing it changes the API: owner decision.
- **SAFE-10 ·** A 200 answer with a body that is not JSON from PanelApp or a reference source
  gives a 500 instead of a 502.
- **SAFE-12 ·** The annotation manifest falls back to `families.metadata.annotation_manifest`,
  which nothing in the backend writes (only tests feed it); the QC-threshold `reason` is
  nullable only for edits made before it was required (making it `NOT NULL` is a schema and
  API change for the owner).

### Security and audit

- **SEC-9 ·** Residuals of #785: a taken family or sample ID answers 409 on create and on
  `POST /ped/manual`, and a non-admin PED upload with overwrite answers 403 "Only admins can
  overwrite…", each telling a user that the ID exists elsewhere (documented as a residual; the
  owner has not accepted it yet); deleting another user's filter preset answers 403 where an
  unknown one answers 404; `build_gene_profile` takes the family's first project, which may be
  one the caller cannot see.
- **SEC-10 ·** TF-09a REQ-SEC-001 and TF-13 do not say that a record outside your projects,
  an id that is no UUID and a body id that names nothing all answer like an unknown record
  (#785, #777, #787); TF-13 also omits a query parameter's name from the control-character
  rule. Requirement text is the owner's.
- **SEC-11 ·** The sign-up notification logs the new user's address at INFO and the SMTP
  error text at ERROR, on the root logger (`routers/auth.py`). Log `describe_error` and no
  address.
- **SEC-12 ·** The `ALGORITHM` environment variable silently changes the JWT algorithm
  (`config.py`), undocumented and unchecked. Make it a constant.
- **SEC-13 ·** The alignment lookup falls back to an unchecked `DATA_DIR/<family>/<file>`
  when every layout fails containment (possible only with symlinked folders).
- **SEC-14 ·** Blocking calls on the event loop: the QC-report routes call `object_exists`
  and `presigned_get_url` directly, and Azure sign-in fetches its signing keys synchronously
  (an unknown `kid` clears both caches).
- **SEC-15 ·** The ID rule accepts C1 controls, bidi and zero-width characters, `/` and `..`
  (owner's call; see SEC-2 and SEC-3).
- **SEC-16 ·** `/api/health/ready` is polled by nothing (the probes and the uptime check use
  `/api/health`), and nothing carries a request ID from a log line to its audit row.
- **SEC-17 ·** `audit_log_events` is append-only but not hash-chained; segment hashing was
  deferred.
- **SEC-18 ·** Two copies of the list of secret-looking keys the logs mask (`ui_events.py`,
  `request_logging.py`), and a stale migration-era 503 in `routers/families.py` that names
  tables.
- **SEC-19 ·** Terraform residuals to accept in TF-13: ClickHouse graceful shutdown is
  best-effort, bucket grants are bucket-wide, secret rotation is manual.

### Clinical behaviour and QC

- **CLIN-11 ·** Sample QC: no warning in code for a callset that covers only part of the
  genome; the site sample includes indels.
- **CLIN-12 ·** A relative's lineage on a male's X is greyed out (TODO in
  `haplotype_lineage_service.py`), which fails safe.
- **CLIN-13 ·** The NIPT per-sample SNV file binds its one column unchecked, on purpose: NIPT
  manifests map verified tubes to files named after other tubes. Guarding it needs the owner's
  decision and `vcf_sample` on remapped entries.
- **CLIN-14 ·** The repeat genome track shows the status stored at import, while the table
  reclassifies against the current catalogue (premutation, the male-X note): the two can
  disagree after the catalogue is reseeded.
- **CLIN-15 ·** The API and signed snapshot fields `cat7_transmitted` and `cat8_absent`, and
  the NIPT profile summary's "categories 7/8", are named after the old paternity logic; the UI
  shows neither. Renaming changes a stored format: owner decision.
- **CLIN-16 ·** Genotype words outside VCF ("HET", "HETEROZYGOUS") are read three different
  ways by three helpers. Only a malformed VCF stores them: reject them at import (owner).
- **CLIN-17 ·** Neither CNV scorer caps section totals; QA confirms against Riggs et al.
  2020 whether a section maximum applies.
- **CLIN-18 ·** The APCAD glossary entry awaits the laboratory's definition; the Sample QC
  pages say they catch contamination, which they do only indirectly (owner wording).
- **CLIN-20 ·** Display differences between pages: the small-variant card and table show the
  stored classification word as typed while the SV pages normalise it; the NIPT report prints
  raw tokens (fetal sex, chrY profile, paternity, plasma status) where the screen's QC panel
  uses readable text; the live report says "no longer present in the dataset" where the
  signed version says "in the data".
- **CLIN-21 ·** Filter-form details: the SV form's active *Gene panel* chip names the draft
  panel, not the applied one; an SV preset's rule count includes one sample template per
  member; the small-variant form's active review- and exclude-tag chips do not mark a deleted
  tag "(deleted)", unlike the other chips since #776.

### Technical file and documentation

- **DOC-1 ·** TF-09b: REQ-SEC-006 does not cite its log-masking tests; REQ-DATA-007,
  REQ-DATA-009 and REQ-MITO-002 do not cite the #762 and #759 tests; REQ-CARR-002's hazards
  differ between TF-09a (H2) and TF-09b/TF-06 (H1, H2); REQ-NIPT-005 was deleted (#700)
  instead of marked deprecated as TF-09a §5 requires.
- **DOC-2 ·** TF-02 §8 lists the sign-out gates without the incomplete-import gate and the
  refusal while data is written (TF-06 has both); TF-09c lists 10 of the 38 e2e suites; the
  ACMG posterior ticks (#765) are in neither REQ-UI-003 nor TF-12.
- **DOC-3 ·** The in-app docs do not describe the repeat statuses (normal, intermediate, FMR1
  premutation, pathogenic), the male chrX two-allele review flag (#757) or Paraphase's
  *changes only* filter (#756); the handleiding does.
- **DOC-4 ·** `RUN_INTEGRATION=1` before the seed scripts does nothing (only the pytest
  conftests read it), yet `docs/testing.md`, TF-09d, `playwright.config.ts`, the stylediff
  README and two seed scripts set it.
- **DOC-5 ·** Stale comments in the schema baselines (`02_reference.sql` names
  `01_core.sql`; a note in `04_traceability.sql`): schema files are edited only with a schema
  change.

### Operations, CI and infrastructure

- **OPS-11 ·** Coverage gate: `ci.yml` passes `--cov-fail-under=70` while
  `check-coverage-floor.py` enforces 74 in the same job (a workflow edit for the owner);
  several module floors lie far below what CI measures; ten floored modules are outside the
  mypy gate (`family_package_import` would pass now).
- **OPS-12 ·** Dependabot does not track the CI service images, `fake-gcs-server`, the
  Terraform image defaults and providers, the Cloud Build builder image, the `pip-audit` and
  `markdown` pins in the workflows, or the image tags in `scripts/*.sh`; Terraform's own
  version floats in CI and its provider lock file is not committed.
- **OPS-13 ·** Workflow tidy-ups (edits for the owner): an unreachable branch-tag case in
  `build.yml`, stale comments in `build.yml` and `ci.yml`, sixteen environment lines repeated
  across three jobs, the coverage job installing the full dev lock.
- **OPS-14 ·** The ClickHouse memory guards (`clickhouse/users.d`) are not deployed to the
  Google Cloud VM, so the 4 GB per-query cap exists only locally.
- **OPS-15 ·** The dev compose file runs the backend without `--proxy-headers`, unlike the
  Dockerfile; `.dockerignore` patterns match only at the root; `package.json` allows Node
  22.22.0 while jsdom needs 22.22.2.
- **OPS-16 ·** The clinical build passes no `VITE_GITHUB_*` values, so the footer's GitHub
  links fall back to the owner's repository, and the footer's *Contact* is the owner's own
  address (allowed for now; a role mailbox or a build variable would fit the roles-not-names
  rule).
- **OPS-17 ·** `REFERENCE_CLINICAL_CNVS_PATH` defaults to a
  `clinical_cnv_syndromes_hg38_combined.tsv` that was never shipped, so the startup bootstrap
  of clinical CNVs never runs (the knowledgebase rebuild fills that table); drop the default
  or the bootstrap. Without UCSC, GRCh38 has no cytobands and T2T one band per chromosome.
- **OPS-18 ·** Rebuild the clinical CNV knowledgebase for both assemblies on each database
  after #721.
- **OPS-19 ·** eslint 10 and TypeScript 7 are blocked upstream (eslint-plugin-react,
  eslint-plugin-jsx-a11y, typescript-eslint); Dependabot ignores them.

### Engineering debt

- **ENG-4 ·** API leftovers whose removal changes the OpenAPI schema (owner decision):
  response fields never set (`GeneHomologOut.percent_id/percent_coverage`) or always the
  default (`ReferenceImportSourceAssemblyOut.cytobands_available/genes_available`); duplicate
  models (`FamilyStructureCoupleUpdate` = `ManualPedCoupleCreate`,
  `MonarchPhenotypeMatchOut` = `PhenotypeTermRefOut`); the CNV route's string-typed `end`.
- **ENG-5 ·** Routes no screen calls (kept, or dropped by the owner): `GET /assemblies/{species_id}`,
  `GET /families/{id}/hpo/query`, `PUT /families/{id}/hpo/{annotation_id}`,
  `GET /hpo/{hpo_id}`, `DELETE /families/{id}/small-variant-filter-presets/{preset_id}`,
  `DELETE /families/{id}/structural-variant-filter-presets/{preset_id}`, the BED endpoints'
  text format. Admin, traceability and ops routes without a screen stay by the owner's choice.
- **ENG-6 ·** Schema leftovers (owner decision): Postgres columns never read or written
  (`auth_login_attempts.last_success_at`, `small_variant_reviews.compound_het_partner_variant_keys`);
  ClickHouse columns always empty or constant and never read (`liftedOverChrom/Pos`,
  `sample_type`) or written and never read (`annotation_index.transcript_ids`).
- **ENG-7 ·** Frontend: about 40 responses read through hand-written types where a generated
  type exists (most visualisation tracks, the admin, gene, reference and import pages,
  `apiTypes.ts` types without a contract check, the Mendeliome response read untyped); eight
  admin alias routes nothing links to (`/admin/hpo`, `/admin/upload`, `/admin/users`,
  `/admin/data/clickhouse`, `/admin/data/presets`, `/admin/data/tags`, `/admin/data/logs`,
  `/admin/gene-reference`; owner: bookmarks — `/admin/data` itself is the breadcrumbs'
  target); the "(deleted)" tag label also lives in the small-variant track; the viewer still
  offers `MT`; wheel-zoom leftovers (#489: a shift scale always 1, a `[data-panning]` rule
  that never matches, so no grabbing cursor during a pan).
- **ENG-8 ·** Backend leftovers: gene-info keys one side reads and nothing writes, or writes and
  nothing reads (`mane_select_transcript`, `ensembl_canonical_transcript`, `hgnc_vega_id`).
  Duplication left for a later refactor: the audit and UI-event queue
  workers, the bounded download helper (Monarch, gene-info), several VCF INFO/FORMAT parsers
  with different flag handling, the interval-track tuple, small exact-copy helpers in the
  mito, repeat and Paraphase services, and parameters no caller sets.
- **ENG-9 ·** The ranking's comments promised a re-sort by the raw variant score (corrected
  in the code comments); the UI could offer that sort for novel-gene candidates.
- **ENG-10 ·** UI details: a failed saved-filter delete on *Settings* leaves the button at
  "Removing…"; a `next` of `/\host` keeps a signed-in user on the login page without a
  message (not an open redirect); two column headings in the data-inventory detail sit over
  the wrong columns; the admin presets page's loading text mentions family-linked presets;
  an empty transcript row spans 7 of 8 columns; several pages show a loading state without a
  spinner.
- **ENG-11 ·** Sentence case is incomplete after #754: headings (*Gene Explorer*,
  *Variant Explorer*, *Package Import*, *Family Builder*), the Family Builder and sample-upload
  tabs and buttons, the sign-up and login forms, the gene-panel page, the admin dashboard's
  groups and tiles, and several kickers; the docs quote some of them, so change both together.

### Product gaps

- **PROD-1 ·** Small-variant summary statistics per individual and family (#1).
- **PROD-2 ·** Variant Explorer: gene, transcript and cohort-frequency views.
- **PROD-3 ·** SV presets: no screen saves a reusable SV preset or deletes one; the API
  supports both.
- **PROD-4 ·** Long-read packages: a multi-sample family needs an annotated joint VCF; no
  dataset type reads a plain multi-sample Sniffles2 VCF; QDNAseq-style raw-bin CNV files are
  not importable.
- **PROD-5 ·** Per-sample GLIMPSE2 BCF haplotypes are registered but not imported.
- **PROD-6 ·** Single-parent recessive risk reads "uninformative" by design; a later version
  could say "carries the known parent's risk allele, donor unknown".
- **PROD-7 ·** REQ-MITO-003: no automated haplogroup comparison (owner to decide).
- **PROD-8 ·** The UI can add or delete a phenotype term but not edit one.

## Closed since the last review

Done and dropped from earlier lists: the BELAC citation (#724); QA confirmation of per-PR
change levels (superseded by #664 and #726); the CodeQL alerts awaiting dismissal (none open);
Python 3.10 (#555); ClickHouse 25.3 (#563); Dependabot for Docker images (#554); the
left-most `X-Forwarded-For` (#550); the required checks on `main`; the `describe_traceback`
recursion (#774); in the last week, the sex check shown as a match (#783), the silent NIPT
parent sex check (#781), 403 for another project's record (#785), 500s on malformed ids
(#777, #787), SV preset saves (#786), tag edits (#771, #776) and the ID rule on member edits
(#773). The cleanup branch that adds this page also corrects the documentation errors the
audit found (the stale-job wording in ROADMAP.md and monitoring.md, TF-16's alerting status, the Sample
QC relatedness copy, which said a warning is outlined red,
the handleiding, the in-app user guide, the test catalogue and the technical file's code
references), removes the dead code it proved dead, and removes five reference files nothing
read (two cytoband files and three files of a superseded clinical-CNV bundle).
