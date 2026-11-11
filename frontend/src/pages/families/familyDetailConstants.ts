import type {
  CarrierStatus,
  CarrierType,
  ClinicalStatus,
  HpoAnnotationStatus,
  StructureMemberDraft,
} from './familyDetailTypes';

export const ROLE_OPTIONS: StructureMemberDraft['role'][] = [
  'proband',
  'father',
  'mother',
  'sibling',
  'embryo',
  'relative',
];
export const SEX_OPTIONS: StructureMemberDraft['sex'][] = ['male', 'female', 'und'];
export const CLINICAL_STATUS_OPTIONS: ClinicalStatus[] = ['unknown', 'unaffected', 'affected'];
export const CARRIER_STATUS_OPTIONS: CarrierStatus[] = ['unknown', 'not_carrier', 'carrier'];
export const CARRIER_TYPE_OPTIONS: CarrierType[] = ['proven', 'obligate', 'reported', 'inferred'];
export const HPO_STATUS_OPTIONS: HpoAnnotationStatus[] = ['present', 'absent', 'unknown'];

export const defaultNewMember: StructureMemberDraft = {
  sample_id: '',
  new_sample_id: '',
  sex: 'und',
  role: 'relative',
  clinical_status: 'unknown',
  carrier_status: 'unknown',
  carrier_type: '',
  isNew: true,
  removed: false,
};
