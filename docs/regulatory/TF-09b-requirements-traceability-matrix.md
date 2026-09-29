# TF-09b — Requirements Traceability Matrix (RTM)

| Field | Value |
| --- | --- |
| Document ID | TF-09b |
| Version | v0.1 DRAFT |
| Status | Draft for internal review |
| Owner | ‹CMGG software lead› |
| Approver | ‹Lab director› |
| Date | 2026-06-25 |
| Traces | [TF-09a SRS](TF-09a-software-requirements-specification.md) → design → implementation → test → risk ([TF-06](TF-06-risk-management-plan.md)) |

> Forward and backward traceability per IEC 62304 §5.1.6 / IVDR Annex I §16. Each requirement
> from the [SRS](TF-09a-software-requirements-specification.md) maps to its implementation and
> its verifying test(s). Backend code is under `backend/app/` (`services/`, `routers/`); backend
> tests under `backend/tests/`; frontend under `frontend/src/`. This matrix was
> built from a point-in-time code/test inventory and is maintained under change control
> ([TF-18](TF-18-change-configuration-management.md)).

**Status:** ✅ implemented + directly verified by a passing test · ◐ implemented, indirect/partial
verification or clinical validation pending (TF-10) · ⚠ verification gap (no direct test) — see §3.

---

## 1. Traceability table

### Monogenic NIPT
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-NIPT-001 | `services/nipt_analysis.py::estimate_fetal_fraction`, `::filter_sites_and_estimate_ff` | `test_nipt_analysis.py::test_fetal_fraction_recovery`; `test_nipt_service.py::test_the_variant_list_reports_the_summary_fetal_fraction` | H6 | ✅ |
| REQ-NIPT-002 | `services/nipt_analysis.py::classify_site`; `services/nipt_service.py::derive_father_state` | `test_nipt_analysis.py::test_category_*`, `::test_classification_never_reports_a_state_the_father_rules_out`, `::test_a_father_call_below_min_father_dp_is_treated_as_no_call`; `test_nipt_service.py::test_derive_father_state*` | H6 | ✅ |
| REQ-NIPT-003 | `services/nipt_analysis.py` | `test_nipt_analysis.py::test_ff_too_low_suppresses_fetal_inheritance` | H6 | ✅ |
| REQ-NIPT-004 | `services/nipt_artifact_pg.py`, `services/nipt_service.py`; `services/clickhouse_family_variants.py::fetch_recurrent_small_variant_ids`; `services/variant_prioritization.py::clinvar_may_assert_pathogenic` | `test_nipt_artifact_pg.py`; `test_nipt_analysis.py::test_run_nipt_analysis_filter_counts`; `test_variant_prioritization.py::test_clinvar_may_assert_pathogenic`; `integration/test_nipt_artifact_seed_integration.py` | H1 | ✅ |
| REQ-NIPT-005 | `services/nipt_analysis.py::estimate_fetal_fraction` (external-FF cross-check) | `test_nipt_analysis.py::test_external_ff_*` (agreement / flagged disagreement / prefer-external) | H6 | ✅ |
| REQ-NIPT-006 | `services/nipt_coverage.py::summarize_on_target_coverage` | `test_nipt_coverage.py` (weighted median, low-coverage flags) | H1 | ✅ |
| REQ-NIPT-007 | `services/nipt_service.py`; `routers/families_nipt.py` (`/nipt/variants`) | `test_nipt_service.py` (presets); `test_nipt_end_to_end.py::test_nipt_demo_recessive_at_risk` | — | ✅ |
| REQ-NIPT-008 | `services/nipt_analysis.py` (category 8) | `test_nipt_analysis.py` (absence/category logic) | H6 | ✅ |

### Expanded carrier screening (BeGECS)
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-CARR-001 | small-variant query + panel filter (`services/clickhouse_family_variants.py`, `panel_metadata_service.py`) | `test_panel_filter_constraints.py`; `test_gene_panel_versions.py` | H1,H12 | ◐ |
| REQ-CARR-002 | recessive/compound-het logic (`services/clickhouse_family_variants.py::get_family_compound_het_candidates`) | (no couple-level test) — clinical validation TF-10 (50 couples) | H2 | ⚠ |
| REQ-CARR-003 | `services/panel_metadata_service.py` | `test_panelapp_service.py`; `test_gene_panel_versions.py` | H8,H12 | ✅ |
| REQ-CARR-004 | `services/panel_metadata_service.py::_resolve_gene_regions`, `_replace_panel_members`, `import_panelapp_panel_data`; `services/clickhouse_family_variants.py::_fetch_panel_constraints`; `gene_panel_regions` (`02_reference.sql`, incl. the in-place upgrade) | `test_panel_region_assembly_scope.py`; `test_panel_filter_constraints.py`; `integration/test_panel_regions_per_assembly.py` (real Postgres: per-assembly storage and filter, upgrade of an older table); `GenePanelDetailPage.test.tsx`, `GenePanelsPage.test.tsx` | H12 | ✅ |

