import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import FamilyMitoDNAAnalysisPage from '../FamilyMitoDNAAnalysisPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

// The hook is stubbed; the assembly-name rules the ACMG modal's links read stay the real ones.
vi.mock('../../../lib/reference', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/reference')>()),
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
                qc: { status: 'warn', notes: ['Contamination estimate is elevated.'], contamination: 0.012, mean_depth: 390, min_mean_depth: 340 },
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

      // The review tags the cards offer.
      if (url.endsWith('/small-variant-tags')) return Promise.resolve({ data: [] });
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
    // The chip shows the shared human label, not the raw wire value the backend sends.
    expect(screen.getByText('Warning')).toBeInTheDocument();
    expect(screen.queryByText('warn')).not.toBeInTheDocument();
    expect(screen.getAllByText('Pass').length).toBeGreaterThanOrEqual(2);

    // The family summary: one compact row per sample, the maternal line and the father as
    // group rows.
    const summary = document.querySelector('.family-mtdna-summary-table') as HTMLElement;
    expect(Array.from(summary.querySelectorAll('tbody tr')).map((row) => row.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
      'Maternal line 1 mother · 1 child sample',
      '♀PROBANDprobandH1420.4x98%0.20%Pass',
      '♀MOMmotherH1390.0x97%1.20%WarningContamination estimate is elevated.',
      'Father',
      '♂DADfatherJ1300.0x96%0.10%Pass',
    ]);

    const pathogenicRow = screen.getByRole('article', { name: /m\.3243A>G/i });
    const rowScope = within(pathogenicRow as HTMLElement);
    expect(rowScope.getByText('MELAS syndrome')).toBeInTheDocument();
    expect(rowScope.getByText('Maternal transmission')).toBeInTheDocument();
    expect(rowScope.getByText('46.0%')).toBeInTheDocument();
    expect(rowScope.getByText('88.0%')).toBeInTheDocument();
    expect(rowScope.getByText('0.0%')).toBeInTheDocument();
    expect(rowScope.getAllByText('Heteroplasmic').length).toBeGreaterThanOrEqual(2);

    // The anatomy of every variant card: the gene (to its gene page) and its disease (to
    // OMIM) heading the card, the variant in the first column, the family's calls in the
    // genotype grid of the second, the heteroplasmy as each member's call.
    const heading = pathogenicRow.querySelector('.gene-disease-heading') as HTMLElement;
    expect(heading).toHaveTextContent('MT-TL1—MELAS syndrome');
    expect(within(heading).getByRole('link', { name: 'MT-TL1' })).toHaveAttribute(
      'href',
      '/genes?gene=MT-TL1&family_id=F1',
    );
    expect(within(heading).getByRole('link', { name: /melas syndrome on omim/i })).toHaveAttribute(
      'href',
      'https://www.omim.org/search?index=entry&search=MELAS%20syndrome',
    );
    const [variantPart, samplePart] = Array.from(
      pathogenicRow.querySelectorAll<HTMLElement>('.variant-card-col'),
    );
    expect(variantPart.querySelector('.variant-card-col-title')).toHaveTextContent('Variant');
    expect(samplePart.querySelector('.variant-card-col-title')).toHaveTextContent('Genotypes');
    expect(within(samplePart).getByText('46.0%')).toHaveClass('variant-card-gtline-gt');
    expect(within(variantPart).queryByText('46.0%')).not.toBeInTheDocument();
    const callRows = samplePart.querySelectorAll('.variant-card-gtline');
    // The maternal line (proband first), then the father, apart.
    expect([...callRows].map((callRow) => callRow.querySelector('.variant-card-gtline-sample')?.textContent)).toEqual([
      '♀PROBAND',
      '♀MOM',
      '♂DAD',
    ]);
    expect(
      Array.from(samplePart.querySelectorAll('.variant-card-gtgroup')).map((group) => group.textContent),
    ).toEqual(['Mother(s) and children', 'Father']);
    const footer = pathogenicRow.querySelector('.variant-card-footer') as HTMLElement;
    expect(
      Array.from(footer.querySelectorAll('.variant-card-nav > *')).map((link) => link.textContent),
    ).toEqual(['IGV', 'Chromosome view', 'OMIM', 'MITOMAP']);

    // IGV on the variant, on chrM — the name IGV's genomes give the mitochondrion (MT is an
    // alias of it, chrMT is not) — returning here, in a new tab like every genome link.
    const igvLink = within(footer).getByRole('link', {
      name: /open igv at chrM:3243-3243.*opens in a new tab/i,
    });
    expect(igvLink).toHaveAttribute(
      'href',
      '/families/F1/igv?locus=chrM%3A3243-3243&back_path=%2Ffamilies%2FF1%2Fmitochondrial-dna',
    );
    expect(igvLink).toHaveAttribute('target', '_blank');
    expect(within(footer).getByRole('link', { name: /open chromosome view at MT:3,243/i })).toHaveAttribute(
      'href',
      '/families/F1/chromosome/MT?start=3143&end=3343',
    );

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
    expect(screen.queryByRole('article', { name: /m\.73A>G/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /maternal review/i }));
    expect(screen.getByText(/1 of 2 variants/)).toBeInTheDocument();
    expect(screen.getByRole('article', { name: /m\.73A>G/i })).toBeInTheDocument();
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
            structural_variants: [
              {
                variant_id: 'SV-M-8470-13447-DEL',
                sv_type: 'DEL',
                start: 8470,
                end: 13447,
                length: 4977,
                genes: ['MT-ATP8', 'MT-ND5'],
                source: 'mito_sv',
                filters: [],
                calls: {
                  PROBAND: { sample: 'PROBAND', role: 'proband', genotype: '0/1', heteroplasmy: 0.35, read_support: 140 },
                },
              },
            ],
          },
        });
      }
      // The review tags the cards offer.
      if (url.endsWith('/small-variant-tags')) return Promise.resolve({ data: [] });
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

    // The chrM deletion and its heteroplasmy, which used to be dropped at import.
    expect(screen.getByRole('heading', { name: 'Mitochondrial structural variants' })).toBeInTheDocument();
    const svRow = screen.getByRole('row', { name: /DEL m\.8,470-13,447/ });
    expect(within(svRow).getByText('4,977 bp')).toBeInTheDocument();
    expect(within(svRow).getByText('MT-ATP8, MT-ND5')).toBeInTheDocument();
    expect(within(svRow).getByText('PROBAND: 35.0% (140 reads)')).toBeInTheDocument();

    // gnomAD frequency: ≤5% removes both common variants, keeps the rare one
    // (no annotated frequency is treated as not-common, so it stays).
    fireEvent.change(screen.getByLabelText('gnomAD frequency'), { target: { value: '0.05' } });
    expect(screen.getByText(/1 of 3 variants/)).toBeInTheDocument();
    expect(screen.getByRole('article', { name: /m\.3243A>G/i })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /m\.73A>G/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /m\.8860A>G/i })).not.toBeInTheDocument();

    // Back to any frequency, then hide synonymous variants.
    fireEvent.change(screen.getByLabelText('gnomAD frequency'), { target: { value: '' } });
    expect(screen.getByText(/3 of 3 variants/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: /hide synonymous/i }));
    expect(screen.getByText(/2 of 3 variants/)).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /m\.8860A>G/i })).not.toBeInTheDocument();

    // ACMG: classify button opens the shared classification modal.
    const row = screen.getByRole('article', { name: /m\.3243A>G/i });
    fireEvent.click(within(row as HTMLElement).getByRole('button', { name: /classify m\.3243A>G with acmg/i }));
    await waitFor(() => {
      const dialog = screen.getByRole('dialog');
      expect(within(dialog).getByText('ACMG classification')).toBeInTheDocument();
    });
  });

  it('labels a variant with no mother to compare as not assessed and keeps it out of maternal review', async () => {
    const variant = (label: string, position: number, transmission: string) => ({
      variant_id: label,
      position,
      ref: 'A',
      alt: 'G',
      label,
      type: 'SNV',
      maternal_transmission: transmission,
      annotation: {
        region: 'MT-ND1',
        gene: 'MT-ND1',
        consequence: 'missense_variant',
        consequence_terms: ['missense_variant'],
        gnomad_af: null,
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
          sex: 'male',
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
            members: [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' }],
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
            samples: [
              {
                sample_id: 'PROBAND',
                role: 'proband',
                affected: true,
                sex: 'male',
                haplogroup: 'H1',
                coverage: { mean_depth: 400, min_depth: 350, max_depth: 450, breadth: 0.98, source: 'coverage', regions: 10 },
                qc: { status: 'pass', notes: [], contamination: 0.001, mean_depth: 400, min_mean_depth: 350 },
              },
            ],
            variants: [
              variant('m.3460G>A', 3460, 'mother_not_assessed'),
              variant('m.4216T>C', 4216, 'maternal_not_observed'),
            ],
          },
        });
      }
      // The review tags the cards offer.
      if (url.endsWith('/small-variant-tags')) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: {} });
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/mitochondrial-dna']}>
          <Routes>
            <Route path="/families/:familyId/mitochondrial-dna" element={<FamilyMitoDNAAnalysisPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const row = await screen.findByRole('article', { name: /m\.3460G>A/i });
    expect(within(row).getByText('Mother not assessed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Maternal review' }));
    expect(screen.getByText(/1 of 2 variants/)).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /m\.3460G>A/i })).not.toBeInTheDocument();
    expect(screen.getByRole('article', { name: /m\.4216T>C/i })).toBeInTheDocument();
  });
});

