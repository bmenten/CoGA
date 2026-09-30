import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi, type Mock } from 'vitest';
import api from '../../../lib/api';
import type { ClickHouseIntegrityMonitorOut } from '../../../lib/apiSchema.generated';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import AdminClickhouseManagementPage from '../AdminClickhouseManagementPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const assembly = (assemblyName: string, health: 'ready' | 'missing' = 'ready') => ({
  assembly_name: assemblyName,
  health,
  expected_table_count: 12,
  existing_table_count: health === 'ready' ? 12 : 10,
  missing_tables: health === 'ready' ? [] : [`${assemblyName}/SNV_INDEL/key_lookup`],
  pending_mutations: 0,
  total_rows: 6200,
  total_bytes_on_disk: 489216,
  small_variant_rows: 5000,
  structural_variant_rows: 1200,
  tables: [],
});

const NO_SWEEP_YET: ClickHouseIntegrityMonitorOut = {
  enabled: true,
  interval_seconds: 21600,
  last_sweep_at: null,
  last_sweep_error: null,
  results: [],
};

const mockApi = (
  assemblies: ReturnType<typeof assembly>[],
  monitor: ClickHouseIntegrityMonitorOut = NO_SWEEP_YET,
  extra: Record<string, unknown> = {},
) => {
  (api.get as unknown as Mock).mockImplementation((url: string) => {
    if (url === '/admin/clickhouse/variants') {
      return Promise.resolve({ data: { assemblies } });
    }
    if (url === '/admin/clickhouse/variants/integrity-monitor') {
      return Promise.resolve({ data: monitor });
    }
    if (url in extra) {
      return Promise.resolve({ data: extra[url] });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
};

const renderPage = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <AdminClickhouseManagementPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('AdminClickhouseManagementPage', () => {
  it('runs ClickHouse ensure actions', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    mockApi([assembly('GRCh38', 'missing')]);
    (api.post as unknown as Mock).mockResolvedValue({ data: { ok: true } });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Ensure tables' }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/admin/clickhouse/variants/GRCh38/ensure'),
    );
    expect(
      await screen.findByText('Ensured ClickHouse variant tables for GRCh38.'),
    ).toBeInTheDocument();
  });

  it('runs an integrity check and shows the report inline', async () => {
    mockApi([assembly('GRCh38')], NO_SWEEP_YET, {
      '/admin/clickhouse/variants/GRCh38/integrity': {
        assembly_name: 'GRCh38',
        status: 'degraded',
        table_checks: [],
        detached_broken_parts: [
          { table: 'GRCh38/SNV_INDEL/entries', reason: 'broken-on-start', count: 52 },
        ],
        gene_index_consistency: {
          checked: true,
          gene_index_keys: 100,
          annotation_index_gene_keys: 100,
          consistent: true,
          drift: 0,
        },
        notes: ['52 detached broken part(s) found — investigate storage-volume health.'],
      },
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Integrity check' }));

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/admin/clickhouse/variants/GRCh38/integrity'),
    );
    expect(await screen.findByText('Integrity: Degraded')).toBeInTheDocument();
    expect(screen.getByText('52 detached broken parts')).toBeInTheDocument();
    expect(screen.getByText('gene_index consistent')).toBeInTheDocument();
    expect(screen.getByText(/detached broken part\(s\) found/)).toBeInTheDocument();
  });

  it('shows the last scheduled result of each assembly without running a check', async () => {
    mockApi([assembly('GRCh38'), assembly('T2T_CHM13v2.0'), assembly('GRCh37', 'missing')], {
      enabled: true,
      interval_seconds: 21600,
      last_sweep_at: '2026-09-30T10:12:00Z',
      last_sweep_error: null,
      results: [
        {
          assembly_name: 'GRCh38',
          checked_at: '2026-09-30T10:12:00Z',
          report: {
            assembly_name: 'GRCh38',
            status: 'corrupt',
            table_checks: [],
            detached_broken_parts: [],
            gene_index_consistency: {
              checked: true,
              gene_index_keys: 10,
              annotation_index_gene_keys: 10,
              consistent: true,
              drift: 0,
            },
            notes: ['One or more active parts failed CHECK TABLE — rebuild or restore affected tables.'],
          },
          error: null,
        },
        {
          assembly_name: 'T2T_CHM13v2.0',
          checked_at: '2026-09-30T10:12:00Z',
          report: null,
          error: 'The integrity check could not run; the backend log has the error.',
        },
      ],
    });

    renderPage();

    expect(
      await screen.findByText(/The scheduled integrity check runs every 6 hours; it last ran/),
    ).toBeInTheDocument();
    const grch38 = await screen.findByLabelText('Scheduled integrity result for GRCh38');
    expect(within(grch38).getByText('Scheduled check: Corrupt')).toBeInTheDocument();
    expect(within(grch38).getByText(/failed CHECK TABLE/)).toBeInTheDocument();
    expect(within(grch38).getByText(/^Checked /)).toBeInTheDocument();
    const t2t = screen.getByLabelText('Scheduled integrity result for T2T_CHM13v2.0');
    expect(within(t2t).getByText('Scheduled check: Could not run')).toBeInTheDocument();
    expect(within(t2t).getByText(/could not run; the backend log has the error/)).toBeInTheDocument();
    // An assembly the sweep did not check (it has no tables) says so.
    expect(screen.getByText('The scheduled check has not checked this assembly.')).toBeInTheDocument();
    // Reading the last result runs no check.
    expect(api.get).not.toHaveBeenCalledWith(expect.stringMatching(/\/integrity$/));
  });

  it('says when the scheduled check is off or has not run yet', async () => {
    mockApi([assembly('GRCh38')], {
      enabled: false,
      interval_seconds: 21600,
      last_sweep_at: null,
      last_sweep_error: null,
      results: [],
    });

    const { unmount } = renderPage();

    expect(
      await screen.findByText(
        'The scheduled integrity check is off (CLICKHOUSE_INTEGRITY_MONITOR_ENABLED).',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/Scheduled integrity result/)).not.toBeInTheDocument();
    unmount();

    mockApi([assembly('GRCh38')], NO_SWEEP_YET);
    renderPage();

    expect(
      await screen.findByText(
        'The scheduled integrity check runs every 6 hours; it has not run since the server started.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText('The scheduled check has not checked this assembly.'),
    ).not.toBeInTheDocument();
  });

  it('says when the scheduled result cannot be loaded', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) =>
      url === '/admin/clickhouse/variants'
        ? Promise.resolve({ data: { assemblies: [assembly('GRCh38')] } })
        : Promise.reject({ response: { status: 500, data: { detail: 'Internal Server Error' } } }),
    );

    renderPage();

    expect(await screen.findByRole('button', { name: 'Integrity check' })).toBeInTheDocument();
    expect(await screen.findByText('Internal Server Error')).toBeInTheDocument();
  });
});
