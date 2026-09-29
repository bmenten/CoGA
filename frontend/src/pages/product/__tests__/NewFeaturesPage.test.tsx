import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import NewFeaturesPage from '../NewFeaturesPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import api from '../../../lib/api';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
  },
}));

const renderPage = () => {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <NewFeaturesPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const CATALOG = {
  repository: 'bmenten/coga',
  repository_url: 'https://github.com/bmenten/coga',
  releases_url: 'https://github.com/bmenten/coga/releases',
  issues_url: 'https://github.com/bmenten/coga/issues/new/choose',
  repo_visibility: 'private',
  sync_status: 'ok',
  sync_error: null,
  fetched_at: '2026-04-23T12:30:00Z',
  releases: [
    {
      version: 'v1.4.0',
      name: 'Pair-level release',
      published_at: '2026-04-20T09:30:00Z',
      summary: 'Added pair-level compound-het search results',
      url: 'https://github.com/bmenten/coga/releases/tag/v1.4.0',
      prerelease: false,
    },
  ],
};
const CMGGMC_ROUTE = 'https://cmggmc.example.org/probleemmelding';
const mockedGet = () => api.get as unknown as ReturnType<typeof vi.fn>;

describe('NewFeaturesPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('renders synced GitHub release history and issue links', async () => {
    mockedGet().mockResolvedValue({ data: CATALOG });

    renderPage();

    expect(await screen.findByRole('heading', { name: /new features and release history/i })).toBeInTheDocument();
    expect(screen.getByText(/repository bmenten\/coga/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open github releases/i })).toHaveAttribute(
      'href',
      'https://github.com/bmenten/coga/releases'
    );
    expect(screen.getByRole('link', { name: /submit issue \/ request/i })).toHaveAttribute(
      'href',
      'https://github.com/bmenten/coga/issues/new/choose'
    );
    expect(screen.getByText('v1.4.0')).toBeInTheDocument();
    expect(screen.getByText(/pair-level compound-het search results/i)).toBeInTheDocument();
  });

  // TF-16/TF-17: a problem goes through the CMGG route; SECURITY.md keeps clinical incidents
  // off GitHub.
  it('sends a problem report to the configured CMGGMC route, not to the GitHub issue form', async () => {
    vi.stubEnv('VITE_PROBLEM_REPORT_URL', CMGGMC_ROUTE);
    mockedGet().mockResolvedValue({ data: CATALOG });

    renderPage();

    expect(await screen.findByRole('link', { name: 'Report a problem' })).toHaveAttribute('href', CMGGMC_ROUTE);
    expect(screen.queryByRole('link', { name: /submit issue/i })).not.toBeInTheDocument();
  });

  it('offers no GitHub issue form in a production build without that route', async () => {
    vi.stubEnv('VITE_PROBLEM_REPORT_URL', '');
    vi.stubEnv('PROD', true);
    vi.stubEnv('DEV', false);
    mockedGet().mockResolvedValue({ data: CATALOG });

    renderPage();

    expect(await screen.findByRole('heading', { name: /new features and release history/i })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /submit issue|report a problem/i })).not.toBeInTheDocument();
    // The release notes stay on GitHub.
    expect(screen.getByRole('link', { name: /open github releases/i })).toBeInTheDocument();
  });

  it('offers the same route when the release feed could not be loaded', async () => {
    vi.stubEnv('VITE_PROBLEM_REPORT_URL', CMGGMC_ROUTE);
    mockedGet().mockRejectedValue(new Error('HTTP 502'));

    renderPage();

    expect(await screen.findByRole('link', { name: 'Report a problem' })).toHaveAttribute('href', CMGGMC_ROUTE);
    expect(screen.queryByRole('link', { name: /submit issue/i })).not.toBeInTheDocument();
  });
});
