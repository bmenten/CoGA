import React, { useEffect, useState } from 'react';
import api from '../../lib/api';

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

const UserListPage: React.FC = () => {
  const [users, setUsers] = useState<User[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    api
      .get('/auth/users')
      .then((res) => setUsers(res.data))
      .catch((err) => console.error(err));
    api
      .get('/projects')
      .then((res) =>
        setProjects(res.data.map((p: any) => ({ id: p.id, name: p.name })))
      )
      .catch((err) => console.error(err));
  }, []);

  const toggleActive = (user: User) => {
    api
      .patch(`/auth/users/${user.id}`, { is_active: !user.is_active })
      .then((res) =>
        setUsers((prev) => prev.map((u) => (u.id === user.id ? res.data : u)))
      )
      .catch((err) => console.error(err));
  };

  return (
    <div className="page-shell space-y-6">
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
                <td>{`${u.first_name ?? ''} ${u.last_name ?? ''}`}</td>
                <td>{u.affiliation}</td>
                <td>
                  <span className="table-chip">{u.role}</span>
                </td>
                <td className="table-cell-center">
                  <input
                    type="checkbox"
                    checked={u.is_active}
                    onChange={() => toggleActive(u)}
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
