import { render as rtlRender, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, it, vi, type Mock } from 'vitest';
import LoginPage from '../LoginPage';
import RequireAdmin from '../../../components/RequireAdmin';
import RequireAuth from '../../../components/RequireAuth';
import api from '../../../lib/api';
import { clearSession } from '../../../lib/auth';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

// LoginPage clears the query cache when a session starts (#521), so it needs a client.
let queryClient: QueryClient;
const render = (ui: ReactElement) =>
  rtlRender(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);

vi.mock('../../../lib/api');

describe('LoginPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    clearSession();
    localStorage.clear();
    queryClient = createTestQueryClient();
  });

  it('renders login form inputs and button', () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );
    expect(screen.getByPlaceholderText(/email/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /login/i })).toBeInTheDocument();
  });

  it('redirects to the dashboard when a session is already stored', () => {
    localStorage.setItem('token', 'token-123');

    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/dashboard" element={<div>Dashboard landing</div>} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText('Dashboard landing')).toBeInTheDocument();
  });

  it('shows error message on failed login', async () => {
    (api.post as unknown as Mock).mockRejectedValue({
      response: { data: { detail: 'Incorrect email or password' } },
    });
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );
    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'wrong' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));
    expect(
      await screen.findByText(/incorrect email or password/i)
    ).toBeInTheDocument();
  });

  it('shows formatted validation errors from the API', async () => {
    (api.post as unknown as Mock).mockRejectedValue({
      response: {
        data: {
          detail: [
            {
              type: 'string_too_short',
              loc: ['body', 'password'],
              msg: 'String should have at least 8 characters',
              input: 'short',
              ctx: { min_length: 8 },
            },
          ],
        },
      },
    });
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );
    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'short' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));
    expect(
      await screen.findByText(/password: string should have at least 8 characters/i)
    ).toBeInTheDocument();
  });

  it('shows an explicit API unavailable message on transport failure', async () => {
    (api.post as unknown as Mock).mockRejectedValue({
      request: {},
      message: 'Network Error',
    });

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'admin' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    expect(
      await screen.findByText(
        /unable to reach the api at \/api\. check that the backend, postgres, and clickhouse services are running\./i
      )
    ).toBeInTheDocument();
  });

  it('uses the fresh access token when loading the current user after login', async () => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: {
        access_token: 'token-123',
        role: 'admin',
      },
    });
    (api.get as unknown as Mock).mockResolvedValue({
      data: {
        email: 'admin@example.com',
        role: 'admin',
      },
    });

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'admin' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/auth/me', {
        headers: {
          Authorization: 'Bearer token-123',
        },
      })
    );
    expect(localStorage.getItem('token')).toBe('token-123');
    expect(localStorage.getItem('role')).toBe('admin');
  });

  it('starts the new session with an empty query cache (#521)', async () => {
    // Data an earlier session loaded in this tab must not be shown to the next user.
    queryClient.setQueryData(['family', 'F1'], { family_id: 'F1', members: ['someone else'] });
    (api.post as unknown as Mock).mockResolvedValue({ data: { access_token: 'token-456' } });
    (api.get as unknown as Mock).mockResolvedValue({ data: { email: 'next@example.com', role: 'viewer' } });

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );
    fireEvent.change(screen.getByPlaceholderText(/email/i), { target: { value: 'next@example.com' } });
    fireEvent.change(screen.getByPlaceholderText(/password/i), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => expect(localStorage.getItem('token')).toBe('token-456'));
    expect(queryClient.getQueryData(['family', 'F1'])).toBeUndefined();
  });

  it('continues to the requested protected page after login', async () => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: {
        access_token: 'token-123',
        role: 'viewer',
      },
    });
    (api.get as unknown as Mock).mockResolvedValue({
      data: {
        email: 'viewer@example.com',
        role: 'viewer',
      },
    });

    render(
      <MemoryRouter
        initialEntries={[
          '/login?next=/families/demo_family/small-variants%3Fpage%3D2',
        ]}
      >
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/families/:familyId/small-variants"
            element={<div>Small variant workspace</div>}
          />
        </Routes>
      </MemoryRouter>
    );

    expect(
      screen.getByText(/sign in to continue to your requested page/i)
    ).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'viewer@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'viewer-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    expect(await screen.findByText('Small variant workspace')).toBeInTheDocument();
  });
});

