import { vi } from 'vitest';

type FetchLikeResponse = { ok: boolean; status?: number; json: () => Promise<unknown> };
type FetchLike = (url: string, init?: { signal?: AbortSignal }) => Promise<FetchLikeResponse>;

/** Stand-in for lib/trackFetch.fetchTrackJson; install with vi.mock in the test file. */
export const fetchTrackJsonMock = vi.fn();

/**
 * Serve the mocked fetchTrackJson from a fetch-shaped fixture ({ ok, status, json }),
 * applying the real helper's rules: an OK response yields its JSON, a 404 yields null
 * when the caller allows "no data" 404s, and anything else throws like the API client.
 */
export function serveTrackFetchFrom(fetchLike: FetchLike): void {
  fetchTrackJsonMock.mockImplementation(
    async (url: string, options: { signal?: AbortSignal; emptyOn404?: boolean } = {}) => {
      const res = await fetchLike(url, { signal: options.signal });
      if (res.ok) return res.json();
      if (options.emptyOn404 && res.status === 404) return null;
      throw Object.assign(new Error(`HTTP ${res.status ?? 'error'}`), {
        response: { status: res.status },
      });
    },
  );
}
