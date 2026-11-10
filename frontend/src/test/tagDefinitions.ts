import type { SmallVariantTagDefinitionOut } from '../lib/apiSchema.generated';

type TagFields = Pick<
  SmallVariantTagDefinitionOut,
  'key' | 'label' | 'group' | 'color' | 'sort_order' | 'scope' | 'is_custom'
> &
  Partial<SmallVariantTagDefinitionOut>;

/**
 * A variant tag as the tag lists serve it (SmallVariantTagDefinitionOut), with the fields a
 * test leaves out at what the backend sends for a tag without them: no description, no
 * project, shared with none, active.
 */
export const tagDefinition = (fields: TagFields): SmallVariantTagDefinitionOut => ({
  description: null,
  project_id: null,
  shared_project_ids: [],
  is_active: true,
  ...fields,
});

/**
 * The built-in tags a small-variant review's class is read beside, as the tag list serves them
 * (small_variant_review_tags.py): the Report quick tag, the ACMG class tags, the VUS tiers and
 * *Secondary finding*, the last three in the classification group.
 */
export const BUILT_IN_REVIEW_TAGS: SmallVariantTagDefinitionOut[] = [
  ['report', 'Report', 'collaboration', 70],
  ['acmg_class_5', 'Pathogenic - class 5', 'classification', 110],
  ['acmg_class_4', 'Likely pathogenic - class 4', 'classification', 120],
  ['acmg_class_3', 'VUS - class 3', 'classification', 130],
  ['acmg_vus_hot', 'VUS — hot', 'classification', 131],
  ['acmg_vus_warm', 'VUS — warm', 'classification', 132],
  ['acmg_vus_cold', 'VUS — cold', 'classification', 133],
  ['acmg_class_2', 'Likely benign - class 2', 'classification', 140],
  ['acmg_class_1', 'Benign - class 1', 'classification', 150],
  ['secondary_finding', 'Secondary finding', 'classification', 160],
].map(([key, label, group, sortOrder]) =>
  tagDefinition({
    key: key as string,
    label: label as string,
    group: group as SmallVariantTagDefinitionOut['group'],
    color: '#336699',
    sort_order: sortOrder as number,
    scope: 'system',
    is_custom: false,
  }),
);
