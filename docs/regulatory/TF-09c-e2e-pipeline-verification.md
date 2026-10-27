# TF-09c — End-to-End Pipeline Verification (golden dataset)

| Field | Value |
| --- | --- |
| Document ID | TF-09c |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-29 |
| Parent | [TF-09 — Verification & Validation](TF-09-verification-validation.md) |
| Standards | IEC 62304 §5.6 (integration & integration testing), §5.7 (system testing); IVDR Annex I §16.1 (repeatability) |

> Controlled companion to [TF-09](TF-09-verification-validation.md). Defines the **system-level
> end-to-end verification** of CoGA: a small, hand-curated **golden dataset** with **documented
> expected results** is driven through the *real* pipeline — ingestion → ClickHouse/Postgres →
> query/API → clinical review/audit → signed report — against live Postgres and ClickHouse (the
> versions of [TF-08 §A.3](TF-08-soup-register.md)).
>
> It exercises the device boundary from [TF-02 §3](TF-02-device-description.md) — **annotated
> VCF (+ tracks) → closed, frozen (signed-out) analysis**; the clinical report drafted from it
> in the EPD is outside the device — as one deterministic, repeatable run, complementing
> the unit suite (mocked datastores) and the `smoke` integration tests. The dataset is fixed and
> its expected outputs are pinned, so a regression anywhere along the chain fails CI. It runs on
> synthetic data, so it does not replace the acceptance testing on real data that H11.1-OP5
> expects ([TF-09](TF-09-verification-validation.md)).

---

## 1. The controlled verification dataset

| Artefact | Path | Role |
| --- | --- | --- |
| Golden-trio package | `backend/tests/e2e/fixtures/golden_trio/` | A synthetic strict trio (FATHER unaffected, MOTHER unaffected, PROBAND affected) with one record of each clinically meaningful kind. |
| Expected results | `backend/tests/e2e/fixtures/golden_trio/EXPECTED.yaml` | Machine-readable ground truth — the acceptance values the run is checked against. |
| Generator | `scripts/generate_golden_trio.py` | Deterministically (re)builds the fixture **and** the expected-results file, so input and acceptance criteria are version-controlled together. |

The fixture is **synthetic** (no patient data) and intentionally tiny, so every expected value is
hand-derivable and auditable. The planted records and their expected outcomes:

| Kind | Planted record | Expected outcome |
| --- | --- | --- |
| Benign SNV | common variant (high gnomAD AF) | ingested, de-prioritised |
| Indel | frameshift deletion in one gene | ingested, typed INDEL |
| MNV | two-base substitution | ingested; typed INDEL by the family query, MNV by the Variant Explorer |
| Pathogenic SNV | nonsense variant | ingested; on review → ACMG **Likely Pathogenic (class 4)** from PVS1+PM2 |
| De novo SNV | proband het, both parents hom-ref | flagged de novo |
| Compound het (SNV+SNV) | two variants in one gene, trans across parents | paired as a compound-het group: `phase=trans`, `phase_evidence=segregation` |
| Compound het (SNV+SV) | maternal SNV + paternal DEL over the same gene | SV second-hit: `phase=trans`, `phase_evidence=segregation`, `deletion_unmasked=true` |
| Structural variants | a DEL and a BND | ingested with type/length/gene overlap; the BND with the mate its ALT names, found by a search on the mate's position; each stored under the id its coordinates and type give it, the BND's with its mate (`1-7000-12000-DEL---`, `1-20000-20001-BND-5-3000000-3000000`), not under its record ID (`SVDEL1`, `SVBND1`), which its annotation keeps |
| Repeat expansion | HTT (HD_HTT), 40-CAG allele | classified **pathogenic** |
| Coverage | per-sample BED tracks | interval-track rows registered |
| Paraphase | one segmental-duplication result | persisted |
| Phenotype | one HPO term | linked to the proband |
| NIPT (realistic) | `demo/nipt_family` | fetal fraction ≈ 12%, 40 paternal sites, all 8 monogenic categories recovered |

## 2. Verification scope & evidence

Each suite drives the real stack (Postgres and ClickHouse; a few, Postgres alone) and asserts
against `EXPECTED.yaml`, the golden trio's, or against synthetic data of its own, removed at
the end. Skipped unless `RUN_INTEGRATION=1`; executed by the CI **`e2e`** job (§4). Every suite
in `backend/tests/e2e/` is listed here.

