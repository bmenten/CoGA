# Open issues

What still has to be done: P1 blocks the release candidate (RC), P2 is due before the RC, P3 after it.
Status 2026-11-04, `origin/main` at `490b13f5`. Ideas that are not defects are in [ROADMAP.md](ROADMAP.md).

| ID | Pri | What has to happen | Who | Blocks |
|---|---|---|---|---|
| REG-1 | P1 | Bring the TF-09b statuses and gap texts in line with the software | QA, owner | RC |
| CLIN-57 | P1 | Validate TRGT's repeat count per catalogue locus; correct FMR1, HTT and POLG | QA, lab director, developer | RC |
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
| CLIN-69 | P2 | Variant Explorer: show the most severe classification (`_most_severe` ranks the stored labels 0, so the first review read wins) | developer | RC |
| DATA-10 | P2 | Warn when a manifest's ROI does not resolve (`_apply_manifest_roi` silently keeps the earlier ROI, which the PGT calls read) | developer | RC |
| UI-7 | P2 | Ask for confirmation before deleting a gene panel or a project (whose delete cascades to every family's link to it) | developer | RC |
| CLIN-70 | P2 | Enter each QC profile's sequencing-QC cut-offs: all ship empty, so nothing is judged (TF-06 H14) | lab director, QA | go-live |
| CLIN-68 | P2 | Add gnomAD's hom, hemi and AC counts to the lab's VEP run; meanwhile decide whether presets rely on them | lab director, owner | validation |
| CLIN-61 | P2 | Confirm or change the provisional PGT phasing-QC thresholds and window | lab director, QA | validation |
| SEC-6 | P2 | Set retention and masking of the request audit and UI-event log (TF-14, INPUTS E5) | owner, DPO | go-live |
| REG-12 | P2 | Confirm or reverse the choices the fix PRs left for the owner or QA | QA, owner | RC |
| REG-14 | P2 | Decide which browser journeys back a TF-09d claim | owner, QA | RC |
| CLIN-9 | P2 | Confirm the carrier-screening preset and the chrM exclusion (#763) | QA | RC |
| CLIN-10 | P2 | Check parent–embryo IBS0 on one real imputed PGT family's Sample QC page | owner | validation |
| CLIN-18 | P2 | Fill in what APCAD stands for (`user-guide/glossary.md`) | lab director | RC |
| OPS-15 | P2 | Fix the clinical build's GitHub links; check the Node image meets the engines floor | owner, developer | RC |
| OPS-6 | P2 | Set the problem-report link, SMTP and the reference-data bucket | ops | go-live |
| OPS-24 | P2 | Try the release mechanics at the first configured build | ops, owner | go-live |
| REG-10 | P2 | Build a GIAB / GeT-RM truth set with a concordance harness (INPUTS C6) | developer, QA | RC |
| REG-11 | P2 | Supply the post-market, vigilance and security-process inputs | QA, RA, owner | go-live |
| OPS-9 | P2 | Prove CoGA at genome scale on the real stack | developer, owner | |
| DOC-9 | P2 | Correct the documentation errors found on 2026-11-04 | developer | |
| DOC-7 | P2 | Replace PR numbers in the technical file and docs by what they stand for | developer, QA | RC |
| ENG-22 | P2 | Remove dead and legacy code, verifying each candidate first | developer | |
| OPS-27 | P2 | Delete branch `feat/family-pgs`; tag the alpha baseline with a GitHub release; name it in every validation record | owner | validation |
| OPS-7 | P3 | Approve the integrity-anchor residual (TF-13 §3) and set how often anchors are made | QA, owner | |
| OPS-22 | P3 | Keep PRs that pass alone from failing together: a merge queue needs an organisation-owned repository | owner | |
| SEC-19 | P3 | Accept in TF-13: best-effort ClickHouse shutdown, bucket-wide grants, VM restart after a certificate reissue | QA | |
| SEC-23 | P3 | Close the HSTS gaps | QA, owner, developer | |
| SEC-27 | P3 | Close the package-path residuals | developer | |
| SEC-15 | P3 | Write the IDs of `ped_service`'s "Father/Mother id not found" through `visible()` | developer | |
| SAFE-36 | P3 | Gene-reference refresh: heartbeat while loading bulk sources; end a failed job only if still the worker's | developer | |
| DATA-11 | P3 | Refuse a corrupt per-sample `.vcf.gz` in Discover and the validation, which read its columns unguarded | developer | |
| CLIN-12 | P3 | Add T2T-CHM13's PAR bounds from a verified source (fails safe now) | developer | |
| CLIN-14 | P3 | Settle the repeat catalogue's open points; move to STRchive v2.28.0 | QA, owner, developer | |
| CLIN-17 | P3 | Confirm against Riggs et al. 2020 whether CNV scoring caps section totals | QA | |
| CLIN-39 | P3 | Decide whether one SV in two siblings' per-sample records gets one second-hit verdict | owner | |
| CLIN-50 | P3 | Decide whether carrier screening reads Ensembl-only genes, and which annotation set the Explorer matches | owner | |
| TRACE-10 | P3 | Decide how a segments-only BED is drawn and what a segments or APCAD read without `source` returns | owner, developer | |
| OPS-11 | P3 | Pin `click` so the lock check compares the header; clear the mypy errors outside the gate | developer | |
| OPS-12 | P3 | Bring what Dependabot does not track under updates | developer, owner | |
| OPS-17 | P3 | Rebuild the clinical CNV knowledgebase for both assemblies on each database | ops | |
| OPS-26 | P3 | At the first apply, check the ClickHouse VM's certificate refresh runs (`/etc` may be no-exec) | ops | |
| ENG-4 | P3 | Remove the API leftovers | developer | |
| ENG-6 | P3 | Put classification tags and VUS tiers in sentence case, with a migration (decided 2026-10-28) | developer | |
| ENG-8 | P3 | Fold the duplicated helpers (queue workers, download helper, VCF parsers, service copies) | developer | |
| ENG-20 | P3 | Name chromosomes as the viewer does in the Circos SV tooltip and on the ROI markers page | developer | |

## Waiting for a decision

Also tracked in issue [#518](https://github.com/bmenten/CoGA/issues/518).

- **Owner:** REG-1 (wording, REQ-TRACE-007), OPS-2, OPS-3 (separate validation environment?),
  OPS-22, SEC-6 (with the DPO), REG-14, CLIN-10, CLIN-39, CLIN-50, CLIN-68, TRACE-10, SEC-23
  (`/api` fallback), OPS-15 (role mailbox), OPS-24 (pre-migration backup retention).
- **QA:** REG-1, REG-5, REG-8, REG-12, REG-13, REG-14, CLIN-9, CLIN-14, CLIN-17, CLIN-57, CLIN-61,
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
- QA and the lab director compare the count with the clinical number on reference samples near each boundary, and record each locus on the *Repeat Catalogue* page (until then CoGA warns near a boundary).
- Developer: add a per-locus count correction (none exists) for FMR1, HTT and POLG.
- QA words the TF-09a requirement and a TF-06 hazard with the next free number (H17 and H18 are taken).

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
- Required reviewers on the `gcp-deploy` environment; TF-13 S-4 (download audit). See `deployment-gcp.md` §12.7–12.12.

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
- A point that asks only for a TF-10 §8 phrase lapses where a TF-10 §8 area covers it.
- Approve the drafts merged for it: TF-09a REQ-SEC-001 and TF-13 §2 (#1037); the APCAD definition and contamination wording (#1038).
- Done when: one QA record marks each choice confirmed or to change.

### REG-14 · Journeys TF-09d does not list

- `frontend/e2e/` holds `findingReview.spec.ts` and `ancestry.spec.ts` beside TF-09d §3's four. Listing them widens the claim; the ancestry journey checks a phone viewport, which TF-09d puts out of scope.

### OPS-15 · Build configuration

- `build.yml` passes no `VITE_GITHUB_*`, so the clinical build's GitHub links fall back to the owner's repository.
- Check that the pinned `node:22-alpine` meets the `>=22.22.2` floor.

### OPS-6 · Deployment settings

- Set `COGA_PROBLEM_REPORT_URL` (no problem-report link without it, TF-15) and `SMTP_HOST` beside `ADMIN_EMAIL`.
- Fill the reference-data bucket: the shipped STRchive file (else the backend does not start), the ACMG SF list then a full gene-reference sync, the vendored `hp.obo`; check `hp.obo` and the ClinGen BED with `shasum -a 256`.
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

### DOC-9 · Documentation errors found on 2026-11-04

- `clinical-traceability.md` *Known limitations*: one embryo-QC measure now gates; markers count per flank; a NIPT record without a per-target table holds `coverage_notes`.
- Handleiding: ch. 6 (non-admins see no import jobs: every route is admin-only); ch. 11's snapshot table (add `repeat_expansion_reviews`, `paraphase_reviews`, `unreviewed_findings`, the acknowledgement fields); ch. 15 (a local knowledgebase file is recorded by name and SHA-256); the ancestry PCA (ch. 3's tables, a section).
- `database.md` (admin deletes also write ClickHouse); `security-posture.md` (the summary's input bounds are described nowhere); `haplotype-segregation-analysis.md` (`PHASE_SWITCH_MIN_CHILD_MARKERS` is in `haplotype_block_builder.py`; the lineage fingerprint also holds sexes, phase corrections, the blocks' digest, the ROI); `snv-sv-compound-het.md` (`_same_sv` needs the same chromosome); `scripts/README.md` (GRCh37 is pinned); `monogenic-nipt.md` and the user guide's *Clinical report* (sign-out is on the NIPT report); `repeat-paraphase-review-design.md` §4.2 (`assembly_id` cascades); `docs/README.md` ("every document"); TF-08 A.1 (`--require-hashes` is not passed: pip's hash mode applies).
- User guide: describe the Annotations *Quick* choice.

### DOC-7 · PR numbers in the docs

- About 90 in `docs/regulatory/`, 9 in the handleiding, 80 in the other docs (this page and `testing.md` may keep theirs): replace each by the behaviour, decision or test it stood for; a CI check refuses `#123` in `docs/regulatory/`.

### ENG-22 · Dead and legacy code

- Unused code, CSS, routes, settings, scripts and shims for data that no longer exist. Found: the JSON log formatter's `requestBody` branch (`coga_logging.py`), which no record reaches.

## P3

### SEC-23 · HSTS gaps

- After the first apply, QA checks the load balancer's and Cloud Armor's own answers (refusal, rate limit, 502).
- The frontend's `/api` fallback reaches the backend only through VPC egress (owner).
- Express's default 500 and Node's own 400 carry no headers.

### SEC-27 · Package paths

- *Write manifest* checks the link, then writes: a symlink swapped in between could redirect the write.
- A recorded track or alignment URI is checked against the import area, not the family's folder.

### CLIN-14 · Repeat catalogue

- STRchive's gaps and overlaps: ATXN7 36 intermediate, PPP2R2B 33–39 normal and 50 intermediate, ATXN10 800–850 pathogenic.
- Motif orientation differs at nine loci; PABPN1's `GCN` is unchecked against the pipeline's TRGT BED; STRchive tags as TRID aliases name no locus. Move to v2.28.0 after CLIN-57.

### OPS-12 · Not tracked by Dependabot

- CI service images, `fake-gcs-server`, workflow pins (`pip-audit`, `markdown`), `scripts/*.sh` image tags, Terraform (version, providers; no lock file committed), the Cloud Build builder (no tag or digest).

### ENG-4 · API leftovers

- `SmallVariantReviewUpdate.cnv_acmg` is accepted and ignored.
- `GeneHomologOut.ensembl_gene_id` is never set, `in_platform` always false; no screen shows homologs.
- `SampleIntegrityPaternityCheckOut` holds one count twice (`hom_alt_absent_sites`, `hom_alt_not_transmitted`).
