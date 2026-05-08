import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, vi, beforeEach } from 'vitest';
import GenePanelsPage from '../GenePanelsPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn().mockResolvedValue({
      data: [
        {
          _id: '1',
          name: 'PanelA',
          genes: ['BRCA1'],
          gene_count: 1,
          regions: [{ chr: 'chr1', start: 0, end: 10000 }],
          created_by: 'admin-id',
          created_by_email: 'admin@example.com',
          created_at: '2026-04-28T08:00:00Z',
          description: 'Tumor suppressor panel',
        },
      ],
    }),
    post: vi.fn().mockResolvedValue({ data: { message: 'Panel created' } }),
  },
}));

describe('GenePanelsPage', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('lists panels from API for non-admin users', async () => {
    localStorage.setItem('role', 'viewer');
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <GenePanelsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    await waitFor(() => expect(screen.getByText('PanelA')).toBeInTheDocument());
    expect(
      screen.getByRole('link', { name: 'PanelA' })
    ).toHaveAttribute('href', '/panels/1');
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /create/i })).not.toBeInTheDocument();
  });

  it('shows server message when creation returns warning', async () => {
    localStorage.setItem('role', 'admin');
    (api.post as any).mockResolvedValueOnce({
      data: {
        message:
          'Panel created with 0 of 2 genes; missing or ambiguous genes: BAD1, BAD2',
      },
    });
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <GenePanelsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    await waitFor(() => expect(screen.getByText('PanelA')).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText(/Panel name/i), 'New');
    await userEvent.type(
      screen.getByLabelText(/Genes \(comma or space separated\)/i),
      'BAD1 BAD2'
    );
    await userEvent.click(screen.getByRole('button', { name: /create/i }));
    await waitFor(() =>
      expect(
        screen.getByText(
          /Panel created with 0 of 2 genes; missing or ambiguous genes: BAD1, BAD2/
        )
      ).toBeInTheDocument()
    );
  });
});
