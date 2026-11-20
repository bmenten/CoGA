import { describe, expect, it } from 'vitest';

import type { MemberWithoutCallsOut } from '../../../lib/apiSchema.generated';
import { membersWithoutCallsNote } from '../membersWithoutCalls';

const member = (
  sample_id: string,
  role: string | null,
  inheritance_reading: MemberWithoutCallsOut['inheritance_reading'],
): MemberWithoutCallsOut => ({ sample_id, role, inheritance_reading });

const PARENTS = [member('MOTHER', 'mother', 'unaffected'), member('FATHER1', 'father', 'unaffected')];

describe('membersWithoutCallsNote', () => {
  it('says nothing when every member holds calls, or the list did not read them', () => {
    expect(membersWithoutCallsNote([], { analysis: 'long_read_wgs', inheritanceApplied: true })).toBeNull();
    expect(membersWithoutCallsNote(null, { analysis: 'long_read_wgs', inheritanceApplied: true })).toBeNull();
    expect(membersWithoutCallsNote(undefined, { analysis: null, inheritanceApplied: false })).toBeNull();
  });

  // The owner's case (2026-11-20): long-read WGS of the proband alone, whose package lists the
  // parents. The de novo / dominant filter reads their missing calls as reference.
  it('warns that an applied inheritance filter cannot leave out variants inherited from parents without calls', () => {
    expect(membersWithoutCallsNote(PARENTS, { analysis: 'long_read_wgs', inheritanceApplied: true })).toEqual({
      tone: 'warning',
      text:
        'The parents (MOTHER and FATHER1) have no calls in the long-read WGS analysis: the inheritance filter ' +
        'reads their missing calls as reference, so it cannot leave out variants inherited from them.',
    });
  });

  it('mentions the members quietly while no inheritance filter is applied', () => {
    expect(membersWithoutCallsNote(PARENTS, { analysis: 'long_read_wgs', inheritanceApplied: false })).toEqual({
      tone: 'quiet',
      text:
        'The parents (MOTHER and FATHER1) have no calls in the long-read WGS analysis. An inheritance or ' +
        'genotype filter reads a missing call as reference.',
    });
  });

  it('names each member by role, and says an affected member without calls leaves the filter nothing', () => {
    const note = membersWithoutCallsNote(
      [
        member('MOTHER', 'mother', 'unaffected'),
        member('SIB1', 'sibling', 'unaffected'),
        member('PROBAND2', 'proband', 'affected'),
        member('EXTRA', null, null),
      ],
      { analysis: null, inheritanceApplied: true },
    );
    expect(note).toEqual({
      tone: 'warning',
      text:
        'MOTHER (mother) and SIB1 (sibling) have no calls in the callsets this list reads: the inheritance ' +
        'filter reads their missing calls as reference, so it cannot leave out variants they carry. ' +
        'PROBAND2 (proband) has no calls in the callsets this list reads: the inheritance filter asks every ' +
        'affected member for an ALT call, so it keeps no variant. EXTRA has no calls either; the filter reads ' +
        'affected and unaffected members only.',
    });
  });

  it('only mentions members an applied inheritance filter does not read', () => {
    expect(
      membersWithoutCallsNote([member('UNCLE', 'other', null)], { analysis: 'short_read_wgs', inheritanceApplied: true }),
    ).toEqual({
      tone: 'quiet',
      text:
        'UNCLE (other) has no calls in the short-read WGS analysis. An inheritance or genotype filter reads a ' +
        'missing call as reference.',
    });
  });
});
