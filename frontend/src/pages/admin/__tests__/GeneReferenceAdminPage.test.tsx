import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import GeneReferenceAdminPage from '../GeneReferenceAdminPage';
import api from '../../../lib/api';
import { formatCount } from '../../../lib/format';
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
        <GeneReferenceAdminPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

/** A refresh job: by default the completed bulk refresh of all human genes. */
const job = (overrides: Record<string, unknown> = {}) => ({
  _id: 'job-1',
  scope: 'all_human',
  symbol: null,
  status: 'completed',
  requested_by: 'admin@example.com',
  requested_at: '2026-03-27T12:00:00Z',
  started_at: '2026-03-27T12:00:05Z',
  heartbeat_at: '2026-03-27T12:01:00Z',
  completed_at: '2026-03-27T12:20:00Z',
  total_symbols: 20000,
  completed_symbols: 20000,
  updated_records: 42000,
  human_assemblies: 2,
  current_symbol: null,
  error: null,
  metadata: {},
  ...overrides,
});

/** What /admin/gene-reference/status serves, with `overrides`. */
const servedStatus = (overrides: Record<string, unknown> = {}) => ({
  data: {
    active_job: null,
    recent_jobs: [job()],
    source_summaries: [
      {
        source: 'hgnc',
        latest_fetched_at: '2026-03-27T12:00:00Z',
        success_count: 18000,
        missing_count: 1000,
        error_count: 100,
        record_count: 19100,
      },
    ],
    total_cached_records: 38200,
    human_gene_symbols: 19100,
    human_assemblies: 2,
    last_completed_at: '2026-03-27T10:00:00Z',
    ...overrides,
  },
});

const statusReads = () =>
  vi.mocked(api.get).mock.calls.filter(([url]) => url === '/admin/gene-reference/status').length;

