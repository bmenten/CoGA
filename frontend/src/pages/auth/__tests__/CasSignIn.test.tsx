// The sign-in page with the organisation's CAS sign-in configured, such as login.ugent.be (SEC-4).
//
// The page asks the backend how users sign in (GET /auth/config). With CAS it offers only
// "Sign in with UGent": no password form and no sign-up. The sign-in goes to the CAS login page
// for CoGA's service, and the page asked for stays in this tab. Back on /login with a service
// ticket, the page has the API check it (POST /auth/cas/session), takes the ticket out of the
// address bar, checks the token (GET /auth/me) and starts the session (marked as a CAS one),
// then opens the page asked for. An account that waits for approval is told so, and no session
// starts. After a sign-out the page offers to sign out of the CAS server too. /login/emergency
// stays the emergency administrator's password sign-in. Values are made up; tokens are put
// together here.
import { fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import LoginPage from '../LoginPage';
import api from '../../../lib/api';
import { clearSession, getAuthToken, getSessionSignIn } from '../../../lib/auth';
import { casTicketFrom } from '../../../lib/casSignIn';
import { resetMicrosoftSignInForTests } from '../../../lib/microsoftSignIn';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api');

const CAS = {
  display_name: 'UGent',
  login_url: 'https://login.example.org/login?service=https%3A%2F%2Fcoga.example.org%2Flogin',
  logout_url: 'https://login.example.org/logout',
};
const TICKET = ['ST', 'a1b2c3d4e5f6'].join('-');
const WAITING = "Your CoGA account waits for an administrator's approval. You can sign in once it is activated.";
// What the API issues, put together here.
const issued = ['cas', 'session', 'one'].join('-');

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

const serve = ({ session, sessionFailure }: { session?: unknown; sessionFailure?: unknown } = {}) => {
  (api.get as unknown as Mock).mockImplementation(async (url: string) => {
    if (url === '/auth/config') return { data: { microsoft: null, cas: CAS } };
    if (url === '/auth/me') return { data: { email: 'a.colleague@example.org', role: 'viewer' } };
    throw new Error(`unexpected GET ${url}`);
  });
  (api.post as unknown as Mock).mockImplementation(async (url: string) => {
    if (url !== '/auth/cas/session') throw new Error(`unexpected POST ${url}`);
    if (sessionFailure) throw sessionFailure;
    return { data: session };
  });
};

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

describe('the CAS sign-in', () => {
  it('offers only the organisation sign-in, and goes to the CAS login page keeping the page asked for', async () => {
    serve();
    render('/login?next=%2Ffamilies%2FFAM1');

    const button = await screen.findByRole('button', { name: 'Sign in with UGent' });
    expect(screen.queryByLabelText('Password')).toBeNull();
    expect(screen.queryByRole('link', { name: 'Sign up' })).toBeNull();

    fireEvent.click(button);

    expect(assign).toHaveBeenCalledWith(CAS.login_url);
    expect(sessionStorage.getItem('coga.cas-sign-in.next')).toBe('/families/FAM1');
  });

  it('has the API check the ticket, starts a CAS session and opens the page asked for', async () => {
    serve({ session: { access_token: issued, token_type: 'bearer', role: 'viewer' } });
    sessionStorage.setItem('coga.cas-sign-in.next', '/families/FAM1');
    render(`/login?ticket=${TICKET}`);

    expect(await screen.findByTestId('landed-on')).toHaveTextContent('/families/FAM1');
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/auth/cas/session', { ticket: TICKET });
    expect(api.get).toHaveBeenCalledWith('/auth/me', { headers: { Authorization: `Bearer ${issued}` } });
    expect(getSessionSignIn()).toBe('cas');
    expect(getAuthToken()).toBe(issued);
    expect(sessionStorage.getItem('coga.cas-sign-in.next')).toBeNull();
  });

  it('tells an account that waits for approval so, and starts no session', async () => {
    serve({ sessionFailure: { isAxiosError: true, response: { status: 403, data: { detail: WAITING } } } });
    render(`/login?ticket=${TICKET}`);

    expect(await screen.findByText(WAITING)).toBeInTheDocument();
    expect(getAuthToken()).toBeNull();
    expect(screen.getByRole('button', { name: 'Sign in with UGent' })).toBeEnabled();
  });

  it('offers to sign out of the CAS server after a sign-out', async () => {
    serve();
    render('/login?logged_out=1');

    const signOut = await screen.findByRole('link', { name: 'Sign out of UGent' });
    expect(signOut).toHaveAttribute('href', CAS.logout_url);
    expect(screen.getByText('You have been signed out of CoGA.')).toBeInTheDocument();
  });

  it('keeps the emergency administrator’s password sign-in at /login/emergency', async () => {
    serve();
    render('/login/emergency');

    expect(await screen.findByLabelText('Password')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to the UGent sign-in' })).toBeInTheDocument();
    await waitFor(() => expect(api.post).not.toHaveBeenCalled());
  });

  it('reads only a service ticket from the address', () => {
    expect(casTicketFrom(`?ticket=${TICKET}`)).toBe(TICKET);
    expect(casTicketFrom('?ticket=PT-123')).toBeNull();
    expect(casTicketFrom('?ticket=ST-abc%20def')).toBeNull();
    expect(casTicketFrom('?next=/x')).toBeNull();
  });
});
