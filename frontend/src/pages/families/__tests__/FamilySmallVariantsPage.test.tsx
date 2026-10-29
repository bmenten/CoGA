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
    // The filter categories open collapsed, behind the "Advanced filters" toggle.
    fireEvent.click(screen.getByRole('button', { name: /Advanced filters/ }));
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
    // The family search applies every exclusion; only the explorer hides some (#526).
    expect(screen.getByPlaceholderText(/^Excluded genes:/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/^Excluded intervals:/)).toBeInTheDocument();
    expect(screen.getByText('Excluded standard tags')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Excluded tag' })).toBeInTheDocument();
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

  // #608 — the search runs within the linked project; with the catalogue failed it used to
  // wait for the project for good. It says the reference failed, and retries.
  it('says the reference failed, instead of loading for good, and runs the search on retry', async () => {
    let projectRequests = 0;
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { members: [], projects: ['p1'] } });
      }
      if (url === '/projects') {
        projectRequests += 1;
        return projectRequests === 1
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
          : Promise.resolve({
              data: [{ _id: 'p1', name: 'Demo project', assembly_id: 'asm1', assembly_name: 'GRCh38' }],
            });
      }
      if (url.startsWith('/families/F1/small-variants?')) {
        return Promise.resolve({ data: { variants: [], total: 7 } });
      }
      return Promise.resolve({ data: [] });
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Reference could not be loaded')).toBeInTheDocument();
    expect(screen.queryByText('Loading small variants')).not.toBeInTheDocument();
    expect(apiMock.get.mock.calls.some(([url]) => String(url).startsWith('/families/F1/small-variants?'))).toBe(
      false,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('Showing 7')).toBeInTheDocument();
  });

  // SAFE-14 — the search waits for the family record, which holds what the header warns of;
  // with the record failed it waited for good. It says the family failed, and retries.
  it('says the family could not be loaded, instead of loading for good, and runs the search on retry', async () => {
    let familyRequests = 0;
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        familyRequests += 1;
        return familyRequests === 1
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
          : Promise.resolve({ data: { members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants?')) {
        return Promise.resolve({ data: { variants: [], total: 7 } });
      }
      return Promise.resolve({ data: [] });
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/small-variants']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const failure = (await screen.findByRole('heading', { name: 'Family could not be loaded' })).closest(
      '.page-state',
    ) as HTMLElement;
    expect(failure).toHaveTextContent('This is a failed request, not a missing family.');
    expect(screen.queryByText('Loading small variants')).not.toBeInTheDocument();
    expect(apiMock.get.mock.calls.some(([url]) => String(url).startsWith('/families/F1/small-variants?'))).toBe(
      false,
    );

    fireEvent.click(within(failure).getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('Showing 7')).toBeInTheDocument();
  });

  // #606 — a failed panel list used to leave the select at "Any gene panel" while the
  // panel from the URL was still applied, and to drop the default Mendeliome scope unsaid.
  it('keeps an applied panel visible when the panel list could not be loaded', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { members: [], projects: [] } });
      }
      if (url === '/panels') {
        return Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
      }
      if (url.startsWith('/families/F1/small-variants?')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      return Promise.resolve({ data: [] });
    });

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/small-variants?page=1&panel_id=P1']}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/Could not load the gene panel list — this is not an empty result/)).toHaveTextContent(
      /Panels cannot be chosen, and the default Mendeliome scope is not applied/,
    );
    fireEvent.click(screen.getByRole('button', { name: /Advanced filters/ }));
    const panelSelect = screen.getByRole('combobox', { name: 'Quick gene panel' });
    expect(panelSelect).toHaveValue('P1');
    expect(within(panelSelect).getByRole('option', { name: 'Panel P1 (not in the panel list)' })).toBeInTheDocument();
  });

  // #604 — an interval list entry that cannot be read is named, not skipped: skipped, the
  // search covered less than the list asked, or read as a family without variants.
  describe('an unreadable interval', () => {
    const serveFamily = () =>
      apiMock.get.mockImplementation((url: string) => {
        if (url === '/families/F1') {
          return Promise.resolve({ data: { members: [], projects: [] } });
        }
        if (url.startsWith('/families/F1/small-variants?')) {
          return Promise.resolve({ data: { variants: [], total: 3 } });
        }
        return Promise.resolve({ data: [] });
      });
    const renderAt = (path: string) =>
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    const searches = () =>
      apiMock.get.mock.calls.filter(([url]) => String(url).startsWith('/families/F1/small-variants?'));

    it('is named under the field, and the search is not run', async () => {
      serveFamily();
      renderAt('/families/F1/small-variants?page=1&gene=BRCA1');
      expect(await screen.findByText('Showing 3')).toBeInTheDocument();
      const before = searches().length;

      fireEvent.change(screen.getByPlaceholderText(/^Intervals:/), {
        target: { value: 'chr17\t43044295\t43125482' },
      });
      fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

      expect(
        await screen.findByText(/Interval 'chr17\s43044295\s43125482' is not chr:start-end\. Nothing was searched\./),
      ).toBeInTheDocument();
      expect(searches().length).toBe(before);
    });

    it('fails a search whose URL carries one, with its reason', async () => {
      serveFamily();
      renderAt('/families/F1/small-variants?page=1&intervals=chr1%3A200-100');

      expect(await screen.findByText('Unable to load small variants')).toBeInTheDocument();
      expect(screen.getByText("Interval 'chr1:200-100' ends before it starts.")).toBeInTheDocument();
      // The page may search once before it reads the URL, but never with the interval.
      expect(searches().filter(([url]) => String(url).includes('intervals='))).toHaveLength(0);
    });
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
    // The per-member table of variants, het and hom calls is gone from the header (owner,
    // 2026-10-29).
    expect(screen.queryByText('345')).not.toBeInTheDocument();
  });

  describe('a search that read only part of the callset (#725 follow-up)', () => {
    const renderWithPage = (page: Record<string, unknown>) => {
      apiMock.get.mockImplementation((url: string) => {
        if (url === '/families/F1') {
          return Promise.resolve({ data: { members: [], projects: [] } });
        }
        if (
          url === '/panels' ||
          url === '/families/F1/small-variant-filter-presets' ||
          url === '/families/F1/small-variant-tags'
        ) {
          return Promise.resolve({ data: [] });
        }
        if (url.startsWith('/families/F1/small-variants?page=1&page_size=100')) {
          return Promise.resolve({ data: { variants: [], ...page } });
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
        </QueryClientProvider>,
      );
    };

    it('says where a capped candidate read stopped, and counts what it read', async () => {
      renderWithPage({
        total: 12,
        total_is_estimated: true,
        count_limit: 10000,
        candidates_capped: true,
        candidate_limit: 1250,
      });
      const notice = await screen.findByTestId('candidate-cap-notice');
      expect(notice).toHaveTextContent(
        'Results may be incomplete: the search stopped after the first 1,250 candidate variants',
      );
      expect(notice).toHaveTextContent('Narrow the filters (a region, a gene panel or a gene)');
      // The 12 rows read are counted as 12, a lower bound; it read "11+".
      expect(screen.getByText('Showing 12+')).toBeInTheDocument();
    });

    it('says how many candidates a truncated ranking covered', async () => {
      renderWithPage({
        total: 0,
        total_is_estimated: true,
        count_limit: 10000,
        ranking_truncated: true,
        candidate_limit: 5000,
      });
      expect(await screen.findByTestId('candidate-cap-notice')).toHaveTextContent(
        'Ranking may be incomplete: the prioritizer ranked only the first 5,000 candidate variants',
      );
    });

    it('shows no notice for a complete search', async () => {
      renderWithPage({ total: 3, total_is_estimated: false });
      expect(await screen.findByText('Showing 3')).toBeInTheDocument();
      expect(screen.queryByTestId('candidate-cap-notice')).not.toBeInTheDocument();
    });
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

    await screen.findByText('Compound-het pairs');
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
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('impact=HIGH'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('impact=MODERATE'));
      expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('max_gnomad_popmax_af=0.01'));
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('clinvar_overrides_frequency=true'),
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
    // #513: the save names the version it was made against.
    expect(payload?.expected_updated_at).toBe('2026-04-14T10:00:00Z');
  });

  // A review saved from a card is shown on it from the server's answer: the list is searched
  // again only when its filters read reviews, and it stays usable meanwhile.
  const searchCalls = () =>
    apiMock.get.mock.calls.filter(([url]) => String(url).startsWith('/families/F1/small-variants?')).length;

  const renderAt = (entry: string) =>
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={[entry]}>
          <Routes>
            <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

  it('saves a quick tag without searching the list again when its filters read no reviews', async () => {
    renderAt('/families/F1/small-variants');
    fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
    await screen.findByRole('columnheader', { name: /Chr/i });
    const searchesBefore = searchCalls();
    const brca2Row = screen.getAllByRole('row').find((row) => within(row).queryByText('BRCA2')) as HTMLElement;

    fireEvent.click(within(brca2Row).getByRole('button', { name: 'Exclude' }));

    await waitFor(() => expect(apiMock.put).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText('Variant review saved.')).toBeInTheDocument());
    expect(searchCalls()).toBe(searchesBefore);
    expect(screen.queryByText('Loading variants')).not.toBeInTheDocument();
  });

  it('searches the list again after a review save when its filters read reviews, without covering it', async () => {
    renderAt('/families/F1/small-variants?exclude_review_tag=excluded');
    fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
    await screen.findByRole('columnheader', { name: /Chr/i });
    const searchesBefore = searchCalls();
    const brca2Row = screen.getAllByRole('row').find((row) => within(row).queryByText('BRCA2')) as HTMLElement;

    fireEvent.click(within(brca2Row).getByRole('button', { name: 'Exclude' }));

    await waitFor(() => expect(searchCalls()).toBe(searchesBefore + 1));
    // The same search run again: the results stay usable, with no loading cover.
    expect(screen.queryByText('Loading variants')).not.toBeInTheDocument();
  });

  it('shows a review conflict and reloads instead of overwriting (#513)', async () => {
    apiMock.put.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            code: 'review_conflict',
            message:
              'This review was changed by bob after you opened it; your changes were not saved. The current review has been loaded.',
            current: null,
          },
        },
      },
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

    fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
    await screen.findByRole('columnheader', { name: /Chr/i });
    const brca2Row = screen.getAllByRole('row').find((row) => within(row).queryByText('BRCA2'));
    fireEvent.click(within(brca2Row as HTMLElement).getByRole('button', { name: 'Tags & notes' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByLabelText(/Needs RNA/i));

    const variantFetches = () =>
      apiMock.get.mock.calls.filter(([url]) => String(url).startsWith('/families/F1/small-variants?'))
        .length;
    const fetchesBeforeSave = variantFetches();
    fireEvent.click(within(dialog).getByRole('button', { name: /save review/i }));

    expect((await screen.findAllByText(/changed by bob after you opened it/)).length).toBeGreaterThan(0);
    // The current review is reloaded rather than the stale copy kept on screen.
    await waitFor(() => expect(variantFetches()).toBeGreaterThan(fetchesBeforeSave));
  });

  describe('the ACMG dialog opened from the list', () => {
    // A classified variant's ACMG record as the save stored it, with an analyst's own
    // criterion (PS3) that the evaluator never suggests, and a strength they lowered.
    const SAVED_ACMG = {
      criteria: [
        {
          code: 'PS3',
          strength: 'strong',
          accepted: true,
          evidence: 'RNA assay shows exon skipping',
          auto_suggested: false,
        },
        { code: 'PM2', strength: 'supporting', accepted: true, evidence: null, auto_suggested: true },
      ],
      point_total: 5,
      classification: 'VUS - class 3',
      vus_tier: 'hot',
    };

    /** The list's review of v1, with these fields as the backend serves them. */
    const serveListReview = (fields: Record<string, unknown>) => {
      const base = apiMock.get.getMockImplementation()!;
      apiMock.get.mockImplementation(async (url: string, config?: unknown) => {
        // The dialog reads the family's HPO terms for PP4: none here.
        if (url === '/families/F1/hpo') return { data: [] };
        const response = await base(url, config);
        if (!url.startsWith('/families/F1/small-variants?page=1&page_size=100')) return response;
        const page = response.data as { variants: Array<Record<string, unknown>> };
        return {
          data: {
            ...page,
            variants: page.variants.map((variant) =>
              variant._id === 'v1'
                ? { ...variant, review: { ...(variant.review as object), ...fields } }
                : variant,
            ),
          },
        };
      });
    };

    const openAcmgFromTheList = async () => {
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/families/F1/small-variants']}>
            <Routes>
              <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
      await screen.findByRole('columnheader', { name: /Chr/i });
      const brca2Row = screen.getAllByRole('row').find((row) => within(row).queryByText('BRCA2'));
      fireEvent.click(within(brca2Row as HTMLElement).getByRole('button', { name: 'ACMG classify' }));
      return screen.findByRole('dialog');
    };

    const resave = async (dialog: HTMLElement) => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Save classification' }));
      await waitFor(() =>
        expect(apiMock.put).toHaveBeenCalledWith('/families/F1/small-variants/v1/review', expect.anything()),
      );
      const payload = apiMock.put.mock.calls.at(-1)?.[1] as {
        acmg?: { criteria: Array<Record<string, unknown>> };
      };
      return payload.acmg?.criteria ?? [];
    };

    it('shows the saved criteria, and an unchanged re-save keeps them', async () => {
      serveListReview({ acmg: SAVED_ACMG, acmg_unreadable: false });
      const dialog = await openAcmgFromTheList();

      expect(await within(dialog).findByRole('checkbox', { name: /^PS3:/ })).toBeChecked();
      expect(within(dialog).getByRole('combobox', { name: 'PS3 strength' })).toHaveValue('strong');
      expect(within(dialog).getByRole('checkbox', { name: /^PM2:/ })).toBeChecked();
      expect(within(dialog).getByRole('combobox', { name: 'PM2 strength' })).toHaveValue('supporting');

      const criteria = await resave(dialog);
      expect(criteria).toContainEqual({
        code: 'PS3',
        strength: 'strong',
        accepted: true,
        evidence: 'RNA assay shows exon skipping',
        auto_suggested: false,
      });
      expect(criteria).toContainEqual(
        expect.objectContaining({ code: 'PM2', strength: 'supporting', accepted: true }),
      );
    });

    it('is seeded from the suggestions alone when the list leaves the ACMG record out', async () => {
      // How the list served a classified variant before it carried the record: acmg null,
      // not marked unreadable. The dialog then showed no saved decision, and an unchanged
      // re-save sent the suggestions, which the server stores in place of the record.
      serveListReview({ acmg: null, acmg_unreadable: false });
      const dialog = await openAcmgFromTheList();

      expect(await within(dialog).findByRole('checkbox', { name: /^PS3:/ })).not.toBeChecked();
      const criteria = await resave(dialog);
      expect(criteria.some((criterion) => criterion.code === 'PS3' && criterion.accepted)).toBe(false);
    });
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

  // Found while fixing #1012: saved searches are each user's own, and this page and the NIPT page
  // share them. A search saved on the NIPT page carries the NIPT list's own filters, and this page
  // sent them: a NIPT inheritance view failed the whole search, the fetal de novo view was read as
  // de novo / dominant, and the NIPT-only filters were chipped and counted though the list never
  // applied them.
  describe('a search saved on the NIPT page, or a link with its filters', () => {
    // What the server reads as an inheritance (clickhouse_variant_queries: its modes, and the
    // spellings it reads as one of them). It refuses any other, and with it the whole search.
    const LIST_INHERITANCE = new Set([
      'de_novo_dominant',
      'recessive_homozygous',
      'compound_het',
      'x_linked',
      'recessive',
      'compound_heterozygous',
      'recessive_hom',
      'homozygous_recessive',
      'dominant',
      'xlinked',
      'x_linked_recessive',
    ]);
    const NIPT_ONLY = ['inheritance', 'category', 'min_confidence', 'include_not_inherited', 'de_novo_priority'];
    const NIPT_DE_NOVO = {
      _id: 'preset-nipt',
      owner: 'reviewer',
      name: 'NIPT de novo, confident',
      description: null,
      filters: {
        inheritance: 'de_novo',
        impact: ['HIGH', 'MODERATE'],
        max_gnomad_af: '0.01',
        clinvar_overrides_frequency: 'true',
        category: ['1'],
        min_confidence: '0.9',
        de_novo_priority: 'medium',
      },
      sample_filters: {},
      sample_templates: {},
      created_at: '2026-10-27T10:00:00Z',
      updated_at: '2026-10-27T10:00:00Z',
    };

    const serve = () => {
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
        if (url === '/panels') return Promise.resolve({ data: [] });
        if (url === '/families/F1/small-variant-filter-presets') return Promise.resolve({ data: [NIPT_DE_NOVO] });
        if (url === '/families/F1/small-variant-tags') return Promise.resolve({ data: [] });
        if (url.startsWith('/families/F1/small-variants')) {
          const inheritance = new URLSearchParams(url.split('?')[1] ?? '').get('inheritance');
          if (inheritance && !LIST_INHERITANCE.has(inheritance)) {
            return Promise.reject(
              Object.assign(new Error('HTTP 400'), {
                response: { status: 400, data: { detail: 'Unsupported small-variant inheritance filter' } },
              }),
            );
          }
          return Promise.resolve({
            data: {
              variants: [
                { _id: 'v1', chr: '1', start: 10, end: 10, type: 'SNV', gene: 'ALPHA', ref: 'C', alt: 'T', genotypes: [] },
              ],
              total: 1,
            },
          });
        }
        return Promise.resolve({ data: {} });
      });
    };

    const renderAt = (path: string) =>
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

    const requests = (prefix: string) =>
      apiMock.get.mock.calls
        .map(([url]) => url as string)
        .filter((url) => url.startsWith(prefix))
        .map((url) => new URLSearchParams(url.split('?')[1]));
    const listRequests = () => requests('/families/F1/small-variants?');

    it('sends none of a NIPT saved search’s own filters, shows no chip or count for them, and names them once', async () => {
      serve();
      renderAt('/families/F1/small-variants');

      await screen.findByRole('option', { name: 'NIPT de novo, confident' });
      fireEvent.change(screen.getByLabelText('Preset or saved search'), { target: { value: 'saved:preset-nipt' } });
      fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

      await waitFor(() => expect(listRequests().at(-1)?.get('max_gnomad_af')).toBe('0.01'));
      const params = listRequests().at(-1) ?? new URLSearchParams();
      NIPT_ONLY.forEach((name) => expect(params.has(name), name).toBe(false));
      expect(params.getAll('impact')).toEqual(['HIGH', 'MODERATE']);
      // The search runs, with every filter the list applies.
      expect(await screen.findByText('Showing 1')).toBeInTheDocument();
      expect(screen.queryByText('Unable to load small variants')).not.toBeInTheDocument();
      // Only what the list applies is chipped and counted: the impacts and the gnomAD AF.
      expect(screen.getByRole('button', { name: 'Impact: HIGH' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Inheritance/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Category/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Min confidence/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^De novo priority/ })).not.toBeInTheDocument();
      expect(screen.getByText('Active filters 3')).toBeInTheDocument();
      // Said once, beside the chips.
      expect(
        screen.getAllByText(
          'The small-variant list does not apply this search’s “De novo in the fetus” inheritance, maternal/fetal category, minimum-confidence and de novo priority filters.',
        ),
      ).toHaveLength(1);

      // The CSV export reads the same search as the list.
      fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
      await waitFor(() => expect(requests('/families/F1/small-variants/export?')).toHaveLength(1));
      const [exported] = requests('/families/F1/small-variants/export?');
      NIPT_ONLY.forEach((name) => expect(exported.has(name), name).toBe(false));
      expect(exported.getAll('impact')).toEqual(['HIGH', 'MODERATE']);

      // Save current saves the search as it is, the NIPT list's filters too.
      fireEvent.click(screen.getByRole('button', { name: /save current/i }));
      fireEvent.change(screen.getByPlaceholderText('Preset name'), { target: { value: 'Copy' } });
      fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
      await waitFor(() =>
        expect(apiMock.post).toHaveBeenCalledWith(
          '/families/F1/small-variant-filter-presets',
          expect.objectContaining({
            filters: expect.objectContaining({
              inheritance: 'de_novo',
              category: ['1'],
              min_confidence: '0.9',
              de_novo_priority: 'medium',
            }),
          }),
        ),
      );
    });

    it('never sends the fetal de novo view a link carries, as itself or as de novo / dominant', async () => {
      serve();
      renderAt('/families/F1/small-variants?inheritance=de_novo&prioritize=false');

      expect(await screen.findByText('Showing 1')).toBeInTheDocument();
      expect(listRequests().length).toBeGreaterThan(0);
      listRequests().forEach((params) => expect(params.has('inheritance')).toBe(false));
      expect(screen.queryByRole('button', { name: /^Inheritance/ })).not.toBeInTheDocument();
      expect(screen.getByText('Active filters 0')).toBeInTheDocument();
      expect(
        screen.getByText('The small-variant list does not apply this search’s “De novo in the fetus” inheritance filter.'),
      ).toBeInTheDocument();
    });

    it('fails no search for another NIPT view a link carries, and names it with the not-inherited alleles', async () => {
      serve();
      renderAt('/families/F1/small-variants?inheritance=paternal_dominant&include_not_inherited=true&prioritize=false');

      expect(await screen.findByText('Showing 1')).toBeInTheDocument();
      expect(screen.queryByText(/Unsupported small-variant inheritance filter/)).not.toBeInTheDocument();
      listRequests().forEach((params) => {
        expect(params.has('inheritance')).toBe(false);
        expect(params.has('include_not_inherited')).toBe(false);
      });
      expect(screen.queryByRole('button', { name: 'Also not inherited' })).not.toBeInTheDocument();
      expect(
        screen.getByText(
          'The small-variant list does not apply this search’s “Paternal, inherited by the fetus” inheritance and not-inherited-alleles filters.',
        ),
      ).toBeInTheDocument();
    });

    it('still sends and chips a mode the list reads, and another spelling of one as that mode', async () => {
      serve();
      renderAt('/families/F1/small-variants?inheritance=dominant&prioritize=false');

      await waitFor(() => expect(listRequests().at(-1)?.get('inheritance')).toBe('de_novo_dominant'));
      expect(await screen.findByRole('button', { name: 'Inheritance: De novo / dominant' })).toBeInTheDocument();
      expect(screen.getByText('Active filters 1')).toBeInTheDocument();
      expect(screen.queryByText(/The small-variant list does not apply/)).not.toBeInTheDocument();
    });
  });

  // The active preset is named above the filters, marked once the draft departs from it, and
  // the filter categories open collapsed behind a toggle that keeps their values.
  describe('the active filter and the advanced filters', () => {
    const serveTrio = () => {
      apiMock.get.mockImplementation((url: string) => {
        if (url === '/families/F1') {
          return Promise.resolve({
            data: {
              members: [
                { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
                { sample_id: 'MOM', role: 'mother', affected: false, sex: 'female' },
                { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
              ],
              projects: [],
            },
          });
        }
        if (url === '/panels') {
          return Promise.resolve({
            data: [{ _id: 'MENDEL', name: 'Mendeliome', source: 'mendeliome' }],
          });
        }
        if (url === '/families/F1/small-variant-filter-presets') {
          return Promise.resolve({
            data: [
              {
                _id: 'preset-1',
                owner: 'reviewer',
                name: 'Dominant shortlist',
                filters: { impact: 'HIGH' },
                sample_filters: {},
                sample_templates: {},
                created_at: '2026-04-14T10:00:00Z',
                updated_at: '2026-04-14T10:00:00Z',
              },
            ],
          });
        }
        if (url.startsWith('/families/F1/small-variants?')) {
          return Promise.resolve({
            data: {
              variants: [
                { _id: 'v1', chr: '1', start: 10, end: 10, type: 'SNV', gene: 'ALPHA', ref: 'C', alt: 'T', genotypes: [] },
              ],
              total: 1,
            },
          });
        }
        return Promise.resolve({ data: [] });
      });
    };

    const renderAt = (path: string) =>
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/families/:familyId/small-variants" element={<FamilySmallVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

    const activeFilter = () => screen.getByText('Active filter:').closest('p') as HTMLElement;
    const advancedToggle = () => screen.getByRole('button', { name: /Advanced filters/ });

    it('names the default filter on a fresh open, with the categories collapsed', async () => {
      serveTrio();
      renderAt('/families/F1/small-variants');

      await waitFor(() =>
        expect(activeFilter()).toHaveTextContent(
          'Active filter: Phenotype priority (Exomiser-style), Mendeliome panel',
        ),
      );
      expect(within(activeFilter()).getByText('default')).toBeInTheDocument();
      expect(within(activeFilter()).queryByText('modified')).not.toBeInTheDocument();
      expect(screen.getByLabelText('Preset or saved search')).toHaveValue('built-in:phenotype_priority');
      expect(screen.getByLabelText('Preset or saved search')).toHaveClass('variant-preset-select--active');

      // Collapsed, the toggle still says how many categories there are and how many are set.
      const toggle = advancedToggle();
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      expect(toggle).toHaveTextContent(/\(10 categories, \d+ with filters set\)/);
      const panel = document.getElementById(toggle.getAttribute('aria-controls') ?? '') as HTMLElement;
      expect(panel).not.toBeVisible();
      expect(screen.queryByRole('combobox', { name: 'Quick gene panel' })).not.toBeInTheDocument();

      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute('aria-expanded', 'true');
      expect(panel).toBeVisible();
      expect(screen.getByRole('combobox', { name: 'Quick gene panel' })).toHaveValue('MENDEL');
    });

    it('marks the filter modified, keeps values while collapsed, and resets to the preset', async () => {
      serveTrio();
      renderAt('/families/F1/small-variants');
      await waitFor(() => expect(within(activeFilter()).getByText('default')).toBeInTheDocument());

      fireEvent.click(advancedToggle());
      fireEvent.change(screen.getByPlaceholderText('Transcript'), { target: { value: 'NM_000059.4' } });
      expect(within(activeFilter()).getByText('modified')).toBeInTheDocument();
      expect(screen.getByLabelText('Preset or saved search')).toHaveClass('variant-preset-select--modified');

      // Collapsing hides the fields without changing them.
      fireEvent.click(advancedToggle());
      expect(advancedToggle()).toHaveAttribute('aria-expanded', 'false');
      expect(screen.getByPlaceholderText('Transcript')).toHaveValue('NM_000059.4');
      fireEvent.click(advancedToggle());
      expect(screen.getByPlaceholderText('Transcript')).toHaveValue('NM_000059.4');
      expect(within(activeFilter()).getByText('modified')).toBeInTheDocument();

      // Hiding the whole filter panel keeps the form, and so the loaded preset, too.
      fireEvent.click(screen.getByRole('button', { name: 'Hide filters' }));
      fireEvent.click(screen.getByRole('button', { name: 'Show filters' }));
      expect(screen.getByPlaceholderText('Transcript')).toHaveValue('NM_000059.4');
      expect(within(activeFilter()).getByText('modified')).toBeInTheDocument();

      fireEvent.click(within(activeFilter()).getByRole('button', { name: 'Reset to preset' }));
      expect(screen.getByPlaceholderText('Transcript')).toHaveValue('');
      expect(screen.getByRole('combobox', { name: 'Quick gene panel' })).toHaveValue('MENDEL');
      expect(within(activeFilter()).queryByText('modified')).not.toBeInTheDocument();
      expect(within(activeFilter()).getByText('default')).toBeInTheDocument();
    });

    it('names a picked saved search, and no preset once the filters are cleared', async () => {
      serveTrio();
      renderAt('/families/F1/small-variants');
      await screen.findByRole('option', { name: 'Dominant shortlist' });

      fireEvent.change(screen.getByLabelText('Preset or saved search'), {
        target: { value: 'saved:preset-1' },
      });
      expect(activeFilter()).toHaveTextContent('Active filter: Dominant shortlist');
      expect(within(activeFilter()).queryByText('default')).not.toBeInTheDocument();
      expect(within(activeFilter()).queryByText('modified')).not.toBeInTheDocument();

      // Applied, the search goes through the URL and back into the draft: still not modified.
      fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));
      await waitFor(() =>
        expect(
          apiMock.get.mock.calls.some(([url]) => (url as string).includes('impact=HIGH')),
        ).toBe(true),
      );
      expect(activeFilter()).toHaveTextContent('Active filter: Dominant shortlist');
      expect(within(activeFilter()).queryByText('modified')).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Clear all filters' }));
      expect(activeFilter()).toHaveTextContent('Active filter: custom (no preset loaded)');
      expect(screen.getByLabelText('Preset or saved search')).toHaveValue('');
    });

    it('names no preset when the page opens on a search from the URL', async () => {
      serveTrio();
      renderAt('/families/F1/small-variants?impact=HIGH');
      await screen.findByText('Showing 1');
      expect(activeFilter()).toHaveTextContent('Active filter: custom (no preset loaded)');
    });
  });
});
