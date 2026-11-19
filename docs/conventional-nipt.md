# Conventional NIPT — developer notes

A conventional NIPT (cNIPT) screens a pregnancy for aneuploidies and copy-number changes from the
genome-wide copy number of the maternal plasma (cell-free DNA, shallow whole-genome sequencing). The
laboratory's tool, ccnv, in its prenatal mode, computes the copy number and the fetal fraction. CoGA
imports its output, draws it in the genome and chromosome views, and gives an automatic result per
target. The in-app reference
[`frontend/src/content/docs/conventional-nipt.md`](../frontend/src/content/docs/conventional-nipt.md)
(shown at `/docs/reference/conventional-nipt`) explains the rule to lab users; keep the two in step
with the code. ccnv's own description of its methods: https://matthdsm.github.io/ccnv/.

## Family and input

- A family is a conventional NIPT when its package says `analysis_type: conventional_nipt`
  (`family.metadata.analysis_type`). Its member is the maternal plasma, the mother of the pregnancy,
  tagged `assay: cnipt_cfdna`; a fetus may be listed without data.
- The `ccnv` dataset names, per plasma, ccnv's bins, segments, calls VCF and QC file. How they are
  read, checked and stored: [data-import.md](data-import.md#conventional-nipt-ccnv)
  (`ccnv_output.py`, `family_package_ccnv.py`).
- What is stored: the bins as the plasma's `coverage` track and the segments as its `segments` track
  (source `ccnv`, log2 ratios, ccnv's values in each row's `metadata_json`); the calls as structural
  variants (source `ccnv`, genotype `./1`); the QC file under `samples.metadata.ccnv`.

## The views

The genome and chromosome views draw a ccnv track on a NIPT axis of its own, each segment in the
colour of ccnv's call, with dashed lines where a fetal trisomy and monosomy lie at the plasma's
fetal fraction (`frontend/src/lib/ccnvTracks.ts`,
[data-import.md](data-import.md#how-tracks-are-drawn)). The track availability serves the plasma's
ccnv summary (`ccnv`, `ccnv_output.ccnv_track_summary`); a segments read serves each segment's
values (`details`).

## The result

`cnipt_result.evaluate_cnipt` (pure) gives each target a result: each autosome (an aneuploidy,
whole or of an arm), the sex chromosomes, and the segmental CNVs. It reads the plasma's ccnv QC
(`stored_ccnv_qc`) and segments, and the rule's bounds. The rule is the owner's choice of
2026-11-19, *z-score and fetal-fraction concordance*.

**The fetal fraction.** ccnv's effective fetal fraction (the Fisher-weighted one, which ccnv's own
expected ratios and lines use; the owner's choice). The other estimates are shown beside it.

**No result** for every target when the fetal fraction is below the `cnipt.fetal_fraction` bound,
or when a bound the rule needs has no error value. A chromosome ccnv gives no segment of has no
result either.

**Findings.** Each segment ccnv called, and each segment whose |z-score| reaches the grey zone, is
a finding:

- *Kind*: a segment spanning at least half of its chromosome's segmented length
  (`WHOLE_CHROMOSOME_SHARE`) is read as an aneuploidy of that chromosome (whole, or of an arm);
  a shorter one as a segmental CNV.
- *Segmental CNVs* are assessed from a size: the `cnipt.min_cnv_size` bound, raised to 35 Mb where the
  fetal fraction is below ccnv's limit of detection of 3 Mb events (`lod_ff_3mb`). None is assessed
  where it is below ccnv's limit of arm-level events (`lod_ff_35mb`). A shorter one is `not_assessed`.
- *Verdict*:
  - **high risk**: |z| ≥ the `cnipt.call_zscore` error bound, and concordant with the fetal fraction.
    Concordant means the implied fetal fraction is within the band set by
    `cnipt.implied_ff_ratio_min` and `cnipt.implied_ff_ratio_max` (× the fetal fraction), ccnv does
    not flag the segment discordant, and its origin is not maternal or mosaic
    (`MATERNAL_OR_MOSAIC_ORIGINS`). An origin ccnv cannot tell (`INDETERMINATE`) does not hold a
    finding back.
  - **inconclusive**: |z| ≥ the error bound but not concordant; or |z| in the grey zone, at or above
    the warning bound and below the error bound.
  - **low risk**: a segment ccnv called whose |z| is below the grey zone.

**Targets.**

- An autosome takes the worst verdict of its aneuploidy findings: low risk without one.
- The sex chromosomes take ccnv's sex-chromosome call:
  - **high risk**: an aneuploidy (XO, XXX, XXY, XYY) at a confidence of at least the
    `cnipt.sca_confidence` bound;
  - **inconclusive**: a call below that confidence, or mixed signals (`DISCORDANT_MIXED`);
  - **low risk**: a euploid call (XX, XY) at that confidence;
  - an aneuploidy finding on X or Y raises it.
- The segmental target takes the worst assessed segmental finding. Without one it is low risk, and
  its reasons give the size assessed from and each finding left unassessed.
- **Warnings**: a fetal fraction below the warning bound, ccnv's low-FF warning, ccnv's prenatal and
  fragmentomics warnings, and a twin pregnancy ccnv reads. In a twin pregnancy an aneuploidy of one
  fetus lies at about half the fetal fraction. The owner put twins in scope with one fetal fraction
  and one result per plasma.

**The bounds** are QA's, in the QC-threshold profile `nipt_conventional` (catalogue entries
`cnipt.*` in `qc_threshold_service.QC_METRICS`, source `cnipt`). Each change takes a reason and is
kept in `qc_threshold_changes`. The rule compares as each entry's help text says, not by the generic
sequencing-QC evaluation. A bound QA has not set takes its provisional value from
`cnipt_result.PROVISIONAL_BOUNDS`, and the result marks it provisional; one QA set without an error
value gives no result.

| Bound | Warning | Error | Read as |
| --- | --- | --- | --- |
| `cnipt.fetal_fraction` | — | 0.04 | no result below the error bound; a warning below the warning bound |
| `cnipt.call_zscore` | 2.5 | 3 | a finding from the error bound, the grey zone from the warning bound |
| `cnipt.implied_ff_ratio_min` | — | 0.5 | sub-fetal below it |
| `cnipt.implied_ff_ratio_max` | — | 1.5 | super-fetal above it |
| `cnipt.sca_confidence` | — | 0.99 | ccnv's sex-chromosome call inconclusive below it |
| `cnipt.min_cnv_size` | — | 3 Mb | a segmental CNV not assessed below it |

The provisional values are for development. QA and the laboratory director confirm or change them
before the release candidate.

## Endpoint and page

- `GET /families/{family_id}/cnipt` (`routers/families_cnipt.py`, `cnipt_service.py`,
  `schemas/cnipt.py`): each plasma's result with its findings, fetal fractions, sex-chromosome call,
  resolution, warnings and the bounds it applied. A family without ccnv output answers 404.
- The page `/families/{id}/cnipt` (`FamilyCniptPage.tsx`, `cniptResult.ts`) and its tab *cNIPT* in
  the family's module row, shown for a conventional NIPT family.

## Sign-out

A conventional NIPT family is signed out like every family, from its report page, which shows the
live result above the reported findings (`CniptReportSummary.tsx`). The signed record holds the
section `cnipt` (`signed_record_cnipt.py`): each plasma's result, the same object the page shows,
with the bounds it applied and its inputs (a digest of the kept QC object, the number and a digest of
the segments read, the segments files). The signed view renders it (`CniptResults` in
`SignedFamilyReport.tsx`, parsed in `signedReportRecord.ts`).

Under the Sample QC's acknowledgement (`cnipt_concerns` in the gate's 409 and in the audit event),
the sign-out is held back by a plasma without a result and by a result that rests on provisional
bounds. The Sample QC runs no check for a conventional NIPT (application `cnipt` in
`sample_integrity_qc.py`, verdict `skip`, owner decision of 2026-11-19): ccnv's output holds no
genotypes, and as for a PGT embryo the fetal sex is known only from the analysis, ccnv's call in
the result. The full description is in
[clinical-traceability.md](clinical-traceability.md), *A conventional NIPT family's record*.

## Where the code lives

- Backend: `ccnv_output.py` (ccnv's files, the stored QC, the track summary), `family_package_ccnv.py`
  (the dataset, Discover), `cnipt_result.py` (the rule, pure), `cnipt_service.py`,
  `routers/families_cnipt.py`, `schemas/cnipt.py`, `signed_record_cnipt.py` (the record's section and
  the gate's concerns); the import in `family_package_datasets.py`
  (`_import_ccnv_dataset`) and `family_package_tracks.py` (`_import_ccnv_track`).
- Frontend: `FamilyCniptPage.tsx`, `cniptResult.ts`, `CniptReportSummary.tsx`; the signed view's
  `CniptResults` (`SignedFamilyReport.tsx`, `signedReportRecord.ts`); the views' ccnv mode in
  `CoverageSegmentsChart.tsx`, `ccnvTracks.ts`, `CcnvTrackNote.tsx`.
- Tests: listed in [testing.md](testing.md).
