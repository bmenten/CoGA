# Open issues

Every known open issue, ranked by priority. Status of **2026-10-20**, `main` at `3b161a85`
(after the fixes #790–#911). Sources: the open GitHub issues and pull requests, the
follow-ups recorded in the pull requests of the last weeks, the residuals and open items in
`docs/` and the technical file, the codebase audit of 2026-10-08, whose agents checked each
item in the code, and what the fixes of 2026-10-09 to 10-20 found. Line numbers drift:
search for the function or file named, and re-check an item before acting on it.

The unranked roadmap is [ROADMAP.md](ROADMAP.md). Decisions only the owner or QA can make
are also tracked in issue [#518](https://github.com/bmenten/CoGA/issues/518).

**Priorities**

- **P0** — a defect that silently gives a wrong clinical result, loses data or opens a
  security hole in production: fix before anything else. **None:** nothing is in clinical
  use and the data are synthetic. Every open P1 is a release, operations, validation or
  regulatory step; the clinical defects left are P2.
- **P1** — blocks the first release candidate (RC): wrong behaviour or records with
  clinical or audit impact, or a step the RC needs.
- **P2** — to be done before the RC.
- **P3** — after the RC, or nice to have.

**Owners:** *Eng* engineering · *Owner* a decision of the device owner · *QA* the quality
manager · *LD* the laboratory director · *Ops* the operator or organisation admin · *DPO*
the data protection officer · *RA* regulatory affairs.

**The repository on that date:** 3 open issues (#1, #364, #518) and no open pull request
besides the one that updates this page; no open Dependabot alert and one open code-scanning
alert, CodeQL's log-injection rule on the account-change log line of `routers/auth.py`
(SEC-34); twelve required checks on `main`, which requires no approving review and no branch
to be up to date before it merges; CI green on `main`; no image pushed, nothing deployed, no
release tagged.

## Ranked list: P1 and P2

| Rank | ID | Pri | Issue | Owner |
|---:|---|---|---|---|
| 1 | REG-1 | P1 | Traceability-matrix rows whose status no longer matches the software | QA, Owner |
| 2 | CLIN-57 | P1 | TRGT's counting units are not validated per locus | QA, LD, Eng |
| 3 | REG-13 | P1 | The technical file still says the embryo calls and NIPT results are unsigned | QA |
| 4 | OPS-2 | P1 | No image pushed; the CI image build does not block a merge | Owner, Ops |
| 5 | OPS-3 | P1 | Terraform never applied; one environment; every push would deploy | Ops, Owner |
| 6 | OPS-4 | P1 | The go-live switches are all off (#364) | Ops |
| 7 | OPS-5 | P1 | No restore has been tested; no consistent Postgres + ClickHouse restore | Ops, Eng |
| 8 | REG-3 | P1 | The RC steps of TF-18 §3a have not started | Owner, QA |
| 9 | REG-4 | P1 | Technical-file sign-off (#518) | Owner, QA, RA |
| 10 | REG-5 | P1 | Risk-file confirmations | LD, QA |
| 11 | REG-6 | P1 | Performance-evaluation inputs (TF-10) | Owner, QA |
| 12 | REG-7 | P1 | Usability evaluation, DPIA filing, national provisions | Owner, DPO, RA, QA |
| 13 | REG-8 | P1 | Independent (4-eye) review: QA's per-release review to confirm and run | QA |
| 14 | SAFE-31 | P2 | Admin deletes and a pedigree replacement can still leave data missing unmarked | Eng, Owner |
| 15 | SAFE-32 | P2 | A refused SNV or haplotype file can still cost its callset | Eng |
| 16 | CLIN-58 | P2 | The sign-out dialog can hide a check that could not run | Eng |
| 17 | CLIN-59 | P2 | The callset's own allele count is read as gnomAD's | Eng |
| 18 | CLIN-60 | P2 | chrX distorts the PGT phasing-QC counts | Eng |
| 19 | CLIN-61 | P2 | The PGT phasing-QC thresholds are provisional | LD, QA |
| 20 | TRACE-13 | P2 | A family re-created under its ID fails its sign-out chain | Eng |
| 21 | UI-4 | P2 | The NIPT page has no review-tag filter | Eng |
| 22 | CLIN-62 | P2 | Small-variant track availability ignores a panel's genes | Eng |
| 23 | TRACE-14 | P2 | Some reference files are read without a checksum | Eng, QA |
| 24 | SEC-6 | P2 | The request audit holds clinical content with no retention period | Owner, DPO |
| 25 | REG-12 | P2 | Owner and QA confirmations of the fixes of 2026-10-09 to 10-20 | Owner, QA |
| 26 | CLIN-9 | P2 | QA confirmation of the carrier-screening preset and the chrM exclusion | QA |
| 27 | CLIN-10 | P2 | Parent–embryo IBS0 never checked on real imputed data | Owner |
| 28 | OPS-6 | P2 | Deployment settings: problem-report link, SMTP, the reference-data bucket | Ops |
| 29 | OPS-23 | P2 | Schema migrations: no backup first, no revision check | Ops, Eng |
| 30 | OPS-24 | P2 | Release mechanics untried until Google Cloud is configured | Ops |
| 31 | REG-10 | P2 | No regression truth set (GIAB / GeT-RM) | Eng, QA |
| 32 | REG-11 | P2 | Post-market, vigilance and security-process inputs | QA, RA, Owner |
| 33 | OPS-9 | P2 | Scale is unproven | Eng |

## P1 — before the release candidate

### REG-1 · Traceability-matrix rows whose status no longer matches the software

Wave 8 changed what stands behind several rows of TF-09b. What remains:

- REQ-QC-005: a sequencing-QC chip with a verdict but no number now shows the verdict and
  the metric that decided it (#867, CLIN-30); QA sets the status.
- REQ-CARR-003 (✅): since #879 the clinical audit trail records the first search that
  applies each panel version, and the signed record lists them; QA re-confirms the ✅. The
  printed NIPT report still names only the local version number.
- REQ-PGT-008, reworded by the owner (#866): CoGA shows each embryo's segment, copy-number
  and APCAD tracks and has no aneuploidy detector; TF-09b marks it ⚠, and QA approves the
  text and sets the status. REQ-PGT-007 ("Detect and present structural variants") and
  REQ-PGT-009 ("direct mutation detection") claim detection as REQ-PGT-008 did, and TF-05
  cites "simultaneous aneuploidy" (#866).
- REQ-TRACE-007 (◐): its gap, that the record holds no variant description, is stale since
  #879 froze each reported variant's description; the PGT and NIPT results are frozen too
  (#899, #900).
- REQ-TRACE-008 (◐): its action still asks for the anchor schedule and the export, which
  the owner has accepted as a residual until after the RC (OPS-7, #892).
- REQ-QC-003 is ✅, but #821's tests found that a limit on a metric that has left the
  catalogue cannot be removed (ENG-12).

**Next:** QA sets the statuses and the gap texts; the owner rewords REQ-PGT-007 and
REQ-PGT-009 as REQ-PGT-008 was.

### CLIN-57 · TRGT's counting units are not validated per locus

TRGT counts a repeat's motif across the region its catalogue gives the locus, which need not
be the stretch the clinical repeat number, and STRchive's ranges, count. In the public HG002
TRGT output and the laboratory's long-read files FMR1 leaves out the AGG interruptions (an
allele reads 1–3 repeats low, so a premutation of 55 can read 53, grey zone), HTT counts one
CAG more and POLG four flanking GCT copies (its common 10-repeat allele reads 14, #875). The
owner decided on 2026-10-14 that QA and the laboratory director validate each catalogue
locus, and that CoGA warns until they have: #889 marks a count within three repeats of a
boundary at a locus not yet validated, and adds the *Repeat Catalogue* admin
page that records each locus's validation, audited. **Next:** QA and the laboratory director
compare CoGA's count with the clinical repeat number on reference samples around every
boundary and record each locus; CoGA applies the corrections FMR1, HTT and POLG need (no
correction field exists yet); QA words the TF-06 hazard H17 and the TF-09a requirement #889
proposes; the mark and each locus's validation state are frozen with the signed repeat
calls (#889's follow-up).

### REG-13 · The technical file still says the embryo calls and NIPT results are unsigned

The owner decided REG-2: CoGA's sign-out is the analyst's analytical sign-off and freezes
the analysis, which the EPD report quotes by its report code, with its variant descriptions,
panels, reference data, repeats, Paraphase, PGT and NIPT results and coverage declarations
(#866, #879, #899, #900, #908, #911). Statements written while REG-2 was open now read
wrong: TF-06 H4 (the NIPT gate acts only on a sign-out of the family report), H5 and H6 (🔲
the embryo calls and the NIPT results are in no signed record) and H9 (🔲 no variant
description; only the family report is signed); TF-01 §4 condition 7 (the NIPT report has no
sign-out) and §3.3 (the embryo call "explicitly gated by QC", where #908's warning never
blocks); TF-02 §6 (no variant description; the embryo calls and the NIPT results unsigned).
**Next:** QA updates them, reconsiders the 🔲 on H5, H6 and H9, and sets the TF-09b rows of
REG-1.

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
`main` or `release` silently disables deploys), and since #904 such a deployment, and every
report it signs, carries a `-dev` version. **Next:** bootstrap per `docs/deployment-gcp.md`
§5–7, set the trigger to `release` before clinical use, and decide on a validation
environment separate from production (TF-09).

### OPS-4 · The go-live switches are all off (#364)

Terraform defaults: `db_runtime_role = "owner"`, the WAF not enforced,
`allowed_ingress_cidrs = []`, `clickhouse_restrict_egress = false`,
`storage_backend = "local"`, `alert_notification_emails = []`; Microsoft sign-in, go-live
switch 5 since #895, not configured (IT registers the application, single-tenant and
limited to the lab's group, and the go-live runs from a deployment with
`azure_admin_override` on to one with it off); no required reviewers on the `gcp-deploy`
environment; TF-13 S-4 (download audit) open. **Next:** switch each on as its own
change-controlled deployment (deployment-gcp.md §12.7–12.10 and §12.12); since #794 the
migration job starts in production, so the switch to the restricted database role can go
first.

### OPS-5 · No restore has been tested

Cloud SQL point-in-time backups and daily, crash-consistent ClickHouse disk snapshots are
taken independently; nothing restores both to one consistent point, and no restore drill has
been run. **Next:** run the drill and write a joint restore procedure before go-live.

### REG-3 · The RC steps of TF-18 §3a have not started

Feature freeze, the `-rc.N` tag, a documented risk-based review of the clinical-critical
modules, the bio-IT ingangsvalidatie (H11.1-F12.2), the security go-live, QA's confirmation
of the TF-10 §8 list and QA's first per-release review (REG-8). #894 proposes, for QA, to
freeze the five schema baselines at the RC (no longer re-applied, guarded by a checksum
test), every later schema change a numbered revision. **Next:** plan them once the P1 items
above are done.

### REG-4 · Technical-file sign-off (#518)

All TF documents are v0.1 drafts with placeholder owner and approver fields; the `Sxxxx`
identifier is unassigned (TF-02, TF-07, TF-18); TF-04 needs the declaring legal person and
its signatory; TF-07's role titles are open; QA is to file dated copies of the BELAC
certificate (the citation was updated in #724). **Next:** work through #518 and update its
checklist (the BELAC citation and the per-change level confirmations are done or
superseded).

### REG-5 · Risk-file confirmations

The accepted residual H15 awaits the laboratory director's confirmation that it now concerns
the authority to close an analysis in CoGA (TF-06, #866); it still says any project member
can sign out and that RBAC has global roles only, while a reader cannot since #887. The
severity matrix awaits QA; the risk review must assign hazards to REQ-DIAG-004/005 and
REQ-DATA-003 (TF-09), and consider two new ones: an EPD report that cites the wrong signed
version, and the use error of taking CoGA's sign-out for the medical sign-off (TF-12, #866).
**Next:** confirm and sign.

### REG-6 · Performance-evaluation inputs

TF-10's comparators, numeric acceptance criteria and sample sizes per application
(INPUTS-QUESTIONNAIRE C1–C7); TF-11 awaits data; the partial (◐) and unverified (⚠) rows of
TF-09b §3. **Next:** fill TF-10 before the beta validation.

### REG-7 · Usability evaluation, DPIA filing, national provisions

The TF-12 summative usability evaluation has not run; TF-14 still needs the signed DPIA, the
DPO's opinion, the data-processing agreement, the processor or sub-processor question and the
pseudonymisation decision; the FAMHP provisions and where the public declaration is published
are open (INPUTS A6/A7, TF-01). **Next:** close before clinical use.

### REG-8 · Independent (4-eye) review: QA's per-release review to confirm and run

The owner decided on 2026-10-14 that a pull request needs no approval and that QA reviews
each release instead (TF-18 §6, #866): every change to a module under `[tool.mypy]` and a
recorded sample of the rest, from each pull request's description, diff summary, tests and
verification records, in one record per release signed before its tag. `main` has no
CODEOWNERS, an administrator can change or disable its ruleset (on 2026-10-13 the checks
were removed for some minutes and #820 merged with a failing check), and there is one human
committer (INPUTS D7). **Next:** QA confirms the design, who performs it, its scope
(frontend clinical logic outside the mypy list?) and the departure from SOP H11.1-OP5's
per-change approval, and records the first review before the RC tag.

## P2 — before the release candidate

- **SAFE-31 · Admin deletes and a pedigree replacement can still leave data missing
  unmarked.** The admin *delete sample with data* rewrites the family's small variants, with
  no restore, before its SV rewrite, so a failure there leaves the sample without its data
  and nothing marking the family; the admin family delete has the same order. The PED or
  manual replacement and the structure clear delete the ClickHouse data last since #876, but
  a failed commit or a crash between that delete and the commit still leaves the family
  unmarked (deleting after the commit would close it for the PED replacement: owner).
  `_rewrite_with_sample_calls` (mito, NIPT) only logs when its restore fails. Found while
  fixing SAFE-28 (#876).
- **SAFE-32 · A refused SNV or haplotype file can still cost its callset.** On an overwrite
  whose snapshot could not be taken, the SNV and haplotypes clean-ups delete their callset
  even when the loader refused the file before it replaced anything. Found while fixing
  SAFE-26 and SAFE-27 (#873).
- **CLIN-58 · The sign-out dialog can hide a check that could not run.** When the Sample QC
  both failed and holds a check that could not run, the dialog lists only the failure: the
  unverifiable concerns (a skipped sex check, the maternal lineage, a NIPT check that could
  not run) reach the 409 and the audit event but not the page, so the reason the analyst
  writes may not cover them. Found while fixing CLIN-8 (#869).
- **CLIN-59 · The callset's own allele count is read as gnomAD's.** When a record's CSQ has
  no gnomAD allele count, the VEP parser takes the INFO `AC`, the count in the callset
  itself, as `gnomad_ac` (`variant_annotation_parser._EXTRA_COUNT_ALIASES`), so a maximum
  gnomAD AC filter filters on the family's own count. Found while fixing ENG-1 (#877).
- **CLIN-60 · chrX distorts the PGT phasing-QC counts.** Outside the PARs
  `phased_marker_service` counts every member as having two copies: when men's calls are
  written as one allele every embryo shows 0 informative sites, and a son's single X written
  as two copies shows a Mendelian error rate up to 100 %, so at an X-linked locus #908's
  warning can fire for that alone (it over-warns, never under-warns). The ROI marker review
  shows the same numbers; the fix belongs in the marker counting. Found while building the
  PGT QC warning (#908).
- **CLIN-61 · The PGT phasing-QC thresholds are provisional.** Since #908, as the owner
  decided on 2026-10-20, an embryo call carries a warning, never a block, when its phasing
  QC at the ROI has fewer than 50 informative sites, a Mendelian error rate above 2 % or
  fewer than 6 markers on a parent's side the call reads (counted over the whole region and
  on imputed data, where ESHRE counts each side of the gene); signed records freeze these
  values. At exactly 2 % the Sample QC warns and the haplotype track does not; the verdict
  follows the track. The laboratory director and QA confirm the values, and whether a small
  ROI should include its flanks, as the warning will fire often there.
- **TRACE-13 · A family re-created under its ID fails its sign-out chain.** Sign-out
  versions are numbered per family row but chained by family ID, so a family deleted and
  re-created under the same ID, with sign-outs in both lives, fails the chain verification
  as if it had been tampered with. Found while fixing SEC-7 (#893).
- **UI-4 · The NIPT page has no review-tag filter.** A NIPT sign-out freezes the variants
  tagged `report` (#900), but the NIPT page cannot list them, so the analyst cannot check
  before signing out which variants the record will hold. Found while building the NIPT
  sign-out (#900).
- **CLIN-62 · Small-variant track availability ignores a panel's genes.** It applies only a
  panel's regions, and tests every row against every region, which may be slow for the
  Mendeliome on the chromosome view; the lists and the SV tracks match a panel by its genes
  and its regions since #897 and #906. Found while extending the panel rule to SVs (#906).
- **TRACE-14 · Some reference files are read without a checksum.** The HPO download, the
  STRchive file and the bundled ClinGen recurrent-CNV file are pinned (#863, #888, #890),
  but the startup imports the segmental-duplication file
  (`REFERENCE_SEGMENTAL_DUPLICATIONS_PATH`, the bucket's copy on Cloud Run) unchecked, the
  clinical CNV knowledgebase build downloads ClinGen's recurrent-CNV files without a
  checksum and records only their URL, and an HPO file already in place is loaded unchecked
  (only the download is checked). Found while pinning the reference data (#863, #890).
- **SEC-6 · The request audit holds clinical content with no retention period.**
  `audit_log_events` stores request bodies in an append-only table, and since #893 the
  UI-event log is append-only too, so neither can be cleaned up without a controlled
  procedure; the retention period is still a placeholder in TF-14 (INPUTS E5). The owner and
  the DPO decide retention and masking.
- **REG-12 · Owner and QA confirmations of the fixes of 2026-10-09 to 10-20.** Each fix made
  a choice its pull request asks the owner or QA to confirm. Round 1:
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

  Wave 8 (what the owner decided on 2026-10-14 to 10-20 is left out):
  - #862 (CNV kinds): evidence and hand edits stay with their kind, and the other kind's are
    discarded on save without a warning; no TF-10 §8 line.
  - #863 (HPO pin): a refused download alerts no one beyond the log; the TF-08 wording;
    whether a requirement covers the pinning of reference data.
  - #864 (raw-file paths): 403 for a path outside CoGA's folders, a relative one included
    (was 410); the rule is not per family; a moved import folder's packages are refused
    until it is listed again; REQ-SEC-007 names CRAM/BAM only.
  - #865 (SV tracks): the grey *Other* row, always shown; a CNV drawn by its copy number, as
    a deletion without one, and on each member's track by that member's own; a TRA drawn as
    a BND; the REQ-PGT-007 citations.
  - #866 (device boundary): QA and RA approve the boundary wording (README, NOTICE, TF-01 §1
    and §4 condition 8, TF-02 §3) and TF-01 §3.3's limits of the analyst's review; QA
    approves REQ-RPT-001's text, REQ-PGT-005's new sentence, the TF-06 controls, TF-15 items
    2 and 8 and the TF-10 change; the owner or the laboratory director says whether to warn
    about reduced penetrance. REQ-PGT-008: REG-1; the new hazards: REG-5.
  - #867 (QC chip): REQ-QC-005's status (REG-1).
  - #868 (column bounds): other unstorable values stored as missing rather than the file
    refused, an allele depth as 0; a record without a storable position skipped and counted;
    the one-time `ALTER` rewrites both depth columns of a large table (ops).
  - #870 (frozen evidence): a value that appears later counts as drift; old snapshots are
    not refilled; the wording of REQ-TRACE-002 and REQ-TRACE-003.
  - #871 (SV alleles): the TF-10 §8 phrase.
  - #872 (empty files): an empty file fails its dataset and keeps the earlier data, an
    all-zero HiFiCNV depth file included; a header-only mito SV file still replaces the
    earlier chrM SV calls with none.
  - #873 (import flags): only the latest job is named in the banner, the 409 and the signed
    record; an update with an unreadable SNV file fails and flags the family; a dataset
    registered for want of an assembly or a file clears nothing.
  - #874 (panel cost): small panels are 0.2–0.4 s slower per page, as one method was kept;
    the REQ-CARR-001 entry.
  - #875 (benign range): the wording, and the "normal 10" cutoff beside POLG calls that read
    "14 / 14" (CLIN-57).
  - #876 (upload marks): a stopped upload is gated like an unfinished import and only an
    overwrite clears it; a stopped Sniffles, Spectre or manual SV upload must be uploaded
    again for every sample; a failed small-variant upload keeps #859's "package import"
    wording; an SV or BED upload that fails after writing stays marked; a failed admin SV
    delete marks every SV dataset.
  - #877 (filter parity): a gene keeps every variant annotated with it in every view
    (reversing CLIN-27's test); a value equal to a threshold at its stored 32-bit precision
    meets it; a variant that starts in a location is kept; the rsID filter reads the stored
    rsID only; whether TF-10 names "the same filter keeps the same variants in every view".
  - #878 (ACMG SF): the shortened LMNA footnote; whether TF-10 §8 names ACMG SF v3.3.
  - #879 (frozen analysis): a search that cannot record its panel fails; gene-reference
    releases are frozen for the reported genes only; a section that cannot be read does not
    stop the sign-out; the report code's first six symbols derive from the family ID; a
    deleted family's records cannot be found by code; the TF-10 §8 text.
  - #887 (reader level): a reader cannot change a case's status, assignee or reviewer, keeps
    personal filter presets, can view and print signed versions but cannot sign out; QA:
    REQ-SEC-001 (the reader level), REQ-TRACE-004 (phenotype events), REQ-UI-004 (readers
    see no audit timeline), TF-15 item 8 and the TF-10 §8 wording; TF-06 H15: REG-5.
  - #888 (STRchive): a copy with other spacing or line endings is refused, and a missing
    file stops the backend; every start removes the loci the release does not list; a call
    over no tract or several of a gene reads *not assessed*; REQ-DIAG-004's ✅; whether TF-10
    §8 names the release.
  - #889 (count units): the three-repeat margin at every locus; a validation per
    catalogue row, recorded by any admin; the FMR1, HTT and POLG texts (CLIN-57).
  - #890 (reference pins): the removed HPO settings are ignored without a warning; no mirror
    can stand in for purl.obolibrary.org; the TF-08 wording and its record of the ClinGen
    comparison; the patch level.
  - #892 (Terraform secrets): the ClickHouse VM is restarted right after any certificate
    reissue; key rotation is a code change; the google provider goes from 5 to 8 in one
    step; the Cloud Run services' deletion protection; QA approves the reworded S-3 actions
    and OPS-7's residual.
  - #893 (clinical audit): family creation, an import that changes a member's status and an
    admin's sample delete are each recorded as a pedigree change; a pair review is recorded
    once, under its variant; a second concurrent pedigree edit is refused; a summary names
    at most five changes; QA words a TF-09a requirement for the pedigree audit and its
    chain.
  - #894 (Alembic): an upgrade runs forwards only, so going back means restoring the backup
    taken before it; revision 0003 also updates two other old checks; QA: the baseline
    freeze (REG-3), risk class H for alembic, Mako and MarkupSafe, a requirement for
    migrations.
  - #895 (Microsoft sign-in): a first sign-in makes an active viewer in no project, with no
    approval step; IT must require assignment to the lab's group; the emergency sign-in, off
    by default, is a password login without MFA; an account with the same e-mail is refused,
    not linked; a leaver's token stays valid for about an hour unless the CoGA account is
    deactivated; QA: REQ-SEC-003's wording, requirements for role changes and new accounts.
  - #896 (SV filters): a hidden family with an unreadable filter number answers 422, not
    404; the REQ-PGT-007 citation.
  - #897 (large panels): a clinical change that needs clinical follow-up validation
    (probably major under TF-18); the TF-10 §8 item.
  - #898 (coverage content): only coverage BEDs declare their content; an undeclared one is
    drawn on its own range with a warning; each depth track has its own scale; the mtDNA and
    NIPT checks name every track they skip; a misspelt declaration stops validation; a chrM
    VCF without a variant fails the mito dataset for every sample; a requirement for the
    chart's axis.
  - #899 (PGT record): the overview and the chromosome view still compute risk states in the
    browser (27 shared cases); one ROI per family (PROD-9); the imputation software and its
    reference panel are frozen only when the annotation versions list them; the live report
    shows no embryo calls; the TF-10 §8 phrase and REQ-PGT-005's evidence.
  - #900 (NIPT record): only the variants tagged `report` are frozen, and the recessive
    fetal risk is computed from them alone; a failed NIPT check blocks unless acknowledged,
    one that could not run does not; all capture targets' coverage is frozen; a later
    artifact-list change or another pregnancy reads as changed; the TF-10 §8 area.
  - #902 (consequence and constraint): not frozen are LOFTEE's filter and flags, the gene's
    pLI and its ClinGen haploinsufficiency, which PVS1 also reads; re-review prompts on
    missense classifications after a gene-reference refresh; the labels; the ✅ of
    REQ-TRACE-002/003 and REQ-UI-003.
  - #904 (release mechanics): every build but a release reports a `-dev` version; QA
    approves the wording in TF-18 §2 and §5, TF-13 §5 and the release-record template,
    decides whether the SBOMs on the GitHub release are the archived copy and whether TF-15
    §1 explains `dev`, and confirms the rollback procedure.
  - #905 (STRchive release): the TF-08 record of v2.18.0.
  - #906 (SV panels): a small panel also keeps an SV whose annotation names one of its genes
    without overlapping it; a panel and a gene symbol together mean both; an SV with its END
    before its POS is kept only inside one region; the SV panel rule under REQ-PGT-007 or a
    requirement of its own; probably a major change.
  - #908 (PGT QC warning): the thresholds (CLIN-61); "QC not counted" warns; the warning
    never blocks a sign-out or asks a reason; the TF-10 §8 phrase.
  - #909 (pickers): a save that resends a current assignee or reviewer who has since become
    a reader is accepted; the emergency administrator is in the pickers; a failed import's
    restore is recorded as done by whoever started the import; whether REQ-TRACE-004 covers
    phenotype edits and imports.
  - #911 (coverage tracks): every record holds the list, even empty, so a coverage track
    loaded, or loaded again, after sign-out reads as a change; only the kind of coverage is
    frozen, not the mtDNA depth or the NIPT coverage result; the footer wording; whether the
    NIPT section and this one are enough; the TF-10 §8 phrase.
- **CLIN-9 · QA confirmation of the carrier-screening preset and the chrM exclusion** (#763).
  TF-09a records the chrM exclusion as the owner's decision.
- **CLIN-10 · Parent–embryo IBS0 never checked on real imputed data** since the genome-wide
  site sample (#742). The owner checks one real PGT family on the Sample QC page.
- **OPS-6 · Deployment settings: problem-report link, SMTP, the reference-data bucket.**
  `COGA_PROBLEM_REPORT_URL` is unset, so a clinical build has no problem-report link
  (TF-15); `SMTP_HOST` is not set in Terraform although `ADMIN_EMAIL` is, so a sign-up
  notification fails against localhost (only without Microsoft sign-in, which refuses
  sign-ups, #895). The reference-data bucket must hold the shipped STRchive file, without
  which, or with another, the backend does not start (#888), the ACMG SF list, followed by a
  full gene-reference sync (#878), and the vendored `hp.obo`, as no mirror can stand in
  since #890. The HPO download is pinned (#863, #890).
- **OPS-23 · Schema migrations: no backup first, no revision check.** Since #894 a version
  with a schema revision migrates the database forwards only, at its first start or in the
  migrate job, but the deploy pipeline takes no backup first (ops takes an on-demand Cloud
  SQL backup by hand), and with the restricted role the API does not check at start that
  the database is at its own revision. Found while fixing ENG-3 (#894).
- **OPS-24 · Release mechanics untried until Google Cloud is configured.** #904's deploy
  reads each image's digest on the assumption that `gcloud builds submit` prints it last
  (the step fails rather than deploy a wrong image), and `release-sbom` needs the workflow
  token's `contents: write`. At the first configured build ops checks the digests and the
  `@sha256:` images in the plan, and at the first beta the SBOMs on the release and the
  plain version in `/api/version`; GitHub's immutable releases must stay off. Found while
  fixing OPS-8 (#904).
- **REG-10 · No regression truth set.** No GIAB or GeT-RM set with a concordance harness,
  which TF-18 §4 needs for every minor change after the RC (INPUTS C6).
- **REG-11 · Post-market, vigilance and security-process inputs.** PMS cadence and
  indicators, FAMHP reportability and the support contact (TF-16, TF-17);
  vulnerability-response targets, a threat model and a base-image SBOM (TF-13); the supported
  browser (TF-15); form H11.1-F2 vs F14 (TF-18); the TF-05 market survey; panel versions
  (TF-01).
- **OPS-9 · Scale is unproven.** No genome-scale import or query benchmark; one uvicorn
  process; the import and refresh workers run in the API process; VCF lines are parsed on
  the event loop between inserts; Sample QC blocks the event loop for about 2.3 s for ten
  samples (genotype parsing and evaluation, no `asyncio.to_thread`), and a NIPT sign-out
  runs the NIPT analysis twice, for the Sample QC and for its record (#900). An unfiltered
  Variant Explorer page took 33 to 43 s on a 2-million-variant synthetic cohort, as it
  aggregates every carrier (#841). Since #906 the Mendeliome keeps every panel SV of a
  family with more than 50,000 SVs, so the ranked view's limit of 5,000 SVs is reached more
  often.

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
- **SAFE-18 ·** Import bookkeeping (#807; its one scope per failed dataset was SAFE-26,
  fixed by #873): the job record's dataset table shows each loader's own status while the
  flag names the dataset failed; `_flag_family_import_incomplete`'s docstring still says the
  import takes no snapshot. Found by #873: when an upload's failure sits beside an import's,
  the banner and the sign-out name the upload's dataset under the import's job (since #859),
  and the job log calls an upload's failure "an import"; `merged_import_failure` drops a
  stored flag that is not in its shape (`true`) when a later import fails.
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
  (the conservative side, #856). An SNV update of imputed genotypes counts the stored small
  variants only, not the haplotype blocks (#873).
- **DATA-6 ·** Lookups that still bind one Postgres parameter per item (a panel save's gene
  terms in `_resolve_gene_regions`, families or samples in other services) fail above 32,767
  items, as the export lookups did before #835 and the panel filter's gene lookup before
  #858; no realistic request reaches that.
- **SAFE-33 ·** Files after #872: a dry run does not report an empty data file; a per-sample
  dataset writes its earlier samples before it finds a later sample's empty file (SAFE-3);
  a chrM SV file whose records all lie on other chromosomes reads as "no call", which
  removes the earlier chrM SV calls; an unreadable chrM SV file fails the mito dataset after
  its SNV files were written; a malformed TRGT value still answers 500, though before any
  delete; a HiFiCNV CNV file without calls counts as "no CNV called", but when no sample has
  a call the earlier calls are kept.
- **SAFE-34 ·** An import's clean-up counts only datasets that ended `imported`, so a new
  family whose phenotypes ended with warnings while every other dataset failed is deleted
  with the phenotypes it did import (#909).
- **DATA-7 ·** Values after #868: a non-numeric allele fraction is dropped from the list,
  which shifts the later alleles' fractions; `is_gnomad_gt_5_percent` is computed from the
  raw value, so an absurd frequency still marks a variant as common for the NIPT artifact
  seed; Postgres integer columns filled from files (Paraphase copy numbers, TRGT) are not
  checked for overflow.
- **DATA-8 ·** A package re-import overwrites the stored PED text but not the stored
  relationships or sexes, so the two can disagree (#893).

### Security and audit

- **OPS-7 ·** Integrity anchors, an accepted residual until after the RC (the owner's
  decision of 2026-10-14, TF-13 §3, #892): made only on an admin's request, nothing
  schedules them, `export_anchor()` is an empty hook (so deleting the newest anchors goes
  unnoticed while the API connects as the owner), verification trusts one key, no HSM
  (REQ-TRACE-008 🔲). QA approves the residual and decides whether the laboratory's
  procedure sets how often anchors are made; the owner re-evaluates it after the RC.
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
  address. (Sign-ups exist only without Microsoft sign-in, #895.)
- **SEC-12 ·** The `ALGORITHM` environment variable silently changes the JWT algorithm
  (`config.py`), undocumented and unchecked. Make it a constant.
- **SEC-14 ·** Blocking calls on the event loop: the QC-report routes call `object_exists`
  and `presigned_get_url` directly, and the admin raw-file list checks each row's file there
  (#864). (Since #895 the Microsoft sign-in fetches its signing keys asynchronously.)
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
  best-effort, bucket grants are bucket-wide, secret rotation is manual. When Terraform
  replaces a ClickHouse TLS secret version it destroys the old one before it creates the new
  one, so a VM booting in between cannot fetch its key, and since #892 the VM must be
  restarted right after any certificate reissue.
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
- **SEC-31 ·** Raw files after #864: the admin page shows a refused Verify as
  `Could not verify <file>.` without its reason, and lists a remote row outside the roots
  as "kept in the object store"; `_staging_root()` takes `<tmp>/coga-family-imports` as it
  finds it, so a link planted on a shared `/tmp` would widen the rule (the container's
  `/tmp` is private).
- **SEC-32 ·** Sign-in after #895: the import job finds its requester by e-mail
  (`family_package_import.py`), so a Microsoft account whose e-mail changes between the
  request and the run is not found; the IGV viewer reads the stored token once, so a long
  IGV session can outlive its token (a Microsoft token lasts about an hour); an
  administrator can deactivate their own account. The HPO download follows a redirect from
  HTTPS to plain HTTP (harmless while its digest is checked, #863).
- **SEC-33 ·** `GET /api/users` still gives every signed-in user the names and e-mail
  addresses of all active accounts (the accepted staff-roster residual,
  `security-posture.md`), though no screen calls it since #909; removing the route would
  end it (owner).
- **SEC-34 ·** CodeQL alert 51 (`py/log-injection`) flags the account-change log line of
  `PATCH /api/auth/users/{id}` (`routers/auth.py`, #895), which logs account IDs, a role
  from a fixed list and a boolean; the owner dismisses it, as the gh token cannot, or the
  line logs less.
- **TRACE-16 ·** The phenotypes that go with a sample, a member or a family an administrator
  deletes are recorded as no `phenotype` event (the `pedigree` and `family_deleted` events
  do not name them; `clinical-traceability.md` says so), and a family deletion records no
  reason, which would need an API and UI change (#893, #909).

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
  of their own: CLIN-58); since #800 the Sample QC page names the samples whose sex was not
  checked. Its wording after #869: `_qc_gate_message` says "could not be verified for an
  asserted pedigree relationship" for a lone sex check too; the joined reasons read
  "…inference.; Sample …"; a warning can appear twice in a dialog without a failure; one
  sentence has two colons ("Sample X: sex could not be checked: …").
- **CLIN-32 ·** STRchive's own gaps and overlaps, for QA (#806): ATXN7 36 reads intermediate,
  PPP2R2B 33–39 normal, ATXN10 800–850 pathogenic; the built-in and STRchive rows differ in
  motif orientation at nine loci (it matters only where TRGT reports several motifs); whether
  PABPN1's `GCN` matches the pipeline's TRGT BED is unchecked.
- **CLIN-38 ·** Repeat details (#826): at a pathogenic-only locus a count between the benign
  maximum and the threshold reads normal (no grey zone); the male chrX note at a catalogued
  locus says "outside catalogued ranges", the wrong reason; *review* calls have no chip
  style of their own.
- **CLIN-39 ·** SV details (#827): a translocation shows as 0 or 1 bp (the report sentence
  reads "A 0 bp breakend"); a symbolic call with neither END nor SVLEN is 1 bp from NeedlR
  and HiFiCNV and 0 bp elsewhere (since #871 a call written as its sequence is measured by
  its alleles); a negative exact length (the list writes deletions with a minus sign)
  silently matches nothing. And a call without END: the package's NeedlR, HiFiCNV and chrM
  Sniffles2 readers end it at POS + SVLEN, its sign dropped, whatever its type (without
  SVLEN at POS + 1, chrM at POS), so an insertion or a breakend spans bases after it there,
  while the uploads put it on its POS as VCF 4.4 does (#831).
- **CLIN-42 ·** SV records (#831): a symbolic deletion, duplication, inversion or CNV with
  neither END nor SVLEN stays on its POS and is stored without a word (the upload could
  count or refuse it); an SVTYPE written in lower case (`bnd`) gets no mate from the
  uploads, which compare `BND` exactly (the NeedlR reader takes any capitals since #834). A
  legacy TRA record whose END lies on the partner chromosome is stored without its
  `remote_chr`, as only a BND reads a mate, so the tracks and Circos place its end on the
  wrong chromosome (#865).
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
  family page finds (#857). Nor does it trim the rsID it filters on (#877).
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
  they give it different ids. Two insertions at one position in one upload become one SV,
  with the first one's length, as the uploads have no discriminator (#871).
- **CLIN-56 ·** mtDNA coverage (#852): `_coverage_from_rows` counts each half-open interval
  one base too wide and names `coverage.source` after the first rows' record ids (not
  displayed); the cap of 50,000 chrM coverage rows, which applied to the whole family, now
  applies per sample.
- **TRACE-12 ·** CNV classifications after #853: the drift banner and the signed view do not
  name the caller of a `variant_missing` entry, so an SV another caller still lists reads as
  gone; the CNV dialog's header does not show its row's caller, and the SV table hides the
  Source column by default.
- **CLIN-63 ·** Filter details after #877 and #906: the API accepts a NaN threshold, on
  which the SQL and the Python paths then differ; the SV list's Python chromosome filter
  compares names as written (MT against M) where its SQL uses aliases; the carrier-screening
  SQL prefilter needs a gene symbol, so a variant with only an Ensembl gene ID never reaches
  the couple rule; the SQL rsID check also reads the shared annotation index, whose rsID
  another family's VCF may have set; the Python gene-filter overlap
  (`_variant_overlaps_regions`) does not swap reversed region coordinates where the SQL does
  (no writer stores them).
- **CLIN-64 ·** Repeat catalogue after #888 and #905: STRchive and disease tags
  (`anticipation`) are registered as TRID aliases (they resolve by position, the safe side,
  but name no locus); moving the pin later leaves a window in which a cold start of the old
  version is refused until the new revision serves (documented); STRchive v2.28.0
  (2026-10-08) is out, and moving to it is a reviewed change of the file and the pin, after
  the per-locus checks (CLIN-57).
- **CLIN-65 ·** Gene page after #878: the *GenCC details* subsection now holds the ClinGen
  counts and the ACMG row; the *Dosage sensitivity* count stays 1 when dbNSFP and ClinGen
  both give an item; the page's type has a `cpic_pharmgkb` key no source writes.
- **TRACE-15 ·** Evidence snapshots after #870 and #902: the gene lookup matches a symbol
  case-sensitively after a case-insensitive database read, and takes the newest profile of
  any assembly; an *ambiguous* AlphaMissense class reads as "No in-silico predictions
  available."; a VCF's own `MISSENSE_Z` is stored but never read;
  `evaluate_classification_drift` skips a stored snapshot that is not a JSON object, which
  the sign-out does not count as `no_snapshot` (the writer always stores an object).
- **TRACE-17 ·** Coverage records after #911: the package's APCAD BED fallback and the
  per-target coverage tables record no `uploaded_from`; the notes of the NIPT
  coverage-over-tracks check are frozen nowhere; a malformed list inside a recorded section
  reads as empty rather than as not readable, in every section parser.
- **UI-5 ·** The SV page's list request carries no `project_id` while its export does, and
  `project_id` picks the assembly, so for a family whose projects span two assemblies the
  list would answer 400 where the export works (not checked on such a family, #896).

### Technical file and documentation

- **DOC-1 ·** TF-09b: REQ-SEC-006 does not cite its log-masking tests; REQ-DATA-007,
  REQ-DATA-009 and REQ-MITO-002 do not cite the #762 and #759 tests; REQ-CARR-002's hazards
  differ between TF-09a (H2) and TF-09b/TF-06 (H1, H2); REQ-NIPT-005 was deleted (#700)
  instead of marked deprecated as TF-09a §5 requires.
- **DOC-2 ·** TF-02 §8 lists the sign-out gates without the incomplete-import gate and the
  refusal while data is written (TF-06 has both); TF-09c names 28 of the 75 e2e suites; the
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
- **DOC-8 ·** Wording after #866 and #904: "signed clinical report" remains in
  `monarch_ingest.py` and `scripts/check-release-version.sh`, and the user guide's section
  "Clinical report and sign-out" keeps its title (#866); RELEASING.md says a tag on its own
  does nothing, "see Known limitations", which do not say why (#904).

### Operations, CI and infrastructure

- **OPS-11 ·** Coverage gate: `ci.yml` passes `--cov-fail-under=70` while
  `check-coverage-floor.py` enforces 74 in the same job (a workflow edit for the owner);
  several module floors lie far below what CI measures; ten floored modules are outside the
  mypy gate (`family_package_import` would pass now). Modules outside the gate carry mypy
  errors: five in `hpo_service.py` (#863), one in `family_service.py` (#898), and
  `get_current_user`'s `request: Request = None` and `UserRead(id=str)` (#895).
- **OPS-12 ·** Dependabot does not track the CI service images, `fake-gcs-server`, the
  Terraform image defaults and providers, the Cloud Build builder image, the `pip-audit` and
  `markdown` pins in the workflows, or the image tags in `scripts/*.sh`; Terraform's own
  version is pinned in CI since #892 (1.16.6), but nothing updates it, and its provider lock
  file is not committed.
- **OPS-13 ·** Workflow tidy-ups (edits for the owner): an unreachable branch-tag case in
  `build.yml` (#904 found its unused branch-build path too), stale comments in `build.yml`
  and `ci.yml`, sixteen environment lines repeated across three jobs, the coverage job
  installing the full dev lock.
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
- **OPS-25 ·** TF-13 S-7 says the backend lockfiles are reproduced in Docker; #894 compiled
  its change with pip-compile under Python 3.13 outside Docker, keeping the Python 3.12
  annotations by hand. Run `./scripts/compile-requirements.sh` in Docker and expect no
  diff, or correct S-7.

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
  is never written (#853), nor is the SV table's `calls.gq` (#868).
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
  `apiPath` (#853), and `ChromosomeViewWorkspace.tsx` puts `source=` into BED URLs
  unencoded (#898); `GenomeOverviewPage.tsx` sends the SV route `qual`, `read_support` and
  `filter`, which it never took (#896); `lib/cnvAcmg/evaluate.ts` keeps its own copy of
  `svKind`, identical to `svTypes.svKind` (#865).
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
  row spans 7 of 8 columns; several pages show a loading state without a spinner; in the
  members table's edit mode a long QC chip overlaps the Remove button (#867).
- **ENG-11 ·** Sentence case is incomplete after #754: headings (*Gene Explorer*,
  *Variant Explorer*, *Package Import*, *Family Builder*), the Family Builder and sample-upload
  tabs and buttons, the sign-up and login forms, the gene-panel page, the admin dashboard's
  groups and tiles, and several kickers; the docs quote some of them, so change both together.
- **ENG-12 ·** Round 2's small leftovers: a Warning or Fail QC chip that opens a report
  turns dark grey on hover (`button:hover` outranks the tone classes), and its tooltip is
  not linked for screen readers; the signed report's `describeBreachedMetric` writes the raw
  value ("18.57 x" where the chip writes "18.6x", #867); the workspace, NIPT, NIPT coverage
  and ROI pages do not wait for the project list, so the scope banner can appear a moment
  after the data; the ROI page says "could not be loaded" for a 404; Sample QC's own failure
  has no retry; saving project access on the Projects page leaves the Users page's account
  list stale for up to 30 s; a limit on a metric that has left the catalogue cannot be
  removed (REQ-QC-003); route tests that leave the request audit on write to whatever
  Postgres listens on localhost:5432.
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
  record leaves rows drawn as a log2 ratio (the family is flagged import-unfinished). A
  segments BED declares nothing of what it holds (#898).
- **ENG-16 ·** `integration/test_variant_explorer_keyset_integration.py` fails when run
  alone: it relies on an earlier test creating the Postgres schema (#857).
- **ENG-17 ·** The panel's window join reads the family's table a second time within its
  query, as the annotation and sample subqueries do, so a write landing between the two
  reads could make one request miss a row (#874).
- **ENG-18 ·** The e2e stack's HPO terms carry no release, so every e2e sign-out records an
  "HPO release not recorded" gap (#879).

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
- **PROD-9 ·** A PGT family has one ROI: the signed record's PGT section holds one, so two
  loci need its format extended after the RC (#899).

## Closed since the last review

Fixed on 2026-10-14 to 10-20, wave 8: the CNV dialog's suggestions per kind (CLIN-55, #862),
the HPO download pinned to the shipped release (OPS-6's HPO part, #863, #890), raw-file
paths kept to CoGA's folders (SEC-29, #864), every SV drawn on the tracks (CLIN-54, #865), a
QC chip's verdict without a number (CLIN-30, #867), values beyond their ClickHouse column
(SAFE-30, #868), refusal reasons that name their sample (CLIN-8, #869), the frequencies,
in-silico scores, consequence and missense constraint frozen with a classification (CLIN-7,
#870, #902), AlphaMissense counted in the PP3/BP4 suggestion (#902), insertions measured by
their alleles (CLIN-41, #871), files without a record refused before anything is replaced
(SAFE-29, #872, #898), every failed scope kept, an `auto` import's own callset read and
flags that can clear (SAFE-26, SAFE-27 and SAFE-17, #873), a panel's regions joined on
windows (ENG-15, #874), a count inside a stated benign range read as normal (CLIN-36, #875),
writes that mark the family before they delete and a failed SV rewrite put back (SAFE-28,
#876), one rule for both filter paths (ENG-1, #877), the ACMG SF list on the gene page
(CLIN-19, #878), the reader level and audited phenotype edits and imports (SEC-1, #887,
#909), the shipped STRchive release and a gene's tracts read by position (CLIN-31, #888,
#905), the ClinGen file confirmed as V2.1 (TRACE-3, #890), secrets kept out of the Terraform
state (OPS-10, #892), pair reviews and pedigree changes as clinical audit events (SEC-7,
#893), an Alembic ledger (ENG-3, #894), Microsoft sign-in (SEC-4, #895, #907), the SV
filters declared once (ENG-2, #896), the coverage BED's content declared and frozen
(TRACE-9, #898, #911) and the release mechanics (OPS-8, #904). The owner's decisions behind
them are recorded: the device ends at the frozen analysis, which the EPD report quotes by
its report code (REG-2, #866, #879), with the reference data, panels, repeats and Paraphase
(TRACE-5, #879) and the PGT and NIPT results (#899, #900, #908) frozen in it; REQ-PGT-008
reworded, QA's review per release as the four-eyes control (REG-8) and the X-linked
recessive rule confirmed (CLIN-6), all in #866; large panels matched by position (#897,
#906); OPS-7's risk accepted until after the RC (#892). #880 and #886 kept the required
checks green, #901 fixed a test, and #910 brought TF-08 up to date with Dependabot's updates
(#881, #884, #885, #891, #903). What they left open is listed above.

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
