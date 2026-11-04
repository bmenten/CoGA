import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi, beforeEach, type Mock } from 'vitest';
import GenePanelsPage from '../GenePanelsPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

const PANEL_A = {
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
};

/** A case of the panel-size test: its name, the panel's regions, and the size listed. */
type PanelSizeCase = [string, Array<{ end: number; assembly: string }>, string];

/** A local panel of BRCA1 whose regions are `regions`, as /panels serves it. */
const panelWithRegions = (id: string, name: string, regions: Array<{ end: number; assembly: string }>) => ({
  _id: id,
  name,
  genes: ['BRCA1'],
  gene_count: 1,
  regions: regions.map(({ end, assembly }) => ({ gene: 'BRCA1', chr: '17', start: 0, end, assembly })),
  created_by: 'admin-id',
  created_at: '2026-04-28T08:00:00Z',
  source: 'local',
});

const ATAXIA = {
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
};

const renderPage = (role: 'viewer' | 'admin') => {
  localStorage.setItem('role', role);
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <GenePanelsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const panelRequests = () => (api.get as unknown as Mock).mock.calls.filter(([url]) => url === '/panels').length;

/** Clicks a panel's Delete, then confirms in the dialog that asks first (UI-7). */
const deletePanel = async () => {
  await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
  await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete panel' }));
};

describe('GenePanelsPage', () => {
  beforeEach(() => {
    localStorage.clear();
    (api.get as unknown as Mock).mockReset().mockResolvedValue({ data: [PANEL_A] });
    (api.post as unknown as Mock).mockReset().mockResolvedValue({ data: { message: 'Panel created' } });
    (api.delete as unknown as Mock).mockReset().mockResolvedValue({ data: {} });
  });

  it('lists panels from API for non-admin users', async () => {
    renderPage('viewer');
    await waitFor(() => expect(screen.getByText('PanelA')).toBeInTheDocument());
    expect(
      screen.getByRole('link', { name: 'PanelA' })
    ).toHaveAttribute('href', '/panels/1');
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /create/i })).not.toBeInTheDocument();
  });

  // #610 — a failed catalogue showed as an empty table, with no message.
  it('says the gene panels could not be loaded, not that there are none, and retries', async () => {
    vi.mocked(api.get).mockRejectedValueOnce(
      Object.assign(new Error('HTTP 500'), { response: { status: 500 } }),
    );
    renderPage('viewer');

    expect(
      await screen.findByText(/Could not load the gene panels — this is not an empty result/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('link', { name: 'PanelA' })).toBeInTheDocument();
    expect(screen.queryByText(/Could not load the gene panels/)).not.toBeInTheDocument();
  });

  // #515 — regions are stored per assembly: a panel's size is given per assembly, not summed
  // across them. The backend lists a gene's assemblies and a panel's regions GRCh38, T2T-CHM13,
  // GRCh37, then the rest (`assembly_display_rank`); the list put GRCh37 among the rest by name,
  // and read only the exact name GRCh38 as GRCh38, so a GRCh38 named hg38 came after the others.
  // The UCSC reference import names T2T `T2T CHM13v2.0`, with a space: read as T2T, it comes after
  // GRCh38 and before the other assemblies, as under its hyphenated name.
  it.each<PanelSizeCase>([
    [
      'per assembly, not summed across them (#515)',
      [
        { end: 20000, assembly: 'T2T-CHM13v2.0' },
        { end: 10000, assembly: 'GRCh38' },
      ],
      '10.00 kb (GRCh38) · 20.00 kb (T2T-CHM13v2.0)',
    ],
    [
      'with T2T second under the name the UCSC import gives it',
      [
        { end: 30000, assembly: 'GRCh37' },
        { end: 20000, assembly: 'T2T CHM13v2.0' },
        { end: 10000, assembly: 'GRCh38' },
      ],
      '10.00 kb (GRCh38) · 20.00 kb (T2T CHM13v2.0) · 30.00 kb (GRCh37)',
    ],
    ...[
      ['GRCh38', 'GRCh37'],
      ['hg38', 'hg19'],
      ['GRCh38.p14', 'GRCh37.p13'],
    ].map(([grch38, grch37]): PanelSizeCase => [
      `in the backend’s order, ${grch38}, T2T, ${grch37}, then the rest`,
      [
        { end: 40000, assembly: 'CHM13v1.1' },
        { end: 30000, assembly: grch37 },
        { end: 20000, assembly: 'T2T CHM13v2.0' },
        { end: 10000, assembly: grch38 },
      ],
      `10.00 kb (${grch38}) · 20.00 kb (T2T CHM13v2.0) · 30.00 kb (${grch37}) · 40.00 kb (CHM13v1.1)`,
    ]),
  ])('gives the panel size %s', async (_case, regions, size) => {
    (api.get as unknown as Mock).mockResolvedValueOnce({ data: [panelWithRegions('2', 'PanelB', regions)] });
    renderPage('viewer');

    expect(await screen.findByText(size)).toBeInTheDocument();
  });

  it('filters the catalogue by name, source, version or description', async () => {
    (api.get as unknown as Mock).mockResolvedValue({
      data: [PANEL_A, { ...PANEL_A, _id: '3', name: 'Hereditary ataxia', source: 'panelapp', external_version: '1.345', description: null }],
    });
    renderPage('viewer');
    await screen.findByRole('link', { name: 'Hereditary ataxia' });

    const filter = screen.getByRole('searchbox', { name: 'Filter panels' });
    for (const [query, shown] of [
      [' Ataxia ', 'Hereditary ataxia'],
      ['panelapp', 'Hereditary ataxia'],
      ['local', 'PanelA'],
      ['1.345', 'Hereditary ataxia'],
      ['TUMOR', 'PanelA'],
    ]) {
      fireEvent.change(filter, { target: { value: query } });
      expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual([shown]);
    }
  });

  it('orders the admin tools: external panels, then a custom panel, then the catalogue', async () => {
    renderPage('admin');
    await screen.findByRole('link', { name: 'PanelA' });

    expect(screen.getByRole('heading', { level: 1, name: 'Gene panels' })).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual([
      'Import and sync external panels',
      'Add new custom gene panel',
      'Existing panels',
    ]);
    // PanelApp import first, the Mendeliome update from Monarch directly below it, in one section.
    const external = screen.getByRole('region', { name: 'Import and sync external panels' });
    expect(
      Array.from(external.querySelectorAll('h3')).map((heading) => heading.textContent),
    ).toEqual(['Import PanelApp panel', 'Mendeliome update from Monarch']);
    expect(screen.getByRole('link', { name: 'Monarch data' })).toHaveAttribute('href', '/admin/reference/monarch');
    const custom = screen.getByRole('region', { name: 'Add new custom gene panel' });
    expect(custom).toContainElement(screen.getByRole('button', { name: 'Create panel' }));
  });

  it('shows server message when creation returns warning', async () => {
    (api.post as unknown as Mock).mockResolvedValueOnce({
      data: {
        message:
          'Panel created with 0 of 2 genes; missing or ambiguous genes: BAD1, BAD2',
      },
    });
    renderPage('admin');
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

  // A refused write says why, naming the genes concerned; a deleted panel leaves the reloaded list.
  it('says why a panel write was refused, naming the genes, and deletes a panel', async () => {
    (api.delete as unknown as Mock)
      .mockRejectedValueOnce({
        response: { status: 409, data: { detail: { message: 'PanelA is applied in a signed analysis', genes: ['BRCA1', 'BRCA2'] } } },
      })
      .mockResolvedValueOnce({ data: {} });
    (api.post as unknown as Mock).mockRejectedValueOnce({
      response: { status: 422, data: { detail: [{ msg: 'genes: not a list' }] } },
    });
    renderPage('admin');
    await screen.findByRole('link', { name: 'PanelA' });

    await deletePanel();
    expect(await screen.findByText('PanelA is applied in a signed analysis: BRCA1, BRCA2')).toBeInTheDocument();
    expect(api.delete).toHaveBeenCalledWith('/panels/1');

    const before = panelRequests();
    await deletePanel();
    expect(await screen.findByText('Panel deleted')).toBeInTheDocument();
    await waitFor(() => expect(panelRequests()).toBeGreaterThan(before));

    // A refusal that is neither a sentence nor a message naming genes reads as the fallback.
    await userEvent.type(screen.getByLabelText(/Panel name/i), 'New');
    await userEvent.click(screen.getByRole('button', { name: 'Create panel' }));
    expect(await screen.findByText('Error creating panel')).toBeInTheDocument();
  });

  // UI-7: a panel was deleted on one click, with its genes, regions and version history. The
  // delete now asks first, naming the panel and its version, and sends nothing until confirmed:
  // neither on Cancel nor on Escape.
  it('asks before deleting a panel, naming it and its version, and sends nothing on cancel', async () => {
    (api.get as unknown as Mock).mockResolvedValue({
      data: [{ ...PANEL_A, version: 3, source: 'panelapp', external_version: '1.345' }],
    });
    renderPage('admin');
    await screen.findByRole('link', { name: 'PanelA' });

    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = screen.getByRole('dialog', { name: 'Delete gene panel PanelA v3' });
    expect(dialog).toHaveTextContent(
      'This deletes the gene panel PanelA v3 (PanelApp 1.345), with its genes, its regions and every archived version.',
    );
    expect(dialog).toHaveTextContent('This cannot be undone.');
    // The safe choice has the focus: Enter on the opened dialog cancels.
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(api.delete).not.toHaveBeenCalled();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.delete).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: 'PanelA' })).toBeInTheDocument();

    await deletePanel();
    await waitFor(() => expect(api.delete).toHaveBeenCalledTimes(1));
    expect(api.delete).toHaveBeenCalledWith('/panels/1');
    expect(await screen.findByText('Panel deleted')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('imports a selected PanelApp panel for admins', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/panels/panelapp/search') {
        return Promise.resolve({ data: { count: 1, results: [ATAXIA] } });
      }
      return Promise.resolve({ data: [PANEL_A] });
    });
    (api.post as unknown as Mock).mockResolvedValueOnce({
      data: {
        message: 'PanelApp panel imported with 168 genes and 185 regions',
      },
    });

    renderPage('admin');

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

  // The confidence levels, assembly, regions and STRs chosen are what the import asks for; a
  // search that finds nothing, or fails, and a refused import say so.
  it('imports with the options chosen, and says when a PanelApp search or import does not succeed', async () => {
    let search: () => Promise<unknown> = () => Promise.resolve({ data: { count: 0, results: [] } });
    (api.get as unknown as Mock).mockImplementation((url: string) =>
      url === '/panels/panelapp/search' ? search() : Promise.resolve({ data: [PANEL_A] }),
    );
    (api.post as unknown as Mock).mockRejectedValueOnce({
      response: { status: 422, data: { detail: [{ msg: 'version: not found' }, 'assembly: GRCh37 has no regions', null] } },
    });
    renderPage('admin');
    await screen.findByRole('link', { name: 'PanelA' });

    await userEvent.type(screen.getByLabelText(/PanelApp search/i), '  nothing  ');
    await userEvent.click(screen.getByRole('button', { name: /search panelapp/i }));
    expect(await screen.findByText('No PanelApp panels found')).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith('/panels/panelapp/search', { params: { query: 'nothing', page_size: 20 } });

    search = () => Promise.reject(new Error('Network Error'));
    await userEvent.click(screen.getByRole('button', { name: /search panelapp/i }));
    expect(await screen.findByText('Could not search PanelApp')).toBeInTheDocument();

    search = () => Promise.resolve({ data: { count: 1, results: [ATAXIA] } });
    await userEvent.click(screen.getByRole('button', { name: /search panelapp/i }));
    await screen.findByText('Hereditary ataxia');
    await userEvent.selectOptions(screen.getByLabelText('Confidence'), '2,3');
    await userEvent.selectOptions(screen.getByLabelText('Assembly'), 'GRCh37');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Regions' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'STRs' }));
    await userEvent.click(screen.getByRole('button', { name: /^import$/i }));

    expect(await screen.findByText('version: not found; assembly: GRCh37 has no regions')).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith('/panels/import/panelapp', {
      panelapp_id: 20,
      version: '1.345',
      confidence_levels: ['2', '3'],
      include_regions: false,
      include_strs: false,
      assembly: 'GRCh37',
    });
  });

  it('lets an admin generate the Mendeliome from Monarch', async () => {
    (api.get as unknown as Mock).mockResolvedValue({ data: [] }); // no panels yet
    (api.post as unknown as Mock).mockResolvedValueOnce({
      data: { message: 'Mendeliome created with 5298 genes (Monarch 2026-06-08).' },
    });
    renderPage('admin');

    const generate = await screen.findByRole('button', { name: /Generate Mendeliome/i });
    await userEvent.click(generate);

    await waitFor(() =>
      expect(screen.getByText(/Mendeliome created with 5298 genes/)).toBeInTheDocument(),
    );
    expect(api.post).toHaveBeenCalledWith('/panels/mendeliome/regenerate');
  });
});
