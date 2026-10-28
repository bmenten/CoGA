// Logout — #521: it cleared the session but left the previous user's query cache. A session
// signed in with Microsoft also forgets the Microsoft account's tokens in the tab (SEC-4).
// Main menu — the main sections behind the arrow at the right of the header; Admin only for an
// admin.
// Footer — the device label (TF-15 §1): the running build, the in-house IVD status and the
// manufacturer, and a problem report that goes to the CMGG route, not to GitHub.

import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Layout from '../Layout';
import { createTestQueryClient } from '../../test/createTestQueryClient';
import { inAnotherTab } from '../../test/otherTab';

const telemetry = vi.hoisted(() => ({ flushUiEventsNow: vi.fn() }));
vi.mock('../../lib/telemetry', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/telemetry')>()),
  flushUiEventsNow: telemetry.flushUiEventsNow,
}));

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../lib/api', () => ({ default: apiMock }));

const microsoftSignIn = vi.hoisted(() => ({ forgetMicrosoftAccount: vi.fn() }));
vi.mock('../../lib/microsoftSignIn', () => microsoftSignIn);

const RUNNING_BUILD = { version: '0.1.0-beta.1', git_sha: '0123456789abcdef' };
const CMGGMC_ROUTE = 'https://cmggmc.example.org/probleemmelding';

beforeEach(() => {
  apiMock.get.mockImplementation((url: string) =>
    url === '/version'
      ? Promise.resolve({ data: RUNNING_BUILD })
      : Promise.reject(new Error(`unexpected request ${url}`)),
  );
});

// The sign-in page, and the query string it was opened with.
const LoginPageProbe = () => (
  <>
    <p>Login page</p>
    <p data-testid="login-query">{useLocation().search}</p>
  </>
);

// As in the app, the sign-in page is shown in the layout too.
const renderLayout = (queryClient = createTestQueryClient(), page = '/dashboard') =>
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[page]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/dashboard" element={<p>Dashboard</p>} />
            <Route path="/login" element={<LoginPageProbe />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('Layout logout', () => {
  it('flushes telemetry, then clears the cached data and the session', () => {
    localStorage.setItem('token', 'token-1');
    localStorage.setItem('username', 'alice@example.org');
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(['family', 'F1'], { family_id: 'F1' });

    renderLayout(queryClient);

    fireEvent.click(screen.getByRole('button', { name: /logout/i }));

    expect(telemetry.flushUiEventsNow).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(['family', 'F1'])).toBeUndefined();
    expect(localStorage.getItem('token')).toBeNull();
    expect(screen.getByText('Login page')).toBeInTheDocument();
    // A password session has no Microsoft account to forget.
    expect(microsoftSignIn.forgetMicrosoftAccount).not.toHaveBeenCalled();
  });

  it('forgets the Microsoft account in the tab too after a Microsoft sign-in', () => {
    microsoftSignIn.forgetMicrosoftAccount.mockResolvedValue(undefined);
    localStorage.setItem('token', ['microsoft', 'session'].join('-'));
    localStorage.setItem('username', 'alice@example.org');
    localStorage.setItem('sign_in', 'microsoft');

    renderLayout();

    fireEvent.click(screen.getByRole('button', { name: /logout/i }));

    expect(microsoftSignIn.forgetMicrosoftAccount).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('token')).toBeNull();
    expect(localStorage.getItem('sign_in')).toBeNull();
    expect(screen.getByText('Login page')).toBeInTheDocument();
  });
});

