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
                warning_min: 41,
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

      return Promise.resolve({ data: [] });
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
      expect(screen.getByRole('heading', { name: /trgt repeat expansions/i })).toBeInTheDocument();
    });

    // A card like every variant card: the repeat at the left, the members' calls in the
    // genotype grid, no table and no column per member.
    expect(screen.queryByRole('columnheader')).not.toBeInTheDocument();
    const row = screen.getByRole('article', { name: /HTT/ });
    const rowScope = within(row);
    expect(row).toHaveClass('variant-card', 'variant-card--clinvar-pathogenic');
    expect(
      Array.from(row.querySelectorAll('.variant-card-col-title')).map((title) => title.textContent),
    ).toEqual(['Repeat', 'Genotypes']);
    const grid = row.querySelector('.variant-card-gtlist') as HTMLElement;
    expect(within(grid).getByText('PROBAND')).toBeInTheDocument();
    expect(within(grid).getByText('MOTHER')).toBeInTheDocument();
    expect(rowScope.getByText('18 / 42')).toBeInTheDocument();
    expect(rowScope.getByText('18 / 42').closest('.variant-card-gtline')).toHaveClass('variant-card-gtline--alert');
    expect(rowScope.getByText('Interruption A2: CAG 40 + CAA 2')).toHaveClass('variant-card-gtline-detail');
    expect(rowScope.getByText('17 / 20')).toBeInTheDocument();
    expect(rowScope.getAllByText(/pathogenic/i).length).toBeGreaterThan(0);
    // The gene leads to its gene page, its disease to OMIM, as on every variant card.
    const heading = row.querySelector('.gene-disease-heading') as HTMLElement;
    expect(heading).toHaveTextContent('HTT—Huntington disease');
    expect(within(heading).getByRole('link', { name: 'HTT' })).toHaveAttribute(
      'href',
      '/genes?gene=HTT&family_id=F1',
    );
    const omimLink = within(heading).getByRole('link', { name: /huntington disease on omim/i });
    expect(omimLink).toHaveAttribute(
      'href',
      'https://www.omim.org/search?index=entry&search=Huntington%20disease',
    );
    expect(omimLink).toHaveAttribute('target', '_blank');
    // The links sit in the footer in the order every card has: IGV, Chromosome view, OMIM.
    const nav = row.querySelector('.variant-card-footer .variant-card-nav') as HTMLElement;
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'IGV',
      'Chromosome view',
      'OMIM',
    ]);
    expect(within(nav).getByRole('link', { name: 'OMIM' })).toHaveAttribute(
      'href',
      'https://www.omim.org/search?search=HTT',
    );
    // Both genome links leave this list behind, so both open a tab — matching the
    // small-variant and structural-variant cards.
    const chromosomeLink = within(nav).getByRole('link', {
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
    const igvLink = within(nav).getByRole('link', {
      name: /open igv at chr4:3073876-3075933.*opens in a new tab/i,
    });
    expect(igvLink).toHaveAttribute(
      'href',
      '/families/F1/igv?locus=chr4%3A3073876-3075933' +
        '&back_path=%2Ffamilies%2FF1%2Frepeat-expansions',
    );
    expect(igvLink).toHaveAttribute('target', '_blank');

    expect(row.querySelector('.repeat-cutoff-cell')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
      'orange ≥ 36 · red ≥ 40',
    );

    // The shared family header: the title is the way back to the workspace, and the
    // page no longer carries a separate button to the same place.
    expect(screen.getByRole('link', { name: 'Family F1' })).toHaveAttribute(
      'href',
      '/families/F1',
    );
    expect(screen.getByText('2 of 2 loci')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Gene'), { target: { value: 'TBP' } });
    expect(screen.getByText('1 of 2 loci')).toBeInTheDocument();
    expect(screen.getByRole('article', { name: /TBP/ })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /HTT/ })).not.toBeInTheDocument();

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
    expect(screen.getByRole('article', { name: /VWA1/ })).toBeInTheDocument();
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

  // CLIN-14: a man's two normal chrX alleles read review for that reason, which the page
  // named "outside catalogued ranges", the reason of a count the catalogue cannot classify.
  it("names a man's two chrX alleles as the reason a call needs review", async () => {
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
                warning_min: 45,
                pathogenic_min: 201,
                status: 'review',
                review_reason: 'male_x_alleles',
                calls: {
                  SON: {
                    sample: 'SON',
                    role: 'proband',
                    affected: true,
                    sex: 'male',
                    genotype: '1/2',
                    allele_count: 2,
                    status: 'review',
                    review_reason: 'male_x_alleles',
                    note: 'Two different chrX alleles in a male.',
                    alleles: [
                      { repeat_count: 20, status: 'normal' },
                      { repeat_count: 30, status: 'normal' },
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

    expect((await screen.findAllByText('Review: two chrX alleles in a male')).length).toBeGreaterThan(0);
    expect(screen.queryByText(/outside catalogued ranges/)).not.toBeInTheDocument();
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

// CLIN-26 — a call whose TRID no catalogue row names got no thresholds and read Normal, so
// the aberrant-only view hid it, whatever its size.
describe('FamilyRepeatExpansionsPage — a locus without thresholds (CLIN-26)', () => {
  const members = [
    { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
    { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
  ];
  const call = (sample: string, status: string, counts: number[]) => ({
    sample,
    role: sample === 'PROBAND' ? 'proband' : 'mother',
    affected: sample === 'PROBAND',
    sex: sample === 'PROBAND' ? 'male' : 'female',
    genotype: '1/2',
    allele_count: counts.length,
    status,
    alleles: counts.map((count) => ({ repeat_count: count, status })),
  });
  const uncatalogued = {
    locus_id: 'chr2_1000000_1000030_CAG',
    gene: 'chr2_1000000_1000030_CAG',
    display_name: 'chr2_1000000_1000030_CAG',
    disease: 'chr2_1000000_1000030_CAG',
    chr: '2',
    start: 1000000,
    end: 1000030,
    motif: 'CAG',
    warning_min: null,
    pathogenic_min: null,
    status: 'not_assessed',
    calls: {
      PROBAND: call('PROBAND', 'not_assessed', [12, 300]),
      MOTHER: call('MOTHER', 'not_assessed', [12, 14]),
    },
  };
  const htt = {
    locus_id: 'HD_HTT',
    gene: 'HTT',
    display_name: 'HTT',
    disease: 'Huntington disease',
    chr: '4',
    start: 3074876,
    end: 3074933,
    motif: 'CAG',
    warning_min: 27,
    pathogenic_min: 36,
    status: 'normal',
    calls: { PROBAND: call('PROBAND', 'normal', [17, 20]) },
  };

  const renderWith = (loci: unknown[]) => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members, projects: [] } });
      }
      if (url === '/families/F1/repeat-expansions') {
        return Promise.resolve({ data: { samples: members, loci } });
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
  };
  const callsNotAssessed = () =>
    screen
      .getByText('Calls not assessed')
      .closest('.family-workspace-stat')
      ?.querySelector('.family-workspace-stat-value')?.textContent;

  it('labels its calls Not assessed, counts them and keeps the locus in the aberrant-only view', async () => {
    renderWith([uncatalogued, htt]);

    const row = await screen.findByRole('article', { name: /chr2_1000000_1000030_CAG/ });
    expect(row).toHaveClass('family-repeat-card--not_assessed');
    // The locus flag and both members' calls; never Normal.
    expect(within(row).getAllByText('Not assessed')).toHaveLength(3);
    expect(within(row).queryByText('Normal')).not.toBeInTheDocument();
    expect(within(row).getByText('12 / 300')).toBeInTheDocument();
    expect(row.querySelector('.repeat-cutoff-cell')?.textContent?.trim()).toBe('No reference cutoff');
    expect(callsNotAssessed()).toBe('2');

    fireEvent.click(screen.getByLabelText('Aberrant only'));
    expect(screen.getByText('1 of 2 loci')).toBeInTheDocument();
    expect(screen.getByRole('article', { name: /chr2_1000000_1000030_CAG/ })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /HTT/ })).not.toBeInTheDocument();
  });

  it('counts none when every call was assessed', async () => {
    renderWith([htt]);

    expect(await screen.findByRole('article', { name: /HTT/ })).toBeInTheDocument();
    expect(callsNotAssessed()).toBe('0');
    expect(screen.queryByText('Not assessed')).not.toBeInTheDocument();
  });

  // CLIN-36 — at a locus that states a benign range and no threshold (STRchive's POLG: 10), the
  // backend reads a count inside the range Normal and any other Not assessed.
  const polg = (calls: Record<string, ReturnType<typeof call>>, status: string) => ({
    locus_id: 'CPEO_POLG',
    gene: 'POLG',
    display_name: 'POLG',
    disease: 'Progressive external ophthalmoplegia',
    chr: '15',
    start: 89333579,
    end: 89333629,
    motif: 'GCT',
    warning_min: null,
    pathogenic_min: null,
    benign_min: 10,
    benign_max: 10,
    pathogenic_max: null,
    status,
    calls,
  });

  it('names the benign range of a locus without thresholds as its cutoff and counts only the calls outside it', async () => {
    renderWith([
      polg(
        {
          PROBAND: { ...call('PROBAND', 'normal', [10, 10]) },
          MOTHER: {
            ...call('MOTHER', 'not_assessed', [10, 13]),
            alleles: [
              { repeat_count: 10, status: 'normal' },
              { repeat_count: 13, status: 'not_assessed' },
            ],
          },
        },
        'not_assessed',
      ),
      htt,
    ]);

    const row = await screen.findByRole('article', { name: /POLG/ });
    expect(row.querySelector('.repeat-cutoff-cell')?.textContent?.trim()).toBe('normal 10 · no threshold');
    // The locus flag and the mother's call; the proband's 10 / 10 carries no flag.
    expect(within(row).getAllByText('Not assessed')).toHaveLength(2);
    expect(within(row).getByText('10 / 10')).toBeInTheDocument();
    expect(within(row).getByText('10 / 13')).toBeInTheDocument();
    expect(callsNotAssessed()).toBe('1');

    fireEvent.click(screen.getByLabelText('Aberrant only'));
    expect(screen.getByText('1 of 2 loci')).toBeInTheDocument();
    expect(screen.getByRole('article', { name: /POLG/ })).toBeInTheDocument();
  });

  it('leaves a locus whose every call is inside its benign range out of the aberrant-only view', async () => {
    renderWith([polg({ PROBAND: call('PROBAND', 'normal', [10, 10]) }, 'normal'), htt]);

    const row = await screen.findByRole('article', { name: /POLG/ });
    expect(row).not.toHaveClass('family-repeat-table-row--abnormal');
    expect(within(row).queryByText('Not assessed')).not.toBeInTheDocument();
    expect(callsNotAssessed()).toBe('0');

    fireEvent.click(screen.getByLabelText('Aberrant only'));
    expect(screen.getByText('0 of 2 loci')).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /POLG/ })).not.toBeInTheDocument();
  });
});

