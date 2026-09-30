import { clamp } from './number';

export const SMALL_VARIANT_TRACK_RESULT_LIMIT = 10000;

// Shared dot radius for the per-point tracks (coverage, APCAD, small variants) so
// they all render at the same size.
export const TRACK_DOT_RADIUS = 1.8;

// No span gate: small-variant / phased-marker detail is shown at any zoom and
// governed solely by the variant cap (SMALL_VARIANT_TRACK_RESULT_LIMIT) — beyond
// that count the track shows "too many"; the phased-marker endpoint is bounded.
export const shouldShowSmallVariantDetails = (span: number): boolean =>
  Number.isFinite(span) && span > 0;

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