### PGT / haplotype segregation
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-PGT-001 | `services/haplotype_lineage_service.py::annotate_lineage` | `test_haplotype_lineage_service.py` (grey/placement) | H5 | ✅ |
| REQ-PGT-002 | `services/haplotype_lineage_service.py` (segment smoothing) | `test_haplotype_lineage_service.py`; `test_phased_marker_service.py` (recombination) | H5 | ✅ |
| REQ-PGT-003 | `services/phased_marker_service.py::compute_phased_markers` | `test_phased_marker_service.py` | H5 | ✅ |
| REQ-PGT-004 | `services/phased_marker_service.py` (QC) | `test_phased_marker_service.py::test_qc_counts_informative_sites_and_mendel_errors` | H4 | ✅ |
| REQ-PGT-005 | `frontend/src/lib/haplotypeRisk.ts`; `HaplotypePhasedTrack.tsx` | `haplotypeRisk.test.ts` (dominant/recessive/X-linked + uninformative incl. one-side donor); `HaplotypePhasedTrack.test.tsx` | H5 | ✅ |
| REQ-PGT-006 | `services/haplotype_lineage_service.py`, `phased_marker_service.py` (single-parent) | `test_haplotype_lineage_service.py`; `test_phased_marker_service.py` (single-parent mode) | H5 | ✅ |
| REQ-PGT-007 | `services/clickhouse_family_variants.py::get_family_structural_variants_page`; `sv_gene_index_service.py` | `test_sv_gene_index.py`; `test_structural_variant_track_slim.py` — >10 Mb claim → TF-10 | H7 | ◐ |
| REQ-PGT-008 | SV/segment interval tracks (`clickhouse_interval_tracks.py`, SV catalog) | — (aneuploidy) clinical validation TF-10 (100 embryos) | H7 | ⚠ |
| REQ-PGT-009 | `services/clickhouse_family_variants.py` (genotype at locus) | `test_clickhouse_family_variants.py` | H1 | ✅ |
| REQ-PGT-010 | `services/bed_service.py::precompute_family_lineage_safe` | `test_bed_service_lineage_precompute.py` (staleness guard) | H5,H8 | ✅ |

### Rare-disorder diagnostics
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-DIAG-001 | `services/clickhouse_variant_queries.py` (inheritance matchers, e.g. `_record_matches_de_novo`), used by `clickhouse_family_variants.py` | `test_clickhouse_family_variants.py`; `test_de_novo_detection.py`; `test_hemizygous_de_novo.py` (de novo / dominant for a hemizygous male) | H2 | ✅ |
| REQ-DIAG-002 | `services/clickhouse_variant_queries.py::_record_matches_de_novo`, `services/sex_chromosomes.py` (de-novo; hemizygous chrX/chrY in a son) | `test_de_novo_detection.py::test_not_de_novo_without_full_trio`; `test_hemizygous_de_novo.py`; `test_sex_chromosomes.py`; `integration/test_hemizygous_positions_clickhouse.py` | H1, H2 | ✅ |
| REQ-DIAG-003 | `services/sv_gene_index_service.py::get_sv_second_hits`, `summarize_second_hit`; `services/compound_het_phase.py::segregation_phase` (cis/trans from the family, shared by SNV + SNV pairs and the SV second hit); `services/clickhouse_variant_queries.py::_compound_het_pair_verdict`; the SV data version in `services/clickhouse_variant_storage.py` (the SV→gene index is rebuilt after any SV write) | `test_sv_gene_index.py`; `test_compound_het_segregation_phase.py` (both paths reach the same verdict); `test_compound_het_phasing.py`; `test_clickhouse_variant_storage.py` (SV data version); `test_clickhouse_family_variants.py` (compound-het); `e2e/test_e2e_sv_second_hit_index.py`; `SvSecondHitBadge.test.tsx`; `SmallVariantPairCards.test.tsx` | H2 | ✅ |
| REQ-DIAG-004 | `services/repeat_expansion_pg.py::ingest_trgt_text`, `classify_repeat_count` | `test_repeat_expansion_pg.py` | — | ✅ |
| REQ-DIAG-005 | `services/paraphase_pg.py` | `test_paraphase_pg.py` (SMN metrics, regions) | — | ✅ |
| REQ-DIAG-006 | `services/mitochondrial_analysis.py` | `test_mitochondrial_analysis.py` (maternal transmission from the mother the pedigree links to the proband, heteroplasmy, ClinVar status) | H13 | ✅ |
| REQ-DIAG-007 | `services/variant_prioritization*.py` | `test_variant_prioritization.py` | H1 | ✅ |
| REQ-DIAG-008 | `services/monarch_semsim.py`, `hpo_service.py` | `test_monarch_semsim.py`; `test_hpo_service.py` | — | ✅ |

