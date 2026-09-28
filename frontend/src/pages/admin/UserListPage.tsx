import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import { apiPath } from '../../lib/apiPath';

interface User {
  id: string;
  email: string;
  first_name?: string;
  last_name?: string;
  affiliation?: string;
  role: string;
  is_active: boolean;
  projects: string[];
}

interface Project {
  id: string;
  name: string;
}

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (typeof error !== 'object' || error === null) return fallback;
  const responseDetail = (
    error as { response?: { data?: { detail?: string } } }
  )?.response?.data?.detail;
  return responseDetail || (error as { message?: string }).message || fallback;
};

const UserListPage: React.FC = () => {
  const queryClient = useQueryClient();

  const {
    data: users = [],
    isLoading: usersLoading,
    error: usersError,
  } = useQuery<User[]>({
    queryKey: ['admin', 'users'],
    queryFn: async () => {
      const response = await api.get('/auth/users');
      return response.data as User[];
    },
    retry: false,
  });

  const {
    data: projects = [],
    isLoading: projectsLoading,
    error: projectsError,
  } = useQuery<Project[]>({
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
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
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
              the project settings view.
            </p>
          </div>
        </div>
      </section>
      <div className="surface-card">
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
