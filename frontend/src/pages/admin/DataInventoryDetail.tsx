import React from 'react';
import api from '../../lib/api';
import FamilyDetailPage from '../families/FamilyDetailPage';
import RawFileProvenanceTable from './RawFileProvenanceTable';
import type { FamilyData, ProjectOption } from './dataManagementTypes';
import {
  FAMILY_TRACK_ORDER,
  SAMPLE_TRACK_ORDER,
  TRACK_LABELS,
  phenotypeLabel,
  roleLabel,
} from './dataManagementTypes';
import { apiPath } from '../../lib/apiPath';
import { formatCount } from '../../lib/format';

interface DataInventoryDetailProps {
  selectedFamilyId: string;
  selectedFamily?: FamilyData;
  selectedFamilyLoading: boolean;
  selectedFamilyErrorMessage?: string | null;
  projects: ProjectOption[];
  familyProjectDrafts: Record<string, string[]>;
  busyKey: string | null;
  onRunAction: (
    key: string,
    confirmation: string,
    action: () => Promise<unknown>,
    successMessage: string,
  ) => void;
  onResetFamilyProjects: (familyId: string) => void;
  onSaveFamilyProjects: (familyId: string) => void;
  onToggleFamilyProject: (familyId: string, projectId: string) => void;
  onRequestDeleteFamily: () => void;
}

