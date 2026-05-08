import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ReferenceCatalogPage from '../ReferenceCatalogPage';
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
        <ReferenceCatalogPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('ReferenceCatalogPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    localStorage.setItem('role', 'admin');

    (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url === '/species') {
        return Promise.resolve({
          data: [
            { _id: 'species-1', name: 'Homo sapiens', common_name: 'human', tax_id: 9606 },
          ],
        });
      }

      if (url === '/assemblies') {
        return Promise.resolve({
          data: [
            {
              _id: 'assembly-1',
              species_id: 'species-1',
              assembly_name: 'GRCh38',
              version: 'p14',
              release_date: '2024-01-01',
            },
          ],
        });
      }

      if (url === '/assemblies/reference-status') {
        return Promise.resolve({
          data: [
            {
              assembly_id: 'assembly-1',
              assembly_name: 'GRCh38',
              chromosomes: 24,
              genes: 19876,
              blacklist_regions: 132,
              clinical_cnvs: 48,
            },
          ],
        });
      }

      if (url === '/assemblies/reference-import/organisms') {
        return Promise.resolve({
          data: [
            {
              scientific_name: 'Homo sapiens',
              common_name: 'human',
              tax_id: 9606,
              assembly_count: 2,
            },
          ],
        });
      }

      if (url === '/assemblies/reference-import/assemblies') {
        return Promise.resolve({
          data: [
            {
              scientific_name: 'Homo sapiens',
              common_name: 'human',
              tax_id: 9606,
              ucsc_genome: 'hg38',
              assembly_name: 'GRCh38',
              assembly_version: 'p14',
              release_date: '2024-01-01',
              description: 'Dec. 2013 (GRCh38/hg38)',
              source_name: 'Genome Reference Consortium',
              cytobands_available: true,
              genes_available: true,
              gene_source: 'UCSC gene tables',
            },
          ],
        });
      }

      throw new Error(`Unexpected GET ${url}`);
    });
  });

  it('renders the catalog with assembly reference counts', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Homo sapiens' })).toBeInTheDocument();
    expect(screen.getByText(/human • tax id 9606/i)).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /cytobands/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /genes/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /blacklist/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /clin cnvs/i })).toBeInTheDocument();
    expect(screen.getByText('19,876')).toBeInTheDocument();
    expect(screen.getByText('132')).toBeInTheDocument();
    expect(screen.getByText('48')).toBeInTheDocument();
  });

  it('imports an organism and assembly from the automatic UCSC flow', async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        species_id: 'species-1',
        species_name: 'Homo sapiens',
        assembly_id: 'assembly-1',
        assembly_name: 'GRCh38',
        assembly_version: 'p14',
        ucsc_genome: 'hg38',
        created_species: false,
        created_assembly: false,
        cytobands_inserted: 24,
        genes_inserted: 19876,
        cytobands_replaced: false,
        genes_replaced: false,
        cytoband_source_url: 'https://example.org/cytoBandIdeo.txt.gz',
        gene_source_url: 'https://example.org/ncbiRefSeqCurated.txt.gz',
        gene_source: 'ncbiRefSeqCurated',
      },
    });

    renderPage();

    await screen.findByRole('heading', { name: 'Homo sapiens' });

    fireEvent.change(screen.getByLabelText(/source organism/i), {
      target: { value: '9606' },
    });

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/assemblies/reference-import/assemblies', {
        params: { tax_id: 9606 },
      })
    );

    await screen.findByRole('option', { name: /grch38 p14 \(hg38\)/i });
    fireEvent.change(screen.getByLabelText(/source assembly/i), {
      target: { value: 'hg38' },
    });
    fireEvent.click(screen.getByRole('button', { name: /download cytobands and genes/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/assemblies/reference-import', {
        tax_id: 9606,
        ucsc_genome: 'hg38',
        overwrite: false,
      })
    );

    expect(
      await screen.findByText(/imported grch38 p14: 24 cytobands and 19876 genes loaded/i)
    ).toBeInTheDocument();
  });

  it('uploads assembly reference data for admins', async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        assembly_id: 'assembly-1',
        assembly_name: 'GRCh38',
        dataset_type: 'genes',
        inserted: 2,
        replaced: false,
      },
    });

    renderPage();

    await screen.findByRole('heading', { name: 'Homo sapiens' });

    fireEvent.change(screen.getByLabelText(/^assembly$/i), {
      target: { value: 'assembly-1' },
    });
    fireEvent.change(screen.getByLabelText(/^dataset$/i), {
      target: { value: 'genes' },
    });
    fireEvent.change(screen.getByLabelText(/reference file/i), {
      target: {
        files: [new File(['chr1\t100\t200\tGENE1\t0\t+\tCCDS1\tTX1\t1\t100-200\t0\t\n'], 'refGene.txt')],
      },
    });
    fireEvent.click(screen.getByRole('button', { name: /upload reference data/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/assemblies/assembly-1/reference-upload/genes',
        expect.any(FormData),
        expect.objectContaining({
          params: { overwrite: false },
          headers: { 'Content-Type': 'multipart/form-data' },
        })
      )
    );

    expect(
      await screen.findByText(/loaded 2 genes records into grch38/i)
    ).toBeInTheDocument();
  });
});
