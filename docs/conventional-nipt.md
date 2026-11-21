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

- A family holds a conventional NIPT when its package says `analysis_type: conventional_nipt` and
  names its pregnancy (`pregnancy_id`, required, [coga-package.md](coga-package.md)): the import adds
  its key (`conventional_nipt:ZW1`) to the family's analyses (`family.metadata.analyses`), where a
  monogenic NIPT of the same pregnancy, and the NIPTs of the woman's other pregnancies, may stand
  beside it. A family holds one conventional NIPT per pregnancy, each an analysis of its own (the
  owner's decision of 2026-11-21). Its member is the maternal plasma, the mother of the pregnancy,
  whose assays then hold `cnipt_cfdna` (`sample.metadata.assays`); the pregnancy's fetus may be
  listed without data (role `fetus`; two in a twin pregnancy). A plasma is one pregnancy's: an
  import of another pregnancy that lists it is refused.
- The cNIPT plasma may carry another sample ID than the monogenic NIPT's plasma of the same
  pregnancy, and the plasma of a later pregnancy always does. The samples are one person, linked as
  the same individual: by the package's `same_individual_as` on the plasma, which names the other
  plasma (the family must already hold it), or with **Same individual** in the family's members
  editor. The pedigree draws the mother once, labelled with every ID. One person is no one
  pregnancy: each pregnancy's results stay its own. A plasma is a mixture of the mother's and a
  fetus's DNA, so Sample QC never compares it for identity; the cNIPT plasma has no genotypes either,
  so the link adds no Sample QC check ([data-import.md](data-import.md#the-existing-familys-members),
  [family-member-management.md](family-member-management.md#the-editor)).
- The `ccnv` dataset names, per plasma, ccnv's bins, segments, calls VCF and QC file. How they are
  read, checked and stored: [data-import.md](data-import.md#conventional-nipt-ccnv)
  (`ccnv_output.py`, `family_package_ccnv.py`).
- What is stored: the bins as the plasma's `coverage` track and the segments as its `segments` track
  (log2 ratios, ccnv's values in each row's `metadata_json`); the calls as structural variants
  (genotype `./1`); each under the pregnancy's own `ccnv` source (`ccnv_p…`,
  `family_package_records.suffixed_source`), which the readers read as `ccnv` (`source_base`), so
  an import of another pregnancy's conventional NIPT replaces none of it; the QC file under
  `samples.metadata.ccnv`.

## The views

The genome and chromosome views draw a ccnv track on a NIPT axis of its own, each segment in the
colour of ccnv's call, with dashed lines where a fetal trisomy and monosomy lie at the plasma's
fetal fraction (`frontend/src/lib/ccnvTracks.ts`,
[data-import.md](data-import.md#how-tracks-are-drawn)). The track availability serves the plasma's
ccnv summary (`ccnv`, `ccnv_output.ccnv_track_summary`); a segments read serves each segment's
values (`details`).

## The result

`cnipt_result.evaluate_cnipt` (pure) gives each target a result: each autosome (an aneuploidy,
whole, or of at least half the chromosome), the sex chromosomes, and the segmental CNVs. It reads the plasma's ccnv QC
(`stored_ccnv_qc`) and segments, and the rule's bounds. The rule is the owner's choice of
2026-11-19, *z-score and fetal-fraction concordance*.

**The fetal fraction.** ccnv's effective fetal fraction (the Fisher-weighted one, which ccnv's own
expected ratios and lines use; the owner's choice). The other estimates are shown beside it.

**No result** for every target when the fetal fraction is below the `cnipt.fetal_fraction` bound,
or when a bound the rule needs has no error value. A chromosome ccnv gives no segment of has no
result either.

**Findings.** Each segment ccnv called, and each segment whose |z-score| reaches the grey zone, is
a finding:

- *Kind*: the findings of one direction (gain or loss) on a chromosome that span together at least
  half of its segmented length (`WHOLE_CHROMOSOME_SHARE`) are an aneuploidy of that chromosome
  (whole, or of most of it), however many segments ccnv cut it into (`_aneuploid_signals`); the
  others are segmental CNVs. An event of one arm shorter than half the chromosome (18p, say) is a
  segmental CNV.
- *Segmental CNVs* are assessed from a size: the `cnipt.min_cnv_size` bound, and no smaller than the
  3 Mb ccnv's focal limit of detection is given for when ccnv gives one (`lod_ff_3mb`); raised to
  35 Mb where the fetal fraction is below that limit. None is assessed where it is below ccnv's
  limit of arm-level events (`lod_ff_35mb`). A shorter one is `not_assessed`.
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
  - **high risk**: at a confidence of at least the `cnipt.sca_confidence` bound, any call but XX and
    XY (XO, XXX, XXY, XYY…), or one ccnv marks aneuploid (`is_aneuploid`);
  - **inconclusive**: a call below that confidence, or mixed signals (`DISCORDANT_MIXED`);
  - **low risk**: a euploid call (XX, XY) at that confidence;
  - an aneuploidy finding on X or Y raises it.
- The segmental target takes the worst assessed segmental finding. A low risk gives in its reasons
  the size assessed from, each low-risk finding and each finding left unassessed.
- **Warnings**: a fetal fraction below the warning bound, ccnv's low-FF warning, ccnv's prenatal and
  fragmentomics warnings, and a twin pregnancy ccnv reads. In a twin pregnancy an aneuploidy of one
  fetus lies at about half the fetal fraction. The owner put twins in scope with one fetal fraction
  and one result per plasma.

**The claim.** Each target says whether the intended purpose claims it (`claimed`, from
`CLAIMED_TARGETS`): trisomies 21, 18 and 13 and the sex chromosomes, for the sex-chromosome
aneuploidies (TF-01 §3.7, the owner's decision of 2026-11-20). The other autosomes and the
segmental CNVs keep their result and are shown outside the claim:

- the cNIPT page writes *outside the validated claim* under each such target's name and on the
  folded list of the other autosomes, and states the claim under the result (`OUTSIDE_CLAIM` and
  `CNIPT_CLAIM_NOTE` in `cniptResult.ts`);
- the report page's summary (`CniptReportSummary.tsx`) and the signed view (`CniptResults`) add
  it to each such target's line and state the claim; the signed record keeps `claimed` with each
  target, as it was when signed.

A change of `CLAIMED_TARGETS` is a change of the intended purpose, under change control.

The population is the intended purpose's (TF-01 §3.7): pregnancies of more than 12 weeks,
singleton or twin. Triplets or more, a vanishing twin, a maternal organ or bone-marrow transplant
or a blood transfusion in the months before sampling, and a known maternal malignancy are outside
the validated scope.

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

- `GET /families/{family_id}/cnipt?analysis=` (`routers/families_cnipt.py`, `cnipt_service.py`,
  `schemas/cnipt.py`): the conventional NIPT of one pregnancy (`analysis`, named when the family
  holds the conventional NIPT of several pregnancies; 422 without one): each plasma its package
  lists, with its findings, fetal fractions, sex-chromosome call, resolution, warnings and the bounds
  it applied, and the pregnancy's `fetuses` with `multiple_pregnancy_note` for a multiple pregnancy
  (a finding may be present in one fetus or in more than one; it does not apply to a monozygotic
  twin). A family without ccnv output answers 404.
- The page `/families/{id}/cnipt` (`FamilyCniptPage.tsx`, `cniptResult.ts`) and its tab *cNIPT* in
  the family's module row, shown for a conventional NIPT family; it offers the pregnancy beside the
  family's name when the family holds several, the last imported first, and shows the note.

## Sign-out

A conventional NIPT family is signed out like every family, from its report page, which shows the
live result above the reported findings (`CniptReportSummary.tsx`). The signed record holds the
section `cnipt` (`signed_record_cnipt.py`) of the conventional NIPT signed out: the analysis, the
pregnancy's fetuses and the multiple-pregnancy note, and each plasma's result, the same object the page shows
(each target with its verdict, reasons and `claimed`), with the bounds it applied and its inputs (a digest of the kept QC object, the number and a digest of
the segments read, the segments files). The signed view renders it (`CniptResults` in
`SignedFamilyReport.tsx`, parsed in `signedReportRecord.ts`).

Under the Sample QC's acknowledgement (`cnipt_concerns` in the gate's 409 and in the audit event),
the sign-out is held back by a plasma without a result, by a result that rests on provisional
bounds, and by a record of a conventional NIPT that froze no result: a section that could not be
read, none, or one without a plasma of the analysis. The Sample QC runs no check for a conventional NIPT (application `cnipt` in
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
