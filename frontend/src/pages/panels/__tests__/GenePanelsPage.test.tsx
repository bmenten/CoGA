import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
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
          source: 'local',
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

  it('imports a selected PanelApp panel for admins', async () => {
    localStorage.setItem('role', 'admin');
    (api.get as any).mockImplementation((url: string) => {
      if (url === '/panels/panelapp/search') {
        return Promise.resolve({
          data: {
            count: 1,
            results: [
              {
                panelapp_id: 20,
                name: 'Hereditary ataxia',
                disease_group: 'Neurology',
                disease_sub_group: 'Motor Disorders',
                status: 'public',
                version: '1.345',
                version_created: '2026-05-01T14:28:12Z',
                relevant_disorders: [],
                gene_count: 168,
                str_count: 14,
                region_count: 3,
                types: ['Rare Disease 100K'],
                url: 'https://panelapp.genomicsengland.co.uk/panels/20/',
              },
            ],
          },
        });
      }
      return Promise.resolve({
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
            source: 'local',
          },
        ],
      });
    });
    (api.post as any).mockResolvedValueOnce({
      data: {
        message: 'PanelApp panel imported with 168 genes and 185 regions',
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
    await userEvent.type(screen.getByLabelText(/PanelApp search/i), 'ataxia');
    await userEvent.click(screen.getByRole('button', { name: /search panelapp/i }));

    await waitFor(() => expect(screen.getByText('Hereditary ataxia')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^import$/i }));

    await waitFor(() =>
      expect(screen.getByText(/PanelApp panel imported with 168 genes/)).toBeInTheDocument(),
    );
    expect(api.get).toHaveBeenCalledWith('/panels/panelapp/search', {
      params: { query: 'ataxia', page_size: 20 },
    });
    expect(api.post).toHaveBeenCalledWith('/panels/import/panelapp', {
      panelapp_id: 20,
      version: '1.345',
      confidence_levels: ['3'],
      include_regions: true,
      include_strs: true,
      assembly: 'GRCh38',
    });
  });

  it('lets an admin generate the Mendeliome from Monarch', async () => {
    localStorage.setItem('role', 'admin');
    (api.get as any).mockResolvedValue({ data: [] }); // no panels yet
    (api.post as any).mockResolvedValueOnce({
      data: { message: 'Mendeliome created with 5298 genes (Monarch 2026-06-08).' },
    });
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <GenePanelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const generate = await screen.findByRole('button', { name: /Generate Mendeliome/i });
    await userEvent.click(generate);

    await waitFor(() =>
      expect(screen.getByText(/Mendeliome created with 5298 genes/)).toBeInTheDocument(),
    );
    expect(api.post).toHaveBeenCalledWith('/panels/mendeliome/regenerate');
  });
});
