// A compound-het pair save sends its partner's review version as the loaded lists hold it. The
// family's tag list is read with the inactive tags, so a review shows each tag it holds by its label.

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { reviewTagLabel } from '../../../lib/reviewTags';
import { tagDefinition } from '../../../test/tagDefinitions';
import { review, smallVariant, smallVariantGroup } from '../../../test/variants';
import { fetchFamilyReviewTags, findLoadedVariantReview } from '../smallVariantReview';
import type { SmallVariantPage } from '../smallVariantSearch';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: apiMock }));

describe('fetchFamilyReviewTags', () => {
  beforeEach(() => apiMock.get.mockReset());

  // SAFE-37: the server lists, inactive, the tags of a deleted project, or of a project the family
  // left, that the family's reviews hold, only with the inactive tags. Each then reads by its label,
  // marked, never as a bare key, and is never offered as active.
  it('asks for the inactive tags too, so a held tag of a deleted project reads by its label', async () => {
    const held = tagDefinition({
      key: 'probe_old_project',
      label: 'Probe old project',
      group: 'custom',
      color: '#aa0022',
      sort_order: 500,
      scope: 'project',
      is_custom: true,
      is_active: false,
    });
    apiMock.get.mockResolvedValue({ data: [held] });

    const tags = await fetchFamilyReviewTags('FAM1', 'p1');

    expect(apiMock.get).toHaveBeenCalledWith('/families/FAM1/small-variant-tags', {
      params: { include_inactive: true, project_id: 'p1' },
    });
    await fetchFamilyReviewTags('FAM1');
    expect(apiMock.get).toHaveBeenLastCalledWith('/families/FAM1/small-variant-tags', {
      params: { include_inactive: true },
    });
    expect(reviewTagLabel(tags[0], tags[0].key)).toBe('Probe old project (deleted)');
  });
});

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
