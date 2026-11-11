import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

/** ATAXIA's entities by confidence level, as /panels/panelapp/20/counts reads them. */
const ATAXIA_COUNTS = {
  panelapp_id: 20,
  version: '1.345',
  genes: { total: 168, green: 120, amber: 30, red: 16, unrated: 2 },
  strs: { total: 14, green: 10, amber: 2, red: 2, unrated: 0 },
  regions: { total: 3, green: 3, amber: 0, red: 0, unrated: 0 },
};

/** The API as the PanelApp import reads it: the catalogue, a search finding ATAXIA, and its counts. */
const panelAppApi =
  (catalogue: unknown[] = [PANEL_A], counts: () => Promise<unknown> = () => Promise.resolve({ data: ATAXIA_COUNTS })) =>
  (url: string) => {
    if (url === '/panels/panelapp/search') return Promise.resolve({ data: { count: 1, results: [ATAXIA] } });
    if (url === '/panels/panelapp/20/counts') return counts();
    return Promise.resolve({ data: catalogue });
  };

const searchAtaxia = async () => {
  await userEvent.type(screen.getByLabelText(/PanelApp search/i), 'ataxia');
  await userEvent.click(screen.getByRole('button', { name: /search panelapp/i }));
  await screen.findByText('Hereditary ataxia');
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

/** Clicks a panel's Delete, then confirms in the dialog that asks first. */
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

  // Regions are stored per assembly: a panel's size is given per assembly, not summed
  // across them, in the backend's order (`assembly_display_rank`): GRCh38 (also when named
  // hg38), T2T-CHM13, GRCh37, then the rest. The UCSC reference import names T2T
  // `T2T CHM13v2.0`, with a space: read as T2T, it comes after GRCh38 and before the other
  // assemblies, as under its hyphenated name.
  it.each<PanelSizeCase>([
    [
      'per assembly, not summed across them',
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

  // A delete takes the panel's genes, regions and version history, so it asks first, naming the
  // panel and its version, and sends nothing until confirmed: neither on Cancel nor on Escape.
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

  it('imports a selected PanelApp panel green only for admins', async () => {
    (api.get as unknown as Mock).mockImplementation(panelAppApi());
    (api.post as unknown as Mock).mockResolvedValueOnce({
      data: {
        message: 'PanelApp panel imported with 120 genes and 185 regions',
      },
    });

    renderPage('admin');

    await waitFor(() => expect(screen.getByText('PanelA')).toBeInTheDocument());
    await searchAtaxia();
    await userEvent.click(await screen.findByRole('button', { name: 'Green (120)' }));

    await waitFor(() =>
      expect(screen.getByText(/PanelApp panel imported with 120 genes/)).toBeInTheDocument(),
    );
    expect(api.get).toHaveBeenCalledWith('/panels/panelapp/search', {
      params: { query: 'ataxia', page_size: 20 },
    });
    // The counts are read for the version the search found, the one the import takes.
    expect(api.get).toHaveBeenCalledWith('/panels/panelapp/20/counts', { params: { version: '1.345' } });
    expect(api.post).toHaveBeenCalledWith('/panels/import/panelapp', {
      panelapp_id: 20,
      version: '1.345',
      confidence_levels: ['3'],
      include_regions: true,
      include_strs: true,
      assembly: 'GRCh38',
    });
  });

  // After a search, each panel's genes, regions and STRs are counted per confidence level: the
  // total, then green, amber and red, and the unrated ones PanelApp lists apart.
  it('counts the genes, regions and STRs of each panel found by confidence level', async () => {
    (api.get as unknown as Mock).mockImplementation(panelAppApi());
    renderPage('admin');
    await screen.findByRole('link', { name: 'PanelA' });
    await searchAtaxia();

    const row = screen.getByText('Hereditary ataxia').closest('tr') as HTMLElement;
    await within(row).findByText('120 green');
    const lines = Array.from(row.querySelectorAll('.panelapp-confidence-line')).map((line) =>
      Array.from(line.children).map((part) => part.textContent),
    );
    expect(lines).toEqual([
      ['168 genes', '120 green', '30 amber', '16 red', '2 unrated'],
      ['3 regions', '3 green', '0 amber', '0 red'],
      ['14 STRs', '10 green', '2 amber', '2 red'],
    ]);
    // Each choice of levels names the genes it takes.
    expect(within(row).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Green (120)',
      'Green + amber (150)',
      'Green + amber + red (166)',
    ]);
  });

  // Each choice of levels of one PanelApp panel is a panel of its own: those in the catalogue
  // are listed with their version, and importing another choice adds a panel next to them.
  it('lists the choices already in the catalogue, and imports another as a panel of its own', async () => {
    const imported = (id: string, selection: string, version: number) => ({
      ...PANEL_A,
      _id: id,
      name: `PanelApp 20: Hereditary ataxia (${selection})`,
      source: 'panelapp',
      external_id: '20',
      external_selection: selection,
      external_version: '1.345',
      version,
    });
    const catalogue = [
      PANEL_A,
      imported('g', '3', 2),
      imported('ga', '2,3', 1),
      { ...imported('other', '3', 1), external_id: '21' },
    ];
    (api.get as unknown as Mock).mockImplementation(panelAppApi(catalogue));
    (api.post as unknown as Mock).mockResolvedValueOnce({ data: { message: 'PanelApp panel imported as version 1' } });
    renderPage('admin');
    await screen.findByRole('link', { name: 'PanelA' });
    await searchAtaxia();

    const row = screen.getByText('Hereditary ataxia').closest('tr') as HTMLElement;
    expect(within(row).getByText(/In catalogue/).textContent).toBe('In catalogue: green v2 · green + amber v1');
    expect(within(row).getByRole('link', { name: 'green v2' })).toHaveAttribute('href', '/panels/g');
    expect(within(row).getByRole('link', { name: 'green + amber v1' })).toHaveAttribute('href', '/panels/ga');
    // The catalogue names the choice under each PanelApp panel's version.
    expect(screen.getAllByText('1.345 · green + amber')).toHaveLength(1);

    await userEvent.click(await within(row).findByRole('button', { name: 'Green + amber + red (166)' }));
    await screen.findByText('PanelApp panel imported as version 1');
    expect(api.post).toHaveBeenCalledWith('/panels/import/panelapp', {
      panelapp_id: 20,
      version: '1.345',
      confidence_levels: ['1', '2', '3'],
      include_regions: true,
      include_strs: true,
      assembly: 'GRCh38',
    });
  });

  // A choice PanelApp rates nothing of cannot be imported (the import would be refused); counts
  // that cannot be read say so, and leave every choice to import.
  it('disables a choice that takes nothing, and says when the counts cannot be read', async () => {
    const noGreen = {
      ...ATAXIA_COUNTS,
      genes: { total: 5, green: 0, amber: 3, red: 2, unrated: 0 },
      strs: { total: 0, green: 0, amber: 0, red: 0, unrated: 0 },
      regions: { total: 1, green: 1, amber: 0, red: 0, unrated: 0 },
    };
    (api.get as unknown as Mock).mockImplementation(panelAppApi([PANEL_A], () => Promise.resolve({ data: noGreen })));
    renderPage('admin');
    await screen.findByRole('link', { name: 'PanelA' });
    await searchAtaxia();

    // The green region is a green import; without regions there is nothing green to take.
    expect(await screen.findByRole('button', { name: 'Green (0)' })).toBeEnabled();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Regions' }));
    expect(screen.getByRole('button', { name: 'Green (0)' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Green + amber (3)' })).toBeEnabled();
    cleanup();

    (api.get as unknown as Mock).mockImplementation(
      panelAppApi([PANEL_A], () => Promise.reject(new Error('Bad Gateway'))),
    );
    renderPage('admin');
    await screen.findByRole('link', { name: 'PanelA' });
    await searchAtaxia();
    expect(await screen.findByText('Could not read the confidence levels from PanelApp')).toBeInTheDocument();
    expect(screen.getByText('168 genes')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Green' })).toBeEnabled();
  });

  // The confidence levels, assembly, regions and STRs chosen are what the import asks for; a
  // search that finds nothing, or fails, and a refused import say so.
  it('imports with the options chosen, and says when a PanelApp search or import does not succeed', async () => {
    let search: () => Promise<unknown> = () => Promise.resolve({ data: { count: 0, results: [] } });
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/panels/panelapp/search') return search();
      if (url === '/panels/panelapp/20/counts') return Promise.resolve({ data: ATAXIA_COUNTS });
      return Promise.resolve({ data: [PANEL_A] });
    });
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
    await userEvent.selectOptions(screen.getByLabelText('Assembly'), 'GRCh37');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Regions' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'STRs' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Green + amber (150)' }));

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
