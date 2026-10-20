// The sign-in page with Microsoft sign-in configured (SEC-4), MSAL replaced by a stand-in.
//
// The page asks the backend how users sign in (GET /auth/config). With Microsoft it offers only
// "Sign in with Microsoft": no password form and no sign-up. The sign-in goes to Microsoft for
// CoGA's API scope, always asking which account continues, and the page asked for stays in this
// tab: Microsoft sees only a random key. Back on /login, the page completes the sign-in, checks
// the token with the API (GET /auth/me) and starts the session (marked as a Microsoft one), then
// opens the page asked for if it is one of this site. A refusal by the API is shown, and leaves
// neither a session nor Microsoft's tokens in the tab. After a sign-out the page offers to sign
// out of Microsoft too. /login/emergency is the emergency administrator's password sign-in; it
// is the ordinary sign-in without Microsoft. Values are made up; tokens are put together here.
import { fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import LoginPage from '../LoginPage';
import api from '../../../lib/api';
import { clearSession } from '../../../lib/auth';
import { resetMicrosoftSignInForTests } from '../../../lib/microsoftSignIn';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const msal = vi.hoisted(() => ({
  client: {
    initialize: vi.fn(),
    loginRedirect: vi.fn(),
    handleRedirectPromise: vi.fn(),
    setActiveAccount: vi.fn(),
    getActiveAccount: vi.fn(),
    getAllAccounts: vi.fn(),
    acquireTokenSilent: vi.fn(),
    clearCache: vi.fn(),
    logoutRedirect: vi.fn(),
  },
  configurations: [] as unknown[],
}));

vi.mock('@azure/msal-browser', () => ({
  PublicClientApplication: class {
    constructor(configuration: unknown) {
      msal.configurations.push(configuration);
      return msal.client;
    }
  },
}));

vi.mock('../../../lib/api');

const MICROSOFT = {
  client_id: 'coga-application',
  authority: 'https://login.microsoftonline.com/coga-directory',
  api_scope: 'api://coga-application/access_as_user',
};
const ACCOUNT = { homeAccountId: 'home', username: 'a.colleague@example.org' };
const NOT_ACTIVE = 'Your CoGA account is not active. Ask a CoGA administrator to activate it.';
// What Microsoft issues, put together here.
const issued = ['microsoft', 'access', 'one'].join('-');

let queryClient: QueryClient;

const LandedOn = () => {
  const location = useLocation();
  return <p data-testid="landed-on">{`${location.pathname}${location.search}`}</p>;
};

const render = (entry: string, ui: ReactElement = <LoginPage />) =>
  rtlRender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/login" element={ui} />
          <Route path="/login/emergency" element={<LoginPage emergency />} />
          <Route path="*" element={<LandedOn />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

type Served = { microsoft?: typeof MICROSOFT | null; me?: Record<string, unknown>; meFailure?: unknown };

const serve = ({ microsoft = MICROSOFT, me, meFailure }: Served = {}) => {
  (api.get as unknown as Mock).mockImplementation(async (url: string) => {
    if (url === '/auth/config') return { data: { microsoft } };
    if (url === '/auth/me') {
      if (meFailure) throw meFailure;
      return { data: me };
    }
    throw new Error(`unexpected GET ${url}`);
  });
};

const nextKey = (key: string) => `coga.sign-in.next.${key}`;

beforeEach(() => {
  vi.resetAllMocks();
  resetMicrosoftSignInForTests();
  clearSession();
  localStorage.clear();
  sessionStorage.clear();
  msal.configurations.length = 0;
  msal.client.initialize.mockResolvedValue(undefined);
  msal.client.loginRedirect.mockResolvedValue(undefined);
  msal.client.handleRedirectPromise.mockResolvedValue(null);
  msal.client.clearCache.mockResolvedValue(undefined);
  msal.client.logoutRedirect.mockResolvedValue(undefined);
  msal.client.getAllAccounts.mockReturnValue([]);
  msal.client.getActiveAccount.mockReturnValue(null);
  queryClient = createTestQueryClient();
});

describe('LoginPage with Microsoft sign-in', () => {
  it('offers the Microsoft sign-in, without a password form or a sign-up', async () => {
    serve();
    render('/login');

    expect(await screen.findByRole('button', { name: 'Sign in with Microsoft' })).toBeEnabled();
    expect(
      screen.getByText('Continue to the family workspace with the Microsoft account of your hospital or university.'),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/email/i)).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/password/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /sign up/i })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Your first sign-in makes your CoGA account\. An administrator then gives you access/),
    ).toBeInTheDocument();
  });

  it('goes to Microsoft for CoGA’s API scope, and the page asked for stays in this tab', async () => {
    serve();
    render(`/login?next=${encodeURIComponent('/families/FAM1/qc?tab=sex')}`);

    fireEvent.click(await screen.findByRole('button', { name: 'Sign in with Microsoft' }));

    await waitFor(() => expect(msal.client.loginRedirect).toHaveBeenCalledTimes(1));
    const [request] = msal.client.loginRedirect.mock.calls[0];
    expect(request).toEqual({
      scopes: ['api://coga-application/access_as_user'],
      prompt: 'select_account',
      state: expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/),
    });
    // Microsoft sees a random key; the family ID stays in this tab's session storage.
    expect(JSON.stringify(request)).not.toContain('FAM1');
    expect(sessionStorage.getItem(nextKey(request.state))).toBe('/families/FAM1/qc?tab=sex');
    expect(msal.configurations).toEqual([
      {
        auth: {
          clientId: 'coga-application',
          authority: 'https://login.microsoftonline.com/coga-directory',
          redirectUri: `${window.location.origin}/login`,
          postLogoutRedirectUri: `${window.location.origin}/login`,
        },
        cache: { cacheLocation: 'sessionStorage' },
      },
    ]);
    expect(msal.client.initialize).toHaveBeenCalledTimes(1);
  });

  it('completes the sign-in Microsoft returns with, checks it with the API and opens the page asked for', async () => {
    const key = crypto.randomUUID();
    sessionStorage.setItem(nextKey(key), '/families/FAM1/qc?tab=sex');
    msal.client.handleRedirectPromise.mockResolvedValue({ accessToken: issued, account: ACCOUNT, state: key });
    serve({ me: { email: 'a.colleague@example.org', role: 'viewer' } });
    queryClient.setQueryData(['family', 'F1'], { family_id: 'F1', members: ['someone else'] });

    render('/login');

    expect(await screen.findByTestId('landed-on')).toHaveTextContent('/families/FAM1/qc?tab=sex');
    expect(msal.client.handleRedirectPromise).toHaveBeenCalledWith({ navigateToLoginRequestUrl: false });
    expect(msal.client.setActiveAccount).toHaveBeenCalledWith(ACCOUNT);
    expect(api.get).toHaveBeenCalledWith('/auth/me', { headers: { Authorization: `Bearer ${issued}` } });
    expect(localStorage.getItem('token')).toBe(issued);
    expect(localStorage.getItem('username')).toBe('a.colleague@example.org');
    expect(localStorage.getItem('role')).toBe('viewer');
    expect(localStorage.getItem('sign_in')).toBe('microsoft');
    // The remembered page is used once, and nothing of an earlier session stays cached (#521).
    expect(sessionStorage.getItem(nextKey(key))).toBeNull();
    expect(queryClient.getQueryData(['family', 'F1'])).toBeUndefined();
  });

  it.each([
    ['another site', 'https://evil.example/phish'],
    ['the login page', '/login/emergency'],
    ['nothing remembered', null],
  ])('opens the dashboard when the page asked for is %s', async (_case, remembered) => {
    const key = crypto.randomUUID();
    if (remembered) sessionStorage.setItem(nextKey(key), remembered);
    msal.client.handleRedirectPromise.mockResolvedValue({ accessToken: issued, account: ACCOUNT, state: key });
    serve({ me: { email: 'a.colleague@example.org', role: 'viewer' } });

    render('/login');

    expect(await screen.findByTestId('landed-on')).toHaveTextContent(/^\/dashboard$/);
  });

  it.each([
    ['an inactive account', { response: { status: 401, data: { detail: NOT_ACTIVE } } }, NOT_ACTIVE],
    [
      'an address another account has',
      { response: { status: 403, data: { detail: 'Another CoGA account already uses the e-mail address.' } } },
      'Another CoGA account already uses the e-mail address.',
    ],
  ])('shows why the API refused %s, and keeps no session', async (_case, failure, shown) => {
    msal.client.handleRedirectPromise.mockResolvedValue({ accessToken: issued, account: ACCOUNT, state: undefined });
    serve({ meFailure: failure });

    render('/login');

    expect(await screen.findByText(shown)).toBeInTheDocument();
    expect(localStorage.getItem('token')).toBeNull();
    // Microsoft's tokens for the refused account are not kept in this tab.
    await waitFor(() => expect(msal.client.clearCache).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: 'Sign in with Microsoft' })).toBeEnabled();
  });

  it('shows a sign-in Microsoft did not complete', async () => {
    msal.client.handleRedirectPromise.mockRejectedValue(new Error('access_denied: the user cancelled'));
    serve();

    render('/login');

    expect(await screen.findByText('access_denied: the user cancelled')).toBeInTheDocument();
    expect(localStorage.getItem('token')).toBeNull();
  });

  it('offers to sign out of Microsoft too after signing out of CoGA', async () => {
    serve();
    render('/login?logged_out=1');

    expect(await screen.findByText('You have been signed out of CoGA.')).toBeInTheDocument();
    expect(screen.getByText(/Your Microsoft account may still be signed in in this browser/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out of Microsoft' }));

    await waitFor(() =>
      expect(msal.client.logoutRedirect).toHaveBeenCalledWith({
        postLogoutRedirectUri: `${window.location.origin}/login`,
      }),
    );
  });

  it('sends a stored session on without asking Microsoft', async () => {
    localStorage.setItem('token', issued);
    serve();

    render('/login');

    expect(screen.getByTestId('landed-on')).toHaveTextContent(/^\/dashboard$/);
    expect(msal.client.handleRedirectPromise).not.toHaveBeenCalled();
  });

  it('says when the sign-in cannot be loaded, and tries again', async () => {
    (api.get as unknown as Mock).mockRejectedValueOnce({ message: 'Network Error', code: 'ERR_NETWORK' });
    render('/login');

    expect(await screen.findByRole('alert')).toHaveTextContent(/The sign-in could not be loaded: Unable to reach the API/);
    expect(screen.queryByPlaceholderText(/password/i)).not.toBeInTheDocument();

    serve();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    expect(await screen.findByRole('button', { name: 'Sign in with Microsoft' })).toBeInTheDocument();
  });
});

