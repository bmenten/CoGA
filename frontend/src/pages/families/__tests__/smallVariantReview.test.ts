// A compound-het pair save sends its partner's review version as the loaded lists hold it. The
// family's tag list is read with the inactive tags, so a review shows each tag it holds by its label.

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { reviewTagLabel } from '../../../lib/reviewTags';
import { tagDefinition } from '../../../test/tagDefinitions';
import { review, smallVariant, smallVariantGroup } from '../../../test/variants';
import { fetchFamilyReviewTags, findLoadedVariantReview, quickTagTogglePayload } from '../smallVariantReview';
import type { SmallVariantPage } from '../smallVariantSearch';
import reviewClassCases from './fixtures/review-class-cases.json';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: apiMock }));

describe('fetchFamilyReviewTags', () => {
  beforeEach(() => apiMock.get.mockReset());

  // The server lists, inactive, the tags of a deleted project, or of a project the family
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

// A quick tag toggle sends the classification text the review holds, never the class shown
// from the class tag: toggling *Report* on a review of text "VUS - class 3" beside the tag of
// class 4 keeps "VUS - class 3", so the clinical audit records no change of classification text
// no one made. Only the review dialog's class choice changes it.
describe('quickTagTogglePayload', () => {
  const stored = (classification: string | null, tags: string[], note: string | null = 'kept') =>
    review({ variant_id: '1-100-A-G', classification, tags, note });

  it('sends the classification text back as stored when a quick tag toggles', () => {
    expect(quickTagTogglePayload(stored('VUS - class 3', ['acmg_class_4']), 'report')).toEqual({
      classification: 'VUS - class 3',
      tags: ['acmg_class_4', 'report'],
      note: 'kept',
    });
    expect(quickTagTogglePayload(stored('VUS', ['acmg_class_4', 'report']), 'report')).toEqual({
      classification: 'VUS',
      tags: ['acmg_class_4'],
      note: 'kept',
    });
  });

  it('adds no text to a review classified by its class tag alone', () => {
    const payload = quickTagTogglePayload(stored(null, ['acmg_class_4'], null), 'excluded');
    expect(payload).toEqual({ classification: undefined, tags: ['acmg_class_4', 'excluded'], note: undefined });
  });

  it.each(reviewClassCases.cases)('keeps the stored text, whatever class it shows: $why', ({ classification, tags }) => {
    const payload = quickTagTogglePayload(stored(classification, tags), 'review');
    expect(payload.classification).toBe(classification || undefined);
    expect(payload.tags).toEqual([...new Set([...tags, 'review'])].sort((a, b) => a.localeCompare(b)));
  });

  it('toggles the tag off as well as on', () => {
    expect(quickTagTogglePayload(stored('Pathogenic - class 5', ['acmg_class_5', 'review']), 'review').tags).toEqual([
      'acmg_class_5',
    ]);
    expect(quickTagTogglePayload(null, 'report')).toEqual({ classification: undefined, tags: ['report'], note: undefined });
  });
});
