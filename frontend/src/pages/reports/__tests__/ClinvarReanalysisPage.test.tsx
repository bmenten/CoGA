// The re-analysis signal after sign-out (B10): a project's signed analyses with a small variant
// CoGA's current ClinVar release classifies otherwise than its signed version saw, each linked
// to that signed version. Read only, and never shown empty for a request that failed.
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ClinvarReanalysisPage from '../ClinvarReanalysisPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: apiMock }));

const PROJECTS = [
  { id: 'p-1', name: 'Rare disease' },
  { id: 'p-2', name: 'NIPT' },
];
const value = (classification: string, release: string, stars = 1) => ({
  alt: 'A',
  status: 'in_release',
  release,
  classification,
  review_status: 'criteria provided, single submitter',
  stars,
  included_only: false,
  conditions: [],
  variation_id: null,
});
const LIST = {
  project_id: 'p-1',
  releases: { GRCh38: '2026-11-22' },
  items: [
    {
      family_id: 'FAM 1',
      analysis: 'long_read_wgs',
      version: 2,
      signed_out_at: '2026-11-16T09:00:00Z',
      report_code: '7KQM2X-2-4PHT',
      variant_id: '1-100-G-A',
      reported: true,
      classification: 'VUS - class 3',
      clinvar_coga_frozen: [value('Uncertain_significance', '2026-11-15')],
      clinvar_coga_now: [value('Likely_pathogenic', '2026-11-22')],
    },
  ],
};

const respond = (list: unknown) =>
  apiMock.get.mockImplementation(async (path: string) => {
    if (path === '/projects') return { data: PROJECTS };
    if (path === '/projects/p-1/clinvar-reanalysis') return { data: list };
    throw new Error(`unexpected ${path}`);
  });

const renderAt = (path: string) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/clinvar-reanalysis" element={<ClinvarReanalysisPage />} />
          <Route path="/families/:familyId/report" element={<p>The signed version</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('ClinvarReanalysisPage', () => {
  beforeEach(() => {
    apiMock.get.mockReset();
  });

  it('lists a chosen project’s reclassified variants, each linked to its signed version', async () => {
    respond(LIST);
    renderAt('/clinvar-reanalysis');

    await screen.findByRole('option', { name: 'Rare disease' });
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'p-1' } });

    const list = await screen.findByRole('region', { name: 'ClinVar reclassifications' });
    expect(list).toHaveTextContent('Read against ClinVar 2026-11-22 for GRCh38.');
    expect(within(list).getAllByRole('columnheader').map((header) => header.textContent)).toContain('ClinVar as signed');
    const row = within(list).getAllByRole('row')[1];
    expect(row).toHaveTextContent('FAM 1 (long_read_wgs)');
    expect(row).toHaveTextContent('Version 2, 2026-11-16 09:00 UTC · 7KQM2X-2-4PHT');
    expect(row).toHaveTextContent('1-100-G-AReportedVUS - class 3');
    expect(row).toHaveTextContent('Uncertain significance ★☆☆☆ (2026-11-15)');
    expect(row).toHaveTextContent('Likely pathogenic ★☆☆☆ (2026-11-22)');
    const open = within(row).getByRole('link', { name: 'Version 2' });
    expect(open).toHaveAttribute('href', '/families/FAM%201/report?version=2');
    fireEvent.click(open);
    expect(await screen.findByText('The signed version')).toBeInTheDocument();
  });

  it('says so when no signed analysis has a reclassified variant', async () => {
    respond({ ...LIST, items: [] });
    renderAt('/clinvar-reanalysis?project=p-1');

    expect(
      await screen.findByText('No signed analysis of this project has a variant that the current release classifies otherwise.'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Project')).toHaveValue('p-1');
  });

  it('names an assembly without a release', async () => {
    respond({ ...LIST, releases: { GRCh38: null }, items: [] });
    renderAt('/clinvar-reanalysis?project=p-1');

    expect(await screen.findByText('Read against no ClinVar release for GRCh38.')).toBeInTheDocument();
  });

  it('says a failed list could not be loaded, never that it is empty', async () => {
    apiMock.get.mockImplementation(async (path: string) => {
      if (path === '/projects') return { data: PROJECTS };
      throw { response: { status: 500, data: { detail: 'The server failed.' } } };
    });
    renderAt('/clinvar-reanalysis?project=p-1');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load the ClinVar reclassifications — this is not an empty result.',
    );
    expect(screen.queryByText(/No signed analysis/)).not.toBeInTheDocument();
  });
});
