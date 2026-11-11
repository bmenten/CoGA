import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import GenePanelDetailPage from '../GenePanelDetailPage';
import api from '../../../lib/api';
import { hasSuperuserRights } from '../../../lib/auth';
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
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn() },
}));
// Gene panels are a superuser's or an admin's to change.
vi.mock('../../../lib/auth', () => ({ hasSuperuserRights: vi.fn(() => false) }));

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
    vi.mocked(hasSuperuserRights).mockReturnValue(false);
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

  it('shows which assembly each region belongs to', async () => {
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

  describe('editing a local panel', () => {
    beforeEach(() => {
      vi.mocked(hasSuperuserRights).mockReturnValue(true);
      vi.mocked(api.put).mockReset();
      vi.mocked(api.post).mockReset();
      vi.mocked(api.put).mockResolvedValue({
        data: { panel: { ...panelData, version: 3 }, message: 'Panel updated to version 3: 1 of 1 genes', missing_genes: [] },
      });
      vi.mocked(api.post).mockImplementation((_url: string, body?: unknown) => {
        const genes = (body as { genes: string[] }).genes;
        const missing = genes.filter((gene) => gene.toUpperCase().startsWith('NOTAGENE'));
        return Promise.resolve({ data: { found: genes.filter((gene) => !missing.includes(gene)), missing } });
      });
    });

    const changes = () => screen.getByRole('region', { name: 'Changes for version 3' });

    it('lists a gene removed with its bin, and removes it only when the list is confirmed', async () => {
      renderPage();

      await userEvent.click(await screen.findByRole('button', { name: 'Remove BRCA1 from the next version' }));

      // Listed, its row struck through, nothing saved.
      expect(within(changes()).getByText('Remove (1)')).toBeInTheDocument();
      expect(within(screen.getByRole('list', { name: 'Genes to remove in version 3' })).getByText('BRCA1')).toBeInTheDocument();
      const regionsTable = screen.getByPlaceholderText('Filter gene').closest('table') as HTMLElement;
      expect(within(regionsTable).getByText('BRCA1').closest('tr')).toHaveClass('panel-gene-row--removed');
      expect(within(changes()).getByText(/Nothing is saved yet\. Confirming saves version 3 with 1 gene; version 2 stays/)).toBeInTheDocument();
      expect(api.put).not.toHaveBeenCalled();

      await userEvent.click(within(changes()).getByRole('button', { name: 'Confirm and save version 3' }));

      // The whole list as it will be, made on version 2.
      await waitFor(() =>
        expect(api.put).toHaveBeenCalledWith('/panels/1', { genes: ['BRCA2'], expected_version: 2 }),
      );
      expect(await screen.findByText('Panel updated to version 3: 1 of 1 genes')).toBeInTheDocument();
      expect(screen.queryByRole('region', { name: /Changes for version/ })).not.toBeInTheDocument();
    });

    it('keeps a gene listed for removal, from its row or from the list, and discards the changes', async () => {
      renderPage();

      await userEvent.click(await screen.findByRole('button', { name: 'Remove BRCA1 from the next version' }));
      await userEvent.click(screen.getAllByRole('button', { name: 'Keep BRCA1' })[0]);
      expect(screen.queryByRole('region', { name: /Changes for version/ })).not.toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: 'Remove BRCA2 from the next version' }));
      await userEvent.click(within(changes()).getByRole('button', { name: 'Discard changes' }));
      expect(screen.queryByRole('region', { name: /Changes for version/ })).not.toBeInTheDocument();
      expect(api.put).not.toHaveBeenCalled();
    });

    it('adds genes typed in the box, flags a name no assembly knows, and leaves it out on request', async () => {
      renderPage();

      await userEvent.type(await screen.findByLabelText('Genes to add'), 'TP53, notagene1 brca1;TP53');
      await userEvent.click(screen.getByRole('button', { name: 'Add to changes' }));

      // A gene the panel holds is not added twice, in any letter case.
      expect(screen.getByText('Already in the panel: brca1.')).toBeInTheDocument();
      const added = screen.getByRole('list', { name: 'Genes to add in version 3' });
      expect(within(added).getAllByRole('listitem').map((item) => item.firstChild?.textContent)).toEqual([
        'TP53',
        'notagene1',
      ]);
      expect(api.post).toHaveBeenCalledWith('/panels/genes/check', { genes: ['TP53', 'notagene1'] });
      expect(await within(changes()).findByText(/notagene1 matches no gene in a loaded assembly/)).toBeInTheDocument();
      expect(within(added).getByText('not found')).toBeInTheDocument();

      await userEvent.click(within(changes()).getByRole('button', { name: 'Leave it out' }));
      expect(within(screen.getByRole('list', { name: 'Genes to add in version 3' })).queryByText('notagene1')).not.toBeInTheDocument();

      await userEvent.click(within(changes()).getByRole('button', { name: 'Confirm and save version 3' }));
      await waitFor(() =>
        expect(api.put).toHaveBeenCalledWith('/panels/1', { genes: ['BRCA1', 'BRCA2', 'TP53'], expected_version: 2 }),
      );
    });

    it('turns an edited whole list into the changes it makes, to confirm like the others', async () => {
      renderPage();

      await userEvent.click(await screen.findByRole('button', { name: 'Edit the whole list' }));
      const textarea = screen.getByLabelText('Panel genes') as HTMLTextAreaElement;
      expect(textarea.value).toBe('BRCA1, BRCA2');
      await userEvent.clear(textarea);
      await userEvent.type(textarea, 'BRCA2 PALB2');
      await userEvent.click(screen.getByRole('button', { name: 'Review changes' }));

      expect(within(screen.getByRole('list', { name: 'Genes to remove in version 3' })).getByText('BRCA1')).toBeInTheDocument();
      expect(within(screen.getByRole('list', { name: 'Genes to add in version 3' })).getByText('PALB2')).toBeInTheDocument();
      expect(api.put).not.toHaveBeenCalled();

      await userEvent.click(within(changes()).getByRole('button', { name: 'Confirm and save version 3' }));
      await waitFor(() =>
        expect(api.put).toHaveBeenCalledWith('/panels/1', { genes: ['BRCA2', 'PALB2'], expected_version: 2 }),
      );
    });

    it('says when another version was saved meanwhile, reloads the panel and keeps the changes listed', async () => {
      vi.mocked(api.put).mockRejectedValue({
        response: { status: 409, data: { detail: 'The panel has changed since you opened it: it is now at version 3, not 2.' } },
      });
      renderPage();
      await userEvent.click(await screen.findByRole('button', { name: 'Remove BRCA1 from the next version' }));
      const panelReads = vi.mocked(api.get).mock.calls.filter(([url]) => url === '/panels/1').length;

      await userEvent.click(within(changes()).getByRole('button', { name: 'Confirm and save version 3' }));

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The panel has changed since you opened it: it is now at version 3, not 2. It has been reloaded: check the changes against it, then confirm again.',
      );
      await waitFor(() =>
        expect(vi.mocked(api.get).mock.calls.filter(([url]) => url === '/panels/1').length).toBeGreaterThan(panelReads),
      );
      expect(within(changes()).getByText('Remove (1)')).toBeInTheDocument();
    });

    it('offers no edits on a generated or imported panel', async () => {
      vi.mocked(api.get).mockImplementation((url: string) =>
        url.endsWith('/versions')
          ? Promise.resolve({ data: versionsPayload })
          : Promise.resolve({ data: { ...panelData, source: 'panelapp' } }),
      );
      renderPage();

      expect(await screen.findByText(/This panel is generated\/imported/)).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Remove .* from the next version/ })).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Genes to add')).not.toBeInTheDocument();
    });
  });

  it('offers a reader no edits', async () => {
    renderPage();

    await screen.findByRole('heading', { name: /PanelA/ });
    expect(screen.queryByRole('button', { name: /Remove .* from the next version/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Edit genes' })).not.toBeInTheDocument();
  });

  it('lists the gene names that got no coordinates, as not imported', async () => {
    vi.mocked(api.get).mockImplementation((url: string) =>
      url.endsWith('/versions')
        ? Promise.resolve({ data: versionsPayload })
        : Promise.resolve({
            data: { ...panelData, genes: ['BRCA1', 'BRCA2', 'NOTAGENE1', 'brca3x'], gene_count: 4 },
          }),
    );
    renderPage();

    const note = await screen.findByRole('note');
    expect(note).toHaveTextContent('2 gene names were not imported.');
    expect(within(screen.getByRole('list', { name: 'Gene names not imported' })).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'NOTAGENE1',
      'brca3x',
    ]);
    expect(screen.getByText(/4 genes \(2 without coordinates\)/)).toBeInTheDocument();
    // A reader sees them, but no bin.
    expect(within(note).queryByRole('button')).not.toBeInTheDocument();
  });

  it('lets an admin remove a name that was not imported from the next version', async () => {
    vi.mocked(hasSuperuserRights).mockReturnValue(true);
    vi.mocked(api.get).mockImplementation((url: string) =>
      url.endsWith('/versions')
        ? Promise.resolve({ data: versionsPayload })
        : Promise.resolve({ data: { ...panelData, genes: ['BRCA1', 'BRCA2', 'NOTAGENE1'], gene_count: 3 } }),
    );
    renderPage();

    const note = await screen.findByRole('note');
    await userEvent.click(within(note).getByRole('button', { name: 'Remove NOTAGENE1 from the next version' }));

    expect(within(note).getByText('removed in v3')).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Genes to remove in version 3' })).getByText('NOTAGENE1')).toBeInTheDocument();
  });

  // A failed panel request, or a failed version history, is said as such, never as a page
  // still loading or a panel without versions.
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
