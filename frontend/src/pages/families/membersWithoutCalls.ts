import type { MemberWithoutCallsOut } from '../../lib/apiSchema.generated';
import { analysisLabel } from '../../lib/familyAnalyses';

/**
 * What the family's small-variant list says of the members without calls in the callsets it
 * reads (backend `family_members_without_calls`): a proband-only long-read callset whose package
 * lists the parents. An inheritance filter reads a call it does not find as no ALT allele, so it
 * cannot leave out a variant inherited from an unaffected member without calls, and keeps no
 * variant at all when an affected member has none. The owner kept that reading (2026-11-20); the
 * list says it: a warning while an inheritance filter that reads those members is applied, a
 * quieter mention otherwise.
 */
export type MembersWithoutCallsNote = { tone: 'warning' | 'quiet'; text: string };

const joinNames = (names: string[]): string =>
  names.length <= 1 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;

const memberName = (member: MemberWithoutCallsOut): string =>
  member.role ? `${member.sample_id} (${member.role})` : member.sample_id;

const capitalize = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);

// "The parents (MOTHER and FATHER1)" for a mother and a father, else each member by name and role.
const describeMembers = (members: MemberWithoutCallsOut[]): string => {
  const roles = members.map((member) => member.role).sort();
  if (members.length === 2 && roles[0] === 'father' && roles[1] === 'mother') {
    return `the parents (${joinNames(members.map((member) => member.sample_id))})`;
  }
  return joinNames(members.map(memberName));
};

const have = (members: MemberWithoutCallsOut[]): string => (members.length === 1 ? 'has' : 'have');

const unaffectedConsequence = (members: MemberWithoutCallsOut[]): string =>
  describeMembers(members).startsWith('the parents')
    ? 'the inheritance filter reads their missing calls as reference, so it cannot leave out variants inherited from them.'
    : 'the inheritance filter reads their missing calls as reference, so it cannot leave out variants they carry.';

/**
 * The note on the members without calls of the analysis the list reads (`analysis`, null for a
 * family read whole), or null when every member holds calls or the list did not read them.
 * `inheritanceApplied` is whether the search applies an inheritance mode.
 */
export const membersWithoutCallsNote = (
  members: MemberWithoutCallsOut[] | null | undefined,
  { analysis, inheritanceApplied }: { analysis: string | null; inheritanceApplied: boolean },
): MembersWithoutCallsNote | null => {
  if (!members?.length) return null;
  const label = analysisLabel(analysis);
  const where = label ? `in the ${label} analysis` : 'in the callsets this list reads';
  const unaffected = members.filter((member) => member.inheritance_reading === 'unaffected');
  const affected = members.filter((member) => member.inheritance_reading === 'affected');
  const unread = members.filter((member) => !member.inheritance_reading);
  if (!inheritanceApplied || (!unaffected.length && !affected.length)) {
    return {
      tone: 'quiet',
      text:
        `${capitalize(describeMembers(members))} ${have(members)} no calls ${where}. ` +
        'An inheritance or genotype filter reads a missing call as reference.',
    };
  }
  const sentences: string[] = [];
  if (unaffected.length) {
    sentences.push(
      `${capitalize(describeMembers(unaffected))} ${have(unaffected)} no calls ${where}: ${unaffectedConsequence(unaffected)}`,
    );
  }
  if (affected.length) {
    sentences.push(
      `${capitalize(describeMembers(affected))} ${have(affected)} no calls ${where}: the inheritance filter asks ` +
        'every affected member for an ALT call, so it keeps no variant.',
    );
  }
  if (unread.length) {
    sentences.push(
      `${capitalize(describeMembers(unread))} ${have(unread)} no calls either; the filter reads affected and ` +
        'unaffected members only.',
    );
  }
  return { tone: 'warning', text: sentences.join(' ') };
};
