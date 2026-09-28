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
