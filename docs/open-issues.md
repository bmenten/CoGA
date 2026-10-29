# Open issues

Every known open issue, ranked by priority. Status of **2026-10-27** (evening), `main` at
`91f54d0b` (after the fixes #790–#1027). Sources: the open GitHub issues and pull requests, the
follow-ups recorded in the pull requests of the last weeks, the residuals and open items in
`docs/` and the technical file, the codebase audit of 2026-10-08, whose agents checked each
item in the code, and what the fixes of 2026-10-09 to 10-27 found. Line numbers drift:
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
- **P3** — after the RC. Ideas and enhancements that are not defects are in
  [ROADMAP.md](ROADMAP.md).

**Owners:** *Eng* engineering · *Owner* a decision of the device owner · *QA* the quality
manager · *LD* the laboratory director · *Ops* the operator or organisation admin · *DPO*
the data protection officer · *RA* regulatory affairs.

**The repository on that date:** 3 open issues (#1, #364, #518) and no open pull request
besides the one that updates this page; no open Dependabot or code-scanning alert: the owner
dismissed Dependabot #125 and #126 (a stale certifi snapshot) and CodeQL 54 (the package-path
guard's false positive, raised again by #978), and #1003 moved dnspython past
GHSA-45c3-73f7-pv4m, which failed the required `deps` check on every branch the afternoon it
was published; twelve required checks on `main`, which requires no approving review and no
branch to be up to date before it merges; CI green on `main`; no image pushed, nothing
deployed, no release tagged.

## Ranked list: P1 and P2

| Rank | ID | Pri | Issue | Owner |
|---:|---|---|---|---|
| 1 | REG-1 | P1 | Traceability-matrix rows whose status no longer matches the software | QA, Owner |
| 2 | CLIN-57 | P1 | TRGT's counting units are not validated per locus | QA, LD, Eng |
| 3 | REG-13 | P1 | QA's review of the technical file's signed-record corrections (#997) | QA |
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
| 14 | CLIN-68 | P2 | The laboratory's VCFs carry no gnomAD homozygote or hemizygote counts | LD, Owner |
| 15 | CLIN-61 | P2 | The PGT phasing-QC thresholds are provisional | LD, QA |
| 16 | SEC-6 | P2 | The request audit holds clinical content with no retention period | Owner, DPO |
| 17 | REG-12 | P2 | Owner and QA confirmations of the fixes of 2026-10-09 to 10-27 | Owner, QA |
| 18 | CLIN-9 | P2 | QA confirmation of the carrier-screening preset and the chrM exclusion | QA |
| 19 | CLIN-10 | P2 | Parent–embryo IBS0 never checked on real imputed data | Owner |
| 20 | OPS-6 | P2 | Deployment settings: problem-report link, SMTP, the reference-data bucket | Ops |
| 21 | OPS-24 | P2 | Release mechanics untried until Google Cloud is configured | Ops, Owner |
| 22 | REG-10 | P2 | No regression truth set (GIAB / GeT-RM) | Eng, QA |
| 23 | REG-11 | P2 | Post-market, vigilance and security-process inputs | QA, RA, Owner |
| 24 | OPS-9 | P2 | Scale is unproven | Eng, Owner |
| 25 | DOC-7 | P2 | The technical file and the docs lean on pull-request numbers | Eng, QA |
| 26 | ENG-21 | P2 | The test suite has never been audited as a whole | Eng |
| 27 | ENG-22 | P2 | Dead and legacy code left after the fix rounds | Eng, Owner |
| 28 | OPS-27 | P2 | Stale branches and worktrees; no alpha baseline tagged | Eng, Owner |

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
proposes. Since #913 the signed record freezes each call's mark and its locus's
validation state.

### REG-13 · QA's review of the technical file's signed-record corrections (#997)

The owner decided REG-2: CoGA's sign-out is the analyst's analytical sign-off and freezes the
analysis, which the EPD report quotes by its report code. #997 corrected, as drafts for QA, the
statements written while REG-2 was open: TF-06 H4, H5, H6, H9 and §4 item 1, TF-01 §3.3 and §4
condition 7, TF-02 §6, TF-03 §16.1 and TF-15 §5 now say that the signed record holds each
reported variant's description (#879), a PGT family's embryo calls with their phasing QC (#899,
#908) and a monogenic NIPT family's NIPT results (#900), and that the embryo call's QC warns
but never gates. No status symbol, score or decision field changed. **Next:** QA reviews the
corrected text and reconsiders the 🔲 beside H5 (what remains: the provisional thresholds,
CLIN-61, and the browser's second implementation of the risk rules), H6 (only the candidates
tagged for the report are frozen) and both on H9 (the gene and phenotype context), the 🔲 OWNER
item of TF-02 §6 (decided on 2026-10-14) and TF-03 §16.1's ◐; decides whether TF-06 §4's
embryo-QC example belongs under item 2; and sets REQ-TRACE-007's gap text with REG-1. TF-12 U2
and TF-15 §3 items 2 and 7 do not name the newer controls (the phasing-QC warning, *Sign out
NIPT analysis*, the NIPT checks that gate): QA's call.

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
been run, nor has the rollback across a schema migration (#921), which restores the
pre-migration backup over the live database. **Next:** run the drill and that rollback,
once on a throwaway instance, and write a joint restore procedure before go-live.

### REG-3 · The RC steps of TF-18 §3a have not started

Feature freeze, the `-rc.N` tag, a documented risk-based review of the clinical-critical
modules, the bio-IT ingangsvalidatie (H11.1-F12.2), the security go-live, QA's confirmation
of the TF-10 §8 list and QA's first per-release review (REG-8). #894 proposes, for QA, to
freeze the five schema baselines at the RC (no longer re-applied, guarded by a checksum
test), every later schema change a numbered revision; until then the start check of an API
that does not migrate cannot see a baseline-only change, such as a new table (#921).
**Next:** plan them once the P1 items
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

- **CLIN-68 · The laboratory's VCFs carry no gnomAD homozygote or hemizygote counts.** Their
  CSQ holds gnomAD's allele frequencies, the population frequencies and `MAX_AF`, and their
  INFO no count at all (read in a real VCF's header for #992). So every maximum gnomAD count
  filter (the homozygotes, the hemizygotes, the allele count) keeps every variant as absent
  from gnomAD, the *Phenotype priority* preset's limits included, and BS2 gets no suggestion;
  since #992 the variant list says so for each count filter applied. **Next:** the laboratory
  adds gnomAD's counts to the VEP run (`--custom` with `nhomalt` and the hemizygote counts),
  outside the device; the laboratory director decides meanwhile whether the presets should rely
  on the counts.
- **CLIN-61 · The PGT phasing-QC thresholds are provisional.** Since #908, as the owner
  decided on 2026-10-20, an embryo call carries a warning, never a block, when its phasing
  QC at the ROI has fewer than 50 informative sites, a Mendelian error rate above 2 % or
  fewer than 6 markers on a parent's side the call reads (counted over the whole region and
  on imputed data, where ESHRE counts each side of the gene); signed records freeze these
  values. At exactly 2 % the Sample QC warns and the haplotype track does not; the verdict
  follows the track. The laboratory director and QA confirm the values, and whether a small
  ROI should include its flanks, as the warning will fire often there.
  Since #914 a man's one X counts outside the PARs; the validation could include an X-linked
  locus with men's calls written once and written twice (#914, #927).
- **SEC-6 · The request audit holds clinical content with no retention period.**
  `audit_log_events` stores request bodies in an append-only table, and since #893 the
  UI-event log is append-only too, so neither can be cleaned up without a controlled
  procedure; the retention period is still a placeholder in TF-14 (INPUTS E5). The owner and
  the DPO decide retention and masking.
- **REG-12 · Owner and QA confirmations of the fixes of 2026-10-09 to 10-27.** Each fix made
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

  Fixes of 2026-10-20 (the P2 items):
  - #913 (frozen count units): the signed record keeps each locus's QA record (validated,
    reason, the recording administrator, the time), so any later change to it, a validation
    or a withdrawal, reads as "repeat expansions changed since sign-out"; a record signed
    before #913 is not compared on them; a new margin would make every record that holds
    warnings read as changed; a validation recorded at the moment of sign-out is frozen with
    the warnings recomputed, which can differ from what the analyst saw; QA: whether TF-10
    §8 names the frozen warning, and the tests cited under REQ-TRACE-007 too.
  - #914 (one X in the phasing QC): a son's call of two different chrX alleles counts as a
    Mendelian error, and a father's is ignored, so his daughters are not checked there; with
    a donor mother a son has nothing to be checked against and warns on 0 informative sites;
    a daughter gets no paternal marker from her father's one X, so an X-linked dominant call
    from an affected father warns on too few paternal markers; a member without a recorded
    sex counts as two copies; an X-linked locus in the validation of the warning (CLIN-61).
  - #915 (gnomAD counts): the unprefixed `AC_Adj`, `nhomalt` and `hemi` are no longer read
    (the pipeline's names: CLIN-67); VEP's `MAX_AF` still reads as gnomAD's popmax; a
    variant without a gnomAD value passes every maximum gnomAD filter (the laboratory
    director); the tests cited under REQ-PERF-003, or a requirement for the population
    filters.
  - #916 (sign-out dialog): the concerns in four groups (failed, could not run, NIPT failed,
    warnings), the warnings named in the audit event, each failed check named, the 409's
    shorter wording, one reason for every concern listed; whether a sex check that could not
    run stays unlisted when a relationship check confirms the sample.
  - #917 (sign-out numbering): a re-created family continues the deleted family's version
    numbers, which shows how many it had, also to another project's users; its pages show
    only its own versions; the database refuses two records with one family ID and version.
  - #918 (track availability): a track wherever the list shows the member a variant under
    the panel, one linked only by its annotation included; a panel with neither genes nor
    regions for the build gives no member a track; the Mendeliome's 0.12–0.15 s in any view;
    whether REQ-CARR-001's new citations change its ◐.
  - #919 (reference pins): a wrong segmental-duplication or HPO file blocks only its own
    import, a wrong STRchive file still stops the start; the knowledgebase's recorded
    release is ClinGen's file and its SHA-256, and a rebuild that did not record it is not
    loaded; the admin HPO sync and the GRCh37 rebuilds (TRACE-18).
  - #920 (NIPT tag filter): "Show reported" clears every other filter, so it shows exactly
    what a sign-out freezes; a tagged variant the NIPT analysis cannot classify is named
    above the list; the NIPT page offers no classification or saved-notes filter; a
    requirement that the page lists the reported variants before sign-out.
  - #921 (pre-migration backup): a backup before every new backend image, not only a new
    revision; a failed backup, or one not done within an hour, stops the deploy; in owner
    mode without a migration at start, whoever migrates takes the backup; the backups'
    retention (OPS-24); QA: the release record naming each backup; QA and the laboratory
    director: the rollback across a migration, which overwrites the live database and loses
    what was written since.
  - #922 (event loop): a NIPT analysis that fails during a sign-out is frozen as "could not
    be read" rather than retried; the shared analysis is reused only on identical inputs;
    QA: the cache of parsed genotype strings needs no clinical re-validation; a cancelled
    upload or import finishes its batch (at most 1,000 records) first.
  - #926 (deletion marks): a failed sample deletion marks the family for that sample's own
    data; a successful one removes every "may be missing" note about that sample alone;
    deleting one type of data, or all small variants, still leaves no mark when it fails
    part-way; one label for every kind of deletion; an import whose SNV file is refused
    before anything is replaced keeps the old callset but is marked failed; the
    REQ-TRACE-013 and REQ-PERF-003 citations.
  - #927 (X-linked blocks): a daughter's paternal side follows the recorded sex, not her
    genotypes; every family's chrX blocks are cut at the PAR boundaries; existing X-linked
    families need a re-import, after which a signed one shows its PGT section as changed;
    the PGT validation with an X-linked locus, PAR1 sites and an X-linked dominant family.
  - #928 (account log line): the log no longer says which role an account got or whether it
    was activated; the request audit keeps both.

  Fixes of 2026-10-21 to 10-27 (the P3 items; what the owner decided is left out):
  - #944 (mtDNA coverage): *Breadth* counts only the bases at a depth above 0.
  - #945 (in-silico evidence): an ambiguous AlphaMissense class leaves PP3 and BP4 open, as
    a grey-zone REVEL score does, or the laboratory prefers a "consider" note.
  - #946 (missing SV's caller): the SV table shows its *Source* column by default.
  - #950 (pedigree drawing): whether to stop writing the stored PED text
    (`families.pedigree`), now kept only for the row order.
  - #964, #965 and #966 (technical file): QA reviews the edits to TF-02 §8, TF-06, TF-09a,
    TF-09b, TF-09d and TF-12 (U10), confirms REQ-CARR-002's hazards, H1 and H2 in all three
    documents, and reads the 52 new TF-09c rows against their suites.
  - #969 (TF-13 S-7): QA reads the corrected row.
  - #976 (Sample QC sites): QA reads it as a change to a clinical check.
  - #986 (chrX lineage): the TF-10 §8 clause that names chrX in the PGT validation scope.

  Fixes of 2026-10-27 (CLIN-66, CLIN-67, TRACE-18 and the rest of the engineering items):
  - #992 (gnomAD counts): whether the population filters keep their citation under
    REQ-PERF-003, and REQ-UI-003's BS2 sentence.
  - #993 (knowledgebase files): the TF-08 note on the GRCh37 ClinGen pin and the recorded
    files; the REQ-TRACE-005 citation. #994 (HPO sync): the TF-08 HPO row and the REQ-TRACE-001
    citation.
  - #995, #996 (outdated haplotype blocks): the REQ-PGT-005 citations, the TF-09c row and
    whether TF-10 §8's PGT area covers it; a PGT family's phased genotypes imported before #995
    read as outdated until imported again.
  - #998 (files checked first): the per-target coverage tables checked first too; the dry run's
    new warnings. #999 (Paraphase copy numbers): refused like #985's TRGT values, not stored as
    missing.
  - #1000 (mypy gate): the ten floored modules joined the clinical-critical set, the owner's
    decision of 2026-10-27; QA confirms TF-18 §3a's wording and the review scope (REG-8).
  - #1001 (deletions): striking a deleted sample off the import flags lifts a gate for that
    sample's data only; an unlink that fails after the commit is logged, not retried.
  - #1002 (Sample QC, blocks, structure versions): a database error in the NIPT step is a
    listed *could not run* concern, no longer a failed sign-out; zero-length blocks at a
    stretch's end are gone from families imported again; the summary sentence of a re-created
    family.
  - #1004, #1008 (sign-out): the freeze follows what the page shows as reported (a pair
    review's report tag); a monogenic NIPT family's record holds a PGT section only for an
    embryo with haplotype blocks of its own; the acknowledged concerns' fingerprint in the
    audit event only, strict matching, 422 without one, the drift 409's detail an object;
    TF-09a's wording for REQ-TRACE-006, -012 and -013.
  - #1005, #1017 (sign-in keys): a job and a preset show the address and name they were saved
    under; revision 0008 removed presets no account's user name found; a job the previous
    version queued after the migration fails; a job whose requester was deactivated ends
    failed, a running import is not stopped.
  - #1006 (segmental duplications): the GRCh38 alias rule rather than the exact name; a warning
    at every start while the setting names another assembly; rows loaded earlier stay. #1013,
    #1021 (assembly names): T2T-CHM13 v1.0 and v1.1 are no longer read as T2T; IGV opens no
    genome for an assembly it has none for; a patched GRCh37 opens hg19; GRCh37 after T2T in
    the panel list.
  - #1007 (audit rows): a row stored without its user link is not counted as lost; TF-13 S-5's
    wording. #1018, #1025 (UI events): kept in the browser until the same account signs in
    again, the owner's decision of 2026-10-27, now also after a 401; the server keeps the
    browser's event id; idle tabs renew their token hourly; the client half cited under
    REQ-SEC-004.
  - #1009, #1012, #1022 (saved searches): a filter a list does not apply is left out and named,
    never refused; a man's `1|1` on his one X shown as one allele; the small-variant route
    refuses the NIPT list's `de_novo`. #1019 (viewer): the chromosome list reads chr1…chrY and
    chrM.
  - #1010 (HSTS): one policy in the app and the load balancer; QA checks after the first apply
    whether the load balancer's and Cloud Armor's own answers carry it. #1011 (ClickHouse VM):
    the 4 GB cap per query now applies on Google Cloud; create-before-destroy extends to what
    the secret versions depend on.
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
  Before deploying, ops checks with `shasum -a 256` that the bucket's `hpo/hp.obo` and
  `ClinGen_recurrent_CNV_V2.1-hg38.bed` are the repository's files, and after deploying to a
  fresh database looks for the HPO and segmental-duplication refusal lines in the backend
  log (#919).
- **OPS-24 · Release mechanics untried until Google Cloud is configured.** #904's deploy
  reads each image's digest on the assumption that `gcloud builds submit` prints it last
  (the step fails rather than deploy a wrong image), and `release-sbom` needs the workflow
  token's `contents: write`. Since #921 the deploy takes a Cloud SQL backup before each new
  backend image: the deploy account needs the `cogaPreMigrationBackup` role first, or every
  deploy stops at the backup, and so does anyone who runs `terraform apply` by hand; the
  owner sets how long the pre-migration backups are kept and who deletes them. At the first
  configured build ops checks the digests and the `@sha256:` images in the plan, and that
  the "Before the schema migration of coga-backend@sha256:…" backup is listed; at the first
  beta, the SBOMs on the release and the plain version in `/api/version`; GitHub's immutable
  releases must stay off. Found while fixing OPS-8 (#904) and OPS-23 (#921).
- **REG-10 · No regression truth set.** No GIAB or GeT-RM set with a concordance harness,
  which TF-18 §4 needs for every minor change after the RC (INPUTS C6).
- **REG-11 · Post-market, vigilance and security-process inputs.** PMS cadence and
  indicators, FAMHP reportability and the support contact (TF-16, TF-17);
  vulnerability-response targets, a threat model and a base-image SBOM (TF-13); the supported
  browser (TF-15); form H11.1-F2 vs F14 (TF-18); the TF-05 market survey; panel versions
  (TF-01).
- **OPS-9 · Scale is unproven.** No genome-scale import or query benchmark on the real stack
  (every timing so far comes from synthetic data on a laptop); one uvicorn process, with the
  import and refresh workers in the API process (the owner decides when to run the benchmark
  and whether to move the workers into processes of their own); an unfiltered Variant
  Explorer page took 33 to 43 s on a 2-million-variant synthetic cohort, as it aggregates
  every carrier (#841); since #906 the Mendeliome keeps every panel SV of a family with more
  than 50,000 SVs, so the ranked view's limit of 5,000 SVs is reached more often; the
  per-sample upload builds each batch's rows twice (#922). #922 moved the Sample QC and VCF
  parsing off the event loop and runs a NIPT sign-out's analysis once;
  `application-scheme.md` lists what remains under *Performance*.
- **DOC-7 · The technical file and the docs lean on pull-request numbers.** `docs/` holds
  about 1,070 references to GitHub pull requests and issues (counted 2026-10-29): most in
  this page and `testing.md`, and a handful in each technical-file document (TF-12 14,
  TF-09b 13, TF-06 7, TF-14 5, TF-02 5, others 1 to 3), `security-posture.md`,
  `clinical-traceability.md`, `database.md` and the handleiding (chapter 11). A reviewer
  cannot read them without GitHub. The owner decided on 2026-10-29 that the technical file
  and the user and reference docs describe the device as it is, standing on their own: each
  reference is replaced by what it stood for (the behaviour, the decision and its reason, the
  test that verifies it). The design history stays in the git history, which is kept: every
  squash commit carries its pull request's description. **Next:** rewrite the references in
  `docs/regulatory/`, the handleiding and the reference docs (this page and `testing.md` are
  working lists and may keep theirs); add a CI check that refuses a `#123`-style reference in
  `docs/regulatory/`; from the RC, TF-18 names a change by its CR number and commit SHA.
- **ENG-21 · The test suite has never been audited as a whole.** About 6,900 tests in 680
  files on 2026-10-29: some 4,900 in the backend (132,000 lines, of which the e2e harness
  32,000 and the integration tests 18,000) and 2,000 in the frontend (49,000 lines), against
  about 174,000 lines of production code. Every fix round added at least one regression test
  per defect, so tests overlap, and some pin how a screen is built (exact text, class names)
  rather than what it does, and break on every UI change. The largest files are the
  sign-out (`test_report_signout.py`, 4,400 lines), `FamilyReportPage.test.tsx` (4,100),
  `test_clickhouse_family_variants.py`, `test_family_import_compensation.py` and
  `test_loaders_refuse_empty_files.py`. **Next:** an audit that lists, before anything is
  removed, the tests that add no coverage another test does not give, the tests that pin
  implementation details, and the small regression tests that fold into one parametrised
  test; keep the e2e and integration tests that back TF-09c and TF-09d and every test a
  TF-09b row cites; update `docs/testing.md` and the coverage floors with the result.
- **ENG-22 · Dead and legacy code left after the fix rounds.** The cleanup of 2026-10-08
  predates more than 250 merged pull requests. A new pass looks for unused functions,
  components, CSS, routes, settings and scripts; compatibility shims for data formats or
  records that no longer exist (the data are synthetic, so nothing old needs reading); and
  the leftovers ENG-4, ENG-5, ENG-6 and ENG-8 list, which wait on the owner's decisions.
  Verify each candidate in the code before removing it: some code that looks dead is live
  (the 2026-10-08 audit lists those).
- **OPS-27 · Stale branches and worktrees; no alpha baseline tagged.** On 2026-10-29 the
  repository has 27 remote and 29 local branches, 9 worktrees besides the main checkout,
  and one stray tag (`my770`). The owner decided to keep the git history rather than restart
  it from one commit, and to start from a tagged baseline instead. **Next:** remove the
  merged and abandoned branches (remote and local), the worktrees whose work is merged, and
  the stray tag, checking each for unmerged work first; then tag the version that goes into
  testing and validation (for example `v0.1.0-alpha.1`) with a GitHub release, and name that
  tag or its commit in every validation record.

## P3 — after the release candidate

### Data integrity and import

- **SAFE-3 ·** A long-read sample's SNV file out of position order is found only while
  streaming, after an overwrite has deleted the stored callset: the overwrite snapshot restores
  it, so it is lost only if that restore fails too (which flags every dataset of the import and
  keeps the family marked until it is imported again in full) or if no snapshot could be taken.
  Left as it is on purpose: a pre-scan would double the longest import. #998 checks the other
  files first (was SAFE-4 and SAFE-33).
- **DATA-4 ·** A bgzip file cut exactly at a block boundary still reads as complete (#817;
  was SAFE-10): requiring BGZF's end-of-file block would catch it, the owner's call (REG-12,
  under #817). #937 fixed the other download points.
- **DATA-9 ·** Refusal messages after #1014: the `repeats_trgt` family VCF's refusal names the
  samples but not the file, and a per-target coverage table's names the file but not the
  sample.

### Security and audit

- **OPS-7 ·** Integrity anchors, an accepted residual until after the RC (the owner's
  decision of 2026-10-14, TF-13 §3, #892): made only on an admin's request, nothing
  schedules them, `export_anchor()` is an empty hook (so deleting the newest anchors goes
  unnoticed while the API connects as the owner), verification trusts one key, no HSM
  (REQ-TRACE-008 is ◐, its action 🔲). QA approves the residual and decides whether the
  laboratory's procedure sets how often anchors are made; the owner re-evaluates it after
  the RC.
- **SEC-9 ·** Residuals of #785 after #940: a taken family or sample ID answers 409 on
  create and on `POST /ped/manual`, and a non-admin PED upload with overwrite answers 403
  "Only admins can overwrite…", each telling a user that the ID exists elsewhere (documented
  as a residual; the owner accepts or changes both).
- **SEC-10 ·** TF-09a REQ-SEC-001 and TF-13 do not say that a record outside your projects,
  an id that is no UUID and a body id that names nothing all answer like an unknown record
  (#785, #777, #787); TF-13 also omits a query parameter's name from the control-character
  rule. Requirement text is the owner's.
- **SEC-15 ·** The ID rule accepts C1 controls, bidi and zero-width characters, `/` and `..`
  (the owner's call; see SEC-2 and SEC-3; was SEC-22 and SEC-28). On a case-insensitive file
  system (a macOS bind mount) family IDs that differ only in case share one folder, for
  every folder named after an ID (#836); production storage is case-sensitive. #979 encoded
  the IDs in the app's links and in download names.
- **SEC-19 ·** Terraform residuals to accept in TF-13: ClickHouse graceful shutdown is
  best-effort, bucket grants are bucket-wide, secret rotation is manual, and the ClickHouse VM
  must be restarted right after any certificate reissue (#892). Since #1011 a new TLS secret
  version is created before the old one is destroyed; Terraform extends that to what the
  versions depend on, so a later change that replaces one of those under the same name fails on
  the name.
- **SEC-23 ·** HSTS after #1010 and #1015 (the bare 500, the load balancer's HTTPS answers, the
  frontend server's own 502 and 504 now carry it): QA checks after the first apply whether the
  answers the load balancer and Cloud Armor make themselves (a refusal, a rate limit, the load
  balancer's own 502) carry it; the frontend's `/api` fallback, now pointed at the backend,
  still reaches it only through VPC egress (a network change, the owner's call); an exception
  outside the proxy's handler gets Express's default 500, and Node's own 400 for a malformed
  request carries no headers.
- **SEC-27 ·** Package paths and file containment after #978 (was SEC-30 and SEC-31): Write
  manifest checks the link and then writes, so a concurrent swap to a symlink in between
  could still redirect the write (`O_NOFOLLOW` would refuse in-package links too). A
  sub-folder link inside a listed package (`ped/`, a long-read sample folder) is still
  followed: it counts towards listing the package, and Discover proposes a member from such
  a folder (a warning may be better: the owner's call). A recorded signal-track or alignment
  URI is checked against the import area, not the family's folder (scoping it needs every
  source folder recorded at import; only the latest package import's is, in
  `families.metadata.package_import` since #653); `family_qc_reports._stored_report_url` and
  the CRAM remote probe check no path segments (object keys are literal, so a key cannot
  climb).
- **SEC-35 ·** Accounts after #1005 and #1017: a requester demoted from administrator between
  queueing and the run still has the import run (the worker checks project access only,
  `ped_service._resolve_accessible_project_id`; read in the code), and a clinical CNV
  knowledgebase rebuild or a gene-info refresh queued by a since-deactivated administrator
  still runs (those jobs act with no account's rights; they record who asked). The owner
  decides whether either is refused.
- **SEC-33 ·** `GET /api/users` still gives every signed-in user the names and e-mail
  addresses of all active accounts (the accepted staff-roster residual,
  `security-posture.md`), though no screen calls it since #909; removing the route would end
  it (owner).
- **SEC-36 ·** The UI-event log after #1018 and #1025: the page-close keepalive's result is
  never read, so a keepalive that fails is not retried; tabs share the kept store, so
  concurrent writes from two tabs could in principle lose a kept event; events made with no
  session at all (the sign-in page) are dropped when no session is found.

### Clinical behaviour and QC

- **CLIN-12 ·** A relative's lineage stays grey on chrY and chrM, and on chrX of an assembly
  whose PAR bounds CoGA does not have (T2T), where the phasing QC (#914) and the haplotype
  blocks (#927) also read chrX as two copies. It fails safe. Since #986 chrX between the
  PARs is read by each member's recorded sex, for relatives and for the embryos of a
  one-parent (donor) family.
- **CLIN-13 ·** The NIPT per-sample SNV file binds its one column unchecked, on purpose:
  NIPT manifests map verified tubes to files named after other tubes. Guarding it needs the
  owner's decision and `vcf_sample` on remapped entries.
- **CLIN-14 ·** Repeat-catalogue points for QA and the owner after #984 (was CLIN-32,
  CLIN-38 and CLIN-64), which made the track read a call as the table does and a count
  between a row's benign range and its threshold intermediate where the row has no grey zone
  (the owner's decision of 2026-10-27). STRchive's own gaps and overlaps (#806): ATXN7 36
  reads intermediate, PPP2R2B 33–39 normal and 50 intermediate, ATXN10 800–850 pathogenic;
  the built-in and STRchive rows differ in motif orientation at nine loci (it matters only
  where TRGT reports several motifs); whether PABPN1's `GCN` matches the pipeline's TRGT BED
  is unchecked (the BED is not in the repository). The catalogue after #888 and #905:
  STRchive and disease tags (`anticipation`) are registered as TRID aliases (they resolve by
  position, the safe side, but name no locus); moving the pin later leaves a window in which
  a cold start of the old version is refused until the new revision serves (documented);
  STRchive v2.28.0 (2026-10-08) is out, and moving to it is a reviewed change of the file
  and the pin, after the per-locus checks (CLIN-57).
- **CLIN-16 ·** Genotype words outside VCF ("HET", "HETEROZYGOUS") are read three different
  ways by three helpers. Only a malformed VCF stores them: reject them at import (owner).
- **CLIN-17 ·** Neither CNV scorer caps section totals; QA confirms against Riggs et al.
  2020 whether a section maximum applies.
- **CLIN-18 ·** The APCAD glossary entry awaits the laboratory's definition; the Sample QC
  pages say they catch contamination, which they do only indirectly (owner wording).
- **CLIN-39 ·** One SV split across two affected siblings' per-sample records still gets two
  second-hit verdicts, so one sibling's cis does not veto the other's trans (#840). It errs
  towards flagging, and the records are separate calls. (#987, #988 and #989 fixed the
  entry's other points; was CLIN-42, CLIN-49 and CLIN-53.)
- **CLIN-50 ·** Filter parity between the views after #975 (was CLIN-63): the
  carrier-screening SQL prefilter needs a gene symbol, so a variant with only an Ensembl
  gene ID never reaches the couple rule (reading it would cost a whole-index subquery per
  screen); the Variant Explorer matches a variant against any annotation set stored for its
  key, the family page against its own row's (different only when one variant was imported
  with different annotations in different families; which set to read is a product choice).
  By design: the explorer's gene filter reads the annotation index only, where the family
  page also reads the record's own gene symbols (no writer makes the two differ, #857), and
  the SQL rsID check reads the shared annotation index, whose rsID another family's VCF may
  have set.
- **TRACE-15 ·** A VCF's own `MISSENSE_Z` reaches the evidence card only as a raw "gene
  missense z" line, read by no logic: use it, hide it or label it, the owner's call (#945
  fixed the entry's other points).
- **TRACE-17 ·** The notes of the NIPT coverage-over-tracks check are frozen nowhere word
  for word: the check depends on the gene or panel queried, and the record's
  `coverage_tracks` freezes what the tracks hold; whether to freeze the notes too is the
  owner's call (#947 fixed the entry's other points).

### Operations, CI and infrastructure

- **OPS-11 ·** CI gates and workflow edits for the owner (was OPS-13). #1000 cleared the mypy
  errors this entry named and added the ten floored modules to the gate. Left, as workflow
  edits for the owner: the coverage gate (`ci.yml` passes `--cov-fail-under=70` while
  `check-coverage-floor.py` enforces 74 in the same job), an unreachable branch-tag case and
  stale comments in `build.yml` and `ci.yml`, seventeen environment lines repeated across three
  jobs, the coverage job installing the full dev lock, a job that runs
  `./scripts/compile-requirements.sh` and fails on a diff (TF-13 S-7, #969), and CI's
  ClickHouse service, which does not mount the memory guards Compose and the VM read (#1011).
  Outside the gate 179 mypy errors in 43 files remain (`reference_metadata_service`, `main` and
  `metadata_service` the largest).
- **OPS-12 ·** Dependency updates (was OPS-19). Dependabot does not track the CI service
  images (a test, `test_deployment_config.py`, holds them to the Compose images Dependabot
  does track, so each Compose image bump needs its `ci.yml` change by hand),
  `fake-gcs-server`, the Terraform image defaults and providers, the Cloud Build builder
  image (`gcr.io/cloud-builders/docker`, with no tag or digest at all), the `pip-audit` and
  `markdown` pins in the workflows, or the image tags in `scripts/*.sh`; Terraform's own
  version is pinned in CI since #892 (1.16.6), but nothing updates it, and its provider lock
  file is not committed. Dependabot holds back eslint 10 until eslint-plugin-react and
  eslint-plugin-jsx-a11y accept it, and TypeScript from 6.1 until typescript-eslint does
  (`dependabot.yml`, #674).
- **OPS-15 ·** Build configuration after #967 (was OPS-16, OPS-20 and OPS-21): the clinical
  build passes no `VITE_GITHUB_*` values to Cloud Build (`build.yml`, a workflow edit for
  the owner), so the footer's GitHub links fall back to the owner's repository, and the
  footer's *Contact* is the owner's own address (allowed for now; a role mailbox or a build
  variable would fit the roles-not-names rule). Whether the Node in the pinned
  `node:22-alpine` digest meets the new floor of 22.22.2 is unchecked, and with
  `engine-strict` unset the floor only changes npm's warnings.
- **OPS-17 ·** Reference data (was OPS-18): rebuild the clinical CNV knowledgebase for both
  assemblies on each database, the GRCh38 one at least once since #919 and the GRCh37 one since
  #993 pinned its ClinGen file, so its import record, and the records signed after it, name
  every file it read and its SHA-256. A start that cannot reach UCSC leaves GRCh38 without
  cytobands (each later start tries again), and T2T gets one band per chromosome, as UCSC has
  no T2T cytoband table (documented since #968). #1006 loads the shipped segmental-duplication
  file into a GRCh38 assembly only.
- **OPS-22 ·** `main` no longer requires a branch to be up to date before it merges, so two
  pull requests that each pass alone can fail together: #857 and #858 left a unit test
  failing on `main` until #860. A merge queue would test each against the one merged before
  it (owner).
- **OPS-26 ·** The ClickHouse VM's daily certificate refresh runs
  `/etc/coga-clickhouse/refresh-certs.sh` from systemd; if Container-Optimized OS mounts `/etc`
  no-exec, it never runs (the restart after a reissue is not affected). Check at the first
  apply (#1011; not verified).

### Engineering debt

- **ENG-4 ·** API leftovers whose removal or renaming changes the OpenAPI schema (owner
  decision; was SAFE-9 and CLIN-15): response fields never set
  (`GeneHomologOut.percent_id/percent_coverage`) or always the default
  (`ReferenceImportSourceAssemblyOut.cytobands_available/genes_available`); duplicate models
  (`FamilyStructureCoupleUpdate` = `ManualPedCoupleCreate`, `MonarchPhenotypeMatchOut` =
  `PhenotypeTermRefOut`); the CNV route's string-typed `end`;
  `FamilyMemberUpdate.clear_existing_genomic_data`, accepted and ignored. The SV review route's
  request model inherits `acmg` and `compound_het`, which the route ignores (#853). The NIPT
  fields `cat7_transmitted` and `cat8_absent` and the profile summary's "categories 7/8" are
  named after the old paternity logic; the Sample QC page prints that summary and signed
  records store it (`application_summary`), so renaming changes a stored text too (the two
  fields are in no record signed since #749). Responses with no response model, read through
  hand-written types since #1020 and #1027: the BED endpoints' JSON (declared `text/plain`),
  `/shared-structural-variant-counts` and several `dict` answers.
- **ENG-5 ·** Routes no screen calls (kept, or dropped by the owner):
  `GET /assemblies/{species_id}`, `GET /families/{id}/hpo/query`,
  `PUT /families/{id}/hpo/{annotation_id}`, `GET /hpo/{hpo_id}`,
  `DELETE /families/{id}/small-variant-filter-presets/{preset_id}`,
  `DELETE /families/{id}/structural-variant-filter-presets/{preset_id}`, the BED endpoints'
  text format. Admin, traceability and ops routes without a screen stay by the owner's
  choice. (Screens for the SV preset delete and the phenotype `PUT` are PROD-3 and PROD-8 in
  ROADMAP.md.)
- **ENG-6 ·** Schema and stored-key leftovers (owner decision; was SAFE-7): Postgres columns
  never read or written (`auth_login_attempts.last_success_at`,
  `small_variant_reviews.compound_het_partner_variant_keys`); ClickHouse columns always
  empty or constant and never read (`liftedOverChrom/Pos`, `sample_type`) or written and
  never read (`annotation_index.transcript_ids`, and its `sift_terms` and `polyphen_terms`,
  from CLIN-52). `structural_variant_reviews.variant_key` is never written (#853), nor is
  the SV table's `calls.gq` (#868). `families.metadata.unresolved_roi` is written and never
  read, and never cleared when a later import resolves the ROI. The QC-threshold `reason` is
  nullable only for edits made before it was required; making it `NOT NULL` is a schema and
  API change (from SAFE-12).
  The classification tags ("Likely Pathogenic - class 4") and VUS tiers keep their capitals,
  as stored review labels: sentence case for them needs a migration (#982).
- **ENG-8 ·** Duplication left for a later refactor (was SEC-18): the audit and UI-event
  queue workers, the bounded download helper (Monarch, gene-info), several VCF INFO/FORMAT
  parsers with different flag handling, the interval-track tuple, small exact-copy helpers
  in the mito, repeat and Paraphase services, and parameters no caller sets. #963 fixed the
  gene-info keys, the migration-era 503 and the second list of masked keys.
- **TRACE-10 ·** Segments BEDs after #948 and #949 (with UI-3's last point): a segments BED
  declares nothing of what it holds (#898, a choice REG-12 lists), so the chromosome view
  and the genome overview draw segments only inside a source's log2-ratio coverage track,
  and a segments-only `web` upload is not drawn (#846); the BED endpoints' segments and
  APCAD reads without `source` still return every caller's rows (not averaged, as coverage
  was before #949).
- **ENG-19 ·** ClickHouse's analyzer plans the panel track-availability query slowly in small
  windows (#918); a member is offered a small-variant track where the list's other filters
  leave it nothing to draw (never the reverse: by design, it errs towards offering). #1002 and
  #1009 fixed the entry's other points.
- **ENG-20 ·** Viewer leftovers after #1023 and #1026: the genome overview's haplotype track
  looks a block up under its stored chromosome name (latent: the blocks are nuclear); the SV
  tooltip writes the stored name (`M:…`), not `formatChromosomeLabel` (#602), which a test
  pins; chrM stays on the genome overview though it is about 0.006 px wide (the Circos plot
  leaves it out: the owner's call); three test files still cast partial objects to the variant
  types.

### Product gaps and enhancements

Moved to [ROADMAP.md](ROADMAP.md#product) on 2026-10-20, as ideas rather than defects:
PROD-1 to PROD-9 and ENG-9 under *Product*, SEC-16 under *Engineering*.

## Closed since the last review

Fixed on 2026-10-27, the P2 items CLIN-66 (#995, #996), CLIN-67 (#992) and TRACE-18 (#993,
#994), and the P3 items SAFE-35 (#1001), DATA-7 (#999), SEC-17 (#1007), SEC-32 (#1005),
TRACE-19 (#1004, #1008), OPS-14 (#1011) and ENG-7 (#1019, #1020, #1027; the eight admin alias
routes stay as the owner's bookmarks), with parts of SAFE-3 (#998), OPS-11 (#1000), OPS-17
(#1006), SEC-19 (#1011), SEC-23 (#1010, #1015), ENG-19 (#1002, #1009) and REG-13 (#997, the
corrections for QA). They fixed what they found too: a deactivated account's queued import
(#1017); the UI events an expired token, a 401 or an unload lost (#1018, #1025); T2T-CHM13 read
under each of its names and IGV opening hg38 for an unknown assembly (#1013); GRCh38 and GRCh37
read under any name in the gene page's links and the assembly order (#1021); a saved search's
filters across the NIPT and small-variant pages (#1012, #1022); a one-allele call in the
haplotype tooltip (#1012); chrM in the ideograms, the repeat and SV tracks and a stuck pan
(#1023, #1026); the file a refused BED or TRGT file is named by (#1014); the seven append-only
tables and #985's citations (#1016); and the Paraphase sentence (#1024). #1003 moved dnspython
past GHSA-45c3-73f7-pv4m. The owner's decisions behind them: the ten floored modules joined the
mypy gate and so QA's per-release review (#1000); the UI events an expired token would lose are
kept in the browser until the same account signs in again (#1018). What they left open is
listed above.

Fixed on 2026-10-21 to 10-27, the P3 items: SAFE-12 (#930), SEC-11 (#928, #929, #931),
SEC-12 (#932), SEC-14 (#933), SAFE-5 (#934), SAFE-6 (#935), SAFE-34 (#936), UI-5 (#942),
CLIN-21 (#943), CLIN-56 (#944), TRACE-12 (#946), SAFE-8 (#950), SAFE-18 (#954, #955, #956,
#958), ENG-12 (#960), ENG-13 (#961), ENG-17 (#962), DOC-1 (#964), DOC-2 (#965), DOC-3
(#966), OPS-25 (#969), ENG-16 (#970), DATA-6 (#971), TRACE-8 (#972), CLIN-52 (#973),
TRACE-16 (#974, #977), CLIN-11 (#976), ENG-10 (#980, #981, #982) and SAFE-15 (#983), with
parts of DATA-4 (#937), DATA-7 (#938, #985), SEC-32 (#939), SEC-9 (#940), SEC-23 (#941),
TRACE-15 (#945), TRACE-17 (#947), UI-3 (#948; its last point is now in TRACE-10), TRACE-10
(#949), ENG-7 (#951), ENG-8 (#963), OPS-15 (#967), OPS-17 (#968), CLIN-50 (#975), SEC-27
(#978), SEC-15 (#979), CLIN-14 (#984), SAFE-3 (#955, #985), CLIN-12 (#986) and CLIN-39
(#987, #988, #989). The owner's decisions behind them are recorded: a HiFiCNV VCF without
calls counts as imported and replaces the sample's earlier calls (#955); TF-13 S-7 corrected
rather than the lock compiled again in Docker (#969); the Sample QC samples SNVs only and
warns when its sites cover fewer than 20 autosomes, though versions signed before then read
their QC as changed (#976); an administrator's family deletion asks a reason (#977); where a
catalogue row has no grey zone, a count between its benign range and its threshold reads
intermediate (#984); a dry run reports an empty data file as a warning (#985); a symbolic
deletion, duplication, inversion or CNV that states neither END nor SVLEN is left out and
counted (#989). Left as they are by design: SAFE-18's two points on the safe side
(`imported_scopes` never shrinks; a glimpse2 `snv` and a `haplotypes` dataset never complete
each other) and ENG-17's limit (a write still running when a search ends is not seen; its
entry marks the family). Stored gene records keep the old *Dosage sensitivity* count until
the next gene-reference refresh (#981). #957 updated body-parser and zod, and Dependabot's
#952, #953 and #959 merged on 2026-10-27; #990 brings TF-08 up to date with them. What they
left open is listed above.

Fixed on 2026-10-20, the P2 items: SAFE-31 and SAFE-32 (#926), CLIN-58 (#916), CLIN-59
(#915), CLIN-60 (#914; #927 reads a man's one X in the haplotype blocks too), CLIN-62
(#918), TRACE-13 (#917), TRACE-14 (#919), UI-4 (#920) and OPS-23 (#921). #913 froze each
repeat call's near-boundary mark and its locus's validation state (CLIN-57), #922 moved the
Sample QC and VCF parsing off the event loop and runs a NIPT sign-out's analysis once
(OPS-9), #928 and #929 closed CodeQL alerts 51 and 52, and #929 moved certifi past
PYSEC-2026-4320, as #925 did follow-redirects past two advisories. Families imported before
#915 keep the callset's count as their gnomAD AC until they are imported again (the data are
synthetic). What they left open is listed above.

Closed in the P3 triage of 2026-10-20: no P3 entry was fixed whole. Checking each against
the code at `be5e09a7`, and against #913 to #919 since, found these parts fixed, now
dropped: TRACE-10's HiFiCNV coverage import stopped between its rows and its record (#898
reads such a track's content as unknown and draws it on its own range), CLIN-49's signed
report that showed an SV only by its id (#879 freezes each reported SV's description),
CLIN-15's fields in the signed snapshot (stored by no record since #749), PROD-4's joint VCF
for a multi-sample long-read family (per-sample SNV files since #763), ENG-11's login form,
already in sentence case, and all but one of CLIN-24's points about the sign-out QC prompt
(#916, CLIN-58's fix: the dialog lists each concern once, under its heading, and its message
no longer calls a lone sex check an asserted pedigree relationship). The triage also merged
duplicates into the entry with the lowest ID, which names the IDs it took in ("was …"),
bundled the wording, layout, documentation and configuration points into ENG-10, DOC-3 and
OPS-15, and moved the product ideas to ROADMAP.md: the 113 P3 entries are now 64.

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
