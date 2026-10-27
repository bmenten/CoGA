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
