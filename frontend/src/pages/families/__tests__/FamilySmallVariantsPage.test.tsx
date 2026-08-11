import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FamilySmallVariantsPage from '../FamilySmallVariantsPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

describe('FamilySmallVariantsPage', () => {
  beforeEach(() => {
    localStorage.clear();
    apiMock.get.mockReset();
    apiMock.post.mockReset();
    apiMock.put.mockReset();
    apiMock.delete.mockReset();

    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            members: [],
            projects: [],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({
          data: [
            {
              _id: 'preset-1',
              family_id: 'F1',
              scope: 'global',
              owner: 'reviewer',
              name: 'Dominant shortlist',
              description: 'Saved family search',
              filters: { impact: 'HIGH' },
              sample_filters: {},
              sample_templates: {},
              created_at: '2026-04-14T10:00:00Z',
              updated_at: '2026-04-14T10:00:00Z',
            },
          ],
        });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({
          data: [
            {
              key: 'review',
              label: 'Review',
              group: 'collaboration',
              color: '#2563eb',
              sort_order: 10,
              scope: 'system',
              is_custom: false,
            },
            {
              key: 'excluded',
              label: 'Excluded',
              group: 'collaboration',
              color: '#64748b',
              sort_order: 20,
              scope: 'system',
              is_custom: false,
            },
            {
              key: 'needs_rna',
              label: 'Needs RNA',
              group: 'custom',
              color: '#c58a18',
              sort_order: 500,
              scope: 'global',
              is_custom: true,
            },
          ],
        });
      }
      if (url.startsWith('/families/F1/small-variants/v1/compound-het-candidates?limit=100')) {
        return Promise.resolve({
          data: {
            variants: [
              {
                _id: 'partner-1',
                chr: '2',
                start: 180,
                end: 180,
                type: 'SNV',
                gene: 'BRCA2',
                ref: 'G',
                alt: 'A',
                impact: 'HIGH',
                effect: 'missense_variant',
                hgvsc: 'c.5408G>A',
                genotypes: [],
              },
            ],
            total: 1,
          },
        });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=100')) {
        return Promise.resolve({
          data: {
            variants: [
              {
                _id: 'v1',
                chr: '2',
                start: 120,
                end: 120,
                type: 'SNV',
                gene: 'BRCA2',
                ref: 'A',
                alt: 'G',
                impact: 'LOW',
                effect: 'synonymous_variant',
                clinvar: 'Pathogenic',
                genotypes: [],
                review: {
                  variant_id: 'v1',
                  classification: 'VUS - class 3',
                  tags: ['review', 'acmg_class_3'],
                  tag_metadata: {
                    review: {
                      updated_by: 'reviewer',
                      updated_at: '2026-04-14T10:00:00Z',
                    },
                    acmg_class_3: {
                      updated_by: 'reviewer',
                      updated_at: '2026-04-14T10:00:00Z',
                    },
                  },
                  note: 'Worth follow-up',
                  updated_by: 'reviewer',
                  updated_at: '2026-04-14T10:00:00Z',
                },
              },
              {
                _id: 'v2',
                chr: '1',
                start: 10,
                end: 10,
                type: 'SNV',
                gene: 'ALPHA',
                ref: 'C',
                alt: 'T',
                impact: 'MODERATE',
                effect: 'missense_variant',
                clinvar: 'Likely benign',
                genotypes: [],
              },
              {
                _id: 'v3',
                chr: '1',
                start: 80,
                end: 80,
                type: 'SNV',
                gene: 'TP53',
                ref: 'G',
                alt: 'A',
                impact: 'HIGH',
                effect: 'stop_gained',
                genotypes: [],
              },
            ],
            total: 31,
          },
        });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=1')) {
        return Promise.resolve({ data: { variants: [], total: 31 } });
      }
      return Promise.resolve({ data: {} });
    });

    apiMock.post.mockImplementation((url: string, payload?: unknown) =>
      Promise.resolve({
        data:
          url === '/families/F1/small-variant-filter-presets'
            ? {
                _id: 'preset-created',
                family_id: 'F1',
                scope: 'global',
                owner: 'reviewer',
                name: 'Saved',
                description: null,
                filters: (payload as { filters?: unknown })?.filters || {},
                sample_filters: (payload as { sample_filters?: unknown })?.sample_filters || {},
                sample_templates: (payload as { sample_templates?: unknown })?.sample_templates || {},
                created_at: '2026-04-14T10:00:00Z',
                updated_at: '2026-04-14T10:00:00Z',
              }
            : {},
      }),
    );
    apiMock.put.mockResolvedValue({ data: {} });
    apiMock.delete.mockResolvedValue({ data: {} });
  });

  it('renders small-variant search controls and result display toggles', async () => {
    localStorage.setItem('role', 'admin');
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: /preset or saved search/i })).toBeInTheDocument();
    });

    expect(screen.getByRole('option', { name: 'Dominant shortlist' })).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: /Expanded carrier screening/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save current/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /save current/i }));
    expect(screen.getByPlaceholderText('Preset name')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Locations'));
    expect(screen.getByPlaceholderText(/Gene list:/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/^Intervals:/i)).toBeInTheDocument();
    expect(screen.getByText('Any gene panel')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Annotations'));
    expect(screen.getByPlaceholderText('Transcript')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('HGVS.c')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Inheritance'));
    expect(screen.getByLabelText(/Inheritance model/i)).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Compound heterozygous/i })).toBeInTheDocument();
    expect(screen.getByText('Any variant type')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Pathogenicity'));
    expect(screen.getByText('ClinVar status')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Frequency'));
    expect(screen.getByText('gnomAD AF')).toBeInTheDocument();
    expect(screen.getAllByRole('slider').length).toBeGreaterThan(0);
    expect(screen.getByLabelText(/Canonical only/i)).toBeInTheDocument();
    expect(screen.getByText('Review and curation')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Review and curation'));
    expect(screen.getByText('Classification')).toBeInTheDocument();
    expect(screen.getByText('Standard tags')).toBeInTheDocument();
    expect(screen.getByText('Custom tags')).toBeInTheDocument();
    expect(screen.getByLabelText(/Only show variants with saved notes/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /apply filters/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /clear all filters/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole('tab', { name: 'Auto' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Table' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Cards' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Tags & notes' }).length).toBeGreaterThan(0);
  });

  it('scopes small-variant requests to the linked project when the URL has no project id', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            members: [],
            projects: ['p1'],
          },
        });
      }
      if (url === '/projects') {
        return Promise.resolve({
          data: [{ _id: 'p1', name: 'Demo project', assembly_id: 'asm1', assembly_name: 'GRCh38' }],
        });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=100&project_id=p1')) {
        return Promise.resolve({ data: { variants: [], total: 7 } });
      }
      return Promise.resolve({ data: { variants: [], total: 0 } });
    });
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('/families/F1/small-variants?page=1&page_size=100&project_id=p1'),
      );
    });
    expect(await screen.findByText('Showing 7')).toBeInTheDocument();
  });

  it('formats bounded variant totals as 1000+', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { members: [], projects: [] } });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=100')) {
        return Promise.resolve({
          data: {
            variants: [],
            total: 1001,
            total_is_estimated: true,
            unfiltered_total: 1001,
            unfiltered_total_is_estimated: true,
            small_variant_summary: {
              total_variants: 1001,
              snv_count: 900,
              indel_count: 101,
              sample_counts: [
                {
                  sample_id: 'PROBAND',
                  non_ref_count: 345,
                  het_count: 300,
                  hom_alt_count: 45,
                },
              ],
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    // Thousands separated, matching the SNV/indel chips beside them.
    expect(await screen.findByText('Showing 1,000+')).toBeInTheDocument();
    expect(screen.getByText('All variants 1,000+')).toBeInTheDocument();
    expect(screen.getByText('SNVs 900')).toBeInTheDocument();
    expect(screen.getByText('Indels 101')).toBeInTheDocument();
    expect(screen.getByText('PROBAND')).toBeInTheDocument();
    expect(screen.getByText('345')).toBeInTheDocument();
    expect(screen.getByText('300')).toBeInTheDocument();
    expect(screen.getByText('45')).toBeInTheDocument();
  });

  it('separates an exact total and makes the family title the only way back', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { members: [], projects: [] } });
      }
      if (url === '/panels' || url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=100')) {
        return Promise.resolve({
          data: {
            variants: [],
            total: 24680,
            total_is_estimated: false,
            unfiltered_total: 135791,
            unfiltered_total_is_estimated: false,
            small_variant_summary: {
              total_variants: 135791,
              snv_count: 120000,
              indel_count: 15791,
              sample_counts: [],
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText('All variants 135,791')).toBeInTheDocument();
    expect(screen.getByText('Showing 24,680')).toBeInTheDocument();

    // The heading itself navigates, so the separate Family button is gone.
    const title = screen.getByRole('link', { name: 'Family F1' });
    expect(title).toHaveAttribute('href', '/families/F1');
    expect(screen.getAllByRole('link', { name: /^Family/ })).toHaveLength(1);
  });

  it('applies CoGA quick filters without opening each section', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
              { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
            ],
            projects: [],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({
          data: [{ _id: 'panel-1', name: 'Cardio panel' }],
        });
      }
      if (url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=100')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=1')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await screen.findByRole('combobox', { name: /preset or saved search/i });

    fireEvent.change(screen.getByLabelText(/Quick inheritance/i), {
      target: { value: 'de_novo_dominant' },
    });
    fireEvent.change(screen.getByLabelText(/Quick pathogenicity/i), {
      target: { value: 'not_benign' },
    });
    fireEvent.change(screen.getByLabelText(/Quick frequency/i), {
      target: { value: 'gnomad_rare' },
    });
    fireEvent.change(screen.getByLabelText(/Quick exclude/i), {
      target: { value: 'excluded_and_benign' },
    });
    fireEvent.change(screen.getByLabelText(/Quick review/i), {
      target: { value: 'pathogenic_vus' },
    });
    fireEvent.change(screen.getByLabelText(/Quick annotations/i), {
      target: { value: 'moderate_to_high' },
    });
    fireEvent.change(screen.getByLabelText(/Quick gene panel/i), {
      target: { value: 'panel-1' },
    });
    fireEvent.change(screen.getByLabelText(/Quick call quality/i), {
      target: { value: 'high_quality' },
    });

    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('inheritance=de_novo_dominant'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('impact=HIGH'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('impact=MODERATE'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('clinvar=Pathogenic'));
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('clinvar=Likely+pathogenic'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('clinvar=Uncertain+significance'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('clinvar=Conflicting+classifications'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('max_gnomad_exomes_af=0.01'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('max_gnomad_genomes_af=0.01'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('max_gnomad_hom_count=10'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('max_gnomad_hemi_count=10'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('exclude_clinvar=Benign'));
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('exclude_clinvar=Likely+benign'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('exclude_review_tag=excluded'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('classification=Pathogenic+-+class+5'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('classification=Likely+Pathogenic+-+class+4'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('classification=VUS+-+class+3'),
      );
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('panel_id=panel-1'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('sample_filter=PROBAND'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('%3A20%3A10%3A0.2%3A4'));
    });
    // Scoped to the request this click produced, not "any call ever made": the page's
    // default preset already bounds popmax, so a toHaveBeenCalledWith here would be
    // satisfied by the initial load and would pass even if the quick filter cleared it.
    const quickFilterUrl = [...apiMock.get.mock.calls]
      .map(([url]) => String(url))
      .filter((url) => url.includes('inheritance=de_novo_dominant'))
      .pop();
    expect(quickFilterUrl).toContain('max_gnomad_exomes_af=0.01');
    // Popmax has to be bounded too: an annotation run can carry VEP's MAX_AF without any
    // gnomAD exome/genome AF, and the query reads a missing AF as 0.
    expect(quickFilterUrl).toContain('max_gnomad_popmax_af=0.01');

    await waitFor(() =>
      expect(screen.queryByText('Loading small variants')).not.toBeInTheDocument(),
    );
    expect(screen.getByText('Gene panel: Cardio panel')).toBeInTheDocument();
    expect(screen.queryByText('Gene panel: panel-1')).not.toBeInTheDocument();
  });

  it('applies the Phenotype priority (Exomiser-style) preset', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
              { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
            ],
            projects: [],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.change(await screen.findByRole('combobox', { name: /preset or saved search/i }), {
      target: { value: 'built-in:phenotype_priority' },
    });
    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      // Phenotype prioritization scoring enabled.
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('prioritize=true'));
      // Annotations: high or moderate impact.
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('impact=HIGH'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('impact=MODERATE'));
      // Rare frequency + H/H cap.
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('max_gnomad_exomes_af=0.01'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('max_gnomad_genomes_af=0.01'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('max_gnomad_hom_count=10'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('max_gnomad_hemi_count=10'));
      // ClinVar P/LP overrides frequency.
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('clinvar_overrides_frequency=true'),
      );
      // Exclude benign / likely benign.
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('exclude_clinvar=Benign'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('exclude_clinvar=Likely+benign'));
      // Affected proband constrained to carry the variant (Hom/Het), not WT.
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('sample_filter=PROBAND'));
    });
  });

  it('shows the ClinVar-overrides-frequency chip by default and clears it on click', async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await screen.findByRole('combobox', { name: /preset or saved search/i });
    const chip = await screen.findByRole('button', {
      name: /clinvar p\/lp overrides frequency/i,
    });
    expect(chip).toBeInTheDocument();

    fireEvent.click(chip);

    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: /clinvar p\/lp overrides frequency/i }),
      ).not.toBeInTheDocument();
    });
  });

  it('applies the review quick tag filter', async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await screen.findByRole('combobox', { name: /preset or saved search/i });

    fireEvent.change(screen.getByLabelText(/Quick review/i), {
      target: { value: 'review_tag' },
    });
    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('review_tag=review'));
    });
  });

  it('sorts the table by position, gene, and impact', async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: /Chr/i })).toBeInTheDocument();
    });

    const getBodyRows = () => screen.getAllByRole('row').slice(1);

    expect(within(getBodyRows()[0]).getByText('ALPHA')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('columnheader', { name: /Gene/i }));
    expect(within(getBodyRows()[0]).getByText('ALPHA')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('columnheader', { name: /Impact/i }));
    expect(within(getBodyRows()[0]).getByText('TP53')).toBeInTheDocument();
  });

  it('does not duplicate quick review tags as extra pills in the result table', async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
    await screen.findByRole('columnheader', { name: /Chr/i });

    const brca2Row = screen
      .getAllByRole('row')
      .find((row) => within(row).queryByText('BRCA2'));

    expect(brca2Row).toBeTruthy();
    expect(within(brca2Row as HTMLElement).getAllByText(/^Review$/)).toHaveLength(1);
  });

  it('highlights ClinVar pathogenic and benign variants in the table', async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
    await screen.findByRole('columnheader', { name: /Chr/i });

    const pathogenicRow = screen
      .getAllByRole('row')
      .find((row) => within(row).queryByText('BRCA2')) as HTMLElement | undefined;
    const benignRow = screen
      .getAllByRole('row')
      .find((row) => within(row).queryByText('ALPHA')) as HTMLElement | undefined;

    expect(pathogenicRow).toBeTruthy();
    expect(benignRow).toBeTruthy();
    expect(pathogenicRow?.className).toContain('variant-table-row--clinvar-pathogenic');
    expect(benignRow?.className).toContain('variant-table-row--clinvar-benign');
    expect(screen.queryByText(/Updated ·/i)).not.toBeInTheDocument();
  });

  it('renders pair-level grouped results for compound-het searches', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
              { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
            ],
            projects: [],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=100&inheritance=compound_het')) {
        return Promise.resolve({
          data: {
            total: 1,
            variants: [],
            variant_groups: [
              {
                group_type: 'compound_het',
                group_key: 'v1::v2',
                gene: 'GENE1',
                variants: [
                  {
                    _id: 'v1',
                    chr: '1',
                    start: 100,
                    end: 100,
                    type: 'SNV',
                    gene: 'GENE1',
                    ref: 'A',
                    alt: 'G',
                    impact: 'HIGH',
                    effect: 'missense_variant',
                    gnomad_af: 0.0001,
                    genotypes: [{ sample: 'PROBAND', gt: '0/1' }],
                  },
                  {
                    _id: 'v2',
                    chr: '1',
                    start: 180,
                    end: 180,
                    type: 'SNV',
                    gene: 'GENE1',
                    ref: 'C',
                    alt: 'T',
                    impact: 'MODERATE',
                    effect: 'frameshift_variant',
                    gnomad_af: 0.0002,
                    genotypes: [{ sample: 'PROBAND', gt: '0/1' }],
                  },
                ],
                review: {
                  group_id: 'grp-1',
                  partner_variant_ids: ['v2'],
                  gene: 'GENE1',
                  classification: 'Likely Pathogenic - class 4',
                  tags: [],
                  tag_metadata: {},
                  note: 'Strong pair-level fit.',
                  phase_status: 'unknown',
                },
              },
            ],
          },
        });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=1')) {
        return Promise.resolve({ data: { variants: [], variant_groups: [], total: 1 } });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants?inheritance=compound_het']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await screen.findByText('Compound-Het Pairs');
    expect(screen.getAllByText('GENE1').length).toBeGreaterThan(0);
    expect(screen.getByText('Strong pair-level fit.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Review pair/i })).toBeInTheDocument();
    expect(screen.queryByText(/No variants match the current search/i)).not.toBeInTheDocument();
  });

  it('saves the active applied search rather than unapplied draft edits', async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants?impact=HIGH']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('/families/F1/small-variants?page=1&page_size=100&impact=HIGH'),
      );
    });
    fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
    await screen.findByRole('columnheader', { name: /Chr/i });
    fireEvent.click(screen.getByText('Annotations'));

    fireEvent.click(screen.getAllByText('Impact')[0]);
    fireEvent.click(screen.getByLabelText('LOW'));
    fireEvent.click(screen.getByRole('button', { name: /save current/i }));
    fireEvent.change(screen.getByPlaceholderText('Preset name'), {
      target: { value: 'Active search preset' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(apiMock.post).toHaveBeenCalledWith(
        '/families/F1/small-variant-filter-presets',
        expect.objectContaining({
          name: 'Active search preset',
          scope: 'global',
          filters: expect.objectContaining({
            impact: ['HIGH'],
          }),
          sample_filters: {},
          sample_templates: {},
        }),
      );
    });
  });

  it('exposes the expanded carrier screening preset only for couples and applies its query flag', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            members: [
              { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
            ],
            projects: [],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=100')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=1')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    const presetSelect = await screen.findByRole('combobox', { name: /preset or saved search/i });
    expect(
      within(presetSelect).getByRole('option', { name: /Expanded carrier screening/i }),
    ).toBeInTheDocument();

    fireEvent.change(presetSelect, {
      target: { value: 'built-in:expanded_carrier_screening' },
    });
    fireEvent.click(screen.getByText('Inheritance'));
    expect(
      screen.getByLabelText(/Couple-based expanded carrier screening/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/both MOM and DAD carry a variant/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('expanded_carrier_screening=true'),
      );
    });
  });

  it('exposes the expanded carrier screening preset for two-member non-parental couples', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            members: [
              { sample_id: 'PARTNER1', role: 'proband', affected: false, sex: 'female' },
              { sample_id: 'PARTNER2', role: 'sibling', affected: false, sex: 'male' },
            ],
            projects: [],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=100')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=1')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    const presetSelect = await screen.findByRole('combobox', { name: /preset or saved search/i });
    expect(
      within(presetSelect).getByRole('option', { name: /Expanded carrier screening/i }),
    ).toBeInTheDocument();

    fireEvent.change(presetSelect, {
      target: { value: 'built-in:expanded_carrier_screening' },
    });
    fireEvent.click(screen.getByText('Inheritance'));
    expect(
      screen.getByLabelText(/Couple-based expanded carrier screening/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/both PARTNER1 and PARTNER2 carry a variant/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('expanded_carrier_screening=true'),
      );
    });
  });

  it('keeps phased heterozygous proband genotypes intact when applying a dominant preset', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
              { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
            ],
            projects: [],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=100')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url.startsWith('/families/F1/small-variants?page=1&page_size=1')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    const presetSelect = await screen.findByRole('combobox', { name: /preset or saved search/i });
    fireEvent.change(presetSelect, {
      target: { value: 'built-in:dominant_strict' },
    });
    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining(
          'sample_filter=PROBAND%3A0%2F1%7C1%2F0%7C0%7C1%7C1%7C0%3A20%3A10%3A0.2%3A4',
        ),
      );
    });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'PROBAND: Het' })).toBeInTheDocument();
    });
  });

  it('applies the explicit compound-het inheritance filter flag', async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await screen.findByRole('combobox', { name: /preset or saved search/i });
    fireEvent.click(screen.getByText('Inheritance'));
    fireEvent.change(screen.getByLabelText(/Inheritance model/i), {
      target: { value: 'compound_het' },
    });
    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('inheritance=compound_het'),
      );
    });
  });

  it('submits variant-level review data from the review dialog without compound-het payload', async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
    await screen.findByRole('columnheader', { name: /Chr/i });

    const brca2Row = screen
      .getAllByRole('row')
      .find((row) => within(row).queryByText('BRCA2'));

    expect(brca2Row).toBeTruthy();

    fireEvent.click(within(brca2Row as HTMLElement).getByRole('button', { name: 'Tags & notes' }));

    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByLabelText(/Needs RNA/i));

    fireEvent.click(within(dialog).getByRole('button', { name: /save review/i }));

    await waitFor(() => {
      expect(apiMock.put).toHaveBeenCalledWith(
        '/families/F1/small-variants/v1/review',
        expect.objectContaining({
          classification: 'VUS - class 3',
          tags: expect.arrayContaining(['needs_rna']),
        }),
      );
    });
    const payload = apiMock.put.mock.calls.at(-1)?.[1] as Record<string, unknown> | undefined;
    expect(payload).toBeTruthy();
    expect(payload?.compound_het).toBeUndefined();
  });

  it('shows a loading indicator while a filtered/paginated refetch is in flight', async () => {
    localStorage.setItem('role', 'admin');

    let resolvePage2: (value: { data: unknown }) => void = () => {};
    const page2Promise = new Promise<{ data: unknown }>((resolve) => {
      resolvePage2 = resolve;
    });

    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { members: [], projects: [] } });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variant-filter-presets')) {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variant-tags')) {
        return Promise.resolve({ data: [] });
      }
      if (url.startsWith('/families/F1/small-variants?page=2')) {
        // Hold the next-page fetch open so the refetch stays in flight.
        return page2Promise;
      }
      if (url.startsWith('/families/F1/small-variants?page=1')) {
        return Promise.resolve({
          data: {
            variants: [
              {
                _id: 'v1',
                chr: '1',
                start: 10,
                end: 10,
                type: 'SNV',
                gene: 'TP53',
                ref: 'A',
                alt: 'G',
                impact: 'HIGH',
                effect: 'stop_gained',
                genotypes: [],
              },
            ],
            total: 300,
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    // First page settles: results shown, no loading indicator.
    const nextButton = await screen.findByRole('button', { name: 'Next' });
    expect(screen.queryByText(/Loading variants/i)).not.toBeInTheDocument();

    // Navigating to the next page keeps the previous results and refetches.
    fireEvent.click(nextButton);

    // The loading indicator appears while the refetch is in flight.
    expect(await screen.findByText(/Loading variants/i)).toBeInTheDocument();

    // Resolve the held request and confirm the indicator clears.
    resolvePage2({ data: { variants: [], total: 300 } });
    await waitFor(() => {
      expect(screen.queryByText(/Loading variants/i)).not.toBeInTheDocument();
    });
  });
});
