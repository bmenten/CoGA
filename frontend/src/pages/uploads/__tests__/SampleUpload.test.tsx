import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, it, vi } from 'vitest';

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

const renderPage = () =>
  render(
    <MemoryRouter>
      <SampleUpload />
    </MemoryRouter>,
  );

const vcf = (name: string) => new File(['##fileformat=VCFv4.2\n'], name, { type: 'text/plain' });

/** Fills the family small-variant form for FAM1 (with the parser `format`, if given) and sends it. */
const submitFamilyUpload = (format?: string) => {
  fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: 'FAM1' } });
  if (format) {
    fireEvent.change(screen.getAllByDisplayValue(/auto detect/i)[0], { target: { value: format } });
  }
  fireEvent.change(screen.getAllByLabelText(/variant file/i)[0], { target: { files: [vcf('family.vcf')] } });
  fireEvent.click(screen.getByRole('button', { name: /upload family variants/i }));
};

/** Fills the structural-variant form for sample S1 and sends it. */
const submitSvUpload = () => {
  fireEvent.change(screen.getAllByLabelText(/sample id/i)[0], { target: { value: 'S1' } });
  fireEvent.change(screen.getAllByLabelText(/variant file/i)[1], { target: { files: [vcf('sample.vcf')] } });
  fireEvent.click(screen.getByRole('button', { name: /upload structural variants/i }));
};

/** Fills the TRGT form for sample S1 and sends it. */
const submitRepeatUpload = () => {
  fireEvent.change(screen.getAllByLabelText(/sample id/i)[2], { target: { value: 'S1' } });
  fireEvent.change(screen.getByLabelText(/trgt file/i), { target: { files: [vcf('sample.trgt.vcf')] } });
  fireEvent.click(screen.getByRole('button', { name: /upload trgt/i }));
};

