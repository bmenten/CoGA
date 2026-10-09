# Open issues

Every known open issue, ranked by priority. Status of **2026-10-09**, `main` at `34684945`
(after the P1 fixes #790–#801 and #788). Sources: the open GitHub issues and pull requests,
the follow-ups recorded in the pull requests of the last weeks, the residuals and open items
in `docs/` and the technical file, the codebase audit of 2026-10-08, whose agents checked
each item in the code, and what the P1 fixes of 2026-10-09 found. Line numbers drift: search
for the function or file named, and re-check an item before acting on it.

The unranked roadmap is [ROADMAP.md](ROADMAP.md). Decisions only the owner or QA can make
are also tracked in issue [#518](https://github.com/bmenten/CoGA/issues/518).

**Priorities**

- **P0** — a defect that silently gives a wrong clinical result, loses data or opens a
  security hole in production: fix before anything else. **None:** nothing is in clinical
  use and the data are synthetic. CLIN-22 and SAFE-13 would be P0 in production.
- **P1** — blocks the first release candidate (RC): wrong behaviour or records with
  clinical or audit impact, or a step the RC needs.
- **P2** — to be done before the RC.
- **P3** — after the RC, or nice to have.

**Owners:** *Eng* engineering · *Owner* a decision of the device owner · *QA* the quality
manager · *LD* the laboratory director · *Ops* the operator or organisation admin · *DPO*
the data protection officer · *RA* regulatory affairs.

**The repository on that date:** 3 open issues (#1, #364, #518) and no open pull request
besides the one that updates this page; no open code-scanning or Dependabot alerts; CI
green on `main`; no image pushed, nothing deployed, no release tagged.

## Ranked list: P1 and P2

| Rank | ID | Pri | Issue | Owner |
|---:|---|---|---|---|
| 1 | CLIN-22 | P1 | The built-in repeat thresholds disagree with STRchive's at eleven loci | Owner, QA, Eng |
| 2 | SAFE-13 | P1 | A restore that fails part-way can leave a deleted dataset unflagged | Owner, Eng |
| 3 | TRACE-6 | P1 | A digit of a `##source` tool's name is recorded as its version | Eng |
| 4 | REG-1 | P1 | Traceability-matrix rows marked verified that the software does not fully meet | QA, Owner, Eng |
| 5 | REG-2 | P1 | Decide what the signed record holds per application | Owner |
| 6 | OPS-2 | P1 | No image pushed; the CI image build does not block a merge | Owner, Ops |
| 7 | OPS-3 | P1 | Terraform never applied; one environment; every push would deploy | Ops, Owner |
| 8 | OPS-4 | P1 | The go-live switches are all off (#364) | Ops |
| 9 | OPS-5 | P1 | No restore has been tested; no consistent Postgres + ClickHouse restore | Ops, Eng |
| 10 | REG-3 | P1 | The RC steps of TF-18 §3a have not started | Owner, QA |
| 11 | REG-4 | P1 | Technical-file sign-off (#518) | Owner, QA, RA |
| 12 | REG-5 | P1 | Risk-file confirmations | LD, QA |
| 13 | REG-6 | P1 | Performance-evaluation inputs (TF-10) | Owner, QA |
| 14 | REG-7 | P1 | Usability evaluation, DPIA filing, national provisions | Owner, DPO, RA, QA |
| 15 | REG-8 | P1 | Independent (4-eye) review is not enforced on `main` | Owner, Ops |
| 16 | CLIN-23 | P2 | A PanelApp re-import changes a panel's genes under the same version | Eng |
| 17 | SAFE-14 | P2 | Three family pages show their data without the warning banners when the family record fails to load | Eng |
| 18 | CLIN-4 | P2 | The SNV + SV second-hit badge can read a phase from a half call | Eng |
| 19 | CLIN-5 | P2 | The Variant Explorer's *MANE only* drops MANE Plus Clinical transcripts | Eng |
| 20 | CLIN-6 | P2 | An X-linked recessive embryo call rests on an unconfirmed assumption | Owner, LD |
| 21 | CLIN-7 | P2 | The evidence snapshot lacks frequencies and in-silico scores | Owner, Eng |
| 22 | AUDIT-1 | P2 | The report page shows the 200 newest clinical audit events as the whole trail | Eng |
| 23 | CLIN-19 | P2 | Gene explorer rows that no source fills read "—" (ACMG secondary finding for BRCA1) | Owner, Eng |
| 24 | SEC-1 | P2 | Any project member can change a family's phenotypes through the API | Owner, Eng |
| 25 | SEC-2 | P2 | Family and sample IDs reach URLs unencoded | Eng |
| 26 | SEC-3 | P2 | Discover reads outside the package folder through `..` in an ID | Eng |
| 27 | SEC-4 | P2 | Users and sessions; Azure AD locks users out | Owner, Eng |
| 28 | SEC-5 | P2 | The frontend sends no HSTS header on Google Cloud | Ops, Eng |
| 29 | SEC-6 | P2 | The request audit holds clinical content with no retention period | Owner, DPO |
| 30 | SEC-7 | P2 | Gaps in what the clinical audit trail and the append-only rule cover | Owner, Eng |
| 31 | UI-1 | P2 | Opening *Users* soon after *Audit logs* crashes the page | Eng |
| 32 | AUDIT-2 | P2 | One malformed UI-event timestamp loses a batch of other users' events | Eng |
| 33 | AUDIT-3 | P2 | The async audit batch write is all-or-nothing | Eng |
| 34 | AUDIT-4 | P2 | The admin audit page does not show `request_meta` | Eng |
| 35 | DATA-1 | P2 | Reference downloads keep only the first member of a gzip file | Eng |
| 36 | DATA-2 | P2 | The Variant Explorer CSV export stops at 50,000 rows without saying so | Eng |
| 37 | TRACE-3 | P2 | The ClinGen recurrent-CNV file names another release than its file name | Eng, QA |
| 38 | TRACE-4 | P2 | The HiFiCNV copy-number track does not record its transform | Eng |
| 39 | TRACE-5 | P2 | Gene reference, PanelApp and CNV knowledgebase releases are not in the manifest | Eng |
| 40 | CLIN-8 | P2 | A sign-out refusal reason does not name its sample | Owner |
| 41 | REG-12 | P2 | Owner and QA confirmations of the 2026-10-09 fixes | Owner, QA |
| 42 | CLIN-9 | P2 | QA confirmation of the carrier-screening preset and the chrM exclusion | QA |
| 43 | CLIN-10 | P2 | Parent–embryo IBS0 never checked on real imputed data | Owner |
| 44 | OPS-6 | P2 | Deployment settings: problem-report link, SMTP, the HPO fallback download | Ops, Eng |
| 45 | OPS-7 | P2 | Integrity anchors: no schedule, no export, one key | Owner, Eng |
| 46 | REG-9 | P2 | Traceability-matrix rows whose cited tests cover less than they claim | QA, Eng |
| 47 | REG-10 | P2 | No regression truth set (GIAB / GeT-RM) | Eng, QA |
| 48 | REG-11 | P2 | Post-market, vigilance and security-process inputs | QA, RA, Owner |
| 49 | ENG-1 | P2 | The small-variant filters exist twice (SQL and Python) | Eng |
| 50 | ENG-2 | P2 | The SV list and its CSV export declare the same filters twice | Eng, Owner |
| 51 | ENG-3 | P2 | No migration ledger | Owner |
| 52 | OPS-8 | P2 | Version identity and release mechanics | Eng |
| 53 | OPS-9 | P2 | Scale is unproven | Eng |
| 54 | OPS-10 | P2 | Secrets in the Terraform state | Owner |

## P1 — before the release candidate

### CLIN-22 · The built-in repeat thresholds disagree with STRchive's at eleven loci

A TRGT call's TRID chooses the catalogue row that classifies it: a gene name (as in the demo
data) the built-in row (`repeat_expansion_catalog.py`), a STRchive ID the STRchive row
(`data/ref-data/STRchive-loci.json`). At eleven loci the two rows start the pathogenic
range (TBP: the intermediate range) at a different repeat count, so the same allele is
called differently depending on the pipeline's TRIDs:

| Locus | Built-in | STRchive |
|---|---:|---:|
| ATXN2 | 34 | 35 |
| CACNA1A | 20 | 21 |
| C9orf72 | 30 | 31 |
| ATXN7 | 36 | 37 |
| JPH3 | 41 | 40 |
| PABPN1 | 13 | 12 |
| ATXN1 | 45 | 39 |
| FXN | 66 | 56 |
| ATXN8OS | 80 | 71 |
| BEAN1 | 500 | 110 |
| TBP (intermediate) | 42 | 41 |

For the first four the built-in row starts one repeat lower, the FMR1 pattern #799 fixed; for
the others an allele between the two counts reads pathogenic (TBP: intermediate) only under
STRchive's row. The intermediate range also starts at a different count at CNBP, PPP2R2B,
NOP56 and ATXN10, and at ATXN1, ATXN2, ATXN7, JPH3 and BEAN1 (STRchive's BEAN1 row has
none); whether PABPN1's built-in `GCN` motif matches TRGT's per-motif counts is unclear.
HTT, ATXN3, ATN1, AR, DMPK and, since #799, FMR1 agree.
**Next:** the owner and QA set each locus's thresholds, or decide that a built-in row defers
to STRchive's where both exist; then a boundary test per locus, as #799 added for FMR1.
REQ-DIAG-004 has no hazard yet (REG-5).

### SAFE-13 · A restore that fails part-way can leave a deleted dataset unflagged

Since #801 a failed overwrite's restore checks every backup before its first delete and,
when one is missing, deletes nothing and keeps the `import_incomplete` flag naming what the
import imported. A restore that fails part-way for another reason may already have deleted
some of the family's tables: the flag then names no dataset as imported, but it does not
name the datasets whose rows may be gone as failed either. Re-importing only the failed
dataset clears the flag, and the family signs out without the incomplete-import gate.
**Next:** the owner decides: in that case flag every dataset of the import as failed, or
keep the family's `import_unfinished` entry; test.

### TRACE-6 · A digit of a `##source` tool's name is recorded as its version

`_parse_source` (`vcf_header_provenance.py`) splits a trailing number off a tool's name:
`##source=Clair3` records clair version "3"; `GLIMPSE2`, `Sniffles2` and `Mutect2` record
"2"; `SHAPEIT5 phase_common 1.1` records shapeit "5"; the demo's `CoGADemoFamilyClair3VEP`
records "3VEP"; and `cuteSV-1.0.13` is filed under the key "cutes". The annotation manifest
is frozen into each sign-out, as for TRACE-2 (fixed in #793). Less information but nothing
wrong: GATK 3's unquoted `Version=`, Sentieon's version and a version in the second word of
`##source` (pbsv, freebayes, hificnv, GLIMPSE_phase) are not read; of several bcftools runs
only the first version read is kept; Strelka's `##source_version` is filed under the module
"source". **Next:** read a version from `##source` only where the line states one apart
from the name (`Sniffles2_2.2`, `cuteSV-1.0.13`, a later word); test each case.

### REG-1 · Traceability-matrix rows marked verified that the software does not fully meet

#796 added the tests the matrix cited but did not have (the sequencing-QC verdict chip, the
gene-panel version history). What remains:

- REQ-QC-005 asks that a QC state be distinguishable, but on the members table's chip
  *pass* and *not run* look the same: no word, the same neutral tone, and the tooltip does
  not say "not assessed". The new tests pin today's look.
- REQ-CARR-003 (*track the gene-panel version a screen used*) is ✅, but only hand edits and
  the Mendeliome archive versions: a PanelApp import archives none and never raises the
  version (CLIN-23), and nothing records which panel version a screen or a report used (the
  sign-out snapshot and the clinical audit hold no panel; the printed NIPT report names only
  the local version number).
- REQ-PGT-008 (*detect embryo aneuploidy*, criticality C) has no detector: CoGA displays the
  segment and CNV tracks, and TF-09b's action "add a detection unit test" assumes code that
  does not exist.

**Next:** QA sets the true statuses; mark *not run* on the chip; record the panel version a
screen or report used; the owner rewords REQ-PGT-008 to what the software does, or a
detector is built.

### REG-2 · Decide what the signed record holds per application

The signed snapshot has no variant description (REQ-TRACE-007 ◐); the NIPT report has no
sign-out; the PGT embryo calls (REQ-PGT-005) are not among the signed report sections
(`REPORT_CONTENT_SECTIONS` in `report_signout_service.py`); repeat-expansion and Paraphase
results have no report tag, so they reach no signed record either. Since #798, TF-02 §6,
TF-06 (H5, H6, H9), `clinical-traceability.md` and handleiding chapter 11 say so. Statements
that still say otherwise wait for the decision: TF-01 §1 and §4 (conditions 1 and 7), TF-06
H4 (the NIPT Sample QC gate acts only on a sign-out of the family report), TF-02 §3 and the
README's device boundary, and TF-03 GSPR §16.1. The device boundary is *annotated VCF →
signed report*, and the RC freezes the snapshot format. **Next:** the owner decides per
application, and whether the user guide or TF-15 tells users that the embryo calls and the
NIPT results are not signed.

### OPS-2 · No image pushed; the CI image build does not block a merge

`build.yml` skips build, push and deploy while `GCP_WIF_PROVIDER` is unset (the repository
has no secrets). The `images` job of `ci.yml` builds both production images on every pull
request and push to `main`, as Cloud Build would, without pushing; it is not a required
check, so a broken Dockerfile fails that check but does not block a merge. **Next:** make
`images` a required check (the owner, in branch protection; TF-18 §6, `docs/testing.md` and
`security-posture.md` §5 then name it); configure Google Cloud.

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
change-controlled deployment (deployment-gcp.md §12.7–12.10); since #794 the migration job
starts in production, so the switch to the restricted database role can go first.

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

- **CLIN-23 · A PanelApp re-import changes a panel's genes under the same version.**
  `import_panelapp_panel_data` (`panel_metadata_service.py`) replaces the panel's genes and
  regions but keeps its version and archives nothing; PanelApp's own version changes only
  when another PanelApp release is imported. The prioritised-ranking cache keys a panel on
  those two versions (`_panel_version` in `variant_ranking_cache.py`), so after a re-import
  of the same release with other confidence levels or options an exact cache hit can serve a
  ranking built on the old genes, while the module says a stale ranking is never served
  (found by reading the code; no test yet). Give a re-import that changes the genes the next
  version and archive it (REQ-CARR-003, REG-1); test the cache miss.
- **SAFE-14 · Three family pages show their data without the warning banners when the family
  record fails to load.** The structural-variant, Sample QC and variant-summary pages do not
  wait for the family record: when that request fails while their own data loads, they show
  the data without the import-incomplete and assembly-scope banners (#791). The small-variant
  page waits for it forever when it fails. Say "Family could not be loaded", with a retry, as
  the NIPT pages do.
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
- **REG-12 · Owner and QA confirmations of the 2026-10-09 fixes.** Each fix made a choice its
  pull request asks the owner or QA to confirm:
  - #800 (Sample QC roll-up): a sample whose sex check could not run shows the amber ring
    and *Warning* even when its parent–child check passed; the wording of the note frozen
    into signed reports ("Sex could not be checked for sample *X*. A check that could not
    run counts as a warning, not a pass."); a NIPT family whose parent genotypes fail to
    load carries both #781's note and this one; the page's text when no check could run;
    whether TF-10 §8 names the roll-up rule.
  - #801 (#746): the reworded *Interrupted* error on job records; TF-06 H16 could name the
    two new controls (a stale heartbeat stops the import; a restore checks its backups); no
    requirement covers the overwrite restore (#365).
  - #795 (ACMG criteria): no criterion may be listed twice; CNV points outside a criterion's
    range are still clamped, not refused; whether TF-06 H3 names the check; a stored
    classification with a strength now refused saves again only once the analyst picks an
    allowed one.
  - #799 (FMR1): QA confirms the 201 boundary.
  - #798 (signed outputs): the wording of TF-02 §6's closing note.
  - #793 (header versions): the REQ-TRACE-011 citation; a tool named only on a command line
    is now left out of the manifest (listing it as "version not stated" would add a value to
    signed records); a single word with a digit, such as a release tag (`r0.8`), is kept as
    the version.
  - #792 (raw files): the REQ-DATA-004 status; the Paraphase `bam` is recorded although
    nothing reads it, while a VCF index found beside its VCF but not named is not.
  - #791 (banners): REQ-TRACE-013 was ✅ while the NIPT pages did not warn; REQ-TRACE-009
    still says "family and report pages"; TF-06 H16 could name the NIPT report; both banners
    say the case cannot be signed out, also on the NIPT report, which has no sign-out.
  - #790 (SV upload): a file whose columns name other samples is refused before the
    overwrite prompt; a joint VCF uploaded per member stores that member's `0/0` and `./.`
    calls at SVs only others carry, as NeedlR does; whether TF-10 §8 names the per-sample
    column rule.
  - #794 (migration job): whether TF-09b cites the new deployment test (under REQ-SEC-006, or
    in REQ-TRACE-008's limits).
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
  is lost only if that restore fails too (SAFE-13).
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
- **SAFE-15 ·** What #801 left of #746 (ROADMAP): nothing stops an import before its
  heartbeat is ten minutes old; an import whose heartbeat never reaches the database again
  keeps running; nothing checks the connection that holds the variant-write locks.

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
  disagree after the catalogue is reseeded. #799 is such a reseed: an FMR1 call of exactly
  200 repeats stored under the built-in row before it reads *pathogenic* on the track until
  its TRGT calls are imported again.
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
- **CLIN-24 ·** The sign-out QC prompt (`_qc_failure_summary`) lists the warning and failure
  messages but no check that could not run (the unverifiable asserted checks come in a list
  of their own); since #800 the Sample QC page names the samples whose sex was not checked.

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
  *changes only* filter (#756); the handleiding does. The in-app data-import reference says
  TRGT calls are scored against the STRchive loci, but a call whose TRID is a gene name is
  scored against the built-in row (CLIN-22).
- **DOC-4 ·** `RUN_INTEGRATION=1` before the seed scripts does nothing (only the pytest
  conftests read it), yet `docs/testing.md`, TF-09d, `playwright.config.ts`, the stylediff
  README and two seed scripts set it.
- **DOC-5 ·** Stale comments in the schema baselines (`02_reference.sql` names
  `01_core.sql`; a note in `04_traceability.sql`): schema files are edited only with a schema
  change.
- **DOC-6 ·** The gene-panel version archive is described as more than it is: the panel
  page says every version is archived and never deleted, but deleting a panel deletes its
  versions; handleiding chapters 13 and 15 say every change keeps an immutable version, which
  a PanelApp re-import does not (CLIN-23); `docs/database.md` calls the archive immutable,
  but its table has no append-only trigger.

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
- **OPS-20 ·** Terraform's `storage_backend` accepts `s3`, but nothing sets `S3_BUCKET`, so
  the API refuses to start with it (`config.py`); only `gcs` is wired. Drop `s3` from the
  variable or wire the bucket.
- **OPS-21 ·** The Postgres connection and admin settings are written twice, in
  `cloudrun.tf` and `migrate.tf` (#794 shared only `APP_ENV` and the cross-origin settings);
  fold them into the shared local, or test that the two copies agree.

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
  spinner; the sequencing-QC chip without a report link takes no keyboard focus, so its
  breach sentence is mouse-only, and the sentence gives the unrounded value without a unit
  ("18.57" where the chip shows "18.6x").
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

Fixed on 2026-10-09: the admin SV upload's sample column (SAFE-1, #790), the warning banners
on every family page (SAFE-11, #791), the raw-file record of NIPT coverage tables and PCF
names (TRACE-1, #792), versions read from command lines (TRACE-2, #793), the migration job's
start in production (OPS-1, #794), ACMG strengths and repeated criteria (CLIN-3, #795), the
missing tests behind two ✅ rows (REG-1's tests, #796), an image build on every pull request
(OPS-2's build, #797), the documentation of what is signed (REG-2's documentation, #798), the
FMR1 full-mutation boundary (CLIN-2, #799), a Sample QC check that could not run read as a
pass (CLIN-1, #800), a live import ended as interrupted (SAFE-2, #801, closing #746), and the
event-loop log line that quoted values (SEC-8, #788). What they left open is listed above.

Done earlier and dropped from earlier lists: the BELAC citation (#724); QA confirmation of
per-PR change levels (superseded by #664 and #726); the CodeQL alerts awaiting dismissal (none
open); Python 3.10 (#555); ClickHouse 25.3 (#563); Dependabot for Docker images (#554); the
left-most `X-Forwarded-For` (#550); the required checks on `main`; the `describe_traceback`
recursion (#774); the sex check shown as a match (#783), the silent NIPT parent sex check
(#781), 403 for another project's record (#785), 500s on malformed ids (#777, #787), SV
preset saves (#786), tag edits (#771, #776) and the ID rule on member edits (#773). #789, which
added this page, also corrected the documentation errors the audit found (the stale-job
wording in ROADMAP.md and monitoring.md, TF-16's alerting status, the Sample QC relatedness
copy, which said a warning is outlined red, the handleiding, the in-app user guide, the test
catalogue and the technical file's code references), removed the dead code it proved dead,
and removed five reference files nothing read (two cytoband files and three files of a
superseded clinical-CNV bundle).
