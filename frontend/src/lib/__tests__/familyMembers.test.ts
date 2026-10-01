import { describe, expect, it } from 'vitest';
import {
  isAffectedMember,
  isCarrierStatus,
  memberLabel,
  sortFamilyMembersProbandFirst,
} from '../familyMembers';

describe('memberLabel', () => {
  it('names a member by role and sample, or by sample alone without a role', () => {
    expect(memberLabel({ sample_id: 'S1', role: 'proband' })).toBe('proband (S1)');
    expect(memberLabel({ sample_id: 'S2', role: '  ' })).toBe('S2');
    expect(memberLabel({ sample_id: 'S3', role: null })).toBe('S3');
  });
});

describe('isCarrierStatus', () => {
  it('is true for a carrier only', () => {
    expect(isCarrierStatus('carrier')).toBe(true);
    expect(isCarrierStatus(true)).toBe(true);
    expect(isCarrierStatus('not_carrier')).toBe(false);
    expect(isCarrierStatus('unknown')).toBe(false);
    expect(isCarrierStatus(false)).toBe(false);
    expect(isCarrierStatus(null)).toBe(false);
  });
});

describe('isAffectedMember', () => {
  it('reads the affected flag or the clinical status', () => {
    expect(isAffectedMember({ affected: true })).toBe(true);
    expect(isAffectedMember({ affected: false, clinical_status: 'affected' })).toBe(true);
    expect(isAffectedMember({ affected: false, clinical_status: 'unaffected' })).toBe(false);
    expect(isAffectedMember({ affected: null })).toBe(false);
    expect(isAffectedMember({})).toBe(false);
  });
});

describe('sortFamilyMembersProbandFirst', () => {
  it('keeps the proband first and then orders close relatives predictably', () => {
    const ordered = sortFamilyMembersProbandFirst([
      { sample_id: 'S3', role: 'sibling', affected: false },
      { sample_id: 'S2', role: 'mother', affected: false },
      { sample_id: 'S4', role: 'father', affected: false },
      { sample_id: 'S1', role: 'proband', affected: true },
    ]);

    expect(ordered.map((member) => member.sample_id)).toEqual(['S1', 'S4', 'S2', 'S3']);
  });
});
