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
 * it — see `family_package_datasets._log2_ratio_transform` — and records that it
 * did, or that it could not: `coverageNotDrawnNote`.)
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

/**
 * The transforms after which a coverage track's values are a log2 ratio, as the import
 * names them in the track's record (`coverage_normalizations` in the track availability).
 */
const LOG2_RATIO_NORMALIZATIONS: ReadonlySet<string> = new Set([
  'log2_ratio_to_autosomal_median',
  'log2_ratio_to_diploid',
]);

/**
 * Why a caller's coverage track is not drawn on the chart's log2-ratio axis, from the
 * transforms its records name; `null` when it is drawn.
 *
 * A track whose records name no transform holds its file's values: WisecondorX's and
 * QDNAseq's files hold log2 ratios, and an uploaded coverage BED, which records nothing
 * either, is taken for one. A track whose records name a log2-ratio transform was put on
 * that scale by the import. Anything else is not a log2 ratio: `none`, HiFiCNV depths
 * stored as read because the sample had no usable autosomal median to normalise them by,
 * or a transform this view does not know. Drawn on the axis, read depth sits above its top
 * and reads as a gain across the genome, so the track is left out and its place says why
 * (TRACE-7).
 */
export const coverageNotDrawnNote = (normalizations: readonly string[] | undefined): string | null => {
  const notLog2 = (normalizations ?? []).filter((value) => !LOG2_RATIO_NORMALIZATIONS.has(value));
  if (notLog2.length === 0) return null;
  if (notLog2.includes('none')) {
    return 'Not drawn: no usable autosomal median, so these depths are stored as read, not as a log2 ratio. IGV shows the depth file.';
  }
  return `Not drawn: these values are stored as ${notLog2.join(' and ')}, not as a log2 ratio.`;
};


/**
 * Callers whose APCAD rows are a *folded* minor allele fraction rather than
 * parent-of-origin markers.
 *
 * A depth caller writes MAF as min(AF, 1-AF) per site, so its values never exceed
 * 0.5 — drawn on the 0-1 B-allele axis the phased track uses, every point is
 * squashed into the bottom half and the top half is dead space.
 */
const FOLDED_APCAD_SOURCES = new Set(['hificnv']);

/** Top of the APCAD y-axis for the callers present on a sample. */
export const apcadAxisMax = (sources: readonly string[] | undefined): number => {
  const present = sources?.filter(Boolean) ?? [];
  // Only fold the axis when everything drawn on it is folded. A sample carrying
  // both a phased track and a MAF track needs the full range for the phased one.
  if (present.length > 0 && present.every((source) => FOLDED_APCAD_SOURCES.has(source))) {
    return 0.5;
  }
  return 1;
};
