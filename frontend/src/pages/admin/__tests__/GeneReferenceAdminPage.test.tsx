import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import GeneReferenceAdminPage from '../GeneReferenceAdminPage';
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
        <GeneReferenceAdminPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('GeneReferenceAdminPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    (api.get as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        active_job: null,
        recent_jobs: [
          {
            _id: 'job-1',
            scope: 'all_human',
            symbol: null,
            status: 'completed',
            requested_by: 'admin@example.com',
            requested_at: '2026-03-27T12:00:00Z',
            started_at: '2026-03-27T12:00:05Z',
            heartbeat_at: '2026-03-27T12:01:00Z',
            completed_at: '2026-03-27T12:20:00Z',
            total_symbols: 20000,
            completed_symbols: 20000,
            updated_records: 42000,
            human_assemblies: 2,
            current_symbol: null,
            error: null,
            metadata: {},
          },
        ],
        source_summaries: [
          {
            source: 'hgnc',
            latest_fetched_at: '2026-03-27T12:00:00Z',
            success_count: 18000,
            missing_count: 1000,
            error_count: 100,
            record_count: 19100,
          },
        ],
        total_cached_records: 38200,
        human_gene_symbols: 19100,
        human_assemblies: 2,
        last_completed_at: '2026-03-27T10:00:00Z',
      },
    });
  });

  it('renders status and starts a single-gene refresh job', async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        _id: 'job-2',
        scope: 'symbol',
        symbol: 'BRCA1',
        status: 'queued',
        requested_by: 'admin@example.com',
        requested_at: '2026-03-27T12:30:00Z',
        total_symbols: 0,
        completed_symbols: 0,
        updated_records: 0,
        human_assemblies: 0,
        metadata: {},
      },
    });

    renderPage();

    expect(await screen.findByText(/cached source coverage/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /gene reference sync/i })).toBeInTheDocument();
    expect(screen.getByText(/no refresh job is active at the moment/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/refresh gene symbol/i), { target: { value: 'brca1' } });
    fireEvent.click(screen.getByRole('button', { name: /refresh gene/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/admin/gene-reference/refresh-gene',
        null,
        expect.objectContaining({
          params: { symbol: 'BRCA1' },
        }),
      ),
    );
  });
});
