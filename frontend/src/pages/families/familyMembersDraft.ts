/**
 * The family members editor's draft: the family's members and relationships as an
 * administrator changes them, in the Family members table or in a member's dialog, until one
 * save sends them as one structure update (`PUT /families/{id}/structure`). Every function here
 * returns a new draft and leaves its input as it was.
 *
 * The draft names each member by the ID it is stored under (`sample_id`); a rename is the
 * member's `new_sample_id`, which the save applies (the server renames it, then reads the rest
 * of the update under the new ID).
 */
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import type { FamilyStructureUpdate } from '../../lib/apiSchema.generated';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import type { FamilyMembersDraft, StructureMemberDraft } from './familyDetailTypes';
import {
  coupleDraftsFromRelationships,
  memberDraftFromFamilyMember,
  parentChildDraftsFromRelationships,
  relativeDraftsFromRelationships,
  sampleKey,
} from './familyDetailHelpers';

export type ParentRole = 'father' | 'mother';

/** What the save records as the reason of the change, on the structure version and audit event. */
export const FAMILY_MEMBERS_CHANGE_REASON = 'family_members_edit';

let draftLinkCounter = 0;
const nextLinkId = (prefix: string): string => {
  draftLinkCounter += 1;
  return `${prefix}-${draftLinkCounter}`;
};

const sameId = (left: string, right: string): boolean => sampleKey(left) === sampleKey(right);

/** The family as stored, as a draft: proband first, as the table reads it. */
export const draftFromFamily = (family: ApiFamilyRecord): FamilyMembersDraft => ({
  members: sortFamilyMembersProbandFirst(family.members ?? []).map(memberDraftFromFamilyMember),
  parentChild: parentChildDraftsFromRelationships(family),
  couples: coupleDraftsFromRelationships(family),
  relatives: relativeDraftsFromRelationships(family),
  baseVersion: family.structure_version?.version ?? null,
});

/** Whether the member gets another ID on save (a change of case alone is none, as on the server). */
export const isRenamed = (member: StructureMemberDraft): boolean =>
  !member.isNew && member.new_sample_id.trim() !== '' && !sameId(member.new_sample_id, member.sample_id);

/** The ID the member is shown under: the one it gets on save. */
export const memberLabel = (member: StructureMemberDraft): string =>
  isRenamed(member) ? member.new_sample_id.trim() : member.sample_id;

export const findMember = (
  draft: FamilyMembersDraft,
  sampleId: string,
): StructureMemberDraft | undefined => draft.members.find((member) => sameId(member.sample_id, sampleId));

export const activeMembers = (draft: FamilyMembersDraft): StructureMemberDraft[] =>
  draft.members.filter((member) => !member.removed);

/** The label of the member a draft ID names, or the ID itself. */
export const labelFor = (draft: FamilyMembersDraft, sampleId: string): string => {
  const member = findMember(draft, sampleId);
  return member ? memberLabel(member) : sampleId;
};

// --- Relationships, read --------------------------------------------------------------------

export const parentOf = (draft: FamilyMembersDraft, childId: string, role: ParentRole): string =>
  draft.parentChild.find((link) => sameId(link.child, childId) && link.parent_role === role)?.parent ?? '';

export const partnerOf = (draft: FamilyMembersDraft, memberId: string): string => {
  const couple = draft.couples.find(
    (link) => sameId(link.partnerA, memberId) || sameId(link.partnerB, memberId),
  );
  if (!couple) return '';
  return sameId(couple.partnerA, memberId) ? couple.partnerB : couple.partnerA;
};

/** The members this one is related through by an unknown degree, comma-joined and sorted. */
export const relativesOf = (draft: FamilyMembersDraft, memberId: string): string =>
  draft.relatives
    .filter((link) => sameId(link.member, memberId))
    .map((link) => link.relatedTo)
    .sort((left, right) => sampleKey(left).localeCompare(sampleKey(right)))
    .join(',');

/**
 * The value "both parents" stands for in a Related to choice: the family's father and mother,
 * when it has one of each, comma-joined as `relativesOf` joins them. Null otherwise.
 */
export const bothParentsValue = (draft: FamilyMembersDraft): string | null => {
  const holders = (['father', 'mother'] as const).map((role) => {
    const members = activeMembers(draft).filter((member) => member.role === role);
    return members.length === 1 ? members[0].sample_id : null;
  });
  if (!holders[0] || !holders[1]) return null;
  return [holders[0], holders[1]].sort((left, right) => sampleKey(left).localeCompare(sampleKey(right))).join(',');
};

