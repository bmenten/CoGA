# Open issues

What still has to be done: P1 blocks the release candidate (RC), P2 is due before the RC, P3 after it.
Status 2026-11-10, `origin/main` at `d82d8ac1`. Ideas that are not defects are in [ROADMAP.md](ROADMAP.md).

| ID | Pri | What has to happen | Who | Blocks |
|---|---|---|---|---|
| REG-1 | P1 | Bring the TF-09b statuses and gap texts in line with the software | QA, owner | RC |
| CLIN-57 | P1 | Validate TRGT's repeat count per catalogue locus; enter the count corrections of FMR1, HTT and POLG | QA, lab director | RC |
| REG-13 | P1 | Review the technical file's signed-record corrections (#997) | QA | RC |
| OPS-2 | P1 | Make the CI `images` job a required check; configure Google Cloud so images are pushed | owner, ops | RC |
| OPS-3 | P1 | Apply Terraform; set the deploy trigger to `release` | ops, owner | RC |
| OPS-4 | P1 | Turn on the go-live switches, one change-controlled deployment each | ops | go-live |
| OPS-5 | P1 | Run a restore drill and the rollback across a migration; write a joint Postgres + ClickHouse restore procedure | ops, developer | go-live |
| REG-3 | P1 | Plan and run the RC steps of TF-18 §3a | owner, QA | RC |
| REG-4 | P1 | Complete and sign the technical file (#518) | owner, QA, RA | RC |
| REG-5 | P1 | Complete and sign the risk file | lab director, QA | RC |
| REG-6 | P1 | Fill TF-10's comparators, acceptance criteria and sample sizes (INPUTS C1–C7) | owner, QA | validation |
| REG-7 | P1 | Run the usability evaluation, file the DPIA, settle the national provisions | owner, DPO, RA, QA | go-live |
| REG-8 | P1 | Confirm QA's per-release review; record the first before the RC tag | QA | RC |
| CLIN-70 | P2 | Enter each QC profile's sequencing-QC cut-offs: all ship empty, so nothing is judged (TF-06 H14) | lab director, QA | go-live |
| CLIN-68 | P2 | Add gnomAD's hom, hemi and AC counts to the lab's VEP run; meanwhile decide whether presets rely on them | lab director, owner | validation |
| CLIN-61 | P2 | Confirm or change the provisional PGT phasing-QC thresholds and window | lab director, QA | validation |
| SEC-6 | P2 | Set retention and masking of the request audit and UI-event log (TF-14, INPUTS E5) | owner, DPO | go-live |
| REG-12 | P2 | Confirm or reverse the choices the fix PRs left for the owner or QA | QA, owner | RC |
| REG-14 | P2 | Decide which browser journeys back a TF-09d claim | owner, QA | RC |
| REG-16 | P2 | Add to TF-10 §8 the clinical behaviour built since 2026-11-03 that no area covers; validate the two Major filter changes with it | developer, QA | RC |
| REG-15 | P2 | Decide whether the variant summary's figures belong in the validation scope: no rule reads them, but its Mendelian table can point to UPD | QA | RC |
| CLIN-9 | P2 | Confirm the carrier-screening preset and the chrM exclusion (#763) | QA | RC |
| CLIN-10 | P2 | Check parent–embryo IBS0 on one real imputed PGT family's Sample QC page | owner | validation |
| CLIN-18 | P2 | Fill in what APCAD stands for (`user-guide/glossary.md`) | lab director | RC |
| OPS-15 | P2 | Fix the clinical build's GitHub links (`build.yml` passes no `VITE_GITHUB_*`) | owner | RC |
| OPS-6 | P2 | Set the problem-report link, SMTP and the reference-data bucket | ops | go-live |
| OPS-24 | P2 | Try the release mechanics at the first configured build | ops, owner | go-live |
| REG-10 | P2 | Build a GIAB / GeT-RM truth set with a concordance harness (INPUTS C6) | developer, QA | RC |
| REG-11 | P2 | Supply the post-market, vigilance and security-process inputs | QA, RA, owner | go-live |
| OPS-9 | P2 | Prove CoGA at genome scale on the real stack | developer, owner | |
| OPS-27 | P2 | Delete branch `feat/family-pgs`; tag the alpha baseline with a GitHub release; name it in every validation record | owner | validation |
| OPS-7 | P3 | Approve the integrity-anchor residual (TF-13 §3) and set how often anchors are made | QA, owner | |
| OPS-22 | P3 | Keep PRs that pass alone from failing together: a merge queue needs an organisation-owned repository | owner | |
| SEC-19 | P3 | Accept in TF-13: best-effort ClickHouse shutdown, bucket-wide grants, VM restart after a certificate reissue | QA | |
| SEC-23 | P3 | Close the HSTS gaps | QA, owner, developer | |
| SEC-40 | P3 | Keep an error's text out of the last stored job errors, HTTP answers and log lines | developer | |
| UI-8 | P3 | Mark a held tag of a project the family left apart from a deleted one; show a detached tag's label in the Explorer | owner, developer | |
| DATA-13 | P3 | Read every package VCF whole at validation: Discover answers 500 on a damaged SNV family VCF, and other VCFs pass unread | developer | |
| CLIN-14 | P3 | Settle the repeat catalogue's open points; move to STRchive v2.28.0 | QA, owner, developer | |
| CLIN-17 | P3 | Confirm against Riggs et al. 2020 whether CNV scoring caps section totals | QA | |
| CLIN-39 | P3 | Decide whether one SV in two siblings' per-sample records gets one second-hit verdict | owner | |
| CLIN-50 | P3 | Decide whether carrier screening reads Ensembl-only genes | owner | |
| CLIN-73 | P3 | Decide whether CoGA splits a multi-allelic record: filters read its alleles together, and a `Number=A` value reads as none | owner, developer | |
| CLIN-74 | P3 | Decide whether the family search lists a variant that only taken-out members carry | owner, developer | |
| CLIN-78 | P3 | Decide which class an Explorer row shows under a class filter: the most severe across families, or the one that matched | owner | |
| CLIN-72 | P3 | Decide whether every reader keeps a VCF's INFO flags: uploads drop them, package imports keep them | owner, developer | |
| TRACE-10 | P3 | Decide how a segments-only BED is drawn and what a segments or APCAD read without `source` returns | owner, developer | |
| TRACE-21 | P3 | Decide whether a family that loses a project link gets a clinical audit event | owner | |
| OPS-12 | P3 | Finish what Dependabot cannot update: the workflow pins, a Terraform lock file, the Cloud Build builder | owner | |
| OPS-17 | P3 | Rebuild the clinical CNV knowledgebase for both assemblies on each database built before its sources failed closed; a start builds it only where an assembly has none | ops | |
| OPS-26 | P3 | At the first apply, check the ClickHouse VM's certificate refresh runs (`/etc` may be no-exec) | ops | |
| ENG-24 | P3 | Make the end-to-end modules independent of their order: the review-audit module leaves a variant tagged for the report | developer | |
| ENG-25 | P3 | Settle the copies that differ: two ROI region parsers, two Mendelian-consistency rules, two `_coerce_int`; drop the unused tag `family_id` column | developer | |

## Waiting for a decision

Also tracked in issue [#518](https://github.com/bmenten/CoGA/issues/518).

- **Owner:** REG-1 (wording, REQ-TRACE-007), OPS-2, OPS-3 (separate validation environment?),
  OPS-12 (builder, Terraform lock), OPS-22, SEC-6 (with the DPO), REG-14, CLIN-10,
  CLIN-39, CLIN-50, CLIN-68, CLIN-72, CLIN-73, CLIN-74, CLIN-78, UI-8, TRACE-10, TRACE-21, SEC-23 (`/api` fallback), OPS-15
  (role mailbox), OPS-24 (pre-migration backup retention).
- **QA:** REG-1, REG-5, REG-8, REG-12, REG-13, REG-14, REG-15, REG-16, CLIN-9, CLIN-14, CLIN-17, CLIN-57, CLIN-61,
  CLIN-70, OPS-7, SEC-19; with RA, REG-11.
- **Lab director:** REG-5, CLIN-18, CLIN-57, CLIN-61, CLIN-68, CLIN-70.

## P1

### REG-1 · TF-09b rows whose status no longer matches the software

- QA confirms REQ-QC-005 ✅ and REQ-CARR-003 ✅ (the printed NIPT report names only the local panel version).
- QA sets REQ-PGT-008's status (⚠ since the owner reworded it: tracks shown, no aneuploidy detector).
- The owner rewords REQ-PGT-007, REQ-PGT-009 and TF-05's "simultaneous aneuploidy" as REQ-PGT-008 was.
- REQ-TRACE-007: the owner answers its 🔲 (freeze frequencies, predictions, gene context too?); QA confirms the gap text.

### CLIN-57 · TRGT's counting units are not validated per locus

- TRGT counts its catalogue region: FMR1 reads 1–3 repeats low, HTT one high, POLG four high (10 reads 14).
- QA and the lab director compare the count with the clinical number on reference samples near each boundary, and record each locus on the *Repeat Catalogue* page: validated or not, and its count correction, which CoGA adds to TRGT's count before it classifies. None is entered; until a locus is validated CoGA warns near a boundary.
- FMR1's difference varies (1–3), so a constant correction is wrong there: it stays warned.
- QA words the TF-09a requirement and a TF-06 hazard for the correction with the next free number (H17 and H18 are taken), and decides whether the backend refuses a correction at FMR1 and whether ±20 is the right bound.

### REG-13 · QA review of the signed-record corrections (#997)

- Review TF-06 H4–H6, H9, §4 item 1; TF-01 §3.3, §4 condition 7; TF-02 §6; TF-03 §16.1; TF-15 §5.
- Reconsider the 🔲 at H5, H6, H9, TF-03 §16.1's ◐ and TF-02 §6's 🔲 OWNER (decided 2026-10-14). TF-02 §6 still signs a NIPT family out from the NIPT page: it is the NIPT report.
- Decide whether TF-06 §4's embryo-QC example belongs under item 2, and whether TF-12 U2 and TF-15 §3 items 2 and 7 name the newer controls.

### OPS-3 · Terraform never applied

- No GitHub environment, secret or variable exists: bootstrap per `deployment-gcp.md` §5–7.
- Set `COGA_DEPLOY_TRIGGER` to `release` before clinical use (unset, every push to `main` deploys); decide on a validation environment apart from production (TF-09).

### OPS-4 · The go-live switches are off

- Terraform: `db_runtime_role = "coga_app"`, `cloud_armor_waf_enforce`, `allowed_ingress_cidrs`, `clickhouse_restrict_egress`, `storage_backend`, `alert_notification_emails`.
- Microsoft sign-in: IT registers the app, single-tenant, limited to the lab's group; go live with `azure_admin_override` off.
- Required reviewers on the `gcp-deploy` environment; TF-13 S-4 (download audit). See `deployment-gcp.md` §12.7–12.11.

### REG-3 · RC steps (TF-18 §3a)

- Feature freeze, the `-rc.N` tag, a risk-based review of the clinical-critical modules, the bio-IT ingangsvalidatie (H11.1-F12.2), the security go-live (OPS-4), QA's confirmation of the TF-10 §8 list, the first per-release review (REG-8).
- QA decides #894's proposal: freeze the schema baselines at the RC; every later change a numbered revision.

### REG-4 · Technical-file sign-off (#518)

- Fill the Owner and Approver fields with roles, not names (the callout in `regulatory/README.md` §4 still offers names).
- Assign the `Sxxxx` identifier; name TF-04's declaring legal person and signatory; set TF-07's role titles.
- QA files dated copies of the BELAC certificate.

### REG-5 · Risk-file confirmations

- QA sets the severity matrix (TF-06 §3).
- Assign hazards to REQ-DIAG-004, REQ-DIAG-005 and REQ-DATA-003 (none in TF-09a).
- Consider hazards for an EPD report citing the wrong signed version, for taking CoGA's sign-out for the medical sign-off, and for CLIN-57's count units.
- Add H17 and H18 to TF-06 §6a's triad rollup. The lab director confirms the residuals and signs.

### REG-7 · Usability, DPIA, national provisions

- TF-12: the summative usability evaluation.
- TF-14: the signed DPIA, the DPO's opinion, the processing agreement, the processor and pseudonymisation questions.
- TF-01 (INPUTS A6–A7): the FAMHP provisions; where the public declaration is published.

### REG-8 · QA's per-release review

- QA confirms who performs it, its scope (frontend clinical logic outside the mypy list?) and the departure from SOP H11.1-OP5's per-change approval (TF-18 §6).
- Align TF-09 §1 ("an administrator can bypass" the checks) with TF-18 §6: no bypass actors, but an administrator can edit or disable the ruleset.

## P2

### CLIN-61 · Provisional PGT phasing-QC thresholds

- Warning: under 50 informative sites, Mendelian errors above 2 %, or under 6 markers on a flank, over the ROI ± 1 Mb.
- Gate: an embryo more than 10 % off one homolog, over at least 20 markers, is uninformative.
- At exactly 2 % the Sample QC warns and the haplotype track does not.
- Validate with an X-linked locus, trisomies of the ROI chromosome and mosaics.

### REG-12 · Owner and QA confirmations of the fix rounds

- The choices of #790–#1027 are listed by PR under REG-12 in this file's previous version (`git show dbdf9ffc:docs/open-issues.md`); later fix PRs name theirs in their description (e.g. #1041, #1045, #1077, #1101).
- The 2026-11-04 round (#1112–#1138):
    - The five decision dates and roles written into the technical file in place of PR numbers (#1113).
    - `security-posture.md` says the device owner confirmed the sign-up password policy; the change that set it says the lab director.
    - Class labels in sentence case: migration 0013 without a clinical audit event, a sign-out check blind to a label's case, saves that store an old-case label in the new case (owner's decisions, #1123).
    - An unresolved manifest ROI clears the family's (owner's decision, #1126).
    - Review saves refuse a field they do not take (owner's decision for the SV save, #1119).
    - An in-package `manifest.yaml` link is replaced, not written through (#1122).
    - A Monarch download over the size cap answers 502, not 500 (#1133).
    - The count correction: always applied once recorded; the boundary warning lifted only by validation (#1135).
    - T2T-CHM13's PARs: a man is now hemizygous outside them on T2T (#1136; T2T is outside the validated scope).
- 2026-11-05 to 2026-11-09:
    - The package file `coga-package.json`'s choices, to confirm with the pipeline owners ([coga-package.md](coga-package.md)): MD5 accepted beside SHA-256, a minimal file accepted (CoGA proposes the members and datasets), `request_id` kept, a pipeline QC `fail` needs attention, an unknown field refused.
    - A review's classification text is audited as stored, trimmed but case-sensitive (#1199).
- 2026-11-10:
    - Raw files are read only in the latest import's package folder and CoGA's upload folder, as IGV reads them (the owner's decision): the files an earlier package of the same family recorded show as not read (#1204).
    - An error of a class CoGA defines keeps its text where an import or a gene source stores its failure (`own_error_message`); any other is named by its kind. The sign-out tamper alarm's log field is now `family_uuid` (#1213). A refused raw-file path is logged by its kind (the owner's decision, #1215).
    - The import-job service refuses a user below superuser itself, as its routes already did (#1206).
    - A stored bare class word ("Pathogenic") shows as its label in the Explorer, and a class tag outranks a class label in the text (#1205).
    - A deleted project's tags are kept inactive and detached; a family lists the tags its reviews hold (the owner's decision, #1210). A held tag of a project the family only left reads "(deleted)" (UI-8).
- A point that asks only for a TF-10 §8 phrase lapses where a TF-10 §8 area covers it.
- Approve the drafts merged for it: TF-09a REQ-SEC-001 and TF-13 §2 (#1037); the APCAD definition and contamination wording (#1038).
- Done when: one QA record marks each choice confirmed or to change.

### REG-14 · Journeys TF-09d does not list

- `frontend/e2e/` holds `findingReview.spec.ts` and `ancestry.spec.ts` beside TF-09d §3's four. Listing them widens the claim; the ancestry journey checks a phone viewport, which TF-09d puts out of scope.

### REG-16 · Validation scope of the work since 2026-11-03

- TF-10 §8 lists the clinical behaviour the first validation must exercise; a pre-release change adds a line only where no area covers it. None was added for:
    - one definition of the small-variant filters, the family search's SQL: the ranked, paired and carrier-screening views keep what the plain list keeps, and the phase-set and PASS-only filters read only the family's members' calls (both Major, by the owner's decision of 2026-11-06; each asked for a clinical follow-up validation before the RC);
    - the PASS-only filter: every member's call that carries the ALT allele passed (PASS, `.` or none), read from its own FILTER in a callset of one file per sample and from the record's in a multi-sample VCF;
    - the Variant Explorer: each family's rows read through the family search's filters, counting only the members the family shows;
    - the annotation read since 2026-11-06: ClinVar from the annotated ClinVar file before VEP's cache, a multi-allelic record's annotation tied to its ALT, a field listing several values read as none, VEP's `MAX_AF` kept apart from gnomAD's popmax, and the owner's ACMG rules of 2026-11-06 (no criterion applied on a missing value; BA1/BS1 on gnomAD's FAF95; PP5/BP6 always to consider; PVS1 to consider on a LOFTEE flag or an NMD-escaping variant; the mtDNA frequencies);
    - imports from a watched folder: the package file and its checksums, the manifest made from it, the approval queue, the manifest kept in CoGA, and an import refused or undone when its package changed;
    - a new instance's references: HPO's newest release, recorded with its SHA-256, and Monarch's newest release, recorded in the annotation manifest.
- The review-classification filters now keep a variant by the class its review shows (Major; the owner signed off its before/after table on 2026-11-10, #1214). That change added its own TF-10 §8 line; its follow-up validation joins this list.
- The developer drafts the lines, after checking each against the code; QA confirms them with the list at the RC (REG-3).

### REG-15 · The variant summary's figures (PROD-1)

- The *Variant summary* counts each kind of variant data a family holds, per member: for small variants the genotypes, types, Ti/Tv, consequences and call quality, and for each child with both parents the Mendelian inconsistencies per autosome and callset. No filter, classification or sign-out reads them, so TF-10 §8 has no line for them.
- The Mendelian table names which parent's allele is missing. Many such inconsistencies on one chromosome can point to uniparental disomy or a deletion; the page says CoGA does not call UPD.
- QA decides whether TF-10 §8 lists the summary, and whether TF-09a needs a requirement or TF-06 a hazard for a count read as a UPD finding.
- Done when: QA records the decision.

### OPS-6 · Deployment settings

- Set `COGA_PROBLEM_REPORT_URL` (no problem-report link without it, TF-15) and `SMTP_HOST` (with `SMTP_FROM`, and `SMTP_STARTTLS`/`SMTP_USERNAME`/`SMTP_PASSWORD` when the relay needs an account), then send the test mail from *Users*.
- Fill the reference-data bucket: the shipped STRchive file (else the backend does not start), the ACMG SF list then a full gene-reference sync, the vendored `hp.obo` (the backup when HPO's newest release cannot be downloaded); check `hp.obo` and the ClinGen BED with `shasum -a 256`.
- On a fresh database, look for the HPO and segmental-duplication refusal lines in the backend log.

### OPS-24 · Release mechanics

- Grant the deploy account `cogaPreMigrationBackup` first, or every deploy stops at the backup.
- First build: check the digests, the `@sha256:` images and the pre-migration backup. First beta: the release's SBOMs (`release-sbom` needs `contents: write`) and `/api/version`. Keep immutable releases off.

### REG-11 · Post-market, vigilance and security inputs

- PMS cadence and indicators, FAMHP reportability, the support contact (TF-16, TF-17).
- TF-17 §5 item 5: who corrects the EPD report when an amendment in CoGA changes a released result.
- Vulnerability-response targets, threat model, base-image SBOM (TF-13); supported browser (TF-15); form H11.1-F2 or F14 (TF-18); TF-05 market survey; panel versions (TF-01).

### OPS-9 · Scale

- Benchmark import and queries on the real stack; every timing so far is synthetic data on a laptop (`clickhouse-scale-benchmark.md`).
- Terraform: a Cloud Run import-worker service, as Compose has; a ClickHouse disk and VM above the 200 GB and `e2-standard-4` defaults.
- The benchmark's "What would scale better": the cohort-wide annotation scans, the Explorer's unfiltered page (fails from about 20 M rows), a finer `entries` partition.
- The variant summary's small-variant statistics are counted on each visit: about 6 s, and up to 2 GiB for one query, for a synthetic trio of five million variants on a laptop. Time them on a real whole-genome family.

## P3

### SEC-23 · HSTS gaps

- After the first apply, QA checks the load balancer's and Cloud Armor's own answers (refusal, rate limit, 502).
- The frontend's `/api` fallback reaches the backend only through VPC egress (owner).
- Express's default 500 and Node's own 400 carry no headers.

### SEC-40 · Error text still stored, answered or logged

- SEC-39 kept an error's text out of the import's and the gene sources' records; the same pattern remains in:
    - stored job errors: `dgv_load_jobs.py` (~678, ~681), `clinical_cnv_kb_jobs.py` (~677), `hpo_service.py` (~1646);
    - CoGA error classes that wrap another error's text, which `own_error_message` would keep: `monarch_ingest.py` (~114), `monarch_semsim.py` (~113), `hpo_release_download.py` (~151, ~177), `hpo_service.py` (~682), `reference_source_service.py` (~696), `postgres_migrations.py` (~166, ~212);
    - HTTP answers that quote an exception's text: `routers/admin.py` (~943, ~945, ~1012, ~1018), `routers/families.py` (~298), `routers/auth.py` (~627), and `family_package_discovery.py` (~2303, a YAML error quoting the manifest);
    - log lines with an exception's text: `dependencies.py` (~130), `routers/auth.py` (~372, ~626), `account_notifications.py` (~205), `monarch_ingest.py` (~124, ~532), `monarch_semsim.py` (~112), `hpo_service.py` (~759, ~825, ~847), `reference_metadata_service.py` (~640), `reference_source_service.py` (~691).

### UI-8 · Held tags and detached tags

- A family lists, inactive, the tags its own reviews hold. Every inactive tag reads "Label (deleted)" (`frontend/src/lib/reviewTags.ts`), which is exact for a deleted project's tag and not for a tag of a project the family only left, still active there. A marker of its own needs a flag in the tag list and the interface (owner).
- The cross-family Variant Explorer shows a non-superuser a bare key for a deleted project's tag that was not shared with their projects (`routers/variant_explorer.py` ~96); the family's own list shows its label.

### CLIN-14 · Repeat catalogue

- STRchive's gaps and overlaps: ATXN7 36 intermediate, PPP2R2B 33–39 normal and 50 intermediate, ATXN10 800–850 pathogenic.
- Motif orientation differs at nine loci; PABPN1's `GCN` is unchecked against the pipeline's TRGT BED; STRchive tags as TRID aliases name no locus. Move to v2.28.0 after CLIN-57.

### CLIN-72 · INFO flags

- The small-variant upload and the per-sample callsets drop an INFO flag (an item without `=`); the family-package readers keep it as `true` (`vcf_fields.parse_info`'s `flags`). So an INFO-level `CANONICAL` or `MANE_SELECT` sets the canonical or MANE transcript in a package import, not in an upload. Keeping flags everywhere changes stored annotations: a PR of its own.

### OPS-12 · What Dependabot cannot update

- Workflow edits: install `ci/requirements-*.txt` in `ci.yml` and `security.yml`; drop the smoke job's own fake-gcs `docker run`; move `build.yml`'s `terraform_version` with each Terraform bump. CI's Postgres and ClickHouse service images still follow compose by hand. Until the workflows match, Dependabot's `/ci` PRs fail `test_pinned_images.py`, which names the file to move.
- Commit `.terraform.lock.hcl`, generated with Terraform for linux_amd64 and darwin_arm64 (remove its `.gitignore` line).
- `gcr.io/cloud-builders/docker` is frozen at Docker 20.10.24: move the Cloud Build configs to `docker:<version>-cli` by digest.

### DATA-13 · Package files that pass validation unread

- Discover on a package whose SNV family VCF is damaged answers 500: `family_package_discovery.py::_vcf_meta_lines` catches `OSError`, `EOFError` and `UnicodeError`, not `zlib.error`.
- The validation still accepts without a word a SNV, NeedlR, TRGT or mito VCF (family or per sample) it cannot read (`family_package_validation.py::_warn_file_without_a_record`), and the per-sample SNV check does not check a BGZF file's end, so one cut in its records passes. The APCAD fix's `read_whole_vcf_sample_columns` reads them as the import does.
- An APCAD per-sample `.csv.gz` or `.bed.gz` table is not read at validation (`_check_table_names_sample` reads only `.csv`, and passes a read error).

### CLIN-73 · Multi-allelic records

- A multi-allelic record (ALT `G,T`) is stored as one variant. Each annotation entry names its ALT, but the filters read the record's entries together: one allele's value keeps the record. A `Number=A` INFO value differs per allele, so it reads as no value.
- `data-import.md` asks the pipeline to split such records (`bcftools norm -m -any`). The owner decides whether that stays a pipeline requirement, checked at import, or CoGA splits them; either is a change of its own.

### CLIN-78 · An Explorer row's class under a class filter

- The Explorer's class filter keeps a variant when any family's review shows the class, but its row shows the most severe class across families: a filter on class 3 can list a row shown as class 5. This was so before the filters followed the shown class; the user guide and handleiding chapter 14 now say so. The owner decides whether the row shows the class that matched.

### CLIN-74 · Variants of taken-out members

- A structure edit that takes a member out keeps their calls in the family's rows unless it clears them. The Variant Explorer counts only the members a family shows (the owner's decision of 2026-11-06), but the family page still lists a variant only taken-out members carry, with 0/0 for each member it shows.
- The family search's sample filter, given a taken-out member's name through the API, reads that member's calls; the interface offers only members.
- The owner decides whether the family search leaves such variants out, as the Explorer does: that changes what the search keeps (Major).

### ENG-24 · End-to-end modules depend on their order

- The end-to-end suite run on a database the integration suite has just used fails in `clickhouse_integrity`, the Explorer's counts and panel match, the family panel-region join, the ranking cache and ancestry; each passes on fresh containers, as CI runs it. Integration tests leave rows behind that these modules read.
- Run on their own, `test_e2e_review_audit.py` then `test_e2e_signout_acknowledges_what_was_shown.py` fail the latter's `test_the_module_leaves_nothing_tagged_for_the_report`: the review-audit module leaves a variant of the shared family tagged for the report, and the sign-out module checks that the family's report list ends empty. The full suite, with other modules run in between, passes. Each module should leave the family as it found it, or check only its own tags.

### ENG-25 · Copies that differ

- The region of interest is parsed twice: `family_service._parse_region_of_interest` uses `GENOMIC_REGION_PATTERN` and answers 400 on negative coordinates; `family_package_registration._ROI_REGION_PATTERN` is a pattern of its own.
- One Mendelian-consistency rule is written twice: `sample_integrity_qc._mendelian_consistent` and `phased_marker_service._is_mendelian_consistent`.
- `clickhouse_variant_records._coerce_int` and `family_package_common._coerce_int` read `b"1.5"` differently (1 against none), and both differ from `vcf_fields.field_int` on a bool: decide one reading before folding them.
- `small_variant_tag_definitions.family_id` is a column no code writes (`03_assay.sql`): drop it with a revision.
