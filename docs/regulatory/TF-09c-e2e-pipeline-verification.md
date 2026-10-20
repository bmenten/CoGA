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
| A live import whose heartbeat goes stale and whose job another worker ends: its heartbeat stops it where it is, leaving the family marked and gating sign-out; an overwrite whose backup was dropped meanwhile deletes nothing when it restores, and its flag names as imported only what is there (TF-06 H16, #746) | [test_e2e_import_stale_heartbeat.py](../../backend/tests/e2e/test_e2e_import_stale_heartbeat.py) |
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
| A trio's per-sample NeedlR files whose record IDs repeat across the files: each call an SV of its own position with its own sample's call, one event in two samples' files one SV with both calls (the mother's own, not the no-call the proband's line gives her), the record IDs kept in the annotations (CLIN-43) | [test_e2e_needlr_coordinate_ids.py](../../backend/tests/e2e/test_e2e_needlr_coordinate_ids.py) |
| A deletion a NeedlR package and a Sniffles upload both call, under one id: each caller's row listed with its own genes, pLI and inheritance; a CNV classification saved on either row freezes that row's evidence, and the drift check compares it with that row (a change to the other caller's call does not drift it, one to its own does); a save naming no caller is refused (422) and writes nothing, one naming a caller that does not call the SV is refused (404), and one naming no caller of an SV one caller calls takes that row (TRACE-11) | [test_e2e_cnv_evidence_own_caller.py](../../backend/tests/e2e/test_e2e_cnv_evidence_own_caller.py) |
| A HiFiCNV depth file without a usable autosomal median is stored as read, and its track's record says so (`normalization: none`) where it said `log2_ratio_to_autosomal_median`; the track availability serves each coverage track's recorded transform, which the viewers read before drawing a track on their log2-ratio axis (TRACE-7) | [test_e2e_depth_track_transform.py](../../backend/tests/e2e/test_e2e_depth_track_transform.py) |
| Each track loader replaces only its own source's track of a sample, rows and records: a package's coverage BED keeps the WisecondorX and QDNAseq bins imported before it, coverage and APCAD imported again keep HiFiCNV's depth and minor allele fraction, an invalid BED uploaded with overwrite is refused with every track and its record as they were, and a BED upload is not refused for another caller's track; the track availability lists every caller's track (SAFE-22) | [test_e2e_track_loaders_keep_other_sources.py](../../backend/tests/e2e/test_e2e_track_loaders_keep_other_sources.py) |
| A small-variant upload with overwrite reads and checks the whole file before it replaces the callset: a file naming a sample the family lacks, with no record it can store, or cut short as gzip is refused with both callsets, the haplotype blocks and their records as they were; a valid one replaces them; a write that fails after the delete leaves the family flagged import-incomplete for the callset, as its page reads it, until the callset is uploaded again. A PED upload with overwrite that is refused leaves the family's variants; one that succeeds replaces them (SAFE-25, TF-06 H16) | [test_e2e_small_variant_upload_reads_before_replacing.py](../../backend/tests/e2e/test_e2e_small_variant_upload_reads_before_replacing.py) |
| A structural-variant upload whose rewrite fails after its delete puts every sample's calls back; one that cannot put them back leaves the family marked for each sample's calls (`import_unfinished`, `upload-…`), which every family page reads and which refuses sign-out until each sample's file is uploaded again with overwrite; the admin delete of a sample's SVs puts the family's SVs back when its rewrite fails; a small-variant upload whose process stops after its delete leaves the family marked until the callset is uploaded again; a BED upload marks and unmarks the family under its own locks; a structure change that clears the data and then fails leaves the family's variants (SAFE-28, TF-06 H16) | [test_e2e_writes_mark_before_delete.py](../../backend/tests/e2e/test_e2e_writes_mark_before_delete.py) |
| A file without a record replaces nothing: an overwrite import whose WisecondorX bins file holds its header alone fails, the dataset failed and the bins kept, rows and record; a TRGT VCF with its header alone uploaded with overwrite is refused (400) with the sample's call kept; an update whose QDNAseq bins file holds its header alone fails the dataset and flags the family import-incomplete for it (SAFE-29); a chrM VCF with its header alone fails the `mito` dataset before any sample's calls are replaced, every call kept, and flags a family the import keeps for `mito` (the owner's decision) | [test_e2e_loaders_refuse_empty_files.py](../../backend/tests/e2e/test_e2e_loaders_refuse_empty_files.py), [test_e2e_mito_import_keeps_every_sample.py](../../backend/tests/e2e/test_e2e_mito_import_keeps_every_sample.py) |
| The readers that take coverage as read depth read one track of read depth per sample, never a log2 ratio: the mtDNA page reads a member's coverage BED beside WisecondorX's and QDNAseq's bins and HiFiCNV's log2 ratio, HiFiCNV's depths stored as read beside QDNAseq's bins, and for a member with bins only her calls' depth, and its configured `mtdna.mean_depth` limits judge those depths (each failed on a blend before); the NIPT coverage check without a per-target table reads the plasma's coverage BED beside WisecondorX's bins, its median and its uncovered stretch (CLIN-46); each BED declared as read depth, and each track left out named with the reason (TRACE-9) | [test_e2e_coverage_readers_read_depth.py](../../backend/tests/e2e/test_e2e_coverage_readers_read_depth.py) |
| Each coverage BED declares what it holds: a package's BED declared as read depth and one declaring nothing are recorded as `read_depth` and `unknown` (records and what the family holds), the track availability serves them, and the mtDNA coverage reads the first and, for the second, the member's calls' depth with a note why; a BED upload without `content` is refused (422) with nothing replaced, one declared as a log2 ratio is kept off the depth readers, and one declared as read depth is read as depth, the raw file's record saying each (TRACE-9) | [test_e2e_coverage_bed_declares_content.py](../../backend/tests/e2e/test_e2e_coverage_bed_declares_content.py) |

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
