// The proband's parents for the de novo criteria (PS2 / PM6), read from the pedigree's
// parent-child links as the backend's de novo mode reads them
// (clickhouse_variant_queries._parent_child_links), and not from the member roles: a PED
// import stores everyone who has a child as 'father' or 'mother' (ped_service), so a lookup
// by role can find a grandparent and compare the proband with them.

import type { AcmgParentLink } from './types';

// The slice of a family relationship (ApiFamilyRelationship) read here, so the engine stays
// free of the API types.
export interface PedigreeRelationshipLike {
  relationship_type: string;
  sample_id_a: string;
  sample_id_b: string;
  role_a?: string | null;
  role_b?: string | null;
}

const isParentRole = (role: string): role is AcmgParentLink['role'] => role === 'father' || role === 'mother';

/**
 * The parent → child links among a family's relationships, the parent on either side. A link
 * whose parent is not named father or mother (a generic 'parent') is skipped, as in the
 * backend: the de novo rule for a son's X or Y needs to know which parent passes it on.
 */
export function parentLinksFromRelationships(
  relationships?: readonly PedigreeRelationshipLike[] | null,
): AcmgParentLink[] {
  const links: AcmgParentLink[] = [];
  for (const relationship of relationships ?? []) {
    if (relationship.relationship_type !== 'parent_child') continue;
    const { sample_id_a: sampleA, sample_id_b: sampleB } = relationship;
    if (!sampleA || !sampleB) continue;
    const roleA = (relationship.role_a ?? '').toLowerCase();
    const roleB = (relationship.role_b ?? '').toLowerCase();
    if (roleB === 'child' && isParentRole(roleA)) {
      links.push({ childId: sampleB, parentId: sampleA, role: roleA });
    } else if (roleA === 'child' && isParentRole(roleB)) {
      links.push({ childId: sampleA, parentId: sampleB, role: roleB });
    }
  }
  return links;
}

/**
 * The sample ids of a child's father and mother. A role that two different members hold for
 * the child names no one: which of them is the parent is unknown.
 */
export function parentsOf(
  childId: string,
  links: readonly AcmgParentLink[] = [],
): { father?: string; mother?: string } {
  const named = (role: AcmgParentLink['role']): string | undefined => {
    const parents = new Set(
      links.filter((link) => link.childId === childId && link.role === role).map((link) => link.parentId),
    );
    return parents.size === 1 ? [...parents][0] : undefined;
  };
  return { father: named('father'), mother: named('mother') };
}
