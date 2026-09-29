# Test Overview

A catalogue of CoGA's automated tests — **what each test file verifies**, with a link to the
file. This is the **file-centric** view; for the **requirement-centric** view (each
requirement → its verifying test), see the Requirements Traceability Matrix
[docs/regulatory/TF-09b](regulatory/TF-09b-requirements-traceability-matrix.md).

| Suite | Files | Tests | Runner |
| --- | --- | --- | --- |
| Backend (Python) | 162 | 1413, of which 79 are integration/e2e tests that only the `smoke` and `e2e` jobs run | `pytest` |
| Frontend (TypeScript/React) | 124 | 663 | `vitest` |

> Counts as of 2026-09-28 (#530); the test tree is the source of truth. Regenerate the
> inventory with the commands in [§ Keeping this current](#keeping-this-current).

---

## Test strategy

- **Unit-first, self-contained.** The backend unit suite mocks Postgres/ClickHouse access, so it runs with no service containers. Pure analysis logic (NIPT, haplotype/PGT, ACMG/CNV, mtDNA, prioritization) is tested directly against synthetic inputs.
- **Integration smoke.** A small integration suite boots the real app against **Postgres + ClickHouse** (schema init, admin seed, health probe) to catch startup/schema failures the unit suite cannot.
- **Frontend component + logic.** `vitest` + Testing Library cover pure `lib/` logic, visualization components (canvas mocked), and page behaviour; safety-relevant UI (route guards, ACMG/CNV/NIPT readouts, sign-out/drift) is explicitly covered.
- **Fail-safe / degraded input.** Across NIPT, ACMG, CNV and haplotype, degraded/empty input must abstain (low-confidence / VUS / uninformative), never silently mis-call.
- **One backend test root.** Every backend test lives under `backend/tests/`, the only `pytest.ini` `testpaths` entry, so `pytest` collects the same suite whether it is run from the repository root or from `backend/`. The former top-level `tests/` folder was merged into it (#530); `scripts/check-test-catalogue.sh` fails if a test file appears there again.

## How to run

```bash
# Backend — unit suite (mocked datastores; honours pytest.ini testpaths)
python -m pytest -q                       # CI; or backend/.venv/bin/python -m pytest

# Backend — integration smoke (needs Postgres + ClickHouse; CI sets RUN_INTEGRATION=1)
python -m pytest backend/tests/integration -q

# Frontend — the full gate (matches CI)
cd frontend && npm run tsc && npm run lint && npm run test
```

## Continuous integration

`.github/workflows/ci.yml` runs the following jobs on every PR and push to `main`
(the security gates — `deps`, `secret-scan`, `codeql` — live in `.github/workflows/security.yml`):

| Job | What it runs | Notes |
| --- | --- | --- |
| **backend** | `pip install -r backend/requirements-dev.txt` → `ruff check .` → `mypy` → `scripts/generate-api-types.py --check` → `pytest -q` | Lint (`ruff.toml`), a type check of the clinical-critical modules listed in `mypy.ini`, a check that the frontend's generated API types (`frontend/src/lib/apiSchema.generated.ts`) match the backend's OpenAPI schema (#528), then the self-contained unit suite (no containers) with its coverage floors (#526). |
| **smoke** | `pytest backend/tests/integration` against `postgres:16` + `clickhouse/clickhouse-server:26.8` services | Real lifespan: schema init, migrations, admin seed, health probe. Records coverage for the `coverage` job. |
| **e2e** | `pytest backend/tests/e2e` against `postgres:16` + `clickhouse/clickhouse-server:26.8` services | Golden-trio pipeline + demo bundles vs documented expected results (see [TF-09c](regulatory/TF-09c-e2e-pipeline-verification.md)). Records coverage for the `coverage` job, like `smoke`. |
| **coverage** | combines the `backend`, `smoke` and `e2e` coverage data → `scripts/check-coverage-floor.py --combined` | Floors on the combined figure for the modules real datastores exercise (import pipeline, integrity anchors, sign-out), which the unit job under-reports (#526). Not yet a required check. |
| **frontend** | `npm ci` → `npm run tsc` → `npm run lint` → `npm run test:coverage` | Type-check + ESLint + vitest. The type-check includes `src/lib/apiContract.ts`: each hand-written API type in `apiTypes.ts` must accept the response shape the backend's schema declares (#528). The React hooks and jsx-a11y rules are errors; `npm run lint` also fails above its `--max-warnings` budget, which counts the remaining `any`s (#526). vitest fails below the coverage floors in `vite.config.mts`: global, and a separate one for `components/visualizations` (#526). |
| **e2e-playwright** | seed golden trio → Playwright drives Chromium against a uvicorn backend + the production frontend (built bundle served by `server.mjs`, with its CSP) | Browser journeys: login → family workspace → genome overview (see [TF-09d](regulatory/TF-09d-browser-e2e-verification.md), incl. a manual reproduction procedure for auditors). |
| **sbom** | CycloneDX SBOM for backend + frontend, uploaded as artifacts | Supply-chain evidence (see [sbom/README.md](../sbom/README.md)). |

All ten checks — `backend`, `frontend`, `smoke`, `e2e`, `e2e-playwright`, `catalogue`
(`ci.yml`) and `deps`, `secret-scan`, `codeql` ×2 (`security.yml`) — are **required
status checks** on `main` with **strict** (up-to-date-before-merge) enforcement, so a
change cannot merge unless they pass (TF-13 S-6).

### Browser end-to-end (Playwright)

`frontend/e2e/*.spec.ts` drive a real Chromium against a running stack: a uvicorn backend and the
production frontend, i.e. the built bundle served by `server.mjs` with its enforcing CSP and `/api`
proxy (#526). It is the only suite that exercises the actual UI, and every journey fails if the
browser reports a CSP violation (`e2e/fixtures.ts`). Run locally with the datastores up:

```bash
RUN_INTEGRATION=1 python scripts/seed_playwright_e2e.py   # golden trio + a known e2e user
cd frontend && E2E_PYTHON=/path/to/python npx playwright test
```

`E2E_FRONTEND=dev` runs the journeys against the Vite dev server instead (faster to iterate, but
no build and no CSP), and `E2E_BACKEND_PORT` moves the backend off port 8000 when it is taken.

| Spec | Journey |
| --- | --- |
| [frontend/e2e/auth.spec.ts](../frontend/e2e/auth.spec.ts) | Login form: bad credentials stay on /login; the seeded e2e user logs in and lands authenticated. |
| [frontend/e2e/family.spec.ts](../frontend/e2e/family.spec.ts) | Open the golden family workspace; render the genome overview (SVG/canvas tracks) — frontend↔backend wiring + viz render. |
| [frontend/e2e/security-headers.spec.ts](../frontend/e2e/security-headers.spec.ts) | The production frontend server sends its security headers: the CSP (`default-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`), `X-Content-Type-Options` and the `Permissions-Policy`. |
| [frontend/e2e/signout.spec.ts](../frontend/e2e/signout.spec.ts) | Clinical report sign-out from the browser: the seeded report-tagged variant renders, click sign-out, handle the drift confirm + sample-QC override dialog, and assert the signed-out version advances. |

---

## Backend tests

### Monogenic NIPT
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_nipt.py](../backend/tests/test_nipt.py) | NIPT family/trio identification and assay-key resolution. |
| [backend/tests/test_nipt_analysis.py](../backend/tests/test_nipt_analysis.py) | Fetal-fraction estimation (+ external-FF reconciliation), fetal-sex inference, 8-category site classification, empty-input fail-safe. |
| [backend/tests/test_nipt_coverage.py](../backend/tests/test_nipt_coverage.py) | On-target coverage summarization, weighted-median, low-coverage flagging. |
| [backend/tests/test_nipt_end_to_end.py](../backend/tests/test_nipt_end_to_end.py) | End-to-end NIPT on a demo VCF: FF + category recovery, recessive-at-risk. |
| [backend/tests/test_nipt_artifact_pg.py](../backend/tests/test_nipt_artifact_pg.py) | Recurrent-artifact list CRUD, auto-seed, per-assay scoping. |
| [backend/tests/test_nipt_package_import.py](../backend/tests/test_nipt_package_import.py) | Discovery/validation of NIPT family packages (manifest/PED, analysis-type tags). |
| [backend/tests/test_nipt_service.py](../backend/tests/test_nipt_service.py) | NIPT orchestration: observation building, filters, coverage, inheritance presets. The interval list, excluded intervals and excluded genes reach the variant load, and an unreadable interval is refused (422) before the cohort load (CR-080). |

### Expanded carrier screening / gene panels
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_panel_filter_constraints.py](../backend/tests/test_panel_filter_constraints.py) | Panel-filter constraint fetch and application in variant queries; stored regions read for the family's own assembly only, and none without a resolved assembly (#515). |
| [backend/tests/test_panel_region_assembly_scope.py](../backend/tests/test_panel_region_assembly_scope.py) | Panel coordinates per assembly (#515): a gene resolved in every assembly that has it (GRCh38 first), counts of genes not rows, regions stored with their assembly, PanelApp coordinates kept for the requested assembly only and dropped (with a message) when it is not loaded. |
| [backend/tests/test_panel_metadata_service.py](../backend/tests/test_panel_metadata_service.py) | Panel version management, JSONB parsing, name-uniqueness. |
| [backend/tests/test_panelapp_service.py](../backend/tests/test_panelapp_service.py) | PanelApp import parsing and summary extraction. |
| [backend/tests/test_gene_panel_versions.py](../backend/tests/test_gene_panel_versions.py) | Panel version creation, normalization, list/detail retrieval. |

### PGT / haplotype segregation
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_haplotype_lineage_service.py](../backend/tests/test_haplotype_lineage_service.py) | Pedigree-aware IBD haplotype colouring, homolog assignment, relative greying. |
| [backend/tests/test_phased_marker_service.py](../backend/tests/test_phased_marker_service.py) | Phased-marker retrieval, parent resolution, Mendel-error/informative-site QC. |
| [backend/tests/test_bed_service_lineage_precompute.py](../backend/tests/test_bed_service_lineage_precompute.py) | Genome-overview lineage precompute: fingerprint, origin-packed intervals, hash-guarded reads. |
| [backend/tests/test_bed_service.py](../backend/tests/test_bed_service.py) | Interval-track / lineage retrieval and sample-context handling. |

### Rare-disorder diagnostics (trio, SV, repeats, Paraphase, mtDNA)
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_de_novo_detection.py](../backend/tests/test_de_novo_detection.py) | Trio de-novo detection with confident hom-ref matching and full-trio requirement. |
| [backend/tests/test_hemizygous_de_novo.py](../backend/tests/test_hemizygous_de_novo.py) | A son's hemizygous call on chrX/chrY outside the PARs is a de novo candidate (#545, CR-084): haploid `1`, `1/1` or `0/1` with a confidently reference mother (X) or father (Y); the other parent need not be genotyped but must not carry the ALT; a duo suffices; the diploid rules in a PAR, in a daughter and off-scope; the de novo / dominant pattern, the SQL pre-filter (hemizygous branch only for an affected male on a known assembly) and the segregation mode. |
| [backend/tests/test_sv_gene_index.py](../backend/tests/test_sv_gene_index.py) | SV→gene index and SV second-hit (compound-het) summarization. |
| [backend/tests/test_mitochondrial_analysis.py](../backend/tests/test_mitochondrial_analysis.py) | mtDNA variant collection, heteroplasmy, maternal transmission, review attachment. |
| [backend/tests/test_paraphase_pg.py](../backend/tests/test_paraphase_pg.py) | Paraphase SMN metrics (copy number, read counts, haplotype groups). |
| [backend/tests/test_repeat_expansion_pg.py](../backend/tests/test_repeat_expansion_pg.py) | Repeat-expansion family table storage/retrieval (TRGT). |
| [backend/tests/test_presence_count_only.py](../backend/tests/test_presence_count_only.py) | `count_only` presence mode for repeat/Paraphase tables. |
| [backend/tests/test_structural_variant_breakends.py](../backend/tests/test_structural_variant_breakends.py) | Manual SV record parsing incl. remote breakend partners. |

### Variant classification (ACMG / CNV)
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_acmg_classification.py](../backend/tests/test_acmg_classification.py) | ACMG points, ClinGen thresholds, BA1 override, VUS tiers, no-evidence→VUS fail-safe. |
| [backend/tests/test_cnv_acmg_points.py](../backend/tests/test_cnv_acmg_points.py) | ClinGen-2019 CNV point system, class thresholds, empty/missing-flag/malformed handling. |

### Clinical traceability & audit
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_annotation_manifest.py](../backend/tests/test_annotation_manifest.py) | Annotation/version-manifest layer merge and canonical ordering; VCF-header refresh-merge semantics. |
| [backend/tests/test_vcf_header_provenance.py](../backend/tests/test_vcf_header_provenance.py) | VCF/TRGT header provenance parsing — caller/engine/DB versions (VEP/snpEff/bcftools/GATK/DeepVariant/Sniffles/Spectre/TRGT) and cross-modality merge. |
| [backend/tests/test_classification_drift.py](../backend/tests/test_classification_drift.py) | Evidence-snapshot build and drift detection across annotation versions. |
| [backend/tests/test_clinical_audit.py](../backend/tests/test_clinical_audit.py) | Clinical-audit diff generation (classification/tags/notes), one event per change. |
| [backend/tests/test_review_concurrency.py](../backend/tests/test_review_concurrency.py) | Optimistic concurrency for review saves (#513): a save against the loaded version goes ahead; an older version, a racing first write and a removed review are refused with 409 and the current review; a client without a version keeps the unconditional write; a stale small-variant or SV save writes nothing, and the per-variant lock is taken before the read. |
| [backend/tests/test_report_signout.py](../backend/tests/test_report_signout.py) | Sign-out canonical content-hash (order-independent), drift gate (acknowledgement needs a reason), versioning, reported SV/CNV frozen into the snapshot, the report-vs-latest-sign-out content check (#508), and the assembly-scope gate: an unvalidated or unresolved assembly is refused before anything is built or written, whatever else is acknowledged (#515). |
| [backend/tests/test_assembly_scope.py](../backend/tests/test_assembly_scope.py) | Validated-assembly scope (TF-06 H12, #515): GRCh38 inside by default, T2T/GRCh37/aliases/unknown outside, the scope follows `VALIDATED_ASSEMBLIES`, an empty scope validates nothing, and the refusal message names the assembly and the scope. |
| [backend/tests/test_hash_chain.py](../backend/tests/test_hash_chain.py) | Tamper-evidence hash-chain primitives (P1-4): canonical determinism, genesis anchoring, and `verify_chain` flagging content tampering / deletion / reordering. |
| [backend/tests/test_integrity_anchor.py](../backend/tests/test_integrity_anchor.py) | Signed chain-head anchor (P1-4 follow-up): Ed25519 sign/verify round-trip, unsigned fallback, deterministic head sort + order-independent anchor_root, `signed_core` isoformat. |
| [backend/tests/test_audit_log_pg.py](../backend/tests/test_audit_log_pg.py) | HTTP audit-log event JSONB serialization/storage. |
| [backend/tests/test_event_pipeline.py](../backend/tests/test_event_pipeline.py) | Audit/UI-event durability (TF-13 S-5): backpressure + synchronous fallback on a full queue, bounded batch-write retry, and never-silent drop accounting. |
| [backend/tests/test_small_variant_review_pg.py](../backend/tests/test_small_variant_review_pg.py) | Small-variant review persistence and payload serialization. |
| [backend/tests/test_structural_variant_review_pg.py](../backend/tests/test_structural_variant_review_pg.py) | Structural-variant reviews and their CNV (ClinGen 2019) classification (#526): the class and points are recomputed server-side, clamped per criterion, only accepted criteria count, unknown codes and kinds refused; unreadable stored classifications flagged; the save path (insert, update in place, delete when emptied, stale save refused, unknown tag refused, lock before read). |
| [backend/tests/test_small_variant_review_tags.py](../backend/tests/test_small_variant_review_tags.py) | Variant tag definitions (#526): key and colour validation, admin-only creation and deletion, built-in and duplicate labels refused, project scope and shared projects, listing order and project filtering, deletion as deactivation. |

### Access control & security
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_access_control.py](../backend/tests/test_access_control.py) | Project-scoped RBAC: viewer/admin access, cross-project visibility. |
| [backend/tests/test_access_rules.py](../backend/tests/test_access_rules.py) | The project-scoped access rules in `services/access_control.py` (#528): which roles are admins, the projects an admin or a member may see, and the 403 for a non-member. |
| [backend/tests/test_auth_rate_limit_pg.py](../backend/tests/test_auth_rate_limit_pg.py) | Failed-login throttling and reset; per-IP signup throttling (independent bucket). |
| [backend/tests/test_signup_throttle.py](../backend/tests/test_signup_throttle.py) | /auth/signup is rate-limited per IP (429 + Retry-After before any hash/create) and returns an identical generic 202 for new vs. already-registered emails (no account enumeration). A password under 15 characters is refused with 422 before throttle bookkeeping or account creation (CR-059). |
| [backend/tests/test_auth_login_timing.py](../backend/tests/test_auth_login_timing.py) | Login runs a dummy bcrypt verify on the no-such-account path so response time doesn't leak account existence; the equalizer hash is a valid bcrypt hash. |
| [backend/tests/test_auth_swagger_token.py](../backend/tests/test_auth_swagger_token.py) | Swagger/bearer token authentication. |
| [backend/tests/test_event_loop_offload.py](../backend/tests/test_event_loop_offload.py) | Slow CPU work runs off the event loop (#527): the bcrypt check at login and the per-gene phenotype scoring run in a worker thread; the signup notification goes to a configured `ADMIN_EMAIL` through `SMTP_HOST`, and is skipped when `ADMIN_EMAIL` is left at its default. |
| [backend/tests/test_password_hashing.py](../backend/tests/test_password_hashing.py) | Password hashing with bcrypt directly (#525): hashes stored by passlib verify, a password over 72 bytes still matches, new hashes keep the `$2b$12$` format, a value that is not a bcrypt hash never matches. |
| [backend/tests/test_reference_status_auth.py](../backend/tests/test_reference_status_auth.py) | `/assemblies/reference-status` requires auth (no unauthenticated provenance/operator-email leak). |
| [backend/tests/test_admin_role_checks.py](../backend/tests/test_admin_role_checks.py) | Every admin check counts `superuser` as an admin, as `ADMIN_ROLES` does: tags, gene panels, PED and manual families, import jobs, the gene profile and the user list admit both admin roles and refuse a viewer; a scan fails on any comparison of a role with the literal `"admin"`. |
| [backend/tests/test_admin_route_gating.py](../backend/tests/test_admin_route_gating.py) | Panel/tag-definition mutations enforce admin at the route (403 for a viewer); the dead `/admin/samples/{id}/projects` route is removed (404). |
| [backend/tests/test_config_security.py](../backend/tests/test_config_security.py) | Refuse-to-start on insecure default or weak secrets outside dev: a short `SECRET_KEY`, an empty `CLICKHOUSE_PASSWORD`, a missing or malformed `INTEGRITY_ANCHOR_SIGNING_KEY` (#522); a complete production configuration starts; CORS origin regex must compile and be fully anchored. |
| [backend/tests/test_backend_hardening.py](../backend/tests/test_backend_hardening.py) | Backend hardening (#522): the species list needs a signed-in user; no unsigned integrity anchor outside development; the HPO download is HTTPS-only, size-bounded, OBO-checked, SHA-256-pinnable and atomic; the knowledgebase script gets an allowlisted environment; QC-report link tokens are masked in the access log. |
| [backend/tests/test_clinical_cnv_kb_jobs.py](../backend/tests/test_clinical_cnv_kb_jobs.py) | The clinical CNV knowledgebase rebuild job (#526): status and the active job, one rebuild at a time (409), a missing script (503) or assembly (404), the background start, a successful build replacing the knowledgebase, a failed build leaving it untouched with the error and log tail recorded. |
| [backend/tests/test_client_ip.py](../backend/tests/test_client_ip.py) | Client address behind trusted proxies (#520): taken `TRUSTED_PROXY_HOPS` entries from the right of `X-Forwarded-For`, never the client-settable left-most; repeated headers form one chain; a short chain or no trust leaves the request as it was. |
| [backend/tests/test_deployment_config.py](../backend/tests/test_deployment_config.py) | Configuration guards (#520): compose publishes databases and the API on loopback only and gives the frontend no `.env`; CI service images are digest-pinned and `markdown` is version-pinned; compose, every CI job and the Terraform VM run the same Postgres and ClickHouse images (#524); Terraform mounts refdata read-only, requires TLS 1.2+, sets the proxy hops and import roots; one deploy trigger with persistent tfvars. |
| [backend/tests/test_coga_logging.py](../backend/tests/test_coga_logging.py) | `scrub_log` control-character neutralization (CWE-117 log forging): CR/LF/tab/DEL replaced, clean values pass through, oversized truncated. |
| [backend/tests/test_request_logging.py](../backend/tests/test_request_logging.py) | Request-logging sanitization, path/secret masking, db-update derivation (incl. stripping the `/api` mount prefix so the audit entity is the real collection). |
| [backend/tests/test_http_resilience.py](../backend/tests/test_http_resilience.py) | P2-9 outbound resilience: idempotent requests retry transient failures (transport errors + 429/5xx) with capped backoff; non-idempotent + 4xx never retry; bounded worst case; own-client body still readable; S3 Config has bounded timeouts + adaptive retries. |
| [backend/tests/test_csv_export.py](../backend/tests/test_csv_export.py) | CSV formula-injection guard: cells starting with `=`/`+`/`-`/`@`/tab/CR/LF are quote-prefixed; benign values untouched; survives a real csv writer/reader round-trip. |
| [backend/tests/test_html_sanitize.py](../backend/tests/test_html_sanitize.py) | Reference-HTML stored-XSS sanitiser: strips `script`/`img`/event-handlers and `javascript:` hrefs to a strict allowlist while keeping safe formatting and safe links. |

### Ingestion / storage / ClickHouse / files
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_clickhouse.py](../backend/tests/test_clickhouse.py) | ClickHouse client query/command/insert dispatch and recording. |
| [backend/tests/test_clickhouse_dataset_key.py](../backend/tests/test_clickhouse_dataset_key.py) | Dataset-key normalization and injection-safe handling. |
| [backend/tests/test_genotypes.py](../backend/tests/test_genotypes.py) | One genotype classification (#511): every genotype in exactly one of hom-alt / het / hom-ref / no-call (haploid, multi-allelic, half calls, malformed), the short vocabulary is exhaustive and partitioned, the SQL condition shape (no-call as the complement), and sample filters and inheritance (X-linked hemizygous son, multi-allelic homozygote, haploid carrier ruling out dominant, X-linked SQL params) on haploid chrM/chrX and multi-allelic calls. |
| [backend/tests/test_sex_chromosomes.py](../backend/tests/test_sex_chromosomes.py) | Where a male is hemizygous (#545, CR-084): the GRC PAR1/PAR2 bounds of GRCh38 and GRCh37, 1-based and inclusive, on X and Y; chromosome names (`chrX`, `23`, …); assembly aliases; no hemizygous positions on an assembly with unknown PARs. |
| [backend/tests/test_clickhouse_family_variants.py](../backend/tests/test_clickhouse_family_variants.py) | Small-variant query, genotype/phasing parsing, inheritance + compound-het. The SV track (track mode) gets the total and the candidate cap, and asks SQL for its sample's SVs only, while the SV table's query is unchanged (#585, CR-063). The small-variant payload says where a male is hemizygous (`hemizygous_in_males`, CR-085). |
| [backend/tests/test_compound_het_phasing.py](../backend/tests/test_compound_het_phasing.py) | Read-backed compound-het phase: cis pairs dropped, trans labelled, haplotypes compared only within a phase set, hom/multi-allelic/unphased calls left unresolved. |
| [backend/tests/test_clickhouse_integrity.py](../backend/tests/test_clickhouse_integrity.py) | ClickHouse table-integrity / detached-parts checks. |
| [backend/tests/test_clickhouse_interval_tracks.py](../backend/tests/test_clickhouse_interval_tracks.py) | Interval-track presence detection and region filtering. |
| [backend/tests/test_clickhouse_variant_storage_ops.py](../backend/tests/test_clickhouse_variant_storage_ops.py) | Variant-assembly listing, table dedup, mutation status. |
| [backend/tests/test_clickhouse_integrity_monitor.py](../backend/tests/test_clickhouse_integrity_monitor.py) | P2-6 scheduled integrity monitor: sweep caches + escalates by status (ERROR on corrupt, WARN on degraded), survives list/check exceptions, disabled is no-op, worker sweeps then stops cleanly. |
| [backend/tests/test_track_availability_presence.py](../backend/tests/test_track_availability_presence.py) | P2-1a: the aggregated small-variant presence query (track availability) — real query construction + per-sample explicit/non-ref logic + id→name mapping + empty short-circuit (CH execute mocked). |
| [backend/tests/test_object_storage.py](../backend/tests/test_object_storage.py) | S3 URI parsing and object-storage helpers. |
| [backend/tests/test_s3_import_and_cram.py](../backend/tests/test_s3_import_and_cram.py) | S3 source authorization; CRAM/BAM **access-before-serve** and sample resolution. |
| [backend/tests/test_raw_import_file_verify.py](../backend/tests/test_raw_import_file_verify.py) | File SHA-256 verification: verified / mismatch / missing / unverifiable. |
| [backend/tests/test_variant_upload_haplotype_golden.py](../backend/tests/test_variant_upload_haplotype_golden.py) | The haplotype blocks of five seeded families (quartet segregation at default and unit thresholds, no father so per-phase-set blocks, sparse sites with switch-error bursts, chromosome sizes known), block for block against a fixture recorded from the in-line builder before it was extracted (#528). |
| [backend/tests/test_small_variant_page_golden.py](../backend/tests/test_small_variant_page_golden.py) | Characterisation of `get_family_small_variants_page` (#528), recorded before it was split up (`fixtures/small_variant_page_golden.json`, 51 KB). 26 scenarios cover each path: the presence probe, the scope that matches nothing (panel, review, intervals), every scope filter at once, the prioritised ranking, the capped track (over, estimated, empty, a page), the native page (a page, an empty page, a clamped huge page, an SV second hit) and the Python path (compound het, recessive with a gene, carrier screening, track sampling, capped candidates). Each records every collaborator call, in order, with its arguments, and the page returned. Regenerate with `COGA_REGENERATE_GOLDEN=1` only for an intended change (CR-068). An unparseable interval list is recorded as a refusal (422), and a list of blank lines as no restriction (CR-080). |
| [backend/tests/test_interval_list_parsing.py](../backend/tests/test_interval_list_parsing.py) | The small-variant interval lists (#604): each entry read as written (hyphen or en dash, single positions, thousands separators, blank entries ignored); an unreadable entry (a BED line, a missing end, an end before its start) refused (422) and named, the whole list rather than part of it (CR-080). |
| [backend/tests/test_variant_upload_service.py](../backend/tests/test_variant_upload_service.py) | VCF/VEP parsing → ClickHouse ingestion; phased-block (PS) preservation. |
| [backend/tests/test_upload_safety.py](../backend/tests/test_upload_safety.py) | Bounded upload decode: read/decompressed size caps reject oversized uploads and gzip bombs (413); corrupt gzip / non-UTF-8 → 400. |
| [backend/tests/test_variant_upload_gene_lookup.py](../backend/tests/test_variant_upload_gene_lookup.py) | Gene lookup during variant upload: distinct sorted overlaps per window; one grouped query per chromosome set, none for empty input. |
| [backend/tests/test_clickhouse_variant_storage.py](../backend/tests/test_clickhouse_variant_storage.py) | Family-scoped variant counting and query building; every small-variant mutation stamps a new family data version (#509). |
| [backend/tests/test_raw_import_files_pg.py](../backend/tests/test_raw_import_files_pg.py) | Raw-import file-size limits and SHA-256 verification. |

### Gene / HPO / panel / Monarch / reference
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_bounded_download.py](../backend/tests/test_bounded_download.py) | Bounded outbound download + gunzip for reference/gene/Monarch fetches (#336): gunzip roundtrip / oversized-output / invalid-stream / a real decompression bomb halted at the cap; streamed size-cap abort, optional-404, and 5xx paths. |
| [backend/tests/test_gene_info_bulk_sources.py](../backend/tests/test_gene_info_bulk_sources.py) | Gene-reference bulk sources: ClinGen validity and dosage rows (download banner skipped), GenCC and ClinVar gene–condition rows, dbNSFP constraint metrics (scientific notation kept) and OMIM, the HGNC set and symbol history, dbNSFP-first bundle with online fallback, per-source release labels and consulted status. |
| [backend/tests/test_gene_info_external.py](../backend/tests/test_gene_info_external.py) | Outbound gene-lookup URL encoding (#336): quote(safe='') escapes '/' so an injected identifier stays in one path segment on the fixed external hosts; legitimate ids unchanged. |
| [backend/tests/test_gene_locus_primary_chromosome.py](../backend/tests/test_gene_locus_primary_chromosome.py) | Primary-chromosome / multi-contig gene-locus resolution. |
| [backend/tests/test_hpo_api.py](../backend/tests/test_hpo_api.py) | HPO router and family HPO-term attachment. |
| [backend/tests/test_hpo_service.py](../backend/tests/test_hpo_service.py) | HPO ontology parsing, closure computation, inline-HPO handling. |
| [backend/tests/test_monarch_ingest.py](../backend/tests/test_monarch_ingest.py) | Monarch gene-disease/phenotype association parsing. |
| [backend/tests/test_monarch_search_api.py](../backend/tests/test_monarch_search_api.py) | Monarch disease/phenotype search endpoint. |
| [backend/tests/test_monarch_semsim.py](../backend/tests/test_monarch_semsim.py) | Monarch semantic-similarity phenotype matching/normalization. |
| [backend/tests/test_hpo_family_annotation_import.py](../backend/tests/test_hpo_family_annotation_import.py) | Family HPO annotation import: batched inserts, duplicate conflict keys collapsed, unknown terms skipped without an insert. |
| [backend/tests/test_gene_info_jobs_pg.py](../backend/tests/test_gene_info_jobs_pg.py) | Gene-reference background refresh job (queue/run/state). |
| [backend/tests/test_reference_metadata_service.py](../backend/tests/test_reference_metadata_service.py) | Reference metadata aggregation and transcript resolution. |
| [backend/tests/test_gencode_import.py](../backend/tests/test_gencode_import.py) | GENCODE GTF → gene rows: release preamble, per-transcript rows, biotypes/identifiers, MANE tags, exon grouping, RefSeq accessions. |
| [backend/tests/test_gene_metadata_transcripts.py](../backend/tests/test_gene_metadata_transcripts.py) | Transcript MANE / Ensembl-canonical flags read from the annotation's own tags. |
| [backend/tests/test_reference_source_service.py](../backend/tests/test_reference_source_service.py) | Reference import-source management and assembly metadata. |

### Variant prioritization & explorer
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_variant_prioritization.py](../backend/tests/test_variant_prioritization.py) | Exomiser-style scoring (pathogenicity, frequency, segregation, mode combinations). |
| [backend/tests/test_variant_ranking_cache.py](../backend/tests/test_variant_ranking_cache.py) | Ranking-cache invalidation keyed by family/filters, the family's variant-data version and the scoring reference releases (HPO, gene_info) (#509). |
| [backend/tests/test_variant_explorer_service.py](../backend/tests/test_variant_explorer_service.py) | Cross-project variant aggregation, ranking, carrier pagination. The ClinVar P/LP rescue lifts the frequency ceilings on the family search's terms, and adds nothing without a ceiling (CR-057). |
| [backend/tests/test_variant_explorer_router.py](../backend/tests/test_variant_explorer_router.py) | Variant-explorer export column formatting. Sample genotype values whose sample id contains `:` (CR-057). |
| [backend/tests/test_variant_explorer_carriers.py](../backend/tests/test_variant_explorer_carriers.py) | Variant-explorer carrier lists (truncation flag, exact count under the cap) and annotation display mapping. |

### Sample QC
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_sample_integrity_qc.py](../backend/tests/test_sample_integrity_qc.py) | Relatedness, paternity, sex inference, Mendelian consistency, QC aggregation (sample-swap/data-integrity). |
| [backend/tests/test_sample_integrity_service.py](../backend/tests/test_sample_integrity_service.py) | Family-scoped sample-integrity report generation. |
| [backend/tests/test_family_structure_validation.py](../backend/tests/test_family_structure_validation.py) | Pedigree-graph validation and parent-sex consistency. |

### SQL / driver contracts
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_sql_parameter_typing.py](../backend/tests/test_sql_parameter_typing.py) | Rejects `CASE WHEN :param IS NULL THEN NULL ELSE CAST(:param …)`, which asyncpg cannot assign a parameter type to. This form silently broke every annotation-manifest write from the day provenance capture was added; a mocked-session test cannot catch it. |

### Pedigree / family metadata / package import
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_family_metadata_api.py](../backend/tests/test_family_metadata_api.py) | Family-metadata API routes. |
| [backend/tests/test_family_metadata_context.py](../backend/tests/test_family_metadata_context.py) | Family-metadata context building from DB rows. |
| [backend/tests/test_family_member_batch_update_service.py](../backend/tests/test_family_member_batch_update_service.py) | Batch member updates with dirty-tracking. |
| [backend/tests/test_family_member_detail_service.py](../backend/tests/test_family_member_detail_service.py) | Member detail retrieval and impact analysis. |
| [backend/tests/test_family_package_import_pcf.py](../backend/tests/test_family_package_import_pcf.py) | PCF (array-CGH) dataset discovery and segment parsing; manifest availability probe stays inside the package root. |
| [backend/tests/test_qc_threshold_service.py](../backend/tests/test_qc_threshold_service.py) | Sequencing-QC threshold evaluation: bound semantics per direction, an unmeasured or unconfigured metric reported as `skip` rather than `pass`, inverted bounds still failing, the worst-metric rollup, and the mitochondrial cut-offs reading the same configuration instead of module constants. |
| [backend/tests/test_family_package_long_read.py](../backend/tests/test_family_package_long_read.py) | Long-read (nf-core/lrsvar) packages: version-tolerant glob discovery inside the package root, VCF sample-column resolution (`Sample0`, `<sample>_sort`), CNV/mito/QC/pipeline-info parsing, and a validator for every supported dataset type. |
| [backend/tests/test_family_import_compensation.py](../backend/tests/test_family_import_compensation.py) | Failed-import compensation: shell delete clears interval tracks; incomplete-flag set/clear on family metadata. |
| [backend/tests/test_family_pedigree_generation.py](../backend/tests/test_family_pedigree_generation.py) | Pedigree file generation / LINKAGE output. |
| [backend/tests/test_family_structure_update_dirty.py](../backend/tests/test_family_structure_update_dirty.py) | Family-structure update with member dirty-tracking. |
| [backend/tests/test_manual_family_metadata.py](../backend/tests/test_manual_family_metadata.py) | Manual family creation and pedigree threading. |
| [backend/tests/test_families_export.py](../backend/tests/test_families_export.py) | Family export cell formatting (reviews/genotypes). |
| [backend/tests/test_family_metadata_context_queries.py](../backend/tests/test_family_metadata_context_queries.py) | Family-metadata context queries: distinct-sample ordering by the selected column; UUID project filter for visible samples. |
| [backend/tests/test_family_package_import.py](../backend/tests/test_family_package_import.py) | Package discovery, validation, manifest loading. |
| [backend/tests/test_dataset_importer_registry.py](../backend/tests/test_dataset_importer_registry.py) | Package-import dataset importers (#528): every type in `SUPPORTED_DATASETS` has exactly one registered importer, and a second registration is refused. Each type reaches its importer with the whole job (session, bundle, manifest entry, summary, contexts, conflict mode, progress). A disabled or absent dataset is not imported, an enabled type without an importer fails loudly, and phenotypes are imported without a manifest dataset (CR-071). |
| [backend/tests/test_family_package_remote_paths.py](../backend/tests/test_family_package_remote_paths.py) | A queued import from a bucket keeps its `gs://` / `s3://` URI (`Path` used to collapse it to `gs:/…`, which the worker then ran as a local path); local folders still expand `~`. |
| [backend/tests/test_family_service.py](../backend/tests/test_family_service.py) | Family ROI payload gene-query ordering. |
| [backend/tests/test_ped_service.py](../backend/tests/test_ped_service.py) | Pedigree service parsing/standardization. |

### Integration / smoke
| Test file | Purpose |
| --- | --- |
| [backend/tests/integration/test_app_startup.py](../backend/tests/integration/test_app_startup.py) | Boots the app against real Postgres + ClickHouse (schema init, admin seed) and asserts it serves. |
| [backend/tests/integration/test_genotype_classes_clickhouse.py](../backend/tests/integration/test_genotype_classes_clickhouse.py) | #511 on real ClickHouse: every short genotype string and a set of long/malformed ones land in exactly the class `classify_genotype` gives them, through the SQL set lookup and its allele-by-allele fallback. |
| [backend/tests/integration/test_hemizygous_positions_clickhouse.py](../backend/tests/integration/test_hemizygous_positions_clickhouse.py) | #545 on real ClickHouse: the de novo pre-filter's hemizygous-position condition agrees with `hemizygous_chromosome` on both sides of every PAR boundary, under each chromosome name, for GRCh38 and GRCh37. |
| [backend/tests/integration/test_clickhouse_integrity.py](../backend/tests/integration/test_clickhouse_integrity.py) | Validates integrity-check SQL against a real ClickHouse server. |
| [backend/tests/integration/test_append_only_triggers.py](../backend/tests/integration/test_append_only_triggers.py) | Fires the append-only triggers on `audit_log_events`/`clinical_audit_events`/`report_signouts`: UPDATE/DELETE rejected; the FK→NULL unlink carve-out allowed (and nothing else); signed report frozen. |
| [backend/tests/integration/test_hash_chain_integration.py](../backend/tests/integration/test_hash_chain_integration.py) | End-to-end per-family hash chains (P1-4): real writers produce a chain `verify_*_chain` accepts; a privileged trigger-bypass UPDATE/DELETE is detected & localised; the FK→NULL carve-out does not break the chain. |
| [backend/tests/integration/test_app_role_privileges.py](../backend/tests/integration/test_app_role_privileges.py) | P1-3 privilege separation: the restricted `coga_app` role may INSERT/SELECT the append-only tables (and delete users — the FK cascade still nulls the audit FK) but is denied UPDATE/DELETE and DISABLE TRIGGER on them. |
| [backend/tests/integration/test_app_boots_as_restricted_role.py](../backend/tests/integration/test_app_boots_as_restricted_role.py) | P1-3 deployability: migrates out-of-band as the owner, then boots the whole app AS `coga_app` with startup DDL disabled — it serves without owner-only DDL, and the append-only REVOKE holds on a real `coga_app` login. |
| [backend/tests/integration/test_integrity_anchor_integration.py](../backend/tests/integration/test_integrity_anchor_integration.py) | Signed chain-head anchor end-to-end: a real signed anchor verifies; an owner-bypass re-chain (recomputed head row_hash) or truncation (deleted head) diverges from it; a tampered anchor signature is caught; anchors chain (prev_anchor_hash). |
| [backend/tests/integration/test_panel_regions_per_assembly.py](../backend/tests/integration/test_panel_regions_per_assembly.py) | #515 on real Postgres: per-assembly resolution and storage, a family's panel filter never reads another assembly's coordinates, and a pre-#515 `gene_panel_regions` table is upgraded in place (rows attributed or dropped, assembly in the primary key, idempotent). |
| [backend/tests/integration/test_gene_search_indexes.py](../backend/tests/integration/test_gene_search_indexes.py) | P2-3: the gene-search expression indexes serve their real queries — seeds ~600 genes/gene_info + ANALYZE, then EXPLAINs the actual autocomplete / panel / region-OR / candidate-OR / gene_info-constraint shapes asserting no Seq Scan on genes/gene_info (and the autocomplete is pinned to its prefix index). |
| [backend/tests/integration/test_track_availability_presence_integration.py](../backend/tests/integration/test_track_availability_presence_integration.py) | P2-1a end-to-end (real ClickHouse): ingests a tiny small-variant fixture and asserts the aggregated per-sample presence — non-ref counts, ref does not, chromosome filter scopes correctly. |
| [backend/tests/integration/test_variant_explorer_keyset_integration.py](../backend/tests/integration/test_variant_explorer_keyset_integration.py) | P2-4b end-to-end (real ClickHouse): pages five variants (incl. a carrier-count tie) at page_size 2 via next_cursor — every variant once, non-increasing sort across page boundaries, correct page count — validating the HAVING seek + cursor round-trip. |
| [backend/tests/integration/test_gcs_storage_integration.py](../backend/tests/integration/test_gcs_storage_integration.py) | GCS storage backend end-to-end against fake-gcs-server (real JSON API): seeds a bucket and asserts `object_exists`, `download_prefix` (nested-prefix layout), and remote package discovery (`list_remote_package_candidates` — manifest vs `.ped` vs neither). Endpoint from `GCS_ENDPOINT_URL`, else a container started via docker. Complements the mocked unit tests; signed-URL signing stays unit-only (needs real IAM SignBlob). |

### End-to-end (golden pipeline)
| Test file | Purpose |
| --- | --- |
| [backend/tests/e2e/test_e2e_import_golden.py](../backend/tests/e2e/test_e2e_import_golden.py) | M1 golden-trio E2E (real Postgres + ClickHouse): imports a hand-curated package through `execute_family_package_import` and asserts every stage against `fixtures/golden_trio/EXPECTED.yaml` — datasets imported, pedigree/roles/HPO persisted, SNV count, compound-het pairing, SV ingest, SNV+SV second-hit (trans/deletion-unmasked), repeat pathogenic classification, coverage tracks, Paraphase. Doubles as an IVDR verification dataset. The fixture is regenerated by [scripts/generate_golden_trio.py](../scripts/generate_golden_trio.py). |
| [backend/tests/e2e/test_e2e_api_contract.py](../backend/tests/e2e/test_e2e_api_contract.py) | M2 API-contract E2E (real stack, in-loop ASGI client): drives the viz-feeding HTTP endpoints over the golden-trio data and asserts their contracts — auth 401, small-variant page (`_id` alias, SNV/INDEL types, offset paging, compound-het groups), structural-variant page (summary, `length==svLen`, BND, track-mode null-omission), structural-variant-lengths, shared-SV counts symmetry, BED json/text + 400/404, haplotypes + batch, track-availability, and the variant explorer (carrier counts, MNV classification, keyset pagination + carriers); a ClinVar-pathogenic variant survives a frequency ceiling in ClickHouse only with the override on (#534). |
| [backend/tests/e2e/test_e2e_review_audit.py](../backend/tests/e2e/test_e2e_review_audit.py) | M3 review/audit/sign-out E2E (real stack): PUTs a clinical review on the P/LP variant and asserts server-side ACMG recompute (bogus client totals ignored) + note/tags persisted; the review emits chained `clinical_audit_events` and `/admin/integrity/verify` re-walks them to `verified=true`; report sign-out versions (v→v+1) with a verified sign-out chain; the sign-out check matches straight after signing (real JSONB round-trip) and flags a review edited afterwards (#508). Chain tamper-evidence (trigger-bypass edit flips `verified` to false) and append-only immutability are exercised on a throwaway family via the direct audit service so FAM_TRIO's chain is never corrupted. |
| [backend/tests/e2e/test_e2e_failure_modes.py](../backend/tests/e2e/test_e2e_failure_modes.py) | M4 failure/degradation + job-lifecycle E2E (real stack): manifest→missing-file and PED family-mismatch fail validation with no family created; a malformed VCF body fails on import and is reported failed with the freshly-created family shell rolled back (the M4 fail-clean hardening, asserted end-to-end); overwrite re-import does not duplicate rows; and the queue→claim→run worker path reaches a terminal status (`completed` for a good package, `failed` + no partial family for a malformed one). Each scenario uses a fresh family/sample id so the shared dev DB stays clean. |
| [backend/tests/e2e/test_e2e_demo_smoke.py](../backend/tests/e2e/test_e2e_demo_smoke.py) | M5 realistic demo-bundle smoke (Tier 2, real stack): imports a uniquely-suffixed copy of `demo/nipt_family` via the package importer, seeds the recurrent-artifact site, and runs the family NIPT analysis over the imported data — recovers fetal fraction ≈12%, 40 paternal sites, and the **exact** `category_counts` {1:1,…,7:40,8:1} and `filter_counts` {total_in:49, passed:47, failed_quality:1, failed_artifact:1}; and loads `demo/quartet_family` through the real `scripts/load_demo_quartet` loader (the direct-upload path) asserting small variants / SVs / repeats / coverage all ingest. Catches integration breakage the tiny golden trio can't surface. |
| [backend/tests/e2e/test_e2e_ranking_cache.py](../backend/tests/e2e/test_e2e_ranking_cache.py) | Prioritised-ranking cache E2E (real stack, in-loop ASGI client, #509): opens the prioritised small-variant view twice over the golden trio (second open served from cache), deletes the family's small variants through the admin data endpoint, and asserts the storage-level data version moved and the next open recomputes (`ranking_cached=false`, total 0) instead of serving the old ranking; re-imports the golden trio afterwards. |
| [backend/tests/e2e/test_e2e_haplotypes.py](../backend/tests/e2e/test_e2e_haplotypes.py) | Haplotype/lineage stage E2E (real stack): imports a phased GLIMPSE2 trio (its own family, so it doesn't clobber the golden trio's clair3 SNVs), then asserts haplotype interval-track blocks are ingested, the genome-wide lineage precompute runs and produces blocks, and the `/families/{id}/haplotypes` endpoint serves phased segments. Robust (blocks/precompute/endpoint) rather than exact IBD colours (those are unit-tested). |

### API / config / infra / other
| Test file | Purpose |
| --- | --- |
| [backend/tests/test_api_route_prefix.py](../backend/tests/test_api_route_prefix.py) | API route-prefix consistency via the OpenAPI schema. |
| [backend/tests/test_api_id_schemas.py](../backend/tests/test_api_id_schemas.py) | UUID/date/enum schema validation for API payloads. |
| [backend/tests/test_async_blocking_io.py](../backend/tests/test_async_blocking_io.py) | Concurrent manifest resolution, reference-cache, and blocking-IO offload. |
| [backend/tests/test_health_endpoint.py](../backend/tests/test_health_endpoint.py) | Health endpoint liveness without datastores. |
| [backend/tests/test_postgres_schema_loader.py](../backend/tests/test_postgres_schema_loader.py) | SQL splitting and dollar-quoted PL/pgSQL handling (migration loader). |
| [backend/tests/test_postgres_engine.py](../backend/tests/test_postgres_engine.py) | Postgres engine selection: Cloud SQL Python Connector (verify-full TLS) when enabled vs DSN when disabled. |
| [backend/tests/test_github_releases_service.py](../backend/tests/test_github_releases_service.py) | GitHub release summary/changelog formatting. |
| [backend/tests/test_ui_events.py](../backend/tests/test_ui_events.py) | UI-event path masking, label sanitization, batch insert. |
| [backend/tests/test_apcad_band_targets.py](../backend/tests/test_apcad_band_targets.py) | APCAD downsample budget allocation across BAF bands. |
| [backend/tests/test_apcad_origin_fallback.py](../backend/tests/test_apcad_origin_fallback.py) | APCAD origin preference: unphased (HiFiCNV MAF) tracks fall back to `und`, phased tracks keep the filter and stay empty where no informative markers fall. |
| [backend/tests/test_family_package_bigwig.py](../backend/tests/test_family_package_bigwig.py) | bigWig reading (primary contigs only, karyotype order, zero-bin skipping) and which HiFiCNV file feeds which interval track. |
| [backend/tests/test_family_package_cnv_callers.py](../backend/tests/test_family_package_cnv_callers.py) | CNV-caller discovery (QDNAseq lrsvar BED and legacy CSV layouts, WisecondorX sample-prefixed names) and interval-track ownership on re-import. |
| [backend/tests/test_coverage_normalization.py](../backend/tests/test_coverage_normalization.py) | Autosomal-median normaliser and the depth→log2-ratio transform that puts three CNV callers' coverage on one axis. |
| [backend/tests/test_signal_track_router.py](../backend/tests/test_signal_track_router.py) | Signal-file serving for IGV: recorded-path resolution, containment against crafted family ids and paths, and the per-kind axis spec. |
| [backend/tests/test_admin_clickhouse_listing.py](../backend/tests/test_admin_clickhouse_listing.py) | Admin ClickHouse listing and variant-count aggregation. |
| [backend/tests/test_config.py](../backend/tests/test_config.py) | Settings parsing: list settings (`CORS_ORIGINS`, `FAMILY_IMPORT_ROOTS`, `VALIDATED_ASSEMBLIES`) read from the real process environment in comma-separated and JSON form, the GRCh38-only validated-assembly default, and build identity. |
| [backend/tests/test_small_variant_clinvar_frequency_rescue.py](../backend/tests/test_small_variant_clinvar_frequency_rescue.py) | ClinVar P/LP rescue overriding frequency thresholds, in the Python matcher AND the ClickHouse filter (same terms); ClinVar's `Pathogenic/Likely_pathogenic` aggregate split into its terms (#534). |
| [backend/tests/test_variant_export_truncation.py](../backend/tests/test_variant_export_truncation.py) | CSV exports lift the page clamp to the export cap, report truncation beyond it (incl. a truncated ranking), flatten compound-het groups, and name a truncated file as such (#512). |
| [backend/tests/test_small_variant_review_payload.py](../backend/tests/test_small_variant_review_payload.py) | Review payload datetime serialization / bigint truncation. |
| [backend/tests/test_structural_panel_gene_match.py](../backend/tests/test_structural_panel_gene_match.py) | SV gene-overlap matching and panel-filter constraints. |
| [backend/tests/test_structural_variant_track_slim.py](../backend/tests/test_structural_variant_track_slim.py) | SV track slimming (annotations dropped in track mode). |
| [backend/tests/test_structural_variant_ingest.py](../backend/tests/test_structural_variant_ingest.py) | SV record iterators coerce coordinates and skip malformed records rather than aborting ingest. |
| [backend/tests/test_variant_annotation_parser.py](../backend/tests/test_variant_annotation_parser.py) | VCF annotation parsing incl. SpliceAI / scientific notation. |
| [backend/tests/test_repeat_contraction_loci.py](../backend/tests/test_repeat_contraction_loci.py) | Repeat loci pathogenic by contraction (VWA1, MIR7-2): a benign count is never flagged, expansion loci keep their thresholds, and pathogenic_max is not treated as a ceiling. |
| [backend/tests/test_sv_filter_before_cap.py](../backend/tests/test_sv_filter_before_cap.py) | SV region push-down: gene/panel regions reach the SQL so the candidate cap bounds the ranking rather than deciding what the filter sees; span overlap, coordinate normalisation, dedupe. |
| [backend/tests/test_variant_cytoband_labels.py](../backend/tests/test_variant_cytoband_labels.py) | Cytoband lookup shared by small and structural variants: band per position, multi-band spans, chromosome aliases. |
| [backend/tests/test_variant_annotation_extraction.py](../backend/tests/test_variant_annotation_extraction.py) | Small-variant annotation extraction from the VEP `CSQ` field, the INFO fallback and VEP TSV entries. |
| [backend/tests/test_admin_inventory.py](../backend/tests/test_admin_inventory.py) | Data-inventory admin endpoint, assembly-scoped counting. |
| [backend/tests/test_admin_service.py](../backend/tests/test_admin_service.py) | Admin service listing and family-count aggregation. |
| [backend/tests/test_backend_hotpath_cleanups.py](../backend/tests/test_backend_hotpath_cleanups.py) | Hot-path cleanups: DDL memoization, HPO table-probe caching. |
| [backend/tests/test_load_demo_quartet.py](../backend/tests/test_load_demo_quartet.py) | Demo-bundle loading and family-definition manifest parsing. |

---

## Frontend tests

### `lib/` — pure logic
| Test file | Purpose |
| --- | --- |
| [frontend/src/__tests__/serverSecurityHeaders.test.ts](../frontend/src/__tests__/serverSecurityHeaders.test.ts) | SPA security response headers + enforcing CSP (IGV/S3/fonts-aware); HSTS opt-in. |
| [frontend/src/__tests__/serverProxy.test.ts](../frontend/src/__tests__/serverProxy.test.ts) | The `/api` proxy against a local backend (#521): survives an upstream reset mid-stream, answers 504 when the backend hangs, sends one clean client address rather than a client-supplied `X-Forwarded-For`, and does not name the backend address in its 502; forwarded-header resolution with and without trusted proxy hops. |
| [frontend/src/lib/__tests__/useMeasuredWidth.test.tsx](../frontend/src/lib/__tests__/useMeasuredWidth.test.tsx) | Viewer width measurement: content box (not border box), re-measure when a background tab becomes visible, ignore zero, listener cleanup. |
| [frontend/src/lib/__tests__/api.test.ts](../frontend/src/lib/__tests__/api.test.ts) | Auth-header attachment and error normalization. |
| [frontend/src/lib/__tests__/trackFetch.test.ts](../frontend/src/lib/__tests__/trackFetch.test.ts) | Track payload fetch through the shared API client without re-prefixing the base; a "no data" 404 is empty only where allowed, every other failure throws (#510). |
| [frontend/src/lib/__tests__/useSameSpanFallbackData.test.ts](../frontend/src/lib/__tests__/useSameSpanFallbackData.test.ts) | Pan fallback holds data only for the same span AND scope — never one chromosome's data under another (#510). A failure never falls back to held data, not even a failed refetch that still carries the last answer; loading again may (#586, CR-064). |
| [frontend/src/lib/__tests__/csvExport.test.ts](../frontend/src/lib/__tests__/csvExport.test.ts) | Export headers → file name and warning: a capped export is saved as TRUNCATED and announced (#512). |
| [frontend/src/lib/__tests__/coverageSources.test.ts](../frontend/src/lib/__tests__/coverageSources.test.ts) | CNV-caller display order, labels for known and unknown callers, and when a coverage track names its caller. |
| [frontend/src/styles/__tests__/controlOverrides.test.ts](../frontend/src/styles/__tests__/controlOverrides.test.ts) | CSS specificity guard: a rule that resizes a form control must actually beat the shared `input:not([type=…])` rule, whose `:not()` arguments make it (0,2,1). |
| [frontend/src/lib/__tests__/auth.test.ts](../frontend/src/lib/__tests__/auth.test.ts) | Session persistence; token/role/username storage; admin/auth checks. |
| [frontend/src/lib/__tests__/chromosomes.test.ts](../frontend/src/lib/__tests__/chromosomes.test.ts) | Natural chromosome ordering (numeric + X/Y/MT); `normalizeChrom`, and `formatChromosomeLabel`, which names the mitochondrion chrM however the data spells it (CR-075). |
| [frontend/src/lib/__tests__/cnvAcmg.test.ts](../frontend/src/lib/__tests__/cnvAcmg.test.ts) | CNV ACMG classification, scoring, criterion evaluation. |
| [frontend/src/lib/__tests__/embryoSegregation.test.ts](../frontend/src/lib/__tests__/embryoSegregation.test.ts) | Embryo classification at ROI and recombination inference. |
| [frontend/src/lib/__tests__/errorMessage.test.ts](../frontend/src/lib/__tests__/errorMessage.test.ts) | Error-message extraction and fallback formatting. Only an HTTP 404 reads as not found (CR-082). |
| [frontend/src/lib/__tests__/familyMembers.test.ts](../frontend/src/lib/__tests__/familyMembers.test.ts) | Proband-first family-member ordering. |
| [frontend/src/lib/__tests__/genotypes.test.ts](../frontend/src/lib/__tests__/genotypes.test.ts) | Genotype classification matching the backend (#511), formatting, alt-allele detection and card zygosity, including haploid and multi-allelic calls. |
| [frontend/src/lib/__tests__/haplotypeCanvas.test.ts](../frontend/src/lib/__tests__/haplotypeCanvas.test.ts) | The risk-haplotype line (#529): solid for an affected haplotype, dashed for a carrier's, set off from the band by a light gap, and drawn below a thin band so the band keeps its colour. |
| [frontend/src/lib/__tests__/haplotypeRisk.test.ts](../frontend/src/lib/__tests__/haplotypeRisk.test.ts) | Disease-haplotype inference + embryo risk states; donor/empty → uninformative. |
| [frontend/src/lib/__tests__/igvLoader.test.ts](../frontend/src/lib/__tests__/igvLoader.test.ts) | IGV library dynamic loading. |
| [frontend/src/lib/__tests__/proxyLocation.test.ts](../frontend/src/lib/__tests__/proxyLocation.test.ts) | Proxy URL rewriting to same-origin paths. |
| [frontend/src/lib/__tests__/apiPath.test.ts](../frontend/src/lib/__tests__/apiPath.test.ts) | API path segments are percent-encoded so an imported id cannot redirect a call; `raw()` passes a deliberate query string through (#521). |
| [frontend/src/lib/__tests__/escapeHtml.test.ts](../frontend/src/lib/__tests__/escapeHtml.test.ts) | HTML escaping for the d3 tooltips built from imported strings (#521). |
| [frontend/src/lib/__tests__/telemetry.test.ts](../frontend/src/lib/__tests__/telemetry.test.ts) | UI telemetry durability (#521): a transiently failed batch is retried on the next flush, a rejected (4xx) batch is dropped, and the logout flush sends the queue with the current token. |
| [frontend/src/lib/__tests__/queryClient.test.ts](../frontend/src/lib/__tests__/queryClient.test.ts) | React-Query client cache/refetch defaults. |
| [frontend/src/lib/__tests__/reference.test.tsx](../frontend/src/lib/__tests__/reference.test.tsx) | Family reference-data fetching hook. A failed project catalogue is reported (`isError`) and loaded again on retry, not read as an unlinked family, and the reference label says it could not be loaded (CR-076). |
| [frontend/src/lib/__tests__/reviewConcurrency.test.ts](../frontend/src/lib/__tests__/reviewConcurrency.test.ts) | Review saves carry the loaded version (null for no review); the review-conflict 409 is recognised and other errors are not (#513). |
| [frontend/src/lib/__tests__/sampleFilterState.test.ts](../frontend/src/lib/__tests__/sampleFilterState.test.ts) | Genotype-selection parse/serialize and default filter state. |
| [frontend/src/lib/__tests__/useModalDialog.test.tsx](../frontend/src/lib/__tests__/useModalDialog.test.tsx) | Dialog behaviour (#529): Escape and a backdrop click close an untouched dialog; unsaved input is only discarded after confirmation; a press inside released over the backdrop does not close; focus moves in, Tab wraps, and only the topmost of two dialogs answers Escape. |
| [frontend/src/lib/__tests__/sanitizeHtml.test.ts](../frontend/src/lib/__tests__/sanitizeHtml.test.ts) | HTML render-sink sanitiser: allowlist strips scripts/event-handlers/`javascript:` hrefs and unwraps disallowed tags while keeping safe formatting. |
| [frontend/src/lib/__tests__/settings.test.ts](../frontend/src/lib/__tests__/settings.test.ts) | UI window/threshold settings storage. |
| [frontend/src/lib/__tests__/smallVariantMarks.test.ts](../frontend/src/lib/__tests__/smallVariantMarks.test.ts) | How a small variant is marked (#529): the ClinVar class wins over the impact ("conflicting" is not pathogenic); the salient classes get their own shape and size and are drawn on top. |
| [frontend/src/lib/__tests__/storage.test.ts](../frontend/src/lib/__tests__/storage.test.ts) | localStorage fallback / session persistence. |
| [frontend/src/lib/__tests__/trackSampling.test.ts](../frontend/src/lib/__tests__/trackSampling.test.ts) | Adaptive track window / point-segment-variant limits. |
| [frontend/src/lib/__tests__/variantSearch.test.ts](../frontend/src/lib/__tests__/variantSearch.test.ts) | The shared locus box (#526): chromosome:start[-end] with or without chr and thousands separators, a single position as a one-base region; anything else, trimmed, is a gene; nothing is nothing (CR-067). An en-dash range reads as a region; a colon or a BED-like line that does not parse, or an end before its start, reads as neither a gene nor a region; `intervalListProblems` names each unreadable interval entry as the backend refuses it (CR-080). |
| [frontend/src/lib/acmg/__tests__/evaluate.test.ts](../frontend/src/lib/acmg/__tests__/evaluate.test.ts) | ACMG criterion auto-suggestion for small variants. With the gene profile or the HPO terms failed (CR-081): PVS1 and BS2 say the gene profile could not be loaded, not an unconfirmed mechanism; PP4 is offered as Consider, "Not assessed", unless a phenotype score applies it. A son where he is hemizygous (CR-085): PM6/PS2 follow the parent who passes him the chromosome (a Y de novo with no maternal call applies PM6; an X variant carried by the mother is inherited from her; one shared with the father is not called de novo), and the trio rule holds in a PAR and for a daughter. |
| [frontend/src/lib/acmg/__tests__/evaluateMito.test.ts](../frontend/src/lib/acmg/__tests__/evaluateMito.test.ts) | Mitochondrial ACMG criterion evaluation. PP4 is offered for review when a lookup it needs failed (CR-081). |
| [frontend/src/lib/acmg/__tests__/score.test.ts](../frontend/src/lib/acmg/__tests__/score.test.ts) | ACMG classification computation from criterion selections. |

### Visualization components
| Test file | Purpose |
| --- | --- |
| [frontend/src/components/__tests__/ApcadChart.test.tsx](../frontend/src/components/__tests__/ApcadChart.test.tsx) | APCAD chart rendering / canvas context (fetch via the shared track client). |
| [frontend/src/components/__tests__/CircosPlot.test.tsx](../frontend/src/components/__tests__/CircosPlot.test.tsx) | Circos plot rendering (bands, telomere/centromere geometry). The accessible name counts the drawn SVs by type; not loaded, none, none in the selection and no chromosome selected are told apart (CR-062). Past the backend cap the name says there are too many SVs to draw (#589, CR-066). |
| [frontend/src/components/__tests__/CnvTrack.test.tsx](../frontend/src/components/__tests__/CnvTrack.test.tsx) | CNV track rendering and query integration. A clinical CNV held from the previous window is not drawn outside the new one during a pan (#526). The accessible name counts the CNVs in view and names the first ones within 150 characters; loading and failed are named as such (CR-062). |
| [frontend/src/components/__tests__/CoverageSegmentsChart.test.tsx](../frontend/src/components/__tests__/CoverageSegmentsChart.test.tsx) | Coverage-segments chart rendering; a "no data" 404 source is empty while any other failed source fails the chart (#510). The accessible name says when the chart failed to load, is loading or holds no data, not only its thresholds (CR-075). |
| [frontend/src/components/__tests__/DgvTrack.test.tsx](../frontend/src/components/__tests__/DgvTrack.test.tsx) | DGV background track rendering. The accessible name counts the variants in view by class, or gives the density total; loading and failed are named as such (CR-062). While a pan loads, a held variant left of the new window is not drawn at its edge (#586, CR-064). |
| [frontend/src/components/__tests__/ErrorBoundary.test.tsx](../frontend/src/components/__tests__/ErrorBoundary.test.tsx) | Render-error screen clears when the route changes, stays on the same route (#510). |
| [frontend/src/components/__tests__/TrackErrorStates.test.tsx](../frontend/src/components/__tests__/TrackErrorStates.test.tsx) | Failed track requests show a failure (with retry), never an empty-region message: genome haplotype track fails whole instead of computing risk from partial sources (risk state `unavailable`), SV and blacklist tracks; a genuinely empty region still reads as empty (#510). The blacklist and genome haplotype tracks name their failed, empty and populated states; the genome haplotype name says where the risk state was assessed (CR-062). Without an ROI the genome haplotype track assesses no risk state (#588, CR-065). |
| [frontend/src/components/__tests__/TrackRegionStates.test.tsx](../frontend/src/components/__tests__/TrackRegionStates.test.tsx) | The chromosome view's ten region tracks over a zero-width and an inverted region: each is named "no region in view" (the phased haplotypes with no risk state assessed), never loading, none or too many, and the nine that request by region ask for nothing; the small-variant and SV tracks say so on screen. On the mitochondrion every track's name reads chrM (CR-075). |
| [frontend/src/components/__tests__/GeneTrack.test.tsx](../frontend/src/components/__tests__/GeneTrack.test.tsx) | Gene track (#526): the region request; overlapping genes stack and free lines are reused; genes are clipped to the region; exons over the gene body, a gene still drawn when the view falls inside an intron; strand arrows; a 6 px minimum; the escaped panel tooltip; empty and failed states. The accessible name counts the genes in view and names the first three; during a pan, held genes are named only where they lie in view, and the wait reads loading, not none (CR-062). While a pan loads, a held gene left of the new window is not drawn at its edge; a failed pan draws nothing (#586, CR-064). |
| [frontend/src/components/__tests__/GenomeHaplotypeTrack.test.tsx](../frontend/src/components/__tests__/GenomeHaplotypeTrack.test.tsx) | Genome-wide haplotype track (#588): the risk haplotype inferred at the ROI is drawn only on the ROI's chromosome, not on another chromosome carrying the same homolog label, and follows the ROI there; without an ROI no risk overlay is drawn and the state reads not assessed (CR-065). |
| [frontend/src/components/__tests__/HaplotypePhasedTrack.test.tsx](../frontend/src/components/__tests__/HaplotypePhasedTrack.test.tsx) | Phased-haplotype track + raw-marker overlay rendering; a failed haplotype request shows the failure (risk state `unavailable`), a failed marker request is flagged over the drawn blocks (#510). The accessible name gives the region and the risk state at the ROI; a load claims no risk state, a failure reads unavailable, and a marker failure is named (CR-062). |
| [frontend/src/components/__tests__/Histogram.test.tsx](../frontend/src/components/__tests__/Histogram.test.tsx) | Histogram rendering with large datasets. The accessible name counts the values and bins and names the tallest bin; an empty histogram stays a text line (CR-062). Equal-width bins narrower than 1 get distinct labels, whole-number bins keep theirs, and bins sharing a label are still drawn side by side (CR-075). |
| [frontend/src/components/__tests__/Ideogram.test.tsx](../frontend/src/components/__tests__/Ideogram.test.tsx) | Chromosome ideogram (#526): cytobands to scale, the centromere triangles and pinched outline, the highlighted and clamped region, axis units, drag-to-select in either direction (a click selects nothing), the band tooltip, loading and failed states. The accessible name gives the highlighted region, clamped to the chromosome, or the whole chromosome; loading and failed (CR-062). |
| [frontend/src/components/__tests__/IgvViewer.test.tsx](../frontend/src/components/__tests__/IgvViewer.test.tsx) | IGV browser init, genome search, track loading, cleanup. A failed signal-track manifest is said, with a retry, while the alignments load (CR-082). |
| [frontend/src/components/__tests__/Pedigree.test.tsx](../frontend/src/components/__tests__/Pedigree.test.tsx) | Pedigree diagram: affected/carrier status, per-sample QC ring told apart by line and glyph as well as colour, leaving the affected and carrier-type fills alone (#529). The accessible name summarises the members, generations, affected, carriers, consanguineous couples, members with HPO phenotypes and the QC verdicts (CR-062). |
| [frontend/src/components/__tests__/RepeatExpansionTrack.test.tsx](../frontend/src/components/__tests__/RepeatExpansionTrack.test.tsx) | Repeat-expansion track rendering. The accessible name counts the loci in chromosome and in region mode; a pan that fails is named as failed, not from the held loci (CR-062). A failed pan draws nothing from the previous window (#586, CR-064). |
| [frontend/src/components/__tests__/SmallVariantLegend.test.tsx](../frontend/src/components/__tests__/SmallVariantLegend.test.tsx) | The small-variant mark legend names each mark with the track's own shape; the haplotype legend's carrier line is dashed (#529). |
| [frontend/src/components/__tests__/SmallVariantTrack.test.tsx](../frontend/src/components/__tests__/SmallVariantTrack.test.tsx) | Small-variant track rendering with query/API mocks; a failed request shows the failure with retry, never "no small variants" (#510). Marks by ClinVar class and impact in shape as well as colour; a review tag rings the mark instead of recolouring it and is named in the tooltip (#529). The accessible name counts the drawn variants and their P/LP and HIGH marks, and names too many, loading and failed (CR-062). |
| [frontend/src/components/__tests__/ZoomedIdeogram.test.tsx](../frontend/src/components/__tests__/ZoomedIdeogram.test.tsx) | Zoomed ideogram (#526): bands mapped to pixels and clipped to the region (half-open), the centromere as two triangles, telomere caps only at a chromosome end, stain gradients, region-edge lines; axis ticks at both edges and round intervals, each with its own label at gene-level zoom; the ISCN band tooltip; blank while loading or for an empty region; a failure with retry (#510); bands fetched per chromosome (#521). The accessible name counts the cytobands in view and names the first three; no bands, no region, loading, and failed with bands cached (CR-062). |
| [frontend/src/components/__tests__/SegmentalDuplicationTrack.test.tsx](../frontend/src/components/__tests__/SegmentalDuplicationTrack.test.tsx) | Segmental-duplication track (#526): the visible window requested on encoded path segments, never an empty one; blocks placed and clipped, at least 2 px, labelled; empty, loading and failed (#510) states; on a same-span pan the held blocks stay at their true positions and one outside the new window is not drawn; blank after a zoom, chromosome or assembly change. The accessible name counts the duplications in view; loading and failed are named as such (CR-062). |
| [frontend/src/components/__tests__/GenomeRepeatExpansionTrack.test.tsx](../frontend/src/components/__tests__/GenomeRepeatExpansionTrack.test.tsx) | Genome repeat-expansion track (#526): the sample's loci requested for the displayed chromosomes (with the project when set); each locus at chromosome offset plus midpoint, clamped inside the track; coloured by status (unknown for an unrecognised one), the most severe drawn on top so a pathogenic locus is not covered; the hover tooltip; loading, empty and failed (#510) states. The accessible name counts the loci, names up to three pathogenic ones and counts those needing review or unknown; loading and failed are named as such (CR-062). Before the genome layout is known the track is loading, and with no chromosome in view it asks for nothing: neither reads as no loci (CR-075). |
| [frontend/src/components/__tests__/VariantTrack.test.tsx](../frontend/src/components/__tests__/VariantTrack.test.tsx) | Base variant-track rendering. The accessible name counts the drawn SVs by type; empty, and a pan that loads and then fails (CR-062). A view holding more SVs than one page, or past the cap, says too many instead of drawing the left-most page; a complete page is drawn (#585, CR-063). A failed pan draws nothing from the previous window (#586, CR-064). |
| [frontend/src/components/__tests__/SvTrack.test.tsx](../frontend/src/components/__tests__/SvTrack.test.tsx) | Genome-wide SV track: the canvas's accessible name counts the sample's drawn SVs by type (on the displayed chromosomes, carried, drawable types only); empty, loading and failed (#510) states are named as such, never as zero (#529, CR-062). Past the backend cap it says too many instead of drawing part of the genome, and the cap flag is read from the response (#585, CR-063). Without a layout or a URL it asks for nothing, and reads as loading or as no SV data, not as no SVs (CR-075). |
| [frontend/src/components/visualizations/__tests__/VizTooltip.test.tsx](../frontend/src/components/visualizations/__tests__/VizTooltip.test.tsx) | Floating tooltip portal rendering. |
| [frontend/src/components/visualizations/__tests__/ApcadChart.test.tsx](../frontend/src/components/visualizations/__tests__/ApcadChart.test.tsx) | APCAD track draws whatever the server sent, unphased (`und`) points included, and still reports a genuinely empty region. The accessible name counts the drawn sites and PCF segments for the sample and window; loading and failed are never read as no data (CR-062). |

### Shared UI components
| Test file | Purpose |
| --- | --- |
| [frontend/src/components/__tests__/AssemblyScopeBanner.test.tsx](../frontend/src/components/__tests__/AssemblyScopeBanner.test.tsx) | "Not validated for clinical use" label for an off-scope or unlinked assembly; nothing for a validated assembly or while the scope is still loading (#515). When the reference could not be loaded, it says the validated scope is unconfirmed, with a retry (CR-076). |
| [frontend/src/components/__tests__/QueryFailure.test.tsx](../frontend/src/components/__tests__/QueryFailure.test.tsx) | The shared failure notice: what could not be loaded, "this is not an empty result", the server's reason, what it means for the page, and a retry (CR-078). |
| [frontend/src/components/__tests__/FamilyLoadFailure.test.tsx](../frontend/src/components/__tests__/FamilyLoadFailure.test.tsx) | The family pages' failure state: "Family not found" only for a 404; any other failure "… could not be loaded", with the server's reason and a retry (CR-082). |
| [frontend/src/components/__tests__/Breadcrumbs.test.tsx](../frontend/src/components/__tests__/Breadcrumbs.test.tsx) | Breadcrumb navigation rendering with router context. |
| [frontend/src/components/__tests__/Layout.test.tsx](../frontend/src/components/__tests__/Layout.test.tsx) | Logout flushes telemetry, then clears the query cache and the session before navigating to login (#521). |
| [frontend/src/components/__tests__/ModalDialog.test.tsx](../frontend/src/components/__tests__/ModalDialog.test.tsx) | Inline dialog shell (#529): a named modal dialog that closes on Escape, can refuse backdrop clicks (acknowledgement dialogs), and asks before Escape discards typed input. A dialog answers Escape from the moment it is drawn, not the dialog underneath: a default-priority open followed at once by Escape, which closed the outer dialog before (CR-074). |
| [frontend/src/components/__tests__/InfoTip.test.tsx](../frontend/src/components/__tests__/InfoTip.test.tsx) | Info tooltip show/hide on hover. |
| [frontend/src/components/__tests__/LoadingBar.test.tsx](../frontend/src/components/__tests__/LoadingBar.test.tsx) | Animated loading-status bar accessibility. |
| [frontend/src/components/__tests__/RequireAuth.test.tsx](../frontend/src/components/__tests__/RequireAuth.test.tsx) | Auth route-guard: unauthenticated → login (`?next=`). |
| [frontend/src/components/__tests__/RequireAdmin.test.tsx](../frontend/src/components/__tests__/RequireAdmin.test.tsx) | Admin route-guard: viewer → dashboard, admin/superuser → content. |
| [frontend/src/components/__tests__/SessionRedirect.test.tsx](../frontend/src/components/__tests__/SessionRedirect.test.tsx) | Authenticated-user redirect to dashboard target. |

### Family workspace pages & parts
| Test file | Purpose |
| --- | --- |
| [frontend/src/pages/families/__tests__/AcmgClassificationModal.test.tsx](../frontend/src/pages/families/__tests__/AcmgClassificationModal.test.tsx) | ACMG modal: criterion selection, server-recompute, payload emission. A failed gene profile or HPO lookup is said in the dialog, with a retry; PVS1 is not read as an unconfirmed mechanism, and PP4 is suggested for review (CR-081). |
| [frontend/src/pages/families/__tests__/AcmgScaleBar.test.tsx](../frontend/src/pages/families/__tests__/AcmgScaleBar.test.tsx) | ACMG scale readout: class, signed points, VUS tier, BA1 override. |
| [frontend/src/pages/families/__tests__/AnnotationProvenanceSummary.test.tsx](../frontend/src/pages/families/__tests__/AnnotationProvenanceSummary.test.tsx) | Filter-page annotation-provenance summary: module versions, source label, show-more toggle. A failed manifest is said in the footer's place, with a retry (CR-082). |
| [frontend/src/pages/families/__tests__/CnvAcmgClassificationModal.test.tsx](../frontend/src/pages/families/__tests__/CnvAcmgClassificationModal.test.tsx) | CNV ACMG modal: kind toggle, overridable criteria, recompute→save. |
| [frontend/src/pages/families/__tests__/CnvScaleBar.test.tsx](../frontend/src/pages/families/__tests__/CnvScaleBar.test.tsx) | ClinGen CNV scale readout. |
| [frontend/src/pages/families/__tests__/FamiliesPage.test.tsx](../frontend/src/pages/families/__tests__/FamiliesPage.test.tsx) | Legacy families route → dashboard redirect. |
| [frontend/src/pages/families/__tests__/FamilyDetailPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyDetailPage.test.tsx) | Family detail page rendering and route/query integration. The member dialog's own flows, recorded before it moved into `FamilyMemberDetailDialog`: adding a phenotype from the HPO search and a failed save, removing a phenotype, removing a member only once confirmed and never the family's last, and a pending edit shown on reopening (#528, CR-072). Failed requests (CR-079): haplotypes at the ROI give "⚠ segregation not derived" per embryo; a failed presence check shows its workspace link and settles; curation counts and HPO terms read as unknown; a failed status list keeps the current status shown and unchangeable. A failed family reads "Family could not be loaded", not "Family not found", and retries; a failed member dialog says so instead of loading for good (CR-082). |
| [frontend/src/pages/families/__tests__/FamilyVariantSummaryPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyVariantSummaryPage.test.tsx) | Family SV summary (#526): the three requests with the family id encoded and the 100k cap with its truncation notice; per-chromosome and per-type counts in karyotype order (missing values as unknown); the unique/shared sample matrix (no totals); length histograms overall, per type and per caller with the log/linear switch; loading, empty and failed states, a failed load never shown as zero counts. The sharing matrix has no totals, which counted an SV once per pair of carriers; a failed load says so, with the reason and a retry (CR-058). |
| [frontend/src/pages/families/__tests__/FamilyIgvPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyIgvPage.test.tsx) | IGV page with sample navigation/track loading. A failed family reads "Family could not be loaded", with a retry (CR-082). |
| [frontend/src/pages/families/__tests__/FamilyMitoDNAAnalysisPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyMitoDNAAnalysisPage.test.tsx) | mtDNA analysis page rendering and API integration. A failed analysis reads "mtDNA analysis could not be loaded", with a retry; "Family not found" only for a 404 (CR-082). |
| [frontend/src/pages/families/__tests__/FamilyNiptPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyNiptPage.test.tsx) | NIPT dashboard (FF, category counts, funnel, coverage). A failed variant search, summary or coverage QC is said as such: never "No variants match", a missing fetal fraction with its warnings, zero counts, or a coverage that loads for good (CR-078). A failed family reads "Family could not be loaded", not "Family not found" (CR-082). |
| [frontend/src/pages/families/__tests__/FamilyNiptReportPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyNiptReportPage.test.tsx) | NIPT report generation/display. A failed family request shows no report, and a fetal-fraction estimate or coverage QC that could not be loaded is said as such, on screen and at the top of the printout, instead of "no estimate" or "no target regions" (CR-077). |
| [frontend/src/pages/families/__tests__/FamilyPageHeader.test.tsx](../frontend/src/pages/families/__tests__/FamilyPageHeader.test.tsx) | Shared family page header: title links to the workspace (not on the workspace itself), project scope preserved, pedigree column present only with a pedigree, actions/footer placement. |
| [frontend/src/pages/families/__tests__/FamilyParaphasePage.test.tsx](../frontend/src/pages/families/__tests__/FamilyParaphasePage.test.tsx) | Paraphase page interaction and API calls. A failed family reads "Family could not be loaded", with a retry; "Family not found" only for a 404 (CR-082). |
| [frontend/src/pages/families/__tests__/FamilyRepeatExpansionsPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyRepeatExpansionsPage.test.tsx) | Repeat-expansion page filtering/display. A failed table reads "Repeat expansions could not be loaded", with a retry; "Family not found" only for a 404 (CR-082). |
| [frontend/src/pages/families/__tests__/FamilyReportPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyReportPage.test.tsx) | Clinical report: provenance footer, drift badge, sign-out/amend, audit timeline; shown as signed only when verified against the latest sign-out (changed / unverified states, print notices), drift and QC overrides with reasons, sign-out failures shown, signed-version download (#508). A failed reference does not render an empty report: the page says it could not be prepared, offers no sign-out, and retries (CR-076). A failed family or reported-SV request shows no report; a sign-out record that could not be loaded is neither "Draft" nor signed, and sign-out is not offered; a gene description, HPO terms, drift check, audit trail or provenance that could not be loaded is marked in place and heads the printout, and retries (CR-077). |
| [frontend/src/pages/families/__tests__/FamilyRoiMarkersPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyRoiMarkersPage.test.tsx) | ROI-markers page filtering/API. A failed marker, haplotype or family request is said as such: never "0 markers in view", uncoloured bands without a word, or "No region of interest" (CR-079). |
| [frontend/src/pages/families/__tests__/FamilySampleQcPage.test.tsx](../frontend/src/pages/families/__tests__/FamilySampleQcPage.test.tsx) | Sample-QC page rendering. |
| [frontend/src/pages/admin/__tests__/AdminQcThresholdsPage.test.tsx](../frontend/src/pages/admin/__tests__/AdminQcThresholdsPage.test.tsx) | Sequencing-QC threshold admin page: metric catalogue with its failing side, per-profile bounds that do not leak across profiles, save/clear payloads, and the no-shipped-defaults notice. |
| [frontend/src/pages/families/__tests__/SampleQcCell.test.tsx](../frontend/src/pages/families/__tests__/SampleQcCell.test.tsx) | Per-member sequencing-QC cell: the metric chip is itself the control that opens the pipeline QC report, via a short-lived link in a detached tab. |
| [frontend/src/pages/families/__tests__/PipelineSettingsPanel.test.tsx](../frontend/src/pages/families/__tests__/PipelineSettingsPanel.test.tsx) | Analysis-pipeline settings panel: grouping and labelling, stage switches collapsed to what ran, unknown parameters still shown, workspace vs report variant. A failed manifest reads "version could not be loaded", not "version not reported", and is said even without a run record (CR-082). |
| [frontend/src/pages/families/__tests__/FamilySmallVariantsPage.test.tsx](../frontend/src/pages/families/__tests__/FamilySmallVariantsPage.test.tsx) | Small-variants page: filtering, search, interactions, loading. A failed project catalogue says the reference could not be loaded instead of loading for good, and the search runs on retry (CR-076). A failed panel list is said, with its consequence, and an applied panel stays visible in the select (CR-078). An unreadable interval is named under its field and not searched; from a URL it fails the search with its reason (CR-080). |
| [frontend/src/pages/families/__tests__/FamilyStructuralVariantsPage.test.tsx](../frontend/src/pages/families/__tests__/FamilyStructuralVariantsPage.test.tsx) | Structural-variants page filtering/API. A failed search is said as such, with a retry, never as zero SVs; a failed total reads as unknown, not as the filtered count; a failed panel list keeps the applied panel visible (CR-078). An unreadable location is named under its field and not searched; from a URL it fails the search with its reason (CR-080). |
| [frontend/src/pages/families/__tests__/MonarchPhenotypeMatchPanel.test.tsx](../frontend/src/pages/families/__tests__/MonarchPhenotypeMatchPanel.test.tsx) | Monarch phenotype-match panel search/display. |
| [frontend/src/pages/families/__tests__/NiptClassificationBlock.test.tsx](../frontend/src/pages/families/__tests__/NiptClassificationBlock.test.tsx) | NIPT per-variant block: category/confidence/VAF/flags. |
| [frontend/src/pages/families/__tests__/SmallVariantCards.test.tsx](../frontend/src/pages/families/__tests__/SmallVariantCards.test.tsx) | Small-variant cards: transcript popup and effects, CCDS/RefSeq badges, gene–disease list, HGVS.g headline, dbSNP link, card-vs-disclosure split. A failed gene profile is said on the card and in the transcript popup, not left loading or shown as transcripts without CCDS/RefSeq (CR-082). |
| [frontend/src/pages/families/__tests__/SmallVariantPairCards.test.tsx](../frontend/src/pages/families/__tests__/SmallVariantPairCards.test.tsx) | Compound-het pair cards: read-backed trans vs unresolved phasing, kept distinct from the curator's phase status. |
| [frontend/src/pages/families/__tests__/SmallVariantFilterForm.test.tsx](../frontend/src/pages/families/__tests__/SmallVariantFilterForm.test.tsx) | The small-variant filter form (#528), rendered whole in the three ways the pages use it: a family's search, the Global Small Variant Explorer and monogenic NIPT. Its markup is recorded (`__snapshots__`), so splitting it into section components must reproduce every section, label, control, value and summary exactly. Update the snapshot only for an intended change to the form's markup (CR-073). |
| [frontend/src/pages/families/__tests__/SmallVariantTable.test.tsx](../frontend/src/pages/families/__tests__/SmallVariantTable.test.tsx) | Small-variant table rendering and tooltips. |
| [frontend/src/pages/families/__tests__/StructuralVariantCards.test.tsx](../frontend/src/pages/families/__tests__/StructuralVariantCards.test.tsx) | SV cards: the Frequencies heading links to the gnomAD SV region browser, and stays plain text on assemblies gnomAD does not publish. The HPO link carries the SV page it came from (CR-060). |
| [frontend/src/pages/families/__tests__/StructuralVariantTable.test.tsx](../frontend/src/pages/families/__tests__/StructuralVariantTable.test.tsx) | SV results table (#526): the columns shown and their sort marks; each value written, with a dash where there is none; lengths up to 1 kb in bp, as in the report narrative; the quick tags toggled, announced as pressed, off while a save is pending; an excluded row, the custom tag chips, one classification and the note; affected and proband genotypes; IGV with its flank and a BND partner's own link, and the chromosome view (CR-067). |
| [frontend/src/pages/families/__tests__/StructuralVariantColumnControls.test.tsx](../frontend/src/pages/families/__tests__/StructuralVariantColumnControls.test.tsx) | SV column picker (#526): a checkbox per column, named and checked as shown; a click toggles that column (CR-067). |
| [frontend/src/pages/families/__tests__/SvSecondHitBadge.test.tsx](../frontend/src/pages/families/__tests__/SvSecondHitBadge.test.tsx) | SV second-hit (compound-het) badge. |
| [frontend/src/pages/families/__tests__/buildLiteraturePubmedHref.test.ts](../frontend/src/pages/families/__tests__/buildLiteraturePubmedHref.test.ts) | PubMed search-URL construction for variants. |
| [frontend/src/pages/families/__tests__/reportNarrative.test.ts](../frontend/src/pages/families/__tests__/reportNarrative.test.ts) | Narrative report text generation. |
| [frontend/src/pages/families/__tests__/smallVariantResultUtils.test.ts](../frontend/src/pages/families/__tests__/smallVariantResultUtils.test.ts) | HGVS.g derivation from VCF alleles (substitution, del, ins, delins, shared-suffix trim) and dbSNP link construction. |
| [frontend/src/pages/families/__tests__/smallVariantSearch.test.ts](../frontend/src/pages/families/__tests__/smallVariantSearch.test.ts) | Small-variant filters, presets, genotype state, search params. An unreadable locus or interval entry is listed by field, and a draft with one is not applied (CR-080). |
| [frontend/src/pages/families/__tests__/structuralVariantNavigation.test.ts](../frontend/src/pages/families/__tests__/structuralVariantNavigation.test.ts) | Structural-variant windows: the call span reported unpadded, IGV opened on a tight flank, the chromosome view on a wide one, point events, clamping. |
| [frontend/src/pages/families/__tests__/structuralVariantSearch.test.ts](../frontend/src/pages/families/__tests__/structuralVariantSearch.test.ts) | Structural-variant search state and filters. A location that reads as neither a gene nor a region is named, and not applied from the draft (CR-080). |

### Genome views
| Test file | Purpose |
| --- | --- |
| [frontend/src/pages/genome/__tests__/ChromosomeViewPage.test.tsx](../frontend/src/pages/genome/__tests__/ChromosomeViewPage.test.tsx) | Chromosome viewer routing, chrM normalization, workspace integration. A reference that could not be loaded is said as such, not as "Reference not linked" (CR-076). A failed availability request reaches the workspace as a failure, and its retry refetches (CR-079). A failed chromosome length reaches the workspace as a failure, not an endless load, and a failed family is not "Family not found" (CR-082). |
| [frontend/src/pages/genome/__tests__/ChromosomeViewWorkspace.test.tsx](../frontend/src/pages/genome/__tests__/ChromosomeViewWorkspace.test.tsx) | Chromosome workspace with coverage/track integration. A failed track-availability request is said, with a retry, instead of "No BED data for selected samples" (CR-079). |
| [frontend/src/pages/genome/__tests__/CircosPlotPage.test.tsx](../frontend/src/pages/genome/__tests__/CircosPlotPage.test.tsx) | Circos page routing and sidebar. A failed chromosome request says so with a retry instead of loading forever; a failed SV request is a failure, not a family without SVs; a capped SV list is not drawn and the page says there are too many (#589, CR-066). |
| [frontend/src/pages/genome/__tests__/CnvDetailsPage.test.tsx](../frontend/src/pages/genome/__tests__/CnvDetailsPage.test.tsx) | CNV detail page rendering. |
| [frontend/src/pages/genome/__tests__/GenomeOverviewPage.test.tsx](../frontend/src/pages/genome/__tests__/GenomeOverviewPage.test.tsx) | Genome-overview page with sidebar/workspace. Failed chromosome lengths reach the workspace as a failure, not an endless load, and a failed family is not "Family not found" (CR-082). |
| [frontend/src/pages/genome/__tests__/GenomeOverviewWorkspace.test.tsx](../frontend/src/pages/genome/__tests__/GenomeOverviewWorkspace.test.tsx) | Genome workspace with coverage/APCAD charts. A failed track-availability request is said, with a retry, instead of "No data for selected samples" (CR-079). The notice names the request the tracks wait on, such as the chromosome lengths (CR-082). |
| [frontend/src/pages/genome/__tests__/ViewerTrackBlock.test.tsx](../frontend/src/pages/genome/__tests__/ViewerTrackBlock.test.tsx) | Genome track-viewer mouse interaction/selection. |
| [frontend/src/pages/genome/__tests__/ChromosomeViewSidebar.test.tsx](../frontend/src/pages/genome/__tests__/ChromosomeViewSidebar.test.tsx) | Chromosome-view sidebar (#526): sample and track checkboxes mirror the parent's state; only the available tracks, in order, under the caller's labels; toggles report the sample id or track key. |
| [frontend/src/pages/genome/__tests__/GenomeOverviewSidebar.test.tsx](../frontend/src/pages/genome/__tests__/GenomeOverviewSidebar.test.tsx) | Genome-overview sidebar (#526): sample, track and chromosome checkboxes mirror the parent's state (only available tracks, chr1–22/X/Y/M, the mitochondrion named chrM as in the viewer, CR-075); toggles report the sample id, track key or chromosome; Select/Deselect all use their own callbacks. |
| [frontend/src/pages/genome/__tests__/ViewerMemberSection.test.tsx](../frontend/src/pages/genome/__tests__/ViewerMemberSection.test.tsx) | Genome-viewer member panel (#526): the sample-id heading, the affected star (titled) only for affected members, the role pill, and the member's tracks inside. The heading reads "S1, affected", with the star hidden from screen readers (CR-062). |

### Auth / dashboard / intake
| Test file | Purpose |
| --- | --- |
| [frontend/src/pages/auth/__tests__/LoginPage.test.tsx](../frontend/src/pages/auth/__tests__/LoginPage.test.tsx) | Login submission, session auth, `next`-path validation. |
| [frontend/src/pages/auth/__tests__/SignupPage.test.tsx](../frontend/src/pages/auth/__tests__/SignupPage.test.tsx) | Self-registration (#526, CR-059): the exact `/auth/signup` body; the server's acknowledgement shown instead of a jump to the login; every field required and a password of at least 15 characters, with no request for an empty form; one request per submission; refusal, validation, unreachable and fallback errors; in a development build only the message is logged, in production nothing. |
| [frontend/src/pages/dashboard/__tests__/Dashboard.test.tsx](../frontend/src/pages/dashboard/__tests__/Dashboard.test.tsx) | Dashboard family list and navigation. |
| [frontend/src/pages/dashboard/__tests__/FamilyBuilderPage.test.tsx](../frontend/src/pages/dashboard/__tests__/FamilyBuilderPage.test.tsx) | Family creation/editing with member form + API. |
| [frontend/src/pages/dashboard/__tests__/PackageImportPage.test.tsx](../frontend/src/pages/dashboard/__tests__/PackageImportPage.test.tsx) | Package-import workflow and upload handling. A failed folder scan is said, not "No families found in the import folder" (CR-082). |
| [frontend/src/pages/uploads/__tests__/SampleUpload.test.tsx](../frontend/src/pages/uploads/__tests__/SampleUpload.test.tsx) | Sample-upload form and API integration. |

### Admin pages
| Test file | Purpose |
| --- | --- |
| [frontend/src/pages/admin/__tests__/AdminAuditLogsPage.test.tsx](../frontend/src/pages/admin/__tests__/AdminAuditLogsPage.test.tsx) | Audit-log viewing/filtering. |
| [frontend/src/pages/admin/__tests__/AdminDashboardPage.test.tsx](../frontend/src/pages/admin/__tests__/AdminDashboardPage.test.tsx) | Admin landing page (#526): six domain groups whose cards link to each admin workspace, every target a route inside the `RequireAdmin` block of `index.tsx`. |
| [frontend/src/pages/admin/__tests__/AdminClickhouseManagementPage.test.tsx](../frontend/src/pages/admin/__tests__/AdminClickhouseManagementPage.test.tsx) | ClickHouse management UI. |
| [frontend/src/pages/admin/__tests__/AdminFamilyStatusesPage.test.tsx](../frontend/src/pages/admin/__tests__/AdminFamilyStatusesPage.test.tsx) | Family-status management. |
| [frontend/src/pages/admin/__tests__/AdminPresetFiltersPage.test.tsx](../frontend/src/pages/admin/__tests__/AdminPresetFiltersPage.test.tsx) | Preset-filter CRUD. |
| [frontend/src/pages/admin/__tests__/AdminVariantTagsPage.test.tsx](../frontend/src/pages/admin/__tests__/AdminVariantTagsPage.test.tsx) | Variant-tag management. |
| [frontend/src/pages/admin/__tests__/DataManagementPage.test.tsx](../frontend/src/pages/admin/__tests__/DataManagementPage.test.tsx) | Data-management workflows and family embedding. |
| [frontend/src/pages/admin/__tests__/GeneReferenceAdminPage.test.tsx](../frontend/src/pages/admin/__tests__/GeneReferenceAdminPage.test.tsx) | Gene-reference data management. |
| [frontend/src/pages/admin/__tests__/HpoTerminologyAdminPage.test.tsx](../frontend/src/pages/admin/__tests__/HpoTerminologyAdminPage.test.tsx) | HPO terminology admin page (#526): the installed release and its counts, the not-installed warning, summary loading and error states; the term browser (trimmed search, typing pause versus Enter, details with Replaced by and the 20-child cap, empty and error states); sync: a preview without confirmation, apply only after confirmation, the refreshed release and cleared HPO caches, failures with the server's reason. Apply only after a preview of exactly the current settings, the preview cleared by a change; the named confirmation; the running action's busy label; a date-only release date on its own day west of UTC (CR-060). |
| [frontend/src/pages/admin/__tests__/MonarchDataAdminPage.test.tsx](../frontend/src/pages/admin/__tests__/MonarchDataAdminPage.test.tsx) | Monarch data sync. |
| [frontend/src/pages/admin/__tests__/UserListPage.test.tsx](../frontend/src/pages/admin/__tests__/UserListPage.test.tsx) | Admin user list (#526): `/auth/users` and `/projects` loaded before the table; one row per account with project names and fallbacks; the activation checkbox PATCHes only `is_active` (the id one encoded segment, locked while in flight, the stored state kept on failure); no role or project editor; load-error messages. A failed (de)activation says what failed and why, a successful one confirms it; each checkbox is named after its account (CR-059). |

### Discovery / catalog / other pages
| Test file | Purpose |
| --- | --- |
| [frontend/src/pages/genes/__tests__/GeneInfoPage.test.tsx](../frontend/src/pages/genes/__tests__/GeneInfoPage.test.tsx) | Gene info page (transcripts, phenotype). A failed profile reads "… could not be loaded", with a retry, not "Select a human gene" (CR-082). |
| [frontend/src/pages/panels/__tests__/GenePanelDetailPage.test.tsx](../frontend/src/pages/panels/__tests__/GenePanelDetailPage.test.tsx) | Gene-panel detail (member filtering/editing). A failed panel or version history is said, with a retry, not left loading or hidden (CR-082). |
| [frontend/src/pages/panels/__tests__/GenePanelsPage.test.tsx](../frontend/src/pages/panels/__tests__/GenePanelsPage.test.tsx) | Gene-panels list (search/filter). A failed list is said, with a retry, not shown as an empty table (CR-082). |
| [frontend/src/pages/product/__tests__/NewFeaturesPage.test.tsx](../frontend/src/pages/product/__tests__/NewFeaturesPage.test.tsx) | New-features / releases page. |
| [frontend/src/pages/projects/__tests__/ProjectsPage.test.tsx](../frontend/src/pages/projects/__tests__/ProjectsPage.test.tsx) | Projects list (creation/filter). A failed list is said, with a retry, not as "No projects yet" with counts of 0 (CR-082). |
| [frontend/src/pages/reference/__tests__/ReferenceCatalogPage.test.tsx](../frontend/src/pages/reference/__tests__/ReferenceCatalogPage.test.tsx) | Reference-genome catalogue browsing. A failed species, assembly or reference-count list is said and its counts read "—", not "No species are configured yet" or 0 (CR-082). |
| [frontend/src/pages/settings/__tests__/SettingsPage.test.tsx](../frontend/src/pages/settings/__tests__/SettingsPage.test.tsx) | User settings/preferences. |
| [frontend/src/pages/variant-explorer/__tests__/globalSmallVariantSearch.test.ts](../frontend/src/pages/variant-explorer/__tests__/globalSmallVariantSearch.test.ts) | P2-4b explorer keyset pagination state: forward/back cursor walk, no page-0, reset-on-sort-change, no legacy `page` param leaking into requests. |
| [frontend/src/pages/variant-explorer/__tests__/GlobalSmallVariantExplorerPage.test.tsx](../frontend/src/pages/variant-explorer/__tests__/GlobalSmallVariantExplorerPage.test.tsx) | Variant explorer page (#526): the first accessible assembly by default and no search without one; assembly changes re-query variants and sample suggestions; loading, failure (never "no variants match") and empty states, and the total; keyset paging both ways; sorting restarts at page 1; the per-sample genotype filter applied on Apply; gene and panel filters; the carrier dialog and CSV export use the table's filters. No control or parameter for a filter the explorer does not apply, the ClinVar rescue sent; a capped total shown as N+; a loading, failed or empty assembly list is not read as no variants (CR-057). A failed search gives the server's reason and a retry, and the header does not count it as 0 variants (CR-078). |
| [frontend/src/pages/variant-explorer/__tests__/GlobalSmallVariantTable.test.tsx](../frontend/src/pages/variant-explorer/__tests__/GlobalSmallVariantTable.test.tsx) | Explorer results table (#526): one row per variant with the gene link, locus with HGVS.c, consequence with impact; the lab classification before ClinVar; tag labels and colours with fallbacks; each non-zero count opens the carrier dialog in its mode; only the gene and count headers sort, and only the active one shows its direction. The gene link uses `?gene=`, the parameter the gene page reads (CR-057). |
| [frontend/src/pages/variant-explorer/__tests__/VariantCarrierModal.test.tsx](../frontend/src/pages/variant-explorer/__tests__/VariantCarrierModal.test.tsx) | Explorer carrier dialog (#526, #529): the request carries the clicked count's genotype, the explorer's assembly and imputed setting; carriers grouped by family with encoded family links, zygosity, role and phenotype; singular counts; the truncation warning; loading, empty and failed states (a failure never reads as no carriers); Escape and Close close it. |
| [frontend/src/pages/docs/__tests__/ReferenceDocPage.test.tsx](../frontend/src/pages/docs/__tests__/ReferenceDocPage.test.tsx) | In-app reference doc page (anchored nav). |
| [frontend/src/pages/docs/__tests__/UserGuidePage.test.tsx](../frontend/src/pages/docs/__tests__/UserGuidePage.test.tsx) | In-app user-guide page (contents/workspace links). The Markdown conventions render as the guide's blocks: callouts, card grids, tables in their wrapper, the In-depth reference block, and the external link in a new tab (#528, CR-069). |
| [frontend/src/pages/docs/__tests__/UserGuideContent.test.tsx](../frontend/src/pages/docs/__tests__/UserGuideContent.test.tsx) | The user guide's content (#528; part of the IFU, TF-15), after its move from JSX to Markdown. Each of the 20 sections is compared with a snapshot taken from the JSX guide before the move (`fixtures/user-guide-content.json`): the whole text, the paragraphs, headings, list items, table cells, callouts and cards, and every emphasis, code span and link with its target. A changed word or emphasis fails it. Regenerate with `COGA_REGENERATE_GOLDEN=1` only for an intended text change (CR-069). |
| [frontend/src/pages/cnv-explorer/__tests__/ClinicalCnvExplorerPage.test.tsx](../frontend/src/pages/cnv-explorer/__tests__/ClinicalCnvExplorerPage.test.tsx) | Clinical CNV catalogue explorer (#526): the first assembly's catalogue requested unfiltered with limit 1000, the assembly picker and the assembly name as one path segment (#521), trimmed search and Clear, rows linking to CNV details with cytoband, locus and size, loading and no-match states with the count. An unfinished or failed lookup is not read as no match; a failed or empty assembly list; a full 1,000-row page reads 1,000+ (CR-058). |
| [frontend/src/pages/phenotypes/__tests__/HpoTermsPage.test.tsx](../frontend/src/pages/phenotypes/__tests__/HpoTermsPage.test.tsx) | HPO term list from SV annotations (#526): terms split on `;`, `,` and `|`, de-duplicated and sorted, each opened in the HPO browser in a new tab with the term as one path segment; the empty states; Back to SVs only when a family is known. Back to SVs returns to the originating SV page with its project and filters, never off the family pages, with the family id encoded (CR-060). |
| [frontend/src/pages/__tests__/NotFound.test.tsx](../frontend/src/pages/__tests__/NotFound.test.tsx) | Catch-all page (#526): names the unmatched path as text, never markup; links to the dashboard; Go back steps back one history entry. |

---

## Regulatory traceability

For the in-house IVD technical file, tests are traced **from requirements** in
[TF-09b — Requirements Traceability Matrix](regulatory/TF-09b-requirements-traceability-matrix.md)
(each `REQ-…` → implementation → verifying test → hazard) and the verification strategy is in
[TF-09 — V&V Plan](regulatory/TF-09-verification-validation.md). This document is the
complementary file→purpose index.

## Keeping this current

This catalogue is maintained by hand, but a **CI guard enforces it**: the `catalogue` job runs
[`scripts/check-test-catalogue.sh`](../scripts/check-test-catalogue.sh), which fails if any test
file in the tree is missing from this document — or if this document references a test file that
no longer exists. Run it locally before pushing:

```bash
./scripts/check-test-catalogue.sh
```

To regenerate the file list + per-file test counts when adding/removing tests:

```bash
# backend
for f in $(git ls-files 'backend/tests/*.py' 'backend/tests/**/*.py' | grep -E 'test_.*\.py$' | sort); do
  printf '%s\t%s\n' "$(grep -cE '^\s*(async )?def test_' "$f")" "$f"; done
# frontend
for f in $(git ls-files 'frontend/src/**/*.test.ts' 'frontend/src/**/*.test.tsx' | sort); do
  printf '%s\t%s\n' "$(grep -cE '\b(it|test)\(' "$f")" "$f"; done
# suite totals (a parametrized test counts once per case)
python -m pytest -q --co | tail -1
(cd frontend && npx vitest run | grep -E 'Test Files|Tests ')
```

When a test file is added, add a row to the relevant section here and (if it verifies a
technical-file requirement) update [TF-09b](regulatory/TF-09b-requirements-traceability-matrix.md).
