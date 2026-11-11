import React, { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import InfoTip from '../../components/InfoTip';
import QueryFailure from '../../components/QueryFailure';
import { describePhaseCorrection } from '../../lib/haplotypePhaseCorrections';
import {
  BLOCKS_OUTDATED_WARNING,
  mixedHomologsWarning,
  qcVerdictWarning,
  segregationStateLabel,
  sexUnknownWarning,
  type EmbryoClassification,
} from '../../lib/embryoSegregation';
import type { ApiFamilyMemberDetail, ApiFamilyRecord, ApiHpoAnnotation } from '../../lib/apiTypes';
import type { FamilyStructureUpdateOut } from '../../lib/apiSchema.generated';
import { buildApiUnavailableMessage, getErrorMessage } from '../../lib/errorMessage';
import { apiPath } from '../../lib/apiPath';
import FamilyMemberDetailDialog from './FamilyMemberDetailDialog';
import SampleQcCell from './SampleQcCell';
import type { FamilyMembersDraft, StructureMemberDraft } from './familyDetailTypes';
import { defaultNewMember } from './familyDetailConstants';
import { hpoTooltip, sampleKey } from './familyDetailHelpers';
import {
  activeMembers,
  changedMemberCount,
  draftChanged,
  draftFromFamily,
  draftProblems,
  findMember,
  isRenamed,
  labelFor,
  memberChanged,
  memberLabel,
  NO_NEW_MEMBER_RELATIONS,
  newMemberIdProblem,
  parentOf,
  partnerOf,
  removedMembers,
  structureUpdateFromDraft,
  withMemberFields,
  withMemberRemoved,
  withMemberRestored,
  withNewMember,
  withParent,
  withPartner,
  withRelatives,
  type NewMemberRelations,
} from './familyMembersDraft';
import {
  CarrierStatusSelect,
  CarrierTypeSelect,
  ClinicalStatusSelect,
  ParentSelect,
  PartnerSelect,
  relatedToText,
  RelatedToSelect,
  RoleSelect,
  SexSelect,
} from './FamilyMemberFields';

type Status = { tone: 'success' | 'error'; message: string } | null;

interface FamilyMembersCardProps {
  family: ApiFamilyRecord;
  familyId: string;
  /** May change the members, their pedigree and status: an administrator. */
  canEditMembers: boolean;
  /** May add, change and remove HPO terms: an analyst of the family's project or an admin. */
  canEditPhenotypes: boolean;
  hpoAnnotationsBySample: Record<string, ApiHpoAnnotation[]>;
  hpoFailed: boolean;
  embryoSegregation: Map<string, EmbryoClassification>;
  segregationFailed: boolean;
  retrySegregation: () => void;
  haplotypeBlocksOutdated: boolean;
  /** After a save: refresh what depends on the pedigree. */
  onPedigreeChanged: () => Promise<unknown>;
}

/** What the confirmation before a save that removes members says, from their impact. */
const removalConfirmation = (details: ApiFamilyMemberDetail[]): string =>
  [
    `Remove ${details.map((detail) => detail.member.sample_id).join(', ')} from the family?`,
    ...details
      .filter((detail) => detail.impact.warnings.length > 0)
      .map((detail) => `${detail.member.sample_id}:\n${detail.impact.warnings.join('\n')}`),
    'This can make derived analyses stale.',
  ].join('\n\n');

/** An embryo's derived call at the ROI and the warnings that come with it, as read. */
const EmbryoCallChips: React.FC<{ embryoClass: EmbryoClassification }> = ({ embryoClass }) => {
  const qcWarning = qcVerdictWarning(embryoClass.qcVerdict, embryoClass.qcNotCountedReason);
  return (
    <>
      <InfoTip
        className={`segregation-badge segregation-badge--${embryoClass.state}`}
        label="Derived from the haplotype analysis at the ROI — not entered by an analyst."
      >
        {segregationStateLabel(embryoClass.state)}
      </InfoTip>
      {/* The markers at the ROI mix both homologs of a parent the call reads: the embryo may
          carry both (a trisomy), so it is not called. */}
      {embryoClass.mixedHomologs.length > 0 && (
        <InfoTip
          className="segregation-warning"
          label={mixedHomologsWarning(embryoClass.mixedHomologs, embryoClass.maxMarkerDiscordance)}
        >
          ⚠ both homologs
        </InfoTip>
      )}
      {/* The phasing QC at the ROI does not meet its thresholds, or was not counted: a warning
          beside the call, which stands (pgt_embryo_qc.py). */}
      {qcWarning && (
        <InfoTip className="segregation-warning" label={qcWarning}>
          {embryoClass.qcVerdict?.status === 'not_counted' ? '⚠ QC not counted' : '⚠ phasing QC'}
        </InfoTip>
      )}
      {embryoClass.recombinationNearRoi && (
        <InfoTip
          className="segregation-warning"
          label="A recombination falls inside or close to the ROI — the call across the locus is uncertain. Review the ROI markers."
        >
          ⚠ recombination
        </InfoTip>
      )}
      {embryoClass.phaseCorrectionsNearRoi.length > 0 && (
        <InfoTip
          className="segregation-warning"
          label={`${embryoClass.phaseCorrectionsNearRoi
            .map(describePhaseCorrection)
            .join(' ')} This is inside or close to the ROI: where the phase switched is known only to within the children's switches, so the call across the locus is less certain, and an embryo's own crossover there may not show. Review the ROI markers.`}
        >
          ⚠ phase corrected
        </InfoTip>
      )}
      {/* X-linked recessive: the call depends on a sex that is not recorded, so it assumes
          neither and the warning gives both calls. */}
      {embryoClass.sexDependent && (
        <InfoTip className="segregation-warning" label={sexUnknownWarning(embryoClass.sexDependent)}>
          ⚠ sex unknown
        </InfoTip>
      )}
      {embryoClass.uninformative &&
        !embryoClass.recombinationNearRoi &&
        !embryoClass.sexDependent &&
        !embryoClass.mixedHomologs.length && (
          <InfoTip
            className="segregation-warning"
            label={
              embryoClass.blocksOutdated
                ? BLOCKS_OUTDATED_WARNING
                : embryoClass.roiNotCovered
                  ? "This embryo's haplotype does not cover the ROI on the parental side the call needs (missing data), so no call can be made."
                  : 'No disease haplotype could be resolved at the ROI (uninformative markers).'
            }
          >
            ⚠ uninformative
          </InfoTip>
        )}
    </>
  );
};

/**
 * The family's members: one table, read as the family stands, that an administrator turns into
 * the editor of its members, pedigree and status (Edit members), in the family workspace and in
 * Admin → Family & sample data alike. The member dialog edits the same draft and adds the
 * member's ID and HPO terms. One save sends the draft as one change of the family's structure:
 * one structure version, one event on the clinical audit trail.
 */
const FamilyMembersCard: React.FC<FamilyMembersCardProps> = ({
  family,
  familyId,
  canEditMembers,
  canEditPhenotypes,
  hpoAnnotationsBySample,
  hpoFailed,
  embryoSegregation,
  segregationFailed,
  retrySegregation,
  haplotypeBlocksOutdated,
  onPedigreeChanged,
}) => {
  const queryClient = useQueryClient();
  const saved = useMemo(() => draftFromFamily(family), [family]);
  // Null while the table is read; the edit otherwise, from Edit members or a first change in
  // the member dialog until it is saved or discarded.
  const [draft, setDraft] = useState<FamilyMembersDraft | null>(null);
  const [newMember, setNewMember] = useState<StructureMemberDraft>(defaultNewMember);
  const [newRelations, setNewRelations] = useState<NewMemberRelations>(NO_NEW_MEMBER_RELATIONS);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>(null);
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);

  const editing = draft !== null;
  const view = draft ?? saved;
  const changed = draft ? draftChanged(draft, saved) : false;
  const changedCount = draft ? changedMemberCount(draft, saved) : 0;
  const problems = draft ? draftProblems(draft) : [];
  const activeCount = activeMembers(view).length;
  const apiMemberByKey = useMemo(
    () =>
      new Map((family.members ?? []).map((member) => [sampleKey(member.sample_id), member] as const)),
    [family.members],
  );

  // A member the family no longer has (renamed or removed by a save) closes its dialog.
  useEffect(() => {
    if (selectedMemberId && !findMember(view, selectedMemberId)) setSelectedMemberId(null);
  }, [selectedMemberId, view]);

  const change = (apply: (current: FamilyMembersDraft) => FamilyMembersDraft) => {
    setStatus(null);
    setDraft((current) => apply(current ?? saved));
  };

  const resetNewMember = () => {
    setNewMember(defaultNewMember);
    setNewRelations(NO_NEW_MEMBER_RELATIONS);
  };

  const discard = () => {
    if (changed && !window.confirm('Discard the changes to the family members?')) return;
    setDraft(null);
    resetNewMember();
    setStatus(null);
  };

  const addMember = () => {
    if (!draft) return;
    const problem = newMemberIdProblem(draft, newMember.sample_id);
    if (problem) {
      setStatus({ tone: 'error', message: problem });
      return;
    }
    change((current) => withNewMember(current, newMember, newRelations));
    resetNewMember();
  };

  const save = async () => {
    if (!draft || !changed || problems.length > 0) return;
    setBusy(true);
    setStatus(null);
    try {
      // A removal is confirmed with what it affects, read now: the linked data and the
      // analyses it makes stale.
      const removed = removedMembers(draft);
      if (removed.length > 0) {
        let details: ApiFamilyMemberDetail[];
        try {
          details = await Promise.all(
            removed.map((member) =>
              queryClient.fetchQuery({
                queryKey: ['family', familyId, 'member', member.sample_id],
                queryFn: async () =>
                  (await api.get(apiPath`/families/${familyId}/members/${member.sample_id}`))
                    .data as ApiFamilyMemberDetail,
                staleTime: 0,
              }),
            ),
          );
        } catch (error) {
          setStatus({
            tone: 'error',
            message: `${getErrorMessage(error, 'Could not read what removing the members affects.')} Nothing was saved.`,
          });
          return;
        }
        if (!window.confirm(removalConfirmation(details))) return;
      }
      const response = await api.put(
        apiPath`/families/${familyId}/structure`,
        structureUpdateFromDraft(draft, saved),
      );
      const payload = response.data as FamilyStructureUpdateOut;
      queryClient.setQueryData(['family', familyId], payload.family);
      setDraft(null);
      resetNewMember();
      setStatus({
        tone: 'success',
        message: payload.warnings?.length
          ? `Family members saved. ${payload.warnings.join(' ')}`
          : 'Family members saved.',
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['families'] }),
        // Admin → Family & sample data lists the same members with their role, sex and status.
        queryClient.invalidateQueries({ queryKey: ['admin', 'data-inventory'] }),
        // A rename moves the member's HPO terms and details to its new ID.
        queryClient.invalidateQueries({ queryKey: ['family', familyId, 'hpo'] }),
        queryClient.invalidateQueries({ queryKey: ['family', familyId, 'member'] }),
        queryClient.invalidateQueries({ queryKey: ['family', familyId, 'clinical-audit'] }),
        onPedigreeChanged(),
      ]);
    } catch (error) {
      setStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to save the family members.', {
          networkFallback: buildApiUnavailableMessage(api.defaults.baseURL),
        }),
      });
    } finally {
      setBusy(false);
    }
  };

  const controlsDisabled = (member: StructureMemberDraft) => busy || member.removed;

  return (
    <section className="surface-card family-members-card space-y-3">
      <div className="family-workspace-card-head">
        <div className="space-y-1">
          <h2 className="section-title">Family members</h2>
          <p className="dashboard-link-note">
            Proband-first overview with pedigree relations and phenotype labels.
          </p>
        </div>
        {canEditMembers && (
          <div className="compact-toolbar family-toolbar">
            {editing ? (
              <>
                <span className={`table-chip${changed ? ' table-chip--critical' : ''}`}>
                  {changed
                    ? changedCount > 0
                      ? `${changedCount} ${changedCount === 1 ? 'member' : 'members'} changed`
                      : 'Relationships changed'
                    : 'No changes yet'}
                </span>
                <button
                  type="button"
                  className="form-button"
                  onClick={save}
                  disabled={busy || !changed || problems.length > 0}
                >
                  {busy ? 'Saving…' : 'Save changes'}
                </button>
                <button type="button" className="button-ghost" onClick={discard} disabled={busy}>
                  Discard
                </button>
              </>
            ) : (
              <button type="button" className="button-secondary" onClick={() => change((current) => current)}>
                Edit members
              </button>
            )}
            {family.structure_version?.version !== undefined && (
              <span className="table-chip">Version {family.structure_version.version}</span>
            )}
          </div>
        )}
      </div>
      {status && (
        <div className={`status-note ${status.tone === 'success' ? 'status-note--success' : 'status-note--error'}`}>
          {status.message}
        </div>
      )}
      {problems.map((problem) => (
        <div key={problem} className="status-note status-note--error">
          {problem}
        </div>
      ))}
      {/* No derived segregation is not "no warnings": the embryos' recombination and
          uninformative flags come with the calls (TF-12 U2). */}
      {segregationFailed ? (
        <QueryFailure
          what="the embryo calls at the ROI"
          onRetry={retrySegregation}
          consequence="The embryos' segregation, and any recombination or uninformative warning, are not shown."
        />
      ) : null}
      {/* The blocks no longer match the pedigree: the calls are uninformative. */}
      {haplotypeBlocksOutdated ? (
        <p className="status-note status-note--warning" role="note">
          ⚠ {BLOCKS_OUTDATED_WARNING}
        </p>
      ) : null}
      <div className={`data-table-shell overflow-x-auto${editing ? '' : ' data-table-shell--fit'}`}>
        <table className={`analysis-table family-members-table${editing ? ' family-members-table--editing' : ''}`}>
          {/* Read, the table is sized to its content. Edited, its columns are fixed, a col for
              each of them: Status, with two or three choices, takes the widest; sequencing QC,
              a compact pill, a narrow one. */}
          {editing ? (
            <colgroup>
              <col style={{ width: '11%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '9%' }} />
              <col style={{ width: '19%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '8%' }} />
            </colgroup>
          ) : null}
          <thead>
            <tr>
              <th>Sample</th>
              <th>Role</th>
              {editing && <th>Sex</th>}
              <th>Father</th>
              <th>Mother</th>
              <th>Partner</th>
              <th title="Related to the family through this member, by an unknown degree">Related to</th>
              <th>Status</th>
              <th>HPO terms</th>
              <th>Seq. QC</th>
              {editing && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {view.members.map((member) => {
              const id = member.sample_id;
              const label = memberLabel(member);
              const apiMember = member.isNew ? undefined : apiMemberByKey.get(sampleKey(id));
              const sexSymbol = member.sex === 'male' ? '♂' : member.sex === 'female' ? '♀' : '⚧';
              const annotations = member.isNew ? [] : hpoAnnotationsBySample[id] || [];
              const embryoClass =
                !editing && member.role === 'embryo' ? embryoSegregation.get(id) : undefined;
              const disabled = controlsDisabled(member);
              const rowChanged = draft ? memberChanged(draft, saved, id) : false;
              return (
                <tr key={id} className={member.removed ? 'family-members-row--removed' : undefined}>
                  <td>
                    <span className="family-member-identity">
                      <span className="family-member-sex" aria-hidden="true">
                        {sexSymbol}
                      </span>
                      {member.isNew ? (
                        <strong>{label}</strong>
                      ) : (
                        <button
                          type="button"
                          className="button-link family-member-name-button"
                          onClick={() => setSelectedMemberId(id)}
                          title={isRenamed(member) ? `Stored as ${id}: renamed when the changes are saved` : undefined}
                        >
                          {label}
                        </button>
                      )}
                      {member.clinical_status === 'affected' && (
                        <span className="family-member-affected" title="Affected">
                          *
                        </span>
                      )}
                      {member.isNew ? (
                        <span className="table-chip table-chip--critical">new</span>
                      ) : member.removed ? (
                        <span className="table-chip table-chip--critical">removed on save</span>
                      ) : rowChanged ? (
                        <span className="table-chip table-chip--critical" title="Changed, not saved yet">
                          changed
                        </span>
                      ) : null}
                    </span>
                  </td>
                  <td>
                    {editing ? (
                      <RoleSelect
                        aria-label={`Role for ${id}`}
                        value={member.role}
                        onChange={(role) => change((current) => withMemberFields(current, id, { role }))}
                        disabled={disabled}
                      />
                    ) : (
                      member.role
                    )}
                  </td>
                  {editing && (
                    <td>
                      <SexSelect
                        aria-label={`Sex for ${id}`}
                        value={member.sex}
                        onChange={(sex) => change((current) => withMemberFields(current, id, { sex }))}
                        disabled={disabled}
                      />
                    </td>
                  )}
                  {(['father', 'mother'] as const).map((role) => (
                    <td key={role}>
                      {editing ? (
                        <ParentSelect
                          aria-label={`${role === 'father' ? 'Father' : 'Mother'} for ${id}`}
                          draft={view}
                          childId={id}
                          role={role}
                          onChange={(parentId) => change((current) => withParent(current, id, role, parentId))}
                          disabled={disabled}
                        />
                      ) : (
                        (parentOf(view, id, role) && labelFor(view, parentOf(view, id, role))) || '-'
                      )}
                    </td>
                  ))}
                  <td>
                    {editing ? (
                      <PartnerSelect
                        aria-label={`Partner for ${id}`}
                        draft={view}
                        memberId={id}
                        onChange={(partnerId) => change((current) => withPartner(current, id, partnerId))}
                        disabled={disabled}
                      />
                    ) : (
                      (partnerOf(view, id) && labelFor(view, partnerOf(view, id))) || '-'
                    )}
                  </td>
                  <td>
                    {editing ? (
                      <RelatedToSelect
                        aria-label={`Related to, for ${id}`}
                        title="Related to the family through this member, by an unknown degree"
                        draft={view}
                        memberId={id}
                        onChange={(value) => change((current) => withRelatives(current, id, value))}
                        disabled={disabled}
                      />
                    ) : (
                      relatedToText(view, id) || '-'
                    )}
                  </td>
                  <td>
                    {editing ? (
                      <div className="family-member-status-controls">
                        <ClinicalStatusSelect
                          aria-label={`Affected status for ${id}`}
                          value={member.clinical_status}
                          onChange={(clinical_status) =>
                            change((current) => withMemberFields(current, id, { clinical_status }))
                          }
                          disabled={disabled}
                        />
                        <CarrierStatusSelect
                          aria-label={`Carrier status for ${id}`}
                          value={member.carrier_status}
                          onChange={(carrier_status) =>
                            change((current) => withMemberFields(current, id, { carrier_status }))
                          }
                          disabled={disabled}
                        />
                        {member.carrier_status === 'carrier' && (
                          <CarrierTypeSelect
                            aria-label={`Carrier type for ${id}`}
                            value={member.carrier_type}
                            onChange={(carrier_type) =>
                              change((current) => withMemberFields(current, id, { carrier_type }))
                            }
                            disabled={disabled}
                          />
                        )}
                      </div>
                    ) : (
                      <div className="family-member-status-chips">
                        <span
                          className={`table-chip ${member.clinical_status === 'affected' ? 'table-chip--critical' : ''}`}
                        >
                          {member.clinical_status}
                        </span>
                        {member.carrier_status === 'carrier' && (
                          <span className="table-chip table-chip--warning">{member.carrier_type || 'carrier'}</span>
                        )}
                        {segregationFailed && member.role === 'embryo' && (
                          <InfoTip
                            className="segregation-warning"
                            label="The embryo calls at the ROI could not be loaded: this embryo's segregation, and any recombination or uninformative warning, are unknown."
                          >
                            ⚠ segregation not derived
                          </InfoTip>
                        )}
                        {embryoClass && <EmbryoCallChips embryoClass={embryoClass} />}
                      </div>
                    )}
                  </td>
                  <td>
                    {annotations.length ? (
                      <div className="family-hpo-chip-list family-hpo-chip-list--compact">
                        {annotations.slice(0, 3).map((annotation) => (
                          <span
                            key={annotation.id}
                            className={`table-chip family-hpo-chip family-hpo-chip--${annotation.status}`}
                            title={hpoTooltip(annotation)}
                          >
                            <strong>{annotation.label}</strong>
                            {annotation.status !== 'present' && <em>{annotation.status}</em>}
                          </span>
                        ))}
                        {annotations.length > 3 && (
                          <span className="table-chip" title={`${annotations.length} HPO annotations`}>
                            +{annotations.length - 3}
                          </span>
                        )}
                      </div>
                    ) : hpoFailed && !member.isNew ? (
                      // Not "-": the member's terms are unknown.
                      <span className="dashboard-link-note">could not be loaded</span>
                    ) : (
                      <span className="dashboard-link-note">-</span>
                    )}
                  </td>
                  <td>
                    <SampleQcCell familyId={family.family_id} member={apiMember} />
                  </td>
                  {editing && (
                    <td>
                      {member.removed ? (
                        <button
                          type="button"
                          className="button-ghost"
                          onClick={() => change((current) => withMemberRestored(current, id))}
                          disabled={busy}
                        >
                          Restore
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="button-ghost"
                          aria-label={`Remove ${label}`}
                          onClick={() => change((current) => withMemberRemoved(current, id))}
                          disabled={busy || activeCount <= 1}
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
          {editing && (
            <tfoot>
              <tr className="family-members-add-row">
                <td>
                  <input
                    aria-label="New member sample ID"
                    placeholder="New sample ID"
                    type="text"
                    value={newMember.sample_id}
                    onChange={(event) => setNewMember((member) => ({ ...member, sample_id: event.target.value }))}
                    disabled={busy}
                  />
                </td>
                <td>
                  <RoleSelect
                    aria-label="New member role"
                    value={newMember.role}
                    onChange={(role) => setNewMember((member) => ({ ...member, role }))}
                    disabled={busy}
                  />
                </td>
                <td>
                  <SexSelect
                    aria-label="New member sex"
                    value={newMember.sex}
                    onChange={(sex) => setNewMember((member) => ({ ...member, sex }))}
                    disabled={busy}
                  />
                </td>
                {(['father', 'mother'] as const).map((role) => (
                  <td key={role}>
                    <ParentSelect
                      aria-label={`New member ${role}`}
                      draft={view}
                      childId=""
                      role={role}
                      value={newRelations[role]}
                      onChange={(parentId) => setNewRelations((relations) => ({ ...relations, [role]: parentId }))}
                      disabled={busy}
                    />
                  </td>
                ))}
                <td>
                  <PartnerSelect
                    aria-label="New member partner"
                    draft={view}
                    memberId=""
                    value={newRelations.partner}
                    onChange={(partner) => setNewRelations((relations) => ({ ...relations, partner }))}
                    disabled={busy}
                  />
                </td>
                <td>
                  <RelatedToSelect
                    aria-label="New member related to"
                    draft={view}
                    memberId=""
                    value={newRelations.related}
                    onChange={(related) => setNewRelations((relations) => ({ ...relations, related }))}
                    disabled={busy}
                  />
                </td>
                <td>
                  <div className="family-member-status-controls">
                    <ClinicalStatusSelect
                      aria-label="New member affected status"
                      value={newMember.clinical_status}
                      onChange={(clinical_status) => setNewMember((member) => ({ ...member, clinical_status }))}
                      disabled={busy}
                    />
                    <CarrierStatusSelect
                      aria-label="New member carrier status"
                      value={newMember.carrier_status}
                      onChange={(carrier_status) =>
                        setNewMember((member) => ({
                          ...member,
                          carrier_status,
                          carrier_type: carrier_status === 'carrier' ? member.carrier_type : '',
                        }))
                      }
                      disabled={busy}
                    />
                    {newMember.carrier_status === 'carrier' && (
                      <CarrierTypeSelect
                        aria-label="New member carrier type"
                        value={newMember.carrier_type}
                        onChange={(carrier_type) => setNewMember((member) => ({ ...member, carrier_type }))}
                        disabled={busy}
                      />
                    )}
                  </div>
                </td>
                <td />
                <td />
                <td>
                  <button type="button" className="button-secondary" onClick={addMember} disabled={busy}>
                    Add member
                  </button>
                </td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      {selectedMemberId && (
        <FamilyMemberDetailDialog
          familyId={familyId}
          family={family}
          memberId={selectedMemberId}
          draft={view}
          editing={editing}
          canEditMember={canEditMembers}
          canEditPhenotypes={canEditPhenotypes}
          onChange={change}
          onClose={() => setSelectedMemberId(null)}
        />
      )}
    </section>
  );
};

export default FamilyMembersCard;