// TRGT's count need not be the clinical repeat number (FMR1 leaves out the AGG interruptions,
// reading 1 to 3 below). Until QA validates a locus's counting units, a count within 3 repeats
// of a boundary is marked, with the boundary in its tooltip; its status is unchanged (owner's
// decision, 2026-10-14).
describe('FamilyRepeatExpansionsPage — a count near a boundary while the counting units are not validated', () => {
  const members = [
    { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
    { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
  ];
  const FMR1_NOTE =
    'TRGT counts the CGG copies only, not the AGG interruptions: an allele reads 1 to 3 below its clinical repeat number (about 31 triplets read 29).';
  const near = (count: number, boundary: number, status: string) => ({
    repeat_count: count,
    margin: 3,
    boundaries: [{ repeat_count: boundary, status }],
  });
  const call = (
    sample: string,
    status: string,
    alleles: Array<{ repeat_count: number; status: string; count_unit_warning?: unknown }>,
  ) => ({
    sample,
    role: sample === 'PROBAND' ? 'proband' : 'mother',
    affected: sample === 'PROBAND',
    sex: 'female',
    genotype: '1/2',
    allele_count: alleles.length,
    status,
    alleles,
  });
  const fmr1 = (validated: boolean) => ({
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
    premutation_min: 55,
    count_units_validated: validated,
    count_units_note: FMR1_NOTE,
    status: 'intermediate',
    calls: {
      // 53 reads grey zone; a premutation of 55 with two AGG interruptions reads 53.
      PROBAND: call('PROBAND', 'intermediate', [
        { repeat_count: 30, status: 'normal' },
        {
          repeat_count: 53,
          status: 'intermediate',
          ...(validated ? {} : { count_unit_warning: near(53, 55, 'premutation') }),
        },
      ]),
      // 40 is far from every boundary.
      MOTHER: call('MOTHER', 'normal', [
        { repeat_count: 30, status: 'normal' },
        { repeat_count: 40, status: 'normal' },
      ]),
    },
  });
  // A normal HTT allele of 26, one below the grey zone.
  const htt = {
    locus_id: 'HD_HTT',
    gene: 'HTT',
    display_name: 'HTT',
    disease: 'Huntington disease',
    chr: '4',
    start: 3074876,
    end: 3074933,
    motif: 'CAG',
    warning_min: 27,
    pathogenic_min: 36,
    count_units_validated: false,
    status: 'normal',
    calls: {
      PROBAND: call('PROBAND', 'normal', [
        { repeat_count: 17, status: 'normal' },
        { repeat_count: 26, status: 'normal', count_unit_warning: near(26, 27, 'intermediate') },
      ]),
    },
  };
  const atxn1 = {
    locus_id: 'ATXN1',
    gene: 'ATXN1',
    display_name: 'ATXN1',
    disease: 'Spinocerebellar ataxia type 1',
    chr: '6',
    start: 16327635,
    end: 16327722,
    motif: 'CAG',
    warning_min: 36,
    pathogenic_min: 39,
    count_units_validated: false,
    status: 'normal',
    calls: { PROBAND: call('PROBAND', 'normal', [{ repeat_count: 29, status: 'normal' }]) },
  };

  const renderWith = (loci: unknown[]) => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members, projects: [] } });
      }
      if (url === '/families/F1/repeat-expansions') {
        return Promise.resolve({ data: { samples: members, loci } });
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
  };
  const callsNearABoundary = () =>
    screen
      .getByText('Calls near a boundary')
      .closest('.family-workspace-stat')
      ?.querySelector('.family-workspace-stat-value')?.textContent;

  it('marks the FMR1 count of 53 and not the one of 40, with the boundary, the count and why in its tooltip', async () => {
    renderWith([fmr1(false)]);

    const row = await screen.findByRole('article', { name: /FMR1/ });
    const [mark] = within(row).getAllByText('Near a boundary');
    expect(within(row).getAllByText('Near a boundary')).toHaveLength(1);
    // The proband's call carries it; the mother's 30 / 40 does not.
    expect(mark.closest('.variant-card-gtline')).toHaveTextContent('PROBAND');
    // The status is the classification's, unchanged by the mark.
    expect(within(row).getByText('30 / 53').closest('.variant-card-gtline')).toHaveTextContent('Grey zone');
    expect(callsNearABoundary()).toBe('1');

    fireEvent.mouseEnter(mark);
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      `Near a boundary: 53 is within 3 repeats of 55 (Premutation). TRGT's counting units are not yet validated for this locus, so its count may differ from the clinical repeat number by a few repeats. Known: ${FMR1_NOTE}`,
    );
    // Reachable without a mouse: the mark takes focus and is described by its tooltip.
    expect(mark).toHaveAttribute('tabindex', '0');
    expect(mark).toHaveAttribute('aria-describedby', screen.getByRole('tooltip').id);
    fireEvent.mouseLeave(mark);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('marks nothing once QA has validated the locus', async () => {
    renderWith([fmr1(true)]);

    const row = await screen.findByRole('article', { name: /FMR1/ });
    expect(within(row).queryByText('Near a boundary')).not.toBeInTheDocument();
    expect(within(row).getByText('30 / 53').closest('.variant-card-gtline')).toHaveTextContent('Grey zone');
    expect(callsNearABoundary()).toBe('0');
  });

  it('keeps a normal locus with a count near a boundary in the aberrant-only view', async () => {
    renderWith([htt, atxn1]);

    const row = await screen.findByRole('article', { name: /HTT/ });
    expect(row).not.toHaveClass('family-repeat-table-row--abnormal');
    expect(within(row).getByText('Near a boundary')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Aberrant only'));
    expect(screen.getByText('1 of 2 loci')).toBeInTheDocument();
    expect(screen.getByRole('article', { name: /HTT/ })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /ATXN1/ })).not.toBeInTheDocument();
  });

  it('names both boundaries of a count between two', async () => {
    renderWith([
      {
        ...atxn1,
        locus_id: 'OPMD_PABPN1',
        gene: 'PABPN1',
        display_name: 'PABPN1',
        disease: 'Oculopharyngeal muscular dystrophy',
        warning_min: 11,
        pathogenic_min: 12,
        status: 'intermediate',
        calls: {
          PROBAND: call('PROBAND', 'intermediate', [
            {
              repeat_count: 11,
              status: 'intermediate',
              count_unit_warning: {
                repeat_count: 11,
                margin: 3,
                boundaries: [
                  { repeat_count: 10, status: 'normal' },
                  { repeat_count: 12, status: 'pathogenic' },
                ],
              },
            },
          ]),
        },
      },
    ]);

    const row = await screen.findByRole('article', { name: /PABPN1/ });
    fireEvent.mouseEnter(within(row).getByText('Near a boundary'));
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      'Near a boundary: 11 is within 3 repeats of 10 (Normal) and 12 (Pathogenic).',
    );
    expect(screen.getByRole('tooltip')).not.toHaveTextContent('Known:');
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
    // A benign range is not a threshold: a count inside it reads Normal, any other Not
    // assessed (CLIN-26, CLIN-36), so the cutoff names the range and says there is no threshold.
    [
      'locus with a benign range only (POLG)',
      { warning_min: null, pathogenic_min: null, benign_min: 10, benign_max: 10, pathogenic_max: null },
      'normal 10 · no threshold',
    ],
    [
      'locus with a wider benign range only',
      { warning_min: null, pathogenic_min: null, benign_min: 6, benign_max: 16, pathogenic_max: null },
      'normal 6-16 · no threshold',
    ],
    // Half a range is not a stated one: the calls there read Not assessed.
    [
      'locus with only a benign maximum',
      { warning_min: null, pathogenic_min: null, benign_min: null, benign_max: 10, pathogenic_max: null },
      'No reference cutoff',
    ],
    // One threshold is a cutoff: "No reference cutoff" is said only where every call reads
    // Not assessed.
    [
      'expansion locus with a grey zone and no pathogenic threshold',
      { warning_min: 20, pathogenic_min: null, benign_min: null, benign_max: null, pathogenic_max: null },
      'orange ≥ 20',
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
      return Promise.resolve({ data: [] });
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

// The locus column is held narrow so the table fits the content area: a long motif shows
// its first 10 nt and "…", the whole of it in the tooltip and the DOM (display only); a long
// disease name likewise. A locus outside the catalogue names no disease after its ID.
describe('FamilyRepeatExpansionsPage — the narrow locus column', () => {
  const members = [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' }];
  const longMotif = 'AAGGGAAGGGAAGGGACAGG';
  const longDisease = 'Cerebellar ataxia, neuropathy and vestibular areflexia syndrome (CANVAS)';
  const locus = (overrides: Record<string, unknown>) => ({
    locus_id: 'RFC1',
    gene: 'RFC1',
    display_name: 'RFC1',
    disease: longDisease,
    chr: '4',
    start: 39348424,
    end: 39348479,
    motif: longMotif,
    warning_min: null,
    pathogenic_min: 400,
    status: 'normal',
    calls: {
      PROBAND: {
        sample: 'PROBAND',
        role: 'proband',
        genotype: '1/1',
        allele_count: 2,
        status: 'normal',
        alleles: [
          { repeat_count: 11, status: 'normal' },
          { repeat_count: 11, status: 'normal' },
        ],
      },
    },
    ...overrides,
  });
  const renderWith = (loci: unknown[]) => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members, projects: [] } });
      }
      if (url === '/families/F1/repeat-expansions') {
        return Promise.resolve({ data: { samples: members, loci } });
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
  };

  it('cuts a long motif to its first 10 nt with the full motif in the DOM and its tooltip', async () => {
    renderWith([locus({})]);
    const row = await screen.findByRole('article', { name: /RFC1/ });
    expect(within(row).getByText('AAGGGAAGGG…')).toHaveAttribute('aria-hidden', 'true');
    // The full motif is what a screen reader reads, and stays in the DOM.
    const full = within(row).getByText(longMotif);
    expect(full).toHaveClass('sr-only');
    const anchor = full.closest('.truncated-text') as HTMLElement;
    expect(anchor).toHaveAttribute('tabindex', '0');
    fireEvent.focus(anchor);
    expect(screen.getByRole('tooltip')).toHaveTextContent(longMotif);
    expect(anchor).toHaveAttribute('aria-describedby', screen.getByRole('tooltip').id);
    fireEvent.blur(anchor);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('cuts a long disease name after the gene, keeping the full name and its OMIM link', async () => {
    renderWith([locus({})]);
    const row = await screen.findByRole('article', { name: /RFC1/ });
    const heading = row.querySelector('.gene-disease-heading') as HTMLElement;
    expect(within(heading).getByRole('link', { name: 'RFC1' })).toHaveClass('variant-card-gene');
    expect(within(heading).getByText(longDisease)).toHaveClass('sr-only');
    expect(within(heading).getByText(/^Cerebellar ataxia, neuropathy and vestibular…$/)).toBeInTheDocument();
    fireEvent.mouseEnter(heading.querySelector('.truncated-text') as HTMLElement);
    expect(screen.getByRole('tooltip')).toHaveTextContent(longDisease);
    expect(within(heading).getByRole('link', { name: /on omim/i })).toHaveAttribute(
      'href',
      `https://www.omim.org/search?index=entry&search=${encodeURIComponent(longDisease)}`,
    );
  });

  it('shows a short motif whole, without a tooltip', async () => {
    renderWith([locus({ motif: 'CAG', disease: 'Short disease' })]);
    const row = await screen.findByRole('article', { name: /RFC1/ });
    expect(within(row).getByText('CAG')).not.toHaveClass('truncated-text--cut');
    expect(within(row).getByText('Short disease')).not.toHaveClass('truncated-text--cut');
  });

  it('names no disease for a locus outside the catalogue, whose disease is its own ID', async () => {
    const id = 'chr2_1000000_1000030_CAG';
    renderWith([locus({ locus_id: id, gene: id, display_name: id, disease: id })]);
    const row = await screen.findByRole('article', { name: new RegExp(id) });
    expect(row.querySelector('.gene-disease-heading-diseases')).toBeNull();
    // Its "gene" is its ID too: no gene page and no OMIM link.
    expect(within(row).queryByRole('link', { name: /omim/i })).not.toBeInTheDocument();
    expect(within(row).queryByRole('link', { name: id })).not.toBeInTheDocument();
  });
});