### Variant classification
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-CLASS-001 | `services/acmg_points.py::compute_classification` | `test_acmg_classification.py::test_point_bands_match_clingen_thresholds` | H3 | ✅ |
| REQ-CLASS-002 | `services/acmg_points.py` | `test_acmg_classification.py::test_ba1_forces_benign_regardless_of_points` | H3 | ✅ |
| REQ-CLASS-003 | `services/acmg_points.py::selection_points` | `test_acmg_classification.py` (unaccepted-criteria exclusion) | H3 | ✅ |
| REQ-CLASS-004 | `services/acmg_points.py` (vus tier) | `test_acmg_classification.py::test_vus_sub_tier_bands` | — | ✅ |
| REQ-CLASS-005 | `services/small_variant_review_pg.py::upsert_small_variant_review` | `test_acmg_classification.py` (normalize); `AcmgClassificationModal.test.tsx` | H3 | ✅ |
| REQ-CLASS-006 | `services/cnv_acmg_points.py::compute_classification`; `CnvAcmgClassificationModal.tsx` | `test_cnv_acmg_points.py`; `CnvAcmgClassificationModal.test.tsx` (kind toggle, overridable criteria, recompute→save); `cnvAcmg.test.ts` | H3 | ✅ |

### Clinical traceability & integrity
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-TRACE-001 | `services/annotation_manifest_service.py::get_family_annotation_manifest`, `_platform_modules` (the reference layer: assembly, gene loci, Monarch and HPO releases); `services/hpo_service.py::get_loaded_hpo_release` | `test_annotation_manifest.py` (the reference modules and their `unavailable` states); `integration/test_hpo_release_provenance.py` (the HPO release of the latest import) | H8 | ✅ |
| REQ-TRACE-002 | `services/small_variant_review_acmg.py::build_evidence_snapshot` | `test_classification_drift.py::test_build_evidence_snapshot_*` | H8 | ✅ |
| REQ-TRACE-003 | `services/classification_drift_service.py::evaluate_classification_drift` | `test_classification_drift.py` (diff states) | H8 | ✅ |
| REQ-TRACE-004 | `services/clinical_audit_service.py::record_review_changes` | `test_clinical_audit.py` (one insert per change) | H9,H8 | ✅ |
| REQ-TRACE-005 | `services/report_signout_service.py::sign_out_report`, `build_report_snapshot` | `test_report_signout.py::test_canonical_hash_is_stable_and_order_independent`, `::test_the_snapshot_records_which_reference_modules_it_looked_up`, `::test_a_record_signed_before_the_hpo_release_was_recorded_still_verifies` | H9 | ✅ |
| REQ-TRACE-006 | `services/report_signout_service.py::sign_out_report` (drift gate) | `test_report_signout.py::test_sign_out_blocks_unacknowledged_drift`, `::test_acknowledging_drift_without_a_reason_is_422`; `FamilyReportPage.test.tsx` (a reason is required, no bare confirm) | H8 | ✅ |
| REQ-TRACE-007 | **Not implemented:** the report page renders live data. Interim control: `services/report_signout_service.py::compare_report_with_latest_signout` (`GET /report/sign-out-check`) and `pages/families/FamilyReportPage.tsx` (the page is marked when it differs from the latest signed version; the signed version downloads as JSON) | Interim control: `test_report_signout.py::test_signout_check_*` (a record signed before the HPO release was recorded is not compared on it, nor reported as changed); `FamilyReportPage.test.tsx` (presented as signed only when it matches; changed or unchecked content marked on screen and in print; download of the signed version) | H9 | ⚠ |
| REQ-TRACE-008 | Append-only triggers (`04_traceability.sql`); the restricted runtime role `coga_app` (`05_grants.sql`); per-family hash chains (`services/hash_chain.py`); signed integrity anchors (`services/integrity_anchor_service.py`) | **Prevent:** `integration/test_append_only_triggers.py` (UPDATE/DELETE rejected); `integration/test_app_role_privileges.py`, `integration/test_app_boots_as_restricted_role.py` (`coga_app` cannot change the append-only tables). **Detect:** `test_hash_chain.py`, `integration/test_hash_chain_integration.py` (an edit made with the trigger disabled is detected and located); `integration/test_integrity_anchor_integration.py` (a re-chain or truncation by the owner diverges from a signed anchor). **Limits:** the API runs as the owner until the restricted role is switched on at go-live ([TF-13 §3](TF-13-cybersecurity.md)); anchors are made only when an admin requests one, and are not exported off the database. Tamper-evident, not tamper-proof — see [clinical-traceability.md](../clinical-traceability.md). | H9 | ◐ |
| REQ-TRACE-009 | `services/assembly_scope.py`; `services/report_signout_service.py::sign_out_report` (assembly-scope gate); `components/AssemblyScopeBanner.tsx` via `FamilyPageHeader.tsx` | `test_assembly_scope.py`; `test_report_signout.py::test_sign_out_refuses_a_family_on_an_unvalidated_assembly`, `::test_sign_out_refuses_a_family_without_a_resolved_assembly`, `::test_a_validated_assembly_is_configured_not_hard_coded`; `test_config.py::test_validated_assemblies_default_to_grch38_only`; `AssemblyScopeBanner.test.tsx` (including the unconfirmed scope, CR-076); `reference.test.tsx` (a failed catalogue reported); `FamilyReportPage.test.tsx` (label, sign-out not offered, refusal not shown as the drift override) | H12 | ✅ |
| REQ-TRACE-010 | `services/review_pg_utils.py::_raise_on_stale_review`, `_lock_review`; `small_variant_review_pg.py::upsert_small_variant_review`, `structural_variant_review_pg.py::upsert_structural_variant_review`; `frontend/src/lib/reviewConcurrency.ts` | `test_review_concurrency.py` (match, mismatch, first write, removed review, legacy client, nothing written on conflict, lock before read); `reviewConcurrency.test.ts`; `FamilySmallVariantsPage.test.tsx` (version sent, conflict shown and reloaded) | H9,H3 | ✅ |
| REQ-TRACE-011 | `services/family_package_qc.py::extract_pipeline_versions`; `annotation_manifest_service.py::merge_vcf_header_provenance`; `frontend/src/pages/families/PipelineSettingsPanel.tsx` | `test_family_package_long_read.py` (version parsing); `test_sql_parameter_typing.py` (guards the write that silently failed); `PipelineSettingsPanel.test.tsx` (workflow shown apart from tools; unversioned modules excluded) | H8 | ✅ |
| REQ-TRACE-012 | `services/report_signout_service.py::sign_out_report` (Sample-QC gate, `_unverifiable_swap_checks`); `pages/families/FamilyReportPage.tsx` (acknowledge-with-reason dialog) | `test_report_signout.py::test_failing_sample_qc_blocks_sign_out`, `::test_failing_sample_qc_acknowledged_without_reason_is_422`, `::test_unverifiable_asserted_relatedness_blocks_sign_out`, `::test_sample_qc_is_bound_into_content_hash`, `::test_audit_records_qc_status_and_acknowledgement`; `FamilyReportPage.test.tsx` (a failing or unverifiable Sample QC opens the reason dialog; the frozen QC status and reason are shown on the signed record) | H4 | ✅ |
| REQ-TRACE-013 | `services/report_signout_service.py::sign_out_report` (incomplete-import gate, `_import_incomplete_state`); `services/family_package_registration.py::_flag_family_import_incomplete`; `components/ImportIncompleteBanner.tsx` via `FamilyPageHeader.tsx`; `pages/families/FamilyReportPage.tsx` (acknowledge-with-reason dialog) | `test_report_signout.py::test_incomplete_import_blocks_sign_out`, `::test_incomplete_import_acknowledged_without_reason_is_422`, `::test_acknowledged_incomplete_import_is_frozen_and_audited`, `::test_the_import_gate_is_acknowledged_independently_of_drift_and_qc`, `::test_an_old_flag_without_an_import_job_still_gates`, `::test_a_sign_out_made_before_the_import_gate_still_verifies`; `test_family_import_compensation.py::test_flag_family_import_incomplete_records_the_import_job`; `integration/test_signout_import_gate_integration.py`; `ImportIncompleteBanner.test.tsx`; `FamilyPageHeader.test.tsx`; `FamilyReportPage.test.tsx` (the warning; the reason dialog, which keeps the earlier acknowledgements; the frozen override on the signed record) | H16 | ✅ |

