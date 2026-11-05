// The sign-in page with the organisation's OAuth 2.0 sign-in configured, such as the OAuth
// application Ghent University's ICT registers (SEC-4).
//
// With OAuth the page offers only "Sign in with UGent": no password form and no sign-up. The
// sign-in goes to the server's authorization page with a one-time state and a PKCE challenge
// (S256), and keeps the state, the verifier and the page asked for in this tab. Back on /login
// with a code and that state, the page has the API redeem the code with the verifier
// (POST /auth/oauth/session), takes the code out of the address bar, checks the token
// (GET /auth/me) and starts the session (marked as an OAuth one), then opens the page asked
// for. A code with another state, or one no sign-in in this tab started, never reaches the
// API; a refusal by the server is shown by its error code. An account that waits for approval
// is told so. A sign-out link is offered only when the server has a sign-out page. Values are
// made up; tokens are put together here.
import { fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import LoginPage from '../LoginPage';
import api from '../../../lib/api';
import { clearSession, getAuthToken, getSessionSignIn } from '../../../lib/auth';
import { oauthReturnFrom } from '../../../lib/oauthSignIn';
import { resetMicrosoftSignInForTests } from '../../../lib/microsoftSignIn';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api');

const AUTHORIZE =
  'https://login.example.org/oauth/authorize?response_type=code&client_id=coga-test&redirect_uri=https%3A%2F%2Fcoga.example.org%2Flogin';
const OAUTH = { display_name: 'UGent', authorize_url: AUTHORIZE, logout_url: null as string | null };
const CODE = ['c0de', 'a1b2c3d4e5f6'].join('-');
const STATE = 'S'.repeat(43);
const VERIFIER = 'v'.repeat(43);
const PENDING_KEY = 'coga.oauth-sign-in';
const WAITING = "Your CoGA account waits for an administrator's approval. You can sign in once it is activated.";
// What the API issues, put together here.
const issued = ['oauth', 'session', 'one'].join('-');

let queryClient: QueryClient;
let assign: Mock;
const realLocation = window.location;

const LandedOn = () => {
  const location = useLocation();
  return <p data-testid="landed-on">{`${location.pathname}${location.search}`}</p>;
};

const render = (entry: string) =>
  rtlRender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/login/emergency" element={<LoginPage emergency />} />
          <Route path="*" element={<LandedOn />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

const serve = ({
  session,
  sessionFailure,
  oauth = OAUTH,
}: { session?: unknown; sessionFailure?: unknown; oauth?: typeof OAUTH } = {}) => {
  (api.get as unknown as Mock).mockImplementation(async (url: string) => {
    if (url === '/auth/config') return { data: { microsoft: null, cas: null, oauth } };
    if (url === '/auth/me') return { data: { email: 'a.colleague@example.org', role: 'viewer' } };
    throw new Error(`unexpected GET ${url}`);
  });
  (api.post as unknown as Mock).mockImplementation(async (url: string) => {
    if (url !== '/auth/oauth/session') throw new Error(`unexpected POST ${url}`);
    if (sessionFailure) throw sessionFailure;
    return { data: session };
  });
};

const keepPending = (state = STATE) =>
  sessionStorage.setItem(PENDING_KEY, JSON.stringify({ state, verifier: VERIFIER, next: '/families/FAM1' }));

const base64Url = (bytes: ArrayBuffer) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');

beforeEach(() => {
  vi.resetAllMocks();
  resetMicrosoftSignInForTests();
  clearSession();
  localStorage.clear();
  sessionStorage.clear();
  queryClient = createTestQueryClient();
  assign = vi.fn();
  Object.defineProperty(window, 'location', { configurable: true, value: { ...realLocation, assign } });
});

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: realLocation });
});