describe('GeneReferenceAdminPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.get).mockResolvedValue(servedStatus());
  });

  it('names the sources the sync reads, and not Ensembl', async () => {
    renderPage();

    const lead = await screen.findByText(/cached human gene context/i);
    for (const source of [
      'HGNC',
      'ClinGen',
      'GenCC',
      'ClinVar',
      'dbNSFP',
      'ACMG secondary-findings list',
      'NCBI Gene',
    ]) {
      expect(lead).toHaveTextContent(source);
    }
    // The sync no longer calls Ensembl: NCBI is its only per-gene request.
    expect(lead).not.toHaveTextContent(/ensembl/i);
  });

  it('renders status and starts a single-gene refresh job', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: {
        _id: 'job-2',
        scope: 'symbol',
        symbol: 'BRCA1',
        status: 'queued',
        requested_by: 'admin@example.com',
        requested_at: '2026-03-27T12:30:00Z',
        total_symbols: 0,
        completed_symbols: 0,
        updated_records: 0,
        human_assemblies: 0,
        metadata: {},
      },
    });

    renderPage();

    expect(await screen.findByText(/cached source coverage/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /gene reference sync/i })).toBeInTheDocument();
    expect(screen.getByText(/no refresh job is active at the moment/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /refresh gene/i })).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/refresh gene symbol/i), { target: { value: 'brca1' } });
    fireEvent.click(screen.getByRole('button', { name: /refresh gene/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/admin/gene-reference/refresh-gene',
        null,
        expect.objectContaining({
          params: { symbol: 'BRCA1' },
        }),
      ),
    );
    expect(await screen.findByText('Started gene refresh job for BRCA1.')).toBeInTheDocument();
    expect(screen.getByLabelText(/refresh gene symbol/i)).toHaveValue('');
    await waitFor(() => expect(statusReads()).toBe(2));
  });

  it('starts a bulk refresh of every human gene', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: job({ _id: 'job-3', status: 'queued' }) });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Refresh all human genes' }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/gene-reference/refresh-all'));
    expect(await screen.findByText('Started bulk refresh job job-3.')).toBeInTheDocument();
    await waitFor(() => expect(statusReads()).toBe(2));
  });

  // While a refresh runs, the page follows its progress and starts no other.
  it.each([
    [
      'a bulk refresh',
      job({ status: 'running', completed_symbols: 5000, current_symbol: 'BRCA2', error: 'NCBI answered 429; retrying.' }),
      'All imported human genes',
      '25%',
      `${formatCount(5000)} / ${formatCount(20000)} genes`,
      'Current gene: BRCA2',
    ],
    [
      'a queued single-gene refresh',
      job({ scope: 'symbol', symbol: 'TP53', status: 'queued', total_symbols: 0, completed_symbols: 0 }),
      'TP53',
      '0%',
      '0 / 0 genes',
      'Waiting for the next update tick.',
    ],
  ])('shows the progress of %s, and starts no other refresh', async (_case, active, target, percent, genes, current) => {
    vi.mocked(api.get).mockResolvedValue(servedStatus({ active_job: active }));
    renderPage();

    expect(await screen.findByText(target)).toBeInTheDocument();
    expect(screen.getByText(percent)).toBeInTheDocument();
    expect(screen.getByText(genes)).toBeInTheDocument();
    expect(screen.getByText(current)).toBeInTheDocument();
    expect(screen.getByText(/Requested by admin@example.com/)).toBeInTheDocument();
    if ((active as { error: string | null }).error) {
      expect(screen.getByText('NCBI answered 429; retrying.')).toHaveClass('status-note--error');
    }

    fireEvent.change(screen.getByLabelText(/refresh gene symbol/i), { target: { value: 'brca1' } });
    expect(screen.getByRole('button', { name: /refresh gene$/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Refresh all human genes' })).toBeDisabled();
  });

  it.each([
    ['bulk', () => fireEvent.click(screen.getByRole('button', { name: 'Refresh all human genes' })), 'Could not start the bulk gene reference refresh.'],
    [
      'single-gene',
      () => {
        fireEvent.change(screen.getByLabelText(/refresh gene symbol/i), { target: { value: 'BRCA1' } });
        fireEvent.click(screen.getByRole('button', { name: /refresh gene$/i }));
      },
      'Could not start the single-gene refresh.',
    ],
  ])('says why a refused %s refresh failed', async (_scope, start, fallback) => {
    vi.mocked(api.post)
      .mockRejectedValueOnce({ response: { status: 409, data: { detail: 'A refresh job is already running.' } } })
      .mockRejectedValueOnce({});
    renderPage();
    await screen.findByText(/cached source coverage/i);

    start();
    expect(await screen.findByText('A refresh job is already running.')).toHaveClass('status-note--error');
    start();
    expect(await screen.findByText(fallback)).toHaveClass('status-note--error');
  });

  it('says the sync status could not be loaded', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 500, data: { detail: 'Database unavailable.' } } });
    renderPage();

    expect(await screen.findByText('Could not load gene reference sync')).toBeInTheDocument();
    expect(screen.getByText('Database unavailable.')).toBeInTheDocument();
  });

  // A source whose cached genes come from more than one release is flagged: a full refresh
  // brings them onto one.
  it('flags a source answered by several releases, and marks one with no release', async () => {
    vi.mocked(api.get).mockResolvedValue(
      servedStatus({
        source_summaries: [
          { source: 'clinvar', release: '2026-03', release_count: 2, latest_fetched_at: null, success_count: 1, missing_count: 0, not_consulted_count: 0, error_count: 0, record_count: 1 },
          { source: 'gencc', release: null, release_count: 1, latest_fetched_at: null, success_count: 1, missing_count: 0, not_consulted_count: 0, error_count: 0, record_count: 1 },
        ],
      }),
    );
    renderPage();

    const clinvar = (await screen.findByText('CLINVAR')).closest('tr') as HTMLElement;
    expect(within(clinvar).getByText('2 releases')).toHaveClass('badge-chip--danger');
    const gencc = screen.getByText('GENCC').closest('tr') as HTMLElement;
    expect(within(gencc).getAllByRole('cell')[1]).toHaveTextContent(/^—$/);
    expect(within(gencc).queryByText(/releases/)).not.toBeInTheDocument();
  });

  it('lists the six latest jobs, and all of them on request', async () => {
    const jobs = Array.from({ length: 8 }, (_, index) =>
      job({ _id: `job-${index}`, scope: 'symbol', symbol: `GENE${index}` }),
    );
    vi.mocked(api.get).mockResolvedValue(servedStatus({ recent_jobs: jobs }));
    renderPage();

    expect(await screen.findByText('GENE5')).toBeInTheDocument();
    expect(screen.queryByText('GENE6')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all 8' }));
    expect(screen.getByText('GENE7')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }));
    expect(screen.queryByText('GENE7')).not.toBeInTheDocument();
  });
});
