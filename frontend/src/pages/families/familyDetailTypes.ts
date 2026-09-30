export type ClinicalStatus = 'unknown' | 'unaffected' | 'affected';
export type CarrierStatus = 'unknown' | 'not_carrier' | 'carrier';
export type CarrierType = 'obligate' | 'proven' | 'reported' | 'inferred';
export type HpoAnnotationStatus = 'present' | 'absent' | 'unknown';

export interface StructureMemberDraft {
  sample_id: string;
  sex: 'male' | 'female' | 'und';
  role: 'proband' | 'father' | 'mother' | 'sibling' | 'embryo' | 'relative';
  clinical_status: ClinicalStatus;
  carrier_status: CarrierStatus;
  carrier_type: CarrierType | '';
  isNew?: boolean;
  removed?: boolean;
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

export interface MemberDetailDraft {
  sample_id: string;
  sex: StructureMemberDraft['sex'];
  role: StructureMemberDraft['role'];
  clinical_status: ClinicalStatus;
  carrier_status: CarrierStatus;
  carrier_type: CarrierType | '';
  father_id: string;
  mother_id: string;
}
