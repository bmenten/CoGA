import React from 'react';
import type {
  CarrierStatus,
  CarrierType,
  ClinicalStatus,
  FamilyMembersDraft,
  StructureMemberDraft,
} from './familyDetailTypes';
import {
  CARRIER_STATUS_OPTIONS,
  CARRIER_TYPE_OPTIONS,
  CLINICAL_STATUS_OPTIONS,
  ROLE_OPTIONS,
  SEX_OPTIONS,
} from './familyDetailConstants';
import {
  bothParentsValue,
  findMember,
  labelFor,
  memberLabel,
  otherMembers,
  parentCandidates,
  partnerOf,
  parentOf,
  relativesOf,
  type ParentRole,
} from './familyMembersDraft';
import { sampleKey } from './familyDetailHelpers';

/**
 * The controls of a member's fields, the same in the Family members table, its add-member row
 * and the member dialog: each offers the same choices and reads and writes the same draft.
 */

export const SEX_LABELS: Record<StructureMemberDraft['sex'], string> = {
  male: 'male',
  female: 'female',
  und: 'unknown',
};
export const CLINICAL_STATUS_LABELS: Record<ClinicalStatus, string> = {
  unknown: 'unknown',
  unaffected: 'unaffected',
  affected: 'affected',
};
export const CARRIER_STATUS_LABELS: Record<CarrierStatus, string> = {
  unknown: 'carrier unknown',
  not_carrier: 'not a carrier',
  carrier: 'carrier',
};

interface SelectProps {
  id?: string;
  disabled?: boolean;
  'aria-label'?: string;
  title?: string;
}

export const RoleSelect: React.FC<
  SelectProps & { value: StructureMemberDraft['role']; onChange: (role: StructureMemberDraft['role']) => void }
> = ({ value, onChange, ...props }) => (
  <select {...props} value={value} onChange={(event) => onChange(event.target.value as StructureMemberDraft['role'])}>
    {ROLE_OPTIONS.map((role) => (
      <option key={role} value={role}>
        {role}
      </option>
    ))}
  </select>
);

export const SexSelect: React.FC<
  SelectProps & { value: StructureMemberDraft['sex']; onChange: (sex: StructureMemberDraft['sex']) => void }
> = ({ value, onChange, ...props }) => (
  <select {...props} value={value} onChange={(event) => onChange(event.target.value as StructureMemberDraft['sex'])}>
    {SEX_OPTIONS.map((sex) => (
      <option key={sex} value={sex}>
        {SEX_LABELS[sex]}
      </option>
    ))}
  </select>
);

export const ClinicalStatusSelect: React.FC<
  SelectProps & { value: ClinicalStatus; onChange: (status: ClinicalStatus) => void }
> = ({ value, onChange, ...props }) => (
  <select {...props} value={value} onChange={(event) => onChange(event.target.value as ClinicalStatus)}>
    {CLINICAL_STATUS_OPTIONS.map((status) => (
      <option key={status} value={status}>
        {CLINICAL_STATUS_LABELS[status]}
      </option>
    ))}
  </select>
);

export const CarrierStatusSelect: React.FC<
  SelectProps & { value: CarrierStatus; onChange: (status: CarrierStatus) => void }
> = ({ value, onChange, ...props }) => (
  <select {...props} value={value} onChange={(event) => onChange(event.target.value as CarrierStatus)}>
    {CARRIER_STATUS_OPTIONS.map((status) => (
      <option key={status} value={status}>
        {CARRIER_STATUS_LABELS[status]}
      </option>
    ))}
  </select>
);

export const CarrierTypeSelect: React.FC<
  SelectProps & { value: CarrierType | ''; onChange: (type: CarrierType | '') => void }
> = ({ value, onChange, ...props }) => (
  <select {...props} value={value} onChange={(event) => onChange(event.target.value as CarrierType | '')}>
    <option value="">—</option>
    {CARRIER_TYPE_OPTIONS.map((type) => (
      <option key={type} value={type}>
        {type}
      </option>
    ))}
  </select>
);

