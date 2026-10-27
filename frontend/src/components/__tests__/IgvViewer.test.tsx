import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { clearSession, getAuthToken, persistSession, updateSessionToken } from '../../lib/auth';
import IgvViewer from '../IgvViewer';

const { apiGetMock, createBrowserMock, searchMock, loadIgvMock, freshMicrosoftAccessTokenMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  createBrowserMock: vi.fn(),
  searchMock: vi.fn(),
  loadIgvMock: vi.fn(),
  freshMicrosoftAccessTokenMock: vi.fn(),
}));

// MSAL, as the API client asks it for a Microsoft session's token (SEC-4).
vi.mock('../../lib/microsoftSignIn', () => ({
  freshMicrosoftAccessToken: freshMicrosoftAccessTokenMock,
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
    freshMicrosoftAccessTokenMock.mockReset();
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
        oauthToken: expect.any(Function),
      },
      {
        name: 'S2',
        type: 'alignment',
        format: 'bam',
        url: 'http://api.test/cram/F1/S2.bam',
        indexURL: 'http://api.test/cram/F1/S2.bam.bai',
        oauthToken: expect.any(Function),
      },
    ]);
  });

  // SEC-2 — an ID may hold `/`, `?`, `#`, `%` and `..`. Put in raw, this family ID let the
  // browser resolve `../` to another path, cut the path at `?`, and `%41` reached the server
  // as `A`.
  it('asks for the manifests with the family ID as one encoded path segment', async () => {
    localStorage.setItem('token', 'token-123');
    const familyId = '../F2?x=1#y%41';
    const sampleId = 'S/1?#%41';
    // As the backend builds them: every ID one encoded segment.
    const cramUrl = '/cram/..%2FF2%3Fx%3D1%23y%2541/S%2F1%3F%23%2541.cram';
    const depthUrl = '/signal-tracks/..%2FF2%3Fx%3D1%23y%2541/S%2F1%3F%23%2541/hificnv/depth_bigwig';
    apiGetMock.mockImplementation((url: string) =>
      Promise.resolve({
        data: url.startsWith('/signal-tracks/')
          ? [
              {
                sample_id: sampleId,
                source: 'hificnv',
                kind: 'depth_bigwig',
                name: `${sampleId} Read depth`,
                format: 'bigwig',
                url: depthUrl,
                min: null,
                max: null,
              },
            ]
          : [{ sample_id: sampleId, format: 'cram', url: cramUrl, index_url: `${cramUrl}.crai` }],
      }),
    );
    createBrowserMock.mockResolvedValue({ destroy: vi.fn(), search: searchMock });
    loadIgvMock.mockResolvedValue({ createBrowser: createBrowserMock });

    render(<IgvViewer familyId={familyId} sampleIds={[sampleId]} genome="hg38" locus="chr1:10-20" />);

    await waitFor(() => expect(createBrowserMock).toHaveBeenCalled());
    expect(apiGetMock.mock.calls.map(([url]) => url)).toEqual([
      '/cram/..%2FF2%3Fx%3D1%23y%2541/manifest?sample=S%2F1%3F%23%2541',
      '/signal-tracks/..%2FF2%3Fx%3D1%23y%2541/manifest?sample=S%2F1%3F%23%2541',
    ]);
    // IGV gets the URLs the backend built as they came, under the API base: neither
    // decoded nor encoded twice.
    const [, options] = createBrowserMock.mock.calls[0];
    expect(options.tracks.map((track: { url: string; indexURL?: string }) => [track.url, track.indexURL])).toEqual([
      [`http://api.test${depthUrl}`, undefined],
      [`http://api.test${cramUrl}`, `http://api.test${cramUrl}.crai`],
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
        oauthToken: expect.any(Function),
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
        oauthToken: expect.any(Function),
      },
      {
        name: 'S1',
        type: 'alignment',
        format: 'cram',
        url: 'http://api.test/cram/F1/S1.cram',
        indexURL: 'http://api.test/cram/F1/S1.cram.crai',
        oauthToken: expect.any(Function),
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

  // #610 — the failure was swallowed, so the depth and MAF tracks were silently absent, as
  // for a family without them.
  it('says the depth and MAF tracks could not be loaded, and retries them', async () => {
    let signalFails = true;
    apiGetMock.mockImplementation((url: string) => {
      if (url.startsWith('/signal-tracks/')) {
        return signalFails
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
          : Promise.resolve({
              data: [
                {
                  sample_id: 'S1',
                  source: 'hificnv',
                  kind: 'depth_bigwig',
                  name: 'S1 depth',
                  format: 'bigwig',
                  url: '/signal-tracks/F1/S1/hificnv/depth_bigwig',
                },
              ],
            });
      }
      return Promise.resolve({
        data: [{ sample_id: 'S1', format: 'cram', url: '/cram/F1/S1.cram', index_url: '/cram/F1/S1.cram.crai' }],
      });
    });
    createBrowserMock.mockResolvedValue({ destroy: vi.fn(), search: searchMock });
    loadIgvMock.mockResolvedValue({ createBrowser: createBrowserMock });

    render(<IgvViewer familyId="F1" sampleIds={['S1']} genome="hg38" locus="chr1:10-20" />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load the depth and MAF tracks — this is not an empty result. The alignments are shown without them.',
    );
    expect(createBrowserMock.mock.calls[0][1].tracks).toHaveLength(1);

    signalFails = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(createBrowserMock).toHaveBeenCalledTimes(2));
    expect(createBrowserMock.mock.calls[1][1].tracks).toHaveLength(2);
    expect(screen.queryByText(/Could not load the depth and MAF tracks/)).not.toBeInTheDocument();
  });

  // SEC-32 — the viewer read the stored token once, when it built the browser, and IGV sent
  // that token with every request it made: a session longer than the token's life (about an
  // hour for Microsoft's) lost its tracks, though the API client renews the token before each
  // of its own requests. IGV now asks for the session's token at each request (its
  // `oauthToken` function), as the API client does.
  describe('the token IGV sends with its requests', () => {
    const held = ['held', 'session'].join('-');
    const renewed = ['renewed', 'session'].join('-');

    type BuiltTrack = { url: string; oauthToken?: () => Promise<string>; headers?: unknown };

    const builtTracks = async (): Promise<BuiltTrack[]> => {
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
              ]
            : [
                { sample_id: 'S1', format: 'cram', url: '/cram/F1/S1.cram', index_url: '/cram/F1/S1.cram.crai' },
                {
                  sample_id: 'S2',
                  format: 'cram',
                  url: 'https://bucket.s3.amazonaws.com/families/F1/S2.cram?sig=abc',
                  index_url: 'https://bucket.s3.amazonaws.com/families/F1/S2.cram.crai?sig=def',
                },
              ],
        }),
      );
      createBrowserMock.mockResolvedValue({ destroy: vi.fn(), search: searchMock });
      loadIgvMock.mockResolvedValue({ createBrowser: createBrowserMock });

      render(<IgvViewer familyId="F1" sampleIds={['S1', 'S2']} genome="hg38" locus="chr1:10-20" />);

      await waitFor(() => expect(createBrowserMock).toHaveBeenCalled());
      return createBrowserMock.mock.calls[0][1].tracks as BuiltTrack[];
    };

    // Each backend track's token, asked for one request after another: in the test runner, two
    // imports at once of the mocked MSAL module from the real API client can reach the real
    // module (a browser shares one module between them).
    const backendTokens = async (tracks: BuiltTrack[]): Promise<string[]> => {
      const tokens: string[] = [];
      for (const track of tracks.filter((candidate) => candidate.url.startsWith('http://api.test/'))) {
        tokens.push(await track.oauthToken!());
      }
      return tokens;
    };

    it('is the session’s token at each request, so a token renewed after the browser was built reaches IGV', async () => {
      persistSession(held, 'analyst@example.org', 'viewer');
      const tracks = await builtTracks();

      // The depth track and the alignments the backend serves ask for the session's token; no
      // track carries a token fixed when the browser was built.
      expect(tracks.map((track) => [track.url, typeof track.oauthToken, track.headers])).toEqual([
        ['http://api.test/signal-tracks/F1/S1/hificnv/depth_bigwig', 'function', undefined],
        ['http://api.test/cram/F1/S1.cram', 'function', undefined],
        // A presigned URL carries its own authorisation, and gets no session token.
        ['https://bucket.s3.amazonaws.com/families/F1/S2.cram?sig=abc', 'undefined', undefined],
      ]);
      expect(await backendTokens(tracks)).toEqual([held, held]);

      // The session's token is renewed (as the API client does before a request): IGV's next
      // requests send the new one.
      updateSessionToken(renewed);
      expect(await backendTokens(tracks)).toEqual([renewed, renewed]);

      // Signed out: IGV's requests carry no token, and the API refuses them.
      clearSession();
      expect(await backendTokens(tracks)).toEqual(['', '']);
      expect(freshMicrosoftAccessTokenMock).not.toHaveBeenCalled();
    });

    it('asks MSAL for a Microsoft session’s token at each request, and the session keeps a renewed one', async () => {
      persistSession(held, 'a.colleague@example.org', 'viewer', 'microsoft');
      const tracks = await builtTracks();

      freshMicrosoftAccessTokenMock.mockResolvedValue(renewed);
      expect(await backendTokens(tracks)).toEqual([renewed, renewed]);
      expect(getAuthToken()).toBe(renewed);

      // MSAL cannot renew without the user: the token the session holds, then the API's 401.
      freshMicrosoftAccessTokenMock.mockResolvedValue(null);
      expect(await backendTokens(tracks)).toEqual([renewed, renewed]);
    });
  });
});
