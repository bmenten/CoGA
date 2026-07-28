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
| Standards | IEC 62304 §5.6 (integration & integration testing), §5.7 (system testing), §16.1 (reproducibility); IVDR Annex I §16.1 |

> Controlled companion to [TF-09](TF-09-verification-validation.md). Defines the **system-level
> end-to-end verification** of CoGA: a small, hand-curated **golden dataset** with **documented
> expected results** is driven through the *real* pipeline — ingestion → ClickHouse/Postgres →
> query/API → clinical review/audit → signed report — against live Postgres 16 + ClickHouse 25.3.
>
> It exercises the device boundary from [TF-02](TF-02-device-description.md) — **annotated
> VCF (+ tracks) → signed clinical report** — as one deterministic, repeatable run, complementing
> the unit suite (mocked datastores) and the `smoke` startup test. In H11.1-OP5 terms this is
> *acceptatietesten op data in een omgeving die de productie benadert*: a fixed verification
> dataset whose expected outputs are pinned, so a regression anywhere along the chain fails CI.

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
| Pathogenic SNV | nonsense variant | ingested; on review → ACMG **Likely Pathogenic (class 4)** from PVS1+PM2 |
| De novo SNV | proband het, both parents hom-ref | flagged de novo |
| Compound het (SNV+SNV) | two variants in one gene, trans across parents | paired as a compound-het group |
| Compound het (SNV+SV) | maternal SNV + paternal DEL over the same gene | SV second-hit: `phase=trans`, `deletion_unmasked=true` |
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
| Realistic demo bundles through their real ingestion paths | [test_e2e_demo_smoke.py](../../backend/tests/e2e/test_e2e_demo_smoke.py) |
| Haplotype / lineage stage against the real stack (PGT segregation) | [test_e2e_haplotypes.py](../../backend/tests/e2e/test_e2e_haplotypes.py) |

A consolidated catalogue of these is in [docs/testing.md](../testing.md) ("End-to-end (golden pipeline)").

## 3. Acceptance criteria

The verification **passes** when, on a clean Postgres 16 + ClickHouse 25.3:

- the golden-trio package imports with every enabled dataset `imported`;
- every per-stage assertion in §2 matches `EXPECTED.yaml`;
- a clinical review round-trips with **server-recomputed** ACMG (client-supplied totals ignored),
  appends a hash-chained audit, and the chain re-verifies (`verified=true`); a trigger-bypass
  edit is detected (tamper-**evident**) and the audit table rejects UPDATE/DELETE (append-only);
- a report signs out, increments version, and its chain verifies;
- a malformed/failed import is reported **failed** and leaves **no partial family** (fail-clean);
- the NIPT bundle recovers fetal fraction ≈ 0.12 and all 8 monogenic categories.

Any deviation is a verification finding handled per [TF-09 §5](TF-09-verification-validation.md)
(anomaly handling) and, if clinically relevant, TF-17 vigilance/CAPA.

## 4. How it runs (gate)

- **Locally:** bring up the datastores (`docker compose up -d postgres clickhouse`) and run
  `RUN_INTEGRATION=1 python -m pytest backend/tests/e2e`.
- **CI:** the **`e2e`** job in `.github/workflows/ci.yml` provisions `postgres:16` +
  `clickhouse/clickhouse-server:25.3`, runs the suite on every PR and on push to `main`, and (like
  `smoke`) runs outside the coverage-measured job. It is a **required status check** on `main`
  with strict (up-to-date-before-merge) enforcement, so a failing golden-trio run blocks the
  merge (see [TF-09 §1](TF-09-verification-validation.md)).

## 5. Reproducibility

The fixture, expected results, and assertions are version-controlled and deterministic: the same
commit yields the same pass/fail verdict. This operationalises the §16.1 reproducibility
requirement at the pipeline level, above the per-report content-hash reproducibility noted in
[TF-09 §2](TF-09-verification-validation.md).

## 6. Mapping to the CMGG report form (H11.1-F12.2)

This document feeds the **EFFECTIVE UITVOERING** axis of the bio-IT ingangsvalidatie
([TF-09 §7](TF-09-verification-validation.md)):

| H11.1-F12.2 axis | Evidence here |
| --- | --- |
| **Accuraatheid / juistheid** | Per-stage expected-vs-actual on a fixed dataset (§1–§3): variant calling pass-through, compound-het/de-novo logic, ACMG/CNV/repeat classification, NIPT categories. |
| **Traceerbaarheid** | End-to-end assertion of the immutable audit chain + content-hashed sign-out over a real review round-trip. |
| **Data-integriteit** | Append-only enforcement + tamper-evidence asserted against the live DB; fail-clean import (no partial state). |
| **Continuïteit** | Reproducible-from-fixture run; deterministic re-import without duplication. |

## 7. Limitations / out of scope

- **Synthetic data only** — analytical/clinical accuracy on real material is the concordance study
  in [TF-10](TF-10-performance-evaluation-plan.md)/[TF-11](TF-11-performance-evaluation-report.md),
  not this document.
- **Browser/UI coverage is shallow** — the browser verification ([TF-09d](TF-09d-browser-e2e-verification.md):
  Playwright `frontend/e2e/`, CI job `e2e-playwright`) drives Chromium through login → family
  workspace → genome-overview render → in-browser sign-out, but it asserts wiring + that the viz
  draws (SVG/canvas), not chart correctness or deep review flows. It is a **required check**, but
  **shallow by design**. Summative usability remains [TF-12](TF-12-usability.md).
- **Tamper-evident, not tamper-proof** — the in-DB chain detects edits/reordering by a privileged
  bypass but not a determined owner who re-chains; that residual is the external signed integrity
  anchor (see [clinical-traceability.md](../clinical-traceability.md)).
