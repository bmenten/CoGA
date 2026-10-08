import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import {
  normalizeReviewClassification,
  type SmallVariant,
  type SmallVariantPage,
  type SmallVariantReview,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';

// Shared small-variant review helpers. The review endpoints are family +
// variant-id scoped, so both the small-variant page and the monogenic NIPT page
// (whose variants are the same underlying records) use these for optimistic
// cache updates.

/**
 * The family's review tags, deleted ones too (flagged inactive): a review that still holds a
 * deleted tag shows it, marked. Structural and small variants share one tag store, so every
 * family page reads this list.
 */
export const fetchFamilyReviewTags = async (
  familyId: string | undefined,
  projectId?: string,
): Promise<SmallVariantTagDefinition[]> => {
  const res = await api.get(apiPath`/families/${familyId}/small-variant-tags`, {
    params: { include_inactive: true, ...(projectId ? { project_id: projectId } : {}) },
  });
  return res.data as SmallVariantTagDefinition[];
};

/**
 * What a quick tag toggle on a variant card or table row saves: the review's tags with the
 * tag added or removed, its classification and note sent back so the save keeps them.
 */
export const quickTagTogglePayload = (
  review: SmallVariantReview | null | undefined,
  tagKey: string,
): SmallVariantReviewSavePayload => {
  const nextTags = new Set(review?.tags || []);
  if (nextTags.has(tagKey)) nextTags.delete(tagKey);
  else nextTags.add(tagKey);
  return {
    classification: normalizeReviewClassification(review?.classification, review?.tags) || undefined,
    tags: Array.from(nextTags).sort((left, right) => left.localeCompare(right)),
    note: review?.note || undefined,
  };
};

export const buildSmallVariantReviewPath = (familyId: string, variantId: string): string =>
  `/families/${encodeURIComponent(familyId)}/small-variants/${encodeURIComponent(variantId)}/review`;

export const hasReviewContent = (review: SmallVariantReview | null | undefined): boolean =>
  Boolean(review?.classification || review?.tags?.length || review?.note || review?.compound_het);

export const buildOptimisticReview = (
  variant: SmallVariant,
  payload: SmallVariantReviewSavePayload,
): SmallVariantReview | null => {
  const nextReview: SmallVariantReview = {
    variant_id: variant.review?.variant_id || variant._id,
    classification: payload.classification ?? null,
    tags: payload.tags,
    tag_metadata: variant.review?.tag_metadata || {},
    note: payload.note ?? null,
    updated_by: variant.review?.updated_by ?? null,
    // The version the save was made against, not a client clock: a following save sends
    // it back, and a made-up timestamp would read as someone else's edit (#513).
    updated_at: variant.review?.updated_at ?? null,
    compound_het: variant.review?.compound_het ?? null,
  };

  return hasReviewContent(nextReview) ? nextReview : null;
};

export const withUpdatedVariantReview = (
  variant: SmallVariant,
  variantId: string,
  review: SmallVariantReview | null,
): SmallVariant => {
  if (variant._id !== variantId) {
    return variant;
  }
  return { ...variant, review };
};

export const updateSmallVariantPageReview = (
  page: SmallVariantPage | undefined,
  variantId: string,
  review: SmallVariantReview | null,
): SmallVariantPage | undefined => {
  if (!page) {
    return page;
  }

  return {
    ...page,
    variants: page.variants.map((variant) =>
      withUpdatedVariantReview(variant, variantId, review),
    ),
    variant_groups: page.variant_groups?.map((group) => ({
      ...group,
      variants: group.variants.map((variant) =>
        withUpdatedVariantReview(variant, variantId, review),
      ),
    })),
  };
};
