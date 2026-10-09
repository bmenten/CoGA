// Pins the family SV summary: the requests it makes (encoded id, 100k cap), per-chromosome/type
// counts in karyotype order, the unique/shared sample matrix, absolute-length histograms, states.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyVariantSummaryPage from '../FamilyVariantSummaryPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import {
  INCOMPLETE_IMPORT_METADATA,
  findAssemblyScopeBanner,
  findImportIncompleteBanner,
  offScopeProject,
} from '../../../test/familyPageBanners';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn() } }));

// Drawing the bars is Histogram's concern (it has its own test); what the page decides is
// which lengths each histogram is fed, on which size bins and scale.
vi.mock('../../../components/visualizations/Histogram', () => ({
  default: ({ data, binLabels, logScale }: { data: number[]; binLabels?: string[]; logScale?: boolean }) => (
    <div data-testid="histogram" data-bins={binLabels?.join('|')} data-log-scale={String(logScale)}>
      {data.join(' ')}
    </div>
  ),
}));

const mockedGet = vi.mocked(api.get);

type LengthRow = { length: number; type: string; source?: string | null; chr: string };

const FAMILY = {
  family_id: 'F1',
  pedigree: 'F1 S1 S2 S3 1 2\nF1 S2 0 0 1 1\nF1 S3 0 0 2 1',
  members: [
    { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' },
    { sample_id: 'S2', role: 'father', affected: false, sex: 'male' },
    { sample_id: 'S3', role: 'mother', affected: false, sex: 'female' },
  ],
};

const sv = (chr: string, type: string, length = 5000, source: string | null = 'sniffles'): LengthRow => ({
  chr,
  type,
  length,
  source,
});

/** Answer the page's three requests; a Promise value is used as-is (pending or rejected). */
const serve = (
  familyId: string,
  {
    lengths = [] as LengthRow[] | Promise<never>,
    shared = {} as Record<string, Record<string, number>> | Promise<never>,
  } = {},
) => {
  const routes: Record<string, unknown> = {
    [`/families/${familyId}`]: FAMILY,
    [`/families/${familyId}/structural-variant-lengths`]: lengths,
    [`/families/${familyId}/shared-structural-variant-counts`]: shared,
  };
  mockedGet.mockImplementation((url: string) => {
    if (!(url in routes)) return Promise.reject(new Error(`Unexpected GET ${url}`));
    const value = routes[url];
    return value instanceof Promise ? value : Promise.resolve({ data: value });
  });
};

const renderPage = (path = '/families/F1/variant-summary') =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/families/:familyId/variant-summary" element={<FamilyVariantSummaryPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

const sectionTitled = (title: string) => {
  const section = screen.getByRole('heading', { name: title, level: 2 }).closest('section');
  expect(section).not.toBeNull();
  return section as HTMLElement;
};

const readTable = (section: HTMLElement) =>
  within(within(section).getByRole('table'))
    .getAllByRole('row')
    .map((row) => Array.from(row.querySelectorAll('th, td')).map((cell) => cell.textContent));

/** The histogram under a sub-heading (a variant type or a caller) of a section. */
const histogramUnder = (section: HTMLElement, heading: string) => {
  const panel = within(section).getByRole('heading', { name: heading, level: 3 }).parentElement;
  expect(panel).not.toBeNull();
  return within(panel as HTMLElement).getByTestId('histogram');
};

describe('FamilyVariantSummaryPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("asks for the family's SV lengths (capped at 100k) and sample sharing, with the id encoded", async () => {
    // A family id with a path separator must stay one segment, not reach another endpoint (#521).
    serve('FAM%201%2F2', { lengths: [sv('1', 'DEL')], shared: { S1: { S1: 1 } } });
    renderPage('/families/FAM%201%2F2/variant-summary');

    expect(await screen.findByText(/Total variants: 1/)).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledWith('/families/FAM%201%2F2/structural-variant-lengths', {
      params: { limit: 100000 },
    });
    expect(mockedGet).toHaveBeenCalledWith('/families/FAM%201%2F2/shared-structural-variant-counts');
    expect(mockedGet).toHaveBeenCalledWith('/families/FAM%201%2F2');
  });

  it('opens with the shared family header, pedigree included', async () => {
    serve('F1', { lengths: [sv('1', 'DEL')], shared: { S1: { S1: 1 } } });
    renderPage();

    expect(await screen.findByRole('link', { name: 'Family F1' })).toHaveAttribute('href', '/families/F1');
    expect(screen.getByText('Variant summary')).toBeInTheDocument();
    // The page says what it summarises: the structural variants, not the small variants.
    expect(screen.getByText(/of this family's structural variants, from every caller/)).toBeInTheDocument();
    expect(await screen.findByText('Pedigree')).toBeInTheDocument();
  });

  // Like every family page, its header warns of a partly imported family and of one off the
  // validated scope.
  it('warns in its header that the import is incomplete and the assembly is not validated', async () => {
    mockedGet.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: { ...FAMILY, projects: ['p1'], metadata: INCOMPLETE_IMPORT_METADATA },
        });
      }
      if (url === '/projects') return Promise.resolve({ data: [offScopeProject('p1')] });
      if (url === '/families/F1/structural-variant-lengths') return Promise.resolve({ data: [sv('1', 'DEL')] });
      if (url === '/families/F1/shared-structural-variant-counts') {
        return Promise.resolve({ data: { S1: { S1: 1 } } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    renderPage();

    const header = (await screen.findByRole('heading', { name: 'Family F1' })).closest('.page-top-card');
    expect(header).toContainElement(await findImportIncompleteBanner());
    expect(header).toContainElement(await findAssemblyScopeBanner());
  });

  it('shows a loading state until both the lengths and the sharing matrix have arrived', async () => {
    serve('F1', { lengths: [sv('1', 'DEL')], shared: new Promise<never>(() => undefined) });
    renderPage();

    expect(await screen.findByRole('status', { name: 'Loading variant summary' })).toBeInTheDocument();
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/families/F1/structural-variant-lengths', expect.anything()),
    );
    // Half a summary is not shown: the counts wait for the matrix.
    expect(screen.queryByText(/Total variants/)).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('counts SVs per chromosome and type, in karyotype order, with a total per chromosome', async () => {
    serve('F1', {
      lengths: [
        sv('2', 'DEL'),
        sv('10', 'DUP'),
        sv('X', 'DEL'),
        sv('1', 'DEL'),
        sv('1', 'INS'),
        sv('2', 'DEL'),
        sv('MT', 'DEL'),
        sv('Y', 'INV'),
        // A call without a chromosome or type is counted, not dropped.
        sv('', ''),
      ],
      shared: { S1: { S1: 9 } },
    });
    renderPage();

    expect(await screen.findByText('Total variants: 9')).toBeInTheDocument();
    expect(readTable(sectionTitled('Variant counts by chromosome and type'))).toEqual([
      ['Chromosome', 'DEL', 'DUP', 'INS', 'INV', 'unknown', 'Total'],
      ['1', '1', '0', '1', '0', '0', '2'],
      ['2', '2', '0', '0', '0', '0', '2'],
      ['10', '0', '1', '0', '0', '0', '1'],
      ['X', '1', '0', '0', '0', '0', '1'],
      ['Y', '0', '0', '0', '1', '0', '1'],
      ['MT', '1', '0', '0', '0', '0', '1'],
      ['unknown', '0', '0', '0', '0', '1', '1'],
    ]);
    // Below the cap nothing is said about truncation.
    expect(screen.queryByText(/showing first/)).not.toBeInTheDocument();
  });

  it('says when the 100,000-SV cap was reached, so the counts are known to be partial', async () => {
    serve('F1', {
      lengths: Array.from({ length: 100000 }, () => sv('1', 'DEL', 50)),
      shared: { S1: { S1: 100000 } },
    });
    renderPage();

    expect(
      await screen.findByText('Total variants: 100000 (showing first 100000)'),
    ).toBeInTheDocument();
  });

  it('tabulates SVs unique to each sample on the diagonal and pairwise-shared ones off it', async () => {
    serve('F1', {
      lengths: [sv('1', 'DEL')],
      // Keys arrive in any order; a pair the server left out reads 0, not blank; a member
      // without SV calls (S4) keeps a row and column of zeros rather than dropping out.
      shared: {
        S3: { S1: 3, S2: 1, S3: 6, S4: 0 },
        S1: { S1: 5, S2: 4, S3: 3, S4: 0 },
        S4: { S1: 0, S2: 0, S3: 0, S4: 0 },
        S2: { S1: 4, S2: 7, S4: 0 },
      },
    });
    renderPage();

    await screen.findByText(/Total variants/);
    const rows = readTable(sectionTitled('Shared and unique variants'));
    expect(rows).toEqual([
      ['Sample', 'S1', 'S2', 'S3', 'S4'],
      ['S1', '5', '4', '3', '0'],
      ['S2', '4', '7', '0', '0'],
      ['S3', '3', '1', '6', '0'],
      ['S4', '0', '0', '0', '0'],
    ]);
    expect(
      screen.getByText(/Diagonal counts denote variants unique to the individual; off-diagonal/),
    ).toBeInTheDocument();
  });

  it('shows no totals for the sharing matrix, whose cells do not add up to one (#526)', async () => {
    // One SV carried by all three of a trio is in every pair's cell: the row sums read
    // 2 each and the grand sum 6, while the family has one SV.
    serve('F1', {
      lengths: [sv('1', 'DEL')],
      shared: {
        M: { M: 0, F: 1, P: 1 },
        F: { M: 1, F: 0, P: 1 },
        P: { M: 1, F: 1, P: 0 },
      },
    });
    renderPage();

    await screen.findByText(/Total variants: 1/);
    const rows = readTable(sectionTitled('Shared and unique variants'));
    expect(rows[0]).toEqual(['Sample', 'F', 'M', 'P']);
    expect(rows.map((row) => row[0])).not.toContain('Total');
    expect(
      screen.getByText(/counted in every pair, so the cells do not add up to a total/),
    ).toBeInTheDocument();
  });

  it('bins every SV by its absolute length — overall, per type and per caller', async () => {
    serve('F1', {
      lengths: [
        // Deletions carry a negative SVLEN; they are binned by size all the same.
        sv('1', 'DEL', -1500, 'sniffles'),
        sv('2', 'DUP', 3000, 'cnvkit'),
        sv('3', 'DEL', -250, null),
        sv('4', 'INV', 20000, 'sniffles'),
      ],
      shared: { S1: { S1: 4 } },
    });
    renderPage();

    await screen.findByText(/Total variants/);
    const all = within(sectionTitled('All variants')).getByTestId('histogram');
    expect(all).toHaveTextContent('1500 3000 250 20000');
    expect(all).toHaveAttribute('data-bins', '0|1-10|10-100|100-1k|1k-10k|10k-100k|100k-1M|>1M');

    const byType = sectionTitled('By type');
    expect(histogramUnder(byType, 'DEL')).toHaveTextContent(/^1500 250$/);
    expect(histogramUnder(byType, 'DUP')).toHaveTextContent(/^3000$/);
    expect(histogramUnder(byType, 'INV')).toHaveTextContent(/^20000$/);

    // A call without a recorded caller is grouped as "unknown", not lost.
    const bySource = sectionTitled('By source');
    expect(histogramUnder(bySource, 'sniffles')).toHaveTextContent(/^1500 20000$/);
    expect(histogramUnder(bySource, 'cnvkit')).toHaveTextContent(/^3000$/);
    expect(histogramUnder(bySource, 'unknown')).toHaveTextContent(/^250$/);
  });

  it('switches every histogram between log and linear scale', async () => {
    serve('F1', { lengths: [sv('1', 'DEL'), sv('2', 'DUP')], shared: { S1: { S1: 2 } } });
    renderPage();

    await screen.findByText(/Total variants/);
    const scales = () => screen.getAllByTestId('histogram').map((h) => h.getAttribute('data-log-scale'));
    // All + two types + one caller.
    expect(scales()).toEqual(['true', 'true', 'true', 'true']);

    fireEvent.click(screen.getByRole('button', { name: 'Linear scale' }));
    expect(scales()).toEqual(['false', 'false', 'false', 'false']);

    fireEvent.click(screen.getByRole('button', { name: 'Log scale' }));
    expect(scales()).toEqual(['true', 'true', 'true', 'true']);
  });

  it('shows a family without SVs as zero counts rather than an error', async () => {
    serve('F1', { lengths: [], shared: {} });
    renderPage();

    expect(await screen.findByText('Total variants: 0')).toBeInTheDocument();
    expect(readTable(sectionTitled('Variant counts by chromosome and type'))).toEqual([
      ['Chromosome', 'Total'],
    ]);
    expect(within(sectionTitled('All variants')).getByTestId('histogram')).toBeEmptyDOMElement();
  });

  it('does not present a failed load as a zero-count summary', async () => {
    serve('F1', {
      lengths: Promise.reject({ response: { status: 500, data: { detail: 'ClickHouse unavailable' } } }),
      shared: { S1: { S1: 1 } },
    });
    renderPage();

    expect(screen.getByRole('status', { name: 'Loading variant summary' })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole('status', { name: 'Loading variant summary' })).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(/Total variants/)).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    // It says it failed, with the reason, rather than "not enough structural variant data".
    expect(screen.getByText('Could not load the variant summary')).toBeInTheDocument();
    expect(screen.getByText(/ClickHouse unavailable\. This is not an empty result\./)).toBeInTheDocument();
    expect(screen.queryByText(/not enough structural variant data/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});
