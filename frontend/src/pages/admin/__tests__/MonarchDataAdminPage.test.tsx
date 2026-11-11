import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MonarchDataAdminPage from '../MonarchDataAdminPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const renderPage = () => {
  const queryClient = createTestQueryClient();

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <MonarchDataAdminPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const statusPayload = {
  release_version: '2026-03-01',
  gene_disease_pairs: 13200,
  genes: 5463,
  diseases: 8900,
  causal_pairs: 7200,
  disease_phenotype_pairs: 245814,
  phenotype_diseases: 11230,
  phenotypes: 9000,
  last_updated_at: '2026-03-27T10:00:00Z',
};

/** The summary POST /admin/monarch/refresh serves for release 2026-04-01, with `overrides`. */
const refreshSummary = (overrides: Record<string, unknown> = {}) => ({
  data: {
    release_version: '2026-04-01',
    files_loaded: 4,
    gene_disease_pairs: 13300,
    genes: 5470,
    diseases: 8910,
    causal_pairs: 7250,
    disease_phenotype_pairs: 246000,
    phenotype_diseases: 11240,
    phenotypes: 9010,
    excluded_phenotype_pairs: 0,
    completed_at: '2026-04-15T12:00:00Z',
    duration_seconds: 2.5,
    ...overrides,
  },
});

const HUNTINGTON = {
  mondo_id: 'MONDO:0007739',
  disease_label: 'Huntington disease',
  match_type: 'disease',
  gene_count: 1,
  genes: [{ gene_symbol: 'HTT', hgnc_id: 'HGNC:4851', predicate: 'causes', causal: true }],
  phenotype_count: 1,
  matched_phenotype_count: 1,
  phenotypes: [{ hpo_id: 'HP:0002072', phenotype_label: 'Chorea', matched: true }],
};

/** Serves the status at /admin/monarch/status, and `search` (its data, or an error) at the search. */
const serveSearch = (search: unknown) =>
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (url === '/admin/monarch/search') {
      return search instanceof Error ? Promise.reject(search) : Promise.resolve({ data: search });
    }
    return Promise.resolve({ data: statusPayload });
  });

const searchCalls = () => vi.mocked(api.get).mock.calls.filter(([url]) => url === '/admin/monarch/search');
const searchBox = () => screen.getByPlaceholderText(/MONDO:0007739/i);
/** The panel of the selected disease. */
const diseasePanel = () =>
  screen.getByRole('heading', { level: 4, name: 'Linked genes' }).closest('aside') as HTMLElement;

