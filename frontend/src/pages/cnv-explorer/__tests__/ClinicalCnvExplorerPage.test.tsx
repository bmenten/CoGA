// Pins the clinical CNV catalogue browser: whose catalogue it asks for (first assembly by default,
// switchable, name encoded), the trimmed search and Clear, and how each CNV's locus and size read.
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ClinicalCnvExplorerPage from '../ClinicalCnvExplorerPage';
import api from '../../../lib/api';
import type { ApiAssemblyRecord, ApiClinicalCnv } from '../../../lib/apiTypes';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn() } }));

const mockedGet = vi.mocked(api.get);

const GRCH38: ApiAssemblyRecord = { id: 'asm38', assembly_name: 'GRCh38', version: 'p14' };
const GRCH37: ApiAssemblyRecord = { id: 'asm37', assembly_name: 'GRCh37', version: 'p13' };

const cnv = (overrides: Partial<ApiClinicalCnv> & Pick<ApiClinicalCnv, '_id' | 'label'>): ApiClinicalCnv => ({
  chr: '1',
  start: 0,
  end: 0,
  type: 'DEL',
  cytoband: null,
  ...overrides,
});

const DEL_22Q11 = cnv({
  _id: 'cnv-22q11',
  label: '22q11.2 recurrent (DGS/VCFS) region (proximal, A-D)',
  chr: '22',
  start: 18912231,
  end: 21465672,
  cytoband: '22q11.21',
});
const DUP_17P12 = cnv({
  _id: 'cnv-17p12',
  label: '17p12 (HNPP/CMT1A) region',
  chr: '17',
  start: 14194598,
  end: 15567589,
  type: 'DUP',
  cytoband: '17p12',
});
const DEL_1Q21 = cnv({
  _id: 'cnv-1q21',
  label: '1q21.1 recurrent region (BP3-BP4, distal)',
  chr: '1',
  start: 147061900,
  end: 147830000,
  cytoband: '1q21.1',
});

type CatalogueParams = { search?: string; limit?: number };

/** Serve the assemblies and a catalogue the "server" filters by label, as the API does. */
const serve = ({
  assemblies = [GRCH38],
  catalogue = [DEL_22Q11, DUP_17P12, DEL_1Q21],
  pendingCatalogue = false,
}: {
  assemblies?: ApiAssemblyRecord[];
  catalogue?: ApiClinicalCnv[];
  pendingCatalogue?: boolean;
} = {}) => {
  mockedGet.mockImplementation((url: string, config?: { params?: unknown }) => {
    if (url === '/assemblies') return Promise.resolve({ data: assemblies });
    if (url.endsWith('/catalog')) {
      if (pendingCatalogue) return new Promise(() => undefined);
      const search = (config?.params as CatalogueParams | undefined)?.search?.toLowerCase();
      return Promise.resolve({
        data: search ? catalogue.filter((row) => row.label.toLowerCase().includes(search)) : catalogue,
      });
    }
    return Promise.reject(new Error(`Unexpected GET ${url}`));
  });
};

const renderPage = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <ClinicalCnvExplorerPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const cellsOf = (label: string) => {
  const row = screen.getByRole('link', { name: label }).closest('tr');
  expect(row).not.toBeNull();
  return within(row as HTMLElement)
    .getAllByRole('cell')
    .map((cell) => cell.textContent);
};

const resultCount = () =>
  screen.getByRole('heading', { level: 1 }).querySelector('.variant-results-count');

const catalogueCalls = () => mockedGet.mock.calls.filter(([url]) => String(url).endsWith('/catalog'));

describe('ClinicalCnvExplorerPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    serve();
  });

  it("asks for the first assembly's catalogue, unfiltered and capped at 1000, and lists every CNV", async () => {
    renderPage();

    const link = await screen.findByRole('link', { name: DEL_22Q11.label });
    expect(link).toHaveAttribute('href', '/cnv-details/cnv-22q11');
    expect(mockedGet).toHaveBeenCalledWith('/cnvs/GRCh38/catalog', {
      params: { search: undefined, limit: 1000 },
    });
    expect(cellsOf(DEL_22Q11.label)).toEqual([
      DEL_22Q11.label,
      '22q11.21',
      '22:18,912,231–21,465,672',
      '2.55 Mb',
    ]);
    expect(resultCount()).toHaveTextContent('3');
    // One assembly leaves nothing to choose.
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('reads each size in bp, kb or Mb and marks a CNV without a cytoband', async () => {
    serve({
      catalogue: [
        cnv({ _id: 'a', label: 'bp-sized', start: 100, end: 1099, cytoband: '' }),
        cnv({ _id: 'b', label: 'exactly 1 kb', start: 5000, end: 6000 }),
        cnv({ _id: 'c', label: 'kb-sized', start: 10000, end: 22345 }),
        cnv({ _id: 'd', label: 'exactly 1 Mb', start: 2000000, end: 3000000 }),
      ],
    });
    renderPage();

    await screen.findByRole('link', { name: 'bp-sized' });
    expect(cellsOf('bp-sized')).toEqual(['bp-sized', '—', '1:100–1,099', '999 bp']);
    expect(cellsOf('exactly 1 kb')[3]).toBe('1.0 kb');
    expect(cellsOf('kb-sized')[3]).toBe('12.3 kb');
    expect(cellsOf('exactly 1 Mb')[3]).toBe('1.00 Mb');
  });

  it('sends the trimmed search to the catalogue, and Clear returns to the full list', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('link', { name: DEL_22Q11.label });
    expect(screen.queryByRole('button', { name: 'Clear' })).not.toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: 'Search' }), '  22q11  ');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() =>
      expect(mockedGet).toHaveBeenLastCalledWith('/cnvs/GRCh38/catalog', {
        params: { search: '22q11', limit: 1000 },
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole('link', { name: DUP_17P12.label })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole('link', { name: DEL_22Q11.label })).toBeInTheDocument();
    expect(resultCount()).toHaveTextContent('1');

    await user.click(screen.getByRole('button', { name: 'Clear' }));

    expect(screen.getByRole('textbox', { name: 'Search' })).toHaveValue('');
    expect(await screen.findByRole('link', { name: DUP_17P12.label })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Clear' })).not.toBeInTheDocument();
  });

  it('treats a whitespace-only search as no search', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('link', { name: DEL_22Q11.label });

    // Enter submits the form just like the button.
    await user.type(screen.getByRole('textbox', { name: 'Search' }), '   {Enter}');

    expect(screen.queryByRole('button', { name: 'Clear' })).not.toBeInTheDocument();
    expect(catalogueCalls().length).toBeGreaterThan(0);
    for (const [, config] of catalogueCalls()) {
      expect(config).toEqual({ params: { search: undefined, limit: 1000 } });
    }
    expect(screen.getAllByRole('link')).toHaveLength(3);
  });

  it('offers an assembly picker when several are loaded and re-queries the chosen one', async () => {
    const user = userEvent.setup();
    serve({ assemblies: [GRCH38, GRCH37] });
    renderPage();
    await screen.findByRole('link', { name: DEL_22Q11.label });

    const picker = screen.getByRole('combobox', { name: 'Assembly' });
    expect(picker).toHaveValue('GRCh38');
    expect(within(picker).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'GRCh38',
      'GRCh37',
    ]);
    // The first assembly listed is the one shown until another is picked.
    expect(mockedGet).toHaveBeenCalledWith('/cnvs/GRCh38/catalog', expect.anything());
    expect(mockedGet).not.toHaveBeenCalledWith('/cnvs/GRCh37/catalog', expect.anything());

    await user.selectOptions(picker, 'GRCh37');

    expect(picker).toHaveValue('GRCh37');
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/cnvs/GRCh37/catalog', {
        params: { search: undefined, limit: 1000 },
      }),
    );
  });

  it('encodes the assembly name as one path segment (#521)', async () => {
    serve({ assemblies: [{ id: 'odd', assembly_name: 'GRCh38/../admin', version: '1' }] });
    renderPage();

    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/cnvs/GRCh38%2F..%2Fadmin/catalog', expect.anything()),
    );
  });

  it('shows a loading line, and no count, while the catalogue loads', async () => {
    serve({ pendingCatalogue: true });
    renderPage();

    expect(await screen.findByText('Loading clinical CNVs…')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(resultCount()).toBeNull();
  });

  it('says so, with a zero count, when nothing in the catalogue matches', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('link', { name: DEL_22Q11.label });

    await user.type(screen.getByRole('textbox', { name: 'Search' }), 'no-such-syndrome{Enter}');

    expect(await screen.findByText('No clinical CNVs match the current search.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(resultCount()).toHaveTextContent('0');
  });
  it('does not read an unfinished or failed lookup as "no match" (#526)', async () => {
    // Still loading the assemblies: no catalogue request yet, and no "no match".
    mockedGet.mockImplementation(() => new Promise(() => undefined));
    const { unmount } = renderPage();
    expect(await screen.findByText('Loading assemblies…')).toBeInTheDocument();
    expect(screen.queryByText(/No clinical CNVs match/)).not.toBeInTheDocument();
    unmount();

    // The catalogue request fails: a failure with the reason and a retry.
    let fail = true;
    mockedGet.mockImplementation((url: string) => {
      if (url === '/assemblies') return Promise.resolve({ data: [GRCH38] });
      if (fail) return Promise.reject({ response: { status: 500, data: { detail: 'ClickHouse unavailable' } } });
      return Promise.resolve({ data: [DEL_22Q11] });
    });
    renderPage();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the clinical CNV catalogue (ClickHouse unavailable)');
    expect(alert).toHaveTextContent('this is not an empty result');
    expect(screen.queryByText(/No clinical CNVs match/)).not.toBeInTheDocument();
    expect(resultCount()).toBeNull();
    fail = false;
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('link', { name: DEL_22Q11.label })).toBeInTheDocument();
  });

  it('shows a failed or empty assembly list for what it is', async () => {
    let fail = true;
    mockedGet.mockImplementation((url: string) => {
      if (url === '/assemblies') {
        return fail ? Promise.reject(new Error('500')) : Promise.resolve({ data: [] });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    renderPage();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the assemblies — this is not an empty result.');
    fail = false;
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(
      await screen.findByText(/No assembly is set up in this CoGA instance/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No clinical CNVs match/)).not.toBeInTheDocument();
  });

  it('says when the 1,000-row page is full, so the list may go on', async () => {
    serve({
      catalogue: Array.from({ length: 1000 }, (_, index) =>
        cnv({ _id: `cnv-${index}`, label: `CNV ${index}`, chr: '1', start: index, end: index + 1 }),
      ),
    });
    renderPage();
    expect(await screen.findByText(/Showing the first 1,000 CNVs; narrow the search/)).toBeInTheDocument();
    expect(resultCount()).toHaveTextContent('1,000+');
  });
});
