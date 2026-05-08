import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import FamilyRepeatExpansionsPage from '../FamilyRepeatExpansionsPage';
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
    expect(
      rowScope.getByRole('link', { name: /chromosome view ±1 mb/i }),
    ).toHaveAttribute(
      'href',
      '/families/F1/chromosome/4?start=2074876&end=4074933',
    );

    const lastCell = (row as HTMLElement).querySelectorAll('td').item(3);
    expect(lastCell.textContent?.replace(/\s+/g, ' ').trim()).toBe('orange ≥ 36 · red ≥ 40');

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