// #610 — any failure read "Family not found", a server error included.
describe('FamilyMitoDNAAnalysisPage — failed requests (#610)', () => {
  const serverError = () =>
    Promise.reject(
      Object.assign(new Error('Request failed with status code 500'), { response: { status: 500 } }),
    );
  const renderPage = () =>
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/mitochondrial-dna']}>
          <Routes>
            <Route path="/families/:familyId/mitochondrial-dna" element={<FamilyMitoDNAAnalysisPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

  it('says the mtDNA analysis could not be loaded, not that the family was not found', async () => {
    let failedRequests = 0;
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1/mitochondrial-dna') {
        failedRequests += 1;
        return serverError();
      }
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      // The review tags the cards offer.
      if (url.endsWith('/small-variant-tags')) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: {} });
    });
    renderPage();

    expect(await screen.findByRole('heading', { name: 'mtDNA analysis could not be loaded' })).toBeInTheDocument();
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

describe('mtDNA IGV link', () => {
  it('spans a deletion from its position over its reference allele, on chrM, with the project', async () => {
    const { buildMitoIgvHref, getMitoIgvLocus } = await import('../FamilyMitoDNAAnalysisPage');
    expect(getMitoIgvLocus({ position: 8270, ref: 'CACCCCCTCT' })).toBe('chrM:8270-8279');
    // The IGV page opens the genome of this project's assembly, read from project_id.
    expect(buildMitoIgvHref('F 1', { position: 3243, ref: 'A' }, 'P1')).toBe(
      '/families/F%201/igv?locus=chrM%3A3243-3243&project_id=P1' +
        '&back_path=%2Ffamilies%2FF%25201%2Fmitochondrial-dna',
    );
  });
});

