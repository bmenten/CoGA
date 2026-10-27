import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import { apiPath } from '../../lib/apiPath';
import type { UserRead, UserUpdate } from '../../lib/apiSchema.generated';
import { getStoredUsername } from '../../lib/auth';
import { getErrorMessage } from '../../lib/errorMessage';
import { userAccountsQuery } from '../../lib/users';
import { memberAccessLevel } from '../../lib/familyAccess';
import type { ProjectOption } from './dataManagementTypes';

type User = UserRead;
type Role = NonNullable<UserUpdate['role']>;

// The global roles (SEC-4): a viewer sees the records of their projects; an admin or a
// superuser, every record, and the administration.
const ROLES: Role[] = ['viewer', 'superuser', 'admin'];
const SIGN_IN_LABELS: Record<User['sign_in'], string> = {
  microsoft: 'Microsoft',
  password: 'Password',
};

const UserListPage: React.FC = () => {
  const queryClient = useQueryClient();
  // What the last role or activation change did: a refused (de)activation used to leave only
  // a checkbox snapping back, with no word of why (#526).
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

  // The administrator signed in here: nobody changes their own role or deactivates their own
  // account (the backend refuses both).
  const signedInAs = getStoredUsername();

  const roleMutation = useMutation({
    mutationFn: async ({ user, role }: { user: User; role: Role }) => {
      const response = await api.patch(apiPath`/auth/users/${user.id}`, { role });
      return response.data as User;
    },
    onMutate: () => setActivationStatus(null),
    onSuccess: async (_updated, { user, role }) => {
      setActivationStatus({ tone: 'success', text: `${user.email} is now ${role}.` });
      await queryClient.invalidateQueries({ queryKey: userAccountsQuery.queryKey });
    },
    onError: (error, { user }) => {
      setActivationStatus({
        tone: 'error',
        text: `Could not change the role of ${user.email}: ${getErrorMessage(error, 'the request failed')}`,
      });
    },
  });

  const changeRole = (user: User, role: Role) => {
    if (role === user.role) return;
    // A select changes with an arrow key too: a role is changed only once confirmed.
    if (!window.confirm(`Change the role of ${user.email} from ${user.role} to ${role}?`)) return;
    roleMutation.mutate({ user, role });
  };

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
        loading
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
              Set each account&apos;s role and activation, and review its project access. Project
              access is managed from the project settings view, where each member is an analyst, who
              reads and changes the project&rsquo;s records, or a reader, who only reads them.
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
              <th>Sign-in</th>
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
                <td>{SIGN_IN_LABELS[u.sign_in] ?? u.sign_in}</td>
                <td>
                  <select
                    aria-label={`Role: ${u.email}`}
                    value={u.role}
                    title={u.email === signedInAs ? 'You cannot change your own role.' : undefined}
                    disabled={
                      u.email === signedInAs ||
                      (roleMutation.isPending && roleMutation.variables?.user.id === u.id)
                    }
                    onChange={(event) => changeRole(u, event.target.value as Role)}
                  >
                    {ROLES.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="table-cell-center">
                  <input
                    type="checkbox"
                    aria-label={`Active: ${u.email}`}
                    checked={u.is_active}
                    title={u.email === signedInAs ? 'You cannot deactivate your own account.' : undefined}
                    disabled={
                      u.email === signedInAs ||
                      (toggleActiveMutation.isPending && toggleActiveMutation.variables?.id === u.id)
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
