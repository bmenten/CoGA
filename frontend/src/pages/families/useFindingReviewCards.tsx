import React, { useMemo, useState } from 'react';
import { useQuery, type QueryKey } from '@tanstack/react-query';
import InfoTip from '../../components/InfoTip';
import { READ_ONLY_HINT } from '../../lib/familyAccess';
import ExcludedCardBar, { useUnfoldedExcludedCards } from './ExcludedCardBar';
import FindingReviewDialog from './FindingReviewDialog';
import ReviewTagChip from './ReviewTagChip';
import { fetchFamilyReviewTags } from './smallVariantReview';
import { sortReviewTagKeys, visibleReviewTagKeys } from './smallVariantResultUtils';
import { COLLABORATION_QUICK_TAGS, getTagDefinitionMap, type SmallVariantTagDefinition } from './smallVariantSearch';
import {
  FINDING_DRIFT_LABEL,
  differsFromStatus,
  findingClassificationLabel,
  findingQuickTagPayload,
  useFindingReviewSave,
  type FindingKind,
  type FindingReview,
} from './findingReview';

const QUICK_TAGS = [
  { key: COLLABORATION_QUICK_TAGS.review, label: 'Review' },
  { key: COLLABORATION_QUICK_TAGS.excluded, label: 'Exclude' },
  { key: COLLABORATION_QUICK_TAGS.report, label: 'Report' },
];

/** One finding's review parts, as FindingCard takes them. */
export type FindingReviewCardParts = {
  subhead: React.ReactNode;
  note: React.ReactNode;
  actions: React.ReactNode;
  excludedBar: React.ReactNode;
  folded: boolean;
  excluded: boolean;
};

/**
 * The review on a repeat locus card and a Paraphase region card, the same on both pages and as
 * on the mtDNA cards: the quick tags Review, Exclude and Report and "Tags & notes" in the
 * footer; under the heading the analyst's classification beside CoGA's status (marked when it
 * agrees with none of the calls, H18), whether the calls changed since the review, and the
 * tags; the note under the columns; an excluded finding folded to its heading (B3).
 */
export function useFindingReviewCards<T>({
  kind,
  familyId,
  projectId,
  queryKey,
  write,
  readOnly,
}: {
  kind: FindingKind;
  familyId: string | undefined;
  projectId: string | null | undefined;
  queryKey: QueryKey;
  write: (table: T, key: string, review: FindingReview | null) => T;
  readOnly: boolean;
}) {
  const { data: tags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', projectId || null],
    enabled: Boolean(familyId),
    queryFn: () => fetchFamilyReviewTags(familyId, projectId || undefined),
  });
  const tagMap = useMemo(() => getTagDefinitionMap(tags), [tags]);
  const excludedCards = useUnfoldedExcludedCards();
  const { save, pending, error, setError } = useFindingReviewSave<T>({ kind, familyId, projectId, queryKey, write });
  const [editing, setEditing] = useState<{ key: string; title: string; review: FindingReview | null } | null>(null);

  const parts = (
    key: string,
    review: FindingReview | null | undefined,
    { subject, statuses }: { subject: string; statuses: (string | null | undefined)[] },
  ): FindingReviewCardParts => {
    const tagKeys = sortReviewTagKeys(review?.tags ?? [], tagMap);
    const visibleTags = visibleReviewTagKeys(tagKeys);
    const excluded = tagKeys.includes(COLLABORATION_QUICK_TAGS.excluded);
    const classification = findingClassificationLabel(kind, review?.classification);
    const drift = review?.drift && review.drift.status !== 'unchanged' ? review.drift : null;
    const toggle = (tagKey: string) => {
      void save(key, review, findingQuickTagPayload(review, tagKey));
    };
    const subhead =
      classification || drift || visibleTags.length ? (
        <div className="variant-card-chip-row variant-card-tag-row">
          {classification ? (
            <span className="variant-card-chip variant-card-chip--strong finding-review-classification">
              Review: {classification}
            </span>
          ) : null}
          {classification && differsFromStatus(kind, review?.classification, statuses) ? (
            <span className="variant-card-chip variant-card-chip--review">Differs from CoGA&apos;s status</span>
          ) : null}
          {drift ? (
            <InfoTip
              staticChild
              className="variant-card-chip variant-card-chip--warning"
              label={
                drift.changes.length
                  ? drift.changes.join(' · ')
                  : 'The calls this review rests on cannot be compared with the calls now.'
              }
            >
              {FINDING_DRIFT_LABEL[drift.status] ?? 'Changed since review'}
            </InfoTip>
          ) : null}
          {visibleTags.map((tagKey) => (
            <ReviewTagChip
              key={tagKey}
              className="variant-card-chip variant-card-chip--tag"
              tagKey={tagKey}
              tagMap={tagMap}
              tagMetadata={review?.tag_metadata}
            />
          ))}
        </div>
      ) : null;
    return {
      excluded,
      folded: excluded && !excludedCards.isUnfolded(key),
      excludedBar: excluded ? (
        <ExcludedCardBar
          subject={subject}
          expanded={excludedCards.isUnfolded(key)}
          onToggleExpanded={() => excludedCards.toggle(key)}
          onUndoExclude={() => toggle(COLLABORATION_QUICK_TAGS.excluded)}
          readOnly={readOnly}
          pending={pending}
        />
      ) : null,
      subhead,
      note: review?.note ? <p className="variant-card-review-note">{review.note}</p> : null,
      actions: (
        <>
          {QUICK_TAGS.map((quickTag) => {
            const active = tagKeys.includes(quickTag.key);
            return (
              <button
                key={quickTag.key}
                type="button"
                className={`variant-quick-toggle${active ? ' variant-quick-toggle--active' : ''}`}
                aria-pressed={active}
                disabled={readOnly || pending}
                title={readOnly ? READ_ONLY_HINT : undefined}
                onClick={() => toggle(quickTag.key)}
              >
                {quickTag.label}
              </button>
            );
          })}
          <button
            type="button"
            className="variant-review-link"
            onClick={() => {
              setError(null);
              setEditing({ key, title: subject, review: review ?? null });
            }}
          >
            Tags &amp; notes
          </button>
        </>
      ),
    };
  };

  // A quick tag that could not be saved says so above the list; the dialog shows its own.
  const errorBanner =
    error && !editing ? (
      <p className="status-note status-note--error" role="alert">
        {error}
      </p>
    ) : null;

  const dialog = editing ? (
    <FindingReviewDialog
      kind={kind}
      title={editing.title}
      review={editing.review}
      tags={tags}
      isPending={pending}
      errorMessage={error}
      onClose={() => {
        setEditing(null);
        setError(null);
      }}
      onSave={(payload) => save(editing.key, editing.review, payload)}
    />
  ) : null;

  return { parts, errorBanner, dialog };
}
