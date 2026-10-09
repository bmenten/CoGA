import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import FamilyRepeatExpansionsPage, { formatCutoff } from '../FamilyRepeatExpansionsPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

vi.mock('../../../lib/reference', () => ({
  useFamilyReference: () => ({
    assemblyName: 'GRCh38',
    assemblyVersion: 'p14',
    projectId: undefined,
    isLoading: false,
  }),
  formatResolvedReferenceLabel: ({
    speciesName,
    assemblyName,
    assemblyVersion,
  }: {
    speciesName?: string;
    assemblyName?: string;
    assemblyVersion?: string;
  }) =>
    [speciesName, assemblyName ? `${assemblyName}${assemblyVersion ? ` ${assemblyVersion}` : ''}` : undefined]
      .filter(Boolean)
      .join(' • ') || 'Not linked',
}));

describe('FamilyRepeatExpansionsPage', () => {
  it('renders family calls stacked in one column and keeps cutoffs at the far right', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
              { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
            ],
            projects: [],
          },
        });
      }

      if (url === '/families/F1/repeat-expansions') {
        return Promise.resolve({
          data: {
            samples: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
              { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
            ],
            loci: [
              {
                locus_id: 'htt',
                gene: 'HTT',
                display_name: 'HTT',
                disease: 'Huntington disease',
                chr: '4',
                start: 3074876,
                end: 3074933,
                motif: 'CAG',
                warning_min: 36,
                pathogenic_min: 40,
                status: 'pathogenic',
                calls: {
                  PROBAND: {
                    sample: 'PROBAND',
                    role: 'proband',
                    affected: true,
                    sex: 'male',
                    genotype: '18/42',
                    allele_count: 2,
                    status: 'pathogenic',
                    alleles: [
                      { repeat_count: 18, status: 'normal' },
                      {
                        repeat_count: 42,
                        status: 'pathogenic',
                        motif_counts: [
                          { motif: 'CAG', count: 40 },
                          { motif: 'CAA', count: 2 },
                        ],
                        interrupted: true,
                        interruption_label: 'CAG 40 + CAA 2',
                      },
                    ],
                  },
                  MOTHER: {
                    sample: 'MOTHER',
                    role: 'mother',
                    affected: false,
                    sex: 'female',
                    genotype: '17/20',
                    allele_count: 2,
                    status: 'normal',
                    alleles: [
                      { repeat_count: 17, status: 'normal' },
                      { repeat_count: 20, status: 'normal' },
                    ],
                  },
                },
              },
              {
                locus_id: 'tbp',
                gene: 'TBP',
                display_name: 'TBP',
                disease: 'Spinocerebellar ataxia type 17',
                chr: '6',
                start: 170561906,
                end: 170561944,
                motif: 'CAG',
                warning_min: 42,
                pathogenic_min: 49,
                status: 'normal',
                calls: {
                  PROBAND: {
                    sample: 'PROBAND',
                    role: 'proband',
                    affected: true,
                    sex: 'male',
                    genotype: '34/36',
                    allele_count: 2,
                    status: 'normal',
                    alleles: [
                      { repeat_count: 34, status: 'normal' },
                      { repeat_count: 36, status: 'normal' },
                    ],
                  },
                },
              },
            ],
          },
        });
      }

      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/repeat-expansions']}>
          <Routes>
            <Route
              path="/families/:familyId/repeat-expansions"
              element={<FamilyRepeatExpansionsPage />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /trgt repeat table/i })).toBeInTheDocument();
    });

    const headers = screen.getAllByRole('columnheader').map((header) =>
      header.textContent?.replace(/\s+/g, ' ').trim(),
    );
    expect(headers).toEqual(['Repeat', 'Disease', 'Family calls', 'Cutoffs']);
    expect(screen.queryByRole('columnheader', { name: /proband/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: /mother/i })).not.toBeInTheDocument();

    const row = screen.getByRole('row', { name: /HTT/ });
    expect(row).not.toBeNull();
    const rowScope = within(row as HTMLElement);
    expect(row).toHaveClass('family-repeat-table-row--pathogenic');

    expect(rowScope.getByText('PROBAND')).toBeInTheDocument();
    expect(rowScope.getByText('MOTHER')).toBeInTheDocument();
    expect(rowScope.getByText('18 / 42')).toBeInTheDocument();
    expect(rowScope.getByText('Interruption A2: CAG 40 + CAA 2')).toBeInTheDocument();
    expect(rowScope.getByText('17 / 20')).toBeInTheDocument();
    expect(rowScope.getAllByText(/pathogenic/i).length).toBeGreaterThan(0);
    expect(rowScope.getByRole('link', { name: 'Huntington disease' })).toHaveAttribute(
      'href',
      'https://www.omim.org/search?index=entry&search=Huntington%20disease',
    );
    // Both genome links leave this list behind, so both open a tab — matching the
    // small-variant and structural-variant tables.
    const chromosomeLink = rowScope.getByRole('link', {
      name: /chromosome view ±1 mb.*opens in a new tab/i,
    });
    expect(chromosomeLink).toHaveAttribute(
      'href',
      '/families/F1/chromosome/4?start=2074876&end=4074933',
    );
    expect(chromosomeLink).toHaveAttribute('target', '_blank');
    expect(chromosomeLink).toHaveAttribute('rel', 'noopener noreferrer');

    // IGV opens tight on the repeat: what matters there is the reads spanning it.
    // The locus is the repeat (3,074,876-3,074,933) padded by 1 kb either side.
    const igvLink = rowScope.getByRole('link', {
      name: /open igv at chr4:3073876-3075933.*opens in a new tab/i,
    });
    expect(igvLink).toHaveAttribute(
      'href',
      '/families/F1/igv?locus=chr4%3A3073876-3075933' +
        '&back_path=%2Ffamilies%2FF1%2Frepeat-expansions',
    );
    expect(igvLink).toHaveAttribute('target', '_blank');

    const lastCell = (row as HTMLElement).querySelectorAll('td').item(3);
    expect(lastCell.textContent?.replace(/\s+/g, ' ').trim()).toBe('orange ≥ 36 · red ≥ 40');

    // The shared family header: the title is the way back to the workspace, and the
    // page no longer carries a separate button to the same place.
    expect(screen.getByRole('link', { name: 'Family F1' })).toHaveAttribute(
      'href',
      '/families/F1',
    );
    expect(screen.getByText('2 of 2 loci')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Gene'), { target: { value: 'TBP' } });
    expect(screen.getByText('1 of 2 loci')).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /TBP/ })).toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /HTT/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Aberrant only'));
    expect(screen.getByText('0 of 2 loci')).toBeInTheDocument();
    expect(screen.getByText(/no repeat expansion loci match/i)).toBeInTheDocument();
  });
});

