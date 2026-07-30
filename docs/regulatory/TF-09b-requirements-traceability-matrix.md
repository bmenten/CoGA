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
> tests under `backend/tests/` and `tests/`; frontend under `frontend/src/`. This matrix was
> built from a point-in-time code/test inventory and is maintained under change control
> ([TF-18](TF-18-change-configuration-management.md)).

**Status:** ✅ implemented + directly verified by a passing test · ◐ implemented, indirect/partial
verification or clinical validation pending (TF-10) · ⚠ verification gap (no direct test) — see §3.

---

## 1. Traceability table

### Monogenic NIPT
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-NIPT-001 | `services/nipt_analysis.py::estimate_fetal_fraction` | `test_nipt_analysis.py::test_fetal_fraction_recovery` | H6 | ✅ |
| REQ-NIPT-002 | `services/nipt_analysis.py::classify_site` | `test_nipt_analysis.py::test_category_*` | H6 | ✅ |
| REQ-NIPT-003 | `services/nipt_analysis.py` | `test_nipt_analysis.py::test_ff_too_low_suppresses_fetal_inheritance` | H6 | ✅ |
| REQ-NIPT-004 | `services/nipt_artifact_pg.py`, `services/nipt_service.py` | `test_nipt_artifact_pg.py`; `test_nipt_analysis.py::test_run_nipt_analysis_filter_counts` | H1 | ✅ |
| REQ-NIPT-005 | `services/nipt_analysis.py::estimate_fetal_fraction` (external-FF cross-check) | `test_nipt_analysis.py::test_external_ff_*` (agreement / flagged disagreement / prefer-external) | H6 | ✅ |
| REQ-NIPT-006 | `services/nipt_coverage.py::summarize_on_target_coverage` | `test_nipt_coverage.py` (weighted median, low-coverage flags) | H1 | ✅ |
| REQ-NIPT-007 | `services/nipt_service.py`; `routers/families.py` `/nipt/variants` | `test_nipt_service.py` (presets); `test_nipt_end_to_end.py::test_nipt_demo_recessive_at_risk` | — | ✅ |
| REQ-NIPT-008 | `services/nipt_analysis.py` (category 8) | `test_nipt_analysis.py` (absence/category logic) | H6 | ✅ |

### Expanded carrier screening (BeGECS)
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-CARR-001 | small-variant query + panel filter (`services/clickhouse_family_variants.py`, `panel_metadata_service.py`) | `test_panel_filter_constraints.py`; `test_gene_panel_versions.py` | H1,H12 | ◐ |
| REQ-CARR-002 | recessive/compound-het logic (`services/clickhouse_family_variants.py::get_family_compound_het_candidates`) | (no couple-level test) — clinical validation TF-10 (50 couples) | H2 | ⚠ |
| REQ-CARR-003 | `services/panel_metadata_service.py` | `test_panelapp_service.py`; `test_gene_panel_versions.py` | H8,H12 | ✅ |

### PGT / haplotype segregation
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-PGT-001 | `services/haplotype_lineage_service.py::annotate_lineage` | `test_haplotype_lineage_service.py` (28 tests; grey/placement) | H5 | ✅ |
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
| REQ-DIAG-001 | `services/clickhouse_family_variants.py` (inheritance matching) | `test_clickhouse_family_variants.py`; `test_de_novo_detection.py` | H2 | ✅ |
| REQ-DIAG-002 | `services/clickhouse_family_variants.py` (de-novo) | `test_de_novo_detection.py::test_not_de_novo_without_full_trio` | H2 | ✅ |
| REQ-DIAG-003 | `services/sv_gene_index_service.py::get_sv_second_hits` | `test_sv_gene_index.py`; `test_clickhouse_family_variants.py` (compound-het); `SvSecondHitBadge.test.tsx` | H2 | ✅ |
| REQ-DIAG-004 | `services/repeat_expansion_pg.py::ingest_trgt_text`, `classify_repeat_count` | `test_repeat_expansion_pg.py` | — | ✅ |
| REQ-DIAG-005 | `services/paraphase_pg.py` | `test_paraphase_pg.py` (SMN metrics, regions) | — | ✅ |
| REQ-DIAG-006 | `services/mitochondrial_analysis.py` | `test_mitochondrial_analysis.py` (maternal, heteroplasmy) | — | ✅ |
| REQ-DIAG-007 | `services/variant_prioritization*.py` | `test_variant_prioritization.py` (23 tests) | H1 | ✅ |
| REQ-DIAG-008 | `services/monarch_semsim.py`, `hpo_service.py` | `test_monarch_semsim.py`; `test_hpo_service.py` | — | ✅ |

