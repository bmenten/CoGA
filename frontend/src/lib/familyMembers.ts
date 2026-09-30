export interface FamilyMemberLike {
  sample_id: string;
  role?: string;
  affected?: boolean;
  sex?: string;
}

const ROLE_PRIORITY: Record<string, number> = {
  proband: 0,
  father: 1,
  mother: 2,
  relative: 3,
  sibling: 4,
  embryo: 5,
};

export const isProband = (member: Pick<FamilyMemberLike, 'role'>): boolean =>
  `${member.role || ''}`.toLowerCase() === 'proband';

export function sortFamilyMembersProbandFirst<T extends FamilyMemberLike>(members: T[]): T[] {
  return [...members].sort((left, right) => {
    const leftRole = `${left.role || ''}`.toLowerCase();
    const rightRole = `${right.role || ''}`.toLowerCase();
    const leftPriority = ROLE_PRIORITY[leftRole] ?? 99;
    const rightPriority = ROLE_PRIORITY[rightRole] ?? 99;

    if (leftPriority !== rightPriority) {
      return leftPriority - rightPriority;
    }

    const leftAffected = Boolean(left.affected);
    const rightAffected = Boolean(right.affected);
    if (leftAffected !== rightAffected) {
      return leftAffected ? -1 : 1;
    }

    return left.sample_id.localeCompare(right.sample_id, undefined, {
      numeric: true,
      sensitivity: 'base',
    });
  });
}

/** A member as the reports name one: "proband (S1)", or the sample id when the role is blank. */
export const memberLabel = (member: { sample_id: string; role?: string | null }): string => {
  const role = (member.role || '').trim();
  return role ? `${role} (${member.sample_id})` : member.sample_id;
};

/** A carrier: `carrier_status` is "carrier", or `true` as a boolean flag. */
export const isCarrierStatus = (status?: string | boolean | null): boolean =>
  status === true || status === 'carrier';

/**
 * An affected member: flagged affected, or with the clinical status "affected". The pedigree
 * also counts a PED row whose phenotype is 2; the haplotype risk model sees members only.
 */
export const isAffectedMember = (member: {
  affected?: boolean | null;
  clinical_status?: string | null;
}): boolean => Boolean(member.affected) || member.clinical_status === 'affected';