### Access control & security
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-SEC-001 | `services/family_metadata_context.py::build_family_metadata_context`; the project-visibility rules in `services/access_control.py` | `test_access_control.py` (cross-user/project); `test_access_rules.py` (the rules themselves) | H11 | ✅ |
| REQ-SEC-002 | `dependencies.py::get_current_admin_user`; `services/access_control.py::is_admin_user` in the services | `test_access_control.py` (admin-only family replacement); `test_admin_role_checks.py` (every admin check admits both admin roles and refuses a viewer; no literal `"admin"` comparison) | H11 | ✅ |
| REQ-SEC-003 | `routers/auth.py`, `dependencies.py` | `test_auth_swagger_token.py`; `test_password_hashing.py` (local passwords: bcrypt hashes stored by passlib still verify) | H11 | ✅ |
| REQ-SEC-004 | `services/audit_log_pg.py`; `middleware/request_logging.py` | `test_audit_log_pg.py`; `test_request_logging.py` | H11 | ✅ |
| REQ-SEC-005 | `services/auth_rate_limit_pg.py` | `test_auth_rate_limit_pg.py` | H11 | ✅ |
| REQ-SEC-006 | `core` settings `validate_security_defaults` | `test_config_security.py`; `test_config.py` | H11 | ✅ |
| REQ-SEC-007 | `routers/cram.py` | `test_s3_import_and_cram.py::test_alignment_*` (denied before serve; sample-outside-family) | H11 | ✅ |

