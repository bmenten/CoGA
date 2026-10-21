import { describe, expect, it } from 'vitest';

import { reviewTagLabel } from '../reviewTags';
import { svKind } from '../svKind';
import { svKind as trackSvKind } from '../../components/visualizations/svTypes';
import { cnvKindForType } from '../cnvAcmg/evaluate';

// One copy each of two rules the review pages, the small-variant track and the CNV classifier
// kept apart (ENG-7).
describe('a review tag’s label', () => {
  it('marks a deleted tag, and reads an unknown key as the key', () => {
    expect(reviewTagLabel({ label: 'Report', is_active: true }, 'report')).toBe('Report');
    expect(reviewTagLabel({ label: 'Old flag', is_active: false }, 'old')).toBe('Old flag (deleted)');
    expect(reviewTagLabel(undefined, 'unknown_key')).toBe('unknown_key');
    expect(reviewTagLabel({ label: '', is_active: false }, 'blank')).toBe('blank (deleted)');
  });
});

describe('an SV’s type without its subtypes', () => {
  it('is one rule for the SV tracks and the CNV classifier', () => {
    expect(trackSvKind).toBe(svKind);
    expect(svKind(' dup:tandem ')).toBe('DUP');
    expect(svKind(null)).toBe('');
    // The classifier reads a tandem duplication as a gain (CLIN-47), through the same rule.
    expect(cnvKindForType('DUP:TANDEM')).toBe('gain');
    expect(cnvKindForType('DEL:ME')).toBe('loss');
  });
});
