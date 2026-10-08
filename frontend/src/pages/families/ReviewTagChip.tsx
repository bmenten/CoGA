import type { SmallVariantReviewTagMetadata, SmallVariantTagDefinition } from './smallVariantSearch';
import { buildReviewTagTooltip, formatReviewTagLabel, getReviewTagStyle } from './smallVariantResultUtils';

/**
 * A review tag as a chip on a variant card or table row: in its definition's colour, a
 * deleted tag dashed and marked, and on hover who set it and when.
 */
const ReviewTagChip = ({
  className,
  tagKey,
  tagMap,
  tagMetadata,
}: {
  className: string;
  tagKey: string;
  tagMap?: Record<string, SmallVariantTagDefinition>;
  tagMetadata?: Record<string, SmallVariantReviewTagMetadata>;
}) => (
  <span
    className={className}
    style={getReviewTagStyle(tagKey, tagMap)}
    title={buildReviewTagTooltip({ tagKey, tagMap, tagMetadata })}
  >
    {formatReviewTagLabel(tagKey, tagMap)}
  </span>
);

export default ReviewTagChip;