| Stage verified | Evidence (test) |
| --- | --- |
| Package import → per-stage persistence (Postgres + ClickHouse) | [test_e2e_import_golden.py](../../backend/tests/e2e/test_e2e_import_golden.py) |
| Query/API contracts feeding the UI (small/SV pages, explorer, BED, haplotypes, track-availability) | [test_e2e_api_contract.py](../../backend/tests/e2e/test_e2e_api_contract.py) |
| Clinical review → ACMG recompute, immutable audit chain, signed report | [test_e2e_review_audit.py](../../backend/tests/e2e/test_e2e_review_audit.py) |
| Failure/degradation handling + job lifecycle (fail-clean) | [test_e2e_failure_modes.py](../../backend/tests/e2e/test_e2e_failure_modes.py) |
| A partly failed import's flag stays through an import that completes without the failed dataset, keeps an earlier failure when a later import fails too (each with its job), and goes once the failed datasets are imported again (TF-06 H16) | [test_e2e_import_incomplete_until_reimported.py](../../backend/tests/e2e/test_e2e_import_incomplete_until_reimported.py) |
| An import whose process stops part-way (inside a dataset, between datasets, or in an overwrite of an existing family): the family stays marked and gates sign-out, the stopped job is ended rather than run again and an overwrite's backup of the family dropped with it, only an overwrite clears the mark (TF-06 H16) | [test_e2e_import_crash_leaves_family_marked.py](../../backend/tests/e2e/test_e2e_import_crash_leaves_family_marked.py) |
| A live import whose heartbeat goes stale and whose job another worker ends: its heartbeat stops it where it is, leaving the family marked and gating sign-out; an overwrite whose backup was dropped meanwhile deletes nothing when it restores, and its flag names as imported only what is there (TF-06 H16, #746) | [test_e2e_import_stale_heartbeat.py](../../backend/tests/e2e/test_e2e_import_stale_heartbeat.py) |
| An import stops itself before another write can start: one whose heartbeat cannot reach the database stops where it is before a worker can take its job for stopped, leaving the family marked; an overwrite whose variant-write locks' connection is ended stops where it is, restores nothing, ends its job failed with the reason, drops its backups and leaves the family marked and gating sign-out (TF-06 H16, SAFE-15) | [test_e2e_import_stops_itself.py](../../backend/tests/e2e/test_e2e_import_stops_itself.py) |
| A failed overwrite whose restore fails part-way, having deleted the family's small variants: its flag names every dataset of the import as failed, its entry stays, and the family is refused for sign-out until each has been imported again, an import of the dataset that had failed alone not sufficing; one whose restore deleted the coverage the overwrite did not carry: the family is refused for sign-out until that has been imported again with overwrite too, an import of the datasets that failed not sufficing; an update after such a restore imports again the coverage it removed, which Postgres still recorded, and skips the coverage it left, which stays flagged; one whose restore deleted SV calls a single-file upload loaded, which the family records: the family is refused for sign-out until those have been uploaded again with overwrite, an import of the package again not sufficing; one whose restore deleted the direct calls an import stored with its `source_format` left at `auto`: the family is refused for sign-out until those have been imported again, an import of imputed genotypes (`auto` too) not sufficing (TF-06 H16) | [test_e2e_import_restore_fails_part_way.py](../../backend/tests/e2e/test_e2e_import_restore_fails_part_way.py) |
| An SNV dataset whose `source_format` is left at `auto` reads the callset its file holds: an update of imputed genotypes is imported beside the family's direct calls, and skipped once the family holds them; a failed import of imputed genotypes whose overwrite had no backup removes them alone, the direct calls staying; the flag keeps each callset that failed, each with its job, so the direct calls imported again leave the family refused for sign-out, naming the job of the imputed genotypes, until those have been imported again too (TF-06 H16; SAFE-27, SAFE-26) | [test_e2e_import_auto_snv_callsets.py](../../backend/tests/e2e/test_e2e_import_auto_snv_callsets.py) |
| Realistic demo bundles through their real ingestion paths | [test_e2e_demo_smoke.py](../../backend/tests/e2e/test_e2e_demo_smoke.py) |
| Haplotype / lineage stage against the real stack (PGT segregation) | [test_e2e_haplotypes.py](../../backend/tests/e2e/test_e2e_haplotypes.py) |
| Prioritised-ranking cache invalidated by a variant-data change on a non-import path | [test_e2e_ranking_cache.py](../../backend/tests/e2e/test_e2e_ranking_cache.py) |
| A narrower panel served from a broader cached ranking only while that ranking's panel holds the genes it was computed on: not after an edit adds a gene, nor from a row without that record or whose panel was deleted (CLIN-27); and only as the panel computed for itself ranks it: numbered within the panel, not when it splits a compound-het pair or reaches a variant the broader ranking lacks (CLIN-34, CLIN-35); a Mendeliome stand-in of more than 1,000 regions, matched by position too, serves a panel that reaches a variant through a gene's locus, and a PanelApp panel whose coordinates for a gene reach further is computed for itself | [test_e2e_ranking_cache_sub_panel.py](../../backend/tests/e2e/test_e2e_ranking_cache_sub_panel.py) |
| The search ranked by phenotype priority keeps what it keeps unranked: each inheritance mode, expanded carrier screening (the trio's parents as the couple) and both together keep the same variants, compound-het pairs (with their phase and its evidence) and total, ranked or not and served from the cache; the ranks number the kept variants only, the GENE_CH pair listed at its better-ranked variant, and the CSV export holds the same variants with their ranks (CLIN-44). The long-read couple's ranked screen keeps the same three findings ([test_e2e_long_read_couple_screen.py](../../backend/tests/e2e/test_e2e_long_read_couple_screen.py)) | [test_e2e_prioritised_search_keeps_pairing_rules.py](../../backend/tests/e2e/test_e2e_prioritised_search_keeps_pairing_rules.py) |
| SV second-hit index rebuilt after SV writes on non-import paths (admin delete, per-sample upload); each SV phased on its own, so a parent's own SV of another type in the gene leaves the deletion in trans biallelic, not cis (CLIN-28) | [test_e2e_sv_second_hit_index.py](../../backend/tests/e2e/test_e2e_sv_second_hit_index.py) |
| The SV ranking scores an SV by the copy-number change it makes, by the rule of the SV second hit: a DEL:ME and a CNV whose stored copy number shows a loss as deletions, a DUP:TANDEM and a CNV gain as duplications, and a male's chrX CNV that keeps his one copy outside the PARs as neither (CLIN-47); the list, and each member's genome track from the member's own calls, serve each SV with that change, which the SV tracks and the Circos plot draw it by (CLIN-54) | [test_e2e_sv_ranking_copy_change.py](../../backend/tests/e2e/test_e2e_sv_ranking_copy_change.py) |
| A gene panel of any size keeps an SV whose annotation names one of its genes or that overlaps one of its regions (owner decision 2026-10-20): a panel of two genes and one of more than 1,000 regions, as the Mendeliome is, keep the same SVs of a HiFiCNV trio on the SV list, ranked or not, its CSV export, each member's genome track and the track availability; an SV beside a gene that its annotation names, one in a gene's locus named otherwise and one on a locus's last base; not one a base beyond, nor one stored with its END before its POS over two loci that no locus holds | [test_e2e_sv_panel_any_size.py](../../backend/tests/e2e/test_e2e_sv_panel_any_size.py) |
| The chromosome view offers a member's small-variant track under a gene panel exactly when the small-variant list shows them a variant under it (CLIN-62): with a panel of two genes and one of 5,300 genes, as the Mendeliome has, in a window and over the whole chromosome, the track availability, each member's track and the list agree on a trio whose father's only variant is named by a panel gene 3 kb outside its locus and whose mother's lies in a gene's locus under another symbol; a panel that keeps nothing offers no member a track | [test_e2e_track_availability_panel.py](../../backend/tests/e2e/test_e2e_track_availability_panel.py) |
| A trio's per-sample NeedlR files whose record IDs repeat across the files: each call an SV of its own position with its own sample's call, one event in two samples' files one SV with both calls (the mother's own, not the no-call the proband's line gives her), the record IDs kept in the annotations (CLIN-43) | [test_e2e_needlr_coordinate_ids.py](../../backend/tests/e2e/test_e2e_needlr_coordinate_ids.py) |
| A deletion a NeedlR package and a Sniffles upload both call, under one id: each caller's row listed with its own genes, pLI and inheritance; a CNV classification saved on either row freezes that row's evidence, and the drift check compares it with that row (a change to the other caller's call does not drift it, one to its own does); a save naming no caller is refused (422) and writes nothing, one naming a caller that does not call the SV is refused (404), and one naming no caller of an SV one caller calls takes that row (TRACE-11) | [test_e2e_cnv_evidence_own_caller.py](../../backend/tests/e2e/test_e2e_cnv_evidence_own_caller.py) |
| A HiFiCNV depth file without a usable autosomal median is stored as read, and its track's record says so (`normalization: none`) where it said `log2_ratio_to_autosomal_median`; the track availability serves each coverage track's recorded transform, which the viewers read before drawing a track on their log2-ratio axis (TRACE-7) | [test_e2e_depth_track_transform.py](../../backend/tests/e2e/test_e2e_depth_track_transform.py) |
| Each track loader replaces only its own source's track of a sample, rows and records: a package's coverage BED keeps the WisecondorX and QDNAseq bins imported before it, coverage and APCAD imported again keep HiFiCNV's depth and minor allele fraction, an invalid BED uploaded with overwrite is refused with every track and its record as they were, and a BED upload is not refused for another caller's track; the track availability lists every caller's track (SAFE-22) | [test_e2e_track_loaders_keep_other_sources.py](../../backend/tests/e2e/test_e2e_track_loaders_keep_other_sources.py) |
| A small-variant upload with overwrite reads and checks the whole file before it replaces the callset: a file naming a sample the family lacks, with no record it can store, or cut short as gzip is refused with both callsets, the haplotype blocks and their records as they were; a valid one replaces them; a write that fails after the delete leaves the family flagged import-incomplete for the callset, as its page reads it, until the callset is uploaded again. A PED upload with overwrite that is refused leaves the family's variants; one that succeeds replaces them (SAFE-25, TF-06 H16) | [test_e2e_small_variant_upload_reads_before_replacing.py](../../backend/tests/e2e/test_e2e_small_variant_upload_reads_before_replacing.py) |
| A structural-variant upload whose rewrite fails after its delete puts every sample's calls back; one that cannot put them back leaves the family marked for each sample's calls (`import_unfinished`, `upload-…`), which every family page reads and which refuses sign-out until each sample's file is uploaded again with overwrite; the admin delete of a sample's SVs puts the family's SVs back when its rewrite fails; a small-variant upload whose process stops after its delete leaves the family marked until the callset is uploaded again; a BED upload marks and unmarks the family under its own locks; a structure change that clears the data and then fails leaves the family's variants (SAFE-28, TF-06 H16) | [test_e2e_writes_mark_before_delete.py](../../backend/tests/e2e/test_e2e_writes_mark_before_delete.py) |
| The admin's deletion of a member with its data whose SV rewrite fails after the rewrite of its small variants answers 500, puts the SVs back, and leaves the family marked (`import_unfinished`, `deletion-…`) for exactly the member's data it removed (its coverage track and its part of the direct calls), which every family page reads and which refuses sign-out, naming the deletion; deleting the member again completes and its items go with it; an SNV overwrite whose backup could not be taken and whose VEP table the loader refuses fails with the family's direct calls kept and the SNV dataset flagged (SAFE-31, SAFE-32, TF-06 H16) | [test_e2e_deletions_mark_the_family.py](../../backend/tests/e2e/test_e2e_deletions_mark_the_family.py) |
| The admin's deletions of a member and of a family whose commit Postgres refuses answer 500 and keep every uploaded file their records name, and unlink those files only once a deletion has committed; a family replaced by a PED upload with overwrite loses its interval tracks in ClickHouse and Postgres; deleting a member strikes it off the import-incomplete flag and off a stopped import's entry, so that uploading again what is left of the others clears the flag and the report signs out past the import gate (SAFE-35, TF-06 H16) | [test_e2e_deletions_files_tracks_and_flags.py](../../backend/tests/e2e/test_e2e_deletions_files_tracks_and_flags.py) |
| A file without a record replaces nothing: an overwrite import whose WisecondorX bins file holds its header alone fails, the dataset failed and the bins kept, rows and record; a TRGT VCF with its header alone uploaded with overwrite is refused (400) with the sample's call kept; an update whose QDNAseq bins file holds its header alone fails the dataset and flags the family import-incomplete for it (SAFE-29); a chrM VCF with its header alone fails the `mito` dataset before any sample's calls are replaced, every call kept, and flags a family the import keeps for `mito` (the owner's decision) | [test_e2e_loaders_refuse_empty_files.py](../../backend/tests/e2e/test_e2e_loaders_refuse_empty_files.py), [test_e2e_mito_import_keeps_every_sample.py](../../backend/tests/e2e/test_e2e_mito_import_keeps_every_sample.py) |
| The readers that take coverage as read depth read one track of read depth per sample, never a log2 ratio: the mtDNA page reads a member's coverage BED beside WisecondorX's and QDNAseq's bins and HiFiCNV's log2 ratio, HiFiCNV's depths stored as read beside QDNAseq's bins, and for a member with bins only her calls' depth, and its configured `mtdna.mean_depth` limits judge those depths (each failed on a blend before); the NIPT coverage check without a per-target table reads the plasma's coverage BED beside WisecondorX's bins, its median and its uncovered stretch (CLIN-46); each BED declared as read depth, and each track left out named with the reason (TRACE-9) | [test_e2e_coverage_readers_read_depth.py](../../backend/tests/e2e/test_e2e_coverage_readers_read_depth.py) |
| Each coverage BED declares what it holds: a package's BED declared as read depth and one declaring nothing are recorded as `read_depth` and `unknown` (records and what the family holds), the track availability serves them, and the mtDNA coverage reads the first and, for the second, the member's calls' depth with a note why; a BED upload without `content` is refused (422) with nothing replaced, one declared as a log2 ratio is kept off the depth readers, and one declared as read depth is read as depth, the raw file's record saying each (TRACE-9) | [test_e2e_coverage_bed_declares_content.py](../../backend/tests/e2e/test_e2e_coverage_bed_declares_content.py) |
| The alignment endpoints serve a sample's reads only from its own family's folders (SEC-20) | [test_e2e_alignment_lookup_family_folders.py](../../backend/tests/e2e/test_e2e_alignment_lookup_family_folders.py) |
| Callsets and SV callers stored side by side stay apart: a GLIMPSE2 callset beside the direct calls, and a Sniffles and a Spectre call of one deletion, each keep their rows after a part merge, and each view reads the callset it names | [test_e2e_callset_storage_identity.py](../../backend/tests/e2e/test_e2e_callset_storage_identity.py) |
| A case is assigned to, and recorded as reviewed by, only someone who may work on it (SEC-1) | [test_e2e_case_assignee_eligibility.py](../../backend/tests/e2e/test_e2e_case_assignee_eligibility.py) |
| The ClickHouse integrity check reads every active part of each table after the golden-trio import and reports the assembly `ok`, an empty assembly included | [test_e2e_clickhouse_integrity.py](../../backend/tests/e2e/test_e2e_clickhouse_integrity.py) |
| Small-variant and interval rows go into the tables of an assembly whose dataset key has a dot (`T2T_CHM13v2.0`) | [test_e2e_dotted_assembly_insert.py](../../backend/tests/e2e/test_e2e_dotted_assembly_insert.py) |
| The Variant Explorer and the family page keep the same variants for the same filters (CLIN-29) | [test_e2e_explorer_filters_match_family_page.py](../../backend/tests/e2e/test_e2e_explorer_filters_match_family_page.py) |
| The Variant Explorer and the family page keep the same variants for a gene panel (CLIN-48) | [test_e2e_explorer_panel_matches_family_page.py](../../backend/tests/e2e/test_e2e_explorer_panel_matches_family_page.py) |
| The family search keeps what it kept for a gene panel's regions on every path, with the regions joined on windows (ENG-15) | [test_e2e_family_panel_region_join.py](../../backend/tests/e2e/test_e2e_family_panel_region_join.py) |
| Concurrent writes of one family's variants (uploads, an admin delete) keep each other's changes: one write at a time per family | [test_e2e_family_variant_writes_serialized.py](../../backend/tests/e2e/test_e2e_family_variant_writes_serialized.py) |
| A variant kept on another transcript than the one its fields describe names that transcript, in every view (CLIN-51) | [test_e2e_filter_transcript.py](../../backend/tests/e2e/test_e2e_filter_transcript.py) |
| A gnomAD count filter reads gnomAD's counts, never the callset's own (CLIN-59) | [test_e2e_gnomad_counts_not_the_callsets.py](../../backend/tests/e2e/test_e2e_gnomad_counts_not_the_callsets.py) |
| An import job records its family before the import writes it, so a sign-out finds it; an import whose job cannot record it writes nothing | [test_e2e_import_job_names_its_family_first.py](../../backend/tests/e2e/test_e2e_import_job_names_its_family_first.py) |
| A package whose manifest is a symlink to one outside the import root, or names a file under an unknown user's home, is refused by Discover, Validate and the import, which writes no family | [test_e2e_import_package_paths.py](../../backend/tests/e2e/test_e2e_import_package_paths.py) |
| Discover, Validate and the import refuse a family or sample ID no record can be stored under, before anything is written | [test_e2e_import_refuses_unstorable_ids.py](../../backend/tests/e2e/test_e2e_import_refuses_unstorable_ids.py) |
| The phenotypes an import or a PED upload changes are on the family's clinical audit trail (SEC-1) | [test_e2e_imported_phenotypes_audited.py](../../backend/tests/e2e/test_e2e_imported_phenotypes_audited.py) |
| A SIFT or PolyPhen filter keeps the same variants in every view (CLIN-45) | [test_e2e_in_silico_prediction_filters.py](../../backend/tests/e2e/test_e2e_in_silico_prediction_filters.py) |
| A family, sample or project outside the caller's projects answers like an unknown one (404) | [test_e2e_inaccessible_records_look_unknown.py](../../backend/tests/e2e/test_e2e_inaccessible_records_look_unknown.py) |
| Member and structure edits refuse an ID no sample can be stored under, before anything is written | [test_e2e_member_edit_refuses_unstorable_ids.py](../../backend/tests/e2e/test_e2e_member_edit_refuses_unstorable_ids.py) |
| Microsoft sign-in: a first sign-in makes one viewer's account in no project; the emergency administrator's and the admin roles' limits hold (SEC-4) | [test_e2e_microsoft_sign_in.py](../../backend/tests/e2e/test_e2e_microsoft_sign_in.py) |
| The monogenic NIPT de novo view's count of other families' cfDNA carriers reads only real calls, before and after a part merge | [test_e2e_nipt_cfdna_carriers.py](../../backend/tests/e2e/test_e2e_nipt_cfdna_carriers.py) |
| A monogenic NIPT family's signed record freezes the fetal-fraction and fetal-sex verdicts, its reported variants and its gates | [test_e2e_nipt_signed_record.py](../../backend/tests/e2e/test_e2e_nipt_signed_record.py) |
| Haplotype blocks built for another pedigree call no embryo: current after the import and after an affected-status edit, outdated after a sex correction, every embryo uninformative, the haplotype tracks told so, and the signed record holding the flag (CLIN-66) | [test_e2e_outdated_haplotype_blocks.py](../../backend/tests/e2e/test_e2e_outdated_haplotype_blocks.py) |
| Each member's per-sample TRGT, chrM and HiFiCNV files are read from their own sample's column; another member's file is refused (REQ-DATA-007) | [test_e2e_per_sample_column.py](../../backend/tests/e2e/test_e2e_per_sample_column.py) |
| An X-linked PGT family's haplotype blocks and embryo calls, with the men's hemizygous chrX outside PAR1 | [test_e2e_pgt_x_linked_blocks.py](../../backend/tests/e2e/test_e2e_pgt_x_linked_blocks.py) |
| A reader in a project reads its records and changes none; an analyst changes them (SEC-1) | [test_e2e_project_reader_role.py](../../backend/tests/e2e/test_e2e_project_reader_role.py) |
| Raw-file download, Verify, the file list and the deletes keep to CoGA's own folders (SEC-29) | [test_e2e_raw_file_paths_contained.py](../../backend/tests/e2e/test_e2e_raw_file_paths_contained.py) |
| Signal tracks and QC reports are served only from their own family's folders (SEC-25) | [test_e2e_recorded_path_lookup_family_folders.py](../../backend/tests/e2e/test_e2e_recorded_path_lookup_family_folders.py) |
| The report's lists of reported variants are complete, whatever the search's candidate cap (#730) | [test_e2e_report_lists_complete.py](../../backend/tests/e2e/test_e2e_report_lists_complete.py) |
| A request is audited whatever characters it carries (a NUL, half a surrogate pair, `NaN`) | [test_e2e_request_audit_unstorable_characters.py](../../backend/tests/e2e/test_e2e_request_audit_unstorable_characters.py) |
| A request body's `assembly_id` or `project_id` that is no UUID, or names no record, is answered 404 with nothing written; each spelling of a real id names the record | [test_e2e_request_body_uuid.py](../../backend/tests/e2e/test_e2e_request_body_uuid.py) |
| A control character in a request's path or query string is refused (400) at sign-in, before the request runs | [test_e2e_request_control_characters.py](../../backend/tests/e2e/test_e2e_request_control_characters.py) |
| On 24 routes a path or query value that is no UUID is answered as a UUID naming no record, never 500, and each spelling of a real id (braces, `urn:uuid:`, capitals, no hyphens) reads it | [test_e2e_request_malformed_uuid.py](../../backend/tests/e2e/test_e2e_request_malformed_uuid.py) |
| A request that fails leaves none of its values in the application log | [test_e2e_server_error_log_without_values.py](../../backend/tests/e2e/test_e2e_server_error_log_without_values.py) |
| The signed analysis record holds each reported variant's description, the panel versions, the reference-data releases and the HPO release, the repeat and Paraphase results; it verifies, chains and has its report code | [test_e2e_signed_analysis_record.py](../../backend/tests/e2e/test_e2e_signed_analysis_record.py) |
| The signed record's coverage tracks: what each sample's coverage BED declared it holds, and which loader brought it | [test_e2e_signed_coverage_tracks.py](../../backend/tests/e2e/test_e2e_signed_coverage_tracks.py) |
| A PGT family's signed embryo calls (REG-2) | [test_e2e_signed_pgt_analysis.py](../../backend/tests/e2e/test_e2e_signed_pgt_analysis.py) |
| The signed repeat calls' near-boundary warnings at a locus whose counting units are not validated | [test_e2e_signed_repeat_count_units.py](../../backend/tests/e2e/test_e2e_signed_repeat_count_units.py) |
| A sign-out is refused while an import of the family is queued or running, or another writer holds its variant-write locks (#727, TF-06 H16) | [test_e2e_signout_during_import.py](../../backend/tests/e2e/test_e2e_signout_during_import.py) |
| A whole-sample delete writes every other small-variant row back exactly as stored | [test_e2e_small_variant_delete_keeps_every_call.py](../../backend/tests/e2e/test_e2e_small_variant_delete_keeps_every_call.py) |
| The family small-variant upload answers with its whole result, and refuses a repeat without overwrite (409) and an unknown format (422) | [test_e2e_small_variant_upload_response.py](../../backend/tests/e2e/test_e2e_small_variant_upload_response.py) |
| An SV filter preset the SV page saves is stored, family-scoped or global | [test_e2e_sv_filter_presets_are_saved.py](../../backend/tests/e2e/test_e2e_sv_filter_presets_are_saved.py) |
| The SV length filters read each SV's length as its caller wrote it (CLIN-25) | [test_e2e_sv_length_filter.py](../../backend/tests/e2e/test_e2e_sv_length_filter.py) |
| A per-sample SV upload and the admin SV delete write every other call back as stored, an inactive member's included | [test_e2e_sv_rewrite_keeps_every_call.py](../../backend/tests/e2e/test_e2e_sv_rewrite_keeps_every_call.py) |
| A source-scoped SV delete or overwrite leaves no entries, details or lookup row of that source, and every other source's and family's rows as stored | [test_e2e_sv_source_delete_leaves_no_orphans.py](../../backend/tests/e2e/test_e2e_sv_source_delete_leaves_no_orphans.py) |
| A per-sample SV upload of a joint VCF stores its sample's own column; another member's file is refused before anything is written (REQ-DATA-007) | [test_e2e_sv_upload_sample_column.py](../../backend/tests/e2e/test_e2e_sv_upload_sample_column.py) |
| A per-sample SV upload replaces only its own source's rows of the family (409 without overwrite) | [test_e2e_sv_upload_source_scope.py](../../backend/tests/e2e/test_e2e_sv_upload_source_scope.py) |
| SV records written by VCF 4.4 (no END or SVTYPE, breakends) are stored with their extent and type (CLIN-33, CLIN-37) | [test_e2e_sv_upload_vcf44_records.py](../../backend/tests/e2e/test_e2e_sv_upload_vcf44_records.py) |
| A UI event with an impossible `occurred_at` is stored with its receive time; no event of its batch is lost | [test_e2e_ui_event_timestamp_bounds.py](../../backend/tests/e2e/test_e2e_ui_event_timestamp_bounds.py) |
| A value beyond its ClickHouse column is stored as missing or the record skipped, never failing the upload or import (SAFE-30) | [test_e2e_values_beyond_columns.py](../../backend/tests/e2e/test_e2e_values_beyond_columns.py) |
| A variant tag edit is saved, a project-scoped tag shared with another project included | [test_e2e_variant_tag_edits_are_saved.py](../../backend/tests/e2e/test_e2e_variant_tag_edits_are_saved.py) |
| A renamed or deleted variant tag stays on the reviews that hold it | [test_e2e_variant_tag_renames_keep_reviews.py](../../backend/tests/e2e/test_e2e_variant_tag_renames_keep_reviews.py) |

A consolidated catalogue of these is in [docs/testing.md](../testing.md) ("End-to-end (golden pipeline)").

## 3. Acceptance criteria

The verification **passes** when, on clean datastores:

- the golden-trio package imports with every enabled dataset `imported`;
- every per-stage assertion in §2 matches `EXPECTED.yaml`;
- a clinical review round-trips with **server-recomputed** ACMG (client-supplied totals ignored),
  appends a hash-chained audit, and the chain re-verifies (`verified=true`); a trigger-bypass
  edit is detected (tamper-**evident**) and the audit table rejects UPDATE/DELETE (append-only);
- a report signs out, increments version, and its chain verifies;
- a malformed/failed import is reported **failed** and leaves **no partial family** (fail-clean);
- a family left partly loaded by a failed import stays **flagged** until its failed datasets
  are imported again, a later failure keeping the earlier one, and the sign-out refuses it
  without an acknowledgement meanwhile;
- an import whose process stops part-way leaves its family **marked**, naming the datasets it had
  not finished, and the sign-out refuses it without an acknowledgement; its job is ended
  `failed` with its log kept, not run again, and an overwrite's backup tables are dropped; an
  update does not clear the mark, an overwrite does;
- the NIPT bundle recovers fetal fraction ≈ 0.12 and all 8 monogenic categories.

Any deviation is a verification finding handled per [TF-09 §5](TF-09-verification-validation.md)
(anomaly handling) and, if clinically relevant, TF-17 vigilance/CAPA.

## 4. How it runs (gate)

- **Locally:** bring up the datastores (`docker compose up -d postgres clickhouse`) and run
  `RUN_INTEGRATION=1 python -m pytest backend/tests/e2e`.
- **CI:** the **`e2e`** job in `.github/workflows/ci.yml` provisions the Postgres and ClickHouse
  images and runs the suite on every PR and on push to `main`. Like `smoke`, it runs in its own
  job, and its coverage feeds the combined `coverage` job. It is a **required status check**
  ([TF-18 §6](TF-18-change-configuration-management.md)), so a failing golden-trio run blocks
  the merge.

## 5. Reproducibility

The fixture, expected results, and assertions are version-controlled and deterministic: the same
commit yields the same pass/fail verdict. This operationalises the IVDR Annex I §16.1
repeatability requirement at the pipeline level, above the per-report content-hash reproducibility noted in
[TF-09 §2](TF-09-verification-validation.md).

## 6. Mapping to the CMGG report form (H11.1-F12.2)

This document is evidence for the validation axes of the bio-IT ingangsvalidatie; the mapping
is in [TF-09 §7](TF-09-verification-validation.md).

## 7. Limitations / out of scope

- **Synthetic data only** — analytical/clinical accuracy on real material is the concordance study
  in [TF-10](TF-10-performance-evaluation-plan.md)/[TF-11](TF-11-performance-evaluation-report.md),
  not this document.
- **Browser/UI** — covered by [TF-09d](TF-09d-browser-e2e-verification.md), with its limits in its §10.
- **Tamper-evident, not tamper-proof** — the in-DB chain detects edits/reordering by a privileged
  bypass but not a determined owner who re-chains; that residual is the external signed integrity
  anchor (see [clinical-traceability.md](../clinical-traceability.md)).
