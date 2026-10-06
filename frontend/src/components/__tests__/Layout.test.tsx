// Logout — #521: it cleared the session but left the previous user's query cache.
// Main menu — the main sections behind the arrow at the right of the header; Admin only for an
// admin.
// Footer — the device label (TF-15 §1): the running build, the in-house IVD status and the
// manufacturer, and a problem report that goes to the CMGG route, not to GitHub.

import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Layout from '../Layout';
import { createTestQueryClient } from '../../test/createTestQueryClient';

const telemetry = vi.hoisted(() => ({ flushUiEventsNow: vi.fn() }));
vi.mock('../../lib/telemetry', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/telemetry')>()),
  flushUiEventsNow: telemetry.flushUiEventsNow,
}));

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../lib/api', () => ({ default: apiMock }));

const RUNNING_BUILD = { version: '0.1.0-beta.1', git_sha: '0123456789abcdef' };
const CMGGMC_ROUTE = 'https://cmggmc.example.org/probleemmelding';

beforeEach(() => {
  apiMock.get.mockImplementation((url: string) =>
    url === '/version'
      ? Promise.resolve({ data: RUNNING_BUILD })
      : Promise.reject(new Error(`unexpected request ${url}`)),
  );
});

const renderLayout = (queryClient = createTestQueryClient()) =>
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/dashboard']}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/dashboard" element={<p>Dashboard</p>} />
          </Route>
          <Route path="/login" element={<p>Login page</p>} />
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
