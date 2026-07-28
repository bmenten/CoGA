import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import RequireAuth from '../RequireAuth';

const LoginLanding = () => {
  const location = useLocation();
  return <div>Login page {location.search}</div>;
};

describe('RequireAuth', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('redirects unauthenticated users to login with the requested path', () => {
    render(
      <MemoryRouter initialEntries={['/families/demo_family/small-variants?page=2']}>
        <Routes>
          <Route element={<RequireAuth />}>
            <Route
              path="/families/:familyId/small-variants"
              element={<div>Small variants</div>}
            />
          </Route>
          <Route path="/login" element={<LoginLanding />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      screen.getByText(
        /next=%2Ffamilies%2Fdemo_family%2Fsmall-variants%3Fpage%3D2/i,
      ),
    ).toBeInTheDocument();
  });

  it('renders protected content when authenticated', () => {
    localStorage.setItem('token', 'token-123');

    render(
      <MemoryRouter initialEntries={['/families/demo_family/small-variants?page=2']}>
        <Routes>
          <Route element={<RequireAuth />}>
            <Route
              path="/families/:familyId/small-variants"
              element={<div>Small variants</div>}
            />
          </Route>
          <Route path="/login" element={<div>Login page</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText('Small variants')).toBeInTheDocument();
  });
});
