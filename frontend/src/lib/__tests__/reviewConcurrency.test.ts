// Optimistic concurrency for review saves (#513).

import { describe, expect, it } from 'vitest';

import { isReviewConflict, withReviewVersion } from '../reviewConcurrency';

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

describe('isReviewConflict', () => {
  const conflict = { response: { status: 409, data: { detail: { code: 'review_conflict' } } } };

  it('recognises the review conflict', () => {
    expect(isReviewConflict(conflict)).toBe(true);
  });

  it('does not take other 409s or errors for it', () => {
    expect(isReviewConflict({ response: { status: 409, data: { detail: 'drift' } } })).toBe(false);
    expect(isReviewConflict({ response: { status: 500, data: { detail: { code: 'review_conflict' } } } })).toBe(false);
    expect(isReviewConflict(new Error('network'))).toBe(false);
    expect(isReviewConflict(undefined)).toBe(false);
  });
});