describe('the OAuth sign-in', () => {
  it('offers only the organisation sign-in, and goes to the authorization page with a state and a PKCE challenge', async () => {
    serve();
    render('/login?next=%2Ffamilies%2FFAM1');

    const button = await screen.findByRole('button', { name: 'Sign in with UGent' });
    expect(screen.queryByLabelText('Password')).toBeNull();
    expect(screen.queryByRole('link', { name: 'Sign up' })).toBeNull();

    fireEvent.click(button);

    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
    const url = new URL(assign.mock.calls[0][0] as string);
    const pending = JSON.parse(sessionStorage.getItem(PENDING_KEY) ?? 'null');
    expect(`${url.origin}${url.pathname}`).toBe('https://login.example.org/oauth/authorize');
    expect(url.searchParams.get('client_id')).toBe('coga-test');
    expect(url.searchParams.get('redirect_uri')).toBe('https://coga.example.org/login');
    expect(url.searchParams.get('state')).toBe(pending.state);
    expect(pending.state).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(pending.verifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(pending.next).toBe('/families/FAM1');
    expect(url.searchParams.get('code_challenge_method')).toBe('S256');
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(pending.verifier));
    expect(url.searchParams.get('code_challenge')).toBe(base64Url(digest));
    // The verifier itself never leaves the tab for the authorization page.
    expect(url.toString()).not.toContain(pending.verifier);
  });

  it('has the API redeem the code with the verifier, starts an OAuth session and opens the page asked for', async () => {
    serve({ session: { access_token: issued, token_type: 'bearer', role: 'viewer' } });
    keepPending();
    render(`/login?code=${CODE}&state=${STATE}`);

    expect(await screen.findByTestId('landed-on')).toHaveTextContent('/families/FAM1');
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/auth/oauth/session', { authorization_code: CODE, code_verifier: VERIFIER });
    expect(api.get).toHaveBeenCalledWith('/auth/me', { headers: { Authorization: `Bearer ${issued}` } });
    expect(getSessionSignIn()).toBe('oauth');
    expect(getAuthToken()).toBe(issued);
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('refuses a code that comes back with another state, or that no sign-in in this tab started', async () => {
    serve();
    keepPending('T'.repeat(43));
    const first = render(`/login?code=${CODE}&state=${STATE}`);

    expect(await screen.findByText('This sign-in was not started in this tab. Sign in again.')).toBeInTheDocument();
    first.unmount();

    render(`/login?code=${CODE}&state=${STATE}`);
    expect(await screen.findByText('This sign-in was not started in this tab. Sign in again.')).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
    expect(getAuthToken()).toBeNull();
  });

  it('shows a refusal by the server by its error code', async () => {
    serve();
    keepPending();
    render(`/login?error=access_denied&error_description=${encodeURIComponent('<b>no</b>')}&state=${STATE}`);

    expect(await screen.findByText('The UGent sign-in did not complete (access_denied). Sign in again.')).toBeInTheDocument();
    expect(screen.queryByText(/<b>no<\/b>/)).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('tells an account that waits for approval so, and starts no session', async () => {
    serve({ sessionFailure: { isAxiosError: true, response: { status: 403, data: { detail: WAITING } } } });
    keepPending();
    render(`/login?code=${CODE}&state=${STATE}`);

    expect(await screen.findByText(WAITING)).toBeInTheDocument();
    expect(getAuthToken()).toBeNull();
    expect(screen.getByRole('button', { name: 'Sign in with UGent' })).toBeEnabled();
  });

  it('offers a sign-out link only when the server has a sign-out page', async () => {
    serve();
    const without = render('/login?logged_out=1');
    expect(await screen.findByText('You have been signed out of CoGA.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Sign out of UGent' })).toBeNull();
    without.unmount();

    serve({ oauth: { ...OAUTH, logout_url: 'https://login.example.org/logout' } });
    queryClient = createTestQueryClient();
    render('/login?logged_out=1');
    const signOut = await screen.findByRole('link', { name: 'Sign out of UGent' });
    expect(signOut).toHaveAttribute('href', 'https://login.example.org/logout');
  });

  it('keeps the emergency administrator’s password sign-in at /login/emergency', async () => {
    serve();
    render('/login/emergency');

    expect(await screen.findByLabelText('Password')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to the UGent sign-in' })).toBeInTheDocument();
    await waitFor(() => expect(api.post).not.toHaveBeenCalled());
  });

  it('reads only a well-formed code and state, or an error code, from the address', () => {
    expect(oauthReturnFrom(`?code=${CODE}&state=${STATE}`)).toEqual({ code: CODE, state: STATE });
    expect(oauthReturnFrom(`?code=${CODE}`)).toBeNull();
    expect(oauthReturnFrom(`?code=a%20b&state=${STATE}`)).toBeNull();
    expect(oauthReturnFrom(`?code=${CODE}&state=short`)).toBeNull();
    expect(oauthReturnFrom('?error=access_denied')).toEqual({ error: 'access_denied' });
    expect(oauthReturnFrom('?error=%3Cscript%3E')).toEqual({ error: 'unknown_error' });
    expect(oauthReturnFrom('?next=/x')).toBeNull();
  });
});