/** A chosen member no longer among the choices (removed, or of a sex that no longer allows it). */
const strandedLabel = (draft: FamilyMembersDraft, sampleId: string): string => {
  const member = findMember(draft, sampleId);
  if (!member) return `${sampleId} (not in the family)`;
  return `${memberLabel(member)} (${member.removed ? 'removed' : SEX_LABELS[member.sex]})`;
};

const MemberOptions: React.FC<{
  draft: FamilyMembersDraft;
  candidates: StructureMemberDraft[];
  value: string;
}> = ({ draft, candidates, value }) => (
  <>
    <option value="">—</option>
    {value && !candidates.some((member) => sampleKey(member.sample_id) === sampleKey(value)) && (
      <option value={value}>{strandedLabel(draft, value)}</option>
    )}
    {candidates.map((member) => (
      <option key={member.sample_id} value={member.sample_id}>
        {memberLabel(member)}
      </option>
    ))}
  </>
);

/** The member's father or mother; `childId` is empty for the member being added. */
export const ParentSelect: React.FC<
  SelectProps & {
    draft: FamilyMembersDraft;
    childId: string;
    role: ParentRole;
    /** For the member being added: its choice, outside the draft. */
    value?: string;
    onChange: (parentId: string) => void;
  }
> = ({ draft, childId, role, value, onChange, ...props }) => {
  const current = value ?? parentOf(draft, childId, role);
  return (
    <select {...props} value={current} onChange={(event) => onChange(event.target.value)}>
      <MemberOptions draft={draft} candidates={parentCandidates(draft, childId, role)} value={current} />
    </select>
  );
};

export const PartnerSelect: React.FC<
  SelectProps & {
    draft: FamilyMembersDraft;
    memberId: string;
    value?: string;
    onChange: (partnerId: string) => void;
  }
> = ({ draft, memberId, value, onChange, ...props }) => {
  const current = value ?? partnerOf(draft, memberId);
  return (
    <select {...props} value={current} onChange={(event) => onChange(event.target.value)}>
      <MemberOptions draft={draft} candidates={otherMembers(draft, memberId)} value={current} />
    </select>
  );
};

/**
 * Related to: the member(s) this one is related through by an unknown degree. One choice
 * offers the family's father and mother together; any other pair it holds is offered as it is.
 */
export const RelatedToSelect: React.FC<
  SelectProps & {
    draft: FamilyMembersDraft;
    memberId: string;
    value?: string;
    onChange: (value: string) => void;
  }
> = ({ draft, memberId, value, onChange, ...props }) => {
  const current = value ?? relativesOf(draft, memberId);
  const candidates = otherMembers(draft, memberId);
  const parents = bothParentsValue(draft);
  const isParent = Boolean(
    parents && memberId && parents.split(',').some((id) => sampleKey(id) === sampleKey(memberId)),
  );
  const several = current.includes(',');
  return (
    <select {...props} value={current} onChange={(event) => onChange(event.target.value)}>
      <option value="">—</option>
      {parents && !isParent && <option value={parents}>both parents</option>}
      {several && current !== parents && (
        <option value={current}>
          {current
            .split(',')
            .map((id) => labelFor(draft, id))
            .join(' and ')}
        </option>
      )}
      {current && !several && !candidates.some((member) => sampleKey(member.sample_id) === sampleKey(current)) && (
        <option value={current}>{strandedLabel(draft, current)}</option>
      )}
      {candidates.map((member) => (
        <option key={member.sample_id} value={member.sample_id}>
          {memberLabel(member)}
        </option>
      ))}
    </select>
  );
};

/** The draft's related-to value as read: "both parents", or the members' IDs. */
export const relatedToText = (draft: FamilyMembersDraft, memberId: string): string => {
  const value = relativesOf(draft, memberId);
  if (!value) return '';
  if (value === bothParentsValue(draft)) return 'both parents';
  return value
    .split(',')
    .map((id) => labelFor(draft, id))
    .join(', ');
};
