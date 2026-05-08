import React from 'react';
import { Link } from 'react-router-dom';
import api from '../../lib/api';
import type { FamilySummaryData, FamilyData, ProjectOption } from './dataManagementTypes';
import {
  FAMILY_TRACK_ORDER,
  SAMPLE_TRACK_ORDER,
  TRACK_LABELS,
  formatCount,
  phenotypeLabel,
  roleLabel,
} from './dataManagementTypes';

interface DataInventoryDetailProps {
  selectedFamilyId: string | null;
  selectedFamilySummary: FamilySummaryData | null;
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
}

const DataInventoryDetail: React.FC<DataInventoryDetailProps> = ({
  selectedFamilyId,
  selectedFamilySummary,
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
}) => {
  if (!selectedFamilyId) {
    return (
      <section className="surface-card admin-data-detail">
        <div className="page-state">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h2 className="page-state-title">No family selected</h2>
            <p className="page-state-copy">
              Choose a family from the catalog to inspect tracks, delete data, or manage project
              access.
            </p>
          </div>
        </div>
      </section>
    );
  }

  if (selectedFamilyLoading) {
    return (
      <section className="surface-card admin-data-detail">
        <div className="page-state">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h2 className="page-state-title">Loading family detail</h2>
            <p className="page-state-copy">
              Preparing track counts, samples, and project assignments for {selectedFamilyId}.
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

  return (
    <section className="surface-card admin-data-detail">
      <div className="space-y-8">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Selected Family</p>
            <h2 className="section-title !text-[1.9rem]">{selectedFamily.family_id}</h2>
            <p className="catalog-card-copy">
              Family-level operations remove shared small variants or the entire family record. The
              compact tables below keep access and track state in one place.
            </p>
          </div>
          <div className="inline-actions">
            {selectedFamilySummary && (
              <Link
                to={`/families/${selectedFamilySummary.family_id}`}
                className="button-secondary hover:no-underline"
              >
                Open family workspace
              </Link>
            )}
            {selectedFamily.track_counts.small_variants > 0 && (
              <button
                type="button"
                className="button-danger"
                disabled={busyKey === `family-small-variants:${selectedFamily.family_id}`}
                onClick={() =>
                    onRunAction(
                      `family-small-variants:${selectedFamily.family_id}`,
                      `Delete all small variants for family ${selectedFamily.family_id}?`,
                      () =>
                        api.delete(`/admin/data/families/${selectedFamily.family_id}/small_variants`, {
                          params: { confirm: true },
                        }),
                      `Deleted family small variants for ${selectedFamily.family_id}.`,
                    )
                  }
              >
                Delete family small variants
              </button>
            )}
            <button
              type="button"
              className="button-danger"
              disabled={busyKey === `family:${selectedFamily.family_id}`}
              onClick={() =>
                onRunAction(
                  `family:${selectedFamily.family_id}`,
                  `Delete family ${selectedFamily.family_id}, all sample records, and all linked assay data?`,
                  () =>
                    api.delete(`/admin/families/${selectedFamily.family_id}`, {
                      params: { confirm: true },
                    }),
                  `Deleted family ${selectedFamily.family_id} and all linked data.`,
                )
              }
            >
              Delete entire family
            </button>
          </div>
        </div>

        <section className="surface-card-muted admin-project-access-card">
          <div className="page-header">
            <div className="space-y-2">
              <p className="page-kicker">Project Access</p>
              <h3 className="section-title">Link family to projects</h3>
              <p className="catalog-card-copy">
                Project assignments here are inherited by every sample in the family. Use this
                section to review current access, adjust the draft selection, and save changes
                explicitly.
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

        <section className="space-y-4">
          <div className="page-header">
            <div className="space-y-2">
              <p className="page-kicker">Inventory</p>
              <h3 className="section-title">Family and sample counts</h3>
              <p className="catalog-card-copy">
                Track counts are shown here after project access is configured above.
              </p>
            </div>
          </div>

          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table admin-access-table">
              <thead>
                <tr>
                  <th>Scope</th>
                  <th>Project access</th>
                  {FAMILY_TRACK_ORDER.map((trackType) => (
                    <th key={trackType}>{TRACK_LABELS[trackType]}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="admin-scope-cell">
                    <strong>{selectedFamily.family_id}</strong>
                    <span>Family</span>
                  </td>
                  <td>
                    {assignedProjects.length ? (
                      <div className="admin-project-access-chip-list">
                        {assignedProjects.map((project) => (
                          <span key={project.id} className="badge-chip">
                            {project.name}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="table-empty">No linked projects</span>
                    )}
                  </td>
                  {FAMILY_TRACK_ORDER.map((trackType) => (
                    <td key={trackType} className="admin-count-cell">
                      {formatCount(selectedFamily.track_counts[trackType] ?? 0)}
                    </td>
                  ))}
                </tr>

                {selectedFamily.samples.map((sample) => {
                  return (
                    <tr key={sample.sample_id}>
                      <td className="admin-scope-cell">
                        <strong>{sample.sample_id}</strong>
                        <span>
                          {roleLabel(sample.role)} · {phenotypeLabel(sample.affected)}
                        </span>
                      </td>
                      <td>
                        <span className="catalog-card-copy">
                          Inherits project access from the family assignments above.
                        </span>
                      </td>
                      {FAMILY_TRACK_ORDER.map((trackType) => (
                        <td key={trackType} className="admin-count-cell">
                          {trackType === 'small_variants'
                            ? '—'
                            : formatCount(sample.track_counts[trackType] ?? 0)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="space-y-4">
          <div className="page-header">
            <div className="space-y-2">
              <p className="page-kicker">Samples</p>
              <h3 className="section-title">Delete sample-level tracks</h3>
            </div>
          </div>

          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table admin-sample-table">
              <thead>
                <tr>
                  <th>Sample</th>
                  <th>Context</th>
                  {SAMPLE_TRACK_ORDER.map((trackType) => (
                    <th key={trackType}>{TRACK_LABELS[trackType]}</th>
                  ))}
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {selectedFamily.samples.map((sample) => (
                  <tr key={sample.sample_id}>
                    <td className="admin-sample-id-cell">
                      <strong>{sample.sample_id}</strong>
                    </td>
                    <td className="admin-sample-context-cell">
                      {roleLabel(sample.role)} · {phenotypeLabel(sample.affected)} · {sample.sex}
                    </td>
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
                                  `Delete ${TRACK_LABELS[trackType].toLowerCase()} for sample ${sample.sample_id}?`,
                                  () =>
                                    api.delete(`/admin/data/samples/${sample.sample_id}/${trackType}`, {
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
                            `Delete sample ${sample.sample_id}, all of its tracks, and remove it from family ${selectedFamily.family_id}?`,
                            () =>
                              api.delete(`/admin/samples/${sample.sample_id}`, {
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
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </section>
  );
};

export default DataInventoryDetail;