### Ingestion & storage
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-DATA-001 | `services/variant_upload_service.py::upload_family_small_variant_file` | `test_variant_upload_service.py` (GT/DP/AF/AD, QUAL) | H1 | ✅ |
| REQ-DATA-002 | `services/variant_upload_service.py`, `haplotype_block_builder.py`; `clickhouse_family_variants.py` | `test_variant_upload_service.py` (PS block); `test_variant_upload_haplotype_golden.py` (the blocks of five seeded families, recorded); `test_clickhouse_family_variants.py` (phasing) | H5 | ✅ |
| REQ-DATA-003 | `services/structural_variant_ingest.py::iter_structural_variant_records` | `test_structural_variant_ingest.py`; `test_structural_variant_breakends.py` (BND remote partner); `test_variant_upload_service.py` | — | ✅ |
| REQ-DATA-004 | `services/raw_import_files_pg.py::verify_raw_import_file` | `test_raw_import_file_verify.py` (verified / mismatch / missing / unverifiable) | H4 | ✅ |
| REQ-DATA-005 | `services/family_package_import.py` | `test_family_package_import.py`; `test_nipt_package_import.py` | H4,H12 | ✅ |
| REQ-DATA-006 | `services/clickhouse_variant_storage.py` | `test_clickhouse_variant_storage_ops.py`; `test_clickhouse_integrity.py` | H9 | ✅ |
| REQ-DATA-007 | `services/family_package_common.py::resolve_vcf_sample_id` / `vcf_sample_alias_map`; `repeat_expansion_pg.py::ingest_family_trgt_text` | `test_family_package_long_read.py` (suffix/prefix/declared resolution; per-sample binding; unresolved reported) | H4 | ✅ |
| REQ-DATA-008 | `services/family_package_variants.py::_iter_cnv_structural_records`; `family_package_datasets.py::_import_cnv_dataset` | `test_family_package_long_read.py` (copy number, CSQ genes, source-scoped ids, sample binding) | H1 | ✅ |
| REQ-DATA-009 | `services/family_package_datasets.py::_import_mito_dataset`; `annotation_table_parser.py::parse_mutserve_annotation_lines` | `test_family_package_long_read.py` (chrM annotation join key; ID column never read as rsid; empty annotation file) | H1 | ✅ |
| REQ-DATA-010 | `services/family_package_qc.py::extract_pipeline_versions` / `parse_pipeline_params`; `annotation_manifest_service.py::merge_vcf_header_provenance` | `test_family_package_long_read.py` (banner-tolerant version parse; both versions of a twice-run tool; params filtering) | H4 | ✅ |
| REQ-DATA-011 | `services/family_package_qc.py::parse_nanostats_text` / `parse_mosdepth_summary_text`; `routers/family_qc_reports.py` | `test_family_package_long_read.py` (QC parsers); `SampleQcCell.test.tsx` (summary + report link) | H4 | ✅ |
| REQ-DATA-012 | `services/genotypes.py` (`classify_genotype`, `clickhouse_genotype_condition`), used by `family_variant_filters.py`, `clickhouse_variant_queries.py` (sample filters, inheritance SQL and Python), `clickhouse_family_variants.py` (counts, cohort, track presence), `clickhouse_variant_storage.py` (summaries), `variant_explorer_service.py`, `sv_gene_index_service.py`, `mitochondrial_analysis.py`; `frontend/src/lib/genotypes.ts` | `test_genotypes.py` (classes, vocabulary, filters and inheritance on haploid chrM/chrX and multi-allelic calls); `integration/test_genotype_classes_clickhouse.py` (SQL = Python on real ClickHouse, short and long strings); `test_clickhouse_family_variants.py`, `test_track_availability_presence.py`, `test_clickhouse_variant_storage_ops.py`, `test_variant_explorer_service.py`; `genotypes.test.ts` | H1,H2 | ✅ |

