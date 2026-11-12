import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import { AUTH_STORAGE_KEYS } from '../../../lib/auth';
import { storage } from '../../../lib/storage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { useFactoryGet } from '../../../test/factoryGet';
import PackageImportPage from '../PackageImportPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn((url: string) => {
      if (url === '/projects') {
        return Promise.resolve({
          data: [
            {
              id: '11111111-1111-1111-1111-111111111111',
              name: 'Accessible Project',
              families: [],
              samples: [],
            },
          ],
        });
      }
      if (url === '/family-imports/job-1') {
        return Promise.resolve({
          data: {
            _id: 'job-1',
            submitted_path: '/data/FAM-100',
            family_id: 'FAM-100',
            status: 'completed',
            dry_run: true,
            requested_by: 'admin@example.com',
            requested_at: '2026-04-29T12:00:00Z',
            heartbeat_at: '2026-04-29T12:00:01Z',
            validation_errors: [],
            validation_warnings: [],
            logs: ['Dry run completed successfully; no data were imported.'],
            datasets: [
              {
                dataset_type: 'snv',
                enabled: false,
                status: 'skipped',
                files: [],
                samples: [],
                summary: {},
              },
            ],
          },
        });
      }
      if (url === '/family-imports/packages') {
        return Promise.resolve({
          data: [
            {
              folder_path: '/data/families/FAM_NIPT_DEMO',
              name: 'FAM_NIPT_DEMO',
              family_id: 'FAM_NIPT_DEMO',
              has_ped: true,
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    }),
    post: vi.fn(),
  },
}));