### Variant classification
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-CLASS-001 | `services/acmg_points.py::compute_classification` | `test_acmg_classification.py::test_point_bands_match_clingen_thresholds` | H3 | ✅ |
| REQ-CLASS-002 | `services/acmg_points.py` | `test_acmg_classification.py::test_ba1_forces_benign_regardless_of_points` | H3 | ✅ |
| REQ-CLASS-003 | `services/acmg_points.py::selection_points` | `test_acmg_classification.py` (unaccepted-criteria exclusion) | H3 | ✅ |
| REQ-CLASS-004 | `services/acmg_points.py` (vus tier) | `test_acmg_classification.py::test_vus_sub_tier_bands` | — | ✅ |
| REQ-CLASS-005 | `services/small_variant_review_pg.py::upsert_small_variant_review` | `test_acmg_classification.py` (normalize); `AcmgClassificationModal.test.tsx` | H3 | ✅ |
| REQ-CLASS-006 | `services/cnv_acmg_points.py::compute_classification`; `CnvAcmgClassificationModal.tsx` | `test_cnv_acmg_points.py` (8 tests); `CnvAcmgClassificationModal.test.tsx` (kind toggle, overridable criteria, recompute→save); `cnvAcmg.test.ts` | H3 | ✅ |

### Clinical traceability & integrity
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-TRACE-001 | `services/annotation_manifest_service.py::get_family_annotation_manifest` | `test_annotation_manifest.py` | H8 | ✅ |
| REQ-TRACE-002 | `services/small_variant_review_pg.py::build_evidence_snapshot` | `test_classification_drift.py::test_build_evidence_snapshot_*` | H8 | ✅ |
| REQ-TRACE-003 | `services/classification_drift_service.py::evaluate_classification_drift` | `test_classification_drift.py` (diff states) | H8 | ✅ |
| REQ-TRACE-004 | `services/clinical_audit_service.py::record_review_changes` | `test_clinical_audit.py` (one insert per change) | H9,H8 | ✅ |
| REQ-TRACE-005 | `services/report_signout_service.py::sign_out_report`, `build_report_snapshot` | `test_report_signout.py::test_canonical_hash_is_stable_and_order_independent` | H9 | ✅ |
| REQ-TRACE-006 | `services/report_signout_service.py` (drift gate) | `test_report_signout.py::test_sign_out_blocks_unacknowledged_drift`; `FamilyReportPage.test.tsx` | H8 | ✅ |
| REQ-TRACE-007 | `services/report_signout_service.py::build_report_snapshot` | `FamilyReportPage.test.tsx` (render-from-snapshot) | H9 | ◐ |
| REQ-TRACE-008 | Postgres append-only triggers (`04_traceability.sql`) + per-family hash chains (`04_traceability.sql`, `services/hash_chain.py`) | **Prevent:** `integration/test_append_only_triggers.py` (triggers fire: UPDATE/DELETE rejected, FK→NULL carve-out allowed) — verified for the app's INSERT-only path. **Evidence (detect):** `integration/test_hash_chain_integration.py` (real-DB: real writer→`verify_*_chain` passes incl. after a family deletion; a `DISABLE TRIGGER` edit/reorder/deletion that does NOT recompute the hashes is detected & localised; FK→NULL carve-out preserved) + `test_hash_chain.py` (chain math) + admin `GET /admin/integrity/verify`. **Scope (honest):** the chain makes tampering evident for any principal who *cannot recompute it* — every non-owner DB role (trigger-blocked from writing `row_hash`) and careless owner mutation. It does **not** yet detect a determined owner who DISABLEs the trigger and re-chains an interior edit/deletion. **Prevent (P1-3a):** the restricted `coga_app` runtime role + grants now exist and are proven locked out of UPDATE/DELETE/TRUNCATE/DISABLE-TRIGGER on the append-only tables (`05_grants.sql`, `integration/test_app_role_privileges.py`); it ships NOLOGIN, so **activating** it (the change-controlled DSN flip — `docs/db-runtime-role-runbook.md`) is what prevents owner-bypass at runtime. Detecting an OWNER who re-chains needs the external signed anchor below. **Detect (P1-4b anchor):** `services/integrity_anchor_service.py` + `04_traceability.sql` periodically capture every chain head and sign the snapshot with an Ed25519 key held outside the DB; `POST /admin/integrity/anchor` (capture) + `GET /admin/integrity/anchor/verify` (signature + per-family prefix check) — `integration/test_integrity_anchor_integration.py` proves a real anchor verifies and an owner-bypass re-chain (recomputed head row_hash) **or** truncation diverges from it, and a tampered anchor signature is caught. **Scope (honest, do NOT overclaim):** this makes interior re-chaining + truncation evident *between retained anchors* against a database-only adversary who lacks the signing key; it does **not** defend against an adversary holding the signing key (HSM = deferred), does **not** by itself detect deletion of the *latest* anchors (needs the deferred out-of-band export), and leaves pre-first-anchor data unanchored. Tamper-EVIDENT against a DB-only adversary, not tamper-proof. | H9 | ◐ |

