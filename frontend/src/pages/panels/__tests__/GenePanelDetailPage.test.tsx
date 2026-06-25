import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import GenePanelDetailPage from '../GenePanelDetailPage';
import api from '../../../lib/api';
import { isAdmin } from '../../../lib/auth';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const panelData = {
  _id: '1',
  name: 'PanelA',
  version: 2,
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
  source: 'local',
};

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), put: vi.fn() },
}));
vi.mock('../../../lib/auth', () => ({ isAdmin: vi.fn(() => false) }));

const versionsPayload = {
  panel_id: '1',
  current_version: 2,
  versions: [
    {
      version: 2,
      name: 'PanelA',
      source: 'local',
      external_version: null,
      gene_count: 2,
      created_by_email: 'admin@example.com',
      created_at: '2026-04-28T08:00:00Z',
    },
    {
      version: 1,
      name: 'PanelA',
      source: 'local',
      external_version: null,
      gene_count: 1,
      created_by_email: 'admin@example.com',
      created_at: '2026-04-27T08:00:00Z',
    },
  ],
};

const renderPage = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={['/panels/1']}>
        <Routes>
          <Route path="/panels/:panelId" element={<GenePanelDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('GenePanelDetailPage', () => {
  beforeEach(() => {
    vi.mocked(isAdmin).mockReturnValue(false);
    vi.mocked(api.get).mockImplementation((url: string) =>
      url.endsWith('/versions')
        ? Promise.resolve({ data: versionsPayload })
        : Promise.resolve({ data: panelData }),
    );
  });

  it('allows sorting and filtering', async () => {
    renderPage();

    await waitFor(() => expect(screen.getByText('BRCA1')).toBeInTheDocument());

    // Scope to the regions table (the version-history table also has rows).
    const regionsTable = screen.getByPlaceholderText('Filter gene').closest('table') as HTMLElement;
    const firstDataRow = within(regionsTable).getAllByRole('row')[2];
    expect(within(firstDataRow).getByText('BRCA1')).toBeInTheDocument();

    await userEvent.click(within(regionsTable).getByText(/Gene/));
    const firstAfterSort = within(regionsTable).getAllByRole('row')[2];
    expect(within(firstAfterSort).getByText('BRCA2')).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText('Filter gene'), 'BRCA2');
    expect(within(regionsTable).queryByText('BRCA1')).not.toBeInTheDocument();
    expect(within(regionsTable).getByText('BRCA2')).toBeInTheDocument();
  });

  it('shows the version chip and archived version history', async () => {
    renderPage();

    // Version chip in the title.
    const heading = await screen.findByRole('heading', { name: /PanelA/ });
    expect(within(heading).getByText('v2')).toBeInTheDocument();
    // Version-history section lists both archived versions (current + prior).
    expect(await screen.findByRole('heading', { name: /Version history/i })).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();
  });

  it('lets an admin edit a local panel into a new version', async () => {
    vi.mocked(isAdmin).mockReturnValue(true);
    vi.mocked(api.put).mockResolvedValue({
      data: { panel: { ...panelData, version: 3 }, message: 'Panel updated to version 3', missing_genes: [] },
    });
    renderPage();

    const editButton = await screen.findByRole('button', { name: /Edit genes \(new version\)/i });
    await userEvent.click(editButton);

    const textarea = screen.getByLabelText('Panel genes');
    expect((textarea as HTMLTextAreaElement).value).toContain('BRCA1');
    await userEvent.click(screen.getByRole('button', { name: /Save new version/i }));

    await waitFor(() =>
      expect(vi.mocked(api.put)).toHaveBeenCalledWith(
        '/panels/1',
        expect.objectContaining({ genes: expect.arrayContaining(['BRCA1', 'BRCA2']) }),
      ),
    );
  });
});
