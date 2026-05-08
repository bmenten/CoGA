import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, it, vi } from 'vitest';

import SampleUpload from '../SampleUpload';
import api from '../../../lib/api';

vi.mock('../../../lib/api');

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
    (api.post as any).mockResolvedValue({
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

  it('uploads structural variants with explicit parser selection', async () => {
    (api.post as any).mockResolvedValue({
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
    (api.post as any).mockResolvedValue({
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
