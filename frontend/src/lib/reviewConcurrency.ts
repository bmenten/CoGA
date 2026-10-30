/**
 * Optimistic concurrency for variant-review saves (#513).
 *
 * A save carries the review's `updated_at` as it was loaded (null when there was none).
 * If another reviewer has saved in between, the server refuses with 409
 * `{ code: 'review_conflict', message, current }` instead of overwriting their edit; the
 * page then shows the message and reloads the current review.
 */
export function withReviewVersion<T extends object>(
  payload: T,
  review?: { updated_at?: string | null } | null,
): T & { expected_updated_at: string | null } {
  return { ...payload, expected_updated_at: review?.updated_at ?? null };
}

/**
 * A compound-het pair save writes the partner's review too, so it carries the partner review's
 * version as well: `compound_het.partner_expected_updated_at`, null when the partner was loaded
 * without a review. A partner review saved in between gets the same 409 `review_conflict`,
 * naming the partner, and the page reloads. `partnerReview` undefined means the partner was not
 * loaded at all: no version is sent, and the server still reads the partner under its lock.
 */
export function withPartnerReviewVersion<T extends object>(
  payload: T,
  partnerReview: { updated_at?: string | null } | null | undefined,
): T {
  const pair = (payload as { compound_het?: { partner_variant_id?: string } | null }).compound_het;
  if (!pair?.partner_variant_id || partnerReview === undefined) {
    return payload;
  }
  return {
    ...payload,
    compound_het: { ...pair, partner_expected_updated_at: partnerReview?.updated_at ?? null },
  };
}

export function isReviewConflict(error: unknown): boolean {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response;
  const detail = response?.data?.detail as { code?: unknown } | undefined;
  return (
    response?.status === 409 &&
    Boolean(detail) &&
    typeof detail === 'object' &&
    detail?.code === 'review_conflict'
  );
}
