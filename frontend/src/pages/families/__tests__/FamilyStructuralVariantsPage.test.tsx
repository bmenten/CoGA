import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import FamilyStructuralVariantsPage from '../FamilyStructuralVariantsPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { findAssemblyScopeBanner, offScopeProject } from '../../../test/familyPageBanners';
import { held, settle } from '../../../test/settle';
import { factText } from '../../../test/factText';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn((url: string) => {
      if (url.startsWith('/families/F1/structural-variants?page=1&page_size=100')) {
        return Promise.resolve({
          data: {
            variants: [
              {
                _id: 'v1',
                chr: 'chr1',
                start: 1,
                end: 2,
                length: 1,
                type: 'DEL',
                annotation_extra: {
                  cytoband: '1p36.33',
                },
                genotypes: [],
              },
            ],
            total: 1,
          },
        });
      }
      if (url.startsWith('/families/F1/structural-variants?page=1&page_size=1')) {
        return Promise.resolve({ data: { variants: [], total: 5 } });
      }
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            pedigree: 'F1\tS1\t0\t0\t1\t2',
            members: [
              {
                sample_id: 'S1',
                role: 'proband',
                affected: true,
                sex: 'male',
              },
            ],
            projects: [],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/F1/structural-variant-filter-presets') {
        return Promise.resolve({ data: [] });
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
              shared_project_ids: [],
              is_custom: false,
            },
            {
              key: 'excluded',
              label: 'Excluded',
              group: 'collaboration',
              color: '#6b7280',
              sort_order: 20,
              scope: 'system',
              shared_project_ids: [],
              is_custom: false,
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    }),
    put: vi.fn(() =>
      Promise.resolve({
        data: {
          variant_id: 'v1',
          classification: null,
          tags: ['review'],
          tag_metadata: {},
          note: null,
        },
      }),
    ),
  },
}));

