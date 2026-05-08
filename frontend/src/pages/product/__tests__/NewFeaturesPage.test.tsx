import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

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

describe('NewFeaturesPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders synced GitHub release history and issue links', async () => {
    (api.get as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
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
      },
    });

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
});
