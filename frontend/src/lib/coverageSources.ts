/**
 * The CNV callers whose coverage a sample can carry, and how to present them.
 *
 * A long-read family package ships up to three at once. They are drawn as
 * separate stacked tracks rather than merged, because they are independent
 * measurements of the same thing — merging them would average three answers into
 * one that is none of them.
 *
 * All three are stored as a log2 ratio against the sample's own baseline, so a
 * single-copy loss sits at -1 in every track and the stacked view is readable
 * across callers. (HiFiCNV reports absolute read depth; the importer normalises
 * it — see `family_package_datasets._log2_ratio_transform`.)
 */

/** Display order: long-read caller first, then the short-read/reference-panel ones. */
export const COVERAGE_SOURCE_ORDER = ['hificnv', 'wisecondorx', 'qdnaseq'] as const;

const COVERAGE_SOURCE_LABEL: Record<string, string> = {
  hificnv: 'HiFiCNV',
  wisecondorx: 'WisecondorX',
  qdnaseq: 'QDNAseq',
};

/**
 * How a caller is named in a track label. Unknown sources are shown verbatim
 * rather than dropped or title-cased: a track with data must never be silently
 * absent, and inventing a capitalisation for an unrecognised tool reads worse
 * than the tag the pipeline actually wrote.
 */
export const coverageSourceLabel = (source: string): string =>
  COVERAGE_SOURCE_LABEL[source] ?? source;

/**
 * Sources for one sample, in a stable display order.
 *
 * Ordering is by `COVERAGE_SOURCE_ORDER` first so the same caller occupies the
 * same row for every family member — a trio with three callers is nine tracks,
 * and comparing them down a column only works if the rows line up. Anything
 * unrecognised sorts alphabetically after the known ones.
 */
export const orderCoverageSources = (sources: readonly string[] | undefined): string[] => {
  if (!sources?.length) return [];
  const rank = (source: string): number => {
    const index = COVERAGE_SOURCE_ORDER.indexOf(source as (typeof COVERAGE_SOURCE_ORDER)[number]);
    return index === -1 ? COVERAGE_SOURCE_ORDER.length : index;
  };
  return [...new Set(sources)].sort((left, right) => {
    const delta = rank(left) - rank(right);
    return delta !== 0 ? delta : left.localeCompare(right);
  });
};

/**
 * The track label for a caller's coverage.
 *
 * A sample with one caller keeps the plain "Coverage" label it has always had —
 * naming the caller only earns its space when there is another to tell it apart
 * from.
 */
export const coverageTrackLabel = (source: string, totalSources: number): string =>
  totalSources > 1 ? `Coverage · ${coverageSourceLabel(source)}` : 'Coverage';
