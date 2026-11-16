// The explorer's gene, transcript and cohort-frequency views (the owner's
// choices of 2026-11-12): what each shows and asks, and how a row or a cell opens the variants.

import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../../test/createTestQueryClient';
import GlobalSmallVariantExplorerPage from '../GlobalSmallVariantExplorerPage';
import type { ExplorerFrequency, ExplorerGenePage, ExplorerTranscriptPage, GlobalVariantPage } from '../types';

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock('../../../lib/api', () => ({
  default: { get: apiGetMock },
}));

const reply = (data: unknown) => Promise.resolve({ data });

const VARIANTS: GlobalVariantPage = {
  total: 0,
  total_is_estimated: false,
  page_size: 50,
  next_cursor: null,
  assembly_id: 'asm-38',
  assembly_name: 'GRCh38',
  pending_families: 0,
  variants: [],
};

const GENES: ExplorerGenePage = {
  total: 2,
  assembly_id: 'asm-38',
  assembly_name: 'GRCh38',
  pending_families: 0,
  sort: 'variants',
  order: 'desc',
  offset: 0,
  genes: [
    { gene: 'SYNGENE1', variants: 3, high: 1, moderate: 1, low: 1, modifier: 0, carriers: 7, het: 5, hom: 2 },
    { gene: 'SYNGENE2', variants: 1, high: 0, moderate: 0, low: 0, modifier: 1, carriers: 1, het: 1, hom: 0 },
  ],
};

const TRANSCRIPTS: ExplorerTranscriptPage = {
  total: 1,
  assembly_id: 'asm-38',
  assembly_name: 'GRCh38',
  pending_families: 0,
  transcripts: 'all',
  sort: 'variants',
  order: 'desc',
  offset: 0,
  rows: [
    {
      transcript_id: 'ENST00000600001',
      gene: 'SYNGENE1',
      mane: true,
      canonical: true,
      variants: 3,
      lof: 1,
      missense: 1,
      splice: 0,
      synonymous: 1,
      other: 0,
      carriers: 7,
    },
  ],
};

const AF_CLASSES = ['absent', 'below_0.1', '0.1_1', '1_5', 'above_5'];
const CARRIER_CLASSES: [string, number, number | null][] = [
  ['1', 1, 1],
  ['2-5', 2, 5],
  ['6-20', 6, 20],
  ['21-100', 21, 100],
  ['>100', 101, null],
];

const FREQUENCY: ExplorerFrequency = {
  assembly_id: 'asm-38',
  assembly_name: 'GRCh38',
  pending_families: 2,
  carrier_classes: CARRIER_CLASSES.map(([label]) => label),
  gnomad_af_classes: AF_CLASSES,
  cells: CARRIER_CLASSES.flatMap(([label, min, max]) =>
    AF_CLASSES.map((afClass) => ({
      carrier_class: label,
      carrier_min: min,
      carrier_max: max,
      gnomad_af_class: afClass,
      variants: label === '2-5' && afClass === 'absent' ? 4 : 0,
    })),
  ),
};

const mockApi = () => {
  apiGetMock.mockImplementation((url: string) => {
    const [path] = url.split('?');
    switch (path) {
      case '/variant-explorer/assemblies':
        return reply([
          { assembly_id: 'asm-38', assembly_name: 'GRCh38', version: null, species_name: null, project_count: 1 },
        ]);
      case '/variant-explorer/small-variant-tags':
      case '/panels':
      case '/variant-explorer/samples':
        return reply([]);
      case '/variant-explorer/small-variants':
        return reply(VARIANTS);
      case '/variant-explorer/small-variants/genes':
        return reply(GENES);
      case '/variant-explorer/small-variants/transcripts':
        return reply(TRANSCRIPTS);
      case '/variant-explorer/small-variants/frequency':
        return reply(FREQUENCY);
      default:
        return Promise.reject(new Error(`Unexpected GET ${url}`));
    }
  });
};

// The query of the last request to ``path``.
const lastRequest = (path: string): URLSearchParams => {
  const urls = apiGetMock.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith(`${path}?`));
  return new URLSearchParams(urls[urls.length - 1]?.split('?')[1] ?? '');
};

const renderPage = () => {
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={['/variant-explorer']}>
        <GlobalSmallVariantExplorerPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

afterEach(() => {
  apiGetMock.mockReset();
});

describe('the explorer views', () => {
  it('shows the genes with their variants by impact and carriers summed, and opens a gene', async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('tab', { name: 'Genes' }));

    expect(await screen.findByText('2 genes')).toBeInTheDocument();
    const row = screen.getByRole('button', { name: 'SYNGENE1' }).closest('tr') as HTMLElement;
    expect(within(row).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      'SYNGENE1',
      '3',
      '1',
      '1',
      '1',
      '0',
      '7',
      '5',
      '2',
    ]);
    expect(lastRequest('/variant-explorer/small-variants/genes').get('assembly_id')).toBe('asm-38');
    expect(lastRequest('/variant-explorer/small-variants/genes').get('sort')).toBe('variants');

    // Sorting by the summed carriers asks the server again, descending first.
    await user.click(screen.getByRole('columnheader', { name: /Carriers, summed/ }));
    await waitFor(() => expect(lastRequest('/variant-explorer/small-variants/genes').get('sort')).toBe('carriers'));

    // A gene opens the variant view with that gene.
    await user.click(screen.getByRole('button', { name: 'SYNGENE1' }));
    expect(screen.getByRole('tab', { name: 'Variants' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(lastRequest('/variant-explorer/small-variants').get('gene')).toBe('SYNGENE1'));
  });

  it('shows the transcripts and asks for the MANE ones on request', async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('tab', { name: 'Transcripts' }));

    expect(await screen.findByText('ENST00000600001')).toBeInTheDocument();
    expect(screen.getByText('MANE, canonical')).toBeInTheDocument();
    expect(lastRequest('/variant-explorer/small-variants/transcripts').get('transcripts')).toBe('all');

    await user.selectOptions(screen.getByRole('combobox', { name: 'Transcripts' }), 'mane');
    await waitFor(() =>
      expect(lastRequest('/variant-explorer/small-variants/transcripts').get('transcripts')).toBe('mane'),
    );
  });

  it('opens a cohort-frequency cell as the variants of its two classes, and names the pending families', async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('tab', { name: 'Cohort frequency' }));

    // The families the summary does not yet hold as they are.
    expect(await screen.findByRole('status')).toHaveTextContent('2 families are being added');
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Carriers',
      'Not in gnomAD',
      '< 0.1 %',
      '0.1–1 %',
      '1–5 %',
      '≥ 5 %',
    ]);

    await user.click(within(table).getByRole('button', { name: '4' }));
    expect(screen.getByRole('tab', { name: 'Variants' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/Cohort frequency: 2–5 carriers, gnomAD Not in gnomAD/)).toBeInTheDocument();
    await waitFor(() => {
      const request = lastRequest('/variant-explorer/small-variants');
      expect([
        request.get('min_total_samples'),
        request.get('max_total_samples'),
        request.get('gnomad_af_class'),
      ]).toEqual(['2', '5', 'absent']);
    });

    // Removing the chip asks for every variant again.
    await user.click(screen.getByRole('button', { name: 'Remove the cohort-frequency filter' }));
    await waitFor(() => expect(lastRequest('/variant-explorer/small-variants').get('gnomad_af_class')).toBeNull());
  });
});
