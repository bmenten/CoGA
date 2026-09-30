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
});
