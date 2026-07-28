import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import FamilyNiptReportPage from '../FamilyNiptReportPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

vi.mock('../../../lib/reference', () => ({
  useFamilyReference: () => ({
    speciesName: 'Homo sapiens',
    assemblyName: 'GRCh38',
    assemblyVersion: 'p14',
    projectId: undefined,
    isLoading: false,
  }),
  formatResolvedReferenceLabel: ({ assemblyName }: { assemblyName?: string }) =>
    assemblyName || 'Not linked',
}));

const niptVariant = (
  id: string,
  gene: string,
  category: number,
  categoryLabel: string,
) => ({
  _id: id,
  chr: '1',
  start: 100,
  end: 100,
  type: 'SNV',
  ref: 'A',
  alt: 'G',
  gene,
  genotypes: [],
  nipt: {
    category,
    category_label: categoryLabel,
    maternal_state: 'het',
    fetal_inheritance: 'transmitted',
    expected_vaf: 0.05,
    observed_vaf: 0.05,
    confidence: 0.95,
    flags: [],
  },
});

const renderPage = () => {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/families/NIPT001/nipt/report?panel_id=panel-1']}>
        <Routes>
          <Route path="/families/:familyId/nipt/report" element={<FamilyNiptReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('FamilyNiptReportPage', () => {
  it('renders fetal fraction, coverage QC and inheritance-grouped candidates', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/NIPT001') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            members: [{ sample_id: 'CFDNA', role: 'proband', active: true }],
            metadata: { analysis_type: 'monogenic_nipt' },
          },
        });
      }
      if (url === '/families/NIPT001/nipt/summary') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            fetal_fraction: {
              ff: 0.12,
              ci_low: 0.1,
              ci_high: 0.14,
              n_sites: 42,
              method: 'category7_pooled',
              low_confidence: false,
              disagreement: false,
            },
            category_counts: {},
            filter_counts: {},
          },
        });
      }
      if (url === '/families/NIPT001/nipt/coverage') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            overall_median_on_target: 140,
            target_region_count: 3,
            per_region: [],
            min_depth: 20,
            min_covered_fraction: 0.9,
            low_coverage_regions: [
              { label: 'ARID1B', chr: '6', median_coverage: 8, covered_fraction: 1, reason: 'low_depth' },
            ],
          },
        });
      }
      if (url === '/families/NIPT001/nipt/variants') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            total: 4,
            variants: [
              niptVariant('v1', 'ARID1B', 1, 'De novo in fetus'),
              niptVariant('v2', 'SCN1A', 7, 'Paternal, transmitted'),
              niptVariant('v3', 'CFTR', 4, 'Maternal het → hom fetus'),
              niptVariant('v4', 'NOISE1', 2, 'Maternal het, not inherited'),
            ],
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderPage();

    // Header + fetal fraction with CI.
    expect(
      await screen.findByRole('heading', { name: /family NIPT001/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/12\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/95% CI 10\.0%–14\.0%/)).toBeInTheDocument();

    // Coverage QC flags the low-depth panel gene.
    expect(screen.getByText('Coverage QC')).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 panel genes were not adequately interrogated/)).toBeInTheDocument();
    expect(screen.getByText(/8x median/)).toBeInTheDocument();

    // Candidates are grouped by inheritance.
    expect(screen.getByText(/De novo in fetus \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Dominant, transmitted \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Recessive \/ biallelic risk \(1\)/)).toBeInTheDocument();
    // Category 2 falls into the "Other" catch-all.
    expect(screen.getByText(/Other categories \(1\)/)).toBeInTheDocument();
    expect(screen.getByText('SCN1A')).toBeInTheDocument();
    expect(screen.getByText('CFTR')).toBeInTheDocument();
  });

  it('shows a not-configured message for a non-NIPT family', async () => {
    apiMock.get.mockResolvedValue({
      data: { family_id: 'FAM001', members: [], metadata: {} },
    });

    renderPage();

    expect(
      await screen.findByRole('heading', { name: /not a monogenic nipt family/i }),
    ).toBeInTheDocument();
  });
});
