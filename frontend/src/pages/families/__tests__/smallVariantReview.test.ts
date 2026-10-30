// A compound-het pair save sends its partner's review version as the loaded lists hold it.

import { describe, expect, it } from 'vitest';

import { review, smallVariant, smallVariantGroup } from '../../../test/variants';
import { findLoadedVariantReview } from '../smallVariantReview';
import type { SmallVariantPage } from '../smallVariantSearch';

const variant = (id: string, reviewed: boolean) =>
  smallVariant({
    _id: id,
    chr: '1',
    start: Number(id.split('-')[1]),
    end: Number(id.split('-')[1]),
    type: 'SNV',
    review: reviewed ? review({ variant_id: id, updated_at: '2026-09-28T10:03:00Z' }) : null,
  });

const page = (fields: Partial<SmallVariantPage>): SmallVariantPage => ({
  total: 0,
  total_is_estimated: false,
  count_limit: 0,
  variants: [],
  ...fields,
});

describe('findLoadedVariantReview', () => {
  it("finds a variant's review in a loaded list, its pairs included", () => {
    const listed = page({ variants: [variant('1-100-A-G', true)] });
    const paired = page({
      variant_groups: [smallVariantGroup({ group_key: 'g1', variants: [variant('1-200-C-T', true)] })],
    });
    expect(findLoadedVariantReview([undefined, listed], '1-100-A-G')?.updated_at).toBe('2026-09-28T10:03:00Z');
    expect(findLoadedVariantReview([listed, paired], '1-200-C-T')?.variant_id).toBe('1-200-C-T');
  });

  it('tells a variant loaded without a review (null) from one no list holds (undefined)', () => {
    const listed = page({ variants: [variant('1-100-A-G', false)] });
    expect(findLoadedVariantReview([listed], '1-100-A-G')).toBeNull();
    expect(findLoadedVariantReview([listed], '1-300-G-A')).toBeUndefined();
    expect(findLoadedVariantReview([], '1-100-A-G')).toBeUndefined();
  });
});
