import { clamp } from './number';

export const SMALL_VARIANT_TRACK_RESULT_LIMIT = 10000;

// Shared dot radius for the per-point tracks (coverage, APCAD, small variants) so
// they all render at the same size.
export const TRACK_DOT_RADIUS = 1.8;

// The widest window the small-variant track draws every variant in. A whole genome holds about
// 1.5 variants per kb per sample, so a wider window would read thousands of variants per member
// that no one can tell apart; there the track asks for the salient ones alone (ClinVar P/LP,
// HIGH and MODERATE impact; `salient_only` in the API), and its head says so.
export const SMALL_VARIANT_ALL_MARKS_MAX_SPAN = 250_000;

/** What the small-variant track draws in a window of `span` bp: every variant, the salient ones
 * alone, or nothing (a window with no width). */
export type SmallVariantTrackScope = 'all' | 'salient' | 'none';

export const smallVariantTrackScope = (span: number): SmallVariantTrackScope => {
  if (!Number.isFinite(span) || span <= 0) return 'none';
  return span <= SMALL_VARIANT_ALL_MARKS_MAX_SPAN ? 'all' : 'salient';
};

// The widest window the haplotype track draws the raw phased markers in (and counts the
// informative sites over). Over a whole chromosome they are hundreds of thousands of ticks
// that hide the blocks; the ROI view (the ROI and 1 Mb on each side) stays well inside it.
export const PHASED_MARKER_MAX_SPAN = 5_000_000;

export const shouldShowPhasedMarkers = (span: number): boolean =>
  Number.isFinite(span) && span > 0 && span <= PHASED_MARKER_MAX_SPAN;

/** A window size as the track heads write it: 250 kb, 5 Mb. */
export const formatSpanLimit = (bp: number): string =>
  bp >= 1_000_000 ? `${bp / 1_000_000} Mb` : `${bp / 1_000} kb`;

/** What the small-variant track's head says in a window that draws the salient variants alone. */
export const SALIENT_ONLY_HEAD_NOTE =
  `ClinVar P/LP, HIGH and MODERATE only: zoom in to ${formatSpanLimit(SMALL_VARIANT_ALL_MARKS_MAX_SPAN)} or less for all`;

export const getTrackVariantLimit = (width: number): number => {
  if (!Number.isFinite(width) || width <= 0) {
    return 800;
  }
  return clamp(Math.round(width * 2), 400, 4000);
};

export const getTrackBinLimit = (width: number): number => {
  if (!Number.isFinite(width) || width <= 0) {
    return 1200;
  }
  return clamp(Math.round(width * 2), 300, 6000);
};

// APCAD is a dense BAF scatter (two homozygous bands + a heterozygous mid-band),
// not a single binned value per position, so it needs many more points than the
// other tracks to read as continuous bands instead of a dotted line. Still capped
// so the genome-wide payload stays bounded (the backend treats this as a global
// point budget across all chromosomes).
export const getApcadPointLimit = (width: number): number => {
  if (!Number.isFinite(width) || width <= 0) {
    return 4000;
  }
  return clamp(Math.round(width * 5), 2000, 8000);
};

export const getTrackSegmentLimit = (width: number): number => {
  if (!Number.isFinite(width) || width <= 0) {
    return 1600;
  }
  return clamp(Math.round(width * 1.5), 200, 4000);
};

export const getAdaptiveTrackWindow = (
  span: number,
  width: number,
  minimumWindow: number,
): number => {
  if (!Number.isFinite(span) || span <= 0) {
    return Math.max(Math.round(minimumWindow), 1);
  }
  const safeWidth = Number.isFinite(width) && width > 0 ? width : 800;
  const targetBins = Math.max(Math.round(safeWidth / 2), 1);
  const dynamicWindow = Math.ceil(span / targetBins);
  return Math.max(Math.round(minimumWindow), dynamicWindow, 1);
};