### Access control & security
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-SEC-001 | `services/metadata_service.py` (`build_family_metadata_context`) | `test_access_control.py` (cross-user/project) | H11 | ✅ |
| REQ-SEC-002 | `dependencies.py::get_current_admin_user` | `test_access_control.py` (admin-only family replacement) | H11 | ✅ |
| REQ-SEC-003 | `routers/auth.py`, `dependencies.py` | `test_auth_swagger_token.py` | H11 | ✅ |
| REQ-SEC-004 | `services/audit_log_pg.py`; `middleware/request_logging.py` | `test_audit_log_pg.py`; `test_request_logging.py` | H11 | ✅ |
| REQ-SEC-005 | `services/auth_rate_limit_pg.py` | `test_auth_rate_limit_pg.py` | H11 | ✅ |
| REQ-SEC-006 | `core` settings `validate_security_defaults` | `test_config_security.py`; `test_config.py` | H11 | ✅ |
| REQ-SEC-007 | `routers/cram.py` | `test_s3_import_and_cram.py::test_alignment_*` (denied before serve; sample-outside-family) | H11 | ✅ |

### Ingestion & storage
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-DATA-001 | `services/variant_upload_service.py::upload_family_small_variant_file` | `test_variant_upload_service.py` (GT/DP/AF/AD, QUAL) | H1 | ✅ |
| REQ-DATA-002 | `services/variant_upload_service.py`; `clickhouse_family_variants.py` | `test_variant_upload_service.py` (PS block); `test_clickhouse_family_variants.py` (phasing) | H5 | ✅ |
| REQ-DATA-003 | `services/structural_variant_ingest.py::iter_structural_variant_records` | `test_structural_variant_ingest.py`; `test_variant_upload_service.py` | — | ✅ |
| REQ-DATA-004 | `services/raw_import_files_pg.py::verify_raw_import_file` | `test_raw_import_file_verify.py` (verified / mismatch / missing / unverifiable) | H4 | ✅ |
| REQ-DATA-005 | `services/family_package_import.py` | `test_family_package_import.py` (25); `test_nipt_package_import.py` | H4,H12 | ✅ |
| REQ-DATA-006 | `services/clickhouse_variant_storage.py` | `test_clickhouse_variant_storage_ops.py`; `test_clickhouse_integrity.py` | H9 | ✅ |
| REQ-DATA-007 | `services/family_package_common.py::resolve_vcf_sample_id` / `vcf_sample_alias_map`; `repeat_expansion_pg.py::ingest_family_trgt_text` | `test_family_package_long_read.py` (suffix/prefix/declared resolution; per-sample binding; unresolved reported) | H4 | ✅ |
| REQ-DATA-008 | `services/family_package_variants.py::_iter_cnv_structural_records`; `family_package_datasets.py::_import_cnv_dataset` | `test_family_package_long_read.py` (copy number, CSQ genes, source-scoped ids, sample binding) | H1 | ✅ |
| REQ-DATA-009 | `services/family_package_datasets.py::_import_mito_dataset`; `variant_upload_service.py::parse_mutserve_annotation_lines` | `test_family_package_long_read.py` (chrM annotation join key; ID column never read as rsid; empty annotation file) | H1 | ✅ |
| REQ-DATA-010 | `services/family_package_qc.py::extract_pipeline_versions` / `parse_pipeline_params`; `annotation_manifest_service.py::merge_vcf_header_provenance` | `test_family_package_long_read.py` (banner-tolerant version parse; both versions of a twice-run tool; params filtering) | H4 | ✅ |
| REQ-DATA-011 | `services/family_package_qc.py::parse_nanostats_text` / `parse_mosdepth_summary_text`; `routers/family_qc_reports.py` | `test_family_package_long_read.py` (QC parsers); `SampleQcCell.test.tsx` (summary + report link) | H4 | ✅ |