describe('the emergency sign-in (/login/emergency)', () => {
  it('signs the emergency administrator in with a password, as a password session', async () => {
    serve({ me: { email: 'emergency@example.org', role: 'admin' } });
    (api.post as unknown as Mock).mockResolvedValue({ data: { access_token: issued, role: 'admin' } });
    render('/login/emergency');

    expect(await screen.findByRole('heading', { name: 'Emergency sign-in' })).toBeInTheDocument();
    expect(await screen.findByPlaceholderText(/email/i)).toBeInTheDocument();
    expect(
      screen.getByText(/For the emergency administrator only, when the Microsoft sign-in cannot be used\./),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sign in with Microsoft' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to the Microsoft sign-in' })).toHaveAttribute('href', '/login');
    expect(screen.queryByRole('link', { name: /sign up/i })).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/email/i), { target: { value: 'emergency@example.org' } });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: ['emergency', 'phrase'].join(' ') },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Login' }));

    expect(await screen.findByTestId('landed-on')).toHaveTextContent(/^\/dashboard$/);
    expect(api.post).toHaveBeenCalledWith('/auth/login', {
      email: 'emergency@example.org',
      password: ['emergency', 'phrase'].join(' '),
    });
    expect(localStorage.getItem('sign_in')).toBe('password');
    expect(localStorage.getItem('role')).toBe('admin');
    // The emergency page completes no Microsoft sign-in.
    expect(msal.client.handleRedirectPromise).not.toHaveBeenCalled();
  });

  it('is the ordinary sign-in without Microsoft sign-in', async () => {
    serve({ microsoft: null });

    render('/login/emergency', <LoginPage />);

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(await screen.findByPlaceholderText(/email/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign up' })).toBeInTheDocument();
    expect(msal.configurations).toEqual([]);
  });
});