/**
 * The members that may be recorded as the member's father or mother: the others still in the
 * family, of a sex that allows it (a father is not recorded as female, a mother not as male;
 * one of unknown sex may be either), as the server checks.
 */
export const parentCandidates = (
  draft: FamilyMembersDraft,
  childId: string,
  role: ParentRole,
): StructureMemberDraft[] =>
  activeMembers(draft).filter(
    (member) =>
      !sameId(member.sample_id, childId) && member.sex !== (role === 'father' ? 'female' : 'male'),
  );

/** The others still in the family: who may be the member's partner or a relative it goes through. */
export const otherMembers = (draft: FamilyMembersDraft, memberId: string): StructureMemberDraft[] =>
  activeMembers(draft).filter((member) => !sameId(member.sample_id, memberId));

// --- Changes --------------------------------------------------------------------------------

export const withMemberFields = (
  draft: FamilyMembersDraft,
  sampleId: string,
  updates: Partial<
    Pick<StructureMemberDraft, 'new_sample_id' | 'sex' | 'role' | 'clinical_status' | 'carrier_status' | 'carrier_type'>
  >,
): FamilyMembersDraft => ({
  ...draft,
  members: draft.members.map((member) => {
    if (!sameId(member.sample_id, sampleId)) return member;
    const next = { ...member, ...updates };
    // A carrier type belongs to a carrier only.
    if (next.carrier_status !== 'carrier') next.carrier_type = '';
    return next;
  }),
});

export const withParent = (
  draft: FamilyMembersDraft,
  childId: string,
  role: ParentRole,
  parentId: string,
): FamilyMembersDraft => {
  const kept = draft.parentChild.filter((link) => !(sameId(link.child, childId) && link.parent_role === role));
  return {
    ...draft,
    parentChild: parentId
      ? [...kept, { id: nextLinkId(`pc-${role}`), parent: parentId, child: childId, parent_role: role }]
      : kept,
  };
};

/** Each member has at most one partner: linking two unlinks either from anyone else. */
export const withPartner = (draft: FamilyMembersDraft, memberId: string, partnerId: string): FamilyMembersDraft => {
  const kept = draft.couples.filter(
    (link) =>
      ![link.partnerA, link.partnerB].some(
        (partner) => sameId(partner, memberId) || (partnerId !== '' && sameId(partner, partnerId)),
      ),
  );
  return {
    ...draft,
    couples: partnerId
      ? [...kept, { id: nextLinkId('couple'), partnerA: memberId, partnerB: partnerId, context: '' }]
      : kept,
  };
};

/** `value`: the members related through, comma-joined (one select offers both parents together). */
export const withRelatives = (draft: FamilyMembersDraft, memberId: string, value: string): FamilyMembersDraft => ({
  ...draft,
  relatives: [
    ...draft.relatives.filter((link) => !sameId(link.member, memberId)),
    ...value
      .split(',')
      .filter(Boolean)
      .map((relatedTo) => ({ id: nextLinkId('relative'), member: memberId, relatedTo })),
  ],
});

const withoutLinksOf = (draft: FamilyMembersDraft, sampleId: string): FamilyMembersDraft => ({
  ...draft,
  parentChild: draft.parentChild.filter((link) => !sameId(link.parent, sampleId) && !sameId(link.child, sampleId)),
  couples: draft.couples.filter((link) => !sameId(link.partnerA, sampleId) && !sameId(link.partnerB, sampleId)),
  relatives: draft.relatives.filter((link) => !sameId(link.member, sampleId) && !sameId(link.relatedTo, sampleId)),
});

/**
 * Take the member out of the family on save. A member added in this draft goes at once, with
 * its links. A stored member stays in the draft marked removed, its links kept so that
 * `withMemberRestored` brings it back as it was; the save leaves them out.
 */
export const withMemberRemoved = (draft: FamilyMembersDraft, sampleId: string): FamilyMembersDraft => {
  const member = findMember(draft, sampleId);
  if (!member) return draft;
  if (member.isNew) {
    return withoutLinksOf({ ...draft, members: draft.members.filter((row) => row !== member) }, sampleId);
  }
  return {
    ...draft,
    members: draft.members.map((row) =>
      row === member ? { ...row, removed: true, new_sample_id: row.sample_id } : row,
    ),
  };
};