### Sample QC
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-QC-001 | `services/sample_integrity_service.py`; `services/nipt_analysis.py` (`paternal_evidence`, `infer_fetal_sex`) | `test_sample_integrity_qc.py`; `test_sample_integrity_service.py` (incl. `::test_service_nipt_paternity_ignores_sites_without_a_confident_father_call`, `::test_service_sexes_a_haploid_called_nipt_father`); `test_nipt_analysis.py::test_paternity_evidence_counts_only_sites_with_a_confident_father_call`; `test_nipt_service.py::test_a_haploid_paternal_x_call_sexes_the_fetus` | H4 | ✅ |
| REQ-QC-002 | `routers/families_reports.py` (`/qc/sample-integrity`) | `test_sample_integrity_service.py`; `FamilySampleQcPage.test.tsx` | H4 | ✅ |
| REQ-QC-003 | `services/qc_threshold_service.py` (`QC_METRICS`, `list_qc_threshold_profiles`, `set_qc_threshold`); `routers/admin.py` (`GET/PUT /admin/qc-thresholds`); `03_assay.sql` (`qc_threshold_profiles`, `qc_thresholds`) | `test_qc_threshold_service.py` (catalogue integrity, unknown metric rejected, inverted bounds rejected); `AdminQcThresholdsPage.test.tsx` (per-profile isolation, save/clear) | H14 | ✅ |
| REQ-QC-004 | `services/qc_threshold_service.py` (`evaluate_metric`, `evaluate_sequencing_qc`, `worst_verdict`, `resolve_family_qc_thresholds`); `services/metadata_service.py::_attach_sequencing_qc_verdicts` | `test_qc_threshold_service.py` (bound semantics per direction; unmeasured and unconfigured both `skip`; worst-metric rollup) | H14 | ✅ |
| REQ-QC-005 | `frontend/src/pages/families/SampleQcCell.tsx` | `SampleQcCell.test.tsx` (verdict word beside the value, tone class, breach sentence in the tooltip) | H14, H10 | ✅ |
| REQ-QC-006 | `services/mitochondrial_analysis.py::_sample_qc` (limits resolved per family, no constants) | `test_qc_threshold_service.py` (configured limits drive warn/fail; unconfigured is not-assessed; contamination fails high); `test_mitochondrial_analysis.py` | H14, H13 | ✅ |
| REQ-QC-007 | `services/qc_threshold_service.py::_record_threshold_change` / `list_qc_threshold_changes`; `03_assay.sql` (`qc_threshold_changes`) + `04_traceability.sql` (append-only trigger) + `05_grants.sql` (`coga_app` revoke) | `AdminQcThresholdsPage.test.tsx` (no write before confirmation; history shows the replaced value); append-only enforcement: `integration/test_append_only_triggers.py` (the trigger rejects UPDATE/DELETE; the `changed_by` FK→NULL unlink is allowed, re-attribution is not) + `integration/test_app_role_privileges.py` (`coga_app` holds only SELECT/INSERT; user deletion still nulls `changed_by`). Server-side recording: `test_qc_threshold_service.py` (an edit writes one history row with the replaced and new limits, the actor and the reason, committed with the change; clearing a limit is recorded too; a refused edit writes nothing) + `integration/test_qc_threshold_change_recording.py` (on the real schema, each edit leaves one row carrying the value it replaced; a change without a reason is refused and leaves none). | H14, H9 | ✅ |

