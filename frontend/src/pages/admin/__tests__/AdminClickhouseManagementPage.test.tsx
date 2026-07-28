import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi, type Mock } from 'vitest';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import AdminClickhouseManagementPage from '../AdminClickhouseManagementPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

describe('AdminClickhouseManagementPage', () => {
  it('runs ClickHouse ensure actions', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/admin/clickhouse/variants') {
        return Promise.resolve({
          data: {
            assemblies: [
              {
                assembly_name: 'GRCh38',
                health: 'missing',
                expected_table_count: 12,
                existing_table_count: 10,
                missing_tables: ['GRCh38/SNV_INDEL/key_lookup'],
                pending_mutations: 0,
                total_rows: 6200,
                total_bytes_on_disk: 489216,
                small_variant_rows: 5000,
                structural_variant_rows: 1200,
                tables: [],
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    (api.post as unknown as Mock).mockResolvedValue({ data: { ok: true } });

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AdminClickhouseManagementPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.click(
      await screen.findByRole('button', { name: 'Ensure tables' })
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/admin/clickhouse/variants/GRCh38/ensure'
      )
    );
    expect(
      await screen.findByText('Ensured ClickHouse variant tables for GRCh38.')
    ).toBeInTheDocument();
  });

  it('runs an integrity check and shows the report inline', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/admin/clickhouse/variants') {
        return Promise.resolve({
          data: {
            assemblies: [
              {
                assembly_name: 'GRCh38',
                health: 'ready',
                expected_table_count: 12,
                existing_table_count: 12,
                missing_tables: [],
                pending_mutations: 0,
                total_rows: 6200,
                total_bytes_on_disk: 489216,
                small_variant_rows: 5000,
                structural_variant_rows: 1200,
                tables: [],
              },
            ],
          },
        });
      }
      if (url === '/admin/clickhouse/variants/GRCh38/integrity') {
        return Promise.resolve({
          data: {
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
      }
      return Promise.resolve({ data: [] });
    });

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AdminClickhouseManagementPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Integrity check' }));

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/admin/clickhouse/variants/GRCh38/integrity')
    );
    expect(await screen.findByText('Integrity: Degraded')).toBeInTheDocument();
    expect(screen.getByText('52 detached broken parts')).toBeInTheDocument();
    expect(screen.getByText('gene_index consistent')).toBeInTheDocument();
    expect(
      screen.getByText(/detached broken part\(s\) found/),
    ).toBeInTheDocument();
  });
});
