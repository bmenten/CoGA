import { formatChromosomeLabel } from '../../lib/chromosomes';

/**
 * A region with width. A zero-width or inverted view asks the API for nothing, so a track
 * over it is neither loading nor empty: it says so with {@link NO_REGION_IN_VIEW} (#602).
 */
export const hasRegionInView = (start: number, end: number): boolean => end > start;

/** The state a track names when {@link hasRegionInView} is false. */
export const NO_REGION_IN_VIEW = 'no region in view';

/**
 * Where a track looks, for its accessible name (#529): `chr1:1,000–2,000`, and `chrM` for
 * the mitochondrion as the viewer header writes it. Without a region in view only the
 * chromosome is named.
 */
export const describeTrackRegion = (chrom: string, start: number, end: number): string =>
  hasRegionInView(start, end)
    ? `${formatChromosomeLabel(chrom)}:${start.toLocaleString()}–${end.toLocaleString()}`
    : formatChromosomeLabel(chrom);