### Mitochondrial disease — combined mtDNA + nuclear (app 3.5)
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-MITO-001 | `services/mitochondrial_analysis.py` (mtDNA) + `clickhouse_family_variants.py` (nuclear mito-gene panel) | `test_mitochondrial_analysis.py`; `test_clickhouse_family_variants.py` — combined-assay clinical concordance → TF-10 §3.5 | H1 | ◐ |
| REQ-MITO-002 | `services/mitochondrial_analysis.py` (heteroplasmy + maternal transmission) | `test_mitochondrial_analysis.py` (maternal transmission from the pedigree-linked mother, three-generation pedigree; heteroplasmy; haplogroup) | H13 | ✅ |
| REQ-MITO-003 | `services/sample_integrity_service.py` (checks); `report_signout_service.py::sign_out_report` (Sample-QC sign-out gate, REQ-TRACE-012); `mitochondrial_analysis.py` (per-sample haplogroups) | `test_sample_integrity_qc.py`; `test_sample_integrity_service.py`; `test_report_signout.py` (Sample-QC gate); `test_mitochondrial_analysis.py` (haplogroup listed). No automated maternal-lineage check: the haplogroup comparison is manual | H4 | ◐ |

### Non-functional
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-PERF-001 | (whole device) | [TF-10](TF-10-performance-evaluation-plan.md) concordance studies → [TF-11](TF-11-performance-evaluation-report.md) | H1–H7 | ⚠ pending data |
| REQ-PERF-002 | `services/report_signout_service.py` (content hash + re-verify-on-read, P1-4) | `test_report_signout.py` (hash stable); the stored hash is **re-verified** against the snapshot on every detail read and re-checked by `verify_report_signout_chain` — `integration/test_hash_chain_integration.py` (content_hash vs snapshot mismatch detected through the real JSONB round-trip); full clinical check TF-10 §4 | H9 | ◐ |
| REQ-PERF-003 | input validation across services (analysis/classification layer) | NIPT `test_nipt_analysis.py::test_*fails_safe_on_empty_input`; ACMG `test_acmg_classification.py::test_no_accepted_criteria_*`; CNV `test_cnv_acmg_points.py` (empty/missing-flag/malformed); haplotype `haplotypeRisk.test.ts` (empty→uninformative) | H1,H6 | ✅ |
| REQ-UI-001 | `pages/families/FamilyNiptPage.tsx`; `NiptClassificationBlock.tsx` | `FamilyNiptPage.test.tsx`; `NiptClassificationBlock.test.tsx` (category/confidence/VAF/flags) | H6, H1, H10 | ✅ |
| REQ-UI-002 | `components/visualizations/HaplotypePhasedTrack.tsx` | `HaplotypePhasedTrack.test.tsx` | H5, H10 | ✅ |
| REQ-UI-003 | `pages/families/AcmgClassificationModal.tsx`; `AcmgScaleBar.tsx`; `CnvScaleBar.tsx`; the suggestions in `lib/acmg/evaluate.ts`, `evaluateMito.ts` and `pedigree.ts` (PM6 / PS2 parents from the pedigree links), with `lib/clinvar.ts` (PP5 / BP6) | `AcmgClassificationModal.test.tsx`; `AcmgScaleBar.test.tsx`; `CnvScaleBar.test.tsx`; `evaluate.test.ts`, `evaluateMito.test.ts`, `pedigree.test.ts`, `clinvar.test.ts` | H3, H10 | ✅ |
| REQ-UI-004 | `pages/families/FamilyReportPage.tsx` | `FamilyReportPage.test.tsx` | H8, H9, H10 | ✅ |
| REQ-UI-005 | `components/RequireAuth.tsx`, `RequireAdmin.tsx` | `RequireAuth.test.tsx`; `RequireAdmin.test.tsx` (unauth→login, viewer→dashboard, admin/superuser→content) | H11 | ✅ |
| REQ-UI-006 | `pages/auth/LoginPage.tsx` | `LoginPage.test.tsx` (next-path validation) | H11 | ✅ |
| REQ-UI-007 | `components/visualizations/Pedigree.tsx` | `Pedigree.test.tsx` (affected/carrier/QC ring) | H4 | ✅ |
| REQ-UI-008 | `routers/health.py` (`/version`, `VersionOut`); `lib/appVersion.ts`, `lib/deviceLabel.ts`, `lib/problemReport.ts`; `components/Layout.tsx` (app footer); `pages/families/ReportSoftwareIdentity.tsx` in `FamilyReportPage.tsx` and `FamilyNiptReportPage.tsx`; `pages/product/NewFeaturesPage.tsx` | `test_health_endpoint.py::test_version_endpoint_serves_a_named_model_for_the_frontend`; `appVersion.test.ts`; `problemReport.test.ts`; `Layout.test.tsx` (label, version, route); `FamilyReportPage.test.tsx` and `FamilyNiptReportPage.test.tsx` (footer build and label, asked again on open, failure marked incomplete); `NewFeaturesPage.test.tsx` (route) | H9, H10 | ✅ |
| REQ-RPT-001 | `pages/families/FamilyReportPage.tsx`; `report_signout_service.py` | `FamilyReportPage.test.tsx` | H9 | ✅ |
| REQ-RPT-002 | `pages/families/FamilyNiptReportPage.tsx` | `FamilyNiptReportPage.test.tsx` | H6 | ✅ |

