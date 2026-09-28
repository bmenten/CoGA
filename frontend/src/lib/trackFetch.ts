import api from './api';

interface TrackFetchOptions {
  signal?: AbortSignal;
  /**
   * The BED endpoints answer "no data for this sample / region" with a 404: an empty
   * track, returned as null. Only set it for endpoints that mean exactly that.
   */
  emptyOn404?: boolean;
}

/**
 * GET a track payload from a URL that already carries the API base (the genome and
 * chromosome views build their track URLs up front). It goes through the shared client,
 * so the token is attached and an expired session is handled, and the base is not
 * prefixed a second time.
 *
 * Any failure other than an allowed "no data" 404 throws. The tracks used to turn a
 * failed source into null and draw the rest — a partial or empty track that looked like
 * real data and stayed cached for the session (#510). A failure must fail the track's
 * query, so the track can say it could not load.
 */
export async function fetchTrackJson<T>(
  url: string,
  { signal, emptyOn404 = false }: TrackFetchOptions = {},
): Promise<T | null> {
  try {
    const res = await api.get(url, { baseURL: '', signal });
    return res.data as T;
  } catch (error) {
    if (emptyOn404 && (error as { response?: { status?: number } }).response?.status === 404) {
      return null;
    }
    throw error;
  }
}
