/**
 * A repeat locus's page on STRchive, the catalogue the repeat thresholds come from: its
 * disease, ranges, motifs and references. ``locusId`` is STRchive's own ID (``HD_HTT``).
 */
export const strchiveLocusHref = (locusId: string): string =>
  `https://strchive.org/loci/${encodeURIComponent(locusId)}`;

/** A STRchive release's page (its notes and files), e.g. ``v2.28.0``. */
export const strchiveReleaseHref = (release: string): string =>
  `https://github.com/dashnowlab/STRchive/releases/tag/${encodeURIComponent(release)}`;
