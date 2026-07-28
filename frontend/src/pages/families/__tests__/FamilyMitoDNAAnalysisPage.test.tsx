import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import FamilyMitoDNAAnalysisPage from '../FamilyMitoDNAAnalysisPage';
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

describe('FamilyMitoDNAAnalysisPage', () => {
  it('separates maternal-line samples from father and displays heteroplasmy ratios', async () => {
    const clipboardWrite = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: clipboardWrite },
      configurable: true,
    });
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            pedigree: 'F1 PROBAND DAD MOM 2 2\nF1 MOM 0 0 2 1\nF1 DAD 0 0 1 1',
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
              { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
            ],
            projects: [],
            metadata: { pgt: { inheritance_model: 'mitochondrial' } },
          },
        });
      }

      if (url === '/families/F1/mitochondrial-dna') {
        return Promise.resolve({
          data: {
            heteroplasmy_threshold: 0.02,
            homoplasmy_threshold: 0.95,
            qc_notes: ['One or more samples have mtDNA QC warnings.'],
            samples: [
              {
                sample_id: 'PROBAND',
                role: 'proband',
                affected: true,
                sex: 'female',
                haplogroup: 'H1',
                coverage: { mean_depth: 420.4, min_depth: 380, max_depth: 470, breadth: 0.98, source: 'coverage', regions: 10 },
                qc: { status: 'pass', notes: [], contamination: 0.002, mean_depth: 420.4, min_mean_depth: 380 },
              },
              {
                sample_id: 'MOM',
                role: 'mother',
                affected: false,
                sex: 'female',
                haplogroup: 'H1',
                coverage: { mean_depth: 390, min_depth: 340, max_depth: 420, breadth: 0.97, source: 'coverage', regions: 10 },
                qc: { status: 'warning', notes: ['Contamination estimate is elevated.'], contamination: 0.012, mean_depth: 390, min_mean_depth: 340 },
              },
              {
                sample_id: 'DAD',
                role: 'father',
                affected: false,
                sex: 'male',
                haplogroup: 'J1',
                coverage: { mean_depth: 300, min_depth: 250, max_depth: 330, breadth: 0.96, source: 'coverage', regions: 10 },
                qc: { status: 'pass', notes: [], contamination: 0.001, mean_depth: 300, min_mean_depth: 250 },
              },
            ],
            variants: [
              {
                variant_id: 'm.3243A>G',
                position: 3243,
                ref: 'A',
                alt: 'G',
                label: 'm.3243A>G',
                type: 'SNV',
                rsid: 'rs199474657',
                maternal_transmission: 'maternal_shared',
                annotation: {
                  region: 'MT-TL1',
                  gene: 'MT-TL1',
                  consequence: 'tRNA_variant',
                  consequence_terms: ['trna_variant'],
                  gnomad_af: null,
                  category: 'tRNA',
                  clinical_significance: 'pathogenic',
                  disorders: ['MELAS syndrome'],
                  polymorphism_notes: [],
                  mitomap_query: 'm.3243A>G',
                  mitomap_url: 'https://www.mitomap.org/cgi-bin/search_allele?variant=3243A%3EG',
                  source_keys: ['mitomap_disease'],
                },
                calls: {
                  PROBAND: {
                    sample: 'PROBAND',
                    role: 'proband',
                    affected: true,
                    sex: 'female',
                    genotype: '0/1',
                    allele_fraction: 0.46,
                    depth: 420,
                    alt_depth: 193,
                    zygosity: 'heteroplasmic',
                    display: '46.0%',
                  },
                  MOM: {
                    sample: 'MOM',
                    role: 'mother',
                    affected: false,
                    sex: 'female',
                    genotype: '0/1',
                    allele_fraction: 0.88,
                    depth: 390,
                    alt_depth: 343,
                    zygosity: 'heteroplasmic',
                    display: '88.0%',
                  },
                  DAD: {
                    sample: 'DAD',
                    role: 'father',
                    affected: false,
                    sex: 'male',
                    genotype: '0/0',
                    allele_fraction: 0,
                    depth: 310,
                    alt_depth: 0,
                    zygosity: 'reference',
                    display: '0.0%',
                  },
                },
              },
              {
                variant_id: 'm.73A>G',
                position: 73,
                ref: 'A',
                alt: 'G',
                label: 'm.73A>G',
                type: 'SNV',
                maternal_transmission: 'father_only',
                annotation: {
                  region: 'control region',
                  gene: 'MT-CR',
                  consequence: null,
                  consequence_terms: [],
                  gnomad_af: 0.85,
                  category: 'control',
                  clinical_significance: 'polymorphism',
                  disorders: [],
                  polymorphism_notes: ['Common haplogroup marker'],
                  mitomap_query: 'm.73A>G',
                  mitomap_url: 'https://www.mitomap.org/cgi-bin/search_allele?variant=73A%3EG',
                  source_keys: ['frequency'],
                },
                calls: {
                  DAD: {
                    sample: 'DAD',
                    role: 'father',
                    affected: false,
                    sex: 'male',
                    genotype: '1/1',
                    allele_fraction: 0.99,
                    depth: 305,
                    alt_depth: 302,
                    zygosity: 'homoplasmic',
                    display: '99.0%',
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
        <MemoryRouter initialEntries={['/families/F1/mitochondrial-dna']}>
          <Routes>
            <Route
              path="/families/:familyId/mitochondrial-dna"
              element={<FamilyMitoDNAAnalysisPage />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /mitochondrial variants/i })).toBeInTheDocument();
    });

    expect(screen.getByText('Maternal line')).toBeInTheDocument();
    expect(screen.getAllByText('Father').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('H1').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('J1')).toBeInTheDocument();
    expect(screen.getByText('Contamination estimate is elevated.')).toBeInTheDocument();

    const pathogenicRow = screen.getByRole('row', { name: /m\.3243A>G/i });
    const rowScope = within(pathogenicRow as HTMLElement);
    expect(rowScope.getByText('MELAS syndrome')).toBeInTheDocument();
    expect(rowScope.getByText('Maternal transmission')).toBeInTheDocument();
    expect(rowScope.getByText('46.0%')).toBeInTheDocument();
    expect(rowScope.getByText('88.0%')).toBeInTheDocument();
    expect(rowScope.getByText('0.0%')).toBeInTheDocument();
    expect(rowScope.getAllByText('Heteroplasmic').length).toBeGreaterThanOrEqual(2);

    fireEvent.click(rowScope.getByRole('button', { name: /open mitomap for m\.3243A>G/i }));

    await waitFor(() => expect(clipboardWrite).toHaveBeenCalledWith('m.3243A>G'));
    expect(openSpy).toHaveBeenCalledWith(
      'https://www.mitomap.org/cgi-bin/search_allele?variant=3243A%3EG',
      '_blank',
      'noopener,noreferrer',
    );
    expect(rowScope.getByText('Copied')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^clinical$/i }));
    expect(screen.getByText(/1 of 2 variants/)).toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /m\.73A>G/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /maternal review/i }));
    expect(screen.getByText(/1 of 2 variants/)).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /m\.73A>G/i })).toBeInTheDocument();
  });

  it('filters on gnomAD frequency and consequence, and opens the ACMG modal', async () => {
    const sample = (sample_id: string, role: string) => ({
      sample_id,
      role,
      affected: role === 'proband',
      sex: role === 'father' ? 'male' : 'female',
      haplogroup: 'H1',
      coverage: { mean_depth: 400, min_depth: 350, max_depth: 450, breadth: 0.98, source: 'coverage', regions: 10 },
      qc: { status: 'pass', notes: [], contamination: 0.001, mean_depth: 400, min_mean_depth: 350 },
    });
    const variant = (
      label: string,
      position: number,
      gnomadAf: number | null,
      consequenceTerms: string[],
    ) => ({
      variant_id: label,
      position,
      ref: 'A',
      alt: 'G',
      label,
      type: 'SNV',
      maternal_transmission: 'maternal_shared',
      annotation: {
        region: 'MT-ATP6',
        gene: 'MT-ATP6',
        consequence: consequenceTerms[0] ?? null,
        consequence_terms: consequenceTerms,
        gnomad_af: gnomadAf,
        category: 'protein coding',
        clinical_significance: 'unknown',
        disorders: [],
        polymorphism_notes: [],
        mitomap_query: label,
        mitomap_url: `https://www.mitomap.org/cgi-bin/search_allele?variant=${position}A%3EG`,
        source_keys: [],
      },
      calls: {
        PROBAND: {
          sample: 'PROBAND',
          role: 'proband',
          affected: true,
          sex: 'female',
          genotype: '0/1',
          allele_fraction: 0.4,
          depth: 400,
          alt_depth: 160,
          zygosity: 'heteroplasmic',
          display: '40.0%',
        },
      },
    });

    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            pedigree: '',
            members: [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' }],
            projects: [],
            metadata: {},
          },
        });
      }
      if (url === '/families/F1/hpo') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/mitochondrial-dna') {
        return Promise.resolve({
          data: {
            heteroplasmy_threshold: 0.02,
            homoplasmy_threshold: 0.95,
            qc_notes: [],
            samples: [sample('PROBAND', 'proband')],
            variants: [
              variant('m.3243A>G', 3243, null, ['missense_variant']),
              variant('m.73A>G', 73, 0.85, []),
              variant('m.8860A>G', 8860, 0.97, ['synonymous_variant']),
            ],
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/mitochondrial-dna']}>
          <Routes>
            <Route path="/families/:familyId/mitochondrial-dna" element={<FamilyMitoDNAAnalysisPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText(/3 of 3 variants/)).toBeInTheDocument();
    });

    // gnomAD frequency: ≤5% removes both common variants, keeps the rare one
    // (no annotated frequency is treated as not-common, so it stays).
    fireEvent.change(screen.getByLabelText('gnomAD frequency'), { target: { value: '0.05' } });
    expect(screen.getByText(/1 of 3 variants/)).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /m\.3243A>G/i })).toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /m\.73A>G/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /m\.8860A>G/i })).not.toBeInTheDocument();

    // Back to any frequency, then hide synonymous variants.
    fireEvent.change(screen.getByLabelText('gnomAD frequency'), { target: { value: '' } });
    expect(screen.getByText(/3 of 3 variants/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: /hide synonymous/i }));
    expect(screen.getByText(/2 of 3 variants/)).toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /m\.8860A>G/i })).not.toBeInTheDocument();

    // ACMG: classify button opens the shared classification modal.
    const row = screen.getByRole('row', { name: /m\.3243A>G/i });
    fireEvent.click(within(row as HTMLElement).getByRole('button', { name: /classify m\.3243A>G with acmg/i }));
    await waitFor(() => {
      const dialog = screen.getByRole('dialog');
      expect(within(dialog).getByText('ACMG classification')).toBeInTheDocument();
    });
  });
});
