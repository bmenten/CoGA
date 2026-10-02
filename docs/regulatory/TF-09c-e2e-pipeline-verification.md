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
> It exercises the device boundary from [TF-02](TF-02-device-description.md) — **annotated
> VCF (+ tracks) → signed clinical report** — as one deterministic, repeatable run, complementing
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
| Structural variants | a DEL and a BND | ingested with type/length/gene overlap |
| Repeat expansion | HTT (HD_HTT), 40-CAG allele | classified **pathogenic** |
| Coverage | per-sample BED tracks | interval-track rows registered |
| Paraphase | one segmental-duplication result | persisted |
| Phenotype | one HPO term | linked to the proband |
| NIPT (realistic) | `demo/nipt_family` | fetal fraction ≈ 12%, 40 paternal sites, all 8 monogenic categories recovered |

## 2. Verification scope & evidence

Each suite drives the real stack and asserts against `EXPECTED.yaml`. Skipped unless
`RUN_INTEGRATION=1`; executed by the CI **`e2e`** job (§4).

| Stage verified | Evidence (test) |
| --- | --- |
| Package import → per-stage persistence (Postgres + ClickHouse) | [test_e2e_import_golden.py](../../backend/tests/e2e/test_e2e_import_golden.py) |
| Query/API contracts feeding the UI (small/SV pages, explorer, BED, haplotypes, track-availability) | [test_e2e_api_contract.py](../../backend/tests/e2e/test_e2e_api_contract.py) |
| Clinical review → ACMG recompute, immutable audit chain, signed report | [test_e2e_review_audit.py](../../backend/tests/e2e/test_e2e_review_audit.py) |
| Failure/degradation handling + job lifecycle (fail-clean) | [test_e2e_failure_modes.py](../../backend/tests/e2e/test_e2e_failure_modes.py) |
| A partly failed import's flag stays through an import that completes without the failed dataset, keeps an earlier failure when a later import fails too (each with its job), and goes once the failed datasets are imported again (TF-06 H16) | [test_e2e_import_incomplete_until_reimported.py](../../backend/tests/e2e/test_e2e_import_incomplete_until_reimported.py) |
| An import whose process stops part-way (inside a dataset, between datasets, or in an overwrite of an existing family): the family stays marked and gates sign-out, the stopped job is ended rather than run again and an overwrite's backup of the family dropped with it, only an overwrite clears the mark (TF-06 H16) | [test_e2e_import_crash_leaves_family_marked.py](../../backend/tests/e2e/test_e2e_import_crash_leaves_family_marked.py) |
| Realistic demo bundles through their real ingestion paths | [test_e2e_demo_smoke.py](../../backend/tests/e2e/test_e2e_demo_smoke.py) |
| Haplotype / lineage stage against the real stack (PGT segregation) | [test_e2e_haplotypes.py](../../backend/tests/e2e/test_e2e_haplotypes.py) |
| Prioritised-ranking cache invalidated by a variant-data change on a non-import path | [test_e2e_ranking_cache.py](../../backend/tests/e2e/test_e2e_ranking_cache.py) |
| SV second-hit index rebuilt after SV writes on non-import paths (admin delete, per-sample upload) | [test_e2e_sv_second_hit_index.py](../../backend/tests/e2e/test_e2e_sv_second_hit_index.py) |

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