// A logout in another tab of the browser removes the session the tabs share, and the browser
// tells this tab of each key removed. This tab went on showing the user's family data, with its
// Microsoft account and its tokens in the tab. A sign-in of another account there replaced the
// session without a logout: this tab went on showing the first account's pages while its
// requests went out as the other account (both found while fixing SEC-36).
describe('Layout: the session ended or replaced in another tab', () => {
  const [token, renewed, next] = ['microsoft', 'renewed', 'next'].map((part) => [part, 'session'].join('-'));

  const signInWithMicrosoft = () => {
    localStorage.setItem('token', token);
    localStorage.setItem('username', 'analyst@example.org');
    localStorage.setItem('role', 'viewer');
    localStorage.setItem('sign_in', 'microsoft');
  };

  // Another tab: the same browser storage, and a page of its own.
  const anotherTab = async () => {
    vi.resetModules();
    return import('../../lib/auth');
  };

  const renderSignedIn = () => {
    signInWithMicrosoft();
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(['family', 'F1'], { family_id: 'F1' });
    renderLayout(queryClient);
    expect(screen.getByText('Dashboard', { selector: 'p' })).toBeInTheDocument();
    return queryClient;
  };

  // This tab's session ended: its data, its Microsoft account and its page.
  const expectSessionEnded = (queryClient: ReturnType<typeof createTestQueryClient>) => {
    expect(queryClient.getQueryData(['family', 'F1'])).toBeUndefined();
    // How the session signed in may have gone with the other tab's write: the tab forgets a
    // Microsoft account whichever it was.
    expect(microsoftSignIn.forgetMicrosoftAccount).toHaveBeenCalledTimes(1);
    // The tab's queued events are kept for their account, never sent under another.
    expect(telemetry.flushUiEventsNow).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Login page')).toBeInTheDocument();
    expect(screen.getByTestId('login-query')).toHaveTextContent(/^\?logged_out=1$/);
    expect(screen.queryByRole('button', { name: 'Logout' })).not.toBeInTheDocument();
  };

  it('a logout there ends the session in this tab too', async () => {
    microsoftSignIn.forgetMicrosoftAccount.mockResolvedValue(undefined);
    const queryClient = renderSignedIn();
    const tabB = await anotherTab();

    inAnotherTab(() => tabB.clearSession());

    expectSessionEnded(queryClient);
  });

  it('a sign-in there of another account ends the session in this tab, and no tab goes on as that account', async () => {
    microsoftSignIn.forgetMicrosoftAccount.mockResolvedValue(undefined);
    const queryClient = renderSignedIn();
    const tabB = await anotherTab();

    inAnotherTab(() => tabB.persistSession(next, 'colleague@example.org', 'viewer', 'microsoft'));

    expectSessionEnded(queryClient);
    // The other account's session is cleared with it, in every tab: it signs in again.
    expect(localStorage.getItem('token')).toBeNull();
    expect(localStorage.getItem('username')).toBeNull();
  });

  it('a sign-in there of the same account keeps this tab signed in: the token is replaced, the account stays', async () => {
    const queryClient = renderSignedIn();
    const tabB = await anotherTab();

    inAnotherTab(() => tabB.persistSession(next, 'analyst@example.org', 'viewer', 'microsoft'));

    expect(screen.getByText('Dashboard', { selector: 'p' })).toBeInTheDocument();
    expect(queryClient.getQueryData(['family', 'F1'])).toEqual({ family_id: 'F1' });
    expect(microsoftSignIn.forgetMicrosoftAccount).not.toHaveBeenCalled();
    expect(localStorage.getItem('token')).toBe(next);
  });

  it('a renewal of the token there keeps this tab signed in', async () => {
    const queryClient = renderSignedIn();
    const tabB = await anotherTab();

    inAnotherTab(() => tabB.updateSessionToken(renewed));

    expect(screen.getByText('Dashboard', { selector: 'p' })).toBeInTheDocument();
    expect(queryClient.getQueryData(['family', 'F1'])).toEqual({ family_id: 'F1' });
    expect(microsoftSignIn.forgetMicrosoftAccount).not.toHaveBeenCalled();
    expect(localStorage.getItem('token')).toBe(renewed);
  });

  it('leaves a sign-in page as it is: it has no session to end', async () => {
    // A session another tab holds, which it logs out of while this tab shows the sign-in page.
    signInWithMicrosoft();
    renderLayout(createTestQueryClient(), '/login?next=%2Ffamilies%2FF1');
    const tabB = await anotherTab();

    inAnotherTab(() => tabB.clearSession());

    expect(screen.getByTestId('login-query')).toHaveTextContent(/^\?next=%2Ffamilies%2FF1$/);
    expect(microsoftSignIn.forgetMicrosoftAccount).not.toHaveBeenCalled();
    expect(telemetry.flushUiEventsNow).not.toHaveBeenCalled();
  });
});

