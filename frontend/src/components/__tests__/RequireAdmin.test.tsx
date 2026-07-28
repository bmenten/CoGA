import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import RequireAdmin from '../RequireAdmin';

const LoginLanding = () => {
  const location = useLocation();
  return <div>Login page {location.search}</div>;
};

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<RequireAdmin />}>
          <Route path="/admin/users" element={<div>Admin area</div>} />
        </Route>
        <Route path="/login" element={<LoginLanding />} />
        <Route path="/dashboard" element={<div>Dashboard</div>} />
      </Routes>
    </MemoryRouter>,
  );

describe('RequireAdmin', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('redirects unauthenticated users to login with the requested path', () => {
    renderAt('/admin/users?tab=projects');

    expect(
      screen.getByText(/next=%2Fadmin%2Fusers%3Ftab%3Dprojects/i),
    ).toBeInTheDocument();
    expect(screen.queryByText('Admin area')).not.toBeInTheDocument();
  });

  it('redirects an authenticated non-admin (viewer) to the dashboard', () => {
    localStorage.setItem('token', 'token-123');
    localStorage.setItem('role', 'viewer');

    renderAt('/admin/users');

    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.queryByText('Admin area')).not.toBeInTheDocument();
  });

  it('renders the admin area for an authenticated admin', () => {
    localStorage.setItem('token', 'token-123');
    localStorage.setItem('role', 'admin');

    renderAt('/admin/users');

    expect(screen.getByText('Admin area')).toBeInTheDocument();
  });

  it('grants access to a superuser', () => {
    localStorage.setItem('token', 'token-123');
    localStorage.setItem('role', 'superuser');

    renderAt('/admin/users');

    expect(screen.getByText('Admin area')).toBeInTheDocument();
  });
});
