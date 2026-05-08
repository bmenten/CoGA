import { describe, expect, it } from 'vitest';
import { sortFamilyMembersProbandFirst } from '../familyMembers';

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
