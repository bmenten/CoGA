import { describe, expect, it } from 'vitest';

import { computeClassification } from '../score';
import type { AcmgSelection, AcmgStrength, AcmgCriterionCode } from '../types';

function sel(
  code: AcmgCriterionCode,
  strength: AcmgStrength,
  accepted = true,
): AcmgSelection {
  return { code, strength, accepted, autoSuggested: false };
}

describe('computeClassification', () => {
  it('returns VUS at 0 points with no selections', () => {
    const result = computeClassification([]);
    expect(result.points).toBe(0);
    expect(result.classKey).toBe('acmg_class_3');
  });

  it('only counts accepted selections', () => {
    const result = computeClassification([sel('PVS1', 'very_strong', false)]);
    expect(result.points).toBe(0);
    expect(result.classKey).toBe('acmg_class_3');
  });

  it.each([
    ['PVS1 (8) + PM2 (1) = 9 → Likely Pathogenic', [sel('PVS1', 'very_strong'), sel('PM2', 'supporting')], 9, 'acmg_class_4'],
    ['PVS1 (8) + PM2 moderate (2) = 10 → Pathogenic', [sel('PVS1', 'very_strong'), sel('PM2', 'moderate')], 10, 'acmg_class_5'],
    ['two benign supporting (-2) → Likely benign', [sel('BP4', 'supporting'), sel('BP7', 'supporting')], -2, 'acmg_class_2'],
    ['benign strong + supporting (-5) stays Likely benign', [sel('BS1', 'strong'), sel('BP4', 'supporting')], -5, 'acmg_class_2'],
    ['two benign strong (-8) tips to Benign', [sel('BS1', 'strong'), sel('BS2', 'strong')], -8, 'acmg_class_1'],
    ['mixed pathogenic and benign evidence nets toward VUS', [sel('PVS1', 'very_strong'), sel('BS1', 'strong')], 4, 'acmg_class_3'],
  ] as const)('%s', (_label, selections, points, classKey) => {
    const result = computeClassification([...selections]);
    expect(result.points).toBe(points);
    expect(result.classKey).toBe(classKey);
  });

  it('scores a criterion or strength it does not know as nothing, never as NaN, which would read as benign', () => {
    const result = computeClassification([
      sel('PVS1', 'very_strong'),
      sel('NOT_A_CODE' as AcmgCriterionCode, 'strong'),
      sel('PM2', 'not_a_strength' as AcmgStrength),
    ]);
    expect(result.points).toBe(8);
    expect(result.classKey).toBe('acmg_class_4');
  });

  it('BA1 forces Benign regardless of point total', () => {
    const result = computeClassification([
      sel('BA1', 'stand_alone'),
      sel('PVS1', 'very_strong'),
      sel('PM2', 'moderate'),
    ]);
    expect(result.standAloneBenign).toBe(true);
    expect(result.classKey).toBe('acmg_class_1');
  });
});

describe('VUS hot/warm/cold sub-tier', () => {
  it('0–1 points → cold', () => {
    expect(computeClassification([]).vusTier).toBe('cold');
    expect(computeClassification([sel('PM2', 'supporting')]).vusTier).toBe('cold');
  });

  it('2–3 points → warm', () => {
    expect(computeClassification([sel('PM2', 'moderate')]).vusTier).toBe('warm');
    expect(
      computeClassification([sel('PM2', 'moderate'), sel('PP3', 'supporting')]).vusTier,
    ).toBe('warm');
  });

  it('4–5 points → hot (one supporting from Likely Pathogenic)', () => {
    expect(computeClassification([sel('PS1', 'strong')]).vusTier).toBe('hot');
    expect(
      computeClassification([sel('PS1', 'strong'), sel('PP3', 'supporting')]).vusTier,
    ).toBe('hot');
  });

  it('is null outside the VUS band', () => {
    expect(computeClassification([sel('PVS1', 'very_strong')]).vusTier).toBeNull(); // LP
    expect(computeClassification([sel('BP4', 'supporting')]).vusTier).toBeNull(); // LB
    expect(computeClassification([sel('BA1', 'stand_alone')]).vusTier).toBeNull(); // Benign
  });
});
