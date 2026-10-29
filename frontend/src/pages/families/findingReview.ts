import { useCallback, useState } from 'react';
import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import { isReviewConflict, withReviewVersion } from '../../lib/reviewConcurrency';
import type { FindingReviewOut } from '../../lib/apiSchema.generated';

/**
 * The analyst's review of a repeat locus and of a Paraphase region (docs/repeat-paraphase-
 * review-design.md): tags, a note and a classification on the type's own scale (B4), beside
 * CoGA's automatic status, which it never changes. One per family and assembly per locus or
 * region; the backend freezes the calls a classification or the Report tag rests on and says
 * whether they still are the family's (`drift`).
 */

export type FindingKind = 'repeat' | 'paraphase';
export type FindingReview = FindingReviewOut;
export type FindingReviewPayload = {
  classification: string | null;
  tags: string[];
  note: string | null;
};

export type ScaleOption = { value: string; label: string };

// The scales, in the order the dialog offers them; the keys are what the backend stores.
export const REPEAT_REVIEW_SCALE: ScaleOption[] = [
  { value: 'pathogenic_expansion', label: 'Pathogenic expansion' },
  { value: 'reduced_penetrance', label: 'Reduced penetrance' },
  { value: 'premutation', label: 'Premutation' },
  { value: 'intermediate', label: 'Intermediate (grey zone)' },
  { value: 'normal', label: 'Normal' },
  { value: 'uncertain', label: 'Uncertain significance' },
];
export const PARAPHASE_REVIEW_SCALE: ScaleOption[] = [
  { value: 'affected', label: 'Affected' },
  { value: 'carrier', label: 'Carrier' },
  { value: 'risk_allele', label: 'Risk allele' },
  { value: 'normal', label: 'Normal' },
  { value: 'uncertain', label: 'Uncertain significance' },
];

export const reviewScale = (kind: FindingKind): ScaleOption[] =>
  kind === 'repeat' ? REPEAT_REVIEW_SCALE : PARAPHASE_REVIEW_SCALE;

/** The label of a stored classification; a value no scale holds is shown as stored. */
export const findingClassificationLabel = (kind: FindingKind, value: string | null | undefined): string | null =>
  value ? (reviewScale(kind).find((option) => option.value === value)?.label ?? value) : null;

// The automatic statuses each classification agrees with: an analyst's classification that
// agrees with none of the family's calls is marked as differing from CoGA's status (H18), never
// reconciled. "Uncertain" and a risk allele agree with any status.
const AGREES_WITH: Record<FindingKind, Record<string, string[] | null>> = {
  repeat: {
    pathogenic_expansion: ['pathogenic'],
    reduced_penetrance: ['pathogenic', 'intermediate', 'review', 'not_assessed'],
    premutation: ['premutation'],
    intermediate: ['intermediate'],
    normal: ['normal'],
    uncertain: null,
  },
  paraphase: {
    affected: ['pathogenic'],
    carrier: ['carrier'],
    risk_allele: null,
    normal: ['normal'],
    uncertain: null,
  },
};

/** Whether the analyst's classification agrees with none of the calls' automatic statuses. */
export const differsFromStatus = (
  kind: FindingKind,
  classification: string | null | undefined,
  statuses: (string | null | undefined)[],
): boolean => {
  if (!classification) return false;
  const agrees = AGREES_WITH[kind][classification];
  if (agrees === undefined || agrees === null) return false;
  const known = statuses.filter((status): status is string => Boolean(status) && status !== 'none');
  return known.length > 0 && !known.some((status) => agrees.includes(status));
};

// What the drift of a review says, in words, for the chip on the card and the report.
export const FINDING_DRIFT_LABEL: Record<string, string> = {
  changed: 'Changed since review',
  not_found: 'No call since review',
  unverifiable: 'Calls could not be read',
  no_snapshot: 'Calls not frozen at review',
};

export const findingReviewPath = (familyId: string, kind: FindingKind, key: string): string =>
  kind === 'repeat'
    ? apiPath`/families/${familyId}/repeat-expansions/${key}/review`
    : apiPath`/families/${familyId}/paraphase/${key}/review`;

/** What a quick tag toggle saves: the review's tags with the tag added or removed, its
 * classification and note sent back so the save keeps them. */
export const findingQuickTagPayload = (
  review: FindingReview | null | undefined,
  tagKey: string,
): FindingReviewPayload => {
  const next = new Set(review?.tags ?? []);
  if (next.has(tagKey)) next.delete(tagKey);
  else next.add(tagKey);
  return {
    classification: review?.classification ?? null,
    tags: Array.from(next).sort((left, right) => left.localeCompare(right)),
    note: review?.note ?? null,
  };
};

export const hasFindingReviewContent = (review: FindingReview | null | undefined): boolean =>
  Boolean(review && (review.classification || review.tags.length || (review.note ?? '').trim()));

/**
 * Saving a review from a card or the dialog. The save shows on the card at once (written into
 * the page's cached table through `write`), the server's answer, which carries the drift,
 * replaces it, and a failed save puts the card back. The table itself is not read again: no
 * filter of the repeat or Paraphase page reads the reviews. A review someone else saved
 * meanwhile (409) reloads the table, so the current one shows.
 */
export function useFindingReviewSave<T>({
  kind,
  familyId,
  projectId,
  queryKey,
  write,
}: {
  kind: FindingKind;
  familyId: string | undefined;
  projectId: string | null | undefined;
  queryKey: QueryKey;
  write: (table: T, key: string, review: FindingReview | null) => T;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const setCached = useCallback(
    (key: string, review: FindingReview | null) =>
      queryClient.setQueryData<T>(queryKey, (current) => (current ? write(current, key, review) : current)),
    [queryClient, queryKey, write],
  );
  const mutation = useMutation({
    mutationFn: async ({
      key,
      review,
      payload,
    }: {
      key: string;
      review: FindingReview | null | undefined;
      payload: FindingReviewPayload;
    }) => {
      if (!familyId) throw new Error('Family id is required');
      const body = withReviewVersion(payload, review);
      const path = findingReviewPath(familyId, kind, key);
      const res = projectId
        ? await api.put(path, body, { params: { project_id: projectId } })
        : await api.put(path, body);
      return res.data as FindingReview;
    },
    onMutate: async ({ key, review, payload }) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<T>(queryKey);
      const optimistic: FindingReview = {
        key,
        tag_metadata: review?.tag_metadata ?? {},
        updated_by: review?.updated_by ?? null,
        updated_at: review?.updated_at ?? null,
        evidence_captured_at: review?.evidence_captured_at ?? null,
        drift: review?.drift ?? null,
        classification: payload.classification,
        tags: payload.tags,
        note: payload.note,
      };
      setCached(key, hasFindingReviewContent(optimistic) ? optimistic : null);
      return { previous };
    },
    onSuccess: (saved, { key }) => {
      setCached(key, hasFindingReviewContent(saved) ? saved : null);
    },
    onError: (failure, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
      if (isReviewConflict(failure)) void queryClient.invalidateQueries({ queryKey });
    },
  });
  const save = useCallback(
    async (key: string, review: FindingReview | null | undefined, payload: FindingReviewPayload) => {
      setError(null);
      try {
        await mutation.mutateAsync({ key, review, payload });
        return true;
      } catch (failure) {
        setError(getErrorMessage(failure, 'Unable to save the review.'));
        return false;
      }
    },
    [mutation],
  );
  return { save, pending: mutation.isPending, error, setError };
}