// The page `next` asks for is opened only when it is a page of this site (REQ-UI-006): a
// `next` that leaves the site, runs a script, is not a path, or leads back to the login or
// sign-up page sends the user to the dashboard instead, after a sign-in and for a user
// already signed in, and the login page promises no requested page. So does a `next` in
// any form CoGA does not write itself (UI-2): RequireAuth and RequireAdmin write the
// page's path, query string and hash as the browser holds them, percent-encoded, so a
// backslash, whitespace, a control character, or a path that starts with anything but a
// letter or a digit, a percent-encoded character included, comes from someone else.
describe('LoginPage: the page `next` asks for', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    clearSession();
    localStorage.clear();
    queryClient = createTestQueryClient();
  });

  // Where the user ended up, on any page but the login page.
  const LandedOn = () => {
    const location = useLocation();
    return (
      <p data-testid="landed-on">{`${location.pathname}${location.search}${location.hash}`}</p>
    );
  };

  const renderLoginWithNext = (next: string) =>
    render(
      <MemoryRouter initialEntries={[`/login?next=${encodeURIComponent(next)}`]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<LandedOn />} />
        </Routes>
      </MemoryRouter>
    );

  const signIn = (role: 'viewer' | 'admin' = 'viewer') => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: { access_token: 'token-123', role },
    });
    (api.get as unknown as Mock).mockResolvedValue({
      data: { email: `${role}@example.com`, role },
    });
    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: `${role}@example.com` },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: `${role}-password` },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));
  };

  // Each `next` as the login page reads it, decoded from its query string.
  const UNSAFE_NEXT: [string, string][] = [
    ['another site', 'https://evil.example/phish'],
    ['another site, without a scheme', '//evil.example/phish'],
    // A browser reads a backslash as a slash and drops a tab, line feed or carriage return,
    // so each of these is //evil.example to it (UI-2). In a link they are
    // `?next=/%5Cevil.example/phish`, `?next=/%09/evil.example/phish`, `/%0A/…` and `/%0D/…`.
    ['another site, through a backslash', '/\\evil.example/phish'],
    ['another site, through a tab', '/\t/evil.example/phish'],
    ['another site, through a line feed', '/\n/evil.example/phish'],
    ['another site, through a carriage return', '/\r/evil.example/phish'],
    ['a script', 'javascript:alert(1)'],
    ['no path of this site', 'evil.example/phish'],
    // No form CoGA writes (UI-2): its paths start with a word, carry no backslash, and hold
    // no whitespace or control character that the browser has not percent-encoded.
    ['a path that starts with an encoded tab', '/%09/evil.example/phish'],
    ['a path that starts with an encoded line feed', '/%0A/evil.example/phish'],
    ['a path that starts with an encoded carriage return', '/%0D/evil.example/phish'],
    ['a path that starts with an encoded NUL', '/%00/evil.example/phish'],
    ['a path that starts with an encoded slash', '/%2F/evil.example/phish'],
    ['a path that starts with an encoded backslash', '/%5C/evil.example/phish'],
    ['a path that starts with a space', '/ /evil.example/phish'],
    ['a path that starts with a NUL', '/\u0000/evil.example/phish'],
    ['a path with a backslash further on', '/families/FAM1\\qc'],
    ['a path with a space further on', '/families/FAM 1/qc'],
    ['a path with a control character further on', '/families/FAM1/qc\u0007'],
    ['the sign-up page', '/signup'],
    ['the login page', '/login?next=%2Ffamilies%2FFAM1'],
    ['nothing', ''],
  ];

  // What RequireAuth and RequireAdmin write into `next`: the page's path, query string and
  // hash as the browser holds them, percent-encoded.
  const PROJECT = '00000000-0000-4000-8000-000000000001';
  const SAME_SITE_NEXT: [string, string][] = [
    ['a family page with its query string', '/families/FAM1/structural-variants?page=2'],
    ['a gene in a family', `/families/FAM1/small-variants?gene=BRCA1&project_id=${PROJECT}`],
    ['a chromosome region', '/families/FAM1/chromosome/X?start=1000&end=2000'],
    ['an IGV locus', '/families/FAM1/igv?locus=chr1%3A100-200'],
    ['a family whose ID the browser percent-encodes', '/families/FAM%C3%891.a-b_c/report'],
    ['a section of the user guide', '/docs#quick-start'],
    ['a section of a family page', `/families/FAM1/variant-summary?project_id=${PROJECT}#counts`],
    ['an admin page', '/admin/monitoring/audit-logs'],
  ];

  it.each(UNSAFE_NEXT)('opens the dashboard after a sign-in, not %s', async (_target, next) => {
    renderLoginWithNext(next);
    expect(
      screen.queryByText(/sign in to continue to your requested page/i)
    ).not.toBeInTheDocument();

    signIn();

    expect(await screen.findByTestId('landed-on')).toHaveTextContent(/^\/dashboard$/);
    expect(localStorage.getItem('token')).toBe('token-123');
  });

  it.each(UNSAFE_NEXT)('sends a signed-in user to the dashboard, not %s', (_target, next) => {
    localStorage.setItem('token', 'token-123');

    renderLoginWithNext(next);

    expect(screen.getByTestId('landed-on')).toHaveTextContent(/^\/dashboard$/);
  });

  it.each(SAME_SITE_NEXT)('sends a signed-in user on to %s', (_page, next) => {
    localStorage.setItem('token', 'token-123');

    renderLoginWithNext(next);

    expect(screen.getByTestId('landed-on').textContent).toBe(next);
  });

  it.each(SAME_SITE_NEXT)('continues to %s after a sign-in', async (_page, next) => {
    renderLoginWithNext(next);
    expect(
      screen.getByText(/sign in to continue to your requested page/i)
    ).toBeInTheDocument();

    signIn();

    expect((await screen.findByTestId('landed-on')).textContent).toBe(next);
  });

  // The whole trip, with the routes guarded as in index.tsx: the guard sends the user to
  // sign in with the page in `next`, and the sign-in (as an admin, so that the admin page
  // opens too) brings them back to it.
  it.each(SAME_SITE_NEXT)(
    'brings the user back to %s after the sign-in its guard asked for',
    async (_page, url) => {
      render(
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<RequireAuth />}>
              <Route element={<RequireAdmin />}>
                <Route path="/admin/*" element={<LandedOn />} />
              </Route>
              <Route path="*" element={<LandedOn />} />
            </Route>
          </Routes>
        </MemoryRouter>
      );
      expect(
        screen.getByText(/sign in to continue to your requested page/i)
      ).toBeInTheDocument();

      signIn('admin');

      expect((await screen.findByTestId('landed-on')).textContent).toBe(url);
    }
  );
});
