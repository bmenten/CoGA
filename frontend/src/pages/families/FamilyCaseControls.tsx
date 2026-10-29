import React, { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { raw, routePath } from '../../lib/routePath';
import type { ApiFamilyRecord, ApiUserRef } from '../../lib/apiTypes';
import type { FamilyStatusOut } from '../../lib/apiSchema.generated';
import QueryFailure from '../../components/QueryFailure';
import { formatUserRef } from '../../lib/users';
import { NOT_ASSIGNABLE_HINT, NOT_ASSIGNABLE_MARK, READ_ONLY_HINT } from '../../lib/familyAccess';
import { getErrorMessage } from '../../lib/errorMessage';
import VariantWorkspaceLink from './VariantWorkspaceLink';
import type { FamilyDataPresence } from './familyDataPresence';

/**
 * The case's workflow fields, in the top card of every family page: the status, who it is assigned
 * to and who reviewed it, each saved as it is picked. A reader in the family's projects changes
 * nothing (SEC-1): the server refuses it, and the controls say so.
 */
const FamilyCaseControls: React.FC<{ familyId: string }> = ({ familyId }) => {
  const queryClient = useQueryClient();
  // The family record every family page reads under this key.
  const { data: family } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    queryFn: async () => (await api.get(apiPath`/families/${familyId}`)).data as ApiFamilyRecord,
  });
  const readOnly = Boolean(family?.read_only);

  const [statusDraft, setStatusDraft] = useState('');
  const [assignedToDraft, setAssignedToDraft] = useState('');
  const [reviewedByDraft, setReviewedByDraft] = useState('');
  const [metadataBusy, setMetadataBusy] = useState(false);
  const [metadataStatus, setMetadataStatus] = useState<{
    tone: 'success' | 'error';
    message: string;
  } | null>(null);

  // The active status catalogue powers the status picker: a small, app-wide lookup, cached by a
  // stable key and shared across family pages.
  const {
    data: familyStatusOptions = [],
    isError: statusesFailed,
    refetch: refetchStatuses,
  } = useQuery<FamilyStatusOut[]>({
    queryKey: ['family-statuses'],
    queryFn: async () => (await api.get('/family-statuses')).data as FamilyStatusOut[],
    // On every family page's header now: an answer that is no list reads as an empty one rather
    // than taking the page down.
    select: (statuses) => (Array.isArray(statuses) ? statuses : []),
  });

  // The assignee and reviewer pickers offer the family's own list: the active analysts of its
  // projects and the active administrators, never a reader (SEC-1).
  const {
    data: assignableUsers = [],
    isError: usersFailed,
    isSuccess: usersListed,
    refetch: refetchUsers,
  } = useQuery<ApiUserRef[]>({
    queryKey: ['family', familyId, 'assignable-users'],
    enabled: Boolean(familyId),
    queryFn: async () => (await api.get(apiPath`/families/${familyId}/assignable-users`)).data as ApiUserRef[],
    select: (users) => (Array.isArray(users) ? users : []),
  });
  // A stored assignee or reviewer the list no longer holds (a reader now, say) stays shown,
  // marked, until it is changed. Only once the list has loaded: a list that failed says nothing
  // about who is on it.
  const notAssignable = (userId: string) =>
    usersListed && Boolean(userId) && !assignableUsers.some((candidate) => candidate.id === userId);

  useEffect(() => {
    setStatusDraft(family?.status?.key ?? '');
    setAssignedToDraft(family?.assigned_to?.id ?? '');
    setReviewedByDraft(family?.reviewed_by?.id ?? '');
    setMetadataStatus(null);
  }, [family?.status?.key, family?.assigned_to?.id, family?.reviewed_by?.id]);

  // Each picker auto-saves the single field it changed (the backend applies a partial update).
  // On failure the pickers roll back to the stored values.
  const saveMetadata = async (patch: {
    status_key?: string | null;
    assigned_to?: string | null;
    reviewed_by?: string | null;
  }) => {
    setMetadataBusy(true);
    setMetadataStatus(null);
    try {
      const response = await api.put(apiPath`/families/${familyId}/metadata`, patch);
      queryClient.setQueryData(['family', familyId], response.data as ApiFamilyRecord);
      // The dashboard's nested table is fed by the project catalog (['projects']), while the
      // unassigned-families table uses ['families']: refresh both.
      await queryClient.invalidateQueries({ queryKey: ['families'] });
      await queryClient.invalidateQueries({ queryKey: ['projects'] });
    } catch (error) {
      setStatusDraft(family?.status?.key ?? '');
      setAssignedToDraft(family?.assigned_to?.id ?? '');
      setReviewedByDraft(family?.reviewed_by?.id ?? '');
      setMetadataStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to update the family status.'),
      });
    } finally {
      setMetadataBusy(false);
    }
  };

  return (
    <>
      <div className="family-workspace-meta-row">
        <div className="family-workspace-meta-field">
          <span className="stat-label">Status</span>
          <select
            className="family-workspace-select"
            aria-label="Family status"
            value={statusDraft}
            onChange={(event) => {
              const value = event.target.value;
              setStatusDraft(value);
              saveMetadata({ status_key: value || null });
            }}
            disabled={readOnly || metadataBusy || statusesFailed}
            title={readOnly ? READ_ONLY_HINT : undefined}
          >
            <option value="">No status</option>
            {familyStatusOptions.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
            {/* The current status stays shown when its list failed: it read "No
                status" on a reviewed case (#607). */}
            {statusDraft && !familyStatusOptions.some((option) => option.key === statusDraft) ? (
              <option value={statusDraft}>
                {family?.status?.key === statusDraft ? family?.status.label : statusDraft}
              </option>
            ) : null}
          </select>
        </div>
        <div className="family-workspace-meta-field">
          <span className="stat-label">Assigned to</span>
          <select
            className="family-workspace-select"
            aria-label="Assigned to"
            value={assignedToDraft}
            onChange={(event) => {
              const value = event.target.value;
              setAssignedToDraft(value);
              saveMetadata({ assigned_to: value || null });
            }}
            disabled={readOnly || metadataBusy || usersFailed}
            title={readOnly ? READ_ONLY_HINT : notAssignable(assignedToDraft) ? NOT_ASSIGNABLE_HINT : undefined}
          >
            <option value="">Unassigned</option>
            {assignableUsers.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {formatUserRef(candidate)}
              </option>
            ))}
            {assignedToDraft && !assignableUsers.some((candidate) => candidate.id === assignedToDraft) ? (
              <option value={assignedToDraft}>
                {family?.assigned_to?.id === assignedToDraft ? formatUserRef(family?.assigned_to) : assignedToDraft}
                {notAssignable(assignedToDraft) ? NOT_ASSIGNABLE_MARK : ''}
              </option>
            ) : null}
          </select>
        </div>
        <div className="family-workspace-meta-field">
          <span className="stat-label">Reviewed by</span>
          <select
            className="family-workspace-select"
            aria-label="Reviewed by"
            value={reviewedByDraft}
            onChange={(event) => {
              const value = event.target.value;
              setReviewedByDraft(value);
              saveMetadata({ reviewed_by: value || null });
            }}
            disabled={readOnly || metadataBusy || usersFailed}
            title={readOnly ? READ_ONLY_HINT : notAssignable(reviewedByDraft) ? NOT_ASSIGNABLE_HINT : undefined}
          >
            <option value="">Not reviewed</option>
            {assignableUsers.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {formatUserRef(candidate)}
              </option>
            ))}
            {reviewedByDraft && !assignableUsers.some((candidate) => candidate.id === reviewedByDraft) ? (
              <option value={reviewedByDraft}>
                {family?.reviewed_by?.id === reviewedByDraft ? formatUserRef(family?.reviewed_by) : reviewedByDraft}
                {notAssignable(reviewedByDraft) ? NOT_ASSIGNABLE_MARK : ''}
              </option>
            ) : null}
          </select>
        </div>
        {(metadataBusy || metadataStatus) && (
          <span
            className={`family-workspace-metadata-status${
              metadataStatus?.tone === 'error' ? ' family-workspace-metadata-status--error' : ''
            }`}
            role="status"
          >
            {metadataBusy ? 'Saving…' : metadataStatus?.message}
          </span>
        )}
      </div>
      {statusesFailed || usersFailed ? (
        <QueryFailure
          what={statusesFailed && usersFailed ? 'the status and user lists' : statusesFailed ? 'the status list' : 'the user list'}
          onRetry={() => {
            if (statusesFailed) void refetchStatuses();
            if (usersFailed) void refetchUsers();
          }}
          consequence="The current values are shown, and cannot be changed until it loads."
        />
      ) : null}
    </>
  );
};

/**
 * The family-level functions, at the right of the title on every family page rather than among the
 * analyses: the sample-integrity QC of the members and the report drawn from the analyses. Each
 * appears once its data is known to exist: Sample QC reads the small-variant genotypes; the report
 * lists every kind of finding that can be marked Report, repeat loci, Paraphase regions and mtDNA
 * variants included.
 */
export const FamilyCaseActions: React.FC<{
  familyId: string;
  projectId?: string | null;
  presence: FamilyDataPresence;
}> = ({ familyId, projectId, presence }) => {
  const projectSuffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  return presence.hasAnyVariantData ? (
    <div className="family-workspace-actions" role="group" aria-label="Family actions">
      <VariantWorkspaceLink
        active={presence.hasSmallVariants}
        to={routePath`/families/${familyId}/qc`}
        className="button-secondary family-workspace-action"
      >
        Sample QC
      </VariantWorkspaceLink>
      <VariantWorkspaceLink
        active={presence.hasAnyVariantData}
        to={routePath`/families/${familyId}/report${raw(projectSuffix)}`}
        className="button-secondary family-workspace-action"
      >
        Report
      </VariantWorkspaceLink>
    </div>
  ) : null;
};

export default FamilyCaseControls;
