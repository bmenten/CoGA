// How the small-variant track marks a variant — #529: the classes were told apart by
// the colour of one dot shape, and a review tag's colour hid the ClinVar class.

import { describe, expect, it } from 'vitest';

import {
  SMALL_VARIANT_MARKS,
  markDrawRank,
  smallVariantMarkExtent,
  smallVariantMarkKind,
  smallVariantMarkPath,
} from '../smallVariantMarks';

describe('smallVariantMarkKind', () => {
  it('lets a decisive ClinVar class win over the impact', () => {
    expect(smallVariantMarkKind({ clinvar: 'Pathogenic', impact: 'LOW' })).toBe('pathogenic');
    expect(smallVariantMarkKind({ clinvar: 'Likely_pathogenic' })).toBe('pathogenic');
    expect(smallVariantMarkKind({ clinvar: 'Benign/Likely benign', impact: 'HIGH' })).toBe('benign');
  });

  // The ClinVar reading itself (lib/clinvar) is tested in clinvar.test.ts.
  it('does not read "conflicting interpretations of pathogenicity" as pathogenic', () => {
    expect(
      smallVariantMarkKind({ clinvar: 'Conflicting_interpretations_of_pathogenicity', impact: 'HIGH' }),
    ).toBe('high');
  });

  it('marks by impact otherwise', () => {
    expect(smallVariantMarkKind({ impact: 'HIGH' })).toBe('high');
    expect(smallVariantMarkKind({ impact: 'moderate' })).toBe('moderate');
    expect(smallVariantMarkKind({ impact: 'MEDIUM' })).toBe('moderate');
    expect(smallVariantMarkKind({ impact: 'LOW' })).toBe('other');
    expect(smallVariantMarkKind({ impact: 'MODIFIER', clinvar: 'Uncertain significance' })).toBe('other');
    expect(smallVariantMarkKind({})).toBe('other');
  });
});

describe('the marks', () => {
  it('give the salient classes their own shape, not only a colour', () => {
    const paths = new Set(
      (['pathogenic', 'high', 'benign', 'other'] as const).map((kind) => smallVariantMarkPath(kind, 1.8)),
    );
    expect(paths.size).toBe(4);
    // MODERATE and LOW share the dot; they differ by colour, and the tooltip names the impact.
    expect(smallVariantMarkPath('moderate', 1.8)).toBe(smallVariantMarkPath('other', 1.8));
    expect(SMALL_VARIANT_MARKS.moderate.color).not.toBe(SMALL_VARIANT_MARKS.other.color);
  });

  it('are larger for the more salient classes, and drawn over the others', () => {
    const extent = (kind: Parameters<typeof smallVariantMarkExtent>[0]) => smallVariantMarkExtent(kind, 1.8);
    expect(extent('pathogenic')).toBeGreaterThan(extent('high'));
    expect(extent('high')).toBeGreaterThan(extent('other'));
    expect(markDrawRank('pathogenic')).toBeGreaterThan(markDrawRank('high'));
    expect(markDrawRank('high')).toBeGreaterThan(markDrawRank('moderate'));
    expect(markDrawRank('moderate')).toBeGreaterThan(markDrawRank('other'));
  });

  it('draws a benign variant hollow, so it recedes', () => {
    expect(SMALL_VARIANT_MARKS.benign.hollow).toBe(true);
    expect(SMALL_VARIANT_MARKS.pathogenic.hollow).toBeUndefined();
  });
});
