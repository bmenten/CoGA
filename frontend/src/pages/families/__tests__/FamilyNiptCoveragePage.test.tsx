// The plasma's on-target coverage page, opened from the NIPT page (REQ-NIPT-006): the genes
// below the target depth, where a fetal variant can be missed, and those above it, each with
// its targets.

import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyNiptCoveragePage from '../FamilyNiptCoveragePage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

const target = (gene: string, start: number, mean: number, covered = 100) => ({
  chr: '7',
  start,
  end: start + 100,
  gene,
  attribute: `${gene};NM_1.1;ENST1;ENSE1;${start / 1000}`,
  mean,
  median: mean,
  min: covered < 100 ? 0 : mean,
  proportion_covered: covered,
});

const TARGET_COVERAGE = {
  targets: 7,
  median_mean: 950,
  q05_mean: 250,
  below_critical: 1,
  below_advisory: 2,
  zero_mean: 0,
  incomplete: 1,
  critical_mean_depth: 300,
  advisory_mean_depth: 1000,
  genes: [
    { gene: 'GENEA', targets: 3, weak_targets: 1, min_mean: 250, mean_of_means: 800, weak: [target('GENEA', 2000, 250)] },
    { gene: 'GENEZ', targets: 0, weak_targets: 0, min_mean: null, mean_of_means: null, weak: [] },
    { gene: 'GENEC', targets: 2, weak_targets: 0, min_mean: 1100, mean_of_means: 1200, weak: [] },
    { gene: 'GENEB', targets: 2, weak_targets: 0, min_mean: 900, mean_of_means: 1000, weak: [] },
  ],
};

const COVERAGE = {
  family_id: 'NIPT001',
  overall_median_on_target: null,
  target_region_count: 0,
  per_region: [],
  min_depth: 20,
  min_covered_fraction: 0.9,
  low_coverage_regions: [],
  targets: TARGET_COVERAGE,
};

const GENEA_TARGETS = {
  family_id: 'NIPT001',
  gene: 'GENEA',
  critical_mean_depth: 300,
  targets: [
    { ...target('GENEA', 1000, 1200), weak: false },
    { ...target('GENEA', 2000, 250), weak: true },
    { ...target('GENEA', 3000, 900, 97.5), weak: true },
  ],
};

const respond = (coverage: unknown) =>
  apiMock.get.mockImplementation((url: string) => {
    if (url === '/families/NIPT001/nipt/coverage') return Promise.resolve({ data: coverage });
    if (url === '/families/NIPT001/nipt/coverage/targets') return Promise.resolve({ data: GENEA_TARGETS });
    if (url === '/panels') return Promise.resolve({ data: [{ _id: 'panel-1', name: 'Skeletal dysplasia', version: 3 }] });
    return Promise.resolve({ data: [] });
  });

