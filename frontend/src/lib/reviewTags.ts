/**
 * A review tag's label as a review shows it: a deleted tag (`is_active` false) is marked
 * "(deleted)", since it can no longer be added; a key the tag list does not hold reads as the
 * key. One rule for the review pages (`tagDefinitionLabel`) and the small-variant track's
 * tooltip, which kept a copy (ENG-7).
 */
export const reviewTagLabel = (
  tag: { label?: string | null; is_active?: boolean | null } | undefined,
  key: string,
): string => {
  const label = tag?.label || key;
  return tag?.is_active === false ? `${label} (deleted)` : label;
};