## 2. Coverage summary

- **Requirements:** every distinct `REQ-*` identifier in
  [TF-09a](TF-09a-software-requirements-specification.md) has a row here. Test counts are in
  [docs/testing.md](../testing.md).
- **Directly verified (✅):** most criticality-C logic — NIPT FF/classification, haplotype
  lineage + phased-marker QC, ACMG/CNV scoring, trio/de-novo/compound-het,
  repeat/Paraphase/mtDNA, the traceability stack (manifest, evidence, drift, audit, sign-out and
  its gates, immutability) and access control.
- **Partial (◐) and gaps (⚠):** listed with their actions in §3. Most are clinical claims that
  the TF-10 study establishes rather than a unit test; one requirement is not implemented
  (REQ-TRACE-007).

## 3. Verification gaps & actions (CAPA backlog)

Per the [TF-09 release checklist](TF-09-verification-validation.md) ("no requirement without a
passing verifying test"), each requirement below is not yet fully verified and must be closed —
by a test, an implementation or the TF-10 study — before the first clinical release (TF-09 §6,
TF-18).

| Req | Status | Gap | Action |
| --- | --- | --- | --- |
| REQ-CARR-001 | ◐ | Carrier panel scoping is unit-tested; its clinical performance is not yet established | Establish in TF-10 (BeGECS couples). |
| REQ-CARR-002 | ⚠ | Couple-level at-risk has no dedicated unit test | Establish clinically in TF-10 (BeGECS couples); add a unit test if a pure helper is extracted. |
| REQ-PGT-007 | ◐ | > 10 Mb SV detection-limit claim unproven by test | Verify the size threshold in TF-10 (PGT embryos). |
| REQ-PGT-008 | ⚠ | Aneuploidy detection has no dedicated unit test — the one gap a unit test can close | Add a detection unit test; validate in TF-10. |
| REQ-TRACE-007 | ⚠ | Not implemented: the report page renders live data, not the frozen signed record. Interim control: the page is checked against the latest signed version and marked when it differs, and the signed version can be downloaded (JSON). | Render signed reports from the frozen record. The snapshot must first hold the narrative inputs the page draws (gene profiles, HGVS, frequencies) — [clinical-traceability.md](../clinical-traceability.md). |
| REQ-TRACE-008 | ◐ | The API runs as the database owner, who can disable the triggers; anchors are made only on an admin request and stay in the database | Switch the API to the restricted role at go-live ([TF-13 §3](TF-13-cybersecurity.md)); **🔲** decide the anchor schedule and the off-database export. |
| REQ-MITO-001 | ◐ | Combined mtDNA + nuclear assay clinical concordance unproven | Establish in TF-10 §3.5 (mtDNA variant + heteroplasmy + nuclear concordance vs comparator). The components (mtDNA, nuclear, Sample QC) are unit-tested. |
| REQ-MITO-003 | ◐ | No automated maternal-lineage check: CoGA lists each sample's haplogroup and the analyst compares them | Keep as a manual review step ([TF-15 §3](TF-15-instructions-for-use.md)), or add an automated haplogroup comparison (**🔲** owner). |
| REQ-PERF-001 | ⚠ | Performance evaluation not yet run | Run the TF-10 studies; report in TF-11. |
| REQ-PERF-002 | ◐ | Content-hash reproducibility is unit- and integration-tested; the clinical check is pending | TF-10 §4. |

> Closing these is a precondition of the first clinical release (TF-18 release verification,
> TF-09 §6). Except for REQ-TRACE-007, they are **not** defects in shipped behavior — the logic
> exists and is largely exercised indirectly — but direct, traceable verification is required
> for the technical file.
