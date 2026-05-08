import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import DataManagementPage from '../DataManagementPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    delete: vi.fn(),
    put: vi.fn(),
  },
}));

const mockInventoryResponses = () => {
  (api.get as unknown as Mock).mockImplementation((url: string) => {
    if (url.startsWith('/admin/data?') || url === '/admin/data') {
      return Promise.resolve({
        data: {
          total: 1,
          page: 1,
          page_size: 25,
          items: [
            {
              family_id: 'F1',
              metadata: { demo: true },
              projects: ['P1'],
              sample_count: 2,
              track_counts: {
                small_variants: 5000,
                structural_variants: 1200,
                coverage: 4000,
                segments: 18,
                apcad: 900,
                haplotype: 22,
              },
              total_records: 11140,
            },
          ],
        },
      });
    }

    if (url === '/admin/data/families/F1') {
      return Promise.resolve({
        data: {
          family_id: 'F1',
          metadata: { demo: true },
          projects: ['P1'],
          sample_count: 2,
          track_counts: {
            small_variants: 5000,
            structural_variants: 1200,
            coverage: 4000,
            segments: 18,
            apcad: 900,
            haplotype: 22,
          },
          total_records: 11140,
          samples: [
            {
              sample_id: 'S1',
              role: 'proband',
              affected: true,
              sex: 'male',
              projects: ['P1'],
              track_counts: {
                coverage: 2000,
                segments: 9,
                apcad: 450,
                haplotype: 11,
                structural_variants: 610,
              },
              total_records: 3080,
            },
            {
              sample_id: 'S2',
              role: 'mother',
              affected: false,
              sex: 'female',
              projects: ['P1'],
              track_counts: {
                coverage: 2000,
                segments: 9,
                apcad: 450,
                haplotype: 11,
                structural_variants: 590,
              },
              total_records: 3060,
            },
          ],
        },
      });
    }

    if (url === '/projects') {
      return Promise.resolve({
        data: [
          { _id: 'P1', name: 'Proj1' },
          { _id: 'P2', name: 'Proj2' },
        ],
      });
    }

    return Promise.resolve({ data: [] });
  });
};

describe('DataManagementPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows family detail and dedicated project-access editor', async () => {
    mockInventoryResponses();
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <DataManagementPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(
      await screen.findByText('Family and sample data management')
    ).toBeInTheDocument();
    expect(
      await screen.findByText('Delete family small variants')
    ).toBeInTheDocument();
    expect(screen.getByText('Delete entire family')).toBeInTheDocument();
    expect(screen.getAllByText('Delete sample').length).toBeGreaterThan(0);
    expect(screen.getByText('Link family to projects')).toBeInTheDocument();
    expect(screen.getByText('Save project access')).toBeInTheDocument();
    expect(screen.getByText('Assigned projects')).toBeInTheDocument();
    expect(screen.getAllByText('Proj1').length).toBeGreaterThan(0);
    expect(
      screen.getByRole('link', { name: /clickhouse tables/i })
    ).toHaveAttribute('href', '/admin/data/clickhouse');
    expect(
      screen.getByRole('link', { name: /preset filters/i })
    ).toHaveAttribute('href', '/admin/data/presets');
    expect(screen.getByRole('link', { name: /variant tags/i })).toHaveAttribute(
      'href',
      '/admin/data/tags'
    );
  });

  it('invalidates shared project and family catalogs after saving family project access', async () => {
    mockInventoryResponses();
    (api.put as unknown as Mock).mockResolvedValue({ data: { ok: true } });

    const queryClient = createTestQueryClient();
    const invalidateSpy = vi
      .spyOn(queryClient, 'invalidateQueries')
      .mockResolvedValue(undefined);

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <DataManagementPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText('Save project access')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Proj2' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Save project access' })
    );

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith('/admin/families/F1/projects', {
        project_ids: ['P1', 'P2'],
      })
    );

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['admin', 'data-inventory'],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['admin', 'data-inventory', 'family', 'F1'],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['projects'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['families'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['family', 'F1'] });
  });
});