describe('SampleUpload', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  it('renders family, structural, BED, and repeat upload sections', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: /family small variants/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /structural variants/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /bed tracks/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /repeat expansions/i })).toBeInTheDocument();
  });

  it('uploads family small variants with parser selection', async () => {
    mockedPost.mockResolvedValue({
      data: {
        inserted: 12,
        haplotypes_inserted: 4,
        source_format: 'glimpse2',
      },
    });

    renderPage();
    submitFamilyUpload('glimpse2');

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

    renderPage();
    submitFamilyUpload();

    expect(await screen.findByText('Imported 3 small variants via clair3.')).toBeInTheDocument();
    expect(screen.queryByText(/upload failed/i)).not.toBeInTheDocument();
  });

  it('replaces the family small variants on confirm after a 409', async () => {
    mockedPost
      .mockRejectedValueOnce({ response: { status: 409, data: { detail: 'exists' } } })
      .mockResolvedValueOnce({ data: servedFamilyUpload });

    renderPage();
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
    mockedPost.mockResolvedValue({
      data: {
        processed: 8,
        created: 7,
        merged: 1,
        source_format: 'sniffles',
      },
    });

    renderPage();

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
    mockedPost.mockResolvedValue({
      data: { processed: 3, created: 3, merged: 0, skipped_without_extent: 2, source_format: 'sniffles' },
    });

    renderPage();
    submitSvUpload();

    expect(
      await screen.findByText(
        'Processed 3 variants via sniffles (3 created, 0 merged). 2 records left out: a deletion, duplication, inversion or CNV that states neither END nor SVLEN.',
      ),
    ).toBeInTheDocument();
  });

  it('uploads TRGT repeat expansions for one sample', async () => {
    mockedPost.mockResolvedValue({
      data: {
        inserted: 21,
        processed: 21,
        source_format: 'trgt',
      },
    });

    renderPage();
    submitRepeatUpload();

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
    renderPage();

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
    renderPage();

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
    renderPage();

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
    renderPage();

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

  // Each form replaces data that already exist (409) only once the user confirms, and says
  // what it replaced.
  it.each<[string, () => void, Record<string, unknown>, string, string]>([
    [
      'family small variants, with their haplotype blocks',
      () => submitFamilyUpload(),
      { ...servedFamilyUpload, haplotypes_inserted: 2 },
      'Replaced family small variants with 3 records via clair3 and 2 haplotype blocks.',
      '/families/FAM1/small-variants/upload',
    ],
    [
      'structural variants, saying the one record left out',
      submitSvUpload,
      { processed: 5, created: 4, merged: 1, skipped_without_extent: 1, source_format: 'sniffles' },
      'Replaced structural variants with 5 records via sniffles (4 created, 1 merged). 1 record left out: a deletion, duplication, inversion or CNV that states neither END nor SVLEN.',
      '/structural-variants/upload/S1',
    ],
    ['TRGT repeat expansions', submitRepeatUpload, { inserted: 21 }, 'Replaced repeat expansion data with 21 loci.', '/repeat-expansions/upload/S1'],
  ])('replaces %s on confirm after a 409', async (_data, submit, served, message, url) => {
    mockedPost
      .mockRejectedValueOnce({ response: { status: 409, data: { detail: 'exists' } } })
      .mockResolvedValueOnce({ data: served });
    renderPage();
    submit();

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(mockedPost.mock.calls.map(([path, , config]) => [path, (config as { params: { overwrite: boolean } }).params.overwrite])).toEqual([
      [url, false],
      [url, true],
    ]);
  });

  // A refused upload says why (the server's reason, else a fixed sentence); one the user does
  // not confirm to overwrite sends nothing more.
  it.each<[string, boolean, unknown[], string, number]>([
    ['refused for another reason', true, [{ response: { status: 422, data: { detail: 'Not a VCF file.' } } }], 'Not a VCF file.', 1],
    ['refused without a reason', true, [new Error('')], 'Family small-variant upload failed.', 1],
    ['not confirmed to overwrite', false, [{ response: { status: 409 } }], 'Family small-variant upload cancelled.', 1],
    [
      'refused again on the overwrite',
      true,
      [{ response: { status: 409 } }, { response: { status: 423, data: { detail: 'An import of FAM1 is running.' } } }],
      'An import of FAM1 is running.',
      2,
    ],
  ])('says so when a family small-variant upload is %s', async (_case, confirms, errors, message, posts) => {
    vi.stubGlobal('confirm', vi.fn(() => confirms));
    for (const error of errors) mockedPost.mockRejectedValueOnce(error);
    renderPage();
    submitFamilyUpload();

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(mockedPost).toHaveBeenCalledTimes(posts);
  });

  // A form sends nothing, and says nothing, without both its ID and its file.
  it.each<[string, RegExp, RegExp, number]>([
    ['family small-variant', /upload family variants/i, /variant file/i, 0],
    ['structural-variant', /upload structural variants/i, /variant file/i, 1],
    ['BED', /upload bed track/i, /bed file/i, 0],
    ['TRGT', /upload trgt/i, /trgt file/i, 0],
  ])('sends no %s upload without its ID or its file', (_form, button, fileLabel, fileIndex) => {
    renderPage();
    const form = screen.getByRole('button', { name: button }).closest('form') as HTMLFormElement;
    const id = within(form).getAllByRole('textbox')[0];
    const file = screen.getAllByLabelText(fileLabel)[fileIndex];

    // A file without an ID: an ID of spaces is none.
    fireEvent.change(id, { target: { value: '  ' } });
    fireEvent.change(file, { target: { files: [vcf('upload.vcf')] } });
    fireEvent.submit(form);
    // An ID without a file.
    fireEvent.change(file, { target: { files: [] } });
    fireEvent.change(id, { target: { value: 'S1' } });
    fireEvent.submit(form);

    expect(mockedPost).not.toHaveBeenCalled();
    expect(form.parentElement!.querySelector('.form-status')).toBeNull();
  });
});