describe('FamilyStructuralVariantsPage', () => {
  it('shows filtered and overall variant counts', async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
          <Routes>
            <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    await waitFor(() =>
      expect(screen.getByText(factText(/Showing 1/))).toBeInTheDocument()
    );
    expect(screen.getByText(factText(/All variants 5/))).toBeInTheDocument();
    expect(screen.getByText(/Pedigree/)).toBeInTheDocument();
    expect(screen.getByText(factText(/Tag library 2/))).toBeInTheDocument();
    expect(screen.getByText('1p36.33')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Gene or region')).toBeInTheDocument();
    expect(screen.getByText(/Preset or saved search/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /apply filters/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /review/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /clear all filters/i }).length).toBeGreaterThan(0);
  });

  it('shows the Source column by default, since several callers call one SV under one id', async () => {
    // Each caller's call is a row of its own, with its own evidence and drift (TRACE-11): the
    // table hid the column that tells them apart (TRACE-12).
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
          <Routes>
            <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    await waitFor(() => expect(screen.getByText(factText(/Showing 1/))).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: 'Table' }));
    expect(await screen.findByRole('columnheader', { name: /^Source/ })).toBeInTheDocument();
  });

  it('opens the genome workspaces in a new tab so the filtered list is not re-run on every look', async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
          <Routes>
            <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    await waitFor(() => expect(screen.getByText(factText(/Showing 1/))).toBeInTheDocument());

    // Per-variant loci and the whole-genome workspaces in the header both leave the
    // list behind, so both open a tab rather than replacing it.
    for (const name of [
      // IGV is labelled with the window it opens — the chr1:1-2 call padded by
      // SV_IGV_FLANK_BP either side, clamped to 1 — while the chromosome view names
      // the call itself.
      /open igv at chr1:1-1002 \(opens in a new tab\)/i,
      /open chromosome view around chr1:1-2 \(opens in a new tab\)/i,
      /open the genome overview \(opens in a new tab\)/i,
      /open the circos view \(opens in a new tab\)/i,
    ]) {
      const link = screen.getByRole('link', { name });
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    }
  });

  it('can save a quick review tag without updating count-only cache entries as variant pages', async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
          <Routes>
            <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByText(factText(/Showing 1/))).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole('button', { name: /^review$/i })[0]);

    await waitFor(() => expect(screen.getByText(/Variant review saved/i)).toBeInTheDocument());
    // A tag toggle leaves the CNV (ClinGen) scoring out of the request, so the server keeps
    // the stored one: sent, even as null, it would replace or clear it.
    const [, body] = (api.put as unknown as Mock).mock.calls.at(-1)!;
    expect(JSON.parse(JSON.stringify(body))).not.toHaveProperty('cnv_acmg');
  });

  it('sends a quick tag toggle with the rest of the review: its class, its tags in order, its note', async () => {
    const get = api.get as unknown as Mock;
    const workingGet = get.getMockImplementation()!;
    get.mockImplementation((url: string) =>
      url.startsWith('/families/F1/structural-variants?page=1&page_size=100')
        ? (workingGet(url) as Promise<{ data: { variants: Record<string, unknown>[] } }>).then((res) => ({
            data: {
              ...res.data,
              variants: res.data.variants.map((variant) => ({
                ...variant,
                review: {
                  variant_id: 'v1',
                  // Stored as the bare class word: sent back as the class label.
                  classification: 'likely pathogenic',
                  tags: ['mosaic'],
                  tag_metadata: {},
                  note: 'Breakpoint in intron 10.',
                  updated_at: '2026-10-01T10:00:00Z',
                },
              })),
            },
          }))
        : workingGet(url),
    );
    try {
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
            <Routes>
              <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await waitFor(() => expect(screen.getByText(factText(/Showing 1/))).toBeInTheDocument());
      fireEvent.click(screen.getAllByRole('button', { name: /^review$/i })[0]);
      await waitFor(() => expect(screen.getByText(/Variant review saved/i)).toBeInTheDocument());

      const [path, body] = (api.put as unknown as Mock).mock.calls.at(-1)!;
      expect(path).toBe('/families/F1/structural-variants/v1/review');
      expect(JSON.parse(JSON.stringify(body))).toEqual({
        classification: 'Likely Pathogenic - class 4',
        tags: ['mosaic', 'review'],
        note: 'Breakpoint in intron 10.',
        expected_updated_at: '2026-10-01T10:00:00Z',
      });
    } finally {
      get.mockImplementation(workingGet);
    }
  });

  it('saves a CNV classification naming the caller of the row it was made on (TRACE-11)', async () => {
    const get = api.get as unknown as Mock;
    const workingGet = get.getMockImplementation()!;
    // NeedlR and an upload call one deletion under one id: a row each, one review.
    get.mockImplementation((url: string) =>
      url.startsWith('/families/F1/structural-variants?page=1&page_size=100')
        ? (workingGet(url) as Promise<{ data: { variants: Record<string, unknown>[] } }>).then((res) => ({
            data: {
              ...res.data,
              variants: [
                { ...res.data.variants[0], source: 'needlr', gene_pli: 0.99 },
                { ...res.data.variants[0], source: 'sniffles' },
              ],
              total: 2,
            },
          }))
        : workingGet(url),
    );
    try {
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
            <Routes>
              <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      const uploadCard = (await screen.findByText('Source sniffles')).closest('article') as HTMLElement;
      fireEvent.click(within(uploadCard).getByRole('button', { name: 'ACMG (CNV)' }));
      fireEvent.click(await screen.findByRole('button', { name: /save classification/i }));
      await waitFor(() => expect(screen.getByText(/Variant review saved/i)).toBeInTheDocument());

      const [path, body] = (api.put as unknown as Mock).mock.calls.at(-1)!;
      expect(path).toBe('/families/F1/structural-variants/v1/review');
      const sent = JSON.parse(JSON.stringify(body));
      expect(sent).toMatchObject({ source: 'sniffles', cnv_acmg: { kind: 'loss' } });
      // A small variant's ACMG classification and compound-het review: the SV route takes
      // neither (#853).
      expect(sent).not.toHaveProperty('acmg');
      expect(sent).not.toHaveProperty('compound_het');
    } finally {
      get.mockImplementation(workingGet);
    }
  });

  it("saves the review dialog without a small variant's ACMG or compound-het fields (#853)", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
          <Routes>
            <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(factText(/Showing 1/))).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /tags & notes/i }));
    fireEvent.change(await screen.findByPlaceholderText('Note or rationale'), {
      target: { value: 'Breakpoints checked.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save review/i }));
    await waitFor(() => expect(screen.getByText(/Variant review saved/i)).toBeInTheDocument());

    const [path, body] = (api.put as unknown as Mock).mock.calls.at(-1)!;
    expect(path).toBe('/families/F1/structural-variants/v1/review');
    const sent = JSON.parse(JSON.stringify(body));
    expect(sent).toMatchObject({ note: 'Breakpoints checked.', tags: [] });
    expect(sent).not.toHaveProperty('acmg');
    expect(sent).not.toHaveProperty('compound_het');
  });

  it('searches the region flags left ticked, each as its own parameter', async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
          <Routes>
            <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(factText(/Showing 1/))).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Segdup'));
    fireEvent.click(screen.getByLabelText('Repeat'));
    fireEvent.click(screen.getByLabelText('Segdup'));
    expect(screen.getByLabelText('Segdup')).not.toBeChecked();
    expect(screen.getByLabelText('Repeat')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    const get = api.get as unknown as Mock;
    await waitFor(() =>
      expect(
        get.mock.calls.some(([url]) => String(url).includes('region_flag=Repeat')),
      ).toBe(true),
    );
    const searched = get.mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.includes('region_flag='));
    expect(searched.every((url) => !url.includes('Segdup'))).toBe(true);
  });

  // #725 follow-up — a search that read only part of the callset says so above the table.
  describe('when the search read only part of the callset', () => {
    const get = api.get as unknown as Mock;
    let workingGet: ((url: string) => Promise<unknown>) | undefined;

    beforeEach(() => {
      workingGet = get.getMockImplementation();
    });
    afterEach(() => {
      get.mockImplementation(workingGet!);
    });

    const renderWithSearch = (flags: Record<string, unknown>) => {
      get.mockImplementation((url: string) =>
        url.startsWith('/families/F1/structural-variants?page=1&page_size=100')
          ? (workingGet!(url) as Promise<{ data: Record<string, unknown> }>).then((res) => ({
              data: { ...res.data, ...flags },
            }))
          : workingGet!(url),
      );
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
            <Routes>
              <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    };

    it('says the search stopped after the candidate cap and marks the total a lower bound', async () => {
      renderWithSearch({ total_is_estimated: true, candidates_capped: true, candidate_limit: 50000 });
      const notice = await screen.findByTestId('candidate-cap-notice');
      expect(notice).toHaveTextContent(
        'Results may be incomplete: the search stopped after the first 50,000 candidate SVs',
      );
      expect(notice).toHaveTextContent('Narrow the filters (a region, a gene panel or a gene)');
      expect(screen.getByText(factText('Showing 1+'))).toBeInTheDocument();
      expect(screen.getByText(/Filtered 1\+ of 5 imported SVs/)).toBeInTheDocument();
    });

    it('says a prioritised ranking covered only its window', async () => {
      renderWithSearch({ total_is_estimated: true, ranking_truncated: true, candidate_limit: 5000 });
      expect(await screen.findByTestId('candidate-cap-notice')).toHaveTextContent(
        'Ranking may be incomplete: the prioritizer ranked only the first 5,000 candidate SVs',
      );
    });

    it('shows no notice for a complete search', async () => {
      renderWithSearch({});
      await waitFor(() => expect(screen.getByText(factText('Showing 1'))).toBeInTheDocument());
      expect(screen.queryByTestId('candidate-cap-notice')).not.toBeInTheDocument();
    });
  });

  // #606 — a failed request is said as such: the search read as a family without SVs.
  describe('when a request fails', () => {
    const get = api.get as unknown as Mock;
    let workingGet: ((url: string) => Promise<unknown>) | undefined;
    const serverError = () => Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));

    beforeEach(() => {
      workingGet = get.getMockImplementation();
    });
    afterEach(() => {
      get.mockImplementation(workingGet!);
    });

    /** The working API, with the matching requests failing until `recover` is called. */
    const failing = (matches: (url: string) => boolean) => {
      let failed = true;
      get.mockImplementation((url: string) => (failed && matches(url) ? serverError() : workingGet!(url)));
      return {
        recover: () => {
          failed = false;
        },
      };
    };

    const renderAt = (path = '/families/F1/structural-variants') =>
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

    it('says a failed search failed, and runs it again on retry', async () => {
      const search = failing((url) => url.startsWith('/families/F1/structural-variants?page=1&page_size=100'));
      const searches = () =>
        get.mock.calls.filter(([url]) => String(url).startsWith('/families/F1/structural-variants?page=1&page_size=100'))
          .length;
      const earlier = searches();
      renderAt();

      // The page runs a search before its default filters apply, and again after: wait
      // for the default search to have failed too, so the page is settled.
      await waitFor(() => expect(searches() - earlier).toBeGreaterThanOrEqual(2));
      const failure = await screen.findByText(/Could not load the structural variants — this is not an empty result/);
      expect(screen.getByText(factText('Showing —'))).toBeInTheDocument();
      expect(screen.queryByText('1p36.33')).not.toBeInTheDocument();

      search.recover();
      fireEvent.click(within(failure).getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText('1p36.33')).toBeInTheDocument();
      expect(screen.getByText(factText('Showing 1'))).toBeInTheDocument();
    });

    it('does not show the filtered count as the total when the total could not be loaded', async () => {
      failing((url) => url === '/families/F1/structural-variants?page=1&page_size=1');
      renderAt();

      expect(await screen.findByText(/Filtered 1 SVs; the number imported could not be loaded/)).toBeInTheDocument();
      expect(screen.getByText(factText('All variants —'))).toBeInTheDocument();
    });

    // #604 — a location that reads as neither a gene nor a region is not searched as a
    // gene name: from the form it is named under its field, from a URL it fails the search.
    it('names an unreadable location under its field instead of searching it', async () => {
      renderAt();
      await waitFor(() => expect(screen.getByText(factText('Showing 1'))).toBeInTheDocument());
      const searchesBefore = get.mock.calls.length;

      fireEvent.change(screen.getByPlaceholderText('Gene or region'), { target: { value: 'chr1:100-' } });
      fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

      expect(await screen.findByText(/Location 'chr1:100-' is not a gene or chr:start-end\. Nothing was searched\./)).toBeInTheDocument();
      expect(get.mock.calls.slice(searchesBefore).some(([url]) => String(url).includes('gene=chr1'))).toBe(false);
    });

    it('fails a search whose URL carries an unreadable location', async () => {
      renderAt('/families/F1/structural-variants?page=1&locus=chr1%3A100-');

      expect(
        await screen.findByText(/Could not load the structural variants — this is not an empty result\. Location 'chr1:100-' is not a gene or chr:start-end/),
      ).toBeInTheDocument();
      expect(get.mock.calls.some(([url]) => String(url).includes('gene=chr1'))).toBe(false);
    });

    it('keeps an applied panel visible when the panel list could not be loaded', async () => {
      failing((url) => url === '/panels');
      renderAt('/families/F1/structural-variants?page=1&panel_id=P1');

      expect(await screen.findByText(/Could not load the gene panel list — this is not an empty result/)).toHaveTextContent(
        /Panels cannot be chosen, and the default Mendeliome scope is not applied/,
      );
      const panelSelect = screen.getByRole('combobox', { name: /panel/i });
      expect(panelSelect).toHaveValue('P1');
      expect(within(panelSelect).getByRole('option', { name: 'Panel P1 (not in the panel list)' })).toBeInTheDocument();
    });

    it('names the applied panel on its chip, not the one chosen in the form (CLIN-21)', async () => {
      get.mockImplementation((url: string) =>
        url === '/panels'
          ? Promise.resolve({ data: [{ _id: 'PA', name: 'Panel A' }, { _id: 'PB', name: 'Panel B' }] })
          : workingGet!(url),
      );
      renderAt('/families/F1/structural-variants?page=1&panel_id=PA');

      expect(await screen.findByText('Gene panel: Panel A')).toBeInTheDocument();
      fireEvent.change(screen.getByRole('combobox', { name: /panel/i }), { target: { value: 'PB' } });

      // The results are filtered by Panel A until the form is applied; the chip said Panel B.
      expect(screen.getByText('Gene panel: Panel A')).toBeInTheDocument();
      expect(screen.queryByText('Gene panel: Panel B')).not.toBeInTheDocument();
    });
  });

  // SAFE-14 — the family record holds what the header warns of: an import that left the
  // family incomplete, and an assembly outside the validated scope. The SVs were shown
  // without those warnings while the record loaded, and when it could not be loaded.
  describe('without the family record', () => {
    const get = api.get as unknown as Mock;
    let workingGet: ((url: string) => Promise<unknown>) | undefined;

    beforeEach(() => {
      workingGet = get.getMockImplementation();
    });
    afterEach(() => {
      get.mockImplementation(workingGet!);
    });

    const renderPage = () =>
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/families/F1/structural-variants']}>
            <Routes>
              <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

    it('shows none of the SVs before the family, and then its reference, are in', async () => {
      const family = held();
      const reference = held();
      get.mockImplementation((url: string) => {
        if (url === '/families/F1') {
          // The family on project p1, whose assembly is outside the validated scope.
          return family.answered
            .then(() => workingGet!(url) as Promise<{ data: Record<string, unknown> }>)
            .then(({ data }) => ({ data: { ...data, projects: ['p1'] } }));
        }
        if (url === '/projects') return reference.answered.then(() => ({ data: [offScopeProject('p1')] }));
        return workingGet!(url);
      });
      renderPage();

      // The SVs are in meanwhile: the page still says it is loading.
      await settle(get);
      expect(screen.getByRole('status', { name: 'Loading structural variants' })).toBeInTheDocument();
      expect(screen.queryByText('1p36.33')).not.toBeInTheDocument();

      // The family alone is not enough: without its reference, the scope warning is unknown.
      family.release();
      await settle(get);
      expect(screen.getByRole('status', { name: 'Loading structural variants' })).toBeInTheDocument();
      expect(screen.queryByText('1p36.33')).not.toBeInTheDocument();

      reference.release();
      expect(await screen.findByText('1p36.33')).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'Family F1' })).toBeInTheDocument();
      await findAssemblyScopeBanner();
    });

    it('reads the list and its total in the page’s project, as the export does (UI-5)', async () => {
      // A family of two projects on two assemblies: the project picks the assembly, and a
      // list without one answered 400 where the export, which sent it, worked.
      get.mockImplementation((url: string) => {
        if (url === '/families/F1') {
          return (workingGet!(url) as Promise<{ data: Record<string, unknown> }>).then(({ data }) => ({
            data: { ...data, projects: ['p1', 'p2'] },
          }));
        }
        if (url === '/projects') {
          return Promise.resolve({
            data: [
              offScopeProject('p1'),
              { ...offScopeProject('p2'), assembly_id: 'assembly-38', assembly_name: 'GRCh38', assembly_validated: true },
            ],
          });
        }
        return workingGet!(url);
      });
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/families/F1/structural-variants?project_id=p2']}>
            <Routes>
              <Route path="/families/:familyId/structural-variants" element={<FamilyStructuralVariantsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      expect(await screen.findByText('1p36.33')).toBeInTheDocument();
      const read = get.mock.calls
        .map(([url]) => String(url))
        .filter((url) => url.startsWith('/families/F1/structural-variants?'));
      // The header's check whether the family has SVs reads apart from the page's list.
      const searched = read.filter((url) => !url.includes('count_only=true'));
      expect(searched.length).toBe(2); // the list and its total, each once
      // Each in the page's project: none ran without it first, the header's check neither.
      expect(searched.map((url) => new URLSearchParams(url.split('?')[1]).get('project_id'))).toEqual(['p2', 'p2']);
      await waitFor(() =>
        expect(
          get.mock.calls
            .map(([url]) => String(url))
            .filter((url) => url.startsWith('/families/F1/structural-variants?') && url.includes('count_only=true'))
            .map((url) => new URLSearchParams(url.split('?')[1]).get('project_id')),
        ).toEqual(['p2']),
      );
    });

    it('says the family could not be loaded, shows none of the SVs, and loads them on retry', async () => {
      let familyFails = true;
      get.mockImplementation((url: string) =>
        url === '/families/F1' && familyFails
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
          : workingGet!(url),
      );
      renderPage();

      const failure = (await screen.findByRole('heading', { name: 'Family could not be loaded' })).closest(
        '.page-state',
      ) as HTMLElement;
      // The SVs are in meanwhile, and not shown.
      await settle(get);
      expect(failure).toBeInTheDocument();
      expect(failure).toHaveTextContent('This is a failed request, not a missing family.');
      expect(screen.queryByText('1p36.33')).not.toBeInTheDocument();
      expect(screen.queryByText(factText(/^Showing/))).not.toBeInTheDocument();

      familyFails = false;
      fireEvent.click(within(failure).getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText('1p36.33')).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'Family F1' })).toBeInTheDocument();
    });
  });
});
