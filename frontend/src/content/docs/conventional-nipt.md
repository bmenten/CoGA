# Conventional NIPT (cell-free DNA) — reference

A conventional NIPT screens a pregnancy for aneuploidies and copy-number changes from the
**genome-wide copy number of the maternal plasma**. The plasma's cell-free DNA (cfDNA) is mostly the
mother's, with a small **fetal fraction (FF)** from the placenta. The laboratory's tool, **ccnv**, in
its prenatal mode, measures the copy number in 100 kb bins, segments it, calls gains and losses and
estimates the fetal fraction and the fetal sex. CoGA imports its output, draws it, and gives an
**automatic result per target**.

> **Screening, not diagnosis.** A high-risk result calls for confirmatory diagnostic testing. An
> inconclusive result needs the analyst's judgement and, often, a repeat or a diagnostic test.

---

## What CoGA reads

A conventional NIPT family is imported from a package whose analysis is **cNIPT**
(`analysis_type: conventional_nipt`). Its member is the maternal plasma, the mother of the pregnancy.
For it, CoGA reads ccnv's four files: the bins, the segments, the gain and loss calls, and the QC
file. They must be one run's result: the calls state the QC file's fetal sex, fetal fraction and
sex-chromosome call, and name the same sample, or the import refuses them.

| CoGA shows | From ccnv |
| --- | --- |
| The plasma's coverage and segments in the genome and chromosome views | the bins and segments (log2 ratios) |
| Gains and losses on the structural-variant page and track | the calls |
| The fetal fractions, fetal sex and sex-chromosome call | the QC file |

## The fetal fraction

CoGA uses ccnv's **effective** fetal fraction for the result and for the expected lines in the
views: the Fisher-weighted fraction ccnv's own expected ratios use. ccnv's other estimates are shown
beside it: the fused *active* fraction with its standard error, and the fractions from chrY (FFY),
chrX (FFX), the autosomes (FFA) and fragment sizes.

## In the genome and chromosome views

A fetal trisomy raises the plasma's log2 ratio by only log2(1 + FF/2): about +0.08 at a fetal
fraction of 12 %. So the plasma's track has an axis of its own (±0.3).

- Each segment is drawn in the colour of ccnv's call: gain, loss, or **purple** where ccnv flags it
  as discordant with the fetal fraction.
- Dashed lines mark where a fetal trisomy (above) and monosomy (below) lie at the fetal fraction, on
  chromosomes 1–22 and X.
- Hover a segment for its values, and the track's head for the fetal-fraction estimates.

## The result

Each target gets one result:

| Result | Means |
| --- | --- |
| **High risk** | a finding concordant with the fetal fraction (below), or ccnv's sex-chromosome call of an aneuploidy at the required confidence |
| **Inconclusive** | a finding that is not concordant, a finding in the grey zone, or a sex-chromosome call below the required confidence or of mixed signals |
| **Low risk** | none of these |
| **No result** | the fetal fraction is below its bound, a bound the rule needs is not set, or ccnv gives no segment of the chromosome |
| **Not assessed** | segmental CNVs the fetal fraction cannot show, by ccnv's limits of detection |

The targets are each autosome (chromosomes 21, 18 and 13 are read first), the sex chromosomes and
the segmental CNVs.

**Findings.** CoGA looks at every segment ccnv called and every segment whose |z-score| reaches the
grey zone.

- A segment spanning at least half of its chromosome is read as an **aneuploidy** of that chromosome
  (whole, or of an arm).
- A shorter segment is a **segmental CNV**. Segmental CNVs are assessed from 3 Mb, or from 35 Mb
  where the fetal fraction is below ccnv's limit of detection of 3 Mb events. None is assessed where
  the fetal fraction is below ccnv's limit for arm-level events.
- A finding is **high risk** when its |z-score| reaches the call bound (3) and it is concordant with
  the fetal fraction:
  - its implied fetal fraction lies within 0.5 to 1.5 times the plasma's;
  - ccnv does not flag it discordant;
  - its origin is not maternal or mosaic.
- It is **inconclusive** when it reaches the call bound but is not concordant: sub-fetal (a mosaic,
  or the mother's own mosaic event), super-fetal (the mother's own CNV), maternal or mosaic by its
  fragment sizes. It is inconclusive too when its |z-score| lies in the grey zone (2.5 to 3).

**The sex chromosomes** follow ccnv's sex-chromosome call. An aneuploidy (XO, XXX, XXY, XYY) at a
confidence of 99 % or more is high risk; a euploid call (XX, XY) at that confidence is low risk;
anything else is inconclusive.

**Twins.** In a twin pregnancy an aneuploidy of one fetus lies at about half the fetal fraction, at
the edge of the concordant band. The page says when ccnv reads a twin pregnancy.

## The bounds

The bounds of the rule are QA's to set: *Admin → Sequencing QC thresholds*, profile **Conventional
NIPT**, each change with its reason in the append-only history. Until QA sets a bound, CoGA applies
a **provisional** value, and the page marks it so:

| Bound | Provisional value |
| --- | --- |
| Fetal fraction (no result below) | 4 % |
| Segment \|z-score\| (grey zone from, finding from) | 2.5, 3 |
| Implied fetal fraction, concordant band | 0.5 to 1.5 × the fetal fraction |
| Sex-chromosome call confidence | 99 % |
| Smallest segmental CNV assessed | 3 Mb |

The provisional values are not validated: QA and the laboratory director confirm or change them
before clinical use.

## Sign-out

The family is signed out from its **Report** page, which shows the result above the reported
findings. The signed version freezes each plasma's result as the cNIPT page showed it: the verdicts,
the findings, the fetal fractions, the bounds (each QA's or provisional) and the files it was read
from.

The sign-out asks for an acknowledgement with a reason, in the Sample QC's dialog, when a plasma has
no result or its result rests on provisional bounds. The Sample QC runs no check of its own here:
ccnv's output holds no genotypes, and, as for a PGT embryo, the fetal sex is known only from the
analysis (ccnv's call, in the result).

## Limits

- CoGA judges ccnv's segments and calls: it does not segment the bins again, and it cannot see an
  event ccnv's segmentation did not separate.
- A mosaic fetal aneuploidy, confined placental mosaicism and a maternal CNV can each look like
  another; the origin and the implied fetal fraction help tell them apart, but an inconclusive
  result is the analyst's to judge.
- ccnv's limits of detection, as fetal fractions, are its own estimates for the sample.
