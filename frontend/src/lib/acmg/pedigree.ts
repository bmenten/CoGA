// The proband's parents for the de novo criteria (PS2 / PM6), read from the pedigree's
// parent-child links as the backend's de novo mode reads them
// (clickhouse_variant_queries._parent_child_links), and not from the member roles: a PED
// import stores everyone who has a child as 'father' or 'mother' (ped_service), so a lookup
// by role can find a grandparent and compare the proband with them.

import type { AcmgParentLink, AcmgParentageCheck } from './types';

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
 * The person each linked sample is a sample of, by the family's same-individual links: the
 * first sample ID (in sort order) of the samples the links join, directly or through another
 * sample. A sample without such a link is absent: it is a person of its own.
 */
export function personOfSamples(
  relationships?: readonly PedigreeRelationshipLike[] | null,
): Record<string, string> {
  const parent = new Map<string, string>();
  const root = (id: string): string => {
    let current = id;
    while (parent.get(current) !== undefined && parent.get(current) !== current) current = parent.get(current)!;
    return current;
  };
  for (const relationship of relationships ?? []) {
    if (relationship.relationship_type !== 'same_individual') continue;
    const { sample_id_a: sampleA, sample_id_b: sampleB } = relationship;
    if (!sampleA || !sampleB || sampleA === sampleB) continue;
    if (!parent.has(sampleA)) parent.set(sampleA, sampleA);
    if (!parent.has(sampleB)) parent.set(sampleB, sampleB);
    const [low, high] = [root(sampleA), root(sampleB)].sort();
    parent.set(high, low);
  }
  const personOf: Record<string, string> = {};
  for (const sampleId of parent.keys()) personOf[sampleId] = root(sampleId);
  return personOf;
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

// The slices of the Sample QC report read here (ApiSampleIntegrityQc's relatedness and
// Mendelian checks), so the engine stays free of the API types.
export interface RelatednessCheckLike {
  sample_a: string;
  sample_b: string;
  expected_relationship: string;
  inferred_relationship: string;
  status: string;
  message: string;
}

export interface MendelianCheckLike {
  child: string;
  parents: string[];
  status: string;
  message: string;
}

export interface SampleQcLike {
  relatedness_checks?: readonly RelatednessCheckLike[] | null;
  mendelian_checks?: readonly MendelianCheckLike[] | null;
}

/**
 * What the Sample QC says of each parent of a child, by the parent's sample id: confirmed where
 * the pair's parent-child check passes (KING kinship and IBS0 read as parent and child) and the
 * child's Mendelian check, where one ran with that parent, passes too; refuted where the pair's
 * check fails; unconfirmed otherwise (a warning or too few sites, or a Mendelian check that warns
 * or fails beside a passing pair). A parent the Sample QC checked no parent-child pair for is
 * absent.
 */
export function parentageFromSampleQc(
  childId: string,
  qc?: SampleQcLike | null,
): Record<string, AcmgParentageCheck> {
  const checks: Record<string, AcmgParentageCheck> = {};
  for (const pair of qc?.relatedness_checks ?? []) {
    if (pair.expected_relationship !== 'parent-child') continue;
    const parentId = pair.sample_a === childId ? pair.sample_b : pair.sample_b === childId ? pair.sample_a : null;
    if (!parentId) continue;
    if (pair.status === 'fail') {
      checks[parentId] = { status: 'refuted', message: pair.message };
      continue;
    }
    if (pair.status !== 'pass' || pair.inferred_relationship !== 'parent-child') {
      checks[parentId] = { status: 'unconfirmed', message: pair.message };
      continue;
    }
    // A Mendelian check that did not pass beside a passing pair: the QC contradicts itself.
    const mendelian = (qc?.mendelian_checks ?? []).find(
      (check) =>
        check.child === childId &&
        check.parents.includes(parentId) &&
        check.status !== 'pass' &&
        check.status !== 'skip',
    );
    checks[parentId] = mendelian
      ? { status: 'unconfirmed', message: `${pair.message} ${mendelian.message}` }
      : { status: 'confirmed', message: pair.message };
  }
  return checks;
}
