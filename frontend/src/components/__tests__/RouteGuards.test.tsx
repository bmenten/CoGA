// The route guards (REQ-UI-005): `RequireAuth` keeps a signed-out user out of the app and
// `RequireAdmin` keeps a non-admin out of the admin pages. A signed-out user is sent to the
// sign-in page with the page they asked for in `next` (query and hash included), which the
// sign-in page opens afterwards (REQ-UI-006, `LoginPage.test.tsx`). `SessionRedirect` sends
// the root to the dashboard or to the sign-in page.
import { render, screen } from '@testing-library/react';
import type { FC } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';

import RequireAdmin from '../RequireAdmin';
import RequireAuth from '../RequireAuth';
import SessionRedirect from '../SessionRedirect';

const LoginLanding = () => {
  const location = useLocation();
  return <div>Login page {location.search}</div>;
};

const signIn = (role?: string) => {
  localStorage.setItem('token', 'token-123');
  if (role) localStorage.setItem('role', role);
};

const renderGuarded = (Guard: FC, path: string, protectedPath: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Guard />}>
          <Route path={protectedPath} element={<div>Protected page</div>} />
        </Route>
        <Route path="/login" element={<LoginLanding />} />
        <Route path="/dashboard" element={<div>Dashboard</div>} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  localStorage.clear();
});

describe.each([
  {
    guard: 'RequireAuth',
    Guard: RequireAuth,
    protectedPath: '/families/:familyId/small-variants',
    requested: '/families/demo_family/small-variants?page=2#top',
    next: 'next=%2Ffamilies%2Fdemo_family%2Fsmall-variants%3Fpage%3D2%23top',
  },
  {
    guard: 'RequireAdmin',
    Guard: RequireAdmin,
    protectedPath: '/admin/users',
    requested: '/admin/users?tab=projects#roles',
    next: 'next=%2Fadmin%2Fusers%3Ftab%3Dprojects%23roles',
  },
])('$guard', ({ Guard, protectedPath, requested, next }) => {
  it('redirects unauthenticated users to login with the requested path', () => {
    renderGuarded(Guard, requested, protectedPath);

    expect(screen.getByText(`Login page ?${next}`)).toBeInTheDocument();
    expect(screen.queryByText('Protected page')).not.toBeInTheDocument();
  });
});

describe('RequireAuth', () => {
  it('renders protected content when authenticated', () => {
    signIn();

    renderGuarded(RequireAuth, '/families/demo_family/small-variants?page=2', '/families/:familyId/small-variants');

    expect(screen.getByText('Protected page')).toBeInTheDocument();
  });
});

describe('RequireAdmin', () => {
  it('redirects an authenticated non-admin (viewer) to the dashboard', () => {
    signIn('viewer');

    renderGuarded(RequireAdmin, '/admin/users', '/admin/users');

    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.queryByText('Protected page')).not.toBeInTheDocument();
  });

  it('takes a stored admin role without a session as signed out', () => {
    localStorage.setItem('role', 'admin');

    renderGuarded(RequireAdmin, '/admin/users', '/admin/users');

    expect(screen.getByText('Login page ?next=%2Fadmin%2Fusers')).toBeInTheDocument();
    expect(screen.queryByText('Protected page')).not.toBeInTheDocument();
  });

  it.each(['admin', 'superuser'])('renders the admin area for an authenticated %s', (role) => {
    signIn(role);

    renderGuarded(RequireAdmin, '/admin/users', '/admin/users');

    expect(screen.getByText('Protected page')).toBeInTheDocument();
  });
});

describe('SessionRedirect', () => {
  const renderRoot = () =>
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={<SessionRedirect authenticatedTo="/dashboard" unauthenticatedTo="/login" />}
          />
          <Route path="/dashboard" element={<div>Dashboard landing</div>} />
          <Route path="/login" element={<div>Login landing</div>} />
        </Routes>
      </MemoryRouter>,
    );

  it('redirects authenticated users to the dashboard target', () => {
    signIn();

    renderRoot();

    expect(screen.getByText('Dashboard landing')).toBeInTheDocument();
  });

  it('redirects unauthenticated users to the login target', () => {
    renderRoot();

    expect(screen.getByText('Login landing')).toBeInTheDocument();
  });
});