### Sample QC
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-QC-001 | `services/sample_integrity_service.py` | `test_sample_integrity_qc.py` (22); `test_sample_integrity_service.py` | H4 | ✅ |
| REQ-QC-002 | `routers/families.py` `/qc/sample-integrity` | `test_sample_integrity_service.py`; `FamilySampleQcPage.test.tsx` | H4 | ✅ |
| REQ-QC-003 | `services/qc_threshold_service.py` (`QC_METRICS`, `list_qc_threshold_profiles`, `set_qc_threshold`); `routers/admin.py` (`GET/PUT /admin/qc-thresholds`); `03_assay.sql` (`qc_threshold_profiles`, `qc_thresholds`) | `test_qc_threshold_service.py` (catalogue integrity, unknown metric rejected, inverted bounds rejected); `AdminQcThresholdsPage.test.tsx` (per-profile isolation, save/clear) | H14 | ✅ |
| REQ-QC-004 | `services/qc_threshold_service.py` (`evaluate_metric`, `evaluate_sequencing_qc`, `worst_verdict`, `resolve_family_qc_thresholds`); `services/metadata_service.py::_attach_sequencing_qc_verdicts` | `test_qc_threshold_service.py` (bound semantics per direction; unmeasured and unconfigured both `skip`; worst-metric rollup) | H14 | ✅ |
| REQ-QC-005 | `frontend/src/pages/families/SampleQcCell.tsx` | `SampleQcCell.test.tsx` (verdict word beside the value, tone class, breach sentence in the tooltip) | H14, H10 | ✅ |
| REQ-QC-006 | `services/mitochondrial_analysis.py::_sample_qc` (limits resolved per family, no constants) | `test_qc_threshold_service.py` (configured limits drive warn/fail; unconfigured is not-assessed; contamination fails high); `test_mitochondrial_analysis.py` | H14, H13 | ✅ |
| REQ-QC-007 | `services/qc_threshold_service.py::_record_threshold_change` / `list_qc_threshold_changes`; `03_assay.sql` (`qc_threshold_changes`) + `04_traceability.sql` (append-only trigger) | `AdminQcThresholdsPage.test.tsx` (no write before confirmation; history shows the replaced value); append-only enforcement covered by `test_append_only_triggers.py`'s pattern | H14, H9 | ◐ |
| REQ-TRACE-008 | `services/family_package_qc.py::extract_pipeline_versions`; `annotation_manifest_service.py::merge_vcf_header_provenance`; `frontend/src/pages/families/PipelineSettingsPanel.tsx` | `test_family_package_long_read.py` (version parsing); `test_sql_parameter_typing.py` (guards the write that silently failed); `PipelineSettingsPanel.test.tsx` (workflow shown apart from tools; unversioned modules excluded) | H8 | ✅ |

