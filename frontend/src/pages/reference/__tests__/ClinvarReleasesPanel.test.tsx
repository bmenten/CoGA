import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import ClinvarReleasesPanel from '../ClinvarReleasesPanel';
import api from '../../../lib/api';
import type { ClinvarLoadJobOut, ClinvarLoadStatusOut } from '../../../lib/apiSchema.generated';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const RELEASE = {
  release: '2026-11-22',
  records: 3_012_345,
  source_url: 'https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar_20261122.vcf.gz',
  source_version: '2026-11-22 (clinvar_20261122.vcf.gz, SHA-256 abc)',
  loaded_at: '2026-11-23T04:00:00Z',
  loaded_by: 'startup-bootstrap',
};

const job = (id: string, status: ClinvarLoadJobOut['status'], extra: Partial<ClinvarLoadJobOut> = {}): ClinvarLoadJobOut => ({
  _id: id,
  assembly_id: 'assembly-1',
  assembly_name: 'GRCh38',
  release: '2026-11-29',
  source_url: null,
  source_md5: null,
  source_sha256: null,
  status,
  requested_by: 'lab.admin@example.org',
  requested_at: '2026-11-30T10:00:00Z',
  started_at: '2026-11-30T10:00:01Z',
  completed_at: status === 'running' ? null : '2026-11-30T10:06:00Z',
  bytes_read: 0,
  bytes_total: null,
  records_read: 0,
  records_stored: 0,
  records_skipped: {},
  error: null,
  ...extra,
});

const serve = (jobs: ClinvarLoadJobOut[], assemblies: ClinvarLoadStatusOut['assemblies']) => {
  (api.get as unknown as Mock).mockImplementation((url: string) =>
    url === '/admin/clinvar/status'
      ? Promise.resolve({
          data: {
            active_job: jobs.find((entry) => entry.status === 'queued' || entry.status === 'running') ?? null,
            recent_jobs: jobs,
            assemblies,
          },
        })
      : Promise.reject(new Error(`unexpected ${url}`)),
  );
};

const renderPanel = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <ClinvarReleasesPanel />
    </QueryClientProvider>,
  );

describe('ClinvarReleasesPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('shows the current and the previous release of each assembly', async () => {
    serve([], [
      { assembly_name: 'GRCh38', current: RELEASE, previous: { ...RELEASE, release: '2026-11-15', records: 3_011_000 } },
      { assembly_name: 'GRCh37', current: null, previous: null },
    ]);
    renderPanel();

    const panel = await screen.findByRole('region', { name: 'ClinVar releases' });
    const [, grch38, grch37] = within(panel).getAllByRole('row');
    expect(grch38).toHaveTextContent('2026-11-22 · 3,012,345 records');
    expect(grch38).toHaveTextContent('2026-11-15 · 3,011,000 records');
    expect(grch37).toHaveTextContent('GRCh37——');
  });

  it('loads the newest release for an assembly once confirmed', async () => {
    serve([], [{ assembly_name: 'GRCh38', current: RELEASE, previous: null }]);
    (api.post as unknown as Mock).mockResolvedValue({ data: job('job-1', 'queued') });
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Load the newest ClinVar release for GRCh38' }));
    const prompt = await screen.findByText(/Load ClinVar's newest weekly release for GRCh38\?/);
    expect(prompt).toHaveTextContent('checks its MD5, its release date and its assembly');
    expect(prompt).toHaveTextContent('no more than 5 % fewer records than the current release');
    expect(api.post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/clinvar/load', { assembly: 'GRCh38' }));
    expect(await screen.findByText('Started the ClinVar load for GRCh38.')).toBeInTheDocument();
  });

  it('shows how far a running load has come, and offers no second one', async () => {
    serve(
      [job('job-1', 'running', { records_read: 1_200_000, records_stored: 1_199_000 })],
      [{ assembly_name: 'GRCh38', current: RELEASE, previous: null }],
    );
    renderPanel();

    expect(await screen.findByLabelText('ClinVar load in progress')).toHaveTextContent(
      'Loading ClinVar for GRCh38, release 2026-11-29: 1,199,000 records stored. The current release stays ' +
        'until the whole file is stored and checked.',
    );
    expect(screen.getByRole('button', { name: 'Load the newest ClinVar release for GRCh38' })).toBeDisabled();
  });

  it('says that the newest load of an assembly failed, and why', async () => {
    const reason = 'ClinVar 2026-11-29 has 2,000 records, more than 5% fewer than the current release.';
    serve(
      [job('job-2', 'failed', { error: reason }), job('job-1', 'completed')],
      [{ assembly_name: 'GRCh38', current: RELEASE, previous: null }],
    );
    renderPanel();

    expect(await screen.findByLabelText('Failed ClinVar load for GRCh38')).toHaveTextContent(reason);
  });

  it('shows nothing where no assembly has a ClinVar VCF', async () => {
    serve([], []);
    renderPanel();

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/admin/clinvar/status'));
    expect(screen.queryByRole('region', { name: 'ClinVar releases' })).not.toBeInTheDocument();
  });
});
