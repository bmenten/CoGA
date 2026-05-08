import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import IgvViewer from '../IgvViewer';

const { apiGetMock, createBrowserMock, searchMock, loadIgvMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  createBrowserMock: vi.fn(),
  searchMock: vi.fn(),
  loadIgvMock: vi.fn(),
}));

vi.mock('../../lib/api', () => ({
  default: {
    get: apiGetMock,
    defaults: {
      baseURL: 'http://api.test',
    },
  },
}));

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
    apiGetMock.mockResolvedValue({
      data: [
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
    });
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
});
