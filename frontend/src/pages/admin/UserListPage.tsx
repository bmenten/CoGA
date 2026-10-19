import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import { apiPath } from '../../lib/apiPath';
import type { UserRead } from '../../lib/apiSchema.generated';
import { getErrorMessage } from '../../lib/errorMessage';
import { userAccountsQuery } from '../../lib/users';
import { memberAccessLevel } from '../../lib/familyAccess';
import type { ProjectOption } from './dataManagementTypes';

type User = UserRead;

const UserListPage: React.FC = () => {
  const queryClient = useQueryClient();
  // What the last (de)activation did: a refused one used to leave only a checkbox
  // snapping back, with no word of why (#526).
  const [activationStatus, setActivationStatus] = React.useState<{
    tone: 'success' | 'error';
    text: string;
  } | null>(null);

  const {
    data: users = [],
    isLoading: usersLoading,
    error: usersError,
  } = useQuery({ ...userAccountsQuery, retry: false });

  const {
    data: projects = [],
    isLoading: projectsLoading,
    error: projectsError,
  } = useQuery<ProjectOption[]>({
    queryKey: ['admin', 'projects'],
    queryFn: async () => {
      const response = await api.get('/projects');
      return (response.data as Array<{ id: string; name: string }>).map((p) => ({
        id: p.id,
        name: p.name,
      }));
    },
    retry: false,
  });

  const toggleActiveMutation = useMutation({
    mutationFn: async (user: User) => {
      const response = await api.patch(apiPath`/auth/users/${user.id}`, {
        is_active: !user.is_active,
      });
      return response.data as User;
    },
    onMutate: () => setActivationStatus(null),
    onSuccess: async (_updated, user) => {
      setActivationStatus({
        tone: 'success',
        text: `${user.email} is now ${user.is_active ? 'inactive' : 'active'}.`,
      });
      await queryClient.invalidateQueries({ queryKey: userAccountsQuery.queryKey });
    },
    onError: (error, user) => {
      setActivationStatus({
        tone: 'error',
        text: `Could not ${user.is_active ? 'deactivate' : 'activate'} ${user.email}: ${getErrorMessage(
          error,
          'the request failed',
        )}`,
      });
    },
  });

  if (usersLoading || projectsLoading) {
    return (
      <PageState
        kicker="Administration"
        title="Loading users"
        message="Preparing user activation state and project access."
      />
    );
  }

  if (usersError || projectsError) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load users"
        message={getErrorMessage(
          usersError ?? projectsError,
          'The user list could not be loaded.'
        )}
      />
    );
  }

  return (
    <div className="page-shell admin-compact space-y-5">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Users</h1>
            <p className="catalog-card-copy">
              Review activation state and current project access. Project access is managed from
              the project settings view, where each member is an analyst, who reads and changes the
              project&rsquo;s records, or a reader, who only reads them.
            </p>
          </div>
        </div>
      </section>
      <div className="surface-card">
        {activationStatus ? (
          <p
            className={`status-note ${
              activationStatus.tone === 'error' ? 'status-note--error' : 'status-note--success'
            }`}
            role={activationStatus.tone === 'error' ? 'alert' : 'status'}
          >
            {activationStatus.text}
          </p>
        ) : null}
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table">
            <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Affiliation</th>
              <th>Role</th>
              <th>Active</th>
              <th>Project access</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.email}</td>
                <td>{`${u.first_name ?? ''} ${u.last_name ?? ''}`.trim() || '—'}</td>
                <td>{u.affiliation || '—'}</td>
                <td>
                  <span className="table-chip">{u.role}</span>
                </td>
                <td className="table-cell-center">
                  <input
                    type="checkbox"
                    aria-label={`Active: ${u.email}`}
                    checked={u.is_active}
                    disabled={
                      toggleActiveMutation.isPending &&
                      toggleActiveMutation.variables?.id === u.id
                    }
                    onChange={() => toggleActiveMutation.mutate(u)}
                  />
                </td>
                <td>
                  <div className="table-checkbox-grid sm:grid-cols-2">
                    {u.projects.length > 0 ? (
                      projects
                        .filter((project) => u.projects.includes(project.id))
                        .map((project) => (
                          <span key={project.id} className="table-chip">
                            {project.name}
                            <span> · {memberAccessLevel(u.reader_projects, project.id)}</span>
                          </span>
                        ))
                    ) : (
                      <span className="table-empty">No project access</span>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default UserListPage;