const renderPage = () => {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PackageImportPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('PackageImportPage', () => {
  const { get, factory } = useFactoryGet(api.get);

  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    storage.clear();
    storage.setItem(AUTH_STORAGE_KEYS.role, 'admin');
  });

  it('shows the deliveries and the import jobs, and no form of its own', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Package import', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Recent family imports' })).toBeInTheDocument();
    expect(await screen.findByText('No family import jobs have been submitted yet.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to dashboard/i })).toHaveAttribute(
      'href',
      '/dashboard'
    );
    // The import starts from Deliveries: no folder import, Discover or manifest of its own.
    expect(screen.queryByRole('button', { name: /discover/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /write manifest/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /start import|validate/i })).not.toBeInTheDocument();
  });

  it('shows a job’s report when asked: its log, datasets and the manifest CoGA kept', async () => {
    const job = {
      _id: 'job-1',
      submitted_path: '/data/FAM-100',
      family_id: 'FAM-100',
      status: 'completed',
      dry_run: false,
      requested_by: 'admin@example.com',
      requested_at: '2026-04-29T12:00:00Z',
      heartbeat_at: '2026-04-29T12:00:01Z',
      completed_at: '2026-04-29T12:00:02Z',
      validation_errors: [],
      validation_warnings: [{ code: 'w', message: 'A dataset was skipped.', dataset: 'sv_needlr' }],
      logs: ['Import completed.'],
      metadata: {},
      manifest_sha256: 'a'.repeat(64),
      package_fingerprint: 'b'.repeat(64),
      datasets: [
        { dataset_type: 'snv', enabled: true, status: 'imported', files: [], samples: ['S1'], summary: {} },
      ],
    };
    get.mockImplementation((url, config) => {
      if (url === '/family-imports') return Promise.resolve({ data: [job] });
      if (url === '/family-imports/job-1') return Promise.resolve({ data: job });
      return factory(url, config);
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'View' }));

    expect(await screen.findByText('Import completed.')).toBeInTheDocument();
    expect(screen.getByText(/Manifest kept in CoGA/)).toHaveTextContent('aaaaaaaaaaaa');
    expect(screen.getByText('A dataset was skipped. (sv_needlr)')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open imported family' })).toHaveAttribute(
      'href',
      '/families/FAM-100'
    );
  });

  it('lists the latest 10 jobs, and every job in a scrolling list on "Show all"', async () => {
    // Jobs numbered 1 (latest) to n, as the history returns them, latest first.
    const jobs = (n: number) =>
      Array.from({ length: n }, (_, index) => ({
        _id: `job-${index + 1}`,
        submitted_path: `/data/FAM-${index + 1}`,
        family_id: `FAM-${index + 1}`,
        status: 'completed',
        dry_run: false,
        requested_by: 'admin@example.com',
        requested_at: '2026-04-29T12:00:00Z',
        heartbeat_at: '2026-04-29T12:00:01Z',
        validation_errors: [],
        validation_warnings: [],
        logs: [],
        metadata: {},
        datasets: [],
      }));
    get.mockImplementation((url, config) => {
      if (url === '/family-imports') {
        const limit = Number((config as { params?: { limit?: number } } | undefined)?.params?.limit);
        return Promise.resolve({ data: jobs(Math.min(limit, 40)) });
      }
      return factory(url, config);
    });
    renderPage();

    expect(await screen.findByText('/data/FAM-10')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'View' })).toHaveLength(10);
    expect(screen.queryByText('/data/FAM-11')).not.toBeInTheDocument();
    expect(screen.getByText(/The latest 10 jobs\./)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
    expect(await screen.findByText('/data/FAM-40')).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/family-imports', { params: { limit: 100 } });
    expect(screen.getAllByRole('button', { name: 'View' })).toHaveLength(40);
    expect(screen.getByText(/All 40 jobs\./)).toBeInTheDocument();
    // The whole history scrolls inside its frame, under its header.
    expect(screen.getByText('/data/FAM-40').closest('.data-table-shell')).toHaveClass('import-jobs-table--all');

    fireEvent.click(screen.getByRole('button', { name: 'Show the latest 10' }));
    expect(screen.getAllByRole('button', { name: 'View' })).toHaveLength(10);
    expect(screen.getByText('/data/FAM-10').closest('.data-table-shell')).not.toHaveClass('import-jobs-table--all');
  });

  it('says so when the history holds more jobs than it lists', async () => {
    get.mockImplementation((url, config) => {
      if (url === '/family-imports') {
        const limit = Number((config as { params?: { limit?: number } } | undefined)?.params?.limit);
        return Promise.resolve({
          data: Array.from({ length: limit }, (_, index) => ({
            _id: `job-${index + 1}`,
            submitted_path: `/data/FAM-${index + 1}`,
            family_id: `FAM-${index + 1}`,
            status: 'completed',
            dry_run: true,
            requested_at: '2026-04-29T12:00:00Z',
            validation_errors: [],
            validation_warnings: [],
            logs: [],
            datasets: [],
          })),
        });
      }
      return factory(url, config);
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Show all' }));
    expect(await screen.findByText('/data/FAM-100')).toBeInTheDocument();
    expect(screen.getByText(/The latest 100 jobs, the most the history lists\./)).toBeInTheDocument();
  });

  it('shows a running import’s progress and time left', async () => {
    const now = Date.now();
    const runningJob = {
      _id: 'job-run',
      submitted_path: '/data/FAM-200',
      family_id: 'FAM-200',
      status: 'running',
      dry_run: false,
      requested_by: 'admin@example.com',
      requested_at: new Date(now - 3_600_000).toISOString(),
      heartbeat_at: new Date(now - 5_000).toISOString(),
      validation_errors: [],
      validation_warnings: [],
      logs: [],
      metadata: {},
      datasets: [
        {
          dataset_type: 'qc',
          enabled: true,
          status: 'imported',
          files: [],
          samples: [],
          summary: {},
          progress: {
            started_at: new Date(now - 3_600_000).toISOString(),
            finished_at: new Date(now - 3_480_000).toISOString(),
          },
        },
        {
          dataset_type: 'snv',
          enabled: true,
          status: 'running',
          files: [],
          samples: [],
          message: 'Importing SNV VCF and VEP annotations',
          summary: {},
          progress: {
            started_at: new Date(now - 3_480_000).toISOString(),
            measured_at: new Date(now - 5_000).toISOString(),
            fraction_read: 0.4837,
            seconds_left: 85 * 60,
          },
        },
        {
          dataset_type: 'sv_needlr',
          enabled: true,
          status: 'valid',
          files: [],
          samples: [],
          summary: {},
        },
      ],
    };
    get.mockImplementation((url, config) => {
      if (url === '/family-imports') return Promise.resolve({ data: [runningJob] });
      if (url === '/family-imports/job-run') return Promise.resolve({ data: runningJob });
      return factory(url, config);
    });
    renderPage();

    // The jobs table: the running dataset, its share read and its time left.
    expect(await screen.findByText('snv: 48% read, about 1 h 25 min left')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'View' }));

    expect(
      await screen.findByText(
        /^snv: 48% read, about 1 h 25 min left \(done around .+\)\. 1 more dataset to follow\.$/
      )
    ).toBeInTheDocument();
    const rows = screen.getAllByRole('row');
    const row = (dataset: string) => rows.find((item) => item.textContent?.startsWith(dataset));
    expect(row('qc')).toHaveTextContent('took 2 min');
    expect(row('snv')).toHaveTextContent('48% read, about 1 h 25 min left');
    expect(row('sv_needlr')).toHaveTextContent('—');
  });

});
