import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { describe, it, vi } from 'vitest';
import GenePanelDetailPage from '../GenePanelDetailPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn().mockResolvedValue({
      data: {
        _id: '1',
        name: 'PanelA',
        genes: ['BRCA1', 'BRCA2'],
        gene_count: 2,
        regions: [
          { gene: 'BRCA1', chr: 'chr1', start: 1, end: 2 },
          { gene: 'BRCA2', chr: 'chr2', start: 3, end: 4 },
        ],
        created_by: 'admin-id',
        created_by_email: 'admin@example.com',
        created_at: '2026-04-28T08:00:00Z',
        description: 'Hereditary cancer genes',
      },
    }),
  },
}));

describe('GenePanelDetailPage', () => {
  it('allows sorting and filtering', async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/panels/1']}>
          <Routes>
            <Route path="/panels/:panelId" element={<GenePanelDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByText('BRCA1')).toBeInTheDocument());

    const rows = screen.getAllByRole('row');
    const firstDataRow = rows[2];
    expect(within(firstDataRow).getByText('BRCA1')).toBeInTheDocument();

    await userEvent.click(screen.getByText(/Gene/));
    const firstAfterSort = screen.getAllByRole('row')[2];
    expect(within(firstAfterSort).getByText('BRCA2')).toBeInTheDocument();

    await userEvent.type(
      screen.getByPlaceholderText('Filter gene'),
      'BRCA2',
    );
    expect(screen.queryByText('BRCA1')).not.toBeInTheDocument();
    expect(screen.getByText('BRCA2')).toBeInTheDocument();
  });
});
