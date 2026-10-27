import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, it, vi, type Mock } from 'vitest';

import SampleUpload from '../SampleUpload';
import api from '../../../lib/api';
import type { SmallVariantUploadResult } from '../../../lib/apiSchema.generated';

vi.mock('../../../lib/api');

const mockedPost = vi.mocked(api.post);

// The whole body the backend serves for a clair3 family VCF, typed with the generated
// schema so a change to the response model fails the type check here.
const servedFamilyUpload: SmallVariantUploadResult = {
  inserted: 3,
  skipped_malformed: 0,
  skipped_filtered: 0,
  excluded_filters: [],
  haplotypes_inserted: 0,
  source_format: 'clair3',
  annotation_rows: 0,
  annotation_source: null,
  annotation_version: 'vcf_info',
  annotation_provenance: { deepvariant: { version: '1.10.0' } },
  insert_batch_size: 1000,
};

const submitFamilyUpload = () => {
  fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: 'FAM1' } });
  fireEvent.change(screen.getAllByLabelText(/variant file/i)[0], {
    target: {
      files: [new File(['##fileformat=VCFv4.2\n'], 'family.vcf', { type: 'text/plain' })],
    },
  });
  fireEvent.click(screen.getByRole('button', { name: /upload family variants/i }));
};

describe('SampleUpload', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  it('renders family, structural, BED, and repeat upload sections', () => {
    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: /family small variants/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /structural variants/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /bed tracks/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /repeat expansions/i })).toBeInTheDocument();
  });

  it('uploads family small variants with parser selection', async () => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: {
        inserted: 12,
        haplotypes_inserted: 4,
        source_format: 'glimpse2',
      },
    });

    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: 'FAM1' } });
    fireEvent.change(screen.getAllByDisplayValue(/auto detect/i)[0], {
      target: { value: 'glimpse2' },
    });
    fireEvent.change(screen.getAllByLabelText(/variant file/i)[0], {
      target: {
        files: [new File(['##fileformat=VCFv4.2\n'], 'family.vcf', { type: 'text/plain' })],
      },
    });
    fireEvent.click(screen.getByRole('button', { name: /upload family variants/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/families/FAM1/small-variants/upload',
        expect.any(FormData),
        expect.objectContaining({
          params: {
            overwrite: false,
            source_format: 'glimpse2',
          },
        }),
      ),
    );

    expect(
      await screen.findByText(/imported 12 small variants via glimpse2 and created 4 haplotype blocks/i),
    ).toBeInTheDocument();
  });

  it('reports the served result of a family small-variant upload', async () => {
    mockedPost.mockResolvedValue({ data: servedFamilyUpload });

    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );
    submitFamilyUpload();

    expect(await screen.findByText('Imported 3 small variants via clair3.')).toBeInTheDocument();
    expect(screen.queryByText(/upload failed/i)).not.toBeInTheDocument();
  });

  it('replaces the family small variants on confirm after a 409', async () => {
    mockedPost
      .mockRejectedValueOnce({ response: { status: 409, data: { detail: 'exists' } } })
      .mockResolvedValueOnce({ data: servedFamilyUpload });

    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );
    submitFamilyUpload();

    expect(
      await screen.findByText('Replaced family small variants with 3 records via clair3.'),
    ).toBeInTheDocument();
    expect(mockedPost).toHaveBeenLastCalledWith(
      '/families/FAM1/small-variants/upload',
      expect.any(FormData),
      expect.objectContaining({ params: { overwrite: true, source_format: 'auto' } }),
    );
  });

  it('uploads structural variants with explicit parser selection', async () => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: {
        processed: 8,
        created: 7,
        merged: 1,
        source_format: 'sniffles',
      },
    });

    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getAllByLabelText(/sample id/i)[0], { target: { value: 'S1' } });
    fireEvent.change(screen.getAllByDisplayValue(/auto detect/i)[1], {
      target: { value: 'sniffles' },
    });
    fireEvent.change(screen.getAllByLabelText(/variant file/i)[1], {
      target: {
        files: [new File(['##fileformat=VCFv4.2\n'], 'sample.vcf', { type: 'text/plain' })],
      },
    });
    fireEvent.click(screen.getByRole('button', { name: /upload structural variants/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/structural-variants/upload/S1',
        expect.any(FormData),
        expect.objectContaining({
          params: {
            overwrite: false,
            source_format: 'sniffles',
          },
        }),
      ),
    );

    expect(
      await screen.findByText(/processed 8 variants via sniffles \(7 created, 1 merged\)/i),
    ).toBeInTheDocument();
  });

  // CLIN-39: a deletion, duplication, inversion or CNV that states neither END nor SVLEN is
  // left out of an SV upload and counted (the owner's decision); the form says how many.
  it('says how many records an SV upload left out as their extent is not stated', async () => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: { processed: 3, created: 3, merged: 0, skipped_without_extent: 2, source_format: 'sniffles' },
    });

    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getAllByLabelText(/sample id/i)[0], { target: { value: 'S1' } });
    fireEvent.change(screen.getAllByLabelText(/variant file/i)[1], {
      target: { files: [new File(['##fileformat=VCFv4.2\n'], 'sample.vcf', { type: 'text/plain' })] },
    });
    fireEvent.click(screen.getByRole('button', { name: /upload structural variants/i }));

    expect(
      await screen.findByText(
        'Processed 3 variants via sniffles (3 created, 0 merged). 2 records left out: a deletion, duplication, inversion or CNV that states neither END nor SVLEN.',
      ),
    ).toBeInTheDocument();
  });

  it('uploads TRGT repeat expansions for one sample', async () => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: {
        inserted: 21,
        processed: 21,
        source_format: 'trgt',
      },
    });

    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getAllByLabelText(/sample id/i)[2], { target: { value: 'S1' } });
    fireEvent.change(screen.getByLabelText(/trgt file/i), {
      target: {
        files: [new File(['##fileformat=VCFv4.2\n'], 'sample.trgt.vcf', { type: 'text/plain' })],
      },
    });
    fireEvent.click(screen.getByRole('button', { name: /upload trgt/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/repeat-expansions/upload/S1',
        expect.any(FormData),
        expect.objectContaining({
          params: {
            overwrite: false,
          },
        }),
      ),
    );

    expect(await screen.findByText(/imported 21 trgt repeat loci/i)).toBeInTheDocument();
  });

  // TRACE-9 — a coverage BED declares what it holds. The form asks, with no default that would
  // pick one: an undeclared BED used to be read as depth while the chart drew it as a log2 ratio.
  const fillBedUpload = (trackType?: string) => {
    fireEvent.change(screen.getAllByLabelText(/sample id/i)[1], { target: { value: 'S1' } });
    if (trackType) fireEvent.change(screen.getByLabelText(/track type/i), { target: { value: trackType } });
    fireEvent.change(screen.getByLabelText(/bed file/i), {
      target: { files: [new File(['chrM\t0\t16569\t400\n'], 'S1.coverage.bed', { type: 'text/plain' })] },
    });
  };

  it('asks what a coverage BED holds, and uploads nothing until it is chosen', async () => {
    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );

    const holds = screen.getByLabelText(/coverage bed holds/i);
    expect(holds).toHaveValue('');
    expect(holds).toBeRequired();
    expect(
      Array.from((holds as HTMLSelectElement).options).map((option) => [option.value, option.textContent]),
    ).toEqual([
      ['', 'Choose…'],
      ['read_depth', 'Read depth'],
      ['log2_ratio', 'Log2 ratio'],
    ]);
    fillBedUpload();
    fireEvent.submit(screen.getByRole('button', { name: /upload bed track/i }).closest('form') as HTMLFormElement);

    expect(await screen.findByText('Choose what the coverage BED holds: read depth or log2 ratio.')).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it('uploads a coverage BED with what it holds', async () => {
    mockedPost.mockResolvedValue({ data: { inserted: 120 } });
    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );

    fillBedUpload();
    fireEvent.change(screen.getByLabelText(/coverage bed holds/i), { target: { value: 'read_depth' } });
    fireEvent.click(screen.getByRole('button', { name: /upload bed track/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/bed/upload/S1/coverage',
        expect.any(FormData),
        expect.objectContaining({ params: { overwrite: false, content: 'read_depth' } }),
      ),
    );
    expect(await screen.findByText('Uploaded 120 coverage record(s) declared as read depth.')).toBeInTheDocument();
  });

  it('replaces a coverage BED on confirm, with what it holds', async () => {
    mockedPost
      .mockRejectedValueOnce({ response: { status: 409, data: { detail: 'BED data already exists for this sample' } } })
      .mockResolvedValueOnce({ data: { inserted: 120 } });
    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );

    fillBedUpload();
    fireEvent.change(screen.getByLabelText(/coverage bed holds/i), { target: { value: 'log2_ratio' } });
    fireEvent.click(screen.getByRole('button', { name: /upload bed track/i }));

    expect(await screen.findByText('Replaced 120 coverage record(s) declared as log2 ratio.')).toBeInTheDocument();
    expect(mockedPost).toHaveBeenLastCalledWith(
      '/bed/upload/S1/coverage',
      expect.any(FormData),
      expect.objectContaining({ params: { overwrite: true, content: 'log2_ratio' } }),
    );
  });

  it('asks nothing of another track type, and sends no content for it', async () => {
    mockedPost.mockResolvedValue({ data: { inserted: 4 } });
    render(
      <MemoryRouter>
        <SampleUpload />
      </MemoryRouter>,
    );

    fillBedUpload('segments');
    expect(screen.queryByLabelText(/coverage bed holds/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /upload bed track/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/bed/upload/S1/segments',
        expect.any(FormData),
        expect.objectContaining({ params: { overwrite: false } }),
      ),
    );
    expect(await screen.findByText('Uploaded 4 segments record(s).')).toBeInTheDocument();
  });
});