const renderPage = (entry: string | { pathname: string; search?: string; state?: unknown }) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/families/:familyId/nipt/coverage" element={<FamilyNiptCoveragePage />} />
          <Route path="/families/:familyId/nipt" element={<p>NIPT page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('FamilyNiptCoveragePage', () => {
  beforeEach(() => {
    apiMock.get.mockReset();
  });

  it('lists the genes below and above target over the NIPT search’s genes', async () => {
    respond(COVERAGE);
    renderPage('/families/NIPT001/nipt/coverage?project_id=project-1&panel_id=panel-1&gene=GENEZ');

    expect(await screen.findByRole('heading', { name: 'On-target coverage · Family NIPT001' })).toBeInTheDocument();
    // Every gene in scope, the passing ones too, of the NIPT page's panel and genes.
    expect(apiMock.get).toHaveBeenCalledWith('/families/NIPT001/nipt/coverage', {
      params: { all_genes: 'true', project_id: 'project-1', panel_id: 'panel-1', gene: 'GENEZ' },
    });
    expect(
      await screen.findByText('The genes of the NIPT search: gene panel Skeletal dysplasia (version 3) and gene GENEZ.'),
    ).toBeInTheDocument();

    const below = screen.getByRole('table', { name: 'Genes below target' });
    const belowRows = within(below).getAllByRole('row').slice(1);
    expect(belowRows.map((row) => within(row).getAllByRole('cell')[0].textContent)).toEqual(['GENEA', 'GENEZ']);
    // A selected gene the panel does not capture is below target too: nothing covers it.
    expect(within(belowRows[1]).getByText('not a target')).toBeInTheDocument();

    const above = screen.getByRole('table', { name: 'Genes above target' });
    const aboveRows = within(above).getAllByRole('row').slice(1);
    // Alphabetical.
    expect(aboveRows.map((row) => within(row).getAllByRole('cell')[0].textContent)).toEqual(['GENEB', 'GENEC']);
  });

  it('opens a gene to its targets, each above or below target and why', async () => {
    respond(COVERAGE);
    renderPage('/families/NIPT001/nipt/coverage?project_id=project-1');

    const toggle = await screen.findByRole('button', { name: 'GENEA' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    const targets = await screen.findByRole('table', { name: 'Targets of GENEA' });
    expect(apiMock.get).toHaveBeenCalledWith('/families/NIPT001/nipt/coverage/targets', {
      params: { gene: 'GENEA', project_id: 'project-1' },
    });
    const rows = within(targets).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(3);
    // BED coordinates: the first base is start + 1. The exon is the attribute's last field.
    expect(within(rows[0]).getByText('7:1,001-1,100')).toBeInTheDocument();
    expect(within(rows[0]).getAllByRole('cell')[0]).toHaveTextContent('1');
    expect(within(rows[0]).getByText('above')).toBeInTheDocument();
    expect(within(rows[1]).getByText('below · mean below 300x')).toBeInTheDocument();
    expect(within(rows[2]).getByText('below · 97.5% covered')).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByRole('table', { name: 'Targets of GENEA' })).not.toBeInTheDocument();
  });

  it('finds a gene in either table', async () => {
    respond(COVERAGE);
    renderPage('/families/NIPT001/nipt/coverage');

    expect(await screen.findByText('Every gene of the capture panel: no gene panel or gene was chosen on the NIPT page.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Find a gene'), { target: { value: 'geneb' } });
    expect(screen.getByText('No gene below target matches the search.')).toBeInTheDocument();
    const above = screen.getByRole('table', { name: 'Genes above target' });
    expect(within(above).getAllByRole('row')).toHaveLength(2);
    expect(within(above).getByText('GENEB')).toBeInTheDocument();
  });

  it('goes back to the NIPT page as it was left', async () => {
    respond(COVERAGE);
    renderPage({
      pathname: '/families/NIPT001/nipt/coverage',
      search: '?panel_id=panel-1',
      state: { from: '/families/NIPT001/nipt?panel_id=panel-1&page=2' },
    });

    expect(await screen.findByRole('link', { name: 'Back to NIPT' })).toHaveAttribute(
      'href',
      '/families/NIPT001/nipt?panel_id=panel-1&page=2',
    );
  });

  it('goes back to the NIPT page when opened directly', async () => {
    respond(COVERAGE);
    renderPage('/families/NIPT001/nipt/coverage');

    expect(await screen.findByRole('link', { name: 'Back to NIPT' })).toHaveAttribute('href', '/families/NIPT001/nipt');
  });

  it('splits the coverage track’s regions without a target table', async () => {
    respond({
      ...COVERAGE,
      targets: null,
      overall_median_on_target: 120,
      target_region_count: 2,
      per_region: [
        { label: 'BRCA1', chr: '17', start: 100, end: 200, median_coverage: 118, covered_bases: 100, target_bases: 100 },
        { label: 'ARID1B', chr: '6', start: 100, end: 200, median_coverage: 8, covered_bases: 100, target_bases: 100 },
      ],
      low_coverage_regions: [
        { label: 'ARID1B', chr: '6', median_coverage: 8, covered_fraction: 1, reason: 'low_depth' },
      ],
    });
    renderPage('/families/NIPT001/nipt/coverage');

    const below = await screen.findByRole('table', { name: 'Regions below target' });
    expect(within(below).getByText('ARID1B')).toBeInTheDocument();
    expect(within(below).getByText('below · 8x median')).toBeInTheDocument();
    const above = screen.getByRole('table', { name: 'Regions above target' });
    expect(within(above).getByText('BRCA1')).toBeInTheDocument();
  });

  // #606 — a failed request is said as such, never as a page without weak genes.
  it('says the coverage could not be loaded', async () => {
    apiMock.get.mockImplementation((url: string) =>
      url === '/families/NIPT001/nipt/coverage'
        ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
        : Promise.resolve({ data: [] }),
    );
    renderPage('/families/NIPT001/nipt/coverage');

    expect(await screen.findByText(/Could not load the coverage — this is not an empty result/)).toBeInTheDocument();
    expect(screen.queryByText(/Below target/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});
