# 11. Report & full traceability

This chapter brings everything together. A case is *signed out* into a **frozen, versioned and hashed snapshot** of the report, bound to the software, annotation and reference versions that made it. That is the **analytical sign-out**, by a user with the role `supervisor` or higher: it closes the analysis and freezes it. The medical sign-off does not happen in CoGA: the clinical report is drafted and medically signed in the EPD (the hospital's electronic patient record) and refers 1:1 to the frozen analysis.

It covers the **gates** that must be met first, the **append-only, hash-chained** audit trail and the external **integrity anchors**. The common thread is the full chain: from the raw file with its hash, via the annotation manifest and the variant, to the signed analysis that the clinical report in the EPD refers to.

What the user sees on the report page (the colours of the sign-out record, the messages when a request fails) is in the app: *Docs → Report traceability & sign-out* (`frontend/src/content/docs/clinical-traceability.md`). The design is in `docs/clinical-traceability.md`, the risks and requirements in the technical file (`TF-06`, `TF-09b`).

A few terms:

- **Snapshot:** a frozen JSON object that records what applied at that moment.
- **Hash:** a short, fixed fingerprint (SHA-256) of data. If one character changes, the hash changes completely; that is how tampering becomes *visible*.
- **Append-only:** only add, never change or delete. The database enforces this with a *trigger* (database code that runs before every change and can refuse it).

## The report

There are two report pages:

- **`FamilyReportPage.tsx`** — the family report. The live report shows the variants with the review tag **`report`** (small and structural variants), with for each reported gene the gene profile and the family's HPO terms. Repeat loci and Paraphase regions with **`report`** are in the section *Reported repeat expansions and Paraphase findings* (`ReportedFindings.tsx`, from the tables of the repeat and Paraphase page). The report requests up to 10,000 reported variants per kind, the API's maximum. If a list is incomplete (more reported variants than that page holds, or a capped search), it says so on screen and on the printout (`reportedListCompleteness.ts`). The signed snapshot reads the reported reviews directly from Postgres and has no such limit. The report's sentences are built by `reportNarrative.ts`, tested separately. `SignedFamilyReport.tsx` renders a signed version from the frozen snapshot (see below).
- **`FamilyNiptReportPage.tsx`** — the report for the monogenic NIPT of one pregnancy: the fetal fraction, the quality checks (fetal sex, paternity, the plasma sample), the coverage, the candidate variants and, for a multiple pregnancy, the note about it (chapter 8).

**The family is signed out, a PGT family with its embryo calls and a NIPT family with its NIPT results** (the owner's decisions of 2026-10-14). For a PGT family, signing out the family report freezes the embryo calls (`pgt_analysis`): the same object that the backend derives from the stored haplotype blocks and the family page shows (chapter 8; see *The embryo calls* below).

A monogenic NIPT family uses the same sign-out:

- It is signed out from the NIPT report (**Sign out NIPT analysis**) or the family report, not from the NIPT page itself; if the family holds other analyses too, only from the NIPT report, which signs out its pregnancy's monogenic NIPT (see *One sign-out per analysis*). There is one signed record per analysis, with the same gates, version and report code.
- Its record also contains the section `nipt` (`signed_record_nipt.py`, see below): its pregnancy's NIPT analysis as the NIPT page showed it, computed by the backend.
- The sample-QC checks stay in `sample_qc`: paternity, the fetal sex from the father's X alleles, the category pattern of the cfDNA and the sex of the parents.
- A reported NIPT variant is in `reported_variants` and `variant_descriptions`, like every reported small variant, and in `nipt` with its category, reads and probabilities.
- The NIPT report names the latest signed version with its report code, and says whether the data still match it.
- It shares the button, the gate dialogs (`reportSignOut.tsx`) and the rule that says when the button is not offered (`signOutRefusal`) with the family report. Anyone who may not sign out sees the button disabled on both pages, with the reason (see *Who may sign out*).

A family with ccnv output (conventional NIPT) uses the same sign-out:

- It is signed out from the family report, which shows the live cNIPT result above the reported findings (*Conventional NIPT result*).
- Its record also contains the section `cnipt` (`signed_record_cnipt.py`): the result per maternal plasma as the cNIPT page shows it (for each target whether the validated claim covers it, `claimed`), with the bounds it applied and the inputs it read.

What each variant section contains is described in `docs/report-template.md`. Exporting is done with the browser's print function.

**Both pages carry a disclaimer:** the report is decision support that a qualified clinical scientist must confirm. The NIPT report also asks for confirmation with an invasive diagnostic test.

**Both pages carry the warnings of every family page in their header**, on the printout too:

- *Not validated for clinical use* for a family outside the validated scope (gate 0);
- *Import incomplete* as long as an import or a single-file upload left the family partly loaded or did not finish (gate 3);
- *Package folder changed after import* (or *Package folder gone*), one per analysis, as long as the package folder that analysis's data were imported from has changed since the import or is gone (gate 3b); *Source files archived* if a superuser confirmed that missing folder as deliberately archived.

The NIPT report is a live report that is printed without a gate; with incomplete data the printout says so itself. Like the live family report, it says that it is not the signed version: on screen next to the latest signed version, and at the top of every printout (*Draft …*, *Not the signed report …*, *Not verified …*; `liveReportSignedState` and `liveReportSignedNotice` in `signedReportRecord.ts`, for both pages).

**Both pages end with a footer:** when the report was made and with which build (*Software: CoGA x.y.z (commit)*, from `GET /api/version`). A build that is not a release gets `dev` added to its version, e.g. *CoGA 0.1.0-dev (commit)*. Then follows the label: in-house IVD under IVDR Article 5(5), not CE-marked, for internal use at CMGG only, and the manufacturer.

- The report requests the build every time it opens and shows no stored value: after an update the footer never names the previous build.
- If the request fails, the footer says so, and a printout starts with the message that it is incomplete.
- A signed version names two builds: *Signed with*, the build that froze it, and *Rendered by*, the build that drew the page from the record.
- The same version and the same label are in the footer of every page of the app.

**Where in the code:** `useReportBuild` in `frontend/src/lib/appVersion.ts`, `frontend/src/pages/families/ReportSoftwareIdentity.tsx`, and the text of the label in `frontend/src/lib/deviceLabel.ts`.

Besides the variants, the family report shows three provenance elements, each with its own endpoint in `backend/app/routers/families_reports.py`:

| Element | Shows | Endpoint |
| --- | --- | --- |
| Provenance footer | The annotation and reference versions (the assembly with the reference FASTA that the package names, VEP, ClinVar, gnomAD, GENCODE, Monarch, HPO, …), with deviations per modality | `GET /families/{id}/annotation-manifest` |
| Drift notice | Classifications (small variants, SVs and CNVs) whose evidence changed since they were made | `GET /families/{id}/classification-drift` |
| Clinical audit trail | Who did what and when, with the value before and after: classifying or tagging, assessing a compound heterozygous pair, changing the pedigree, the family members or a phenotype, replacing the annotation manifest | `GET /families/{id}/clinical-audit` (not for a viewer or a reader, chapter 2) |

The versions and the drift status are frozen at sign-out. The audit trail is not in the snapshot: it is itself append-only.

**The audit trail comes in pages.** `GET /families/{id}/clinical-audit` returns the most recent entries first, in one fixed order: `created_at`, then `id` (the order of the hash chain, reversed).

- 200 entries per page by default, at most 500 (`limit`).
- `before` is the id of the last entry of the previous page; then come the entries written before it. An entry added in the meantime shifts nothing: no entry comes twice or drops out.
- A `before` that names no entry of that family's trail gets `404`.
- The response says how many entries the trail holds (`total`, read in the same query as the page) and whether there are older ones (`has_more`).

The report shows the trail under *Clinical audit trail*. If it shows only part of it, it says so at the trail (*Showing the 200 most recent of 450 events …*) and at the top of every printed page (*Incomplete — the audit trail lists …*). **Show older events** reads the next page.

**Where in the code:** `list_clinical_audit` in `backend/app/services/clinical_audit_service.py`; the trail's pages in `frontend/src/pages/families/clinicalAuditTrail.ts` and `FamilyReportPage.tsx`.

## Signing out

**What signing out means.** *Sign out* closes the analysis and freezes it into a signed version. The clinical report in the EPD refers 1:1 to one signed version, with its **report code** (see *The report code*). CoGA does not see that report and does not check that reference (`TF-02` §3). In the app the terms are *Sign out report*, *signed version* and *signed report*. The record itself says what it is, in one sentence that is frozen with it (`record_statement`); the live report says it too.

Signing out goes through **`POST /api/families/{id}/report/sign-out`**. The service `sign_out_report` first loads the family through its checkpoint: a family outside the user's projects answers `404`, a viewer or reader gets `403` because they only read (chapter 2). Then it requires the role `supervisor` or higher (`require_sign_out_authority`): an analyst gets `403`, before any gate, and nothing is written. After that, in order:

0. **Gate 0 — the reference is validated.** If the family's assembly is not in `VALIDATED_ASSEMBLIES` (by default only `GRCh38`), or there is none, the backend refuses (`409`, `gate = "assembly_scope"`). That refusal cannot be acknowledged or bypassed. The pages show such a family as *Not validated for clinical use*, and the report offers no sign-out button (`TF-06`, hazard H12).
1. **Gate 0b — the family is not being written to** (below). This cannot be acknowledged either.
2. **Building the snapshot:** the family context, the annotation manifest, the drift check, the sample QC, the sequencing QC with the limits used, the import status, all reported reviews with their frozen evidence, and what the analysis looked at (`signed_record_sections.py`; each section in *The frozen snapshot*). Then the import jobs are checked once more (gate 0b).
3. **Gate 1 — drift** (below).
4. **Gate 2 — sample QC** (below).
5. **Gate 3 — incomplete import** and **gate 3b — a package folder that changed after the import** (below).
6. **Gate 4 — abnormal repeat or Paraphase calls without a review** (below).
7. **Recording the version and the chain** under a lock per family, so that two simultaneous sign-outs do not interfere with each other.
8. **Computing the hashes and the report code** under one lock for all sign-outs (`report-code`). If another record already has that code, the record is signed again, a microsecond later; that changes the content hash and so the code. Then the new row is **appended** to `report_signouts` as the next version.
9. **Writing a clinical audit event** (`sign_out`), with the report code, in the same transaction.

**Acknowledging.** The signer acknowledges the findings of gates 1, 2, 3, 3b and 4 with a reason (without a reason `422`). The acknowledgement and the reason are frozen in the snapshot, and so in the hash, and included in the audit event.

**Who may sign out.** A user with the role `supervisor`, `superuser` or `admin` (the owner's decision of 2026-11-04). A supervisor signs out in the projects in which they are an analyst; a superuser and an administrator in every project. A viewer, a reader in the project and an analyst get `403`, an analyst with *Signing out needs the supervisor role: ask a supervisor of this family's project to sign out the analysis.* (`SIGN_OUT_ROLE_DETAIL`, chapter 2). The family the API returns says so in advance (`can_sign_out`), and both report pages then disable *Sign out* with that reason. That makes hazard H15 (`TF-06`: an analysis signed out by someone without sign-out authority) a control in the software. Who gets the role `supervisor` is the laboratory's decision: an administrator gives it only to those the laboratory authorises to close an analysis. The medical sign-off falls under the EPD's signing rights. The signer is recorded as a reference to the account and as text, so that they remain recognisable if the account disappears.

**Changes.** Every sign-out is a new version; a signed version is never changed. In the live report the button is then called *Amend sign-out*. A clinical report that relies on the amended analysis refers to the new version.

**Where in the code:** `backend/app/services/report_signout_service.py` (`sign_out_report`, `build_report_snapshot`); the role in `require_sign_out_authority` and `user_can_sign_out_metadata_projects` (`backend/app/services/access_control.py`).

### One sign-out per analysis

A family can hold several analyses (`families.metadata.analyses`), each imported from a package of its own: the monogenic and the conventional NIPT of each pregnancy of a woman, or short-read and then long-read WGS of the same DNA. Each analysis has a key: its type, and for a NIPT the pregnancy too (`monogenic_nipt:ZW1`, chapter 6). Each analysis is signed out on its own (the owner's decisions of 2026-11-19 and 2026-11-21): its record freezes and checks that analysis's data only.

- **The request names the analysis** (`analysis_type` in `ReportSignoutRequest`, with the analysis's key). A family with two or more analyses must name one: without it `422`, and `422` for an analysis the family does not hold. A family with one analysis or none need name none: its record names its only analysis, or none, and covers the whole family.
- **One series of versions per family**, whichever analysis a version closes; the report code is made in the same way. Each snapshot names its analysis (`analysis_type`), and the content hash covers it.
- **What belongs to an analysis.** Each package record (`families.metadata.package_imports`, chapter 6) lists the storage sources its import wrote, per kind of data (`sources`: `small_variants`, `structural_variants`, `tracks`, `repeats`, `paraphase`), and its datasets. An item belongs to analysis X if one of its sources is X's own or is claimed by no other analysis of the family. Data from an upload, from an analyst's addition or from a package that named no analysis therefore belong to every analysis. A package record that is not in the shape the import writes stops the sign-out (`PackageRecordShapeError`): CoGA does not guess which data are the analysis's.
- **What the sign-out of X freezes and checks:**
    - the reported small variants and SVs, their drift and their descriptions: the variants X's reviews tag for the report, per variant X's own review, else the shared one (chapter 10); another analysis's review is not X's;
    - the coverage tracks: by the source of the track, in the same way;
    - the repeat calls and the Paraphase results, with their reviews, their drift and gate 4: if the sources they are stored under (`repeat_expansions.source`; Paraphase's one source) are X's, or no other analysis's. Otherwise both sections hold no call, as in the record of a family without such calls, and the signed view says so of the analysis (*The short-read WGS had no repeat-expansion calls.*);
    - the section of an application: `nipt` only for the monogenic NIPT, `cnipt` only for the conventional NIPT, each of X's own pregnancy, `pgt_analysis` for PGT or the monogenic NIPT. A section of an application none of whose analyses the family holds goes to every analysis;
    - the sample QC: that of X's own application (the conventional NIPT runs no check), on genotypes from a callset of its own;
    - `sequencing_qc`: the sequencing QC that X's import recorded, and that of a package that named no analysis, each assessed against its analysis's limits;
    - `package_intake` and `package_source_changes`: X's own record, and that of a package that named no analysis;
    - `import_incomplete` and `import_unfinished`: the markers of X's imports and those of no analysis (each marker names the analysis of the import that set it, `analysis_type`); an unfinished restore of an `overwrite` stays whole, whichever analysis it imported.
- **The modules:** X's annotation manifest (chapter 6): its own versions, never another analysis's.
- **Whole:** the gene panels and the reference data (for the genes the record describes).
- **The gates and the check.** The gates read this snapshot, so each gate is the analysis's own. The check (`sign-out-check?analysis_type=…`) compares X's latest record with X's snapshot as it would be frozen now, and skips a section that neither holds: a change to another analysis's data leaves X's check matching. Whoever signs out X as the family's only analysis and then imports another analysis therefore sees X's check matching: its versions and its repeat and Paraphase sections stay its own.
- **Where it is signed out.** The NIPT report signs out the monogenic NIPT of the pregnancy it shows, the family report every other analysis (the conventional NIPT of each pregnancy too), with a choice (*Analysis to sign out*) when several remain. The button names the analysis (*Sign out the conventional NIPT of pregnancy ZW1*, *Amend sign-out of the conventional NIPT of pregnancy ZW1*). The family report lists each analysis (*Sign-out of each analysis*): the latest signed version with its report code, signer and date, and whether the family's data still match it. The live pages show the family's current data whole; the sign-out freezes the analysis's part of it. When the content matches, the family report says which part (*The short-read WGS data on this page still match signed version 2, which holds the short-read WGS data only: …*), and the clinical audit event of a sign-out names its analysis (*Report of the short-read WGS signed out (v3, …)*).

**Where in the code:** `backend/app/services/signout_scope.py`; `backend/app/services/analysis_reviews.py`; `resolve_signout_analysis` and `family_signout_scope` in `report_signout_service.py`; `package_sources` in `family_package_records.py`; `AnalysisSignouts.tsx` and `familyAnalyses.ts` in the frontend.

### Gate 0b — the family is not being written to

A package import writes a family over several minutes: first the pedigree and the samples, then dataset by dataset, with a commit after each step (chapter 6). The flag `import_incomplete` only comes when the import fails. A snapshot read during the import can therefore freeze a half-imported family as complete (`TF-06`, hazard H16). That is why the backend refuses at once (`409`, cannot be acknowledged):

- as long as an import job (not a dry run) for the family is `queued` (with the family in its request), `validating` or `running`: `gate = "import_in_progress"`, with the job's id and status.
    - A job whose worker stopped keeps refusing until a worker takes it over. If the import had already started writing, that worker ends the job as interrupted, and the family stays marked (gate 3).
    - If the import was still alive and only its heartbeat (its periodic sign of life) could not be written, that heartbeat stops the import as soon as it reaches the database again (chapter 6). Until then the import's write locks refuse the sign-out (next point), as long as their connection holds them;
- as long as a writer holds the write locks of the family's variants (an import, an upload, a deletion by an admin; `family_variant_write_lock.py`, chapter 6): `gate = "variant_writes_in_progress"`.

Whoever passes the gate shares those locks until the end of the transaction (`pg_try_advisory_xact_lock_shared`): sign-outs do not hold each other up, but a writer of the variants waits until the sign-out is done. The locks are taken before the lock of the sign-out chain, just as every writer takes them first.

After the snapshot, the sign-out checks the import jobs again. An import that was picked up in the meantime sets its job to `running`, with the family, before it writes anything of that family; that job then refuses the sign-out. If recording this fails (a database error, or another worker took over the job), the import stops without writing anything of the family. The job then ends as `failed` with the reason, as long as it still belongs to this worker and can be updated.

The report page shows both refusals as *Not signed out.*, without a dialog.

**Where in the code:** `_refuse_while_family_is_written` and `_refuse_if_import_started` in `report_signout_service.py`; `try_share_family_variant_writes` in `family_variant_write_lock.py`.

### Gate 1 — no unexplained drift

Drift means that the evidence behind a classification changed since it was made. The check compares with the current data and names, each time, what shifted (chapter 10):

- every ACMG classification of a small variant in the family with frozen evidence (reported or not): the frozen hash of the annotation set, the ClinVar value, the consequence and LoF call, the frequencies and in-silico scores and, for a missense variant, the gene's missense Z and its source (the gene profile or the VCF);
- every CNV classification of an SV or CNV: the frozen evidence (the genes, the pLI, the inheritance, the type and the position), against the row of the same caller as it now stands in the data.

A missing hash or unreadable evidence counts as `unknown`; a reported classification without frozen evidence, of a small variant or an SV, as `no_snapshot`. Both count as drift.

If there is drift and the signer has not acknowledged it, `409` follows (`gate = "evidence_drift"`); the message says how many of them are SVs or CNVs. The `409` lists every counted classification as the snapshot freezes it: those of the small variants in `drifted`, those of the SVs and CNVs in `structural_drifted`. `classified_by` and `classified_at` are not in it, because a note or tag on the review changes them. The dialog shows them as the signed version does. One acknowledgement covers both kinds.

**Repeat and Paraphase reviews.** A review of a repeat locus or a Paraphase region with a classification or the tag *Report* freezes the calls it relies on (`finding_review_pg.py`):

- for a repeat locus: the locus entry as the record freezes it (each member's call, the alleles, the near-boundary warnings, the thresholds and QA's count correction);
- for a Paraphase region: the results, without their load time.

A save that sets or changes the classification, or adds *Report*, freezes the calls again. A save of only tags or a note leaves them as they are, so that it does not silently remove a change. If at sign-out the frozen calls differ from those of that moment (a new import, a different threshold in the catalogue, a count correction by QA), or they are no longer there, the review counts in this gate, with what changed (`finding_drifted`, REQ-TRACE-015; `TF-06`, hazard H17). The repeat and Paraphase tables also show that drift with each review.

**Where in the code:** `sign_out_report` and `backend/app/services/classification_drift_service.py`; for the repeat and Paraphase reviews `backend/app/services/finding_review_pg.py`.

### Gate 2 — no unexplained sample-QC failure

The sample QC detects swapped samples or a wrong pedigree (`TF-06`, hazard H4, severity S5). The gate blocks in two cases:

1. a **failure found** (the overall status is `fail`);
2. a **check that could not run** for a relationship the pedigree claims (parent–child, siblings, two samples of the same person, the NIPT line, or the sex of a sample without a kinship anchor). A swap can show up as *missing* data, and that must not be signed out silently.

If the gate blocks and it has not been acknowledged, `409` follows with the findings. The report page asks for the reason in a separate dialog; it is frozen next to the QC verdict.

For a monogenic NIPT family, a failed NIPT quality check from the section `nipt` also counts, under the same acknowledgement:

- no estimate of the fetal fraction;
- a fetal sex on which the father's X alleles and the chrY coverage contradict each other;
- male DNA as the mother's plasma (REQ-NIPT-015).

The `409` names them in `nipt_failed_checks` and in `qc_summary.messages` (the plasma one with its sample); the audit event records them (`nipt_failed_checks`). Paternity is not among them: the sample QC reads it from the same analysis and already blocks on it. NIPT results that could not be read are in `not_captured`; they do not block.

For a family with ccnv output, the section `cnipt` also counts, under the same acknowledgement:

- a maternal plasma without a result, with the reasons;
- a result that rests on provisional bounds, which QA has not set yet, with those bounds.

The `409` names them in `cnipt_concerns` and in `qc_summary.messages`; the audit event records them (`cnipt_concerns`). The sample QC of a conventional NIPT runs no check (application `cnipt`, verdict *skip*), so nothing else holds the sign-out back: ccnv's output has no genotypes, and the fetal sex is, as for a PGT embryo, known only from the analysis.

Every finding names, at the start, the sample, the pair, the child with its parents or the NIPT father of its check, e.g. *Sample X: no chrX genotypes available for sex inference.* That way the message for two samples without chrX calls does not read the same sentence twice. This holds in the `409` (`qc_summary.messages` and each group below) and in the groups of the audit event. The fetal sex and the NIPT maternal line are read from the cfDNA as a whole and name no sample. The checks in the snapshot (`sample_qc`) keep their own message, next to their sample fields.

**Each group of findings separately.** The `409` puts each finding in its group:

- `failed_checks`: the checks that failed;
- `unverifiable_checks`: the checks that could not run;
- `nipt_failed_checks`: the NIPT quality checks that failed;
- `cnipt_concerns`: what of the conventional NIPT holds the sign-out back;
- `warning_checks`: the checks that warn; these do not block on their own.

A check that warns because it could not run is only in `unverifiable_checks`: every finding appears once. The message names every kind of finding that keeps the gate closed, so that a failure does not hide a check that could not run. The dialog of the family report and the NIPT report (`reportSignOut.tsx`) shows each group under its own heading, before the field for the reason. The audit event of the acknowledged sign-out records the same groups (`qc_failed`, `qc_unverifiable`, `nipt_failed_checks`, `cnipt_concerns`, `qc_warnings`): what the acknowledgement covered.

**Where in the code:** `sign_out_report`, `_qc_failure_summary`, `_failed_qc_checks`, `_warning_qc_checks`, `_unverifiable_swap_checks` and `_named` in `report_signout_service.py`; the dialog in `frontend/src/pages/families/reportSignOut.tsx`.

### Gate 3 — no unexplained incomplete import

If a package import fails for part of the datasets, it sets the flag `import_incomplete` in the family metadata (chapter 6). The flag names the failed and the successful datasets, the time and the import job, with the error per dataset. Such a family can be missing whole datasets, e.g. all structural variants (`TF-06`, hazard H16). A single-file upload of small variants that fails after it has already deleted the callset it replaces sets the same flag for that callset, without an import job (chapter 6).

An import whose process stopped halfway (a restart, a crash, out of memory) does not set that flag. Its entry in `import_unfinished` (in the family metadata), which it set before its first write, does remain (chapter 6). The entry names the import job, the start, the planned and the completed datasets; the others may be half written or missing. Such an entry counts as the flag. These writers also set such an entry, without a job, before their first write in ClickHouse:

- a single-file upload that writes to ClickHouse: key `upload-…`;
- a deletion of the family's data that deletes or rewrites rows there: key `deletion-…`. That is deleting a sample, its SVs or the family, a PED upload or Family Builder that replaces the family, and a structure change that deletes its data (chapter 6).

Those entries also count as the flag. The message with the `409` then names “a single-file upload” or “a deletion of this family's data” that “did not finish”, and what may be half written or missing. The signed record names the upload or the deletion, from the key that it freezes along with it.

If there is a flag or an entry that the signer did not acknowledge, `409` follows (`gate = "import_incomplete"`) with the failed datasets, the import job and each entry (`import_unfinished`). One acknowledgement covers both; the report page asks for the reason in a separate dialog. The flag and the entries are frozen along with the record. As long as one is present, every family page shows *Import incomplete*. If the family holds several analyses, a sign-out of one analysis is held only by its own markers and by those of no analysis: every failure in the flag and every entry names the analysis of its import (`analysis_type`; a single-file upload or a deletion names none). So a failed `snv` of the long-read import does not hold the short-read sign-out, although that package has an `snv` dataset too. Only an import of the same analysis, or an upload, brings such a marker in again.

The flag and the entries each have one form, and CoGA reads them only in that form (chapter 6). It never reads a flag or entry in another form as no flag, nor as something it does not say. The sign-out fails on it, so the gate stays closed. Every family page then shows *Import incomplete* and names nothing: the marks do not have the form CoGA writes, the family's data may be missing or half written, and signing out fails until they are put right. The frontend reads only the keys it shows (`importMarksFromMetadata` in `ImportIncompleteBanner.tsx`). If a refusal or a signed record comes with marks in another form, the dialog and the signed version say so too, instead of naming anything.

This is how they disappear again:

- The flag disappears only when an import has re-imported every failed dataset, or a single-file upload has brought them in again. A later failure keeps the earlier one, each with its import job.
- The entry of a stopped import is cleared only by an import with `overwrite` that re-imports the datasets the stopped import did not complete, for the same samples and (for the small variants) the same source.
- Deleting a sample in **Admin** removes that sample from the flag and from the entry of a stopped import. What named only that sample drops out: no import or upload can bring in its data any more (chapter 6).

**An `overwrite` whose restore also failed.** If an `overwrite` failed and then the restore of the family failed too (other than by refusing without a full backup), every dataset of the family may have lost rows, whichever import or upload wrote them. Therefore:

- the flag names every dataset of that import as failed, the successful ones too;
- the entry of that import remains, with every dataset the family holds in ClickHouse (what its imports and single-file uploads tracked in `imported_scopes`) and every dataset of the import. It does not name a dataset of another import or upload that is only in Postgres (repeats, Paraphase, QC, phenotypes): the restore resets Postgres in one transaction and so cannot leave such a dataset half written;
- the snapshot freezes such an entry with `whole_family: true`, so that the `409`, the dialog and the signed record call it a restore that did not finish, not an import that stopped.

The gate then stays closed until the whole family has been re-imported with `overwrite` (not only what failed), and what a single-file upload brought in has been uploaded again with `overwrite`; an administrator does both (chapter 2). Every import that succeeds with `overwrite`, and every upload with `overwrite`, removes from the entry what it brought in again, for the same samples and source. An `update` removes nothing from it (chapter 6).

**Where in the code:** `sign_out_report`, `_import_incomplete_state` and `_import_unfinished_state` in `report_signout_service.py`, which keep the markers of the analysis signed out with `frozen_import_flag` and `frozen_unfinished_entries` (`holds_mark` in `signout_scope.py`); in `family_package_registration.py` the flag in `_flag_family_import_incomplete`, the entry in `ImportMark` (after a failed restore in `whole_family_entry`), what a single-file upload tracks in `record_family_upload` and its mark in `FamilyWriteMark`; the message in `frontend/src/components/ImportIncompleteBanner.tsx`, which `FamilyPageBanners.tsx` shows on every family page: in `FamilyPageHeader.tsx`, or in the own header of the NIPT pages, the viewers and the ROI marker page. `frontend/src/__tests__/familyRouteBanners.test.tsx` goes through every family route and requires the warnings of gates 0, 3 and 3b.

### Gate 3b — a package folder that changed after the import

A family has one package record per analysis it was loaded for (the monogenic and the conventional NIPT of each pregnancy each have their own). CoGA shows an analysis's reads (IGV), signal tracks and QC report from the package folder its record names, wherever that folder is. Each such folder must be as its analysis's reference delivery was imported: the delivery from which that analysis's data were last fully loaded, as a new family or with overwrite (an update skips the datasets the family has, and keeps the reference); each folder is held against its own reference, never against another analysis's. After the import, the import watcher follows each folder (chapter 6; the owner's decisions of 2026-11-11). At sign-out CoGA also takes the folder's fingerprint itself, with stat only and outside the event loop.

- **What counts as a change:** a folder that has changed (a file added, gone, or with a different size or time), is gone, cannot be read (within 30 seconds, `SIGN_OUT_FOLDER_CHECK_SECONDS`, or not at all), or a folder from which CoGA shows nothing (outside every import folder; it is not read; in development without configured import folders every local folder counts, as for the watcher). A folder with more files than a list keeps still gets a fingerprint; a folder too large for a fingerprint counts as changed. An error while reading the folder is never a pass: it counts as a folder that cannot be read.
- **The refusal:** `409` (`gate = "package_source_change"`) with `package_source_changes`, one item per analysis whose folder changed (the message names each analysis), each with the analysis (`analysis_type`), the reference delivery, the folder's name, the fingerprint it was imported with and the current one, and what was found: `changed`, with the paths that were added, disappeared or changed (each list up to 50 paths, with the count), or `gone`, the source files are no longer available; `found_at_sign_out` if the sign-out's own check found it before the watcher.
- **A warning, not a block:** the signer acknowledges it with a reason (`acknowledge_package_source_change`), and signing out remains possible. The change is frozen along with the record (REQ-TRACE-018; `TF-06`, hazard H16).
- The change is read outside any savepoint: if that read fails, the sign-out stops; the gate never opens silently.
- **When it disappears:** when the folder is again as the reference was imported, when a delivery of the same analysis is imported with overwrite, or when the folder's new delivery is refused with a reason.
- **An archived folder:** a missing folder that a superuser confirmed as deliberately archived (`archived`) does not hold up the sign-out. The snapshot freezes it, with who, when and why, and the family page says that the source files are archived. If a check finds that folder again in a different state, the gate warns again.

**Where in the code:** `sign_out_report` (`PACKAGE_SOURCE_GATE`) and `_package_source_changes_state` in `report_signout_service.py`; `package_changed_after_import.py`; the message in `frontend/src/components/PackageSourceChangedBanner.tsx`, which `FamilyPageBanners.tsx` shows on every family page, one per changed folder, naming the analysis.

### Gate 4 — no abnormal repeat or Paraphase call without a review

An analyst reviews a repeat locus and a Paraphase region per family and assembly: tags (including *Review*, *Exclude* and *Report*), a note and a classification on the type's own scale. That classification sits next to the automatic status; the review never changes that status (REQ-DIAG-009, the owner's decisions of 2026-10-28 in `docs/repeat-paraphase-review-design.md`).

If a repeat locus or a Paraphase region has an abnormal call and no review, `409` follows (`gate = "unreviewed_findings"`) with every such finding (`unreviewed_findings`: the kind, the locus or the region, and every abnormal member with their status).

- Abnormal for a repeat: the status *pathogenic*, *premutation*, *review* or *not assessed*, or a count close to a boundary. A grey zone alone does not count.
- Abnormal for Paraphase: *pathogenic*, *carrier* or *review*.
- A review counts as soon as it has a classification, a tag or a note.
- If the repeat or Paraphase section could not be read, that is in the list.

The signer reviews the findings, or acknowledges them with a reason (`acknowledge_unreviewed_findings`). The list is frozen in the record and the audit event (REQ-TRACE-016; `TF-06`, hazard H1). The record also contains the reviews with a classification or *Report*, with their frozen calls and their drift (`repeat_expansion_reviews`, `paraphase_reviews`, REQ-TRACE-017), next to all calls of the sections `repeat_expansions` and `paraphase`: a review marks a finding, it takes none away.

**Where in the code:** `sign_out_report` (`UNREVIEWED_GATE`) in `report_signout_service.py`; `finding_reviews_for_record` in `finding_review_pg.py`.

### What an acknowledgement covers

An acknowledgement covers exactly the findings its dialog showed. For that, every `409` of gates 1, 2, 3, 3b and 4 includes `concerns_fingerprint`: a SHA-256 over the canonical JSON (sorted keys) of the gate and its findings as the `409` lists them, each list sorted. The findings per gate:

- drift: `drifted`, `structural_drifted` and `finding_drifted`;
- sample QC: the five groups (`failed_checks`, `unverifiable_checks`, `nipt_failed_checks`, `cnipt_concerns`, `warning_checks`);
- incomplete import: `import_incomplete` and `import_unfinished`, each without its `at` (the message names the time; the jobs and the datasets say which failure it is);
- a package folder that changed after the import: `package_source_changes`, each change that makes the gate warn (one per analysis), without `detected_at` (the same change found again later is the same finding);
- abnormal calls without a review: `unreviewed_findings`.

It holds no time, and nothing that changes while the findings stay the same: the same findings give the same fingerprint. The dialog sends the fingerprint back with the acknowledgement (`drift_concerns_fingerprint`, `qc_concerns_fingerprint`, `import_incomplete_concerns_fingerprint`, `package_source_change_concerns_fingerprint`, `unreviewed_findings_concerns_fingerprint`). The sign-out reads the gate again and compares. It receives:

- the fingerprint of the current findings: the acknowledgement covers them;
- a different one (a finding was added, disappeared or changed after the dialog showed them): `409` again, with the findings as they are now, their fingerprint and `concerns_changed: true`. Nothing is written (no signed version, no audit event). The dialog shows the findings again, says why (*Nothing was signed out: the concerns changed after this dialog listed them …*), clears the field for the reason and asks for a new reason, which it sends with the new fingerprint;
- none: `422`. The SPA (the web app in the browser), the only client, always sends the fingerprint its dialog received. A value that is not a SHA-256 in lowercase hexadecimal is refused by the validation of the request itself (`422`).

That way a finding that turned up after the dialog showed the findings is never acknowledged along with them unseen.

- A gate without findings asks for no fingerprint. An acknowledgement of findings that have since disappeared acknowledges nothing.
- The gates are read in order. A change at an earlier gate is refused there first; the acknowledgements already given are carried over to the next attempt.
- The sign-out's audit event records the same fingerprint fields, `null` for a gate without findings.
- The snapshot freezes the findings themselves (the drift sections, `sample_qc`, the import status, `package_source_changes`, the list of gate 4), and the audit event the groups of the sample QC. That way every fingerprint can be recomputed from the record. The snapshot itself contains no fingerprint: the findings it freezes are those against which the sign-out checked the fingerprint.

**Where in the code:** `concerns_fingerprint`, `_gate_refusal` and `_refuse_unless_shown` in `report_signout_service.py`; `ReportSignoutRequest` in `backend/app/schemas/traceability.py`; the dialogs in `frontend/src/pages/families/reportSignOut.tsx`.

## The frozen snapshot

| Field | Content |
| --- | --- |
| `family_id`, `assembly` | The family and the reference assembly |
| `analysis_type` | The analysis the record closes; `null` for a family that holds none, whose record covers the whole family (see *One sign-out per analysis*) |
| `modules` | The full annotation and reference manifest, per modality |
| `software` | The build that made the snapshot (`app_version` and `git_sha`) |
| `drift` | The number of small-variant classifications checked and the list with drift (including `no_snapshot`), with for each drift what shifted |
| `structural_drift` | The same for the classifications of SVs and CNVs |
| `sample_qc` | The full sample QC |
| `sequencing_qc` | The sequencing QC per analysis the record holds (`analyses`): the profiles the limits were read from, in order (`profiles`), per metric the limit with the profile it came from (`thresholds`: `warn_value`, `error_value`, `profile_key`), and per sample the values and verdicts (`samples`). An analysis's record holds its own QC and that of a package that named no analysis; that of a family with one analysis or none holds every analysis's. If the limits could not be read, `analyses` is empty and `unavailable` gives the reason |
| `import_incomplete` | Empty (`null`) if the data were imported completely; otherwise the failed and the successful datasets, the time, the import job, per failed dataset its job (`failed_jobs`) and the analyses of its failures (`failed_analysis_types`) |
| `import_unfinished` | Empty (`{}`) if no import stopped halfway; otherwise the entries of gate 3: per import the job, the start, the analysis (`analysis_type`), the datasets and which were completed; after an unfinished restore of an `overwrite`, every dataset that must be re-imported, none completed; for a single-file upload or a deletion no job, under `upload-…` or `deletion-…` |
| `package_source_changes` | A list, one item per analysis of the family (one package record per analysis) whose package folder, from which CoGA shows its source files, is no longer as its reference delivery was imported, in the order of the analyses; empty (`[]`) if every folder still is (or the family did not come from a delivery). Each item names the analysis (`analysis_type`, the key of its package record) and the change as gate 3b lists it, with when the watcher found it (or `found_at_sign_out`), and for a folder confirmed as deliberately archived `archived` (when, by whom and why). The check after sign-out compares the list without `detected_at` and `found_at_sign_out` (REQ-TRACE-018) |
| `reported_variants` | Every reported small variant with class, criteria, tags, note and frozen evidence. A variant is reported when its own review or that of its compound heterozygous pair carries the tag `report`: the rule of the review-tag filter, by which the live report and *Show reported* in the header of the NIPT page also show them. If a variant reports its pair, that pair review is included too (`compound_het`: the partner, the gene, the classification, the tags and the note of the pair, and the phase) |
| `reported_structural_variants` | Every reported SV or CNV with classification, CNV criteria, tags, note and frozen evidence |
| `class_shown` (in each variant of the two sections above) | The class the variant's review shows, by the one rule of the family page (`review_pg_utils.review_class_label`: a bare class word in the text, otherwise the most severe ACMG class tag, otherwise the text); `null` if it shows none. The backend computes it at sign-out from the stored review, never from the request. A small variant also freezes its classification text as stored (`classification`), as an SV does. The ACMG record and the CNV score stay next to it, so that a class the analyst chose over the ACMG score keeps that score in the record |
| `record_statement` | The sentence that says what the record is: the analytical sign-out, which the EPD refers to through the report code |
| `variant_descriptions` | Every reported variant as the analyst saw it, one per callset or caller. Small variant: the position, the gene, the transcript (and whether it is MANE Select), HGVS g. (VEP's HGVSg, `null` if the annotation has none), c. and p., the consequence, and per sample the genotype with depth, GQ, allele depths and allele fraction. SV or CNV: the type, the copy change (loss or gain), the position, the length, the genes, the cytoband, the caller, and per sample the genotype and the copy number (REQ-TRACE-007) |
| `gene_panels` | Every gene panel version that the family's searches applied: name, id, local version, number of genes, source and the source's release (PanelApp's version of the panel, or the Monarch release of the Mendeliome), when CoGA imported that release, and when and by whom it was first applied (REQ-CARR-003) |
| `reference_data` | Besides the modules: per source of the gene reference, the release and checksum that answered for the genes of the reported variants, and the clinical CNV knowledge base (source, the release the source states, and when it was loaded) |
| `repeat_expansions` | Every TRGT call of the samples, with the status CoGA gives it at sign-out, the alleles, the thresholds behind it and the catalogue (number of loci, sources and a digest of the thresholds). Per allele the classified count (`repeat_count`: TRGT's with QA's count correction) and TRGT's (`trgt_repeat_count`), with the near-boundary warning as the repeat page shows it (`count_unit_warning`: the count, per side the nearest count within the margin with a different status, and the margin; empty without a warning). Per locus QA's count correction as `repeat_locus_count_units` held it (`count_units`: the correction `count_offset`, the reason, who and when; without a register `count_offset: 0` and the rest empty). And the margin that applied (`count_unit_margin`) |
| `paraphase` | Every Paraphase result of the samples, with the status CoGA gives it at sign-out, the copy numbers, the region's status rule and copy norm, and the catalogue (number of regions and a digest) |
| `pgt_analysis` | Only for a PGT family (a family with an embryo): the PGT analysis at the ROI, as the family page shows it (`pgt_analysis.py`, `GET /families/{id}/pgt-analysis`). It contains the ROI and the window of haplotype blocks read (the ROI and 1 Mb on each side), the inheritance model as entered and as applied, the role, the sex and the status of each member, the disease haplotype at the ROI (or that none was found) and the parents' phase corrections close to the ROI. Per embryo: the sex; the call (none for a family without an ROI); whether an *Uninformative* is due to the embryo's own missing data; both calls when an X-linked recessive call depends on a sex that was not entered; the homologues the embryo carries at the ROI, with their blocks; the block boundaries within 250 kb of the ROI (recombinations); the phasing QC at the ROI as the ROI marker review counts it over the same window (informative sites, Mendel errors and their rate, the sites per parental side, in total and left and right of the ROI separately); the verdict on that QC against the thresholds of `pgt_embryo_qc.py` (`qc_verdict`: passed; not passed, with each measure, its value and the threshold; or not counted, which is not a passed QC), with the parental sides whose markers were tested. In addition, the input: each member's haplotype file and when it was loaded, the number of blocks read with a SHA-256 digest of them, the distances the warnings use and the QC thresholds that applied (`rules.qc_thresholds`) (REQ-PGT-005). A family without an embryo does not have this key; a monogenic NIPT family only if one of its embryos has its own haplotype blocks (`embryo_has_haplotype_blocks`). The import reads a child of unknown sex with an unknown or unaffected status as an embryo (as a NIPT PED usually gives the fetus), and the NIPT trio takes that embryo as the fetus. The fetus has no sequencing column and so no blocks |
| `nipt` | Only for a monogenic NIPT family (`signed_record_nipt.py`), of the monogenic NIPT of one pregnancy: the analysis (`analysis`); the trio, with the pregnancy's fetuses (`samples.fetuses`); for a multiple pregnancy the note about it (`multiple_pregnancy_note`, otherwise empty); the fetal fraction with interval, number of sites, median per site and verdict; the de novo window; the categories; the filter funnel and why cfDNA calls did not pass the quality filter; the quality checks with their verdict and the sentence that explains it (paternity, the fetal sex from both signals, the plasma sample; `nipt_quality_checks.py`, the same verdicts as on the NIPT page); the coverage of every capture target and every gene with a weak target; without a per-target coverage table, the notes of the coverage check (`coverage_notes`, see *The notes of the NIPT coverage check*); every variant with the tag `report` (on its own review or on that of its pair) as the NIPT variant list classifies it (category, reads, probabilities, flags, its recurrence in the assay's other cfDNA samples, and for a de novo candidate in the fetal window its triage); every reported variant that the analysis does not classify, with the reason; the recessive fetal risk of their genes, as the recessive view computes it over *Show reported*; and what the analysis ran with (the coverage tables, the artefact list with a digest of its alleles, and the model) |
| `cnipt` | Only for a family with ccnv output (`signed_record_cnipt.py`), of the conventional NIPT of one pregnancy: the analysis (`analysis`), the pregnancy's fetuses (`fetuses`), for a multiple pregnancy the note about it (`multiple_pregnancy_note`), and per maternal plasma its package names the result as the cNIPT page shows it. It contains the status (without a result, with the reasons), the verdict per target with the reasons and whether the claim covers the target (`claimed`), the findings with ccnv's values, the fetal fractions, the fetal sex, the call of the sex chromosomes, the limits of detection and the warnings. Besides these, the bounds the rule applied, each QA's or provisional, and the inputs: a digest of the QC object as the import kept it, the number of segments read with their digest, and the segment files with their rows and load time |
| `coverage_tracks` | Every coverage track of the members, per sample and caller (`web` for a coverage BED, from an upload or a package; `wisecondorx`, `qdnaseq`, `hificnv`), with what it contains: `read_depth`, `log2_ratio` or `unknown`, by the one rule of the depth readers (`coverage_read_depth.coverage_track_content`): for a coverage BED what it declared at loading, for another caller what the transformation of its import made of it. Per file the name, the number of rows, when and from where it was loaded (`upload` for a single-file upload, `package` for a package import; empty if the record does not say), with that declaration or transformation (the owner's decision of 2026-10-20). Only a track with read depth is read as depth (the mtDNA coverage and its verdict `mtdna.mean_depth`, the NIPT coverage check without a per-target table); the record therefore says what kind of coverage a depth check relied on. Every family has this key, without a coverage track as `{"tracks": []}`, so that a track loaded after sign-out counts as a change. A NIPT family's per-target coverage tables are a different track type and are in `nipt`, as are the notes of the NIPT coverage check about these tracks (`nipt.coverage_notes`) |
| `package_intake` | Per analysis the family was loaded for, in `deliveries` and in the order of the analyses: the delivered package from which that analysis's latest package import came, as its package record names it (`delivery` in `families.metadata.package_imports`): the analysis (`analysis_type`), the id, the pipeline run, the SHA-256 of the pipeline's package file, the fingerprint of the package folder, the imported revision and the SHA-256 of the imported package. In addition, every correction made in CoGA to that package before the import (`import_delivery_revisions`), in revision order: every changed field with its path (`members.PROBAND.sex`), its kind (`pipeline_used` for sex, parents and couples, which the pipeline computed with; otherwise `laboratory`) and its value before and after, with the reason, who and when (the owner's decision of 2026-11-10). Every family has this key, as `{"deliveries": []}` for a family no analysis of which was last loaded from a delivery, so that an import after sign-out counts as a change. The family page shows the same corrections, one notice *Corrected at intake* per corrected delivery, naming the analysis |
| `repeat_expansion_reviews`, `paraphase_reviews` | Every review of a repeat locus or a Paraphase region in the family's assembly that carries a classification or the tag *Report*: the key, the classification with its label, the tags, whether it is reported, the note, who last saved it and when, the calls it froze, and whether those are still the family's calls (`drift`) (REQ-TRACE-017) |
| `finding_review_drift` | How many of those reviews were tested, and every review whose calls changed, are no longer there or cannot be compared; gate 1 counts them (REQ-TRACE-015) |
| `unreviewed_findings` | The list of gate 4: every repeat locus and every Paraphase region with an abnormal call and no review, with every abnormal member and their status, and a section that could not be read (REQ-TRACE-016) |
| `version`, `generated_at`, `signed_out_by` | The version, the time and the signer |
| `acknowledged_drift`, `acknowledged_qc`, `acknowledged_import_incomplete`, `acknowledged_package_source_change`, `acknowledged_unreviewed_findings` | Whether the signer acknowledged the findings of gates 1, 2, 3, 3b and 4. Only `true` if that gate would refuse without an acknowledgement |
| `drift_acknowledgement_reason`, `qc_acknowledgement_reason`, `import_incomplete_acknowledgement_reason`, `package_source_change_acknowledgement_reason`, `unreviewed_findings_acknowledgement_reason` | The reason for each acknowledgement; empty (`null`) without an acknowledgement |

The nine from `variant_descriptions` to `package_intake` are read by `signed_record_sections.py`, each in its own savepoint (a part of the transaction that can fail on its own).

- A section of a single application (`pgt_analysis` for a PGT family, `nipt` for a monogenic NIPT family, `cnipt` for a family with ccnv output) names that application in its `RecordSection` (`application="pgt"`, `application="nipt"`, `application="cnipt"`). It is built only for such a family: another family does not have that key.
- The check (`sign-out-check`) compares such a section if the record or the family's current data has it (if only one of the two has it, that is a change); if neither has it, it does not.
- A section that cannot be read is frozen as `{"unavailable": "…"}` holding a fixed sentence, never as an empty section, and is listed in `not_captured`. The text of the error itself, which can quote values, is not frozen.

**The gene panels.** Without a registration of its own, nothing would track which panel a search applied: the HTTP access log by default keeps only the query's keys (`AUDIT_LOG_QUERY_STRING_MODE=keys`), writes in batches after the response and can be switched off. That is why a family search that applies a panel writes, before it runs, a clinical audit event `panel_applied`, the first time it applies that version of the panel. That holds for the lists of small variants and SVs and their CSV exports, the NIPT variants and the NIPT coverage. The event contains the id, the name, the local version, the number of genes and the source's release.

- The same version again writes nothing; a new version (every version is archived) or a new PanelApp version with the same genes does.
- The check runs a second time under the lock of the family's audit chain, so that two simultaneous searches record the panel once.
- If the write fails, the search fails.
- A panel in the variant explorer, which searches across families, is recorded for no family (`panel_use_audit.py`).

**The embryo calls.** The backend makes them (`pgt_embryo_calls.py`) with the same rules as the haplotype tracks in the browser (`haplotypeRisk.ts`), which draw the risk lane and the risk status per member. Both give the same answers on the cases of `frontend/src/lib/__tests__/fixtures/pgtEmbryoCallCases.json`, which `test_pgt_embryo_calls.py` and `haplotypeRisk.test.ts` both run. The family page shows the calls the backend gives, and the sign-out freezes that same object: the record contains the calls the analyst saw, made by one implementation from stored data. The check compares the whole section: a QC verdict or threshold that has changed since counts as a change.

**The class each reported variant shows** (`class_shown`, with the classification text next to it; the owner's decision of 2026-11-10). The live report and the signed version show that class, *Not classified* if there is none, and next to it the ACMG or CNV score if that gives a different class (*VUS - class 3 (ACMG score: Likely pathogenic - class 4 · 8 pts)*); both use `ReportClassification.tsx`. The rationale then names the class of the criteria separately from the class shown. The check compares the class shown: a class that changed since the sign-out counts as a change, even if only the text of a small variant changed (*VUS* next to the class-4 tag rewritten as *Likely benign*).

**The repeat expansions and the count correction.** Counts, statuses, warnings and QA's register with its count correction (chapter 8) never contradict each other in the record: the family table reads the register in the same query as the calls, and `signed_record_sections.py` freezes exactly that register. A correction recorded or changed after sign-out counts as a change of `repeat_expansions`.

**The notes of the NIPT coverage check.** Without a per-target coverage table, the NIPT page's coverage check reads the plasma's coverage tracks, and names every track it does not read as depth, with the reason (chapter 8). The section `nipt` freezes those notes word for word (the owner's decision of 2026-10-28), as the NIPT page shows them, with what the check ran for (`coverage_notes`: `gene` and `panel`, both empty, the `regions` and the `notes`). The signed version thus shows the notes the analyst saw, not ones recomputed later.

- The check depends on the gene or panel of the search on the NIPT page, which the sign-out does not receive. It therefore runs with the page's code (`nipt_service.nipt_coverage_notes`) without a gene or panel, over the family's ROI, just as `target_coverage` holds the check of every capture target. Without an ROI the check runs over no region and names nothing. With a per-target coverage table `coverage_notes` is `null`, because the check then reads the targets.
- The signed view shows the notes under *Coverage tracks not read as depth*, with what the check ran for (*The coverage check ran without a gene or panel, over the family’s region of interest …*).
- The check compares the whole section, the notes (also when `null`) included. A track that has been loaded, declared or deleted since, or a changed ROI, which change the notes or the region, counts as a change.

**Adding a section** takes four pieces: a builder in `signed_record_sections.py` (deterministic, JSON-safe, in `RECORD_SECTIONS` and `build_record_sections`); the reading in `signedReportRecord.ts` (`parseRecordSection`) with a label in `REPORT_SECTION_LABELS`; the display in `SignedFamilyReport.tsx` (`RecordSectionBlock`); and the description in `docs/clinical-traceability.md`. Without the second and the third piece, the section is missing from the signed view without a trace.

At sign-out the version, the time, the signer and the acknowledgements with their reasons are added. That binds the snapshot to **three version axes**:

- **Software:** `app_version` and `git_sha`, recorded when the image is built.
- **Annotation and pipeline:** the frozen manifest and, per classification, the frozen evidence with the hash of the annotation set (gate 1); for an SV or CNV, the hash of the frozen evidence, with the versions of the SV callset behind it.
- **Reference:** the assembly, the source of the gene loci that CoGA loaded itself (GENCODE, or the UCSC table if GENCODE failed), the Monarch release and the HPO release (the release of the latest import of the ontology).

**The hash.** The content hash is a SHA-256 over a fixed, key-sorted JSON encoding; lists are sorted beforehand on a stable key. The clinical sections are deterministic: the same content gives the same fingerprint. The check below relies on that. The content hash also covers the version, the time and the signer, and is therefore unique per sign-out. A stored hash is always recomputed over the snapshot as it was stored.

**Independent of ClickHouse.** The snapshot itself contains the values it needs. A later rebuild of ClickHouse or a new annotation version does not change the signed record.

**Where in the code:** `build_report_snapshot`; the encoding in `backend/app/services/hash_chain.py`; the table `report_signouts` in `04_traceability.sql`.

### The report code

The clinical report in the EPD cites the signed record through its **report code** (`backend/app/services/report_code.py`), e.g. `7KQM2X-2-4PHT`. The code is computed from fields of the record that never change; no column stores it. It consists of:

- six characters from the SHA-256 of the family ID, the same in every version of the family's record;
- the version, in digits;
- three characters from the first 15 bits of the content hash, which bind the code to the signed content;
- a check character over all the others (the Damm algorithm over GF(32)). It catches every mistyped character and every swap of two adjacent characters: a wrongly copied code is refused and never finds another record.

The characters are Crockford's base 32 (digits and capital letters without I, L, O and U). Upper or lower case, hyphens and spaces do not matter; an I or L reads as 1, an O as 0. A code has 13 characters for versions 1 to 9, and at most 16 up to version 9999.

**Lookup.** `GET /api/report-codes/{code}` returns the family, the version, the signer, the time and the content hash of the record. A text that is not a report code gets `422` (`report_code_invalid`) with the reason; that follows from the text alone. A valid code without a record, a code of a family outside the user's projects and a code of a deleted family all get the same `404`; only the audit row names the hidden family (REQ-SEC-001). In the app, the lookup is under **Report codes** in the main menu (`/report-codes`, and `/report-codes/<code>` as a link from the EPD).

**Where in the code:** `report_code.py`; `find_report_signout_by_code` and the uniqueness in `sign_out_report` (`report_signout_service.py`); `backend/app/routers/report_codes.py`; `frontend/src/pages/reports/ReportCodeLookupPage.tsx`.

### Does the report page show the signed report?

Only the signed version is the signed report. The report page has two views:

- **A signed version** (`?version=N`). A signed-out case opens on the latest one (for a family with several analyses, the latest of the analyses the family report signs out). `SignedFamilyReport.tsx` draws it solely from the frozen snapshot that `GET /families/{id}/report/sign-outs/{version}` returns; `signedReportRecord.ts` reads it section by section. The page shows:
    - the version, the signer, the time, the **report code**, the sentence that says what the record is, the content hash and whether it still matches (`verified`), and the build that signed out;
    - the analysis the record closes, in the title (*Clinical report — signed version 2 of the conventional NIPT of pregnancy ZW1*) and, if the family's versions close more than one analysis, next to each version in the list of versions; the *latest* and the check are that analysis's;
    - per reported variant the description as the analyst saw it, the class, the accepted criteria, the frozen evidence (including the gnomAD datasets the callset was annotated with), the tags and the note; for a variant that reports its compound-het pair, that pair review too, under *Compound-het pair*. The class is the one the record froze as shown (`class_shown`), with the ACMG score next to it if that gives a different class (see *The class each reported variant shows*);
    - the SVs with their description and ClinGen CNV criteria, the class as the SV page shows it and the CNV score next to it if that gives a different class, never as the class;
    - for a PGT family, the embryo calls (*PGT embryo results*): per embryo the call, the homologues at the ROI as the track names them (P1, P2, M1 and M2), the warnings, the QC, the verdict on that QC and the thresholds that applied;
    - the gene panels applied;
    - the repeat expansions, with next to the call every count close to a boundary (*⚠ Near a boundary: 53 is within 3 repeats of 55 (Premutation)*), a count corrected by QA with TRGT's count in brackets (*18 (TRGT 19)*), and per locus the count correction as QA recorded it (*Count correction: none (no QA record)*, or *TRGT’s count −1*, with the reason, who and when);
    - the Paraphase results;
    - the coverage tracks (*Coverage tracks*): per sample and caller read depth, a log2 ratio or unknown, with the file, where it came from (a single-file upload or a package) and when it was loaded;
    - per analysis, the delivered package and its corrections at intake (*Package intake*): the analysis, the run, the package file and the imported revision, and every correction with what it changed, why, by whom and when; a change the pipeline used and a member added or removed at intake are marked;
    - the releases of the reference data;
    - the reported repeat expansions and Paraphase findings (*Reported repeat expansions and Paraphase findings*): per finding the gene and the disease, each member's call as the review froze it with CoGA's status, the analyst's classification and note, and the marks *differs from CoGA's status* and *Changed since review*;
    - the drift, including that of the SV and CNV classifications and of the repeat and Paraphase reviews;
    - the sample QC, the sequencing QC, the abnormal repeat and Paraphase calls without a review, and the import status with the acknowledgements;
    - in the footer the frozen versions with the reference data, the coverage tracks on one line (*Coverage tracks as signed: S1 coverage BED (package) — read depth; …*) and the report code.

    Right after signing out, the page says, on screen only, *Signed out as version N. Report code …*.

- **The live report** (`?view=live`) shows the **current** data, with per variant the class its review shows on the family page and the ACMG or CNV score next to it, as the signed version does. This is where the case is signed out; after that the page shows the new version. It is never the signed report, even if the content matches ("This is the live report, not signed version N"), and every printout of it gets a notice at the top. While the sign-out record is loading or could not be loaded, it offers no sign-out.

In addition:

- The signed version says what the snapshot does not contain, for the report and per variant: the population frequencies and in-silico predictions, the segregation, the gene and phenotype context, the audit trail and the pipeline settings. It never fills these in with current data. A section that could not be read at sign-out, or that the page cannot read, appears as *Not captured*, with the reason, never as empty. If the page cannot read a list in a section (a value that is not a list, or an element that is not of its kind), it leaves it out and the section says so (*Part of … cannot be read here*; `parseRecordSection` in `signedReportRecord.ts`).
- The print button prints the signed version. At the top of the printout there is a notice for a record that no longer matches its content hash, for a version that a later one has superseded, and for a version that is not established to be the latest.
- A check (`GET /families/{id}/report/sign-out-check`) builds the snapshot of an analysis as it would be frozen now, and compares it section by section, each section as a whole, with that analysis's latest signed version. That way the live report says whether it still matches, which parts changed, or that the check could not run. On the latest signed version the same check says, on screen only, whether the family's data have changed since.
- The frozen record itself can be downloaded as JSON in both views.
- If a lookup fails during sign-out (e.g. the QC limits or the version of the assembly, the gene loci, Monarch or HPO), the sign-out goes ahead, but the snapshot freezes that part explicitly as *not available*, with the reason. The audit event and the signed version name those parts (`not_captured`, also in the response of `GET …/sign-outs/{version}`).
- A section that CoGA has since come to compute differently also reads as changed. The signed version shows what was signed out; whoever wants to record the new assessment signs out again.
- CoGA reads one snapshot format: the format it writes itself. Every record contains every section and every field (a section of a single application only for the families of that application; the repeat and Paraphase sections only in the record of an analysis that holds such data). Verification hashes the record as it is stored.
- What the snapshot freezes was decided by the owner: the variant description, the gene panels applied, the releases of the reference data and the repeat and Paraphase results on 2026-10-14; what each coverage track contains and, for a PGT family, the embryo calls on 2026-10-20. It does not freeze the gene and phenotype context.
- None of the QC thresholds holds back an embryo call. If the phasing QC at the ROI does not meet its thresholds (at least 50 informative sites, a Mendel error rate of at most 2%, at least 6 markers on each side of the ROI for each parental side the call reads), or it was not counted, a warning sits next to the call, on the family page and in the signed version. The call stands, the sign-out is not held back and the analyst decides (the owner's decision of 2026-10-20); the record freezes the verdict with the thresholds. Those thresholds are provisional: the laboratory director and QA still have to confirm them (CLIN-61). One other measure does change the call: an embryo that carries both homologues of a parent at the ROI becomes *Uninformative* (chapter 8).

## Append-only, hash-chained audit trail

CoGA keeps two audit logs:

- **The HTTP access log** `audit_log_events`: every request, with method, path, status and user (chapter 7). Next to it is the log of UI events, `ui_events` (clicks and navigation in the browser).
- **The clinical audit trail** `clinical_audit_events`: meaningful clinical actions, with per field the value before and after. The trail is written in the same transaction as the change itself, so that it never falls out of step with the data. It records:
    - **Reviews** of small variants, SVs and CNVs: classifying, setting or removing a tag, changing a note, clearing or deleting a review. The entry for the class records the whole stored record: per criterion the strength or, for a CNV, the points, whether it is accepted, the evidence and whether it was a suggestion, with the points total (and for a CNV the ClinGen kind). That way a changed criterion is also in the trail when the class stays the same. Entries of SVs and CNVs carry `metadata.modality = "sv"`, so that their id is never taken for that of a small variant.
    - **A compound heterozygous pair** (action `compound_het_pair`): a save that records, changes or breaks up a pair writes one entry per changed pair, with the pair before and after (partner, gene, classification, tags, note and phase). The entry is filed under the variant whose review carried the pair and names the partner.
    - **Replacing the annotation manifest**, and **signing out**.
    - **A gene panel version** that is applied for the first time in a search of the family (`panel_applied`).
    - **The list of NIPT artefacts:** every change (adding, changing or deleting an artefact, filling in the list automatically or importing a list), because it changes which variants every NIPT analysis of that assay filters out (chapter 15).
    - **A family's phenotypes:** for each HPO term that an analyst, superuser or administrator adds, changes or deletes, an entry `phenotype`, with the member, the term and its label, the status, the onset, the evidence, the source and the note before and after, and a summary. An import that changes phenotypes writes, as an action of whoever started it, one entry `phenotype` per member whose phenotypes it added or changed, with the package, the phenotype file and the import job; without a phenotype change it writes nothing. The same goes for the recovery after a failed overwrite that restores the phenotypes, a failed import that deletes again the family it created, and a PED upload or the Family Builder that replaces a family. Those last ones record the phenotypes that disappear with the family, just before its entry `family_deleted`.
    - **The pedigree and the family members** (action `pedigree`): creating the family; an edit of the structure or of members (sex, role, affected status, carrier status, parents, a new name, making a member inactive); a package import that gives a member a different affected or carrier status; the deletion of a sample with its data by an administrator. The entry contains every changed member and, if they changed, the couples, the relatives of unknown degree and the links between two samples of one person (*same individual PLASMA1 = PLASMA2 added*), before and after. It names the structure version that stored the change.
    - **Deleting a family** (action `family_deleted`): by an administrator with its data, by a PED upload or manual family that replaces it with overwrite, or by a package import that created it and imported nothing. It is the last entry of its trail: who deleted it, its samples, its last structure version and what disappeared with it (the variants, the reviews, the repeats, the tracks and the files). The entry is written in the transaction of the deletion, before the family row disappears; a deletion that fails therefore records nothing. The trail, the sign-outs and the structure versions remain afterwards, detached from the family and verifiable under its family name.

Both are **append-only** in the database: a trigger blocks deleting and changing. The only exception is setting to `NULL` a reference to an account or family that is being deleted; the denormalised fields (copies as text) then keep who it was. The same protection applies to `ui_events`, `report_signouts`, `integrity_anchors`, `qc_threshold_changes` and `family_structure_versions`. That last one is the history of the pedigree: one version per change, with the whole pedigree as it was then.

**Where in the code:** the triggers in `backend/db/schema/postgres/04_traceability.sql`; the writing in `backend/app/services/clinical_audit_service.py`.

### The hash chain

A trigger stops the normal application, but a database user with enough privileges can disable a trigger. That is why a **hash chain** lies over `clinical_audit_events`, `report_signouts` and `family_structure_versions`. Every row gets a `row_hash`: a SHA-256 over its own immutable content **plus** the `row_hash` of the previous row (the first row uses the text `GENESIS`). If you change a row, its hash no longer matches; if you delete or move one, the next row no longer points to the right predecessor.

- The chain runs **per family**, on the immutable family name (`family_identifier`), not on the UUID, which goes to `NULL` on deletion. That way the signed history of a deleted family remains verifiable.
- A family that is later created again under the same name (e.g. by replacing it with overwrite) continues writing on the same chains.
    - Its sign-outs continue the numbering: after versions 1 and 2 of the deleted family, its first sign-out is version 3. The chain follows the version numbers; if the new family started again at version 1, the chain check would fail as if it had been tampered with.
    - Per family name every version is unique (`uq_report_signouts_family_identifier_version`). A family name with a version, and so a report code, therefore always points to one signed record.
    - Its structure versions, however, do restart from 1 per family row. The audit trail keeps them apart: every `pedigree` entry of the new family, and its `family_deleted` entry, names how many structure versions the deleted families before it put on the chain (`deleted_family_versions`). Its first entry also says so in the summary (*Made again under this family ID, after 2 structure versions of a deleted family: its own start again at 1.*).
- The changes to the NIPT artefact list belong to no family and form their own chain, under the name `system:nipt-artifacts`; the count corrections per repeat locus (chapter 8) are under `system:repeat-loci`.
- The hashed content **leaves out the references** that may go to `NULL`, so that a permitted deletion does not break the chain.
- On every read of a sign-out the content hash is recomputed. A difference is logged as an error ("possible tampering"), with the family, the version of the stored row and both hashes. An administrator can also have a family's chain checked in full (`GET /api/admin/integrity/verify`, with `table` equal to `clinical_audit_events`, `report_signouts` or `family_structure_versions`).
- The structure versions hash their content (version, structure hash, the whole pedigree and the metadata), but not who made the change: that is in the `pedigree` entry of the clinical audit trail, which is itself chained. No chain lies over `ui_events` and `audit_log_events`: a UI event is what the browser reports about itself, not a clinical action.

**Honest scope.** The chain is **tamper-evident, not tamper-proof**: it makes tampering visible, not impossible. It catches anyone who cannot recompute the chain. The owner of the tables, however, can disable a trigger, change a row and recompute the hashes of that row and all the following ones. As long as the app runs as the owner (chapter 2), that is the app itself. That is what the anchors below are for. Towards a regulator, therefore, phrase it as "tamper-evident against an adversary who has only the database, between stored anchors", never as "tamper-proof" or "immutable".

**Where in the code:** `backend/app/services/hash_chain.py`; the columns `row_hash` and `prev_hash` in `04_traceability.sql`.

## Integrity anchors

An anchor makes even a recomputed chain visible. `create_integrity_anchor` does three things:

1. record the **head of every chain** (per family, for `report_signouts`, `clinical_audit_events` and `family_structure_versions`: the length and the last `row_hash`);
2. summarise those heads in one hash, chain it to the previous anchor, and **sign** the whole with an Ed25519 key that is in the configuration, **never in the database**;
3. store the anchor append-only in `integrity_anchors`.

Anyone without the key can recompute a chain, but cannot forge a valid signed anchor. A check compares the current chains with the latest anchor, or goes through the whole anchor chain and all signatures. It reports `ok`, `diverged`, `chain_broken`, `signature_invalid`, `unknown_key` or `unverifiable_unsigned`, and `no_anchor` as long as there is no anchor yet.

**When is an anchor created?** An administrator or an external scheduler calls `POST /api/admin/integrity/anchor`; the checks are `GET /api/admin/integrity/anchor/verify` and `…/verify-chain`. The code does not schedule this and there is no screen for it: how often an anchor is made is a procedural arrangement (OPS-7).

**Limits.** An anchor key that falls into the wrong hands (e.g. through a compromised server) breaks the protection; guarding against that would need a hardware key (HSM). Deleting the *latest* anchors can only be seen with a copy outside the database. That export to external storage is planned but not built (`export_anchor` does nothing).

**Where in the code:** `backend/app/services/integrity_anchor_service.py`; the table and trigger in `04_traceability.sql`; the privileges in `05_grants.sql`.

### The variant store in ClickHouse

The anchors cover the Postgres chains. A separate monitor watches the variant store in ClickHouse. Shortly after start-up, and then at a fixed interval, it checks the variant tables of every assembly. For a damaged or missing table it writes an error to the log, so that an alert can go off before users are affected.

- The server keeps the latest result per assembly, with the time of the check, in memory. After a restart there is no result until the first check.
- `GET /api/admin/clickhouse/variants/integrity-monitor` shows it to administrators; the ClickHouse maintenance page shows it per assembly (chapter 15).
- If a check could not run, that is shown instead of the previous result; the error itself is in the log.
- An administrator starts a check themselves through `GET /api/admin/clickhouse/variants/{assembly}/integrity`.

**What the check assesses.** For each variant table, ClickHouse itself gives its verdict on the whole table, over every active part. Only for a table that is not in order does CoGA read the parts one by one, to name the broken ones. A table without rows is in order; a result that cannot be read never is. The same check guards the rebuild of the small variants' gene index: the new index is only taken into use once all its parts are in order.

**Where in the code:** `backend/app/services/clickhouse_integrity_monitor.py`; the check itself in `check_clickhouse_variant_integrity` and `_check_table` (`backend/app/services/clickhouse_variant_storage.py`).

## The full traceability chain

1. **Raw file → hash.** Every source file is in `raw_import_files`, with its SHA-256 (chapter 6).
2. **Annotation manifest.** The tool and database versions are stored per analysis in `family_annotation_manifest` (chapter 6): from the VCF headers or the package's run manifest, with the reference FASTA that the package file names. A sign-out freezes those of its analysis. A replacement by an administrator goes into the clinical audit trail.
3. **Variant in ClickHouse.** Every variant carries the hash of the annotation set it was annotated with (chapter 3).
4. **Classification and evidence.** Every ACMG classification of a small variant freezes the annotation version, the hash of the annotation set, the evidence of the primary transcript (ClinVar, consequence, frequencies, in-silico scores, for missense the missense Z with its source) and the time. Every CNV classification freezes the SV's evidence, a hash of its annotation and the versions behind it (chapter 10).
5. **Gates.** Before signing out, the assembly, ongoing writes, the drift, the sample QC, an incomplete import, a package folder that changed after the import and abnormal repeat and Paraphase calls without a review are checked. The assembly and ongoing writes are a hard refusal; the other findings must be acknowledged with a reason.
6. **Frozen report.** The snapshot (manifest, software version, drift, QC, reported variants and what the analysis looked at; see *The frozen snapshot*) is hashed and stored append-only as a new version, with a report code computed from the record.
7. **Chain and anchor.** The sign-out goes into the family's hash chain, a `sign_out` entry goes into the clinical audit trail, and a signed anchor can record the chain heads externally.
8. **Clinical report (outside CoGA).** Drafted and medically signed in the EPD; it refers 1:1 to the signed version, with its report code. That part is outside the device and CoGA does not check it; the report code leads back to the version.

That way an auditor goes from the hash of the raw file, via the versions and the evidence behind each classification, to a signed analysis whose integrity can be checked externally, and to which the clinical report in the EPD refers.

## IVDR mapping

This chain is the technical implementation of the traceability requirement under the IVDR (in-house IVD, Article 5(5); device boundary "annotated VCF → closed, frozen analysis in CoGA; the clinical report is drafted and medically signed in the EPD and refers 1:1 to that analysis"; the EPD is outside the device). The design is in `docs/clinical-traceability.md`, the provenance of annotations in `docs/annotation-provenance.md`. For the regulatory file: `docs/regulatory/`, in particular TF-06 (risk management, including hazards H4, H12 and H15) and TF-09 (verification and validation).

## Key files

| File | Role |
| --- | --- |
| `backend/app/routers/families_reports.py` | Manifest, drift, clinical audit, sample QC, signing out |
| `backend/app/services/report_signout_service.py` | Signing out, snapshot, gates, hashes and chain |
| `backend/app/services/signed_record_sections.py` · `signed_record_nipt.py` · `signed_record_cnipt.py` | The sections of the record (descriptions, panels, reference data, repeats, Paraphase, coverage, intake, PGT, NIPT, conventional NIPT) |
| `backend/app/services/package_changed_after_import.py` | The package folder after the import (gate 3b) |
| `backend/app/services/report_code.py` · `backend/app/routers/report_codes.py` | The report code and the lookup |
| `backend/app/services/hash_chain.py` | Fixed JSON encoding, SHA-256, the chain and its check |
| `backend/app/services/clinical_audit_service.py` | The clinical audit trail |
| `backend/app/services/family_structure_audit.py` | The pedigree's structure versions, their chain and the `pedigree` entries |
| `backend/app/services/integrity_anchor_service.py` | Signed anchors and their check |
| `backend/app/services/classification_drift_service.py` | Drift |
| `backend/app/services/structural_variant_evidence.py` | The evidence of a CNV classification and its drift |
| `backend/app/services/annotation_manifest_service.py` | The annotation and reference manifest |
| `backend/app/services/clickhouse_integrity_monitor.py` | Monitoring of the variant store |
| `backend/db/schema/postgres/04_traceability.sql` · `05_grants.sql` | The tables, triggers and privileges |
| `frontend/src/pages/families/FamilyReportPage.tsx` · `FamilyNiptReportPage.tsx` | The report pages |
| `frontend/src/pages/families/reportSignOut.tsx` | The sign-out button and the gate dialogs |
| `frontend/src/pages/families/SignedFamilyReport.tsx` · `signedReportRecord.ts` | A signed version, drawn from its frozen snapshot |
| `frontend/src/pages/families/ReportClassification.tsx` · `reportNarrative.ts` | The class of a reported variant with the score next to it, live and signed |
| `frontend/src/lib/appVersion.ts` · `frontend/src/lib/deviceLabel.ts` | The running build and the label in the footers |
