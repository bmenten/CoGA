import { useRef } from 'react';

/**
 * Keeps the previously fetched track data on screen while the next window loads —
 * but ONLY when the viewport span is unchanged (a pan) and the track still shows the
 * same thing. On a pan the stale points still sit at their true genomic coordinates, so
 * drawing them against the new region scale positions them correctly and covers the
 * overlap, making the pan glide instead of blanking. On a zoom (span changed) the stale
 * data would be mis-scaled (the bunched-band artifact), so it is dropped and the track
 * blanks until the new window arrives.
 *
 * `scope` identifies what the data is OF — the chromosome plus whatever else keys the
 * track (sample, family, source). Held data is never shown under a different scope:
 * jumping to another chromosome at the same zoom must not keep painting the previous
 * chromosome's blocks (#510).
 *
 * `failed` is the request's error state. A failure never falls back to held data: the
 * track shows the failure over an empty window, not the previous window's marks, which
 * no longer match the region (#586). Passing `null` for the data could not say this,
 * since a request still loading has no data either.
 *
 * Returns null on a failure; otherwise the live data when present, otherwise the last
 * data if its span and scope match the current ones, otherwise null.
 */
export function useSameSpanFallbackData<T>(
  data: T | null | undefined,
  span: number,
  scope: string,
  failed = false,
): T | null {
  const held = useRef<{ data: T; span: number; scope: string } | null>(null);
  if (failed) return null;
  if (data != null) {
    held.current = { data, span, scope };
    return data;
  }
  if (held.current && held.current.span === span && held.current.scope === scope) {
    return held.current.data;
  }
  return null;
}
