import { describe, expect, it } from 'vitest';

import type { ApiFamilyRecord } from '../../../lib/apiTypes';
import { defaultNewMember } from '../familyDetailConstants';
import {
  changedMemberCount,
  draftChanged,
  draftFromFamily,
  draftProblems,
  findMember,
  memberChanged,
  memberLabel,
  NO_NEW_MEMBER_RELATIONS,
  newMemberIdProblem,
  parentCandidates,
  partnerOf,
  relativesOf,
  structureUpdateFromDraft,
  withMemberFields,
  withMemberRemoved,
  withMemberRestored,
  withNewMember,
  withParent,
  withPartner,
  withRelatives,
} from '../familyMembersDraft';

const link = (type: string, a: string, b: string, roleA = 'partner') => ({
  id: `${type}-${a}-${b}`,
  relationship_type: type,
  sample_id_a: a,
  sample_id_b: b,
  role_a: roleA,
  role_b: type === 'parent_child' ? 'child' : roleA,
  metadata: {},
});

/** A trio and an index related through the mother, at structure version 3. */
const family = (): ApiFamilyRecord =>
  ({
    _id: 'fam1',
    family_id: 'F1',
    members: [
      { sample_id: 'EMB1', role: 'embryo', affected: false, sex: 'und', clinical_status: 'unknown', carrier_status: 'unknown' },
      { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female', clinical_status: 'unaffected', carrier_status: 'carrier', carrier_type: 'obligate', carrier_evidence: { derived_from: 'mother' } },
      { sample_id: 'FATHER1', role: 'father', affected: false, sex: 'male', clinical_status: 'unaffected', carrier_status: 'unknown' },
      { sample_id: 'INDEX1', role: 'relative', affected: true, sex: 'female', clinical_status: 'affected', carrier_status: 'unknown' },
    ],
    relationships: [
      link('parent_child', 'FATHER1', 'EMB1', 'father'),
      link('parent_child', 'MOTHER', 'EMB1', 'mother'),
      link('couple', 'FATHER1', 'MOTHER'),
      link('relative', 'MOTHER', 'INDEX1', 'relative'),
    ],
    structure_version: { version: 3 },
    projects: [],
    metadata: {},
  }) as unknown as ApiFamilyRecord;

describe('the family members draft', () => {
  it('reads the family as stored: proband first, with its links and version', () => {
    const draft = draftFromFamily(family());

    expect(draft.members.map((member) => member.sample_id)).toEqual(['FATHER1', 'MOTHER', 'INDEX1', 'EMB1']);
    expect(draft.baseVersion).toBe(3);
    expect(partnerOf(draft, 'MOTHER')).toBe('FATHER1');
    expect(relativesOf(draft, 'INDEX1')).toBe('MOTHER');
    expect(findMember(draft, 'mother')?.carrier_type).toBe('obligate');
  });

  it('saves nothing of an unchanged family but its members as they stand, and keeps the links', () => {
    const saved = draftFromFamily(family());
    const update = structureUpdateFromDraft(saved, saved);

    expect(draftChanged(saved, saved)).toBe(false);
    expect(update.expected_structure_version).toBe(3);
    expect(update.change_reason).toBe('family_members_edit');
    expect(update.relationships).toBeNull();
    expect(update.add_members).toEqual([]);
    expect(update.remove_members).toEqual([]);
    // The carrier type goes with the carrier; the import's carrier evidence is never sent.
    const mother = update.members?.find((member) => member.sample_id === 'MOTHER');
    expect(mother).toEqual({
      sample_id: 'MOTHER',
      sex: 'female',
      role: 'mother',
      clinical_status: 'unaffected',
      carrier_status: 'carrier',
      carrier_type: 'obligate',
    });
  });

  it('drops the carrier type of a member that is no carrier any more', () => {
    const saved = draftFromFamily(family());
    const draft = withMemberFields(saved, 'MOTHER', { carrier_status: 'not_carrier' });

    expect(findMember(draft, 'MOTHER')?.carrier_type).toBe('');
    expect(memberChanged(draft, saved, 'MOTHER')).toBe(true);
    expect(changedMemberCount(draft, saved)).toBe(1);
    expect(structureUpdateFromDraft(draft, saved).members?.find((m) => m.sample_id === 'MOTHER')?.carrier_type).toBeNull();
  });

  it('counts a change undone as none', () => {
    const saved = draftFromFamily(family());
    const draft = withMemberFields(withMemberFields(saved, 'EMB1', { sex: 'male' }), 'EMB1', { sex: 'und' });

    expect(draftChanged(draft, saved)).toBe(false);
  });

  it('sends every link once one changed, among the members still in the family', () => {
    const saved = draftFromFamily(family());
    const draft = withRelatives(saved, 'INDEX1', 'FATHER1,MOTHER');
    const update = structureUpdateFromDraft(draft, saved);

    expect(memberChanged(draft, saved, 'INDEX1')).toBe(true);
    // Its links are the father's and mother's too, but their own fields did not change.
    expect(memberChanged(draft, saved, 'EMB1')).toBe(false);
    expect(update.relationships?.relatives).toEqual([
      { member: 'INDEX1', related_to: 'FATHER1', metadata: {} },
      { member: 'INDEX1', related_to: 'MOTHER', metadata: {} },
    ]);
    expect(update.relationships?.parent_child).toHaveLength(2);
    expect(update.relationships?.couples).toEqual([{ partners: ['FATHER1', 'MOTHER'], context: null, metadata: {} }]);
  });

  it('offers as father or mother only the others of a sex that allows it', () => {
    const draft = draftFromFamily(family());

    expect(parentCandidates(draft, 'EMB1', 'father').map((member) => member.sample_id)).toEqual(['FATHER1']);
    expect(parentCandidates(draft, 'EMB1', 'mother').map((member) => member.sample_id)).toEqual(['MOTHER', 'INDEX1']);
    expect(parentCandidates(draft, 'MOTHER', 'mother').map((member) => member.sample_id)).toEqual(['INDEX1', 'EMB1']);
  });

  it('gives each member at most one partner', () => {
    const draft = withPartner(draftFromFamily(family()), 'INDEX1', 'FATHER1');

    expect(partnerOf(draft, 'INDEX1')).toBe('FATHER1');
    expect(partnerOf(draft, 'MOTHER')).toBe('');
    expect(withPartner(draft, 'INDEX1', '').couples).toEqual([]);
  });

  it('removes a stored member on save, and restores it with its links', () => {
    const saved = draftFromFamily(family());
    const draft = withMemberRemoved(saved, 'MOTHER');
    const update = structureUpdateFromDraft(draft, saved);

    expect(update.remove_members).toEqual(['MOTHER']);
    expect(update.members?.map((member) => member.sample_id)).not.toContain('MOTHER');
    // Its links leave the update; the server would refuse a link to a removed member.
    expect(update.relationships?.parent_child).toEqual([
      { parent: 'FATHER1', child: 'EMB1', parent_role: 'father', metadata: {} },
    ]);
    expect(update.relationships?.couples).toEqual([]);
    expect(update.relationships?.relatives).toEqual([]);

    const restored = withMemberRestored(draft, 'MOTHER');
    expect(draftChanged(restored, saved)).toBe(false);
  });

  it('adds a member with its links, and drops it again without a trace', () => {
    const saved = draftFromFamily(family());
    const draft = withNewMember(
      saved,
      { ...defaultNewMember, sample_id: ' EMB2 ', role: 'embryo', carrier_status: 'not_carrier', carrier_type: 'proven' },
      { ...NO_NEW_MEMBER_RELATIONS, father: 'FATHER1', mother: 'MOTHER' },
    );
    const update = structureUpdateFromDraft(draft, saved);

    expect(update.add_members).toEqual([
      { sample_id: 'EMB2', sex: 'und', role: 'embryo', clinical_status: 'unknown', carrier_status: 'not_carrier', carrier_type: null },
    ]);
    expect(update.relationships?.parent_child).toEqual(
      expect.arrayContaining([
        { parent: 'FATHER1', child: 'EMB2', parent_role: 'father', metadata: {} },
        { parent: 'MOTHER', child: 'EMB2', parent_role: 'mother', metadata: {} },
      ]),
    );
    expect(draftChanged(withMemberRemoved(draft, 'EMB2'), saved)).toBe(false);
  });

  it('refuses a new member under an ID a member has or gets', () => {
    const draft = withMemberFields(draftFromFamily(family()), 'EMB1', { new_sample_id: 'EMB9' });

    expect(newMemberIdProblem(draft, ' ')).toBe('Sample ID is required.');
    expect(newMemberIdProblem(draft, 'emb1')).toBe('Member emb1 already exists.');
    expect(newMemberIdProblem(draft, 'EMB9')).toBe('Member EMB9 already exists.');
    expect(newMemberIdProblem(draft, 'EMB2')).toBeNull();
  });

  it('renames a member by its stored ID, and names a clash or an empty ID', () => {
    const saved = draftFromFamily(family());
    const renamed = withMemberFields(saved, 'EMB1', { new_sample_id: ' EMB1B ' });

    expect(memberLabel(findMember(renamed, 'EMB1')!)).toBe('EMB1B');
    expect(structureUpdateFromDraft(renamed, saved).members?.find((m) => m.sample_id === 'EMB1')).toMatchObject({
      new_sample_id: 'EMB1B',
    });
    // A change of case alone is no rename, as on the server.
    expect(memberChanged(withMemberFields(saved, 'EMB1', { new_sample_id: 'emb1' }), saved, 'EMB1')).toBe(false);
    expect(draftProblems(withMemberFields(saved, 'EMB1', { new_sample_id: 'index1' }))).toEqual([
      'INDEX1 and EMB1 would both be index1.',
    ]);
    expect(draftProblems(withMemberFields(saved, 'EMB1', { new_sample_id: '  ' }))).toEqual(['EMB1 needs a sample ID.']);
  });

  it('records a parent by role, replacing the one there was', () => {
    const saved = draftFromFamily(family());
    const draft = withParent(saved, 'EMB1', 'mother', 'INDEX1');

    expect(draft.parentChild.filter((row) => row.child === 'EMB1').map((row) => [row.parent, row.parent_role])).toEqual([
      ['FATHER1', 'father'],
      ['INDEX1', 'mother'],
    ]);
    expect(withParent(draft, 'EMB1', 'mother', '').parentChild).toHaveLength(1);
  });
});
