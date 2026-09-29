import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
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

  it('shows which assembly each region belongs to (#515)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) =>
      url.endsWith('/versions')
        ? Promise.resolve({ data: versionsPayload })
        : Promise.resolve({
            data: {
              ...panelData,
              regions: [
                { gene: 'BRCA1', chr: '17', start: 43044295, end: 43125483, assembly_id: 'a', assembly: 'GRCh38' },
                { gene: 'BRCA1', chr: '17', start: 44000000, end: 44100000, assembly_id: 'b', assembly: 'T2T-CHM13v2.0' },
              ],
            },
          }),
    );
    renderPage();

    const regionsTable = (await screen.findByPlaceholderText('Filter gene')).closest('table') as HTMLElement;
    await waitFor(() => expect(within(regionsTable).getByText('GRCh38')).toBeInTheDocument());
    expect(within(regionsTable).getByText('T2T-CHM13v2.0')).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText('Filter assembly'), 'T2T');
    expect(within(regionsTable).queryByText('GRCh38')).not.toBeInTheDocument();
    expect(within(regionsTable).getByText('44000000')).toBeInTheDocument();
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

  // #610 — a failed panel request stayed at "Loading gene panel" for good, and a failed
  // version history was hidden.
  describe('when a request fails', () => {
    const failWith = (matches: (url: string) => boolean) => {
      let failing = true;
      const working = vi.mocked(api.get).getMockImplementation()!;
      vi.mocked(api.get).mockImplementation((url, config) =>
        failing && matches(url)
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500, data: { detail: 'Panel store unavailable' } } }))
          : working(url, config),
      );
      return () => {
        failing = false;
      };
    };

    it('says the gene panel could not be loaded, and retries', async () => {
      const recover = failWith((url) => !url.endsWith('/versions'));
      renderPage();

      expect(await screen.findByRole('heading', { name: 'Gene panel could not be loaded' })).toBeInTheDocument();
      expect(screen.getByText('Panel store unavailable')).toBeInTheDocument();
      expect(screen.queryByText(/Loading gene panel/)).not.toBeInTheDocument();

      recover();
      await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(await screen.findByRole('heading', { name: /PanelA/ })).toBeInTheDocument();
    });

    it('says the version history could not be loaded, instead of hiding it', async () => {
      failWith((url) => url.endsWith('/versions'));
      renderPage();

      expect(
        await screen.findByText(/Could not load the panel's version history — this is not an empty result/),
      ).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /PanelA/ })).toBeInTheDocument();
    });
  });
});
