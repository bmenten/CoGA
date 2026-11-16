import {
  SPLICEAI_NO_IMPACT,
  SPLICEAI_PP3_MODERATE,
  SPLICEAI_PP3_SUPPORTING,
} from '../../lib/acmg/evaluate';

/**
 * How a predictor's value reads, as the mark beside it on a variant card shows it.
 *
 * Six steps, from strong benign to strong pathogenic, coloured as seqr colours them:
 * green, olive, grey, yellow, red and dark red.
 */
export type PredictorTone =
  | 'benign-strong'
  | 'benign'
  | 'indeterminate'
  | 'pathogenic-supporting'
  | 'pathogenic'
  | 'pathogenic-strong';

export const PREDICTOR_TONE_LABELS: Record<PredictorTone, string> = {
  'benign-strong': 'benign, strong',
  benign: 'benign',
  indeterminate: 'indeterminate',
  'pathogenic-supporting': 'pathogenic, supporting',
  pathogenic: 'pathogenic, moderate',
  'pathogenic-strong': 'pathogenic, strong',
};

// Each predictor's ranges, lowest first: a value takes the tone of the first range whose upper
// bound it is below. The bounds are seqr's (ORDERED_PREDICTOR_FIELDS), which rest on ClinGen's
// calibration of the predictors for PP3/BP4 (Pejaver et al. 2022; for AlphaMissense, ClinGen's
// 2025 recommendation). REVEL's agree with the REVEL cut-offs of the ACMG evaluation to REVEL's
// three decimals.
type Ranges = ReadonlyArray<readonly [number, PredictorTone]>;

const CADD_PHRED: Ranges = [
  [0.151, 'benign-strong'],
  [22.8, 'benign'],
  [25.3, 'indeterminate'],
  [28.1, 'pathogenic-supporting'],
  [Infinity, 'pathogenic'],
];

const REVEL: Ranges = [
  [0.0161, 'benign-strong'],
  [0.291, 'benign'],
  [0.644, 'indeterminate'],
  [0.773, 'pathogenic-supporting'],
  [0.932, 'pathogenic'],
  [Infinity, 'pathogenic-strong'],
];

const ALPHA_MISSENSE: Ranges = [
  [0.071, 'benign-strong'],
  [0.17, 'benign'],
  [0.792, 'indeterminate'],
  [0.906, 'pathogenic-supporting'],
  [0.99, 'pathogenic'],
  [Infinity, 'pathogenic-strong'],
];

const POLYPHEN: Ranges = [
  [0.114, 'benign'],
  [0.978, 'indeterminate'],
  [0.999, 'pathogenic-supporting'],
  [Infinity, 'pathogenic'],
];

// SIFT reads the other way: a low score is deleterious.
const SIFT: Ranges = [
  [0.002, 'pathogenic-supporting'],
  [0.081, 'indeterminate'],
  [Infinity, 'benign'],
];

// SpliceAI by the cut-offs the ACMG evaluation reads for PP3, BP4 and BP7, so the card's mark
// and the evidence the classifier proposes never disagree. seqr reads 0.5 and 0.8 instead.
const SPLICE_AI: Ranges = [
  [SPLICEAI_NO_IMPACT, 'benign'],
  [SPLICEAI_PP3_SUPPORTING, 'indeterminate'],
  [SPLICEAI_PP3_MODERATE, 'pathogenic-supporting'],
  [Infinity, 'pathogenic'],
];

const toneIn = (ranges: Ranges, value?: number | null): PredictorTone | null => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return ranges.find(([upper]) => value < upper)?.[1] ?? null;
};

// The score VEP writes after a SIFT or PolyPhen call: `deleterious(0.01)`.
const scoreInCall = (value?: string | null): number | null => {
  const match = /\(\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\)\s*$/.exec(value || '');
  return match ? Number(match[1]) : null;
};

export const caddTone = (phred?: number | null) => toneIn(CADD_PHRED, phred);
export const revelTone = (revel?: number | null) => toneIn(REVEL, revel);
export const spliceAiTone = (maxDelta?: number | null) => toneIn(SPLICE_AI, maxDelta);
export const siftTone = (call?: string | null) => toneIn(SIFT, scoreInCall(call));
export const polyphenTone = (call?: string | null) => toneIn(POLYPHEN, scoreInCall(call));

/**
 * AlphaMissense by its score; without one, by its class, read as the ACMG evaluation reads it
 * (likely pathogenic supports PP3, likely benign BP4).
 */
export const alphaMissenseTone = (
  pathogenicity?: number | null,
  amClass?: string | null,
): PredictorTone | null => {
  const scored = toneIn(ALPHA_MISSENSE, pathogenicity);
  if (scored) return scored;
  switch ((amClass || '').trim().toLowerCase()) {
    case 'likely_pathogenic':
    case 'likely pathogenic':
      return 'pathogenic-supporting';
    case 'likely_benign':
    case 'likely benign':
      return 'benign';
    case 'ambiguous':
      return 'indeterminate';
    default:
      return null;
  }
};
