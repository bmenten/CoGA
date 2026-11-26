// How the small-variant track marks a variant: ClinVar P/LP by a larger red diamond, a ClinVar
// conflict that lists P/LP by the same diamond in orange, every other class by the colour of a
// small dot.

import { describe, expect, it } from 'vitest';

import {
  SMALL_VARIANT_MARKS,
  SMALL_VARIANT_MARK_ORDER,
  markDrawRank,
  smallVariantMarkExtent,
  smallVariantMarkKind,
  smallVariantMarkPath,
} from '../smallVariantMarks';
import { SMALL_VARIANT_DOT_RADIUS, TRACK_DOT_RADIUS } from '../trackSampling';

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

  it('marks a conflict that lists P/LP by its own diamond, whatever the impact', () => {
    expect(smallVariantMarkKind({ clinvar: 'uncertain_significance&likely_pathogenic', impact: 'LOW' })).toBe(
      'conflicting_pathogenic',
    );
    expect(smallVariantMarkKind({ clinvar: 'benign&uncertain_significance', impact: 'HIGH' })).toBe('high');
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
  it('draw ClinVar P/LP as a diamond and every other class as a dot of its own colour', () => {
    const dot = smallVariantMarkPath('other', 1.8);
    expect(smallVariantMarkPath('pathogenic', 1.8)).not.toBe(dot);
    expect(smallVariantMarkPath('conflicting_pathogenic', 1.8)).toBe(smallVariantMarkPath('pathogenic', 1.8));
    (['benign', 'high', 'moderate'] as const).forEach((kind) => {
      expect(smallVariantMarkPath(kind, 1.8)).toBe(dot);
    });
    const colours = SMALL_VARIANT_MARK_ORDER.map((kind) => SMALL_VARIANT_MARKS[kind].color);
    expect(new Set(colours).size).toBe(colours.length);
    expect(SMALL_VARIANT_MARKS).toMatchObject({
      pathogenic: { color: '#dc2626' }, // red
      conflicting_pathogenic: { color: '#d97706' }, // orange
      benign: { color: '#16a34a' }, // green
      high: { color: '#ea580c' }, // orange
      moderate: { color: '#eab308' }, // yellow
      other: { color: '#9ca3af' }, // grey
    });
  });

  it('draw the dots 6 px across, larger than the other tracks\' points', () => {
    expect(SMALL_VARIANT_DOT_RADIUS * 2).toBe(6);
    expect(SMALL_VARIANT_DOT_RADIUS).toBeGreaterThan(TRACK_DOT_RADIUS);
  });

  it('keep the P/LP diamond larger than a dot and within a parental-origin row', () => {
    // The furthest point of a mark's path from its centre.
    const reach = (kind: Parameters<typeof smallVariantMarkPath>[0]) =>
      Math.max(
        ...(smallVariantMarkPath(kind, SMALL_VARIANT_DOT_RADIUS).match(/-?\d+(\.\d+)?/g) ?? []).map((n) =>
          Math.abs(Number(n)),
        ),
      );
    expect(reach('pathogenic')).toBeGreaterThan(reach('other'));
    // A row is a third of the chromosome view's 45 px origin track.
    expect(reach('pathogenic')).toBeLessThanOrEqual(45 / 3 / 2);
  });

  it('draw P/LP larger, and the more salient classes over the others', () => {
    const extent = (kind: Parameters<typeof smallVariantMarkExtent>[0]) => smallVariantMarkExtent(kind, 1.8);
    expect(extent('pathogenic')).toBeGreaterThan(extent('high'));
    expect(extent('high')).toBe(extent('other'));
    expect(markDrawRank('pathogenic')).toBeGreaterThan(markDrawRank('conflicting_pathogenic'));
    expect(markDrawRank('conflicting_pathogenic')).toBeGreaterThan(markDrawRank('high'));
    expect(markDrawRank('high')).toBeGreaterThan(markDrawRank('moderate'));
    expect(markDrawRank('moderate')).toBeGreaterThan(markDrawRank('benign'));
    expect(markDrawRank('benign')).toBeGreaterThan(markDrawRank('other'));
  });
});
