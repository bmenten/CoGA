import { useRef } from 'react';

/**
 * Keeps the previously fetched track data on screen while the next window loads —
 * but ONLY when the viewport span is unchanged (a pan). On a pan the stale points
 * still sit at their true genomic coordinates, so drawing them against the new
 * region scale positions them correctly and covers the overlap, making the pan
 * glide instead of blanking. On a zoom (span changed) the stale data would be
 * mis-scaled (the bunched-band artifact), so it is dropped and the track blanks
 * until the new window arrives.
 *
 * Returns the live data when present, otherwise the last data if its span matches
 * the current one, otherwise null.
 */
export function useSameSpanFallbackData<T>(data: T | null | undefined, span: number): T | null {
  const held = useRef<{ data: T; span: number } | null>(null);
  if (data != null) {
    held.current = { data, span };
    return data;
  }
  if (held.current && held.current.span === span) {
    return held.current.data;
  }
  return null;
}