### Mitochondrial disease — combined mtDNA + nuclear (app 3.5)
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-MITO-001 | `services/mitochondrial_analysis.py` (mtDNA) + `clickhouse_family_variants.py` (nuclear mito-gene panel) | `test_mitochondrial_analysis.py`; `test_clickhouse_family_variants.py` — combined-assay clinical concordance → TF-10 §3.5 | H1 | ◐ |
| REQ-MITO-002 | `services/mitochondrial_analysis.py` (heteroplasmy + maternal transmission) | `test_mitochondrial_analysis.py` (maternal, heteroplasmy, haplogroup) | H13 | ✅ |
| REQ-MITO-003 | `services/sample_integrity_service.py` (data integrity / sample-swap gate) | `test_sample_integrity_qc.py`; `test_sample_integrity_service.py` | H4 | ✅ |

### Non-functional
| Req | Implementation | Verifying test | Risk | Status |
| --- | --- | --- | --- | --- |
| REQ-PERF-001 | (whole device) | [TF-10](TF-10-performance-evaluation-plan.md) concordance studies → [TF-11](TF-11-performance-evaluation-report.md) | H1–H7 | ⚠ pending data |
| REQ-PERF-002 | `services/report_signout_service.py` (content hash + re-verify-on-read, P1-4) | `test_report_signout.py` (hash stable); the stored hash is now **re-verified** against the snapshot on every detail read and re-checked by `verify_report_signout_chain` — `integration/test_hash_chain_integration.py` (content_hash vs snapshot mismatch detected through the real JSONB round-trip); full clinical check TF-10 §4 | H9 | ◐ |
| REQ-PERF-003 | input validation across services (analysis/classification layer) | NIPT `test_nipt_analysis.py::test_*fails_safe_on_empty_input`; ACMG `test_acmg_classification.py::test_no_accepted_criteria_*`; CNV `test_cnv_acmg_points.py` (empty/missing-flag/malformed); haplotype `haplotypeRisk.test.ts` (empty→uninformative) | H1,H6 | ✅ |
| REQ-UI-001 | `pages/families/FamilyNiptPage.tsx`; `NiptClassificationBlock.tsx` | `FamilyNiptPage.test.tsx`; `NiptClassificationBlock.test.tsx` (category/confidence/VAF/flags) | H6,H1 | ✅ |
| REQ-UI-002 | `components/visualizations/HaplotypePhasedTrack.tsx` | `HaplotypePhasedTrack.test.tsx` | H5 | ✅ |
| REQ-UI-003 | `pages/families/AcmgClassificationModal.tsx`; `AcmgScaleBar.tsx`; `CnvScaleBar.tsx` | `AcmgClassificationModal.test.tsx`; `AcmgScaleBar.test.tsx`; `CnvScaleBar.test.tsx` | H3 | ✅ |
| REQ-UI-004 | `pages/families/FamilyReportPage.tsx` | `FamilyReportPage.test.tsx` | H8,H9 | ✅ |
| REQ-UI-005 | `components/RequireAuth.tsx`, `RequireAdmin.tsx` | `RequireAuth.test.tsx`; `RequireAdmin.test.tsx` (unauth→login, viewer→dashboard, admin/superuser→content) | H11 | ✅ |
| REQ-UI-006 | `pages/auth/LoginPage.tsx` | `LoginPage.test.tsx` (next-path validation) | H11 | ✅ |
| REQ-UI-007 | `components/visualizations/Pedigree.tsx` | `Pedigree.test.tsx` (affected/carrier/QC ring) | H4 | ✅ |
| REQ-RPT-001 | `pages/families/FamilyReportPage.tsx`; `report_signout_service.py` | `FamilyReportPage.test.tsx` | H9 | ✅ |
| REQ-RPT-002 | `pages/families/FamilyNiptReportPage.tsx` | `FamilyNiptReportPage.test.tsx` | H6 | ✅ |

