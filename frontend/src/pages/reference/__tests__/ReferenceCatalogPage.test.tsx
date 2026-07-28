import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
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
  let recentActivity: unknown[] = [];

  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    localStorage.setItem('role', 'admin');
    recentActivity = [];

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
              segmental_duplications: 64,
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

      if (url === '/assemblies/reference-import/recent') {
        return Promise.resolve({ data: recentActivity });
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
    expect(screen.getByRole('columnheader', { name: /segdup\/lcr/i })).toBeInTheDocument();
    expect(screen.getByText('19,876')).toBeInTheDocument();
    expect(screen.getByText('132')).toBeInTheDocument();
    expect(screen.getByText('48')).toBeInTheDocument();
    expect(screen.getByText('64')).toBeInTheDocument();
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

    fireEvent.click(screen.getByRole('button', { name: /add organism \/ assembly/i }));

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

  it('imports the assembly and warns when no UCSC gene table is available', async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        species_id: 'species-1',
        species_name: 'Homo sapiens',
        assembly_id: 'assembly-2',
        assembly_name: 'T2T-CHM13v2.0',
        assembly_version: 'hs1',
        ucsc_genome: 'hs1',
        created_species: false,
        created_assembly: true,
        cytobands_inserted: 24,
        genes_inserted: 0,
        cytobands_replaced: false,
        genes_replaced: false,
        cytoband_source_url: 'https://example.org/cytoBandIdeo.txt.gz',
        gene_source_url: '',
        gene_source: 'unavailable',
        gene_warning: 'No supported UCSC gene table was found for hs1',
      },
    });

    renderPage();

    await screen.findByRole('heading', { name: 'Homo sapiens' });

    fireEvent.click(screen.getByRole('button', { name: /add organism \/ assembly/i }));
    fireEvent.change(screen.getByLabelText(/source organism/i), { target: { value: '9606' } });
    await screen.findByRole('option', { name: /grch38 p14 \(hg38\)/i });
    fireEvent.change(screen.getByLabelText(/source assembly/i), { target: { value: 'hg38' } });
    fireEvent.click(screen.getByRole('button', { name: /download cytobands and genes/i }));

    const note = await screen.findByText(/no gene table was available from ucsc/i);
    expect(note).toHaveTextContent(/imported t2t-chm13v2\.0 hs1: 24 cytobands loaded/i);
    expect(note).toHaveTextContent(/upload genes manually/i);
    // genes should not be reported as loaded
    expect(note).not.toHaveTextContent(/genes loaded/i);
  });

  it('shows detailed recent reference activity', async () => {
    recentActivity = [
      {
        assembly_id: 'assembly-1',
        assembly_name: 'GRCh38',
        species_name: 'Homo sapiens',
        dataset_type: 'clinical_cnvs',
        inserted: 1234,
        replaced: true,
        source: 'clinical-cnv-kb',
        performed_by: 'admin@example.org',
        performed_at: '2026-06-12T10:00:00Z',
      },
    ];

    renderPage();

    expect(
      await screen.findByRole('heading', { name: 'Recent reference activity' })
    ).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /^dataset$/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /^rows$/i })).toBeInTheDocument();
    expect(screen.getByText('Clinical CNVs')).toBeInTheDocument();
    expect(screen.getByText('1,234')).toBeInTheDocument();
    expect(screen.getByText('clinical-cnv-kb')).toBeInTheDocument();
    expect(screen.getByText('admin@example.org')).toBeInTheDocument();
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

    fireEvent.click(screen.getByRole('button', { name: /upload reference data/i }));

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
    fireEvent.click(screen.getByRole('button', { name: /upload file/i }));

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

  it('confirms in a modal before refreshing gene metadata', async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ data: {} });

    renderPage();

    await screen.findByRole('heading', { name: 'Homo sapiens' });

    fireEvent.click(screen.getByRole('button', { name: /refresh gene metadata/i }));

    expect(
      await screen.findByText(/refresh cached human gene metadata/i)
    ).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/admin/gene-reference/refresh-all')
    );
  });

  it('opens the upload modal pre-targeted when no source re-sync exists', async () => {
    renderPage();

    await screen.findByRole('heading', { name: 'Homo sapiens' });

    fireEvent.click(screen.getByRole('button', { name: /upload cytobands/i }));

    expect(
      await screen.findByRole('heading', { name: 'Upload reference data' })
    ).toBeInTheDocument();
    expect((screen.getByLabelText(/^assembly$/i) as HTMLSelectElement).value).toBe('assembly-1');
    expect((screen.getByLabelText(/^dataset$/i) as HTMLSelectElement).value).toBe('cytobands');
  });

  const startConflictingUpload = async () => {
    renderPage();
    await screen.findByRole('heading', { name: 'Homo sapiens' });

    fireEvent.click(screen.getByRole('button', { name: /upload reference data/i }));
    fireEvent.change(screen.getByLabelText(/^assembly$/i), { target: { value: 'assembly-1' } });
    fireEvent.change(screen.getByLabelText(/^dataset$/i), { target: { value: 'genes' } });
    fireEvent.change(screen.getByLabelText(/reference file/i), {
      target: { files: [new File(['chr1\t100\t200\tGENE1'], 'refGene.txt')] },
    });
    fireEvent.click(screen.getByRole('button', { name: /upload file/i }));
  };

  it('asks to overwrite in a modal when the data already exist (409), then replaces', async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce({ response: { status: 409 } })
      .mockResolvedValueOnce({
        data: {
          assembly_id: 'assembly-1',
          assembly_name: 'GRCh38',
          dataset_type: 'genes',
          inserted: 5,
          replaced: true,
        },
      });

    await startConflictingUpload();

    expect(
      await screen.findByText(/already exist for the selected assembly/i)
    ).toBeInTheDocument();
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: 'Overwrite' }));

    await waitFor(() =>
      expect(api.post).toHaveBeenLastCalledWith(
        '/assemblies/assembly-1/reference-upload/genes',
        expect.any(FormData),
        expect.objectContaining({
          params: { overwrite: true },
          headers: { 'Content-Type': 'multipart/form-data' },
        })
      )
    );
    expect(
      await screen.findByText(/replaced genes for grch38 with 5 records/i)
    ).toBeInTheDocument();
  });

  it('surfaces an error and keeps the upload modal open when the overwrite re-post fails', async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce({ response: { status: 409 } })
      .mockRejectedValueOnce({ response: { data: { detail: 'Boom' } } });

    await startConflictingUpload();

    fireEvent.click(await screen.findByRole('button', { name: 'Overwrite' }));

    // the inner catch surfaces the error via uploadError…
    expect(await screen.findByText(/boom/i)).toBeInTheDocument();
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
    // …the confirm modal is dismissed (finally cleared confirmAction)…
    expect(screen.queryByRole('button', { name: 'Overwrite' })).not.toBeInTheDocument();
    // …and the upload modal stays open so the user can retry.
    expect(screen.getByRole('heading', { name: 'Upload reference data' })).toBeInTheDocument();
  });

  it('cancels the overwrite confirmation without re-posting', async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce({
      response: { status: 409 },
    });

    await startConflictingUpload();

    expect(
      await screen.findByText(/already exist for the selected assembly/i)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(await screen.findByText(/reference upload cancelled/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/already exist for the selected assembly/i)
    ).not.toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});
