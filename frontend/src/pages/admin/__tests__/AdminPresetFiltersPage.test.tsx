import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, type Mock } from 'vitest';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import AdminPresetFiltersPage from '../AdminPresetFiltersPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
  },
}));

describe('AdminPresetFiltersPage', () => {
  it('renders the preset table', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/admin/small-variant-filter-presets') {
        return Promise.resolve({
          data: [
            {
              _id: 'preset-1',
              family_id: null,
              scope: 'global',
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
        });
      }
      return Promise.resolve({ data: [] });
    });

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AdminPresetFiltersPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(
      await screen.findByText('Preset filter management')
    ).toBeInTheDocument();
    expect(await screen.findByText('Dominant shortlist')).toBeInTheDocument();
    expect(screen.getByText('reviewer')).toBeInTheDocument();
  });
});