// An mtDNA variant's review is the shared small-variant review: the cards carry the same
// quick tags (Review, Exclude, Report), Tags & notes, and show the review's tags and note.
describe('mtDNA variant review', () => {
  const tagDefinitions = [
    {
      key: 'segregates',
      label: 'Segregates',
      description: null,
      group: 'custom',
      color: '#5b6b79',
      sort_order: 500,
      scope: 'global',
      project_id: null,
      shared_project_ids: [],
      is_custom: true,
      is_active: true,
    },
  ];
  const mitoVariant = (variantId: string, review: Record<string, unknown> | null) => ({
    variant_id: variantId,
    position: 3243,
    ref: 'A',
    alt: 'G',
    label: variantId,
    type: 'SNV',
    maternal_transmission: 'maternal_shared',
    review,
    annotation: {
      region: 'MT-TL1',
      gene: 'MT-TL1',
      consequence: null,
      consequence_terms: [],
      gnomad_af: null,
      category: 'tRNA',
      clinical_significance: 'pathogenic',
      disorders: [],
      polymorphism_notes: [],
      mitomap_query: variantId,
      mitomap_url: 'https://www.mitomap.org/',
      source_keys: [],
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
    },
  });
  const renderWith = (variants: unknown[], readOnly = false) => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            pedigree: '',
            members: [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' }],
            projects: [],
            metadata: {},
            read_only: readOnly,
          },
        });
      }
      if (url === '/families/F1/mitochondrial-dna') {
        return Promise.resolve({
          data: {
            heteroplasmy_threshold: 0.02,
            homoplasmy_threshold: 0.95,
            qc_notes: [],
            samples: [
              {
                sample_id: 'PROBAND',
                role: 'proband',
                affected: true,
                sex: 'female',
                haplogroup: 'H1',
                coverage: { mean_depth: 420, min_depth: 380, max_depth: 470, breadth: 0.98, source: 'coverage', regions: 10 },
                qc: { status: 'pass', notes: [], contamination: 0.002, mean_depth: 420, min_mean_depth: 380 },
              },
            ],
            variants,
            structural_variants: [],
          },
        });
      }
      if (url.endsWith('/small-variant-tags')) return Promise.resolve({ data: tagDefinitions });
      return Promise.resolve({ data: {} });
    });
    apiMock.put.mockReset();
    apiMock.put.mockResolvedValue({ data: {} });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/mitochondrial-dna']}>
          <Routes>
            <Route path="/families/:familyId/mitochondrial-dna" element={<FamilyMitoDNAAnalysisPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  };

  it('toggles the quick tags on the shared review, keeping its tags and note', async () => {
    renderWith([
      mitoVariant('m.3243A>G', {
        tags: ['review', 'segregates'],
        note: 'Checked in IGV',
        updated_at: '2026-10-01T10:00:00Z',
      }),
    ]);
    const card = await screen.findByRole('article', { name: /m\.3243A>G/ });
    // The review's own tags and note on the card; the quick tags as pressed toggles.
    expect(within(card).getByText('Segregates')).toBeInTheDocument();
    expect(within(card).getByText('Checked in IGV')).toHaveClass('variant-card-review-note');
    const footer = card.querySelector('.variant-card-footer .variant-card-quick') as HTMLElement;
    expect(within(footer).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Review',
      'Exclude',
      'Report',
      'Tags & notes',
      'ACMG classify',
    ]);
    expect(within(footer).getByRole('button', { name: 'Review' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(footer).getByRole('button', { name: 'Report' })).toHaveAttribute('aria-pressed', 'false');

    // Report puts the variant on the report: the same save as on the small-variant cards.
    fireEvent.click(within(footer).getByRole('button', { name: 'Report' }));
    await waitFor(() => expect(apiMock.put).toHaveBeenCalledTimes(1));
    expect(apiMock.put).toHaveBeenCalledWith('/families/F1/small-variants/m.3243A%3EG/review', {
      tags: ['report', 'review', 'segregates'],
      note: 'Checked in IGV',
      classification: undefined,
      expected_updated_at: '2026-10-01T10:00:00Z',
    });
  });

  it('opens Tags & notes on the variant', async () => {
    renderWith([mitoVariant('m.3243A>G', null)]);
    const card = await screen.findByRole('article', { name: /m\.3243A>G/ });
    fireEvent.click(within(card).getByRole('button', { name: 'Tags & notes' }));
    const dialog = await screen.findByRole('dialog', { name: 'MT-TL1' });
    expect(within(dialog).getByText('Segregates')).toBeInTheDocument();
  });

  it('folds an excluded variant to its heading, unfolds it on request and undoes the exclusion', async () => {
    renderWith([mitoVariant('m.3243A>G', { tags: ['excluded'], updated_at: '2026-10-01T10:00:00Z' })]);
    const card = await screen.findByRole('article', { name: /m\.3243A>G/ });
    // Folded: the heading and the bar, nothing else; still in the list.
    expect(card).toHaveClass('variant-card--excluded');
    expect(card.querySelector('.gene-disease-heading')).toHaveTextContent('MT-TL1');
    expect(within(card).getByText('Excluded')).toBeInTheDocument();
    expect(card.querySelector('.variant-card-cols')).toBeNull();
    expect(card.querySelector('.variant-card-footer')).toBeNull();

    const show = within(card).getByRole('button', { name: /show the excluded card of m\.3243A>G/i });
    expect(show).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(show);
    expect(card.querySelector('.variant-card-cols')).not.toBeNull();
    expect(within(card).getByRole('button', { name: 'Exclude' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(card).getByRole('button', { name: /hide the excluded card/i })).toHaveAttribute('aria-expanded', 'true');

    // Undo exclude takes the tag off the shared review.
    fireEvent.click(within(card).getByRole('button', { name: /undo the exclusion of m\.3243A>G/i }));
    await waitFor(() => expect(apiMock.put).toHaveBeenCalledTimes(1));
    expect(apiMock.put.mock.calls[0][1]).toMatchObject({ tags: [] });
  });

  it('lets a reader see the review but not change it', async () => {
    renderWith([mitoVariant('m.3243A>G', { tags: ['report'], updated_at: '2026-10-01T10:00:00Z' })], true);
    const card = await screen.findByRole('article', { name: /m\.3243A>G/ });
    for (const name of ['Review', 'Exclude', 'Report']) {
      expect(within(card).getByRole('button', { name })).toBeDisabled();
    }
    expect(within(card).getByRole('button', { name: 'Report' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('says so when a quick tag cannot be saved', async () => {
    renderWith([mitoVariant('m.3243A>G', null)]);
    apiMock.put.mockRejectedValue(new Error('Network down'));
    const card = await screen.findByRole('article', { name: /m\.3243A>G/ });
    fireEvent.click(within(card).getByRole('button', { name: 'Review' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/network down|unable to save the variant review/i);
  });
});
