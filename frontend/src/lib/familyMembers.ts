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
  sibling: 3,
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
