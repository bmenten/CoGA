import { describe, expect, it } from 'vitest';

import {
  alphaMissenseTone,
  caddTone,
  polyphenTone,
  revelTone,
  siftTone,
  spliceAiTone,
} from '../inSilicoTones';
import { SPLICEAI_NO_IMPACT, SPLICEAI_PP3_MODERATE, SPLICEAI_PP3_SUPPORTING } from '../../../lib/acmg/evaluate';

// Each bound belongs to the range above it, as in seqr: a value at a bound reads as the
// stronger pathogenic or the weaker benign step.
describe('the marks of the in-silico predictions', () => {
  it('reads CADD by seqr’s bounds, moderate pathogenic at most', () => {
    expect([0.15, 0.151, 22.79, 22.8, 25.29, 25.3, 28.09, 28.1, 99].map(caddTone)).toEqual([
      'benign-strong',
      'benign',
      'benign',
      'indeterminate',
      'indeterminate',
      'pathogenic-supporting',
      'pathogenic-supporting',
      'pathogenic',
      'pathogenic',
    ]);
  });

  it('reads REVEL as the ACMG evaluation’s REVEL cut-offs at three decimals', () => {
    expect([0.016, 0.017, 0.29, 0.291, 0.643, 0.644, 0.772, 0.773, 0.931, 0.932].map(revelTone)).toEqual([
      'benign-strong',
      'benign',
      'benign',
      'indeterminate',
      'indeterminate',
      'pathogenic-supporting',
      'pathogenic-supporting',
      'pathogenic',
      'pathogenic',
      'pathogenic-strong',
    ]);
  });

  it('reads AlphaMissense by its score, else by its class', () => {
    expect([0.07, 0.071, 0.169, 0.17, 0.791, 0.792, 0.906, 0.99].map((score) => alphaMissenseTone(score))).toEqual([
      'benign-strong',
      'benign',
      'benign',
      'indeterminate',
      'indeterminate',
      'pathogenic-supporting',
      'pathogenic',
      'pathogenic-strong',
    ]);
    // The score decides when both are there.
    expect(alphaMissenseTone(0.05, 'likely_pathogenic')).toBe('benign-strong');
    expect(['likely_pathogenic', 'likely_benign', 'ambiguous', '', null].map((amClass) => alphaMissenseTone(null, amClass))).toEqual([
      'pathogenic-supporting',
      'benign',
      'indeterminate',
      null,
      null,
    ]);
  });

  it('reads SIFT and PolyPhen from the score in VEP’s call, SIFT low as deleterious', () => {
    expect(['deleterious(0)', 'deleterious(0.001)', 'deleterious(0.002)', 'tolerated(0.08)', 'tolerated(0.081)'].map(siftTone)).toEqual([
      'pathogenic-supporting',
      'pathogenic-supporting',
      'indeterminate',
      'indeterminate',
      'benign',
    ]);
    expect(['benign(0.113)', 'benign(0.114)', 'possibly_damaging(0.977)', 'probably_damaging(0.978)', 'probably_damaging(0.999)'].map(polyphenTone)).toEqual([
      'benign',
      'indeterminate',
      'indeterminate',
      'pathogenic-supporting',
      'pathogenic',
    ]);
    // A call without a score has no mark.
    expect([siftTone('deleterious'), polyphenTone(''), siftTone(null)]).toEqual([null, null, null]);
  });

  it('reads SpliceAI by the cut-offs the ACMG evaluation reads', () => {
    expect([SPLICEAI_NO_IMPACT, SPLICEAI_PP3_SUPPORTING, SPLICEAI_PP3_MODERATE]).toEqual([0.1, 0.2, 0.5]);
    expect([0.09, 0.1, 0.19, 0.2, 0.49, 0.5, 1].map(spliceAiTone)).toEqual([
      'benign',
      'indeterminate',
      'indeterminate',
      'pathogenic-supporting',
      'pathogenic-supporting',
      'pathogenic',
      'pathogenic',
    ]);
  });

  it('marks no value it cannot read', () => {
    expect([caddTone(null), revelTone(undefined), spliceAiTone(Number.NaN)]).toEqual([null, null, null]);
  });
});