export const withMemberRestored = (draft: FamilyMembersDraft, sampleId: string): FamilyMembersDraft => ({
  ...draft,
  members: draft.members.map((member) => (sameId(member.sample_id, sampleId) ? { ...member, removed: false } : member)),
});

/** Why `sampleId` cannot be the ID of a member added now, or null. */
export const newMemberIdProblem = (draft: FamilyMembersDraft, sampleId: string): string | null => {
  const id = sampleId.trim();
  if (!id) return 'Sample ID is required.';
  const taken = draft.members.some(
    (member) => sameId(member.sample_id, id) || (isRenamed(member) && sameId(member.new_sample_id, id)),
  );
  return taken ? `Member ${id} already exists.` : null;
};

export interface NewMemberRelations {
  father: string;
  mother: string;
  partner: string;
  /** Comma-joined, as `withRelatives` takes it. */
  related: string;
}

export const NO_NEW_MEMBER_RELATIONS: NewMemberRelations = { father: '', mother: '', partner: '', related: '' };

export const withNewMember = (
  draft: FamilyMembersDraft,
  member: StructureMemberDraft,
  relations: NewMemberRelations,
): FamilyMembersDraft => {
  const sampleId = member.sample_id.trim();
  let next: FamilyMembersDraft = {
    ...draft,
    members: [
      ...draft.members,
      {
        ...member,
        sample_id: sampleId,
        new_sample_id: sampleId,
        carrier_type: member.carrier_status === 'carrier' ? member.carrier_type : '',
        isNew: true,
        removed: false,
      },
    ],
  };
  if (relations.father) next = withParent(next, sampleId, 'father', relations.father);
  if (relations.mother) next = withParent(next, sampleId, 'mother', relations.mother);
  if (relations.partner) next = withPartner(next, sampleId, relations.partner);
  if (relations.related) next = withRelatives(next, sampleId, relations.related);
  return next;
};

// --- Comparing and saving -------------------------------------------------------------------

const memberFields = (member: StructureMemberDraft) => [
  member.sex,
  member.role,
  member.clinical_status,
  member.carrier_status,
  member.carrier_status === 'carrier' ? member.carrier_type : '',
];

interface LinkKey {
  key: string;
  /** The keys of the members the link names. */
  members: string[];
}

/** The draft's links among the members still in it, each with a sortable key. */
const draftLinks = (draft: FamilyMembersDraft): LinkKey[] => {
  const active = new Set(activeMembers(draft).map((member) => sampleKey(member.sample_id)));
  const kept = (...ids: string[]) => ids.every((id) => active.has(sampleKey(id)));
  const link = (key: string, ...ids: string[]): LinkKey => ({ key, members: ids.map(sampleKey) });
  return [
    ...draft.parentChild
      .filter((row) => kept(row.parent, row.child))
      .map((row) =>
        link(`pc|${sampleKey(row.parent)}|${sampleKey(row.child)}|${row.parent_role}`, row.parent, row.child),
      ),
    ...draft.couples
      .filter((row) => kept(row.partnerA, row.partnerB))
      .map((row) =>
        link(
          `couple|${[sampleKey(row.partnerA), sampleKey(row.partnerB)].sort().join('|')}|${row.context.trim()}`,
          row.partnerA,
          row.partnerB,
        ),
      ),
    ...draft.relatives
      .filter((row) => kept(row.member, row.relatedTo))
      .map((row) => link(`relative|${sampleKey(row.member)}|${sampleKey(row.relatedTo)}`, row.member, row.relatedTo)),
  ];
};

const sortedKeys = (links: LinkKey[]): string[] => links.map((row) => row.key).sort();

const sameKeys = (left: string[], right: string[]): boolean =>
  left.length === right.length && left.every((key, index) => key === right[index]);

const sameLinks = (left: FamilyMembersDraft, right: FamilyMembersDraft): boolean =>
  sameKeys(sortedKeys(draftLinks(left)), sortedKeys(draftLinks(right)));

/** The links that name the member, as sorted keys. */
const memberLinkKeys = (draft: FamilyMembersDraft, sampleId: string): string[] => {
  const key = sampleKey(sampleId);
  return sortedKeys(draftLinks(draft).filter((row) => row.members.includes(key)));
};

/**
 * Whether the save would change the member: its fields, its ID, its removal or its links. A
 * member added in the draft is changed by being added.
 */
