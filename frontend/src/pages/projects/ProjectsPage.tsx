import React, { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import type {
  ApiAssemblyRecord,
  ApiFamilyBase,
  ApiFamilyMemberRef,
  ApiProjectRecord,
  ApiSpeciesRecord,
  ApiUserRecord,
} from '../../lib/apiTypes';
import { withEntityId } from '../../lib/entity';
import { isAdmin } from '../../lib/auth';

type Project = ApiProjectRecord<ApiFamilyBase<ApiFamilyMemberRef>>;
type Species = ApiSpeciesRecord;
type Assembly = ApiAssemblyRecord;
type User = ApiUserRecord;

interface ProjectFormState {
  name: string;
  description: string;
  speciesId: string;
  assemblyId: string;
  userIds: string[];
}

interface PageStatus {
  tone: 'success' | 'error';
  message: string;
}

const EMPTY_FORM: ProjectFormState = {
  name: '',
  description: '',
  speciesId: '',
  assemblyId: '',
  userIds: [],
};

const pluralize = (count: number, label: string) => `${count} ${label}${count === 1 ? '' : 's'}`;

const truncateText = (value?: string, maxLength = 110) => {
  const text = (value || '').trim();
  if (!text) return '—';
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 1).trimEnd()}…`;
};

const getProjectSampleCount = (project: Project) => {
  return new Set([
    ...(project.samples ?? []),
    ...project.families.flatMap((family) => family.members.map((member) => member.sample_id)),
  ]).size;
};

const getSpeciesLabel = (project: Project, speciesNameById: Map<string, string>) =>
  project.species_name || speciesNameById.get(project.species_id ?? '') || 'Not set';

const getAssemblyLabel = (project: Project, assemblyById: Map<string, Assembly>) => {
  if (project.assembly_name) {
    return `${project.assembly_name}${project.assembly_version ? ` ${project.assembly_version}` : ''}`;
  }
  const assembly = assemblyById.get(project.assembly_id ?? '');
  return assembly ? `${assembly.assembly_name} ${assembly.version}` : 'Reference not set';
};

const getUserLabel = (user: User) => {
  const displayName = `${user.first_name ?? ''} ${user.last_name ?? ''}`.trim();
  return displayName ? `${displayName} · ${user.email}` : user.email;
};

const getErrorMessage = (error: unknown, fallback: string) => {
  if (typeof error === 'object' && error && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === 'string') {
      return detail;
    }
  }
  return fallback;
};

const ProjectsPage: React.FC = () => {
  const userIsAdmin = isAdmin();
  const queryClient = useQueryClient();
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [createFormOpen, setCreateFormOpen] = useState(false);
  const [createForm, setCreateForm] = useState<ProjectFormState>(EMPTY_FORM);
  const [editForm, setEditForm] = useState<ProjectFormState>(EMPTY_FORM);
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState<PageStatus | null>(null);

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: async () => {
      const res = await api.get('/projects');
      return (res.data as any[]).map((project) => withEntityId(project)) as Project[];
    },
  });

  const { data: species = [] } = useQuery<Species[]>({
    queryKey: ['species'],
    queryFn: async () => {
      const res = await api.get('/species');
      return (res.data as any[]).map((entry) => withEntityId(entry)) as Species[];
    },
  });

  const { data: assemblies = [] } = useQuery<Assembly[]>({
    queryKey: ['assemblies', 'all'],
    queryFn: async () => {
      const res = await api.get('/assemblies');
      return (res.data as any[]).map((entry) => withEntityId(entry)) as Assembly[];
    },
  });

  const { data: users = [] } = useQuery<User[]>({
    queryKey: ['admin', 'users'],
    queryFn: async () => {
      const res = await api.get('/auth/users');
      return (res.data as any[]).map((entry) => withEntityId(entry)) as User[];
    },
    enabled: userIsAdmin,
  });

  const speciesNameById = useMemo(
    () => new Map(species.map((entry) => [entry.id, entry.name])),
    [species],
  );
  const assemblyById = useMemo(
    () => new Map(assemblies.map((entry) => [entry.id, entry])),
    [assemblies],
  );

  const filteredProjects = useMemo(() => {
    const term = searchQuery.trim().toLowerCase();
    if (!term) return projects;
    return projects.filter((project) => {
      const speciesLabel = getSpeciesLabel(project, speciesNameById);
      const assemblyLabel = getAssemblyLabel(project, assemblyById);
      const haystack = [
        project.name,
        project.description ?? '',
        speciesLabel,
        assemblyLabel,
        project.families.map((family) => family.family_id).join(' '),
        project.families
          .flatMap((family) => family.members.map((member) => member.sample_id))
          .join(' '),
      ]
        .join(' ')
        .toLowerCase();
      return haystack.includes(term);
    });
  }, [projects, searchQuery, speciesNameById, assemblyById]);

  useEffect(() => {
    if (filteredProjects.length === 0) {
      setSelectedProjectId(null);
      return;
    }
    if (!selectedProjectId || !filteredProjects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(filteredProjects[0].id);
    }
  }, [filteredProjects, selectedProjectId]);

  const selectedProject = useMemo(
    () => filteredProjects.find((project) => project.id === selectedProjectId) ?? null,
    [filteredProjects, selectedProjectId],
  );

  useEffect(() => {
    if (!selectedProject) {
      setEditForm(EMPTY_FORM);
      return;
    }
    setEditForm({
      name: selectedProject.name,
      description: selectedProject.description ?? '',
      speciesId: selectedProject.species_id ?? '',
      assemblyId: selectedProject.assembly_id ?? '',
      userIds: selectedProject.user_ids ?? [],
    });
  }, [selectedProject]);

  const createAssemblies = useMemo(
    () =>
      assemblies.filter((entry) => !createForm.speciesId || entry.species_id === createForm.speciesId),
    [assemblies, createForm.speciesId],
  );
  const editAssemblies = useMemo(
    () =>
      assemblies.filter((entry) => !editForm.speciesId || entry.species_id === editForm.speciesId),
    [assemblies, editForm.speciesId],
  );

  const projectTotals = useMemo(() => {
    const totalFamilies = projects.reduce((sum, project) => sum + project.families.length, 0);
    const totalSamples = projects.reduce((sum, project) => sum + getProjectSampleCount(project), 0);
    return {
      projects: projects.length,
      families: totalFamilies,
      samples: totalSamples,
    };
  }, [projects]);

  const refetchProjects = async () => {
    await queryClient.invalidateQueries({ queryKey: ['projects'] });
  };

  const toggleUserId = (
    setForm: React.Dispatch<React.SetStateAction<ProjectFormState>>,
    userId: string,
  ) => {
    setForm((current) => ({
      ...current,
      userIds: current.userIds.includes(userId)
        ? current.userIds.filter((value) => value !== userId)
        : [...current.userIds, userId],
    }));
  };

  const handleCreateProject = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy('create');
    setStatus(null);
    try {
      await api.post('/projects', {
        name: createForm.name,
        description: createForm.description,
        species_id: createForm.speciesId,
        assembly_id: createForm.assemblyId,
        user_ids: createForm.userIds,
      });
      setCreateForm(EMPTY_FORM);
      setCreateFormOpen(false);
      await refetchProjects();
      setStatus({ tone: 'success', message: 'Project created.' });
    } catch (error) {
      setStatus({ tone: 'error', message: getErrorMessage(error, 'Unable to create project.') });
    } finally {
      setBusy(null);
    }
  };

  const handleSaveProject = async () => {
    if (!selectedProject) return;
    setBusy(`save:${selectedProject.id}`);
    setStatus(null);
    try {
      await api.put(`/projects/${selectedProject.id}`, {
        name: editForm.name,
        description: editForm.description,
        species_id: editForm.speciesId,
        assembly_id: editForm.assemblyId,
        user_ids: editForm.userIds,
      });
      await refetchProjects();
      setStatus({ tone: 'success', message: `Saved ${selectedProject.name}.` });
    } catch (error) {
      setStatus({ tone: 'error', message: getErrorMessage(error, 'Unable to save project.') });
    } finally {
      setBusy(null);
    }
  };

  const handleDeleteProject = async (projectId: string) => {
    setBusy(`delete:${projectId}`);
    setStatus(null);
    try {
      await api.delete(`/projects/${projectId}`);
      await refetchProjects();
      setStatus({ tone: 'success', message: 'Project deleted.' });
    } catch (error) {
      setStatus({ tone: 'error', message: getErrorMessage(error, 'Unable to delete project.') });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page-shell project-page space-y-6">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Projects</h1>
            <p className="catalog-card-copy">
              Keep all project definitions in one searchable catalog, then open a selected project
              to review notes, linked families, reference context, and access.
            </p>
          </div>
          <div className="project-header-actions">
            <div className="variant-summary-row">
              <span className="badge-chip">Projects {projectTotals.projects}</span>
              <span className="badge-chip">Families {projectTotals.families}</span>
              <span className="badge-chip">Samples {projectTotals.samples}</span>
            </div>
            {userIsAdmin && (
              <button
                type="button"
                className={createFormOpen ? 'button-secondary' : 'form-button'}
                onClick={() => {
                  setCreateFormOpen((current) => !current);
                  setStatus(null);
                }}
              >
                {createFormOpen ? 'Close new project form' : 'Create new project'}
              </button>
            )}
          </div>
        </div>
      </section>

      {status && (
        <p className={status.tone === 'success' ? 'form-status' : 'status-note status-note--error'}>
          {status.message}
        </p>
      )}

      {userIsAdmin && createFormOpen && (
        <section className="surface-card-flat space-y-5">
          <div className="page-header">
            <div className="space-y-2">
              <p className="page-kicker">Create</p>
              <h2 className="section-title">New project</h2>
              <p className="catalog-card-copy">
                Add the project title, notes, organism, assembly, and initial user access in one
                step.
              </p>
            </div>
          </div>

          <form onSubmit={handleCreateProject} className="space-y-5">
            <div className="field-grid project-form-grid">
              <label className="field-label">
                Title
                <input
                  value={createForm.name}
                  onChange={(event) =>
                    setCreateForm((current) => ({ ...current, name: event.target.value }))
                  }
                  placeholder="Cancer Trio 2026"
                />
              </label>
              <label className="field-label">
                Notes
                <textarea
                  value={createForm.description}
                  onChange={(event) =>
                    setCreateForm((current) => ({
                      ...current,
                      description: event.target.value,
                    }))
                  }
                  placeholder="Short notes describing the cohort, purpose, or handoff context."
                />
              </label>
              <label className="field-label">
                Organism
                <select
                  value={createForm.speciesId}
                  onChange={(event) =>
                    setCreateForm((current) => ({
                      ...current,
                      speciesId: event.target.value,
                      assemblyId: '',
                    }))
                  }
                >
                  <option value="">Select organism</option>
                  {species.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-label">
                Assembly
                <select
                  value={createForm.assemblyId}
                  onChange={(event) =>
                    setCreateForm((current) => ({
                      ...current,
                      assemblyId: event.target.value,
                    }))
                  }
                  disabled={!createForm.speciesId}
                >
                  <option value="">Select assembly</option>
                  {createAssemblies.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.assembly_name} {entry.version}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {users.length > 0 && (
              <div className="space-y-3">
                <div className="space-y-1">
                  <p className="eyebrow-label">Initial access</p>
                  <p className="catalog-card-copy">
                    Leave this empty if the project should not start with a restricted user list.
                  </p>
                </div>
                <div className="table-checkbox-grid project-access-grid">
                  {users.map((user) => (
                    <label key={user.id} className="admin-project-chip">
                      <input
                        type="checkbox"
                        checked={createForm.userIds.includes(user.id)}
                        onChange={() => toggleUserId(setCreateForm, user.id)}
                      />
                      <span>{getUserLabel(user)}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            <div className="project-form-actions">
              <button type="submit" className="form-button" disabled={busy === 'create'}>
                {busy === 'create' ? 'Creating…' : 'Create project'}
              </button>
            </div>
          </form>
        </section>
      )}

      <section className="surface-card space-y-5">
        <div className="project-toolbar">
          <div className="space-y-2">
            <p className="page-kicker">Catalog</p>
            <h2 className="section-title">Existing projects</h2>
            <p className="catalog-card-copy">
              Select a row to open the project detail workspace below.
            </p>
          </div>
          <label className="field-label project-filter-field">
            Search projects
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search by project, family, sample, organism, or assembly"
            />
          </label>
        </div>

        <p className="project-filter-summary">
          Showing {filteredProjects.length} of {projects.length} projects.
        </p>

        {filteredProjects.length === 0 ? (
          <div className="page-state">
            <div className="space-y-2">
              <p className="page-kicker">Projects</p>
              <h2 className="page-state-title">
                {projects.length === 0 ? 'No projects yet' : 'No matching projects'}
              </h2>
              <p className="page-state-copy">
                {projects.length === 0
                  ? 'Create the first project to assign organism and assembly context for imported data.'
                  : 'Adjust the search to reopen a project in the detail workspace.'}
              </p>
            </div>
          </div>
        ) : (
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table project-catalog-table">
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Organism</th>
                  <th>Assembly</th>
                  <th>Families</th>
                  <th>Samples</th>
                  <th>Users</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {filteredProjects.map((project) => {
                  const isSelected = project.id === selectedProjectId;
                  return (
                    <tr
                      key={project.id}
                      className={`project-catalog-row${isSelected ? ' project-catalog-row--selected' : ''}`}
                    >
                      <td>
                        <button
                          type="button"
                          className="button-link project-catalog-select"
                          onClick={() => setSelectedProjectId(project.id)}
                        >
                          {project.name}
                        </button>
                        <div className="project-table-meta">
                          {project.families.map((family) => family.family_id).join(', ') || 'No linked families'}
                        </div>
                      </td>
                      <td>{getSpeciesLabel(project, speciesNameById)}</td>
                      <td>{getAssemblyLabel(project, assemblyById)}</td>
                      <td>{project.families.length}</td>
                      <td>{getProjectSampleCount(project)}</td>
                      <td>{project.user_ids?.length ?? 0}</td>
                      <td className="project-catalog-note-cell">{truncateText(project.description)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="surface-card space-y-6">
        {!selectedProject ? (
          <div className="page-state">
            <div className="space-y-2">
              <p className="page-kicker">Projects</p>
              <h2 className="page-state-title">No project selected</h2>
              <p className="page-state-copy">
                Select a project from the table to inspect its notes, linked families, and
                settings.
              </p>
            </div>
          </div>
        ) : (
          <>
            <div className="page-header">
              <div className="space-y-2">
                <p className="page-kicker">Selected project</p>
                <h2 className="section-title !text-[1.9rem]">{selectedProject.name}</h2>
                <p className="catalog-card-copy">
                  {getSpeciesLabel(selectedProject, speciesNameById)} •{' '}
                  {getAssemblyLabel(selectedProject, assemblyById)}
                </p>
              </div>
              <div className="project-header-actions">
                <span className="badge-chip">
                  {pluralize(selectedProject.families.length, 'family')} ·{' '}
                  {pluralize(getProjectSampleCount(selectedProject), 'sample')}
                </span>
                {userIsAdmin && (
                  <div className="inline-actions">
                    <button
                      type="button"
                      className="button-secondary"
                      disabled={busy === `save:${selectedProject.id}`}
                      onClick={handleSaveProject}
                    >
                      {busy === `save:${selectedProject.id}` ? 'Saving…' : 'Save settings'}
                    </button>
                    <button
                      type="button"
                      className="button-danger"
                      disabled={busy === `delete:${selectedProject.id}`}
                      onClick={() => handleDeleteProject(selectedProject.id)}
                    >
                      Delete project
                    </button>
                  </div>
                )}
              </div>
            </div>

            <div className="project-detail-layout">
              <div className="project-detail-main">
                <div className="surface-card-muted space-y-4">
                  <h3 className="section-title !text-[1.15rem]">Overview</h3>
                  <div className="project-stat-grid">
                    <div className="project-stat-card">
                      <span className="stat-label">Families</span>
                      <strong className="family-workspace-stat-value">
                        {selectedProject.families.length}
                      </strong>
                    </div>
                    <div className="project-stat-card">
                      <span className="stat-label">Samples</span>
                      <strong className="family-workspace-stat-value">
                        {getProjectSampleCount(selectedProject)}
                      </strong>
                    </div>
                    <div className="project-stat-card">
                      <span className="stat-label">Organism</span>
                      <strong className="family-workspace-stat-copy">
                        {getSpeciesLabel(selectedProject, speciesNameById)}
                      </strong>
                    </div>
                    <div className="project-stat-card">
                      <span className="stat-label">Assembly</span>
                      <strong className="family-workspace-stat-copy">
                        {getAssemblyLabel(selectedProject, assemblyById)}
                      </strong>
                    </div>
                    <div className="project-stat-card">
                      <span className="stat-label">Restricted users</span>
                      <strong className="family-workspace-stat-value">
                        {selectedProject.user_ids?.length ?? 0}
                      </strong>
                    </div>
                  </div>
                </div>

                <div className="surface-card-muted space-y-4">
                  <h3 className="section-title !text-[1.15rem]">Notes</h3>
                  <p className="project-note-block">
                    {selectedProject.description?.trim() || 'No project notes have been added yet.'}
                  </p>
                </div>

                <div className="surface-card-muted space-y-4">
                  <h3 className="section-title !text-[1.15rem]">Linked families</h3>
                  {selectedProject.families.length ? (
                    <div className="data-table-shell overflow-x-auto">
                      <table className="analysis-table">
                        <thead>
                          <tr>
                            <th>Family</th>
                            <th>Samples</th>
                            <th>Sample IDs</th>
                          </tr>
                        </thead>
                        <tbody>
                          {selectedProject.families.map((family) => (
                            <tr key={family.family_id}>
                              <td>{family.family_id}</td>
                              <td>{family.members.length}</td>
                              <td>
                                <div className="project-family-samples">
                                  {family.members.map((member) => (
                                    <span key={`${family.family_id}:${member.sample_id}`} className="badge-chip">
                                      {member.sample_id}
                                    </span>
                                  ))}
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="table-empty">No families are currently linked to this project.</p>
                  )}
                </div>
              </div>

              <div className="project-detail-side">
                {userIsAdmin && (
                  <div className="surface-card-muted space-y-4">
                    <h3 className="section-title !text-[1.15rem]">Settings</h3>
                    <div className="field-grid project-form-grid">
                      <label className="field-label">
                        Title
                        <input
                          value={editForm.name}
                          onChange={(event) =>
                            setEditForm((current) => ({ ...current, name: event.target.value }))
                          }
                        />
                      </label>
                      <label className="field-label">
                        Notes
                        <textarea
                          value={editForm.description}
                          onChange={(event) =>
                            setEditForm((current) => ({
                              ...current,
                              description: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="field-label">
                        Organism
                        <select
                          value={editForm.speciesId}
                          onChange={(event) =>
                            setEditForm((current) => ({
                              ...current,
                              speciesId: event.target.value,
                              assemblyId: '',
                            }))
                          }
                        >
                          <option value="">Select organism</option>
                          {species.map((entry) => (
                            <option key={entry.id} value={entry.id}>
                              {entry.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field-label">
                        Assembly
                        <select
                          value={editForm.assemblyId}
                          onChange={(event) =>
                            setEditForm((current) => ({
                              ...current,
                              assemblyId: event.target.value,
                            }))
                          }
                          disabled={!editForm.speciesId}
                        >
                          <option value="">Select assembly</option>
                          {editAssemblies.map((entry) => (
                            <option key={entry.id} value={entry.id}>
                              {entry.assembly_name} {entry.version}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  </div>
                )}

                <div className="surface-card-muted space-y-4">
                  <h3 className="section-title !text-[1.15rem]">User access</h3>
                  {userIsAdmin ? (
                    users.length > 0 ? (
                      <div className="table-checkbox-grid project-access-grid">
                        {users.map((user) => {
                          const checked = editForm.userIds.includes(user.id);
                          return (
                            <label key={user.id} className="admin-project-chip">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleUserId(setEditForm, user.id)}
                              />
                              <span>{getUserLabel(user)}</span>
                            </label>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="table-empty">No users are available for project assignment.</p>
                    )
                  ) : (
                    <p className="catalog-card-copy">
                      {(selectedProject.user_ids ?? []).length > 0
                        ? 'This project has restricted user access configured by an administrator.'
                        : 'No explicit user list is shown in viewer mode.'}
                    </p>
                  )}
                </div>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
};

export default ProjectsPage;
