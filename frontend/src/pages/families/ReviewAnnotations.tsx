import type { ReactNode } from 'react';
import { formatDate } from '../../lib/format';
import ReviewTagChip from './ReviewTagChip';
import type {
  SmallVariantReview,
  SmallVariantReviewTagMetadata,
  SmallVariantTagDefinition,
} from './smallVariantSearch';

/** What the block reads of a review; a repeat or Paraphase review (FindingReview) has it too. */
type AnnotatedReview = Pick<SmallVariantReview, 'tag_metadata' | 'note' | 'note_updated_by' | 'note_updated_at'>;

interface TagSave {
  by: string | null;
  at: string | null;
  tagKeys: string[];
}

/**
 * The tags in their order, those one analyst set on one day together, as their line names them.
 * A tag whose save is still on its way has no entry yet, and goes with the others that have none.
 */
const tagSaves = (tagKeys: string[], metadata: AnnotatedReview['tag_metadata']): TagSave[] => {
  const saves = new Map<string, TagSave>();
  for (const tagKey of tagKeys) {
    const entry: SmallVariantReviewTagMetadata | undefined = metadata[tagKey];
    const by = entry?.updated_by ?? null;
    const at = entry?.updated_at ?? null;
    const key = `${by}\u0000${formatDate(at)}`;
    const save = saves.get(key);
    if (save) save.tagKeys.push(tagKey);
    else saves.set(key, { by, at, tagKeys: [tagKey] });
  }
  return [...saves.values()];
};

/** Who wrote it and on which day, after what they wrote; nothing while its save is on its way. */
export const ReviewAuthorship = ({ by, at }: { by: string | null | undefined; at: string | null | undefined }) =>
  by ? (
    <span className="variant-card-review-author">
      {by} · {formatDate(at)}
    </span>
  ) : null;

/**
 * What the analysts wrote on a finding, at the top of its card under the heading: its review
 * tags, with who added them and on which day, and its note with who wrote it and on which day.
 * `leading` opens the tag row with what the card adds to the tags (a compound-het pair, a
 * repeat or Paraphase classification). Nothing to show, no block.
 */
const ReviewAnnotations = ({
  review,
  tagKeys,
  tagMap,
  leading,
}: {
  review: AnnotatedReview | null | undefined;
  /** The review's tags to show, in their order (`cardReviewTagKeys`, the class tags the heading
   *  shows left out). */
  tagKeys: string[];
  tagMap?: Record<string, SmallVariantTagDefinition>;
  leading?: ReactNode;
}) => {
  const saves = review ? tagSaves(tagKeys, review.tag_metadata) : [];
  if (!leading && !saves.length && !review?.note) return null;
  return (
    <div className="variant-card-review">
      {leading || saves.length ? (
        <div className="variant-card-chip-row variant-card-tag-row">
          {leading}
          {saves.map((save) => (
            <span key={save.tagKeys[0]} className="variant-card-review-tags">
              {save.tagKeys.map((tagKey) => (
                <ReviewTagChip
                  key={tagKey}
                  className="variant-card-chip variant-card-chip--tag"
                  tagKey={tagKey}
                  tagMap={tagMap}
                  tagMetadata={review?.tag_metadata}
                />
              ))}
              <ReviewAuthorship by={save.by} at={save.at} />
            </span>
          ))}
        </div>
      ) : null}
      {review?.note ? (
        <p className="variant-card-review-note">
          {review.note} <ReviewAuthorship by={review.note_updated_by} at={review.note_updated_at} />
        </p>
      ) : null}
    </div>
  );
};

export default ReviewAnnotations;