export const memberChanged = (draft: FamilyMembersDraft, saved: FamilyMembersDraft, sampleId: string): boolean => {
  const member = findMember(draft, sampleId);
  const stored = findMember(saved, sampleId);
  if (!member) return false;
  if (!stored || member.isNew || member.removed || isRenamed(member)) return true;
  if (!sameKeys(memberFields(member), memberFields(stored))) return true;
  return !sameKeys(memberLinkKeys(draft, sampleId), memberLinkKeys(saved, sampleId));
};

/** How many members the save would change. */
export const changedMemberCount = (draft: FamilyMembersDraft, saved: FamilyMembersDraft): number =>
  draft.members.filter((member) => memberChanged(draft, saved, member.sample_id)).length;

/** Whether saving the draft would change anything. */
export const draftChanged = (draft: FamilyMembersDraft, saved: FamilyMembersDraft): boolean =>
  changedMemberCount(draft, saved) > 0 || !sameLinks(draft, saved);

/**
 * What stops the draft from being saved: a member whose ID was emptied, two members that would
 * share an ID. The server checks the rest (sexes of parents, cycles, one proband, IDs it can store).
 */
export const draftProblems = (draft: FamilyMembersDraft): string[] => {
  const problems: string[] = [];
  const seen = new Map<string, string>();
  for (const member of activeMembers(draft)) {
    if (!member.isNew && !member.new_sample_id.trim()) {
      problems.push(`${member.sample_id} needs a sample ID.`);
      continue;
    }
    const label = memberLabel(member);
    const key = sampleKey(label);
    const other = seen.get(key);
    if (other !== undefined) {
      problems.push(`${other} and ${member.sample_id} would both be ${label}.`);
    } else {
      seen.set(key, member.sample_id);
    }
  }
  return problems;
};

/** The stored members the save removes. */
export const removedMembers = (draft: FamilyMembersDraft): StructureMemberDraft[] =>
  draft.members.filter((member) => member.removed && !member.isNew);

const carrierTypeOf = (member: StructureMemberDraft) =>
  member.carrier_status === 'carrier' ? member.carrier_type || null : null;

/**
 * The draft as one structure update. Each member's fields go as they stand (the server writes
 * only those that changed). The import's carrier evidence is not sent, so it stays. The
 * relationships go only when one changed: a list replaces the family's links, and the server
 * keeps them as they are when the update names none.
 */
export const structureUpdateFromDraft = (
  draft: FamilyMembersDraft,
  saved: FamilyMembersDraft,
): FamilyStructureUpdate => {
  const active = activeMembers(draft);
  const activeIds = new Set(active.map((member) => sampleKey(member.sample_id)));
  const kept = (...ids: string[]) => ids.every((id) => activeIds.has(sampleKey(id)));
  return {
    expected_structure_version: draft.baseVersion,
    change_reason: FAMILY_MEMBERS_CHANGE_REASON,
    clear_existing_genomic_data: false,
    add_members: active
      .filter((member) => member.isNew)
      .map((member) => ({
        sample_id: member.sample_id,
        sex: member.sex,
        role: member.role,
        clinical_status: member.clinical_status,
        carrier_status: member.carrier_status,
        carrier_type: carrierTypeOf(member),
      })),
    members: active
      .filter((member) => !member.isNew)
      .map((member) => ({
        sample_id: member.sample_id,
        ...(isRenamed(member) ? { new_sample_id: member.new_sample_id.trim() } : {}),
        sex: member.sex,
        role: member.role,
        clinical_status: member.clinical_status,
        carrier_status: member.carrier_status,
        carrier_type: carrierTypeOf(member),
      })),
    remove_members: removedMembers(draft).map((member) => member.sample_id),
    relationships: sameLinks(draft, saved)
      ? null
      : {
          parent_child: draft.parentChild
            .filter((link) => kept(link.parent, link.child))
            .map((link) => ({ parent: link.parent, child: link.child, parent_role: link.parent_role, metadata: {} })),
          couples: draft.couples
            .filter((link) => kept(link.partnerA, link.partnerB))
            .map((link) => ({
              partners: [link.partnerA, link.partnerB],
              context: link.context.trim() || null,
              metadata: {},
            })),
          relatives: draft.relatives
            .filter((link) => kept(link.member, link.relatedTo))
            .map((link) => ({ member: link.member, related_to: link.relatedTo, metadata: {} })),
        },
  };
};
