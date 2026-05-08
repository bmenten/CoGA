import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import SessionRedirect from '../SessionRedirect';

describe('SessionRedirect', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('redirects authenticated users to the dashboard target', () => {
    localStorage.setItem('token', 'token-123');

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

    expect(screen.getByText('Dashboard landing')).toBeInTheDocument();
  });

  it('redirects unauthenticated users to the login target', () => {
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

    expect(screen.getByText('Login landing')).toBeInTheDocument();
  });
});
