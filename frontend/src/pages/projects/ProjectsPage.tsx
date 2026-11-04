import React, { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import type { ApiProjectRecord } from '../../lib/apiTypes';
import type {
  AssemblyOut,
  ProjectDashboardOut,
  SpeciesOut,
  UserRead,
} from '../../lib/apiSchema.generated';
import { withEntityId } from '../../lib/entity';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatShortDate } from '../../lib/format';
import { apiPath } from '../../lib/apiPath';
import { userAccountsQuery } from '../../lib/users';
import { memberAccessLevel, type ProjectAccessLevel } from '../../lib/familyAccess';
import ConfirmDeleteDialog from '../../components/ConfirmDeleteDialog';
import PageHeader from '../../components/PageHeader';
import QueryFailure from '../../components/QueryFailure';

// Each served under its `_id`, which withEntityId copies to `id`.
type Project = ApiProjectRecord;
type Species = SpeciesOut & { id: string };
type Assembly = AssemblyOut & { id: string };
type User = UserRead;

interface ProjectFormState {
  name: string;
  description: string;
  speciesId: string;
  assemblyId: string;
  userIds: string[];
  // The members among userIds who are readers; the others are analysts (SEC-1).
  readerIds: string[];
}

interface PageStatus {
  tone: 'success' | 'error';
  message: string;
}

type FamilySortKey = 'family_id' | 'created_at' | 'samples' | 'sample_ids';

const EMPTY_FORM: ProjectFormState = {
  name: '',
  description: '',
  speciesId: '',
  assemblyId: '',
  userIds: [],
  readerIds: [],
};

const pluralize = (count: number, singular: string, plural = `${singular}s`) =>
  `${count} ${count === 1 ? singular : plural}`;