describe('Layout main menu', () => {
  afterEach(() => {
    localStorage.clear();
  });

  const signIn = (role: string) => {
    localStorage.setItem('token', 'token-1');
    localStorage.setItem('username', 'alice@example.org');
    localStorage.setItem('role', role);
  };

  it('keeps the main sections behind the arrow until it is opened', () => {
    signIn('viewer');
    renderLayout();

    const toggle = screen.getByRole('button', { name: 'Main menu' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const nav = screen.getByRole('navigation', { name: 'Main' });
    const projects = within(nav).getByRole('link', { name: 'Projects' });
    expect(projects).toHaveAttribute('href', '/dashboard');
    expect(projects).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'Variant explorer' })).toHaveAttribute(
      'href',
      '/variant-explorer',
    );
    expect(within(nav).getByRole('link', { name: 'Gene explorer' })).toHaveAttribute(
      'href',
      '/genes',
    );
    expect(within(nav).getByRole('link', { name: 'CNV explorer' })).toHaveAttribute(
      'href',
      '/cnv-explorer',
    );
    expect(within(nav).getByRole('link', { name: 'Panels' })).toHaveAttribute('href', '/panels');
    expect(within(nav).getByRole('link', { name: 'Report codes' })).toHaveAttribute(
      'href',
      '/report-codes',
    );
    expect(within(nav).getByRole('link', { name: 'User guide' })).toHaveAttribute('href', '/docs');
    // Not an admin: no way into the admin pages from the menu.
    expect(within(nav).queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument();
  });

  it('adds Admin for an admin', () => {
    signIn('admin');
    renderLayout();

    fireEvent.click(screen.getByRole('button', { name: 'Main menu' }));

    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Admin' })).toHaveAttribute('href', '/admin');
  });

  it('closes on Escape, back to the arrow, and on a click elsewhere', () => {
    signIn('viewer');
    renderLayout();
    const toggle = screen.getByRole('button', { name: 'Main menu' });

    fireEvent.click(toggle);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();

    fireEvent.click(toggle);
    fireEvent.pointerDown(document.body);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument();
  });

  it('closes when a section is chosen', () => {
    signIn('viewer');
    renderLayout();

    fireEvent.click(screen.getByRole('button', { name: 'Main menu' }));
    fireEvent.click(
      within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Projects' }),
    );

    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument();
  });

  it('shows no menu before sign-in', () => {
    renderLayout();

    expect(screen.queryByRole('button', { name: 'Main menu' })).not.toBeInTheDocument();
  });
});

describe('Layout footer: the device label', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('names the running build, the in-house IVD status and the manufacturer', async () => {
    renderLayout();
    const footer = screen.getByRole('contentinfo');

    // The build as the backend reports it, SHA shortened and in its own case.
    expect(await within(footer).findByText('Version 0.1.0-beta.1 (0123456)')).toBeInTheDocument();
    expect(apiMock.get).toHaveBeenCalledWith('/version');
    expect(footer).toHaveTextContent('CoGA, Comprehensive Genomic Analysis');
    expect(footer).toHaveTextContent(
      'In-house IVD per IVDR Article 5(5) · Not CE-marked · For internal CMGG use only',
    );
    expect(footer).toHaveTextContent(
      'Manufacturer: Center for Medical Genetics, Ghent University Hospital, C. Heymanslaan 10, 9000 Ghent',
    );
  });

  it('says the version is unavailable when it could not be loaded, instead of leaving it out', async () => {
    apiMock.get.mockRejectedValue(Object.assign(new Error('HTTP 503'), { response: { status: 503 } }));
    renderLayout();

    expect(
      await within(screen.getByRole('contentinfo')).findByText('Version unavailable'),
    ).toBeInTheDocument();
  });

  it('sends a problem report to the configured CMGGMC route, not to GitHub', () => {
    vi.stubEnv('VITE_PROBLEM_REPORT_URL', CMGGMC_ROUTE);
    renderLayout();

    const link = screen.getByRole('link', { name: 'Report a problem' });
    expect(link).toHaveAttribute('href', CMGGMC_ROUTE);
    expect(link).toHaveAttribute('target', '_blank');
    expect(screen.queryByRole('link', { name: 'Submit issue / request' })).not.toBeInTheDocument();
  });

  it('offers no GitHub issue form in a production build without that route', () => {
    vi.stubEnv('VITE_PROBLEM_REPORT_URL', '');
    vi.stubEnv('PROD', true);
    vi.stubEnv('DEV', false);
    renderLayout();

    const footer = screen.getByRole('contentinfo');
    expect(within(footer).queryByRole('link', { name: /submit issue|report a problem/i })).not.toBeInTheDocument();
    expect(
      within(footer)
        .getAllByRole('link')
        .filter((link) => link.getAttribute('href')?.includes('/issues')),
    ).toEqual([]);
    // The rest of the footer is unchanged.
    expect(within(footer).getByRole('link', { name: 'New features' })).toBeInTheDocument();
    expect(within(footer).getByRole('link', { name: 'Contact' })).toBeInTheDocument();
  });

  it('keeps the GitHub issue form in a development build without that route', () => {
    vi.stubEnv('VITE_PROBLEM_REPORT_URL', '');
    renderLayout();

    expect(screen.getByRole('link', { name: 'Submit issue / request' })).toHaveAttribute(
      'href',
      'https://github.com/bmenten/coga/issues/new/choose',
    );
  });
});