const DataInventoryDetail: React.FC<DataInventoryDetailProps> = ({
  selectedFamilyId,
  selectedFamily,
  selectedFamilyLoading,
  selectedFamilyErrorMessage,
  projects,
  familyProjectDrafts,
  busyKey,
  onRunAction,
  onResetFamilyProjects,
  onSaveFamilyProjects,
  onToggleFamilyProject,
  onRequestDeleteFamily,
}) => {
  if (selectedFamilyLoading) {
    return (
      <section className="surface-card admin-data-detail">
        <div className="page-state">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h2 className="page-state-title">Loading family detail</h2>
            <p className="page-state-copy">
              Preparing the workspace, track counts, and provenance for {selectedFamilyId}.
            </p>
          </div>
        </div>
      </section>
    );
  }

  if (!selectedFamily || selectedFamilyErrorMessage) {
    return (
      <section className="surface-card admin-data-detail">
        <div className="page-state">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h2 className="page-state-title">Could not load family detail</h2>
            <p className="page-state-copy">
              {selectedFamilyErrorMessage || 'The selected family detail could not be loaded.'}
            </p>
          </div>
        </div>
      </section>
    );
  }

  const normalizedDraftProjectIds = Array.from(
    new Set(familyProjectDrafts[selectedFamily.family_id] ?? selectedFamily.projects),
  ).sort((left, right) => left.localeCompare(right));
  const normalizedSavedProjectIds = Array.from(new Set(selectedFamily.projects)).sort((left, right) =>
    left.localeCompare(right),
  );
  const hasProjectDraftChanges =
    normalizedDraftProjectIds.length !== normalizedSavedProjectIds.length ||
    normalizedDraftProjectIds.some((projectId, index) => projectId !== normalizedSavedProjectIds[index]);
  const assignedProjects = projects.filter((project) => normalizedDraftProjectIds.includes(project.id));
  const unassignedProjectCount = projects.length - assignedProjects.length;

  const downloadPedigree = async () => {
    const response = await api.get(apiPath`/admin/families/${selectedFamily.family_id}/ped`, {
      responseType: 'blob',
    });
    const url = window.URL.createObjectURL(response.data as Blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${selectedFamily.family_id}.ped`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  };

  return (
    <section className="surface-card admin-data-detail">
      <div className="space-y-8">
        {/* Prominent destructive action at the very top of the overview. */}
        <div className="page-header admin-family-overview-header">
          <div className="space-y-2">
            <p className="page-kicker">Selected Family</p>
            <h2 className="section-title">{selectedFamily.family_id}</h2>
            <p className="catalog-card-copy">
              Manage this family directly below: edit members and structure, review
              statistics and samples, and trace every imported source file.
            </p>
          </div>
          <div className="inline-actions">
            <button type="button" className="button-secondary" onClick={downloadPedigree}>
              Download PED
            </button>
            <button type="button" className="button-danger" onClick={onRequestDeleteFamily}>
              Delete entire family
            </button>
          </div>
        </div>

        {/* Reused Family Workspace: members, relationships, phenotypes, carrier
            status, sample info, and click-to-edit member details. */}
        <section className="admin-embedded-workspace">
          <FamilyDetailPage familyId={selectedFamily.family_id} editable embedded />
        </section>

        {/* Project access — displayed once, at the family level. */}
        <section className="surface-card-muted admin-project-access-card">
          <div className="page-header">
            <div className="space-y-2">
              <p className="page-kicker">Project Access</p>
              <h3 className="section-title">Link family to projects</h3>
              <p className="catalog-card-copy">
                Project assignments here are inherited by every sample in the family and are
                shown once, at the family level.
              </p>
            </div>
            <div className="inline-actions">
              <span className={`badge-chip${hasProjectDraftChanges ? ' badge-chip--signature' : ''}`}>
                {hasProjectDraftChanges ? 'Unsaved changes' : 'Saved'}
              </span>
              <button
                type="button"
                className="button-secondary"
                disabled={!hasProjectDraftChanges}
                onClick={() => onResetFamilyProjects(selectedFamily.family_id)}
              >
                Reset draft
              </button>
              <button
                type="button"
                className="form-button"
                disabled={!hasProjectDraftChanges || busyKey === `family-projects:${selectedFamily.family_id}`}
                onClick={() => onSaveFamilyProjects(selectedFamily.family_id)}
              >
                {busyKey === `family-projects:${selectedFamily.family_id}`
                  ? 'Saving…'
                  : 'Save project access'}
              </button>
            </div>
          </div>

          {projects.length === 0 ? (
            <p className="section-copy">
              No projects are available yet. Create a project before linking this family.
            </p>
          ) : (
            <div className="admin-project-access-layout">
              <div className="admin-project-access-summary">
                <div className="admin-project-access-group">
                  <span className="admin-project-access-label">Assigned projects</span>
                  {assignedProjects.length ? (
                    <div className="admin-project-access-chip-list">
                      {assignedProjects.map((project) => (
                        <span key={project.id} className="badge-chip">
                          {project.name}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="table-empty">This family is not linked to any project yet.</p>
                  )}
                </div>
                <div className="admin-project-access-metrics">
                  <span className="analysis-count">{assignedProjects.length} linked</span>
                  <span className="analysis-count">{unassignedProjectCount} available</span>
                </div>
              </div>

              <div className="admin-project-access-group">
                <span className="admin-project-access-label">Available projects</span>
                <div className="admin-project-matrix admin-project-matrix--editor">
                  {projects.map((project) => {
                    const checked = normalizedDraftProjectIds.includes(project.id);
                    return (
                      <label
                        key={project.id}
                        className={`admin-project-chip admin-project-chip--toggle${
                          checked ? ' admin-project-chip--selected' : ''
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => onToggleFamilyProject(selectedFamily.family_id, project.id)}
                        />
                        <span>{project.name}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </section>

        {/* Consolidated family statistics + sample management. */}
        <section className="space-y-4">
          <div className="page-header">
            <div className="space-y-2">
              <p className="page-kicker">Inventory</p>
              <h3 className="section-title">Family statistics &amp; sample management</h3>
              <p className="catalog-card-copy">
                Family-wide counts followed by a single per-sample table that combines sample
                metadata, sample-level track inventory, and deletion actions.
              </p>
            </div>
          </div>

          <div className="admin-data-summary surface-card-muted" aria-label="Family statistics">
            <div className="admin-data-summary-item">
              <span className="admin-data-summary-label">Samples</span>
              <strong className="admin-data-summary-value">
                {formatCount(selectedFamily.sample_count)}
              </strong>
            </div>
            <div className="admin-data-summary-item">
              <span className="admin-data-summary-label">Total records</span>
              <strong className="admin-data-summary-value">
                {formatCount(selectedFamily.total_records)}
              </strong>
            </div>
            {FAMILY_TRACK_ORDER.map((trackType) => (
              <div key={trackType} className="admin-data-summary-item">
                <span className="admin-data-summary-label">{TRACK_LABELS[trackType]}</span>
                <strong className="admin-data-summary-value">
                  {formatCount(selectedFamily.track_counts[trackType] ?? 0)}
                </strong>
              </div>
            ))}
          </div>

          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table admin-sample-table">
              <thead>
                <tr>
                  <th>Sample</th>
                  <th>Individual</th>
                  <th>Sample type</th>
                  <th>Metadata</th>
                  {SAMPLE_TRACK_ORDER.map((trackType) => (
                    <th key={trackType}>{TRACK_LABELS[trackType]}</th>
                  ))}
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {selectedFamily.samples.length === 0 ? (
                  <tr>
                    <td colSpan={5 + SAMPLE_TRACK_ORDER.length}>
                      <p className="table-empty">This family has no samples.</p>
                    </td>
                  </tr>
                ) : (
                  selectedFamily.samples.map((sample) => (
                    <tr key={sample.sample_id}>
                      <td className="admin-sample-id-cell">
                        <strong>{sample.sample_id}</strong>
                      </td>
                      <td>{roleLabel(sample.role)}</td>
                      <td>{sample.sex || '—'}</td>
                      <td className="admin-sample-context-cell">{phenotypeLabel(sample.affected)}</td>
                      {SAMPLE_TRACK_ORDER.map((trackType) => {
                        const count = sample.track_counts[trackType] ?? 0;
                        const actionKey = `sample-track:${sample.sample_id}:${trackType}`;
                        return (
                          <td key={trackType}>
                            <div className="admin-track-inline">
                              <span className="admin-track-inline-count">{formatCount(count)}</span>
                              <button
                                type="button"
                                className="button-secondary admin-track-inline-action"
                                disabled={count === 0 || busyKey === actionKey}
                                onClick={() =>
                                  onRunAction(
                                    actionKey,
                                    `Delete ${TRACK_LABELS[trackType].toLowerCase()} for sample ${sample.sample_id}? This permanently removes the stored ${TRACK_LABELS[trackType].toLowerCase()} track for this sample.`,
                                    () =>
                                      api.delete(apiPath`/admin/data/samples/${sample.sample_id}/${trackType}`, {
                                        params: { confirm: true },
                                      }),
                                    `Deleted ${TRACK_LABELS[trackType].toLowerCase()} for sample ${sample.sample_id}.`,
                                  )
                                }
                              >
                                Delete
                              </button>
                            </div>
                          </td>
                        );
                      })}
                      <td>
                        <button
                          type="button"
                          className="button-danger admin-sample-delete-action"
                          disabled={
                            busyKey === `sample:${selectedFamily.family_id}:${sample.sample_id}`
                          }
                          onClick={() =>
                            onRunAction(
                              `sample:${selectedFamily.family_id}:${sample.sample_id}`,
                              `Delete sample ${sample.sample_id}, all of its tracks, and remove it from family ${selectedFamily.family_id}? This permanently deletes every track and variant record for this sample.`,
                              () =>
                                api.delete(apiPath`/admin/samples/${sample.sample_id}`, {
                                  params: { confirm: true },
                                }),
                              `Deleted sample ${sample.sample_id} and removed it from ${selectedFamily.family_id}.`,
                            )
                          }
                        >
                          Delete sample
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* Raw data provenance: every source file used to import this family. */}
        <RawFileProvenanceTable familyId={selectedFamily.family_id} />
      </div>
    </section>
  );
};

export default DataInventoryDetail;
