// Optimistic concurrency for review saves (#513).

import { describe, expect, it } from 'vitest';

import { isReviewConflict, withPartnerReviewVersion, withReviewVersion } from '../reviewConcurrency';

describe('withReviewVersion', () => {
  it('sends the version the review was loaded at', () => {
    expect(withReviewVersion({ note: 'x' }, { updated_at: '2026-09-28T10:00:00.123456Z' })).toEqual({
      note: 'x',
      expected_updated_at: '2026-09-28T10:00:00.123456Z',
    });
  });

  it('sends null for a variant that had no review, so a racing first save is caught', () => {
    expect(withReviewVersion({ note: 'x' }, null)).toEqual({ note: 'x', expected_updated_at: null });
    expect(withReviewVersion({ note: 'x' }, undefined).expected_updated_at).toBeNull();
  });
});

describe('withPartnerReviewVersion', () => {
  const pair = { tags: [], compound_het: { partner_variant_id: '1-200-C-T', tags: [] } };

  it('sends the version the partner review was loaded at with a pair save', () => {
    expect(withPartnerReviewVersion(pair, { updated_at: '2026-09-28T10:03:00.123456Z' })).toEqual({
      tags: [],
      compound_het: {
        partner_variant_id: '1-200-C-T',
        tags: [],
        partner_expected_updated_at: '2026-09-28T10:03:00.123456Z',
      },
    });
  });

  it('sends null for a partner loaded without a review, so a review saved since is caught', () => {
    expect(withPartnerReviewVersion(pair, null)).toEqual({
      tags: [],
      compound_het: { partner_variant_id: '1-200-C-T', tags: [], partner_expected_updated_at: null },
    });
  });

  it('sends no partner version for a partner the page never loaded, or a save without a pair', () => {
    expect(withPartnerReviewVersion(pair, undefined)).toBe(pair);
    const unpaired = { tags: [], compound_het: { tags: [] } };
    expect(withPartnerReviewVersion(unpaired, { updated_at: '2026-09-28T10:03:00Z' })).toBe(unpaired);
    const single = { tags: ['report'] };
    expect(withPartnerReviewVersion(single, { updated_at: '2026-09-28T10:03:00Z' })).toBe(single);
  });
});

describe('isReviewConflict', () => {
  const conflict = { response: { status: 409, data: { detail: { code: 'review_conflict' } } } };

  it('recognises the review conflict', () => {
    expect(isReviewConflict(conflict)).toBe(true);
  });

  it("recognises a pair save's conflict on the partner's review", () => {
    const partnerConflict = {
      response: { status: 409, data: { detail: { code: 'review_conflict', variant_id: '1-200-C-T', current: null } } },
    };
    expect(isReviewConflict(partnerConflict)).toBe(true);
  });

  it('does not take other 409s or errors for it', () => {
    expect(isReviewConflict({ response: { status: 409, data: { detail: 'drift' } } })).toBe(false);
    expect(isReviewConflict({ response: { status: 500, data: { detail: { code: 'review_conflict' } } } })).toBe(false);
    expect(isReviewConflict(new Error('network'))).toBe(false);
    expect(isReviewConflict(undefined)).toBe(false);
  });
});
