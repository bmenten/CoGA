import React, { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import ModalDialog from '../../components/ModalDialog';
import type {
  ApiFamilyRecord,
  ApiFamilyMemberDeleteResponse,
  ApiFamilyMemberDetail,
  ApiHpoTerm,
} from '../../lib/apiTypes';
import { getErrorMessage } from '../../lib/errorMessage';
import type {
  CarrierStatus,
  CarrierType,
  ClinicalStatus,
  HpoAnnotationStatus,
  MemberDetailDraft,
  StructureMemberDraft,
} from './familyDetailTypes';
import {
  CARRIER_STATUS_OPTIONS,
  CARRIER_TYPE_OPTIONS,
  CLINICAL_STATUS_OPTIONS,
  HPO_STATUS_OPTIONS,
  ROLE_OPTIONS,
  SEX_OPTIONS,
} from './familyDetailConstants';
import {
  formatHpoTermOption,
  hpoTooltip,
  memberDetailDraftFromDetail,
  sameMemberDetailDraft,
} from './familyDetailHelpers';
import { apiPath } from '../../lib/apiPath';

interface FamilyMemberDetailDialogProps {
  familyId?: string;
  family: ApiFamilyRecord;
  memberId: string;
  orderedMembers: ApiFamilyRecord['members'];
  userIsAdmin: boolean;
  /** This member's edits already waiting in the page's pending batch, if any. */
  pendingDraft?: MemberDetailDraft;
  /** Put the member's edits into the page's pending batch. */
  onQueue: (sampleId: string, draft: MemberDetailDraft) => void;
  /** After the member is removed: refresh what depends on the pedigree. */
  onRemoved: () => Promise<unknown>;
  onClose: () => void;
}

/**
 * The family member dialog (#528): the member's metadata, edited as a draft for the page's
 * pending batch; their HPO phenotypes; and removing them. It moved out of
 * FamilyDetailPage with the state only it used. The page keeps which member is open and
 * the pending batch.
 */
const FamilyMemberDetailDialog: React.FC<FamilyMemberDetailDialogProps> = ({
  familyId,
  family,
  memberId,
  orderedMembers,
  userIsAdmin,
  pendingDraft,
  onQueue,
  onRemoved,
  onClose,
}) => {
  const queryClient = useQueryClient();
  const [memberDraft, setMemberDraft] = useState<MemberDetailDraft | null>(null);
  const [memberBusy, setMemberBusy] = useState(false);
  const [memberStatus, setMemberStatus] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const [hpoSearchInput, setHpoSearchInput] = useState('');
  const [selectedHpoTerm, setSelectedHpoTerm] = useState<ApiHpoTerm | null>(null);
  const [hpoAnnotationStatus, setHpoAnnotationStatus] = useState<HpoAnnotationStatus>('present');
  const [hpoNote, setHpoNote] = useState('');
  const [hpoBusy, setHpoBusy] = useState(false);
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
  const { data: selectedMemberDetail } = useQuery<ApiFamilyMemberDetail>({
    queryKey: ['family', familyId, 'member', memberId],
    enabled: Boolean(familyId && memberId),
    queryFn: async () => {
      const res = await api.get(
        apiPath`/families/${familyId}/members/${memberId}`,
      );
      return res.data as ApiFamilyMemberDetail;
    },
  });

  useEffect(() => {
    if (!selectedMemberDetail) {
      setMemberDraft(null);
      return;
    }
    const pending = pendingDraft;
    if (pending) {
      setMemberDraft(pending);
      return;
    }
    setMemberDraft(memberDetailDraftFromDetail(selectedMemberDetail));
    setMemberStatus(null);
    setHpoStatus(null);
    setHpoSearchInput('');
    setSelectedHpoTerm(null);
    setHpoNote('');
    setHpoAnnotationStatus('present');
  }, [pendingDraft, selectedMemberDetail]);

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

  // Whether closing the member dialog would lose input: edits not yet applied to the
  // pending updates, or a phenotype being entered. Applied edits stay pending, so they
  // do not count.
  const memberDetailHasUnsavedInput = () => {
    if (hpoSearchInput.trim() || hpoNote.trim()) return true;
    if (!selectedMemberDetail || !memberDraft) return false;
    const applied =
      pendingDraft ?? memberDetailDraftFromDetail(selectedMemberDetail);
    return !sameMemberDetailDraft(memberDraft, applied);
  };

  const closeMemberDetail = () => {
    onClose();
    setMemberDraft(null);
    setMemberStatus(null);
    setHpoStatus(null);
    setHpoSearchInput('');
    setSelectedHpoTerm(null);
    setHpoNote('');
  };

  const applyMemberDetail = () => {
    if (!selectedMemberDetail || !memberDraft) return;
    onQueue(selectedMemberDetail.member.sample_id, {
      ...memberDraft,
      sample_id: memberDraft.sample_id.trim() || selectedMemberDetail.member.sample_id,
    });
    setMemberStatus({
      tone: 'success',
      message: 'Member changes are pending. Save pending updates to commit them together.',
    });
  };

  const deleteMemberDetail = async () => {
    if (!familyId || !selectedMemberDetail) return;
    const impactWarnings = selectedMemberDetail.impact.warnings.join('\n');
    const confirmed = window.confirm(
      [
        `Remove ${selectedMemberDetail.member.sample_id} from the active family?`,
        impactWarnings,
        'This can make derived analyses stale.',
      ]
        .filter(Boolean)
        .join('\n\n'),
    );
    if (!confirmed) return;
    setMemberBusy(true);
    setMemberStatus(null);
    try {
      const response = await api.delete(
        apiPath`/families/${familyId}/members/${selectedMemberDetail.member.sample_id}`,
        {
          params: {
            confirm: true,
          },
        },
      );
      const payload = response.data as ApiFamilyMemberDeleteResponse;
      queryClient.setQueryData(['family', familyId], payload.family);
      await queryClient.invalidateQueries({ queryKey: ['families'] });
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      await onRemoved();
      closeMemberDetail();
    } catch (error) {
      setMemberStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to remove family member.'),
      });
    } finally {
      setMemberBusy(false);
    }
  };

  const addHpoAnnotation = async () => {
    if (!familyId || !selectedHpoTerm || !selectedMemberDetail) {
      setHpoStatus({ tone: 'error', message: 'Select a family member and HPO term.' });
      return;
    }
    setHpoBusy(true);
    setHpoStatus(null);
    try {
      await api.post(
        apiPath`/families/${familyId}/members/${selectedMemberDetail.member.sample_id}/hpo`,
        {
          hpo_id: selectedHpoTerm.hpo_id,
          status: hpoAnnotationStatus,
          source: 'manual',
          note: hpoNote.trim() || null,
        },
      );
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      await queryClient.invalidateQueries({
        queryKey: ['family', familyId, 'member', selectedMemberDetail.member.sample_id],
      });
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

  const removeHpoAnnotation = async (annotationId: string) => {
    if (!familyId) return;
    setHpoBusy(true);
    setHpoStatus(null);
    try {
      await api.delete(apiPath`/families/${familyId}/hpo/${annotationId}`);
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] });
      await queryClient.invalidateQueries({ queryKey: ['family', familyId, 'member'] });
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

  return (
    // ModalDialog: Escape, a focus trap and focus restore, and a check before
    // unapplied edits or a half-entered phenotype are discarded (#529).
    <ModalDialog
      onClose={closeMemberDetail}
      isDirty={memberDetailHasUnsavedInput}
      label="Family member details"
      className="modal-surface surface-card family-member-modal"
      backdropClassName="modal-backdrop family-member-modal-backdrop"
    >
      {(requestClose) => (
        <>
          <div className="variant-review-modal-header">
            <div>
              <p className="page-kicker">Family Member</p>
              <h2 className="catalog-card-title">
                {selectedMemberDetail?.member.sample_id ?? memberId}
              </h2>
            </div>
            <button type="button" className="button-secondary" onClick={requestClose}>
              Close
            </button>
          </div>

          {!selectedMemberDetail || !memberDraft ? (
            <p className="dashboard-link-note">Loading member details.</p>
          ) : (
            <>
              <div className="family-member-modal-grid">
                <label className="field-label">
                  Identifier
                  <input
                    type="text"
                    value={memberDraft.sample_id}
                    onChange={(event) =>
                      setMemberDraft((draft) =>
                        draft ? { ...draft, sample_id: event.target.value } : draft,
                      )
                    }
                    disabled={!userIsAdmin || memberBusy}
                  />
                </label>
                <label className="field-label">
                  Sex
                  <select
                    value={memberDraft.sex}
                    onChange={(event) =>
                      setMemberDraft((draft) =>
                        draft
                          ? { ...draft, sex: event.target.value as StructureMemberDraft['sex'] }
                          : draft,
                      )
                    }
                    disabled={!userIsAdmin || memberBusy}
                  >
                    {SEX_OPTIONS.map((sex) => (
                      <option key={sex} value={sex}>
                        {sex}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field-label">
                  Family role
                  <select
                    value={memberDraft.role}
                    onChange={(event) =>
                      setMemberDraft((draft) =>
                        draft
                          ? { ...draft, role: event.target.value as StructureMemberDraft['role'] }
                          : draft,
                      )
                    }
                    disabled={!userIsAdmin || memberBusy}
                  >
                    {ROLE_OPTIONS.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field-label">
                  Phenotype
                  <select
                    value={memberDraft.clinical_status}
                    onChange={(event) =>
                      setMemberDraft((draft) =>
                        draft
                          ? { ...draft, clinical_status: event.target.value as ClinicalStatus }
                          : draft,
                      )
                    }
                    disabled={!userIsAdmin || memberBusy}
                  >
                    {CLINICAL_STATUS_OPTIONS.map((status) => (
                      <option key={status} value={status}>
                        {status}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field-label">
                  Carrier status
                  <select
                    value={memberDraft.carrier_status}
                    onChange={(event) =>
                      setMemberDraft((draft) =>
                        draft
                          ? { ...draft, carrier_status: event.target.value as CarrierStatus }
                          : draft,
                      )
                    }
                    disabled={!userIsAdmin || memberBusy}
                  >
                    {CARRIER_STATUS_OPTIONS.map((status) => (
                      <option key={status} value={status}>
                        {status}
                      </option>
                    ))}
                  </select>
                </label>
                {memberDraft.carrier_status === 'carrier' && (
                  <label className="field-label">
                    Carrier type
                    <select
                      value={memberDraft.carrier_type}
                      onChange={(event) =>
                        setMemberDraft((draft) =>
                          draft
                            ? { ...draft, carrier_type: event.target.value as CarrierType }
                            : draft,
                        )
                      }
                      disabled={!userIsAdmin || memberBusy}
                    >
                      <option value="">type</option>
                      {CARRIER_TYPE_OPTIONS.map((type) => (
                        <option key={type} value={type}>
                          {type}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="field-label">
                  Father
                  <select
                    value={memberDraft.father_id}
                    onChange={(event) =>
                      setMemberDraft((draft) =>
                        draft ? { ...draft, father_id: event.target.value } : draft,
                      )
                    }
                    disabled={!userIsAdmin || memberBusy}
                  >
                    <option value="">None</option>
                    {orderedMembers
                      .filter((member) => member.sample_id !== selectedMemberDetail.member.sample_id)
                      .map((member) => (
                        <option key={member.sample_id} value={member.sample_id}>
                          {member.sample_id}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="field-label">
                  Mother
                  <select
                    value={memberDraft.mother_id}
                    onChange={(event) =>
                      setMemberDraft((draft) =>
                        draft ? { ...draft, mother_id: event.target.value } : draft,
                      )
                    }
                    disabled={!userIsAdmin || memberBusy}
                  >
                    <option value="">None</option>
                    {orderedMembers
                      .filter((member) => member.sample_id !== selectedMemberDetail.member.sample_id)
                      .map((member) => (
                        <option key={member.sample_id} value={member.sample_id}>
                          {member.sample_id}
                        </option>
                      ))}
                  </select>
                </label>
              </div>

              <div className="family-member-modal-review-grid">
                <div className="variant-review-modal-section family-member-modal-section">
                  <div className="family-workspace-card-head">
                    <h3 className="section-title">HPO Phenotypes</h3>
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
                          {userIsAdmin && (
                            <button
                              type="button"
                              className="button-ghost"
                              onClick={() => removeHpoAnnotation(annotation.id)}
                              disabled={hpoBusy}
                            >
                              Remove
                            </button>
                          )}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="dashboard-link-note">No HPO phenotypes linked.</p>
                  )}

                  {userIsAdmin && (
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

              {memberStatus && (
                <div
                  className={`status-note ${
                    memberStatus.tone === 'success' ? 'status-note--success' : 'status-note--error'
                  }`}
                >
                  {memberStatus.message}
                </div>
              )}

              {userIsAdmin && (
                <div className="variant-review-modal-actions compact-toolbar">
                  <button type="button" className="form-button"onClick={applyMemberDetail} disabled={memberBusy}>
                    Apply to pending
                  </button>
                  <button
                    type="button"
                    className="button-ghost"
                    onClick={deleteMemberDetail}
                    disabled={memberBusy || orderedMembers.length <= 1}
                  >
                    Remove member
                  </button>
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