## 2. Coverage summary

- **Requirements:** **73 across 13 areas** (incl. the combined mtDNA + nuclear mitochondrial app, REQ-MITO) — counted from the `REQ-*` identifiers in [TF-09a](TF-09a-software-requirements-specification.md). Test suites at this revision: **backend 138 test files, 953 passing / 68 skipped**; **frontend 98 test files, 417 tests**.
- **Directly verified (✅):** the large majority of Class C backend logic — NIPT FF/classification, haplotype lineage + phased-marker QC, ACMG/CNV scoring, trio/de-novo/compound-het, repeat/Paraphase/mtDNA, the full traceability stack (manifest/evidence/drift/audit/sign-out/immutability), and access control.
- **Partial/pending (◐):** items whose **clinical** performance is established in TF-10 rather than a unit test (carrier panel scoping, >10 Mb SV), or verified by integration rather than unit test (render-from-snapshot, content-hash reproducibility).
- **Gaps (⚠):** see §3. Most remaining actions are **clinical** — couple-level carrier, SV detection limit, and the combined mtDNA+nuclear assay concordance — and are established by the TF-10 study. **One unit-testable gap remains open:** REQ-PGT-008 (aneuploidy) has no dedicated detection test.

## 3. Verification gaps & actions (CAPA backlog)

Per the [TF-09 release checklist](TF-09-verification-validation.md) ("no requirement without a
passing verifying test"), the remaining **Class C** requirements below lack direct verification
and must be closed (add a test, or establish via the TF-10 study) before sign-off.

**Closed (PR #244 + follow-ups):** REQ-UI-005 (RequireAdmin), REQ-SEC-007 (access-before-serve),
REQ-DATA-004 (checksum), REQ-NIPT-005 (external-FF); REQ-PGT-005 (embryo classification) was
found already covered by `haplotypeRisk.test.ts` and re-marked ✅ (the lib test the initial
inventory missed), with a one-side-donor `uninformative` case added; and the safety-relevant UI
sub-components (REQ-UI-001 `NiptClassificationBlock`, REQ-UI-003 `AcmgScaleBar`/`CnvScaleBar`,
REQ-CLASS-006 `CnvAcmgClassificationModal`) now have vitest coverage; and REQ-PERF-003
(degraded-input fail-safe) is now verified across NIPT, ACMG, CNV and haplotype.

Most remaining gaps are **clinical detection-limit/concordance claims established by the TF-10
study rather than by unit tests**. The exception is **REQ-PGT-008**, which still needs a
detection unit test and is the one unit-testable gap outstanding:

| Req | Gap | Action |
| --- | --- | --- |
| REQ-CARR-002 | Couple-level at-risk has no dedicated unit test | Establish clinically in TF-10 (50 BeGECS couples); add a unit test if a pure helper is extracted. |
| REQ-PGT-007 | >10 Mb SV detection-limit claim unproven by test | Verify the size threshold in TF-10 (100 embryos). |
| REQ-PGT-008 | Aneuploidy detection has no dedicated unit test | Add a detection unit test; validate in TF-10. |
| REQ-MITO-001 | Combined mtDNA + nuclear assay clinical concordance unproven | Establish in TF-10 §3.5 (mtDNA variant + heteroplasmy + nuclear concordance vs comparator). Components (mtDNA, nuclear, Sample QC) are unit-tested. |

> These gaps are tracked as actions; closing them is a precondition of the first clinical
> release (TF-18 release verification, TF-09 §6). They are **not** defects in shipped behavior —
> the logic exists and is largely exercised indirectly — but direct, traceable verification is
> required for the technical file.
