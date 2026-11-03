import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import AdminPresetFiltersPage from '../AdminPresetFiltersPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
  },
}));

const renderPage = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <AdminPresetFiltersPage />
      </MemoryRouter>
    </QueryClientProvider>
  );

/** Serves `answer` for the presets of every user. */
const servePresets = (answer: () => Promise<unknown>) =>
  vi.mocked(api.get).mockImplementation((url: string) =>
    url === '/admin/small-variant-filter-presets' ? answer() : Promise.resolve({ data: [] }),
  );

describe('AdminPresetFiltersPage', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('renders the preset table', async () => {
    servePresets(() =>
      Promise.resolve({
        data: [
          {
            _id: 'preset-1',
            owner: 'reviewer',
            name: 'Dominant shortlist',
            description: 'Reusable dominant review filter',
            filters: { impact: ['HIGH'] },
            sample_filters: {},
            sample_templates: {},
            created_at: '2026-04-14T10:00:00Z',
            updated_at: '2026-04-14T10:00:00Z',
          },
        ],
      }),
    );

    renderPage();

    expect(
      await screen.findByText('Preset filter management')
    ).toBeInTheDocument();
    expect(await screen.findByText('Dominant shortlist')).toBeInTheDocument();
    expect(screen.getByText('reviewer')).toBeInTheDocument();
    // The administrator audits the presets here; each owner removes their own.
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
  });

  it('says no filter is saved yet', async () => {
    servePresets(() => Promise.resolve({ data: [] }));
    renderPage();
    expect(await screen.findByText('No saved filters available.')).toBeInTheDocument();
  });

  it('says the presets could not be loaded, and why', async () => {
    servePresets(() => Promise.reject({ response: { status: 500, data: { detail: 'Database unavailable.' } } }));
    renderPage();

    expect(await screen.findByText('Could not load preset filters')).toBeInTheDocument();
    expect(screen.getByText('Database unavailable.')).toBeInTheDocument();
    expect(screen.queryByText('No saved filters available.')).not.toBeInTheDocument();
  });
});
