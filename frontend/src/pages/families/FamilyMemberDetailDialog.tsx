import React, { useId, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import ModalDialog from '../../components/ModalDialog';
import type { ApiFamilyRecord, ApiFamilyMemberDetail, ApiHpoTerm } from '../../lib/apiTypes';
import { getErrorMessage } from '../../lib/errorMessage';
import type { FamilyMembersDraft, HpoAnnotationStatus } from './familyDetailTypes';
import { HPO_STATUS_OPTIONS } from './familyDetailConstants';
import { formatHpoTermOption, hpoTooltip } from './familyDetailHelpers';
import {
  activeMembers,
  findMember,
  memberLabel,
  withMemberFields,
  withMemberRemoved,
  withMemberRestored,
  withParent,
  withPartner,
  withRelatives,
} from './familyMembersDraft';
import {
  CarrierStatusSelect,
  CarrierTypeSelect,
  ClinicalStatusSelect,
  ParentSelect,
  PartnerSelect,
  RelatedToSelect,
  RoleSelect,
  SexSelect,
} from './FamilyMemberFields';
import { apiPath } from '../../lib/apiPath';
import QueryFailure from '../../components/QueryFailure';

/** A phenotype of the member being changed: its status and note as the analyst edits them. */
interface HpoAnnotationEdit {
  id: string;
  hpoId: string;
  label: string;
  status: HpoAnnotationStatus;
  note: string;
  // What it held when the edit began, to tell whether anything changed.
  savedStatus: HpoAnnotationStatus;
  savedNote: string;
}

interface FamilyMemberDetailDialogProps {
  familyId: string;
  family: ApiFamilyRecord;
  /** The member's stored ID. */
  memberId: string;
  /** The family members as the table shows them: the edit, or the family as stored. */
  draft: FamilyMembersDraft;
  /** Whether the family members are being edited (Save changes pending). */
  editing: boolean;
  /** May change the member's ID, pedigree and status, and remove it: an administrator. */
  canEditMember: boolean;
  /**
   * May add, change and remove the member's phenotypes: an analyst of the family's project or
   * an admin, not a reader. Each change is on the family's clinical audit trail.
   */
  canEditPhenotypes: boolean;
  /** Change the family members' edit (started from the family as stored when there is none). */
  onChange: (apply: (draft: FamilyMembersDraft) => FamilyMembersDraft) => void;
  onClose: () => void;
}

/**
 * The family member dialog: the member's ID, pedigree and status, edited in the same draft as
 * the Family members table (a change shows there at once and is saved with Save changes);
 * its HPO phenotypes, each saved on its own; and what removing it affects.
 */
const FamilyMemberDetailDialog: React.FC<FamilyMemberDetailDialogProps> = ({
  familyId,
  family,
  memberId,
  draft,
  editing,
  canEditMember,
  canEditPhenotypes,
  onChange,
  onClose,
}) => {
  const queryClient = useQueryClient();
  // The ids that tie each field's label to its control.
  const fieldId = useId();
  const [hpoSearchInput, setHpoSearchInput] = useState('');
  const [selectedHpoTerm, setSelectedHpoTerm] = useState<ApiHpoTerm | null>(null);
  const [hpoAnnotationStatus, setHpoAnnotationStatus] = useState<HpoAnnotationStatus>('present');
  const [hpoNote, setHpoNote] = useState('');
  const [hpoBusy, setHpoBusy] = useState(false);
  const [hpoEdit, setHpoEdit] = useState<HpoAnnotationEdit | null>(null);
  const [hpoStatus, setHpoStatus] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const hpoSearchQuery = hpoSearchInput.trim();
  const { data: hpoSearchResults = [] } = useQuery<ApiHpoTerm[]>({
    queryKey: ['hpo-search', hpoSearchQuery],
    enabled: hpoSearchQuery.length >= 2,
    queryFn: async () => {
      const res = await api.get('/hpo/search', { //
        params: { q: hpoSearchQuery, limit: 20 },
      });
      return res.data as ApiHpoTerm[];
    },
  });
  const {
    data: selectedMemberDetail,
    isError: memberDetailFailed,
    error: memberDetailError,
    refetch: refetchMemberDetail,
  } = useQuery<ApiFamilyMemberDetail>({
    queryKey: ['family', familyId, 'member', memberId],
    enabled: Boolean(familyId && memberId),
    queryFn: async () => {
      const res = await api.get(
        apiPath`/families/${familyId}/members/${memberId}`,
      );
      return res.data as ApiFamilyMemberDetail;
    },
  });

  const updateHpoSearchInput = (value: string) => {
    setHpoSearchInput(value);
    const normalizedValue = value.trim().toLowerCase();
    const matchedTerm = hpoSearchResults.find((term) => {
      return (
        term.hpo_id.toLowerCase() === normalizedValue ||
        formatHpoTermOption(term).toLowerCase() === normalizedValue
      );
    });
    setSelectedHpoTerm(matchedTerm ?? null);
  };

  // Whether closing the dialog would lose input: a phenotype being entered or changed. The
  // member's own fields are in the family members' edit already, so they do not count.
  const hpoEditChanged = (edit: HpoAnnotationEdit | null): boolean =>
    Boolean(edit && (edit.status !== edit.savedStatus || edit.note.trim() !== edit.savedNote));

  const memberDetailHasUnsavedInput = () =>
    Boolean(hpoSearchInput.trim() || hpoNote.trim() || hpoEditChanged(hpoEdit));

  const member = findMember(draft, memberId);
  const fieldsDisabled = !canEditMember || !member || member.removed;
  const lastMember = activeMembers(draft).length <= 1;

  const addHpoAnnotation = async () => {
    if (!familyId || !selectedHpoTerm || !selectedMemberDetail) {
      setHpoStatus({ tone: 'error', message: 'Select a family member and HPO term.' });
      return;
    }
    setHpoBusy(true);
    setHpoStatus(null);
    try {
      await api.post(
        apiPath`/families/${familyId}/members/${memberId}/hpo`,
        {
          hpo_id: selectedHpoTerm.hpo_id,
          status: hpoAnnotationStatus,
          source: 'manual',
          note: hpoNote.trim() || null,
        },
      );
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      await queryClient.invalidateQueries({
        queryKey: ['family', familyId, 'member', memberId],
      });
      // Each phenotype change is an event on the family's clinical audit trail.
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'clinical-audit'] });
      setHpoNote('');
      setHpoSearchInput('');
      setSelectedHpoTerm(null);
      setHpoStatus({ tone: 'success', message: 'Phenotype annotation saved.' });
    } catch (error) {
      setHpoStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to save phenotype annotation.'),
      });
    } finally {
      setHpoBusy(false);
    }
  };

  const startHpoEdit = (annotation: ApiFamilyMemberDetail['hpo_annotations'][number]) => {
    const status = HPO_STATUS_OPTIONS.includes(annotation.status as HpoAnnotationStatus)
      ? (annotation.status as HpoAnnotationStatus)
      : 'present';
    const note = annotation.note ?? '';
    setHpoStatus(null);
    setHpoEdit({
      id: annotation.id,
      hpoId: annotation.hpo_id,
      label: annotation.label,
      status,
      note,
      savedStatus: status,
      savedNote: note,
    });
  };

  // Change a phenotype's status or note: the term stays; the change is an event on
  // the family's clinical audit trail, as an addition or a removal is.
  const saveHpoEdit = async () => {
    if (!familyId || !hpoEdit || !hpoEditChanged(hpoEdit)) return;
    setHpoBusy(true);
    setHpoStatus(null);
    try {
      await api.put(apiPath`/families/${familyId}/hpo/${hpoEdit.id}`, {
        status: hpoEdit.status,
        note: hpoEdit.note.trim() || null,
      });
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'member'] });
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'clinical-audit'] });
      setHpoEdit(null);
      setHpoStatus({ tone: 'success', message: 'Phenotype annotation updated.' });
    } catch (error) {
      setHpoStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to update phenotype annotation.'),
      });
    } finally {
      setHpoBusy(false);
    }
  };

  const removeHpoAnnotation = async (annotationId: string) => {
    if (!familyId) return;
    setHpoBusy(true);
    setHpoStatus(null);
    try {
      await api.delete(apiPath`/families/${familyId}/hpo/${annotationId}`);
      if (hpoEdit?.id === annotationId) setHpoEdit(null);
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'member'] });
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'clinical-audit'] });
      setHpoStatus({ tone: 'success', message: 'Phenotype annotation removed.' });
    } catch (error) {
      setHpoStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to remove phenotype annotation.'),
      });
    } finally {
      setHpoBusy(false);
    }
  };

  // The member's fields write into the family members' edit as they change: the table shows
  // the change at once, and Save changes saves it with the rest.
  const changeMember = (apply: (current: FamilyMembersDraft) => FamilyMembersDraft) => {
    if (!fieldsDisabled) onChange(apply);
  };

  return (
    // ModalDialog: Escape, a focus trap and focus restore, and a check before a half-entered
    // phenotype is discarded.
    <ModalDialog
      onClose={onClose}
      isDirty={memberDetailHasUnsavedInput}
      label="Family member details"
      className="modal-surface surface-card family-member-modal"
      backdropClassName="modal-backdrop family-member-modal-backdrop"
    >
      {(requestClose) => (
        <>
          <div className="variant-review-modal-header">
            <div>
              <p className="page-kicker">Family member</p>
              <h2 className="catalog-card-title">{member ? memberLabel(member) : memberId}</h2>
            </div>
            <button type="button" className="button-secondary" onClick={requestClose}>
              Close
            </button>
          </div>

          {memberDetailFailed ? (
            // It stayed at "Loading member details." for good.
            <QueryFailure
              what="the member's details"
              error={memberDetailError}
              onRetry={() => void refetchMemberDetail()}
            />
          ) : !selectedMemberDetail || !member ? (
            <p className="dashboard-link-note">Loading member details.</p>
          ) : (
            <>
              <p className="dashboard-link-note">
                {canEditMember
                  ? member.removed
                    ? `${member.sample_id} is taken out of the family when you save the family members.`
                    : editing
                      ? 'Your changes here are part of the family members you are editing: they show in the table, and Save changes saves them together.'
                      : 'A change here starts editing the family members: it shows in the table, and Save changes saves it.'
                  : canEditPhenotypes
                    ? "Only an administrator changes a member's sample, pedigree and status. You can add, change and remove its HPO terms."
                    : "You have read access to this family's project: nothing here can be changed."}
              </p>
              {/* The member's details in three groups: the sample itself, where it sits in the
                  pedigree (its role and its relationships), and its affected and carrier status. */}
              <div className="family-member-modal-groups">
                <fieldset className="family-member-modal-group family-member-modal-group--sample">
                  <legend>Sample</legend>
                  <label className="field-label">
                    Identifier
                    <input
                      type="text"
                      value={member.new_sample_id}
                      onChange={(event) => {
                        const new_sample_id = event.target.value;
                        changeMember((current) => withMemberFields(current, memberId, { new_sample_id }));
                      }}
                      disabled={fieldsDisabled}
                    />
                  </label>
                  <label className="field-label" htmlFor={`${fieldId}-sex`}>
                    Sex
                    <SexSelect
                      id={`${fieldId}-sex`}
                      value={member.sex}
                      onChange={(sex) => changeMember((current) => withMemberFields(current, memberId, { sex }))}
                      disabled={fieldsDisabled}
                    />
                  </label>
                </fieldset>
                <fieldset className="family-member-modal-group family-member-modal-group--pedigree">
                  <legend>Pedigree</legend>
                  <label className="field-label" htmlFor={`${fieldId}-role`}>
                    Family role
                    <RoleSelect
                      id={`${fieldId}-role`}
                      value={member.role}
                      onChange={(role) => changeMember((current) => withMemberFields(current, memberId, { role }))}
                      disabled={fieldsDisabled}
                    />
                  </label>
                  <label className="field-label" htmlFor={`${fieldId}-father`}>
                    Father
                    <ParentSelect
                      id={`${fieldId}-father`}
                      draft={draft}
                      childId={memberId}
                      role="father"
                      onChange={(parentId) => changeMember((current) => withParent(current, memberId, 'father', parentId))}
                      disabled={fieldsDisabled}
                    />
                  </label>
                  <label className="field-label" htmlFor={`${fieldId}-mother`}>
                    Mother
                    <ParentSelect
                      id={`${fieldId}-mother`}
                      draft={draft}
                      childId={memberId}
                      role="mother"
                      onChange={(parentId) => changeMember((current) => withParent(current, memberId, 'mother', parentId))}
                      disabled={fieldsDisabled}
                    />
                  </label>
                  <label className="field-label" htmlFor={`${fieldId}-partner`}>
                    Partner
                    <PartnerSelect
                      id={`${fieldId}-partner`}
                      draft={draft}
                      memberId={memberId}
                      onChange={(partnerId) => changeMember((current) => withPartner(current, memberId, partnerId))}
                      disabled={fieldsDisabled}
                    />
                  </label>
                  <label className="field-label" htmlFor={`${fieldId}-related`} title="Related to the family through this member, by an unknown degree">
                    Related to
                    <RelatedToSelect
                      id={`${fieldId}-related`}
                      draft={draft}
                      memberId={memberId}
                      onChange={(value) => changeMember((current) => withRelatives(current, memberId, value))}
                      disabled={fieldsDisabled}
                    />
                  </label>
                </fieldset>
                <fieldset className="family-member-modal-group family-member-modal-group--status">
                  <legend>Affected and carrier status</legend>
                  <label className="field-label" htmlFor={`${fieldId}-affected`}>
                    Affected status
                    <ClinicalStatusSelect
                      id={`${fieldId}-affected`}
                      value={member.clinical_status}
                      onChange={(clinical_status) =>
                        changeMember((current) => withMemberFields(current, memberId, { clinical_status }))
                      }
                      disabled={fieldsDisabled}
                    />
                  </label>
                  <label className="field-label" htmlFor={`${fieldId}-carrier`}>
                    Carrier status
                    <CarrierStatusSelect
                      id={`${fieldId}-carrier`}
                      value={member.carrier_status}
                      onChange={(carrier_status) =>
                        changeMember((current) => withMemberFields(current, memberId, { carrier_status }))
                      }
                      disabled={fieldsDisabled}
                    />
                  </label>
                  {member.carrier_status === 'carrier' && (
                    <label className="field-label" htmlFor={`${fieldId}-carrier-type`}>
                      Carrier type
                      <CarrierTypeSelect
                        id={`${fieldId}-carrier-type`}
                        value={member.carrier_type}
                        onChange={(carrier_type) =>
                          changeMember((current) => withMemberFields(current, memberId, { carrier_type }))
                        }
                        disabled={fieldsDisabled}
                      />
                    </label>
                  )}
                </fieldset>
              </div>

              <div className="family-member-modal-sections">
                <div className="variant-review-modal-section family-member-modal-section">
                  <div className="family-workspace-card-head">
                    <h3 className="section-title">HPO phenotypes</h3>
                    <span className="table-chip">
                      {selectedMemberDetail.hpo_annotations.length} terms
                    </span>
                  </div>
                  {selectedMemberDetail.hpo_annotations.length ? (
                    <div className="family-hpo-chip-list">
                      {selectedMemberDetail.hpo_annotations.map((annotation) => (
                        <span
                          key={annotation.id}
                          className={`table-chip family-hpo-chip family-hpo-chip--${annotation.status}`}
                          title={hpoTooltip(annotation)}
                        >
                          <span>{annotation.hpo_id}</span>
                          <strong>{annotation.label}</strong>
                          <em>{annotation.status}</em>
                          {canEditPhenotypes && (
                            <>
                              <button
                                type="button"
                                className="button-ghost"
                                onClick={() => startHpoEdit(annotation)}
                                disabled={hpoBusy}
                                aria-pressed={hpoEdit?.id === annotation.id}
                              >
                                Edit
                              </button>
                              <button
                                type="button"
                                className="button-ghost"
                                onClick={() => removeHpoAnnotation(annotation.id)}
                                disabled={hpoBusy}
                              >
                                Remove
                              </button>
                            </>
                          )}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="dashboard-link-note">No HPO phenotypes linked.</p>
                  )}

                  {canEditPhenotypes && hpoEdit && (
                    <fieldset className="family-hpo-edit">
                      <legend>
                        Edit {hpoEdit.hpoId} {hpoEdit.label}
                      </legend>
                      <div className="family-hpo-controls">
                        <label className="field-label">
                          Status
                          <select
                            value={hpoEdit.status}
                            onChange={(event) => {
                              const status = event.target.value as HpoAnnotationStatus;
                              setHpoEdit((edit) => (edit ? { ...edit, status } : edit));
                            }}
                            disabled={hpoBusy}
                          >
                            {HPO_STATUS_OPTIONS.map((status) => (
                              <option key={status} value={status}>
                                {status}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="field-label family-hpo-note-field">
                          Note
                          <input
                            type="text"
                            value={hpoEdit.note}
                            onChange={(event) => {
                              const note = event.target.value;
                              setHpoEdit((edit) => (edit ? { ...edit, note } : edit));
                            }}
                            disabled={hpoBusy}
                          />
                        </label>
                        <button
                          type="button"
                          className="form-button"
                          onClick={saveHpoEdit}
                          disabled={hpoBusy || !hpoEditChanged(hpoEdit)}
                        >
                          Save change
                        </button>
                        <button
                          type="button"
                          className="button-secondary"
                          onClick={() => setHpoEdit(null)}
                          disabled={hpoBusy}
                        >
                          Cancel
                        </button>
                      </div>
                    </fieldset>
                  )}
                  {canEditPhenotypes && (
                    <>
                      <div className="family-hpo-controls">
                        <label className="field-label family-hpo-term-field">
                          HPO term
                          <input
                            type="text"
                            value={hpoSearchInput}
                            onChange={(event) => updateHpoSearchInput(event.target.value)}
                            placeholder="HP:0001250 or seizure"
                            disabled={hpoBusy}
                            list={`hpo-term-options-${family.family_id}`}
                          />
                          <datalist id={`hpo-term-options-${family.family_id}`}>
                            {hpoSearchResults.map((term) => (
                              <option key={term.hpo_id} value={formatHpoTermOption(term)} />
                            ))}
                          </datalist>
                        </label>
                        <label className="field-label">
                          Status
                          <select
                            value={hpoAnnotationStatus}
                            onChange={(event) =>
                              setHpoAnnotationStatus(event.target.value as HpoAnnotationStatus)
                            }
                            disabled={hpoBusy}
                          >
                            {HPO_STATUS_OPTIONS.map((status) => (
                              <option key={status} value={status}>
                                {status}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="field-label family-hpo-note-field">
                          Note
                          <input
                            type="text"
                            value={hpoNote}
                            onChange={(event) => setHpoNote(event.target.value)}
                            disabled={hpoBusy}
                          />
                        </label>
                        <button
                          type="button"
                          className="form-button"
                          onClick={addHpoAnnotation}
                          disabled={hpoBusy || !selectedHpoTerm}
                        >
                          Add phenotype
                        </button>
                      </div>

                    </>
                  )}
                  {hpoStatus && (
                    <div className={`status-note ${hpoStatus.tone === 'success' ? 'status-note--success' : 'status-note--error'}`}>
                      {hpoStatus.message}
                    </div>
                  )}
                </div>
                <div className="variant-review-modal-section family-member-modal-section">
                  <div className="family-workspace-card-head">
                    <h3 className="section-title">Impact</h3>
                    {selectedMemberDetail.impact.destructive && (
                      <span className="table-chip table-chip--critical">Linked data</span>
                    )}
                  </div>
                  {selectedMemberDetail.impact.warnings.length ? (
                    <ul className="family-member-impact-list">
                      {selectedMemberDetail.impact.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="dashboard-link-note">No derived-data dependencies detected.</p>
                  )}
                  <div className="family-member-impact-chips">
                    {Object.entries(selectedMemberDetail.impact.pedigree_references).map(([key, count]) => (
                      <span key={key} className="table-chip">
                        {key} {count}
                      </span>
                    ))}
                    {Object.entries(selectedMemberDetail.impact.data_counts).map(([key, count]) => (
                      <span key={key} className="table-chip">
                        {key} {count}
                      </span>
                    ))}
                    {selectedMemberDetail.impact.stale_analysis_scopes.map((scope) => (
                      <span key={scope} className="table-chip table-chip--critical">
                        {scope}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {canEditMember && (
                <div className="variant-review-modal-actions compact-toolbar">
                  {member.removed ? (
                    <button
                      type="button"
                      className="button-secondary"
                      onClick={() => onChange((current) => withMemberRestored(current, memberId))}
                    >
                      Restore member
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="button-ghost"
                      onClick={() => onChange((current) => withMemberRemoved(current, memberId))}
                      disabled={lastMember}
                    >
                      Remove member
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}
    </ModalDialog>
  );
};

export default FamilyMemberDetailDialog;