describe('FamilyRepeatExpansionsPage — review status (#535)', () => {
  it('keeps a locus the catalog cannot classify in the aberrant-only view', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' }],
            projects: [],
          },
        });
      }
      if (url === '/families/F1/repeat-expansions') {
        return Promise.resolve({
          data: {
            samples: [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' }],
            loci: [
              {
                locus_id: 'vwa1',
                gene: 'VWA1',
                display_name: 'VWA1',
                disease: 'Hereditary motor neuropathy',
                chr: '1',
                start: 1435798,
                end: 1435818,
                motif: 'GGCGCGGAGC',
                warning_min: null,
                pathogenic_min: 1,
                benign_min: 2,
                benign_max: 2,
                pathogenic_max: 3,
                // VWA1 2/5: one normal allele and one outside every catalogued range.
                status: 'review',
                calls: {
                  PROBAND: {
                    sample: 'PROBAND',
                    role: 'proband',
                    affected: true,
                    sex: 'male',
                    genotype: '2/5',
                    allele_count: 2,
                    status: 'review',
                    alleles: [
                      { repeat_count: 2, status: 'normal' },
                      { repeat_count: 5, status: 'review' },
                    ],
                  },
                },
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/repeat-expansions']}>
          <Routes>
            <Route path="/families/:familyId/repeat-expansions" element={<FamilyRepeatExpansionsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect((await screen.findAllByText(/Review: outside catalogued ranges/)).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByLabelText('Aberrant only'));
    // Before #535 this allele ranked below "normal" and the row vanished from the view.
    expect(screen.getByText('1 of 1 loci')).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /VWA1/ })).toBeInTheDocument();
  });

  it("shows both of a male's chrX alleles and the backend's review note", async () => {
    const note =
      'Two different chrX alleles in a male: size mosaicism, TRGT run without --karyotype XY, ' +
      'a recorded sex that does not match the sample, or 47,XXY. Both alleles are shown; review.';
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [{ sample_id: 'SON', role: 'proband', affected: true, sex: 'male' }],
            projects: [],
          },
        });
      }
      if (url === '/families/F1/repeat-expansions') {
        return Promise.resolve({
          data: {
            samples: [{ sample_id: 'SON', role: 'proband', affected: true, sex: 'male' }],
            loci: [
              {
                locus_id: 'FXS_FMR1',
                gene: 'FMR1',
                display_name: 'FMR1',
                disease: 'Fragile X syndrome',
                chr: 'X',
                start: 147912051,
                end: 147912110,
                motif: 'CGG',
                warning_min: 55,
                pathogenic_min: 201,
                // A mosaic male: the premutation-sized and full-mutation alleles.
                status: 'pathogenic',
                calls: {
                  SON: {
                    sample: 'SON',
                    role: 'proband',
                    affected: true,
                    sex: 'male',
                    genotype: '1/2',
                    allele_count: 2,
                    status: 'pathogenic',
                    note,
                    alleles: [
                      { repeat_count: 90, status: 'intermediate' },
                      { repeat_count: 300, status: 'pathogenic' },
                    ],
                  },
                },
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/repeat-expansions']}>
          <Routes>
            <Route path="/families/:familyId/repeat-expansions" element={<FamilyRepeatExpansionsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('90 / 300')).toBeInTheDocument();
    expect(screen.getByText(note)).toBeInTheDocument();
  });
});

describe('FamilyRepeatExpansionsPage — FMR1 premutation', () => {
  it("labels a female partner's premutation as one and keeps it in the aberrant-only view", async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [
              { sample_id: 'MOTHER1', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'FATHER1', role: 'father', affected: false, sex: 'male' },
            ],
            projects: [],
          },
        });
      }
      if (url === '/families/F1/repeat-expansions') {
        return Promise.resolve({
          data: {
            samples: [
              { sample_id: 'MOTHER1', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'FATHER1', role: 'father', affected: false, sex: 'male' },
            ],
            loci: [
              {
                locus_id: 'FXS_FMR1',
                gene: 'FMR1',
                display_name: 'FMR1',
                disease: 'Fragile X syndrome',
                chr: 'X',
                start: 147912049,
                end: 147912111,
                motif: 'CGG',
                warning_min: 45,
                pathogenic_min: 201,
                benign_min: 5,
                benign_max: 44,
                pathogenic_max: 2000,
                premutation_min: 55,
                status: 'premutation',
                calls: {
                  MOTHER1: {
                    sample: 'MOTHER1',
                    role: 'mother',
                    affected: false,
                    sex: 'female',
                    genotype: '1/2',
                    allele_count: 2,
                    status: 'premutation',
                    alleles: [
                      { repeat_count: 30, status: 'normal' },
                      { repeat_count: 72, status: 'premutation' },
                    ],
                  },
                  FATHER1: {
                    sample: 'FATHER1',
                    role: 'father',
                    affected: false,
                    sex: 'male',
                    genotype: '1',
                    allele_count: 1,
                    status: 'normal',
                    alleles: [{ repeat_count: 29, status: 'normal' }],
                  },
                },
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/repeat-expansions']}>
          <Routes>
            <Route path="/families/:familyId/repeat-expansions" element={<FamilyRepeatExpansionsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect((await screen.findAllByText('Premutation')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Grey zone')).not.toBeInTheDocument();
    expect(screen.getByText('orange ≥ 45 · premutation ≥ 55 · red ≥ 201')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Aberrant only'));
    expect(screen.getByText('1 of 1 loci')).toBeInTheDocument();
  });
});

describe('repeat cutoff labels', () => {
  // A locus that is pathogenic by contraction cannot be stated as a lower bound: VWA1
  // is normal at exactly 2, and "red ≥ 1" would mark every healthy call.
  const cases: Array<[string, Parameters<typeof formatCutoff>[0], string]> = [
    [
      'VWA1 (normal exactly 2, abnormal 1 or 3)',
      { warning_min: null, pathogenic_min: 1, benign_min: 2, benign_max: 2, pathogenic_max: 3 },
      'normal 2 · red 1-3',
    ],
    [
      'MIR7-2 (normal 4, abnormal 3)',
      { warning_min: null, pathogenic_min: 3, benign_min: 4, benign_max: 4, pathogenic_max: 3 },
      'normal 4 · red 3',
    ],
    [
      'HTT (expansion, both thresholds)',
      { warning_min: 27, pathogenic_min: 36, benign_min: 6, benign_max: 26, pathogenic_max: 250 },
      'orange ≥ 27 · red ≥ 36',
    ],
    [
      'FMR1 (grey zone, premutation and full mutation)',
      {
        warning_min: 45,
        pathogenic_min: 201,
        benign_min: 5,
        benign_max: 44,
        pathogenic_max: 2000,
        premutation_min: 55,
      },
      'orange ≥ 45 · premutation ≥ 55 · red ≥ 201',
    ],
    [
      'expansion locus with no grey zone',
      { warning_min: null, pathogenic_min: 40, benign_min: null, benign_max: null, pathogenic_max: null },
      'red ≥ 40',
    ],
    [
      'uncatalogued locus',
      { warning_min: null, pathogenic_min: null, benign_min: null, benign_max: null, pathogenic_max: null },
      'No reference cutoff',
    ],
  ];

  it.each(cases)('%s', (_name, row, expected) => {
    expect(formatCutoff(row)).toBe(expected);
  });
});

// #610 — any failure read "Family not found", a server error included.
describe('FamilyRepeatExpansionsPage — failed requests (#610)', () => {
  const serverError = () =>
    Promise.reject(
      Object.assign(new Error('Request failed with status code 500'), { response: { status: 500 } }),
    );
  const renderPage = () =>
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/repeat-expansions']}>
          <Routes>
            <Route path="/families/:familyId/repeat-expansions" element={<FamilyRepeatExpansionsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

  it('says the repeat expansions could not be loaded, not that the family was not found', async () => {
    let failedRequests = 0;
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1/repeat-expansions') {
        failedRequests += 1;
        return serverError();
      }
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      return Promise.resolve({ data: {} });
    });
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Repeat expansions could not be loaded' })).toBeInTheDocument();
    expect(
      screen.getByText('Request failed with status code 500 This is a failed request, not a missing family.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Family not found')).not.toBeInTheDocument();
    const before = failedRequests;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(failedRequests).toBeGreaterThan(before));
  });

  it('keeps "Family not found" for a family the API does not know', async () => {
    apiMock.get.mockImplementation(() =>
      Promise.reject(
        Object.assign(new Error('Request failed with status code 404'), {
          response: { status: 404, data: { detail: 'Family not found' } },
        }),
      ),
    );
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Family not found' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });
});
