export type ClinicalStatus = 'unknown' | 'unaffected' | 'affected';
export type CarrierStatus = 'unknown' | 'not_carrier' | 'carrier';
export type CarrierType = 'obligate' | 'proven' | 'reported' | 'inferred';
export type HpoAnnotationStatus = 'present' | 'absent' | 'unknown';

/**
 * A member as the family members editor holds it. `sample_id` is the ID it is stored under (a
 * new member's ID): the draft's relationships name it by that. `new_sample_id` is its ID as
 * the editor shows it, the stored one until an edit renames the member.
 */
export interface StructureMemberDraft {
  sample_id: string;
  new_sample_id: string;
  sex: 'male' | 'female' | 'und';
  role: 'proband' | 'father' | 'mother' | 'sibling' | 'embryo' | 'relative';
  clinical_status: ClinicalStatus;
  carrier_status: CarrierStatus;
  carrier_type: CarrierType | '';
  isNew: boolean;
  removed: boolean;
}

export interface ParentChildDraft {
  id: string;
  parent: string;
  child: string;
  parent_role: 'father' | 'mother' | 'parent';
}

export interface CoupleDraft {
  id: string;
  partnerA: string;
  partnerB: string;
  context: string;
}

/** A member related to the family through another member, by an unknown degree. */
export interface RelativeDraft {
  id: string;
  member: string;
  relatedTo: string;
}

/**
 * The family's members and relationships as the editor changes them, saved together in one
 * structure update. The table and the member dialog edit the same draft.
 */
export interface FamilyMembersDraft {
  members: StructureMemberDraft[];
  parentChild: ParentChildDraft[];
  couples: CoupleDraft[];
  relatives: RelativeDraft[];
  /** The structure version the draft started from: a save is refused once the family moved on. */
  baseVersion: number | null;
}