const truncateText = (value?: string | null, maxLength = 110) => {
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

// "3", or "3 (1 reader)" when some members only read the project's records.
const memberCountLabel = (project: Project) => {
  const members = project.user_ids?.length ?? 0;
  const readers = project.reader_user_ids?.length ?? 0;
  return readers ? `${members} (${pluralize(readers, 'reader')})` : `${members}`;
};

// What the project body says of a member's level.
const memberLevels = (form: ProjectFormState) => ({
  user_ids: form.userIds,
  reader_user_ids: form.readerIds.filter((userId) => form.userIds.includes(userId)),
});

/** How many users without access the member picker lists at once; a search finds the others. */
const MEMBER_CANDIDATE_LIMIT = 8;

const byUserLabel = (left: User, right: User) => getUserLabel(left).localeCompare(getUserLabel(right));

/**
 * Who may open the project, and what each member may do in it (SEC-1): an analyst reads and
 * changes its records, a reader only reads them. A superuser or an administrator has every right
 * in every project and needs no membership. The members are a table; a user is added from a
 * search that lists a few users without access at a time, so the section stays short however
 * many accounts there are.
 */
const ProjectMemberPicker: React.FC<{
  users: User[];
  form: ProjectFormState;
  onToggle: (userId: string) => void;
  onLevel: (userId: string, level: ProjectAccessLevel) => void;
}> = ({ users, form, onToggle, onLevel }) => {
  const [search, setSearch] = useState('');
  const query = search.trim().toLowerCase();
  const members = users.filter((user) => form.userIds.includes(user.id)).sort(byUserLabel);
  const others = users.filter((user) => !form.userIds.includes(user.id));
  const matches = (
    query ? others.filter((user) => getUserLabel(user).toLowerCase().includes(query)) : others
  ).sort(byUserLabel);
  const shown = matches.slice(0, MEMBER_CANDIDATE_LIMIT);

  return (
    <div className="project-member-picker">
      {members.length > 0 ? (
        <div className="data-table-shell data-table-shell--fit">
          <table className="analysis-table project-member-table">
            <thead>
              <tr>
                <th scope="col">Member</th>
                <th scope="col">Access</th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {members.map((user) => (
                <tr key={user.id}>
                  <td>{getUserLabel(user)}</td>
                  <td>
                    <select
                      aria-label={`Access level of ${user.email}`}
                      value={memberAccessLevel(form.readerIds, user.id)}
                      onChange={(event) => onLevel(user.id, event.target.value as ProjectAccessLevel)}
                    >
                      <option value="analyst">Analyst (read and change)</option>
                      <option value="reader">Reader (read only)</option>
                    </select>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="button-ghost project-member-action"
                      aria-label={`Remove ${getUserLabel(user)}`}
                      onClick={() => onToggle(user.id)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : form.userIds.length === 0 ? (
        <p className="table-empty">
          No members yet: only superusers and administrators can open the project.
        </p>
      ) : null}

      {others.length > 0 ? (
        <div className="project-member-add">
          <label className="field-label project-member-search">
            Add a member
            <input
              type="search"
              value={search}
              placeholder="Search by name or email"
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                // Enter adds the one user the search found; it never submits the form around it.
                if (event.key !== 'Enter') return;
                event.preventDefault();
                if (query && matches.length === 1) {
                  onToggle(matches[0].id);
                  setSearch('');
                }
              }}
            />
          </label>
          {shown.length > 0 ? (
            <ul className="project-member-candidates">
              {shown.map((user) => (
                <li key={user.id}>
                  <span>{getUserLabel(user)}</span>
                  <button
                    type="button"
                    className="button-secondary project-member-action"
                    aria-label={`Add ${getUserLabel(user)}`}
                    onClick={() => onToggle(user.id)}
                  >
                    Add
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="table-empty">No user without access matches &ldquo;{search.trim()}&rdquo;.</p>
          )}
          {matches.length > shown.length ? (
            <p className="table-subtle">
              {shown.length} of {matches.length} shown: search by name or email for the others.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="table-subtle">Every user is a member.</p>
      )}
    </div>
  );
};

/**
 * What deleting `project` takes with it, for the confirmation (UI-7). The delete cascades to the
 * project's links: its families and samples stay but lose their link to it, its members the
 * access it gave them, and the review tags defined for it go. A family in no other project is
 * then open to administrators only. `projects` is every project: the page is an administrator's.
 */
const projectDeletionEffects = (project: Project, projects: Project[]): string[] => {
  const families = project.families.length;
  const elsewhere = new Set(
    projects
      .filter((other) => other.id !== project.id)
      .flatMap((other) => other.families.map((family) => family.family_id)),
  );
  const orphans = project.families.filter((family) => !elsewhere.has(family.family_id)).length;
  const members = project.user_ids?.length ?? 0;
  return [
    families === 0
      ? 'No family is linked to it.'
      : `${pluralize(families, 'family', 'families')}, with ${pluralize(getProjectSampleCount(project), 'sample')}, ${
          families === 1 ? 'loses its' : 'lose their'
        } link to it.`,
    ...(orphans > 0
      ? [
          `${pluralize(orphans, 'family', 'families')} of these ${orphans === 1 ? 'is' : 'are'} in no other project: after the deletion only administrators can open ${
            orphans === 1 ? 'it' : 'them'
          }.`,
        ]
      : []),
    ...(members > 0
      ? [`${pluralize(members, 'member')} ${members === 1 ? 'loses' : 'lose'} the access the project gave them.`]
      : []),
    'Review tags defined for this project are deleted with it.',
  ];
};

const uploadedDateMs = (family: Project['families'][number]) => {
  const timestamp = family.created_at ? Date.parse(family.created_at) : 0;
  return Number.isFinite(timestamp) ? timestamp : 0;
};

const familySampleIds = (family: Project['families'][number]) =>
  family.members.map((member) => member.sample_id);

// Mounted only behind RequireAdmin (/admin/access/projects and /projects in index.tsx), so
// every control is an administrator's.
const ProjectsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [createFormOpen, setCreateFormOpen] = useState(false);
  const [createForm, setCreateForm] = useState<ProjectFormState>(EMPTY_FORM);
  const [editForm, setEditForm] = useState<ProjectFormState>(EMPTY_FORM);
  const [busy, setBusy] = useState<string | null>(null);
  // The project whose deletion awaits confirmation (UI-7), read from the current list.
  const [projectToDeleteId, setProjectToDeleteId] = useState<string | null>(null);
  const [status, setStatus] = useState<PageStatus | null>(null);
  const [familySortKey, setFamilySortKey] = useState<FamilySortKey>('created_at');
  const [familySortAsc, setFamilySortAsc] = useState(false);

  const {
    data: projects = [],
    isError: projectsFailed,
    error: projectsError,
    refetch: retryProjects,
  } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: async () => {
      const res = await api.get('/projects');
      return (res.data as ProjectDashboardOut[]).map((project) => withEntityId(project));
    },
  });

  const { data: species = [] } = useQuery<Species[]>({
    queryKey: ['species'],
    queryFn: async () => {
      const res = await api.get('/species');
      return (res.data as SpeciesOut[]).map((entry) => withEntityId(entry));
    },
  });

  const { data: assemblies = [] } = useQuery<Assembly[]>({
    queryKey: ['assemblies', 'all'],
    queryFn: async () => {
      const res = await api.get('/assemblies');
      return (res.data as AssemblyOut[]).map((entry) => withEntityId(entry));
    },
  });

  const { data: users = [] } = useQuery(userAccountsQuery);

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

  const sortedSelectedFamilies = useMemo(() => {
    if (!selectedProject) return [];
    const direction = familySortAsc ? 1 : -1;
    return [...selectedProject.families].sort((left, right) => {
      let delta = 0;
      if (familySortKey === 'created_at') {
        delta = uploadedDateMs(left) - uploadedDateMs(right);
      } else if (familySortKey === 'samples') {
        delta = left.members.length - right.members.length;
      } else if (familySortKey === 'sample_ids') {
        delta = familySampleIds(left).join(' ').localeCompare(
          familySampleIds(right).join(' '),
          undefined,
          { numeric: true, sensitivity: 'base' },
        );
      } else {
        delta = left.family_id.localeCompare(right.family_id, undefined, {
          numeric: true,
          sensitivity: 'base',
        });
      }
      if (delta !== 0) return delta * direction;
      return left.family_id.localeCompare(right.family_id, undefined, {
        numeric: true,
        sensitivity: 'base',
      });
    });
  }, [familySortAsc, familySortKey, selectedProject]);

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
      readerIds: selectedProject.reader_user_ids ?? [],
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

  // Unknown, not zero, while the project list could not be loaded (#610).
  const projectTotals = useMemo(() => {
    if (projectsFailed) return { projects: '—', families: '—', samples: '—' };
    const totalFamilies = projects.reduce((sum, project) => sum + project.families.length, 0);
    const totalSamples = projects.reduce((sum, project) => sum + getProjectSampleCount(project), 0);
    return {
      projects: projects.length,
      families: totalFamilies,
      samples: totalSamples,
    };
  }, [projects, projectsFailed]);

  // The Users page lists each account's projects, and their names, from caches of its own:
  // without these, a saved change of access showed there only once they went stale, up to
  // 30 s later (ENG-10).
  const refetchProjects = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['projects'] }),
      queryClient.invalidateQueries({ queryKey: userAccountsQuery.queryKey }),
      queryClient.invalidateQueries({ queryKey: ['admin', 'projects'] }),
    ]);
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
      // A member removed is no reader; one added starts as an analyst.
      readerIds: current.readerIds.filter((value) => value !== userId),
    }));
  };

  const setUserLevel = (
    setForm: React.Dispatch<React.SetStateAction<ProjectFormState>>,
    userId: string,
    level: ProjectAccessLevel,
  ) => {
    setForm((current) => ({
      ...current,
      readerIds:
        level === 'reader'
          ? Array.from(new Set([...current.readerIds, userId]))
          : current.readerIds.filter((value) => value !== userId),
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
        ...memberLevels(createForm),
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
      await api.put(apiPath`/projects/${selectedProject.id}`, {
        name: editForm.name,
        description: editForm.description,
        species_id: editForm.speciesId,
        assembly_id: editForm.assemblyId,
        ...memberLevels(editForm),
      });
      await refetchProjects();
      setStatus({ tone: 'success', message: `Saved ${selectedProject.name}.` });
    } catch (error) {
      setStatus({ tone: 'error', message: getErrorMessage(error, 'Unable to save project.') });
    } finally {
      setBusy(null);
    }
  };

  const projectToDelete = projects.find((project) => project.id === projectToDeleteId) ?? null;

  const handleDeleteProject = async (projectId: string) => {
    setBusy(`delete:${projectId}`);
    setStatus(null);
    try {
      await api.delete(apiPath`/projects/${projectId}`);
      await refetchProjects();
      setStatus({ tone: 'success', message: 'Project deleted.' });
    } catch (error) {
      setStatus({ tone: 'error', message: getErrorMessage(error, 'Unable to delete project.') });
    } finally {
      setBusy(null);
      setProjectToDeleteId(null);
    }
  };

  const handleFamilySort = (key: FamilySortKey) => {
    if (familySortKey === key) {
      setFamilySortAsc((currentAsc) => !currentAsc);
      return;
    }
    setFamilySortKey(key);
    setFamilySortAsc(key === 'created_at' ? false : true);
  };

  const familySortIndicator = (key: FamilySortKey) =>
    familySortKey === key ? (familySortAsc ? '▲' : '▼') : '';

  return (
    <div className="page-shell project-page space-y-6">
      <PageHeader
        kicker="Administration"
        title="Projects"
        description="Keep all project definitions in one searchable catalog, then open a selected project to review its settings, user access, and linked families and samples."
        actions={
          <>
            <div className="variant-summary-row">
              <span className="badge-chip">Projects {projectTotals.projects}</span>
              <span className="badge-chip">Families {projectTotals.families}</span>
              <span className="badge-chip">Samples {projectTotals.samples}</span>
            </div>
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
          </>
        }
      />

      {status && (
        <p className={status.tone === 'success' ? 'form-status' : 'status-note status-note--error'}>
          {status.message}
        </p>
      )}

      {createFormOpen && (
        <section className="surface-card space-y-4" aria-labelledby="project-create-heading">
          <div className="space-y-1">
            <h2 id="project-create-heading" className="section-title">
              New project
            </h2>
            <p className="catalog-card-copy">
              Add the project title, notes, organism, assembly, and initial user access in one
              step.
            </p>
          </div>

          <form onSubmit={handleCreateProject} className="space-y-4">
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
                  rows={1}
                  value={createForm.description}
                  onChange={(event) =>
                    setCreateForm((current) => ({
                      ...current,
                      description: event.target.value,
                    }))
                  }
                  placeholder="Cohort, purpose or handoff context"
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
                    Members can also be added later, from the project&rsquo;s settings.
                    An analyst reads and changes the project&rsquo;s records; a reader only reads
                    them.
                  </p>
                </div>
                <ProjectMemberPicker
                  users={users}
                  form={createForm}
                  onToggle={(userId) => toggleUserId(setCreateForm, userId)}
                  onLevel={(userId, level) => setUserLevel(setCreateForm, userId, level)}
                />
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

      <section className="surface-card space-y-4" aria-labelledby="project-catalog-heading">
        <div className="project-toolbar">
          <div className="space-y-1">
            <h2 id="project-catalog-heading" className="section-title">
              Existing projects
            </h2>
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

        {projectsFailed ? null : (
          <p className="project-filter-summary">
            Showing {filteredProjects.length} of {projects.length} projects.
          </p>
        )}

        {/* A failed list is not an empty one: it read "No projects yet" (#610). */}
        {projectsFailed ? (
          <QueryFailure what="the projects" error={projectsError} onRetry={() => void retryProjects()} />
        ) : filteredProjects.length === 0 ? (
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
                      <td>{memberCountLabel(project)}</td>
                      <td className="project-catalog-note-cell">{truncateText(project.description)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {!selectedProject ? (
        <section className="surface-card">
          <div className="page-state">
            <div className="space-y-2">
              <p className="page-kicker">Projects</p>
              <h2 className="page-state-title">No project selected</h2>
              <p className="page-state-copy">
                Select a project from the table to inspect its settings, user access, and linked
                families.
              </p>
            </div>
          </div>
        </section>
      ) : (
        <section className="surface-card card-sections" aria-labelledby="project-selected-heading">
          <div className="card-section">
            <div className="page-header">
              <div className="space-y-1">
                <p className="page-kicker">Selected project</p>
                <h2 id="project-selected-heading" className="section-title">
                  {selectedProject.name}
                </h2>
                <p className="catalog-card-copy">
                  {getSpeciesLabel(selectedProject, speciesNameById)} •{' '}
                  {getAssemblyLabel(selectedProject, assemblyById)}
                </p>
              </div>
              <div className="project-header-actions">
                <span className="badge-chip">
                  {pluralize(selectedProject.families.length, 'family', 'families')} ·{' '}
                  {pluralize(getProjectSampleCount(selectedProject), 'sample')}
                </span>
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
                    onClick={() => setProjectToDeleteId(selectedProject.id)}
                  >
                    Delete project
                  </button>
                </div>
              </div>
            </div>
          </div>

          <section className="card-section" aria-labelledby="project-settings-heading">
            <h3 id="project-settings-heading" className="card-section-subtitle">
              Project settings
            </h3>
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
                  rows={1}
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
            <p className="table-subtle">
              Save settings, at the top of the project, saves these fields and the user access below.
            </p>
          </section>

          <section className="card-section" aria-labelledby="project-access-heading">
            <h3 id="project-access-heading" className="card-section-subtitle">
              User access
            </h3>
            <p className="catalog-card-copy">
              An analyst reads and changes the project&rsquo;s records; a reader only reads
              them. Superusers and administrators have every right in every project.
            </p>
            {users.length > 0 ? (
              <ProjectMemberPicker
                key={selectedProject.id}
                users={users}
                form={editForm}
                onToggle={(userId) => toggleUserId(setEditForm, userId)}
                onLevel={(userId, level) => setUserLevel(setEditForm, userId, level)}
              />
            ) : (
              <p className="table-empty">No users are available for project assignment.</p>
            )}
          </section>

          <section className="card-section" aria-labelledby="project-overview-heading">
            <h3 id="project-overview-heading" className="card-section-subtitle">
              Project overview
            </h3>
            <dl className="project-stat-list">
              <div>
                <dt>Families</dt>
                <dd>{selectedProject.families.length}</dd>
              </div>
              <div>
                <dt>Samples</dt>
                <dd>{getProjectSampleCount(selectedProject)}</dd>
              </div>
              <div>
                <dt>Organism</dt>
                <dd>{getSpeciesLabel(selectedProject, speciesNameById)}</dd>
              </div>
              <div>
                <dt>Assembly</dt>
                <dd>{getAssemblyLabel(selectedProject, assemblyById)}</dd>
              </div>
              <div>
                <dt>Restricted users</dt>
                <dd>{selectedProject.user_ids?.length ?? 0}</dd>
              </div>
              <div>
                <dt>Readers</dt>
                <dd>{selectedProject.reader_user_ids?.length ?? 0}</dd>
              </div>
            </dl>
            <div className="project-notes">
              <h4 className="stat-label">Notes</h4>
              <p className="project-note-block">
                {selectedProject.description?.trim() || 'No project notes have been added yet.'}
              </p>
            </div>
            <div className="project-linked-families">
              <h4 className="stat-label">Linked families</h4>
              {selectedProject.families.length ? (
                <div className="data-table-shell overflow-x-auto">
                  <table className="analysis-table project-family-table">
                    <thead>
                      <tr>
                        <th className="table-sortable" onClick={() => handleFamilySort('family_id')}>
                          Family {familySortIndicator('family_id')}
                        </th>
                        <th
                          className="table-sortable project-family-uploaded-header"
                          onClick={() => handleFamilySort('created_at')}
                        >
                          Uploaded {familySortIndicator('created_at')}
                        </th>
                        <th className="table-sortable" onClick={() => handleFamilySort('samples')}>
                          Samples {familySortIndicator('samples')}
                        </th>
                        <th className="table-sortable" onClick={() => handleFamilySort('sample_ids')}>
                          Sample IDs {familySortIndicator('sample_ids')}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedSelectedFamilies.map((family) => (
                        <tr key={family.family_id}>
                          <td>{family.family_id}</td>
                          <td className="project-family-uploaded-cell" title={family.created_at ?? undefined}>
                            {formatShortDate(family.created_at)}
                          </td>
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
          </section>
        </section>
      )}

      {projectToDelete ? (
        <ConfirmDeleteDialog
          title={`Delete project ${projectToDelete.name}`}
          confirmLabel="Delete project"
          busy={busy === `delete:${projectToDelete.id}`}
          onCancel={() => setProjectToDeleteId(null)}
          onConfirm={() => void handleDeleteProject(projectToDelete.id)}
        >
          <p>
            This deletes the project <strong>{projectToDelete.name}</strong>. No family or sample is
            deleted with it.
          </p>
          <ul>
            {projectDeletionEffects(projectToDelete, projects).map((effect) => (
              <li key={effect}>{effect}</li>
            ))}
          </ul>
        </ConfirmDeleteDialog>
      ) : null}
    </div>
  );
};

export default ProjectsPage;
