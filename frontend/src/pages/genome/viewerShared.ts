import type { ApiFamilyRegionOfInterest } from '../../lib/apiTypes';

export const CHROMS = [
  ...Array.from({ length: 22 }, (_, i) => String(i + 1)),
  'X',
  'Y',
];

export const DEFAULT_TRACK_WIDTH = 1200;
export const TRACK_WIDTH_PADDING = 32;

export const formatBp = (bp: number): string => {
  if (bp >= 1_000_000) return `${(bp / 1_000_000).toFixed(2)} Mb`;
  if (bp >= 1_000) return `${(bp / 1_000).toFixed(2)} kb`;
  return `${bp} bp`;
};

export const normalizeChrom = (value: string): string =>
  value.toLowerCase().startsWith('chr') ? value.slice(3) : value;

export const formatRoiCoordinates = (roi: ApiFamilyRegionOfInterest): string => {
  const chrom = roi.chr.startsWith('chr') ? roi.chr : `chr${roi.chr}`;
  return `${chrom}:${roi.start.toLocaleString()}-${roi.end.toLocaleString()}`;
};

export const buildTrackFilterSummary = (
  variantFilters: Record<string, string>,
  sampleFilter?: string,
): string | null => {
  const parts = [
    ...Object.entries(variantFilters).map(([key, value]) => `${key}=${value}`),
    sampleFilter ? `sample_filter=${sampleFilter}` : null,
  ].filter(Boolean);

  return parts.length ? parts.join(', ') : null;
};
