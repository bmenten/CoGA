import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MonarchDataAdminPage from '../MonarchDataAdminPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const renderPage = () => {
  const queryClient = createTestQueryClient();

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <MonarchDataAdminPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const statusPayload = {
  release_version: '2026-03-01',
  gene_disease_pairs: 13200,
  genes: 5463,
  diseases: 8900,
  causal_pairs: 7200,
  disease_phenotype_pairs: 245814,
  phenotype_diseases: 11230,
  phenotypes: 9000,
  last_updated_at: '2026-03-27T10:00:00Z',
};

describe('MonarchDataAdminPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    (api.get as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ data: statusPayload });
  });

  it('renders the loaded release and triggers a refresh', async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        release_version: '2026-04-01',
        files_loaded: 4,
        gene_disease_pairs: 13300,
        genes: 5470,
        diseases: 8910,
        causal_pairs: 7250,
        disease_phenotype_pairs: 246000,
        phenotype_diseases: 11240,
        phenotypes: 9010,
        excluded_phenotype_pairs: 0,
        completed_at: '2026-04-15T12:00:00Z',
        duration_seconds: 2.5,
      },
    });

    renderPage();

    expect(await screen.findByText('2026-03-01')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Monarch data' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /update monarch data/i }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/monarch/refresh'));
    expect(await screen.findByText(/updated to monarch release 2026-04-01/i)).toBeInTheDocument();
  });

  it('searches diseases and shows linked genes and phenotypes', async () => {
    (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url === '/admin/monarch/search') {
        return Promise.resolve({
          data: {
            query: 'huntington',
            total: 1,
            diseases: [
              {
                mondo_id: 'MONDO:0007739',
                disease_label: 'Huntington disease',
                match_type: 'disease',
                gene_count: 1,
                genes: [
                  { gene_symbol: 'HTT', hgnc_id: 'HGNC:4851', predicate: 'causes', causal: true },
                ],
                phenotype_count: 1,
                matched_phenotype_count: 1,
                phenotypes: [
                  { hpo_id: 'HP:0002072', phenotype_label: 'Chorea', matched: true },
                ],
              },
            ],
            gene_overview: {
              total: 1,
              genes: [
                { gene_symbol: 'HTT', hgnc_id: 'HGNC:4851', causal: true, disease_count: 1 },
              ],
            },
          },
        });
      }
      return Promise.resolve({ data: statusPayload });
    });

    renderPage();

    await screen.findByText('2026-03-01');
    fireEvent.change(screen.getByPlaceholderText(/MONDO:0007739/i), {
      target: { value: 'huntington' },
    });

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/admin/monarch/search', expect.objectContaining({
        params: { q: 'huntington', limit: 25 },
      }))
    );

    expect(await screen.findByText('Linked genes across all matches')).toBeInTheDocument();
    // HTT appears both in the aggregated overview chip and the per-disease detail.
    expect((await screen.findAllByText('HTT')).length).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText(/Chorea/)).toBeInTheDocument();
    expect(screen.getAllByText('Huntington disease').length).toBeGreaterThan(0);
  });
});
