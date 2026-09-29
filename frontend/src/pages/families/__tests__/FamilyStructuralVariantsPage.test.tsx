import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import FamilyStructuralVariantsPage from '../FamilyStructuralVariantsPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

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
      expect(screen.getByText(/Showing 1/)).toBeInTheDocument()
    );
    expect(screen.getByText(/All variants 5/)).toBeInTheDocument();
    expect(screen.getByText(/Pedigree/)).toBeInTheDocument();
    expect(screen.getByText(/Tag library 2/)).toBeInTheDocument();
    expect(screen.getByText('1p36.33')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Gene or region')).toBeInTheDocument();
    expect(screen.getByText(/Preset or saved search/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /apply filters/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /review/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /clear all filters/i }).length).toBeGreaterThan(0);
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
    await waitFor(() => expect(screen.getByText(/Showing 1/)).toBeInTheDocument());

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

    await waitFor(() => expect(screen.getByText(/Showing 1/)).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole('button', { name: /^review$/i })[0]);

    await waitFor(() => expect(screen.getByText(/Variant review saved/i)).toBeInTheDocument());
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
      expect(screen.getByText('Showing —')).toBeInTheDocument();
      expect(screen.queryByText('1p36.33')).not.toBeInTheDocument();

      search.recover();
      fireEvent.click(within(failure).getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText('1p36.33')).toBeInTheDocument();
      expect(screen.getByText('Showing 1')).toBeInTheDocument();
    });

    it('does not show the filtered count as the total when the total could not be loaded', async () => {
      failing((url) => url === '/families/F1/structural-variants?page=1&page_size=1');
      renderAt();

      expect(await screen.findByText(/Filtered 1 SVs; the number imported could not be loaded/)).toBeInTheDocument();
      expect(screen.getByText('All variants —')).toBeInTheDocument();
    });

    // #604 — a location that reads as neither a gene nor a region is not searched as a
    // gene name: from the form it is named under its field, from a URL it fails the search.
    it('names an unreadable location under its field instead of searching it', async () => {
      renderAt();
      await waitFor(() => expect(screen.getByText('Showing 1')).toBeInTheDocument());
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
  });
});
