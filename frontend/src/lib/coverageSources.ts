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
 * did, or that it could not.) A coverage BED holds what it declared when it was
 * loaded, read depth or a log2 ratio (TRACE-9). What each track holds is served
 * with the track availability (`coverage_contents`), and decides its axis:
 * `coverageScale`.
 */

import type { CoverageContent } from './apiTypes';

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
 * The axis a caller's coverage track is drawn on: what the track holds, as the track
 * availability serves it (`coverage_contents`), decided once by the backend by the rule the
 * depth readers read it by (`coverage_read_depth`, TRACE-9). Read depth on a depth axis, a
 * log2 ratio on the log2-ratio axis, and anything else — a coverage BED that does not declare
 * what it holds, a transform not known, a track the availability says nothing of — on neither:
 * its values on their own range, with a note (`coverageScaleNote`). An undeclared coverage BED
 * used to be drawn on the log2-ratio axis, while the depth readers read it as depth.
 */
export const coverageScale = (content: string | undefined): CoverageContent =>
  content === 'read_depth' || content === 'log2_ratio' ? content : 'unknown';

/**
 * The note in a coverage track's head, saying why it is drawn as it is; `null` when the
 * chart's own legend says it all (a log2 ratio, a coverage BED declared as read depth). A note
 * for values of unknown kind is a warning (`warning`).
 */
export const coverageScaleNote = (
  source: string,
  content: string | undefined,
  normalizations?: readonly string[],
): { text: string; warning: boolean } | null => {
  const scale = coverageScale(content);
  if (scale === 'log2_ratio') return null;
  if (scale === 'read_depth') {
    // HiFiCNV's depths stored as read: the other callers' tracks are log2 ratios (TRACE-7).
    return normalizations?.includes('none')
      ? {
          text: 'Read depth, stored as read: the sample had no usable autosomal median to normalise it by.',
          warning: false,
        }
      : null;
  }
  return {
    text:
      source === 'web'
        ? 'This coverage BED does not declare whether it holds read depth or a log2 ratio: drawn on its own range without an axis, and not read as depth. Load it again with what it holds.'
        : 'What these values are is not recorded: drawn on their own range without an axis, and not read as depth.',
    warning: true,
  };
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
