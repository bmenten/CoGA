# Open issues

Every known open issue, ranked by priority. Status of **2026-10-13**, `main` at `d7317167`
(after the fixes #790–#860). Sources: the open GitHub issues and pull requests, the
follow-ups recorded in the pull requests of the last weeks, the residuals and open items in
`docs/` and the technical file, the codebase audit of 2026-10-08, whose agents checked each
item in the code, and what the fixes of 2026-10-09 to 10-13 found. Line numbers drift:
search for the function or file named, and re-check an item before acting on it.

The unranked roadmap is [ROADMAP.md](ROADMAP.md). Decisions only the owner or QA can make
are also tracked in issue [#518](https://github.com/bmenten/CoGA/issues/518).

**Priorities**

- **P0** — a defect that silently gives a wrong clinical result, loses data or opens a
  security hole in production: fix before anything else. **None:** nothing is in clinical
  use and the data are synthetic. Every open P1 is a release, operations or regulatory step;
  the clinical defects left are P2.
- **P1** — blocks the first release candidate (RC): wrong behaviour or records with
  clinical or audit impact, or a step the RC needs.
- **P2** — to be done before the RC.
- **P3** — after the RC, or nice to have.

**Owners:** *Eng* engineering · *Owner* a decision of the device owner · *QA* the quality
manager · *LD* the laboratory director · *Ops* the operator or organisation admin · *DPO*
the data protection officer · *RA* regulatory affairs.

**The repository on that date:** 3 open issues (#1, #364, #518) and no open pull request
besides the one that updates this page; no open code-scanning or Dependabot alerts; twelve
required checks on `main`, which no longer requires a branch to be up to date before it
merges; CI green on `main`; no image pushed, nothing deployed, no release tagged.

## Ranked list: P1 and P2

| Rank | ID | Pri | Issue | Owner |
|---:|---|---|---|---|
| 1 | REG-1 | P1 | Traceability-matrix rows marked verified that the software does not fully meet | QA, Owner, Eng |
| 2 | REG-2 | P1 | Decide what the signed record holds per application | Owner |
| 3 | OPS-2 | P1 | No image pushed; the CI image build does not block a merge | Owner, Ops |
| 4 | OPS-3 | P1 | Terraform never applied; one environment; every push would deploy | Ops, Owner |
| 5 | OPS-4 | P1 | The go-live switches are all off (#364) | Ops |
| 6 | OPS-5 | P1 | No restore has been tested; no consistent Postgres + ClickHouse restore | Ops, Eng |
| 7 | REG-3 | P1 | The RC steps of TF-18 §3a have not started | Owner, QA |
| 8 | REG-4 | P1 | Technical-file sign-off (#518) | Owner, QA, RA |
| 9 | REG-5 | P1 | Risk-file confirmations | LD, QA |
| 10 | REG-6 | P1 | Performance-evaluation inputs (TF-10) | Owner, QA |
| 11 | REG-7 | P1 | Usability evaluation, DPIA filing, national provisions | Owner, DPO, RA, QA |
| 12 | REG-8 | P1 | Independent (4-eye) review is not enforced on `main` | Owner, Ops |
| 13 | SAFE-27 | P2 | An `auto` SNV import's update check and clean-up assume direct calls | Eng |
| 14 | SAFE-28 | P2 | Writes that fail or stop after their delete leave the family unmarked | Eng |
| 15 | SAFE-29 | P2 | Loaders accept a file with no records after deleting what it replaces | Eng |
| 16 | SAFE-26 | P2 | The import-incomplete flag keeps one scope per dataset | Eng |
| 17 | SAFE-30 | P2 | A value beyond its ClickHouse column fails the whole import | Eng |
| 18 | CLIN-55 | P2 | Switching a CNV classification's kind keeps the other kind's suggestions | Eng |
| 19 | CLIN-54 | P2 | The genome SV tracks draw only five exact SV types | Eng |
| 20 | SEC-29 | P2 | Raw-file download, verify and purge trust the recorded storage path | Eng |
| 21 | TRACE-9 | P2 | What a coverage BED holds is not recorded | Owner, Eng |
| 22 | CLIN-41 | P2 | An insertion written as its sequence, without SVLEN, measures 0 bp | Owner, Eng |
| 23 | CLIN-30 | P2 | A sequencing-QC chip with a verdict but no number shows "-" | Owner, Eng |
| 24 | SAFE-17 | P2 | Some incomplete-import flags can never clear | Owner, Eng |
| 25 | CLIN-31 | P2 | The repeat catalogue: other STRchive releases and multi-locus genes | Owner, QA, Eng |
| 26 | CLIN-36 | P2 | Two STRchive loci read not assessed in every family | Owner, QA |
| 27 | CLIN-6 | P2 | An X-linked recessive embryo call rests on an unconfirmed assumption | Owner, LD |
| 28 | CLIN-7 | P2 | The evidence snapshot lacks frequencies and in-silico scores | Owner, Eng |
| 29 | CLIN-19 | P2 | Gene explorer rows that no source fills read "—" (ACMG secondary finding for BRCA1) | Owner, Eng |
| 30 | SEC-1 | P2 | Any project member can change a family's phenotypes through the API | Owner, Eng |
| 31 | SEC-4 | P2 | Users and sessions; Azure AD locks users out | Owner, Eng |
| 32 | SEC-6 | P2 | The request audit holds clinical content with no retention period | Owner, DPO |
| 33 | SEC-7 | P2 | Gaps in what the clinical audit trail and the append-only rule cover | Owner, Eng |
| 34 | TRACE-3 | P2 | The ClinGen recurrent-CNV file names another release than its file name | Eng, QA |
| 35 | TRACE-5 | P2 | Gene reference, PanelApp and CNV knowledgebase releases are not in the manifest | Eng |
| 36 | CLIN-8 | P2 | A sign-out refusal reason does not name its sample | Owner |
| 37 | REG-12 | P2 | Owner and QA confirmations of the fixes of 2026-10-09 to 10-13 | Owner, QA |
| 38 | CLIN-9 | P2 | QA confirmation of the carrier-screening preset and the chrM exclusion | QA |
| 39 | CLIN-10 | P2 | Parent–embryo IBS0 never checked on real imputed data | Owner |
| 40 | OPS-6 | P2 | Deployment settings: problem-report link, SMTP, the HPO fallback download | Ops, Eng |
| 41 | OPS-7 | P2 | Integrity anchors: no schedule, no export, one key | Owner, Eng |
| 42 | REG-10 | P2 | No regression truth set (GIAB / GeT-RM) | Eng, QA |
| 43 | REG-11 | P2 | Post-market, vigilance and security-process inputs | QA, RA, Owner |
| 44 | ENG-1 | P2 | The small-variant filters exist twice (SQL and Python) | Eng |
| 45 | ENG-2 | P2 | The SV list and its CSV export declare the same filters twice | Eng, Owner |
| 46 | ENG-15 | P2 | Applying a large panel costs rows × panel size | Eng |
| 47 | ENG-3 | P2 | No migration ledger | Owner |
| 48 | OPS-8 | P2 | Version identity and release mechanics | Eng |
| 49 | OPS-9 | P2 | Scale is unproven | Eng |
| 50 | OPS-10 | P2 | Secrets in the Terraform state | Owner |

## P1 — before the release candidate

### REG-1 · Traceability-matrix rows marked verified that the software does not fully meet

#796 added the tests the matrix cited but did not have, and #813 marks a sequencing-QC chip
that was not assessed as *Not run* (REQ-QC-005). What remains:

- REQ-QC-005: a sample whose recorded QC has no depth, N50 or report shows "-" even when the
  server served a verdict, a fail included (CLIN-30).
- REQ-CARR-003 (*track the gene-panel version a screen used*) is ✅. Since #809 a PanelApp
  re-import that changes the genes archives a new version, but nothing records which panel
  version a screen or a report used (the sign-out snapshot and the clinical audit hold no
  panel; the printed NIPT report names only the local version number).
- REQ-PGT-008 (*detect embryo aneuploidy*, criticality C) has no detector: CoGA displays the
  segment and CNV tracks, and TF-09b's action "add a detection unit test" assumes code that
  does not exist.
- REQ-QC-003 is ✅, but #821's tests found what its row does not mention: a limit on a metric
  that has left the catalogue cannot be removed (ENG-12). The REQ-UI-006 defect they found,
  UI-2, is fixed (#833); QA confirms its ✅ (REG-12).

**Next:** QA sets the true statuses; record the panel version a screen or report used; the
owner rewords REQ-PGT-008 to what the software does, or a detector is built.

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

`main` requires no approving review and has no CODEOWNERS; its ruleset's twelve checks apply
to administrators, but an administrator can change or disable the ruleset (on 2026-10-13 the
checks were removed for some minutes and #820 merged with a failing check); there is one human
committer (TF-18 §6, INPUTS D7). **Next:** require a review, and decide how independence is
met, by the first beta at the latest.

## P2 — before the release candidate

- **SAFE-27 · An `auto` SNV import's update check and clean-up assume direct calls.** With
  `source_format: auto` the update check counts the family's clair3 rows whatever the file
  holds: an update of imputed genotypes is skipped when the family has direct calls and,
  when it has none, replaces the stored glimpse2 callset, in update mode. The clean-up after
  a failed `auto` import deletes clair3 rows whatever the file held: when the overwrite's
  snapshot could not be taken, a failed glimpse2 import removes the direct calls, and the
  flag does not name them (handleiding ch. 06 says a failed SNV import removes only its own
  rows). Read the callset as #856 records it. Found while fixing SAFE-23 (#856).
- **SAFE-28 · Writes that fail or stop after their delete leave the family unmarked.**
  ClickHouse has no transactions. `rewrite_family_structural_variants` has no restore when
  an insert fails after its delete, so the SV upload, the admin SV deletes and the NeedlR,
  CNV and mito SV datasets can leave a family without its SVs (an import's snapshot puts
  them back, best-effort; the mito SV rewrite in update mode takes none). A single-file
  upload whose process dies (memory, a restart) after its delete leaves nothing marking the
  family, where a mark written before the delete, as an import's `import_unfinished` entry
  is, would gate sign-out. The family-structure clear deletes the ClickHouse data before its
  Postgres writes, as the PED upload did before #859. Found while fixing SAFE-25 (#859).
- **SAFE-29 · Loaders accept a file with no records after deleting what it replaces.** The
  TRGT upload with overwrite commits an empty replacement (200) and strikes the sample off a
  whole-family import entry, and the package's TRGT path does the same; the interval-track
  readers and the mito SV file also take an empty file after their delete. Refuse an empty
  file before anything is deleted, as the small-variant upload does since #859. Found while
  fixing SAFE-25 (#859).
- **SAFE-26 · The import-incomplete flag keeps one scope per dataset.**
  `merged_import_failure` keeps one scope per failed dataset name, so a later failure of the
  same dataset under another callset or other samples replaces the earlier scope, and
  importing the later scope again clears the flag while the earlier data may still be
  missing. Since #856 two `auto` SNV imports of different callsets reach it. Keep every
  failed scope. From SAFE-18; found again while fixing SAFE-23 (#856).
- **SAFE-30 · A value beyond its ClickHouse column fails the whole import.** DP, GQ or AD
  above 65,535 (UInt16; realistic for mitochondrial or amplicon depth), a POS outside
  UInt32, a negative PS or a float beyond Float32 fails the insert with a 500, for any
  upload or import, and with overwrite now leaves the family flagged import-incomplete
  (#859). `_coerce_int` and `_first_present_int` raise OverflowError on `inf`, and a GQ of
  `inf` raises at row conversion: 500s, not skipped values. Clamp, skip or refuse such
  values before the write. Found while fixing SAFE-25 (#859).
- **CLIN-55 · Switching a CNV classification's kind keeps the other kind's suggestions.**
  The CNV dialog seeds both kinds from the suggestions computed for the type's own kind, so
  switching from loss to gain carries the loss section-3 tier and the 2H note into the gain
  scoring: a loss's 3B at 25–34 genes becomes an accepted +0.45 on gain. Compute each kind's
  suggestions for that kind. Found while fixing CLIN-47 (#854).
- **CLIN-54 · The genome SV tracks draw only five exact SV types.** `SvTrack.tsx` and
  `VariantTrack.tsx` draw DEL, DUP, INV, INS and BND by their exact names: a `DEL:ME`,
  `DUP:TANDEM`, `INS:ME`, CNV or TRA is silently not drawn, though the SV list shows it.
  Read the type without its subtypes, and a CNV by its copy number, as the ranking does
  since #854. Found while fixing CLIN-47 (#854).
- **SEC-29 · Raw-file download, verify and purge trust the recorded storage path.**
  `routers/admin.py::download_raw_import_file`, `raw_import_files_pg.verify_raw_import_file`
  and the list's exists check use `raw_import_files.storage_path` with no containment check,
  so an edited row lets an admin download any file the backend can read;
  `purge_family_managed_files` and `purge_sample_managed_files` unlink each `managed` row's
  path unchecked, so an edited row turns a family or sample delete into deleting any file
  the backend may write. Keep them to the import roots and the managed folder (a package
  folder need not be named after its family). Found while fixing SEC-25 (#851).
- **TRACE-9 · What a coverage BED holds is not recorded.** Uploads and the package's
  `coverage` dataset (source `web`) record no transform, so the coverage chart draws them on
  its log2-ratio axis, although the importer calls the file a BED of depths, the NIPT demo
  uploads depths and the NIPT check reads them as depth. The owner decides what a coverage
  BED holds, or the upload records it (#837).
- **CLIN-41 · An insertion written as its sequence, without SVLEN, measures 0 bp.** VCF 4.4
  defines SVLEN for symbolic alleles only and writes it `.` for a sequence allele, so such a
  file's insertions carry no SVLEN: the length filters measure them by END − POS, 0 bp, and
  a minimum length drops them (#827's rule: SVLEN, else END − POS). A deletion written as
  its sequence measures its REF since #831. Today's callers (Sniffles2, pbsv) write SVLEN.
  The owner decides whether a call without SVLEN is measured by its alleles. Found while
  fixing CLIN-33 (#831).
- **CLIN-30 · A sequencing-QC chip with a verdict but no number shows "-".** A sample whose
  recorded QC has no depth, N50 or report (an embryo that appears only in the PGT tables:
  `_import_qc_dataset` records only its `pgt` entry) shows "-" even when the server served a
  verdict, a fail included, so REQ-QC-005 is not met there. Decide what such a chip shows.
- **SAFE-17 · Some incomplete-import flags can never clear.** A failed dataset clears only
  on a re-import that ends *imported*. One that a re-import can only register or skip
  (phenotypes with no rows, a PCF with no files), or that ends with warnings, never clears,
  so every sign-out of that family needs an acknowledgement. Since #807 a part-way restore
  flags such datasets too. #829's whole-family entry can stay for good too: only an
  overwrite that imports each dataset it lists again, under the scope it lists (or, since
  #843, an upload with overwrite), removes it, so a dataset a re-import can only register,
  one recorded under another source (an SNV scope recorded as `auto` before #856, which now
  matches nothing) or one an administrator has since deleted keeps it.
- **CLIN-31 · The repeat catalogue: other STRchive releases and multi-locus genes.** #806's
  guard checks the shipped STRchive file, while a deployment reads `TRGT_STRCHIVE_LOCI_PATH`
  first (the reference-data bucket on Cloud Run): another release there makes the built-in rows
  disagree again. A gene-name TRID for a gene with several STRchive loci (ARX, HOXA13) resolves
  to the first row by locus ID, possibly another tract's thresholds.
- **CLIN-36 · Two STRchive loci read not assessed in every family.** STRchive's POLG (a
  benign range of 10 only) and MUC1 (nothing stated) have no thresholds, so since #826 every
  count reads *not assessed*: if the lab's TRGT catalogue holds them, every family shows
  them under *Aberrant only*. The owner decides whether a count inside a stated benign range
  reads normal.
- **CLIN-6 · An X-linked recessive embryo call rests on an unconfirmed assumption.**
  `haplotypeRisk.ts` assumes, "owner to confirm", that an unaffected father's X is not at
  risk when it classifies a female embryo. The owner or laboratory director confirms it, and
  TF-09a (REQ-PGT-005) and the haplotype docs record it.
- **CLIN-7 · The evidence snapshot lacks frequencies and in-silico scores.** It keeps the
  annotation version, the annotation-set hash, the ClinVar significance and the time. It is a
  stored format: decide the fields before the RC.
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
- **SEC-4 · Users and sessions; Azure AD locks users out.** No server-side logout or token
  revocation (an accepted residual), no password change or reset, no MFA;
  `PATCH /api/auth/users/{id}` changes only `is_active`, so a role changes only by SQL. With
  Azure AD configured, every local token but an admin's with `AZURE_ADMIN_OVERRIDE` is
  refused, and the frontend has no Azure sign-in, so setting Terraform's `azure_ad_*`
  variables locks everyone else out. The owner decides the go-live sign-in model.
- **SEC-6 · The request audit holds clinical content with no retention period.**
  `audit_log_events` stores request bodies in an append-only table; the retention period is
  still a placeholder in TF-14 (INPUTS E5). The owner and the DPO decide retention and masking.
- **SEC-7 · Gaps in what the clinical audit trail and the append-only rule cover.** A
  compound-het pair review writes no clinical audit event; pedigree, member and HPO edits are
  only in the HTTP audit; `family_structure_versions` is neither append-only nor hash-chained;
  and AGENTS.md lists `ui_events` among the traceability tables, though it has no
  `*_block_mutation` trigger and no `REVOKE`. Decide which of these are clinical-audit events,
  and correct AGENTS.md or protect `ui_events`.
- **TRACE-3 · The ClinGen recurrent-CNV file names another release than its file name.** The
  bundled `ClinGen_recurrent_CNV_V2.1-hg38.bed` has the track name
  `ClinGen_recurrent_CNV_V2.0-hg38`, and the import records the release from the file name.
  Check the file against ClinGen's V2.1 download by checksum.
- **TRACE-5 · Gene reference, PanelApp and CNV knowledgebase releases are not in the
  manifest** a report used (clinical-traceability.md, known limitations).
- **CLIN-8 · A sign-out refusal reason does not name its sample.** The 409 repeats each
  check's message ("No chrX genotypes available…"; the relatedness and Mendelian messages omit
  the pair or child), and the list is frozen into the signed record. Prefixing the IDs
  changes audit text: owner decision.
- **REG-12 · Owner and QA confirmations of the fixes of 2026-10-09 to 10-13.** Each fix made a
  choice its pull request asks the owner or QA to confirm. Round 1:
  - #800 (Sample QC roll-up): a sample whose sex check could not run shows the amber ring and
    *Warning* even when its parent–child check passed; the wording of the note frozen into
    signed reports ("Sex could not be checked for sample *X*. A check that could not run counts
    as a warning, not a pass."); a NIPT family whose parent genotypes fail to load carries both
    #781's note and this one; the page's text when no check could run; whether TF-10 §8 names
    the roll-up rule.
  - #801 (#746): the reworded *Interrupted* error on job records; TF-06 H16 could name the two
    new controls; no requirement covers the overwrite restore (#365).
  - #795 (ACMG criteria): no criterion may be listed twice; CNV points outside a criterion's
    range are still clamped, not refused; whether TF-06 H3 names the check.
  - #799 (FMR1): QA confirms the 201 boundary. #798: the wording of TF-02 §6's closing note.
  - #793 (header versions), #792 (raw files), #791 (banners), #790 (SV upload), #794 (migration
    job): the TF-09b citations and choices their pull requests list.

  Round 2:
  - #806 (repeat thresholds): the 15 loci's new values and the direction of each change; the
    family table now takes both thresholds from the matched row.
  - #807 (partial restore): "every dataset" includes those that ended registered, skipped or
    with warnings; a failed Postgres restore, or backup check, flags every dataset too.
  - #803 (second-hit badge): a half call falls back to segregation, as on the SNV path.
  - #809 (PanelApp versions): the same genes under a new PanelApp release keep the version; a
    rename makes none; panels imported earlier have no archived version 1.
  - #810 (audit trail): 200 events a page, at most 500; a partial trail marks the printout
    *Incomplete*; 404 for an unknown `before`; whether REQ-UI-004 says the whole trail.
  - #813 (QC chip): REQ-QC-005's ✅ and the new TF-10 §8 clause; the reason sentences, the grey,
    and *Not run* replacing *QC report*.
  - #814 (`##source`): the `mutect` → `mutect2` alias; SHAPEIT5's "1.1" not recorded; a leading
    `v` kept.
  - #808 (explorer export): the `review-filter-limit` reason; no requirement covers export
    completeness. #817 (gzip): trailing padding and an empty `.gz` refused; truncated uploads
    refused; whether to require BGZF's end-of-file block.
  - #816 (Discover): a warning, not a refusal, for an ID that leads out of the package
    (SEC-15). #805 (URLs): TF-13 lacks the URL-encoding control.
  - #818 (UI-event time): replace, not refuse; the bounds (a day back, five minutes ahead); a
    value without an offset read as UTC. #820 (audit batch): the early stop on an unreachable
    database; the batch line at WARNING; the not-persisted counter counts only events lost.
    TF-13 S-5's and REQ-SEC-004's wording for both.
  - #811 (family pages): the reference wait. #812 (track transform) and #815 (HSTS): whether
    either becomes a requirement. #819 (audit flags): the chip wording. #821 (matrix tests):
    whether the ✅ of REQ-UI-006 and REQ-QC-003 stand.

  Round 3:
  - #827 (SV length): TRA counts as a translocation, CTX not; the TF-10 §8 phrase;
    REQ-PGT-007's status.
  - #826 (not assessed): the rank above review; the colour and the stat label;
    REQ-DIAG-004's text lists four statuses (POLG and MUC1: CLIN-36).
  - #828 (ranking cache): rows without the record never serve as supersets; a
    description-only edit still serves; the TF-09b and TF-09c rows.
  - #829 (partial restore): clearing counts each overwrite; Postgres-only datasets are
    required; a stop during the restore keeps the entry; TF-06 H16.

  Round 4:
  - #831 (SV records without END or SVTYPE): the TF-10 §8 phrase; `SV` as the type of an ALT
    that states none, or refusing such a record.

  Wave 5:
  - #833 (sign-in `next`): REQ-UI-006's status; a bare `/` and a path starting with a
    percent-encoded character are refused.
  - #834 (NeedlR mates): TF-10 §8 naming a breakend's mate.
  - #835 (export lookups): whether the exports' truncation signal deserves a requirement.
  - #836 (alignment lookup): the stricter symlink rule (a family folder linked to a
    differently named folder serves no reads); REQ-SEC-007's citations.
  - #837 (depth transform): the `none` value; a not-drawn track hides that sample's HiFiCNV
    segments in the chart; unknown transforms are not drawn; the wording; whether a
    requirement covers a coverage track's scale.
  - #838 (update skips): an update leaves stored rows Postgres does not record; a partly
    skipped dataset reads `imported`.
  - #839 (sub-panel ranking): the REQ-DIAG-007 and REQ-CARR-003 citations; a panel that
    splits a pair is recomputed.
  - #840 (second-hit badge): what counts as a copy loss (DEL:ME counts, a CNV gain no
    longer); the 1 kb same-SV distance for per-sample files; the combinations that changed;
    rebuilding older indexes; the change level (major by the rule of thumb).
  - #841 (explorer filters): one in-silico predictor is enough and a SpliceAI score at its
    minimum passes the impact filter (the family search's rules, now documented); a
    requirement for the explorer.
  - #842 (package paths): any bad manifest name refuses the package; Write manifest refuses;
    a loop is absent in Discover, refused in validation.
  - #843 (upload records): the dataset names and matching; only an upload with overwrite
    strikes an item off; TRGT and BED uploads wait for an import (the 409 says "variants");
    a record that cannot be written fails the upload; TF-06 H16's control text.

  Round 6:
  - #844 (SIFT and PolyPhen): spaces read as underscores, a value naming several
    predictions, a score alone matching none; a filter value naming no prediction ignored
    rather than refused (422); the form offers no *tolerated low confidence* and no PolyPhen
    *unknown*; no TF-09a requirement covers the annotation and in-silico filters (nor the
    explorer, #841).
  - #845 (prioritised pairing): a pair ranks at its better-ranked variant; segregation is
    counted among the kept variants; past its window a search reports what it found as a
    lower bound; pairs come before single variants, so the CSV's Rank column is not
    monotonic; every cached ranking is recomputed once (`_ALGORITHM_VERSION` 3 and 4).
  - #846 (track sources): the BED upload and the package's coverage BED stay one source
    (`web`); the 409 reads stored rows; an uploaded and a package APCAD track are drawn
    together; the REQ-PGT-008 and REQ-PERF-003 citations; a requirement keeping callers'
    tracks apart.
  - #847 (NeedlR ids): the uploads' id form, so a review is shared with an upload's call of
    the same event; |SVLEN| in what counts as one event; a genotype over a no-call, then a
    sample's own call; the TF-10 §8 phrase.

  Wave 7:
  - #849 (SV export mates): its tests cited under REQ-DATA-003 rather than REQ-PGT-007.
  - #850 (package list): a folder linked out of the import roots is left out of the list,
    not listed as refused.
  - #851 (signal tracks and QC report): a QC report path that leads out answers 404, no
    longer 400; a package folder linked to a differently named folder serves no tracks or
    report; REQ-SEC-007's text names CRAM/BAM only.
  - #852 (coverage depth): a coverage BED is read as depth (TRACE-9 stays open); `none` from
    any caller reads as depth; one track per sample, the BED first; a family whose only chrM
    data are log2-ratio tracks, without MT calls, loses the mtDNA workspace link, and its
    mother reads *not assessed*; no TF-10 §8 line.
  - #853 (CNV evidence caller): 422, not 409, for a save that names no caller on a shared
    SV; `variant_missing` while another caller still lists the SV; evidence that names no
    caller reads unknown; snapshots frozen from the wrong caller before it are left as they
    are.
  - #854 (SV copy change): LOSS and CNV_LOSS count as deletions in the badge; a CNV without
    a copy number ranks as a loss; a CNV with a loss in one carrier and a gain in another
    counts as a loss; `INS:ME` scores as INS; a sample without a recorded sex has two X
    copies; the CNV classifier still opens every CNV on loss.
  - #855 (filter transcript): both transcripts shown rather than the shown one swapped; the
    note follows every annotation filter, the default view's impact filter included; the
    order among the matching transcripts; the ACMG dialog, the ranking and the report stay
    on the primary transcript, and a variant kept for another transcript's impact is scored
    on its primary; whether TF-10 §8's filtering area covers it.
  - #856 (auto SNV callset): a resolved `auto` import and an upload of the same callset
    complete each other; records still reading `auto` match nothing, so a family marked by
    one stays marked; a family VCF that changed during the import is refused before any
    write.
  - #857 (explorer panel): whether REQ-CARR-004 names the explorer.
  - #858 (panel resolution): the kept resolution trusts the reference import to be the only
    writer of `genes` (a row written by hand is seen at the next import or restart); no
    TF-06 H12 note.
  - #859 (upload reads first): the banner and the 409 call an upload's failure a package
    import (proposed: "An import or upload … failed", "Re-run the import or upload"); an
    upload clears the package-import failures it brings again; a package import with
    overwrite skips the check, relying on its snapshot; whether TF-10 §8 covers it.
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
- **ENG-15 · Applying a large panel costs rows × panel size.** The family page tests each
  row against every region of a panel (1.3 s at 500 regions and 2.1 s at 1,000 for a family
  of about 190,000 rows; a whole-genome family takes much longer); the explorer's window
  join (#857) could serve it. The record filter (`_kept_small_candidates` →
  `_small_record_matches`) rebuilds the set of the panel's gene names for every candidate
  and scans every region after the gene has matched: 1.5 to 4.4 s of a fresh Mendeliome
  ranking, which a compound-het, recessive or carrier-screening search pays even on an exact
  cache hit. Found while fixing CLIN-48 (#857) and ENG-14 (#858).
- **ENG-3 · No migration ledger.** The Postgres baselines are re-applied at every start. The
  owner decides how a schema change reaches a populated production database, before the RC
  freezes the schema. #826 is such a change: a database created before it keeps the old
  `repeat_expansions_status_check`, so its TRGT imports fail at a locus without thresholds
  until it is reset, the dev stack's included (`docs/database.md`).
- **OPS-8 · Version identity and release mechanics.** A build of `main` stamps the plain
  `VERSION`, so after the RC tag every main build reports the RC's version (in `/api/version`
  and the signed report's footer); deploy pins a tag, not a digest; nothing checks that a
  release tag is on `main`; the SBOM is archived by hand (RELEASING.md).
- **OPS-9 · Scale is unproven.** No genome-scale import or query benchmark; one uvicorn
  process; the import and refresh workers run in the API process; VCF lines are parsed on
  the event loop between inserts; Sample QC blocks the event loop for about 2.3 s for ten
  samples (genotype parsing and evaluation, no `asyncio.to_thread`). An unfiltered Variant
  Explorer page took 33 to 43 s on a 2-million-variant synthetic cohort, as it aggregates
  every carrier (#841).
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
  after an overwrite has deleted the stored callset; the overwrite snapshot restores it, so
  it is lost only if that restore fails too (since #807 and #829 that flags every dataset of
  the import and keeps the family marked until it is imported again in full).
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
- **SAFE-18 ·** Import bookkeeping (#807; its one scope per failed dataset is now SAFE-26):
  the job record's dataset table shows each loader's own status while the flag names the
  dataset failed; `_flag_family_import_incomplete`'s docstring still says the import takes
  no snapshot.
- **DATA-4 ·** Downloads (#817): a bgzip file cut exactly at a block boundary still reads as
  complete (requiring BGZF's end-of-file block would catch it: owner); a Monarch refresh
  whose download is corrupt answers 500, as its router catches only `httpx.HTTPError`. Nor
  does the refresh guard against a download that parses to zero rows (#859).
- **TRACE-8 ·** Header versions (#814): of several versions of one tool in a header the
  first is kept, where the pipeline-record parser joins them; `##SnpEffVersion` keeps the
  last; the keys glimpsephase, generatesvcandidates and dragensnv are not folded into
  glimpse, manta and dragen; TF-08 B.1 says HiFiCNV's version comes from `##source=hificnv`,
  but HiFiCNV VCFs are not header-parsed.
- **SAFE-21 ·** #829's whole-family entry: the sign-out 409, its dialog and the signed
  record describe it as an import that "stopped before it finished" (the frozen snapshot has
  no marker for it); Postgres-only datasets (repeats, Paraphase, phenotypes, QC) must be
  imported again too, which is stricter than needed; `_write_import_failure` has a mypy
  error (the module is not in the gated list).
- **SAFE-24 ·** Import bookkeeping after #838 and #843: the haplotype blocks' update check
  still reads the registry (it reports `skipped`, so it clears nothing); a CNV dataset with
  no parsed calls ends `registered` even after importing its tracks, so they are not
  recorded in `imported_scopes`; deletes and structure-change clears never shrink
  `imported_scopes` (the safe side); the admin deletes of tracks and repeats take no
  variant-write lock; `scripts/load_demo_quartet.py` writes through the upload services
  without recording what it loads; `_import_interval_track_unless_present` with overwrite
  clears a source's track only when Postgres records it, so that source's rows without a
  record survive next to the new ones; a BED upload whose ClickHouse insert fails after its
  delete leaves the rows gone while Postgres keeps the record (#846). `snv` stored as
  glimpse2 and the `haplotypes` dataset replace the same rows but never complete each other
  (the conservative side, #856).
- **DATA-6 ·** Lookups that still bind one Postgres parameter per item (a panel save's gene
  terms in `_resolve_gene_regions`, families or samples in other services) fail above 32,767
  items, as the export lookups did before #835 and the panel filter's gene lookup before
  #858; no realistic request reaches that.

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
- **SEC-22 ·** About 70 client-route links in some 45 files put IDs raw into the URL (`Link
  to`, `navigate`, breadcrumbs), so an ID with `?`, `#`, `%` or `/` breaks in-app
  navigation; a signed URL's `Content-Disposition` filename is built from the sample ID
  unescaped (#805).
- **SEC-23 ·** HSTS details (#815): Compose passes one `ENABLE_HSTS` to both containers, and
  the frontend takes only exactly `true` where the backend also takes `1`, `True`, `yes` or
  `on`; the frontend's Cloud Run service sets no `BACKEND_URL` (harmless while the load
  balancer routes `/api`); answers from Google's infrastructure and the backend's bare 500s
  carry no HSTS.
- **SEC-24 ·** Request audit (#820): a row naming a user deleted while it waited in the
  queue is still lost (a foreign key; operator SQL only); a retry after a commit with an
  unknown outcome can duplicate rows (no idempotency key).
- **SEC-27 ·** Package paths after #842: a directory named `*.ped` is taken for the PED by
  Discover (500, IsADirectoryError); Write manifest checks the link and then writes, so a
  concurrent swap to a symlink in between could still redirect the write (`O_NOFOLLOW` would
  refuse in-package links too). A sub-folder link inside a listed package (`ped/`, a
  long-read sample folder) is still followed: it counts towards listing the package, and
  Discover proposes a member from such a folder, all of whose files count as absent (a
  warning may be better: owner); a typed path into the staging root, or a root entry linking
  there, reads another import's staged copy (#850).
- **SEC-28 ·** On a case-insensitive file system (a macOS bind mount) family IDs that differ
  only in case share one folder, for every folder named after an ID (#836);
  `routers/cram.py` has two mypy arg-type errors in `_resolve_alignment_manifest_entry` and
  `routers/signal_tracks.py` one, each a `format` read as `str` (the modules are outside the
  gate).
- **SEC-30 ·** Remote storage after #851: a recorded signal-track or alignment URI is
  checked against the import area, not the family's folder (the documented design: scoping
  it needs the family's source folder recorded at import);
  `family_qc_reports._stored_report_key` and the CRAM remote probe check no path segments
  (object keys are literal, so a key cannot climb).

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
  its TRGT calls are imported again, as do the calls at the thresholds #806 moved (the
  demo's ATXN2 34 and ATXN8OS 74). So do #826's: a call stored as normal at a locus without
  thresholds reads not assessed in the table but normal on the track.
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
- **CLIN-32 ·** STRchive's own gaps and overlaps, for QA (#806): ATXN7 36 reads intermediate,
  PPP2R2B 33–39 normal, ATXN10 800–850 pathogenic; the built-in and STRchive rows differ in
  motif orientation at nine loci (it matters only where TRGT reports several motifs); whether
  PABPN1's `GCN` matches the pipeline's TRGT BED is unchecked.
- **CLIN-38 ·** Repeat details (#826): at a pathogenic-only locus a count between the benign
  maximum and the threshold reads normal (no grey zone); the male chrX note at a catalogued
  locus says "outside catalogued ranges", the wrong reason; *review* calls have no chip
  style of their own.
- **CLIN-39 ·** SV details (#827): a translocation shows as 0 or 1 bp (the report sentence
  reads "A 0 bp breakend"); a call with neither END nor SVLEN is 1 bp from NeedlR and
  HiFiCNV and 0 bp elsewhere; a negative exact length (the list writes deletions with a
  minus sign) silently matches nothing. And a call without END: the package's NeedlR,
  HiFiCNV and chrM Sniffles2 readers end it at POS + SVLEN, its sign dropped, whatever its
  type (without SVLEN at POS + 1, chrM at POS), so an insertion or a breakend spans bases
  after it there, while the uploads put it on its POS as VCF 4.4 does (#831).
- **CLIN-42 ·** SV records (#831): a symbolic deletion, duplication, inversion or CNV with
  neither END nor SVLEN stays on its POS and is stored without a word (the upload could
  count or refuse it); an SVTYPE written in lower case (`bnd`) gets no mate from the
  uploads, which compare `BND` exactly (the NeedlR reader takes any capitals since #834).
- **CLIN-49 ·** SV details after #834 and #840: the chrM Sniffles2 and HiFiCNV readers store
  a breakend without its mate; the Sniffles/Spectre upload ignores FORMAT/CN, so an
  SVTYPE=CNV record loaded that way has no direction and counts as a possible deletion in
  the second-hit badge and, since #854, ranks as a loss; in per-sample files the same SV
  split across two affected siblings' records gets two second-hit verdicts, so one sibling's
  cis does not veto the other's trans (errs towards flagging). The live report's SV footer
  line does not show a breakend's mate, though the sentence above it does, and the signed
  report shows an SV only by its id (changing it changes the signed-record format, #849).
- **CLIN-50 ·** Variant Explorer after #841: its excluded ClinVar statuses are only
  lowercased, so an API value `Likely_benign` excludes nothing (the UI sends "Likely
  benign"); it matches a variant against any annotation set stored for its key, the family
  page against its own row's (different only when one variant was imported with different
  annotations in different families). Its rows take the most severe impact over the
  transcripts but the first distinct consequence and HGVS (`_fetch_annotation_display`), so
  a row can pair one transcript's consequence with another's HGVS (#855); its gene filter
  (`gene=`) reads the annotation index only, so it misses a symbol the record carries
  outside its annotation (the chrM genes of the package import's MT gene map), which the
  family page finds (#857).
- **CLIN-52 ·** In-silico details after #844: the report draft (`reportNarrative.ts`) prints
  the raw value ("SIFT deleterious_low_confidence(0.03)") where the cards print "deleterious
  low confidence (0.03)"; the VEP TSV parser does not read the `Extra` column, so a file in
  VEP's default format (not `--tab`) would import without SIFT and PolyPhen (read in the
  code); `annotation_index.sift_terms` and `polyphen_terms` are written but never read; an
  export lists a variant that sits in two compound-het pairs twice (#845).
- **CLIN-53 ·** SV ids after #847: the HiFiCNV reader still takes a record's ID as its SV id
  (its IDs carry coordinates in practice); a discriminated NeedlR id depends on line and
  file order, so a changed input can move a review to the other clashing event; NeedlR and
  the uploads end an insertion or breakend written without END differently (CLIN-39), so
  they give it different ids.
- **CLIN-56 ·** mtDNA coverage (#852): `_coverage_from_rows` counts each half-open interval
  one base too wide and names `coverage.source` after the first rows' record ids (not
  displayed); the cap of 50,000 chrM coverage rows, which applied to the whole family, now
  applies per sample.
- **TRACE-12 ·** CNV classifications after #853: the drift banner and the signed view do not
  name the caller of a `variant_missing` entry, so an SV another caller still lists reads as
  gone; the CNV dialog's header does not show its row's caller, and the SV table hides the
  Source column by default.

### Technical file and documentation

- **DOC-1 ·** TF-09b: REQ-SEC-006 does not cite its log-masking tests; REQ-DATA-007,
  REQ-DATA-009 and REQ-MITO-002 do not cite the #762 and #759 tests; REQ-CARR-002's hazards
  differ between TF-09a (H2) and TF-09b/TF-06 (H1, H2); REQ-NIPT-005 was deleted (#700)
  instead of marked deprecated as TF-09a §5 requires.
- **DOC-2 ·** TF-02 §8 lists the sign-out gates without the incomplete-import gate and the
  refusal while data is written (TF-06 has both); TF-09c lists 10 of the 38 e2e suites; the
  ACMG posterior ticks (#765) are in neither REQ-UI-003 nor TF-12.
- **DOC-3 ·** The in-app docs do not describe Paraphase's *changes only* filter (#756); the
  handleiding does. (#826 added the repeat statuses and the male chrX flag to the user
  guide.)
- **DOC-4 ·** `RUN_INTEGRATION=1` before the seed scripts does nothing (only the pytest
  conftests read it), yet `docs/testing.md`, TF-09d, `playwright.config.ts`, the stylediff
  README and two seed scripts set it.
- **DOC-5 ·** Stale comments in the schema baselines (`02_reference.sql` names
  `01_core.sql`; a note in `04_traceability.sql`): schema files are edited only with a schema
  change.
- **DOC-7 ·** `docs/security-posture.md` lists neither the sign-in redirect check (#833) nor
  the package path guards (#842).

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
- **OPS-22 ·** `main` no longer requires a branch to be up to date before it merges, so two
  pull requests that each pass alone can fail together: #857 and #858 left a unit test
  failing on `main` until #860. A merge queue would test each against the one merged before
  it (owner).

### Engineering debt

- **ENG-4 ·** API leftovers whose removal changes the OpenAPI schema (owner decision):
  response fields never set (`GeneHomologOut.percent_id/percent_coverage`) or always the
  default (`ReferenceImportSourceAssemblyOut.cytobands_available/genes_available`);
  duplicate models (`FamilyStructureCoupleUpdate` = `ManualPedCoupleCreate`,
  `MonarchPhenotypeMatchOut` = `PhenotypeTermRefOut`); the CNV route's string-typed `end`.
  The SV review route's request model inherits `acmg` and `compound_het`, which the route
  ignores (#853).
- **ENG-5 ·** Routes no screen calls (kept, or dropped by the owner): `GET /assemblies/{species_id}`,
  `GET /families/{id}/hpo/query`, `PUT /families/{id}/hpo/{annotation_id}`,
  `GET /hpo/{hpo_id}`, `DELETE /families/{id}/small-variant-filter-presets/{preset_id}`,
  `DELETE /families/{id}/structural-variant-filter-presets/{preset_id}`, the BED endpoints'
  text format. Admin, traceability and ops routes without a screen stay by the owner's choice.
- **ENG-6 ·** Schema leftovers (owner decision): Postgres columns never read or written
  (`auth_login_attempts.last_success_at`,
  `small_variant_reviews.compound_het_partner_variant_keys`); ClickHouse columns always
  empty or constant and never read (`liftedOverChrom/Pos`, `sample_type`) or written and
  never read (`annotation_index.transcript_ids`). `structural_variant_reviews.variant_key`
  is never written (#853).
- **ENG-7 ·** Frontend: about 40 responses read through hand-written types where a generated
  type exists (most visualisation tracks, the admin, gene, reference and import pages,
  `apiTypes.ts` types without a contract check, the Mendeliome response read untyped); eight
  admin alias routes nothing links to (`/admin/hpo`, `/admin/upload`, `/admin/users`,
  `/admin/data/clickhouse`, `/admin/data/presets`, `/admin/data/tags`, `/admin/data/logs`,
  `/admin/gene-reference`; owner: bookmarks — `/admin/data` itself is the breadcrumbs'
  target); the "(deleted)" tag label also lives in the small-variant track; the viewer still
  offers `MT`; wheel-zoom leftovers (#489: a shift scale always 1, a `[data-panning]` rule
  that never matches, so no grabbing cursor during a pan).
  `buildStructuralVariantReviewPath` builds its path with `encodeURIComponent`, not
  `apiPath` (#853).
- **ENG-8 ·** Backend leftovers: gene-info keys one side reads and nothing writes, or writes and
  nothing reads (`mane_select_transcript`, `ensembl_canonical_transcript`, `hgnc_vega_id`).
  Duplication left for a later refactor: the audit and UI-event queue
  workers, the bounded download helper (Monarch, gene-info), several VCF INFO/FORMAT parsers
  with different flag handling, the interval-track tuple, small exact-copy helpers in the
  mito, repeat and Paraphase services, and parameters no caller sets.
- **ENG-9 ·** The ranking's comments promised a re-sort by the raw variant score (corrected
  in the code comments); the UI could offer that sort for novel-gene candidates.
- **ENG-10 ·** UI details: a failed saved-filter delete on *Settings* leaves the button at
  "Removing…"; two column headings in the data-inventory detail sit over the wrong columns;
  the admin presets page's loading text mentions family-linked presets; an empty transcript
  row spans 7 of 8 columns; several pages show a loading state without a spinner.
- **ENG-11 ·** Sentence case is incomplete after #754: headings (*Gene Explorer*,
  *Variant Explorer*, *Package Import*, *Family Builder*), the Family Builder and sample-upload
  tabs and buttons, the sign-up and login forms, the gene-panel page, the admin dashboard's
  groups and tiles, and several kickers; the docs quote some of them, so change both together.
- **ENG-12 ·** Round 2's small leftovers: a Warning or Fail QC chip that opens a report
  turns dark grey on hover (`button:hover` outranks the tone classes), and its tooltip is
  not linked for screen readers; the signed report's `describeBreachedMetric` writes the raw
  value; the workspace, NIPT, NIPT coverage and ROI pages do not wait for the project list,
  so the scope banner can appear a moment after the data; the ROI page says "could not be
  loaded" for a 404; Sample QC's own failure has no retry; saving project access on the
  Projects page leaves the Users page's account list stale for up to 30 s; a limit on a
  metric that has left the catalogue cannot be removed (REQ-QC-003); route tests that leave
  the request audit on write to whatever Postgres listens on localhost:5432.
- **ENG-13 ·** Ranking cache (#828): the background warm still computes queries without
  their intervals and exclusions, which takes one of six cache slots per family; the
  pedigree is read before the structure hash, so an edit in between can store a ranking on
  the old pedigree under the new hash (the warm after the edit usually replaces it). The
  warm also replays a ranking's stored `sv_hit_genes` instead of resolving them again, so
  its result goes under a key no live request matches (#845). `find_superset_candidates`
  reads each candidate row's whole ranking before it checks the row's panel (#858).
- **UI-3 ·** After #833: when a session expires (401) the app goes to `/login` without a
  `next`, so a sign-in returns to the dashboard, not to the page the user was on;
  `frontend/proxyLocation.mjs` would turn a backend Location of `http://backend:8000//host`
  into `//host` (no backend redirect produces one); the chromosome view draws a segments
  track only for a source that also has coverage (#846).
- **TRACE-10 ·** Coverage tracks after #837: the BED endpoints (`GET /bed/{sample}/coverage`
  and `/batch`) without `source` merge every caller's rows, averaging them in a window (the
  UI always names the source, #852), and their text format labels the value column `ratio`
  whatever the track holds; a HiFiCNV coverage import stopped between its rows and its
  record leaves rows drawn as a log2 ratio (the family is flagged import-unfinished).
- **ENG-16 ·** `integration/test_variant_explorer_keyset_integration.py` fails when run
  alone: it relies on an earlier test creating the Postgres schema (#857).

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

Fixed on 2026-10-13, wave 7, the P2 engineering items: the SV export's mate columns (DATA-5,
#849), package folders linked out of the import roots (SEC-26, #850), signal tracks and the
QC report kept to the family's folders (SEC-25, #851), coverage read as depth only from a
track of read depth (CLIN-46, #852), a CNV classification's evidence from its own caller's
row (TRACE-11, #853), SVs ranked by their copy change (CLIN-47, #854), the transcript the
filters held on named beside the card's (CLIN-51, #855), `auto` SNV imports recorded by
their callset (SAFE-23, #856), the explorer applying a panel's regions (CLIN-48, #857), a
panel's resolution kept between requests (ENG-14, #858) and small-variant uploads read whole
before they replace a callset (SAFE-25, #859). What they left open is listed above.

Fixed on 2026-10-13, round 6, four P1 defects the wave-5 fixes found: SIFT and PolyPhen read
by their prediction (CLIN-45, #844), phenotype prioritisation keeping the pairing and
carrier-screening rules (CLIN-44, #845), track loaders replacing only their own source
(SAFE-22, #846) and NeedlR SV ids from coordinates (CLIN-43, #847; the owner's choice). What
they left open is listed above.

Fixed on 2026-10-13, wave 5: the sign-in `next` (UI-2, #833), NeedlR breakend mates
(CLIN-40, #834), exports above 32,766 rows (DATA-3, #835), the alignment lookup (SEC-20 and
SEC-13, #836), the depth track's transform (TRACE-7, #837), update skips (SAFE-20, #838),
sub-panels served from a broader ranking (CLIN-34 and CLIN-35, #839), the second-hit badge
(CLIN-28, #840), the explorer's annotation filters (CLIN-29, #841), package paths (SEC-21,
#842) and uploads recorded in what a family holds (SAFE-19, #843). What they left open is
listed above.

Fixed on 2026-10-13, round 4: SV records without END or SVTYPE, read as VCF 4.4 defines them
(CLIN-33 and CLIN-37, #831). What it left open is listed above.

Fixed on 2026-10-13, round 3: the SV length filters (CLIN-25, #827), the *not assessed*
repeat status (CLIN-26, #826), the sub-panel ranking cache (CLIN-27, #828) and the
whole-family mark after a part-way restore (SAFE-16, #829). What they left open is listed
above.

Fixed on 2026-10-09 to 10-13, round 2: the built-in repeat thresholds (CLIN-22, #806), a
part-way restore's flag (SAFE-13, #807), `##source` versions (TRACE-6, #814), the *Not run*
QC chip (REG-1's REQ-QC-005 point and part of ENG-10, #813), PanelApp versions and the
archive's docs (CLIN-23 and DOC-6, #809), family pages without their record (SAFE-14,
#811), the second-hit half call (CLIN-4, #803), *MANE only* in the explorer (CLIN-5, #804),
the paged audit trail (AUDIT-1, #810), IDs in URLs (SEC-2, #805), Discover's containment
(SEC-3, #816), HSTS on the app page (SEC-5, #815), the admin account list and audit flags
(UI-1 and AUDIT-4, #819), UI-event times (AUDIT-2, #818), the audit batch (AUDIT-3, #820),
gzip members (DATA-1, #817), the explorer export (DATA-2, #808), the HiFiCNV transform
(TRACE-4, #812) and the matrix's cited tests (REG-9, #821). What they left open is listed
above.

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
