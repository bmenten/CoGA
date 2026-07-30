import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import IgvViewer from '../IgvViewer';

const { apiGetMock, createBrowserMock, searchMock, loadIgvMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  createBrowserMock: vi.fn(),
  searchMock: vi.fn(),
  loadIgvMock: vi.fn(),
}));

vi.mock('../../lib/api', async () => {
  // Spread the real module so the shared URL helpers stay real; only the axios
  // instance is replaced, and its baseURL is what the assertions below pin.
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    default: {
      get: apiGetMock,
      defaults: {
        baseURL: 'http://api.test',
      },
    },
  };
});

vi.mock('../../lib/igvLoader', () => ({
  loadIgv: loadIgvMock,
}));

describe('IgvViewer', () => {
  beforeEach(() => {
    localStorage.clear();
    apiGetMock.mockReset();
    createBrowserMock.mockReset();
    searchMock.mockReset();
    loadIgvMock.mockReset();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  it('loads alignment tracks from the manifest endpoint and passes them to IGV', async () => {
    localStorage.setItem('token', 'token-123');
    apiGetMock.mockImplementation((url: string) =>
      Promise.resolve({
        data: url.startsWith('/signal-tracks/')
          ? []
          : [
              {
                sample_id: 'S1',
                format: 'cram',
                url: '/cram/F1/S1.cram',
                index_url: '/cram/F1/S1.cram.crai',
              },
              {
                sample_id: 'S2',
                format: 'bam',
                url: '/cram/F1/S2.bam',
                index_url: '/cram/F1/S2.bam.bai',
              },
            ],
      }),
    );
    createBrowserMock.mockResolvedValue({
      destroy: vi.fn(),
      search: searchMock,
    });
    loadIgvMock.mockResolvedValue({
      createBrowser: createBrowserMock,
    });

    render(
      <IgvViewer
        familyId="F1"
        sampleIds={['S1', 'S2']}
        genome="hg38"
        locus="chr1:10-20"
      />,
    );

    await waitFor(() => expect(apiGetMock).toHaveBeenCalled());
    await waitFor(() => expect(createBrowserMock).toHaveBeenCalled());
    await waitFor(() => expect(searchMock).toHaveBeenCalledWith('chr1:10-20'));

    expect(apiGetMock.mock.calls[0][0]).toBe('/cram/F1/manifest?sample=S1&sample=S2');

    const [, options] = createBrowserMock.mock.calls[0];
    expect(options.genome).toBe('hg38');
    expect(options.tracks).toEqual([
      {
        name: 'S1',
        type: 'alignment',
        format: 'cram',
        url: 'http://api.test/cram/F1/S1.cram',
        indexURL: 'http://api.test/cram/F1/S1.cram.crai',
        headers: { Authorization: 'Bearer token-123' },
      },
      {
        name: 'S2',
        type: 'alignment',
        format: 'bam',
        url: 'http://api.test/cram/F1/S2.bam',
        indexURL: 'http://api.test/cram/F1/S2.bam.bai',
        headers: { Authorization: 'Bearer token-123' },
      },
    ]);
  });

  it('uses presigned S3 URLs directly, without attaching auth headers', async () => {
    localStorage.setItem('token', 'token-123');
    apiGetMock.mockImplementation((url: string) =>
      Promise.resolve({
        data: url.startsWith('/signal-tracks/')
          ? []
          : [
              {
                sample_id: 'S1',
                format: 'cram',
                url: 'https://bucket.s3.amazonaws.com/families/F1/S1.cram?sig=abc',
                index_url: 'https://bucket.s3.amazonaws.com/families/F1/S1.cram.crai?sig=def',
              },
            ],
      }),
    );
    createBrowserMock.mockResolvedValue({ destroy: vi.fn(), search: searchMock });
    loadIgvMock.mockResolvedValue({ createBrowser: createBrowserMock });

    render(<IgvViewer familyId="F1" sampleIds={['S1']} genome="hg38" locus="chr1:10-20" />);

    await waitFor(() => expect(createBrowserMock).toHaveBeenCalled());
    const [, options] = createBrowserMock.mock.calls[0];
    expect(options.tracks).toEqual([
      {
        name: 'S1',
        type: 'alignment',
        format: 'cram',
        url: 'https://bucket.s3.amazonaws.com/families/F1/S1.cram?sig=abc',
        indexURL: 'https://bucket.s3.amazonaws.com/families/F1/S1.cram.crai?sig=def',
      },
    ]);
  });

  it('shows a recoverable error state when the IGV loader bootstrap fails', async () => {
    apiGetMock.mockResolvedValue({
      data: [
        {
          sample_id: 'S1',
          format: 'cram',
          url: '/cram/F1/S1.cram',
          index_url: '/cram/F1/S1.cram.crai',
        },
      ],
    });
    loadIgvMock
      .mockRejectedValueOnce(new Error('IGV bundle unavailable'))
      .mockResolvedValueOnce({
        createBrowser: createBrowserMock,
      });
    createBrowserMock.mockResolvedValue({
      destroy: vi.fn(),
      search: searchMock,
    });

    render(
      <IgvViewer
        familyId="F1"
        sampleIds={['S1']}
        genome="hg38"
        locus="chr2:20-40"
      />,
    );

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /unable to load igv/i })).toBeInTheDocument(),
    );
    expect(screen.getByText('IGV bundle unavailable')).toBeInTheDocument();
    expect(createBrowserMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /try again/i }));

    await waitFor(() => expect(createBrowserMock).toHaveBeenCalled());
    await waitFor(() => expect(searchMock).toHaveBeenCalledWith('chr2:20-40'));
  });

  it('adds caller signal tracks above the alignments, with the right axis per quantity', async () => {
    localStorage.setItem('token', 'token-123');
    apiGetMock.mockImplementation((url: string) =>
      Promise.resolve({
        data: url.startsWith('/signal-tracks/')
          ? [
              {
                sample_id: 'S1',
                source: 'hificnv',
                kind: 'depth_bigwig',
                name: 'S1 Read depth',
                format: 'bigwig',
                url: '/signal-tracks/F1/S1/hificnv/depth_bigwig',
                min: null,
                max: null,
              },
              {
                sample_id: 'S1',
                source: 'hificnv',
                kind: 'maf_bigwig',
                name: 'S1 Minor allele fraction',
                format: 'bigwig',
                url: '/signal-tracks/F1/S1/hificnv/maf_bigwig',
                min: 0,
                max: 0.5,
              },
            ]
          : [
              {
                sample_id: 'S1',
                format: 'cram',
                url: '/cram/F1/S1.cram',
                index_url: '/cram/F1/S1.cram.crai',
              },
            ],
      }),
    );
    createBrowserMock.mockResolvedValue({ destroy: vi.fn(), search: searchMock });
    loadIgvMock.mockResolvedValue({ createBrowser: createBrowserMock });

    render(<IgvViewer familyId="F1" sampleIds={['S1']} genome="hg38" locus="chr1:10-20" />);

    await waitFor(() => expect(createBrowserMock).toHaveBeenCalled());
    const [, options] = createBrowserMock.mock.calls[0];
    expect(options.tracks).toEqual([
      {
        name: 'S1 Read depth',
        type: 'wig',
        format: 'bigwig',
        url: 'http://api.test/signal-tracks/F1/S1/hificnv/depth_bigwig',
        // Depth is unbounded and sample-specific.
        autoscale: true,
        headers: { Authorization: 'Bearer token-123' },
      },
      {
        name: 'S1 Minor allele fraction',
        type: 'wig',
        format: 'bigwig',
        url: 'http://api.test/signal-tracks/F1/S1/hificnv/maf_bigwig',
        // MAF is 0-0.5 by construction; autoscaling would move the bands about.
        autoscale: false,
        min: 0,
        max: 0.5,
        headers: { Authorization: 'Bearer token-123' },
      },
      {
        name: 'S1',
        type: 'alignment',
        format: 'cram',
        url: 'http://api.test/cram/F1/S1.cram',
        indexURL: 'http://api.test/cram/F1/S1.cram.crai',
        headers: { Authorization: 'Bearer token-123' },
      },
    ]);
  });

  it('still shows alignments when the signal manifest fails', async () => {
    localStorage.setItem('token', 'token-123');
    apiGetMock.mockImplementation((url: string) =>
      url.startsWith('/signal-tracks/')
        ? Promise.reject(new Error('signal manifest unavailable'))
        : Promise.resolve({
            data: [
              {
                sample_id: 'S1',
                format: 'cram',
                url: '/cram/F1/S1.cram',
                index_url: '/cram/F1/S1.cram.crai',
              },
            ],
          }),
    );
    createBrowserMock.mockResolvedValue({ destroy: vi.fn(), search: searchMock });
    loadIgvMock.mockResolvedValue({ createBrowser: createBrowserMock });

    render(<IgvViewer familyId="F1" sampleIds={['S1']} genome="hg38" locus="chr1:10-20" />);

    // Signal tracks are an addition, not a dependency: losing them must not cost
    // the reader the alignments they opened the browser for.
    await waitFor(() => expect(createBrowserMock).toHaveBeenCalled());
    const [, options] = createBrowserMock.mock.calls[0];
    expect(options.tracks).toHaveLength(1);
    expect(options.tracks[0].type).toBe('alignment');
  });
});
