import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import { useProjectCatalog } from '../../lib/reference';
import {
  sortTagDefinitions,
  SYSTEM_TAG_GROUP_LABELS,
  type SmallVariantTagDefinition,
} from '../families/smallVariantSearch';
import { formatCount, type StatusTone } from './dataManagementTypes';

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (typeof error !== 'object' || error === null) return fallback;
  const responseDetail = (
    error as { response?: { data?: { detail?: string } } }
  )?.response?.data?.detail;
  return responseDetail || (error as { message?: string }).message || fallback;
};

type EditableTagDraft = {
  label: string;
  description: string;
  scope: 'project' | 'global';
  project_id: string;
  shared_project_ids: string[];
  group: SmallVariantTagDefinition['group'];
  color: string;
};

const EMPTY_NEW_TAG: EditableTagDraft = {
  label: '',
  description: '',
  scope: 'project',
  project_id: '',
  shared_project_ids: [],
  group: 'custom',
  color: '#5b6b79',
};

const AdminVariantTagsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: projects = [], isLoading: projectsLoading, error: projectsError } = useProjectCatalog();

  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [status, setStatus] = useState<{ tone: StatusTone; message: string } | null>(null);
  const [newTag, setNewTag] = useState<EditableTagDraft>(EMPTY_NEW_TAG);
  const [editingTagKey, setEditingTagKey] = useState<string | null>(null);
  const [editingTagDraft, setEditingTagDraft] = useState<EditableTagDraft>(EMPTY_NEW_TAG);

  useEffect(() => {
    if (!projects.length) {
      setSelectedProjectId(null);
      return;
    }
    if (selectedProjectId && !projects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(null);
    }
  }, [projects, selectedProjectId]);

  useEffect(() => {
    if (newTag.scope !== 'project' || newTag.project_id || !projects.length) {
      return;
    }
    setNewTag((current) => ({
      ...current,
      project_id: selectedProjectId || projects[0]?.id || '',
    }));
  }, [newTag.project_id, newTag.scope, projects, selectedProjectId]);

  const projectNameById = useMemo(
    () => new Map(projects.map((project) => [project.id, project.name])),
    [projects],
  );

  const {
    data: tags = [],
    isLoading: tagsLoading,
    error: tagsError,
  } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['admin', 'variant-tags', selectedProjectId || 'all'],
    queryFn: async () => {
      const response = await api.get('/admin/variant-tags', {
        params: selectedProjectId ? { project_id: selectedProjectId } : undefined,
      });
      return response.data as SmallVariantTagDefinition[];
    },
    retry: false,
  });

  const sortedTags = useMemo(() => sortTagDefinitions(tags), [tags]);
  const customTagCount = sortedTags.filter((tag) => tag.is_custom).length;

  const createTagMutation = useMutation({
    mutationFn: async () => {
      const response = await api.post(
        '/admin/variant-tags',
        {
          label: newTag.label.trim(),
          description: newTag.description.trim() || undefined,
          scope: newTag.scope,
          project_id: newTag.scope === 'project' ? newTag.project_id || undefined : undefined,
          shared_project_ids:
            newTag.scope === 'project'
              ? newTag.shared_project_ids.filter((projectId) => projectId !== newTag.project_id)
              : [],
          group: newTag.group,
          color: newTag.color,
        },
        {
          params: selectedProjectId ? { project_id: selectedProjectId } : undefined,
        },
      );
      return response.data as SmallVariantTagDefinition;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'variant-tags'] });
      setStatus({
        tone: 'success',
        message: `Created tag "${newTag.label.trim()}".`,
      });
      setNewTag((current) => ({
        ...EMPTY_NEW_TAG,
        project_id: current.scope === 'project' ? current.project_id : '',
      }));
    },
    onError: (error) => {
      setStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Could not create variant tag.'),
      });
    },
  });

  const updateTagMutation = useMutation({
    mutationFn: async () => {
      if (!editingTagKey) throw new Error('Tag context is required');
      const response = await api.put(
        `/admin/variant-tags/${editingTagKey}`,
        {
          label: editingTagDraft.label.trim(),
          description: editingTagDraft.description.trim() || null,
          scope: editingTagDraft.scope,
          project_id: editingTagDraft.scope === 'project' ? editingTagDraft.project_id || null : null,
          shared_project_ids:
            editingTagDraft.scope === 'project'
              ? editingTagDraft.shared_project_ids.filter(
                  (projectId) => projectId !== editingTagDraft.project_id,
                )
              : [],
          group: editingTagDraft.group,
          color: editingTagDraft.color,
        },
        {
          params: selectedProjectId ? { project_id: selectedProjectId } : undefined,
        },
      );
      return response.data as SmallVariantTagDefinition;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'variant-tags'] });
      setStatus({
        tone: 'success',
        message: `Updated tag "${editingTagDraft.label.trim()}".`,
      });
      setEditingTagKey(null);
    },
    onError: (error) => {
      setStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Could not update variant tag.'),
      });
    },
  });

  const deleteTagMutation = useMutation({
    mutationFn: async (tagKey: string) => {
      await api.delete(`/admin/variant-tags/${tagKey}`);
      return tagKey;
    },
    onSuccess: async (tagKey) => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'variant-tags'] });
      setStatus({
        tone: 'success',
        message: `Deleted tag "${tagKey}".`,
      });
      if (editingTagKey === tagKey) {
        setEditingTagKey(null);
      }
    },
    onError: (error) => {
      setStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Could not delete variant tag.'),
      });
    },
  });

  if (projectsLoading) {
    return (
      <PageState
        kicker="Administration"
        title="Loading variant tags"
        message="Preparing project context and tag definitions."
      />
    );
  }

  if (projectsError) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load projects"
        message={getErrorMessage(projectsError, 'The project catalog could not be loaded.')}
      />
    );
  }

  return (
    <div className="page-shell space-y-8">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Variant tag management</h1>
            <p className="catalog-card-copy">
              Define custom tags as global or project-scoped, then share project tags with one or more
              additional projects.
            </p>
          </div>
          <div className="compact-toolbar dashboard-toolbar">
            <Link to="/admin/data" className="button-secondary hover:no-underline">
              Data inventory
            </Link>
            <Link to="/admin/data/presets" className="button-secondary hover:no-underline">
              Preset filters
            </Link>
            <Link to="/admin/data/clickhouse" className="button-secondary hover:no-underline">
              ClickHouse tables
            </Link>
            <Link to="/admin/data/logs" className="button-secondary hover:no-underline">
              Audit logs
            </Link>
          </div>
        </div>
      </section>

      {status && (
        <div
          className={`status-note ${
            status.tone === 'error' ? 'status-note--error' : 'status-note--success'
          }`}
        >
          {status.message}
        </div>
      )}

      <div className="admin-data-layout">
        <aside className="surface-card admin-data-sidebar">
          <div className="space-y-2">
            <p className="page-kicker">Projects</p>
            <h2 className="section-title">Tag visibility context</h2>
          </div>
          <div className="admin-family-list">
            <button
              type="button"
              className={`admin-family-card${selectedProjectId === null ? ' admin-family-card--active' : ''}`}
              onClick={() => setSelectedProjectId(null)}
            >
              <div className="admin-family-card-header">
                <div>
                  <h3>All projects</h3>
                  <p>Global + project tags</p>
                </div>
              </div>
            </button>
            {projects.map((project) => (
              <button
                key={project.id}
                type="button"
                className={`admin-family-card${selectedProjectId === project.id ? ' admin-family-card--active' : ''}`}
                onClick={() => setSelectedProjectId(project.id)}
              >
                <div className="admin-family-card-header">
                  <div>
                    <h3>{project.name}</h3>
                    <p>{project.id}</p>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section className="surface-card admin-data-detail space-y-6">
          <div className="page-header">
            <div className="space-y-2">
              <p className="page-kicker">Selected Context</p>
              <h2 className="section-title !text-[1.9rem]">
                {selectedProjectId ? projectNameById.get(selectedProjectId) || selectedProjectId : 'All projects'}
              </h2>
              <p className="catalog-card-copy">
                {selectedProjectId
                  ? 'Showing global tags plus project tags visible in this project.'
                  : 'Showing all global tags and all project-scoped tags.'}
              </p>
            </div>
            <div className="admin-project-access-metrics">
              <span className="analysis-count">{formatCount(sortedTags.length)} total tags</span>
              <span className="analysis-count">{formatCount(customTagCount)} custom tags</span>
            </div>
          </div>

          <section className="surface-card-muted space-y-4">
            <div className="space-y-2">
              <h3 className="section-title">Create custom tag</h3>
              <p className="section-copy">
                Tags are managed at global/project scope only. Family assignment is not used here.
              </p>
            </div>
            <div className="field-grid">
              <label className="field-label">
                Label
                <input
                  type="text"
                  value={newTag.label}
                  onChange={(event) => setNewTag((current) => ({ ...current, label: event.target.value }))}
                  placeholder="Follow-up Sanger"
                />
              </label>
              <label className="field-label">
                Scope
                <select
                  value={newTag.scope}
                  onChange={(event) =>
                    setNewTag((current) => ({
                      ...current,
                      scope: event.target.value as 'project' | 'global',
                      project_id:
                        event.target.value === 'project'
                          ? current.project_id || selectedProjectId || ''
                          : '',
                      shared_project_ids: event.target.value === 'project' ? current.shared_project_ids : [],
                    }))
                  }
                >
                  <option value="project">Project</option>
                  <option value="global">Global</option>
                </select>
              </label>
              <label className="field-label">
                Primary project
                <select
                  value={newTag.project_id}
                  disabled={newTag.scope !== 'project'}
                  onChange={(event) =>
                    setNewTag((current) => ({
                      ...current,
                      project_id: event.target.value,
                      shared_project_ids: current.shared_project_ids.filter(
                        (projectId) => projectId !== event.target.value,
                      ),
                    }))
                  }
                >
                  <option value="">Select project</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-label">
                Group
                <select
                  value={newTag.group}
                  onChange={(event) =>
                    setNewTag((current) => ({
                      ...current,
                      group: event.target.value as SmallVariantTagDefinition['group'],
                    }))
                  }
                >
                  <option value="custom">Custom</option>
                  <option value="collaboration">Collaboration</option>
                  <option value="classification">Classification</option>
                </select>
              </label>
              <label className="field-label">
                Color
                <input
                  type="color"
                  value={newTag.color}
                  onChange={(event) => setNewTag((current) => ({ ...current, color: event.target.value }))}
                />
              </label>
              <label className="field-label">
                Description
                <input
                  type="text"
                  value={newTag.description}
                  onChange={(event) =>
                    setNewTag((current) => ({ ...current, description: event.target.value }))
                  }
                  placeholder="Used when validation is planned."
                />
              </label>
            </div>
            {newTag.scope === 'project' && (
              <div className="space-y-2">
                <p className="section-copy">Share with other projects (optional)</p>
                <div className="variant-checkbox-grid variant-checkbox-grid--small">
                  {projects
                    .filter((project) => project.id !== newTag.project_id)
                    .map((project) => {
                      const checked = newTag.shared_project_ids.includes(project.id);
                      return (
                        <label key={project.id} className="analysis-checkbox variant-compact-checkbox">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() =>
                              setNewTag((current) => ({
                                ...current,
                                shared_project_ids: checked
                                  ? current.shared_project_ids.filter((projectId) => projectId !== project.id)
                                  : [...current.shared_project_ids, project.id],
                              }))
                            }
                          />
                          {project.name}
                        </label>
                      );
                    })}
                </div>
              </div>
            )}
            <div className="inline-actions">
              <button
                type="button"
                disabled={
                  createTagMutation.isPending ||
                  !newTag.label.trim() ||
                  (newTag.scope === 'project' && !newTag.project_id)
                }
                onClick={() => {
                  setStatus(null);
                  createTagMutation.mutate();
                }}
              >
                {createTagMutation.isPending ? 'Creating…' : 'Create tag'}
              </button>
            </div>
          </section>

          <section className="space-y-4">
            <h3 className="section-title">Tag catalog</h3>
            {tagsLoading ? (
              <p className="table-empty">Loading tags…</p>
            ) : tagsError ? (
              <p className="table-empty">
                {getErrorMessage(tagsError, 'Could not load tags for this context.')}
              </p>
            ) : sortedTags.length === 0 ? (
              <p className="table-empty">No tags are available yet.</p>
            ) : (
              <div className="data-table-shell overflow-x-auto">
                <table className="analysis-table">
                  <thead>
                    <tr>
                      <th>Label</th>
                      <th>Key</th>
                      <th>Description</th>
                      <th>Group</th>
                      <th>Scope</th>
                      <th>Primary project</th>
                      <th>Shared with</th>
                      <th>Color</th>
                      <th>Type</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedTags.map((tag) => {
                      const isCustom = tag.is_custom;
                      const isEditing = editingTagKey === tag.key;
                      const isDeleteBusy =
                        deleteTagMutation.isPending && deleteTagMutation.variables === tag.key;
                      return (
                        <tr key={tag.key}>
                          <td>
                            {isEditing ? (
                              <input
                                type="text"
                                value={editingTagDraft.label}
                                onChange={(event) =>
                                  setEditingTagDraft((current) => ({
                                    ...current,
                                    label: event.target.value,
                                  }))
                                }
                              />
                            ) : (
                              <strong>{tag.label}</strong>
                            )}
                          </td>
                          <td>{tag.key}</td>
                          <td>
                            {isEditing ? (
                              <input
                                type="text"
                                value={editingTagDraft.description}
                                onChange={(event) =>
                                  setEditingTagDraft((current) => ({
                                    ...current,
                                    description: event.target.value,
                                  }))
                                }
                                placeholder="Optional description"
                              />
                            ) : (
                              tag.description || <span className="table-empty">—</span>
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              <select
                                value={editingTagDraft.group}
                                onChange={(event) =>
                                  setEditingTagDraft((current) => ({
                                    ...current,
                                    group: event.target.value as SmallVariantTagDefinition['group'],
                                  }))
                                }
                              >
                                <option value="custom">Custom</option>
                                <option value="collaboration">Collaboration</option>
                                <option value="classification">Classification</option>
                              </select>
                            ) : (
                              SYSTEM_TAG_GROUP_LABELS[tag.group]
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              <select
                                value={editingTagDraft.scope}
                                onChange={(event) =>
                                  setEditingTagDraft((current) => ({
                                    ...current,
                                    scope: event.target.value as 'project' | 'global',
                                    project_id:
                                      event.target.value === 'project'
                                        ? current.project_id || selectedProjectId || ''
                                        : '',
                                    shared_project_ids:
                                      event.target.value === 'project' ? current.shared_project_ids : [],
                                  }))
                                }
                              >
                                <option value="project">Project</option>
                                <option value="global">Global</option>
                              </select>
                            ) : (
                              tag.scope
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              editingTagDraft.scope === 'project' ? (
                                <select
                                  value={editingTagDraft.project_id}
                                  onChange={(event) =>
                                    setEditingTagDraft((current) => ({
                                      ...current,
                                      project_id: event.target.value,
                                      shared_project_ids: current.shared_project_ids.filter(
                                        (projectId) => projectId !== event.target.value,
                                      ),
                                    }))
                                  }
                                >
                                  <option value="">Select project</option>
                                  {projects.map((project) => (
                                    <option key={project.id} value={project.id}>
                                      {project.name}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <span className="table-empty">—</span>
                              )
                            ) : tag.scope === 'project' ? (
                              projectNameById.get(tag.project_id || '') || tag.project_id || (
                                <span className="table-empty">—</span>
                              )
                            ) : (
                              <span className="table-empty">—</span>
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              editingTagDraft.scope === 'project' ? (
                                <div className="variant-checkbox-grid variant-checkbox-grid--small">
                                  {projects
                                    .filter((project) => project.id !== editingTagDraft.project_id)
                                    .map((project) => {
                                      const checked =
                                        editingTagDraft.shared_project_ids.includes(project.id);
                                      return (
                                        <label
                                          key={project.id}
                                          className="analysis-checkbox variant-compact-checkbox"
                                        >
                                          <input
                                            type="checkbox"
                                            checked={checked}
                                            onChange={() =>
                                              setEditingTagDraft((current) => ({
                                                ...current,
                                                shared_project_ids: checked
                                                  ? current.shared_project_ids.filter(
                                                      (projectId) => projectId !== project.id,
                                                    )
                                                  : [...current.shared_project_ids, project.id],
                                              }))
                                            }
                                          />
                                          {project.name}
                                        </label>
                                      );
                                    })}
                                </div>
                              ) : (
                                <span className="table-empty">—</span>
                              )
                            ) : tag.shared_project_ids?.length ? (
                              tag.shared_project_ids
                                .map((projectId) => projectNameById.get(projectId) || projectId)
                                .join(', ')
                            ) : (
                              <span className="table-empty">—</span>
                            )}
                          </td>
                          <td>
                            {isEditing ? (
                              <div className="inline-actions">
                                <input
                                  type="color"
                                  value={editingTagDraft.color}
                                  onChange={(event) =>
                                    setEditingTagDraft((current) => ({
                                      ...current,
                                      color: event.target.value,
                                    }))
                                  }
                                />
                                <span>{editingTagDraft.color.toUpperCase()}</span>
                              </div>
                            ) : (
                              <span
                                className="table-chip"
                                style={{ backgroundColor: tag.color, color: '#ffffff' }}
                              >
                                {tag.color.toUpperCase()}
                              </span>
                            )}
                          </td>
                          <td>{isCustom ? 'Custom' : 'Built-in'}</td>
                          <td>
                            {isCustom ? (
                              <div className="inline-actions">
                                {isEditing ? (
                                  <>
                                    <button
                                      type="button"
                                      disabled={
                                        updateTagMutation.isPending ||
                                        !editingTagDraft.label.trim() ||
                                        (editingTagDraft.scope === 'project' &&
                                          !editingTagDraft.project_id)
                                      }
                                      onClick={() => {
                                        setStatus(null);
                                        updateTagMutation.mutate();
                                      }}
                                    >
                                      {updateTagMutation.isPending ? 'Saving…' : 'Save'}
                                    </button>
                                    <button
                                      type="button"
                                      className="button-secondary"
                                      disabled={updateTagMutation.isPending}
                                      onClick={() => setEditingTagKey(null)}
                                    >
                                      Cancel
                                    </button>
                                  </>
                                ) : (
                                  <>
                                    <button
                                      type="button"
                                      className="button-secondary"
                                      onClick={() => {
                                        setStatus(null);
                                        setEditingTagKey(tag.key);
                                        setEditingTagDraft({
                                          label: tag.label,
                                          description: tag.description || '',
                                          scope: tag.scope === 'project' ? 'project' : 'global',
                                          project_id: tag.project_id || selectedProjectId || '',
                                          shared_project_ids: tag.shared_project_ids || [],
                                          group: tag.group,
                                          color: tag.color,
                                        });
                                      }}
                                    >
                                      Edit
                                    </button>
                                    <button
                                      type="button"
                                      className="button-danger"
                                      disabled={isDeleteBusy}
                                      onClick={() => {
                                        if (!window.confirm(`Delete custom tag "${tag.label}"?`)) {
                                          return;
                                        }
                                        setStatus(null);
                                        deleteTagMutation.mutate(tag.key);
                                      }}
                                    >
                                      {isDeleteBusy ? 'Deleting…' : 'Delete'}
                                    </button>
                                  </>
                                )}
                              </div>
                            ) : (
                              <span className="table-empty">Not editable</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </section>
      </div>
    </div>
  );
};

export default AdminVariantTagsPage;