describe('MonarchDataAdminPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.get).mockResolvedValue({ data: statusPayload });
  });

  it('renders the loaded release and triggers a refresh', async () => {
    vi.mocked(api.post).mockResolvedValue(refreshSummary());

    renderPage();

    expect(await screen.findByText('2026-03-01')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Monarch data' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /update monarch data/i }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/monarch/refresh'));
    expect(await screen.findByText(/updated to monarch release 2026-04-01/i)).toBeInTheDocument();
    // A summary from before the Mendeliome outcome was reported is a plain success.
    expect(screen.getByRole('status')).toHaveClass('status-note--success');
    expect(screen.queryByText(/not regenerated/)).not.toBeInTheDocument();
    // The loaded release is read again after the refresh.
    await waitFor(() =>
      expect(vi.mocked(api.get).mock.calls.filter(([url]) => url === '/admin/monarch/status')).toHaveLength(2),
    );
  });

  it.each([
    [
      'with the reason it failed',
      'RuntimeError',
      /The Mendeliome panel was not regenerated \(RuntimeError\) — it still reflects the previous release/,
    ],
    ['without a reason', undefined, /The Mendeliome panel was not regenerated — it still reflects the previous release/],
  ])('warns when the Mendeliome was not regenerated after the refresh, %s', async (_case, reason, warning) => {
    vi.mocked(api.post).mockResolvedValue(
      refreshSummary({ mendeliome_regenerated: false, ...(reason ? { mendeliome_error: reason } : {}) }),
    );

    renderPage();
    await screen.findByText('2026-03-01');
    fireEvent.click(screen.getByRole('button', { name: /update monarch data/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/updated to monarch release 2026-04-01/i);
    expect(alert).toHaveTextContent(warning);
    expect(alert).toHaveClass('status-note--warning');
  });

  it('says why a refused refresh failed, and keeps showing the loaded release', async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { status: 502, data: { detail: 'Monarch download failed.' } } });

    renderPage();
    await screen.findByText('2026-03-01');
    fireEvent.click(screen.getByRole('button', { name: /update monarch data/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Monarch download failed.');
    expect(alert).toHaveClass('status-note--error');
    expect(screen.getByText('2026-03-01')).toBeInTheDocument();
  });

  it('offers an import, not an update, while no Monarch data are loaded', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { ...statusPayload, release_version: null, gene_disease_pairs: 0, disease_phenotype_pairs: 0 },
    });

    renderPage();

    expect(await screen.findByRole('button', { name: 'Import Monarch data' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /update monarch data/i })).not.toBeInTheDocument();
  });

  it('says the Monarch status could not be loaded', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 500, data: { detail: 'Database unavailable.' } } });

    renderPage();

    expect(await screen.findByText('Could not load Monarch data')).toBeInTheDocument();
    expect(screen.getByText('Database unavailable.')).toBeInTheDocument();
  });

  it('searches diseases and shows linked genes and phenotypes', async () => {
    serveSearch({
      query: 'huntington',
      total: 1,
      diseases: [HUNTINGTON],
      gene_overview: {
        total: 1,
        genes: [{ gene_symbol: 'HTT', hgnc_id: 'HGNC:4851', causal: true, disease_count: 1 }],
      },
    });

    renderPage();

    await screen.findByText('2026-03-01');
    fireEvent.change(searchBox(), {
      target: { value: 'huntington' },
    });

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/admin/monarch/search', expect.objectContaining({
        params: { q: 'huntington', limit: 25 },
      }))
    );

    expect(await screen.findByText('Linked genes across all matches')).toBeInTheDocument();
    // HTT appears both in the aggregated overview chip and the per-disease detail.
    expect((await screen.findAllByText('HTT')).length).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText(/Chorea/)).toBeInTheDocument();
    expect(screen.getAllByText('Huntington disease').length).toBeGreaterThan(0);
  });

  // Enter searches at once, without the typing delay; Enter on the term already searched
  // searches it again (the knowledgebase may have been refreshed since), and an empty term is
  // no search.
  it('searches at once on Enter, again on Enter for the same term, and not for an empty term', async () => {
    serveSearch({ query: 'huntington', total: 1, diseases: [HUNTINGTON], gene_overview: { total: 0, genes: [] } });
    renderPage();
    await screen.findByText('2026-03-01');

    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    fireEvent.keyDown(searchBox(), { key: 'Enter' });
    expect(searchCalls()).toHaveLength(0);

    fireEvent.change(searchBox(), { target: { value: ' huntington ' } });
    fireEvent.keyDown(searchBox(), { key: 'Enter' });
    await waitFor(() => expect(searchCalls()).toHaveLength(1));
    expect(searchCalls()[0][1]).toMatchObject({ params: { q: 'huntington', limit: 25 } });
    await within(await screen.findByRole('complementary')).findByText('Huntington disease');

    fireEvent.keyDown(searchBox(), { key: 'Enter' });
    await waitFor(() => expect(searchCalls()).toHaveLength(2));
    // A key other than Enter does not search.
    fireEvent.keyDown(searchBox(), { key: 'a' });
    expect(searchCalls()).toHaveLength(2);
  });

  it('shows how many matches are listed, and the selected disease’s genes and phenotypes', async () => {
    serveSearch({
      query: 'chorea',
      total: 30,
      diseases: [
        {
          ...HUNTINGTON,
          match_type: 'both',
          genes: [{ gene_symbol: 'HTT', hgnc_id: 'HGNC:4851', predicate: 'contributes_to', causal: false }],
          phenotype_count: 5,
        },
        {
          mondo_id: 'MONDO:0000001',
          disease_label: null,
          match_type: 'phenotype',
          gene_count: 0,
          genes: [],
          phenotype_count: 0,
          matched_phenotype_count: 0,
          phenotypes: [],
        },
      ],
      gene_overview: {
        total: 40,
        genes: [
          { gene_symbol: 'HTT', hgnc_id: 'HGNC:4851', causal: false, disease_count: 2 },
          { gene_symbol: 'JPH3', hgnc_id: 'HGNC:14203', causal: true, disease_count: 1 },
        ],
      },
    });
    renderPage();
    await screen.findByText('2026-03-01');
    fireEvent.change(searchBox(), { target: { value: 'chorea' } });
    fireEvent.keyDown(searchBox(), { key: 'Enter' });

    expect(await screen.findByText('2 of 30 shown')).toBeInTheDocument();
    expect(screen.getByText('2 of 40 genes')).toBeInTheDocument();
    expect(screen.getByText('Showing 2 of 40 linked genes.')).toBeInTheDocument();
    expect(screen.getByText('JPH3').closest('li')).toHaveAttribute('title', 'HGNC:14203 · causal · 1 disease');
    expect(screen.getAllByText('HTT')[0].closest('li')).toHaveAttribute('title', 'HGNC:4851 · 2 diseases');

    // The first match is selected.
    expect(within(diseasePanel()).getByRole('heading', { level: 3 })).toHaveTextContent('Huntington disease');
    expect(within(diseasePanel()).getByText('Disease & phenotype')).toBeInTheDocument();
    expect(within(diseasePanel()).getByText('contributes to')).toBeInTheDocument();
    expect(within(diseasePanel()).queryByText('causal')).not.toBeInTheDocument();
    expect(within(diseasePanel()).getByText('Expected phenotypes — 1 matched')).toBeInTheDocument();
    expect(within(diseasePanel()).getByText('Showing 1 of 5 phenotypes.')).toBeInTheDocument();

    fireEvent.click(screen.getByText('MONDO:0000001'));
    expect(within(diseasePanel()).getByRole('heading', { level: 3 })).toHaveTextContent('Unnamed disease');
    expect(within(diseasePanel()).getByText('No genes are linked to this disease in Monarch.')).toBeInTheDocument();
    expect(within(diseasePanel()).getByText('No expected phenotypes are stored for this disease.')).toBeInTheDocument();
    expect(within(diseasePanel()).getByRole('heading', { level: 4, name: 'Expected phenotypes' })).toBeInTheDocument();
  });

  it.each([
    [
      'says nothing matches',
      { query: 'none', total: 0, diseases: [], gene_overview: { total: 0, genes: [] } },
      'No diseases or phenotypes match this search.',
    ],
    [
      'says why the search failed',
      Object.assign(new Error('HTTP 500'), { response: { status: 500, data: { detail: 'Search index unavailable.' } } }),
      'Search index unavailable.',
    ],
  ])('%s, and selects no disease', async (_case, search, message) => {
    serveSearch(search);
    renderPage();
    await screen.findByText('2026-03-01');
    fireEvent.change(searchBox(), { target: { value: 'none' } });
    fireEvent.keyDown(searchBox(), { key: 'Enter' });

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByText('Select a disease to view its genes and phenotypes.')).toBeInTheDocument();
    expect(screen.queryByText('Linked genes across all matches')).not.toBeInTheDocument();
  });
});
